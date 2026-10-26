"""时间线 schema（F-07）。"""

from typing import List, Optional

from app.common.schemas import CamelModel


class TimelineEventOut(CamelModel):
    id: str
    eventType: str
    occurredAt: str
    occurredAtPrecision: str
    recordedAt: Optional[str] = None
    title: Optional[str] = None
    narrative: Optional[str] = None
    diagnosis: Optional[str] = None
    department: Optional[str] = None
    eventSource: str = "document"
    documentId: Optional[str] = None
    observationCount: int = 0


class TimelineGroup(CamelModel):
    year: int
    month: Optional[int] = None
    items: List[TimelineEventOut]
