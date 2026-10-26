#!/bin/sh
# 后端容器入口：先跑 Alembic 迁移（版本化，§12.2），再执行传入命令（api/worker）。
set -e

echo "[entrypoint] running alembic upgrade head ..."
alembic upgrade head

# 冷启动数据：指标字典必须存在，否则所有指标都"字典未命中"、趋势不可用。
# 种子脚本幂等（按 code upsert）；失败不阻塞启动（记录后继续）。
if [ "${SEED_ON_STARTUP:-true}" = "true" ]; then
  echo "[entrypoint] seeding indicator dictionary (idempotent) ..."
  python /app/scripts/seed_dictionary.py \
    || echo "[entrypoint] WARN: dictionary seed failed; extraction runs with unmapped names"
fi

echo "[entrypoint] exec: $*"
exec "$@"
