"""首 token 等待期心跳（§19）：长等待时必须有事件，不能静默空白。"""

import json
import time

import pytest

from app.chat.orchestration import _iter_tokens_with_heartbeat
from app.common.errors import LLMUnavailableError
from app.config import resolver


class _SlowLLM:
    """首 token 前故意停顿，模拟真实网关长等待。"""

    def __init__(self, delay: float = 0.5, tokens=("甲", "乙", "丙")):
        self.delay = delay
        self.tokens = tokens
        self.model = "fake"
        self.scenario = "chat"

    def available(self):
        return True

    def chat_stream(self, messages, **kwargs):
        time.sleep(self.delay)
        for t in self.tokens:
            yield t


class _BoomLLM:
    model = "fake"
    scenario = "chat"

    def available(self):
        return True

    def chat_stream(self, messages, **kwargs):
        raise LLMUnavailableError("gateway down")
        yield ""  # pragma: no cover


class TestHeartbeat:
    def test_emits_heartbeat_during_long_wait(self):
        llm = _SlowLLM(delay=0.4)
        events = list(
            _iter_tokens_with_heartbeat(llm, [], heartbeat_seconds=0.1)
        )
        kinds = [k for k, _ in events]
        assert "heartbeat" in kinds, "长等待期间必须发心跳"
        assert kinds.count("heartbeat") >= 2

    def test_heartbeat_carries_elapsed_label(self):
        llm = _SlowLLM(delay=0.35)
        events = list(_iter_tokens_with_heartbeat(llm, [], heartbeat_seconds=0.1))
        beats = [p for k, p in events if k == "heartbeat"]
        assert beats
        assert "已等待" in beats[0]

    def test_tokens_still_delivered(self):
        llm = _SlowLLM(delay=0.25, tokens=("一", "二", "三"))
        events = list(_iter_tokens_with_heartbeat(llm, [], heartbeat_seconds=0.05))
        tokens = [p for k, p in events if k == "token"]
        assert "".join(tokens) == "一二三"

    def test_no_heartbeat_when_fast(self):
        llm = _SlowLLM(delay=0.0, tokens=("快",))
        events = list(_iter_tokens_with_heartbeat(llm, [], heartbeat_seconds=5))
        assert [k for k, _ in events] == ["token"]

    def test_error_propagates(self):
        with pytest.raises(LLMUnavailableError):
            list(_iter_tokens_with_heartbeat(_BoomLLM(), [], heartbeat_seconds=1))

    def test_error_after_heartbeats(self):
        class _SlowBoom(_BoomLLM):
            def chat_stream(self, messages, **kwargs):
                time.sleep(0.25)
                raise LLMUnavailableError("late failure")
                yield ""  # pragma: no cover

        collected = []
        with pytest.raises(LLMUnavailableError):
            for kind, payload in _iter_tokens_with_heartbeat(
                _SlowBoom(), [], heartbeat_seconds=0.08
            ):
                collected.append(kind)
        assert "heartbeat" in collected


@pytest.fixture(autouse=True)
def _reset():
    resolver.invalidate()
    yield
    resolver.invalidate()
