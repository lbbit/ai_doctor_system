"""文档上传/列表/详情 schema（F-04/F-13）。"""

from typing import List, Optional

from app.common.schemas import CamelModel

DOC_STATUSES = (
    "pending", "extracting", "awaiting_review", "confirmed",
    "parsed_no_data", "failed", "archived",
)
DOC_TYPES = ("lab_report", "imaging_report", "visit_record", "other")


class DocumentOut(CamelModel):
    id: str
    subjectId: str
    kind: str
    docType: Optional[str] = None
    status: str
    fileName: str
    mimeType: str
    sizeBytes: int
    pageCount: Optional[int] = None
    failureReason: Optional[str] = None
    reviewVersion: int = 0
    contentHash: Optional[str] = None
    archivedAt: Optional[str] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None


class UploadResult(CamelModel):
    document: dict
    duplicate: Optional[bool] = None


class ContentUrlOut(CamelModel):
    url: str
    expiresAt: str


class PageUrlOut(CamelModel):
    pageNo: int
    url: str
