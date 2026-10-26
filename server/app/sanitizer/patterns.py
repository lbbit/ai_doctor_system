"""出域脱敏规则（A-4/AC-14.2）：证件/电话/生日/邮箱/姓名/地址 识别与掩码。"""

import re
from dataclasses import dataclass
from typing import List, Optional

# 顺序重要：证件 → 电话 → 邮箱 → 日期 → 姓名 → 地址
_ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# 出生/生于 上下文的精确生日（避免误伤任意日期：需上下文共现）
_BIRTH_CONTEXT = re.compile(
    r"(出生|生于|生日|出生日期|出生日)[是为:：\s]*(\d{4})\s*[年\-/\.]\s*(\d{1,2})\s*[月\-/\.]\s*(\d{1,2})\s*日?"
)
_CN_DATE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
# 地址（P0 简化：整串掩码）
_ADDRESS = re.compile(
    r"[一-龥]{2,10}(?:省|市|自治区)[一-龥0-9]{0,10}(?:区|县|市)[一-龥0-9]{0,10}(?:路|街|道|巷)[一-龥0-9号弄栋单元室]*"
)


@dataclass
class Replaced:
    type: str
    repl: str


def age_bracket(birthday_iso: Optional[str], today=None) -> str:
    """生日 → 年龄段（AC-14.2 保留年龄段）。"""
    if not birthday_iso:
        return "约?岁"
    import datetime as _dt

    today = today or _dt.date.today()
    try:
        born = _dt.date.fromisoformat(birthday_iso[:10])
    except ValueError:
        return "约?岁"
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    if age < 0:
        return "约?岁"
    lower = (age // 10) * 10
    return f"约{lower}-{lower + 9}岁" if age >= 20 else f"约{age}岁"


def sanitize_text(
    text: str,
    *,
    names: Optional[List[str]] = None,
    relation_map: Optional[dict] = None,
    birthday_iso: Optional[str] = None,
) -> tuple[str, List[Replaced]]:
    """出域脱敏（§6.2 规则表）：返回 (脱敏文本, 替换记录[不含明文])。"""
    if not text:
        return text, []
    replaced: List[Replaced] = []
    out = text

    def _sub(pattern, repl, kind):
        nonlocal out
        new = pattern.sub(repl, out)
        if new != out:
            replaced.append(Replaced(kind, repl if isinstance(repl, str) else ""))
        out = new

    # 1) 证件号（先于电话）
    _sub(_ID_CARD, "***证件号***", "id_card")
    # 2) 电话
    _sub(_PHONE, "***电话***", "phone")
    # 3) 邮箱
    _sub(_EMAIL, "***邮箱***", "email")
    # 4) 出生日期（上下文共现）→ 年龄段
    bracket = age_bracket(birthday_iso)

    def _birth_repl(m):
        replaced.append(Replaced("birthday", bracket))
        return f"{m.group(1)}{bracket}"

    out = _BIRTH_CONTEXT.sub(_birth_repl, out)

    # 5) 姓名：精确匹配 Subject 名单 → 关系占位符/档案对象
    for name in sorted({n for n in (names or []) if n and len(n) >= 2},
                       key=len, reverse=True):
        if name in out:
            placeholder = "档案对象"
            for n, rel in (relation_map or {}).items():
                if n == name and rel:
                    placeholder = rel
                    break
            out = out.replace(name, placeholder)
            replaced.append(Replaced("name", placeholder))

    # 6) 地址（P0 整串掩码）
    _sub(_ADDRESS, "***地址***", "address")

    # 中文日期散落（如"2026年3月12日"在非生日语境）保留——日期本身非强 PII；
    # 出生日期已按上下文处理。
    return out, replaced
