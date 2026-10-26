# Eval 用例库（KPI Go/No-Go 评测）

> 对应 `docs/PRD.md` §3 门槛 K1/K2/K3；跑分脚本 `scripts/score_kpi.py`（纯标准库，任意 Python ≥3.10）。

## 流程

1. **收样本**：真实化验单扫描件/PDF ≥30 张放入 `dataset/kpi_k1k2/files/`（Q-4：客户提供，需求发出后 1 周内）。
2. **人工标注**：复制 `TEMPLATE_annotations.jsonl` 为 `annotations.jsonl`，每张一行。字段口径与系统 DerivedRecord 对齐（数值、单位、日期三要素全对才算命中）。
3. **导出预测**：样本走完整链路（上传→HITL 确认入库）后，在标注行回填 `documentId`，运行自动导出：
   ```bash
   # Windows/Linux 通用；需 EVAL_EMAIL/EVAL_PASSWORD（评测专用账号）
   python scripts/export_predictions.py --base-url http://localhost:8080/api
   ```
   产物写入 `dataset/kpi_k1k2/predictions/<样本同名>.json`。（连通性自检加 `--live-test`。）
4. **跑分**：
   ```bash
   python scripts/score_kpi.py k1k2 --dir dataset/kpi_k1k2          # K1 准确率 / K2 异常召回
   python scripts/score_kpi.py k3 --questions dataset/kpi_k3/questions.jsonl
   ```
5. **判定**：K1≥90%、K2≥95%、K3≥85%，任一不达标 → No-Go（先修预处理流水线/指标字典，不加功能）。

## 一键重测（推荐，无需登录凭证）

改过预处理/字典/归一化后，用它一次性重抽+导出+评分：

```bash
# 仓库根目录；前置：docker compose 栈已启动
bash evals/scripts/retest_kpi.sh                      # 全量重抽 + primary,qualitative
bash evals/scripts/retest_kpi.sh --no-reextract       # 只导出+评分（样本未变时）
bash evals/scripts/retest_kpi.sh --classes primary    # 只评 primary
```

它做了三件手工容易漏的事：

| 步骤 | 脚本 | 为什么需要 |
|---|---|---|
| 重抽 | `reextract_all.py` | 字典/归一化改动后，旧草稿不会自动反映新逻辑 |
| 导出 | `export_predictions.py --source db` | 评测账号密码不在仓库；容器重建会清掉 `/evals`、`/predictions` 挂载。`db` 模式在容器内直读 `derived_records`，**免凭证** |
| 评分 | `score_kpi.py` | 先跑 `selftest`（安全阀断言）再计分 |

⚠️ **`derived_records` 每次重抽新增一行**（不覆盖）。导出时必须
`ORDER BY version DESC LIMIT 1`，读错版本会拿到旧结果——`--source db`
已正确处理。

⚠️ **改代码后必须重建镜像**：`deploy/docker-compose.yaml` 未挂载源码，
`restart` 只重启旧镜像。正确流程：

```bash
docker compose -f deploy/docker-compose.yaml build api worker
docker compose -f deploy/docker-compose.yaml up -d --force-recreate api worker
```

## 跑分口径（易踩坑，务必了解）

**提升 K1/K2 的正确方式是修系统缺陷，不是放宽比对规则。** 以下规则都有安全阀：

| 规则 | 作用 | 安全阀 |
|---|---|---|
| 名称归一化 | `血红蛋白` ≡ `血红蛋白量` | 成对指标（收缩压/舒张压、MCV/MCH/MCHC）绝不合并 |
| 五分类族校验 | `中性粒细胞` ≡ `中性粒细胞计数` | 跨族（中性粒细胞 vs 淋巴细胞）不合并；只比 kind 会把「中性粒细胞」误判成「白细胞计数」 |
| 标本前缀归一 | `糖` ≡ `尿糖`、`颜色` ≡ `尿液颜色` | `糖` ≠ `血糖`（不同标本体系） |
| 同义组 | 红细胞比容 ≡ 红细胞压积、谷丙转氨酶 ≡ 丙氨酸氨基转移酶 | 只收录医学上确为同一指标的异名 |
| 单位写法等价 | `ng/ml` ≡ `ng/mL`、`个/HP` ≡ `/HP`、`HPF` ≡ `HP` | `/HP` ≠ `10^9/L`（镜检 ≠ 血液定量） |
| 单位换算 | `7.52 mmol/L` ≡ `7520 μmol/L` | 量纲不同时数值巧合也不判命中（`5 mmol/L` ≠ `5 %`） |
| 定性值比较 | `132` ≡ `132.0`、`阴性/2.00` ≡ `阴性/<2.00` | `阴性` ≠ `阳性`、`未见` ≠ `0` |

改这些规则时必须同时补 `score_kpi.py selftest()` 断言，否则下次容易为了涨分放松安全阀。

## 标注格式

### K1/K2 — `dataset/kpi_k1k2/annotations.jsonl`（每行一个样本）

```json
{"file": "files/report_001.jpg", "subject": "本人",
 "fields": [
   {"name": "血糖(空腹)", "value": "6.8", "unit": "mmol/L", "date": "2026-03-12", "abnormal": true},
   {"name": "血红蛋白",   "value": "145",  "unit": "g/L",    "date": "2026-03-12", "abnormal": false}
 ],
 "notes": ""}
```

评分口径：
- **K1** = 三要素(value+unit+date)全对的字段数 / 标注字段总数（预测缺失记错；多余输出不扣分但列出供抽查）。
- **K2** = 被正确抽出的 abnormal=true 字段数 / abnormal=true 总数（召回率）。数值容差 ±0.01×数量级尾位；单位需与字典标准单位或常见别名一致。

### K3 — `dataset/kpi_k3/questions.jsonl`（≥20 问，覆盖多时间窗/跨多次报告）

```json
{"id": "q01", "subject": "本人", "question": "最近三次空腹血糖趋势如何？",
 "expect": {"metric": "血糖(空腹)", "citations_min": 3, "must_include_dates": ["2026-03-12"], "subject_ok": true},
 "verdict_manual": null}
```

评分口径：人工核对回答引用的数据值/时间/人三项是否全部正确 → `verdict_manual: pass|fail`；脚本汇总通过率。**评测必须在真实 LLM 网关下跑**（mock 降级结果不计入 Go/No-Go）。

## 现状

> 2026-10-26 状态导航：实时结果与限制以根目录 `PLAN.md` §0 为准，本目录说明评测方法，不单独维护发布状态。

- [x] 框架、真实样本与标注已具备；最近已记录结果为 K1=281/282、K2=37/37（2026-10-23）。
- [x] 真实网关与免凭证重测链已具备（见上文）。
- [ ] K3 ≥20 问完整问题集及全部人工 verdict 尚未闭环；样本不足或存在未判定项时不得输出“通过”结论。
- [ ] 需冻结数据/标注/评分器/预测版本，并在未参与调参的新增医院与类型样本上复验；38 份、37 个异常项不能外推为生产级稳定性。
- [ ] 新版关键证据检索与临床策略评测按 `docs/superpowers/plans/2026-10-26-health-record-intelligence.md` 实施，当前尚未实现。
