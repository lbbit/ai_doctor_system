"""时间线/趋势 API 测试（AC-7.1~7.3、AC-8.1~8.3）。"""

from datetime import date, timedelta

from app.common.ids import new_id
from app.db.models import (
    Document,
    IndicatorDictionary,
    MedicalEvent,
    Observation,
)


def _seed(db, account_id, subject_id):
    today = date.today()
    events = [
        ("visit", today - timedelta(days=2), "门诊复查"),
        ("checkup", today - timedelta(days=40), "年度体检"),
        ("medication", today - timedelta(days=400), "开始服药"),
    ]
    for etype, occurred, title in events:
        db.add(
            MedicalEvent(
                id=new_id(), account_id=account_id, subject_id=subject_id,
                event_type=etype, occurred_at=occurred, title=title,
            )
        )
    d = IndicatorDictionary(
        id=new_id(), code="GLU_FASTING", name_cn="空腹血糖", category="生化",
        unit="mmol/L", aliases=["血糖"], is_top_trend=True, active=True,
    )
    db.add(d)
    db.flush()
    for i, val in enumerate([5.5, 6.2, 7.0]):
        db.add(
            Observation(
                id=new_id(), account_id=account_id, subject_id=subject_id,
                dictionary_id=d.id, code="GLU_FASTING", display_name="空腹血糖",
                value_num=val, unit="mmol/L",
                occurred_at=today - timedelta(days=60 - i * 30),
                is_confirmed=True,
            )
        )
    db.commit()


def test_timeline_grouped_desc(client, auth, db):
    """AC-7.1 按 occurredAt 倒序分组（年/月）。"""
    _seed(db, auth["account"]["id"], auth["subjectId"])
    resp = client.get(f"/api/timeline/events?subjectId={auth['subjectId']}",
                      headers=auth["headers"])
    assert resp.status_code == 200
    groups = resp.json()["data"]["groups"]
    assert groups
    dates = [i["occurredAt"] for g in groups for i in g["items"]]
    assert dates == sorted(dates, reverse=True)
    years = [g["year"] for g in groups]
    assert years == sorted(years, reverse=True)


def test_timeline_filter_type(client, auth, db):
    """AC-7.2 筛选事件类型。"""
    _seed(db, auth["account"]["id"], auth["subjectId"])
    resp = client.get(
        f"/api/timeline/events?subjectId={auth['subjectId']}&types=visit",
        headers=auth["headers"],
    )
    items = [i for g in resp.json()["data"]["groups"] for i in g["items"]]
    assert items and all(i["eventType"] == "visit" for i in items)


def test_timeline_empty_guided(client, auth):
    """AC-7.3 空档案返回 isEmpty 引导数据（不报错）。"""
    resp = client.get(f"/api/timeline/summary?subjectId={auth['subjectId']}",
                      headers=auth["headers"])
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["isEmpty"] is True
    assert data["eventCount"] == 0
    assert data["documentCount"] == 0


def test_summary_counts(client, auth, db):
    _seed(db, auth["account"]["id"], auth["subjectId"])
    resp = client.get(f"/api/timeline/summary?subjectId={auth['subjectId']}",
                      headers=auth["headers"])
    data = resp.json()["data"]
    assert data["eventCount"] == 3
    assert data["typeCounts"]["visit"] == 1
    assert data["isEmpty"] is False


def test_trend_sql_deterministic(client, auth, db):
    """AC-8.1 SQL 聚合；AC-8.2 refBand/unit/横轴；序列升序。"""
    _seed(db, auth["account"]["id"], auth["subjectId"])
    resp = client.get(
        f"/api/observations/trend?subjectId={auth['subjectId']}&code=GLU_FASTING",
        headers=auth["headers"],
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["name"] == "空腹血糖"
    assert data["unit"] == "mmol/L"
    assert len(data["points"]) == 3
    values = [p["valueNum"] for p in data["points"]]
    assert values == sorted(values)
    assert data["insufficient"] is False


def test_trend_insufficient(client, auth, db):
    """AC-8.3 仅 1 条数据 → insufficient 提示。"""
    db.add(
        Observation(
            id=new_id(), account_id=auth["account"]["id"],
            subject_id=auth["subjectId"],
            code="HBA1C", display_name="糖化", value_num=6.1,
            occurred_at=date.today(), is_confirmed=True,
        )
    )
    db.commit()
    resp = client.get(
        f"/api/observations/trend?subjectId={auth['subjectId']}&code=HBA1C",
        headers=auth["headers"],
    )
    data = resp.json()["data"]
    assert data["insufficient"] is True


def test_trend_options(client, auth, db):
    _seed(db, auth["account"]["id"], auth["subjectId"])
    resp = client.get("/api/dictionary/trend-options", headers=auth["headers"])
    assert resp.status_code == 200
    codes = [o["code"] for o in resp.json()["data"]]
    assert "GLU_FASTING" in codes  # is_top_trend 标记生效


def test_observations_list(client, auth, db):
    _seed(db, auth["account"]["id"], auth["subjectId"])
    resp = client.get(
        f"/api/observations?subjectId={auth['subjectId']}&code=GLU_FASTING",
        headers=auth["headers"],
    )
    data = resp.json()["data"]
    assert data["total"] == 3
