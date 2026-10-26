"""全量 DDL + pgvector 扩展（ARCHITECTURE §3.2，22 张表）

Revision ID: 0001
Revises:
Create Date: 2026-07-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.db.types import CaseInsensitiveString, Jsonb, TextArray, Vector

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS_DEFAULT = sa.text("CURRENT_TIMESTAMP")  # PG=now() 语义；SQLite 兼容


def _ts_cols():
    """全表约定：created_at / updated_at（§3.2 顶部约定）。"""
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=TS_DEFAULT),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=TS_DEFAULT),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    pg = bind.dialect.name == "postgresql"

    # ---- 0. 扩展（仅 PostgreSQL）----
    if pg:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")     # pgvector（P0 只建列不填充）
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")    # 字典别名模糊匹配（可选）
        op.execute("CREATE EXTENSION IF NOT EXISTS citext")     # accounts.email

    # ---- 1. accounts（F-01）----
    op.create_table(
        "accounts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("email", CaseInsensitiveString(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('active','disabled')", name="ck_accounts_status"),
        sa.UniqueConstraint("email", name="uq_accounts_email"),
        *_ts_cols(),
    )

    # ---- 2. subjects（F-02/F-03；P1 预留 member 角色，仅注释不实现）----
    op.create_table(
        "subjects",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "account_id", sa.Text(),
            sa.ForeignKey("accounts.id", name="fk_subjects_account_id_accounts", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("relation", sa.Text(), nullable=False, server_default="本人"),
        sa.Column("birthday", sa.Date(), nullable=True),
        sa.Column("gender", sa.Text(), nullable=True),
        sa.Column("color", sa.Text(), nullable=True),
        sa.Column("avatar", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "relation IN ('本人','父亲','母亲','祖父母','外祖父母','配偶','儿子','女儿','其他')",
            name="ck_subjects_relation",
        ),
        sa.CheckConstraint("gender IN ('male','female','unknown')", name="ck_subjects_gender"),
        sa.UniqueConstraint("account_id", "name", name="uq_subjects_account_id_name"),
        *_ts_cols(),
    )
    op.create_index(
        "idx_subjects_account", "subjects", ["account_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )
    # 每 Account 仅一个 is_default=true：部分唯一索引
    op.create_index(
        "uq_subject_default", "subjects", ["account_id"], unique=True,
        postgresql_where=sa.text("is_default"),
        sqlite_where=sa.text("is_default"),
    )

    # ---- 3. last_subject_preferences（AC-2.3 记忆上次选择）----
    op.create_table(
        "last_subject_preferences",
        sa.Column(
            "account_id", sa.Text(),
            sa.ForeignKey("accounts.id",
                          name="fk_last_subject_preferences_account_id_accounts",
                          ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "subject_id", sa.Text(),
            sa.ForeignKey("subjects.id",
                          name="fk_last_subject_preferences_subject_id_subjects",
                          ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=TS_DEFAULT),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=TS_DEFAULT),
    )

    # ---- 4. profiles（L0 静态上下文来源）----
    op.create_table(
        "profiles",
        sa.Column(
            "subject_id", sa.Text(),
            sa.ForeignKey("subjects.id", name="fk_profiles_subject_id_subjects", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("blood_type", sa.Text(), nullable=True),
        sa.Column("allergies", sa.Text(), nullable=False, server_default=""),
        sa.Column("chronic_diseases", sa.Text(), nullable=False, server_default=""),
        sa.Column("medications", sa.Text(), nullable=False, server_default=""),
        sa.Column("family_history", sa.Text(), nullable=False, server_default=""),
        sa.Column("lifestyle", sa.Text(), nullable=False, server_default=""),
        sa.Column("extra", Jsonb(), nullable=False, server_default=sa.text("'{}'")),
        sa.CheckConstraint("blood_type IN ('A','B','AB','O','unknown')",
                           name="ck_profiles_blood_type"),
        *_ts_cols(),
    )

    # ---- 5. documents（F-04/F-13；状态机 PRD §9.2）----
    op.create_table(
        "documents",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("account_id", sa.Text(),
                  sa.ForeignKey("accounts.id", name="fk_documents_account_id_accounts"),
                  nullable=False),
        sa.Column("subject_id", sa.Text(),  # 写入必须选档（B-1）
                  sa.ForeignKey("subjects.id", name="fk_documents_subject_id_subjects"),
                  nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default="IMAGE"),
        sa.Column("doc_type", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("file_key", sa.Text(), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("review_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('PDF','IMAGE','DICOM','AUDIO','TEXT')", name="ck_documents_kind"),
        sa.CheckConstraint(
            "doc_type IN ('lab_report','imaging_report','visit_record','other')",
            name="ck_documents_doc_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending','extracting','awaiting_review','confirmed',"
            "'parsed_no_data','failed','archived')",
            name="ck_documents_status",
        ),
        sa.CheckConstraint("size_bytes <= 20971520", name="ck_documents_size_bytes"),
        *_ts_cols(),
    )
    # E-6 同 subject 同内容去重（强制上传需显式覆盖，服务层处理）
    op.create_index(
        "uq_doc_hash", "documents", ["subject_id", "content_hash"], unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("idx_doc_subject_status", "documents", ["subject_id", "status"])

    # ---- 6. document_pages（多页 PDF/VLM 分页处理）----
    op.create_table(
        "document_pages",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("document_id", sa.Text(),
                  sa.ForeignKey("documents.id",
                                name="fk_document_pages_document_id_documents",
                                ondelete="CASCADE"),
                  nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("image_key", sa.Text(), nullable=True),
        sa.UniqueConstraint("document_id", "page_no",
                            name="uq_document_pages_document_id_page_no"),
        *_ts_cols(),
    )

    # ---- 7. extraction_jobs（预处理队列持久化；Redis 只做信号，DB 为事实源）----
    op.create_table(
        "extraction_jobs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("document_id", sa.Text(),
                  sa.ForeignKey("documents.id",
                                name="fk_extraction_jobs_document_id_documents",
                                ondelete="CASCADE"),
                  nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("parser", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.Text(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('queued','running','succeeded','failed','cancelled')",
            name="ck_extraction_jobs_state",
        ),
        *_ts_cols(),
    )

    # ---- 8. derived_records（F-05/F-06 抽取草稿；幂等核心，B-4）----
    op.create_table(
        "derived_records",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("document_id", sa.Text(),
                  sa.ForeignKey("documents.id",
                                name="fk_derived_records_document_id_documents",
                                ondelete="CASCADE"),
                  nullable=False),
        sa.Column("subject_id", sa.Text(),
                  sa.ForeignKey("subjects.id", name="fk_derived_records_subject_id_subjects"),
                  nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("payload", Jsonb(), nullable=False),
        sa.Column("parser", sa.Text(), nullable=False),
        sa.Column("overall_confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("source_text", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="system"),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("document_id", "version",
                            name="uq_derived_records_document_id_version"),
        *_ts_cols(),
    )
    op.create_index("idx_dr_subject_status", "derived_records",
                    ["subject_id", "confirmed_at"])

    # ---- 9. dictionary_misses（E-3 字典未命中反馈队列）----
    op.create_table(
        "dictionary_misses",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("subject_id", sa.Text(),
                  sa.ForeignKey("subjects.id", name="fk_dictionary_misses_subject_id_subjects"),
                  nullable=False),
        sa.Column("document_id", sa.Text(),
                  sa.ForeignKey("documents.id",
                                name="fk_dictionary_misses_document_id_documents",
                                ondelete="SET NULL"),
                  nullable=True),
        sa.Column("raw_name", sa.Text(), nullable=False),
        sa.Column("raw_value", sa.Text(), nullable=True),
        sa.Column("raw_unit", sa.Text(), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("resolved_to_code", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=TS_DEFAULT),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=TS_DEFAULT),
    )

    # ---- 10. indicator_dictionary（指标字典，冷启动 TOP50）----
    op.create_table(
        "indicator_dictionary",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name_cn", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False, server_default="其他"),
        sa.Column("unit", sa.Text(), nullable=True),
        sa.Column("aliases", TextArray(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("loinc_code", sa.Text(), nullable=True),
        sa.Column("is_top_trend", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("code", name="uq_indicator_dictionary_code"),
        *_ts_cols(),
    )

    # ---- 11. indicator_ref_ranges（参考区间版本化，§9.3 换指南不改历史）----
    op.create_table(
        "indicator_ref_ranges",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("dictionary_id", sa.Text(),
                  sa.ForeignKey("indicator_dictionary.id",
                                name="fk_indicator_ref_ranges_dictionary_id_indicator_dictionary",
                                ondelete="CASCADE"),
                  nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("low_value", sa.Numeric(), nullable=True),
        sa.Column("high_value", sa.Numeric(), nullable=True),
        sa.Column("text_range", sa.Text(), nullable=True),
        sa.Column("applies_to", Jsonb(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("effective_from", sa.Date(), nullable=False,
                  server_default=sa.text("CURRENT_DATE")),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.UniqueConstraint("dictionary_id", "version",
                            name="uq_indicator_ref_ranges_dictionary_id_version"),
        *_ts_cols(),
    )

    # ---- 12. medical_events（F-07；双时间轴 B-2）----
    op.create_table(
        "medical_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("account_id", sa.Text(),
                  sa.ForeignKey("accounts.id", name="fk_medical_events_account_id_accounts"),
                  nullable=False),
        sa.Column("subject_id", sa.Text(),
                  sa.ForeignKey("subjects.id", name="fk_medical_events_subject_id_subjects"),
                  nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.Date(), nullable=False),  # 业务发生时间（AI 时序推理唯一依据）
        sa.Column("occurred_at_precision", sa.Text(), nullable=False, server_default="day"),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=TS_DEFAULT),  # 录入时间（不覆盖 occurred_at）
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("narrative", sa.Text(), nullable=True),
        sa.Column("diagnosis", sa.Text(), nullable=True),
        sa.Column("department", sa.Text(), nullable=True),
        sa.Column("event_source", sa.Text(), nullable=False, server_default="document"),
        sa.Column("document_id", sa.Text(),
                  sa.ForeignKey("documents.id", name="fk_medical_events_document_id_documents"),
                  nullable=True),
        sa.Column("draft_id", sa.Text(), nullable=True),
        sa.Column("embedding", Vector(1024), nullable=True),  # P1 L3 语义层填充，P0 为 NULL
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "event_type IN ('visit','hospitalization','surgery','checkup',"
            "'medication','vaccine','self_reported','other')",
            name="ck_medical_events_event_type",
        ),
        sa.CheckConstraint(
            "occurred_at_precision IN ('day','month','year','unknown')",
            name="ck_medical_events_occurred_at_precision",
        ),
        sa.CheckConstraint("event_source IN ('document','chat','manual')",
                           name="ck_medical_events_event_source"),
        *_ts_cols(),
    )
    # B-4 幂等防重：同文档同事件唯一
    op.create_index(
        "uq_event_draft", "medical_events", ["document_id", "draft_id"], unique=True,
        postgresql_where=sa.text("document_id IS NOT NULL AND draft_id IS NOT NULL"),
        sqlite_where=sa.text("document_id IS NOT NULL AND draft_id IS NOT NULL"),
    )
    op.create_index("idx_event_subject_time", "medical_events",
                    ["subject_id", sa.text("occurred_at DESC")])
    if pg:
        # P1 启用的向量索引（仅 PG）
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_embedding ON medical_events "
            "USING ivfflat (embedding vector_cosine_ops) WHERE embedding IS NOT NULL"
        )

    # ---- 13. medical_event_documents（事件 ↔ 证据多对多 refs[]）----
    op.create_table(
        "medical_event_documents",
        sa.Column("event_id", sa.Text(),
                  sa.ForeignKey("medical_events.id",
                                name="fk_medical_event_documents_event_id_medical_events",
                                ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("document_id", sa.Text(),
                  sa.ForeignKey("documents.id",
                                name="fk_medical_event_documents_document_id_documents"),
                  primary_key=True),
        sa.Column("ref_note", sa.Text(), nullable=True),
        *_ts_cols(),
    )

    # ---- 14. observations（F-08；结构化时序）----
    op.create_table(
        "observations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("account_id", sa.Text(),
                  sa.ForeignKey("accounts.id", name="fk_observations_account_id_accounts"),
                  nullable=False),
        sa.Column("subject_id", sa.Text(),
                  sa.ForeignKey("subjects.id", name="fk_observations_subject_id_subjects"),
                  nullable=False),
        sa.Column("event_id", sa.Text(),
                  sa.ForeignKey("medical_events.id",
                                name="fk_observations_event_id_medical_events",
                                ondelete="SET NULL"),
                  nullable=True),
        sa.Column("dictionary_id", sa.Text(),
                  sa.ForeignKey("indicator_dictionary.id",
                                name="fk_observations_dictionary_id_indicator_dictionary"),
                  nullable=True),
        sa.Column("code", sa.Text(), nullable=True),  # 冗余字典码（未命中存 NULL）
        sa.Column("display_name", sa.Text(), nullable=False),  # 未命中=原文（E-3 unmapped）
        sa.Column("value_num", sa.Numeric(), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("unit", sa.Text(), nullable=True),
        sa.Column("ref_low", sa.Numeric(), nullable=True),
        sa.Column("ref_high", sa.Numeric(), nullable=True),
        sa.Column("ref_text", sa.Text(), nullable=True),
        sa.Column("abnormal_flag", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.Date(), nullable=False),  # 报告内检查日期（双时间轴）
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=TS_DEFAULT),
        sa.Column("source_document_id", sa.Text(),
                  sa.ForeignKey("documents.id",
                                name="fk_observations_source_document_id_documents"),
                  nullable=True),
        sa.Column("source_field_ref", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("is_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("abnormal_flag IN ('N','L','H','A')",
                           name="ck_observations_abnormal_flag"),
        *_ts_cols(),
    )
    # B-4 幂等：同一文档 + 同一字段来源不重复入库
    op.create_index(
        "uq_obs_doc_field", "observations",
        ["source_document_id", "source_field_ref"], unique=True,
        postgresql_where=sa.text(
            "source_document_id IS NOT NULL AND source_field_ref IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "source_document_id IS NOT NULL AND source_field_ref IS NOT NULL"
        ),
    )
    op.create_index("idx_obs_subject_code_time", "observations",
                    ["subject_id", "code", "occurred_at"])
    op.create_index("idx_obs_subject_time", "observations",
                    ["subject_id", sa.text("occurred_at DESC")])

    # ---- 15. conversations（F-09；状态机 active → archived）----
    op.create_table(
        "conversations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("account_id", sa.Text(),
                  sa.ForeignKey("accounts.id", name="fk_conversations_account_id_accounts"),
                  nullable=False),
        sa.Column("subject_id", sa.Text(),  # 会话级锁定（A-7）
                  sa.ForeignKey("subjects.id", name="fk_conversations_subject_id_subjects"),
                  nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('active','archived')", name="ck_conversations_status"),
        *_ts_cols(),
    )
    op.create_index("idx_conv_subject", "conversations", ["subject_id", "status"])

    # ---- 16. messages ----
    op.create_table(
        "messages",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("conversation_id", sa.Text(),
                  sa.ForeignKey("conversations.id",
                                name="fk_messages_conversation_id_conversations",
                                ondelete="CASCADE"),
                  nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("red_flag_hit_id", sa.Text(), nullable=True),
        sa.Column("llm_model", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_state", sa.Text(), nullable=True),  # 'timeout' 等（E-5）
        sa.CheckConstraint("role IN ('user','assistant','system')", name="ck_messages_role"),
        *_ts_cols(),
    )
    op.create_index("idx_msg_conv", "messages", ["conversation_id", "created_at"])

    # ---- 17. message_drafts（F-12 对话式草稿卡片）----
    op.create_table(
        "message_drafts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("message_id", sa.Text(),
                  sa.ForeignKey("messages.id",
                                name="fk_message_drafts_message_id_messages",
                                ondelete="CASCADE"),
                  nullable=False),
        sa.Column("subject_id", sa.Text(),
                  sa.ForeignKey("subjects.id", name="fk_message_drafts_subject_id_subjects"),
                  nullable=False),
        sa.Column("draft_type", sa.Text(), nullable=False),
        sa.Column("payload", Jsonb(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_event_id", sa.Text(),
                  sa.ForeignKey("medical_events.id",
                                name="fk_message_drafts_result_event_id_medical_events"),
                  nullable=True),
        sa.Column("result_observation_ids", TextArray(), nullable=True,
                  server_default=sa.text("'{}'")),
        sa.Column("discarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("draft_type IN ('event','observation','both')",
                           name="ck_message_drafts_draft_type"),
        sa.CheckConstraint("status IN ('pending','confirmed','discarded')",
                           name="ck_message_drafts_status"),
        *_ts_cols(),
    )

    # ---- 18. traces（F-10；A-3）----
    op.create_table(
        "traces",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("message_id", sa.Text(),
                  sa.ForeignKey("messages.id", name="fk_traces_message_id_messages",
                                ondelete="CASCADE"),
                  nullable=False),
        sa.Column("subject_id", sa.Text(),
                  sa.ForeignKey("subjects.id", name="fk_traces_subject_id_subjects"),
                  nullable=False),
        sa.Column("context_layers", Jsonb(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        *_ts_cols(),
    )
    # 一次回答一条 Trace（E-5 失败则无）
    op.create_index("uq_trace_message", "traces", ["message_id"], unique=True)

    # ---- 19. trace_refs ----
    op.create_table(
        "trace_refs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("trace_id", sa.Text(),
                  sa.ForeignKey("traces.id", name="fk_trace_refs_trace_id_traces",
                                ondelete="CASCADE"),
                  nullable=False),
        sa.Column("ref_index", sa.Integer(), nullable=False),  # 引用序号 [1] [2]…
        sa.Column("ref_type", sa.Text(), nullable=False),
        sa.Column("ref_id", sa.Text(), nullable=False),  # 多态 ID（service 层校验归属）
        sa.Column("label", sa.Text(), nullable=False),
        sa.CheckConstraint("ref_type IN ('observation','event','document')",
                           name="ck_trace_refs_ref_type"),
        *_ts_cols(),
    )
    op.create_index("idx_trace_ref", "trace_refs", ["trace_id"])

    # ---- 20. red_flag_rules（F-11；可热更新）----
    op.create_table(
        "red_flag_rules",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False, server_default="成人急症"),
        sa.Column("keywords", TextArray(), nullable=False),
        sa.Column("keyword_logic", sa.Text(), nullable=False, server_default="any"),
        sa.Column("context_logic", Jsonb(), nullable=True),
        sa.Column("severity", sa.Text(), nullable=False, server_default="high"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False, server_default="refer_now"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False,
                  server_default=TS_DEFAULT),
        sa.CheckConstraint("keyword_logic IN ('any','all')",
                           name="ck_red_flag_rules_keyword_logic"),
        sa.CheckConstraint("severity IN ('high','medium')", name="ck_red_flag_rules_severity"),
        sa.UniqueConstraint("code", name="uq_red_flag_rules_code"),
        *_ts_cols(),
    )

    # ---- 21. red_flag_hits（A-1 命中即审计）----
    op.create_table(
        "red_flag_hits",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("rule_id", sa.Text(),
                  sa.ForeignKey("red_flag_rules.id",
                                name="fk_red_flag_hits_rule_id_red_flag_rules"),
                  nullable=False),
        sa.Column("message_id", sa.Text(),
                  sa.ForeignKey("messages.id",
                                name="fk_red_flag_hits_message_id_messages",
                                ondelete="SET NULL"),
                  nullable=True),
        sa.Column("conversation_id", sa.Text(), nullable=True),
        sa.Column("subject_id", sa.Text(),
                  sa.ForeignKey("subjects.id", name="fk_red_flag_hits_subject_id_subjects"),
                  nullable=False),
        sa.Column("account_id", sa.Text(),
                  sa.ForeignKey("accounts.id", name="fk_red_flag_hits_account_id_accounts"),
                  nullable=False),
        sa.Column("matched_terms", TextArray(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=TS_DEFAULT),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=TS_DEFAULT),
    )

    # ---- 22. audit_logs（F-14；B-5 不可绕过）----
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("account_id", sa.Text(),  # 系统动作为 NULL
                  sa.ForeignKey("accounts.id", name="fk_audit_logs_account_id_accounts"),
                  nullable=True),
        sa.Column("subject_id", sa.Text(),
                  sa.ForeignKey("subjects.id", name="fk_audit_logs_subject_id_subjects"),
                  nullable=True),
        sa.Column("actor_type", sa.Text(), nullable=False, server_default="user"),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("resource_id", sa.Text(), nullable=True),
        sa.Column("before_value", Jsonb(), nullable=True),
        sa.Column("after_value", Jsonb(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=TS_DEFAULT),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=TS_DEFAULT),
        sa.CheckConstraint("actor_type IN ('user','system','worker')",
                           name="ck_audit_logs_actor_type"),
    )
    op.create_index("idx_audit_account_time", "audit_logs",
                    ["account_id", sa.text("created_at DESC")])
    op.create_index("idx_audit_resource", "audit_logs", ["resource_type", "resource_id"])


def downgrade() -> None:
    bind = op.get_bind()
    pg = bind.dialect.name == "postgresql"

    tables = [
        "audit_logs", "red_flag_hits", "red_flag_rules", "trace_refs", "traces",
        "message_drafts", "messages", "conversations", "observations",
        "medical_event_documents", "medical_events", "indicator_ref_ranges",
        "indicator_dictionary", "dictionary_misses", "derived_records",
        "extraction_jobs", "document_pages", "documents", "profiles",
        "last_subject_preferences", "subjects", "accounts",
    ]
    for table in tables:
        op.drop_table(table)

    if pg:
        op.execute("DROP INDEX IF EXISTS idx_event_embedding")
