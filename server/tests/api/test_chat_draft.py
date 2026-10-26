"""对话式草稿测试（AC-12.1~12.4、A-8 先草稿后入库）。"""

import json
from datetime import date

from app.common.ids import new_id
from app.db.models import AuditLog, MedicalEvent, MessageDraft, Observation

DRAFT_JSON = json.dumps(
    {
        "reply": "好的，已为您生成记录卡片，确认后入库。",
        "has_draft": True,
        "draft": {
            "event": {
                "type": "self_reported",
                "title": "头晕",
                "narrative": "今天自述头晕",
                "occurred_at_expression": "今天",
                "confidence": 0.85,
            },
            "observations": [
                {
                    "display_name": "血压",
                    "value": "150/95",
                    "unit": "mmHg",
                    "occurred_at_expression": "今天",
                    "confidence": 0.9,
                }
            ],
        },
    },
    ensure_ascii=False,
)


def parse_sse(text: str) -> list:
    events, cur_event, cur_data = [], None, []
    for line in text.splitlines():
        if line.startswith("event: "):
            cur_event, cur_data = line[7:], []
        elif line.startswith("data: "):
            cur_data.append(line[6:])
        elif line == "" and cur_event:
            try:
                events.append((cur_event, json.loads("\n".join(cur_data))))
            except json.JSONDecodeError:
                events.append((cur_event, {}))
            cur_event, cur_data = None, []
    return events


def _smart_llm(messages, **kwargs):
    """主回答给普通文本；草稿抽取（has_draft 提示词）给 JSON。"""
    first = str(messages[0]["content"])
    if "has_draft" in first:
        return DRAFT_JSON
    return "已了解，请查看下方记录卡片。"


def test_draft_extract_pending_not_in_timeline(client, auth, db, fake_llm):
    """AC-12.1/12.3 草稿卡片 pending 不进入时间线；SSE draft 事件。"""
    fake_llm(_smart_llm)
    conv = client.post("/api/chat/conversations", headers=auth["headers"],
                       json={"subjectId": auth["subjectId"]}).json()["data"]
    resp = client.post(f"/api/chat/conversations/{conv['id']}/messages",
                       headers=auth["headers"],
                       json={"content": "帮我记一下：我今天说头晕，血压 150/95"})
    events = parse_sse(resp.text)
    names = [e for e, _ in events]
    assert "draft" in names
    draft_ev = next(d for e, d in events if e == "draft")
    assert draft_ev["subjectId"] == auth["subjectId"]
    assert draft_ev["subjectName"]  # AC-12.1 含 Subject 头像信息
    assert draft_ev["draftType"] == "both"
    assert draft_ev["status"] == "pending"

    # AC-12.3 未确认草稿不进入时间线
    tl = client.get(f"/api/timeline/events?subjectId={auth['subjectId']}",
                    headers=auth["headers"])
    assert tl.json()["data"]["total"] == 0

    db.expire_all()
    draft = db.get(MessageDraft, draft_ev["id"])
    assert draft.status == "pending"
    # AC-12.4 occurredAt "今天" → 当日
    assert draft.payload["event"]["occurredAt"] == date.today().isoformat()


def test_draft_confirm_creates_chat_sourced_rows(client, auth, db, fake_llm):
    """AC-12.2 确认入库（编辑后以编辑值为准）；recordedAt=now。"""
    fake_llm(_smart_llm)
    conv = client.post("/api/chat/conversations", headers=auth["headers"],
                       json={"subjectId": auth["subjectId"]}).json()["data"]
    resp = client.post(f"/api/chat/conversations/{conv['id']}/messages",
                       headers=auth["headers"],
                       json={"content": "帮我记一下：我今天头晕，血压 150/95"})
    draft_ev = next(d for e, d in parse_sse(resp.text) if e == "draft")

    # 编辑后入库：以编辑值为准
    payload = draft_ev["payload"]
    payload["event"]["title"] = "编辑后的头晕记录"
    conf = client.post(f"/api/chat/drafts/{draft_ev['id']}/confirm",
                       headers=auth["headers"], json={"payload": payload})
    assert conf.status_code == 200, conf.text
    data = conf.json()["data"]
    assert data["eventId"]
    assert len(data["observationIds"]) == 1

    db.expire_all()
    ev = db.get(MedicalEvent, data["eventId"])
    assert ev.title == "编辑后的头晕记录"
    assert ev.event_source == "chat"
    assert ev.occurred_at == date.today()
    obs = db.get(Observation, data["observationIds"][0])
    assert obs.display_name == "血压"
    assert obs.value_text == "150/95"
    assert obs.event_id == ev.id
    draft = db.get(MessageDraft, draft_ev["id"])
    assert draft.status == "confirmed"

    # 时间线即时可见
    tl = client.get(f"/api/timeline/events?subjectId={auth['subjectId']}",
                    headers=auth["headers"])
    assert tl.json()["data"]["total"] == 1

    # 审计
    assert db.query(AuditLog).filter_by(action="draft.confirm").count() >= 1

    # 二次确认 409（幂等）
    again = client.post(f"/api/chat/drafts/{draft_ev['id']}/confirm",
                        headers=auth["headers"], json={"payload": payload})
    assert again.status_code == 409


def test_draft_discard_no_orphan(client, auth, db, fake_llm):
    """AC-12.3 丢弃草稿不产生孤儿数据。"""
    fake_llm(_smart_llm)
    conv = client.post("/api/chat/conversations", headers=auth["headers"],
                       json={"subjectId": auth["subjectId"]}).json()["data"]
    resp = client.post(f"/api/chat/conversations/{conv['id']}/messages",
                       headers=auth["headers"],
                       json={"content": "帮我记一下：今天头晕血压高"})
    draft_ev = next(d for e, d in parse_sse(resp.text) if e == "draft")

    out = client.post(f"/api/chat/drafts/{draft_ev['id']}/discard",
                      headers=auth["headers"])
    assert out.status_code == 200
    assert out.json()["data"]["status"] == "discarded"

    db.expire_all()
    assert db.query(MedicalEvent).count() == 0
    assert db.query(Observation).count() == 0
    draft = db.get(MessageDraft, draft_ev["id"])
    assert draft.status == "discarded"
    assert draft.discarded_at is not None

    # 丢弃后不可再确认
    again = client.post(f"/api/chat/drafts/{draft_ev['id']}/confirm",
                        headers=auth["headers"], json={})
    assert again.status_code == 409


def test_no_draft_without_intent(client, auth, db, fake_llm):
    """无意图消息不产生草稿。"""
    fake_llm(_smart_llm)
    conv = client.post("/api/chat/conversations", headers=auth["headers"],
                       json={"subjectId": auth["subjectId"]}).json()["data"]
    resp = client.post(f"/api/chat/conversations/{conv['id']}/messages",
                       headers=auth["headers"], json={"content": "今天天气如何"})
    events = parse_sse(resp.text)
    assert "draft" not in [e for e, _ in events]
    db.expire_all()
    assert db.query(MessageDraft).count() == 0
