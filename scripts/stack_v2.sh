#!/usr/bin/env bash
# Manage the local FastAPI workbench and the V2 research Worker together.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/data/runtime"
LOG_DIR="$RUNTIME_DIR/logs"
WEB_PID_FILE="$RUNTIME_DIR/stock_agent.web.pid"
WORKER_PID_FILE="$RUNTIME_DIR/stock_agent.worker.pid"
WEB_LOG="$LOG_DIR/web.log"
WORKER_LOG="$LOG_DIR/worker.log"
WEB_HEALTH_URL="http://127.0.0.1:8000/api/v1/health"
AGENT_BIN="$ROOT_DIR/.venv/bin/stock-agent"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

usage() {
  cat <<'EOF'
Usage: ./scripts/stack_v2.sh <start|stop|restart|status|logs>

start   Start the local FastAPI workbench and V2 research Worker.
stop    Stop only processes started by this script.
restart Stop then start script-managed processes.
status  Show process and FastAPI health status.
logs    Follow Web and Worker logs. Press Ctrl+C to stop viewing logs.
EOF
}

load_environment() {
  local env_file="$HOME/.config/stock-agent/env"
  if [[ ! -f "$env_file" ]]; then
    echo "Missing environment file: $env_file" >&2
    exit 1
  fi
  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a
}

require_runtime() {
  if [[ ! -x "$AGENT_BIN" || ! -x "$PYTHON_BIN" ]]; then
    echo "Missing .venv runtime. Install project dependencies before starting." >&2
    exit 1
  fi
}

is_running() {
  local pid_file="$1"
  [[ -f "$pid_file" ]] || return 1
  local pid
  pid="$(<"$pid_file")"
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null
}

clear_stale_pid_file() {
  local pid_file="$1"
  if [[ -f "$pid_file" ]] && ! is_running "$pid_file"; then
    rm -f "$pid_file"
  fi
}

validate_runtime_config() {
  "$PYTHON_BIN" -c '
from pathlib import Path
import os
from stock_agent.config_loader import load_config

llm = load_config(Path.cwd()).config.llm
print(f"llm_provider={llm.provider}")
print(f"llm_model={llm.model}")
print(f"llm_key_present={bool(os.environ.get(llm.api_key_env))}")
'
}

start_web() {
  clear_stale_pid_file "$WEB_PID_FILE"
  if curl --silent --show-error --max-time 1 "$WEB_HEALTH_URL" >/dev/null 2>&1; then
    echo "web_status=already_available url=http://127.0.0.1:8000"
    return
  fi
  if is_running "$WEB_PID_FILE"; then
    echo "web_status=already_running pid=$(<"$WEB_PID_FILE")"
    return
  fi
  (
    cd "$ROOT_DIR"
    exec "$AGENT_BIN" web --host 127.0.0.1 --port 8000
  ) >>"$WEB_LOG" 2>&1 &
  echo "$!" >"$WEB_PID_FILE"
  sleep 1
  if ! is_running "$WEB_PID_FILE"; then
    echo "web_status=failed log=$WEB_LOG" >&2
    tail -n 20 "$WEB_LOG" >&2 || true
    rm -f "$WEB_PID_FILE"
    exit 1
  fi
  echo "web_status=started pid=$(<"$WEB_PID_FILE") url=http://127.0.0.1:8000"
}

start_worker() {
  clear_stale_pid_file "$WORKER_PID_FILE"
  if is_running "$WORKER_PID_FILE"; then
    echo "worker_status=already_running pid=$(<"$WORKER_PID_FILE")"
    return
  fi
  (
    cd "$ROOT_DIR"
    exec "$AGENT_BIN" worker --interval-sec 5
  ) >>"$WORKER_LOG" 2>&1 &
  echo "$!" >"$WORKER_PID_FILE"
  sleep 1
  if ! is_running "$WORKER_PID_FILE"; then
    echo "worker_status=failed log=$WORKER_LOG" >&2
    tail -n 20 "$WORKER_LOG" >&2 || true
    rm -f "$WORKER_PID_FILE"
    exit 1
  fi
  echo "worker_status=started pid=$(<"$WORKER_PID_FILE") interval_sec=5"
}

stop_component() {
  local name="$1"
  local pid_file="$2"
  if ! is_running "$pid_file"; then
    clear_stale_pid_file "$pid_file"
    echo "${name}_status=not_script_managed"
    return
  fi
  local pid
  pid="$(<"$pid_file")"
  kill "$pid"
  for _ in {1..10}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$pid_file"
      echo "${name}_status=stopped pid=$pid"
      return
    fi
    sleep 1
  done
  echo "${name}_status=still_stopping pid=$pid" >&2
  exit 1
}

show_status() {
  if is_running "$WEB_PID_FILE"; then
    echo "web_process=running pid=$(<"$WEB_PID_FILE")"
  else
    echo "web_process=not_script_managed"
  fi
  if is_running "$WORKER_PID_FILE"; then
    echo "worker_process=running pid=$(<"$WORKER_PID_FILE")"
  else
    echo "worker_process=not_script_managed"
  fi
  if curl --silent --show-error --max-time 2 "$WEB_HEALTH_URL" >/dev/null; then
    echo "fastapi_health=reachable"
  else
    echo "fastapi_health=unreachable"
  fi
}

main() {
  local action="${1:-}"
  case "$action" in
    start)
      require_runtime
      load_environment
      mkdir -p "$LOG_DIR"
      cd "$ROOT_DIR"
      validate_runtime_config
      start_web
      start_worker
      ;;
    stop)
      stop_component "worker" "$WORKER_PID_FILE"
      stop_component "web" "$WEB_PID_FILE"
      ;;
    restart)
      stop_component "worker" "$WORKER_PID_FILE"
      stop_component "web" "$WEB_PID_FILE"
      require_runtime
      load_environment
      mkdir -p "$LOG_DIR"
      cd "$ROOT_DIR"
      validate_runtime_config
      start_web
      start_worker
      ;;
    status)
      show_status
      ;;
    logs)
      mkdir -p "$LOG_DIR"
      touch "$WEB_LOG" "$WORKER_LOG"
      tail -n 50 -f "$WEB_LOG" "$WORKER_LOG"
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
}

main "$@"
