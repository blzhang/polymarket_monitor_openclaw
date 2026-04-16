#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
import websockets

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
HTTP_EVENT_API = "https://gamma-api.polymarket.com/events?slug={slug}"
WORKSPACE = Path("/Users/zhangbeilong/.openclaw/workspace-polymarket-monitor")
WATCHLIST_FILE = WORKSPACE / "watchlist.json"
STATE_FILE = WORKSPACE / "poll_state.json"
ALERT_OUTBOX = WORKSPACE / "alert_outbox.json"  # WhatsApp
TELEGRAM_OUTBOX = WORKSPACE / "telegram_outbox.json"  # Telegram
SUMMARY_OUTBOX = WORKSPACE / "summary_outbox.json"
PID_FILE = WORKSPACE / "monitor.pid"
LOG_FILE = WORKSPACE / "monitor.log"

RELATIVE_THRESHOLD = 0.10
ABSOLUTE_THRESHOLD = 0.05
MIN_VOLUME_DELTA = 100000.0
DEDUP_SECONDS = 60
WINDOW_SECONDS = 300
SLOW_TREND_WINDOWS = [1800, 3600, 21600]
SLOW_TREND_RULES = [
    # (时间窗口秒数，变化阈值)
    # 2026-04-16: 方案 A - 提高阈值减少噪音
    (1800, 0.25),   # 30 分钟：25%
    (3600, 0.40),   # 1 小时：40%
    (21600, 0.60),  # 6 小时：60%
]
HIGH_PROB_THRESHOLD = 0.90
HIGH_PROB_REARM_THRESHOLD = 0.85
MAX_HISTORY_HOURS = 30
REBUILD_WATCHLIST_SECONDS = 300


def now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def append_log(message: str) -> None:
    line = f"[{now_local().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n"
    with LOG_FILE.open("a") as f:
        f.write(line)


def load_watchlist() -> list[dict[str, Any]]:
    raw = load_json(WATCHLIST_FILE, {"markets": []})
    items = raw.get("markets", []) if isinstance(raw, dict) else []
    out = []
    now = now_local()
    for item in items:
        if not item.get("enabled", True):
            continue
        expires_at = item.get("expiresAt")
        if expires_at:
            try:
                expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00")).astimezone()
                if expiry < now:
                    continue
            except Exception:
                pass
        out.append(item)
    return out


def zh_label_for_market(question: str, slug: str) -> str:
    q = (question or '').strip()
    if q == 'Strait of Hormuz traffic returns to normal by end of April?':
        return '霍尔木兹海峡航运是否在4月底前恢复正常'
    if q == 'Strait of Hormuz traffic returns to normal by end of May?':
        return '霍尔木兹海峡航运是否在5月底前恢复正常'
    if q.startswith('Iran x Israel/US conflict ends by '):
        date_part = q.removeprefix('Iran x Israel/US conflict ends by ').rstrip('?')
        return f'伊朗 x 以色列/美国冲突是否在{date_part}前结束'
    if q.startswith('Trump announces US blockade of Hormuz lifted by '):
        date_part = q.removeprefix('Trump announces US blockade of Hormuz lifted by ').rstrip('?')
        return f'特朗普宣布美军封锁霍尔木兹解除 ({date_part})'
    if q.startswith('US-Iran permanent peace deal by '):
        date_part = q.removeprefix('US-Iran permanent peace deal by ').rstrip('?')
        return f'美伊永久和平协议达成 ({date_part})'
    return q or slug


def fetch_markets_for_slug(slug: str) -> list[dict[str, Any]]:
    r = requests.get(HTTP_EVENT_API.format(slug=slug), timeout=10)
    r.raise_for_status()
    raw = r.json()
    events = raw if isinstance(raw, list) else [raw]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    out = []
    for event in events:
        title = event.get("title") or slug
        for m in event.get("markets", []):
            if not m.get("active") or m.get("closed"):
                continue
            end = m.get("endDate")
            if end:
                try:
                    dt = datetime.fromisoformat(end.replace("Z", "+00:00")).replace(tzinfo=None)
                    if dt < now:
                        continue
                except Exception:
                    pass
            token_ids = []
            raw_token_ids = m.get("clobTokenIds")
            if isinstance(raw_token_ids, str):
                try:
                    token_ids = json.loads(raw_token_ids)
                except Exception:
                    token_ids = []
            elif isinstance(raw_token_ids, list):
                token_ids = raw_token_ids
            market_title = m.get("question") or m.get("title") or title
            # 从 market_title 提取日期，用于告警显示
            market_date = ""
            if "by " in market_title:
                market_date = market_title.split("by ")[-1].rstrip("?").rstrip(" ")
            # 优先使用市场自身的 slug 构造精确 URL
            market_slug = m.get("slug") or m.get("marketSlug") or ""
            if market_slug and market_slug != slug:
                market_url = f'https://polymarket.com/zh/market/{market_slug}'
            else:
                market_url = f'https://polymarket.com/zh/event/{slug}'
            out.append({
                "market_id": str(m.get("id") or m.get("conditionId") or ""),
                "slug": slug,
                "market_slug": market_slug,
                "label": zh_label_for_market(market_title, slug),
                "market_title": market_title,
                "market_date": market_date,  # 如 "April 22, 2026"
                "url": market_url,
                "token_ids": [str(x) for x in token_ids],
                "yes_token_id": str(token_ids[0]) if len(token_ids) >= 1 else None,
                "no_token_id": str(token_ids[1]) if len(token_ids) >= 2 else None,
            })
    return [x for x in out if x["market_id"]]


def window_label(seconds_back: int) -> str:
    if seconds_back % 3600 == 0:
        return f"{seconds_back // 3600}小时"
    if seconds_back % 60 == 0:
        return f"{seconds_back // 60}分钟"
    return f"{seconds_back}秒"


class Monitor:
    def __init__(self) -> None:
        self.state = load_json(STATE_FILE, {"markets": {}, "last_watchlist_refresh": None})
        self.market_map: dict[str, dict[str, Any]] = {}
        self.dedup: dict[str, float] = {}
        self.last_refresh = 0.0

    def save_state(self) -> None:
        save_json(STATE_FILE, self.state)

    def ensure_market_state(self, market_id: str, meta: dict[str, Any]) -> dict[str, Any]:
        markets = self.state.setdefault("markets", {})
        item = markets.setdefault(market_id, {
            "history": [],
            "label": meta.get("label"),
            "market_title": meta.get("market_title"),
            "slug": meta.get("slug"),
            "url": meta.get("url"),
            "yes_token_id": meta.get("yes_token_id"),
            "no_token_id": meta.get("no_token_id"),
            "last_yes_price": None,
            "last_no_price": None,
            "display_yes_price": None,
            "display_no_price": None,
            "last_trade_yes_price": None,
            "last_trade_no_price": None,
            "trend_dedup": {},
            "high_prob_alerted": False,
        })
        item["label"] = meta.get("label") or item.get("label")
        item["market_title"] = meta.get("market_title") or item.get("market_title")
        item["slug"] = meta.get("slug") or item.get("slug")
        item["url"] = meta.get("url") or item.get("url")
        item["yes_token_id"] = meta.get("yes_token_id") or item.get("yes_token_id")
        item["no_token_id"] = meta.get("no_token_id") or item.get("no_token_id")
        return item

    def prune_history(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cutoff = now_local() - timedelta(hours=MAX_HISTORY_HOURS)
        out = []
        for x in history:
            try:
                ts = datetime.fromisoformat(x["ts"].replace("Z", "+00:00")).astimezone()
                if ts >= cutoff:
                    out.append(x)
            except Exception:
                continue
        return out

    def get_baseline(self, history: list[dict[str, Any]], seconds_back: int) -> dict[str, Any] | None:
        """获取 baseline 数据点，允许 ±20% 时间误差。如果找不到合适数据点返回 None。"""
        if not history:
            return None
        target = now_local() - timedelta(seconds=seconds_back)
        # 允许 20% 的时间误差，避免用太老的数据
        min_age = timedelta(seconds=seconds_back * 0.8)
        max_age = timedelta(seconds=seconds_back * 1.2)
        chosen = None
        chosen_ts = None
        for item in history:
            try:
                ts = datetime.fromisoformat(item["ts"].replace("Z", "+00:00")).astimezone()
            except Exception:
                continue
            age = now_local() - ts
            if min_age <= age <= max_age:
                return item  # 找到合适的数据点
            if ts <= target:
                chosen = item  # 记录最后一个早于 target 的点
                chosen_ts = ts
        # fallback：只允许最多 1.5 倍窗口时长以内的旧数据，超出则认为数据太旧不计算
        if chosen and chosen_ts:
            if (now_local() - chosen_ts) > timedelta(seconds=seconds_back * 1.5):
                return None
        return chosen

    def can_alert(self, market_id: str, kind: str) -> bool:
        # 去重按 market_id 统一去，不按方向；避免价格小幅波动导致连发
        last = self.dedup.get(market_id, 0)
        return time.time() - last >= DEDUP_SECONDS
    
    def can_slow_trend_alert(self, market_id: str) -> bool:
        """缓慢趋势告警专用去重：同一市场 30 分钟内只推送 1 次（方案 B）"""
        last = self.dedup.get(f"slow_{market_id}", 0)
        return time.time() - last >= 1800  # 30 分钟
    
    def mark_slow_trend_alert(self, market_id: str) -> None:
        """标记缓慢趋势告警已发送"""
        self.dedup[f"slow_{market_id}"] = time.time()

    def mark_alert(self, market_id: str, kind: str) -> None:
        self.dedup[market_id] = time.time()

    def queue_alert(self, text: str) -> None:
        """Queue alert to both WhatsApp and Telegram outboxes."""
        # WhatsApp outbox
        whatsapp_outbox = load_json(ALERT_OUTBOX, {"messages": []})
        whatsapp_outbox.setdefault("messages", []).append({"text": text, "ts": now_local().isoformat()})
        save_json(ALERT_OUTBOX, whatsapp_outbox)
        
        # Telegram outbox
        telegram_outbox = load_json(TELEGRAM_OUTBOX, {"messages": []})
        telegram_outbox.setdefault("messages", []).append({"text": text, "ts": now_local().isoformat()})
        save_json(TELEGRAM_OUTBOX, telegram_outbox)

    def check_slow_trend_alerts(self, market_id: str, item: dict[str, Any], trigger_yes_price: float, display_yes: Any, display_no: Any) -> None:
        # 方案 B：市场级去重检查
        if not self.can_slow_trend_alert(market_id):
            return
        history = item.get("history", [])
        if not history:
            return
        trend_dedup = item.setdefault("trend_dedup", {})
        label = item.get("label") or item.get("market_title") or market_id
        show_yes = as_float(display_yes, trigger_yes_price)
        show_no = as_float(display_no, max(0.0, 1.0 - show_yes))

        # 先收集所有满足条件且未被去重的窗口
        triggered: list[tuple[int, float, float, float, str]] = []
        for seconds_back, threshold in SLOW_TREND_RULES:
            baseline = self.get_baseline(history, seconds_back)
            if not baseline:
                continue
            base_price = as_float(baseline.get("price"), trigger_yes_price)
            if base_price <= 0:
                continue
            abs_change = trigger_yes_price - base_price
            rel_change = abs_change / base_price
            if abs(rel_change) < threshold:
                continue
            dedup_key = f"{seconds_back}:{'up' if rel_change > 0 else 'down'}"
            last_ts = as_float(trend_dedup.get(dedup_key), 0.0)
            if time.time() - last_ts < seconds_back:
                continue
            triggered.append((seconds_back, base_price, abs_change, rel_change, dedup_key))

        if not triggered:
            return

        # 多个窗口同时满足时只发最长窗口的告警，避免重复消息
        # 所有触发窗口的 dedup key 都标记，防止短窗口随后补发
        seconds_back, base_price, abs_change, rel_change, _ = triggered[-1]
        now_ts = time.time()
        for entry in triggered:
            trend_dedup[entry[4]] = now_ts

        direction_emoji = "📈" if rel_change > 0 else "📉"
        direction_text = "缓慢上行" if rel_change > 0 else "缓慢下行"
        msg = "\n".join([
            f"{direction_emoji} Polymarket {direction_text}: {label}",
            f"事件：{label}",
            f"YES：{show_yes:.2%}",
            f"NO：{show_no:.2%}",
            f"当前成交价（YES）：{trigger_yes_price:.2%}",
            f"对比窗口：{window_label(seconds_back)}",
            f"窗口起点价格：{base_price:.2%}",
            f"窗口相对变化：{rel_change:+.2%}",
            f"窗口绝对变化：{abs_change:+.2%}",
            f"触发条件：{window_label(seconds_back)}累计变化达到 {rel_change:+.2%}",
            f"时间：{now_local().strftime('%Y-%m-%d %H:%M:%S')}",
            f"链接：{item.get('url') or ('https://polymarket.com/zh/event/' + str(item.get('slug') or ''))}",
        ])
        self.queue_alert(msg)
            self.mark_slow_trend_alert(market_id)  # 方案 B：标记市场级告警已发送
            append_log(f"slow trend alert queued for {market_id} window={seconds_back} rel={rel_change:+.4f} (suppressed {len(triggered)-1} shorter windows)")

    def check_high_probability_alert(self, market_id: str, item: dict[str, Any], trigger_yes_price: float, display_yes: Any, display_no: Any) -> None:
        label = item.get("label") or item.get("market_title") or market_id
        show_yes = as_float(display_yes, trigger_yes_price)
        show_no = as_float(display_no, max(0.0, 1.0 - show_yes))
        alerted = bool(item.get("high_prob_alerted", False))
        if trigger_yes_price >= HIGH_PROB_THRESHOLD and not alerted:
            msg = "\n".join([
                f"🚨 Polymarket 高概率阈值: {label}",
                f"事件：{label}",
                f"YES：{show_yes:.2%}",
                f"NO：{show_no:.2%}",
                f"当前成交价（YES）：{trigger_yes_price:.2%}",
                f"触发条件：YES 概率达到或超过 {HIGH_PROB_THRESHOLD:.0%}",
                f"时间：{now_local().strftime('%Y-%m-%d %H:%M:%S')}",
                f"链接：{item.get('url') or ('https://polymarket.com/zh/event/' + str(item.get('slug') or ''))}",
            ])
            self.queue_alert(msg)
            item["high_prob_alerted"] = True
            append_log(f"high probability alert queued for {market_id} price={trigger_yes_price:.4f}")
        elif trigger_yes_price <= HIGH_PROB_REARM_THRESHOLD and alerted:
            item["high_prob_alerted"] = False
            append_log(f"high probability alert rearmed for {market_id} price={trigger_yes_price:.4f}")

    def refresh_watchlist(self) -> bool:
        if time.time() - self.last_refresh < REBUILD_WATCHLIST_SECONDS:
            return False
        self.last_refresh = time.time()
        market_map = {}
        for item in load_watchlist():
            slug = item["event_id"]
            label = item.get("label") or slug
            try:
                markets = fetch_markets_for_slug(slug)
                for m in markets:
                    # 仅当子市场没有专属标签时才用 watchlist 事件级标签兜底
                    # fetch_markets_for_slug 已经通过 zh_label_for_market 生成了含日期的子市场标签
                    if not m.get("label") or m["label"] == slug:
                        m["label"] = label
                    m["event_label"] = label  # 保存事件级标签供调试
                    token_ids = m.get("token_ids", [])
                    for token_id in token_ids:
                        market_map[str(token_id)] = {**m, "token_id": str(token_id)}
            except Exception as e:
                append_log(f"watchlist refresh failed for {slug}: {e}")
        changed = set(self.market_map.keys()) != set(market_map.keys())
        # 更新 market_map 中每个 token 的 meta，保留 per-market label（含日期），不用 event label 覆盖
        self.market_map = market_map
        self.state["last_watchlist_refresh"] = now_local().isoformat()
        self.save_state()
        append_log(f"watchlist refreshed, active markets={len(self.market_map)}")
        return changed

    def process_trade(self, token_id: str, price: float, volume_delta: float) -> None:
        meta = self.market_map.get(token_id)
        if not meta:
            return
        market_id = str(meta.get("market_id") or token_id)
        item = self.ensure_market_state(market_id, meta)
        yes_token_id = str(item.get("yes_token_id") or "")
        no_token_id = str(item.get("no_token_id") or "")
        
        # 调试日志：检查 token 匹配
        token_type = "YES" if str(token_id) == yes_token_id else ("NO" if str(token_id) == no_token_id else "UNKNOWN")
        append_log(f"process_trade: market={market_id} token={token_id[:20]}... price={price:.4f} type={token_type}")

        display_yes = item.get("display_yes_price")
        display_no = item.get("display_no_price")
        if display_yes is None or display_no is None:
            display_yes = item.get("last_yes_price")
            display_no = item.get("last_no_price")

        if str(token_id) == yes_token_id:
            trigger_yes_price = price
            trigger_no_price = max(0.0, 1.0 - price)
            item["last_trade_yes_price"] = trigger_yes_price
            item["last_trade_no_price"] = trigger_no_price
        elif str(token_id) == no_token_id:
            trigger_no_price = price
            trigger_yes_price = max(0.0, 1.0 - price)
            item["last_trade_no_price"] = trigger_no_price
            item["last_trade_yes_price"] = trigger_yes_price
        else:
            return
        if trigger_yes_price <= 0:
            return

        # WS size 字段不是单笔 USD 成交，而是累计量或放大值；这里只记价格，成交量改用 HTTP snapshot 差分
        history = self.prune_history(item.get("history", []))
        history.append({
            "ts": now_local().isoformat(),
            "price": trigger_yes_price,
            "volume_delta": 0.0,  # 占位，实际成交量由 snapshot refresher 填充
        })
        item["history"] = history
        item["updated_at"] = now_local().isoformat()

        baseline = self.get_baseline(history, WINDOW_SECONDS)
        if baseline:
            base_price = as_float(baseline.get("price"), trigger_yes_price)
            abs_change = trigger_yes_price - base_price
            rel_change = 0.0 if base_price == 0 else abs_change / base_price
            window_volume = as_float(item.get("volume_delta_5m"), 0.0)
            hit_relative = abs(rel_change) >= RELATIVE_THRESHOLD
            hit_absolute = abs(abs_change) >= ABSOLUTE_THRESHOLD
            hit_volume = window_volume >= MIN_VOLUME_DELTA
            # 成交异动告警必须有成交量，避免 WebSocket 价格波动误报
            # 只看绝对变化，相对变化在低价时太敏感（如 4%→5%=+25%）
            if hit_absolute and hit_volume:
                kind = "up" if abs_change > 0 else "down" if abs_change < 0 else "flat"
                if self.can_alert(market_id, kind):
                    label = item.get("label") or item.get("market_title") or market_id
                    show_yes = as_float(display_yes, trigger_yes_price)
                    show_no = as_float(display_no, max(0.0, 1.0 - show_yes))
                    reasons = []
                    if hit_relative:
                        reasons.append(f"相对变化 {rel_change:+.2%}")
                    if hit_absolute:
                        reasons.append(f"绝对变化 {abs_change:+.2%}")
                    if hit_volume:
                        reasons.append(f"成交额增量 ${window_volume:,.0f}")
                    market_date = item.get("market_date", "")
                    date_line = f"\n到期：{market_date}" if market_date else ""
                    msg = "\n".join([
                        f"💰 Polymarket 成交异动: {label}",
                        f"事件：{label}{date_line}",
                        f"YES：{show_yes:.2%}",
                        f"NO：{show_no:.2%}",
                        f"最新成交价（YES）：{trigger_yes_price:.2%}",
                        f"近5分钟相对变化：{rel_change:+.2%}",
                        f"近5分钟绝对变化：{abs_change:+.2%}",
                        f"近5分钟新增成交额：${window_volume:,.0f}",
                        f"触发条件：{'；'.join(reasons)}",
                        f"时间：{now_local().strftime('%Y-%m-%d %H:%M:%S')}",
                        f"链接：{item.get('url') or ('https://polymarket.com/zh/event/' + str(item.get('slug') or ''))}",
                    ])
                    self.queue_alert(msg)
                    self.mark_alert(market_id, kind)
                    append_log(f"alert queued for {market_id} reasons={','.join(reasons)}")
        self.check_slow_trend_alerts(market_id, item, trigger_yes_price, display_yes, display_no)
        self.check_high_probability_alert(market_id, item, trigger_yes_price, display_yes, display_no)
        self.save_state()

    async def snapshot_refresher(self) -> None:
        while True:
            try:
                seen = set()
                for meta in list(self.market_map.values()):
                    market_id = str(meta.get("market_id") or "")
                    if not market_id or market_id in seen:
                        continue
                    seen.add(market_id)
                    item = self.ensure_market_state(market_id, meta)
                    try:
                        r = requests.get(HTTP_EVENT_API.format(slug=meta.get("slug")), timeout=10)
                        r.raise_for_status()
                        raw = r.json()
                        events = raw if isinstance(raw, list) else [raw]
                        for ev in events:
                            for mm in ev.get("markets", []):
                                if str(mm.get("id")) != market_id:
                                    continue
                                op = mm.get("outcomePrices")
                                if isinstance(op, str):
                                    op = json.loads(op)
                                new_volume24 = as_float(mm.get("volume24hr") or mm.get("volume24hrClob"), 0.0)
                                item["last_volume24hr"] = new_volume24
                                vol_snapshots = item.setdefault("volume_snapshots", [])
                                vol_snapshots.append({"ts": now_local().isoformat(), "volume24hr": new_volume24})
                                cutoff = now_local() - timedelta(minutes=10)
                                vol_snapshots[:] = [x for x in vol_snapshots if datetime.fromisoformat(x["ts"].replace("Z", "+00:00")).astimezone() >= cutoff]
                                target = now_local() - timedelta(seconds=WINDOW_SECONDS)
                                base_vol = vol_snapshots[0]["volume24hr"] if vol_snapshots else new_volume24
                                for snap in vol_snapshots:
                                    ts = datetime.fromisoformat(snap["ts"].replace("Z", "+00:00")).astimezone()
                                    if ts <= target:
                                        base_vol = snap["volume24hr"]
                                    else:
                                        break
                                item["volume_delta_5m"] = max(0.0, new_volume24 - base_vol)
                                if isinstance(op, str):
                                    op = json.loads(op)
                                if isinstance(op, list) and len(op) >= 2:
                                    new_display_yes = as_float(op[0], 0.0)
                                    new_display_no = as_float(op[1], 0.0)
                                    # 只更新 display 价格用于展示，不覆盖 last_trade 价格
                                    # last_yes_price 由 WebSocket trade 更新，更准确
                                    item["display_yes_price"] = new_display_yes
                                    item["display_no_price"] = new_display_no
                                    # snapshot 更新价格后，检查高概率阈值和缓慢趋势（使用 display 价格）
                                    self.check_high_probability_alert(market_id, item, new_display_yes, new_display_yes, new_display_no)
                                    self.check_slow_trend_alerts(market_id, item, new_display_yes, new_display_yes, new_display_no)
                                break
                    except Exception as e:
                        append_log(f"snapshot refresh failed for {market_id}: {e}")
                        # snapshot 失败时，用最近 WS 价格作为 display fallback，避免展示价过旧
                        last_trade = item.get("last_trade_yes_price")
                        if last_trade and item.get("display_yes_price") is None:
                            item["display_yes_price"] = last_trade
                            item["display_no_price"] = max(0.0, 1.0 - last_trade)
                self.save_state()
            except Exception as e:
                append_log(f"snapshot refresher error: {e}")
            await asyncio.sleep(30)

    async def run(self) -> None:
        self.refresh_watchlist()
        asyncio.create_task(self.snapshot_refresher())
        while True:
            try:
                if not self.market_map:
                    self.refresh_watchlist()
                    await asyncio.sleep(5)
                    continue
                subscribed_ids = tuple(sorted(self.market_map.keys()))
                async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20, open_timeout=30, proxy=None) as ws:
                    sub = {
                        "assets_ids": list(subscribed_ids),
                        "type": "market",
                        "custom_feature_enabled": True,
                    }
                    await ws.send(json.dumps(sub))
                    append_log(f"ws subscribed markets={len(subscribed_ids)}")
                    while True:
                        watchlist_changed = self.refresh_watchlist()
                        if watchlist_changed:
                            append_log("watchlist changed, reconnecting ws")
                            break
                        raw = await ws.recv()
                        try:
                            data = json.loads(raw)
                        except Exception:
                            continue

                        items = data if isinstance(data, list) else [data]
                        for item_data in items:
                            if not isinstance(item_data, dict):
                                continue
                            event_type = item_data.get("event_type")
                            if event_type == "price_change" and isinstance(item_data.get("price_changes"), list):
                                for change in item_data.get("price_changes", []):
                                    market_id = str(change.get("asset_id") or "")
                                    if not market_id:
                                        continue
                                    # 用 best_bid/best_ask 中间价作为市场价，而非单笔订单价
                                    # change.get("price") 是触发此事件的单笔订单价格，可能是任意限价单
                                    best_bid = as_float(change.get("best_bid"), 0.0)
                                    best_ask = as_float(change.get("best_ask"), 0.0)
                                    if best_bid > 0 and best_ask > 0:
                                        price = (best_bid + best_ask) / 2.0
                                    elif best_bid > 0:
                                        price = best_bid
                                    elif best_ask > 0:
                                        price = best_ask
                                    else:
                                        # 没有 bid/ask 信息时才 fallback 到订单价
                                        price = as_float(change.get("price"), 0.0)
                                    if price <= 0:
                                        continue
                                    volume_delta = as_float(change.get("size") or 0.0)
                                    self.process_trade(market_id, price, volume_delta)
                                continue

                            market_id = str(item_data.get("asset_id") or item_data.get("marketId") or item_data.get("market_id") or "")
                            if not market_id:
                                continue
                            # 同样优先用 best_bid/best_ask 中间价
                            best_bid = as_float(item_data.get("best_bid") or item_data.get("bestBid"), 0.0)
                            best_ask = as_float(item_data.get("best_ask") or item_data.get("bestAsk"), 0.0)
                            if best_bid > 0 and best_ask > 0:
                                price = (best_bid + best_ask) / 2.0
                            elif best_bid > 0:
                                price = best_bid
                            elif best_ask > 0:
                                price = best_ask
                            else:
                                price = as_float(item_data.get("price") or item_data.get("last_price") or item_data.get("lastTradePrice"), 0.0)
                            if price <= 0:
                                continue
                            volume_delta = as_float(item_data.get("size") or item_data.get("volume") or item_data.get("amount"), 0.0)
                            self.process_trade(market_id, price, volume_delta)
            except Exception as e:
                append_log(f"ws loop error: {e}")
                await asyncio.sleep(5)


def main() -> None:
    PID_FILE.write_text(str(os.getpid()))
    append_log("daemon started")
    monitor = Monitor()
    asyncio.run(monitor.run())


if __name__ == "__main__":
    main()
