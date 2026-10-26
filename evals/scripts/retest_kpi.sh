#!/usr/bin/env bash
# 一键重测 K1/K2：重抽 → 导出 predictions → 评分。
#
# 为什么需要：
#   1. 评测账号密码不在仓库里，重建容器后 HTTP 登录会阻塞重测；
#   2. 容器重建会清掉 /evals、/predictions 挂载；
#   3. 每次手抄 docker cp / exec 命令容易漏步骤且不可复现。
#
# 用法（在仓库根目录）：
#   bash evals/scripts/retest_kpi.sh                                  # 全量重抽 + 双类评分
#   bash evals/scripts/retest_kpi.sh --no-reextract                   # 只导出+评分
#   bash evals/scripts/retest_kpi.sh --classes primary                # 只评 primary
#   bash evals/scripts/retest_kpi.sh --classes=primary,qualitative
#
# 前置：docker compose 栈已启动（api/worker/postgres 健康）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE="docker compose -f $ROOT/deploy/docker-compose.yaml"
DATASET="$ROOT/evals/dataset/kpi_k1k2"
ANNOTATIONS="$DATASET/annotations.uploaded.jsonl"
CLEAN="$DATASET/annotations.clean.jsonl"
PRED="$DATASET/predictions"

REEXTRACT=1
CLASSES="primary,qualitative"

while [ $# -gt 0 ]; do
  case "$1" in
    --no-reextract) REEXTRACT=0; shift ;;
    --classes)      CLASSES="$2"; shift 2 ;;
    --classes=*)    CLASSES="${1#*=}"; shift ;;
    *)              echo "未知参数: $1"; exit 2 ;;
  esac
done

for f in "$ANNOTATIONS" "$CLEAN"; do
  [ -f "$f" ] || { echo "[error] 缺少 $f"; exit 2; }
done

echo "[1/4] 检查栈状态"
$COMPOSE ps --format '{{.Name}} {{.State}}' | sed 's/^/  /'

if [ "$REEXTRACT" = "1" ]; then
  echo "[2/4] 批量重抽（走 worker / VLM 路径，约 1 分钟一份）"
  $COMPOSE exec -T api mkdir -p /evals/scripts
  $COMPOSE cp "$ROOT/evals/scripts/reextract_all.py" api:/evals/scripts/reextract_all.py
  $COMPOSE exec -T -w /app api python /evals/scripts/reextract_all.py
else
  echo "[2/4] 跳过重抽（--no-reextract）"
fi

echo "[3/4] 导出 predictions（--source db，免登录凭证）"
$COMPOSE exec -T api mkdir -p /evals/dataset /evals/scripts /predictions
$COMPOSE cp "$ANNOTATIONS" api:/evals/dataset/annotations.jsonl
$COMPOSE cp "$ROOT/evals/scripts/export_predictions.py" api:/evals/scripts/export_predictions.py
$COMPOSE exec -T -w /app api python /evals/scripts/export_predictions.py \
  --source db --annotations /evals/dataset/annotations.jsonl --out /predictions

rm -rf "$PRED"
mkdir -p "$PRED"
$COMPOSE cp api:/predictions/. "$PRED/"

echo "[4/4] 评分"
cd "$ROOT"
PY="python"
if [ -x "$ROOT/.venv/Scripts/python.exe" ]; then
  PY="$ROOT/.venv/Scripts/python.exe"
elif [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
fi
PYTHONIOENCODING=utf-8 "$PY" evals/scripts/score_kpi.py selftest
PYTHONIOENCODING=utf-8 "$PY" evals/scripts/score_kpi.py k1k2 \
  --dir "$DATASET" --annotations "$CLEAN" --classes "$CLASSES"

echo "[done] 重测完成。"
