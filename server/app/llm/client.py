"""OpenAI 兼容 LLM 客户端（timeout/retry/降级开关，§1.2/§6.7/§6.9）。

分场景配置：``extraction``（文档识别，量大简单，成本优先）与 ``chat``
（问诊，要求能力强）分别从 system_settings/env 解析，见 ``app/config/resolver.py``。
"""

import base64
import logging
import time
from typing import Iterator, List, Optional

from app.common.errors import LLMTimeoutError, LLMUnavailableError
from app.config.resolver import LLMProfile, resolve_llm_profile
from app.config.settings import settings

logger = logging.getLogger("app.llm")

DEFAULT_SCENARIO = "extraction"


class LLMClient:
    """按场景配置的 OpenAI 兼容网关封装（零硬编码，密钥仅来自 env/Secret）。"""

    def __init__(self, profile: Optional[LLMProfile] = None) -> None:
        if profile is None:
            profile = LLMProfile(
                scenario=DEFAULT_SCENARIO,
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                model=settings.llm_model,
                vlm_model=settings.llm_vlm_model,
                timeout_seconds=settings.llm_timeout_seconds,
            )
        self.scenario = profile.scenario
        self.base_url = profile.base_url
        self.api_key = profile.api_key
        self.model = profile.model
        self.vlm_model = profile.vlm_model
        self.timeout = profile.timeout_seconds
        self.max_retries = settings.llm_max_retries

    def available(self) -> bool:
        """LLM 可用性：显式配置了 api key（空 key = 离线降级）。"""
        return bool(self.api_key and self.api_key.strip())

    def _client(self):
        from openai import OpenAI

        return OpenAI(
            base_url=self.base_url or None,
            api_key=self.api_key,
            timeout=self.timeout,
            max_retries=0,  # 自研重试（E-5：重试 1 次）
        )

    def chat(
        self,
        messages: List[dict],
        *,
        model: Optional[str] = None,
        json_mode: bool = False,
        temperature: float = 0.1,
    ) -> str:
        """对话补全；超时/5xx → 重试 1 次 → 抛 LLMTimeoutError/LLMUnavailableError。"""
        if not self.available():
            raise LLMUnavailableError()

        from openai import APIConnectionError, APITimeoutError, APIStatusError, RateLimitError

        use_model = model or self.model
        kwargs = {"model": use_model, "messages": messages, "temperature": temperature}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        last_exc: Exception | None = None
        started = time.perf_counter()
        for attempt in range(1 + max(0, self.max_retries)):
            try:
                resp = self._client().chat.completions.create(**kwargs)
                content = resp.choices[0].message.content or ""
                logger.info(
                    "llm.call scenario=%s model=%s latencyMs=%.0f attempt=%s tokens=%s",
                    self.scenario,
                    use_model,
                    (time.perf_counter() - started) * 1000,
                    attempt + 1,
                    getattr(resp.usage, "total_tokens", None),
                )
                return content
            except APITimeoutError as exc:
                last_exc = exc
            except APIStatusError as exc:
                if exc.status_code and exc.status_code >= 500:
                    last_exc = exc
                else:
                    raise LLMUnavailableError(f"LLM 网关错误（{exc.status_code}）") from exc
            except (RateLimitError, APIConnectionError) as exc:
                last_exc = exc
        # 重试耗尽
        if isinstance(last_exc, APITimeoutError):
            raise LLMTimeoutError() from last_exc
        raise LLMUnavailableError("AI 服务暂不可用") from last_exc

    def image_to_data_url(self, image_bytes: bytes, mime: str) -> str:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{mime};base64,{b64}"

    def chat_stream(
        self,
        messages: List[dict],
        *,
        model: Optional[str] = None,
        temperature: float = 0.1,
    ) -> Iterator[str]:
        """**真流式**：逐块产出增量文本（§19）。

        与 ``chat()`` 的差异：
        - 使用 ``stream=True``，首个 token 到达即 yield；
        - **不做重试**：流已开始后重试会导致内容重复，调用方负责降级；
        - 若在首个 chunk 之前失败，抛 LLMTimeoutError/LLMUnavailableError，
          调用方可安全回退到阻塞 ``chat()``。

        失败语义：连接失败/超时/网关错误 → 抛异常；流中途断开 → 异常向上传播，
        调用方保留已累积内容并落库（不丢已生成部分）。
        """
        if not self.available():
            raise LLMUnavailableError()

        from openai import APIConnectionError, APITimeoutError, APIStatusError, RateLimitError

        use_model = model or self.model
        started = time.perf_counter()
        first_token_at: Optional[float] = None
        chunk_count = 0
        try:
            stream = self._client().chat.completions.create(
                model=use_model,
                messages=messages,
                temperature=temperature,
                stream=True,
            )
            for event in stream:
                try:
                    choices = event.choices or []
                except AttributeError:
                    choices = []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                piece = getattr(delta, "content", None) if delta is not None else None
                if not piece:
                    continue
                if first_token_at is None:
                    first_token_at = time.perf_counter()
                    logger.info(
                        "llm.stream.first_token scenario=%s model=%s firstTokenMs=%.0f",
                        self.scenario,
                        use_model,
                        (first_token_at - started) * 1000,
                    )
                chunk_count += 1
                yield piece
        except APITimeoutError as exc:
            raise LLMTimeoutError() from exc
        except APIStatusError as exc:
            raise LLMUnavailableError(
                f"LLM 网关错误（{exc.status_code}）"
            ) from exc
        except (RateLimitError, APIConnectionError) as exc:
            raise LLMUnavailableError("AI 服务暂不可用") from exc
        finally:
            logger.info(
                "llm.stream.done scenario=%s model=%s chunks=%s totalMs=%.0f",
                self.scenario,
                use_model,
                chunk_count,
                (time.perf_counter() - started) * 1000,
            )


_cache: dict[str, "LLMClient"] = {}


def reset_client_cache() -> None:
    """清空客户端缓存（配置变更或测试隔离时调用）。"""
    _cache.clear()


def get_llm_client(scenario: str = DEFAULT_SCENARIO, db=None) -> "LLMClient":
    """按场景返回 LLM 客户端。

    缓存键包含场景与配置版本号，避免抽取与问诊互相覆盖；
    配置修改后由 ``app.config.resolver.invalidate()`` 使版本号前进。
    """
    from app.config import resolver

    key = f"{scenario}:{resolver._cache_loaded_at}"  # noqa: SLF001
    cached = _cache.get(key)
    if cached is not None:
        return cached
    profile = resolve_llm_profile(db, scenario)
    client = LLMClient(profile)
    if len(_cache) > 8:  # 避免无界增长
        _cache.clear()
    _cache[key] = client
    return client
