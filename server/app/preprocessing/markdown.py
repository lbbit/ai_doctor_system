"""类型化 Markdown 生成器（R1 T104）。

目标：把原始资料转成**可读、保真、可定位**的 Markdown 副本。

四条硬约束（决定了它不是"让模型写个总结"）：
1. **保真**：原文顺序、表格列序、标题层级由代码按结构生成，模型不得重排；
2. **可定位**：每页带 `<!-- source:page=N -->` 锚点，块带 `block_id`；
3. **可区分**：AI 推断 / OCR 不确定 / 人工修订用显式标记，不混入原文；
4. **不越界**：影像/心电只写"报告结论 + 原件引用 + 复读注意事项"，
   **不把 VLM 观察伪装成报告原文**。

不同记录类型的产物形态不同（见 `TEMPLATES`）：
- 检验/检查单：指标表 + 参考区间 + 异常标记；
- 病历/出院小结：章节结构（主诉/现病史/诊断/治疗/用药/出院医嘱）；
- 体检报告：目录 + 分项结论；
- 影像/心电：检查信息 + 报告所见/结论 + 原件引用；
- 用药记录：药品/剂量/频次原文；
- 纯文本：原文分段 + 章节识别。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("app.preprocessing.markdown")

# 生成器版本（写进 front matter，便于追溯产物差异）
MARKDOWN_GENERATOR = "markdown/1.0"

# 记录类型 → 模板
TEMPLATE_LAB = "lab"
TEMPLATE_VISIT = "visit"
TEMPLATE_DISCHARGE = "discharge"
TEMPLATE_CHECKUP = "checkup"
TEMPLATE_IMAGING = "imaging"
TEMPLATE_MEDICATION = "medication"
TEMPLATE_TEXT = "text"
TEMPLATE_GENERIC = "generic"

TEMPLATES = (
    TEMPLATE_LAB,
    TEMPLATE_VISIT,
    TEMPLATE_DISCHARGE,
    TEMPLATE_CHECKUP,
    TEMPLATE_IMAGING,
    TEMPLATE_MEDICATION,
    TEMPLATE_TEXT,
    TEMPLATE_GENERIC,
)

# 病历类常见章节（按顺序匹配）
_VISIT_SECTIONS = (
    ("主诉", ("chief_complaint", "主诉")),
    ("现病史", ("present_illness", "现病史", "病情简介")),
    ("既往史", ("past_history", "既往史")),
    ("个人史", ("personal_history", "个人史")),
    ("家族史", ("family_history", "家族史")),
    ("体格检查", ("physical_exam", "体格检查", "查体")),
    ("辅助检查", ("auxiliary_exam", "辅助检查", "检查结果")),
    ("诊断", ("diagnosis", "诊断", "临床诊断")),
    ("处理意见", ("treatment", "处理意见", "诊疗计划", "治疗方案")),
    ("用药", ("medication", "用药", "医嘱用药")),
)

# 出院小结常见章节
_DISCHARGE_SECTIONS = (
    ("入院情况", ("admission", "入院情况")),
    ("入院诊断", ("admission_diagnosis", "入院诊断")),
    ("住院经过", ("course", "住院经过", "诊疗经过")),
    ("出院诊断", ("discharge_diagnosis", "出院诊断")),
    ("出院医嘱", ("discharge_advice", "出院医嘱", "出院带药")),
)

# 药名/剂量/频次（用于用药记录的原文结构化）
_MED_RE = re.compile(
    r"(?P<name>[A-Za-z一-龥][A-Za-z0-9一-龥\-\(\)]{0,30}?)"
    r"\s*(?P<dose>\d+(?:\.\d+)?\s*(?:mg|g|ml|mL|IU|片|粒|袋|支|单位)?)?"
    r"\s*(?P<freq>每日\s*\d+\s*次|每天\s*\d+\s*次|bid|tid|qd|qn|prn|po|iv|im|必要时|睡前|餐后|餐前)?",
    re.I,
)


@dataclass
class MarkdownArtifact:
    """Markdown 产物（含元数据与锚点索引）。"""

    content: str
    template: str
    generator: str = MARKDOWN_GENERATOR
    blocks: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_asset_meta(self) -> Dict[str, Any]:
        return {
            "template": self.template,
            "generator": self.generator,
            "blockCount": len(self.blocks),
            "warnings": self.warnings,
        }


def _yaml_escape(value: Any) -> str:
    """YAML 标量转义（front matter 只放元数据引用，不放正文）。

    只在**真的会破坏 YAML 解析**时才加引号：
    - 含 `: `（冒号+空格）、`#`（注释符）、引号、括号、逗号等结构字符；
    - 首尾有空白，或以特殊字符开头（`-`/`?`/`:` 等会触发指示符语义）。

    注意：普通连字符（如 `doc-1`）**不需要**引号——过度加引号会让
    front matter 变得难读且不利于人工核对。
    """
    if value is None:
        return "null"
    text = str(value)
    if text == "":
        return '""'
    needs_quote = (
        ": " in text
        or text.endswith(":")
        or "#" in text
        or '"' in text
        or "'" in text
        or any(ch in text for ch in "[]{},&*!|>%@`")
        or text != text.strip()
        or text[0] in "-?:,[]{}#&*!|>'\"%@`"
    )
    if not needs_quote:
        return text
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_front_matter(
    *,
    document_id: str,
    record_type: Optional[str],
    template: str,
    generator: str,
    occurred: Optional[str] = None,
    time_precision: Optional[str] = None,
    facility: Optional[str] = None,
    department: Optional[str] = None,
    file_name: Optional[str] = None,
    page_count: Optional[int] = None,
) -> str:
    """front matter：仅元数据引用，不重复正文，也不放任何密钥。"""
    lines = [
        "---",
        f"documentId: {_yaml_escape(document_id)}",
        f"recordType: {_yaml_escape(record_type)}",
        f"template: {_yaml_escape(template)}",
        f"generator: {_yaml_escape(generator)}",
    ]
    if occurred:
        lines.append(f"occurredAt: {_yaml_escape(occurred)}")
    if time_precision:
        lines.append(f"timePrecision: {_yaml_escape(time_precision)}")
    if facility:
        lines.append(f"facility: {_yaml_escape(facility)}")
    if department:
        lines.append(f"department: {_yaml_escape(department)}")
    if file_name:
        lines.append(f"fileName: {_yaml_escape(file_name)}")
    if page_count is not None:
        lines.append(f"pageCount: {int(page_count)}")
    lines.append("---")
    return "\n".join(lines)


def _page_anchor(page_no: int) -> str:
    return f"<!-- source:page={page_no} -->"


def _block_anchor(page_no: int, block_id: str) -> str:
    return f"<!-- block:{page_no}:{block_id} -->"


def _escape_cell(value: Any) -> str:
    """表格单元格转义（竖线与换行会破坏 Markdown 表格结构）。"""
    if value is None:
        return ""
    text = str(value).strip()
    text = text.replace("|", "\\|")
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return text


def _render_table(headers: List[str], rows: List[List[Any]]) -> str:
    """渲染 Markdown 表格（列序严格保持，缺列补空）。"""
    if not headers:
        return ""
    head = "| " + " | ".join(_escape_cell(h) for h in headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    body = []
    for row in rows:
        cells = list(row) + [""] * (len(headers) - len(row))
        body.append("| " + " | ".join(_escape_cell(c) for c in cells[: len(headers)]) + " |")
    return "\n".join([head, sep] + body)


def _pick(payload: Dict[str, Any], keys) -> Any:
    for key in keys:
        val = payload.get(key)
        if val:
            return val
    return None


def _render_lab(payload: Dict[str, Any], pages: List[Dict[str, Any]]) -> str:
    """检验/检查单：指标表 + 原文页。"""
    out: List[str] = []
    observations = payload.get("observations") or []
    if observations:
        headers = ["项目", "结果", "单位", "参考区间", "异常"]
        rows = []
        for o in observations:
            rows.append([
                o.get("display_name") or o.get("raw_name") or "",
                o.get("value") if o.get("value") is not None else "",
                o.get("unit") or "",
                _format_ref_range(o),
                _flag_label(o.get("abnormal_flag")),
            ])
        out.append("## 检验/检查项目")
        out.append(_render_table(headers, rows))
        out.append("")
    out.extend(_render_raw_pages(pages))
    return "\n".join(out)


def _format_ref_range(obs: Dict[str, Any]) -> str:
    low = obs.get("ref_low")
    high = obs.get("ref_high")
    if low is not None and high is not None:
        return f"{low} ~ {high}"
    if obs.get("ref_text"):
        return str(obs["ref_text"])
    return ""


def _flag_label(flag: Optional[str]) -> str:
    return {"H": "偏高", "L": "偏低", "A": "异常", "N": ""}.get(
        (flag or "").upper(), ""
    )


def _render_visit(
    payload: Dict[str, Any], pages: List[Dict[str, Any]], sections
) -> str:
    """病历/出院小结：按章节结构呈现（原文顺序由 section 顺序固定）。"""
    out: List[str] = []
    for title, keys in sections:
        value = _pick(payload, keys)
        if not value:
            continue
        out.append(f"## {title}")
        out.append(str(value).strip())
        out.append("")
    if not out:
        out.append("## 正文")
        out.append("")
    out.extend(_render_raw_pages(pages))
    return "\n".join(out)


def _render_checkup(payload: Dict[str, Any], pages: List[Dict[str, Any]]) -> str:
    """体检报告：分项结论 + 原文页。"""
    out: List[str] = []
    sections = payload.get("sections") or []
    if sections:
        out.append("## 体检项目")
        headers = ["项目", "结果", "参考", "提示"]
        rows = []
        for s in sections:
            if isinstance(s, dict):
                rows.append([
                    s.get("name") or "", s.get("result") or "",
                    s.get("reference") or "", s.get("flag") or "",
                ])
        out.append(_render_table(headers, rows))
        out.append("")
    out.extend(_render_raw_pages(pages))
    return "\n".join(out)


def _render_imaging(
    payload: Dict[str, Any], pages: List[Dict[str, Any]]
) -> str:
    """影像/心电：报告所见与结论 + 原件引用（**不写 VLM 观察**）。"""
    out: List[str] = []
    modality = payload.get("modality") or payload.get("exam_type")
    if modality:
        out.append(f"## 检查项目\n{modality}")
        out.append("")
    findings = payload.get("findings")
    if findings:
        out.append("## 报告所见")
        out.append(str(findings).strip())
        out.append("")
    conclusion = payload.get("conclusion") or payload.get("impression")
    if conclusion:
        out.append("## 报告结论")
        out.append(str(conclusion).strip())
        out.append("")
    out.append(
        "> 说明：以上为报告原文要点。图像的详细判读需以原件为准，"
        "系统不把图像识别结果当作报告结论。"
    )
    out.append("")
    out.extend(_render_raw_pages(pages))
    return "\n".join(out)


def _render_medication(payload: Dict[str, Any], pages: List[Dict[str, Any]]) -> str:
    """用药记录：药名/剂量/频次（原文抽取，不做用药建议）。"""
    out: List[str] = []
    meds = payload.get("medications") or []
    if meds:
        headers = ["药品", "剂量", "频次", "备注"]
        rows = []
        for m in meds:
            if isinstance(m, dict):
                rows.append([
                    m.get("name") or "", m.get("dose") or "",
                    m.get("frequency") or "", m.get("note") or "",
                ])
        out.append("## 用药记录")
        out.append(_render_table(headers, rows))
        out.append("")
    out.append("> 说明：本节仅为报告原文中的用药信息整理，不构成用药建议。")
    out.append("")
    out.extend(_render_raw_pages(pages))
    return "\n".join(out)


def _render_text(payload: Dict[str, Any], pages: List[Dict[str, Any]]) -> str:
    """纯文本：原文分段（不摘要）。"""
    out: List[str] = []
    out.append("## 原文")
    out.append("")
    out.extend(_render_raw_pages(pages))
    return "\n".join(out)


def _render_raw_pages(pages: List[Dict[str, Any]]) -> List[str]:
    """原文页（带页锚点与块锚点）。"""
    out: List[str] = []
    for page in pages or []:
        page_no = page.get("page_no") or 1
        out.append(_page_anchor(page_no))
        out.append("")
        text = (page.get("text") or "").strip()
        if text:
            out.append(text)
            out.append("")
        for block in page.get("blocks") or []:
            block_id = block.get("block_id")
            if not block_id:
                continue
            out.append(_block_anchor(page_no, block_id))
            btext = (block.get("text") or "").strip()
            if btext:
                out.append(btext)
                out.append("")
    return out


def pick_template(
    record_type: Optional[str],
    doc_type: Optional[str],
    payload: Optional[Dict[str, Any]] = None,
) -> str:
    """按记录类型/文档类型选择模板。"""
    payload = payload or {}
    rt = (record_type or "").lower()
    if rt in ("lab_report", "lab", "test_report"):
        return TEMPLATE_LAB
    if rt == "visit_record":
        return TEMPLATE_VISIT
    if rt == "discharge_summary":
        return TEMPLATE_DISCHARGE
    if rt == "checkup_report":
        return TEMPLATE_CHECKUP
    if rt in ("imaging_report", "ecg"):
        return TEMPLATE_IMAGING
    if rt == "medication_record":
        return TEMPLATE_MEDICATION
    if rt == "text_note":
        return TEMPLATE_TEXT
    if doc_type == "lab_report":
        return TEMPLATE_LAB
    if doc_type == "imaging_report":
        return TEMPLATE_IMAGING
    if doc_type == "visit_record":
        return TEMPLATE_VISIT
    return TEMPLATE_GENERIC


def render(
    *,
    document_id: str,
    record_type: Optional[str],
    doc_type: Optional[str],
    payload: Optional[Dict[str, Any]] = None,
    pages: Optional[List[Dict[str, Any]]] = None,
    occurred: Optional[str] = None,
    time_precision: Optional[str] = None,
    facility: Optional[str] = None,
    department: Optional[str] = None,
    file_name: Optional[str] = None,
    generator: str = MARKDOWN_GENERATOR,
) -> MarkdownArtifact:
    """生成 Markdown 副本（结构由代码决定，模型不得重排）。"""
    payload = payload or {}
    pages = pages or []
    template = pick_template(record_type, doc_type, payload)

    front = render_front_matter(
        document_id=document_id,
        record_type=record_type,
        template=template,
        generator=generator,
        occurred=occurred,
        time_precision=time_precision,
        facility=facility,
        department=department,
        file_name=file_name,
        page_count=len(pages) or None,
    )

    if template == TEMPLATE_LAB:
        body = _render_lab(payload, pages)
    elif template == TEMPLATE_VISIT:
        body = _render_visit(payload, pages, _VISIT_SECTIONS)
    elif template == TEMPLATE_DISCHARGE:
        body = _render_visit(payload, pages, _DISCHARGE_SECTIONS)
    elif template == TEMPLATE_CHECKUP:
        body = _render_checkup(payload, pages)
    elif template == TEMPLATE_IMAGING:
        body = _render_imaging(payload, pages)
    elif template == TEMPLATE_MEDICATION:
        body = _render_medication(payload, pages)
    elif template == TEMPLATE_TEXT:
        body = _render_text(payload, pages)
    else:
        body = "\n".join(_render_raw_pages(pages))

    content = front + "\n\n" + body.strip() + "\n"

    # 收集块索引（供检索与定位使用）
    blocks: List[Dict[str, Any]] = []
    for page in pages:
        page_no = page.get("page_no") or 1
        for block in page.get("blocks") or []:
            if block.get("block_id"):
                blocks.append({
                    "blockId": block["block_id"],
                    "pageNo": page_no,
                    "text": block.get("text") or "",
                })

    warnings: List[str] = []
    if not pages:
        warnings.append("无页级文本：副本仅含元数据，未包含原文")
    if template == TEMPLATE_IMAGING:
        warnings.append("影像类副本不含图像判读结论，以原件为准")

    return MarkdownArtifact(
        content=content, template=template, generator=generator,
        blocks=blocks, warnings=warnings,
    )
