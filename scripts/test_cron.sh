#!/bin/bash
echo "=== Cron 测试 $(date) ===" >> /tmp/cron_test.log
echo "PATH: $PATH" >> /tmp/cron_test.log
which openclaw >> /tmp/cron_test.log 2>&1
cd /Users/zhangbeilong/.openclaw/workspace-polymarket-monitor
TEXT=$(python3 scripts/polymarket_monitor.py scan telegram 2>&1 | head -c 500)
echo "TEXT 长度：${#TEXT}" >> /tmp/cron_test.log
if [ -n "$TEXT" ]; then
    /opt/homebrew/bin/openclaw message send --channel telegram --target "-1003692750762" --thread-id "2346" --message "Cron 测试：$TEXT" >> /tmp/cron_test.log 2>&1
    echo "发送完成" >> /tmp/cron_test.log
fi
