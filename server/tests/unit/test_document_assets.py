"""资产版本化与完整性（R1 T102）。

不变量（被测）：
1. 派生资产**追加版本**，不覆盖旧版（旧版要能对比）；
2. `role='original'` 不可通过派生接口写入（原件是证据）；
3. 写入后 hash 与实际内容一致；内容被篡改时读取必须失败（不得当作可信副本）；
4. 源文件哈希变化 → 老副本标 `stale`（提示该重抽），但**不删除**；
5. 读取必须做归属校验（跨 Account/Subject → 404）与生命周期校验（软删 → 404）；
6. 幂等：原件资产登记重复执行不产生第二行。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.common.errors import ConflictError, NotFoundError
from app.db.models import Document, DocumentAsset
from app.documents import assets as asset_service
from tests.conftest import make_png_bytes


def _upload(client, auth, salt=b""):
    resp = client.post(
        "/api/documents",
        headers=auth["headers"],
        data={"subjectId": auth["subjectId"]},
        files={"file": ("a.png", make_png_bytes(salt), "image/png")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["document"]


def _doc(db, doc_id):
    db.expire_all()
    return db.get(Document, doc_id)


def test_put_derivative_appends_version(client, auth, db):
    """两次写入派生资产 → 版本 1、2，旧版不被覆盖。"""
    doc = _upload(client, auth)
    row = _doc(db, doc["id"])

    a1 = asset_service.put_derivative(
        db, row, role="markdown", data="# 第一版".encode("utf-8"),
        mime_type="text/markdown", generator="markdown/1.0",
    )
    a2 = asset_service.put_derivative(
        db, row, role="markdown", data="# 第二版".encode("utf-8"),
        mime_type="text/markdown", generator="markdown/1.0",
    )
    db.commit()

    assert a1.version == 1
    assert a2.version == 2
    assert a1.object_key != a2.object_key

    listed = asset_service.list_assets(db, row, role="markdown")
    assert [a.version for a in listed] == [1, 2]


def test_original_role_cannot_be_written_as_derivative(client, auth, db):
    """原件不可经派生接口写入。"""
    doc = _upload(client, auth)
    row = _doc(db, doc["id"])
    with pytest.raises(ConflictError):
        asset_service.put_derivative(
            db, row, role="original", data=b"x", mime_type="application/octet-stream",
            generator="test",
        )


def test_asset_hash_matches_content(client, auth, db):
    """登记的 sha256 必须等于实际内容哈希。"""
    doc = _upload(client, auth)
    row = _doc(db, doc["id"])
    payload = "# 副本内容".encode("utf-8")
    asset = asset_service.put_derivative(
        db, row, role="markdown", data=payload, mime_type="text/markdown",
        generator="markdown/1.0",
    )
    db.commit()
    assert asset.sha256 == asset_service.sha256_bytes(payload)
    assert asset.size_bytes == len(payload)


def test_read_asset_detects_tampering(client, auth, db, monkeypatch):
    """内容被篡改 → 读取失败，不得当作可信副本返回。"""
    doc = _upload(client, auth)
    row = _doc(db, doc["id"])
    asset = asset_service.put_derivative(
        db, row, role="markdown", data="可信内容".encode("utf-8"),
        mime_type="text/markdown", generator="markdown/1.0",
    )
    db.commit()

    # 篡改对象存储中的内容（不改 DB 里的 sha256）
    from app.documents import storage as storage_mod

    store = storage_mod.get_storage_client()
    original_get = store.get

    def _tampered_get(key):
        if key == asset.object_key:
            return "被篡改的内容".encode("utf-8")
        return original_get(key)

    monkeypatch.setattr(store, "get", _tampered_get)
    with pytest.raises(NotFoundError):
        asset_service.read_asset(db, row, "markdown")


def test_stale_flag_when_source_hash_changes(client, auth, db):
    """源文件哈希变化 → 老副本标 stale，但**不删除**。"""
    doc = _upload(client, auth)
    row = _doc(db, doc["id"])
    asset = asset_service.put_derivative(
        db, row, role="markdown", data=b"v1", mime_type="text/markdown",
        generator="markdown/1.0",
    )
    db.commit()
    assert asset_service.is_stale(asset, row) is False

    # 模拟源文件变化（原件被替换）
    row.content_hash = "different-hash"
    db.flush()
    assert asset_service.is_stale(asset, row) is True
    # 老副本仍在
    assert len(asset_service.list_assets(db, row, role="markdown")) == 1


def test_read_asset_cross_account_404(client, auth, second_auth, db):
    """跨 Account 读取资产 → 404。"""
    doc = _upload(client, auth)
    row = _doc(db, doc["id"])
    asset_service.put_derivative(
        db, row, role="markdown", data=b"x", mime_type="text/markdown",
        generator="markdown/1.0",
    )
    db.commit()

    resp = client.get(
        f"/api/documents/{doc['id']}/assets/markdown", headers=second_auth["headers"]
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


def test_read_asset_soft_deleted_404(client, auth, db):
    """软删文档的资产 → 404。"""
    doc = _upload(client, auth)
    row = _doc(db, doc["id"])
    asset_service.put_derivative(
        db, row, role="markdown", data=b"x", mime_type="text/markdown",
        generator="markdown/1.0",
    )
    db.commit()

    row.deleted_at = datetime.now(timezone.utc)
    db.commit()

    resp = client.get(
        f"/api/documents/{doc['id']}/assets/markdown", headers=auth["headers"]
    )
    assert resp.status_code == 404


def test_ensure_original_asset_is_idempotent(client, auth, db):
    """原件资产登记幂等：重复执行不产生第二行。"""
    doc = _upload(client, auth)
    row = _doc(db, doc["id"])
    first = asset_service.ensure_original_asset(db, row)
    db.commit()
    second = asset_service.ensure_original_asset(db, row)
    db.commit()
    assert first.id == second.id
    count = db.query(DocumentAsset).filter_by(
        document_id=doc["id"], role="original"
    ).count()
    assert count == 1


# --------------------------------------------------------------------------
# API：资产列表与内容
# --------------------------------------------------------------------------


def test_assets_api_lists_and_serves(client, auth, db):
    """资产列表与内容接口（签名 URL + 版本信息）。"""
    doc = _upload(client, auth)
    row = _doc(db, doc["id"])
    asset_service.put_derivative(
        db, row, role="markdown", data="# 副本".encode("utf-8"),
        mime_type="text/markdown", generator="markdown/1.0",
    )
    db.commit()

    listing = client.get(f"/api/documents/{doc['id']}/assets", headers=auth["headers"])
    assert listing.status_code == 200
    items = listing.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["role"] == "markdown"
    assert items[0]["version"] == 1
    assert items[0]["stale"] is False

    one = client.get(
        f"/api/documents/{doc['id']}/assets/markdown", headers=auth["headers"]
    )
    assert one.status_code == 200
    data = one.json()["data"]
    assert data["url"]
    assert data["expiresAt"]
    assert data["sha256"]


def test_assets_api_cross_account_404(client, auth, second_auth, db):
    """跨 Account 访问资产 → 404。"""
    doc = _upload(client, auth)
    row = _doc(db, doc["id"])
    asset_service.put_derivative(
        db, row, role="markdown", data=b"x", mime_type="text/markdown",
        generator="markdown/1.0",
    )
    db.commit()

    resp = client.get(
        f"/api/documents/{doc['id']}/assets", headers=second_auth["headers"]
    )
    assert resp.status_code == 404
    resp2 = client.get(
        f"/api/documents/{doc['id']}/assets/markdown", headers=second_auth["headers"]
    )
    assert resp2.status_code == 404
