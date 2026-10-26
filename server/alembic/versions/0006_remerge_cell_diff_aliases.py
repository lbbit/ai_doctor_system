"""补执行 `*_ABS` → `*_CNT` 的别名合并（幂等重放）

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-23

背景（真实缺陷，2026-10-23）：0005 本身逻辑正确——它把 `*_ABS` 关联的
observations/参考区间改指 `*_CNT`，并把不重复的别名并入 `*_CNT`，最后停用
`*_ABS`。**但升级序列是 `alembic upgrade` 紧接 `seed_dictionary`**，而当时的
`upsert()` 是「用 TOP50 的 aliases 覆盖整列」，于是 0005 刚合并进去的别名
（如「淋巴细胞绝对值」）立刻被回冲掉。

实测证据：迁移后 `_ABS` 全部 `active=false`、观测全部改指成功，但
`LYMPH_CNT.aliases` 里**没有**「淋巴细胞绝对值」。

两个修法都已落地：
1. `dictionary_seed.upsert()` 改为**合并**别名（不再覆盖）——防止再次回冲；
2. 本迁移做一次**幂等重放**，把已被回冲掉的那部分别名补回来。

为什么单独一个 revision 而不是改 0005：0005 已在真实环境执行过，
改历史迁移不会重跑；新增 0006 才能修复既有部署。
"""

from typing import Dict, List, Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LEGACY_TO_CANONICAL: Dict[str, str] = {
    "NEUT_ABS": "NEUT_CNT",
    "LYMPH_ABS": "LYMPH_CNT",
    "MONO_ABS": "MONO_CNT",
    "EOS_ABS": "EOS_CNT",
    "BASO_ABS": "BASO_CNT",
}

# 不应进入字典的别名：纯排版碎片，或与规范条目已有别名语义重复的空壳
_ALIAS_BLOCKLIST = ("第1页", "共", "页码", "报告页码文本")


def _is_postgres() -> bool:
    try:
        return op.get_bind().dialect.name == "postgresql"
    except Exception:  # noqa: BLE001
        return True


def _aliases_param(aliases: List[str]):
    import json as _json

    return aliases if _is_postgres() else _json.dumps(aliases, ensure_ascii=False)


def _fetch(bind, codes: Sequence[str]) -> Dict[str, dict]:
    """按 code 取 (id, aliases, active)。用 IN 展开，兼容 SQLite。"""
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
    out: Dict[str, dict] = {}
    for row in rows:
        aliases = row[2]
        if isinstance(aliases, str):
            import json as _json

            try:
                aliases = _json.loads(aliases)
            except ValueError:
                aliases = []
        out[row[1]] = {"id": row[0], "aliases": list(aliases or []), "active": row[3]}
    return out


def upgrade() -> None:
    bind = op.get_bind()
    codes = list(_LEGACY_TO_CANONICAL) + list(_LEGACY_TO_CANONICAL.values())
    found = _fetch(bind, codes)

    for legacy, canonical in _LEGACY_TO_CANONICAL.items():
        old = found.get(legacy)
        new = found.get(canonical)
        if old is None or new is None:
            continue

        merged = list(new["aliases"])
        existing = {str(a).strip() for a in merged}
        for alias in old["aliases"]:
            text = str(alias or "").strip()
            if not text or text in existing:
                continue
            if any(bad in text for bad in _ALIAS_BLOCKLIST):
                continue
            merged.append(text)
            existing.add(text)

        if merged != list(new["aliases"]):
            bind.execute(
                sa.text("UPDATE indicator_dictionary SET aliases = :a WHERE id = :id"),
                {"a": _aliases_param(merged), "id": new["id"]},
            )

    # 再确认一次旧条目停用（0005 已做过，此处幂等兜底）
    for legacy in _LEGACY_TO_CANONICAL:
        row = found.get(legacy)
        if row is None:
            continue
        bind.execute(
            sa.text("UPDATE indicator_dictionary SET active = false WHERE id = :id"),
            {"id": row["id"]},
        )


def downgrade() -> None:
    """别名合并属"信息增补"，不回滚（回滚会让历史写法再次匹配不到）。"""
    pass
