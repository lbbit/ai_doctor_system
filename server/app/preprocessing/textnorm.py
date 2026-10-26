"""文本归一化：CJK 兼容字符 / 康熙部首 → 标准汉字（NFKC）。

真实报告实测（2021_0907.pdf）：PDF 文本层把「检查项目」写成「检查项⽬」，
其中「⽬」是 Kangxi Radical（U+2F6C）而非标准汉字「目」。这会让
表头关键词、指标名匹配、噪声过滤全部失效（表现为 0 条指标）。

`unicodedata.normalize("NFKC")` 会把这类兼容字符映射回标准汉字，同时
保持全角数字/字母的合理转换；不改变医学语义。
"""

import unicodedata


def normalize_text(text: str) -> str:
    """NFKC 归一化（把康熙部首/兼容字形还原为标准字符）。"""
    if not text:
        return text
    return unicodedata.normalize("NFKC", text)


def normalize_cell(value) -> str:
    """单元格归一化：NFKC + 空白折叠。"""
    if value is None:
        return ""
    text = normalize_text(str(value))
    return " ".join(text.split())
