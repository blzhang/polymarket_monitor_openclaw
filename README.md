# Polymarket Monitor

这是一个面向 Jacob 的 Polymarket 实时监控 agent。

目标很简单，不做花哨摘要，专注两件事：

1. 监控关注标的的价格与成交额变化
2. 在真正有异动时，把有价值的信息发到 WhatsApp，而不是刷屏

---

## 这个 agent 是干嘛的

它主要用于监控地缘政治 / 宏观叙事相关的 Polymarket 标的，尤其是 Jacob 当前关心的：

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

维护文件：`watchlist.json`

当前关注的事件 slug 包括：

- `strait-of-hormuz-traffic-returns-to-normal-by-april-30`
- `strait-of-hormuz-traffic-returns-to-normal-by-end-of-may`
- `iran-x-israelus-conflict-ends-by`
- `us-x-iran-permanent-peace-deal-by`
- `trump-announces-us-blockade-of-hormuz-lifted-by`

说明：

- `watchlist.json` 中 `enabled=false` 的项目不会被监控
- 如果设置了 `expiresAt`，过期后会自动忽略

---

## 当前实现结构

### 1. 常驻 daemon

主脚本：`scripts/polymarket_ws_daemon.py`

职责：

- 通过 Polymarket WebSocket 持续接收市场价格变动
- 通过 HTTP snapshot 补充 display price 和 volume24hr
- 维护统一状态文件 `poll_state.json`
- 把需要发送的异动写入 `alert_outbox.json`

### 2. 播报脚本

脚本：`scripts/polymarket_broadcast.py`

职责：

- 读取 `alert_outbox.json`
- 输出待发送告警
- 读取 `poll_state.json` 生成全盘 summary

### 3. 看门狗

脚本：`scripts/ws_watchdog.sh`

职责：

- 确保常驻 daemon 还活着
- 异常退出时拉起

---

## 状态文件

### `poll_state.json`

统一状态文件，daemon 写入。

里面按 market_id 维护：

- `history`：价格时间序列
- `last_yes_price` / `last_no_price`
- `display_yes_price` / `display_no_price`
- `last_volume24hr`
- `volume_snapshots`
- `volume_delta_5m`
- `trend_dedup`

### `alert_outbox.json`

待发告警队列。

daemon 命中条件后先写这里，再由 broadcast 路径发送。

### `summary_outbox.json`

用于全盘播报的输出缓冲。

---

## 告警逻辑

当前有三层。

### 第一层：快异动层

窗口：**5 分钟**

以下三个条件是 **或关系**，满足任意一个就告警：

- 相对变化 >= 10%
- 绝对变化 >= 5%
- 5 分钟成交额增量 >= 100,000 USD

注意：

- 绝对变化现在按 **百分比** 展示
- 例如从 19% 到 24%，绝对变化显示为 `+5.00%`

### 第二层：慢趋势层

用于捕获不是突然 spike，而是几小时内缓慢 repricing 的情况。

当前规则：

- **30 分钟累计变化 >= 5%**
- **1 小时累计变化 >= 10%**
- **6 小时累计变化 >= 20%**

这层会抓到类似：

- 霍尔木兹恢复通航概率从 10% 慢慢爬到 20%
- 4 月停火概率从 50% 慢慢爬到 70%

即使中间每 5 分钟都没有特别大的单次跳变，也能被抓到。

### 第三层：高概率阈值层

用于捕获某个标的已经进入非常高概率区间的时刻。

当前规则：

- 当某个标的的 YES 概率 **首次达到或超过 90%** 时，触发一次告警

为了避免提醒太频繁，当前做了过滤：

- 触发一次后，不会因为 90% 上方的小幅波动反复提醒
- 只有当该标的先回落到 **85% 或以下**，才会重新武装，等待下一次突破 90%

---

## 告警正文格式

### 快异动

当前会包含：

- 标的名称
- YES / NO 当前价格
- 最新成交价（YES）
- 近 5 分钟相对变化
- 近 5 分钟绝对变化（百分比）
- 近 5 分钟新增成交额
- **触发条件**（明确写出是相对变化 / 绝对变化 / 成交额哪一条命中）
- 时间
- Polymarket 链接

### 慢趋势异动

当前标题会更醒目，区分方向：

- `📈 Polymarket 缓慢上行`
- `📉 Polymarket 缓慢下行`

正文会包含：

- 标的名称
- YES / NO 当前价格
- 当前成交价（YES）
- 对比窗口（30 分钟 / 1 小时 / 6 小时）
- 窗口起点价格
- 窗口相对变化
- 窗口绝对变化（百分比）
- 触发条件
- 时间
- Polymarket 链接

### 高概率阈值告警

当前会包含：

- 标的名称
- YES / NO 当前价格
- 当前成交价（YES）
- 触发条件（YES 概率达到或超过 90%）
- 时间
- Polymarket 链接

---

## 为什么要有慢趋势层

Jacob 当前真正关心的，不只是消息出来瞬间的 spike，而是：

- 市场是否在持续重估某个叙事
- 这种重估是短时噪音，还是慢慢形成共识

单纯监控 5 分钟内跳变，会漏掉很多“持续 repricing”的重要变化。

所以现在采用：

- 快异动层抓突发
- 慢趋势层抓持续重估

但**不做额外定时 regime 播报**，避免 WhatsApp 频道信息过多，降低注意力。

---

## 数据来源

### WebSocket

- `wss://ws-subscriptions-clob.polymarket.com/ws/market`

用于接收实时价格变化。

### HTTP Event API

- `https://gamma-api.polymarket.com/events?slug={slug}`

用于补：

- market metadata
- outcomePrices
- volume24hr / volume24hrClob

---

## 重要实现细节

### YES/NO 处理

- WebSocket 事件按 token 进来
- daemon 根据 yes_token_id / no_token_id 归一化成 YES 价格视角
- 如果收到的是 NO token，会转换成对应 YES 价格

### 成交额逻辑

- WebSocket 的 `size` 字段不可靠，不能直接当作 5 分钟成交额
- 当前 5 分钟成交额增量，使用 `volume24hr` 的 snapshot 差分计算
- snapshot 落在 `volume_snapshots`
- `volume_delta_5m = 当前 volume24hr - 5 分钟前的 volume24hr`

### 去重逻辑

- 快异动层：按 market 统一去重，避免一分钟内连发
- 慢趋势层：按窗口和方向去重，例如
  - `1800:up`
  - `3600:down`
- 高概率阈值层：采用阈值武装机制
  - 首次突破 90% 时报一次
  - 只有先回落到 85% 或以下，才允许下一次再次突破 90% 时重报

这样可以避免同一趋势反复刷屏。

---

## 已修复的问题

### 1. 告警规则从“与”改为“或”

现在：

- 相对变化
- 绝对变化
- 成交额增量

三者任意一个满足，就告警。

### 2. `volume_snapshots` / `volume_delta_5m` 修复

之前存在量的快照没有正确落库的问题，可能导致：

- 明明有价格异动
- 但 `volume_delta_5m = 0`
- 从而把应报告警压掉

当前该链路已修复，snapshot 已恢复写入。

### 3. 告警正文加入触发原因

现在不会再出现“收到告警但不知道为什么触发”的情况。

### 4. 新增慢趋势层

用于捕获缓慢爬升 / 缓慢下跌，不再只盯 spike。

### 5. 新增高概率阈值层

用于捕获 YES 概率首次进入 90%+ 高位区间，同时通过 90% 触发 / 85% 重武装机制控制频率。

---

## 当前不做的事情

为了控制 WhatsApp 频道噪音，当前**明确不做**：

- 不做额外的定时 regime 播报
- 不做额外的 cumulative repricing monitor 专门播报层
- 不做研究型长摘要

这个 agent 的定位是：

**少发，但发出来就要值得看。**

---

## 运行与维护

### 启动 daemon

常驻主脚本：

```bash
python scripts/polymarket_ws_daemon.py
```

### 看门狗

```bash
./scripts/ws_watchdog.sh
```

### 语法校验

```bash
python3 -m py_compile scripts/polymarket_ws_daemon.py
```

---

## 以后如果要继续优化

优先级建议：

### P1

- 去掉 websocket 主循环中的同步 HTTP 拉取，避免阻塞事件循环
- watchlist 热更新后更稳地处理 subscribe / reconnect

### P2

- 继续清理历史兼容路径，保证 state / broadcast 只有一条真实链路
- 增加更小粒度测试，覆盖：
  - YES/NO 归一化
  - 5 分钟 volume 差分
  - 慢趋势窗口判断
  - 去重逻辑

---

## 一句话总结

这是一个专门为 Jacob 设计的 **Polymarket 叙事重估监控 agent**：

- 抓突发异动
- 抓缓慢趋势
- 抓 90%+ 高概率进入时刻
- 告警里直接说清楚为什么触发
- 尽量减少频道噪音
