"""运行时配置项白名单（唯一事实来源）。

设计原则（对应 AGENTS.md「AI 底座原则」第 3/5 条）：
- 只有登记在此的 key 才能被读取或写入；未登记的 key 一律 422。
- 所有校验在代码侧完成，不依赖前端约束，也不交给模型判断。
- 密钥类字段标记 ``secret_env_only=True``：可从 env 读取并掩码展示，
  但**拒绝写入 DB**，严格遵循「密钥只来自环境变量或 Secret」。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse


class ConfigValidationError(ValueError):
    """配置项校验失败（API 层转 422）。"""

    def __init__(self, key: str, reason: str) -> None:
        super().__init__(f"{key}: {reason}")
        self.key = key
        self.reason = reason


@dataclass(frozen=True)
class SettingSpec:
    """单个配置项定义。"""

    key: str
    kind: str  # bool | int | float | str | enum
    default: Any
    label: str
    description: str = ""
    group: str = "general"
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    choices: Tuple[str, ...] = ()
    secret_env_only: bool = False
    env_name: str = ""  # 对应 env 变量名（用于展示来源与回退）
    validate: Optional[Callable[[Any], Optional[str]]] = None


# ---------------------------------------------------------------- 校验器


def _check_url(value: Any) -> Optional[str]:
    """只允许 http/https 且必须有 host（防止 file:// / javascript: 注入）。"""
    if not isinstance(value, str):
        return "必须是字符串"
    text = value.strip()
    if text == "":
        return None  # 空 = 未配置
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https"):
        return "只支持 http/https 地址"
    if not parsed.hostname:
        return "地址缺少主机名"
    return None


def _check_model_name(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return "模型名不能为空"
    if len(value.strip()) > 200:
        return "模型名过长"
    if any(ch in value for ch in ("\n", "\r", "\t")):
        return "模型名不能包含换行或制表符"
    return None


# ---------------------------------------------------------------- 白名单

_SPECS: List[SettingSpec] = [
    # ---------- 抽取场景 LLM（量大、简单、成本优先）----------
    SettingSpec(
        key="llm.extraction.base_url",
        kind="str",
        default="",
        label="抽取网关地址",
        description="文档识别使用的 OpenAI 兼容网关；留空回退环境变量",
        group="llm_extraction",
        env_name="LLM_BASE_URL",
        validate=_check_url,
    ),
    SettingSpec(
        key="llm.extraction.api_key",
        kind="str",
        default="",
        label="抽取 API Key",
        description="仅可查看是否已配置，出于安全不可在界面修改",
        group="llm_extraction",
        env_name="LLM_API_KEY",
        secret_env_only=True,
    ),
    SettingSpec(
        key="llm.extraction.model",
        kind="str",
        default="",
        label="抽取文本模型",
        description="预算有限时选便宜模型，例如 flash 系列",
        group="llm_extraction",
        env_name="LLM_MODEL",
        validate=_check_model_name,
    ),
    SettingSpec(
        key="llm.extraction.vlm_model",
        kind="str",
        default="",
        label="抽取视觉模型",
        description="用于图片/PDF 逐页识别；需支持图片输入",
        group="llm_extraction",
        env_name="LLM_VLM_MODEL",
        validate=_check_model_name,
    ),
    SettingSpec(
        key="llm.extraction.timeout_seconds",
        kind="int",
        default=60,
        label="抽取超时（秒）",
        description="单次调用超时；逐页识别时按页计时",
        group="llm_extraction",
        minimum=5,
        maximum=300,
    ),
    SettingSpec(
        key="llm.extraction.max_context_tokens",
        kind="int",
        default=256000,
        label="抽取最大上下文（tokens）",
        description="请求前预算校验；超过时降分辨率而不是报错",
        group="llm_extraction",
        minimum=2000,
        maximum=2000000,
    ),
    SettingSpec(
        key="llm.extraction.max_output_tokens",
        kind="int",
        default=32000,
        label="抽取最大输出（tokens）",
        description="传给网关的输出上限；超过模型上限会被网关拒绝",
        group="llm_extraction",
        minimum=256,
        maximum=256000,
    ),
    # ---------- 问诊场景 LLM（能力优先）----------
    SettingSpec(
        key="llm.chat.base_url",
        kind="str",
        default="",
        label="问诊网关地址",
        description="问诊对话使用的 OpenAI 兼容网关；留空回退环境变量",
        group="llm_chat",
        env_name="LLM_BASE_URL",
        validate=_check_url,
    ),
    SettingSpec(
        key="llm.chat.api_key",
        kind="str",
        default="",
        label="问诊 API Key",
        description="仅可查看是否已配置，出于安全不可在界面修改",
        group="llm_chat",
        env_name="LLM_API_KEY",
        secret_env_only=True,
    ),
    SettingSpec(
        key="llm.chat.model",
        kind="str",
        default="",
        label="问诊文本模型",
        description="问诊要求推理能力，建议选强模型",
        group="llm_chat",
        env_name="LLM_MODEL",
        validate=_check_model_name,
    ),
    SettingSpec(
        key="llm.chat.vlm_model",
        kind="str",
        default="",
        label="问诊视觉模型",
        description="问诊中若需读图则使用该模型",
        group="llm_chat",
        env_name="LLM_VLM_MODEL",
        validate=_check_model_name,
    ),
    SettingSpec(
        key="llm.chat.timeout_seconds",
        kind="int",
        default=90,
        label="问诊超时（秒）",
        description="问诊回答通常更长，超时可大于抽取",
        group="llm_chat",
        minimum=10,
        maximum=600,
    ),
    SettingSpec(
        key="llm.chat.max_context_tokens",
        kind="int",
        default=256000,
        label="问诊最大上下文（tokens）",
        description="组装上下文时的预算上限；超限会截断历史与检索资料",
        group="llm_chat",
        minimum=2000,
        maximum=2000000,
    ),
    SettingSpec(
        key="llm.chat.max_output_tokens",
        kind="int",
        default=32000,
        label="问诊最大输出（tokens）",
        description="传给网关的输出上限",
        group="llm_chat",
        minimum=256,
        maximum=256000,
    ),
    # ---------- 视觉抽取（§21）----------
    SettingSpec(
        key="extract.parser_strategy",
        kind="enum",
        default="vlm_first",
        label="解析策略",
        description=(
            "vlm_first=LLM 视觉优先（准确率高，成本略高）；"
            "table_first=表格优先（现状，可回滚）；auto=按文档特征自动选择"
        ),
        group="extract",
        choices=("vlm_first", "table_first", "auto"),
    ),
    SettingSpec(
        key="extract.vlm_always_pdf_max_pages",
        kind="int",
        default=6,
        label="少页 PDF 直转视觉阈值",
        description="页数 ≤ 该值的 PDF 直接渲染成图片走视觉识别，跳过表格解析",
        group="extract",
        minimum=1,
        maximum=40,
    ),
    SettingSpec(
        key="vision.enabled",
        kind="bool",
        default=True,
        label="启用视觉抽取",
        description="关闭后即使 LLM 可用也不会读图（回到纯文本链路）",
        group="vision",
    ),
    SettingSpec(
        key="vision.detail",
        kind="enum",
        default="auto",
        label="图像细节档位",
        description="low=省 token（牺牲小字）；high=保清晰度；auto=按文字密度自适应",
        group="vision",
        choices=("low", "high", "auto"),
    ),
    SettingSpec(
        key="vision.tile_enabled",
        kind="bool",
        default=True,
        label="多页拼图",
        description="把同文档连续页拼成一张图，减少请求次数与固定开销",
        group="vision",
    ),
    SettingSpec(
        key="vision.tile_max_side",
        kind="int",
        default=2200,
        label="拼图单边上限（像素）",
        description="token 与像素面积成正比，必须有硬上限",
        group="vision",
        minimum=800,
        maximum=4096,
    ),
    SettingSpec(
        key="vision.max_image_bytes",
        kind="int",
        default=1500000,
        label="单图字节上限",
        description="超过则重新压缩；质量下限锁定为近无损",
        group="vision",
        minimum=100000,
        maximum=10000000,
    ),
    SettingSpec(
        key="vision.jpeg_quality",
        kind="int",
        default=92,
        label="JPEG 质量",
        description="≥90 视为近无损；低于 85 会让化验单小字模糊",
        group="vision",
        minimum=85,
        maximum=98,
    ),
    SettingSpec(
        key="vision.max_pages_per_request",
        kind="int",
        default=4,
        label="单请求最大页数",
        description="拼图上限；超过会拆成多次请求",
        group="vision",
        minimum=1,
        maximum=10,
    ),
    # ---------- Agent（联网 + 临床 skill）----------
    SettingSpec(
        key="agent.enabled",
        kind="bool",
        default=True,
        label="启用问诊 Agent",
        description="开启后问诊可联网检索并引用外部资料；关闭则仅基于已入库档案回答",
        group="agent",
    ),
    SettingSpec(
        key="agent.search_provider",
        kind="enum",
        default="free",
        label="搜索提供方",
        description="free=内置免费搜索；searxng=自建实例；none=不联网",
        group="agent",
        choices=("free", "searxng", "none"),
    ),
    SettingSpec(
        key="agent.searxng_base_url",
        kind="str",
        default="",
        label="SearXNG 地址",
        description="自建 SearXNG 实例地址，例如 http://searxng:8080",
        group="agent",
        validate=_check_url,
    ),
    SettingSpec(
        key="agent.max_tool_rounds",
        kind="int",
        default=2,
        label="最大工具轮数",
        description="模型最多连续决定调用工具的轮数，超过即生成回答",
        group="agent",
        minimum=0,
        maximum=3,
    ),
    SettingSpec(
        key="agent.max_tool_calls",
        kind="int",
        default=3,
        label="单次最大工具调用数",
        description="单条提问允许的工具调用总次数上限",
        group="agent",
        minimum=0,
        maximum=8,
    ),
    SettingSpec(
        key="agent.tool_timeout_seconds",
        kind="int",
        default=5,
        label="工具超时（秒）",
        description="单个工具调用超时；超时只记录失败，不阻塞回答（越小用户等待越短）",
        group="agent",
        minimum=2,
        maximum=30,
    ),
    SettingSpec(
        key="agent.max_results",
        kind="int",
        default=5,
        label="搜索返回条数",
        description="单次搜索注入上下文的最大结果数",
        group="agent",
        minimum=1,
        maximum=10,
    ),
    # ---------- 对话与隐私 ----------
    SettingSpec(
        key="chat.history_limit",
        kind="int",
        default=20,
        label="历史消息条数",
        description="组装上下文时携带的历史消息上限",
        group="chat",
        minimum=5,
        maximum=50,
    ),
    SettingSpec(
        key="privacy.pii_masking_enabled",
        kind="bool",
        default=True,
        label="出域脱敏",
        description="云端模式下强制开启",
        group="privacy",
        env_name="PII_MASKING_ENABLED",
    ),
    SettingSpec(
        key="upload.max_upload_mb",
        kind="int",
        default=20,
        label="上传大小上限（MB）",
        description="单个文件允许的最大体积",
        group="upload",
        env_name="MAX_UPLOAD_MB",
        minimum=1,
        maximum=100,
    ),
]

SPECS: Dict[str, SettingSpec] = {s.key: s for s in _SPECS}


def list_specs(group: Optional[str] = None) -> List[SettingSpec]:
    """返回全部（或指定分组的）配置项定义。"""
    if group is None:
        return list(_SPECS)
    return [s for s in _SPECS if s.group == group]


def get_spec(key: str) -> SettingSpec:
    """取配置项定义；未登记 key 直接拒绝（防止任意 key 落库）。"""
    spec = SPECS.get(key)
    if spec is None:
        raise ConfigValidationError(key, "未登记的配置项")
    return spec


def coerce_and_validate(key: str, raw: Any) -> Any:
    """按类型强转并校验，返回可安全落库的值。

    任何不合法的输入都抛 ConfigValidationError，绝不静默修正——
    配置错误必须显式暴露，避免"看起来保存成功实际未生效"。
    """
    spec = get_spec(key)
    if spec.secret_env_only:
        raise ConfigValidationError(key, "密钥只允许来自环境变量，不能在界面修改")

    value = _coerce(spec, raw)

    if spec.kind == "int":
        if not isinstance(value, int):
            raise ConfigValidationError(key, "必须是整数")
        if spec.minimum is not None and value < spec.minimum:
            raise ConfigValidationError(key, f"不能小于 {int(spec.minimum)}")
        if spec.maximum is not None and value > spec.maximum:
            raise ConfigValidationError(key, f"不能大于 {int(spec.maximum)}")
    if spec.kind == "enum" and value not in spec.choices:
        raise ConfigValidationError(key, f"只能是 {', '.join(spec.choices)} 之一")

    if spec.validate is not None:
        reason = spec.validate(value)
        if reason:
            raise ConfigValidationError(key, reason)
    return value


def _coerce(spec: SettingSpec, raw: Any) -> Any:
    """按 kind 做强类型转换；转换失败即报错。"""
    if spec.kind == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            low = raw.strip().lower()
            if low in ("true", "1", "yes", "on"):
                return True
            if low in ("false", "0", "no", "off"):
                return False
        raise ConfigValidationError(spec.key, "必须是布尔值")
    if spec.kind == "int":
        if isinstance(raw, bool):
            raise ConfigValidationError(spec.key, "必须是整数（当前为布尔值）")
        if isinstance(raw, int):
            return raw
        if isinstance(raw, float):
            if raw != int(raw):
                raise ConfigValidationError(spec.key, "必须是整数")
            return int(raw)
        if isinstance(raw, str):
            text = raw.strip()
            try:
                return int(text)
            except ValueError as exc:
                raise ConfigValidationError(spec.key, "必须是整数") from exc
        raise ConfigValidationError(spec.key, "必须是整数")
    if spec.kind in ("str", "enum"):
        if raw is None:
            return ""
        if not isinstance(raw, str):
            raise ConfigValidationError(spec.key, "必须是字符串")
        return raw.strip()
    raise ConfigValidationError(spec.key, f"未知类型 {spec.kind}")
