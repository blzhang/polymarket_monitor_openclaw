#!/bin/bash
# Telegram 推送 cron 脚本 - 每分钟执行
cd /Users/zhangbeilong/.openclaw/workspace-polymarket-monitor

TEXT=$(python3 scripts/polymarket_monitor.py scan telegram)

if [ -n "$TEXT" ]; then
    # 输出到 stdout，由 cron 捕获
    echo "$TEXT"
fi
