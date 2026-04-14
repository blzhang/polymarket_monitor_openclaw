#!/bin/bash
# Telegram 推送脚本 - 从 telegram_outbox.json 读取告警并推送到 Telegram
# 用法：./telegram_push.sh

cd /Users/zhangbeilong/.openclaw/workspace-polymarket-monitor

# 检查是否有待推送的告警
TEXT=$(python3 scripts/polymarket_monitor.py scan telegram)

if [ -n "$TEXT" ]; then
    # 通过 openclaw 发送到当前 Telegram 群组
    # 使用 session_send 或直接回复到当前会话
    echo "$TEXT"
fi
