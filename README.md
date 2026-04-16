# Polymarket Monitor

这是一个运行在 OpenClaw 上的 Polymarket 实时监控 agent。

目标很简单，不做花哨摘要，专注两件事：

1. 监控关注标的的价格与成交额变化
2. 在真正有异动时，把有价值的信息发到 WhatsApp / Telegram，而不是刷屏

---

## 这个 agent 是干嘛的

它主要用于监控地缘政治 / 宏观叙事相关的 Polymarket 标的，尤其是当前关心的：

- 霍尔木兹海峡航运恢复正常相关标的
- 伊朗 / 以色列 / 美国冲突结束或停火相关标的
- 美伊永久和平协议相关标的
- 特朗普宣布解除霍尔木兹封锁相关标的

它的职责不是做研究报告，而是做 **市场定价异动捕获器**：

- 突然跳变时，及时告警
- 缓慢爬升 / 缓慢下跌时，也能捕获
- 告警里直接说明为什么触发

---

## 当前 watchlist

维护文件：`watchlist.json`（workspace 目录下）

当前关注的事件 slug：

- `strait-of-hormuz-traffic-returns-to-normal-by-april-30`
- `strait-of-hormuz-traffic-returns-to-normal-by-end-of-may`
- `iran-x-israelus-conflict-ends-by`
- `us-x-iran-permanent-peace-deal-by`
- `trump-announces-us-blockade-of-hormuz-lifted-by`

说明：

- `enabled: false` 的项目不会被监控
- 设置了 `expiresAt` 的项目，过期后自动忽略
- 每个事件 slug 可能对应多个子市场（如"4月底前解除"、"5月底前解除"），每个子市场独立监控
- watchlist 每 5 分钟热更新一次，变更后自动重连 WebSocket

---

## 系统架构

```
WebSocket (实时价格)
     ↓
 process_trade()          ←── 每次 WS 推送都调用
     ├── 归一化为 YES 价格视角
     ├── 写入 history（价格时间序列）
     ├── 快异动检查（5 分钟窗口）
     ├── 慢趋势检查（30分钟 / 1小时 / 6小时）
     └── 高概率阈值检查

HTTP snapshot_refresher()  ←── 每 30 秒运行一次
     ├── 获取 outcomePrices（display 展示价）
     ├── 获取 volume24hr（计算 volume_delta_5m）
     ├── 高概率阈值检查（用 display 价）
     └── 慢趋势检查（用 display 价）

alert_outbox.json / telegram_outbox.json
     ↓
 polymarket_broadcast.py  ←── 被 OpenClaw 定时调用，读取并发送
```

### 主要脚本

| 文件 | 职责 |
|------|------|
| `scripts/polymarket_ws_daemon.py` | 常驻主进程，WebSocket 监听 + 告警写入 |
| `scripts/polymarket_broadcast.py` | 读取 outbox，生成 summary，被外部调用 |
| `scripts/ws_watchdog.sh` | 看门狗，daemon 死掉时自动拉起 |

### 状态文件

| 文件 | 内容 |
|------|------|
| `poll_state.json` | 每个市场的价格历史、display 价、成交量快照、去重记录 |
| `alert_outbox.json` | 待发 WhatsApp 告警队列 |
| `telegram_outbox.json` | 待发 Telegram 告警队列 |
| `summary_outbox.json` | 全盘播报缓冲 |

---

## 价格数据来源

### WebSocket（实时触发）

- 地址：`wss://ws-subscriptions-clob.polymarket.com/ws/market`
- 事件类型：`price_change`
- **价格取值**：`(best_bid + best_ask) / 2`，即 orderbook 买卖中间价
  - 注意：不使用 `price` 字段（那是单笔订单价，可能是任意限价单，与网页显示不符）
- YES/NO 归一化：根据 `yes_token_id` / `no_token_id` 判断收到的是哪一侧，NO token 的价格转换为 `1 - price`

### HTTP Snapshot（每 30 秒补充）

- 地址：`https://gamma-api.polymarket.com/events?slug={slug}`
- 用途：
  - `outcomePrices`：orderbook 报价，作为告警中展示的 display 价格
  - `volume24hr`：用于计算 5 分钟成交额增量

---

## 告警逻辑详解

### 前置过滤一：末日模式（距到期 ≤ 3 天）

距到期天数通过市场 `endDate` 字段计算，所有告警判断的第一道关卡。

| 距到期 | 模式 | 行为 |
|--------|------|------|
| > 3 天 | 正常 | 走后续全套告警判断 |
| ≤ 3 天 | 末日模式 | 只发一次末日播报，之后全静默；仅反转 ≥ 10% 时例外 |

**末日播报（首次进入末日模式时发一次）**：
```
⏰ Polymarket 末日播报: {标的名称}
事件：{标的名称}
YES：{价格}  NO：{价格}
当前成交价（YES）：{价格}
距到期：{N}天
说明：标的临近到期，此后仅在出现重大反转时再次通知
时间：{时间}  链接：{URL}
```

**末日反转告警（末日模式中价格从锚点移动 ≥ 10%）**：
```
⚡📈 Polymarket 末日反转: {标的名称}   （或 ⚡📉）
反转前价格：{锚点价格}
价格变化：{+/-N%}
距到期：{N}天
```
每次触发后锚点更新为当前价，继续守候下一次反转。

**设计理由**：临近到期的标的，正常的价格波动（5%→9%→5%）完全是噪音，不值得关注；但真正的反转（谈判破裂、突发事件）绝对值变化会很大，10% 的绝对门槛能有效区分。

---

### 前置过滤二：动态信息熵

计算市场的**香农信息熵**：`H(p) = -p·log₂(p) - (1-p)·log₂(1-p)`

H 越低代表市场越确定。阈值**根据距到期天数动态收紧**：

| 距到期 | 熵阈值 | YES 有效区间（近似） |
|--------|--------|-------------------|
| > 7 天 | 0.25   | 4% ~ 96% |
| 3 ~ 7 天 | 0.50 | 11% ~ 89% |
| 1 ~ 3 天 | 0.72 | 25% ~ 75% |
| < 1 天 | 2.0（全静默） | — |

低于阈值时，记录历史但跳过快异动和慢趋势告警（高概率告警不受此过滤）。

---

### 第一层：快异动告警（5 分钟窗口）

**触发条件**（同时满足）：
1. 绝对变化 ≥ 5%（5 分钟内 YES 价格移动超过 5 个百分点）
2. 5 分钟成交额增量 ≥ $100,000

**去重机制（双重）**：
- **方向冷却**：同一方向（up/down）2 小时内只发 1 次
- **价格锚**：上次告警后，价格需再移动 ≥ 5% 才允许重发（即使冷却期过了，价格没动也不发）

**告警格式**：
```
💰 Polymarket 成交异动: {标的名称}
YES：{price}  NO：{price}
最新成交价（YES）：{price}
近5分钟相对/绝对变化 + 成交额
触发条件：{具体命中项}
```

---

### 第二层：慢趋势告警（多时间窗口）

**触发前置**：信息熵 ≥ 动态阈值（见上）

**三个窗口规则**（只发最长触发窗口的告警）：

| 窗口 | 相对变化阈值 | 绝对变化下限 |
|------|------------|------------|
| 30 分钟 | ≥ 25% | ≥ 2% |
| 1 小时  | ≥ 40% | ≥ 2% |
| 6 小时  | ≥ 60% | ≥ 2% |

绝对变化下限防止低价区误报（如 YES 3%→4% 相对+33% 但绝对只有 1%）。

**去重机制（双重）**：
- **方向冷却**：同一方向 6 小时内只发 1 次
- **价格锚**：上次告警后，价格需再移动 ≥ 8% 才允许重发

多窗口同时满足时，所有窗口的去重 key 同时标记，只发最长窗口。

**baseline 选取**：优先找 ±20% 时间误差内的数据点；fallback 最多 1.5x 窗口时长，超出放弃计算。

**告警格式**：
```
📈 Polymarket 缓慢上行: {标的名称}   （或 📉 缓慢下行）
对比窗口 + 起点/终点价格 + 相对/绝对变化
```

---

### 第三层：高概率阈值告警（90% 首次突破）

**触发条件**：YES 价格首次达到或超过 90%

**武装机制**：
- 触发后锁定，不重复告警
- YES 回落至 85% 以下才重新武装
- **不受信息熵过滤**（高概率进入本身就是有价值的事件）

**告警格式**：
```
🚨 Polymarket 高概率阈值: {标的名称}
触发条件：YES 概率达到或超过 90%
```

---

## 完整判断流程图

```
收到价格信号（WS 或 HTTP snapshot）
         ↓
  记录历史（始终执行）
         ↓
  ┌─ 距到期 ≤ 3 天？─────────────────────────────────────────────┐
  │ YES（末日模式）                                               │ NO
  │                                                              ↓
  │  expiry_alert_sent = False？                        动态熵 H < 阈值？
  │    ↓ YES               ↓ NO                           ↓ YES      ↓ NO
  │  发末日播报          检测反转                        静默      继续判断
  │  锁定 sent=True      |当前-锚点| ≥ 10%？                          ↓
  │  记录锚点价格           ↓ YES                         ┌─────────────────┐
  │                      发反转告警                       ↓                 ↓
  │                      更新锚点                    快异动检查         高概率检查
  │                                               绝对变化≥5%          YES≥90%
  └──────────────────────────────────────────    + 成交量≥$10万        首次触发
                                                 + 方向冷却2h           武装机制
                                                 + 价格锚5%                ↓
                                                       ↓             发送告警
                                                  慢趋势检查
                                                 多窗口判断
                                                 + 绝对变化≥2%
                                                 + 方向冷却6h
                                                 + 价格锚8%
                                                 只发最长窗口
```

---

## 关键常量（`polymarket_ws_daemon.py`）

| 常量 | 值 | 说明 |
|------|----|------|
| `ABSOLUTE_THRESHOLD` | 0.05 | 快异动绝对变化触发线（5%） |
| `RELATIVE_THRESHOLD` | 0.10 | 快异动相对变化（仅用于 reasons 展示） |
| `MIN_VOLUME_DELTA` | 100,000 | 快异动 5 分钟成交额最低要求（USD） |
| `WINDOW_SECONDS` | 300 | 快异动时间窗口（5 分钟） |
| `FAST_ALERT_COOLDOWN` | 7,200 | 快异动同方向冷却（2 小时） |
| `FAST_ALERT_MIN_MOVE` | 0.05 | 快异动价格锚最小移动（5%） |
| `SLOW_TREND_RULES` | (1800,0.25),(3600,0.40),(21600,0.60) | 慢趋势：(窗口秒, 相对阈值) |
| `SLOW_TREND_MIN_ABS` | 0.02 | 慢趋势绝对变化下限（2%） |
| `SLOW_ALERT_COOLDOWN` | 21,600 | 慢趋势同方向冷却（6 小时） |
| `SLOW_ALERT_MIN_MOVE` | 0.08 | 慢趋势价格锚最小移动（8%） |
| `HIGH_PROB_THRESHOLD` | 0.90 | 高概率告警触发线 |
| `HIGH_PROB_REARM_THRESHOLD` | 0.85 | 高概率告警重新武装线 |
| `ENTROPY_THRESHOLD` | 0.25 | 默认信息熵阈值（远期标的，约 YES 4%~96%） |
| `EXPIRY_MODE_DAYS` | 3 | 末日模式触发（距到期 ≤ N 天） |
| `EXPIRY_REVERSAL_ABS` | 0.10 | 末日反转阈值（绝对变化 ≥ 10%） |
| `MAX_HISTORY_HOURS` | 30 | 历史数据保留时长 |
| `REBUILD_WATCHLIST_SECONDS` | 300 | watchlist 热更新间隔（5 分钟） |

---

## 成交量逻辑

WebSocket 的 `size` 字段不可靠（可能是累计值或放大值），不直接用于成交量判断。

**5 分钟成交额增量的计算方式**：

1. HTTP snapshot_refresher 每 30 秒拉一次 `volume24hr`
2. 保存到 `volume_snapshots`（保留最近 10 分钟的快照）
3. `volume_delta_5m = 当前 volume24hr - 5 分钟前的 volume24hr`

这样虽然精度只有 30 秒粒度，但来源可靠。

---

## 运行与维护

### 启动 daemon

```bash
python scripts/polymarket_ws_daemon.py
```

### 看门狗

```bash
./scripts/ws_watchdog.sh
```

### 重置状态（慎用，会清空历史数据）

```bash
python3 reset_state.py
```

重置场景：daemon 长时间停止后重启，旧 history 数据可能是用错误价格（订单价而非中间价）记录的，建议重置后重新积累。

### 语法校验

```bash
python3 -m py_compile scripts/polymarket_ws_daemon.py
```

---

## 设计原则

**少发，但发出来就要值得看。**

- 不做额外的定时 regime 播报
- 不做研究型长摘要
- 告警里必须写清楚触发原因
- 宁可漏报，不要误报（所有过滤都是往"更严"的方向调）

---

## 已知限制与待优化项

### P1
- WebSocket 主循环中 snapshot_refresher 使用同步 HTTP 请求，可能阻塞事件循环
- watchlist 热更新后应更优雅地处理订阅变更（目前是直接重连）

### P2
- 测试覆盖不足，核心告警逻辑（熵过滤、慢趋势窗口、武装机制）缺乏单元测试
- state 文件长期运行后体积增长（history 保留 30 小时，高频标的可能数千条记录）
