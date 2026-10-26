"""一次性清理存量草稿版本堆积（在 api 容器内运行）。

背景：`prune_derived_records()` 只在**新抽取完成时**生效，对已经堆积的
历史数据不起作用。本脚本按同一保留策略批量清理存量。

安全性：
- 默认 **dry-run**，只打印将删除的行数，不改数据；
- 加 `--apply` 才真正删除；
- 已确认版本（`confirmed_at` 非空）永不删除；
- 每份文档至少保留最新 `--keep` 版（默认 5）。

用法：
  docker compose -f deploy/docker-compose.yaml exec -T -w /app api \
      python /evals/scripts/prune_derived_records.py            # 预览
  docker compose -f deploy/docker-compose.yaml exec -T -w /app api \
      python /evals/scripts/prune_derived_records.py --apply    # 执行
"""

from __future__ import annotations

import argparse

from sqlalchemy import func, select


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--keep", type=int, default=5, help="每份文档保留的版本数（默认 5）")
    ap.add_argument("--apply", action="store_true", help="真正删除（默认只预览）")
    ap.add_argument("--document-id", default="", help="只处理指定文档")
    args = ap.parse_args()

    try:
        from app.db.models import DerivedRecord
        from app.db.session import SessionLocal
        from app.preprocessing.pipeline import prune_derived_records
    except Exception as exc:  # noqa: BLE001
        print(f"[error] 需在 api 容器内运行（{type(exc).__name__}: {exc}）")
        return 2

    if args.keep < 1:
        print("[error] --keep 必须 >= 1（防止把历史全删）")
        return 2

    db = SessionLocal()
    try:
        q = select(
            DerivedRecord.document_id,
            func.count(DerivedRecord.id).label("cnt"),
        ).group_by(DerivedRecord.document_id)
        if args.document_id:
            q = q.where(DerivedRecord.document_id == args.document_id)
        rows = db.execute(q).all()

        over = [(doc_id, cnt) for doc_id, cnt in rows if cnt > args.keep]
        total_excess = sum(cnt - args.keep for _, cnt in over)
        print(f"[info] 文档总数={len(rows)} 超限文档={len(over)} 预计可清理行数={total_excess}")
        for doc_id, cnt in sorted(over, key=lambda x: -x[1])[:20]:
            print(f"  {doc_id}  versions={cnt}")

        if not args.apply:
            print("[dry-run] 未修改数据；加 --apply 执行删除。")
            return 0

        removed = 0
        for doc_id, _cnt in over:
            removed += prune_derived_records(db, doc_id, keep=args.keep)
        db.commit()
        print(f"[done] 实际删除 {removed} 行（保留最近 {args.keep} 版，已确认版不删）")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
