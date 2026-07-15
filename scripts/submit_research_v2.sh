#!/usr/bin/env bash
# Submit one bounded V2 research task through the local FastAPI entrypoint.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
API_URL="http://127.0.0.1:8000/api/v2/research"

usage() {
  cat <<'EOF'
Usage: ./scripts/submit_research_v2.sh --symbol SYMBOL --question TEXT [options]

Options:
  --days DAYS            Historical lookback window, default: 30.
  --report-type TYPE     facts, anomaly, macro, signal, or full; default: full.
  --current-data         Require current data instead of historical-only evidence.
  --no-news-features     Disable news-derived signal features.
  --help                 Show this help text.

Examples:
  ./scripts/submit_research_v2.sh \
    --symbol QQQ \
    --days 30 \
    --question "结合近一个月的价格、成交量与新闻，分析 QQQ 的异动是否具备持续性，并列出证据不足之处。"
EOF
}

symbol=""
question=""
days="30"
report_type="full"
require_current_data="false"
allow_news_features="true"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --symbol)
      symbol="${2:-}"
      shift 2
      ;;
    --question)
      question="${2:-}"
      shift 2
      ;;
    --days)
      days="${2:-}"
      shift 2
      ;;
    --report-type)
      report_type="${2:-}"
      shift 2
      ;;
    --current-data)
      require_current_data="true"
      shift
      ;;
    --no-news-features)
      allow_news_features="false"
      shift
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Missing .venv runtime. Install project dependencies before submitting a task." >&2
  exit 1
fi
if [[ -z "$symbol" || -z "$question" ]]; then
  echo "Both --symbol and --question are required." >&2
  usage >&2
  exit 2
fi
if ! [[ "$days" =~ ^[1-9][0-9]*$ ]]; then
  echo "--days must be a positive integer." >&2
  exit 2
fi
case "$report_type" in
  facts|anomaly|macro|signal|full) ;;
  *)
    echo "--report-type must be facts, anomaly, macro, signal, or full." >&2
    exit 2
    ;;
esac
if ! curl --silent --show-error --max-time 2 "http://127.0.0.1:8000/api/v2/health" >/dev/null; then
  echo "FastAPI is unavailable. Run ./scripts/stack_v2.sh start first." >&2
  exit 1
fi

request_json="$("$PYTHON_BIN" - "$symbol" "$question" "$days" "$report_type" "$require_current_data" "$allow_news_features" <<'PY'
from datetime import UTC, datetime, timedelta
import json
import sys
from uuid import uuid4

symbol, question, days, report_type, current_data, news_features = sys.argv[1:]
to_ts = datetime.now(UTC)
from_ts = to_ts - timedelta(days=int(days))
request = {
    "request_id": f"script-{uuid4().hex}",
    "question": question,
    "symbols": [symbol.upper()],
    "time_window": {
        "from_ts": from_ts.isoformat().replace("+00:00", "Z"),
        "to_ts": to_ts.isoformat().replace("+00:00", "Z"),
        "timezone": "America/New_York",
    },
    "report_type": report_type,
    "constraints": {
        "allow_mcp": False,
        "allow_news_features": news_features == "true",
        "require_current_data": current_data == "true",
    },
}
print(json.dumps({"request": request}, ensure_ascii=False))
PY
)"

response_file="$(mktemp "${TMPDIR:-/tmp}/stock-agent-response.XXXXXX")"
trap 'rm -f "$response_file"' EXIT
http_status="$(curl --silent --show-error -o "$response_file" -w '%{http_code}' -X POST "$API_URL" -H "Content-Type: application/json" --data "$request_json")"
if [[ ! "$http_status" =~ ^2 ]]; then
  echo "FastAPI rejected the research request (HTTP $http_status)." >&2
  "$PYTHON_BIN" - "$response_file" <<'PY' >&2
import json
import sys

try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    print("No structured error response was returned.")
else:
    print(payload.get("detail", payload))
PY
  exit 1
fi
response="$(<"$response_file")"
echo "$response" | "$PYTHON_BIN" -c '
import json
import sys

payload = json.load(sys.stdin)
task = payload["task"]
print(f"research_status=submitted")
print("task_id=" + task["task_id"])
print("symbol=" + task["request"]["symbols"][0])
print("report_type=" + task["request"]["report_type"])
print("next=Use ./scripts/stack_v2.sh status, then open http://127.0.0.1:8000")
'
