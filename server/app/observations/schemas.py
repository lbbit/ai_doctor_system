"""观测/趋势/字典 schema（F-08）。"""

from typing import List, Optional

from app.common.schemas import CamelModel


class TrendPoint(CamelModel):
    occurredAt: str
    valueNum: Optional[float] = None
    valueText: Optional[str] = None
    abnormalFlag: Optional[str] = None
    refLow: Optional[float] = None
    refHigh: Optional[float] = None
    documentId: Optional[str] = None
    observationId: Optional[str] = None


class TrendResponse(CamelModel):
    code: Optional[str] = None
    name: str
    unit: Optional[str] = None
    points: List[TrendPoint] = []
    refBand: Optional[dict] = None
    insufficient: bool = False


class TrendOption(CamelModel):
    code: str
    name: str
    unit: Optional[str] = None
    category: Optional[str] = None


class ObservationOut(CamelModel):
    id: str
    code: Optional[str] = None
    displayName: str
    valueNum: Optional[float] = None
    valueText: Optional[str] = None
    unit: Optional[str] = None
    refLow: Optional[float] = None
    refHigh: Optional[float] = None
    refText: Optional[str] = None
    abnormalFlag: Optional[str] = None
    occurredAt: str
    recordedAt: Optional[str] = None
    sourceDocumentId: Optional[str] = None
    confidence: Optional[float] = None
    isConfirmed: bool = False
    unmapped: bool = False
