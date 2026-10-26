"""迁移 0006：重放 `*_ABS` → `*_CNT` 的别名合并（0005 成果被 seed 回冲后的补救）。"""

import importlib.util
import json
from pathlib import Path

import pytest
import sqlalchemy as sa

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0006_remerge_cell_diff_aliases.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("mig0006", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def conn():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE indicator_dictionary ("
                " id TEXT PRIMARY KEY, code TEXT UNIQUE, aliases JSON, active BOOLEAN DEFAULT 1)"
            )
        )
        yield connection


def _insert(conn, code, aliases, active=True):
    conn.execute(
        sa.text(
            "INSERT INTO indicator_dictionary (id, code, aliases, active)"
            " VALUES (:id, :code, :a, :active)"
        ),
        {"id": f"id-{code}", "code": code,
         "a": json.dumps(aliases, ensure_ascii=False), "active": active},
    )


def _aliases(conn, code):
    raw = conn.execute(
        sa.text("SELECT aliases FROM indicator_dictionary WHERE code=:c"), {"c": code}
    ).scalar()
    return json.loads(raw) if isinstance(raw, str) else list(raw or [])


def test_restores_lost_aliases(conn, monkeypatch):
    """0005 合并后被 seed 覆盖的情形：这里要把丢失的别名补回来。"""
    _insert(conn, "LYMPH_ABS", ["淋巴细胞绝对值", "淋巴细胞数"], active=False)
    _insert(conn, "LYMPH_CNT", ["淋巴细胞#", "淋巴细胞计数"])   # 已被回冲

    mod = _load()
    monkeypatch.setattr(mod.op, "get_bind", lambda: conn)
    mod.upgrade()

    aliases = _aliases(conn, "LYMPH_CNT")
    assert "淋巴细胞绝对值" in aliases
    assert "淋巴细胞数" in aliases
    assert "淋巴细胞计数" in aliases        # 原有保留
    assert len(aliases) == len(set(aliases))


def test_blocks_page_meta_aliases(conn, monkeypatch):
    """排版碎片不能借合并进入字典。"""
    _insert(conn, "NEUT_ABS", ["中性粒细胞#", "第1页 共"], active=False)
    _insert(conn, "NEUT_CNT", ["中性粒细胞#"])

    mod = _load()
    monkeypatch.setattr(mod.op, "get_bind", lambda: conn)
    mod.upgrade()

    aliases = _aliases(conn, "NEUT_CNT")
    assert "第1页 共" not in aliases


def test_idempotent(conn, monkeypatch):
    _insert(conn, "MONO_ABS", ["单核细胞绝对值"], active=False)
    _insert(conn, "MONO_CNT", ["单核细胞#"])

    mod = _load()
    monkeypatch.setattr(mod.op, "get_bind", lambda: conn)
    mod.upgrade()
    first = _aliases(conn, "MONO_CNT")
    mod.upgrade()
    assert _aliases(conn, "MONO_CNT") == first


def test_skips_when_canonical_missing(conn, monkeypatch):
    _insert(conn, "EOS_ABS", ["嗜酸性粒细胞#"], active=False)

    mod = _load()
    monkeypatch.setattr(mod.op, "get_bind", lambda: conn)
    mod.upgrade()   # 不应抛异常

    assert _aliases(conn, "EOS_ABS") == ["嗜酸性粒细胞#"]


def test_deactivates_legacy_again(conn, monkeypatch):
    """幂等兜底：即使旧条目被误启用，也应重新停用。"""
    _insert(conn, "BASO_ABS", ["嗜碱性粒细胞#"], active=True)
    _insert(conn, "BASO_CNT", ["嗜碱性粒细胞#"])

    mod = _load()
    monkeypatch.setattr(mod.op, "get_bind", lambda: conn)
    mod.upgrade()

    assert not conn.execute(
        sa.text("SELECT active FROM indicator_dictionary WHERE code='BASO_ABS'")
    ).scalar()
