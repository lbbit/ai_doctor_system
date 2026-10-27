"""R1 出口对账（只读诊断）。

用途：在真实部署（api 容器内，DB + MinIO 可达）上核对"资产底座"是否自洽，
作为 R1 出口证据。**默认只读，不写任何数据**。

检查项：
1. 原件存在性：抽样文档的 `file_key` 在对象存储是否存在（缺失=原件丢失，红线）；
2. 副本新鲜度：派生资产 `source_hash` 是否等于文档 `content_hash`（不等=已过期，需重生成）；
3. 孤儿资产：资产指向的文档已软删（可接受，但需计数）或不存在（异常）；
4. 处理完整性：已产出草稿（awaiting_review/processed）却无 markdown 副本的文档数（缺口）；
5. 计数概览：文档总数、按处理状态分布、草稿版本数、已确认版本数、有效/软删观测数。

用法（compose 已起）：
  docker compose -f deploy/docker-compose.yaml exec -T -w /app api \
      python /app/scripts/reconcile_assets.py
  # 加大原件抽样量：
  ... python /app/scripts/reconcile_assets.py --sample 50
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Report:
    documents_total: int = 0
    by_processing_status: dict = field(default_factory=dict)
    originals_checked: int = 0
    originals_missing: list = field(default_factory=list)  # 红线：原件丢失
    assets_total: int = 0
    assets_stale: int = 0
    assets_orphan_soft_deleted_doc: int = 0
    assets_orphan_missing_doc: int = 0  # 异常
    drafts_total: int = 0
    drafts_confirmed: int = 0
    docs_processed_without_markdown: list = field(default_factory=list)
    observations_active: int = 0
    observations_soft_deleted: int = 0
    errors: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        # 红线：原件丢失、资产指向不存在的文档、或对账过程异常
        return (
            not self.originals_missing
            and self.assets_orphan_missing_doc == 0
            and not self.errors
        )


def classify_asset(source_hash: Optional[str], content_hash: Optional[str]) -> str:
    """副本相对源文件的状态：fresh / stale / unknown。

    纯函数，便于单测。`source_hash` 为空（历史行）视为 unknown，不武断判过期。
    """
    if not source_hash or not content_hash:
        return "unknown"
    return "fresh" if source_hash == content_hash else "stale"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sample",
        type=int,
        default=20,
        help="原件存在性抽样份数（0=不检查原件，避免大库过慢）",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="仅对账最近 N 份文档（0=全部）；用于大库快速体检",
    )
    args = ap.parse_args()

    try:
        from sqlalchemy import func, select

        from app.db.models import (
            DerivedRecord,
            Document,
            DocumentAsset,
            Observation,
        )
        from app.db.session import SessionLocal
    except Exception as exc:  # noqa: BLE001
        print(f"[error] 需在 api 容器内运行（{type(exc).__name__}: {exc}）")
        return 2

    r = Report()
    db = SessionLocal()
    try:
        r.documents_total = int(db.scalar(select(func.count(Document.id))) or 0)

        # 处理状态分布（用旧 status 兼容列，避免依赖未回填新列的历史行）
        for status, cnt in db.execute(
            select(Document.status, func.count(Document.id)).group_by(Document.status)
        ).all():
            r.by_processing_status[status or "unknown"] = int(cnt)

        doc_ids = select(Document.id)
        if args.limit:
            doc_ids = doc_ids.order_by(Document.created_at.desc()).limit(args.limit)
        scope_ids = set(db.execute(doc_ids).scalars().all())
        if not scope_ids:
            print("[info] 无文档可对账")
            return 0

        # 1) 原件存在性（抽样）
        if args.sample > 0:
            from app.documents.storage import get_storage_client

            storage = get_storage_client()
            sample = list(
                db.execute(
                    select(Document)
                    .where(Document.id.in_(scope_ids))
                    .order_by(Document.created_at.desc())
                    .limit(args.sample)
                ).scalars()
            )
            for doc in sample:
                r.originals_checked += 1
                try:
                    if not storage.exists(doc.file_key):
                        r.originals_missing.append(doc.id)
                except Exception as exc:  # noqa: BLE001
                    r.errors.append(f"exists({doc.file_key}) {type(exc).__name__}")

        # 2/3) 资产：新鲜度 + 孤儿
        doc_hash = dict(
            db.execute(
                select(Document.id, Document.content_hash).where(
                    Document.id.in_(scope_ids)
                )
            ).all()
        )
        doc_deleted = dict(
            db.execute(
                select(Document.id, Document.deleted_at).where(
                    Document.id.in_(scope_ids)
                )
            ).all()
        )
        assets = list(
            db.execute(
                select(DocumentAsset).where(DocumentAsset.document_id.in_(scope_ids))
            ).scalars()
        )
        r.assets_total = len(assets)
        for a in assets:
            if a.document_id not in doc_hash:
                r.assets_orphan_missing_doc += 1
                continue
            state = classify_asset(a.source_hash, doc_hash[a.document_id])
            if state == "stale":
                r.assets_stale += 1
            if a.role == "markdown" and doc_deleted.get(a.document_id) is not None:
                r.assets_orphan_soft_deleted_doc += 1

        # 4) 已处理但无 markdown 副本（缺口）
        markdown_docs = set(
            db.execute(
                select(DocumentAsset.document_id).where(
                    DocumentAsset.role == "markdown",
                    DocumentAsset.document_id.in_(scope_ids),
                )
            ).scalars().all()
        )
        processed = list(
            db.execute(
                select(Document.id, Document.status, Document.deleted_at).where(
                    Document.id.in_(scope_ids)
                )
            ).all()
        )
        for did, status, deleted in processed:
            if deleted is not None:
                continue
            if status in ("awaiting_review", "confirmed", "parsed_no_data") and (
                did not in markdown_docs
            ):
                r.docs_processed_without_markdown.append(did)

        # 5) 草稿/确认计数 + 观测
        r.drafts_total = int(
            db.scalar(
                select(func.count(DerivedRecord.id)).where(
                    DerivedRecord.document_id.in_(scope_ids)
                )
            )
            or 0
        )
        r.drafts_confirmed = int(
            db.scalar(
                select(func.count(DerivedRecord.id)).where(
                    DerivedRecord.document_id.in_(scope_ids),
                    DerivedRecord.confirmed_at.is_not(None),
                )
            )
            or 0
        )
        r.observations_active = int(
            db.scalar(
                select(func.count(Observation.id)).where(
                    Observation.source_document_id.in_(scope_ids),
                    Observation.deleted_at.is_(None),
                )
            )
            or 0
        )
        r.observations_soft_deleted = int(
            db.scalar(
                select(func.count(Observation.id)).where(
                    Observation.source_document_id.in_(scope_ids),
                    Observation.deleted_at.is_not(None),
                )
            )
            or 0
        )
    finally:
        db.close()

    _print(r)
    return 0 if r.ok else 1


def _print(r: Report) -> None:
    print("=== R1 资产对账报告 ===")
    print(f"文档总数：{r.documents_total}")
    print(f"按状态分布：{r.by_processing_status}")
    print(f"原件抽样：检查 {r.originals_checked}，缺失 {len(r.originals_missing)}"
          + (f" → {r.originals_missing[:10]}" if r.originals_missing else ""))
    print(f"资产总数：{r.assets_total}（过期 {r.assets_stale}，"
          f"指向软删文档 {r.assets_orphan_soft_deleted_doc}，"
          f"指向不存在文档 {r.assets_orphan_missing_doc}）")
    print(f"草稿：共 {r.drafts_total}，已确认 {r.drafts_confirmed}")
    print(f"观测：有效 {r.observations_active}，软删（被取代）{r.observations_soft_deleted}")
    if r.docs_processed_without_markdown:
        print(f"[warn] 已处理但无 Markdown 副本：{len(r.docs_processed_without_markdown)} 份 "
              f"→ {r.docs_processed_without_markdown[:10]}")
    if r.errors:
        print(f"[error] 对账异常：{r.errors[:10]}")
    print("结论：" + ("PASS（无红线）" if r.ok else "FAIL（存在原件丢失/孤儿资产/异常）"))


if __name__ == "__main__":
    raise SystemExit(main())
