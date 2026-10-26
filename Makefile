# AI 私人医生系统 — 常用命令
# 用法示例：make up / make migrate / make seed / make test / make lint

COMPOSE := docker compose -f deploy/docker-compose.yaml
ENV_FILE := deploy/.env

.PHONY: up down ps logs build migrate seed test lint smoke fmt

up: ## 一键起全栈（验收基线）
	$(COMPOSE) --env-file $(or $(ENV_FILE),deploy/.env.example) up -d --build

down: ## 停止并清理容器（保留数据卷）
	$(COMPOSE) --env-file $(or $(ENV_FILE),deploy/.env.example) down

ps: ## 查看服务状态
	$(COMPOSE) ps

logs: ## 跟随日志
	$(COMPOSE) logs -f

build: ## 构建镜像
	$(COMPOSE) --env-file $(or $(ENV_FILE),deploy/.env.example) build

migrate: ## Alembic 迁移到最新（本地/容器外，依赖 DATABASE_URL）
	cd server && alembic upgrade head

seed: ## 冷启动种子数据：指标字典 + 红旗规则
	cd server && python ../scripts/seed_dictionary.py
	cd server && python ../scripts/seed_redflag_rules.py

test: ## 后端测试
	cd server && python -m pytest -q

lint: ## 后端 import 自检 + 前端 lint
	cd server && python -c "import app.main; import app.worker; print('backend import ok')"
	cd web && npm run lint || true

smoke: ## compose 起后端到端冒烟
	bash scripts/smoke_e2e.sh

fmt: ## 前端构建自检
	cd web && npm run build
