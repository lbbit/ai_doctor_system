"""P0 兜底解析器：pypdf 文本 + 正则规则解析（LLM 不可用仍可产出低置信草稿）。

另含 MockParser（离线演示降级：VLM 不可用且规则解析无文本时，产出
明确标注的占位草稿，保证"上传→抽取→确认→入库"演示闭环可跑通）。
"""

import re
from datetime import date
from typing import List, Optional, Tuple

from app.preprocessing.extractor import validate_derived_record
from app.preprocessing.parser_base import ParseInput, ParserResult

# ---- 行级启发式 ----
_DATE_PATTERNS = [
    re.compile(r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})\s*日?"),
]
_DATE_KEYWORDS = ("检查日期", "采样日期", "报告日期", "体检日期", "就诊日期", "日期")

_OBS_LINE = re.compile(
    r"^(?P<name>[一-龥A-Za-z0-9()（）/·％%\-、]{2,30}?)"
    r"[\s:：]+"
    r"(?P<value>[<>≤≥<>\s]*-?\d+(?:\.\d+)?)"
    r"[\s]*"
    r"(?P<unit>[a-zA-Zμ%/\.IU]+)?"
    r"[\s]*"
    r"(?P<rest>.*)$"
)
_REF_RANGE_NUM = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*(?:-{1,2}|~|～|—|–|至)\s*(-?\d+(?:\.\d+)?)"
)
_FLAG_TOKENS = {"H": "H", "L": "L", "↑": "H", "↓": "L", "高": "H", "低": "L", "A": "A"}

# 表头/页眉噪声行（跳过）
_NOISE = re.compile(
    r"医院|报告|检验报告|姓名|性别|年龄|样本|条码|编号|送检|检验者|审核|打印|"
    r"门诊|住院|科室|床号|科别|住院号|门诊号|标本|仪器|方法|单位：|备注|"
    # 联系方式/机构信息：真实报告页眉常见，绝不应成为指标（2026-10-21 实测缺陷）
    r"电话|热线|专线|客服|传真|邮箱|邮件|网址|网站|邮编|地址|版权|公众号|微信|扫码|二维码|"
    r"体检号|卡\s*号|项目号|会员号|诊疗卡|健康管理"
)

# 整行形如“联系方式”（无中文指标名，只有电话/网址/邮箱）
_CONTACT_LINE = re.compile(
    r"^[\s·:：]*("
    r"(www\.|https?://|mailto:)"
    r"|(\+?\d[\d\s\-()]{6,})"
    r")[\s]*$",
    re.I,
)

_SKIP_VALUES = {"阴性", "阳性", "-", "—", "正常", "未检出", "无"}


def _extract_date(text: str) -> Optional[str]:
    """优先取关键词附近的日期（报告内检查日期，AC-5.3 双时间轴）。"""
    for kw in _DATE_KEYWORDS:
        idx = text.find(kw)
        if idx >= 0:
            window = text[idx : idx + 40]
            for pat in _DATE_PATTERNS:
                m = pat.search(window)
                if m:
                    return _norm_date(*m.groups())
    for pat in _DATE_PATTERNS:
        m = pat.search(text)
        if m:
            return _norm_date(*m.groups())
    return None


def _norm_date(y: str, mo: str, d: str) -> Optional[str]:
    try:
        return date(int(y), int(mo), int(d)).isoformat()
    except ValueError:
        return None


def _parse_ref(rest: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    rest = (rest or "").strip()
    if not rest:
        return None, None, None
    m = _REF_RANGE_NUM.search(rest)
    if m:
        try:
            return float(m.group(1)), float(m.group(2)), None
        except ValueError:
            pass
    # 文本参考（阴性 等）
    text_ref = re.sub(r"[HhLl↑↓]", "", rest).strip()
    return None, None, (text_ref or None)


def _parse_flag(rest: str, value_num: Optional[float],
                low: Optional[float], high: Optional[float]) -> Optional[str]:
    for tok in _FLAG_TOKENS:
        if re.search(rf"(?<![A-Za-z]){re.escape(tok)}(?![A-Za-z])", rest or ""):
            if tok in ("H", "L"):
                return _FLAG_TOKENS[tok]
    if value_num is not None:
        if high is not None and value_num > high:
            return "H"
        if low is not None and value_num < low:
            return "L"
        if high is not None or low is not None:
            return "N"
    if rest and any(ch in rest for ch in ("↑", "↓")):
        return "H" if "↑" in rest else "L"
    return None


def _parse_value(raw: str):
    raw = (raw or "").strip().replace(" ", "")
    if raw in _SKIP_VALUES:
        return raw
    try:
        if re.fullmatch(r"-?\d+", raw):
            return int(raw)
        if re.fullmatch(r"-?\d+\.\d+", raw):
            return float(raw)
    except ValueError:
        pass
    m = re.match(r"^[<>≤≥]*(-?\d+(?:\.\d+)?)", raw)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return raw or None


def rule_extract(text: str, doc_type: str) -> dict:
    """从 PDF 文本抽取 observations（低置信 0.45~0.65）。"""
    observations: list[dict] = []
    report_date = _extract_date(text)

    for line in text.splitlines():
        line = line.strip()
        if not line or len(line) > 200:
            continue
        if _NOISE.search(line):
            # 噪声行仍可能含日期（如 "检查日期：2026-03-12"），但不含指标
            continue
        if _CONTACT_LINE.match(line):
            # 纯电话/网址/邮箱行（如 "400 081 8899"）
            continue
        m = _OBS_LINE.match(line)
        if not m:
            continue
        name = m.group("name").strip()
        value = _parse_value(m.group("value") or "")
        if value is None:
            continue
        # 排除“报告编号 20260312 09:00”类伪指标
        if re.fullmatch(r"[\d\-/:.]+", name):
            continue
        if re.search(r"^(编号|编码|号)$", name):
            continue
        unit = (m.group("unit") or "").strip() or None
        rest = (m.group("rest") or "").strip()
        low, high, ref_text = _parse_ref(rest)
        value_num = value if isinstance(value, (int, float)) else None
        flag = _parse_flag(rest, value_num, low, high)
        conf = 0.62 if (low is not None or ref_text) else 0.48
        observations.append(
            {
                "raw_name": name,
                "value": value,
                "unit": unit,
                "ref_low": low,
                "ref_high": high,
                "ref_text": ref_text,
                "abnormal_flag": flag,
                "occurred_at": report_date,
                "confidence": conf,
                "bbox": None,
                "status": "proposed",
                "code": None,
                "dictionary_id": None,
                "display_name": name,
            }
        )

    return {
        "report_date": report_date,
        "doc_type_suggest": doc_type,
        "events": [],
        "observations": observations,
        "mock": False,
    }


class RuleParser:
    """兜底：pypdf 文本 + 正则（LLM 不可用时的主力）。"""

    name = "rule"
    supported_doc_types = {"lab_report", "imaging_report", "visit_record", "other"}

    def available(self) -> bool:
        return True

    def parse(self, inp: ParseInput) -> ParserResult:
        pages = inp.get("pages") or []
        text = "\n".join((p.get("text") or "") for p in pages).strip()
        doc_type = inp.get("doc_type") or "other"

        if not text:
            return _fail("无可解析文本（图片需 VLM 或手动录入）")

        payload = rule_extract(text, doc_type)
        if not payload["observations"]:
            return _fail("规则解析未提取到任何指标")

        return {
            "parser_name": self.name,
            "report_date": payload["report_date"],
            "doc_type": doc_type,
            "events": payload["events"],
            "observations": payload["observations"],
            "source_text": text,
            "success": True,
            "error": None,
        }


class MockParser:
    """离线演示降级（仅在 LLM **未配置** 时启用）。

    重要：LLM 已配置但调用失败（超时/网关 504）时不应降级到本解析器，
    否则用户会看到“待确认”但内容全是占位文字的报告（2026-10-21 用户反馈）。
    此时应让文档 failed 并给出可读原因，用户可重试。
    """

    name = "mock"
    supported_doc_types = {"lab_report", "imaging_report", "visit_record", "other"}

    def available(self) -> bool:
        """仅当 LLM 未配置（离线演示）时可用。"""
        try:
            from app.llm.client import get_llm_client

            return not get_llm_client("extraction").available()
        except Exception:  # noqa: BLE001
            return True

    def parse(self, inp: ParseInput) -> ParserResult:
        today = date.today().isoformat()
        doc_type = inp.get("doc_type") or "other"
        payload = {
            "report_date": today,
            "doc_type_suggest": doc_type,
            "events": [
                {
                    "type": "other",
                    "title": "（离线演示）已上传健康报告",
                    "narrative": (
                        "AI 抽取当前不可用（离线或未配置 LLM）。"
                        "此为演示占位记录：请在确认卡片中编辑为真实信息，或全部拒绝后手动整理。"
                    ),
                    "diagnosis": None,
                    "department": None,
                    "occurred_at": today,
                    "confidence": 0.2,
                }
            ],
            "observations": [],
            "mock": True,
        }
        validate_derived_record(payload, doc_type)
        return {
            "parser_name": self.name,
            "report_date": today,
            "doc_type": doc_type,
            "events": payload["events"],
            "observations": payload["observations"],
            "source_text": "",
            "success": True,
            "error": None,
            "mock": True,
        }


def _fail(error: str) -> ParserResult:
    return {
        "parser_name": "rule",
        "report_date": None,
        "doc_type": "",
        "events": [],
        "observations": [],
        "source_text": "",
        "success": False,
        "error": error,
    }
