"""LLM 接入层：OpenAI 兼容客户端 + 抽取调用 + 提示词模板（§1.2/§6.7）。"""

from app.llm.client import LLMClient, get_llm_client

__all__ = ["LLMClient", "get_llm_client"]
