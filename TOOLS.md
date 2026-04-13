# TOOLS.md - Local Notes

## Polymarket 监控配置

### 目标事件
- **事件**: Iran x Israel/US conflict ends by...?
- **Slug**: `iran-x-israelus-conflict-ends-by`
- **监控市场**: 6 个活跃市场（自动过滤已到期）

### 检测阈值
- **相对变化**: 5 分钟内涨跌幅 > 10%
- **绝对变化**: 5 分钟内价格变化 > 0.05
- **成交量**: 5 分钟内单方向成交 > $100,000

### 去重规则
- 同一市场 1 分钟内不重复推送相同类型异动

### 推送渠道
- **WhatsApp**: 默认群组（需先链接账户）
- **命令**: `openclaw channels login --channel whatsapp`

### WebSocket 端点
- **URL**: `wss://ws-subscriptions-clob.polymarket.com/ws/market`
- **订阅**: `{"assets_ids": [...], "type": "market", "custom_feature_enabled": true}`

### 服务管理
- **主进程**: `python3 scripts/polymarket_ws_daemon.py`
- **看门狗**: `./scripts/ws_watchdog.sh`（cron 每分钟调用）
- **兼容 cron 入口**: `python3 scripts/polymarket_monitor.py scan|summary`
- **日志**: `monitor.log`
- **PID 文件**: `monitor.pid`

### 文件结构
```
/workspace-polymarket-monitor/
├── scripts/
│   ├── polymarket_ws_daemon.py    # 常驻 WS 采集
│   ├── polymarket_monitor.py      # cron 兼容入口
│   ├── polymarket_broadcast.py    # outbox/summary 输出
│   └── ws_watchdog.sh             # 看门狗脚本
├── venv/                          # Python 虚拟环境
├── requirements.txt               # 依赖
├── poll_state.json                # 统一状态文件
├── alert_outbox.json              # 异动播报 outbox
├── monitor.log                    # 运行日志
└── monitor.pid                    # 进程 ID
```
