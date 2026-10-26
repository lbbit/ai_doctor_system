"""合并重复的五分类计数组指标（*_ABS → *_CNT）并停用页面元信息指标

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-23

背景（真实缺陷，2026-10-23 端到端实测）：

1. **五分类计数组重复**。字典早期只有 `NEUT_PCT`/`LYMPH_PCT`/… 五条（百分比），
   没有计数组。抽取时「中性粒细胞 2.22 10^9/L」匹配不上任何指标，AI 兜底
   对齐于是新建了 `NEUT_ABS`/`LYMPH_ABS`/`MONO_ABS`/`EOS_ABS`/`BASO_ABS`。
   后来字典补齐 `*_CNT` 五条，但上述 `*_ABS` 仍 `active=True`，且别名与
   `*_CNT` **完全重复**（`中性粒细胞#`、`中性粒细胞计数` 等）。结果是
   「中性粒细胞#」精确命中旧的 `NEUT_ABS` 而不是 `NEUT_CNT` ——
   这正是 AGENTS.md「AI 底座原则」第 2 条禁止的"同一实体在多次导入后变成多个"。

2. **页面元信息被当成指标**。OCR/VLM 把版式文字块也送进对齐，AI 建出了
   `REPORT_PAGE_TEXT`（名称「报告页码文本」，别名「第1页 共」）。这类是
   报告页面元信息，不是检验指标，会污染标准主数据与趋势查询。

处理策略（**停用而非删除**，原件与审计不可丢）：

- 把 `*_ABS` 关联的 `observations` 与 `derived` 引用改指同族的 `*_CNT`
  条目（值/单位/参考区间原样保留，只换字典归属，语义等价）；
- 把 `*_ABS` 上「确实不与 `*_CNT` 重复」的别名并入 `*_CNT`（如「…绝对值」），
  保证历史写法今后仍能命中标准项；
- `*_ABS` 与 `REPORT_PAGE_TEXT` 置 `active=False`（保留行，供审计回溯）；
- 幂等：目标 `*_CNT` 不存在时跳过该项，不制造半个状态。

代码侧已有双保险（`is_page_meta` 拦元信息、`match_indicator._pick` 让规范
code 优先），本迁移是把**已污染的数据**也拉回一致，二者不互相替代。
"""

from typing import Dict, List, Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# legacy code -> 规范 code
_LEGACY_TO_CANONICAL: Dict[str, str] = {
    "NEUT_ABS": "NEUT_CNT",
    "LYMPH_ABS": "LYMPH_CNT",
    "MONO_ABS": "MONO_CNT",
    "EOS_ABS": "EOS_CNT",
    "BASO_ABS": "BASO_CNT",
}

# 需要停用的非指标项（页面元信息）
_PAGE_META_CODES = ("REPORT_PAGE_TEXT",)


def _aliases_param(aliases: List[str]):
    """别名数组的绑定值：PostgreSQL 用 list，其它方言（SQLite）用 JSON 文本。"""
    import json as _json

    return aliases if _is_postgres() else _json.dumps(aliases, ensure_ascii=False)


def _is_postgres() -> bool:
    try:
        bind = op.get_bind()
        return bind.dialect.name == "postgresql"
    except Exception:  # noqa: BLE001
        return True


def _fetch_code_rows(bind, codes: Sequence[str]) -> Dict[str, dict]:
    """按 code 取字典行（id / code / aliases / active）。

    用绑定参数展开成 ``IN (:c0, :c1, ...)``，避免 ``= ANY(array)`` 这类
    PostgreSQL 专属语法（迁移需在测试用的 SQLite 上也能跑）。
    """
    if not codes:
        return {}
    params = {f"c{i}": code for i, code in enumerate(codes)}
    placeholders = ", ".join(f":c{i}" for i in range(len(codes)))
    rows = bind.execute(
        sa.text(
            "SELECT id, code, aliases, active FROM indicator_dictionary "
            f"WHERE code IN ({placeholders})"
        ),
        params,
    ).fetchall()
    result: Dict[str, dict] = {}
    for row in rows:
        aliases = row[2]
        if isinstance(aliases, str):
            # SQLite 下 JSON 列以文本返回
            try:
                import json as _json

                aliases = _json.loads(aliases)
            except ValueError:
                aliases = []
        result[row[1]] = {
            "id": row[0], "code": row[1],
            "aliases": list(aliases or []), "active": row[3],
        }
    return result


def upgrade() -> None:
    bind = op.get_bind()
    all_codes = list(_LEGACY_TO_CANONICAL) + list(_LEGACY_TO_CANONICAL.values()) + list(_PAGE_META_CODES)
    found = _fetch_code_rows(bind, all_codes)

    for legacy, canonical in _LEGACY_TO_CANONICAL.items():
        old = found.get(legacy)
        new = found.get(canonical)
        if old is None:
            continue  # 该部署没被污染，跳过
        if new is None:
            # 目标不存在则不动，避免出现"观测指向不存在字典"的半个状态
            continue

        # 1) 观测改指规范条目（值/单位/参考区间不动，语义等价）
        bind.execute(
            sa.text(
                "UPDATE observations SET dictionary_id = :new_id, code = :new_code "
                "WHERE dictionary_id = :old_id OR code = :old_code"
            ),
            {
                "new_id": new["id"],
                "new_code": canonical,
                "old_id": old["id"],
                "old_code": legacy,
            },
        )

        # 1b) 参考区间改指规范条目。注意 (dictionary_id, version) 在本表上语义唯一，
        # 若规范条目已有同 version 区间，改指会撞唯一约束 —— 冲突时保留规范条目
        # 既有区间、删除重复的旧区间（两者描述的是同一指标同一版本）。
        dup_versions = bind.execute(
            sa.text(
                "SELECT version FROM indicator_ref_ranges "
                "WHERE dictionary_id = :old_id AND version IN ("
                "  SELECT version FROM indicator_ref_ranges WHERE dictionary_id = :new_id"
                ")"
            ),
            {"old_id": old["id"], "new_id": new["id"]},
        ).fetchall()
        for (version,) in dup_versions:
            bind.execute(
                sa.text(
                    "DELETE FROM indicator_ref_ranges "
                    "WHERE dictionary_id = :old_id AND version = :version"
                ),
                {"old_id": old["id"], "version": version},
            )
        bind.execute(
            sa.text(
                "UPDATE indicator_ref_ranges SET dictionary_id = :new_id "
                "WHERE dictionary_id = :old_id"
            ),
            {"new_id": new["id"], "old_id": old["id"]},
        )

        # 2) 把不重复的别名并入规范条目（如「…绝对值」），保证历史写法仍能命中
        merged = list(new["aliases"])
        existing = {str(a).strip() for a in merged}
        for alias in old["aliases"] or []:
            text = str(alias or "").strip()
            if text and text not in existing:
                merged.append(text)
                existing.add(text)
        if merged != list(new["aliases"]):
            bind.execute(
                sa.text("UPDATE indicator_dictionary SET aliases = :aliases WHERE id = :id"),
                {"aliases": _aliases_param(merged), "id": new["id"]},
            )

        # 3) 停用旧条目（保留行，供审计回溯；不物理删除）
        bind.execute(
            sa.text("UPDATE indicator_dictionary SET active = false WHERE id = :id"),
            {"id": old["id"]},
        )

    # 页面元信息：直接停用
    for code in _PAGE_META_CODES:
        row = found.get(code)
        if row is None:
            continue
        bind.execute(
            sa.text("UPDATE indicator_dictionary SET active = false WHERE id = :id"),
            {"id": row["id"]},
        )


def downgrade() -> None:
    """回滚只恢复 active 标记与归属；别名合并是"信息增补"，不反向拆分。

    理由：把别名从规范条目里再剥出来没有业务价值，且可能与后续人工维护冲突；
    回滚的核心诉求是"让流程能退回上一版本行为"，恢复 active 即可满足。
    """
    bind = op.get_bind()
    codes = list(_LEGACY_TO_CANONICAL) + list(_PAGE_META_CODES)
    found = _fetch_code_rows(bind, codes)
    for row in found.values():
        bind.execute(
            sa.text("UPDATE indicator_dictionary SET active = true WHERE id = :id"),
            {"id": row["id"]},
        )

    # 观测归属不自动改回：合并时无法区分哪些是"原本就属于 _ABS"的记录，
    # 强行拆分会把后来正常抽取的 _CNT 观测误标回 _ABS。
