# ADR-0001：文档处理状态与生命周期正交

- 状态：Accepted（2026-10-26 随 R0 T004 落地；迁移整链验证待补，见 `PLAN.md` B-1）
- 日期：2026-10-26
- 关联：`docs/superpowers/specs/2026-10-26-health-record-intelligence.md` §4.5、§11；
  实施任务 `docs/superpowers/plans/2026-10-26-health-record-intelligence.md` T004

## 背景

`documents.status` 此前同时承担两个互不相干的语义：

1. **抽取处理进度**：`pending → extracting → awaiting_review → confirmed / parsed_no_data / failed`
2. **生命周期**：`archived`（用户归档，替代删除）

后果：

- 归档会把 `confirmed` 覆盖成 `archived`，"已确认入库"这一临床事实被生命周期动作抹掉；
- 无法表达"已归档 · 待确认"或"已归档 · 处理失败"这类真实组合；
- `archived` 是终态且 `DOC_TRANSITIONS["archived"] == set()`，归档后无法恢复；
- 列表按 `status` 过滤时，归档记录与未处理记录混在同一维度，语义不可解释。

## 决策

拆成两个正交维度，**旧 `status` 保留为派生投影**：

| 维度 | 字段 | 取值 |
|---|---|---|
| 处理状态 | `documents.processing_status` | `pending / processing / awaiting_review / processed / failed` |
| 内容细分 | `documents.content_status` | `confirmed`（已确认入库）/ `no_data`（已解析但无临床字段）；仅 `processed` 时非空 |
| 生命周期 | `documents.lifecycle_status` | `active / archived / deletion_pending` |
| 对账标记 | `documents.needs_reconcile` | 历史行无法反推处理状态时为真 |

`status` 由 `project_legacy_status(processing, content, lifecycle)` 唯一确定；
所有写入经 `app/documents/state.py` 的 `set_processing_status` /
`set_lifecycle_status`，**业务代码不得直接赋值 `document.status`**。

## 关键后果

- 归档只改生命周期，处理状态不变；`restore` 可逆，且不重抽、不改已确认事实。
- 生命周期可扩展到 `deletion_pending`（两阶段删除）而不触碰处理状态机。
- 旧客户端读 `status` 行为不变（归档行仍返回 `archived`），零破坏性升级。
- 历史 `archived` 行无法反推归档前的处理状态 → 回填 `pending + needs_reconcile=true`，
  **不猜为 `processed`**（避免把未确认记录当作已确认事实）。
- `processed` 与 `confirmed` 分离后，"已解析"不再等同于"已入库"。

## 迁移策略

1. 迁移 `0007`：新列可空/带安全默认 → 回填 → 建索引。
2. 双读：读取一律经 `state.py` 的 `processing_of/lifecycle_of/content_of`，
   兼容尚未回填的历史行。
3. 双写：应用层在每次状态写入时同步旧 `status`（单一同步点）。
4. 全量回归通过后，**旧 `status` 降级为纯投影**；删除旧列需另开迁移 + 兼容窗口。

### 实施中的关键修正（2026-10-26）

初版实现让**新列优先**于旧列，结果是：既有代码、脚本与运维直接写旧列时"改不动"
（`processing → pending` 抛 STATE_INVALID，worker 孤儿回收查不到文档，
13 个既有测试失败）。根因是**双向权威**。

最终规则：

- **活跃文档以旧列为兼容权威**（旧列能完整表达 6 种处理状态，且既有写入路径
  仍直接写旧列）；新列同步写入，供归档后读取。
- **归档文档以新列为权威**（旧列被归档覆盖，`processing_status` 才保留
  "归档前是已确认"这一临床事实）。
- 状态写入一律经 `state.py` 的 `set_processing_status` / `set_lifecycle_status`，
  并在同函数内投影回旧列——**单一同步点**。

补充：处理状态机补 `processing → pending` 合法边（worker 租约回收回退等待重试，
与用户主动"失败后重试"是不同语义）。

## 备选方案与否决理由

| 方案 | 否决理由 |
|---|---|
| 保持单列，仅改枚举 | 无法表达"归档 + 已确认"组合；已确认事实仍被覆盖 |
| 直接删除 `status` 换新列 | 破坏性变更，旧前端与第三方调用方立即失效，且无迁移期 |
| 用 `deleted_at` 表达归档 | `deleted_at` 是软删（不可见）语义，与归档（可见但隐藏）不同；混用会让"归档"变成事实删除 |
| 新建独立 `document_lifecycle` 表 | 一行文档的生命周期与处理状态强相关，拆表增加一致性与联表成本，无收益 |

## 验证

- `server/tests/unit/test_document_state.py`：投影不变量、状态转移、
  归档不改处理状态、历史行 needs_reconcile。
- `server/tests/api/test_document_lifecycle.py`：软删一律 404、后台任务不处理已删原件、
  归档仍可查看。
- `server/tests/integration/test_state_machine.py`：旧状态机兼容表仍被校验。

## 后续

- R1 引入 `DocumentAsset` 后，资产版本与 `content_status` 共同构成"可检索事实"判定。
- 永久删除（`deletion_pending` → 物理清理）需另立 ADR：合规授权、引用影响预览、
  两阶段 outbox、对象存储多资产清理、备份保留期。
