# 长期健康档案与主动取证式问诊实施计划

> 日期：2026-10-26 | 版本：V1.0 | 状态：Draft，待评审后实施
> 设计依据：`docs/superpowers/specs/2026-10-26-health-record-intelligence.md`
> 工程约束：`AGENTS.md`；当前状态：`PLAN.md` 顶部
> 范围：本计划是下一阶段路线图，不表示列出的功能已存在。

## 1. 目标、架构与执行约束

### 1.1 目标

在不破坏现有上传、HITL、趋势、问诊引用和部署的前提下，交付：多类型长期档案、版本化 Markdown、统一混合检索、可主动查档/读原件/查医学证据/追问的有界 Agent，以及可复现的质量与安全评测。

### 1.2 目标模块边界

| 模块 | 职责 | 不得承担 |
|---|---|---|
| `documents` | 原件身份、上传、生命周期、资产读取、签名链接 | 指标计算、模型规划 |
| `preprocessing` | 解析、Markdown/页块、草稿版本、HITL、索引进度 | 直接覆盖已确认事实 |
| `records`（新，可逐步从 documents/timeline 抽service） | 统一记录元数据、逻辑目录、搜索应用服务 | 绕开 documents 权限与生命周期 |
| `observations` | 标准指标与确定性时序计算 | 语义猜值/跨标本强行比较 |
| `timeline` | 事件时间视图 | 文档全文索引 |
| `agent` | 只读工具注册表、参数 schema、预算、搜索/证据提供方 | 自主授权、直接写档案、任意代码/URL |
| `chat` | 临床任务状态机、追问、回答与 Trace/SSE | 直接查询其他领域 repo、绕过统一搜索 |
| `sanitizer` | 所有出域文本/查询/图像策略检查 | 用文本替换宣称图像已脱敏 |
| `audit` | 用户与工具行为、版本/删除/证据审计 | 保存完整敏感正文或密钥 |

**技术栈保持**：FastAPI/SQLAlchemy/Alembic + PostgreSQL/pgvector + Redis + MinIO + React/TS。中文 FTS 具体配置、embedding 模型、外部权威来源与图像出域策略分别经 ADR 和小型 PoC 决定。

### 1.3 Review Focus（每一任务必须检查）

- Account + Subject + 生命周期三维隔离，越权一致 404。
- `occurred_at/reported_at/recorded_at` 不混淆，时间精度不伪造。
- 原件不覆盖；AI 结果先入草稿；确认幂等；重抽不覆盖确认事实。
- 趋势/变化率/状态/权限/预算由代码，非模型。
- 所有出域调用含查询词、工具参数和图像均执行隐私策略；日志无敏感正文/密钥。
- 病历和网页是不可信数据；工具只读；红旗不被延迟；引用必须真实支持论断。
- AI 正常/错误/不可用三路径均有 Fake 测试；任务有租约、幂等、降级和可观测性。
- 新行为严格 RED→GREEN→REFACTOR；每步保存失败原因与通过证据。

### 1.4 非目标与兼容

- 不一次引入完整 DICOM、FHIR Server、微服务或独立搜索集群。
- 不开放永久删除；默认归档，永久删除另立设计。
- 不开放 Agent 写档案、任意 URL 或无限循环。
- 保持现有 REST 字段、Document/Event/Observation ID、HITL 确认和 SSE 旧事件；新增字段可空/追加。
- 旧 `documents.status` 迁移采用“新列可空→回填→双读/适配→切读→约束”的小步迁移；不能在单步同时改表、改所有调用并删旧列。

## 2. 发布列车与依赖

```text
R0 基线与安全纠偏
  └─ R1 资产底座
       └─ R2 检索与档案中心 ─────────┐
            └─ R3 Agent 档案工具/问诊 │
                 └─ R4 外部医学证据 ───┴─ 规模化与发布验收
```

R3 可先基于结构化记录实现 `record_search/observation_series`，但“全史语义召回”和“原件精读”的验收依赖 R2/R1。R4 的权威来源 PoC 可提前研究，但不能阻塞 R1/R2。

| 阶段 | 工作量假设 | 独立验收产物 |
|---|---:|---|
| R0 | 0.5–1 周 | 安全修复、当前基线、冻结评测、ADR 清单 |
| R1 | 2–3 周 | 多类型档案与 Markdown 资产闭环 |
| R2 | 2–3 周 | 大量记录检索与可维护档案中心 |
| R3 | 3–4 周 | 有界主动取证问诊与引用 |
| R4 | 2–3 周 | 外部证据、性能/恢复、真实留出集验收 |

每个 PR 只完成一个可测试切片；按任务顺序提交测试证据到当次 PR/`PLAN.md` 当前里程碑，不复制成长篇历史日志。

## 3. R0 — 基线与安全纠偏（T001–T006）

### T001 建立当前事实仪表盘与文档优先级

- **文件**：`PLAN.md`、`docs/PRD.md`、`docs/ARCHITECTURE.md`、本设计/计划。
- 将当前测试/KPI/待办集中到 PLAN 顶部，历史记录保留但不作为当前状态；PRD 管 AC，ARCHITECTURE 管规范架构，设计/ADR 管新决策。
- 校验：文档链接存在；“已实现/目标/待验证”无混写；K1/K2 标清 38 份/37 异常与未重测日期。
- 完成定义：维护者只读 PLAN 顶部即可获得真实当前状态，不会被旧“待样本/全部完成”误导。

### T002 冻结评测口径并建立独立留出集

- **文件**：`evals/README.md`、`evals/dataset/manifest.schema.json`（新）、`evals/scripts/check_annotations.py`、`evals/scripts/score_kpi.py`。
- manifest 记录 `dataset_version, annotation_version, scorer_version, prediction_version, model_profile, sample_counts, split`；K3 不足 20 个且有人工 verdict 时不得给出通过率。
- K1/K2 除 micro 指标外增加预测精确率/误增、按文档 macro、按医院/类型/定性定量分层；固定评分器后重跑历史预测分离系统修复与口径变化。
- **先写失败测试**：少量 verdict、缺失 manifest、误增多余字段、跨类型分层计数；确认失败正确。
- **验收**：`python evals/scripts/score_kpi.py selftest` 通过；现有 K1/K2 可复现；留出集只用于最终复验，禁止用于调参。

### T003 修复软删/归档读取与后台再处理

- **代码**：`server/app/documents/{service,router,storage}.py`、`server/app/preprocessing/{pipeline,review}.py`、`server/app/worker.py`。
- 假设根因：`select_owned` 与任务查询只看所有权/状态，未统一过滤 `deleted_at`；先以当前代码复现，不凭审计结论直接改。
- **RED**：`server/tests/api/test_document_lifecycle.py`（新）覆盖软删后详情/derived/content/pages/retry/reextract；`test_job_recovery.py` 覆盖软删任务不恢复；`test_extraction_pipeline.py` 覆盖 job 开始时已删除则取消且不产生新草稿。
- **GREEN**：所有读路径与任务路径统一使用生命周期 scope；已删除任务转 `cancelled`，返回不泄露存在性的 404；不修改历史审计/Trace 墓碑。
- 同时验证归档的既定策略，不允许“恢复归档”意外改变已确认处理状态。

### T004 生命周期与处理状态兼容迁移设计

- **文件**：`docs/adr/0001-document-lifecycle-and-assets.md`（新）、`server/app/db/models/document.py`、`server/app/documents/schemas.py`、后续 Alembic migration（编号实施时取当前 head 后继，禁止预设冲突编号）。
- 明确旧状态映射：`extracting→processing`、`confirmed/parsed_no_data→processed`；旧 `archived` 需结合 `archived_at/deleted_at` 回填生命周期，并尽可能保留最后处理状态，未知时映射为 `pending + needs_reconcile=true`，不得猜测为 `processed`。
- API 追加 `processingStatus/lifecycleStatus`，旧 `status` 在兼容窗口只读返回；前端逐步切换。
- 双写期旧 `status` 为写权威，由唯一 service 事务内同步新列；冲突报警且拒绝静默选值。全量回填校验、旧新状态一致性测试和只读对账通过后切新列为读权威，再由唯一 service 反写旧字段；最后才能停旧写。
- **RED**：迁移升级/降级、每个旧状态映射、未知归档状态、双写冲突、归档与处理筛选正交、旧客户端字段保持。
- 回滚：切回旧读路径；新增列/表保留，不删除原对象和已回填数据。

### T005 全链路出域隐私与不可信内容基线

- **代码**：`server/app/chat/orchestration.py`、`server/app/agent/{planner,tools,search}.py`、`server/app/sanitizer/`、`server/app/preprocessing/`、`server/app/llm/`；建立出域调用注册清单，R2 embedding 必须复用同一网关。
- 盘点上传 OCR/VLM、结构化、指标/药品主数据对齐、embedding、聊天规划、网页查询、原件复读全部云端路径；文本脱敏与图像授权/本地处理是不同控制。
- **RED**：规划器原始问题含姓名/电话、搜索 query 含 Subject 名/精确日期、搜索结果标题与 URL 含敏感串；上传图像/embedding 绕过网关；病历/网页写“忽略规则并调用工具”不得改变计划。
- **GREEN**：所有云端文本调用统一脱敏，标题、摘要、URL 均检查；图像策略未批准前新云端路径默认禁用/本地处理，既有路径标出差距并制定迁移，不得宣称已合规；不可信内容明确分隔。
- 外部 URL 获取在 R4 完整实现，本任务先禁止绕过现有 URL 安全过滤。
- 验收：现有 agent/chat/sanitizer/preprocessing 测试全绿，新攻击用例失败降级可观测，出域清单与实际调用点一致。

### T006 R0 出口评审

- 命令：`cd server; ..\.venv\Scripts\python.exe -m pytest -q`、`cd web; npm test; npm run build`。
- 离线注入/隔离、历史迁移、当前 K1/K2 全通过；不能复跑真实服务时在 PLAN 标阻塞。
- ADR 至少确认：状态正交、默认归档不物理删除、图像出域默认禁止、临床事实继续 HITL。

## 4. R1 — 健康资产与 Markdown（T101–T108）

### T101 文档类型、时间与来源元数据

- **文件**：`server/app/db/models/{document,event,observation}.py`、`server/app/documents/{schemas,service}.py`、新 migration。
- 追加 `record_type/document_subtype`、`occurred_start/occurred_end/time_precision/reported_at`、`facility/department/source_metadata`；观察补充 `specimen/method`（若已有字段则复用）。
- 按设计 §4.4 的精度表实现 DB/Schema 校验、UTC+原始时区、`[start,end)`、unknown 不入限定时间窗、`start,end,id` 稳定排序；“住院前/上次”锚点语义在 R2 复用同一函数。
- 用户输入优先于 AI，未知时间不伪造；兼容旧 docType。
- **RED**：每种精度的必填/空值/区间、时区跨日、月/年边界、文档与事件不同日期、用户日期不被重抽覆盖、不同标本不被趋势混并。
- 验收：API/迁移/时间筛选和趋势测试通过，历史行可升级。

### T102 `DocumentAsset` 与资产存储服务

- **新文件**：`server/app/db/models/document_asset.py`、`server/app/documents/assets.py`、`server/tests/unit/test_document_assets.py`。
- 建议接口：
  ```python
  put_derivative(document_id, role, content, version, generator, source_hash) -> Asset
  get_asset(document_id, role, version=None, *, subject_id, account_id) -> AssetContent
  list_assets(document_id, *, subject_id, account_id) -> list[AssetMetadata]
  ```
  具体类型以项目 Pydantic/SQLAlchemy 风格定义；服务函数不接收未经路由鉴权的任意 Subject。
- `original` 仅可创建不可覆盖；同 `document_id/role/version` 唯一；sha256 校验；读取沿用归属和生命周期。
- 现有 `file_key` 双读，不移动原件；`source_text` 不自动标 Markdown。
- **RED**：原件覆盖拒绝、重复写幂等、篡改 hash 拒绝、跨档/归档读取、源变化生成新版。
- 验收：资产下载签名有效；对象失败不产生“成功”DB 状态。

### T103 页块与来源锚点

- **新文件**：`server/app/db/models/document_block.py`（或 page 表扩展，由 ADR 决定）、`server/app/preprocessing/anchors.py`。
- 页块字段：`document_id,asset_id,asset_version,extraction_version,page_no,block_id,text,bbox,coordinate_system,parser_version`；锚点绑定不可变资产/抽取版本，唯一键至少含版本与 `(document_id,page_no,block_id)`，坐标允许空但需 `anchor_status`。
- 适配现有表格/OCR/VLM 输出；无可靠 bbox 时只保证页级，不生成伪坐标。`EvidenceRef` 固定同一资产版本，重抽不得改变旧引用的解释。
- **RED**：重抽幂等、不同版本不串锚、跨页表头、无文本层、页图重建失败、坐标缩放、旧 EvidenceRef 仍定位旧版。
- 验收：任一结构化草稿项可定位文档、版本与页；已有 bbox 时可回链区域。

### T104 类型化 Markdown 生成器

- **新文件**：`server/app/preprocessing/markdown/{service,schema,renderer}.py` 及 `templates/`；扩展现有 `pipeline.py`。
- `render(document, pages, extraction) -> MarkdownArtifact`；稳定标题、表格、页锚、front matter；文本与 AI 解释分栏。
- 每类模板：检验/检查、病历/出院、体检、影像/心电、用药、未知；影像只保留报告结论与原件引用。
- 保存 MinIO Markdown 资产，数据库只存元数据；大文档流式/分片写，避免超大 JSON 一次性入模。
- **RED**：表格列序、特殊字符、多页、空文本、OCR 不确定、源摘要与原文混淆、重抽版本、前端 XSS 字符串。
- 验收：TXT/MD 原样可读、PDF/图片副本可预览下载；schema 无内容时失败而非空白“已处理”。

### T105 文本文件与批量导入

- **代码**：`documents/router.py`、`preprocessing/pipeline.py`、现有 `web/src/pages/ArchivePage.tsx`；可新增 `web/src/components/documents/UploadDropzone.tsx` 抽离上传逻辑（当前该组件不存在）。
- 支持 UTF-8/常见中文编码的 TXT/MD（检测失败要求用户选择），JPG/PNG/PDF 保持；魔数、扩展、大小/页数、批量每文件错误校验。
- 批量请求逐项返回 documentId，单项失败不回滚已成功原件；限制每批数量/总字节。
- **RED**：伪装 MIME、空文件、过大/页数超限、部分失败、重复 hash、批量 Subject 必须一致。
- 验收：支持格式在 UI 与 API 一致；任务公平性与限流测试。

### T106 重抽差异与确认版本不覆盖

- **代码**：`preprocessing/{pipeline,review}.py`、`db/models/preprocessing.py`。
- 新草稿关联 `based_on_revision/source_asset_version`；确认后保留修订来源与 before/after。重抽只生成草稿，不更新已确认 Observation/Event。
- **RED**：并发重抽/确认、重复确认、已确认后重抽、旧版本签名链接、字段拒绝、删除旧草稿不影响确认版。
- 验收：现有幂等与版本保留测试不回归；前后差异可逐项接受。

### T107 文档详情四区与处理中心

- **代码**：现有 `web/src/pages/{ArchivePage,DocumentDetailPage}.tsx`、`web/src/api/documents.ts`；新增 `web/src/components/documents/` 目录承载资产与版本组件（不是现有目录）。
- 增加原件/Markdown/结构化/版本历史；独立状态和生命周期徽标；失败重试、取消、人工编辑、归档/恢复、索引状态。归档界面明确“从默认视图与检索隐藏，不移除原件”，若评审要求物理删除则另立项，不能以归档宣称永久删除已完成。
- Markdown 继续采用 token→React 白名单，禁止原始 HTML；原件引用定位页/区域。
- **RED**：状态组合、大文件分页、签名过期、XSS、版本切换、无草稿、失败/索引中不显示可完整使用。
- 验收：桌面/移动浏览器完成上传→副本→确认→版本回看→归档恢复。

### T108 标准主数据完整性与 R1 出口对账

- **代码**：现有 `preprocessing/{mapper,indicator_aligner,indicator_names,units}.py`，按类型新增机构/科室/药品主数据 service/model，不在 chat 内另建一套名称。
- 指标继续标准名/单位/别名、AI 对齐留痕和禁止合并；药品区分通用名/成分/剂型/规格/商品名；机构/科室保留历史原文和别名。
- **RED**：AI 正确/错误/不可用，收缩压/舒张压、MCV/MCH/MCHC、不同标本同名、药品不同剂型/规格不得错并；每次对齐 `match_kind/ai_alignment/model/prompt_version` 可追溯。
- 标准主数据未通过上述测试时阻断 R1，不能用“有字段”替代“同一实体不重复、不合并错误”。
- 提供只读对账脚本/管理诊断：DB 原件与 MinIO 存在性/hash 抽查、派生物源版本、无归属资产、草稿与确认计数。
- 恢复演练：备份 DB 与对象后到隔离环境，抽 20 份不同年代/类型记录原件与链接一致。
- 完整测试 + Compose `build/up --force-recreate` + smoke；K1/K2 固定评分器重跑。
- 不声称“全部类型准确”：必须按类型报告覆盖与人工复核结果。

## 5. R2 — 统一检索与长期档案中心（T201–T208）

### T201 检索契约与中文召回 PoC

- **新文件**：`server/app/records/{schemas,service}.py`、`docs/adr/0002-hybrid-record-search.md`、`evals/dataset/record_search/`。
- `SearchQuery` 包含 Subject、query、类型/时间/问题/code、lifecycle、cursor/limit；`SearchHit` 包含文档/事件/页锚、发生时间、snippet、scoreBreakdown。
- 用中文合成病历比较 PostgreSQL 可用 FTS/应用 token 化/trigram，记录准确率/索引大小/延迟；不凭感觉选分词。
- **RED**：服务禁止跨档、无 limit、无稳定 cursor、返回失效文档。
- PoC 出口：确定分词和索引 DDL，金标基础 Recall@20 可测。

### T202 页/章节全文索引与 outbox

- **新文件**：`server/app/db/models/{record_search_document,index_outbox}.py`、`server/app/records/indexer.py`。
- 业务事务写 outbox；索引记录 `subject_id,document_id,asset_version,page/block,visibility,content_tsv/embedding`；同版本幂等更新。
- **RED**：worker 崩溃、重复事件、乱序归档/索引、旧版本晚到、归档移除、重建索引、草稿与确认可见性。
- 验收：DB↔索引对账最终一致；失败可重试；索引中 UI 诚实可见。

### T203 pgvector 语义候选与嵌入版本

- **文件**：`records/{embeddings,indexer}.py`、`llm` 配置注册、ADR 0002。
- 先固定模型/维度，解决现有 `VECTOR(1024)` 兼容；不混写不同维度；向量只用于叙述候选，不用于数值计算。
- 索引按事件/章节/页块适度切分，保留 Subject 与时间；未确认草稿默认不进常规问诊索引，用户显式查看时也标未确认且不能作为高风险论断唯一依据。
- 云端 embedding 统一经过 R0 出域网关；失败不得绕过直接发送正文。
- **RED**：模型不可用、空文本、维度错误、归档删除、跨档、同文档过度占 topK、未确认内容误作临床事实、embedding 隐私绕过。
- 验收：离线索引可重建；模型/提示/维度版本可追溯；失败退回 SQL+FTS。

### T204 混合召回、时间解析与排序

- **文件**：`records/{query_time,rank,service}.py`。
- 结构化+FTS+向量多路，RRF/等价融合；时间范围和任务上下文由显式参数传入，不把“旧”固定降权。
- 支持近三年/上次/某次住院前后；解析不了返回需澄清状态；同文档多样性限制。
- **RED**：跨年、时区、月精度、unknown 过滤、旧慢性关键史、急性干扰旧异常、标本/单位、同义术语、分页不重不漏。
- 验收：独立检索集必要证据 Recall@20≥95%、候选 Precision@20≥90%（分母为返回的前 20 个候选，由标注者判定是否包含回答该问题所需证据），并报告分层结果；最终 Agent 采用证据精确率在 T306/T308 另行验收。延迟目标在 T207 实测。

### T205 档案统一查询 API

- **文件**：`records/router.py`、现有 `documents/router.py` 兼容适配。
- `GET /api/records/search`、`GET /api/records`、`GET /api/records/facets`、`GET /api/documents/{id}/assets/{role}`、`POST /api/documents/{id}/restore`。
- 使用游标分页与稳定排序；筛选计数不泄露其他 Subject；全文 snippet 安全截断。
- **RED**：每 API 的 IDOR、归档、状态组合、未知时间、游标篡改、limit/超时、索引不可用降级。
- 验收：旧 `/api/documents` 客户端不回归；所有新搜索统一走 service。

### T206 档案中心与逻辑目录

- **文件**：`web/src/pages/ArchivePage.tsx`、`web/src/components/records/`、`web/src/api/records.ts`。
- 全局搜索、保存筛选、年/月/类型/机构/问题导航、批量任务、游标/虚拟列表、搜索命中页定位。
- **RED**：1000+ 行不一次渲染、筛选与分页一致、切换 Subject 清缓存/游标、索引中状态、空结果/失败。
- 验收：浏览器跨档切换无旧结果闪现；长列表、移动端与键盘可用。

### T207 容量与性能验证

- **新文件**：`evals/scripts/generate_synthetic_archive.py`、`evals/scripts/benchmark_record_search.py`（合成数据，不含真实 PII）。
- 1,000 文档基线、10,000 文档预演；同时记录总页数、总字节、图像占比、索引体积、并发、P50/P95/P99、后台吞吐、内存、慢 SQL，而非只测均值或文档数。
- 初始验收预算（PoC 后可经 ADR 调整）：元数据筛选 P95<500ms，混合检索 P95<1.5s（不含 LLM 查询扩展），索引积压可恢复；须注明硬件与数据分布。
- 不达标先优化索引/查询/分页，未证明必要前不引入新搜索引擎。

### T208 R2 出口

- 隔离、注入、生命周期、重建索引、分页、性能和检索金标全通过；前端全测/build、后端全测。
- 保存可复现 benchmark 和数据版本；未达召回门槛不得进入“智能全史问诊”宣传或 R3 最终验收。

## 6. R3 — 主动取证式问诊（T301–T309）

### T301 只读工具注册表与统一执行上下文

- **文件**：`agent/{registry,context,tools}.py`、`chat` 调用侧。
- `ToolContext(account_id,subject_id,conversation_id,budget,request_id)` 由服务端创建；工具参数不含可切换 Subject 的字段。
- 注册 name、JSON Schema、handler、timeout、结果上限、是否出域；统一 ToolOutcome 包含证据 ID、错误与版本。
- **RED**：模型伪造 Subject/工具名/参数、跨档 ID、超 limit、超时、并发重复、只读写操作拒绝、取消与预算耗尽。
- 验收：现有 `web_search` 适配后行为不回归，所有调用可审计且无敏感参数日志。

### T302 档案与指标工具

- **文件**：`agent/tools/records.py`、`agent/tools/observations.py`（或按现有结构单文件）。
- `record_search` 调 R2 service；`observation_series` 调 SQL 趋势，返回完整序列或标注抽稀/首末极值，并保留每个来源 ID。
- **RED**：急性默认窗口/慢病全史由调用策略控制、SQL 数值与模型文本解耦、单位/标本不可比、索引失败、空档案。
- 验收：工具结果只含本 Subject；旧上下文构建器可作为兼容降级而非并行另一套召回。

### T303 原件文本与图像复读工具

- **文件**：`agent/tools/documents.py`、`llm` 视觉调用、`documents/assets.py`。
- `document_read` 按页/章节返回资产版本与锚点；`image_read` 返回 AI 复读观察和局限，永不覆盖原报告。
- 默认禁用云端原图；只有 ADR 配置满足隐私策略才启用。本地/云端路径均限页数、分辨率、总时长。
- **RED**：跨档、无效页/坐标、 archived、版本改变、VLM 错误/超时、未授权云端图像、把复读结论当原文。
- 验收：引用定位到所读页；用户可区分“原报告结论”和“AI 复读”。

### T304 临床任务建模与有界状态机

- **文件**：`chat/{task_model,agent_orchestration,evidence_assessment}.py`、`llm/prompts/`。
- 状态 `triage→plan→retrieve→assess→clarify|read_original|respond→verify`；最多 2 次取证循环，并受总工具数/时间/token/费用预算。
- **只有非危险分支进入常规建模/检索**。危险时先独立提交就医/急救提示，再可选生成简短安全补充；不得并行等待常规工具后才显示提示。
- LLM 输出严格 schema：任务类型、所需证据、时间窗、缺失信息、下一步；规则可确定的排危/ID/状态不交模型。
- **RED**：坏 JSON、无限重复搜索、工具无新证据、预算超时、模型不可用、缓存旧答案；用永久阻塞 Fake 工具/模型证明红旗事件先于任何外部工具发出。
- 验收：任一状态可降级到安全回答/追问，无卡死、无无界等待；记录本地危险提示实测延迟；现有 SSE delta/citations 兼容。

### T305 急慢病与按证据追问策略

- **文件**：`chat/policies/`、`llm/prompts/consult_system.md`、`evals/dataset/clinical_strategy/`。
- 将设计 §7.2 场景策略实现为可版本化配置/提示与代码限制；不硬编码唯一诊断决策树。
- 追问最多 3 个且高信息量；足够时有倾向性判断、主要依据、验证/下一步和升级阈值；禁止无差别长篇罗列。
- R3 仅验收“当前/历史用药记录理解与待补证据”，**不验收具体用药剂量、禁忌、相互作用或治疗选择建议**；这些高风险论断须等待 R4 可靠全文证据工具。
- **RED**：信息不足/充分、用户已回答、危险不等待、急性旧异常干扰、慢病历史缺失、用药当前/历史混淆、R3 无可靠原文时擅自给高风险用药指导。
- 验收：医学顾问标注首选追问命中≥85%（分母为标注“信息不足且应追问”的用例；信息充分集不得为了追问率乱问）；对“敢判断”和“无依据乱判断”分别评分，不能只看语言风格。

### T306 证据池、稳定引用与论断校验

- **文件**：`chat/{citations,evidence}.py`、`db/models/chat.py`、migration。
- 使用服务端证据 ID 替代标签前缀猜配；Trace 保存工具行动、候选与采用证据、资产版本、页/段锚和降级原因，不保存隐藏思维链。
- 医学关键论断生成结构化 claim→evidence 对应，服务端校验存在/归属/版本，支持性离线评测后再决定自动拦截阈值。
- **RED**：伪造 ID、跨档、无原件、旧版本、相似标签、引用不支持、流式标记截断。
- 验收：档案证据 100% 可回链；最终采用证据精确率≥90%（分母为回答实际采用的档案证据条目，由评审判定是否与对应论断相关且内容相符）；引用精确率与关键论断支持率达到设计门槛后才宣称完成。

### T307 追问与工具过程 Web/SSE

- **文件**：`web/src/pages/ChatPage.tsx`、`web/src/components/chat/`、`web/src/types/api.ts`。
- 追加兼容事件 `tool/evidence/clarification`，保留旧事件；前端展示档案时间范围、页精读、证据面板和 1–3 问卡片。
- 不展示完整敏感搜索词或隐藏推理，只展示行动摘要与可审计依据。
- **RED**：乱序/未知事件、断流、重复 done、追问回答、跨 Subject 会话缓存、工具失败提示、红旗优先。
- 验收：现有 120 项基线以最新实际为准全绿；浏览器急/慢/追问/读原件四场景。

### T308 临床策略与安全评测门

- **新文件**：`evals/scripts/score_clinical_strategy.py`、`evals/dataset/clinical_strategy/`、相关 unit/integration tests。
- FakeLLM/FakeSearch 固定输出覆盖正确/错误/不可用；真实模型另跑冻结集，不混为单元测试。
- 指标：必要证据召回、急性干扰、追问命中、判断校准、红旗零延误、引用支持、工具预算、延迟与 token。
- 红线：跨档、伪造引用、急症延误、未经策略图像出域任一失败即 No-Go。

### T309 R3 出口

- 端到端跑通“上传/确认→索引→急性问诊→慢病全史→原件复读→追问→引用回链”。
- 结果按模型、检索、提示词、数据版本记录；人工医学评审与工程测试分开签署。
- 允许通过功能开关回退旧编排，但不得用旧编排的通过结果宣称新 Agent 达标。

## 7. R4 — 外部证据、规模与发布（T401–T407）

### T401 权威来源与全文获取 PoC

- **文件**：`docs/adr/0003-external-medical-evidence.md`、`agent/evidence/`。
- 由医学顾问确定首发白名单、适用地区/语言、许可与更新方式；比较搜索摘要与全文对关键论断支持率。
- **RED**：日期缺失、旧指南、适用人群不符、摘要与全文冲突、来源不可达；无法全文时必须降级标记。
- 出口：每个来源有可执行解析与版本字段，不接入只靠首页标题推断“最新”的源。

### T402 安全 fetch 与证据缓存

- **文件**：`agent/evidence/{fetch,store,models}.py`、migration。
- 仅 http(s)/白名单，DNS 解析和每次重定向防私网/环回/元数据地址，限制大小/格式/解压/时间；缓存内容 hash 与获取时间。
- **RED**：SSRF、重定向到内网、DNS rebinding 模拟、超大/无限流、恶意 HTML、注入、缓存过期、许可证缺失。
- 验收：正文为不可信数据；日志不记敏感 query；失败不影响档案回答。

### T403 外部证据引用与“最新”校验

- **文件**：`chat/evidence.py`、`db/models/chat.py`、前端 EvidencePanel。
- `EvidenceRef` 区分档案/外部；保存来源、发布/更新日期、版本、检索时间、摘录与 hash。
- 急症、用药剂量/禁忌/相互作用等高风险论断必须由可靠全文支持；无法取得时不得给确定性高风险指导。百科仅背景；用户问“最新”时回答实际检索日期与版本，不确定就明说。
- 由医学顾问定义关键论断标注规范；每类规定最小样本量、双人独立评审和分歧仲裁，报告样本量/置信区间，不能小样本 pooled 百分比掩盖高风险错误。
- **RED**：伪造 URL/日期、搜索摘要冒称指南全文、旧版当最新、外链失效、跨证据池错配、高风险论断只有摘要/模型记忆。
- 验收：严重无依据高风险论断=0，其余关键论断支持率≥95%，所有外部引用可打开或显示已存档快照状态。

### T404 备份、恢复、索引再建与版本保留

- **文件**：`deploy/`、`scripts/`、`docs/ARCHITECTURE.md` 运维节。
- DB+MinIO 一致性备份流程、隔离环境恢复、索引全量重建、派生版本保留；原件不随派生清理。
- **RED/演练**：随机抽记录校验 hash、外键、Trace、搜索；缺失对象可报警，不伪装成功。
- 验收：形成恢复时间/数据点实测，未演练不得写“可恢复”。

### T405 全量真实留出集与性能复验

- 新增未见医院/类型样本，朝 ≥100 份扩充；解析、检索、临床策略、Web、恢复、容量全部按冻结脚本复验。
- K1/K2 保留并报告精确率、macro 和分层；K3≥20 问全部人工判定，K4=0，K5 按真实漏斗统计。
- 医学安全与“倾向性判断”由医学顾问审阅；没有顾问结果只称工程候选，不做临床效果宣称。

### T406 文档定稿与灰度开关

- 更新 PRD AC、ARCHITECTURE 当前模型/API/状态、部署配置和 README 使用范围；ADR 标 Accepted/Superseded。
- `record_search_v2/agent_v2/external_evidence/image_read_cloud` 独立开关；默认值符合隐私和降级策略。
- 回滚只停新读路径/工具，不删除新资产；兼容层继续供旧 UI；迁移 downgrade 在副本演练。

### T407 发布评审

- 当前树后端/前端/build/Compose smoke/KPI/安全/性能全量新鲜证据；环境阻塞明确写 PLAN。
- Go：所有红线和目标门槛达标，医学/产品/工程分别确认。
- No-Go：任何跨档、引用伪造、原件丢失、危险延误、未授权出域，或关键指标未测量。

## 8. 测试矩阵与命令

| 层级 | 必测 | 命令/证据 |
|---|---|---|
| 单元 | 状态、时间、Markdown、锚点、检索排序、工具 schema、追问、脱敏、引用 | `cd server; ..\.venv\Scripts\python.exe -m pytest tests/unit -q` |
| API | 归属/生命周期、上传、资产、搜索、分页、确认、恢复 | `... -m pytest tests/api -q` |
| 集成 | 任务恢复、索引 outbox、重抽版本、Agent 降级、SSE | `... -m pytest tests/integration -q` |
| 全量后端 | 所有旧新路径 | `cd server; ..\.venv\Scripts\python.exe -m pytest -q` |
| 前端 | token 安全、状态、搜索分页、SSE、追问、跨档缓存 | `cd web; npm test` |
| 构建 | TS/生产包 | `cd web; npm run build` |
| 评测 | scorer selftest、K1/K2/K3、检索、临床策略、benchmark | 各阶段脚本；保留版本化报告 |
| 部署 | build+recreate、冒烟、迁移、恢复 | `bash scripts/smoke_e2e.sh` 与隔离恢复记录 |

每次改代码后容器验证必须 `docker compose -f deploy/docker-compose.yaml build api worker`（涉及 web 则含 web），再 `up -d --force-recreate ...`；只 restart 不算新代码验证。

## 9. 数据迁移、回滚与观测

### 9.1 数据迁移

- 实施前打印当前 Alembic head 与表结构，迁移编号从真实 head 继续；不在计划里假设仍是 `0006`。
- 所有新列可空/有安全默认；大批回填分批、可重入；不在长事务持有全表锁。
- 历史日期未知即 unknown；`created_at` 仅作为 recorded_at，不能反填业务日期。
- 已确认版本永久保留；未确认草稿沿用现有保留策略；索引/派生可重建，原件不参与破坏性清理。

### 9.2 回滚

- 每阶段独立 feature flag；关闭新功能不影响原件读取、HITL 和旧问诊。
- DB 回滚优先“切回旧读+保留新表”，downgrade 只在已备份副本验证；严禁为回滚删除唯一原件。
- 对象写入采用临时键→hash 校验→DB 提交→正式可见；失败孤儿由可审计清理任务处理，不能同步误删有效资产。

### 9.3 观测

- 指标：各阶段任务量/失败/耗时、索引积压/版本漂移、搜索通道命中/耗时、Agent 每工具调用/总预算、原件复读、追问率、引用校验失败、出域拒绝。
- 日志仅 ID、版本、状态、计数和脱敏原因；审计可串联 request/conversation/tool/asset。
- 告警：原件缺失、索引长期不一致、软删任务仍运行、出域隐私拒绝异常增多、危险场景工具延迟。

## 10. 风险、决策点与完成定义

### 10.1 风险登记

| 风险 | 早期信号 | 应对 |
|---|---|---|
| 范围过大导致全而无验收 | R1 同时做 Agent/外链 | 按依赖发布，每阶段只做可独立验收切片 |
| 中文检索选型错误 | 金标 Recall 低/索引膨胀 | T201 PoC，未达标不调 Agent 掩盖 |
| Agent 反复读全史 | token/延迟高、证据重复 | 有界循环、新证据停止条件、按需精读 |
| AI 判断变武断 | 无证据肯定句/追问少 | claim-evidence、医学策略集、校准评审 |
| 图像隐私泄露 | 云端工具收到整页原件 | 默认禁用；授权/裁剪/遮蔽 ADR 后单独开 |
| 指标被评测算术美化 | micro 高但误增多/单文档差 | 精确率+macro+留出集+冻结评分器 |

### 10.2 待确认（按阶段）

- R0：默认归档是否满足“删除”诉求（界面须明说“不移除原件”，不能宣称永久删除）；图像云端默认禁止是否接受，既有上传 VLM 的差距如何整改；医学顾问/来源评审资源何时到位，未确认须重估 R3/R4。
- R1：单文件/批量/页数上限与历史版本保留数量；目前 20MB 不可静默放宽。
- R2：中文分词、embedding、规模硬件和延迟预算由 PoC/ADR 定。
- R3：医学顾问、首发临床场景和追问评测集；不能由工程师独立确认医学有效。
- R4：权威来源/许可/地区、全文快照保留政策、永久删除合规要求。

### 10.3 全局 Definition of Done

只有同时满足才可把对应功能从“计划”改为“已实现”：

1. 代码与 migration 在当前树；AC/架构/配置同步。
2. 新行为先有正确失败的测试，再有当前树通过证据；旧功能全量不回归。
3. 权限、审计、脱敏、状态、幂等、超时、降级均有测试，不只 happy path。
4. 真实服务或明确合成环境完成端到端验证；未执行项写阻塞，绝不当作通过。
5. 数据/模型/提示/评分器版本可复现；质量指标达到本设计与 PRD 对应门槛。
6. PLAN 顶部只回写当前结果、证据链接、风险和下一步；本计划标相应任务完成，不复制第二套实时状态。
