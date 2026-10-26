# AI 私人医生系统（P0）

家庭健康档案 + 带依据的 AI 问诊助手。上传报告 → AI 抽取 → 人工确认 → 结构化入库 →
时间线/趋势 → 对话问诊（引用可回链原件）。

技术栈：FastAPI 单体 + PostgreSQL 16(+pgvector) + Redis + MinIO + Alembic +
React/Vite/TS SPA（详见 `docs/ARCHITECTURE.md`、`docs/PRD.md`）。

## 文档导航（先看这里）

- **当前真实状态、评测与下一步**：`PLAN.md` §0（唯一实时状态入口）。
- **工程规则**：`AGENTS.md`（Subject 隔离、HITL、审计、脱敏、TDD 等强制约束）。
- **下一阶段设计（2026-10-26，待评审/未实施）**：
  `docs/superpowers/specs/2026-10-26-health-record-intelligence.md`。
  覆盖多类型长期档案、版本化 Markdown、混合检索、主动查档/原件复读/追问和外部医学证据。
- **对应迭代计划**：`docs/superpowers/plans/2026-10-26-health-record-intelligence.md`。
- `docs/PRD.md` 与 `docs/ARCHITECTURE.md` 主体为 P0 初始基线，不能单独视为当前完整功能清单。

## 一键启动（验收基线，Docker Compose）

```bash
# 1. 配置环境变量（复制样例后按需修改；切勿提交真实密钥）
cp deploy/.env.example deploy/.env

# 2. 一键起全栈（web:8080 / api:8000 / postgres / redis / minio）
docker compose -f deploy/docker-compose.yaml up -d --build

# 3. （可选）冷启动种子：TOP50 指标字典 + 红旗规则
docker compose -f deploy/docker-compose.yaml exec api python scripts/seed_dictionary.py
docker compose -f deploy/docker-compose.yaml exec api python scripts/seed_redflag_rules.py

# 4. 冒烟验证（上传→抽取→确认→时间线→问诊闭环）
bash scripts/smoke_e2e.sh
```

打开 <http://localhost:8080> → 注册（自动建“本人”档案）→ 上传化验单 → 确认草稿 →
时间线/趋势 → 问诊。

服务与端口：web `8080`（nginx 静态 + `/api`、`/v1` 反代）、api `8000`（`/api/docs`
Swagger）、postgres `5432`、redis `6379`、minio `9000`（控制台 `9001`）。

## 本地开发

后端（Python 3.12+）：

```bash
cd server
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export DATABASE_URL="postgresql+psycopg://aiuser:aipass@localhost:5432/aidoctor"
alembic upgrade head
python scripts/seed_dictionary.py && python scripts/seed_redflag_rules.py
python -m uvicorn app.main:app --reload            # api
python -m app.worker                               # worker（另一终端）
```

前端（Node 22+）：

```bash
cd web
npm install
npm run dev        # http://localhost:5173（/api 代理到 :8000）
npm run build      # 生产构建
npm test           # 单测（vitest）
```

测试：`cd server && python -m pytest -q`（离线可跑：SQLite + 内存队列/存储替身）。

## 目录结构

```
server/                 FastAPI 单体（模块化边界：auth/subjects/documents/preprocessing/
                        observations/timeline/chat/redflag/sanitizer/audit/llm）
  app/                  应用代码（main.py / worker.py 为入口）
  alembic/              迁移（0001_init 全量 DDL）
  tests/                pytest（api / unit / integration）
web/                    React SPA（api/stores/routes/components/pages/hooks）
deploy/
  docker-compose.yaml   六服务一键编排（healthcheck + service_healthy + 命名卷）
  k8s/                  15 个 K8s 清单（kubectl apply -k deploy/k8s）
  .env.example          全量环境变量样例（24 项，镜像零硬编码）
scripts/                种子脚本 + 冒烟脚本
docs/                   PRD/ARCHITECTURE 基线 + superpowers 设计与计划（导航见上）
```

## K8s 部署

```bash
kubectl apply -k deploy/k8s
# 镜像需先构建并推送到集群可访问的仓库（修改 deploy/k8s/*.yaml 中 image 引用）
```

## 降级与边界（P0）

- **LLM 不可用**：抽取链自动降级 `vlm → rule(pdf 文本+正则) → mock(离线演示占位草稿)`；
  问诊返回友好错误并可重试（不产生 Trace）；对话式草稿跳过并提示手动录入。
- **Redis/MinIO 不可用**：入队失败由 worker 周期扫描 DB 补偿；本地测试用内存替身。
- **P1 留缝（未实现）**：pgvector 填充（`embedding` 列已建）、DICOM 解析、
  L3 向量检索、阈值自动入库（`AUTO_COMMIT_ENABLED` 恒 false）。
- 安全：JWT 鉴权、跨 Account 一律 404、写操作强制选档（422 `SUBJECT_REQUIRED`）、
  出域 LLM 调用前脱敏（姓名/证件/电话/生日→占位符，云端模式开关只读）、
  审计日志覆盖全部写操作。
