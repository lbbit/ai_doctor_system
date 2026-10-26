"""PDF 表格抽取（确定性，不依赖 LLM）→ 指标行。

真实体检报告（爱康/美年等）的正文几乎都以表格排版，pdfplumber 能直接还原
`检查项目 | 测量结果 | 单位 | 异常描述 | 正常参考值` 这样的结构：

    p5 | 检查项目 | 测量结果 |  | 单位 | 异常描述 | 正常参考值
    p5 | 身高 | 179.5 |  | cm |  |
    p5 | 体重指数 | 32.4 |  |  | ↑ | 18.5 -- 23.99
    p5 | 收缩压 | 116 |  | mmHg |  | 90 -- 139

相比逐页 VLM：更快（毫秒级）、零 token、字段边界确定，因此作为首选链路；
抽不到足够指标时再降级到视觉模型（VLM）/ OCR。
"""

import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.preprocessing.noise import is_noise_measurement
from app.preprocessing.parsers.rule_parser import (
    _parse_flag,
    _parse_ref,
    _parse_value,
    _extract_date,
)
from app.preprocessing.textnorm import normalize_cell

logger = logging.getLogger("app.parsers.table")

# 表头关键词 → 列语义（命中即采用；兼容不同机构的表头措辞）
_NAME_KEYS = ("检查项目", "检验项目", "项目名称", "指标", "项目")
_VALUE_KEYS = (
    "测量结果", "检验结果", "检查结果", "结果", "数值", "测定值",
    # 描述型表（一般检查/内外科）用“检查所见”承载结果值
    "检查所见", "检查描述", "所见",
)
_UNIT_KEYS = ("单位",)
_REF_KEYS = ("正常参考值", "参考值", "参考范围", "参考区间", "正常范围")
_FLAG_KEYS = ("异常描述", "异常提示", "提示", "异常", "标志", "结果提示")

# 这些表头出现在“专家建议”“阳性结果汇总”等非指标表里，应跳过整张表
_EXCLUDE_HEADER = re.compile(r"建议|复查|结论|科普|目录|预约|说明|须知")
# “历年对比”表：同指标多期历史值（如“2020年09月 | 2021年09月”两列），
# 当成本次指标会与正文项冲突（实测：体重指数 36 vs 32.4）。不取。
_HISTORY_HEADER = re.compile(r"历年|对比|比较|趋势|既往")
_HISTORY_CELL = re.compile(r"(19|20)\d{2}\s*[年\-/\.]\s*\d{1,2}\s*月?")
# “检查项目 | 检查所见”是描述型表格，仍可抽取（value 为文本），但不算数值指标
_FINDING_HEADER = re.compile(r"检查所见|检查描述|所见")


def _norm_cell(value: Any) -> str:
    """单元格归一化：NFKC（康熙部首→标准汉字）+ 空白折叠。

    必需：真实报告会用兼容字形（如「检查项⽬」的 U+2F6C），不归一化会导致
    表头完全匹配不到、整份报告 0 指标（2026-10-21 实测）。
    """
    return normalize_cell(value)


def _match_col(header: Sequence[str], keys: Iterable[str]) -> Optional[int]:
    for index, cell in enumerate(header):
        text = _norm_cell(cell)
        if not text:
            continue
        for key in keys:
            if key in text:
                return index
    return None


def _find_header_row(rows: Sequence[Sequence[Any]]) -> Optional[int]:
    """定位表头行：必须同时含“名称列”和（结果列 or 参考列）。"""
    for index, row in enumerate(rows):
        cells = [_norm_cell(c) for c in row]
        joined = " ".join(cells)
        if _EXCLUDE_HEADER.search(joined) and not _REF_KEYS[0] in joined:
            continue
        name_col = _match_col(cells, _NAME_KEYS)
        value_col = _match_col(cells, _VALUE_KEYS)
        ref_col = _match_col(cells, _REF_KEYS)
        if name_col is not None and (value_col is not None or ref_col is not None):
            return index
    return None


def table_rows_to_observations(
    tables: Sequence[Sequence[Sequence[Any]]],
    *,
    report_date: Optional[str] = None,
    doc_type: str = "",
) -> List[dict]:
    """把 pdfplumber 的表格结果转成 observations（纯函数，便于单测）。"""
    observations: List[dict] = []
    seen: set = set()

    for table in tables:
        if not table:
            continue
        header_index = _find_header_row(table)
        if header_index is None:
            continue
        header = [_norm_cell(c) for c in table[header_index]]
        # 历年对比表：同指标多期历史值，不作为本次报告指标
        if _HISTORY_HEADER.search(" ".join(header)) or any(
            _HISTORY_CELL.search(_norm_cell(c))
            for row in table[header_index : header_index + 3]
            for c in row
        ):
            continue
        name_col = _match_col(header, _NAME_KEYS)
        value_col = _match_col(header, _VALUE_KEYS)
        unit_col = _match_col(header, _UNIT_KEYS)
        ref_col = _match_col(header, _REF_KEYS)
        flag_col = _match_col(header, _FLAG_KEYS)
        if name_col is None:
            continue
        finding_table = bool(_FINDING_HEADER.search(" ".join(header)))

        for row in table[header_index + 1 :]:
            cells = [_norm_cell(c) for c in row]
            if not any(cells):
                continue
            raw_name = cells[name_col] if name_col < len(cells) else ""
            if not raw_name or is_noise_measurement(raw_name):
                continue
            # 表头重复行（分页续排常见）
            if _match_col(cells, _NAME_KEYS) == name_col and any(
                key in raw_name for key in _NAME_KEYS
            ):
                continue

            raw_value = cells[value_col] if value_col is not None and value_col < len(cells) else ""
            unit = cells[unit_col] if unit_col is not None and unit_col < len(cells) else ""
            ref_text_raw = cells[ref_col] if ref_col is not None and ref_col < len(cells) else ""
            flag_raw = cells[flag_col] if flag_col is not None and flag_col < len(cells) else ""

            value = _parse_value(raw_value) if raw_value else None
            low, high, text_ref = _parse_ref(ref_text_raw)
            # 异常描述列可能只有箭头（↑/↓）或文字（增高/降低）
            combined_rest = f"{flag_raw} {ref_text_raw}"
            value_num = value if isinstance(value, (int, float)) else None
            flag = _parse_flag(combined_rest, value_num, low, high)
            if flag is None and flag_raw:
                if re.search(r"增[高强]|↑|偏高", flag_raw):
                    flag = "H"
                elif re.search(r"降[低减]|↓|偏低", flag_raw):
                    flag = "L"
                elif re.search(r"异常|阳性", flag_raw):
                    flag = "A"
                elif re.search(r"正常", flag_raw):
                    flag = "N"

            if value is None and not text_ref and not raw_value:
                continue

            key = (raw_name, str(value), unit)
            if key in seen:
                continue
            seen.add(key)

            numeric = isinstance(value, (int, float))
            observations.append(
                {
                    "raw_name": raw_name[:128],
                    "value": value if value is not None else (raw_value or None),
                    "unit": unit or None,
                    "ref_low": low,
                    "ref_high": high,
                    "ref_text": text_ref,
                    "abnormal_flag": flag,
                    "occurred_at": report_date,
                    # 表格数值项可信度高；描述型文本（检查所见/结论）低一档
                    "confidence": 0.92 if numeric else (0.6 if finding_table else 0.7),
                    "bbox": None,
                    "status": "proposed",
                    "code": None,
                    "dictionary_id": None,
                    "display_name": raw_name,
                }
            )

    return observations


def extract_pdf_tables(content: bytes, *, max_pages: Optional[int] = None) -> List[list]:
    """调用 pdfplumber 抽取表格；依赖缺失或 PDF 损坏时返回空列表（降级到下一解析器）。"""
    try:
        import pdfplumber
    except Exception:  # noqa: BLE001
        logger.info("table.parser_unavailable pdfplumber missing")
        return []

    import io

    tables: List[list] = []
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            pages = pdf.pages[: max_pages] if max_pages else pdf.pages
            for page in pages:
                try:
                    page_tables = page.extract_tables() or []
                except Exception:  # noqa: BLE001  单页失败继续
                    continue
                tables.extend(page_tables)
    except Exception as exc:  # noqa: BLE001
        logger.info("table.extract_failed err=%s", exc)
        return []
    return tables


class TableParser:
    """首选解析器：PDF 表格 → 指标行（确定性、零 LLM 调用）。

    仅在 PDF 且表格能产出足够指标时成功，否则返回 success=False 让链路继续。
    """

    name = "table"
    supported_doc_types = {"lab_report", "imaging_report", "visit_record", "other"}
    min_observations = 3

    def available(self) -> bool:
        try:
            import pdfplumber  # noqa: F401
        except Exception:  # noqa: BLE001
            return False
        return True

    def parse(self, inp: Dict[str, Any]) -> Dict[str, Any]:
        content = inp.get("raw_content")
        if not content:
            return _fail("无原始文件内容（表格解析仅适用于 PDF）")
        pages = inp.get("pages") or []
        text = "\n".join((p.get("text") or "") for p in pages)
        report_date = _extract_date(text)
        tables = extract_pdf_tables(content)
        if not tables:
            return _fail("未检测到表格")
        observations = table_rows_to_observations(
            tables,
            report_date=report_date,
            doc_type=inp.get("doc_type") or "",
        )
        if len(observations) < self.min_observations:
            return _fail(f"表格指标过少（{len(observations)} 项）")
        logger.info(
            "table.done doc=%s tables=%s obs=%s",
            inp.get("document_id"),
            len(tables),
            len(observations),
        )
        return {
            "parser_name": self.name,
            "report_date": report_date,
            "doc_type": inp.get("doc_type") or "",
            "events": [],
            "observations": observations,
            "source_text": text,
            "success": True,
            "error": None,
        }


def _fail(error: str) -> Dict[str, Any]:
    return {
        "parser_name": "table",
        "report_date": None,
        "doc_type": "",
        "events": [],
        "observations": [],
        "source_text": "",
        "success": False,
        "error": error,
    }
