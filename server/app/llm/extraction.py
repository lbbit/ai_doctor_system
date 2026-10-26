"""抽取专用调用（VLM JSON 模式）：提示词渲染 + schema 加载 + LLM 结果解析。

被 preprocessing/parsers/vlm_parser.py 与 chat 草稿抽取复用（§6.7）。
"""

import json
import logging
import os
from functools import lru_cache
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.common.errors import LLMUnavailableError
from app.llm.client import LLMClient

logger = logging.getLogger("app.llm.extraction")

_PROMPT_DIR = os.path.join(os.path.dirname(__file__), "prompts")
_SCHEMA_DIR = os.path.join(_PROMPT_DIR, "json_schemas")

_env = Environment(
    loader=FileSystemLoader(_PROMPT_DIR),
    undefined=StrictUndefined,
    autoescape=False,
    keep_trailing_newline=True,
)


def render_prompt(name: str, **ctx: Any) -> str:
    """渲染 prompts/ 下的 jinja2 模板。"""
    return _env.get_template(name).render(**ctx)


@lru_cache
def load_schema(name: str) -> dict:
    """加载 prompts/json_schemas/ 下的 JSON schema 文件。"""
    with open(os.path.join(_SCHEMA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def _parse_json_content(content: str) -> dict:
    """从 LLM 输出提取 JSON 对象（容忍 ```json 包裹与前后噪声）。"""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("LLM 输出中未找到 JSON 对象")
    return json.loads(text[start : end + 1])


def extract_report_payload(
    llm: LLMClient,
    *,
    content: str,
    doc_type: str,
    image_bytes: Optional[bytes] = None,
    mime: Optional[str] = None,
) -> dict:
    """报告抽取：图片走 VLM（image_url）；文本走补全；json_mode 输出。"""
    if not llm.available():
        raise LLMUnavailableError()

    prompt = render_prompt(
        "extract_report.md",
        doc_type=doc_type or "未分类",
        content=content or "（无可提取文本）",
        image_present=bool(image_bytes),
    )
    messages: list[dict] = [{"role": "user", "content": prompt}]
    if image_bytes:
        data_url = llm.image_to_data_url(image_bytes, mime or "image/jpeg")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]

    raw = llm.chat(
        messages,
        model=getattr(llm, "vlm_model", None) or getattr(llm, "model", None),
        json_mode=True,
        temperature=0,
    )
    return _parse_json_content(raw)


def extract_chat_reply_with_draft(llm: LLMClient, messages: list[dict]) -> dict:
    """问诊回复 + 草稿双字段抽取（§6.5：{reply, draft} 同一次调用）。"""
    if not llm.available():
        raise LLMUnavailableError()
    sys_prompt = render_prompt("consult_system.md")
    full = [{"role": "system", "content": sys_prompt}] + messages
    raw = llm.chat(full, json_mode=True, temperature=0.2)
    return _parse_json_content(raw)
