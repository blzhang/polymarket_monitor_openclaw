#!/bin/zsh
set -euo pipefail

WORKDIR="/Users/zhangbeilong/.openclaw/workspace-polymarket-monitor"
PID_FILE="$WORKDIR/monitor.pid"
SCRIPT="$WORKDIR/scripts/polymarket_ws_daemon.py"
LOG_FILE="$WORKDIR/monitor.log"

if [[ -f "$PID_FILE" ]]; then
  PID=$(cat "$PID_FILE" 2>/dev/null || true)
  if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
    exit 0
  fi
fi

cd "$WORKDIR"
nohup python3 "$SCRIPT" >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
