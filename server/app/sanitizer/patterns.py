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

# 常见姓氏（用于在**无档案上下文**时保守剔除中文人名）
_SURNAMES = (
    "张王李赵刘陈杨黄周吴徐孙马朱胡郭何林高罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤"
)
_PERSON_RE = re.compile(
    rf"[{_SURNAMES}][一-龥]{{1,2}}"
)

# 人名上下文：只有紧邻这些词的姓氏候选才可能是人名。
# 为什么必须要上下文：中文姓氏与医学词首字大量重合（高/林/白/何/史…），
# 无条件剔除会把"血脂偏高"删成"血脂要"、把"高血压"删成"血压"，
# 直接破坏检索词与临床语义（2026-10-26 实测教训：T005 首版因此误删"高血压"）。
_PERSON_CONTEXT_BEFORE = (
    "妈", "爸", "父", "母", "爷", "奶", "患者", "病人", "姓名", "名字", "名叫",
    "叫", "的", "老", "小", "本人", "先生", "女士", "同志", "老人", "孩子",
    "儿子", "女儿", "爷爷", "奶奶", "外公", "外婆", "叔叔", "阿姨", "先生",
)
_PERSON_CONTEXT_AFTER = ("医生", "医师", "主任", "教授", "同志", "先生", "女士")


def _has_person_context(text: str, start: int, end: int) -> bool:
    """判断姓氏候选左侧/右侧是否出现人名上下文标记。

    左侧窗口取 4 个字：中文称谓（"父亲""母亲""患者"）长度不一，
    只看 2 个字会漏掉"我的父亲张建国"这类（实测修复）。
    """
    left = text[max(0, start - 4):start]
    right = text[end:end + 3]
    for marker in _PERSON_CONTEXT_BEFORE:
        if marker in left:
            return True
    for marker in _PERSON_CONTEXT_AFTER:
        if marker in right:
            return True
    return False


# 医学术语保护：这些后缀/词条**不得**被当成人名剔除
# （否则"高血压"会因"高"是姓氏而被误删——2026-10-26 实测教训）
_MEDICAL_SUFFIXES = (
    "血压", "血糖", "血脂", "尿酸", "肌酐", "转氨酶", "胆红素", "血红蛋白",
    "癌", "瘤", "炎", "症", "病", "感染", "结石", "结节", "狭窄", "硬化",
    "衰竭", "梗死", "综合征", "疾病", "细胞", "蛋白", "酶", "素", "菌",
    "心", "肝", "肾", "肺", "胃", "肠", "胆", "胰", "脾", "血", "尿",
    "过敏", "发热", "疼痛", "水肿", "痉挛", "溃疡", "囊肿", "积水",
    "偏高", "偏低", "升高", "降低", "正常", "异常", "要", "的",
)
_MEDICAL_TERMS = frozenset(
    {
        "高血压", "低血压", "高血糖", "低血糖", "高血脂", "高尿酸", "甲亢", "甲减",
        "贫血", "肺炎", "肝炎", "胃炎", "肠炎", "关节炎", "颈椎病", "糖尿病",
        "冠心病", "脑梗", "脑血栓", "心梗", "肿瘤", "结节", "囊肿", "结石",
        "感染", "发热", "咳嗽", "咳血", "胸痛", "头痛", "腹痛", "腰痛", "皮疹",
        "过敏", "水肿", "心悸", "眩晕", "失眠", "抑郁", "焦虑", "骨折", "脱位",
    }
)

# 常见医学搭配：前一个字 + 候选首字构成医学表达时，候选不是人名。
# 例："血脂偏高"→"偏"+"高"；"血糖高"→"糖"+"高"；"血压高"→"压"+"高"。
_MEDICAL_PAIRS = frozenset(
    {
        "偏高", "偏低", "升高", "降低", "压高", "糖高", "脂高", "酸高", "盐高",
        "紧张", "快感", "失常", "失常", "安", "康", "泰", "和", "田", "史",
    }
)


def _is_medical_token(token: str) -> bool:
    """判断候选 token 是否是医学术语（是则不得当人名剔除）。"""
    if token in _MEDICAL_TERMS:
        return True
    return any(token.endswith(sfx) for sfx in _MEDICAL_SUFFIXES)


def strip_person_names(text: str) -> str:
    """保守剔除中文人名（无档案上下文时的兜底）。

    三重保险（2026-10-26 实测教训）：
    1. **必须有人名上下文**（"我妈张秀英""患者李某"）才剔除，避免把
       "血脂偏高""高血压"这类医学词误删；
    2. 候选本身具医学特征时**一律保留**；
    3. 候选**前一个字**构成医学搭配（如"血脂偏高"的"偏"、"高血压"的"压"）
       时也保留——否则"血脂偏高"会被吃成"血脂偏"（实测）。

    已知档案姓名（subject.name）由 `sanitize_text` 的精确匹配负责（更可靠），
    本函数只是没有档案上下文时的兜底，不替代它。
    """
    if not text:
        return text
    out = text
    for m in list(_PERSON_RE.finditer(text)):
        token = m.group(0)
        if _is_medical_token(token):
            continue
        if not _has_person_context(text, m.start(), m.end()):
            continue
        # 候选前一个字构成医学搭配时保留（如"血脂偏高""血压高"）
        prev_char = text[m.start() - 1] if m.start() > 0 else ""
        if prev_char and any(
            prev_char + ch in _MEDICAL_PAIRS for ch in token
        ):
            continue
        out = out.replace(token, " ", 1)
    return out


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

    # 7) 中文人名兜底（无档案上下文时）：保守剔除，医学术语受保护
    stripped = strip_person_names(out)
    if stripped != out:
        out = stripped
        replaced.append(Replaced("person", "***姓名***"))

    # 中文日期散落（如"2026年3月12日"在非生日语境）保留——日期本身非强 PII；
    # 出生日期已按上下文处理。
    return out, replaced
