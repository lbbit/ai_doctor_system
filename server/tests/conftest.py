"""测试 fixtures：SQLite 测试库、TestClient、注册登录、假存储、假 LLM。"""

import os
import tempfile
import uuid

# 必须在导入 app 之前设置（settings 单例读取）
#
# 为什么用**每次运行独立**的库文件：固定路径会让并发的两个 pytest 进程
# 互相 drop/create 同一批表，产生 "no such table" / "table already exists"
# 的假失败（2026-10-26 实测踩到两次）。用进程唯一名隔离后，
# 并发运行互不干扰，串行运行的既有行为不变。
_DB_PATH = os.path.join(
    tempfile.gettempdir(), f"aidoctor_pytest_{os.getpid()}_{uuid.uuid4().hex[:8]}.db"
).replace("\\", "/")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["LLM_API_KEY"] = ""          # 默认离线（VLM 不可用 → 降级链）
os.environ["REDIS_URL"] = "redis://localhost:6399/0"  # 不存在端口 → 快速失败放行
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["APP_ENV"] = "test"

from typing import Generator  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.config.settings import settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import engine as _engine  # noqa: E402  # 与 app 同一 engine/URL
import app.db.models  # noqa: F401,E402

TestingSession = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


@pytest.fixture(autouse=True)
def reset_db() -> Generator[None, None, None]:
    """每例重建全部表（SQLite create_all，与迁移 DDL 等价）。"""
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    yield


@pytest.fixture()
def db():
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


class FakeStorage:
    """内存对象存储（替代 MinIO）。"""

    def __init__(self) -> None:
        self.data: dict = {}
        self.client = None

    def put(self, key, data, content_type="application/octet-stream") -> None:
        self.data[key] = data

    def get(self, key) -> bytes:
        if key not in self.data:
            raise KeyError(key)
        return self.data[key]

    def presign_get(self, key, expires_seconds=None) -> str:
        return f"https://fake.local/{key}?expires=600"

    def exists(self, key) -> bool:
        return key in self.data


@pytest.fixture(autouse=True)
def fake_storage(monkeypatch):
    """替换全部模块内 get_storage_client 引用。"""
    store = FakeStorage()

    def _fake():
        return store

    import app.documents.assets as assets_mod
    import app.documents.service as documents_service
    import app.documents.storage as storage_mod
    import app.preprocessing.pipeline as pipeline_mod
    import app.preprocessing.review as review_mod

    # chat.citations 在函数内 import，会自动取到 storage_mod 的补丁；
    # 仅对真正持有该符号的模块打补丁（review 已改为异步入队，不再直接读存储）
    for mod in (documents_service, storage_mod, pipeline_mod, review_mod, assets_mod):
        if hasattr(mod, "get_storage_client"):
            monkeypatch.setattr(mod, "get_storage_client", _fake)
    return store


@pytest.fixture(autouse=True)
def fake_queue(monkeypatch):
    """内存队列替身：测试不依赖真实 Redis（离线可跑）。"""
    import app.documents.service as documents_service
    import app.preprocessing.queue as queue_mod

    enqueued: list = []

    def _enqueue(job_id):
        enqueued.append(job_id)
        return True

    class _FakeRedis:
        """仅覆盖测试用到的命令（incr/expire/ping/publish）。"""

        def __init__(self):
            self._counts = {}

        def incr(self, key):
            self._counts[key] = self._counts.get(key, 0) + 1
            return self._counts[key]

        def expire(self, key, seconds):
            return True

        def publish(self, channel, msg):
            return 1

        def ping(self):
            return True

    fake = _FakeRedis()
    monkeypatch.setattr(queue_mod, "enqueue_extract", _enqueue)
    monkeypatch.setattr(queue_mod, "dequeue_extract", lambda timeout=3: None)
    monkeypatch.setattr(queue_mod, "ping", lambda: True)
    monkeypatch.setattr(queue_mod, "get_redis", lambda: fake)
    # documents.service 顶层 import 的 enqueue_extract 需单独替换
    monkeypatch.setattr(documents_service, "enqueue_extract", _enqueue)
    return enqueued


@pytest.fixture()
def client() -> TestClient:
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth(client: TestClient) -> dict:
    """注册账户 → {headers, account, subjectId}。"""
    resp = client.post(
        "/api/auth/register",
        json={"email": "tester@example.com", "password": "password123"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    return {
        "headers": {"Authorization": f"Bearer {data['token']}"},
        "account": data["account"],
        "subjectId": data["defaultSubject"]["id"],
    }


@pytest.fixture()
def second_auth(client: TestClient) -> dict:
    resp = client.post(
        "/api/auth/register",
        json={"email": "other@example.com", "password": "password456"},
    )
    data = resp.json()["data"]
    return {
        "headers": {"Authorization": f"Bearer {data['token']}"},
        "account": data["account"],
        "subjectId": data["defaultSubject"]["id"],
    }


class FakeLLM:
    """替换 LLM 客户端（测试问诊成功路径）。

    支持 json_mode 规划调用：planner 会带 json_mode=True 请求，
    此时返回一个“不需要检索”的合法 JSON，避免影响既有断言。
    """

    def __init__(self, answer: str = "", plan_json: str | None = None) -> None:
        self.answer = answer
        self.plan_json = plan_json or '{"need_search": false, "calls": []}'
        self.model = "fake-model"
        self.scenario = "chat"
        self.timeout = 60
        self.calls: list = []
        self.plan_calls: list = []
        self.stream_calls: list = []
        # 流式替身参数：便于测试分块/中断
        self.stream_chunk_size = 8
        self.stream_error: Exception | None = None

    def available(self) -> bool:
        return True

    def chat(self, messages, **kwargs) -> str:
        # 规划器与草稿抽取都使用 json_mode，必须按提示词区分，
        # 否则草稿测试会拿到空计划 JSON 而抽不到草稿。
        first_text = str(messages[0].get("content", "")) if messages else ""
        is_planning = "工具调度器" in first_text or "need_search" in first_text
        if is_planning:
            self.plan_calls.append(messages)
            return self.plan_json
        self.calls.append(messages)
        if isinstance(self.answer, Exception):
            raise self.answer
        if callable(self.answer):
            return self.answer(messages)
        return self.answer

    def chat_stream(self, messages, **kwargs):
        """流式替身（§19）：按固定宽度切块产出，模拟真实 token 流。"""
        self.stream_calls.append(messages)
        first_text = str(messages[0].get("content", "")) if messages else ""
        if "会话标题生成器" in first_text or "工具调度器" in first_text:
            # 这类内部调用不应走流式；若被请求则退回非流式内容
            piece = self.chat(messages, **kwargs)
            yield piece
            return
        self.calls.append(messages)
        if isinstance(self.stream_error, Exception):
            raise self.stream_error
        if isinstance(self.answer, Exception):
            raise self.answer
        text = self.answer(messages) if callable(self.answer) else self.answer
        size = max(1, self.stream_chunk_size)
        for i in range(0, len(text), size):
            yield text[i : i + size]

    def image_to_data_url(self, image_bytes, mime) -> str:
        return f"data:{mime};base64,AAAA"


@pytest.fixture()
def fake_llm(monkeypatch):
    """提供 fake_llm(answer) 工厂。"""

    def _make(answer: str = "", plan_json: str | None = None) -> FakeLLM:
        fake = FakeLLM(answer, plan_json=plan_json)

        import app.llm.client as llm_client_mod

        # 兼容 get_llm_client(scenario, db=...) 两种调用形式
        def _get(scenario: str = "extraction", db=None) -> FakeLLM:  # noqa: ARG001
            return fake

        monkeypatch.setattr(llm_client_mod, "get_llm_client", _get)
        return fake

    return _make


def make_png_bytes(salt: bytes = b"") -> bytes:
    """最小合法 PNG（1x1 像素）。

    ``salt``：用于生成**内容不同**的 PNG（避免同 Subject 上传触发去重 409）。
    """
    import struct
    import zlib

    def chunk(tag, data):
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    # salt 参与像素数据：内容不同的 PNG（去重 hash 不同）
    idat = zlib.compress(b"\x00\xff\x00\x00" + salt)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )


def make_pdf_with_text(text: str) -> bytes:
    """手工构造含文本的最小 PDF（规则解析用；支持 \\n 多行，ASCII 文本）。"""
    lines = text.split("\n")
    parts = []
    parts.append("BT /F1 12 Tf 72 720 Td")
    for i, line in enumerate(lines):
        if i > 0:
            parts.append("0 -16 Td")
        safe = line.replace("(", "").replace(")", "")
        parts.append(f"({safe}) Tj")
    parts.append("ET")
    content = " ".join(parts).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
        + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF"
    ).encode()
    return bytes(out)
