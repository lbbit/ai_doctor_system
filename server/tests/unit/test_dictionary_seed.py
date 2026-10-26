"""指标字典自动播种（真实环境缺陷：字典为空导致所有指标"字典未命中"）。

复现：生产库 indicator_dictionary 为 0 行时，map_payload 全部走未命中分支，
前端每条指标都显示"字典未命中"，趋势 TOP20 不可用。
"""

from sqlalchemy import func, select

from app.db.models import IndicatorDictionary, IndicatorRefRange


def test_ensure_seeded_populates_dictionary(db):
    from app.data.dictionary_seed import ensure_seeded

    assert db.scalar(select(func.count()).select_from(IndicatorDictionary)) == 0

    stats = ensure_seeded(db)

    assert stats["created"] > 0
    assert stats["alreadyPresent"] is False
    total = db.scalar(select(func.count()).select_from(IndicatorDictionary))
    assert total == stats["total"]
    ranges = db.scalar(select(func.count()).select_from(IndicatorRefRange))
    assert ranges > 0


def test_ensure_seeded_is_idempotent(db):
    from app.data.dictionary_seed import ensure_seeded

    first = ensure_seeded(db)
    second = ensure_seeded(db)

    assert second["alreadyPresent"] is True
    assert second["created"] == 0
    assert db.scalar(select(func.count()).select_from(IndicatorDictionary)) == first["total"]


def test_dictionary_maps_real_report_names(db):
    """真实报告常见名称应能命中字典（不再一律"字典未命中"）。"""
    from app.data.dictionary_seed import ensure_seeded
    from app.preprocessing.mapper import load_dictionary, match_indicator

    ensure_seeded(db)
    entries = load_dictionary(db)

    for name in (
        "总胆固醇",
        "空腹血糖",
        "空腹血葡萄糖",
        "血小板计数",
        "血小板",
        "血红蛋白",
        "低密度脂蛋白胆固醇",
        "体重指数",
        "平均红细胞血红蛋白含量",
    ):
        assert match_indicator(entries, name) is not None, name


def test_seed_merges_aliases_instead_of_overwriting(db):
    """播种的别名必须**合并**，不能覆盖已有的迁移/人工别名。

    真实缺陷（2026-10-23）：迁移 0005 把历史 `*_ABS` 上的别名
    （如「淋巴细胞绝对值」）并入 `LYMPH_CNT`，但启动时 `seed_dictionary`
    用 TOP50 的列表覆盖 aliases，合并成果被回冲掉。
    """
    from app.data.dictionary_seed import ensure_seeded

    ensure_seeded(db)

    added = "淋巴细胞绝对值X"   # 模拟迁移或人工补充的别名
    row = db.scalar(
        select(IndicatorDictionary).where(IndicatorDictionary.code == "LYMPH_CNT")
    )
    assert row is not None
    row.aliases = [*(row.aliases or []), added]
    db.commit()

    ensure_seeded(db)   # 再次播种

    db.refresh(row)
    assert added in (row.aliases or []), row.aliases
    # TOP50 原有别名仍在
    assert "淋巴细胞计数" in (row.aliases or [])


def test_seed_does_not_duplicate_aliases(db):
    """重复播种不得把别名越堆越多。"""
    from app.data.dictionary_seed import ensure_seeded

    ensure_seeded(db)
    row = db.scalar(
        select(IndicatorDictionary).where(IndicatorDictionary.code == "HGB")
    )
    before = list(row.aliases or [])

    ensure_seeded(db)
    ensure_seeded(db)

    db.refresh(row)
    assert len(row.aliases or []) == len(before), row.aliases
    assert len(set(row.aliases or [])) == len(row.aliases or [])
