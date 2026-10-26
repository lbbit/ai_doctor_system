"""纯文本记录与批量导入（R1 T105）。

期望语义：
1. 支持 TXT/Markdown 上传（kind=TEXT），非白名单格式仍 415；
2. 文本文件按内容校验（不可解码的二进制 → 415，不当文本存进来）；
3. 文本文件能被抽取处理并产出 Markdown 副本与页锚点；
4. **批量上传逐项返回结果**：单项失败不回滚已成功的原件；
5. 批量请求必须绑定同一 Subject（防止误传到别的档案）。
"""

from __future__ import annotations

import pytest

from app.db.models import Document, DocumentAsset, ExtractionJob
from app.preprocessing.pipeline import process_job
from tests.conftest import make_png_bytes


def _upload_text(client, auth, text, name="note.txt", mime="text/plain", salt=b""):
    return client.post(
        "/api/documents",
        headers=auth["headers"],
        data={"subjectId": auth["subjectId"]},
        files={"file": (name, text.encode("utf-8") + salt, mime)},
    )


def test_upload_text_file_accepted(client, auth):
    """TXT 上传 → kind=TEXT。"""
    resp = _upload_text(client, auth, "2026-03-12 门诊记录\n主诉：头晕")
    assert resp.status_code == 201, resp.text
    doc = resp.json()["data"]["document"]
    assert doc["kind"] == "TEXT"
    assert doc["mimeType"] == "text/plain"


def test_upload_markdown_accepted(client, auth):
    """Markdown 上传 → kind=TEXT / text/markdown。"""
    resp = _upload_text(
        client, auth, "# 体检报告\n\n结论：正常", name="r.md", mime="text/markdown"
    )
    assert resp.status_code == 201
    doc = resp.json()["data"]["document"]
    assert doc["kind"] == "TEXT"
    assert doc["mimeType"] == "text/markdown"


def test_upload_binary_as_text_rejected(client, auth):
    """二进制内容伪装成 .txt → 415（不当文本存进来）。"""
    resp = client.post(
        "/api/documents",
        headers=auth["headers"],
        data={"subjectId": auth["subjectId"]},
        files={"file": ("bad.txt", b"\x00\xff\xfe\x00\x81\x82", "text/plain")},
    )
    assert resp.status_code == 415
    assert resp.json()["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_still_rejects_unsupported_extension(client, auth):
    """非白名单扩展名仍 415。"""
    resp = client.post(
        "/api/documents",
        headers=auth["headers"],
        data={"subjectId": auth["subjectId"]},
        files={"file": ("a.gif", b"GIF89a....", "image/gif")},
    )
    assert resp.status_code == 415


def test_text_document_gets_processed_and_markdown(client, auth, db):
    """文本文件可被抽取处理并产出 Markdown 副本。"""
    resp = _upload_text(
        client, auth,
        "2026-03-12 门诊记录\n主诉：反复头晕 2 周\n诊断：高血压病 2 级",
    )
    assert resp.status_code == 201
    doc_id = resp.json()["data"]["document"]["id"]

    job = db.query(ExtractionJob).filter_by(document_id=doc_id).one()
    process_job(db, job.id)

    db.expire_all()
    assets = (
        db.query(DocumentAsset)
        .filter_by(document_id=doc_id, role="markdown")
        .all()
    )
    assert assets, "文本记录也应产出 Markdown 副本"

    from app.documents import assets as asset_service

    _, data = asset_service.read_asset(
        db, db.get(Document, doc_id), "markdown"
    )
    text = data.decode("utf-8")
    assert "<!-- source:page=" in text
    assert "头晕" in text


def test_batch_upload_returns_per_item_results(client, auth, db):
    """批量上传逐项返回：单项失败不影响其他项。"""
    resp = client.post(
        "/api/documents/batch",
        headers=auth["headers"],
        data={"subjectId": auth["subjectId"]},
        files=[
            ("files", ("a.png", make_png_bytes(b"a"), "image/png")),
            ("files", ("b.png", make_png_bytes(b"b"), "image/png")),
        ],
    )
    assert resp.status_code in (200, 201), resp.text
    data = resp.json()["data"]
    items = data["items"]
    assert len(items) == 2
    assert all(i["ok"] for i in items)
    assert len({i["document"]["id"] for i in items}) == 2


def test_batch_upload_reports_failure_per_item(client, auth):
    """批量中的坏文件单独报错，其余成功。"""
    resp = client.post(
        "/api/documents/batch",
        headers=auth["headers"],
        data={"subjectId": auth["subjectId"]},
        files=[
            ("files", ("good.png", make_png_bytes(b"g"), "image/png")),
            ("files", ("bad.gif", b"GIF89a", "image/gif")),
        ],
    )
    assert resp.status_code in (200, 201), resp.text
    items = resp.json()["data"]["items"]
    assert len(items) == 2
    ok = [i for i in items if i["ok"]]
    bad = [i for i in items if not i["ok"]]
    assert len(ok) == 1 and len(bad) == 1
    assert bad[0]["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_batch_upload_requires_subject(client, auth):
    """批量上传必须绑定档案（写操作强制选档）。"""
    resp = client.post(
        "/api/documents/batch",
        headers=auth["headers"],
        files=[("files", ("a.png", make_png_bytes(b"x"), "image/png"))],
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "SUBJECT_REQUIRED"
