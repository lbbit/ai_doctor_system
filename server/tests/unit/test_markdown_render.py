"""类型化 Markdown 副本（R1 T104）。

契约（被测）：
1. **保真**：表格列序与原文一致；单元格里的竖线/换行被转义（不破坏表格）；
2. **可定位**：每页有 `<!-- source:page=N -->` 锚点，块有块锚点；
3. **可区分**：AI 推断与原文分区呈现（front matter 只放元数据，不放正文）；
4. **不越界**：影像类**不**把 VLM 观察写成报告结论；
5. 病历类按固定章节顺序呈现；
6. 无页文本时不得生成"看起来完整"的空副本（要有警告）。
"""

from __future__ import annotations

import re

import pytest

from app.preprocessing import markdown as md


def _pages(text="血红蛋白 145 g/L", page_no=1, blocks=None):
    return [{
        "page_no": page_no,
        "text": text,
        "blocks": blocks if blocks is not None else [],
    }]


def _render(**kwargs):
    base = dict(
        document_id="doc-1",
        record_type="lab_report",
        doc_type="lab_report",
        payload={},
        pages=_pages(),
    )
    base.update(kwargs)
    return md.render(**base)


# --------------------------------------------------------------------------
# 结构与锚点
# --------------------------------------------------------------------------


def test_front_matter_contains_metadata_not_body():
    art = _render(facility="市第一医院")
    assert art.content.startswith("---\n")
    assert "documentId: doc-1" in art.content
    assert "facility: 市第一医院" in art.content
    # front matter 结束标记只出现一次
    assert art.content.count("\n---\n") == 1


def test_front_matter_escapes_special_chars():
    art = _render(facility="医院: 第一分院")
    assert "facility:" in art.content
    # 冒号值必须被引号包裹，避免 YAML 解析歧义
    assert '"医院: 第一分院"' in art.content


def test_page_anchor_present():
    art = _render(pages=_pages(page_no=3))
    assert "<!-- source:page=3 -->" in art.content


def test_block_anchor_present():
    art = _render(
        pages=_pages(blocks=[{"block_id": "b7", "text": "血糖 6.8"}])
    )
    assert "<!-- block:1:b7 -->" in art.content
    assert art.blocks and art.blocks[0]["blockId"] == "b7"


# --------------------------------------------------------------------------
# 表格保真
# --------------------------------------------------------------------------


def test_lab_table_preserves_column_order():
    payload = {
        "observations": [
            {
                "display_name": "血红蛋白", "value": 145, "unit": "g/L",
                "ref_low": 130, "ref_high": 175, "abnormal_flag": "N",
            },
            {
                "display_name": "白细胞计数", "value": 6.8, "unit": "10^9/L",
                "ref_low": 3.5, "ref_high": 9.5, "abnormal_flag": "H",
            },
        ]
    }
    art = _render(payload=payload)
    lines = [ln for ln in art.content.splitlines() if ln.startswith("|")]
    assert lines[0] == "| 项目 | 结果 | 单位 | 参考区间 | 异常 |"
    assert "| 血红蛋白 | 145 | g/L | 130 ~ 175 |  |" in lines
    assert "| 白细胞计数 | 6.8 | 10^9/L | 3.5 ~ 9.5 | 偏高 |" in lines


def test_table_cell_escapes_pipe_and_newline():
    payload = {
        "observations": [
            {
                "display_name": "备注|说明", "value": "第一行\n第二行",
                "unit": "", "abnormal_flag": None,
            }
        ]
    }
    art = _render(payload=payload)
    row = [ln for ln in art.content.splitlines() if "备注" in ln][0]
    # 竖线被转义（不破坏列结构）
    assert "\\|" in row
    # 换行被压成空格（不产生跨行单元格）
    assert "第一行 第二行" in row
    # 列数正确：按未转义的分隔符计数（转义竖线不参与分隔）
    separators = len(re.findall(r"(?<!\\)\|", row))
    assert separators == 6, f"5 列应有 6 个分隔符，实际 {separators}"


# --------------------------------------------------------------------------
# 类型化
# --------------------------------------------------------------------------


def test_visit_record_uses_fixed_section_order():
    payload = {
        "diagnosis": "高血压病 2 级",
        "chief_complaint": "头晕 3 天",
        "treatment": "口服降压药",
    }
    art = _render(record_type="visit_record", doc_type="visit_record", payload=payload)
    order = [
        art.content.index("## 主诉"),
        art.content.index("## 诊断"),
        art.content.index("## 处理意见"),
    ]
    assert order == sorted(order), "章节顺序应固定，不随字典顺序变化"


def test_discharge_summary_uses_its_own_sections():
    payload = {
        "admission_diagnosis": "急性阑尾炎",
        "discharge_diagnosis": "阑尾炎（术后）",
        "discharge_advice": "一周后复查",
    }
    art = _render(
        record_type="discharge_summary", doc_type="visit_record", payload=payload
    )
    assert "## 入院诊断" in art.content
    assert "## 出院诊断" in art.content
    assert "## 出院医嘱" in art.content
    assert "## 主诉" not in art.content


def test_imaging_does_not_fabricate_findings():
    """影像类：没有报告结论时不得凭空生成结论。"""
    art = _render(
        record_type="imaging_report", doc_type="imaging_report",
        payload={"modality": "胸部 CT"},
        pages=_pages(text=""),
    )
    assert "## 报告结论" not in art.content
    assert "以原件为准" in art.content
    assert art.warnings


def test_imaging_keeps_report_conclusion_verbatim():
    payload = {
        "modality": "胸部 CT",
        "findings": "双肺纹理增多。",
        "conclusion": "双肺纹理增多，未见明显实变影。",
    }
    art = _render(
        record_type="imaging_report", doc_type="imaging_report", payload=payload
    )
    assert "双肺纹理增多，未见明显实变影。" in art.content
    assert "## 报告所见" in art.content


def test_medication_record_has_no_advice():
    """用药副本只整理原文，不给建议。"""
    payload = {
        "medications": [
            {"name": "氨氯地平", "dose": "5mg", "frequency": "每日1次"}
        ]
    }
    art = _render(record_type="medication_record", doc_type="other", payload=payload)
    assert "氨氯地平" in art.content
    assert "不构成用药建议" in art.content


# --------------------------------------------------------------------------
# 降级与诚实
# --------------------------------------------------------------------------


def test_no_pages_produces_warning_not_fake_content():
    art = _render(pages=[])
    assert art.warnings
    assert "无页级文本" in art.warnings[0]
    # 仍应产出可读的结构（front matter），而不是空字符串
    assert "documentId: doc-1" in art.content


def test_template_selection_by_record_type():
    assert md.pick_template("lab_report", None) == md.TEMPLATE_LAB
    assert md.pick_template("discharge_summary", None) == md.TEMPLATE_DISCHARGE
    assert md.pick_template("imaging_report", None) == md.TEMPLATE_IMAGING
    assert md.pick_template("text_note", None) == md.TEMPLATE_TEXT
    # 未知类型回退到通用模板（不报错）
    assert md.pick_template("mystery", "other") == md.TEMPLATE_GENERIC


def test_raw_text_is_preserved_not_summarized():
    raw = "主诉：反复头晕 2 周。既往高血压病史 5 年。"
    art = _render(record_type="text_note", doc_type="other", pages=_pages(text=raw))
    assert raw in art.content
