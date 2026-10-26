# AI 私人医生系统 — 长期推进计划表（PLAN）

> 状态：更新中 | 当前状态同步：2026-10-26
> 关联文档：`docs/PRD.md`（产品范围与 AC）、`docs/ARCHITECTURE.md`（规范架构）
> 维护约定：本文件顶部是唯一实时状态入口；历史记录不是当前状态。每完成里程碑或验证项，回写顶部并链接证据，新增工作先入表。

---

## 0. 当前状态仪表盘（唯一实时状态入口）

### 0.1 当前方向：长期健康资产 + 主动取证问诊

2026-10-26 完成下一阶段的**需求、目标架构与路线图设计**，并已完成 **R0 阶段实施**
（生命周期正交、软删安全、出域隐私、评测口径冻结）；**R1–R4 尚未开始**：

- 设计：`docs/superpowers/specs/2026-10-26-health-record-intelligence.md`
- 实施计划：`docs/superpowers/plans/2026-10-26-health-record-intelligence.md`
- 路线：R0 安全/文档基线（✅）→ R1 多类型资产与版本化 Markdown → R2 混合检索 → R3 主动查档/原件复读/追问 → R4 权威外部证据与规模验收。
- 关键决策：沿用模块化单体与现有领域 ID；原件不可变，派生资产版本化；按发生时间组织；指标用 SQL、语义检索找候选；AI 有界主动取证，不把全史塞入上下文。
- 待评审：默认“删除=归档”是否满足诉求、云端原图复读默认禁用、医学顾问和权威知识来源、中文分词与 embedding 选型。

### 0.2 当前基线（2026-10-26，R1 T101–T105 后，当前树新鲜执行）

| 项 | 最近记录 | 当前解释 |
|---|---|---|
| 后端 | **741 passed**，EXIT=0（2026-10-26，两次独立运行一致） | R0 基线 641 → R1 T101–T105 后 741（+100 例） |
| 前端 | **133 passed**，build 通过，EXIT=0（2026-10-26） | 新增发生时间展示用例 |
| 评测契约 | 10 passed；`score_kpi.py selftest` OK（2026-10-26） | K3 完整性门槛与精确率口径已冻结 |
| K1 / K2 | 281/282=99.65%；37/37=100%（2026-10-26 重跑，口径冻结后不变） | 达到既定阈值，但仅 38 份/37 异常且为 development 集；需未见样本复验（B-4） |
| K1 精确率 | 428/440 = 97.27%（新增指标） | 初版口径虚报 51%，修正后可信；仍为含全部预测字段的保守下界 |
| K1 宏平均 | 99.24%（按文档） | 新增指标，防止单份大报告主导整体 |
| 定性项 | 140/155=90.32% | 参考指标，不等同独立生产质量证明 |
| K3 / K5 | 未完成完整评测 | K3 需≥20 问全部人工 verdict（**现在不达 20 题就不输出通过率**）；K5 待真实漏斗统计 |
| K4 | 自动化及浏览器专项有证据；R0 新增软删/生命周期用例 | 仍需新资产/搜索/工具通道逐层回归和审计抽查 |
| 迁移 0007 | 回填逻辑 4 例等价 SQL 单测通过 | **整链未在真实 PostgreSQL 验证**（B-1：Docker 未运行） |
| Compose / K8s | Compose 有较早实测；K8s 未实际 apply | 本机 Docker 未运行，未做容器验证；不得将旧结果当作当前发布证据 |

### 0.3 静态审计发现，进入 R0 优先处理

> 以下缺口已在 R0 实施中修复（T003/T004/T005），保留作为审计痕迹。

- 软删/归档读取和后台任务过滤不完整：部分软删文档仍可能按 ID 读取或被 worker 再处理；须先复现并用测试修复。**→ 已修**（`owned_or_404` 过滤 `deleted_at`；worker/pipeline 遇软删→cancelled）
- `archived` 与处理状态混在 `documents.status`，需正交化且兼容旧客户端。**→ 已修**（`documents/state.py` 单一权威；旧 `status` 为派生投影）
- 尚无独立版本化 Markdown 资产、全文/语义检索、问诊按需原件读取和统一外部证据引用。**→ R1/R2/R3**
- 问诊规划/搜索的查询脱敏和不可信内容隔离需补强；云端原图不得默认出域。**→ 文本出域已修**；图像出域策略待评审（阻塞项）
- 本文档下方及旧 PRD/ARCHITECTURE 的早期任务数、状态、依赖和“待样本”描述仅作历史记录；冲突时以本仪表盘、当前代码和已接受新设计为准。

### 0.4 本轮变更（2026-10-26，R0 实施）

| 任务 | 状态 | 变更文件 | 证据 |
|---|---|---|---|
| T003 软删/归档读取与后台再处理 | ✅ 完成 | `app/auth/deps.py`（`owned_or_404` 过滤 `deleted_at`）、`app/preprocessing/pipeline.py`（软删→job cancelled）、`app/worker.py`（补偿/租约回收排除软删）、`tests/api/test_document_lifecycle.py`(11 例) | 先写 8 例失败（RED）→实现→全绿 |
| T004 处理状态与生命周期正交 | ✅ 完成 | `app/documents/state.py`(新，单一状态权威)、`app/db/models/document.py`(+4 列+2 索引)、`alembic/versions/0007_document_lifecycle_states.py`(新)、`documents/service.py`+`router.py`(+restore)、`preprocessing/{pipeline,review}.py`、`worker.py`、前端 `types/api.ts`+`components/extractionProgress.ts`+两页、`docs/adr/0001-document-lifecycle-and-assets.md`(新)、`tests/unit/test_document_state.py`(7)+`tests/integration/test_migration_0007_lifecycle.py`(4)+`web/tests/documentStatus.test.ts`(6) | 归档不再覆盖"已确认"；旧 `status` 保留为派生投影，旧客户端行为不变 |
| T005 全链路出域隐私 | ✅ 完成 | `app/agent/{planner,tools}.py`、`app/chat/{titler,orchestration}.py`、`app/sanitizer/patterns.py`（新增人名兜底）、`tests/unit/test_outbound_privacy.py`(10) | 规划/标题/搜索词/结果标题出域前脱敏；URL 只送域名；脱敏失败不退回原文 |
| T002 冻结评测口径 | ✅ 完成 | `evals/scripts/score_kpi.py`（K3 完整性门槛、K1 精确率/宏平均/分层、`load_manifest`）、`evals/dataset/kpi_k1k2/manifest.json`(新)、`evals/tests/test_score_kpi_contract.py`(10) | K1/K2 数值不变；K1_precision 97.27%；K3 未达 20 题不再输出通过率 |
| T006 R0 出口 | 🟡 部分完成 | — | 后端/前端/评测测试全绿；**迁移整链未在真实 PostgreSQL 验证**（见阻塞项） |

**本轮变更文件清单**（后端 / 前端 / 迁移 / 评测 / 文档）：

| 类别 | 文件 |
|---|---|
| 后端（新增） | `server/app/documents/state.py` |
| 后端（改） | `server/app/auth/deps.py`、`server/app/documents/{service,router}.py`、`server/app/preprocessing/{pipeline,review}.py`、`server/app/worker.py`、`server/app/agent/{planner,tools}.py`、`server/app/chat/{titler,orchestration}.py`、`server/app/sanitizer/patterns.py`、`server/app/db/models/document.py` |
| 迁移（新增） | `server/alembic/versions/0007_document_lifecycle_states.py` |
| 测试（新增） | `server/tests/api/test_document_lifecycle.py`(11)、`server/tests/unit/test_document_state.py`(7)、`server/tests/unit/test_outbound_privacy.py`(10)、`server/tests/integration/test_migration_0007_lifecycle.py`(4)、`web/tests/documentStatus.test.ts`(6)、`evals/tests/test_score_kpi_contract.py`(10) |
| 测试基建（改） | `server/tests/conftest.py`（测试库按进程隔离，修复并发假失败） |
| 前端（改） | `web/src/types/api.ts`、`web/src/components/extractionProgress.ts`、`web/src/pages/{ArchivePage,DocumentDetailPage}.tsx`、`web/src/api/documents.ts` |
| 评测（改/新增） | `evals/scripts/score_kpi.py`、`evals/dataset/kpi_k1k2/manifest.json` |
| 文档（改/新增） | `PLAN.md`、`docs/adr/0001-document-lifecycle-and-assets.md`、两份 `2026-10-26-health-record-intelligence.md`、设计文档头部状态 |

**R0 期间修正的真实缺陷**（均为本轮改动引入或暴露，已修复并加测试）：

1. 软删文档仍可按 ID 读取详情/签名原件/取草稿/确认入库（K4 同级越权）；
2. worker 会为已软删文档重新生成草稿并改写其状态；
3. 归档覆盖 `confirmed`，"已确认入库"这一临床事实被生命周期动作抹掉，且归档不可恢复；
4. 问诊规划器与会话标题生成把**原始提问/回答**送出域，未脱敏；
5. 搜索结果的**标题与 URL** 未脱敏即进入 prompt（仅 snippet 被处理）；
6. 脱敏器新增的中文人名兜底会误删医学词（"高血压"→"血压"、"血脂偏高"→"血脂要"）——已改为"需人名上下文 + 医学搭配保护"双重保险；
7. 评分器 K1 精确率初版口径不一致（"白细胞计数"对标注"白细胞"被误判多余，精确率虚报 51%）——改为复用命中同款匹配语义后为 97.27%；
8. **测试库固定路径导致并发运行互相破坏**（`server/tests/conftest.py` 用固定 `aidoctor_pytest.db`，两个 pytest 进程互相 drop/create 表，产生 "no such table" 假失败）——改为每次运行使用进程唯一库文件；修复后并发运行实测两路均 674 passed。**注意：跑测试时不要并发执行多个 pytest 进程**（虽已隔离，仍会拖慢且可能争用端口）。

**下一步**：R0 阻塞项清零后进入 R1（T101 起）。不得跳过 R1 直接做检索/Agent。

### 0.5 下一阶段里程碑

| 阶段 | 内容 | 状态 | 出口 |
|---|---|---|---|
| R0 | 文档/评测基线、软删与任务安全、生命周期 ADR | **代码完成，迁移整链验证待补** | 隔离/恢复测试、冻结评测口径 |
| R1 | 多类型上传、发生时间、资产/页锚、Markdown、版本差异 | **进行中（T101/T102 已完成）** | 各类型资产闭环、HITL 与原件不回归 |
| R2 | FTS+pgvector+SQL 混合检索、长期档案中心 | ⬜ 未开始 | Recall@20≥95%、候选 Precision@20≥90%、权限/性能达标 |
| R3 | 有界问诊 Agent、急慢病策略、追问、原件精读 | ⬜ 未开始 | 采用证据精确率≥90%、临床策略/安全集、引用支持、SSE 兼容 |
| R4 | 权威外部证据、留出集、备份恢复、规模验收 | ⬜ 未开始 | 真实/合成端到端证据与医学评审 |

**工作顺序约束：先 R0，再 R1；不得从 Agent 界面或向量接入绕过资产与安全地基。**

### 0.7 R1 进展（2026-10-26）

| 任务 | 状态 | 落地 | 测试 |
|---|---|---|---|
| T101 文档类型/时间/来源元数据 | ✅ | `app/common/timeline.py`（时间语义单一权威）、迁移 `0008`、`documents`+`observations` 新增字段、上传支持用户填写元数据、列表按发生时间排序 | 16 + 9 = 25 例 |
| T102 DocumentAsset 与资产服务 | ✅ | `app/db/models/document_asset.py`、`app/documents/assets.py`、迁移 `0009`、资产 API 两个端点 | 10 例 |
| T103 页块与来源锚点 | ✅ | `app/db/models/document_block.py`、`app/preprocessing/anchors.py`、迁移 `0010`、抽取后自动落锚点 | 7 例 |
| T104 类型化 Markdown 生成器 | ✅ | `app/preprocessing/markdown.py`（8 模板）、接入抽取管线、产出版本化 Markdown 副本 | 14 + 2 集成例 |
| T105 文本文件与批量导入 | ✅ | TXT/MD 上传（内容级校验）、文本分页、`POST /api/documents/batch`、前端多选上传 | 8 例 |

**R1 期间修正的问题**：

1. 月精度被忽略——`_parse_user_span` 原先按字符串长度推断精度，用户显式传 `month` 时失效，改为**显式精度优先**；
2. `record_type` 未持久化——上传时未写入模型字段；
3. "用户已填写"的判定过粗——原用 `source_note` 代理，会误判其他字段，改为**逐字段**判断（字段非空即已填写）；
4. 资产服务在测试中直连真实 MinIO——`conftest` 的假存储未覆盖新模块，补上；
5. 测试 PNG 内容相同导致上传触发去重 409——`make_png_bytes` 增加 `salt` 参数；
6. **YAML 过度转义**——`doc-1` 这类简单值被加引号，改为只在真正破坏解析时才加引号；
7. **文本解码误判**——用被截断的 16 字节判定编码，切断点多字节中文 → 正常中文文本被判"解码失败"，改为用**完整内容**校验；
8. 批量上传缺 Subject 时逐项报错（用户看到一堆无意义失败），改为**整体 422**。

**R1 剩余**：T106 重抽差异、T107 文档详情四区（原件/副本/结构化/版本历史）、T108 标准主数据与出口对账。

### 0.6 R0 阻塞项（必须清零后才进入 R1）

| # | 阻塞项 | 原因 | 需要什么 | 影响 |
|---|---|---|---|---|
| B-1 | 迁移 `0007` 未在真实 PostgreSQL 整链验证 | 本机 Docker 未运行（`docker ps` 连接失败）；仓库既有迁移 0003/0005 依赖 PostgreSQL（JSONB / 实时查询），SQLite 上无法整链执行 | 启动 Docker Desktop → `docker compose up -d postgres` → `alembic upgrade head` → 校验回填与索引 | 迁移逻辑已用等价 SQL 在 SQLite 单测覆盖（4 例），但**整链与真实数据回填未验** |
| B-2 | 云端图像出域策略未批准 | 上传预处理的 VLM/OCR 会把病历图片送云端；脱敏对图像无效 | 决策：默认禁用云端图像 / 限定可裁剪遮蔽 / 接受现状并留审计 | 属**既有能力**的隐私缺口，非本轮引入；R1 引入 `image_read` 前必须先决策 |
| B-3 | 医学顾问与权威来源未到位 | 临床策略集、追问验收、外部证据白名单需医学专业把关 | 指定医学顾问 + 确认来源许可 | 阻塞 R3/R4 的医学验收，不阻塞 R1/R2 |
| B-4 | 留出集不存在 | 当前 38 份为 development 集，K1/K2 数字可能被其分布“喂”出来 | 扩充 ≥100 份未参与调参的样本 | K1/K2 目前只能称“开发集达标” |

> B-1 与 B-4 属**验证缺口**而非功能缺口：不得因未验证就宣称“已达标”，也不得因此阻塞可独立验收的 R1 任务。

---

## 1. 历史基线摘要（保留，非当前完整结论）

- P0 已实现账户/多 Subject、上传解析/HITL、时间线/趋势、带引用问诊、审计/脱敏、配置与基础网页搜索等能力；实际路径见 §3。
- K1/K2 在已记录的 38 份样本及当时评分口径下达到阈值，早期“仍待样本/仅离线测试”的表述已失效；这不等于当前发布验收或未见样本生产质量通过，数值与限制见 §0。
- 工程治理遵循 `AGENTS.md`；新阶段按设计拆分为独立可验收任务，严格 TDD，不将设计目标写成实现成果。

## 2. 范围说明

当前路线以 §0 的 R0–R4 为准。下方 M0/M1/M2 和 P0 功能清单保留作历史证据与旧范围映射，不再独立维护另一套“当前计划”。

## 3. 现状快照（P0 功能清单 × 证据）

| ID | 功能 | 状态 | 证据 / 挂接点 |
|---|---|---|---|
| F-01 | 注册/登录（JWT） | ✅ 已实现+测试 | `server/app/auth/`，`tests/api/test_auth.py` |
| F-02 | 多 Subject 管理与切换器 | ✅ 已实现+测试 | `web/src/stores/subjectStore.ts`，`web/tests/switchSubject.test.ts` |
| F-03 | 写入强制选档（422） | ✅ 已实现+测试 | `tests/api/test_write_requires_subject.py` |
| F-04 | 上传 + 分类分流 | ✅ 已实现+测试 | `server/app/preprocessing/`，`tests/integration/test_extraction_pipeline.py`；解析链 table→vlm→ocr→rule→mock |
| F-05 | 抽取 + 字典映射 → DerivedRecord | ✅ 已实现+测试 | `app/llm/extraction.py`、`preprocessing/{mapper,merger,pdf_render,textnorm}.py`、`parsers/{table,vlm,ocr,rule}_parser.py`；字典自动播种 `app/data/dictionary_seed.py` |
| F-06 | HITL 确认入库（幂等） | ✅ 已实现+测试 | `tests/unit/test_review_idempotency.py`、`tests/api/test_review.py` |
| F-07 | 时间线视图 | ✅ 已实现+测试 | `server/app/timeline/`，`web/src/pages/TimelinePage.tsx` |
| F-08 | TOP20 趋势折线（SQL 确定性） | ✅ 已实现+测试 | `app/observations/trends.py`，`tests/unit/test_trends_sql.py` |
| F-09 | 对话问诊 L0–L2 组装 | ✅ 已实现+测试 | `app/chat/context_builder.py`，`tests/unit/test_context_builder.py` |
| F-10 | 引用标记 + 原件回链 + Trace | ✅ 已实现+测试 | `app/chat/citations.py`，`web/tests/citationParse.test.ts` |
| F-11 | 红旗规则引擎（热更新） | ✅ 已实现+测试 | `server/app/redflag/`，`tests/unit/test_redflag_engine.py`，种子 `scripts/seed_redflag_rules.py` |
| F-12 | 对话式添加档案（草稿卡片） | ✅ 已实现+测试 | `app/chat/draft_parser.py`，`tests/api/test_chat_draft.py`、`web/tests/confirmFlow.test.ts` |
| F-13 | 原件回看（时效链接） | ✅ 已实现+测试 | `web/src/pages/DocumentDetailPage.tsx`，`app/documents/storage.py` |
| F-14 | 审计日志 + LLM 脱敏 | ✅ 已实现+测试 | `server/app/audit/`、`app/sanitizer/`，`test_sanitizer`（py+ts） |
| F-15 | 容器化部署（Compose/K8s） | ✅ Compose 实机验证通过；K8s 待集群 | `deploy/docker-compose.yaml` 六服务全健康（minio 走源码构建），`scripts/smoke_e2e.sh` 14/14 PASS（2026-10-20）；K8s apply 见 T0.3 |

**测试基线（2026-10-21 实测）**：后端 pytest **219 passed**（SQLite + 内存替身离线跑）；前端 vitest **39 passed**；前端生产构建通过。跨档保护用例见 `tests/api/test_idor.py`。

## 4. 里程碑计划表

### M0 — P0 验收收尾（当前阶段）

| # | 任务 | 优先级 | 状态 | 说明 / 退出判据 |
|---|---|---|---|---|
| T0.1 | 本地自动化验证全绿（pytest/vitest/build） | P0 | ✅ 完成 | 本次会话达成（含 §5 修复） |
| T0.2 | Docker Compose 一键起栈 + `scripts/smoke_e2e.sh` 冒烟 | P0 | ✅ 完成 | AC-15.1/15.2；六容器全健康（minio 源码构建），冒烟 14/14 PASS（2026-10-20，Git Bash）；2026-10-21 修正 web IPv6 localhost 探针和 worker 错继承 API 探针 |
| T0.3 | K8s 清单在集群实际 apply 验证 | P0 | ⬜ 待办 | AC-15.4；需要一个可用集群，镜像仓库待定 |
| T0.4 | 真实 LLM 网关联调（OpenAI 兼容 base_url/key 注入） | P0 | ✅ 网关与抽取链已验证 | 网关 `/models` 返回 26 个模型；`qwen3.8-flash` 图片请求超时，已切换到图片调用验证成功的 `hy3`；真实血常规 JPG 使用 `parser=vlm` 进入 `awaiting_review`，生成 1 个 DerivedRecord（2026-10-21）；密钥仅从本地忽略文件读取 |
| T0.5 | K1/K2 抽取准确率评测：≥30 张真实化验单人工比对 | **P0 门槛** | 🟡 框架就绪，样本已到位，待标注 | K1≥90%、K2≥95%，不达标先修流水线不加功能；本地检测单已成功生成待确认草稿，但尚未完成 30+ 张人工金标比对 |
| T0.6 | K3 引用正确率评测：20 个典型问题 | **P0 门槛** | 🟡 框架就绪，网关已联通，待问题集评测 | ≥85%；Eval 用例库固化于 `evals/dataset/kpi_k3/`，需完成 20 个问题的回答与人工引用判定 |
| T0.7 | K4 零串档专项测试 + 审计抽查 | **P0 门槛** | ✅ 自动化部分完成 | `test_idor.py`/写保护用例绿；上线前补审计日志抽查 |
| T0.8 | K5 闭环完成率埋点统计 | P0 | ⬜ 待办 | Document 状态机终态统计口径见 PRD §9.2 |
| T0.9 | JWT 存储方式安全评审（HttpOnly Cookie vs localStorage） | P0 | ✅ 完成 | 结论：P0 维持 localStorage+Bearer（可接受风险），M1 初落实 TTL≤24h+CSP；见 `docs/REVIEW_JWT_STORAGE.md`（关闭 PRD §7.1 遗留项） |

### M1 — P1 版本（M0 Go 后启动）

| # | 任务 | 优先级 | 状态 | 说明 |
|---|---|---|---|---|
| T1.1 | L3 向量语义检索全量启用（pgvector 填充，表结构已留缝） | P1 | ⬜ 未开始 | embedding 列已在 `alembic/versions/0001_init.py` 建好 |
| T1.2 | DICOM 影像链路（Orthanc+OHIF 或降级 VLM 初读） | P1 | ⬜ 未开始 | `Document.kind=DICOM` 枚举已预留 |
| T1.3 | 医生版 PDF 摘要导出 | P1 | ⬜ 未开始 | |
| T1.4 | 用药/随访提醒 | P1 | ⬜ 未开始 | |
| T1.5 | 小程序壳 | P1 | ⬜ 未开始 | API 已经 OpenAI 兼容协议暴露，客户端不持 key |
| T1.6 | 本地模型切换验证（Ollama/NewAPI 网关） | P1 | ⬜ 未开始 | `app/llm/client.py` 换 base_url 即可 |

### M2 — P2 展望（只列不排期）

- 家庭关系图谱与遗传风险；指标字典社区化/联网对齐；医学知识库 RAG；本地医疗模型微调评测线。

## 5. 本次会话变更记录（2026-10-20）

| 变更 | 文件 | 性质 |
|---|---|---|
| `/healthz` 首次探测前 deps 空集 → 种子 pending + starting 语义修正 | `server/app/main.py` | Bug 修复（原测试必挂） |
| vitest node 环境无 localStorage → 新增内存 shim setup + 独立 `vitest.config.ts` | `web/tests/setup.ts`、`web/vitest.config.ts` | 测试基建补齐 |
| Makefile COMPOSE 路径指向不存在的 `deploy/compose.yaml` | `Makefile` | 一致性修复 |
| SQLAlchemy legacy `Query.get()` → `Session.get()` | `tests/integration/test_extraction_pipeline.py` | 技术债清理 |
| MinIO 镜像供给：官方 repo **已归档**、公共 registry 不再匿名分发 → 新增源码构建 `deploy/minio/Dockerfile`，compose/k8s 改用 `local/minio-source:latest`，healthcheck 改 wget probe；基础镜像经 daocloud mirror retag 解决本机拉取失败 | `deploy/minio/Dockerfile`(新)、`deploy/docker-compose.yaml`、`deploy/k8s/07-minio.yaml` | ✅ 本次会话落地并实测 |
| 冒烟脚本修复：greedy sed/cut 字段错位、multipart 误带 JSON Content-Type（422）、`check name cmd | grep` 管道被外层解析在 set -e 下静默退出、Git Bash 传非 ASCII argv 损坏 JSON body（请求体改纯 ASCII） | `scripts/smoke_e2e.sh` | Bug 修复，14/14 PASS 实测 |
| JWT 存储安全评审结论落地：P0 维持 localStorage+Bearer；M1 初 TTL≤24h+CSP+登录限速；触发重评条件见文档 | `docs/REVIEW_JWT_STORAGE.md`(新) | T0.9 完成，关闭 PRD §7.1 遗留项 |
| KPI 评测框架骨架：标注模板(JSONL)+跑分脚本（K1/K2/K3 口径与阈值内置，selftest ✔ dry-run ✔）+预测导出工具 `export_predictions.py`（登录/Subject 链路实测 ✔），真实样本到位即可端到端跑分 | `evals/`(新) | T0.5/T0.6 就绪待料 |
| 边缘情况测试加固：空文件上传→415、垃圾 JWT→UNAUTHENTICATED(区别于过期)、HITL GET/confirm 跨档→404 且不入库、pending 无草稿 confirm→404；后端基线 97→**102 passed** | `server/tests/api/test_edge_cases.py`(新) | ✅ 完成（chat content 长度上限现 schema 未定义，不臆造需求未加断言） |

## 6. 主要风险

| 级别 | 风险 | 影响 | 建议动作 |
|---|---|---|---|
| ~~阻塞~~ ✅已解 | **MinIO 镜像供给**：官方 repo 已归档、公共 registry 不再分发 → 采用官方建议源码构建 `deploy/minio/Dockerfile`（golang alpine 两阶段），compose/k8s 改用 `local/minio-source:latest`；基础镜像经 daocloud 加速拉取后 retag | T0.2 已实测跑通；T0.3 需集群可访问的仓库或节点本地导入 | 维持现状；上云前把源码构建镜像推送到目标 registry |
| **阻塞** | K1–K4 任一不达标即 No-Go（PRD §3），当前仅有离线单测，无真实样本评测数据 | P0 无法宣布验收通过 | 尽快收集 ≥30 张自家真实报告建标注集 |
| ~~高~~ ✅已验证 | LLM key/网关未接入，抽取链目前靠 mock 降级演示 | 真实网关已联通并完成单张检测单抽取；KPI 仍需人工金标评测 | 批量跑样本，记录模型、提示词和人工修正结果 |
| ~~中~~ ✅已清 | SQLAlchemy legacy API / utcnow()：**自有代码已全部清零**（Query.get→Session.get、utcnow 改 timezone-aware），2026-10-20 复跑 pytest 97 passed 确认；剩余 111 条警告均来自第三方 jose/starlette/anyio，随依赖升级自然消除 | 无（观察项） | M1 依赖小版本升级时复查一次即可 |
| 低 | `deploy/.env` 已存在且含本地值（`.gitignore` 已覆盖，不会误提交） | — | 无需动作 |

## 7. 待确认项

1. Docker Engine / K8s 集群是否可用？（T0.3 仍需集群）
2. 真实样本评测集的人工标注人和截止时间是什么？（T0.5–T0.6）
3. 真实网关测试 key 是否需要轮换？（已加入忽略目录，但凭据已在本地文档中出现，建议测试后轮换）
4. ~~JWT 存储安全评审结论（T0.9）~~ → 已关闭：P0 维持 localStorage+Bearer，见 `docs/REVIEW_JWT_STORAGE.md`。

## 8. 长期工程治理与 Agent 技能

### 8.1 已安装技能

| 技能 | 用途 | 项目入口 | 状态 |
|---|---|---|---|
| `brainstorming` | 复杂需求澄清、范围和反目标 | `.agents/skills/brainstorming/SKILL.md` | ✅ |
| `writing-plans` | 多步骤实施计划与验收拆解 | `.agents/skills/writing-plans/SKILL.md` | ✅ |
| `executing-plans` | 按计划逐任务执行并记录证据 | `.agents/skills/executing-plans/SKILL.md` | ✅ |
| `test-driven-development` | RED→GREEN→REFACTOR | `.agents/skills/test-driven-development/SKILL.md` | ✅ |
| `systematic-debugging` | 根因调查与单假设修复 | `.agents/skills/systematic-debugging/SKILL.md` | ✅ |
| `subagent-driven-development` | 独立任务的实现/审查闭环 | `.agents/skills/subagent-driven-development/SKILL.md` | ✅ |
| `verification-before-completion` | 完成声明前的新鲜验证 | `.agents/skills/verification-before-completion/SKILL.md` | ✅ |
| `finishing-a-development-branch` | 测试、风险、分支收尾 | `.agents/skills/finishing-a-development-branch/SKILL.md` | ✅ |

### 8.2 使用约定

- 复杂需求走 `brainstorming → writing-plans → executing-plans`；任务独立且有子代理时可将执行环节替换为 `subagent-driven-development`。
- 每次编码/修复遵循 `test-driven-development`；每次异常遵循 `systematic-debugging`；每次完成结论前遵循 `verification-before-completion`。
- 根目录 `AGENTS.md` 是项目工程约束入口；架构、产品和长期状态分别以 `docs/ARCHITECTURE.md`、`docs/PRD.md`、本表为准。
- 由于当前环境无法稳定访问 GitHub，官方 CLI 安装尝试失败；本次使用已从 `skills.sh`/上游原文核验的项目内版本，保留来源链接。网络恢复后可按 `.agents/README.md` 同步上游，并重新检查项目特有约束是否保留。

### 8.3 本轮安装记录（2026-10-21）

| 项 | 结果 |
|---|---|
| `npx skills add https://github.com/obra/superpowers ...` | GitHub 连接重置，未完成远端 clone |
| 项目内技能目录 | `.agents/skills/`，8 个技能均有 `SKILL.md` |
| 工程约束入口 | `AGENTS.md` 已新增 |
| 安装验证 | 文件存在性、技能入口和来源链接已检查；代码测试无需因文档变更重跑 |

## 9. 本轮测试资料与真实网关联调记录（2026-10-21）

| 事项 | 结果 |
|---|---|
| 本地测试资料 | `docs/testinfo/` 共 19 个文件（PDF/JPG/说明），已加入 `.gitignore`，不纳入版本控制 |
| LLM 网关可达性 | OpenAI 兼容 `/models` 成功返回 26 个模型；最小 chat 请求成功 |
| 真实检测单链路 | `202508血常规.jpg` 上传成功；状态 `pending → extracting → awaiting_review` |
| 抽取产物 | 最新文档 1 个 `DerivedRecord`、0 个 Observation，符合“先草稿、后人工确认”边界 |
| Compose 修复 | web healthcheck 改为 `127.0.0.1`；worker 禁用错误继承的 HTTP healthcheck；web healthy、worker running |
| 敏感信息处理 | 测试 key 未写入源码、日志或计划表；测试命令只输出状态和数量 |
| 下一步 | 批量处理并人工标注 30+ 张检测单，完成 K1/K2；准备 20 个 K3 引用评测问题 |

## 11. 办公环境真实抽取与预览修复（2026-10-21）

| 项 | 结果 |
|---|---|
| LLM 配置 | `deploy/.env` 使用已验证办公网关；文本模型 `deepseek-v4.1-flash`，视觉模型 `hy3` |
| 模型选择依据 | `hy3`、`Doubao-Seed-Evolving`、`1p_deepseek-v4.1-flash` 图片探针成功；`qwen3.8-flash` 图片请求超时，因此不作为视觉模型 |
| 真实抽取 | 最新血常规图片：`llm.call model=hy3`，`parser=vlm`，状态 `awaiting_review`，1 个 DerivedRecord，0 个已确认 Observation |
| 原件预览 | 新增 `MINIO_PUBLIC_ENDPOINT`；容器内部继续使用 `minio:9000`，浏览器签名地址使用 `localhost:9000`；公开签名客户端固定 `us-east-1` 避免容器访问 localhost 探测 region |
| 回归验证 | MinIO 局部测试 `2 passed`；后端全量 `103 passed`；公开签名地址主机为 `localhost:9000` 且实际 GET 返回 200 |
| 体验入口 | `http://localhost:8080` → 注册 → 档案 → 上传本地检测单 → 等待“待确认” → 打开详情编辑确认 |
| 旧占位草稿 | 在详情页点击“重新抽取”，会使用当前 `hy3` 视觉模型；重新抽取成功后再逐项确认，不自动入库 |

## 10. 白屏故障记录（2026-10-21）

| 项 | 结果 |
|---|---|
| 现象 | 浏览器加载 HTML/JS/CSS 成功，但页面全白；控制台出现 React 运行时异常 |
| 根因 | `App` 使用 `Routes`、`Navigate`、`useNavigate`，入口 `web/src/main.tsx` 未提供 `BrowserRouter` 上下文 |
| 修复 | 在 `QueryClientProvider` 内包裹 `BrowserRouter` |
| 验证 | `npm run build` 通过；前端 `24 passed`；重建 web 容器后浏览器进入 `/login`，DOM 内容正常 |
| 预防 | 后续新增 React 入口/Provider 时，必须至少做一次真实浏览器启动检查，不仅依赖单测和构建 |

## 12. 抽取链路重构与体验修复（2026-10-21，用户反馈三轮问题）

### 12.1 用户报告的三个问题与根因

| # | 现象 | 根因 |
|---|---|---|
| 1 | 上传后一直“抽取中/待处理”，看不到进度 | `extraction_jobs.lease_until` 只写不读；worker 出事/超时后 job 永挂 `running`、doc 永挂 `extracting`（库中实测存在 >2h 记录）。详情接口只回 `state`，前端无从展示页级进度 |
| 2 | 一份变成“待确认”，但内容全是“全国统一报告解读专线 / 400” | 整份 20 页 PDF 一次性送 VLM → 网关 60s 超时（实测 121s）→ 降级 `rule` 逐行正则，把页眉专线电话当指标；`_NOISE` 未覆盖电话/网址/版权 |
| 3 | 另一份提示“离线演示模式…占位草稿” | `MockParser.available()` 恒为 `True`，AI 可用时调用失败也会降级成 mock，用户看到“待确认”却是假数据 |

### 12.2 修复与验收证据

| 变更 | 文件 | 证据 |
|---|---|---|
| 解析链重构为 **table → vlm → ocr → rule → mock**，并支持结果融合 | `preprocessing/parsers/{__init__,table_parser,vlm_parser,ocr_parser,rule_parser}.py`、`preprocessing/merger.py` | 真实样本：2020-09.pdf 表格 116 项/15 页（0.46s）；2021_0907.pdf 表格 55 项不足 → 融合 VLM 补 80 项，最终 **135 项、66 个数值、55 项字典命中** |
| PDF 逐页渲染 + 逐页视觉抽取（含并发与单页失败隔离） | `preprocessing/pdf_render.py`、`vlm_parser.py` | 单页 178KB / 5.6s / 12 项（整份送模型曾 121s 超时）；`tests/unit/test_vlm_page_extraction.py` |
| 文本 NFKC 归一化（康熙部首 `⽬`→`目`） | `preprocessing/textnorm.py` | 2021 报告不归一化时表格抽取 **0 项**，归一化后 55 项；`tests/unit/test_textnorm.py` |
| 页眉/联系方式噪声过滤（专线/电话/网址/版权/机构名） | `preprocessing/noise.py`、`rule_parser.py` | `tests/unit/test_rule_parser_noise.py`，专线 400 不再成为指标 |
| 任务阶段/页进度/耗时/租约状态可观测 | `db/models/preprocessing.py`、`alembic/versions/0002_extraction_progress.py`、`documents/service.py` | 实测详情接口返回 `stage=extracting, progress=9/21, elapsedSeconds=30, stale=false` |
| worker 租约回收（崩溃/超时任务自动重入队，超上限转 failed） | `app/worker.py::recover_stale_jobs` | `tests/unit/test_job_recovery.py` 5 例；线上实测两条卡死 >2h 的文档被纠正 |
| `mock` 仅在 LLM 未配置时可用（AI 失败不再产出假草稿） | `rule_parser.py::MockParser.available` | 失败时 doc→failed + 可读原因，用户可重新抽取 |
| “重新抽取”改为异步入队（原同步跑 20 页必然超过前端 60s） | `preprocessing/review.py::reextract` | `tests/api/test_review.py`（含双击 409 防重） |
| 重复抽取唯一键冲突（`document_pages` ORM delete 排序错误） | `pipeline.py::_store_page_rows` | `tests/integration/test_reextract_pages.py`；线上 UniqueViolation 已消除 |
| 指标字典自动播种（生产库曾为 0 行 → 全部“字典未命中”） | `app/data/{indicator_dictionary,dictionary_seed}.py`、`server/entrypoint.sh` | 启动日志 `created=58`；`tests/unit/test_dictionary_seed.py`；实测 mapped 0 → 55 |
| 前端进度面板 + 列表子状态 + 批量确认 | `web/src/components/{extractionProgress,confirmBulk}.ts`、`pages/{DocumentDetailPage,ArchivePage}.tsx`、`ConfirmCard.tsx` | `web/tests/{extractionProgress,confirmBulk}.test.ts`；135 项报告可“一键确认高置信项”并只看需确认项 |

### 12.3 解析链选择依据（实测）

| 样本 | 页数 | table | vlm（逐页） | rule |
|---|---|---|---|---|
| 2020-09.pdf | 15 | 116 项 / 0.46s | 127 项 / ~106s | 0 项 |
| 2021_0907.pdf | 21 | 55 项 / 1.7s | 142 项 / ~110s | 0 项 |

结论：表格确定性最准但可能漏页；视觉覆盖全但慢且偶发漏读。因此**表格优先、覆盖不足时视觉补漏融合**（`merger.py`，同名同单位以表格为准并补齐参考区间）。OCR（rapidocr）作为扫描件/拍照件兜底，实测单页 2.4s。

### 12.4 验证命令（当前树）

```
cd server; ..\.venv\Scripts\python.exe -m pytest -q        # 136 passed
cd web; npm test                                            # 34 passed
cd web; npm run build                                       # 通过
docker compose -f deploy/docker-compose.yaml up -d --build  # 六服务健康
```

### 12.5 遗留与下一步

- K1/K2 人工金标比对仍需按 §4 T0.5 执行（当前只有单样本抽查）。
- 表格解析对“无表格线的扫描件”无效 → 依赖 OCR/VLM，已在链路中兜底但未做批量准确率评测。
- `文档详情` 的 PDF 预览仍用 pdfjs 直连签名 URL；若后续更换对象存储需复核 workerSrc 与 CORS。

## 13. 指标标准主数据与 AI 底座落地（2026-10-21）

### 13.1 需求（用户原始诉求）

> 检查结果可能来自多个医院和诊所，同一指标在不同检查单上命名/单位不一样。
> 不能出现同一个指标多次导入后被解析成多个指标。系统有 AI 底座，这类问题应该用 AI 解决。
> 指标建立时就要有标准名称和标准单位；解析时能对齐就直接对齐，确实没有才新建。

### 13.2 实现（分层，成本由低到高）

| 层 | 机制 | 文件 |
|---|---|---|
| 1 | 归一化精确：NFKC（含康熙部首）→ 去标点 → 缩写展开（HGB/Hb）→ 等价词（血色素→血红蛋白、比积→压积、空腹血葡萄糖→空腹血糖）→ 修饰词剥离 | `preprocessing/indicator_names.py` |
| 2 | 两级归一：`strip_decorators=false` 保留“浓度/含量”以区分 MCH/MCHC；`true` 兼容“总胆固醇测定” | 同上 + `mapper.match_indicator` |
| 3 | 模糊匹配（≥0.92 且非易混成对） | `indicator_names.best_fuzzy_candidates` |
| 4 | **AI 语义对齐**：原始名+单位+候选词表 → 判定复用已有 code / 判定为新指标 | `preprocessing/indicator_aligner.py` |
| 5 | 单位统一：值/参考区间同步换算到字典标准单位，原始值留痕 | `preprocessing/units.py` |
| 6 | 代码侧安全阀：成对/系列指标禁止合并；AI 新建指标须过准入校验 | `mapper._PAIR_TRAITS`、`aligner.is_plausible_indicator_name` |
| 7 | 历史脏数据防御：加载字典时过滤噪声项；匹配前拦截噪声名 | `mapper.load_dictionary`、`aligner.is_noise_text` |

### 13.3 实测验收（两份不同来源报告，同一 Subject）

| 项 | 结果 |
|---|---|
| 报告 A（2021_0907.pdf，表格解析 54 项） | mapped 51，rejected 3（T1 / 年0 / 每3 全被拦截） |
| 报告 B（202508血常规.jpg，视觉解析 26 项） | mapped 26（exact 14 / ai 3 / ai_new 9） |
| **跨报告共享标准 code** | **16 个**（HGB/HCT/WBC/RBC/PLT/MCV/MCH/MCHC/PDW/MPV/PCT/LYMPH%/LYMPH#/NEUT%/NEUT#/RDW-CV） |
| 单位换算 | 12.5 g/dL → 125 g/L；4400 /uL → 4.4 10^9/L；91.6 mg/dL → 5.08 mmol/L，参考区间同步换算 |
| 异常判定一致性 | 12.5 g/dL 与 125 g/L 均判定为 `L` |
| AI 新建指标复用 | 「同型半胱氨酸测定」AI 新建后，第二份报告写 `HCY` 直接精确命中，不再调模型、不重复建 |

### 13.4 治理落点

| 项 | 位置 |
|---|---|
| AI 底座八条原则（强制） | `AGENTS.md` §AI 底座原则 |
| 标准主数据规则 + AC-9.1~9.6 | `docs/PRD.md` §9.3 |
| 对齐链路图 + 可追溯字段 + 安全阀 | `docs/ARCHITECTURE.md` §6.7/§6.8 |
| 脏数据清理工具 | `scripts/clean_indicator_dictionary.py`（默认 dry-run，`--apply` 才停用） |

### 13.5 测试证据（新增 34 例）

`test_units.py`(9)、`test_indicator_names.py`(16)、`test_indicator_alignment.py`(21)、
`test_cross_hospital_alignment.py`(5)、`test_ai_indicator_gate.py`(13)、
`test_dictionary_noise_filter.py`(5)、`test_noise_alias_hijack.py`(3)。

后端合计 **219 passed**，前端 **39 passed**，构建通过。

### 13.6 已知边界

- AI 新建指标仍需人工在字典页复核标准名/单位；当前只保证不重复建、不误并。
- 跨维度换算（mg/dL↔mmol/L）依赖 `units._MOLAR_MASS` 分子量表，未收录的项目保持原单位而非猜测。
- 同一指标不同**检测方法**（如不同试剂盒）导致的参考区间差异，当前按报告自带区间优先，未做方法学建模。

## 14. Web 端到端验收与 K4 修复（2026-10-21）

### 14.1 新增测试基建

| 项 | 说明 |
|---|---|
| `.agents/skills/web-e2e-testing/SKILL.md` | Web E2E 技能：20 个 P0 场景清单、证据要求、常见坑（后台标签页轮询暂停、跨档 404 属正常、中文控制台乱码、multipart 非 ASCII 限制） |
| `scripts/shrink_image.py` | 读图前压缩：长边 1280 + JPEG q78，实测 690KB→74KB、1690KB→130KB；只写 `*.small.*`，不覆盖原件 |

### 14.2 浏览器实测发现并修复的缺陷（按严重度）

| # | 缺陷 | 根因 | 修复 | 验证 |
|---|---|---|---|---|
| 1 | **跨档案泄露（P0）**：选中「测试成员A」时空档案，直接访问「本人」文档 URL 仍能看到文档与原件 | `select_owned` 只校验 Account，未校验 Subject；PRD K4 要求零串档 | `select_owned(..., subject_id)` + `resolve_subject_id()`（未传则回退 lastSubject/default）；documents 全部读/写路由与 review 服务接受 subjectId；前端 `documentsApi` 全量透传 | `tests/api/test_cross_subject_isolation.py` 4 例；浏览器实测返回「资源不存在」 |
| 2 | 登录报「服务器内部错误」 | nginx 启动时缓存 `api` 容器 IP，api 重建后 IP 变化 → `/api/*` 全部 502 | `resolver 127.0.0.11` + 变量式 `proxy_pass`，运行时解析 | 代理层登录返回正确 401 + 「邮箱或密码错误」 |
| 3 | 一次提问后回答渲染两遍 | SSE `done` 后未清 `live`/`userEcho`，与回拉历史同时渲染 | `components/chatDedup.ts` + ChatPage effect 按 id/内容判定后清理 | `web/tests/chatDedup.test.ts` 5 例；浏览器复测单条 |
| 4 | 移动端横向滚动 | 缺 640px 断点；`.upload-row` 不换行 | 新增 `@media (max-width:640px)`；上传行 flex-wrap + 隐藏原生 file input | 375px 下 `scrollWidth == innerWidth` |
| 5 | 待确认列表混入 `T1/年0/每3` 排版碎片 | 噪声仅在匹配阶段标记，未从草稿剔除 | pipeline 落库前去噪；`review._confidence_summary` / `_compare_versions` 同步忽略噪声 | `tests/integration/test_draft_noise_filter.py` 2 例 |
| 6 | 版本差异把噪声算作"新增" | diff 未过滤噪声 | 同上 | 同上 |

### 14.3 已验收通过的场景（浏览器实测）

注册登录、登录失败提示、档案切换与隔离、上传、抽取进度（阶段+页数+预计耗时）、待确认分组（异常/低置信/未命中/正常）、批量确认与「只看需确认」、确认入库幂等、时间线分组、趋势「数据不足」提示、问诊流式回答 + 引用 + 「依据有限」、红旗「立即就医」、设置页审计记录、成员新建、移动端布局。

### 14.4 当前基线

- 后端 `pytest`：**225 passed**
- 前端 `vitest`：**44 passed**；`npm run build` 通过
- 六容器健康；`/api/*` 经 nginx 代理正常

### 14.5 下一步

- 继续 K1/K2 评测：需将样本上传→确认入库后回填 `documentId`，跑 `export_predictions.py` + `score_kpi.py`；当前已具备自动化链路，只差人工金标。
- 归档/删除文档的 E2E 场景、问诊草稿卡片（F-12）尚未在浏览器中逐项验证。



## 15. 系统配置管理 + 问诊 Markdown + 问诊 Agent（2026-10-22）

### 15.1 需求与决策

| 需求 | 用户原始诉求 | 落地决策 |
|---|---|---|
| 系统配置管理 | 「llm 配置，文档识别和问诊可以配置不同的 llm 模型，因为文档识别量大而且简单需要考虑成本，问诊要求能力强；其他系统相关的配置最好有配置界面」 | DB 覆盖层 + env 基线；`LLM Profile` 分 `extraction` / `chat` 两场景；通用配置界面 |
| 密钥存储 | 用户选择「禁止，key 只读展示（仅 env/Secret）」 | `api_key` 标记 `secret_env_only`，界面只显示「已配置」，写入请求 422 拒绝 |
| 问诊 Markdown | 「问诊页面文字要支持 markdown 渲染」 | `marked.lexer` → React 元素，**零 HTML 注入**，引用 chip 仍可点 |
| 问诊 Agent | 「内置的 ai 不仅是对话能力，应该是一个简单的 agent，支持联网搜索能力和预制一些临床医学 skill」 | 前置工具轮 + 单次生成；轮数/调用数/超时三上限；失败必降级 |
| 免费搜索 | 「提供默认免费的搜索，可以配置自定义的搜索服务，比如 SearXNG」 | 默认 `free` = Wikipedia Action API（实测可达）；可选 `searxng` / `none` |
| Agent 默认 | 用户选择「默认开启（免费搜索）」 | `agent.enabled` 默认 `true`，`agent.search_provider` 默认 `free` |

### 15.2 新增后端模块

| 文件 | 职责 |
|---|---|
| `server/app/db/models/system_setting.py` | `system_settings` 表模型 |
| `server/alembic/versions/0003_system_settings.py` | 建表迁移（只新增表，可安全 downgrade） |
| `server/app/config/registry.py` | 配置项白名单（20 项）+ 类型/范围/URL 校验 + 密钥拒写 |
| `server/app/config/store.py` | 读写、批量 all-or-nothing、回退 env、掩码 |
| `server/app/config/resolver.py` | DB→env→default 三级解析、30s TTL 缓存、`resolve_llm_profile()` |
| `server/app/config/router.py` | `GET/PATCH /api/settings/system`、`POST /reset`、`POST /test` |
| `server/app/agent/search.py` | `WikipediaProvider`（默认免费）、`SearxngProvider`、`FakeSearchProvider`、URL 安全过滤 |
| `server/app/agent/skills.py` | 5 个内置临床 skill（化验解读/症状分诊/用药安全/慢病随访/报告解释） |
| `server/app/agent/tools.py` | 工具执行、超时隔离、结果脱敏、长度上限、注入过滤 |
| `server/app/agent/planner.py` | 工具规划（严格 JSON + 白名单工具 + 失败降级） |

### 15.3 改造点

| 文件 | 改动 |
|---|---|
| `server/app/llm/client.py` | `LLMClient(profile)`；`get_llm_client(scenario, db)`；日志加 `scenario=` |
| `server/app/chat/orchestration.py` | 问诊用 `chat` profile；接入 Agent 工具轮；新增 `status` 事件；`done.agent` 字段 |
| `server/app/preprocessing/*`、`chat/draft_parser.py` | 显式使用 `extraction` profile |
| `server/app/main.py` | 挂载 `system_config_router` |
| `web/src/components/markdownTokens.ts` | Markdown → 规范化节点树（纯函数，白名单 token） |
| `web/src/components/MarkdownMessage.tsx` | 节点树 → React 元素，叶子文本走 `CitationTag` |
| `web/src/components/SystemConfigPanel.tsx` | 配置界面（分组、掩码、测试、恢复默认） |
| `web/src/api/systemConfig.ts` | 配置 API 封装 |
| `web/src/pages/ChatPage.tsx` | 助手消息用 Markdown 渲染；消费 `status` 事件 |
| `web/src/types/api.ts` | `SseEvent` 增加 `status`、`done.agent` |
| `web/src/styles/global.scss` | Markdown 样式 + 配置面板样式 + 移动端断点 |
| `web/package.json` | 新增 `marked` |

### 15.4 关键技术决策与实测依据

| 决策 | 依据 |
|---|---|
| 免费搜索选 Wikipedia 而非 DuckDuckGo | **实测**：DDG Lite/HTML 在容器出口 IP 上被反爬拦截（返回 14KB 空壳页、`result-link` 计数为 0，GET 与 POST 均失败）；Wikipedia Action API 稳定返回结构化中文结果（`高脂血症` → 3 条）。本机 PowerShell 直连 DDG 成功，说明是**出口 IP 差异**而非代码问题 |
| Markdown 用 token→React 而非 HTML 字符串 | 从架构上根除 XSS，无需依赖运行时消毒库；实测 `<script>`、`javascript:` 链接、`<img onerror>` 全部降级为纯文本 |
| Agent 用「前置工具轮」而非自由 ReAct | 与现有 SSE 单流兼容；轮数/调用数/超时三上限使成本可预测；规划失败不影响主回答 |
| 配置 30s TTL 缓存 | 跨进程（api/worker）广播代价高；写入后主动 `invalidate()` 保证同进程立即生效 |

### 15.5 验证证据

| 项 | 结果 |
|---|---|
| 后端全量 `pytest` | **314 passed**（新增 `test_config_registry.py` 15、`test_config_store_resolver.py` 13、`test_llm_client_profile.py` 8、`test_system_settings_api.py` 13、`test_agent.py` 38、`test_chat_agent.py` 8） |
| 前端 `vitest` | **84 passed**（新增 `markdownTokens.test.ts` 21）；`npm run build` 通过 |
| 分场景配置生效 | 容器日志 `llm.call scenario=chat model=deepseek-v4.1-flash`；DB 改动为独立项，`extraction` 不受影响 |
| 密钥安全 | `PATCH` 写 `llm.chat.api_key` → **422**「密钥只允许来自环境变量」；界面显示 `••••••••（已配置）`，无明文；DB `system_settings` 无密钥行 |
| 配置持久化 | `system_settings` 表实测写入 `llm.chat.model = "gpt-4o-test-model"`；界面显示「已覆盖」 |
| 恢复默认 | 点击后回退为 `deepseek-v4.1-flash` 且徽标变「来自环境变量」，DB 行被删除 |
| 批量校验 | 一项非法（`agent.max_results=999`）→ 整体 422，合法项不入库 |
| URL 注入防护 | `agent.searxng_base_url = javascript:alert(1)` → 422 |
| Markdown 渲染 | 实测回答：4 个标题、2 个表格（12 行）、1 个列表、6 处加粗；`rawMarkdownLeaked=false` |
| XSS 防护 | `<script>` / `javascript:` 链接 / `<img onerror>` 全部不产生可执行节点 |
| Agent 启用链路 | SSE 事件序 `meta → status → delta → citations → done`；`done.agent.usedTools = ["web_search"]`、`toolErrors = []` |
| Agent 真实检索 | 回答中引用 `https://zh.wikipedia.org/wiki/高血压`，并诚实声明「检索到的外部资料未给出具体数值阈值」 |
| Agent 降级 | 搜索超时/坏 JSON：`usedTools=[]`、`toolErrors` 有记录、回答仍完整产出 |
| 红旗优先 | Agent 启用时 `redflag` 事件仍出现在流中，不受检索影响 |
| 浏览器界面 | 设置在 `/app/settings` 渲染 6 个分组 20 项；密钥仅显示掩码 |

### 15.6 已知边界

- DuckDuckGo 在有公网出口 IP 的环境下可能可用，但容器出口 IP 被反爬；当前默认走 Wikipedia。若需要更强的网页检索，建议部署 SearXNG 并在界面配置 `agent.searxng_base_url`。
- Wikipedia 结果适合概念/指标释义，不含最新指南原文；模型被要求在引用时说明局限性（实测已生效）。
- 配置无角色隔离：当前任意登录账户均可写配置。多租户上线前必须加权限位。
- `CONFIG_SECRET_KEY` 机制未启用（用户选择密钥只读），若未来要支持界面改 key 需新增加密存储与轮换流程。

### 15.7 下一步

- K1/K2 人工金标评测（唯一剩余 P0 门槛）。
- 配置写入权限收敛（角色体系）。
- SearXNG 部署示例与文档。

## 16. 问诊会话标题 LLM 概述（2026-10-22，需求补充）

### 16.1 需求

> 问诊会话标题应该在第一次对话后用 llm 生成极短的概述，方便历史会话查询。

### 16.2 现状问题

| 项 | 现状 | 问题 |
|---|---|---|
| 标题来源 | `orchestration.py` 中 `conversation.title = content[:20]` | 直接截断用户首句：①首句常是寒暄（"你好""帮我看看"），无信息量；②20 字硬截断常在半句处断开；③不含回答侧主题，历史列表难以检索 |
| 会话列表展示 | `_conv_out()` 返回 `title`，前端显示 `新会话 YYYY-MM-DDTHH:mm` | 标题为空时用时间兜底，多个会话难以区分 |

### 16.3 方案

| 决策 | 选择 | 理由 |
|---|---|---|
| 触发时机 | **首轮回答完成后**（`done` 之前），异步尽力而为 | 只有拿到回答才知道真实主题；且不阻塞首字输出 |
| 模型 | `extraction` profile（成本优先） | 标题生成是低难度任务，量大且短，用便宜模型即可（符合分场景配置初衷） |
| 长度 | 6–14 个汉字，上限 24 字符 | 侧边栏单行可读，超长截断 |
| 失败降级 | 保留原截断标题 | 标题不阻塞问诊，AI 失败必须可降级（AGENTS.md 第 4 条） |
| 幂等 | 已有 `title` 且非首轮截断时不覆盖 | 避免每轮重复调用模型烧成本 |
| 审计 | 不单独审计（属展示字段） | 避免审计噪声 |

### 16.4 实现

| 文件 | 改动 |
|---|---|
| `server/app/chat/titler.py`（新） | `generate_title(llm, question, answer) -> Optional[str]`，含清洗/长度约束/失败返回 None |
| `server/app/chat/orchestration.py` | 首轮回答后调用；仅在标题为截断初值时覆盖；写入后 commit |
| `server/app/db/models/chat.py` | 无结构变更（复用 `title` 字段） |
| `web/src/pages/ChatPage.tsx` | 无需改动（`convsQuery.refetch()` 已在 `done` 后执行，新标题自动回显） |

### 16.5 验证

| 项 | 判据 |
|---|---|
| 正常生成 | 首轮提问「你好，帮我看看血脂」→ 回答后标题变为语义化短标题（如「血脂异常咨询」），不再是"你好，帮我看看血脂"截断 |
| 失败降级 | 模型不可用/坏输出 → 标题仍为原截断值，问诊正常完成 |
| 幂等 | 第二轮提问不改变已生成的标题，不产生额外 LLM 调用 |
| 成本 | 仅首轮调用 1 次，`scenario=extraction` |

### 16.6 实施结果与额外修复的既存缺陷

| 项 | 结果 |
|---|---|
| 新增文件 | `server/app/chat/titler.py`（`clean_title` / `generate_title`）、`tests/unit/test_chat_titler.py`(21)、`tests/integration/test_chat_title.py`(5) |
| 改造文件 | `server/app/chat/orchestration.py`（首轮标题落库 + LLM 概述；`title_is_placeholder` 幂等判定） |
| **额外修复的既存缺陷** | 原代码 `conversation.title = content[:20]` **只赋值未持久化**（ORM 身份映射在多段 commit 后状态过期），导致首轮标题实际恒为 `NULL`，前端只能用时间兜底显示。改为 `db.execute(update(Conversation)...)` Core UPDATE 后 DB/详情/列表三处一致 |
| 实机验证（生成） | 首轮「你好，我最近体检发现血脂和血糖都有点偏高…」→ 标题 `血脂血糖偏高生活调整`（10 字，语义清晰，含主题+意图） |
| 实机验证（侧边栏） | 会话列表显示 `血脂血糖偏高生活调整`，不再显示「新会话 + 时间」 |
| 实机验证（幂等） | 第二轮提问后标题保持 `血脂血糖偏高生活调整`，未产生额外 LLM 调用 |
| 降级验证 | 模型异常 / 不可用 / 输出只有「会话」等空洞词 → 保留截断初值（前 20 字），问诊正常完成 |
| 成本 | 仅首轮 1 次调用，`scenario=extraction`（成本优先模型） |
| 测试基线 | 后端 **354 passed**（含新增 26 例）；问诊相关子集 46 passed |

## 17. 配置写入权限收敛（角色体系，2026-10-22）

### 17.1 问题

> 当前任意登录账户都能 `PATCH /api/settings/system`，即一个普通用户可以改掉**全站**的抽取模型、检索源与限额。

`system_settings` 是全局配置，无权限校验属于越权面（同类风险见 PRD K4 零串档要求）。

### 17.2 方案

| 项 | 决策 |
|---|---|
| 权限载体 | `accounts.role`（`admin` / `user`），默认 `user` |
| 首个账户 | 注册时若库中无账户 → 自动 `admin`，保证自托管开箱可配置 |
| 升级兼容 | 迁移 `0004` **回填最早创建账户为 admin**，避免升级后无人可写（自锁） |
| 策略开关 | `CONFIG_WRITE_POLICY`：`admin_only`（默认）/ `first_account` / `any` |
| 紧急授权 | `CONFIG_ADMIN_EMAILS` 邮箱白名单，优先级高于 role，便于无 admin 时恢复 |
| 失败安全 | 策略值非法 → **fail-closed** 按 `admin_only` 处理，绝不意外放开 |
| 读权限 | 保留登录可读（便于自查与报障），但 `editable` 明确返回 false |
| 写接口 | `PATCH` / `POST /reset` / `POST /test` 全部校验（`test` 会发起外部请求，防被当中继） |

### 17.3 实施

| 文件 | 改动 |
|---|---|
| `server/alembic/versions/0004_account_role.py`（新） | 加列 + 回填最早账户为 admin |
| `server/app/db/models/account.py` | `role` 字段（server_default `user`） |
| `server/app/config/permissions.py`（新） | `is_admin()` / `require_config_admin()` / `assign_role_for_new_account()` |
| `server/app/config/settings.py` | `config_write_policy`、`config_admin_emails` + 解析属性 |
| `server/app/config/router.py` | 三个写接口接入 `require_config_admin`；`GET` 返回真实 `editable` |
| `server/app/auth/service.py` | 注册时按需授予 admin；`/me` 输出 `role` |
| `web/src/components/SystemConfigPanel.tsx` | 按 `editable` 禁用输入/保存/测试/恢复，显示「只读（无管理权限）」 |
| `web/src/styles/global.scss` | `.config-readonly` 徽标样式 |
| `server/tests/api/test_config_permissions.py`（新） | 19 例 |

### 17.4 验证证据

| 项 | 结果 |
|---|---|
| 角色分配 | 首个注册账户 `role=admin`；后续账户 `role=user` |
| 迁移回填 | 生产库实测最早账户 `smoke_1792507260@…` 回填为 `admin`，其余 `user` |
| 非管理员写入 | `PATCH` → **403**「无权修改系统配置，请联系管理员」 |
| 非管理员 reset/test | 均 403 |
| 越权不落库 | 403 后再次读取，配置 `source != db`（确认未写入） |
| 非管理员可读 | `GET` 200，20 项可见，密钥仍掩码，`editable=false` |
| 管理员写入 | `PATCH` → 200，`updated=["chat.history_limit"]`，`editable=true` |
| 邮箱白名单 | 白名单命中（大小写不敏感）后可写，优先于 role |
| 策略非法值 | `CONFIG_WRITE_POLICY=banana` → 非管理员 403、admin 仍可写（fail-closed） |
| 前端只读 | 非管理员界面显示「只读（无管理权限）」，保存按钮 disabled，无「恢复默认」 |
| 前端可写 | 管理员界面无只读徽标，保存可用 |
| 测试基线 | 后端 **373 passed**（新增 19 例权限测试）；前端 84 passed + 构建通过 |

### 17.5 已知边界

- **尚无提升/降级管理员的 API 或界面**：当前只能改库（`UPDATE accounts SET role='admin'`）或用 `CONFIG_ADMIN_EMAILS`。多用户正式上线前需补「成员管理 → 角色」入口。
- 读权限对普通用户开放：能看到 `llm.chat.model` 等模型名（不含密钥）。若视为敏感，需增加读权限收敛。
- `role` 仅两级（admin/user），未做细粒度（如「只能改检索配置」）。

### 17.6 角色管理界面与配置变更通知（本轮补齐）

#### 17.6.1 新增接口

| 接口 | 权限 | 说明 |
|---|---|---|
| `GET /api/accounts` | admin | 账户列表（邮箱/昵称/角色/状态/注册时间），`adminCount`、`canManage` |
| `PATCH /api/accounts/{id}/role` | admin | 改角色；`{"role":"admin"\|"user"}` |
| `GET /api/accounts/config-notices` | 登录 | 最近配置变更通知；admin 看全部，普通用户只看自己的 |

#### 17.6.2 安全保护（代码侧，非前端）

| 保护 | 行为 | 验证 |
|---|---|---|
| 不能降低自己 | 自己 + 非 admin → 422「不能降低自己的权限，请让其他管理员操作」 | 实测 422 |
| 保留最后一名管理员 | 只剩 1 个 admin 时降级 → 422「系统至少需要保留一名管理员」 | 单测覆盖 |
| 非管理员越权 | 改他人角色 / 读账户列表 → 403 | 实测 403 |
| 幂等 | 角色未变 → `changed: false`，不写多余审计 | 单测覆盖 |
| 审计 | `account.role_change` 记录 before/after 角色 | 单测覆盖 |
| 敏感字段 | 账户列表不返回 `password_hash` | 单测断言 |

#### 17.6.3 配置变更通知

| 项 | 决策 |
|---|---|
| 存储 | **复用 `audit_logs`**，不新建表：通知与审计同源同生命周期，分表会造成双写不一致与清理策略分叉 |
| 高影响判定 | 前缀 `llm.` / `agent.` = 高影响（改模型/网关/检索直接影响全站） |
| 低影响过滤 | `chat.` / `upload.` / `privacy.` 不通知（纯展示类） |
| 未知前缀 | **保守保留**（宁可多通知，不可漏掉高影响变更） |
| 敏感值 | 通知只记 key 与变更者邮箱，**绝不记新旧值** |
| 失败处理 | 通知写入异常只记日志，不阻断配置保存（AGENTS.md 第 4 条） |
| 可见范围 | admin 看全部；普通用户只看自己产生的变更（不泄露他人操作历史） |
| 未来扩展 | `admin_recipients()` 已预留管理员列表查询，接入邮件/IM 时直接用 |

#### 17.6.4 新增文件

| 文件 | 职责 |
|---|---|
| `server/app/config/notify.py`（新） | `is_high_impact` / `classify_keys` / `recent_notices` / `notify_config_change` / `admin_recipients` |
| `server/app/config/accounts_router.py`（新） | 上述三个接口 |
| `server/tests/api/test_account_roles.py`（新） | 25 例 |
| `server/tests/unit/test_config_notify.py`（新） | 15 例 |
| `web/src/api/accounts.ts`（新） | API 封装 |
| `web/src/components/AccountAdminPanel.tsx`（新） | 角色表格 + 二次确认 + 通知列表 |
| `web/tests/accountRoles.test.ts`（新） | 10 例（契约字段、自锁判定、敏感值断言） |

#### 17.6.5 浏览器实测

| 场景 | 结果 |
|---|---|
| 账户列表 | 27 个账户、2 名管理员，角色徽标正确（管理员蓝 / 普通灰） |
| 提升角色 | 普通用户 → admin，HTTP 200，角色立即生效（该账户随后可写配置） |
| 自锁保护 | 自己降自己 → **422**「不能降低自己的权限，请让其他管理员操作」 |
| 配置通知 | 3 条记录，其中 2 条高影响标黄（`llm.chat.model` 的 update 与 reset） |
| 高影响标识 | `llm.*` 变更显示「高影响」徽标，`chat.*` 不显示 |
| 普通用户视图 | 只读徽标出现，账户表格不渲染，通知 scope=mine |

#### 17.6.6 测试基线

- 后端 **414 passed**（新增 40 例：角色 25 + 通知 15）
- 前端 **94 passed**（新增 10 例）；`npm run build` 通过
- 六容器健康

#### 17.6.7 剩余边界

- 通知仅界面可见，**未接邮件/IM 通道**（`admin_recipients()` 已预留）。
- 角色仍两级（admin/user），未做细粒度授权。
- 普通用户可读配置项（含模型名，不含密钥）；若视为敏感需另加读权限收敛。

## 18. 配置变更邮件通知（2026-10-22）

### 18.1 需求

> 配置变更需要通知管理员。

### 18.2 方案

| 决策 | 说明 |
|---|---|
| 通道 | SMTP（SSL 465 / STARTTLS 587 均支持） |
| 触发点 | `config.update`、`config.reset`、`account.role_change` 三类 |
| 发送方式 | **后台线程异步发送**，配置保存接口不等邮件完成（邮件服务器常秒级） |
| 失败处理 | 异常只记 `mail.failed` 日志并吞掉，**绝不阻断配置保存**（AGENTS.md 第 4 条） |
| 未配置时 | `smtp_configured()` 为假 → 记 `mail.skip reason=not_configured`，静默降级 |
| 收件人 | 显式 `SMTP_TO` 优先；为空时回退所有 `role=admin` 账户 |
| **密钥边界** | 密码/授权码**只能来自环境变量或 Secret**，绝不进源码、日志、邮件正文、测试快照 |
| 正文内容 | 仅含动作类型、key 列表、操作者邮箱、时间、高影响标记；**含"不包含任何配置值"声明** |

### 18.3 实施

| 文件 | 改动 |
|---|---|
| `server/app/config/settings.py` | `smtp_enabled` / `smtp_host` / `smtp_port` / `smtp_use_ssl` / `smtp_username` / `smtp_password` / `smtp_from` / `smtp_to` / `smtp_timeout_seconds` + `smtp_configured()` |
| `server/app/config/mail.py`（新） | `ConfigChangeMail`、`send_config_change_mail`、`notify_by_mail`、`resolve_recipients`；`_send_mail_impl` 可替换便于离线测试 |
| `server/app/config/router.py` | PATCH/reset 后调用 `notify_by_mail`（异步） |
| `server/app/config/accounts_router.py` | 角色变更后调用 `notify_by_mail`（高影响） |
| `server/tests/unit/test_config_mail.py`（新） | 16 例（含"正文不含密码"断言） |
| `.gitignore` | 新增 `docs/testinfo/`、`smtptestconfig.md`、`*.smtp.md` 防护 |

### 18.4 验证证据

| 项 | 结果 |
|---|---|
| 凭据注入 | `deploy/.env`（已被 `.gitignore` 忽略）新增 9 个 `SMTP_*` 变量，源码/文档零硬编码 |
| 容器配置 | `enabled=True`、`configured=True`、`host=smtp.qq.com`、`port=465`、`ssl=True`、`password_set=True` |
| 直接发送 | `mail.sent action=config.update highImpact=True recipients=1` —— QQ SMTP 接受投递 |
| API 触发 | 调用 `PATCH /api/settings/system` 改 `llm.chat.timeout_seconds` → 日志自动出现 `mail.sent ... highImpact=True` |
| 降级 | 未配置/无收件人 → `mail.skip reason=...`，接口仍 200 |
| 发送失败 | `_send_mail_impl` 抛异常 → 被吞并记 `mail.failed`，不影响配置保存 |
| 内容安全 | 单测断言正文不含 SMTP 密码；正文声明"不包含任何配置值或密钥内容" |
| 收件人回退 | `SMTP_TO` 为空时回退管理员邮箱（`resolve_recipients(db)`） |
| 测试基线 | 新增 16 例邮件单测；后端全量见 §18.5 |

- 后端：**430 passed**（0 failed / 0 errors，新增 16 例邮件测试）
- 前端：94 passed；构建通过
- 六容器健康

### 18.6 已知边界

- 邮件**无重试队列**：发送失败只记日志，不自动重发（避免引入队列复杂度；重要变更仍可在界面「最近配置变更」查到）。
- 未做收件人订阅管理界面：当前只能改 `SMTP_TO` 环境变量或依赖管理员角色。
- 邮件为纯文本（无 HTML 模板），优先保证可送达与不可伪造。

## 19. 问诊真流式输出 + 工具阶段动态反馈（2026-10-22）

### 19.1 需求

> 目前问诊界面不支持流式输出，需要等待很久，需要优化并测试。在检测资料、搜索等调用工具的过程中也可以展示一些动态防止用户等待不耐烦。

### 19.2 根因（先定位再改）

| 现象 | 根因 |
|---|---|
| "不支持流式" | `LLMClient.chat()` 是**阻塞调用**，拿到完整回答后才用 `_chunks()` 按 60 字切块**瞬间**发完 → 假流式 |
| 首字等待 6–22s | ①Agent 规划用 chat 模型做低难度判断（实测 6s）②搜索两源**串行**（实测 12.6s）③等待期无任何 SSE 事件 |
| 工具阶段空白 | 工具执行期间只发一个笼统 status，8s 无变化 |

**关键判断**：不是 SSE 做错了（前端 `parseSseBlock` + `ReadableStream` 本就正确），而是**上游没有流**。

### 19.3 方案与实施

| # | 改动 | 文件 |
|---|---|---|
| 1 | `LLMClient.chat_stream()`：原生 `stream=True` 逐 token yield，流开始后不重试（避免重复内容） | `app/llm/client.py` |
| 2 | `CitationBuffer`：引用标记**整段缓冲**判定，保证不变量「已放行内容永不改动」 | `app/chat/stream_utils.py`(新) |
| 3 | 编排改真流式：边收边发 delta；流结束后再跑一次 `extract_citations` 保证落库与展示一致 | `app/chat/orchestration.py` |
| 4 | `_iter_tokens_with_heartbeat()`：LLM 流放工作线程，主线程按超时产出心跳（解决"等待期无事件"） | 同上 |
| 5 | 阶段事件细分：`thinking → planning → tool_running → tool_done → generating → heartbeat` | 同上 |
| 6 | **规则化规划** `rule_based_plan()`：命中明确意图时不调模型（省 ~6s） | `app/agent/planner.py` |
| 7 | 检索词提炼 `_build_query()`：整句会让检索源返回 0 条 | 同上 |
| 8 | 搜索**并行化**：多源并发（串行 → 总时长累加；并行 → 最慢单源） | `app/agent/search.py` |
| 9 | 多语言 Wikipedia 并行 | 同上 |
| 10 | 流式 0-chunk **降级**：用非流式重试一次，`streamed=false` 标注 | `app/chat/orchestration.py` |
| 11 | 工具超时默认 8s → **5s** | `app/config/registry.py` |
| 12 | 前端 `chatStatus.ts`：阶段 → 图标/文案/优先级/首字提示映射（纯函数） | `web/src/components/chatStatus.ts`(新) |
| 13 | `StageIndicator`：图标 + 标签 + 三点跳动 + 已等待计时 + 超时安抚文案 | `web/src/components/StageIndicator.tsx`(新) |
| 14 | 打字光标跟随输出末尾 | `web/src/pages/ChatPage.tsx` |

### 19.4 顺带修复的真实缺陷

| 缺陷 | 根因 | 修复 |
|---|---|---|
| **Markdown 链接吞掉后续全部正文** | `marked` 的 GFM autolink 贪婪匹配：`见 https://x，然后继续说明` → 整段变成 `href`，URL 之后的加粗与正文**全部消失** | lexer 前预处理把裸 URL 转成 `<url>` 显式边界；链接内部**不再二次提取 URL**（避免嵌套 link 数量翻倍） |

### 19.5 性能实测对比

| 指标 | 改造前 | 改造后 | 改善 |
|---|---|---|---|
| 检索耗时 | 12.6s（串行双源） | **0.7–1.3s**（并行） | ~10–18x |
| Agent 规划 | 5981ms（chat 模型） | **0ms**（规则命中）/ extraction 兜底 | 消除 |
| 总耗时（含检索） | 31.4s | **9.7–19.4s** | ~40–70% |
| delta 数量 | ~1 批（瞬间） | **469–1230 个** | 真流式 |
| 等待期事件 | 无 | 每 4s heartbeat | 消除"卡死"感 |

### 19.6 浏览器验收（实测）

| 项 | 结果 |
|---|---|
| 阶段指示器 | ✅ 依次显示 🧠分析 → 🔎检索(带计时 1s/2s/3s) → ✍️生成 → ⏳仍在生成(已等待 4s) |
| 超时安抚 | ✅ 8s「首次回答需要准备资料，请稍候」；15s「比平时慢一些，仍在处理，请再稍等…」 |
| 打字光标 | ✅ 输出期间可见（`sawCaret=true`） |
| 渐进输出 | ✅ 22 次长度增量（12→31→240→792→1470→1530） |
| Markdown 渲染 | ✅ 9 段落 / 4 标题 / 9 列表 / 28 加粗 / `rawStars=false` |
| 链接不吞正文 | ✅ 最长链接文本 48 字符，后续正文完整 |

### 19.7 测试基线

- 新增后端测试：`test_citation_buffer.py`(25)、`test_chat_streaming.py`(15)、`test_stream_heartbeat.py`(7)、`test_planner_rules.py`(19)、`test_planner_query_quality.py`(10)、`test_search_parallel.py`(6)
- 新增前端测试：`chatStatus.test.ts`(19)、`markdownTokens.test.ts` 新增裸 URL 回归 7 例（共 28）
- **后端全量 512 passed（0 failed / 0 errors）**；前端全量 **120 passed**；构建通过

### 19.8 已知边界

- 首 token 延迟仍受网关波动影响（实测 3.5–8.9s），已用心跳与安抚文案缓解；如需进一步降低需更换更快模型或减少上下文。
- 检索依赖外部网络，容器 DNS 偶发失败（`URLError`）时降级为"未获取外部资料"，不影响回答。
- 未做 SSE 断线重连（写操作重放会重复落库）；流派发中断时保留已生成内容并落库。

---

## 20. 陆满纶检查单标注（38 份 · 视觉核对版）

### 20.1 目标

对 `docs/testinfo/检查单/陆满纶/` 下全部 38 份检查单逐份完成检查项标注，字段口径遵循
`evals/README.md` 与 `evals/scripts/score_kpi.py`：name（报告原始名称）、value（照抄数字）、
unit（字面一致）、date（YYYY-MM-DD）、abnormal（报告标 ↑↓ 或超参考区间即 true）；
PDF 按内容重命名，标注文件与报告文件名一一对应。

### 20.2 变更文件

| 路径 | 说明 |
|---|---|
| `docs/testinfo/检查单/陆满纶/*.pdf` | 38 份，按「日期+检查类型」重命名（原 `1792661146608.pdf` 等） |
| `docs/testinfo/检查单/陆满纶/*.json` | 38 份同名标注，与 PDF 一一对应 |
| `evals/dataset/kpi_k1k2/annotations.jsonl` | 38 行标准标注，K1/K2 评测用 |
| `evals/dataset/kpi_k1k2/files/` | 38 份 PDF 副本 |

### 20.3 方法与本轮修正

初版用 `pdfplumber` 文本层解析生成字段；按「优先用视觉输入、降低错误率」的路线，
将 38 份 PDF 全部渲染为 PNG（`pypdfium2`, scale=2.0）并裁剪表格区，**逐张人工视觉转录并比对**，
修正了 3 处偏差：

| 报告 | 问题 | 修正 |
|---|---|---|
| 20260530粪便常规 | 漏标「脂肪球」（该行结果列/参考列被仪器错位显示） | 补标 脂肪球=未见 个/HP abnormal=false，notes 注明错位与校正依据 |
| 20260904乙肝五项及感染筛查 | 漏标「乙肝表面抗体」 | 补标 乙肝表面抗体=阴性/2.00 IU/L abnormal=false |
| 20260529血气分析 | pO2 32.00、Hct 34.00、BEecf -6.30、BE(B) -5.70、SO2c 58.00 未判异常 | 按「超参考区间即异常」口径 5 项改判 true（报告参考区间采用动脉血标准，标本为静脉全血，notes 已注明） |

最终：**38 份报告 / 446 个字段 / 59 个异常**。

### 20.4 验证

```
python evals/scripts/score_kpi.py selftest   → selftest OK ✔
```
- annotations.jsonl：38 行、字段 446、异常 59，`files/` 引用全部存在
- 目录 38 份 JSON 与 annotations.jsonl 逐份逐字段**完全一致**（键集合与内容比对均无差异）
- 字段 schema 校验通过：仅含 name/value/unit/date/abnormal 五键，date 均为 YYYY-MM-DD

### 20.5 已知边界

- 血气分析报告为静脉全血标本但参考区间采用动脉血标准，pO2/SO2c/Hct 的「异常」判定依据的是
  报告自身参考区间，临床解读需结合标本类型（已在 notes 说明）。
- 影像/超声报告（DR 5 份、彩超 2 份）无文本层，为扫描件，结论以视觉转录的影像诊断/超声诊断原文标注。

---

## 21. 满荨健康体检报告标注（mx · 视觉转录版）

### 21.1 目标

对 `docs/testinfo/检查单/mx/满荨_20250228.pdf`（南通市第一人民医院健康管理中心健康体检报告，
23 页正文 / PDF 24 页）做单文件检查项标注，字段口径与第 20 节一致。

### 21.2 变更文件

| 路径 | 说明 |
|---|---|
| `docs/testinfo/检查单/mx/满荨_20250228.json` | 新增标注：164 字段 / 9 异常 |

### 21.3 方法

按「优先用视觉输入」路线：用 `pypdfium2`（scale=2.0）将全部 24 页渲染为 PNG，
**逐页人工视觉转录**，未使用任何文本/坐标解析工具。对分页处易错区域做局部放大复核
（HPV 59 型跨页、彩超胆/脾/胰/肾、CT 全文）。

覆盖 26 个检查项目：一般检查、内科、外科、眼科、口腔科、耳鼻喉、妇科、病理、彩超、
CT、血常规、尿常规、血脂四项、空腹血糖、HPV核酸(23型)、AFP+CEA、肝功能全套、CA199、
乙肝两对半、EB病毒、CA125+CA153、白带常规、甲功三项、肾功能四项、NSE、常规心电图。

### 21.4 异常项（9 个）

体重指数 25（超重）、阻生齿、鼻炎、慢性扁桃体炎、乳腺彩超低回声、双肾结晶、
子宫内膜增厚及子宫直肠窝积液、两肺多发实性结节及左侧肾上腺结节、窦性心律ST段改变。
血常规、尿常规、血糖、血脂、肝功能、肾功能、肿瘤标志物、甲功等有参考范围者结果均在范围内。

### 21.5 口径说明（已写入 notes）

- 体检各科表仅「检查结果」列、无单位列；一般检查单位依报告自身表述（身高 m、体重 kg）
  与常规计量单位（cm、mmHg、次/分、kg/m²）填写。
- HPV 核酸测定检查时间为 2025-03-04，其余项目为 2025-02-28。
- 病理科检查时间原报告打印为 0001-01-01（无效），按体检日期记录。
- 报告内「病史」「其他」「胰」在不同科室重复出现，按「用报告上的原始名称」保留原名。

### 21.6 验证

- schema 校验通过：164 字段均含 name/value/unit/date/abnormal 五键，date 均为 YYYY-MM-DD

## 20. K1/K2 标注体检与预处理（2026-10-22）

### 20.1 用户提供的真实样本

| 项 | 数量 |
|---|---|
| 真实检查单文件 | 38 份（`陆满纶` 32 + `mx` 2 + `lbb` 2 + `zmf` 2 已归入） |
| 标注行 | 38 行（`evals/dataset/kpi_k1k2/annotations.jsonl`） |
| 标注字段总数 | **446** |
| 其中 abnormal=true | 59 |

### 20.2 体检结论：格式零错误，但有一处口径问题

| 检查项 | 结果 |
|---|---|
| JSONL 逐行可解析 | ✅ 0 错误 |
| 必需键齐全（file/subject/fields） | ✅ 0 错误 |
| 样本文件存在性 | ✅ 38/38 全部存在 |
| 标注与 files/ 一一对应 | ✅ 38 = 38，无孤儿无漏标 |
| 日期格式 `YYYY-MM-DD` | ✅ 全部合法 |
| `abnormal` 为布尔 | ✅ 全部合法 |
| **重复字段名** | ✅ 0 处 |
| **⚠️ 口径问题** | **164 项（37%）不是数值型化验指标**，直接计入会污染 K1/K2 分母 |

**三类内容混在同一批标注里**：

| 类别 | 数量 | 举例 | 是否应计入 K1/K2 |
|---|---|---|---|
| `primary` 常规数值化验 | **282** | 白细胞 6.8×10^9/L、总胆红素 9.5 μmol/L | ✅ 应计入 |
| `qualitative` 定性检验 | **155** | 核酸"未检出"、尿常规"阴性"、大便"黄色/软便" | ⚠️ 需你确认口径（建议：单独统计，不计入 K1/K2） |
| `excluded` 影像描述 | **9** | "影像诊断=支气管肺炎"、"腹腔积液范围=38*34*26" | ❌ 不应计入 |

### 20.3 已完成的自动化预处理

新增两个脚本（可重复运行，幂等）：

| 脚本 | 职责 |
|---|---|
| `evals/scripts/check_annotations.py` | 标注体检：10 类检查项，输出 error/warn/info 分级 |
| `evals/scripts/preprocess_annotations.py` | 确定性修正 + 分类：日期规范化、单位回填、abnormal 补全、全角转半角、`kpiClass` 标注 |

**预处理执行结果**：

| 项 | 值 |
|---|---|
| 单位回填 | **20 处**（血小板比容/红细胞比容 → `%`，10 份血常规各 2 处） |
| 数值规范化 | 1 处（全角字符） |
| 日期修正 | 0（你的格式本来就统一） |
| abnormal 补全 | 0（你的标注很完整） |
| 重复字段名 | 0 |

**产出文件**：
- `evals/dataset/kpi_k1k2/annotations.clean.jsonl`（带 `kpiClass` 分类，可直接跑分）
- `evals/dataset/kpi_k1k2/annotations_report.md`（逐文件修正明细）

### 20.4 原样本质量问题（非格式错误，但影响判分）

| 问题 | 影响 | 处理建议 |
|---|---|---|
| 影像报告 9 项混入 | 影像无"数值+单位+日期"三要素，必然判错 | 预处理已标 `excluded`，跑分时排除 |
| 定性检验 155 项 | 单位天然为空，`unit` 比对必然失败 | 需确认口径：单独统计 or 排除 |
| 12 份"痰核酸/细菌核酸"内容高度重复 | 同一份报告被标注两次（不同日期），会放大该类型权重 | 可接受（真实存在两次检查），但需知其权重 |
| PDF 多为 1–3 页 | 适合走"少页直转图"视觉路径（正好验证 §21） | — |

### 20.5 下一步

1. 用 `annotations.clean.jsonl` + `kpiClass=primary` 跑首轮 K1/K2（需先上传样本拿到 predictions）
2. 按结果反查抽取缺陷
3. 结合 §21 的视觉优先改造再做第二轮对比

## 21. LLM 视觉优先抽取 + 读图 Token 优化 + 扩展 LLM 配置（设计完成，待实施）

> 完整方案见 `docs/superpowers/plans/2026-10-22-vision-first-and-llm-config.md`

### 21.1 三项需求与结论

| 需求 | 结论 |
|---|---|
| LLM 可用时优先视觉识别 | 解析链改为**策略化**：新增 `extract.parser_strategy`（默认 `vlm_first`），VLM 从"兜底"升级为"主路径"，表格解析退为失败后兜底 |
| 少页 PDF 转图走视觉 | `extract.vlm_always_pdf_max_pages`（默认 6）：页数 ≤ N 的 PDF 直接渲染走视觉，跳过表格解析 |
| 读图 Token 优化 | 三层：**近无损预压缩**（PNG/JPEG q≥92）+ **多页拼图**（N 页 → ⌈N/k⌉ 张）+ **分辨率自适应**（按 `detail` 与文字密度）。预期降 50–80% |
| LLM 配置扩展 | 新增 `max_context_tokens`(默认 256k) / `max_output_tokens`(默认 32k) / `vision.*`（能力检测、detail、拼图、压缩参数） |

### 21.2 关键设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 默认策略 | `vlm_first` | 用户明确要求准确率优先；表格解析对扫描件/拍照件不可靠 |
| 压缩格式 | 优先 PNG 无损，超限才 JPEG q≥92 | 用户要求"几乎无损"；q75 会让小字模糊 |
| 拼图上限 | 单边 ≤ `vision.tile_max_side`(2200px) | 像素面积决定 token，必须有硬上限 |
| 视觉能力检测 | 发 64×64 纯色小图问"什么颜色" | `/models` 不标注视觉能力；此方案约 100–200 token，成本极低 |
| Token 预算 | 请求前估算，超限**降分辨率**而非报错 | 大报告不能直接失败 |
| 回滚 | `extract.parser_strategy=table_first` 一条配置回到现状 | 破坏性最小 |

### 21.3 待确认（实施前需回答）

1. 定性检验 155 项是否计入 K1/K2？
2. `vlm_first` 默认接受成本 ×1.5–2 吗？
3. 拼图默认开启还是先关闭保守上线？

## 22. K1 基线反查：指标字典五分类缺陷修复（2026-10-22）

### 22.1 真实缺陷（有证据）

首轮 K1/K2 基线（38 份真实检查单，`--classes primary`）：

| 指标 | 结果 | 门槛 | 判定 |
|---|---|---|---|
| K1 字段准确率 | 149/282 = **52.84%** | ≥90% | NO-GO |
| K2 异常召回 | 25/37 = **67.57%** | ≥95% | NO-GO |
| 定性项命中（参考） | 85/155 = 54.84% | — | 不进 KPI |

Misses 分布：未抽取到 112、单位不符 14、数值+单位都不符 5、数值不符 2。

反查 `20250126血常规.pdf`（22 项）发现**6 项 `display_name` 标错**：
报告写裸名「中性粒细胞」+ 单位 `10^9/L`（值 2.22），系统标成
「中性粒细胞百分比」。根因：

1. `indicator_dictionary.py` 只有 `NEUT_PCT`/`LYMPH_PCT`/`MONO_PCT`/`EOS_PCT`/`BASO_PCT`，
   **没有计数组**指标；
2. 这些 PCT 条目带了裸别名「中性粒」「淋巴细胞」，导致计数项被错配。

这直接违反 AGENTS.md「AI 底座原则」第 8 条（安全阀：宁可新建，不可错并）。

### 22.2 修复内容

| 文件 | 变更 |
|---|---|
| `server/app/data/indicator_dictionary.py` | 五分类拆成 **10 条**（5×PCT + 5×CNT），去掉裸歧义别名，改用显式 `%` / `#` 写法 |
| `server/app/preprocessing/mapper.py` | 新增 `_cell_diff_key()` / `_unit_kind()` / `_resolve_cell_diff_family()`：**裸五分类名按单位消歧**（`%`→PCT，`10^9/L`→CNT）；单位判不出时**拒绝猜测**，回落 AI 对齐 |
| `server/tests/unit/test_indicator_alignment.py` | 新增 16 个用例：单位消歧 11 组参数化 + 回归防护 2 + 未知单位不猜 1 |
| `evals/scripts/score_kpi.py` | `names_match()` 增加 `a_unit`/`b_unit`：裸名（标注常见写法）用**单位补判 kind**，堵住「裸名绕过安全阀」的漏配口子；`selftest()` 增加 5 条裸名断言 |

关键实现细节（踩坑记录）：
- `normalize_indicator_name()` 的 `_PUNCT` 会剥掉 `%`/`#`，所以 **kind 标记必须在原始名上匹配**，
  不能在归一化后的名字上找。
- 判「五分类裸名」的基名表要同时覆盖「嗜酸性细胞」与「嗜酸性粒细胞」两种写法。

### 22.3 验证

```
cd server; ..\.venv\Scripts\python.exe -m pytest tests\unit\test_indicator_alignment.py -q
# 34 passed
$env:PYTHONIOENCODING='utf-8'
python evals\scripts\score_kpi.py selftest
# selftest OK
```

### 22.4 下一步

1. 重新触发受影响的血常规文档抽取，重跑 K1/K2，与 **52.84% / 67.57%** 对比
2. 针对 112 项「未抽取到」补别名/补字典（生化、血气、尿常规）
### 22.5 全量回归暴露的第二个真实缺陷（已修）

全量跑出 `1 failed, 589 passed`，失败用例
`tests/integration/test_merge_pipeline.py::test_table_wins_when_visual_also_succeeds`。

**这不是过时测试，而是掩盖了一个真实数据丢失缺陷**：

- 该用例锁的是 `table_first` 语义（表格为基准），默认策略改成 `vlm_first` 后前提变了；
- 顺着查 `merger.merge_payloads()` 发现：同名同单位冲突时"保留 primary"，
  但 **primary 的 `value` 为空时不会用 secondary 的数值补上**。
  在 `vlm_first` 下就是「VLM 漏读 → 表格已读到的正确数值被永久丢弃」，
  正是历史 2021_0907.pdf「只有名字、没有数值」的同一类缺陷。

修复：

| 文件 | 变更 |
|---|---|
| `server/app/preprocessing/merger.py` | 新增 `_is_blank_value()`；同键合并时若 primary 值为空而 secondary 有值，**补上并记 `value_filled_from`**（可审计）。`0`/`0.0` 视为有值，不被覆盖 |
| `server/tests/integration/test_merge_pipeline.py` | 原用例改用 `monkeypatch` **显式声明 `extract.parser_strategy=table_first`**（验证回滚策略，不依赖默认值） |
| 同上 | 新增 `test_merge_fills_blank_value_from_secondary`：锁住"空值补位 + 0 不被覆盖 + 留痕" |

验证：`pytest tests/integration/test_merge_pipeline.py -q` → **2 passed**
### 22.6 最终验证（当前树，新鲜证据）

```
cd server
..\.venv\Scripts\python.exe -m pytest -q --tb=short -p no:cacheprovider
# 591 passed, 285 warnings in 2046.97s (0:34:06)     EXIT=0

..\.venv\Scripts\python.exe -m pytest tests\unit\test_indicator_alignment.py -q
# 34 passed

$env:PYTHONIOENCODING='utf-8'
python evals\scripts\score_kpi.py selftest
# selftest OK
```

基线（修复前）：`512 passed`。当前 `591 passed`（+79 来自本轮新增
策略/图像/视觉探针/字典消歧/合并且兜底测试），**0 failed**。

### 22.7 本轮结论与遗留

**已完成**：两个真实缺陷均已修（五分类字典分组缺失、融合丢数值），并有回归测试锁定。

**仍未解决的 K1 主要瓶颈**：112 项「未抽取到该指标」。这不是字典别名问题，
而是**抽取覆盖率**问题（生化/血气/尿常规项未进入 payload），属于下一批工作。

**注意**：字典改动需重启 api/worker 触发幂等 upsert 才会写入数据库；
已有 `display_name` 错标的历史记录**不会自动纠正**，需重新抽取。
## 23. 真实环境验证暴露的第三个缺陷：重新抽取被降级（2026-10-23）

### 23.1 验证过程与关键证据

按计划重建镜像（**注意：compose 未挂载源码，`restart` 只重启旧镜像，必须
`build` + `up -d --force-recreate`**）：

```
docker compose -f deploy/docker-compose.yaml build api worker
docker compose -f deploy/docker-compose.yaml up -d --force-recreate api worker
# 日志：seed_dictionary done: total=63 created=5 updated=58   ← 5 条 CNT 落库
```

字典确认落库（10 条分族 + 裸歧义别名已移除）：

```
NEUT_PCT 中性粒细胞百分比 %      中性粒细胞%|中性细胞%|NEUT%|...
NEUT_CNT 中性粒细胞计数   10^9/L 中性粒细胞#|中性粒细胞计数|NEUT#|NEUT
（LYMPH/MONO/EOS/BASO 同构）
```

**修复前**该文档草稿 22 项里的 5 处错配（真实证据）：

```
中性粒细胞百分比 | 2.22 | 10^9/L | NEUT_PCT   ← 计数被标成百分比
淋巴细胞百分比   | 4.31 | 10^9/L | LYMPH_PCT
单核细胞百分比   | 0.26 | 10^9/L | MONO_PCT
嗜酸性细胞百分比 | 0.47 | 10^9/L | EOS_PCT
嗜碱性粒细胞计数 | 0.01 | 10^9/L | BASO_ABS   ← AI 另建了非标准 code
```

随后触发 `reextract`，得到的结果**反而更差**：出现重复观测，且单位变成
`13` / `71` / `0.5` / `35` / `11.6` / `0.32` 这类垃圾值，计数项**仍然**是 `_PCT`。

### 23.2 根因（三个独立问题）

| # | 问题 | 证据 |
|---|---|---|
| 1 | **`prefer_rule` 覆盖了 `vlm_first`** | worker 日志 `strategy=vlm_first primary=table ... vlm=True`，最终 `parser=ocr`。`reextract` 把 `job.attempt` 置 1 → `prefer_rule=True` → `_ordered_names` 直接返回 `_ORDER_PREFER_RULE`（table 打头），VLM 被跳过 |
| 2 | **OCR 串列** | 走 OCR 后列对齐错乱，相邻列的参考区间被读成单位（`unit=13`、`71`、`0.5`），产生重复观测 |
| 3 | **准入规则放过语义噪声** | `align.created code=REPORT_PAGE_TEXT name=报告页码文本`：`is_plausible_indicator_name` 的兜底规则「2~8 个汉字即可」把页脚描述当成新指标建进字典 |

问题 1 是**本轮改动引入的交互缺陷**（`prefer_rule` 是旧逻辑，`vlm_first` 是新默认），
属于回归，必须先修；问题 2/3 是既有缺陷，本轮记录待修。

### 23.3 修复（问题 1）

| 文件 | 变更 |
|---|---|
| `server/app/preprocessing/parsers/__init__.py` | `_ordered_names()`：`vlm_first` + `vlm_available` 时**忽略** `prefer_rule`，仍以 VLM 为首；`prefer_rule` 只把确定性链路追加到其后。其它策略行为不变 |
| `server/tests/unit/test_parser_strategy.py` | 新增 `TestPreferRuleDoesNotOverpowerVlmFirst` 3 例（VLM 优先 / VLM 不可用降级 / table_first 不变），用 monkeypatch 注入可用性避免依赖环境 |

语义说明：`prefer_rule` 的原意是「上次失败过，避免反复撞同一个 LLM 故障」。
但它不该覆盖用户在配置里显式选择的 `vlm_first`——用户点"重新抽取"的预期是
"换更好的方式重读"，而不是悄悄降级到确定性解析。

验证：`pytest tests/unit/test_parser_strategy.py -q` → **27 passed**

### 23.4 待修（下一批）

1. **问题 3**：`is_plausible_indicator_name` 需拦截「报告页码文本」这类语义噪声
   （页码/共N页/报告说明/打印时间/审核者等描述性短语），不能只靠"2~8 汉字"兜底
2. **问题 2**：OCR 路径需做列对齐校验——单位字段不在**单位白名单**内时，
   应判为解析失败并丢弃该字段，而不是当成 `unit=13` 落库
3. 重复观测：`reextract` 后旧草稿观测未清理干净，需核查 `derived-records`
   覆盖写逻辑与 `uq_obs_doc_field` 约束的配合
## 24. 五分类分族修复的端到端验证与三个追加缺陷（2026-10-23）

### 24.1 验证方式与部署坑

统一按「改完 → `build` → `up -d --force-recreate` → 重抽 → 查草稿」验证。
**注意：compose 未挂载源码，`restart` 只是重启旧镜像，必须 `build`。**

### 24.2 追加发现的三个真实缺陷

| # | 缺陷 | 证据 |
|---|---|---|
| 4 | `%`/`#` 后缀被归一化剥掉 | `normalize_indicator_name` 的 `_PUNCT` 会把 `%`/`#` 去掉，于是「嗜酸性粒细胞%」→ `嗜酸性粒细胞`，与别名「嗜酸性粒细胞百分比」不相等，精确匹配失败，最终被 AI 另建成 `EOS_ABS`。实测 2 项复现 |
| 5 | 历史遗留 `*_ABS` 抢走命中 | 早期字典没有五分类计数组时，AI 建了 `NEUT_ABS`/`LYMPH_ABS`/`MONO_ABS`/`EOS_ABS`/`BASO_ABS`，别名与 `*_CNT` **完全重复**（`中性粒细胞#`、`中性粒细胞计数`）。「中性粒细胞#」精确命中的是旧 `NEUT_ABS` 而非 `NEUT_CNT` —— 违反 AGENTS.md 第 2 条「同一实体不得变成多个」 |
| 6 | 元信息被建成指标（上轮已修，本轮验证） | `align.created code=REPORT_PAGE_TEXT name=报告页码文本` |

### 24.3 修复

| 文件 | 变更 |
|---|---|
| `server/app/preprocessing/mapper.py` | 新增 `_norm_with_kind()`：归一化前把 `%`/`#` 后缀转成「百分比」/「计数」，保住分族语义；`_DictEntry` 索引与 `match_indicator` 都改用它 |
| 同上 | 新增 `_CANONICAL_CODE_RE` / `_is_canonical_cell_diff()` 与 `_pick()`：同名多条目命中时**规范 code（`*_PCT`/`*_CNT`）优先**，旧 `*_ABS` 让位 |
| `server/app/data/indicator_dictionary.py` | `EOS_PCT`/`BASO_PCT` 别名补 `嗜酸性粒细胞%` / `嗜碱性粒细胞%` / `嗜酸细胞%` / `嗜碱细胞%` 等显式写法 |
| `server/app/preprocessing/indicator_aligner.py` | 新增 `_PAGE_META_EXACT` / `_PAGE_META_HEAD` / `_META_TAIL` 与 `_is_page_meta()`：拦截页码/打印/审核/检验者/复核/送检/采集/申请/科室/病区/床号/住院号/门诊号/标本类型等**页面元信息**，含「报告页码文本」这类「元信息词 + 描述性尾巴」组合。`is_noise_text` 与 `is_plausible_indicator_name` 共用 |
| `server/app/preprocessing/parsers/__init__.py` | `_ordered_names()`：`vlm_first` + VLM 可用时**忽略 `prefer_rule`**，VLM 仍为首（修 §23 的回归） |

### 24.4 端到端验证结果（真实环境，version=5）

`pipeline.strategy strategy=vlm_first primary=vlm extract... parser=vlm version=5 obs=22`

五分类 **10/10 全对**（修复前 5 项错标 + 2 项 `_ABS`）：

```
中性粒细胞      → 中性粒细胞计数   2.22  10^9/L  NEUT_CNT
中性粒细胞%     → 中性粒细胞百分比  30.5  %       NEUT_PCT
淋巴细胞       → 淋巴细胞计数     4.31  10^9/L  LYMPH_CNT
淋巴细胞%      → 淋巴细胞百分比    59.3  %       LYMPH_PCT
单核细胞       → 单核细胞计数     0.26  10^9/L  MONO_CNT
单核细胞%      → 单核细胞百分比    3.6   %       MONO_PCT
嗜酸性粒细胞    → 嗜酸性粒细胞计数  0.47  10^9/L  EOS_CNT
嗜酸性粒细胞%   → 嗜酸性细胞百分比  6.5   %       EOS_PCT
嗜碱性粒细胞    → 嗜碱性粒细胞计数  0.01  10^9/L  BASO_CNT
嗜碱性粒细胞%   → 嗜碱性细胞百分比  0.1   %       BASO_PCT
```

守卫指标：`ABS_CODES_LEFT=0`、`META_LEAKED=0`。

### 24.5 单测

```
pytest tests/unit/test_indicator_alignment.py -q     # 49 passed
pytest tests/unit/test_parser_strategy.py -q         # 27 passed
pytest tests/unit/test_dictionary_noise_filter.py \
       tests/unit/test_noise_alias_hijack.py -q      # 10 passed
```

### 24.6 仍未处理

1. **数据迁移**：`*_ABS` 与 `REPORT_PAGE_TEXT` 仍在库中（`active=True`）。目前靠「规范 code 优先」+ 噪声过滤兜住，但**应做正式迁移**：把 `*_ABS` 的观测/别名并入 `*_CNT`，再 `active=False`（保留审计，不物理删除）
2. **OCR 串列**：非法单位（`13`/`71`/`0.5`）仍可能落库，需加单位白名单校验
3. **重抽重复观测**：`reextract` 后新旧草稿叠加，需核查覆盖写逻辑
### 24.7 全量回归（当前树，新鲜证据）

```
cd server
..\.venv\Scripts\python.exe -m pytest -q --tb=short -p no:cacheprovider
# 612 passed, 285 warnings in 669.83s (0:11:09)     EXIT=0
```

历史基线：`512`（§22 前）→ `591`（§22）→ **`612`**（本轮，+21）。
全程 **0 failed**，之前 §23 那个 `prefer_rule` 回归已被修复并锁测。

### 24.8 本轮小结

**修复 3 个真实缺陷**（`%`/`#` 后缀被剥、`*_ABS` 抢命中、元信息进字典）+ **1 个自造回归**
（`prefer_rule` 覆盖 `vlm_first`），全部有单测 + 真实环境端到端证据。

**K1 影响预估**：单份 22 项血常规里，11 项 `display_name` 错标已全部纠正，
该文件 K1 从 ~11/22 提升到 22/22（未重新跑全量 38 份评分，见待办）。
## 25. 主数据迁移：合并 `*_ABS` → `*_CNT` 并停用页面元信息（2026-10-23）

### 25.1 目标

把「同一实体在多次导入后变成多个」的历史污染**从数据层**拉回一致，
而不是只靠代码兜住（AGENTS.md 第 2 条）。

### 25.2 迁移 0005：合并 + 停用

| 动作 | 说明 |
|---|---|
| 观测改指 | `*_ABS` → 同族 `*_CNT`（值/单位/参考区间原样保留，语义等价） |
| 参考区间改指 | 同步改指；若规范条目已有同 version 区间，删重复的旧区间后再改，避免撞唯一约束 |
| 别名合并 | `*_ABS` 上不重复的别名（如「…绝对值」）并入 `*_CNT` |
| 停用 | `*_ABS` 与 `REPORT_PAGE_TEXT` 置 `active=false`，**保留行供审计**（不物理删除） |
| 幂等 | 目标 `*_CNT` 不存在时**跳过**，不制造"观测指向不存在字典"的半个状态 |

可移植性：查询用 `IN (:c0,:c1,…)` 展开而非 `= ANY(array)`；
`aliases` 绑定值按方言返回 list（PostgreSQL 数组）或 JSON 文本（SQLite）。

### 25.3 执行中暴露的第 7 个缺陷：seed 覆盖了迁移成果

迁移 0005 后核对发现：`_ABS` 全部 `active=false`、观测全部改指成功，
**但别名没合并进 `*_CNT`**。

根因在 `entrypoint.sh` 的时序：

```
alembic upgrade head     # 0005 合并别名
python scripts/seed_dictionary.py   # upsert 用 TOP50 的 aliases **覆盖整列**
```

即 0005 刚合并进去的别名立刻被种子回冲掉。这属于「播种的职责越界」——
种子应**补齐缺失**，不该覆盖已有（迁移/人工）别名。

修复 `dictionary_seed.upsert()`：

```python
merged = list(dict.fromkeys([*(row.aliases or []), *aliases]))
row.aliases = merged
```

### 25.4 迁移 0006：幂等重放

0005 已在真实环境执行过，**改历史迁移不会重跑**，所以新增 0006 做补合并：

- 遍历 `*_ABS`，把其别名按「去重 + 黑名单过滤」并入 `*_CNT`；
- 黑名单拦排版碎片（`第1页`、`共`、`页码`、`报告页码文本`），防止借合并进字典；
- 再次兜底停用 `*_ABS`。

### 25.5 验证（真实 PostgreSQL）

```
alembic_version = 0006

LYMPH_CNT  aliases: 淋巴细胞#|淋巴细胞计数|LYMPH#|LYMPH|淋巴细胞数|淋巴细胞绝对值|淋巴细胞绝对计数
NEUT_CNT   aliases: 中性粒细胞#|中性粒细胞计数|NEUT#|NEUT|中性粒细胞(?)|中性粒细胞绝对值
MONO_CNT   aliases: 单核细胞#|单核细胞计数|MONO#|MONO|单核细胞绝对值
BASO_CNT   aliases: 嗜碱性粒细胞#|…|嗜碱性粒细胞|嗜碱性粒细胞绝对值|嗜碱性粒细胞计数
所有 *_ABS / REPORT_PAGE_TEXT: active = f
```

匹配验证：

```
淋巴细胞绝对值  -> LYMPH_CNT
中性粒细胞绝对值 -> NEUT_CNT
单核细胞绝对值  -> MONO_CNT
淋巴细胞数     -> LYMPH_CNT
中性粒细胞#    -> NEUT_CNT
中性粒细胞%    -> NEUT_PCT
```

### 25.6 迁移测试

无 DB 依赖，用 SQLite 建最小表 + `monkeypatch` 替换 `op.get_bind`：

```
tests/unit/test_migration_0005.py   6 passed
tests/unit/test_migration_0006.py   5 passed
  覆盖：合并正确 / 参考区间改指 / 别名去重 / 元信息停用 / 目标缺失跳过 / 幂等重复执行
tests/unit/test_dictionary_seed.py  +2 例（别名合并不覆盖、不重复堆积）
```
### 25.7 全量回归（当前树）

```
cd server
..\.venv\Scripts\python.exe -m pytest -q --tb=short -p no:cacheprovider
# 625 passed, 285 warnings in 698.68s (0:11:38)     EXIT=0
```

基线演进：`512` → `591` → `612` → **`625`**（+13 为本轮迁移与种子测试）。

### 25.8 本轮（§25）小结

**做了正式的治理而非补丁**：

| 层次 | 内容 |
|---|---|
| 数据迁移 | 0005 合并观测/参考区间/别名 + 停用；0006 重放补合并（因 0005 已被 seed 回冲） |
| 根因修复 | `dictionary_seed.upsert()` 别名改为**合并**，从源头防止迁移成果被回冲 |
| 测试 | 迁移 11 例（SQLite 无 DB 依赖）+ 种子 2 例 |
| 真实环境验证 | `alembic_version=0006`，别名已合并，6 种历史写法全部正确匹配规范 code |

**关键教训**：`alembic upgrade` 与 `seed_dictionary` 的**执行时序**会互相影响。
种子脚本的职责是「补齐缺失」，一旦改成「覆盖整列」，任何迁移写入的数据都会
在下次启动时被冲掉。这类跨组件隐式耦合需要显式测试（已补）。

### 25.9 剩余待办

1. **OCR 串列**：非法单位（`13`/`71`/`0.5`）需白名单校验
2. **重抽重复观测**：`reextract` 后新旧草稿叠加
3. **K1/K2 未重跑全量 38 份**：单份血常规 10/10，整体数字待更新
## 26. K1/K2 全量重测：**两项首次跨过验收门槛**（2026-10-23）

### 26.1 结果对比

38 份真实检查单，`--classes primary`（282 个必填字段 / 37 个异常项）：

| 指标 | 首轮基线 | 最终 | 变化 | 门槛 | 结论 |
|---|---|---|---|---|---|
| **K1 字段准确率** | 149/282 = 52.84% | **263/282 = 93.26%** | **+40.42pp** | ≥90% | ✅ **PASS** |
| **K2 异常召回** | 25/37 = 67.57% | **36/37 = 97.30%** | **+29.73pp** | ≥95% | ✅ **PASS** |
| 定性项命中（参考） | 85/155 = 54.84% | 84/155 = 54.18% | -0.66pp | — | 不进 KPI |

**这是本项目首次 K1/K2 同时达标**，此前一直是 NO-GO。

### 26.2 提升来源拆解（按贡献排序）

| # | 修复 | K1 贡献 |
|---|---|---|
| 1 | **评分口径族校验**（§26.3，真实缺陷） | +24.8pp（52.84%→74.11% 含部分同义） |
| 2 | **同义异名归并**（红细胞分布宽度/比容/压积/平均体积 4 组） | +14.2pp（74.11%→88.30%） |
| 3 | **生化同义归一**（转氨酶↔转移酶、白球比↔白蛋白/球蛋白比值） | +3.4pp（88.30%→91.84%） |
| 4 | **单位写法等价**（无量纲占位符、镜检量词「个」） | +1.4pp（91.84%→93.26%） |
| — | 系统侧五分类分族 + 字典迁移（§22–25） | 已包含在基线改善中 |

### 26.3 关键缺陷：评分脚本把裸五分类名匹配成「白细胞计数」

重跑首轮得到 **K1 反降**（52.84%→49.29%），逐条比对 misses 才定位到：

```
20250126血常规.pdf | 中性粒细胞 | matched=白细胞计数 | 数值 2.22 ≠ 7.27
20250126血常规.pdf | 淋巴细胞   | matched=白细胞计数 | 数值 4.31 ≠ 7.27
... × 5 项 × 10 份血常规 = 50 次错配
```

根因：§22 加 `_is_cell_diff_base` 后，五分类裸名分支退化成「kind 相同即匹配」，
完全没校验是否**同族**。而「中性粒细胞」与「白细胞计数」的单位都是 `10^9/L`，
kind 都判为「计数」→ 被误判为同一指标。

修法：

1. `_cell_diff_family()` 返回族名（NEUT/LYMPH/MONO/EOS/BASO），**跨族一律不匹配**；
2. 同族须双方 kind 都判定出且一致，任一方判不出（裸名+无单位）**不放行**
   （宁可漏配，不可错配）。

### 26.4 系统侧真实缺陷（同批修复）

| 缺陷 | 说明 | 修法 |
|---|---|---|
| 同义词重复展开 | `谷氨酰转肽酶` → `谷氨酰谷氨酰转肽酶`；`天门冬氨酸` → `天天门冬氨酸` | `_apply_synonyms()` 改为**单次扫描、最长优先、不回头**；裸后缀用否定前瞻 `(?<!谷氨酰)转肽酶` |
| 生化异名未归一 | 「丙氨酸氨基**转氨酶**」≠「丙氨酸氨基**转移酶**」；「白球比」≠「白蛋白/球蛋白比值」 | `_SYNONYMS` 补齐（谷丙/谷草/GGT/白球比/A-G） |

### 26.5 剩余未达标项（不影响门槛，但需跟进）

1. **镜检 vs 血液同名指标错配（真实缺陷）**：粪便/尿液常规的「白细胞」「红细胞」
   被匹配到血液 `WBC`/`RBC`（`个/HP` → `10^9/L`）。血/尿/便的白细胞是**不同项目**，
   需按标本类型（`specimen`）分族，同 `_PAIR_TRAITS` 思路加安全阀。
2. **9 项未抽取**：铁、PH值、二氧化碳总量、磷、乙肝三系抗体 ×3、HIV/HCV 筛查
   —— 分散在微量元素/血气/感染筛查报告，属覆盖率问题。
3. **定性项口径**：84/155 = 54.18%，需确认是否纳入 KPI（当前只作参考）。
### 26.6 最终验证（当前树）

```
cd server
..\.venv\Scripts\python.exe -m pytest -q --tb=short -p no:cacheprovider
# 625 passed, 285 warnings in 520.83s (0:08:40)     EXIT=0

cd ..
$env:PYTHONIOENCODING='utf-8'
python evals\scripts\score_kpi.py selftest            # selftest OK
python evals\scripts\score_kpi.py k1k2 \
  --dir evals\dataset\kpi_k1k2 \
  --annotations evals\dataset\kpi_k1k2\annotations.clean.jsonl \
  --classes primary
# K1 = 263/282 = 93.26%   PASS (gate 90%)
# K2 = 36/37 = 97.30%     PASS (gate 95%)
```

### 26.7 评测执行链（可复现）

1. 重建镜像：`docker compose -f deploy/docker-compose.yaml build api worker`
   然后 `up -d --force-recreate api worker`
2. 批量重抽 38 份（容器内脚本调用 `review.reextract`，避免登录凭证）
3. 从数据库 `derived_records.payload.observations` 导出 predictions（同样免登录）
4. `score_kpi.py k1k2 --classes primary`

⚠️ 评测需要登录凭证才能用官方 `export_predictions.py`；
本轮用等价的数据库直读脚本（已删除）。**长期应给评测账号固定凭证**，
避免每次重建镜像后 `/evals` 挂载与凭证问题。

### 26.8 结论

K1/K2 **首次同时跨过 PRD 门槛**，从 NO-GO 转为 GO 候选。但需注意：

- 提升主要来自**修正度量口径 + 真实归一化缺陷**，不是模型能力变化；
- 剩余 19 项 miss 里仍有 7 项是**真实语义错配**（镜检 vs 血液同名指标），
  这是下一优先事项，不能因为指标达标就忽略；
- 定性项 155 项命中率仅 54.18%，口径未定，不能算作已验证。
## 27. 标本类型安全阀：镜检（尿/便）vs 血液定量（2026-10-23）

### 27.1 缺陷

评测剩余 miss 中 7 项属**真实语义错配**：

```
粪便常规.pdf | 白细胞 | matched=白细胞计数 | 单位 个/HP ≠ 10^9/L
粪便常规.pdf | 红细胞 | matched=红细胞计数 | 单位 个/HP ≠ 10^12/L
尿液分析.pdf | 红细胞 | matched=红细胞计数 | 单位 /HP   ≠ 10^12/L
尿液分析.pdf | 白细胞 | matched=白细胞计数 | 单位 /HP   ≠ 10^9/L
```

血、尿、便里的「白细胞」是**完全不同的检验项目**。混配后医生会看到
错误量级（镜检 0.5 个/HP 被当成 0.5×10^9/L，差 3 个数量级）。
这是 AGENTS.md 第 8 条"宁可新建，不可错并"要拦的典型场景。

### 27.2 判定依据：单位量纲，而非猜标本类型

系统当前**没有 specimen 字段**（`documents` 表无此列），也可靠地拿到
`doc_type`。但**单位本来就在观测数据里**，且量纲差异明确：

| 体系 | 典型单位 | 量纲 |
|---|---|---|
| 血液定量 | `10^9/L`、`10^12/L`、`/uL` | `count/volume`、`count12/volume` |
| 镜检计数 | `个/HP`、`/HP`、`/LP`、`个/高倍视野` | 不在换算表（None） |

因此判定规则：**观测单位形态像镜检（`/HP`、`/LP`、`/视野`），而候选指标的
标准单位量纲属于体积/浓度类 → 冲突，拒绝匹配**。

### 27.3 实现

| 文件 | 变更 |
|---|---|
| `server/app/preprocessing/mapper.py` | 新增 `_MICROCOPY_UNIT_RE`、`_looks_microscopy_unit()`、`_unit_dimension()`、`_specimen_conflict()` |
| 同上 | `match_indicator()` 内新增 `_drop_specimen_conflicts()`，精确匹配两级都过滤 |
| 同上 | `match_indicator_fuzzy()` 增加 `unit` 参数并**先过滤候选词表**（模糊匹配同样会撞上血液指标） |
| 同上 | `map_payload()` 调用点传 `unit` |

关键点：`count12/volume`（红细胞 `10^12/L`）是独立量纲，**必须显式纳入**冲突判定，
否则「红细胞」仍会被错配（第一次实现就漏了这条，测试失败 3 例）。

冲突剔除后若候选为空 → 返回 `None` → 走 AI 对齐兜底，由 AI 新建正确的镜检指标
（而不是错配到血液指标）。

### 27.4 测试

`tests/unit/test_indicator_alignment.py` 新增：

- `test_microscopy_unit_not_matched_to_blood_indicator` ×6（参数化）
  ：`个/HP`、`/HP`、`/LP`、`个/低倍视野` 配「白细胞」「红细胞」都不得命中 WBC/RBC
- `test_blood_units_still_match` ×3：`10^9/L`、`/uL`、`10^12/L` 仍正常命中（安全阀不能误伤）
- `test_microscopy_detection_helper`：镜检单位识别正反例

```
pytest tests/unit/test_indicator_alignment.py -q    # 59 passed
pytest tests/unit -q                                # 454 passed
```
### 27.5 真实环境验证（2026-10-23）

重抽 4 份粪便/尿液常规后，最新版本草稿证据（`20260904尿液分析.pdf` v4）：

| raw_name | code | unit | 判定 |
|---|---|---|---|
| 红细胞 | **URINE_RBC_MICRO** | 个/HPF | ✅ 镜检 |
| 白细胞 | **URINE_WBC_MICRO** | 个/HPF | ✅ 镜检 |
| 白细胞计数 | **WBC** | 10^9/L | ✅ 血液定量 |

同一份尿常规**同时有**镜检「白细胞」和定量「白细胞计数」——这是真实的
（尿常规确实同时报告镜检与仪器计数）。安全阀正确区分，不再混配。

### 27.6 性质变化：从"危险错配"变成"保守漏配"

评测口径下的表现变化值得记录：

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 粪便常规「白细胞」 | → `WBC`（10^9/L）**危险错配** | → `URINE_WBC_MICRO`（个/HPF）语义正确 |
| 评分结果 | 记为 `单位不符` miss | 记为 `未抽取到` miss |

K1/K2 数字未变（93.26% / 97.30%），但**问题的严重性 downgrade 了**：
- 之前是"把镜检值当成血液值展示给医生"（可能导致误判）
- 现在是"评分器按标注名 `白细胞` 找不到 `尿镜检白细胞`"（纯度量口径问题）

这是正确的取舍：**宁可漏配，不可错配**（AGENTS.md 第 8 条）。

后续可选优化：让评分器也做"标本限定同义映射"（`白细胞` + 单位 `个/HPF`
→ `尿镜检白细胞`），但那属于度量口径，不影响系统正确性。

### 27.7 全量回归

```
pytest -q --tb=line -p no:cacheprovider
# 635 passed, 285 warnings in 560.72s (0:09:20)     EXIT=0
```

基线演进：`512` → `591` → `612` → `625` → **`635`**
## 28. 抽取覆盖率攻坚：异名归一 + 评分换算（2026-10-23）

### 28.1 最终结果

| 指标 | 初始基线 | §27 后 | **本轮后** | 门槛 | 结论 |
|---|---|---|---|---|---|
| **K1 字段准确率** | 52.84% | 93.26% | **96.45%** | ≥90% | ✅ PASS |
| **K2 异常召回** | 67.57% | 97.30% | **100.00%** | ≥95% | ✅ PASS |
| 定性项（参考） | 54.84% | 54.18% | 55.48% | — | 不进 KPI |

K2 **满分**，K1 距门槛 +6.45pp 余量。miss 从 19 项降到 **10 项**。

### 28.2 剩余 15 项 miss 的真实性质：全部"抽到了但名字不同"

逐个核对预测产物，**没有一项是真的没抽到**：

| 标注名 | 系统产出 | 差异类型 |
|---|---|---|
| 乙肝表面抗体 | 乙型肝炎表面抗体 | 简称 vs 全称 |
| 乙肝e抗体 / 乙肝核心抗体 | 乙型肝炎e抗体 / 乙型肝炎核心抗体 | 同上 |
| 人免疫缺陷病毒筛查试验 | 人类免疫缺陷病毒抗体筛查试验 | 同上 + 有无"抗体" |
| 丙型肝炎病毒抗体筛查试验 | 丙型肝炎病毒抗体 | 有无"筛查试验" |
| PH值 | pH | 大小写 + 「值」 |
| 二氧化碳总量 | 总二氧化碳 | **词序** |
| 铁 | 血清铁 | 「血清」限定 |
| 磷 | （确实缺） | 抽取遗漏 |

### 28.3 修复

| 层 | 变更 |
|---|---|
| `server/app/preprocessing/indicator_names.py` | `_SYNONYMS` 补齐乙肝五项（`乙肝X` → `乙型肝炎X`）、传染病筛查（`人免疫缺陷病毒` → `人类免疫缺陷病毒`、`丙肝抗体` → `丙型肝炎病毒抗体`）、血气（`二氧化碳总量` → `总二氧化碳`）、微量元素（`血清铁` → `铁`） |
| `evals/scripts/score_kpi.py` | `_SYNONYM_GROUPS` 补乙肝五项/HIV/HCV/血气/微量元素/pH；新增 `_UNIT_SCALE` + `_units_convertible()` + `_value_eq()`，同量纲不同量级先换算再比数值 |

### 28.4 又踩到同一个坑：后缀追加式同义词会自指

第一版写了 `("人类免疫缺陷病毒筛查", "人类免疫缺陷病毒抗体筛查试验")`，
结果产出 **`人类免疫缺陷病毒抗体筛查试验试验`** —— 目标串包含源串，
替换后再命中自己。

这是我本轮**第二次**踩同一类坑（第一次是 §26 的 `谷氨酰转肽酶`）。结论：

> **同义词映射的源串绝不能被目标串包含**，否则单次扫描也会自指。

修法：只做等长/改写的整串映射；「有无某词」的差异交给评分器的同义组处理。

### 28.5 评分器缺单位换算（真实度量缺陷）

微量元素「铁」标注 `7.52 mmol/L`，系统输出 `7520 μmol/L` —— **同一数值**，
但评分器只做字符串/浮点直接比较，判成"数值不符"。

修法：`_value_eq()` 在**同量纲**时换算到 base 再比（`mmol/L`→`umol/L` ×0.001）。
同时加安全阀：**量纲不同时即使数值巧合也不判命中**（`5 mmol/L` ≠ `5 %`）。

### 28.6 剩余 10 项 miss 的性质

| 类别 | 数量 | 说明 |
|---|---|---|
| 镜检保守漏配 | 7 | 安全阀正确拦下，AI 已建 `URINE_WBC_MICRO`/`URINE_RBC_MICRO`；评分器按裸名"白细胞"找不到（度量口径问题，非系统缺陷） |
| 定性值写法 | 2 | `阴性/2.00` vs `阴性/<2.00`、`未见` vs `0` —— 需要定性值归一 |
| 确实缺抽取 | 1 | 生化「磷」 |
### 28.7 最终验证（当前树）

```
cd server
..\.venv\Scripts\python.exe -m pytest -q --tb=line -p no:cacheprovider
# 635 passed, 285 warnings in 665.12s (0:11:05)     EXIT=0

$env:PYTHONIOENCODING='utf-8'
python evals\scripts\score_kpi.py selftest            # selftest OK
python evals\scripts\score_kpi.py k1k2 \
  --dir evals\dataset\kpi_k1k2 \
  --annotations evals\dataset\kpi_k1k2\annotations.clean.jsonl \
  --classes primary
# K1 = 272/282 = 96.45%   PASS (gate 90%)
# K2 = 37/37  = 100.00%   PASS (gate 95%)
```

### 28.8 全流程 K1 轨迹（可追溯）

| 阶段 | K1 | K2 | 主要变更 |
|---|---|---|---|
| 初始基线 | 52.84% | 67.57% | — |
| §26 评分族校验 + 同义名 + 单位写法 | 93.26% | 97.30% | 修正度量口径 |
| §27 标本类型安全阀 | 93.26% | 97.30% | 消除镜检↔血液危险错配（数字不变，性质降级） |
| §28 生化/传染病异名 + 评分单位换算 | **96.45%** | **100.00%** | 消除异名漏配、修正换算度量 |

### 28.9 需要诚实说明的三点

1. **提升主要来自"修正度量口径 + 补真实归一化缺陷"，不是模型能力跃升。**
   系统侧真实缺陷（五分类分族、元信息污染、同义词重复展开、镜检错配）
   也会实际影响抽取质量，但 K1 从 52.84% 到 96.45% 的跨度里，
   相当一部分是"评分器此前把对的判成错的"。

2. **K2 = 100% 只代表这 37 个异常项，样本量小**（38 份报告 / 37 个异常），
   不能外推为"异常召回已达生产级"。需要更大样本复核。

3. **剩余 10 项 miss 都不是"模型不够强"**：
   7 项是安全阀正确拦下的镜检项目（评分口径未适配）、
   2 项是定性值写法差异（`阴性/2.00` vs `阴性/<2.00>`）、
   1 项是生化「磷」确实漏抽。

### 28.10 下一批候选

1. **定性值归一**：`阴性/2.00` vs `阴性/<2.00>`、`未见` vs `0`、`-` vs 空
2. **评分器镜检同义映射**：`白细胞` + `个/HPF` → `尿镜检白细胞`（度量口径）
3. **固化评测凭证**：目前靠容器内直读数据库，重建容器就要重配 `/evals` 与 `/predictions`
4. **草稿版本清理策略**：`derived_records` 每次重抽新增一行，需保留 N 版或定期清理
## 29. 定性项攻坚：从 55.48% 到 90.32%（2026-10-23）

### 29.1 最终结果

| 指标 | 初始 | §28 后 | **本轮后** | 门槛 |
|---|---|---|---|---|
| **K1** | 52.84% | 96.45% | **99.29%** | ≥90% ✅ |
| **K2** | 67.57% | 100.00% | **100.00%** | ≥95% ✅ |
| **定性项（参考）** | 54.84% | 55.48% | **90.32%** | — |

**miss 总数从 10 项降到 2 项**。

### 29.2 先量化，再动手

69 项定性 miss 的真实构成（逐个核对，不是猜）：

| 类别 | 数量 | 性质 |
|---|---|---|
| 纯数值格式差异 | **27** | **评分器缺陷**：`132` vs `132.0`、`5.50` vs `5.5` |
| 名字未匹配 | 41 | 标本前缀 + 镜检命名 |
| 真实值不符 | 3 | 真问题 |

**39% 是评分器把对的判成错的** —— 如果直接去"优化抽取"，方向就错了。

### 29.3 三处修复

| 问题 | 修法 |
|---|---|
| 定性值只做 `str(a)==str(b)` | 新增 `_qual_value_eq()`：先 `_num_eq` 比数值，再处理占位符（`` ≡ `-`）、检出限前缀（`阴性/2.00` ≡ `阴性/<2.00`） |
| 系统给尿/便项目加标本前缀（`糖`→`尿糖`、`颜色`→`尿液颜色`），标注用裸名 | 新增 `_strip_specimen_prefix()`：剥 尿液/尿/粪便/大便/血清/全血/血浆/脑脊液 + 镜检/显微镜检查/镜下 |
| 多个候选时随便取第一个 | `_find_pred()` 按**单位体系**排序：单位等价 > 镜检↔镜检 > 其它 |
| `个/HPF` vs `/HP` 判不等 | `_unit_eq` 补 `HPF→HP`、`LPF→LP`、`高倍视野→HP` |

### 29.4 关键安全阀（不能为了涨分放松）

```python
assert not _qual_value_eq("阴性", "阳性")     # 临床意义相反
assert not _qual_value_eq("未见", "0")        # "未检出" ≠ 测得值 0
assert not _qual_value_eq("黄色", "黄褐色")    # 不同颜色描述
assert not names_match("糖", "血糖")          # 尿糖 ≠ 血糖
assert not names_match("蛋白", "血红蛋白")     # 剥前缀不能把不同指标混一起
```

`_strip_specimen_prefix("尿糖") == "糖"` 但 `normalize_name("血糖") == "血糖"`，
所以「糖」只与「尿糖」合并、不与「血糖」合并——靠的是**归一后仍不同名**，
不是额外规则。这点在设计时就验证过。

### 29.5 最后一处修复：候选挑选顺序

尿常规同时有两条：
```
尿镜检红细胞  未见  个/HPF    ← 标注「红细胞 /HP」应命中这个
红细胞计数    0     /ul       ← 不应命中
```
`names_match("红细胞","尿镜检红细胞")` 原本返回 False，因为剥掉「尿」后剩
「镜检红细胞」。「镜检」由**单位**（个/HPF）承担语义，不该留在名字里，
所以剥前缀时一并剥离「镜检/显微镜检查/镜下」。

修好后两个候选都匹配，再由单位体系排序选中正确的那个。

### 29.6 剩余 2 项

| 项 | 原因 |
|---|---|
| 生化「磷」 | 确实没抽到（覆盖率） |
| 乙肝表面抗体 | `阴性/2.00` vs `阴性/<2.00` + `IU/L` vs `mIU/mL`（定性+单位双差异） |
### 29.7 最终验证（当前树）

```
cd server
..\.venv\Scripts\python.exe -m pytest -q --tb=line -p no:cacheprovider
# 635 passed, 285 warnings in 631.38s (0:10:31)     EXIT=0

$env:PYTHONIOENCODING='utf-8'
python evals\scripts\score_kpi.py selftest            # selftest OK
python evals\scripts\score_kpi.py k1k2 \
  --dir evals\dataset\kpi_k1k2 \
  --annotations evals\dataset\kpi_k1k2\annotations.clean.jsonl \
  --classes primary,qualitative
# K1 = 280/282 = 99.29%   PASS (gate 90%)
# K2 = 37/37  = 100.00%   PASS (gate 95%)
# qualitative = 140/155 = 90.32%
```

### 29.8 全流程轨迹

| 阶段 | K1 | K2 | 定性 | 主要变更 |
|---|---|---|---|---|
| 初始 | 52.84% | 67.57% | 54.84% | — |
| §26 度量口径 | 93.26% | 97.30% | 54.18% | 族校验 + 同义名 + 单位写法 |
| §27 标本安全阀 | 93.26% | 97.30% | 54.18% | 消除镜检↔血液危险错配 |
| §28 异名 + 换算 | 96.45% | 100.00% | 55.48% | 生化/传染病异名、评分单位换算 |
| §29 定性口径 | **99.29%** | **100.00%** | **90.32%** | 定性值归一、标本前缀、候选排序 |

### 29.9 必须诚实说明

**这四轮提升的绝大部分来自"修正度量口径"，不是模型能力变化。**

- §26–§29 改的几乎都是 `evals/scripts/score_kpi.py`（评分器）；
- 系统侧真实缺陷（五分类分族、元信息污染、同义词重复展开、镜检错配）确实修了，
  但它们在 K1 上的贡献远小于"评分器此前把对的判成错的"这一项；
- 若只看系统侧改动，K1 的真实提升幅度**没有** 52.84%→99.29% 这么夸张。

**K2 = 100% 的样本量极小**（38 份报告 / 37 个异常项），不能外推为生产级。

**剩余 2 项 miss 中，「磷」是真漏抽**（覆盖率问题），不是口径问题。

### 29.10 下一步建议

1. **扩样本复验**：38 份太少，K2=100% 统计意义弱；需 ≥100 份才能谈稳定性
2. **固化评测流程**：目前靠容器内直读数据库 + 手动 cp，重建容器就要重配
3. **草稿版本清理**：`derived_records` 每次重抽新增一行，已明显堆积
4. **生化「磷」漏抽**：确认真实原因（解析器漏行 or 字典缺项）
## 30. 工程加固：评测可复现 + 「磷」异名 + 草稿版本保留（2026-10-23）

### 30.1 一次性完成三件事

按用户要求"依次做完"，三项均落地并有测试/实测证据。

### 30.2 固化评测流程（免凭证 + 一键重测）

**问题**：此前每次重测都要手抄 5+ 条 `docker cp` / `docker exec` 命令，
且评测账号密码不在仓库里，重建容器后 HTTP 登录直接阻塞。

**方案**：

| 文件 | 作用 |
|---|---|
| `evals/scripts/export_predictions.py --source db` | 新增免凭证模式：在 api 容器内直读 `derived_records`，不经 HTTP、不需登录。**只取 `version` 最大的一行**（历史踩过读旧版本的坑） |
| `evals/scripts/reextract_all.py` | 批量重抽评测账号下 `awaiting_review` 文档；`--dry-run` 预览、`--contains` 按文件名过滤、自动跳过队列中任务 |
| `evals/scripts/retest_kpi.sh` | 一键串起：栈检查 → 重抽 → 导出 → `selftest` → 评分。支持 `--no-reextract` / `--classes` |

**验证**：`export_predictions.py --source db` 实测 `写出 38，缺 0`；
`reextract_all.py --dry-run` 实测匹配 38 份。

### 30.3 「磷」不是漏抽，是异名

之前判为"确实没抽到"，逐个核对草稿后发现系统抽到的是 **`无机磷` 1.51 mmol/L**，
而标注写的是 `磷`。补映射：

- 系统侧 `_SYNONYMS`：`无机磷`/`血清磷`/`血磷` → `磷`
- 评分器 `_SYNONYM_GROUPS`：`("磷", "无机磷", "血清磷", "血磷")`

**安全阀**：`磷` ≠ `碱性磷酸酶`、`磷` ≠ `磷脂`（只做整串映射，不做子串包含）。

结果：K1 从 99.29% → **99.65%**，只剩 1 项 miss（乙肝表面抗体定性值）。

### 30.4 草稿版本保留策略

**问题**：`pipeline` 以 `max(version)+1` 写新草稿行是**有意的**（审计要追溯
每次抽取结果），但反复重抽会无限堆积——实测单份已到 **7 版**，每版还带
最多 200KB `source_text`。

**方案**：

| 文件 | 变更 |
|---|---|
| `server/app/preprocessing/pipeline.py` | 新增 `DEFAULT_KEEP_VERSIONS=5` 与 `prune_derived_records()`；抽取写入后自动清理 |
| `evals/scripts/prune_derived_records.py` | 存量清理工具：默认 dry-run，`--apply` 才删 |
| `server/tests/unit/test_derived_record_prune.py` | 6 例：保留最新 N 版 / 不超限不动 / **已确认版永不删** / keep=0 视为不清理 / 不影响其他文档 / 幂等 |

**安全约束**（贯穿设计）：

1. **最新版本永不删除**——前端与导出都以它为准；
2. **已确认版本（`confirmed_at` 非空）永不删除**——那是 HITL 审计线索；
3. `keep < 1` 视为不清理——防止误传 0 把历史全删。

**实测**：存量 dry-run 发现 2 份超限（7 版、6 版），`--apply` 删除 3 行；
清理后 `MAX(versions)` 从 7 → **5**。
### 30.5 最终验证（当前树）

```
cd server
..\.venv\Scripts\python.exe -m pytest -q --tb=line -p no:cacheprovider
# 641 passed, 285 warnings in 860.78s (0:14:20)     EXIT=0

$env:PYTHONIOENCODING='utf-8'
python evals\scripts\score_kpi.py selftest            # selftest OK
python evals\scripts\score_kpi.py k1k2 \
  --dir evals\dataset\kpi_k1k2 \
  --annotations evals\dataset\kpi_k1k2\annotations.clean.jsonl \
  --classes primary,qualitative
# K1 = 281/282 = 99.65%   GO  (gate 90%)
# K2 = 37/37  = 100.00%   GO  (gate 95%)
# qualitative = 140/155 = 90.32%
```

基线演进：`512` → `591` → `612` → `625` → `635` → **`641`**

### 30.6 全流程总轨迹

| 阶段 | K1 | K2 | 定性 | 主要变更 |
|---|---|---|---|---|
| 初始 | 52.84% | 67.57% | 54.84% | — |
| §26 | 93.26% | 97.30% | 54.18% | 族校验 / 同义名 / 单位写法 |
| §27 | 93.26% | 97.30% | 54.18% | 标本类型安全阀 |
| §28 | 96.45% | 100.00% | 55.48% | 生化/传染病异名 + 评分单位换算 |
| §29 | 99.29% | 100.00% | 90.32% | 定性值归一 + 标本前缀 + 候选排序 |
| §30 | **99.65%** | **100.00%** | **90.32%** | 「磷」异名 + 工程加固 |

### 30.7 遗留（按优先级）

1. **扩样本复验（最重要）**：38 份 / 37 个异常项，K2=100% 统计意义弱；
   K1=99.65% 也可能被这 38 份的分布"喂"出来。**没有 ≥100 份样本前，
   不应把这两个数字当作生产级结论。**
2. **乙肝表面抗体**：`阴性/2.00` vs `阴性/<2.00` + `IU/L` vs `mIU/mL`
   ——定性值 + 单位双差异，属定性口径最后一处。
3. **系统侧真实提升未有独立度量**：§26–§30 大部分改的是评分器；
   系统侧修复（五分类分族、元信息污染、同义词重复展开、镜检错配）
   的实际抽取质量贡献，需要**固定标注 + 固定评分口径**重跑才能分离出来。