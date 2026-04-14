# Polymarket Monitor - OpenClaw Cron 方案

## 目标

这个 agent 只做两件事：

1. **每 1 分钟扫描一次异动**，命中阈值就立刻播报到 WhatsApp
2. **每天 10:00 / 22:00 做一次全盘播报**，汇总所有关注标的的 1 小时、12 小时、24 小时变化和 24 小时交易量

## 异动定义

在 5 分钟窗口内，以下三个条件是“或”关系，满足任意一个就告警：

- 相对变化 >= 10%
- 绝对变化 >= 0.05
- 成交额增量 >= 100,000 USD

## 标的维护

维护文件：`watchlist.json`

- 新增标的：往 `markets` 里加一项
- 删除标的：删除该项或把 `enabled` 设为 `false`
- 不播报已过期标的：可填写 `expiresAt`，过期后自动忽略

## 脚本

- 常驻采集：`./scripts/ws_watchdog.sh`
- 1 分钟异动播报：`python3 scripts/polymarket_monitor.py scan`（读取统一 `poll_state.json` / `alert_outbox.json`）
- 10:00 / 22:00 全盘播报：`python3 scripts/polymarket_monitor.py summary`（读取统一 `poll_state.json`）

## OpenClaw cron

建议拆成三条 job：

### 1) 常驻采集看门狗
- `everyMs: 60000`
- command: `./scripts/ws_watchdog.sh`

### 2) 1分钟异动播报
- `everyMs: 60000`
- delivery -> WhatsApp 群

### 3) 10:00 / 22:00 全盘播报
- cron: `0 10,22 * * *` @ `Asia/Shanghai`
- delivery -> WhatsApp 群

## 说明

- 常态路径默认不用 LLM 做研究或摘要。
- `scripts/polymarket_monitor.py` 是兼容入口，读取 daemon 维护的同一套 state / outbox，不再维护独立状态文件。
- cron 仍通过 agentTurn 驱动，但输出应只保留脚本 stdout。
- 如果脚本输出 `NO_REPLY`，则保持静默。
