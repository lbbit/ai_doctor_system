"""问诊 API 测试（AC-9.1~9.4、AC-10.1~10.4、A-1/A-3/A-7、/v1 兼容端点）。"""

import json
from datetime import date

from app.common.ids import new_id
from app.db.models import (
    Conversation,
    Document,
    IndicatorDictionary,
    MedicalEvent,
    Observation,
    RedFlagHit,
    RedFlagRule,
    Trace,
)

def parse_sse(text: str) -> list:
    events = []
    cur_event, cur_data = None, []
    for line in text.splitlines():
        if line.startswith("event: "):
            cur_event = line[7:]
            cur_data = []
        elif line.startswith("data: "):
            cur_data.append(line[6:])
        elif line == "" and cur_event:
            try:
                events.append((cur_event, json.loads("\n".join(cur_data))))
            except json.JSONDecodeError:
                events.append((cur_event, {}))
            cur_event, cur_data = None, []
    return events


def _seed_citable(db, account_id, subject_id) -> str:
    """带原件回链的观测（引用池来源），返回引用标签。"""
    doc = Document(
        id=new_id(), account_id=account_id, subject_id=subject_id,
        kind="PDF", status="confirmed", file_key="objects/t/doc",
        file_name="血糖报告.pdf", mime_type="application/pdf",
        size_bytes=10, content_hash=new_id(),
    )
    d = IndicatorDictionary(
        id=new_id(), code="GLU_FASTING", name_cn="空腹血糖", category="生化",
        unit="mmol/L", aliases=["血糖"], is_top_trend=True, active=True,
    )
    db.add_all([doc, d])
    db.flush()
    obs = Observation(
        id=new_id(), account_id=account_id, subject_id=subject_id,
        dictionary_id=d.id, code="GLU_FASTING", display_name="空腹血糖",
        value_num=7.1, unit="mmol/L", occurred_at=date(2026, 3, 12),
        source_document_id=doc.id, is_confirmed=True,
    )
    db.add(obs)
    db.commit()
    return f"2026-03-12 空腹血糖"


def test_conversation_lifecycle(client, auth):
    """AC-9.1 会话创建绑定 subjectId；关闭→archived。"""
    resp = client.post("/api/chat/conversations", headers=auth["headers"],
                       json={"subjectId": auth["subjectId"], "title": "问诊1"})
    assert resp.status_code == 200
    conv = resp.json()["data"]
    assert conv["subjectId"] == auth["subjectId"]
    assert conv["status"] == "active"

    lst = client.get("/api/chat/conversations", headers=auth["headers"])
    assert any(c["id"] == conv["id"] for c in lst.json()["data"])

    close = client.post(f"/api/chat/conversations/{conv['id']}/close",
                        headers=auth["headers"])
    assert close.json()["data"]["status"] == "archived"
    # 已归档会话不可再发消息
    send = client.post(f"/api/chat/conversations/{conv['id']}/messages",
                       headers=auth["headers"], json={"content": "hi"})
    assert send.status_code == 409


def test_sse_flow_with_citations(client, auth, db, fake_llm):
    """AC-9.2 组装 + AC-10.1/10.3 引用与 Trace；A-3 清单外引用剥除。"""
    label = _seed_citable(db, auth["account"]["id"], auth["subjectId"])
    answer = f"空腹血糖 7.1 偏高，建议复查。[依据：{label}] [依据：档案里没有的东西]"
    fake_llm(answer)

    conv = client.post("/api/chat/conversations", headers=auth["headers"],
                       json={"subjectId": auth["subjectId"]}).json()["data"]
    resp = client.post(f"/api/chat/conversations/{conv['id']}/messages",
                       headers=auth["headers"],
                       json={"content": "血糖最近怎么样"})
    assert resp.status_code == 200
    events = parse_sse(resp.text)
    names = [e for e, _ in events]
    assert names[0] == "meta"
    assert "delta" in names and "citations" in names and "done" in names

    deltas = "".join(d["content"] for e, d in events if e == "delta")
    assert f"[依据：{label}]" in deltas
    assert "档案里没有的东西" not in deltas  # A-3 清单外剥除

    cit = next(d for e, d in events if e == "citations")
    assert cit["traceId"]
    assert len(cit["refs"]) == 1
    assert cit["refs"][0]["documentId"]  # A-3 每引用必有原件 ID

    done = next(d for e, d in events if e == "done")
    trace_id = done["traceId"]
    db.expire_all()
    trace = db.query(Trace).filter_by(message_id=done["messageId"]).one()
    assert trace.id == trace_id
    # L0-L2 组装进 Trace（AC-9.2）
    assert "L0" in trace.context_layers
    assert "L1" in trace.context_layers

    # trace 查询接口（AC-10.3）
    t = client.get(f"/api/chat/messages/{done['messageId']}/trace",
                   headers=auth["headers"])
    assert t.status_code == 200
    tdata = t.json()["data"]
    assert tdata["traceId"] == trace_id
    assert tdata["refs"][0]["label"] == label
    assert tdata["refs"][0]["url"]  # 原件回链（E-9 带过期）


def test_llm_unavailable_no_trace(client, auth, db):
    """AC-9.4/E-5 LLM 不可用：友好 error，无 Trace，message.error_state。"""
    conv = client.post("/api/chat/conversations", headers=auth["headers"],
                       json={"subjectId": auth["subjectId"]}).json()["data"]
    resp = client.post(f"/api/chat/conversations/{conv['id']}/messages",
                       headers=auth["headers"], json={"content": "你好"})
    events = parse_sse(resp.text)
    names = [e for e, _ in events]
    assert "error" in names
    err = next(d for e, d in events if e == "error")
    assert err["code"] in ("LLM_UNAVAILABLE", "LLM_TIMEOUT")
    assert err["message"]
    assert "done" not in names
    db.expire_all()
    assert db.query(Trace).count() == 0  # 失败不产生 Trace
    from app.db.models import Message

    msgs = db.query(Message).filter_by(role="assistant").all()
    assert msgs and msgs[0].error_state in ("unavailable", "timeout")


def test_redflag_inserted_independent_of_model(client, auth, db):
    """A-1/AC-11.1 规则命中：落库+审计+卡片事件（模型失败也必现，E-11）。"""
    rule = RedFlagRule(
        id=new_id(), code="RF_TEST_CHEST", name="测试胸痛",
        keywords=["测试胸痛词"], keyword_logic="any",
        context_logic=None, severity="high",
        message="请立即就医测试", action="refer_now", enabled=True, version=1,
    )
    db.add(rule)
    db.commit()
    from app.redflag import loader

    loader.reload(force=True)

    conv = client.post("/api/chat/conversations", headers=auth["headers"],
                       json={"subjectId": auth["subjectId"]}).json()["data"]
    # LLM 不可用路径：卡片仍必现（E-11）
    resp = client.post(f"/api/chat/conversations/{conv['id']}/messages",
                       headers=auth["headers"],
                       json={"content": "我今天有测试胸痛词"})
    events = parse_sse(resp.text)
    names = [e for e, _ in events]
    assert "redflag" in names  # 失败路径也插入
    rf = next(d for e, d in events if e == "redflag")
    assert rf["code"] == "RF_TEST_CHEST"
    assert rf["severity"] == "high"

    db.expire_all()
    assert db.query(RedFlagHit).count() >= 1
    from app.db.models import AuditLog

    assert db.query(AuditLog).filter_by(action="redflag.hit").count() >= 1


def test_a7_context_subject_isolation(client, auth, db, fake_llm):
    """AC-9.3/A-7 会话历史与上下文只读本 Subject（K4）。"""
    # 另一个档案 + 事件
    sid2 = client.post("/api/subjects", headers=auth["headers"],
                       json={"name": "父亲", "relation": "父亲"}).json()["data"]["id"]
    other_event = MedicalEvent(
        id=new_id(), account_id=auth["account"]["id"], subject_id=sid2,
        event_type="surgery", occurred_at=date.today(), title="他档手术记录",
    )
    db.add(other_event)
    db.commit()

    fake_llm("根据当前档案，暂无相关记录。依据有限：档案中未找到相关内容。")
    conv = client.post("/api/chat/conversations", headers=auth["headers"],
                       json={"subjectId": auth["subjectId"]}).json()["data"]
    resp = client.post(f"/api/chat/conversations/{conv['id']}/messages",
                       headers=auth["headers"], json={"content": "做过什么手术"})
    events = parse_sse(resp.text)
    done = next(d for e, d in events if e == "done")

    trace = db.query(Trace).filter_by(message_id=done["messageId"]).one()
    l2_ids = trace.context_layers.get("L2") or []
    assert other_event.id not in l2_ids  # 他档事件不进本档上下文


def test_openai_compat_endpoint(client, auth, fake_llm, db):
    """§4.3 /v1/chat/completions OpenAI 形态 + 私有扩展字段。"""
    label = _seed_citable(db, auth["account"]["id"], auth["subjectId"])
    fake_llm(f"血糖偏高。[依据：{label}]")

    # 缺 _subject_id → OpenAI 错误形态 422
    bad = client.post(
        "/v1/chat/completions",
        headers=auth["headers"],
        json={"model": "primary", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "SUBJECT_REQUIRED"

    ok = client.post(
        "/v1/chat/completions",
        headers=auth["headers"],
        json={
            "model": "primary",
            "messages": [{"role": "user", "content": "血糖怎么样"}],
            "stream": False,
            "_subject_id": auth["subjectId"],
        },
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["object"] == "chat.completion"
    msg = body["choices"][0]["message"]
    assert msg["role"] == "assistant"
    assert "_trace_id" in msg and "_citations" in msg
    assert msg["_citations"][0]["document_id"]  # A-3 每项必有 document_id


def test_openai_compat_stream(client, auth, fake_llm):
    fake_llm("好的，已查看。")
    resp = client.post(
        "/v1/chat/completions",
        headers=auth["headers"],
        json={
            "model": "primary",
            "messages": [{"role": "user", "content": "你好"}],
            "stream": True,
            "_subject_id": auth["subjectId"],
        },
    )
    assert resp.status_code == 200
    assert "data: [DONE]" in resp.text
    assert "chat.completion.chunk" in resp.text


def test_history_only_same_conversation(client, auth, db, fake_llm):
    """AC-9.3 会话历史只取本会话消息。"""
    llm = fake_llm("收到。")
    conv1 = client.post("/api/chat/conversations", headers=auth["headers"],
                        json={"subjectId": auth["subjectId"]}).json()["data"]
    conv2 = client.post("/api/chat/conversations", headers=auth["headers"],
                        json={"subjectId": auth["subjectId"]}).json()["data"]
    client.post(f"/api/chat/conversations/{conv1['id']}/messages",
                headers=auth["headers"], json={"content": "第一句机密"})
    resp = client.post(f"/api/chat/conversations/{conv2['id']}/messages",
                       headers=auth["headers"], json={"content": "第二句"})
    # llm.calls 记录了发送给 LLM 的 messages：不应包含 conv1 的内容
    sent = llm.calls[-1]
    joined = json.dumps(sent, ensure_ascii=False)
    assert "第一句机密" not in joined
