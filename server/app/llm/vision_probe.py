"""视觉能力探测（§21）：发一张极小测试图判断模型是否支持读图。

为什么需要：OpenAI 兼容网关的 ``/models`` 列表**不标注**是否支持视觉，
而配置错了会导致每份文档都失败。探测成本极低（64×64 纯色约 100–200 token），
但能在保存配置时立刻给出确定答案。

设计约束：
- **不阻塞主流程**：探测失败只返回 ``ok=False``，不抛异常
- **不猜能力**：只回答"能/不能/超时"，不做降级推断
- **可离线测试**：``_probe_impl`` 可替换
"""

from __future__ import annotations

import base64
import io
import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger("app.llm.vision_probe")

# 探测图：64×64 纯红色（极简，任何视觉模型都能答对）
PROBE_COLOR = "red"
PROBE_SIZE = 64

PROBE_PROMPT = (
    "这张图片是什么颜色？只回答一个词：red / green / blue / white / black。"
)

_probe_impl: Optional[Callable[[str, str], str]] = None


@dataclass
class ProbeResult:
    ok: bool
    detail: str
    latency_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "supported": True,
            "ok": self.ok,
            "detail": self.detail,
            "latencyMs": self.latency_ms,
        }


def build_probe_image() -> bytes:
    """生成 64×64 纯红 PNG（体积约 200 字节）。"""
    from PIL import Image

    img = Image.new("RGB", (PROBE_SIZE, PROBE_SIZE), (255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _default_probe(base_url: str, api_key: str, model: str, timeout: int) -> str:
    """真实探测：把测试图发给模型，看是否返回合理答案。"""
    from openai import OpenAI

    data_url = "data:image/png;base64," + base64.b64encode(
        build_probe_image()
    ).decode("ascii")
    client = OpenAI(base_url=base_url or None, api_key=api_key or "none", timeout=timeout)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROBE_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        temperature=0.0,
        max_tokens=10,
    )
    return (resp.choices[0].message.content or "").strip()


def probe_vision(
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout: int = 20,
    enabled: bool = True,
) -> ProbeResult:
    """探测视觉能力。任何异常都降级为 ``ok=False``，绝不抛出。"""
    if not enabled:
        return ProbeResult(ok=False, detail="视觉抽取已关闭（vision.enabled=false）")
    if not model or not model.strip():
        return ProbeResult(ok=False, detail="未配置视觉模型")
    if not api_key or not api_key.strip():
        return ProbeResult(ok=False, detail="未配置 API Key（离线降级）")

    started = time.perf_counter()
    try:
        if _probe_impl is not None:
            answer = _probe_impl(base_url, api_key, model, timeout)
        else:
            answer = _default_probe(base_url, api_key, model, timeout)
    except Exception as exc:  # noqa: BLE001 - 探测失败不得影响调用方
        latency = int((time.perf_counter() - started) * 1000)
        name = type(exc).__name__
        if "Timeout" in name or "timeout" in str(exc).lower():
            detail = f"探测超时（{latency}ms）：模型可能不支持读图或网关过慢"
        else:
            detail = f"探测失败（{name}）：模型可能不支持图片输入"
        logger.warning("vision.probe.failed reason=%s latencyMs=%s", name, latency)
        return ProbeResult(ok=False, detail=detail, latency_ms=latency)

    latency = int((time.perf_counter() - started) * 1000)
    text = (answer or "").strip().lower()
    # 只要求答案里出现颜色词即可（模型可能答 "red" 或 "红色"）
    hit = any(word in text for word in ("red", "红")) and len(text) <= 30
    if hit:
        logger.info("vision.probe.ok model=%s latencyMs=%s", model, latency)
        return ProbeResult(ok=True, detail=f"视觉可用（模型 {model}，{latency}ms）", latency_ms=latency)
    logger.warning("vision.probe.unexpected answer=%r", text[:60])
    return ProbeResult(
        ok=False,
        detail=f"模型有响应但答案异常（{text[:20]!r}），可能不支持读图",
        latency_ms=latency,
    )
