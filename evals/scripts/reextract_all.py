"""批量重抽评测样本（在 api 容器内运行，免登录凭证）。

用途：字典/解析链/归一化改动后，让评测样本按新逻辑重新抽取，
再配合 `export_predictions.py --source db` + `score_kpi.py k1k2` 做回归。

为什么放在 evals/scripts 而不是临时脚本：
- 每次验证都要用，手抄容易漏步骤；
- 必须只重抽**评测账号**下的文档，避免污染其他账号数据；
- 必须跳过已在队列中的任务（`reextract` 自身会抛冲突，但批量时先跳过更清晰）。

用法（仓库根目录）：
  docker compose -f deploy/docker-compose.yaml cp evals/scripts/reextract_all.py \
      api:/evals/scripts/reextract_all.py
  docker compose -f deploy/docker-compose.yaml exec -T -w /app api \
      python /evals/scripts/reextract_all.py [--email X] [--contains 关键词]

环境变量兜底：EVAL_EMAIL（与 export_predictions.py 保持一致）。
"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--email",
        default=os.environ.get("EVAL_EMAIL", "perma1792646605878@example.com"),
        help="评测账号邮箱（默认取 EVAL_EMAIL 或内置评测账号）",
    )
    ap.add_argument(
        "--contains",
        action="append",
        default=[],
        help="只重抽文件名含该关键词的文档（可多次指定）",
    )
    ap.add_argument("--dry-run", action="store_true", help="只列出将重抽的文档")
    args = ap.parse_args()

    try:
        from sqlalchemy import select

        from app.db.models import Account, Document, ExtractionJob
        from app.db.session import SessionLocal
        from app.preprocessing.review import reextract
    except Exception as exc:  # noqa: BLE001
        print(f"[error] 需在 api 容器内运行（{type(exc).__name__}: {exc}）")
        return 2

    db = SessionLocal()
    try:
        account = db.scalar(select(Account).where(Account.email == args.email))
        if account is None:
            print(f"[error] 账号不存在: {args.email}")
            return 2

        docs = db.scalars(
            select(Document)
            .where(
                Document.account_id == account.id,
                Document.status == "awaiting_review",
                Document.deleted_at.is_(None),
            )
            .order_by(Document.file_name)
        ).all()

        if args.contains:
            docs = [d for d in docs if any(k in d.file_name for k in args.contains)]

        if not docs:
            print("[skip] 没有匹配的待审文档")
            return 0

        print(f"[info] 匹配 {len(docs)} 份文档")
        if args.dry_run:
            for d in docs:
                print(f"  {d.file_name}")
            return 0

        queued = skipped = failed = 0
        for d in docs:
            active = db.scalar(
                select(ExtractionJob)
                .where(
                    ExtractionJob.document_id == d.id,
                    ExtractionJob.state.in_(("queued", "running")),
                )
                .limit(1)
            )
            if active is not None:
                skipped += 1
                continue
            try:
                reextract(db, account, d.id, None, d.subject_id)
                queued += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"[warn] {d.file_name}: {type(exc).__name__}: {exc}")

        print(f"[done] queued={queued} skipped={skipped} failed={failed}")
        print("[hint] worker 按队列顺序处理，全部完成后再导出评分。")
        return 0 if failed == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
