"""抽取链数据结构（§6.6 ParseInput/ParserResult 协议 + 草稿条目 TypedDict）。"""

from datetime import date
from typing import Any, List, Optional, Protocol, TypedDict, runtime_checkable


class BBox(TypedDict, total=False):
    page: int
    x: float
    y: float
    w: float
    h: float


class ObsDraft(TypedDict, total=False):
    raw_name: str
    value: Optional[Any]          # 数值或文本（"阴性"）
    unit: Optional[str]
    ref_low: Optional[float]
    ref_high: Optional[float]
    ref_text: Optional[str]
    abnormal_flag: Optional[str]  # N|L|H|A
    occurred_at: Optional[str]    # YYYY-MM-DD
    confidence: float             # 0~1
    bbox: Optional[BBox]


class EventDraft(TypedDict, total=False):
    type: str                     # visit|hospitalization|surgery|checkup|medication|vaccine|self_reported|other
    title: Optional[str]
    narrative: Optional[str]
    diagnosis: Optional[str]
    department: Optional[str]
    occurred_at: Optional[str]    # YYYY-MM-DD
    confidence: float


class PageAsset(TypedDict, total=False):
    page_no: int
    image_bytes: Optional[bytes]
    text: Optional[str]
    mime: Optional[str]


class ParseInput(TypedDict, total=False):
    document_id: str
    doc_type: str                 # 分类分流结果
    filename: str
    pages: List[PageAsset]        # [{page_no, image_bytes|text}]
    language_hint: str            # 'zh-CN'
    llm_client: Any               # LLMClient（可不可用→抛 LLMUnavailable）
    raw_content: Optional[bytes]  # 原始文件字节（PDF 表格解析用）
    pdf_render: Any               # () -> [(pageNo, pngBytes)]，PDF 逐页渲染回调
    progress: Any                 # (done, total) -> None 进度回调


class ParserResult(TypedDict, total=False):
    parser_name: str              # 'table'|'vlm'|'ocr'|'rule'|'mock'|(P1)'paddleocr'|'mineru'
    report_date: Optional[str]    # 双时间轴：报告内检查日期（YYYY-MM-DD）
    doc_type: str
    events: List[EventDraft]
    observations: List[ObsDraft]
    source_text: str              # 全文（L3 embedding 源）
    success: bool
    error: Optional[str]
    page_count: Optional[int]     # 实际处理的页数（进度与诊断）
    page_errors: Optional[List[str]]  # 部分页失败诊断（部分成功仍可用）


@runtime_checkable
class BaseParser(Protocol):
    """可插拔解析器协议（§6.6）：P1 新增 PaddleOCR/MinerU 只需实现并注册。"""

    name: str
    supported_doc_types: set

    def available(self) -> bool:
        """依赖可用性探测（模型/权重缺失→False）。"""
        ...

    def parse(self, inp: ParseInput) -> ParserResult:
        """解析；失败返回 success=False + error。"""
        ...
