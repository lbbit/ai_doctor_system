"""SQLAlchemy 模型注册（与 ARCHITECTURE §3.2 一一对应，11 文件）。"""

from app.db.models.account import Account, LastSubjectPreference
from app.db.models.audit import AuditLog
from app.db.models.chat import (
    Conversation,
    Message,
    MessageDraft,
    Trace,
    TraceRef,
)
from app.db.models.document import Document, DocumentPage
from app.db.models.event import MedicalEvent, MedicalEventDocument
from app.db.models.observation import (
    IndicatorDictionary,
    IndicatorRefRange,
    Observation,
)
from app.db.models.preprocessing import (
    DerivedRecord,
    DictionaryMiss,
    ExtractionJob,
)
from app.db.models.redflag import RedFlagHit, RedFlagRule
from app.db.models.subject import Profile, Subject
from app.db.models.system_setting import SystemSetting

__all__ = [
    "Account",
    "LastSubjectPreference",
    "AuditLog",
    "Conversation",
    "Message",
    "MessageDraft",
    "Trace",
    "TraceRef",
    "Document",
    "DocumentPage",
    "MedicalEvent",
    "MedicalEventDocument",
    "IndicatorDictionary",
    "IndicatorRefRange",
    "Observation",
    "DerivedRecord",
    "DictionaryMiss",
    "ExtractionJob",
    "RedFlagHit",
    "RedFlagRule",
    "Profile",
    "Subject",
    "SystemSetting",
]
