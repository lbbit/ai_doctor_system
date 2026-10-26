"""资产服务（R1 T102）：原件与派生资产的登记、读取与版本化。

职责边界：
- 本模块只管"**字节 + 元数据**"，不关心临床语义（那是 preprocessing/review 的事）。
- 所有读取都做归属校验（Account + Subject）与生命周期校验（软删 → 404）。
- 写入是"追加版本"，**永不覆盖**已有资产（原件是证据，派生副本要可对比）。

版本语义：
- `version` 在同一 (document, role) 内单调递增；
- `source_hash` 记录生成时的源文件哈希：源变了，老副本**仍然保留**（可对比），
  但 `is_stale()` 会标出"该重抽了"；
- `generator` 记录产物生成器与版本（如 `markdown/1.0`），不同算法产物不混同版本号。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.common.errors import ConflictError, NotFoundError
from app.common.ids import new_id
from app.db.models import Account, Document, DocumentAsset
from app.documents.state import lifecycle_of
from app.documents.storage import get_storage_client

logger = logging.getLogger("app.documents.assets")

# 派生资产的对象键模板（只用不可变 ID，不含真实姓名）
_ASSET_KEY_TMPL = (
    "objects/{account_id}/{subject_id}/{document_id}/derived/{role}/{version}"
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def asset_key(
    account_id: str, subject_id: str, document_id: str, role: str, version: int
) -> str:
    """派生资产的对象键（原件沿用 `documents.file_key`，不迁移）。"""
    return _ASSET_KEY_TMPL.format(
        account_id=account_id,
        subject_id=subject_id,
        document_id=document_id,
        role=role,
        version=version,
    )


def _next_version(db: Session, document_id: str, role: str) -> int:
    current = db.scalar(
        select(func.max(DocumentAsset.version)).where(
            DocumentAsset.document_id == document_id,
            DocumentAsset.role == role,
        )
    )
    return int(current or 0) + 1


def put_derivative(
    db: Session,
    document: Document,
    *,
    role: str,
    data: bytes,
    mime_type: str,
    generator: str,
    source_hash: Optional[str] = None,
    page_no: Optional[int] = None,
    version: Optional[int] = None,
) -> DocumentAsset:
    """写入一个派生资产（追加新版本，不覆盖）。

    幂等：同一 (document, role, version) 重复写入会抛 ConflictError
    （由调用方决定是否复用已有版本）。
    """
    if role == "original":
        raise ConflictError("原件资产不可通过派生接口写入")
    ver = version if version is not None else _next_version(db, document.id, role)
    content_hash = sha256_bytes(data)
    key = asset_key(document.account_id, document.subject_id, document.id, role, ver)

    storage = get_storage_client()
    storage.put(key, data, mime_type)

    asset = DocumentAsset(
        id=new_id(),
        document_id=document.id,
        account_id=document.account_id,
        subject_id=document.subject_id,
        role=role,
        version=ver,
        object_key=key,
        mime_type=mime_type,
        size_bytes=len(data),
        sha256=content_hash,
        source_hash=source_hash or document.content_hash,
        generator=generator,
        page_no=page_no,
    )
    db.add(asset)
    db.flush()
    logger.info(
        "asset.put doc=%s role=%s version=%s bytes=%s",
        document.id, role, ver, len(data),
    )
    return asset


def list_assets(
    db: Session,
    document: Document,
    *,
    role: Optional[str] = None,
) -> list[DocumentAsset]:
    """列出文档的资产（按角色、版本升序）。"""
    q = select(DocumentAsset).where(DocumentAsset.document_id == document.id)
    if role:
        q = q.where(DocumentAsset.role == role)
    return list(
        db.execute(q.order_by(DocumentAsset.role, DocumentAsset.version)).scalars()
    )


def get_asset(
    db: Session,
    document: Document,
    role: str,
    *,
    version: Optional[int] = None,
) -> DocumentAsset:
    """取指定角色的资产（默认最新版本）；不存在 → 404。"""
    q = select(DocumentAsset).where(
        DocumentAsset.document_id == document.id,
        DocumentAsset.role == role,
    )
    if version is not None:
        q = q.where(DocumentAsset.version == version)
    else:
        q = q.order_by(DocumentAsset.version.desc())
    asset = db.execute(q.limit(1)).scalars().first()
    if asset is None:
        raise NotFoundError("资产不存在")
    return asset


def read_asset(
    db: Session,
    document: Document,
    role: str,
    *,
    version: Optional[int] = None,
) -> tuple[DocumentAsset, bytes]:
    """读取资产内容（归属与生命周期已在调用方校验）。"""
    asset = get_asset(db, document, role, version=version)
    data = get_storage_client().get(asset.object_key)
    # 完整性校验：内容被篡改时不得当作可信副本返回
    actual = sha256_bytes(data)
    if actual != asset.sha256:
        logger.error(
            "asset.corrupted doc=%s role=%s version=%s",
            document.id, role, asset.version,
        )
        raise NotFoundError("资产内容校验失败")
    return asset, data


def asset_out(asset: DocumentAsset, *, stale: bool = False) -> dict:
    """资产视图（供 API 返回）。"""
    return {
        "id": asset.id,
        "documentId": asset.document_id,
        "role": asset.role,
        "version": asset.version,
        "mimeType": asset.mime_type,
        "sizeBytes": asset.size_bytes,
        "sha256": asset.sha256,
        "sourceHash": asset.source_hash,
        "generator": asset.generator,
        "pageNo": asset.page_no,
        "stale": stale,
        "createdAt": asset.created_at.isoformat() if asset.created_at else None,
    }


def is_stale(asset: DocumentAsset, document: Document) -> bool:
    """副本是否已过期（源文件哈希与当前原件不一致）。"""
    if not asset.source_hash:
        return False
    return asset.source_hash != document.content_hash


def ensure_original_asset(
    db: Session,
    document: Document,
    *,
    storage=None,
) -> DocumentAsset:
    """为已有原件补登记 `original` 资产（迁移期双读，不移动原对象）。

    幂等：已登记则直接返回；不覆盖 `documents.file_key`。
    """
    existing = db.scalar(
        select(DocumentAsset).where(
            DocumentAsset.document_id == document.id,
            DocumentAsset.role == "original",
        )
    )
    if existing is not None:
        return existing
    store = storage or get_storage_client()
    try:
        data = store.get(document.file_key)
    except Exception:  # noqa: BLE001 - 原件不可读时不伪造登记
        logger.warning("asset.original_unreadable doc=%s", document.id)
        raise NotFoundError("原件不可读")
    asset = DocumentAsset(
        id=new_id(),
        document_id=document.id,
        account_id=document.account_id,
        subject_id=document.subject_id,
        role="original",
        version=1,
        object_key=document.file_key,
        mime_type=document.mime_type,
        size_bytes=document.size_bytes,
        sha256=document.content_hash,
        source_hash=document.content_hash,
        generator="upload/1.0",
    )
    db.add(asset)
    db.flush()
    return asset
