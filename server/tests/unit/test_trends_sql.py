"""趋势 SQL 聚合单测（AC-8.1/8.3：确定性、insufficient、序列有序）。"""

from datetime import date, timedelta

from app.common.ids import new_id
from app.db.models import (
    Account,
    IndicatorDictionary,
    Observation,
    Subject,
)
from app.observations.trends import trend_series


def _seed(db, points):
    account = Account(id=new_id(), email="t@example.com", password_hash="x")
    subject = Subject(id=new_id(), account_id=account.id, name="本人", relation="本人")
    d = IndicatorDictionary(
        id=new_id(), code="BP_SYS", name_cn="收缩压", category="血压",
        unit="mmHg", aliases=["高压"], is_top_trend=True, active=True,
    )
    db.add_all([account, subject, d])
    db.flush()
    for i, (day_offset, val) in enumerate(points):
        db.add(
            Observation(
                id=new_id(), account_id=account.id, subject_id=subject.id,
                dictionary_id=d.id, code="BP_SYS", display_name="收缩压",
                value_num=val, unit="mmHg",
                occurred_at=date.today() - timedelta(days=day_offset),
                is_confirmed=True,
            )
        )
    db.commit()
    return account, subject


def test_series_sorted_asc(db):
    account, subject = _seed(db, [(60, 130), (10, 150), (30, 140)])
    data = trend_series(db, account, subject.id, "BP_SYS")
    dates = [p["occurredAt"] for p in data["points"]]
    assert dates == sorted(dates)
    assert [p["valueNum"] for p in data["points"]] == [130.0, 140.0, 150.0]
    assert data["insufficient"] is False
    assert data["unit"] == "mmHg"
    assert data["refBand"] is None or "low" in data["refBand"]


def test_insufficient_single_point(db):
    account, subject = _seed(db, [(5, 130)])
    data = trend_series(db, account, subject.id, "BP_SYS")
    assert data["insufficient"] is True  # AC-8.3 数据不足成线
    assert len(data["points"]) == 1


def test_empty_code_returns_insufficient(db):
    account, subject = _seed(db, [])
    data = trend_series(db, account, subject.id, "BP_SYS")
    assert data["points"] == []
    assert data["insufficient"] is True


def test_limit_recent_n(db):
    points = [(i, 120 + i) for i in range(30, 0, -1)]
    account, subject = _seed(db, points)
    data = trend_series(db, account, subject.id, "BP_SYS", limit=5)
    assert len(data["points"]) == 5
    # 近 N 次：倒序取最新后正序
    assert data["points"][-1]["occurredAt"] >= data["points"][0]["occurredAt"]
