#!/bin/bash
# cron 兼容的 watchdog 脚本
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

WORKDIR="/Users/zhangbeilong/.openclaw/workspace-polymarket-monitor"

# 读取本地配置（不上传到 git）
CONFIG_FILE="$WORKDIR/config_local.json"
if [ -f "$CONFIG_FILE" ]; then
    WHATSAPP_TARGET=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('whatsapp_target',''))" 2>/dev/null)
    TELEGRAM_TARGET=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('telegram_target',''))" 2>/dev/null)
    TELEGRAM_THREAD=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('telegram_thread_id',''))" 2>/dev/null)
else
    WHATSAPP_TARGET=""
    TELEGRAM_TARGET="-1003692750762"
    TELEGRAM_THREAD="2346"
fi

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

# 推送 Telegram 积压告警
cd "$WORKDIR"
TELEGRAM_TEXT=$(python3 scripts/polymarket_monitor.py scan telegram 2>/dev/null || true)
if [ -n "$TELEGRAM_TEXT" ] && [ -n "$TELEGRAM_TARGET" ]; then
    if [ -n "$TELEGRAM_THREAD" ]; then
        /opt/homebrew/bin/openclaw message send --channel telegram --target "$TELEGRAM_TARGET" --thread-id "$TELEGRAM_THREAD" --message "$TELEGRAM_TEXT" >> "$LOG_FILE" 2>&1 || true
    else
        /opt/homebrew/bin/openclaw message send --channel telegram --target "$TELEGRAM_TARGET" --message "$TELEGRAM_TEXT" >> "$LOG_FILE" 2>&1 || true
    fi
fi

# 推送 WhatsApp 积压告警
WHATSAPP_TEXT=$(python3 scripts/polymarket_monitor.py scan whatsapp 2>/dev/null || true)
if [ -n "$WHATSAPP_TEXT" ] && [ -n "$WHATSAPP_TARGET" ]; then
    /opt/homebrew/bin/openclaw message send --channel whatsapp --target "$WHATSAPP_TARGET" --message "$WHATSAPP_TEXT" >> "$LOG_FILE" 2>&1 || true
fi
