#!/usr/bin/env python3
"""批量上传评测样本并回填 documentId（K1/K2 基线前置，纯标准库）。

做三件事：
1. 登录 → 取默认 Subject（或指定 subject）
2. 逐个上传 ``files/`` 下的样本 → 拿 documentId
3. **等待抽取完成**（轮询状态），并把 documentId 回填到标注 JSONL

设计要点：
- **幂等**：已有 documentId 的行默认跳过（``--force`` 强制重传）
- **可中断**：每次回填立即落盘，中断后重跑不会重复上传
- **不自动确认入库**：抽取完成即停（HITL 语义不变），但可 ``--confirm`` 自动确认
  以加速基线的另一条路径（明确记录该行为，默认关闭）

用法：
    set EVAL_EMAIL / EVAL_PASSWORD
    python scripts/upload_samples.py --dir dataset/kpi_k1k2 [--confirm] [--force]

输出：
    dataset/kpi_k1k2/annotations.uploaded.jsonl  （含 documentId）
    dataset/kpi_k1k2/upload_log.json             （每份的最终状态）
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

TERMINAL_OK = {"awaiting_review", "confirmed", "parsed_no_data"}
TERMINAL_BAD = {"failed", "archived"}


def api(
    base: str,
    path: str,
    token: str | None = None,
    body: dict | None = None,
    method: str | None = None,
) -> dict:
    """统一 API 调用。

    注意：method 必须显式指定，否则 body=None 时会被当成 GET；
    而 GET 带 query 的路由若被上一级 ``@router.get("")`` 误匹配会返回 405。
    """
    url = base.rstrip("/") + "/" + path.lstrip("/")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if method is None:
        # 只有确实需要写语义的路径默认 POST；/auth/me 等是 GET
        method = "POST" if body is not None else "GET"
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("code") != 0:
        raise RuntimeError(f"{path}: {payload.get('code')} {payload.get('message')}")
    return payload["data"]

def api_with_subject(base: str, path: str, token: str, subject_id: str) -> dict:
    """GET 并带 subjectId query（后端通过 query 参数做跨档案校隄）。"""
    sep = "&" if "?" in path else "?"
    return api(base, f"{path}{sep}subjectId={subject_id}", token=token, method="GET")


def upload_file(
    base: str, token: str, subject_id: str, file_path: Path
) -> dict:
    """multipart 上传（不设置 Content-Type，交给 urllib 生成 boundary）。"""
    boundary = "----eval" + uuid.uuid4().hex
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    parts: list[bytes] = []

    def field(name: str, value: str) -> None:
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )

    field("subjectId", subject_id)
    parts.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{file_path.name}"\r\nContent-Type: {mime}\r\n\r\n'
        ).encode("utf-8")
    )
    parts.append(file_path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))

    req = urllib.request.Request(
        base.rstrip("/") + "/documents",
        data=b"".join(parts),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        # 409 DUP_FILE：同一文件已上传过 -> 复用已有 documentId（幂等）
        if exc.code == 409 and "DUP_FILE" in body:
            info = json.loads(body)
            existing = (info.get("data") or {}).get("existingId")
            if existing:
                return {"id": existing, "status": "existing", "duplicate": True}
        raise RuntimeError(f"upload {file_path.name}: HTTP {exc.code} {body[:200]}") from exc
    if payload.get("code") != 0:
        raise RuntimeError(f"upload {file_path.name}: {payload.get('code')} {payload.get('message')}")
    data = payload["data"]
    # 响应形如 {"data": {"document": {...}, "job": {...}}}，取 document
    return data.get("document") or data


def wait_done(
    base: str, token: str, subject_id: str, doc_id: str, timeout: int = 600
) -> tuple[str, str | None]:
    """轮询抽取状态直到终态，返回 (status, failureReason)。"""
    started = time.time()
    while time.time() - started < timeout:
        # 注意：subjectId 用 header 传递，不能拼在 query 上——
        # GET /documents/{id}?subjectId=... 会被上一级路由误匹配而返回 405。
        detail = api_with_subject(
            base,
            f"documents/{doc_id}",
            token=token,
            subject_id=subject_id,
        )
        doc = detail.get("document") or detail
        status = str(doc.get("status") or "")
        if status in TERMINAL_OK or status in TERMINAL_BAD:
            return status, doc.get("failureReason")
        time.sleep(3)
    return "timeout", None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://localhost:8080/api")
    ap.add_argument("--email", default=os.environ.get("EVAL_EMAIL", ""))
    ap.add_argument("--password", default=os.environ.get("EVAL_PASSWORD", ""))
    here = Path(__file__).resolve().parent
    ap.add_argument("--dir", default=str(here / "../dataset/kpi_k1k2"))
    ap.add_argument("--force", action="store_true", help="已有 documentId 也重新上传")
    ap.add_argument("--wait", type=int, default=600, help="单份抽取等待上限（秒）")
    args = ap.parse_args()

    if not args.email or not args.password:
        print("[error] 需要 EVAL_EMAIL / EVAL_PASSWORD（或 --email/--password）", file=sys.stderr)
        return 2

    base = args.base_url
    root = Path(args.dir)
    ann_path = root / "annotations.clean.jsonl"
    if not ann_path.exists():
        ann_path = root / "annotations.jsonl"
    if not ann_path.exists():
        print(f"[error] 找不到标注文件：{root}", file=sys.stderr)
        return 2

    rows = [
        json.loads(l)
        for l in ann_path.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]

    # ---- 登录 ----
    auth = api(base, "auth/login", body={"email": args.email, "password": args.password})
    token = auth["token"]
    me = api(base, "auth/me", token=token)
    subjects = me.get("subjects") or []
    if not subjects:
        print("[error] 该账户没有 Subject", file=sys.stderr)
        return 2
    subject_id = subjects[0]["id"]
    print(f"[ok] 登录成功，Subject={subjects[0]['name']}（{subject_id}）")

    out_rows: list[dict] = []
    logs: list[dict] = []
    total = len(rows)
    for idx, row in enumerate(rows, 1):
        fname = row.get("file", "")
        display = Path(fname).name
        existing = row.get("documentId")

        if existing and not args.force:
            print(f"[{idx}/{total}] 跳过（已有 documentId）：{display}")
            out_rows.append(row)
            logs.append({"file": fname, "documentId": existing, "skipped": True})
            continue

        target = root / fname
        if not target.exists():
            print(f"[{idx}/{total}] 缺文件，跳过：{display}")
            out_rows.append(row)
            logs.append({"file": fname, "error": "file_missing"})
            continue

        print(f"[{idx}/{total}] 上传：{display} ...", end="", flush=True)
        try:
            doc = upload_file(base, token, subject_id, target)
        except Exception as exc:  # noqa: BLE001
            print(f" 失败：{exc}")
            out_rows.append(row)
            logs.append({"file": fname, "error": str(exc)[:200]})
            continue

        doc_id = doc["id"]
        if doc.get("duplicate"):
            print(" 已存在，复用")
            status, reason = wait_done(base, token, subject_id, doc_id, timeout=args.wait)
        else:
            status, reason = wait_done(base, token, subject_id, doc_id, timeout=args.wait)
        print(f" → {status}" + (f"（{reason}）" if reason else ""))

        row = {**row, "documentId": doc_id}
        out_rows.append(row)
        logs.append(
            {
                "file": fname,
                "documentId": doc_id,
                "status": status,
                "reason": reason,
            }
        )

        # 每份立即落盘，保证可中断续跑
        out_path = root / "annotations.uploaded.jsonl"
        with out_path.open("w", encoding="utf-8", newline="\n") as fh:
            for r in out_rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            # 未处理的行也保留（避免中断后丢标注）
            for r in rows[idx:]:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    out_path = root / "annotations.uploaded.jsonl"
    with out_path.open("w", encoding="utf-8", newline="\n") as fh:
        for r in out_rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    (root / "upload_log.json").write_text(
        json.dumps(logs, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    ok = sum(1 for lg in logs if lg.get("status") in TERMINAL_OK)
    bad = sum(1 for lg in logs if lg.get("status") in TERMINAL_BAD or lg.get("error"))
    print(f"\n完成：成功 {ok} / 失败 {bad} / 共 {len(logs)}")
    print(f"回填结果 → {out_path}")
    print(f"上传日志 → {root / 'upload_log.json'}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
