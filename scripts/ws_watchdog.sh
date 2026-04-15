#!/bin/bash
# cron 兼容的 watchdog 脚本
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

WORKDIR="/Users/zhangbeilong/.openclaw/workspace-polymarket-monitor"
PID_FILE="$WORKDIR/monitor.pid"
SCRIPT="$WORKDIR/scripts/polymarket_ws_daemon.py"
LOG_FILE="$WORKDIR/monitor.log"

# 检查进程是否存在，不存在则重启
if [ -f "$PID_FILE" ]; then
  PID=$(cat "$PID_FILE" 2>/dev/null || true)
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    : # 进程正常运行，跳过重启
  else
    cd "$WORKDIR"
    nohup python3 "$SCRIPT" >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
  fi
else
  cd "$WORKDIR"
  nohup python3 "$SCRIPT" >> "$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
fi

# 推送 Telegram 积压告警（论坛需要 thread-id）
cd "$WORKDIR"
TELEGRAM_TEXT=$(python3 scripts/polymarket_monitor.py scan telegram 2>/dev/null || true)
if [ -n "$TELEGRAM_TEXT" ]; then
    /opt/homebrew/bin/openclaw message send --channel telegram --target "-1003692750762" --thread-id "2346" --message "$TELEGRAM_TEXT" >> "$LOG_FILE" 2>&1 || true
fi

# 推送 WhatsApp 积压告警（需要配置 target 号码）
# WHATSAPP_TARGET="+8613800138000"  # ← 在这里配置你的 WhatsApp 号码
WHATSAPP_TEXT=$(python3 scripts/polymarket_monitor.py scan whatsapp 2>/dev/null || true)
if [ -n "$WHATSAPP_TEXT" ] && [ -n "${WHATSAPP_TARGET:-}" ]; then
    /opt/homebrew/bin/openclaw message send --channel whatsapp --target "$WHATSAPP_TARGET" --message "$WHATSAPP_TEXT" >> "$LOG_FILE" 2>&1 || true
fi
