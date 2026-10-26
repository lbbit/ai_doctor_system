"""AI 指标语义对齐（AI 底座原则的核心应用）。

问题：不同医院/诊所对同一指标叫法不同（血红蛋白/血色素/HGB/Hb/血红蛋白量），
单位也不同（g/L、g/dL、/uL、10^9/L）。若每次导入都新建指标，趋势会断成多段。

对齐顺序（成本与确定性由高到低，**能确定就不调 AI**）：
1. 归一化精确命中（标准名 / code / 已学别名） —— 0 成本、100% 确定
2. 模糊候选（字符相似度）且分数足够高（≥0.92）—— 0 成本、高置信
3. **AI 语义对齐**：把候选清单与原始名一起交给模型判断是否同一指标
4. AI 判定为新指标 → 给出标准名/code/单位/分类，落库为**待审核新标准指标**
   （不直接污染主词表，避免模型幻觉长期生效）

降级：LLM 不可用时跳过 3–4，行为等同于当前实现（未命中入反馈队列），不阻塞抽取。
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from app.llm.client import LLMClient

logger = logging.getLogger("app.indicator_aligner")

# 模糊匹配可直接采信的阈值（低于此值才问 AI）
AUTO_MATCH_THRESHOLD = 0.92
# AI 判定为同一指标的最低置信度
AI_MATCH_CONFIDENCE = 0.7

# ---- 新建标准指标的准入门槛（代码侧，不可被模型说服）----
# 实测噪声：T1 / 年0 / 每3 / ① / 第20/20页 / 项目号 / 备注 / 报告说明
_NOISE_NAME = re.compile(
    r"^[\d\W_]+$"                       # 纯数字/符号
    r"|^[a-z]\d$"                       # T1 / A2 这类表格行标（单字母+单数字）
    r"|^\D{1,2}\d{1,2}$"                # 年0 / 每3（极短的非数字+数字）
    r"|^[年月日时分秒岁第每]\d*$"       # 年0 / 每3 / 第 / 岁 这类排版碎片
    r"|第?\s*\d+\s*/\s*\d+\s*页?"       # 页码
    r"|^(项目号|编号|卡号|条码|备注|说明|小结|结论|提示|序号|页码)$"
    r"|阅读说明|科普|目录|免责|声明|电话|地址|网址|版权",
    re.I,
)
# 结论/建议措辞：是判断结果而非指标名（增高/降低/异常/偏高/偏低/正常）
_CONCLUSION_WORD = re.compile(
    r"增[高强]|升[高]|降[低减]|减[低少]|偏[高低]|异常|正常|阳性|阴性|可疑|建议|复查|未见"
)

# 报告页面「元信息」：是版式/流程字段，不是检验指标。
# 真实缺陷（2026-10-23 实测 OCR 路径）：`报告页码文本`、`打印时间`、`审核者`
# 等被 AI 当成新指标建进字典（code=REPORT_PAGE_TEXT），污染标准主数据。
# 注意：这里只拦「页面元信息/流程角色」，不能误伤真实指标——
# 反例必须继续放行：「标本类型」在血常规里不是指标，但「标本量」可能是；
# 因此只用**明确无歧义**的整词，不做宽泛的子串匹配。
_PAGE_META_EXACT = re.compile(
    r"^(报告)?(页码|页数|第[一二三四五六七八九十\d]+页(共?[一二三四五六七八九十\d]+页)?|"
    r"共[一二三四五六七八九十\d]+页|"
    r"打印(时间|日期|人|者)?|报告(时间|日期|说明|备注|名称)?|"
    r"审核(者|人|医师|医生|时间|日期)?|检验(者|人|医师|医生)|"
    r"复核(者|人|医师|医生)?|送检(时间|日期|医师|医生|科室)?|"
    r"采集(时间|日期|人|者)?|接收(时间|日期|人|者)?|"
    r"申请(医师|医生|科室|时间|日期)?|科室|病区|床号|住院号|门诊号|"
    r"标本(类型|种类|编号|号|条码))$",
    re.I,
)

# 元信息词 + 描述性尾巴：AI 常把版式块命名成「报告页码文本」「打印时间区域」这类
# 组合词（真实缺陷：`报告页码文本` → code=REPORT_PAGE_TEXT 进了字典）。
# 规则：以元信息词**开头**，且整体只是它的描述性扩展（不含任何检验特征词）。
_PAGE_META_HEAD = re.compile(
    r"^(报告)?(页码|页数|打印(时间|日期)?|报告(时间|日期|说明|备注)?|"
    r"审核(者|人|医师|时间)?|检验(者|人|医师)|复核(者|人|医师)?|"
    r"送检(时间|日期)?|采集(时间|日期)?|接收(时间|日期)?|"
    r"申请(医师|科室)?|标本(类型|种类)|"
    r"科室|病区|床号|住院号|门诊号)",
    re.I,
)
# 描述性尾巴（只描述"这是个什么文本块"，不携带检验语义）
_META_TAIL = re.compile(r"(文本|文字|内容|信息|字段|区域|栏|区块|数据|值)?$", re.I)

# 正常指标名的形态要求：中文/字母开头，长度 2~24
_VALID_NAME = re.compile(r"^[一-龥A-Za-z][一-龥A-Za-z0-9()（）/%·\-\.]{1,23}$")

# 允许作为指标的特征词（有其一即可；覆盖体检常见项，避免误杀）
_MEASURE_SUFFIX = re.compile(
    r"蛋白|细胞|计数|血红蛋白|血糖|胆固醇|甘油三酯|酶|激素|抗体|抗原|离子|"
    r"比值|比率|百分比|指数|压|酯|酸|素|钙|钠|钾|氯|镁|铁|锌|铜|糖|"
    r"原|体|量|度|率|容积|体积|面积|宽度|直径|"
    r"眼|牙|皮肤|淋巴结|甲状腺|乳房|腹|肝|脾|肾|心|肺|脊柱|四肢|关节|"
    r"身高|体重|视力|色觉|听力|"
    r"二氧化碳|酸碱|比重|潜血|隐血|红|白|球|"
    r"[A-Za-z]{2,}[_%\d]*$|pH",
    re.I,
)


def _is_page_meta(name: str) -> bool:
    """是否为报告页面的元信息（页码/打印/审核/科室…），不是检验指标。

    两级判定：
    1. 整名精确命中元信息词（``打印时间``、``审核者``）；
    2. 以元信息词开头、且整体只是描述性扩展（``报告页码文本``）——
       后者是 AI 命名版式块的典型产物，必须拦，否则会建进标准主数据。
    """
    text = (name or "").strip()
    if not text:
        return False
    if _PAGE_META_EXACT.match(text):
        return True
    head = _PAGE_META_HEAD.match(text)
    if not head:
        return False
    rest = text[head.end():]
    # 剩余部分只能是描述性尾巴；一旦出现检验特征词就不是元信息
    if _MEASURE_SUFFIX.search(rest):
        return False
    return bool(_META_TAIL.match(rest))


def is_noise_text(name: str) -> bool:
    """是否为明确的排版噪声（表格行标、页码、机构信息、结论措辞）。

    与 `is_plausible_indicator_name` 的区别：
    - 本函数只判断"肯定不是指标名"（严格），用于**匹配前**拦截；
    - 后者判断"可以成为新标准指标"（有准入下限），用于 AI 新建前把关。
    两者分开可以避免把「钾」「身高」这类合法短名误伤。
    """
    text = (name or "").strip()
    if not text:
        return True
    if _NOISE_NAME.search(text):
        return True
    if _is_page_meta(text):
        return True
    if _CONCLUSION_WORD.search(text):
        return True
    # 单字母+数字 / 纯符号 / 极短非中文 / 圈号（①②③ 是序号，不是指标名）
    if re.fullmatch(r"[A-Za-z]\d{1,2}", text):
        return True
    if re.fullmatch(r"[\u2460-\u24ff\u2776-\u2793]", text):
        return True
    if re.fullmatch(r"[\W_]+", text):
        return True
    return False


def is_plausible_indicator_name(name: str, *, value=None) -> tuple[bool, str]:
    """判断一个名字是否可能是真实检验指标（AI 新建前必须通过）。

    返回 (ok, reason)；reason 会写入 `align_rejected` 供审计。

    注意：这是**准入下限**而非精确白名单——目标是拦住排版碎片与结论措辞，
    不能把「钾」「钠」「身高」「体重」这类合法短名误杀。
    """
    text = (name or "").strip()
    if not text:
        return False, "空名称"
    if len(text) < 1:
        return False, "名称过短"
    if len(text) > 40:
        return False, "名称过长"
    if _NOISE_NAME.search(text):
        return False, "排版噪声（编号/页码/机构信息）"
    if _is_page_meta(text):
        return False, "报告页面元信息（页码/打印/审核等），不是检验指标"
    if _CONCLUSION_WORD.search(text):
        return False, "结论性措辞，不是指标名"
    if not _VALID_NAME.match(text) and not re.match(r"^[一-龥]$", text):
        return False, "不符合指标名形态"
    if len(text) == 1:
        # 单字仅允许常见血液指标（钾/钠/氯/钙/镁…），且需命中特征词
        return (
            (_MEASURE_SUFFIX.search(text) is not None),
            "" if _MEASURE_SUFFIX.search(text) else "单字名称缺少检验特征",
        )
    if _MEASURE_SUFFIX.search(text):
        return True, ""
    # 无特征词时：允许 2~8 个汉字的中文名（如「身高」「体重」「色觉」）
    if re.fullmatch(r"[一-龥]{2,8}", text):
        return True, ""
    return False, "缺少检验项目特征词"



@dataclass
class AlignDecision:
    """一次对齐的结果。"""

    action: str                       # 'exact' | 'fuzzy' | 'ai_match' | 'ai_new' | 'unmatched'
    code: Optional[str] = None
    display_name: Optional[str] = None
    unit: Optional[str] = None
    category: Optional[str] = None
    aliases: List[str] = field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""
    used_llm: bool = False

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "code": self.code,
            "displayName": self.display_name,
            "unit": self.unit,
            "category": self.category,
            "aliases": self.aliases,
            "confidence": self.confidence,
            "reason": self.reason,
            "usedLlm": self.used_llm,
        }


_PROMPT = """你是医学检验指标标准化专家。任务：判断一个新的检验指标名称是否与已有标准指标是同一个指标；
若不是，给出它的标准名称、建议英文代码、标准单位与分类。

## 已有标准指标（code | 标准中文名 | 标准单位 | 分类）
{catalog}

## 待判断的指标
- 原始名称：{raw_name}
- 原始单位：{raw_unit}
- 数值：{raw_value}
- 所在报告类型：{doc_type}

## 判断规则
1. 只有**确实是同一个检验项目**才算同一个指标（例如「血色素」=「血红蛋白」，「红细胞比积」=「红细胞压积」）。
2. 不同指标绝不能合并，尤其注意成对/系列指标：收缩压 vs 舒张压、总胆固醇 vs 甘油三酯、
   平均红细胞体积(MCV) vs 平均血红蛋白浓度(MCHC) vs 平均血红蛋白量(MCH)、白细胞 vs 血小板。
3. 单位不同不是合并理由，但要指出标准单位。
4. 若无法确定，宁可 `matched=false` 且 `confidence` 给低值。

## 输出（仅一个 JSON 对象，不要 Markdown）
{{
  "matched": true/false,
  "code": "匹配到的 code；未匹配时为你建议的新 code（大写字母数字下划线）",
  "standard_name": "标准中文名",
  "standard_unit": "标准单位，无法判断为 null",
  "category": "血液|生化|电解质|尿液|血压|其他",
  "aliases": ["可并入的别名"],
  "confidence": 0.0-1.0,
  "reason": "一句话理由"
}}
"""


def build_catalog(entries: Sequence) -> str:
    """构造给模型看的指标词表（控制长度，避免过大 prompt）。"""
    lines = []
    for entry in entries:
        unit = getattr(entry, "unit", None) or "-"
        category = getattr(entry, "category", None) or "-"
        lines.append(
            f"{getattr(entry, 'code', '')} | {getattr(entry, 'name_cn', '')} | {unit} | {category}"
        )
    return "\n".join(lines) or "（空）"


def _parse_json(text: str) -> Optional[dict]:
    content = (text or "").strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:]
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return None


def _slug_code(name: str, taken: set) -> str:
    """为 AI 建议的新指标生成稳定 code（拼音不可靠，用规范化名的哈希后缀）。"""
    import hashlib

    base = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    if not base or not re.match(r"^[a-z]", base):
        digest = hashlib.sha1((name or "").encode("utf-8")).hexdigest()[:8]
        base = f"auto_{digest}"
    code = base.upper()[:40]
    if code in taken:
        digest = hashlib.sha1((name or "").encode("utf-8")).hexdigest()[:6].upper()
        code = f"{code}_{digest}"
    return code


def align_with_llm(
    llm: Optional[LLMClient],
    *,
    raw_name: str,
    raw_unit: Optional[str] = None,
    raw_value=None,
    doc_type: str = "",
    entries: Sequence = (),
    taken_codes: Optional[set] = None,
) -> Optional[AlignDecision]:
    """让模型判断是否已有同一标准指标；返回 None 表示 AI 不可用/输出不可用。"""
    if llm is None or not getattr(llm, "available", lambda: False)():
        return None
    prompt = _PROMPT.format(
        catalog=build_catalog(entries),
        raw_name=raw_name,
        raw_unit=raw_unit or "-",
        raw_value=raw_value if raw_value is not None else "-",
        doc_type=doc_type or "-",
    )
    try:
        raw = llm.chat(
            [{"role": "user", "content": prompt}],
            json_mode=True,
            temperature=0,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("align.llm_unavailable name=%s err=%s", raw_name, exc)
        return None

    data = _parse_json(raw)
    if not isinstance(data, dict):
        logger.info("align.llm_bad_json name=%s", raw_name)
        return None

    confidence = float(data.get("confidence") or 0)
    matched = bool(data.get("matched"))
    code = (data.get("code") or "").strip() or None
    standard_name = (data.get("standard_name") or data.get("standardName") or "").strip() or None
    aliases = [str(a).strip() for a in (data.get("aliases") or []) if str(a).strip()]
    category = (data.get("category") or "").strip() or None
    unit = (data.get("standard_unit") or data.get("standardUnit") or "").strip() or None
    reason = (data.get("reason") or "").strip()

    if matched and code and confidence >= AI_MATCH_CONFIDENCE:
        return AlignDecision(
            action="ai_match",
            code=code,
            display_name=standard_name,
            unit=unit,
            category=category,
            aliases=aliases,
            confidence=confidence,
            reason=reason or "AI 判定为已有指标",
            used_llm=True,
        )

    if not matched and standard_name:
        codes = taken_codes or set()
        new_code = code if code and code not in codes else _slug_code(standard_name, codes)
        return AlignDecision(
            action="ai_new",
            code=new_code,
            display_name=standard_name,
            unit=unit,
            category=category or "其他",
            aliases=aliases or [raw_name],
            confidence=confidence,
            reason=reason or "AI 判定为新指标",
            used_llm=True,
        )

    return AlignDecision(
        action="unmatched",
        confidence=confidence,
        reason=reason or "AI 无法确定",
        used_llm=True,
    )


def align_observations_with_llm(
    db,
    llm: Optional[LLMClient],
    payload: dict,
    *,
    doc_type: str = "",
    entries: Sequence,
    max_calls: int = 8,
) -> Dict[str, int]:
    """批量对齐未命中的指标（限制调用次数，避免成本失控）。

    返回 {"matched", "created", "skipped"}，新指标同时写入标准指标表（待审核）。
    """
    from app.db.models import IndicatorDictionary

    observations = payload.get("observations") or []
    pending = [o for o in observations if not o.get("code")]
    stats = {"matched": 0, "created": 0, "skipped": 0}
    if not pending:
        return stats

    taken = {getattr(e, "code", None) for e in entries}
    catalog = list(entries)

    for obs in pending[:max_calls]:
        decision = align_with_llm(
            llm,
            raw_name=str(obs.get("raw_name") or ""),
            raw_unit=obs.get("unit"),
            raw_value=obs.get("value"),
            doc_type=doc_type,
            entries=catalog,
            taken_codes=taken,
        )
        if decision is None:
            stats["skipped"] += len(pending) - stats["matched"] - stats["created"]
            break

        if decision.action == "ai_match" and decision.code:
            existing = next(
                (e for e in catalog if getattr(e, "code", None) == decision.code), None
            )
            obs["code"] = decision.code
            obs["dictionary_id"] = getattr(existing, "id", None)
            obs["display_name"] = decision.display_name or obs.get("raw_name")
            if decision.unit and not obs.get("unit"):
                obs["unit"] = decision.unit
            obs["unmapped"] = existing is None
            obs["ai_alignment"] = decision.to_dict()
            stats["matched"] += 1

        elif decision.action == "ai_new" and decision.code:
            row = _create_standard_indicator(
                db, decision, raw_name=str(obs.get("raw_name") or ""),
                raw_unit=obs.get("unit"), raw_value=obs.get("value"),
            )
            if row is not None:
                obs["code"] = row.code
                obs["dictionary_id"] = row.id
                obs["display_name"] = row.name_cn
                if row.unit and not obs.get("unit"):
                    obs["unit"] = row.unit
                obs["unmapped"] = False
                obs["ai_alignment"] = decision.to_dict()
                taken.add(row.code)
                catalog.append(_EntryView(row))
                stats["created"] += 1
            else:
                stats["skipped"] += 1
        else:
            stats["skipped"] += 1

    stats["skipped"] += max(0, len(pending) - max_calls)
    return stats


class _EntryView:
    """把 ORM 行包装成与 _DictEntry 相同的最小接口（供 catalog 拼装）。"""

    __slots__ = ("id", "code", "name_cn", "unit", "category")

    def __init__(self, row) -> None:
        self.id = row.id
        self.code = row.code
        self.name_cn = row.name_cn
        self.unit = row.unit
        self.category = row.category


def _create_standard_indicator(
    db,
    decision: AlignDecision,
    *,
    raw_name: str,
    raw_unit,
    raw_value,
):
    """新指标落库（先过代码侧准入门槛，再写库）。

    AI 只是"建议"，能否成为标准主数据由代码决定：
    - 名称必须通过 `is_plausible_indicator_name`（拦截编号/页码/结论措辞等噪声）；
    - code 必须唯一，不覆盖既有标准项；
    - AI 结论写回 aliases，后续同类写法直接精确命中，不再调模型。
    """
    from app.common.ids import new_id
    from app.db.models import IndicatorDictionary

    ok, reason = is_plausible_indicator_name(decision.display_name or raw_name)
    if not ok:
        logger.info(
            "align.rejected_indicator name=%r code=%s reason=%s",
            decision.display_name or raw_name, decision.code, reason,
        )
        return None

    try:
        existing = db.query(IndicatorDictionary).filter_by(code=decision.code).first()
        if existing is not None:
            return existing
        row = IndicatorDictionary(
            id=new_id(),
            code=decision.code,
            name_cn=decision.display_name or raw_name,
            category=decision.category or "其他",
            unit=decision.unit or raw_unit,
            aliases=sorted({raw_name, *(decision.aliases or [])}),
            is_top_trend=False,
            active=True,
        )
        db.add(row)
        db.flush()
        logger.info(
            "align.created code=%s name=%s aliases=%s raw=%s",
            row.code, row.name_cn, row.aliases, raw_name,
        )
        return row
    except Exception:  # noqa: BLE001  新指标失败不应阻断抽取
        db.rollback()
        logger.exception("align.create_failed name=%s", raw_name)
        return None
