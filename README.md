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

### 前置过滤：信息熵检查（所有告警通用）

在进入任何告警判断之前，先计算市场的**香农信息熵**：

```
H(p) = -p·log₂(p) - (1-p)·log₂(1-p)
```

其中 `p` 是 YES 的当前价格。H 越低，市场结果越确定，价格波动越没有信息含量。

**当 H < 0.25 时，跳过所有告警（快异动、慢趋势均静默）。**

对应的价格边界如下：

| YES 价格 | 信息熵 H | 是否告警 |
|---------|---------|---------|
| 0.35%   | 0.034   | ❌ 静默 |
| 2%      | 0.141   | ❌ 静默 |
| 4%      | 0.242   | ❌ 静默（临界） |
| 5%      | 0.286   | ✅ 正常 |
| 10%     | 0.469   | ✅ 正常 |
| 50%     | 1.000   | ✅ 正常 |
| 90%     | 0.469   | ✅ 正常 |
| 95%     | 0.286   | ✅ 正常 |
| 96%     | 0.242   | ❌ 静默（临界） |
| 98%     | 0.141   | ❌ 静默 |
| 99.65%  | 0.034   | ❌ 静默 |

**设计理由**：纯粹的价格截断（如"YES < 2%"）会漏掉 3%~5% 区间的有效信号；信息熵是对"市场还有多少不确定性"的连续度量，天然适配"末日期权"场景——临近到期日的标的往往已经从低概率区域进入确定性结果，此时的价格噪音没有决策价值。

---

### 第一层：快异动告警（5 分钟窗口）

**触发条件**（以下两个条件同时满足）：

1. **绝对变化 ≥ 5%**（YES 价格在 5 分钟内绝对变化超过 5 个百分点）
2. **5 分钟成交额增量 ≥ $100,000**

同时满足才触发，避免无成交量的虚假价格跳动。

**去重**：同一市场 60 秒内只发 1 次快异动告警。

**告警格式**：
```
💰 Polymarket 成交异动: {标的名称}
事件：{标的名称}
到期：{到期日}（如有）
YES：{display 价格}
NO：{display 价格}
最新成交价（YES）：{WS 中间价}
近5分钟相对变化：{rel_change}
近5分钟绝对变化：{abs_change}
近5分钟新增成交额：${volume_delta_5m}
触发条件：{命中的具体条件}
时间：{时间}
链接：{市场 URL}
```

---

### 第二层：慢趋势告警（多时间窗口）

用于捕获"没有单次大跳变，但在几小时内持续 repricing"的情况。

**触发前置检查**：
1. 信息熵 H ≥ 0.25（见上）
2. 同一市场 30 分钟内只发 1 次慢趋势告警（市场级去重）

**三个窗口规则**（同时检查，只发最长窗口的告警）：

| 窗口 | 相对变化阈值 | 绝对变化下限 |
|------|------------|------------|
| 30 分钟 | ≥ 25% | ≥ 2% |
| 1 小时 | ≥ 40% | ≥ 2% |
| 6 小时 | ≥ 60% | ≥ 2% |

**绝对变化下限（2%）的必要性**：相对变化在低价区会虚高，例如 YES 从 3%→4% 相对变化是 +33%，远超 25% 阈值，但实际只动了 1 个百分点，毫无意义。绝对变化下限兜住了这类情况。

**多窗口同时触发时的处理**：如果 30 分钟和 1 小时同时满足，只发 1 小时（最长）的告警，所有满足窗口的去重 key 同时标记，避免短窗口随后补发造成重复。

**baseline 选取逻辑**：
- 优先找 ±20% 时间误差范围内的历史数据点（例如 30 分钟窗口，找 24~36 分钟前的数据点）
- 找不到时 fallback 到最后一个早于目标时间的数据点
- **fallback 最多允许 1.5 倍窗口时长**（例如 30 分钟窗口最多用 45 分钟前的数据），超出则放弃计算，避免用过旧数据导致误报

**告警格式**：
```
📈 Polymarket 缓慢上行: {标的名称}   （或 📉 缓慢下行）
事件：{标的名称}
YES：{display 价格}
NO：{display 价格}
当前成交价（YES）：{WS 中间价}
对比窗口：{最长触发窗口}
窗口起点价格：{baseline 价格}
窗口相对变化：{rel_change}
窗口绝对变化：{abs_change}
触发条件：{窗口}累计变化达到 {rel_change}
时间：{时间}
链接：{市场 URL}
```

---

### 第三层：高概率阈值告警（90% 首次突破）

用于捕获"某个标的概率首次进入极高区间"这一一次性事件。

**触发条件**：YES 价格 **首次达到或超过 90%**

**武装机制（防止刷屏）**：
- 触发一次后，标记 `high_prob_alerted = True`，不再重复告警
- 只有 YES 价格回落到 **85% 或以下**，才重新武装（`high_prob_alerted = False`）
- 下次再次突破 90% 时，才会再发一条

**注意**：高概率告警**不受信息熵过滤**，因为它本身就是在捕获高确定性状态的"首次进入"事件。

**告警格式**：
```
🚨 Polymarket 高概率阈值: {标的名称}
事件：{标的名称}
YES：{display 价格}
NO：{display 价格}
当前成交价（YES）：{WS 中间价}
触发条件：YES 概率达到或超过 90%
时间：{时间}
链接：{市场 URL}
```

---

## 告警触发流程图

```
收到 WS price_change 事件
         ↓
  归一化为 YES 价格
         ↓
  计算信息熵 H(p)
         ↓
  ┌──────┴──────────────────────┐
  H < 0.25?                    H ≥ 0.25?
  (市场已定局)                  (市场有不确定性)
  ↓                             ↓
  记录历史，                    ┌────────────────────┐
  跳过全部告警                  ↓                    ↓
                          快异动检查            高概率检查
                          绝对变化≥5%           YES≥90%
                          + 成交量≥$10万         首次触发?
                          + 去重60秒            武装机制
                               ↓                    ↓
                          慢趋势检查             发送告警
                          多窗口判断
                          + 绝对变化≥2%
                          + 市场去重30分钟
                          只发最长窗口
```

---

## 关键常量（`polymarket_ws_daemon.py`）

| 常量 | 值 | 说明 |
|------|----|------|
| `RELATIVE_THRESHOLD` | 0.10 | 快异动相对变化阈值（10%，仅用于 reasons 展示） |
| `ABSOLUTE_THRESHOLD` | 0.05 | 快异动绝对变化阈值（5%，实际触发条件） |
| `MIN_VOLUME_DELTA` | 100,000 | 快异动 5 分钟成交额最低要求（USD） |
| `WINDOW_SECONDS` | 300 | 快异动时间窗口（5 分钟） |
| `DEDUP_SECONDS` | 60 | 快异动去重冷却时间 |
| `SLOW_TREND_RULES` | (1800, 0.25), (3600, 0.40), (21600, 0.60) | 慢趋势规则：(窗口秒数, 相对阈值) |
| `SLOW_TREND_MIN_ABS` | 0.02 | 慢趋势绝对变化下限（2%） |
| `HIGH_PROB_THRESHOLD` | 0.90 | 高概率告警触发线 |
| `HIGH_PROB_REARM_THRESHOLD` | 0.85 | 高概率告警重新武装线 |
| `ENTROPY_THRESHOLD` | 0.25 | 信息熵过滤阈值（约对应 YES < 4% 或 > 96%） |
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
