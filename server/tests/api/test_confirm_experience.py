"""确认入库体验：按名称修正后应重新映射字典；重抽后应给出与上一版差异。

真实使用场景：
- 表格/OCR 抽到的名字有偏差（如「红细胞比积」应为「红细胞压积」），用户希望
  在确认前直接改名，改完后应带出标准 code 与标准名，而不是继续「字典未命中」；
- 重新抽取后用户想快速判断“这版比上版多/少了什么”。
"""

from app.common.ids import new_id
from app.db.models import DerivedRecord, Document
from app.data.dictionary_seed import ensure_seeded


def _seed_doc(db, account_id, subject_id, payload, version=1):
    import hashlib

    doc = Document(
        id=new_id(), account_id=account_id, subject_id=subject_id,
        kind="PDF", status="awaiting_review", file_key="objects/x",
        file_name="lab.pdf", mime_type="application/pdf", size_bytes=100,
        content_hash=hashlib.sha256(new_id().encode()).hexdigest(),
        doc_type="lab_report", review_version=0,
    )
    db.add(doc)
    db.flush()
    dr = DerivedRecord(
        id=new_id(), document_id=doc.id, subject_id=subject_id,
        version=version, payload=payload, parser="table",
    )
    db.add(dr)
    db.commit()
    return doc, dr


def _obs(name, value=1.0, code=None, flag="N", conf=0.92):
    return {
        "raw_name": name,
        "display_name": name,
        "code": code,
        "dictionary_id": None,
        "value": value,
        "unit": None,
        "ref_low": None,
        "ref_high": None,
        "ref_text": None,
        "abnormal_flag": flag,
        "occurred_at": "2026-03-12",
        "confidence": conf,
        "bbox": None,
        "status": "proposed",
    }


def test_correct_name_remaps_dictionary(client, auth, db):
    """修正指标名后应命中字典，写入标准 code 与标准名。"""
    ensure_seeded(db)
    payload = {
        "report_date": "2026-03-12",
        "doc_type_suggest": "lab_report",
        "events": [],
        "observations": [_obs("红细胞比积", value=38.8, flag="L")],
        "mock": False,
    }
    doc, dr = _seed_doc(db, auth["account"]["id"], auth["subjectId"], payload)

    resp = client.post(
        f"/api/documents/{doc.id}/derived-record/confirm",
        headers=auth["headers"],
        json={
            "version": 1,
            "decisions": [
                {
                    "fieldRef": "obs:0",
                    "action": "correct",
                    "value": {"display_name": "红细胞压积"},
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    obs = resp.json()["data"]["observations"][0]
    assert obs["code"] == "HCT"
    assert obs["displayName"] == "红细胞压积"


def test_correct_name_keeps_unmapped_when_unknown(client, auth, db):
    """改成字典里也没有的名字时，保留用户输入并标记未命中（不静默丢弃）。"""
    ensure_seeded(db)
    payload = {
        "report_date": "2026-03-12",
        "doc_type_suggest": "lab_report",
        "events": [],
        "observations": [_obs("医院自建项目", value=1.0)],
        "mock": False,
    }
    doc, dr = _seed_doc(db, auth["account"]["id"], auth["subjectId"], payload)

    resp = client.post(
        f"/api/documents/{doc.id}/derived-record/confirm",
        headers=auth["headers"],
        json={
            "version": 1,
            "decisions": [
                {"fieldRef": "obs:0", "action": "correct", "value": {"display_name": "自费加项A"}}
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    obs = resp.json()["data"]["observations"][0]
    assert obs["displayName"] == "自费加项A"


def test_get_derived_record_reports_diff_from_previous_version(client, auth, db):
    """重新抽取后返回与上一版的差异，便于用户判断结果是否变好。"""
    from app.common.ids import new_id as nid

    prev = {
        "report_date": "2026-03-12",
        "doc_type_suggest": "lab_report",
        "events": [],
        "observations": [
            _obs("血小板计数", value=69.0, flag="L"),
            _obs("旧指标", value=1.0),
        ],
        "mock": False,
    }
    curr = {
        "report_date": "2026-03-12",
        "doc_type_suggest": "lab_report",
        "events": [],
        "observations": [
            _obs("血小板计数", value=93.0, flag="L"),
            _obs("总胆固醇", value=5.94, flag="H"),
        ],
        "mock": False,
    }
    doc, _ = _seed_doc(db, auth["account"]["id"], auth["subjectId"], prev, version=1)
    db.add(
        DerivedRecord(
            id=nid(), document_id=doc.id, subject_id=auth["subjectId"],
            version=2, payload=curr, parser="table",
        )
    )
    db.commit()

    resp = client.get(f"/api/documents/{doc.id}/derived-record",
                      headers=auth["headers"])
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["version"] == 2
    diff = data["comparison"]
    assert diff["previousVersion"] == 1
    assert diff["added"] == 1        # 总胆固醇
    assert diff["removed"] == 1      # 旧指标
    assert diff["changed"] == 1      # 血小板计数 值变化
    assert diff["addedNames"] == ["总胆固醇"]
    assert diff["removedNames"] == ["旧指标"]


def test_get_derived_record_without_previous_has_no_comparison(client, auth, db):
    payload = {
        "report_date": "2026-03-12",
        "doc_type_suggest": "lab_report",
        "events": [],
        "observations": [_obs("身高", value=180.5, code="HGT")],
        "mock": False,
    }
    doc, _ = _seed_doc(db, auth["account"]["id"], auth["subjectId"], payload)
    resp = client.get(f"/api/documents/{doc.id}/derived-record",
                      headers=auth["headers"])
    assert resp.status_code == 200
    assert resp.json()["data"]["comparison"] is None
