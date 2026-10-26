"""迁移 0007 回填逻辑测试（R0 T004）。

目标：不依赖真实 PostgreSQL，验证"旧 status → 新维度"的回填规则正确，
尤其是**归档行不得被猜成已确认**（needs_reconcile）。

说明：仓库既有迁移 0003/0005 依赖 PostgreSQL（JSONB / 实时查询），
在 SQLite 上无法整链执行，因此这里只对 0007 的回填语句做等价验证。
真实 PostgreSQL 上的整链验证需在有容器的环境执行（见 PLAN 阻塞项）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from sqlalchemy import (
    Boolean,
    Column,
    MetaData,
    Table,
    Text,
    create_engine,
    insert,
    select,
    text,
)

_SERVER_ROOT = Path(__file__).resolve().parents[2]
_VERSIONS = _SERVER_ROOT / "alembic" / "versions"
_MIG = _VERSIONS / "0007_document_lifecycle_states.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("mig_0007", _MIG)
    module = importlib.util.module_from_spec(spec)
    sys.modules["mig_0007"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def legacy_db(tmp_path):
    """只含旧列的 documents 表（模拟迁移前的库）。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    meta = MetaData()
    Table(
        "documents",
        meta,
        Column("id", Text, primary_key=True),
        Column("status", Text, nullable=False, server_default="pending"),
    )
    meta.create_all(engine)
    return engine


def _table_ref(engine):
    """反射出当前 documents 表（迁移后含新列），用于参数化 INSERT。"""
    from sqlalchemy import inspect

    insp = inspect(engine)
    cols = {c["name"]: c["type"] for c in insp.get_columns("documents")}
    meta = MetaData()
    return Table("documents", meta, *[
        Column(name, typ, primary_key=(name == "id"))
        for name, typ in cols.items()
    ])


def _add_columns(engine):
    """模拟 0007 的 add_column 步骤（SQLite 支持 ADD COLUMN）。"""
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE documents ADD COLUMN processing_status TEXT"))
        conn.execute(text("ALTER TABLE documents ADD COLUMN content_status TEXT"))
        conn.execute(
            text(
                "ALTER TABLE documents ADD COLUMN lifecycle_status TEXT "
                "NOT NULL DEFAULT 'active'"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE documents ADD COLUMN needs_reconcile BOOLEAN "
                "NOT NULL DEFAULT 0"
            )
        )


def _backfill(engine, mig):
    """执行 0007 的回填语句（与迁移内逻辑一致）。"""
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE documents SET processing_status = 'pending', "
                "content_status = NULL, lifecycle_status = 'archived', "
                "needs_reconcile = 1 WHERE status = 'archived'"
            )
        )
        for legacy, (processing, content) in mig._LEGACY_MAP.items():
            content_sql = f"'{content}'" if content else "NULL"
            conn.execute(
                text(
                    f"UPDATE documents SET processing_status = '{processing}', "
                    f"content_status = {content_sql}, "
                    f"lifecycle_status = 'active', needs_reconcile = 0 "
                    f"WHERE status = '{legacy}'"
                )
            )


def test_migration_revision_chain_is_linear():
    """0007 必须接在 0006 之后（编号取自真实 head），且回填映射完整。"""
    mig = _load_migration()
    assert mig.revision == "0007"
    assert mig.down_revision == "0006"
    # 回填映射必须覆盖全部旧状态（漏一个就会有行停留在旧语义）
    assert set(mig._LEGACY_MAP) == {
        "pending", "extracting", "awaiting_review",
        "confirmed", "parsed_no_data", "failed",
    }
    # 归档行不在映射里（单独处理为 needs_reconcile）
    assert "archived" not in mig._LEGACY_MAP
    # confirmed → processed + confirmed（不得丢"已确认"语义）
    assert mig._LEGACY_MAP["confirmed"] == ("processed", "confirmed")
    assert mig._LEGACY_MAP["parsed_no_data"] == ("processed", "no_data")


def test_backfill_maps_legacy_states(legacy_db):
    """旧状态 → 新维度映射正确。"""
    mig = _load_migration()
    _add_columns(legacy_db)
    docs = _table_ref(legacy_db)
    with legacy_db.begin() as conn:
        for status in ("pending", "extracting", "awaiting_review", "confirmed",
                       "parsed_no_data", "failed", "archived"):
            conn.execute(docs.insert().values(id=status, status=status))
    _backfill(legacy_db, mig)

    out = _rows(legacy_db)

    assert out["pending"].processing_status == "pending"
    assert out["extracting"].processing_status == "processing"
    assert out["awaiting_review"].processing_status == "awaiting_review"
    assert out["confirmed"].processing_status == "processed"
    assert out["confirmed"].content_status == "confirmed"
    assert out["parsed_no_data"].processing_status == "processed"
    assert out["parsed_no_data"].content_status == "no_data"
    assert out["failed"].processing_status == "failed"
    assert out["failed"].content_status is None


def _rows(engine):
    """读取 documents 全表为 {id: mapping}。"""
    conn = engine.connect()
    try:
        out = {}
        for row in conn.execute(text("SELECT * FROM documents")):
            out[row._mapping["id"]] = row._mapping
        return out
    finally:
        conn.close()


def test_backfill_archived_row_is_not_guessed_as_confirmed(legacy_db):
    """归档行无法反推处理状态 → pending + needs_reconcile，**不得**猜 confirmed。"""
    mig = _load_migration()
    _add_columns(legacy_db)
    docs = _table_ref(legacy_db)
    with legacy_db.begin() as conn:
        conn.execute(docs.insert().values(id="arch", status="archived"))
    _backfill(legacy_db, mig)

    row = _rows(legacy_db)["arch"]

    assert row.lifecycle_status == "archived"
    assert row.processing_status == "pending"
    assert row.content_status is None
    assert bool(row.needs_reconcile) is True
    # 关键断言：绝不能是 confirmed/processed
    assert row.processing_status != "processed"
    assert row.content_status != "confirmed"


def test_backfill_is_idempotent(legacy_db):
    """重复执行回填不得改变结果（迁移可重放）。"""
    mig = _load_migration()
    _add_columns(legacy_db)
    docs = _table_ref(legacy_db)
    with legacy_db.begin() as conn:
        conn.execute(docs.insert().values(id="c", status="confirmed"))
    _backfill(legacy_db, mig)
    _backfill(legacy_db, mig)

    row = _rows(legacy_db)["c"]
    assert row.processing_status == "processed"
    assert row.content_status == "confirmed"
    assert bool(row.needs_reconcile) is False
