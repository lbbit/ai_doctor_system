#!/usr/bin/env python3
"""从运行中的栈导出抽取预测（evals K1/K2 跑分输入），纯标准库。

流程：登录 → 列 Subject → 分页拉取观测 → 按标注行 documentId 分组写出
      predictions/<样本同名>.json，格式 {"file":..., "fields":[{"name","value","unit","date"}]}。

用法：
  set EVAL_EMAIL/EVAL_PASSWORD（或 --email/--password）
  python export_predictions.py [--base-url http://localhost:8080/api] \
      [--annotations ../dataset/kpi_k1k2/annotations.jsonl] \
      [--out ../dataset/kpi_k1k2/predictions] [--live-test]

前置：真实样本已上传→HITL 确认入库；标注 JSONL 每行补上 documentId（见 evals/README.md）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def api(base: str, path: str, token: str | None = None, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        base.rstrip("/") + "/" + path.lstrip("/"),
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
        # 只有带 body 的才是 POST；/auth/me 等是 GET（曾因按前缀判断导致 405）
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))["data"]


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _export_from_db(todo: list[dict], out_dir: Path) -> int:
    """免凭证导出：直读 `derived_records` 最新版本草稿（需在 api 容器内运行）。

    与 ``--source draft`` 语义等价（都取待确认草稿的 observartions），
    区别只在于不经 HTTP、不需要登录凭证，避免：
    - 评测账号密码未知/变更后评测被阻塞；
    - 容器重建后 `/evals` 挂载丢失导致 HTTP 路径失败。

    只取 ``version`` 最大的一条：`derived_records` 每次重抽新增一行，
    读错版本会拿到旧结果（真实踩坑）。
    """
    try:
        from sqlalchemy import select  # noqa: F401

        from app.db.models import DerivedRecord  # noqa: F401
        from app.db.session import SessionLocal  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print(f"[error] --source db 需在 api 容器内运行（{type(exc).__name__}: {exc}）")
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    written = missing = 0
    db = SessionLocal()
    try:
        for row in todo:
            dr = db.scalar(
                select(DerivedRecord)
                .where(DerivedRecord.document_id == row["documentId"])
                .order_by(DerivedRecord.version.desc())
                .limit(1)
            )
            if dr is None:
                print(f"[warn] {Path(row['file']).name}: 无草稿记录")
                missing += 1
                continue
            fields = []
            for o in (dr.payload or {}).get("observations") or []:
                name = o.get("display_name") or o.get("raw_name") or o.get("code") or ""
                if not name:
                    continue
                value = o.get("value")
                fields.append(
                    {
                        "name": str(name),
                        "value": "" if value is None else str(value),
                        "unit": str(o.get("unit") or ""),
                        "date": str(o.get("occurred_at") or "")[:10],
                    }
                )
            (out_dir / (Path(row["file"]).stem + ".json")).write_text(
                json.dumps({"file": row["file"], "fields": fields},
                           ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            written += 1
    finally:
        db.close()
    print(f"[done] predictions 写出 {written}，缺 {missing}（source=db）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://localhost:8080/api")
    ap.add_argument("--email", default=os.environ.get("EVAL_EMAIL", ""))
    ap.add_argument("--password", default=os.environ.get("EVAL_PASSWORD", ""))
    here = Path(__file__).resolve().parent
    ap.add_argument("--annotations", default=str(here / "../dataset/kpi_k1k2/annotations.uploaded.jsonl"))
    ap.add_argument("--subject-id", default=os.environ.get("EVAL_SUBJECT_ID", ""))
    ap.add_argument("--out", default=str(here / "../dataset/kpi_k1k2/predictions"))
    ap.add_argument(
        "--source",
        choices=("draft", "observation", "db"),
        default="draft",
        help=(
            "draft=从待确认草稿导出（K1/K2 衡量抽取准确率）；"
            "observation=仅已确认入库；"
            "db=直接在**容器内**读 derived_records（无需登录凭证，"
            "配合 docker compose exec 使用）"
        ),
    )
    ap.add_argument("--live-test", action="store_true", help="仅验证登录+Subject 列表连通性")
    args = ap.parse_args()

    rows = load_jsonl(Path(args.annotations)) if Path(args.annotations).exists() else []
    todo = [r for r in rows if r.get("documentId")]
    skipped = len(rows) - len(todo)
    if skipped:
        print(f"[warn] {skipped} 行缺 documentId（样本尚未上传入库或未回填），跳过。")
    if not todo and not args.live_test:
        print("[skip] 无待导出条目；等待真实样本上传确认后重跑（或 --live-test 测连通）。")
        return 0

    if args.source == "db":
        # 免凭证模式：必须在 **api 容器内**运行（能连 DATABASE_URL）。
        # 用途：容器重建后 `/evals` 挂载与评测账号密码都会丢失，
        # 靠 HTTP 登录会阻塞重测；直读 derived_records 是等价且可复现的。
        return _export_from_db(todo, Path(args.out))

    if not args.email or not args.password:
        print("[error] 需要 EVAL_EMAIL/EVAL_PASSWORD（或 --email/--password）")
        return 2

    token = api(args.base_url, "auth/login", body={"email": args.email, "password": args.password})["token"]
    me = api(args.base_url, "auth/me", token)
    subject_list = me.get("subjects") or []
    if not subject_list:
        print("[error] 该账户没有 Subject")
        return 2
    # 评测简化为单档案：用默认（首个）Subject
    subject_id = args.subject_id or subject_list[0]["id"]
    print(f"[ok] login ok，subject={subject_list[0]['name']}（{subject_id}）")
    if args.live_test:
        return 0

    # 拉全量观测并按文档分组（仅 observation 模式需要）
    by_doc: dict[str, list[dict]] = {}
    if args.source == "observation":
        page = 1
        while True:
            data = api(
                args.base_url,
                f"observations?subjectId={subject_id}&page={page}&pageSize=200",
                token,
            )
            for o in data.get("items", []):
                doc_id = o.get("sourceDocumentId")
                if not doc_id:
                    continue
                val = o.get("valueNum") if o.get("valueNum") is not None else o.get("valueText")
                by_doc.setdefault(doc_id, []).append(
                    {
                        "name": o.get("displayName") or o.get("code"),
                        "value": str(val),
                        "unit": o.get("unit") or "",
                        "date": (o.get("occurredAt") or "")[:10],
                    }
                )
            if len(data.get("items", [])) < 200:
                break
            page += 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = missing = 0

    if args.source == "draft":
        # 从草稿（DerivedRecord）导出：K1/K2 衡量的是「抽取准确率」，
        # 未确认的草稿即代表系统抽取结果，无需逐项点击确认 446 项。
        print("[info] source=draft：从待确认草稿导出（不改变入库状态）")
        for r in todo:
            doc_id = r["documentId"]
            try:
                detail = api(
                    args.base_url,
                    f"documents/{doc_id}/derived-record?subjectId={subject_id}",
                    token,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] {Path(r['file']).name}: 取草稿失败 {exc}")
                missing += 1
                continue
            payload = (detail or {}).get("payload") or {}
            observations = payload.get("observations") or []
            if not observations:
                print(f"[warn] {Path(r['file']).name}: 草稿无观测项")
                missing += 1
                continue
            fields = []
            for o in observations:
                name = o.get("display_name") or o.get("raw_name") or o.get("code") or ""
                if not name:
                    continue
                value = o.get("value")
                fields.append(
                    {
                        "name": str(name),
                        "value": "" if value is None else str(value),
                        "unit": str(o.get("unit") or ""),
                        "date": str(o.get("occurred_at") or "")[:10],
                    }
                )
            (out_dir / (Path(r["file"]).stem + ".json")).write_text(
                json.dumps({"file": r["file"], "fields": fields}, ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )
            written += 1
        print(f"[done] predictions 写出 {written}，缺 {missing}（source=draft）")
        return 0

    # source=observation：只统计已确认入库的观测
    for r in todo:
        fields = by_doc.get(r["documentId"])
        if fields is None:
            print(f"[warn] documentId={r['documentId']}（{r['file']}）无已入库观测。")
            missing += 1
            continue
        (out_dir / (Path(r["file"]).stem + ".json")).write_text(
            json.dumps({"file": r["file"], "fields": fields}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        written += 1
    print(f"[done] predictions 写出 {written}，缺 {missing}（source=observation）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
