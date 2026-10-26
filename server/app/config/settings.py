"""pydantic-settings 全量 env 定义（ARCHITECTURE §8.5，镜像零硬编码）。"""

from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置单例。所有字段来自环境变量（.env 亦可）。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- 运行环境 ---
    app_env: str = "prod"  # prod/dev/test

    # --- 数据层 ---
    database_url: str = "postgresql+psycopg://aiuser:aipass@localhost:5432/aidoctor"
    redis_url: str = "redis://localhost:6379/0"

    # --- MinIO ---
    minio_endpoint: str = "localhost:9000"
    minio_public_endpoint: str = ""
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "medical-docs"
    minio_secure: bool = False

    # --- 认证（JWT）---
    jwt_secret: str = Field(default="dev-only-insecure-secret-change-me!!")
    jwt_alg: str = "HS256"
    jwt_expire_minutes: int = 10080  # 默认 7 天

    # --- LLM 网关（OpenAI 兼容）---
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_vlm_model: str = "gpt-4o"
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 1
    local_model_mode: bool = False  # true=本地 base_url，不出域

    # --- 隐私与 HITL ---
    pii_masking_enabled: bool = True
    hitl_confidence_threshold: float = 0.85
    auto_commit_enabled: bool = False  # P1 留缝，P0 恒 false

    # --- 上传与抽取 ---
    max_upload_mb: int = 20
    extract_max_attempts: int = 2  # 含首次（重试 1 次）
    # PDF 逐页视觉抽取：整份送模型会超过网关 60s 超时（2026-10-21 实测），改为逐页
    pdf_vision_max_pages: int = 40      # 超过后仅解析前 N 页，并在草稿中标记
    pdf_vision_concurrency: int = 3     # 并发页数（网关限流下保守值）
    pdf_render_scale: float = 1.8       # pypdfium2 渲染倍率（≈144dpi）
    pdf_page_image_max_bytes: int = 4 * 1024 * 1024  # 单页 PNG 上限，超限降倍率重渲染
    job_lease_seconds: int = 900        # 单任务租约；worker 定期续租，过期即回收
    queue_extract: str = "queue:extract"
    sign_url_expire_seconds: int = 600
    redflag_reload_ttl_seconds: int = 60

    # --- 日志与观测 ---
    log_level: str = "INFO"
    log_format: str = "json"  # json | console
    cors_origins: str = "http://localhost:5173"

    # --- 限流 ---
    rate_limit_chat_per_min: int = 20
    rate_limit_login_per_15min: int = 10

    # --- 前端构建期（compose 注入 web 构建）---
    vite_api_base: str = "/api"

    # --- 免责声明版本（F-14 设置页展示）---
    disclaimer_version: str = "v0.1"

    # --- 系统配置写权限（§6.9）---
    # 存储级配置（LLM 模型、检索源、限额）影响全站，必须收敛写入者。
    # 取值：admin_only（默认，需 role=admin）| first_account | any
    # - admin_only：仅 role=admin 可写；首个注册账户自动获得 admin（见 auth/service）
    # - first_account：仅最早创建的账户可写（无 role 体系时的过渡策略）
    # - any：任意登录账户可写（**仅限本地单用户部署**，禁止用于多租户）
    config_write_policy: str = "admin_only"
    # 逗号分隔的管理员邮箱白名单（优先级高于 role，便于环境变量紧急授权）
    config_admin_emails: str = ""

    # --- 邮件通知（配置变更告警，§6.9 / PLAN §17）---
    # 密码/授权码**只能**来自环境变量或 Secret，绝不写入源码、日志、测试快照。
    smtp_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 465  # 465=SSL，587=STARTTLS
    smtp_use_ssl: bool = True
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""          # 发件人；为空时回退 smtp_username
    smtp_to: str = ""            # 收件人，逗号分隔；为空时回退管理员邮箱
    smtp_timeout_seconds: int = 10

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def config_admin_email_list(self) -> List[str]:
        return [
            e.strip().lower()
            for e in self.config_admin_emails.split(",")
            if e.strip()
        ]

    @property
    def smtp_recipient_list(self) -> List[str]:
        return [e.strip() for e in self.smtp_to.split(",") if e.strip()]

    @property
    def smtp_sender(self) -> str:
        return (self.smtp_from or self.smtp_username).strip()

    def smtp_configured(self) -> bool:
        """邮件通道是否可用（缺任意必需项即视为未配置，静默降级）。"""
        return bool(
            self.smtp_enabled
            and self.smtp_host.strip()
            and self.smtp_username.strip()
            and self.smtp_password.strip()
            and self.smtp_sender
        )


@lru_cache
def get_settings() -> Settings:
    """全局配置单例（进程内缓存）。"""
    return Settings()


settings = get_settings()
