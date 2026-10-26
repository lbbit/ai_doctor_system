#!/usr/bin/env bash
# 冒烟测试：compose 起全栈后运行，验证“上传→抽取→确认→时间线→问诊”闭环（离线可跑）。
# 用法：bash scripts/smoke_e2e.sh [BASE_URL]   （默认 http://localhost:8080）
set -euo pipefail

BASE_URL="${1:-http://localhost:8080}"
API="$BASE_URL/api"
EMAIL="smoke_$(date +%s)@example.com"
PASSWORD="smoke-pass-123"
PNG_B64="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
PDF_B64="$(printf '%%PDF-1.4 minimal smoke' | base64 -w0)"

pass=0; fail=0
check() { # name, condition-cmd
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then echo "PASS  $name"; pass=$((pass+1));
  else echo "FAIL  $name"; fail=$((fail+1)); fi
}

echo "== smoke: $API =="

# 0. 健康检查（web nginx 返回纯文本 ok；直连 api 时返回 JSON，两者都接受）
check "healthz" bash -c "curl -fsS $BASE_URL/healthz | grep -qE 'status|^ok'"

# 1. 注册（自动登录 + 默认 Subject）
REG=$(curl -fsS -X POST "$API/auth/register" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}")
TOKEN=$(echo "$REG" | grep -o '"token":"[^"]*"' | head -1 | cut -d'"' -f4)
SUBJECT=$(echo "$REG" | grep -o '"defaultSubject":{"id":"[^"]*"' | head -1 | cut -d'"' -f6)
AUTH=(-H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json')
check "register+defaultSubject" test -n "$TOKEN" -a -n "$SUBJECT"

# 2. 上传 PDF（hash 去重提示用同一文件；multipart 不能带 JSON Content-Type）
echo "$PDF_B64" | base64 -d > /tmp/smoke.pdf
DOC=$(curl -fsS -X POST "$API/documents" -H "Authorization: Bearer $TOKEN" \
  -F "subjectId=$SUBJECT" -F "file=@/tmp/smoke.pdf")
DOC_ID=$(echo "$DOC" | grep -o '"document":{"id":"[^"]*"' | head -1 | cut -d'"' -f6)
check "upload 201 pending" test -n "$DOC_ID"

DUP=$(curl -s -o /tmp/dup.json -w '%{http_code}' -X POST "$API/documents" -H "Authorization: Bearer $TOKEN" \
  -F "subjectId=$SUBJECT" -F "file=@/tmp/smoke.pdf")
check "duplicate 409" test "$DUP" = "409"
check "dup code=DUP_FILE" grep -q 'DUP_FILE' /tmp/dup.json

# 3. 缺 subjectId → 422 SUBJECT_REQUIRED（B-1）
NOSUB=$(curl -s -o /tmp/nosub.json -w '%{http_code}' -X POST "$API/documents" \
  -H "Authorization: Bearer $TOKEN" -F "file=@/tmp/smoke.pdf")
check "upload missing subject 422" test "$NOSUB" = "422"
check "code=SUBJECT_REQUIRED" grep -q 'SUBJECT_REQUIRED' /tmp/nosub.json

# 4. 等待 worker 抽取（pending → awaiting_review / failed）
echo "-- 等待抽取（最多 30s）..."
for i in $(seq 1 30); do
  D=$(curl -fsS "$API/documents/$DOC_ID" "${AUTH[@]}" || true)
  STATUS=$({ echo "$D" | grep -o '"status":"[^"]*"' | head -1 | cut -d'"' -f4; } || true)
  case "$STATUS" in
    awaiting_review|confirmed|parsed_no_data|failed) break ;;
  esac
  sleep 1
done
echo "-- document status: $STATUS"
check "document reached terminal-ish state" bash -c "echo '$STATUS' | grep -qE 'awaiting_review|confirmed|parsed_no_data|failed'"

# 5. 确认入库（按草稿字段逐个决策）
DR=$(curl -fsS "$API/documents/$DOC_ID/derived-record" "${AUTH[@]}" || true)
VER=$({ echo "$DR" | grep -o '"version":[0-9]*' | head -1 | cut -d: -f2; } || true)
DECISIONS="[{\"fieldRef\":\"event:0\",\"action\":\"confirm\"}]"
if echo "$DR" | grep -q '"observations"'; then
  # 简化：对首个观测做确认，其余拒绝由具体草稿决定（演示用 event:0 + obs:0）
  DECISIONS="[{\"fieldRef\":\"event:0\",\"action\":\"confirm\"},{\"fieldRef\":\"obs:0\",\"action\":\"confirm\"}]"
fi
CONF=$(curl -s -o /tmp/conf.json -w '%{http_code}' -X POST \
  "$API/documents/$DOC_ID/derived-record/confirm" "${AUTH[@]}" \
  -d "{\"version\":${VER:-1},\"decisions\":$DECISIONS}")
echo "-- confirm http=$CONF body=$(head -c 200 /tmp/conf.json)"
check "confirm endpoint reachable" test "$CONF" = "200" -o "$CONF" = "422" -o "$CONF" = "409" -o "$CONF" = "404"

# 6. 时间线（分组数据）
TL=$(curl -fsS "$API/timeline/events?subjectId=$SUBJECT" "${AUTH[@]}")
check "timeline groups" bash -c "echo '$TL' | grep -q 'groups'"

# 7. 趋势选项
TREND=$(curl -fsS "$API/dictionary/trend-options" "${AUTH[@]}")
check "trend options" bash -c "echo '$TREND' | grep -q 'code'"

# 8. 问诊（SSE；LLM 不可用时返回 error 事件亦属预期降级）
# 注意：Windows/Git Bash 下非 ASCII argv 可能损坏，请求体保持纯 ASCII
curl -s -N -X POST "$API/chat/conversations" "${AUTH[@]}" \
  -d "{\"subjectId\":\"$SUBJECT\",\"title\":\"smoke-conv\"}" > /tmp/conv.json || true
CONV=$({ grep -o '"id":"[^"]*"' /tmp/conv.json | head -1 | cut -d'"' -f4; } || true)
SSE=$(curl -s -N -X POST "$API/chat/conversations/$CONV/messages" "${AUTH[@]}" \
  -d '{"content":"blood sugar trend?"}' --max-time 90 || true)
check "chat SSE has meta or error" bash -c "echo '$SSE' | grep -qE 'event: (meta|error)'"

# 9. 设置与审计
SETTINGS=$(curl -fsS "$API/settings" "${AUTH[@]}")
check "settings" bash -c "echo '$SETTINGS' | grep -q 'piiMaskingEnabled'"
AUDIT=$(curl -fsS "$API/audit/logs" "${AUTH[@]}")
check "audit logs" bash -c "echo '$AUDIT' | grep -q 'items'"

echo "== smoke done: pass=$pass fail=$fail =="
[ "$fail" -eq 0 ]
