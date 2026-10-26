"""迁移 0005：`*_ABS` → `*_CNT` 合并 + 页面元信息停用。

真实缺陷背景（2026-10-23）：字典早期缺五分类计数组，AI 兜底新建了
`NEUT_ABS`/`LYMPH_ABS`/… ；后来补齐 `*_CNT` 后，旧条目仍 active 且别名
完全重复，导致「中性粒细胞#」命中的是 `NEUT_ABS` 而非 `NEUT_CNT`。
"""

import importlib.util
import json
from pathlib import Path

import pytest
import sqlalchemy as sa

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0005_merge_cell_diff_abs.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("mig0005", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeOp:
    """最小 op 替身：把 get_bind() 换成我们的连接。"""

    def __init__(self, conn):
        self._conn = conn

    def get_bind(self):
        return self._conn


@pytest.fixture()
def conn():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE indicator_dictionary ("
                " id TEXT PRIMARY KEY, code TEXT UNIQUE, name_cn TEXT, unit TEXT,"
                " aliases JSON, active BOOLEAN DEFAULT 1)"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TABLE observations ("
                " id TEXT PRIMARY KEY, dictionary_id TEXT, code TEXT)"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TABLE indicator_ref_ranges ("
                " id TEXT PRIMARY KEY, dictionary_id TEXT, version INTEGER)"
            )
        )
        yield connection


def _insert_dict(conn, code, name_cn, aliases, active=True):
    conn.execute(
        sa.text(
            "INSERT INTO indicator_dictionary (id, code, name_cn, unit, aliases, active)"
            " VALUES (:id, :code, :name, '10^9/L', :aliases, :active)"
        ),
        {"id": f"id-{code}", "code": code, "name": name_cn,
         "aliases": json.dumps(aliases, ensure_ascii=False), "active": active},
    )


def _read_aliases(conn, code):
    raw = conn.execute(
        sa.text("SELECT aliases FROM indicator_dictionary WHERE code=:c"), {"c": code}
    ).scalar()
    if isinstance(raw, str):
        return json.loads(raw)
    return list(raw or [])


def test_legacy_abs_merged_into_cnt(conn, monkeypatch):
    _insert_dict(conn, "NEUT_ABS", "中性粒细胞计数",
                 ["中性粒细胞#", "中性粒细胞绝对值", "中性粒细胞计数"])
    _insert_dict(conn, "NEUT_CNT", "中性粒细胞计数",
                 ["中性粒细胞#", "中性粒细胞计数", "NEUT#"])
    conn.execute(
        sa.text("INSERT INTO observations (id, dictionary_id, code) VALUES ('o1','id-NEUT_ABS','NEUT_ABS')")
    )
    conn.execute(
        sa.text("INSERT INTO indicator_ref_ranges (id, dictionary_id, version) VALUES ('r1','id-NEUT_ABS',1)")
    )

    mod = _load_migration()
    monkeypatch.setattr(mod.op, "get_bind", lambda: conn)
    mod.upgrade()

    # 观测改指规范条目
    row = conn.execute(
        sa.text("SELECT dictionary_id, code FROM observations WHERE id='o1'")
    ).one()
    assert row == ("id-NEUT_CNT", "NEUT_CNT")
    # 参考区间也改指
    rr = conn.execute(
        sa.text("SELECT dictionary_id FROM indicator_ref_ranges WHERE id='r1'")
    ).scalar()
    assert rr == "id-NEUT_CNT"
    # 旧条目停用但保留行（审计不可丢）
    old_active = conn.execute(
        sa.text("SELECT active FROM indicator_dictionary WHERE code='NEUT_ABS'")
    ).scalar()
    assert not old_active
    assert conn.execute(
        sa.text("SELECT COUNT(*) FROM indicator_dictionary WHERE code='NEUT_ABS'")
    ).scalar() == 1


def test_aliases_merged_without_duplicates(conn, monkeypatch):
    _insert_dict(conn, "NEUT_ABS", "中性粒细胞计数",
                 ["中性粒细胞#", "中性粒细胞绝对值"])
    _insert_dict(conn, "NEUT_CNT", "中性粒细胞计数", ["中性粒细胞#"])

    mod = _load_migration()
    monkeypatch.setattr(mod.op, "get_bind", lambda: conn)
    mod.upgrade()

    aliases = _read_aliases(conn, "NEUT_CNT")
    assert "中性粒细胞绝对值" in aliases     # 新别名并入
    assert aliases.count("中性粒细胞#") == 1  # 不重复


def test_page_meta_indicator_deactivated(conn, monkeypatch):
    _insert_dict(conn, "REPORT_PAGE_TEXT", "报告页码文本", ["第1页 共"])

    mod = _load_migration()
    monkeypatch.setattr(mod.op, "get_bind", lambda: conn)
    mod.upgrade()

    active = conn.execute(
        sa.text("SELECT active FROM indicator_dictionary WHERE code='REPORT_PAGE_TEXT'")
    ).scalar()
    assert not active
    # 不物理删除
    assert conn.execute(
        sa.text("SELECT COUNT(*) FROM indicator_dictionary WHERE code='REPORT_PAGE_TEXT'")
    ).scalar() == 1


def test_skips_when_canonical_missing(conn, monkeypatch):
    """目标 `*_CNT` 不存在时不动，避免产生"观测指向不存在字典"的半个状态。"""
    _insert_dict(conn, "NEUT_ABS", "中性粒细胞计数", ["中性粒细胞#"])
    conn.execute(
        sa.text("INSERT INTO observations (id, dictionary_id, code) VALUES ('o1','id-NEUT_ABS','NEUT_ABS')")
    )

    mod = _load_migration()
    monkeypatch.setattr(mod.op, "get_bind", lambda: conn)
    mod.upgrade()

    row = conn.execute(
        sa.text("SELECT dictionary_id, code FROM observations WHERE id='o1'")
    ).one()
    assert row == ("id-NEUT_ABS", "NEUT_ABS")
    assert conn.execute(
        sa.text("SELECT active FROM indicator_dictionary WHERE code='NEUT_ABS'")
    ).scalar()


def test_idempotent(conn, monkeypatch):
    """重复执行不报错、不扩大影响。"""
    _insert_dict(conn, "NEUT_ABS", "中性粒细胞计数", ["中性粒细胞#"])
    _insert_dict(conn, "NEUT_CNT", "中性粒细胞计数", ["中性粒细胞#"])

    mod = _load_migration()
    monkeypatch.setattr(mod.op, "get_bind", lambda: conn)
    mod.upgrade()
    mod.upgrade()  # 第二次不应抛错

    assert not conn.execute(
        sa.text("SELECT active FROM indicator_dictionary WHERE code='NEUT_ABS'")
    ).scalar()


def test_no_legacy_rows_is_noop(conn, monkeypatch):
    """干净部署（无 * _ABS / REPORT_PAGE_TEXT）不应报错。"""
    _insert_dict(conn, "NEUT_CNT", "中性粒细胞计数", ["中性粒细胞#"])

    mod = _load_migration()
    monkeypatch.setattr(mod.op, "get_bind", lambda: conn)
    mod.upgrade()

    assert conn.execute(
        sa.text("SELECT active FROM indicator_dictionary WHERE code='NEUT_CNT'")
    ).scalar()
