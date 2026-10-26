# AI 私人医生系统 — 架构设计文档（ARCHITECTURE.md）

> 关联文档：`docs/PRD.md`（需求基准 V0.1）、《AI私人医生系统-产品方案V0.1.md》
> 作者：架构师高见远（Gao） | P0 基线版本：V1.0
> 适用范围：本文主体描述 P0 初始规范，不能单独代表当前全部代码。实际实现状态看 `PLAN.md` §0；后续已接受的 ADR 与本文共同构成规范架构。
> 已定裁决（兼容演进，不得无评审推翻）：Docker Compose 一键起全栈为验收基线，K8s 清单为附件；LLM 走 OpenAI 兼容网关（支持脱敏出域与本地模型）；HITL 强制人工确认；FastAPI 模块化单体 + PostgreSQL(+pgvector) + Redis + MinIO + Alembic + React/Vite/TS SPA；解析器可插拔；LLM 不可用可观测降级。
> **2026-10-26 导航**：当前代码已增加多路解析、DB 覆盖配置（密钥仍仅 env/Secret）、角色、前置搜索 Agent、真流式等后续能力，本文早期文件/DDL/env/任务清单为历史基线。下一阶段目标架构见 `docs/superpowers/specs/2026-10-26-health-record-intelligence.md`，实施顺序见同名计划，未经实施不得当作现状。

---

## 0. 下一阶段架构决策摘要（2026-10-26，待评审）

1. **资产分层**：保留 Document 稳定 ID，新增 DocumentAsset/页块；原件不可变，Markdown、页图、索引和草稿版本化，不直接覆盖 `file_key` 或已确认事实。
2. **双轴状态**：处理状态与 `active/archived/deletion_pending` 生命周期正交；旧 `status` 经可空回填、双读适配迁移。默认删除为归档，永久删除另行设计。
3. **时间模型**：发生/报告/录入时间与精度分别保存；历史未知日期不伪造。趋势按指标、标本、单位和方法可比性由 SQL/代码计算。
4. **检索边界**：统一只读 RecordSearchService，SQL + PostgreSQL FTS + pgvector 多路召回，再按需精读；权限/生命周期先过滤，向量不代替时间与结构化查询。
5. **问诊编排**：保留 SSE/HITL/红旗，采用有界 `triage→plan→retrieve→assess→clarify/read_original/respond→verify`；工具只读、服务端绑定 Subject、全参数脱敏与审计。
6. **证据模型**：档案与外部医学证据分池；Trace 记录行动、证据版本、页/段锚和降级原因，不暴露隐藏思维链。引用存在性校验之外还需论断支持性评测。
7. **隐私安全**：云端原图复读默认禁用；外部 fetch 防 SSRF，病历/网页均不可信；新增资产、索引和工具逐一执行 Account+Subject+生命周期校验。
8. **架构治理**：新决策以 ADR 逐步接受，避免同时维护多份互相冲突的“唯一依据”；本文件主体实施时按已接受 ADR 更新。

---

## 目录

1. [实现方案总览与框架选型](#1-实现方案总览与框架选型)
2. [目录结构与完整文件列表](#2-目录结构与完整文件列表)
3. [数据库设计（DDL + ER 图）](#3-数据库设计ddl--er-图)
4. [接口设计（REST API 清单）](#4-接口设计rest-api-清单)
5. [关键时序图](#5-关键时序图)
6. [AI Flow 设计](#6-ai-flow-设计)
7. [任务列表（T1…Tn）](#7-任务列表t1tn)
8. [共享知识与跨文件约定](#8-共享知识与跨文件约定)
9. [待明确事项](#9-待明确事项)

---

## 1. 实现方案总览与框架选型

### 1.1 总体架构

```
[Browser Web SPA]
      │ HTTPS
[nginx/web 容器] ──静态──► SPA
      │ /api 反向代理
[api 容器 FastAPI 单体（模块化边界）]
      │  ├──────────► PostgreSQL 16 (+pgvector)   档案/时序/审计/字典
      │  ├──────────► Redis 7                      任务队列/缓存/限流/最近会话
      │  ├──────────► MinIO                        原件证据层（S3 兼容）
      │  └─脱敏后──► LLM 网关（OpenAI 兼容, base_url/key env 注入）
[worker 容器（同代码不同入口）] ── BRPOP Redis 队列：分类→VLM/规则抽取→字典映射→写 DerivedRecord
```

**单体 + 模块化边界**：后端为一个 FastAPI 进程（`uvicorn app.main:app`），内部按领域拆为 14 个模块（见 §2），模块间只允许经由 service 层函数互调，禁止跨模块直接 import 对方的 `repo`。worker 复用同一份代码，入口为 `python -m app.worker`，便于独立扩缩。

**关键设计原则**（对齐 PRD §8/§9）：

| # | 原则 | 落点 |
|---|---|---|
| 1 | 数值计算不交给模型（A-6） | 趋势/聚合全部 SQL；LLM 只解读 |
| 2 | 红旗由规则引擎插入（A-1） | chat 编排在 LLM 返回后独立插入卡片 |
| 3 | 引用必须可回链（A-3） | 回答中每个引用标记必须挂 Trace 行；渲染层过滤 |
| 4 | 写入必须选 Subject（B-1） | `require_subject` 依赖 + DB 层跨表 FK 校验 |
| 5 | 双时间轴（B-2） | `occurred_at`/`recorded_at` 两列并存，AI 只读 occurred_at |
| 6 | 原件不可删除（B-3） | Document 无 DELETE 接口，仅 archive 软删 |
| 7 | 幂等入库（B-4） | `UNIQUE(document_id, version)` 唯一约束 + 乐观锁 |
| 8 | 审计不可绕过（B-5） | 统一 `audit.record()` 依赖/工具函数 |
| 9 | LLM 不可用降级 | extraction 与 chat 失败路径均落到手动录入 |

### 1.2 框架选型与依赖清单

#### 后端 `server/requirements.txt`

```text
# --- Web 框架 ---
fastapi==0.115.6
uvicorn[standard]==0.34.0
python-multipart==0.0.20          # 文件上传
pydantic==2.10.4
pydantic-settings==2.7.0          # env 配置单例

# --- 数据层 ---
sqlalchemy==2.0.36                # ORM（同步 session，P0 足够）
psycopg[binary]==3.2.3
alembic==1.14.0                   # 迁移
greenlet==3.1.1

# --- 认证与安全 ---
python-jose[cryptography]==3.3.0  # JWT 签发/校验
passlib[bcrypt]==1.7.4
bcrypt==4.2.1                     # 固定版本避免 passlib 不兼容

# --- 任务队列 ---
redis==5.2.1

# --- 对象存储 ---
minio==7.2.12

# --- LLM 接入（OpenAI 兼容网关） ---
openai==1.59.6                    # base_url 可指向 NewAPI/Ollama/直连

# --- 文件处理 ---
pypdf==5.1.0                      # PDF 页转图/文本兜底（P0 规则解析）
Pillow==11.1.0                    # 图片归一化（EXIF 方向、缩放、灰度）
python-magic==0.4.27              # MIME 检测（容器内需 libmagic）
hashlib（标准库）                  # 文件去重 hash

# --- 观测 ---
structlog==24.4.0                 # 结构化日志
prometheus-client==0.21.1         # /metrics（可选，K8s 友好）

# --- 测试 ---
pytest==8.3.4
pytest-asyncio==0.25.2
httpx==0.28.1                     # TestClient / ASGI 测试
factory-boy==3.3.1
testcontainers[postgres]==4.9.0   # 集成测试（可选，CI 用）
```

> **刻意不引入**：Celery（Redis 轻量队列自研足够）、大型 OCR 包（PaddleOCR/MinerU 留作 P1 插件）、向量检索框架（P0 用 SQL，pgvector 列先建）。

#### 前端 `web/package.json`（依赖级）

```json
{
  "name": "ai-doctor-web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "lint": "eslint . --ext ts,tsx"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.28.0",
    "@tanstack/react-query": "^5.62.7",
    "zustand": "^5.0.2",
    "axios": "^1.7.9",
    "echarts": "^5.5.1",
    "echarts-for-react": "^3.0.2",
    "dayjs": "^1.11.13",
    "pdfjs-dist": "^4.9.155",
    "clsx": "^2.1.1"
  },
  "devDependencies": {
    "@types/react": "^18.3.12",
    "@types/react-dom": "^18.3.1",
    "@vitejs/plugin-react": "^4.3.4",
    "typescript": "^5.7.2",
    "vite": "^6.0.3",
    "vitest": "^2.1.8",
    "msw": "^2.6.8",
    "eslint": "^9.16.0",
    "sass": "^1.83.0"
  }
}
```

> PDF 预览：`pdfjs-dist` 在浏览器端渲染 P0 足够（AC-13.1）；图片直接 `<img>` + 签名 URL。

#### 选型理由对照（对齐 PRD §12.1）

| 层 | 选型 | 理由 |
|---|---|---|
| 后端 | FastAPI 单体 | Python OCR/VLM 生态；交付快；模块边界替代微服务 |
| 结构化存储 | PostgreSQL 16 + pgvector | 档案+时序 SQL+（P1）向量一体；幂等唯一约束/事务 |
| 队列/缓存 | Redis 7 | 预处理任务队列、限流、会话缓存；不引入 Celery |
| 对象存储 | MinIO | 原件证据层，S3 兼容，K8s 友好，签名 URL 带时效 |
| 前端 | React 18 + Vite + TS | 单页应用；TanStack Query 做服务端状态，Zustand 做全局 subject 状态 |
| LLM | openai SDK + base_url env | 网关隔离密钥；切 base_url 即接 Ollama 本地模型 |
| 解析 | VLM 直读 + 规则解析 | P0 快速打穿抽取闭环；解析器接口可插拔，P1 接 PaddleOCR/MinerU |

---

## 2. 目录结构与完整文件列表

代码仓库布局（相对仓库根 `私人医生系统/`，**完整文件清单**，`…` 展开见各节）：

```text
私人医生系统/
├── docs/                                  # 已有
│   ├── PRD.md
│   └── ARCHITECTURE.md                    # 本文档
├── server/                                # 后端
│   ├── Dockerfile
│   ├── Dockerfile.worker                  # 同 Dockerfile 不同 CMD（见部署节）
│   ├── requirements.txt
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/
│   │       └── 0001_init.py               # 全部表 + pgvector 扩展
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                        # FastAPI 实例、路由挂载、异常处理、middleware
│   │   ├── worker.py                      # worker 入口（python -m app.worker）
│   │   ├── config/
│   │   │   ├── __init__.py
│   │   │   └── settings.py                # pydantic-settings，全量 env 定义
│   │   ├── db/
│   │   │   ├── __init__.py
│   │   │   ├── base.py                    # DeclarativeBase、命名约定
│   │   │   ├── session.py                 # engine/SessionLocal/get_db
│   │   │   └── models/                    # SQLAlchemy 模型（与 §3 一一对应）
│   │   │       ├── __init__.py
│   │   │       ├── account.py             # accounts, last_subject_preferences
│   │   │       ├── subject.py             # subjects, profiles
│   │   │       ├── document.py            # documents, document_pages
│   │   │       ├── preprocessing.py       # derived_records, extraction_jobs, dictionary_misses
│   │   │       ├── observation.py         # observations, indicator_dictionary, indicator_ref_ranges
│   │   │       ├── event.py               # medical_events, medical_event_documents
│   │   │       ├── chat.py                # conversations, messages, message_drafts, traces, trace_refs
│   │   │       ├── redflag.py             # red_flag_rules, red_flag_hits
│   │   │       └── audit.py               # audit_logs
│   │   ├── auth/                          # F-01
│   │   │   ├── __init__.py
│   │   │   ├── router.py                  # /api/auth/* 路由
│   │   │   ├── service.py                 # 注册/登录/刷新逻辑
│   │   │   ├── security.py                # 密码哈希、JWT encode/decode
│   │   │   ├── deps.py                    # get_current_account, require_subject, ownership 校验
│   │   │   └── schemas.py
│   │   ├── subjects/                      # F-02/F-03
│   │   │   ├── __init__.py
│   │   │   ├── router.py                  # /api/subjects/*
│   │   │   ├── service.py
│   │   │   └── schemas.py
│   │   ├── documents/                     # F-04/F-13
│   │   │   ├── __init__.py
│   │   │   ├── router.py                  # /api/documents/*
│   │   │   ├── service.py                 # 上传、hash 去重、分类分流、入队
│   │   │   ├── storage.py                 # MinIO 封装：put/get/签名 URL/归档
│   │   │   └── schemas.py
│   │   ├── preprocessing/                 # F-05/F-06（worker 侧主逻辑）
│   │   │   ├── __init__.py
│   │   │   ├── queue.py                   # Redis 队列封装（enqueue/dequeue/retry/heartbeat）
│   │   │   ├── pipeline.py                # 分类→解析→抽取→映射 编排
│   │   │   ├── extractor.py               # VLM 直读抽取 + JSON schema 校验
│   │   │   ├── parser_base.py             # 可插拔解析器接口（见 §6.6）
│   │   │   ├── parsers/
│   │   │   │   ├── __init__.py            # 解析器注册表
│   │   │   │   ├── vlm_parser.py          # P0 默认：VLM 直读
│   │   │   │   └── rule_parser.py         # P0 兜底：PDF 文本 + 正则规则解析
│   │   │   ├── mapper.py                  # 指标字典映射（别名→code、参考区间版本化）
│   │   │   ├── review.py                  # HITL 确认入库（幂等、乐观锁、审计）
│   │   │   └── schemas.py
│   │   ├── observations/                  # F-08 趋势 + 字典管理
│   │   │   ├── __init__.py
│   │   │   ├── router.py                  # /api/observations/*, /api/dictionary/*
│   │   │   ├── trends.py                  # SQL 聚合趋势（确定性计算）
│   │   │   ├── dictionary.py              # 字典查询/未命中反馈
│   │   │   └── schemas.py
│   │   ├── timeline/                      # F-07
│   │   │   ├── __init__.py
│   │   │   ├── router.py                  # /api/timeline/*
│   │   │   ├── service.py                 # 事件流分组（年/月）、筛选
│   │   │   └── schemas.py
│   │   ├── chat/                          # F-09/F-10/F-12
│   │   │   ├── __init__.py
│   │   │   ├── router.py                  # /api/chat/* + /v1/chat/completions
│   │   │   ├── service.py                 # 会话/消息 CRUD
│   │   │   ├── orchestration.py           # 问诊编排主流程（见 §5.2）
│   │   │   ├── context_builder.py         # L0/L1/L2(/L4) 上下文组装器（见 §6.1）
│   │   │   ├── citations.py               # 引用标记生成 + Trace 落库
│   │   │   ├── draft_parser.py            # 对话式草稿卡片抽取与落库
│   │   │   └── schemas.py
│   │   ├── redflag/                       # F-11
│   │   │   ├── __init__.py
│   │   │   ├── engine.py                  # 规则匹配引擎（词表+逻辑，热更新）
│   │   │   ├── router.py                  # /api/redflag/rules CRUD
│   │   │   ├── loader.py                  # 规则缓存加载（Redis pub/sub 失效）
│   │   │   └── schemas.py
│   │   ├── sanitizer/                     # F-14 脱敏
│   │   │   ├── __init__.py
│   │   │   ├── pipeline.py                # 出域脱敏编排（见 §6.2）
│   │   │   ├── patterns.py                # 姓名/证件/电话/生日识别与掩码规则
│   │   │   └── tests_app.py               # 单测（PII 抽查用例）
│   │   ├── llm/                           # LLM 接入层
│   │   │   ├── __init__.py
│   │   │   ├── client.py                  # OpenAI 兼容客户端（timeout/retry/降级开关）
│   │   │   ├── extraction.py              # 抽取专用调用（VLM JSON 模式）
│   │   │   └── prompts/                   # 提示词模板（jinja2 文本）
│   │   │       ├── extract_report.md
│   │   │       ├── extract_draft.md
│   │   │       ├── consult_system.md      # 含免责、引用格式、依据不足声明要求
│   │   │       └── json_schemas/          # 抽取结果 JSON schema
│   │   │           ├── derived_record.schema.json
│   │   │           └── chat_draft.schema.json
│   │   ├── audit/                         # F-14
│   │   │   ├── __init__.py
│   │   │   ├── service.py                 # audit.record()（who/when/what/前值后值）
│   │   │   └── router.py                  # /api/audit/*（本人可见）
│   │   └── common/
│   │       ├── __init__.py
│   │       ├── errors.py                  # AppError + 错误码 + 全局 handler（见 §8.2）
│   │       ├── responses.py               # api_ok/api_err 包裹格式（见 §8.1）
│   │       ├── logging.py                 # structlog 配置 + request-id
│   │       ├── pagination.py              # 分页参数/返回
│   │       └── ids.py                     # ULID/UUID 生成
│   ├── tests/
│   │   ├── conftest.py                    # fixtures：db/session/test account
│   │   ├── unit/
│   │   │   ├── test_sanitizer.py
│   │   │   ├── test_redflag_engine.py
│   │   │   ├── test_dictionary_mapper.py
│   │   │   ├── test_context_builder.py
│   │   │   ├── test_trends_sql.py
│   │   │   └── test_review_idempotency.py
│   │   ├── api/
│   │   │   ├── test_auth.py               # AC-1.x
│   │   │   ├── test_subjects.py           # AC-2.x
│   │   │   ├── test_write_requires_subject.py  # AC-3.x（K4）
│   │   │   ├── test_documents.py          # AC-4.x
│   │   │   ├── test_review.py             # AC-6.x
│   │   │   ├── test_timeline_trends.py    # AC-7.x / AC-8.x
│   │   │   ├── test_chat.py               # AC-9.x / AC-10.x
│   │   │   ├── test_chat_draft.py         # AC-12.x
│   │   │   └── test_idor.py               # 跨 Account 404/403 专项
│   │   └── integration/
│   │       ├── test_extraction_pipeline.py
│   │       └── test_state_machine.py      # Document 状态机全转移
├── web/
│   ├── Dockerfile
│   ├── nginx.conf                         # SPA fallback + /api 反代
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── index.html
│   ├── public/
│   │   └── favicon.svg
│   └── src/
│       ├── main.tsx
│       ├── App.tsx                        # 路由树
│       ├── styles/
│       │   ├── global.scss
│       │   └── tokens.scss                # 设计变量
│       ├── api/
│       │   ├── client.ts                  # axios 实例、拦截器、错误码翻译、Token 注入
│       │   ├── auth.ts
│       │   ├── subjects.ts
│       │   ├── documents.ts
│       │   ├── timeline.ts
│       │   ├── observations.ts
│       │   ├── chat.ts                    # SSE 流式接收
│       │   ├── audit.ts
│       │   └── types.ts                   # 与后端 schemas 对应的 TS 类型
│       ├── stores/
│       │   ├── authStore.ts               # JWT/账户（zustand persist）
│       │   ├── subjectStore.ts            # 当前 subjectId（localStorage 记忆，AC-2.3）
│       │   └── uiStore.ts                 # 免责弹窗、全局提示
│       ├── hooks/
│       │   ├── useRequireAuth.ts
│       │   ├── useWriteGuard.ts           # 无 subject 时禁用写按钮（AC-3.1）
│       │   ├── useTimelineQuery.ts
│       │   ├── useTrendSeries.ts
│       │   └── useChatStream.ts           # SSE 消费
│       ├── components/
│       │   ├── layout/
│       │   │   ├── AppShell.tsx            # 顶栏 + 侧导航
│       │   │   ├── SubjectSwitcher.tsx     # 大 chip 切换器 + 新建（F-02）
│       │   │   └── DisclaimerBar.tsx       # 常驻免责（A-5）
│       │   ├── common/
│       │   │   ├── ErrorState.tsx
│       │   │   ├── EmptyGuide.tsx          # 空档案引导上传（AC-7.3）
│       │   │   ├── ConfidenceBadge.tsx
│       │   │   └── Modal.tsx
│       │   ├── documents/
│       │   │   ├── UploadDropzone.tsx      # 拖拽上传+格式大小校验（E-10）
│       │   │   ├── DocumentList.tsx
│       │   │   ├── DocumentViewer.tsx      # PDF/图片预览（F-13）
│       │   │   └── ReviewCard.tsx          # DerivedRecord 确认卡片（F-06）
│       │   ├── timeline/
│       │   │   ├── TimelineList.tsx        # 事件流按年/月分组
│       │   │   ├── TimelineFilters.tsx
│       │   │   └── TrendChart.tsx          # ECharts 折线+参考区间带（F-08）
│       │   └── chat/
│       │       ├── ConversationList.tsx
│       │           ├── MessageBubble.tsx
│       │           ├── CitationTag.tsx     # [依据：…] 标记 + 回链（F-10）
│       │           ├── RedFlagBanner.tsx   # 规则插入的转诊卡片（F-11）
│       │           ├── DraftCard.tsx       # 对话式草稿确认卡片（F-12）
│       │           └── TracePanel.tsx      # "查看依据"面板
│       └── pages/
│           ├── LoginPage.tsx               # / 登录·注册（F-01）
│           ├── AppLayout.tsx               # /app
│           ├── TimelinePage.tsx            # /app/timeline（F-07/F-08）
│           ├── ArchivePage.tsx             # /app/archive（F-04 列表/待确认）
│           ├── DocumentDetailPage.tsx      # /app/archive/:id（原件+确认卡片）
│           ├── ChatPage.tsx                # /app/chat（F-09）
│           ├── ChatConversationPage.tsx    # /app/chat/:conversationId
│           ├── SubjectsPage.tsx            # /app/subjects（F-02）
│           ├── SettingsPage.tsx            # /app/settings（脱敏开关/审计/免责，F-14）
│           └── AuditPage.tsx               # /app/settings/audit
├── deploy/
│   ├── compose.yaml                        # 一键全栈（验收基线）
│   ├── .env.example                        # 全量环境变量样例
│   ├── docker/
│   │   ├── api.dockerfile                  # （若与 server/ 内分离则二选一，P0 放 server/）
│   │   └── nginx.conf                      # （P0 用 web/nginx.conf）
│   ├── k8s/
│   │   ├── namespace.yaml
│   │   ├── configmap.yaml
│   │   ├── secret.yaml                     # 示例值，README 注明生产用 sealed-secret
│   │   ├── postgres-pvc.yaml
│   │   ├── minio-pvc.yaml
│   │   ├── redis-pvc.yaml
│   │   ├── postgres-deployment.yaml
│   │   ├── postgres-service.yaml
│   │   ├── redis-deployment.yaml
│   │   ├── redis-service.yaml
│   │   ├── minio-deployment.yaml
│   │   ├── minio-service.yaml
│   │   ├── api-deployment.yaml             # 含 initContainer 等待 DB + alembic upgrade
│   │   ├── api-service.yaml
│   │   ├── worker-deployment.yaml
│   │   ├── web-deployment.yaml
│   │   ├── web-service.yaml                # （P0 LoadBalancer/NodePort 二选一）
│   │   └── kustomization.yaml
│   └── README.md                           # 部署手册：compose 起停、K8s 应用、备份策略
├── scripts/
│   ├── seed_dictionary.py                  # 冷启动 TOP50 指标字典 + TOP20 趋势指标清单
│   ├── seed_redflag_rules.py               # 红旗规则初版导入
│   └── smoke_e2e.sh                        # compose 起后冒烟：注册→上传→确认→问诊
└── Makefile                                # make up / migrate / seed / test / lint
```

> 文件总数估算：后端 ~85、前端 ~50、部署 ~20、脚本/测试 ~25，合计 **~180 个文件**。任务列表（§7）按批次拆分给工程师。

---

## 3. 数据库设计（DDL + ER 图）

### 3.1 ER 图（Mermaid，对齐 PRD §5.1）

```mermaid
erDiagram
    accounts ||--o{ subjects : "拥有(N个)"
    accounts ||--o{ audit_logs : "产生"
    accounts ||--o| last_subject_preferences : "记忆上次选档"
    subjects ||--o| profiles : "基本信息(L0)"
    subjects ||--o{ medical_events : "事件时间线"
    subjects ||--o{ observations : "指标观测"
    subjects ||--o{ documents : "证据原件"
    subjects ||--o{ conversations : "问诊会话"
    medical_events }o--o{ documents : "event_documents(refs)"
    medical_events ||--o{ observations : "可关联(event_id)"
    observations }o--|| indicator_dictionary : "code映射"
    observations }o--o| documents : "source_document_id"
    documents ||--o{ derived_records : "AI抽取草稿"
    documents ||--o{ document_pages : "分页渲染"
    documents ||--o{ extraction_jobs : "处理任务"
    conversations ||--o{ messages : "消息"
    conversations ||--o{ message_drafts : "对话式草稿(F-12)"
    messages ||--o{ traces : "回答溯源"
    traces ||--o{ trace_refs : "引用明细"
    trace_refs }o--|| observations : "→obs"
    trace_refs }o--|| medical_events : "→event"
    trace_refs }o--|| documents : "→document"
    indicator_dictionary ||--o{ indicator_ref_ranges : "参考区间(版本化)"
    red_flag_rules ||--o{ red_flag_hits : "命中记录"
    dictionary_misses }o--|| documents : "字典未命中反馈"
```

### 3.2 全量 DDL（PostgreSQL，Alembic 迁移 `0001_init`）

> 约定：所有主键 `id TEXT PRIMARY KEY`（ULID，26 字符，可排序，见 §8.4）；所有表含 `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`、`updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`（下文省略）；软删字段 `deleted_at TIMESTAMPTZ` 仅在注明的表存在。时间统一 `TIMESTAMPTZ`，存 UTC，前端按本地时区渲染。

```sql
-- 0. 扩展
CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector（P0 只建列不填充）
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- 字典别名模糊匹配（可选）

-- 1. accounts（F-01）
CREATE TABLE accounts (
    id            TEXT PRIMARY KEY,
    email         CITEXT UNIQUE NOT NULL,   -- 或 lower(email) 唯一部分索引
    password_hash TEXT NOT NULL,            -- bcrypt
    display_name  TEXT,
    status        TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active','disabled')),
    last_login_at TIMESTAMPTZ
);

-- 2. subjects（F-02/F-03；P1 预留 member 角色，仅注释不实现）
CREATE TABLE subjects (
    id         TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    relation   TEXT NOT NULL DEFAULT '本人'
               CHECK (relation IN ('本人','父亲','母亲','祖父母','外祖父母','配偶','儿子','女儿','其他')),
    birthday   DATE,
    gender     TEXT CHECK (gender IN ('male','female','unknown')),
    color      TEXT,                        -- 顶栏 chip 颜色
    avatar     TEXT,                        -- 头像 key 或 emoji
    is_default BOOLEAN NOT NULL DEFAULT false,   -- 注册时自动建的"本人"
    -- P1 留缝：role TEXT DEFAULT 'owner'（viewer/editor），P0 不实现
    deleted_at TIMESTAMPTZ,
    UNIQUE (account_id, name)               -- 同档案不重名（可按需放宽）
);
CREATE INDEX idx_subjects_account ON subjects(account_id) WHERE deleted_at IS NULL;
-- 每 Account 仅一个 is_default=true：部分唯一索引
CREATE UNIQUE INDEX uq_subject_default ON subjects(account_id) WHERE is_default;

-- 3. last_subject_preferences（AC-2.3 记忆上次选择）
CREATE TABLE last_subject_preferences (
    account_id TEXT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    subject_id TEXT NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 4. profiles（L0 静态上下文来源）
CREATE TABLE profiles (
    subject_id   TEXT PRIMARY KEY REFERENCES subjects(id) ON DELETE CASCADE,
    blood_type   TEXT CHECK (blood_type IN ('A','B','AB','O','unknown')),
    allergies    TEXT NOT NULL DEFAULT '',     -- 结构化 JSON 数组也可，P0 文本
    chronic_diseases TEXT NOT NULL DEFAULT '',
    medications  TEXT NOT NULL DEFAULT '',     -- 用药中
    family_history   TEXT NOT NULL DEFAULT '',
    lifestyle    TEXT NOT NULL DEFAULT '',
    extra        JSONB NOT NULL DEFAULT '{}'   -- 扩展字段
);

-- 5. documents（F-04/F-13；状态机 PRD §9.2）
CREATE TABLE documents (
    id            TEXT PRIMARY KEY,
    account_id    TEXT NOT NULL REFERENCES accounts(id),
    subject_id    TEXT NOT NULL REFERENCES subjects(id),   -- 写入必须选档（B-1）
    kind          TEXT NOT NULL DEFAULT 'IMAGE'
                  CHECK (kind IN ('PDF','IMAGE','DICOM','AUDIO','TEXT')),  -- DICOM 为 P1 预留
    doc_type      TEXT CHECK (doc_type IN ('lab_report','imaging_report','visit_record','other')),
                -- 分类分流结果（化验单/影像报告/病历/其他）；未分类为 NULL
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','extracting','awaiting_review',
                                    'confirmed','parsed_no_data','failed','archived')),
                -- pending 待处理 | extracting 抽取中 | awaiting_review 待确认
                -- | confirmed 已确认 | parsed_no_data 已解析但无事件 | failed 解析失败
                -- | archived 归档（B-3，非删除）
    file_key      TEXT NOT NULL,             -- MinIO object key（证据层，永不物理删除）
    file_name     TEXT NOT NULL,
    mime_type     TEXT NOT NULL,
    size_bytes    BIGINT NOT NULL CHECK (size_bytes <= 20971520),  -- ≤20MB（E-10）
    content_hash  TEXT NOT NULL,             -- sha256，重复检测（E-6）
    page_count    INT,
    failure_reason TEXT,                     -- E-1 记录原因
    review_version INT NOT NULL DEFAULT 0,   -- 乐观锁（E-7），确认时 +1
    archived_at   TIMESTAMPTZ,               -- B-3 软删
    deleted_at    TIMESTAMPTZ                -- 仅合规流程
);
-- E-6 同 subject 同内容去重（强制上传需显式覆盖，服务层处理）
CREATE UNIQUE INDEX uq_doc_hash ON documents(subject_id, content_hash) WHERE deleted_at IS NULL;
CREATE INDEX idx_doc_subject_status ON documents(subject_id, status);

-- 6. document_pages（多页 PDF/VLM 分页处理）
CREATE TABLE document_pages (
    id          TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_no     INT NOT NULL,
    image_key   TEXT,                        -- 渲染页图 MinIO key（原件预览用）
    UNIQUE (document_id, page_no)
);

-- 7. extraction_jobs（预处理队列持久化；Redis 只做信号，DB 为事实源）
CREATE TABLE extraction_jobs (
    id          TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    attempt     INT NOT NULL DEFAULT 0,      -- E-1 最多重试 1 次
    state       TEXT NOT NULL DEFAULT 'queued'
                CHECK (state IN ('queued','running','succeeded','failed','cancelled')),
    parser      TEXT,                        -- 实际使用的解析器名（vlm/rule）
    error       TEXT,
    started_at  TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    worker_id   TEXT,
    lease_until TIMESTAMPTZ                 -- worker 租约，崩溃可回收
);

-- 8. derived_records（F-05/F-06 抽取草稿；幂等核心，B-4）
CREATE TABLE derived_records (
    id           TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    subject_id   TEXT NOT NULL REFERENCES subjects(id),
    version      INT NOT NULL DEFAULT 1,     -- 草稿版本（重试/重新抽取 +1）
    payload      JSONB NOT NULL,             -- 完整草稿：{report_date, doc_type_suggest,
                 --   events:[{...}], observations:[{value, unit, ref_range, raw_name,
                 --   code, confidence, bbox, status}]}, status ∈ proposed|confirmed|corrected|rejected
    parser       TEXT NOT NULL,              -- 'vlm' | 'rule' | 'chat' | 'manual'
    overall_confidence NUMERIC(4,3),         -- 整体置信度（字段级在 payload 内）
    source_text  TEXT,                       -- 解析出的原文（L3 P1 embedding 源，先存文本）
    created_by   TEXT NOT NULL DEFAULT 'system',  -- system | account_id
    confirmed_at TIMESTAMPTZ,
    UNIQUE (document_id, version)            -- B-4 幂等：同文档同版本唯一
);
CREATE INDEX idx_dr_subject_status ON derived_records(subject_id, confirmed_at);

-- 9. dictionary_misses（E-3 字典未命中反馈队列）
CREATE TABLE dictionary_misses (
    id          TEXT PRIMARY KEY,
    subject_id  TEXT NOT NULL REFERENCES subjects(id),
    document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
    raw_name    TEXT NOT NULL,
    raw_value   TEXT,
    raw_unit    TEXT,
    resolved    BOOLEAN NOT NULL DEFAULT false,
    resolved_to_code TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 10. indicator_dictionary（指标字典，冷启动 TOP50，scripts/seed 导入）
CREATE TABLE indicator_dictionary (
    id          TEXT PRIMARY KEY,
    code        TEXT NOT NULL UNIQUE,        -- 标准码，如 GLU_FASTING
    name_cn     TEXT NOT NULL,               -- 标准中文名
    category    TEXT NOT NULL DEFAULT '其他', -- 血液/生化/尿液/血压/其他
    unit        TEXT,                        -- 标准单位
    aliases     TEXT[] NOT NULL DEFAULT '{}',-- 别名（老人手写法）
    loinc_code  TEXT,                        -- P2 联网对齐留缝
    is_top_trend BOOLEAN NOT NULL DEFAULT false, -- 是否 TOP20 趋势指标（F-08）
    active      BOOLEAN NOT NULL DEFAULT true
);

-- 11. indicator_ref_ranges（参考区间版本化，§9.3：换指南不改历史）
CREATE TABLE indicator_ref_ranges (
    id            TEXT PRIMARY KEY,
    dictionary_id TEXT NOT NULL REFERENCES indicator_dictionary(id) ON DELETE CASCADE,
    version       INT NOT NULL DEFAULT 1,
    low_value     NUMERIC,
    high_value    NUMERIC,
    text_range    TEXT,                      -- 如 "阴性"
    applies_to    JSONB NOT NULL DEFAULT '{}', -- {"gender":"male","age_min":18,"age_max":70}
    effective_from DATE NOT NULL DEFAULT CURRENT_DATE,
    effective_to   DATE,
    UNIQUE (dictionary_id, version)
);

-- 12. medical_events（F-07；双时间轴 B-2）
CREATE TABLE medical_events (
    id           TEXT PRIMARY KEY,
    account_id   TEXT NOT NULL REFERENCES accounts(id),
    subject_id   TEXT NOT NULL REFERENCES subjects(id),
    event_type   TEXT NOT NULL
                 CHECK (event_type IN ('visit','hospitalization','surgery','checkup',
                                       'medication','vaccine','self_reported','other')),
    occurred_at  DATE NOT NULL,              -- 业务发生时间（AI 时序推理唯一依据）
    occurred_at_precision TEXT NOT NULL DEFAULT 'day'
                 CHECK (occurred_at_precision IN ('day','month','year','unknown')),
    recorded_at  TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 录入时间（不覆盖 occurred_at）
    title        TEXT,
    narrative    TEXT,
    diagnosis    TEXT,
    department   TEXT,                        -- 科室
    event_source TEXT NOT NULL DEFAULT 'document'
                 CHECK (event_source IN ('document','chat','manual')),
    document_id  TEXT REFERENCES documents(id), -- sourceDocumentId（事件级主证据）
    draft_id     TEXT,                        -- 来源草稿：derived_records.id 或 message_drafts.id
    embedding    VECTOR(1024),                -- pgvector 预定义（P1 L3 语义层填充，P0 为 NULL）
    deleted_at   TIMESTAMPTZ
);
-- B-4 幂等防重：同文档同事件唯一
CREATE UNIQUE INDEX uq_event_draft ON medical_events(document_id, draft_id)
    WHERE document_id IS NOT NULL AND draft_id IS NOT NULL;
CREATE INDEX idx_event_subject_time ON medical_events(subject_id, occurred_at DESC);
CREATE INDEX idx_event_embedding ON medical_events USING ivfflat (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;              -- P1 启用

-- 13. medical_event_documents（事件 ↔ 证据多对多 refs[]）
CREATE TABLE medical_event_documents (
    event_id    TEXT NOT NULL REFERENCES medical_events(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES documents(id),
    ref_note    TEXT,
    PRIMARY KEY (event_id, document_id)
);

-- 14. observations（F-08；结构化时序）
CREATE TABLE observations (
    id                 TEXT PRIMARY KEY,
    account_id         TEXT NOT NULL REFERENCES accounts(id),
    subject_id         TEXT NOT NULL REFERENCES subjects(id),
    event_id           TEXT REFERENCES medical_events(id) ON DELETE SET NULL,
    dictionary_id      TEXT REFERENCES indicator_dictionary(id),  -- 未命中为 NULL
    code               TEXT,                 -- 冗余字典码（未命中存 NULL）
    display_name       TEXT NOT NULL,        -- 展示名；未命中=原文（E-3 unmapped 标记）
    value_num          NUMERIC,              -- 数值型值
    value_text         TEXT,                 -- 文本型值（如 阴性/阳性）
    unit               TEXT,
    ref_low            NUMERIC,              -- 该次报告内参考区间快照
    ref_high           NUMERIC,
    ref_text           TEXT,
    abnormal_flag      TEXT CHECK (abnormal_flag IN ('N','L','H','A')),  -- 正常/低/高/异常
    occurred_at        DATE NOT NULL,        -- 报告内检查日期（双时间轴）
    recorded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_document_id TEXT REFERENCES documents(id),
    source_field_ref   TEXT,                 -- 原文位置（bbox/页码/字段路径），对应 DerivedRecord
    confidence         NUMERIC(4,3),         -- 抽取置信度（数据结构 P0 建立，HITL 后为已确认值）
    is_confirmed       BOOLEAN NOT NULL DEFAULT false,
    embedding          VECTOR(1024),         -- pgvector 预定义（P1）
    deleted_at         TIMESTAMPTZ
);
-- B-4 幂等：同一文档 + 同一字段来源不重复入库
CREATE UNIQUE INDEX uq_obs_doc_field ON observations(source_document_id, source_field_ref)
    WHERE source_document_id IS NOT NULL AND source_field_ref IS NOT NULL;
CREATE INDEX idx_obs_subject_code_time ON observations(subject_id, code, occurred_at);
CREATE INDEX idx_obs_subject_time ON observations(subject_id, occurred_at DESC);

-- 15. conversations（F-09；状态机 active → archived）
CREATE TABLE conversations (
    id          TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL REFERENCES accounts(id),
    subject_id  TEXT NOT NULL REFERENCES subjects(id),   -- 会话级锁定（A-7）
    title       TEXT,
    status      TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','archived')),  -- §9.2
    archived_at TIMESTAMPTZ,
    deleted_at  TIMESTAMPTZ
);
CREATE INDEX idx_conv_subject ON conversations(subject_id, status);

-- 16. messages
CREATE TABLE messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
    content         TEXT NOT NULL,
    red_flag_hit_id TEXT,                    -- 关联 red_flag_hits（红旗卡片）
    llm_model       TEXT,
    latency_ms      INT,
    error_state     TEXT,                    -- 'timeout' 等（E-5，半截内容不落 Trace）
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_msg_conv ON messages(conversation_id, created_at);

-- 17. message_drafts（F-12 对话式草稿卡片）
CREATE TABLE message_drafts (
    id              TEXT PRIMARY KEY,
    message_id      TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    subject_id      TEXT NOT NULL REFERENCES subjects(id),
    draft_type      TEXT NOT NULL CHECK (draft_type IN ('event','observation','both')),
    payload         JSONB NOT NULL,          -- {event:{type,title,narrative,occurred_at,...},
                    --    observations:[{display_name,value,unit,occurred_at,...}]}
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','confirmed','discarded')),
    confirmed_at    TIMESTAMPTZ,
    result_event_id TEXT REFERENCES medical_events(id),
    result_observation_ids TEXT[] DEFAULT '{}',
    discarded_at    TIMESTAMPTZ              -- 会话删除草稿 → discard，无孤儿数据（AC-12.3）
);

-- 18/19. traces + trace_refs（F-10；A-3）
CREATE TABLE traces (
    id              TEXT PRIMARY KEY,
    message_id      TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    subject_id      TEXT NOT NULL REFERENCES subjects(id),
    context_layers  JSONB NOT NULL DEFAULT '{}',
                    -- {L0:[...], L1:[{code,series:[...]}], L2:[event_id...], L4:[doc_id...]}
    prompt_tokens   INT,
    completion_tokens INT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_trace_message ON traces(message_id);   -- 一次回答一条 Trace（E-5 失败则无）

CREATE TABLE trace_refs (
    id           TEXT PRIMARY KEY,
    trace_id     TEXT NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
    ref_index    INT NOT NULL,               -- 引用序号 [1] [2]…
    ref_type     TEXT NOT NULL CHECK (ref_type IN ('observation','event','document')),
    ref_id       TEXT NOT NULL,              -- 多态 ID（不建 FK，统一在 service 校验归属）
    label        TEXT NOT NULL               -- 展示文案："2026-03-12 血常规报告"
);
CREATE INDEX idx_trace_ref ON trace_refs(trace_id);

-- 20. red_flag_rules（F-11；可热更新）
CREATE TABLE red_flag_rules (
    id            TEXT PRIMARY KEY,
    code          TEXT NOT NULL UNIQUE,      -- e.g. RF_CHEST_PAIN_RADIATING
    name          TEXT NOT NULL,             -- "胸痛伴放射痛"
    category      TEXT NOT NULL DEFAULT '成人急症',
    keywords      TEXT[] NOT NULL,           -- 词表（正则串）
    keyword_logic TEXT NOT NULL DEFAULT 'any' CHECK (keyword_logic IN ('any','all')),
    context_logic JSONB,                     -- 结构化条件：{"all":[{...},{any:[...]}]}
                                            -- 比较项：symptom/value/unit/window 等
    severity      TEXT NOT NULL DEFAULT 'high' CHECK (severity IN ('high','medium')),
    message       TEXT NOT NULL,             -- 强制展示的转诊文案
    action        TEXT NOT NULL DEFAULT 'refer_now',
    enabled       BOOLEAN NOT NULL DEFAULT true,
    version       INT NOT NULL DEFAULT 1,
    effective_from TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 热更新：每 60s 轮询 max(version, updated_at) + Redis pub/sub 主动失效

-- 21. red_flag_hits（A-1 命中即审计）
CREATE TABLE red_flag_hits (
    id              TEXT PRIMARY KEY,
    rule_id         TEXT NOT NULL REFERENCES red_flag_rules(id),
    message_id      TEXT REFERENCES messages(id) ON DELETE SET NULL,
    conversation_id TEXT,
    subject_id      TEXT NOT NULL REFERENCES subjects(id),
    account_id      TEXT NOT NULL REFERENCES accounts(id),
    matched_terms   TEXT[],
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 22. audit_logs（F-14；B-5 不可绕过）
CREATE TABLE audit_logs (
    id             TEXT PRIMARY KEY,
    account_id     TEXT REFERENCES accounts(id),     -- 系统动作为 NULL
    subject_id     TEXT REFERENCES subjects(id),
    actor_type     TEXT NOT NULL DEFAULT 'user'
                   CHECK (actor_type IN ('user','system','worker')),
    actor_id       TEXT,
    action         TEXT NOT NULL,   -- subject.create / document.upload / record.confirm /
                 -- message.send / redflag.hit / auth.login …
    resource_type  TEXT NOT NULL,   -- account|subject|document|derived_record|event|
                 -- observation|conversation|draft|rule
    resource_id    TEXT,
    before_value   JSONB,
    after_value    JSONB,
    request_id     TEXT,            -- 关联结构化日志
    ip             TEXT,
    user_agent     TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_account_time ON audit_logs(account_id, created_at DESC);
CREATE INDEX idx_audit_resource ON audit_logs(resource_type, resource_id);
```

### 3.3 状态机落点对照

| 状态机（PRD §9.2） | 字段 | 转移实现位置 |
|---|---|---|
| Document：pending→extracting→awaiting_review→confirmed / parsed_no_data；pending→failed；failed→pending；confirmed→awaiting_review（撤回） | `documents.status` | `preprocessing/pipeline.py`（前向）、`preprocessing/review.py`（确认/拒绝/撤回）、`documents/service.py`（重试）；转移函数校验合法边，非法转移抛 `E_STATE_INVALID` |
| DerivedRecord 字段级：proposed → confirmed｜corrected｜rejected | `derived_records.payload.observations[].status` | `preprocessing/review.py`；`rejected` 字段不生成 Observation |
| Conversation：active → archived | `conversations.status` | `chat/service.py`（切换 Subject/用户关闭） |

---

## 4. 接口设计（REST API 清单）

### 4.1 全局约定

- 前缀：全部 `/api`（除兼容端点 `/v1/chat/completions`）。
- 鉴权：`Authorization: Bearer <JWT>`（access token，默认 7d，见 §8.5）；健康检查与注册登录除外。
- 写接口（Create/Update 落库）：请求体必须显式携带 `subjectId`，缺失 → **422**（B-1）。
- 越权（他 Account 资源）→ **404**（防 IDOR 探测，K4）。
- 响应统一包裹：`{code, message, data, requestId}`（§8.1）；下表"响应"列给 `data` 结构。
- 分页：`?page=1&pageSize=20` → `data:{items:[], page, pageSize, total}`。

### 4.2 完整 API 清单

| # | 方法 | 路径 | 功能（AC） | 请求（body / query） | 响应 data | 鉴权 |
|---|---|---|---|---|---|---|
| **账户 F-01** |
| 1 | POST | `/api/auth/register` | 注册并自动登录、建默认 Subject（AC-1.1） | `{email, password, displayName?}` | `{token, account, defaultSubject}` | 公开 |
| 2 | POST | `/api/auth/login` | 登录（AC-1.2：失败统一 401） | `{email, password}` | `{token, account, lastSubjectId}` | 公开 |
| 3 | POST | `/api/auth/refresh` | 刷新 token | — | `{token}` | Bearer |
| 4 | GET | `/api/auth/me` | 当前账户 | — | `{account, subjects[], lastSubjectId}` | Bearer |
| **Subject F-02/F-03** |
| 5 | GET | `/api/subjects` | 成员列表 | — | `[{id,name,relation,color,isDefault,avatar}]` | Bearer |
| 6 | POST | `/api/subjects` | 新建（AC-2.1） | `{name, relation, birthday?, gender?, color?}` | `subject` | Bearer |
| 7 | PATCH | `/api/subjects/{id}` | 编辑 | 同上（部分） | `subject` | Bearer+归属 |
| 8 | DELETE | `/api/subjects/{id}` | 删除（级联：会话归档、文件软删、审计保留） | — | `{archivedConversations, softDeletedFiles}` | Bearer+归属 |
| 9 | PUT | `/api/subjects/last-selected` | 记忆上次选档（AC-2.3） | `{subjectId}` | `{subjectId}` | Bearer |
| **上传 F-04（E-6/E-10）** |
| 10 | POST | `/api/documents` | 上传（multipart：`file` + `subjectId` + `force?`） | 表单 | `{document, duplicate?:true}`；重复且未 force → **409 code=DUP_FILE** | Bearer+subject |
| 11 | GET | `/api/documents` | 列表（筛选 status/type） | `?subjectId&status&docType&page` | 分页 `document[]` | Bearer |
| 12 | GET | `/api/documents/{id}` | 详情（含草稿、任务） | — | `{document, derivedRecord?, job?}` | Bearer+归属 |
| 13 | POST | `/api/documents/{id}/retry` | 解析失败重试（E-1，1 次） | — | `{job}` | Bearer+subject |
| 14 | POST | `/api/documents/{id}/archive` | 归档（B-3，替代删除） | — | `document` | Bearer+subject |
| 15 | GET | `/api/documents/{id}/content` | 原件签名 URL（AC-13.3 带过期；过期 403） | `?page=` | `{url, expiresAt}` | Bearer+归属 |
| 16 | GET | `/api/documents/{id}/pages` | 分页页图 URL 列表（PDF 预览） | — | `[{pageNo, url}]` | Bearer+归属 |
| **抽取与确认 F-05/F-06** |
| 17 | GET | `/api/documents/{id}/derived-record` | 取草稿（确认卡片数据） | — | `{id, version, payload, parser, confidenceSummary}` | Bearer+subject |
| 18 | POST | `/api/documents/{id}/derived-record/confirm` | HITL 确认入库（AC-6.1~6.4） | `{version, decisions:[{fieldRef, action:'confirm'\|'correct'\|'reject', value?, note?}]}`；**全部字段必须显式决策**（P0 全强制） | `{documentStatus, events[], observations[], auditId}`；版本冲突 → **409 code=REVIEW_CONFLICT**（E-7） | Bearer+subject |
| 19 | POST | `/api/documents/{id}/derived-record/reextract` | 重新抽取（version+1） | — | `{derivedRecord}` | Bearer+subject |
| 20 | GET | `/api/dictionary/misses` | 字典未命中队列（E-3） | `?resolved` | 分页 | Bearer |
| 21 | POST | `/api/dictionary/misses/{id}/resolve` | 反馈归并到字典 | `{code \| newCode}` | `miss` | Bearer |
| **时间线/趋势 F-07/F-08** |
| 22 | GET | `/api/timeline/events` | 事件流（按 occurredAt 倒序，年/月分组服务端可选） | `?subjectId&types&from&to&page` | `{groups:[{year,month,items:[event]}], total}` | Bearer+subject 归属 |
| 23 | GET | `/api/timeline/summary` | 时间线摘要（角标、类型计数） | `?subjectId` | `{typeCounts, dateRange, documentCount}` | Bearer |
| 24 | GET | `/api/observations/trend` | 趋势序列（AC-8.1 SQL 确定性；<2 点返回 `insufficient:true`） | `?subjectId&code&from&to&limit=20` | `{code, name, unit, points:[{occurredAt, valueNum, abnormalFlag, refLow, refHigh, documentId}], refBand:{low,high}, insufficient}` | Bearer |
| 25 | GET | `/api/observations` | 观测列表/明细 | `?subjectId&code&page` | 分页 | Bearer |
| 26 | GET | `/api/dictionary/trend-options` | TOP20 趋势指标选项 | — | `[{code,name,unit,category}]` | Bearer |
| **问诊 F-09/F-10/F-12** |
| 27 | GET | `/api/chat/conversations` | 会话列表（`?status=active`） | — | `[{id,subjectId,title,status,lastMessageAt}]` | Bearer |
| 28 | POST | `/api/chat/conversations` | 新建会话（绑定 subjectId，A-7） | `{subjectId, title?}` | `conversation` | Bearer+subject |
| 29 | GET | `/api/chat/conversations/{id}` | 会话详情+消息 | — | `{conversation, messages[], drafts[]}` | Bearer+归属 |
| 30 | POST | `/api/chat/conversations/{id}/messages` | 发送消息→流式回答（AC-9.2/9.4） | `{content}` | **SSE**：`event: meta\|delta\|citations\|redflag\|draft\|done\|error`（§5.2） | Bearer+归属 |
| 31 | POST | `/api/chat/conversations/{id}/close` | 归档会话（状态机） | — | `conversation` | Bearer+归属 |
| 32 | GET | `/api/chat/messages/{id}/trace` | 查看依据（AC-10.3） | — | `{traceId, layers, refs:[{refType,refId,label,url}]}` | Bearer+归属 |
| 33 | POST | `/api/chat/drafts/{id}/confirm` | 草稿确认入库（AC-12.2，subjectId 隐含自草稿） | `{payload?}`（编辑后以编辑值为准） | `{eventId, observationIds}` | Bearer+归属 |
| 34 | POST | `/api/chat/drafts/{id}/discard` | 丢弃草稿（AC-12.3） | — | `{status:'discarded'}` | Bearer+归属 |
| 35 | POST | `/v1/chat/completions` | **OpenAI 兼容内部约定**（§4.3） | OpenAI 形态 | OpenAI 形态 | Bearer（JWT 或网关 token） |
| **红旗 F-11** |
| 36 | GET | `/api/redflag/rules` | 规则列表（管理页） | `?enabled` | `rule[]` | Bearer |
| 37 | POST | `/api/redflag/rules` | 新建规则（热更新） | `{code,name,keywords,keywordLogic,contextLogic?,severity,message}` | `rule` | Bearer |
| 38 | PATCH | `/api/redflag/rules/{id}` | 修改/启停（version+1，AC-11.3） | 部分字段 | `rule` | Bearer |
| 39 | POST | `/api/redflag/rules/test` | 规则试跑（文本→命中预览） | `{text}` | `{hits:[{code,message}]}` | Bearer |
| **成员/设置/审计 F-14** |
| 40 | GET | `/api/audit/logs` | 本人操作审计（AC-14.4） | `?subjectId&action&from&to&page` | 分页 `auditLog[]` | Bearer |
| 41 | GET | `/api/settings` | 脱敏开关、本地模型开关、免责版本 | — | `{piiMaskingEnabled, localModelMode, disclaimerVersion, confidenceThreshold(0.85, 只读)}` | Bearer |
| 42 | PATCH | `/api/settings` | 修改（脱敏开关仅可开不可关？→ 见待明确 Q-2） | `{piiMaskingEnabled?}` | `settings` | Bearer |
| 43 | GET | `/api/subjects/{id}/stats` | 档案概览（成员页） | — | `{eventCount, observationCount, documentCount, lastRecordedAt}` | Bearer+归属 |
| **运维** |
| 44 | GET | `/healthz` | 探活（含 DB/Redis/MinIO 依赖检查，§12.2） | — | `{status, deps:{db,redis,minio}}` | 公开 |
| 45 | GET | `/readyz` | 就绪（K8s readiness） | — | 同上 | 公开 |
| 46 | GET | `/metrics` | Prometheus 指标 | — | 文本 | 内网 |

### 4.3 OpenAI Chat Completions 兼容内部约定

`POST /v1/chat/completions` 按 OpenAI 形态暴露，供 P1 多客户端/外部网关调用；内部仍走同一编排（脱敏→组装→LLM→Trace→红旗）：

```jsonc
// 请求（OpenAI 兼容字段 + 私有扩展，扩展字段放顶层并以 "_" 前缀避免冲突）
{
  "model": "primary",                     // 逻辑模型名，settings 映射到网关真实模型
  "messages": [{"role": "user", "content": "我妈血糖最近怎么样"}],
  "stream": true,
  "_subject_id": "01J...",                // 必填；缺失 → 422（B-1）
  "_conversation_id": "01J..."             // 可选；缺省自动建会话
}
// 响应：OpenAI 标准 chunks（choices[].delta.content），私有扩展字段
// 于流结束前以 delta 附加：
// {"choices":[{"delta":{"_trace_id":"01J...","_citations":[{"i":1,"label":"2026-03-12 血常规报告","document_id":"01J..."}],"_red_flag":{"code":"RF_..."}}}] }
// 错误：OpenAI 错误形态 {"error":{"message","type","code"}}，HTTP 4xx/5xx
```

约束：① `_citations` 中每项必有 `document_id`（A-3，无原件 ID 不准出现）；② 会话消息历史只取本 subject（A-7）；③ LLM 失败重试 1 次后返回 `type=server_error, code=LLM_TIMEOUT`，不产生半截 Trace（E-5/AC-9.4）；④ 该端点同样过脱敏管线（A-4）。

### 4.4 错误码规范（与 §8.2 联动）

| HTTP | code | 场景 |
|---|---|---|
| 400 | `BAD_REQUEST` | 参数格式错误 |
| 401 | `UNAUTHENTICATED` / `TOKEN_EXPIRED` | 未登录/过期（AC-1.3）；登录失败也返回 401 `INVALID_CREDENTIALS`（不泄露账号存在性） |
| 403 | `LINK_EXPIRED` | 原件链接过期（E-9） |
| 403/404 | `NOT_FOUND` | 越权一律 404（K4/IDOR） |
| 409 | `DUP_FILE` | 重复上传（E-6） |
| 409 | `REVIEW_CONFLICT` | 并发确认（E-7） |
| 409 | `STATE_INVALID | 非法状态转移 |
| 413 | `FILE_TOO_LARGE` | >20MB（E-10） |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | 非白名单格式（E-10） |
| 422 | `SUBJECT_REQUIRED` | 写请求缺 subjectId（B-1/AC-3.2） |
| 422 | `VALIDATION_ERROR` | pydantic 校验 |
| 429 | `RATE_LIMITED` | Redis 限流（问诊/上传） |
| 502/504 | `LLM_UNAVAILABLE` / `LLM_TIMEOUT` | LLM 网关错误/超时（E-5） |
| 500 | `INTERNAL` | 未捕获异常（含 request_id） |

---

## 5. 关键时序图

### 5.1 上传 → worker 抽取 → 确认入库（F-04/F-05/F-06）

```mermaid
sequenceDiagram
    autonumber
    participant U as Web(SPA)
    participant API as FastAPI
    participant DB as PostgreSQL
    participant RQ as Redis
    participant W as Worker
    participant M as MinIO
    participant L as LLM网关(OpenAI兼容)

    U->>API: POST /api/documents (multipart, subjectId, token)
    API->>API: 鉴权 + require_subject(B-1) + 格式/20MB 校验(E-10)
    API->>DB: sha256(content) 查重 (subject_id, content_hash)
    alt 重复且未 force
        API-->>U: 409 DUP_FILE {duplicate:true}（E-6：提示仍要上传/取消）
    end
    API->>M: put objects/{accountId}/{subjectId}/{docId}/original
    API->>DB: INSERT documents(status=pending) + extraction_jobs(queued)
    API->>RQ: LPUSH queue:extract {jobId}（enqueue=信号，DB 为事实源）
    API-->>U: 201 {document(status=pending)}

    W->>RQ: BRPOP queue:extract
    W->>DB: job.state=running, lease_until=now+5m, doc.status→extracting
    W->>M: get 原件；PDF→逐页转图存 pages/{n}.png
    W->>W: 分类分流 doc_type（化验单/影像报告/病历/其他）
    W->>L: VLM 直读抽取（图片+分类提示词+JSON schema）[经脱敏管线见§6.2, A-4]
    alt VLM 失败/超时
        W->>W: 降级 rule_parser（pypdf 文本+正则）；仍失败→
        W->>DB: doc.status=failed, failure_reason（E-1）
        W-->>U: （前端轮询到 failed）提示 [重试][手动录入] → POST retry → status=pending
    end
    W->>DB: 字典映射（aliases→code，命中率/未命中→dictionary_misses, E-3）
    W->>DB: INSERT derived_records(version=1, payload含字段级confidence+bbox)
    W->>DB: doc.status→awaiting_review, job.state=succeeded

    U->>API: GET /api/documents/{id}/derived-record
    API-->>U: 草稿（低置信<0.85/异常字段高亮，P0 全部必确认）
    U->>API: POST .../confirm {version, decisions:[…]}
    API->>DB: 事务：校验 review_version（乐观锁, E-7）
    API->>DB: INSERT medical_events(occurred_at=报告日期, recorded_at=now)
    API->>DB: INSERT observations(confirmed, source_field_ref 幂等唯一, B-4)
    API->>DB: documents.status→confirmed, review_version+1
    API->>DB: INSERT audit_logs(前值/后值)（B-5）
    API-->>U: {documentStatus:'confirmed', events, observations}
    U->>U: 时间线/趋势即时可见（SQL 确定性聚合）
```

### 5.2 问诊 L0–L2 组装 → 脱敏 → LLM → Trace → 红旗插入（F-09~F-11）

```mermaid
sequenceDiagram
    autonumber
    participant U as Web(SPA)
    participant API as Chat编排(orchestration.py)
    participant DB as PostgreSQL
    participant S as Sanitizer
    participant L as LLM网关
    participant RF as 红旗规则引擎

    U->>API: POST /api/chat/conversations/{id}/messages {content}
    API->>API: 鉴权；校验 conversation.subjectId 归属（A-7 跨档=404）
    API->>DB: 取本 subject 历史消息（只读该 Subject）
    API->>DB: L0 = profiles 直取（年龄/性别/过敏/慢病/用药）
    API->>DB: L1 = 指标字典匹配话题→SQL 聚合序列+变化率（A-6 确定性）
    API->>DB: L2 = 时间窗事件（近30天 + 历史同类）
    API->>RF: 预检（用户消息文本）
    RF-->>API: hits?（命中→red_flag_hits 落库+审计, A-1）
    API->>S: 出域脱敏（姓名→占位符/证件电话→掩码/生日→年龄段, A-4）
    alt 本地模型模式(localModelMode=true)
        S-->>API: 不出域，跳过云端调用路径开关
    end
    API->>L: chat.completions(system=免责+引用格式+依据不足声明, context=L0/L1/L2)
    alt LLM 超时/5xx（E-5）
        L-->>API: error → 重试 1 次 → 仍失败
        API->>DB: INSERT messages(error_state=timeout)【不写 Trace】
        API-->>U: SSE error {message:'回答超时，请重试'}
    end
    L-->>API: 回答草稿
    API->>API: 解析引用标记 ↔ 已提供上下文实体交叉校验（A-3）
    API->>DB: INSERT traces(context_layers) + trace_refs（每引用一行）
    API->>API: 规则引擎在回答顶部独立插入转诊卡片（E-11，不交模型）
    API->>DB: INSERT messages(assistant, red_flag_hit_id?)
    API-->>U: SSE: meta → delta* → citations → [redflag] → done
    U->>API: 点击引用 → GET /api/documents/{id}/content
    API-->>U: 签名 URL（带 expiresAt；过期 403→前端刷新, E-9）
```

### 5.3 对话式添加草稿卡片（F-12）

```mermaid
sequenceDiagram
    autonumber
    participant U as Web(SPA)
    participant API as Chat编排
    participant L as LLM网关
    participant DB as PostgreSQL

    Note over U: 已选 Subject（chip 常驻, AC-2.4）
    U->>API: 发送 "我爸今天说头晕，血压 150/95"（消息绑定 subjectId）
    API->>L: 抽取意图+结构化草稿（JSON schema: chat_draft）
    L-->>API: {event:{type:self_reported,title,narrative,occurred_at:"今天"},
                observations:[{name:"血压", systolic/diastolic 或 value:"150/95"}]}
    API->>API: occurredAt 相对时间解析（"今天"→当日, AC-12.4）；脱敏后调用
    API->>DB: INSERT message_drafts(status=pending)（不入时间线, AC-12.3）
    API-->>U: SSE: draft 事件 → 渲染确认卡片（含 Subject 头像）
    alt 确认入库
        U->>API: POST /api/chat/drafts/{id}/confirm {payload?}
        API->>DB: 事务 INSERT medical_events(recorded_at=now) + observations
        API->>DB: draft.status=confirmed, result ids；audit_logs
        API-->>U: 成功 → 时间线即时可见
    else 编辑后入库
        U->>API: confirm {payload: 编辑值}（以编辑值为准, AC-12.2）
    else 丢弃
        U->>API: POST .../discard → draft.status=discarded（无孤儿数据, AC-12.3）
    end
```

---

## 6. AI Flow 设计

### 6.1 上下文组装器（`chat/context_builder.py`）

分层确定性 + 引用回链；P0 实现 L0/L1/L2/L4，**L3 留接口返回空数组**（P1 启用 pgvector）。

```python
class ContextLayer(str, Enum): L0="L0"; L1="L1"; L2="L2"; L3="L3"; L4="L4"

class AssembledContext:
    layers: dict[str, list]        # 各层条目（已脱敏前的内部形态）
    citation_pool: list[Citation]  # 可引用实体池（obs/event/doc + label）
    token_budget: dict[str, int]   # 各层配额
```

| 层 | 内容 | 获取 | P0 预算（tokens） | 超预算策略 |
|---|---|---|---|---|
| L0 静态 | 年龄段（由 birthday 算）、性别、过敏、慢病、用药中 | `profiles` SQL 直取，每轮必带 | ≤400 | 截断长文本字段 |
| L1 时序 | 话题相关指标近 N 次序列 + 变化率 + 异常标记 | 关键词→`indicator_dictionary.aliases` 匹配 code→**SQL 聚合**（A-6）；未命中话题取 `is_top_trend` 中与关键词交集者；无交集取最近 3 个高频 code | ≤1200 | 每 code 最多 10 点，超出给首尾+极值 |
| L2 事件 | 近 30 天全部 + 历史同类（按 event_type 与话题词） | `medical_events` 按 occurred_at 窗口 + 标题/诊断 ILIKE | ≤1500 | 就近取 8 条，按时间倒序 |
| L3 语义 | 叙述文本片段 | **P0 恒空**（接口返回 `[]`，P1 接 pgvector `embedding` 列 topK） | 0（P1 800） | — |
| L4 证据 | L1–L3 引用实体的 `source_document_id` 回链信息 | ID 去重 → documents 元数据（不传原件内容，仅 label+日期）；必要时二次 VLM 读原件（P0 不启用） | ≤300 | — |

组包顺序（system prompt）：`免责与定位(A-5) → L0 → L1(序列以 markdown 表) → L2 → L4(可引用清单，含 [n] 编号) → L3(P1)`。
引用协议：LLM 只能引用清单内编号，输出形如 `[依据：2026-03-12 血常规报告]`；`citations.py` 将其映射回实体 ID，**清单外引用直接剥除**（A-3）。依据不足（三层皆空或与话题无关）：system 强制输出"依据有限/档案中未找到 XX"声明句（A-2），并用检测用例库回归。

### 6.2 脱敏器规则（`sanitizer/pipeline.py`，A-4）

| # | PII | 识别 | 替换 | 说明 |
|---|---|---|---|---|
| 1 | 证件号 | `\d{17}[\dXx]`（身份证）等 | `***证件号***` | 先于电话匹配 |
| 2 | 电话 | `(?<!\d)1[3-9]\d{9}(?!\d)` | `***电话***` | |
| 3 | 精确生日/日期 of person | `(\d{4})年(\d{1,2})月(\d{1,2})日`/`YYYY-MM-DD` 与上下文（出生/生于）共现；及上下文中出现的 subject.name | 生日→`约{年龄段}岁`（由 Profile 算）；姓名→`{关系}（如"父亲"）`或`档案对象` | 姓名替换需先注入当前 Subject 及同 Account 全部 Subject 名单做精确匹配 |
| 4 | 邮箱 | 邮箱正则 | 掩码 | |
| 5 | 地址 | 省市区级保留，门牌掩码（P0 简化：整串掩码） | | |

- 执行时机：**所有出域 LLM 调用前**（抽取、问诊、对话草稿）；`sanitize(text, context) -> SanitizedResult(text, replaced:[{type, repl}])`，替换记录写入 Trace 脱敏摘要（不含明文）。
- 开关：`PII_MASKING_ENABLED`（默认 true，settings 管理）；`LOCAL_MODEL_MODE=true` 时调用走本地 base_url，仍建议保留脱敏（双保险）。出域请求体抽查测试：`tests/unit/test_sanitizer.py` 断言无明文姓名/证件号（AC-14.3）。
- 日志脱敏：structlog processor 对 `name/id_card/phone` 键统一掩码。

### 6.3 红旗规则表结构与热更新（F-11/A-1）

表结构见 §3.2 第 20/21 项（`red_flag_rules`/`red_flag_hits`）。匹配算法：

```
输入: 用户消息文本 + 上下文数值(L1 中的异常观测)
1) keyword 层: keywords 正则/子串匹配，any/all 按 keyword_logic
2) context_logic 层: 结构化条件树
   示例: {"all":[{"kw":"胸痛"},{"any":[{"kw":"放射"},{"kw":"左臂"},{"kw":"后背"}]}]}
         {"all":[{"kw":"头晕"},{"obs":{"code":"BP_SYS","gte":180}}]}
   obs 条件从 L1 观测序列取确定性数值（A-6，不问模型）
3) 命中→ severity: high 强制转诊卡片 + red_flag_hits + audit_logs(action=redflag.hit)
   medium→ 卡片提示但措辞缓和
4) 插入位置: 回答流顶部，由 orchestration 在 LLM 返回后拼接（E-11：模型拒绝配合也必现）
```

热更新（AC-11.3）：规则加载进进程内存字典，**TTL 60s 轮询 `max(version)`** + 管理端 PATCH 后 `PUBLISH redflag:invalidate`，订阅端收到即重载；不发版生效。

### 6.4 Trace 生成约定（F-10/A-3）

1. 每条成功回答恰好 1 条 `traces`（`UNIQUE(message_id)`）；失败回答无 Trace（E-5）。
2. `trace_refs.ref_index` 与正文引用标记编号一一对应；每个 ref 必须能解析到本 subject 名下实体（service 层校验归属，防跨档）。
3. `context_layers` 记录组装时各层实际条目 ID 与序列摘要，供"查看依据"面板与后续 Eval（K3）。
4. 前端渲染：仅 `ref_index` 在 `trace_refs` 中存在的标记可点击回链；**无 Trace ID 的引用格式禁止渲染**（计数=0 为检测指标）。
5. 引用的 Document 回链走 `/api/documents/{id}/content` 签名 URL（E-9 过期→403→刷新）。

### 6.5 对话式草稿抽取约定（F-12）

- 触发：问诊编排判定用户消息含"记录意图"（先录入/帮记/…或 LLM 意图分类输出），走 `extract_draft` 提示词 + JSON schema；与回答生成可在同一次调用中以结构化输出返回（`{reply, draft}` 双字段）。
- 产出落 `message_drafts.payload`，字段级同样带 `confidence`（数据结构统一）；`occurred_at` 由相对时间解析器换算（今天/上周三/2026-03），失败则 occurred_at=当日并标 precision。
- 确认时 `recorded_at=now`；同一 draft 确认幂等（status 从 pending 转移，二次确认 409）。

### 6.6 可插拔解析器接口（`preprocessing/parser_base.py`）

```python
class ParseInput(TypedDict):
    document_id: str
    doc_type: str                    # 分类分流结果
    pages: list[PageAsset]           # [{page_no, image_bytes|text}]
    language_hint: str               # 'zh-CN'
    llm_client: LLMClient            # 已配置的 OpenAI 兼容客户端（可不可用→抛 LLMUnavailable）

class ParserResult(TypedDict):
    parser_name: str                 # 'vlm' | 'rule' | (P1)'paddleocr' | 'mineru'
    report_date: date | None         # 双时间轴：报告内检查日期
    doc_type: str
    events: list[EventDraft]         # {type,title,narrative,diagnosis,department}
    observations: list[ObsDraft]     # {raw_name, value, unit, ref_low, ref_high,
                                     #  confidence, bbox(page,x,y,w,h), occurred_at}
    source_text: str                 # 全文（L3 embedding 源）
    success: bool
    error: str | None

class BaseParser(Protocol):
    name: str
    supported_doc_types: set[str]
    def available(self) -> bool: ...         # 依赖可用性探测（模型/权重缺失→False）
    def parse(self, inp: ParseInput) -> ParserResult: ...
```

- 注册表：`preprocessing/parsers/__init__.py` 中 `PARSERS: dict[str, BaseParser]` + `select_parser(doc_type, available) -> BaseParser`。
- P0 实现两个：`VlmParser`（默认，首选）与 `RuleParser`（兜底：pypdf 文本 + 正则表格解析，LLM 不可用时仍可产出低置信草稿；再失败→手动录入）。
- P1 新增 `PaddleOcrParser`/`MinerUParser` 只需实现协议并注册，pipeline 不改。
- 解析失败重试：`extraction_jobs.attempt ≤ 1`（E-1），重试优先换 parser（vlm→rule）。

### 6.7 LLM 接入层与降级矩阵

| 场景 | LLM 可用 | 降级行为 |
|---|---|---|
| 文档抽取 | ✅ | 表格直读 → 逐页 VLM → OCR，必要时融合 |
| 文档抽取 | ❌ | 表格/规则解析 → 仍失败 `status=failed` → 人工录入（F-05.4） |
| 指标语义对齐 | ✅ | 归一化 → 模糊 → **AI 判定复用/新建标准指标** |
| 指标语义对齐 | ❌ | 原文保留 + `dictionary_misses` 反馈队列（不阻塞抽取） |
| 问诊回答 | ✅ | 完整 L0–L2 组装+引用 |
| 问诊回答 | ❌ 超时/5xx | 重试 1 次 → SSE error 友好提示，无 Trace（E-5） |
| 对话式草稿 | ❌ | 提示"当前 AI 不可用，请到档案页手动录入"（降级入口常驻） |

### 6.8 指标标准主数据与对齐链路（AI 底座）

```
观测原始名/单位
   │
   ├─(1) 归一化精确  : NFKC → 去标点 → 缩写展开(HGB/Hb) → 等价词(血色素→血红蛋白)
   │                   → 修饰词剥离(总胆固醇测定→总胆固醇) → 命中 name_cn/code/aliases
   │                   实现：preprocessing/indicator_names.py
   │
   ├─(2) 模糊匹配    : 字符相似度 ≥ 0.92 且非易混成对 → 命中
   │                   实现：indicator_names.best_fuzzy_candidates
   │
   ├─(3) AI 语义对齐 : 原始名+单位+候选词表 → 模型判定
   │                   ├ 已有指标 → 复用 code/标准名/标准单位
   │                   └ 新指标   → 生成标准名/code/单位/分类/别名 → 落库
   │                   实现：preprocessing/indicator_aligner.py
   │
   ├─(4) 单位统一    : value/ref_low/ref_high 同步换算到标准单位
   │                   raw_value/raw_unit/unit_conversion 留痕
   │                   实现：preprocessing/units.py
   │
   └─(5) 降级        : AI 不可用/无结论 → 原文保留 + dictionary_misses
```

**安全阀（代码侧，非模型可推翻）**：`mapper._PAIR_TRAITS` 列出成对/系列指标
（收缩压/舒张压、总胆固醇/甘油三酯、MCV/MCH/MCHC 等）。规则与 AI 的合并结论只要
落在该清单内一律拒绝，宁可新建。

**可追溯字段**（写入 `DerivedRecord.payload.observations[i]`）：

| 字段 | 取值 | 用途 |
|---|---|---|
| `match_kind` | `exact` / `fuzzy` / `ai` / `ai_new` | 说明该指标如何对齐 |
| `match_score` | 0~1（fuzzy 时） | 模糊命中置信度 |
| `ai_alignment` | `{action, code, confidence, reason, usedLlm}` | AI 判定依据，供人工复核 |
| `unmapped` | bool | 是否仍为原文保留 |
| `raw_value` / `raw_unit` / `unit_conversion` | 原始值/单位/换算理由 | 审计回溯 |

**并发与成本**：AI 对齐按指标逐个调用，单文档设上限（默认 8 次），超出的走原文保留；
对齐结果按 `code` 复用，同一文档内同名词不会重复调模型。


---

## 7. 任务列表（T1…Tn）

> 格式：**依赖** = 前置任务；**文件** = 预计产出文件（路径相对仓库根）。
> 并行批次：**A（后端）/ B（前端）/ C（部署）三线在 T1 后可并行**；线内按序号执行；跨线依赖已标注。
> 预计总计 ~180 文件；标注为估数。

### 批次 0：骨架与地基（阻塞一切）

| # | 内容 | 依赖 | 文件（估） |
|---|---|---|---|
| **T1** | **仓库骨架**：建 `server/`、`web/`、`deploy/` 目录与占位文件；根 `Makefile`；`deploy/.env.example`（全量 env 草案，§8.5）；`.gitignore` | — | 8：Makefile、.gitignore、server/requirements.txt、server/app/\_\_init\_\_.py、web/package.json、web/tsconfig.json、web/vite.config.ts、deploy/.env.example |
| **T2** | **后端地基**：config(settings.py 全量 env)、db(session/base)、common(errors/responses/logging/pagination/ids)、main.py（middleware：request-id、structlog、全局异常→包裹格式、/healthz 骨架） | T1 | 9：server/app/config/settings.py、db/base.py、db/session.py、common/\_\_init\_\_.py、common/errors.py、common/responses.py、common/logging.py、common/pagination.py、common/ids.py、main.py |
| **T3** | **数据库迁移 0001**：§3.2 全量 DDL（Alembic）+ §3.1 ER 校验；pgvector 扩展 | T2 | 3：alembic.ini、alembic/env.py、alembic/versions/0001_init.py |
| **T4** | **SQLAlchemy 模型层**：db/models/ 全部 11 个文件（与 §3.2 一一对应，含状态 CHECK、唯一约束、pgvector 列） | T3 | 12：db/models/（\_\_init\_\_ + account/subject/document/preprocessing/observation/event/chat/redflag/audit） |

### 批次 A：后端（T4 后线性执行；A2 起部分可互并行）

| # | 内容 | 依赖 | 文件（估） |
|---|---|---|---|
| **A1** | **鉴权 F-01**：注册（建默认 Subject+Profile）、登录（统一 401）、/me、JWT；deps：get_current_account / require_subject / ownership→404 | T4 | 6：auth/security.py、auth/service.py、auth/schemas.py、auth/deps.py、auth/router.py、tests/api/test_auth.py |
| **A2** | **Subject 管理 F-02/F-03**：CRUD、last-selected、级联删除（会话归档/文件软删/审计保留）、stats | A1 | 6：subjects/service.py、subjects/schemas.py、subjects/router.py、audit/service.py（先实现 record()）、audit/router.py（骨架）、tests/api/test_subjects.py |
| **A3** | **审计服务补全**：audit.record() 通用封装（before/after、request_id 注入），接入 A1/A2 写路径；`test_write_requires_subject.py`（AC-3.x/K4 专项） | A2 | 3：audit/service.py（补全）、tests/api/test_write_requires_subject.py、tests/api/test_idor.py |
| **A4** | **文档上传 F-04**：MinIO storage 封装、hash 去重(409)、分类分流、创建 Document+Job、入 Redis 队列、content 签名 URL（过期 403）、pages、retry、archive | A3 | 8：documents/storage.py、documents/schemas.py、documents/service.py、documents/router.py、preprocessing/queue.py、tests/api/test_documents.py、tests/conftest.py、tests/unit/test_*.py（去重） |
| **A5** | **抽取链 F-05**：parser_base 协议、VlmParser、RuleParser、注册表、extractor（JSON schema 校验）、mapper（字典映射+E-3 未命中队列）、pipeline 编排、llm/client、prompts 模板、worker.py 入口 | A4 | 12：preprocessing/parser_base.py、parsers/\_\_init\_\_.py、parsers/vlm_parser.py、parsers/rule_parser.py、preprocessing/extractor.py、preprocessing/mapper.py、preprocessing/pipeline.py、preprocessing/schemas.py、llm/client.py、llm/extraction.py、llm/prompts/（3 md + 2 schema json）、worker.py |
| **A6** | **HITL 确认 F-06**：derived-record 读取、confirm（字段级 decisions、乐观锁 409、幂等唯一、状态机转移、审计前值后值）、reextract | A5 | 4：preprocessing/review.py、tests/unit/test_review_idempotency.py、tests/api/test_review.py、tests/integration/test_state_machine.py |
| **A7** | **时间线+趋势 F-07/F-08**：timeline 事件流（分组/筛选/摘要/空档案）、trends SQL 聚合（参考区间带数据、insufficient）、字典 trend-options | A3（与 A4-A6 并行） | 6：timeline/service.py、timeline/schemas.py、timeline/router.py、observations/trends.py、observations/dictionary.py、observations/router.py（字典+观测部分）、tests/api/test_timeline_trends.py |
| **A8** | **字典与规则种子脚本**：TOP50 指标字典（含 TOP20 is_top_trend 标记、参考区间版本行）、红旗规则初版 | A6/A9 表就绪（T4 即可写，seed 逻辑可先于 A6） | 2：scripts/seed_dictionary.py、scripts/seed_redflag_rules.py |
| **A9** | **红旗引擎 F-11**：匹配（keyword+context_logic+obs 条件）、热更新（轮询+pub/sub）、规则 CRUD/test 接口、hits 审计 | A3（并行） | 6：redflag/engine.py、redflag/loader.py、redflag/schemas.py、redflag/router.py、tests/unit/test_redflag_engine.py、rules 数据（并入 seed） |
| **A10** | **脱敏管线 F-14**：patterns + pipeline + settings 开关 + 日志 processor；接入所有 LLM 出域调用点 | A5（并行于 A6-A9） | 4：sanitizer/patterns.py、sanitizer/pipeline.py、tests/unit/test_sanitizer.py、（settings 增补） |
| **A11** | **问诊 F-09/F-10**：会话/消息 CRUD、context_builder（L0/L1/L2/L4、L3 空接口）、orchestration（脱敏→LLM→重试→Trace→红旗插入）、citations（引用校验+trace_refs）、SSE 流式端点、trace 查询 | A7+A9+A10 | 10：chat/schemas.py、chat/service.py、chat/context_builder.py、chat/citations.py、chat/orchestration.py、chat/router.py、llm/prompts/consult_system.md、tests/unit/test_context_builder.py、tests/api/test_chat.py、（OpenAI 兼容端点并入 router） |
| **A12** | **对话式草稿 F-12**：draft_parser、message_drafts 确认/丢弃端点、时间表达解析 | A11 | 4：chat/draft_parser.py、chat/schemas.py（增补）、tests/api/test_chat_draft.py、（路由并入 chat/router.py） |
| **A13** | **审计/设置查询收尾 F-14**：audit 查询接口（分页/筛选）、settings 读写 | A11 | 3：audit/router.py（补全）、common 或 auth 增 settings 端点、tests/api 覆盖 |

### 批次 B：前端（T1 后即可启动；对 API 的依赖按下列标注等待联调）

| # | 内容 | 依赖 | 文件（估） |
|---|---|---|---|
| **B1** | **前端地基**：main/App 路由树、api/client.ts（axios+拦截器+错误码翻译+requestId 展示）、api/types.ts、stores（auth/subject persist）、styles、AppShell/DisclaimerBar/ErrorState/EmptyGuide/Modal | T1 | 12：main.tsx、App.tsx、styles/\*（2）、api/client.ts、api/types.ts、stores/\*（3）、components/layout/\*（2）、components/common/ErrorState/EmptyGuide/Modal |
| **B2** | **登录页 F-01**（依赖 A1 联调） | B1 | 3：pages/LoginPage.tsx、api/auth.ts、hooks/useRequireAuth.ts |
| **B3** | **Subject 切换器 F-02/F-03**（依赖 A2）：SubjectSwitcher 大 chip+新建弹窗、subjectStore 记忆、useWriteGuard（无选档禁用写按钮 AC-3.1）、成员页 | B2 | 6：components/layout/SubjectSwitcher.tsx、hooks/useWriteGuard.ts、pages/SubjectsPage.tsx、api/subjects.ts、stores/subjectStore.ts（完善） |
| **B4** | **档案页 F-04/F-13**（依赖 A4）：上传拖拽（格式/大小前置校验 E-10）、文档列表（状态徽标/失败重试/手动录入入口）、文档详情（pdfjs 预览+签名 URL 过期刷新 E-9） | B3 | 6：pages/ArchivePage.tsx、DocumentDetailPage.tsx、components/documents/UploadDropzone/DocumentList/DocumentViewer、api/documents.ts |
| **B5** | **确认卡片 F-05/F-06**（依赖 A6）：DerivedRecord 确认 UI（低置信/异常高亮、字典未命中标记、字段级 confirm/correct/reject、版本冲突提示 E-7） | B4 | 4：components/documents/ReviewCard.tsx、ConfidenceBadge.tsx、confirm 提交逻辑、hooks |
| **B6** | **时间线+趋势 F-07/F-08**（依赖 A7）：事件流分组、筛选、空引导、ECharts 折线（参考区间带、insufficient 提示、点击→回看原件） | B3（可早于 B4/B5 并行） | 6：pages/TimelinePage.tsx、components/timeline/\*（3）、api/timeline.ts、api/observations.ts、hooks/useTrendSeries.ts |
| **B7** | **问诊页 F-09/F-10**（依赖 A11）：会话列表、SSE 流渲染（useChatStream）、引用标记可点击回链、Trace 面板、切换 Subject 确认弹窗（E-8）、超时友好提示（E-5）、空档案引导（E-4） | B5/B6 | 9：pages/ChatPage.tsx、ChatConversationPage.tsx、components/chat/ConversationList/MessageBubble/CitationTag/TracePanel、api/chat.ts、hooks/useChatStream.ts |
| **B8** | **红旗卡片 F-11 + 草稿卡片 F-12**（依赖 A9/A12）：RedFlagBanner 顶部强制展示、DraftCard（头像、确认/编辑后入库/丢弃） | B7 | 4：components/chat/RedFlagBanner.tsx、DraftCard.tsx、集成进 MessageBubble、api 补充 |
| **B9** | **设置与审计 F-14**（依赖 A13）：脱敏开关、免责展示、审计列表 | B7 | 4：pages/SettingsPage.tsx、AuditPage.tsx、api/audit.ts、集成 |

### 批次 C：部署（T1 后可启动；与 A/B 并行）

| # | 内容 | 依赖 | 文件（估） |
|---|---|---|---|
| **C1** | **镜像**：server/Dockerfile（多阶段、非 root、含 libmagic/pypdf 依赖）、Dockerfile.worker（同镜像不同 CMD）、web/Dockerfile + nginx.conf（SPA fallback、/api 反代、gzip） | T1（Dockerfile 可先写，联调在 C2） | 4 |
| **C2** | **Compose 一键全栈（验收基线 AC-15.1~15.3）**：6 服务（api/worker/web/postgres/redis/minio）、healthcheck、depends_on service_healthy、重启策略、数据卷、migration 服务（alembic upgrade head）、env 注入 | C1+A3（迁移就绪） | 2：deploy/compose.yaml、deploy/.env.example（定稿） |
| **C3** | **K8s 清单附件（AC-15.4）**：namespace/configmap/secret、3 个 stateful PVC+Deployment+Service（pg/redis/minio）、api（initContainer 等 DB+alembic）、worker、web、kustomization | C2 | 15：deploy/k8s/\*（见 §2 清单） |
| **C4** | **部署手册与冒烟**：deploy/README.md（起停/备份 pg_dump+minio 策略/FAQ）、scripts/smoke_e2e.sh（注册→上传→确认→问诊 四步冒烟） | C2+C3 | 2 |

### 批次 D：联调与 Go/No-Go 评测（全部合并后）

| # | 内容 | 依赖 | 文件（估） |
|---|---|---|---|
| **D1** | 端到端联调：AC-1.x~15.x 逐条过；跨档专项（K4）；出域 PII 抽查（AC-14.3） | A13+B9+C4 | 修 bug（不新增文件） |
| **D2** | K1/K2 评测线：30 张真实样本跑抽取链、准确率/召回率报告脚本 | A6 | 2：scripts/eval_extraction.py、docs/EVAL_REPORT.md（产出物） |
| **D3** | K3 评测：20 个典型问题引用正确率人工评测表 + 自动比对辅助 | A11 | 2：scripts/eval_citations.py、docs/EVAL_REPORT.md（并入） |

**并行性总结**：

- **T1 →（T2…T4 线性）**；T1 完成后 **B（前端）即可全速并行**，C（部署）也可启动 C1。
- T4 完成后 A 线执行；A 线内：**A7/A9/A10 与 A4-A6 并行**（共享 A3 后分叉）。
- C2 需要 A3（迁移+审计骨架即有 alembic）——实际只需 T3 完成即可跑 migration，C2 依赖 T3。
- D 批次在 A/B/C 全部完成后执行。

---

## 8. 共享知识与跨文件约定

### 8.1 API 响应包裹格式（前后端唯一契约）

成功：
```json
{ "code": 0, "message": "ok", "data": { }, "requestId": "01J..." }
```
失败：
```json
{ "code": "SUBJECT_REQUIRED", "message": "写入必须选择档案对象", "data": null, "requestId": "01J..." }
```
- 后端实现：`common/responses.py` 的 `api_ok(data, request)` / `api_err(code, message, http_status)`；FastAPI 路由经统一 `response_model` 或直接 `JSONResponse`。
- 例外：**SSE 流**（chat messages）不包裹，用 OpenAI chunk 形态；`/v1/chat/completions` 用 OpenAI 错误形态；`/healthz`、`/metrics` 裸格式。
- 前端 `api/client.ts` 拦截器解包：`code===0` 返回 `data`，否则抛 `ApiError{code,message,requestId}`，按 code 映射中文提示（映射表集中于 `api/errors.ts`）。

### 8.2 错误码与异常规范

- 统一异常类：`common/errors.py` 中 `AppError(code, message, http_status, detail?)`；各业务抛子类（如 `SubjectRequiredError`）。全局 handler 捕获 → `api_err`；未捕获 → 500 `INTERNAL`（日志含 stack + requestId）。
- 错误码字符串 = §4.4 表；**新增错误码必须同步更新 §4.4 与前端 errors.ts**。
- 状态机非法转移 → `STATE_INVALID`；越权统一 404 `NOT_FOUND`（不区分存在性）。

### 8.3 命名与代码约定

| 项 | 约定 |
|---|---|
| 后端包/模块 | snake_case；路由文件统一 `router.py`，schema 统一 `schemas.py` |
| API 路径 | kebab-case 复数资源，`/api/{domain}/...`；字段 camelCase（Pydantic `alias_generator`） |
| DB | 表名 snake_case 复数（`medical_events`）；列 snake_case；索引 `idx_`/`uq_` 前缀 |
| ID | ULID 文本（`ids.py`：`new_id()`），26 字符，时间有序 |
| TS | camelCase；组件 PascalCase；类型与后端 schemas 一一对应（api/types.ts 单一来源） |
| 前端状态 | 服务端状态一律 TanStack Query；Zustand 仅 auth/subject/ui 三 store |
| 测试命名 | `test_<模块>_<场景>.py`；AC 编号写进 docstring |

### 8.4 日志与可观测

- structlog JSON 单行：`{"ts","level","event","requestId","accountId","subjectId","method","path","status","latencyMs",...}`。
- 中间件生成 `requestId`（ULID）→ 放响应头 `X-Request-Id` 并注入 audit_logs.request_id。
- 关键事件（必打）：`auth.login`、`document.uploaded`、`extraction.done/failed`、`record.confirmed`、`chat.message`、`llm.call`（含 latency/tokens/重试）、`redflag.hit`。
- 审计日志（业务）与运行日志分离：audit_logs 表 = 业务审计（用户可见）；stdout JSON = 运维日志。

### 8.5 环境变量清单（`.env.example` 全量；镜像零硬编码）

| 变量 | 示例 | 说明 |
|---|---|---|
| `APP_ENV` | `prod` | prod/dev/test |
| `DATABASE_URL` | `postgresql+psycopg://aiuser:aipass@postgres:5432/aidoctor` | |
| `REDIS_URL` | `redis://redis:6379/0` | |
| `MINIO_ENDPOINT` / `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` / `MINIO_BUCKET` / `MINIO_SECURE` | `minio:9000` / … / `medical-docs` / `false` | |
| `JWT_SECRET` | （必填，≥32 字节随机） | |
| `JWT_ALG` / `JWT_EXPIRE_MINUTES` | `HS256` / `10080` | 默认 7 天 |
| `LLM_BASE_URL` | `https://api.openai.com/v1` 或 `http://newapi:3000/v1` / Ollama | OpenAI 兼容 |
| `LLM_API_KEY` | `sk-...` | 仅 env，不入库不下发 |
| `LLM_MODEL` / `LLM_VLM_MODEL` | `gpt-4o-mini` / `gpt-4o` | 回答模型 / 抽取模型 |
| `LLM_TIMEOUT_SECONDS` / `LLM_MAX_RETRIES` | `60` / `1` | E-5 |
| `LOCAL_MODEL_MODE` | `false` | true=本地 base_url，出域策略切换 |
| `PII_MASKING_ENABLED` | `true` | A-4 |
| `HITL_CONFIDENCE_THRESHOLD` | `0.85` | P0 仅用于**高亮**（全部强制确认），P1 自动入库开关 |
| `AUTO_COMMIT_ENABLED` | `false` | P1 留缝，P0 恒 false |
| `MAX_UPLOAD_MB` | `20` | E-10 |
| `EXTRACT_MAX_ATTEMPTS` | `2` | 含首次（E-1 重试 1 次） |
| `QUEUE_EXTRACT` | `queue:extract` | |
| `SIGN_URL_EXPIRE_SECONDS` | `600` | 原件链接时效（E-9） |
| `REDFLAG_RELOAD_TTL_SECONDS` | `60` | 热更新 |
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `json` | |
| `CORS_ORIGINS` | `http://localhost:5173` | dev；prod 同源由 nginx 收敛 |
| `RATE_LIMIT_CHAT_PER_MIN` | `20` | Redis 限流 |
| `WEB_PORT` / `API_PORT` 等 compose 端口 | `8080` / `8000` | |

前端构建期 env：`VITE_API_BASE=/api`（仅 base 路径，**不含任何密钥**）。

### 8.6 前后端共享契约核对清单

- §4 表即契约：新增/修改接口必须先改本文档再实现（变更需主理人确认）。
- 时间：后端 UTC ISO8601（`2026-07-19T12:00:00Z`）、日期 `YYYY-MM-DD`；前端 dayjs 转本地。
- 金额外数值：`NUMERIC` 序列化为 string（避免 JS 精度问题），前端 chart 前 `Number()`。
- 分页信封、错误信封、SSE 事件名（meta/delta/citations/redflag/draft/done/error）以 §4.1/§5.2 为准。

---

## 9. 待明确事项

| # | 问题 | 影响 | 需谁决策 | 建议默认值 |
|---|---|---|---|---|
| Q-1 | `pgvector` embedding 维度：P0 列已建 `VECTOR(1024)`，P1 选用模型（bge-m3=1024 / text-embedding-3=1536/3072）若不同需迁移改维度 | P1 L3 层迁移成本（仅 ALTER 列，不丢业务数据） | 主理人（P1 前定即可） | 维持 `VECTOR(1024)`（bge-m3 中文效果好、本地可跑） |
| Q-2 | 设置页"脱敏开关"是否允许用户**关闭**（即明文出域）？涉及 A-4 与 AC-14.2/14.3 检测口径 | F-14 实现与测试口径 | 主理人 | P0 只允许"本地模型模式"整体切换（不出域），**云端模式下脱敏开关只读强制开** |
| Q-3 | JWT 载体：HttpOnly Cookie 还是 localStorage（PRD §7.1 留待安全评审）；影响 XSS/CSRF 防护与 nginx 配置 | F-01、B2、C1 | 主理人+安全评审 | P0 用 **localStorage + 短期 access token（7d）+ /auth/refresh**，同源部署（nginx）降 CSRF 风险；评审后可切 Cookie |
| Q-4 | 红旗规则初版词表与 20MB/TOP20 清单的医学定稿（PRD Q-5）何时到位 | A8/A9 种子数据、K2/K3 评测 | 医学顾问/主理人 | 产品/架构先出草案（seed 文件），医学顾问评审后改 seed 重跑 |
| Q-5 | SSE 是否需要兼容不支持 EventSource 的旧浏览器（IE/内网环境）；影响 chat 流式实现（SSE vs fetch-stream） | A11、B7 | 客户部署环境 | 按现代浏览器 SSE（fetch + ReadableStream 解析）实现；不支持时降级整段返回 |

---

*本文档为 P0 架构基线，变更需主理人确认；接口表（§4）与任务列表（§7）为工程师执行依据，发现冲突以 `docs/PRD.md` 为准并回报架构师修订。*
