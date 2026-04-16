#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import json
import math
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

# ── 快异动（5 分钟窗口） ──────────────────────────────────────────────────
RELATIVE_THRESHOLD = 0.10        # 相对变化（仅用于 reasons 展示）
ABSOLUTE_THRESHOLD = 0.05        # 绝对变化触发线
MIN_VOLUME_DELTA   = 100_000.0   # 5 分钟成交额最低要求（USD）
WINDOW_SECONDS     = 300         # 快异动时间窗口
FAST_ALERT_COOLDOWN = 7_200      # 快异动同方向冷却 2 小时
FAST_ALERT_MIN_MOVE = 0.05       # 价格锚去重：上次告警后需再移动 5% 才重发

# ── 慢趋势（多窗口） ─────────────────────────────────────────────────────
SLOW_TREND_RULES = [
    (1800,  0.25),   # 30 分钟：25%
    (3600,  0.40),   # 1 小时：40%
    (21600, 0.60),   # 6 小时：60%
]
SLOW_TREND_MIN_ABS     = 0.02    # 绝对变化下限（2%），防低价区误报
SLOW_ALERT_COOLDOWN    = 21_600  # 慢趋势同方向冷却 6 小时
SLOW_ALERT_MIN_MOVE    = 0.08    # 价格锚去重：上次告警后需再移动 8%

# ── 高概率阈值 ────────────────────────────────────────────────────────────
HIGH_PROB_THRESHOLD       = 0.90
HIGH_PROB_REARM_THRESHOLD = 0.85

# ── 信息熵过滤 ────────────────────────────────────────────────────────────
# H(p) = -p·log₂(p) - (1-p)·log₂(1-p)
# 动态阈值根据距到期天数收紧，见 effective_entropy_threshold()
ENTROPY_THRESHOLD = 0.25         # 默认（远期标的）：约 YES < 4% 或 > 96%

# ── 末日期权模式（临近到期） ──────────────────────────────────────────────
EXPIRY_MODE_DAYS      = 3        # 距到期 ≤ 3 天进入末日模式
EXPIRY_REVERSAL_ABS   = 0.10     # 末日模式下，反转幅度 ≥ 10% 才发反转告警

# ── 其他 ──────────────────────────────────────────────────────────────────
MAX_HISTORY_HOURS        = 30
REBUILD_WATCHLIST_SECONDS = 300


# ═══════════════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════════════

def market_entropy(yes_price: float) -> float:
    """香农熵，衡量市场剩余不确定性。H ∈ [0,1]，越低越确定。"""
    p = max(1e-9, min(1 - 1e-9, yes_price))
    return -(p * math.log2(p) + (1 - p) * math.log2(1 - p))


def effective_entropy_threshold(days: float | None) -> float:
    """根据距到期天数返回动态信息熵阈值。越临近到期，有效区间越收窄。"""
    if days is None:
        return ENTROPY_THRESHOLD
    if days < 1:
        return 2.0   # 实际上等于全部静默（H 最大值为 1.0）
    if days < 3:
        return 0.72  # YES 25%~75%
    if days < 7:
        return 0.50  # YES 11%~89%
    return ENTROPY_THRESHOLD  # YES 4%~96%


def days_to_expiry(item: dict[str, Any]) -> float | None:
    """返回距市场 endDate 的天数，无 endDate 返回 None。"""
    end = item.get("end_date")
    if not end:
        return None
    try:
        dt = datetime.fromisoformat(end.replace("Z", "+00:00")).astimezone()
        delta = dt - now_local()
        return max(0.0, delta.total_seconds() / 86400)
    except Exception:
        return None


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
            market_date = ""
            if "by " in market_title:
                market_date = market_title.split("by ")[-1].rstrip("?").rstrip(" ")
            market_slug = m.get("slug") or m.get("marketSlug") or ""
            if market_slug and market_slug != slug:
                market_url = f'https://polymarket.com/zh/market/{market_slug}'
            else:
                market_url = f'https://polymarket.com/zh/event/{slug}'
            out.append({
                "market_id":   str(m.get("id") or m.get("conditionId") or ""),
                "slug":        slug,
                "market_slug": market_slug,
                "label":       zh_label_for_market(market_title, slug),
                "market_title": market_title,
                "market_date": market_date,
                # TODO: API 的 endDate 字段可能错误，建议从 market_title 解析
            # 示例：API 返回 2026-04-30，但实际市场是 "by April 17, 2026?"
            "end_date":    m.get("endDate") or "",   # 存入 end_date 供 days_to_expiry 使用
                "url":         market_url,
                "token_ids":   [str(x) for x in token_ids],
                "yes_token_id": str(token_ids[0]) if len(token_ids) >= 1 else None,
                "no_token_id":  str(token_ids[1]) if len(token_ids) >= 2 else None,
            })
    return [x for x in out if x["market_id"]]


def window_label(seconds_back: int) -> str:
    if seconds_back % 3600 == 0:
        return f"{seconds_back // 3600}小时"
    if seconds_back % 60 == 0:
        return f"{seconds_back // 60}分钟"
    return f"{seconds_back}秒"


# ═══════════════════════════════════════════════════════════════════════════
# Monitor
# ═══════════════════════════════════════════════════════════════════════════

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
            "end_date": meta.get("end_date", ""),
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
            # 末日模式
            "expiry_alert_sent": False,
            "expiry_alert_anchor_price": None,
            # 价格锚（用于快异动去重）
            "fast_alert_anchor_price": None,
            "fast_alert_anchor_direction": None,
            # 价格锚（用于慢趋势去重）
            "slow_alert_anchor_price": None,
            "slow_alert_anchor_direction": None,
        })
        # 每次 meta 刷新时同步关键字段
        for key in ("label", "market_title", "slug", "url", "end_date", "yes_token_id", "no_token_id"):
            if meta.get(key):
                item[key] = meta[key]
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
        """找 ±20% 时间误差内的数据点；fallback 到最近的旧点，但最多允许 1.5x 窗口时长。"""
        if not history:
            return None
        target    = now_local() - timedelta(seconds=seconds_back)
        min_age   = timedelta(seconds=seconds_back * 0.8)
        max_age   = timedelta(seconds=seconds_back * 1.2)
        chosen    = None
        chosen_ts = None
        for entry in history:
            try:
                ts = datetime.fromisoformat(entry["ts"].replace("Z", "+00:00")).astimezone()
            except Exception:
                continue
            age = now_local() - ts
            if min_age <= age <= max_age:
                return entry
            if ts <= target:
                chosen    = entry
                chosen_ts = ts
        if chosen and chosen_ts:
            if (now_local() - chosen_ts) > timedelta(seconds=seconds_back * 1.5):
                return None
        return chosen

    # ── 去重辅助 ─────────────────────────────────────────────────────────

    def _fast_cooldown_key(self, market_id: str, direction: str) -> str:
        return f"fast_{market_id}_{direction}"

    def _slow_cooldown_key(self, market_id: str, direction: str) -> str:
        return f"slow_{market_id}_{direction}"

    def can_fast_alert(self, market_id: str, direction: str, current_price: float,
                       item: dict[str, Any]) -> bool:
        """快异动去重：同方向冷却 2h + 价格锚（需再移动 5% 才重发）。"""
        key = self._fast_cooldown_key(market_id, direction)
        last_ts = self.dedup.get(key, 0)
        if time.time() - last_ts < FAST_ALERT_COOLDOWN:
            return False
        # 价格锚检查：若方向相同且价格未移动足够，也不发
        anchor = item.get("fast_alert_anchor_price")
        anchor_dir = item.get("fast_alert_anchor_direction")
        if anchor is not None and anchor_dir == direction:
            if abs(current_price - anchor) < FAST_ALERT_MIN_MOVE:
                return False
        return True

    def mark_fast_alert(self, market_id: str, direction: str, price: float,
                        item: dict[str, Any]) -> None:
        key = self._fast_cooldown_key(market_id, direction)
        self.dedup[key] = time.time()
        item["fast_alert_anchor_price"]     = price
        item["fast_alert_anchor_direction"] = direction

    def can_slow_alert(self, market_id: str, direction: str, current_price: float,
                       item: dict[str, Any]) -> bool:
        """慢趋势去重：同方向冷却 6h + 价格锚（需再移动 8% 才重发）。"""
        key = self._slow_cooldown_key(market_id, direction)
        last_ts = self.dedup.get(key, 0)
        if time.time() - last_ts < SLOW_ALERT_COOLDOWN:
            return False
        anchor = item.get("slow_alert_anchor_price")
        anchor_dir = item.get("slow_alert_anchor_direction")
        if anchor is not None and anchor_dir == direction:
            if abs(current_price - anchor) < SLOW_ALERT_MIN_MOVE:
                return False
        return True

    def mark_slow_alert(self, market_id: str, direction: str, price: float,
                        item: dict[str, Any]) -> None:
        key = self._slow_cooldown_key(market_id, direction)
        self.dedup[key] = time.time()
        item["slow_alert_anchor_price"]     = price
        item["slow_alert_anchor_direction"] = direction

    def mark_alert(self, market_id: str, kind: str) -> None:
        """兼容旧调用，保留不删。"""
        self.dedup[market_id] = time.time()

    def queue_alert(self, text: str) -> None:
        """写入 WhatsApp 和 Telegram outbox。"""
        for outbox_file in (ALERT_OUTBOX, TELEGRAM_OUTBOX):
            box = load_json(outbox_file, {"messages": []})
            box.setdefault("messages", []).append({"text": text, "ts": now_local().isoformat()})
            save_json(outbox_file, box)

    # ── 末日模式 ──────────────────────────────────────────────────────────

    def check_expiry_mode_alerts(self, market_id: str, item: dict[str, Any],
                                  trigger_yes_price: float,
                                  display_yes: Any, display_no: Any,
                                  days: float) -> bool:
        """
        处理末日模式（距到期 ≤ 3 天）的告警逻辑。
        返回 True 表示已进入末日模式（调用方跳过普通告警）。
        """
        label    = item.get("label") or item.get("market_title") or market_id
        show_yes = as_float(display_yes, trigger_yes_price)
        show_no  = as_float(display_no, max(0.0, 1.0 - show_yes))
        days_str = f"{days:.1f}" if days >= 1 else f"{days * 24:.1f}小时"
        sent     = bool(item.get("expiry_alert_sent", False))
        anchor   = item.get("expiry_alert_anchor_price")

        if not sent:
            # ── 首次告警：走一遍正常触发判断，发后锁定 ─────────────────
            # 这里不再重复三层判断，而是直接发一条末日播报，告知当前状态
            msg = "\n".join([
                f"⏰ Polymarket 末日播报: {label}",
                f"事件：{label}",
                f"YES：{show_yes:.2%}",
                f"NO：{show_no:.2%}",
                f"当前成交价（YES）：{trigger_yes_price:.2%}",
                f"距到期：{days_str}天",
                f"说明：标的临近到期，此后仅在出现重大反转时再次通知",
                f"时间：{now_local().strftime('%Y-%m-%d %H:%M:%S')}",
                f"链接：{item.get('url') or ('https://polymarket.com/zh/event/' + str(item.get('slug') or ''))}",
            ])
            self.queue_alert(msg)
            item["expiry_alert_sent"]          = True
            item["expiry_alert_anchor_price"]  = trigger_yes_price
            append_log(f"expiry alert sent for {market_id} days={days:.2f} price={trigger_yes_price:.4f}")
            return True

        # ── 已发过：只检测反转 ────────────────────────────────────────────
        if anchor is not None:
            abs_move = trigger_yes_price - anchor
            if abs(abs_move) >= EXPIRY_REVERSAL_ABS:
                direction = "大幅上行" if abs_move > 0 else "大幅下行"
                emoji     = "⚡📈" if abs_move > 0 else "⚡📉"
                msg = "\n".join([
                    f"{emoji} Polymarket 末日反转: {label}",
                    f"事件：{label}",
                    f"YES：{show_yes:.2%}",
                    f"NO：{show_no:.2%}",
                    f"当前成交价（YES）：{trigger_yes_price:.2%}",
                    f"反转前价格：{anchor:.2%}",
                    f"价格变化：{abs_move:+.2%}",
                    f"距到期：{days_str}天",
                    f"时间：{now_local().strftime('%Y-%m-%d %H:%M:%S')}",
                    f"链接：{item.get('url') or ('https://polymarket.com/zh/event/' + str(item.get('slug') or ''))}",
                ])
                self.queue_alert(msg)
                # 更新锚点，继续守候下一次反转
                item["expiry_alert_anchor_price"] = trigger_yes_price
                append_log(f"expiry reversal alert for {market_id} anchor={anchor:.4f} now={trigger_yes_price:.4f} move={abs_move:+.4f}")

        return True  # 无论是否触发反转，均阻止普通告警

    # ── 慢趋势 ────────────────────────────────────────────────────────────

    def check_slow_trend_alerts(self, market_id: str, item: dict[str, Any],
                                  trigger_yes_price: float,
                                  display_yes: Any, display_no: Any) -> None:
        # 动态信息熵过滤
        days = days_to_expiry(item)
        threshold = effective_entropy_threshold(days)
        if market_entropy(trigger_yes_price) < threshold:
            return

        history = item.get("history", [])
        if not history:
            return
        trend_dedup = item.setdefault("trend_dedup", {})
        label    = item.get("label") or item.get("market_title") or market_id
        show_yes = as_float(display_yes, trigger_yes_price)
        show_no  = as_float(display_no, max(0.0, 1.0 - show_yes))

        triggered: list[tuple[int, float, float, float, str]] = []
        for seconds_back, threshold_rel in SLOW_TREND_RULES:
            baseline = self.get_baseline(history, seconds_back)
            if not baseline:
                continue
            base_price = as_float(baseline.get("price"), trigger_yes_price)
            if base_price <= 0:
                continue
            abs_change = trigger_yes_price - base_price
            rel_change = abs_change / base_price
            if abs(rel_change) < threshold_rel:
                continue
            if abs(abs_change) < SLOW_TREND_MIN_ABS:
                continue
            dedup_key = f"{seconds_back}:{'up' if rel_change > 0 else 'down'}"
            last_ts = as_float(trend_dedup.get(dedup_key), 0.0)
            if time.time() - last_ts < seconds_back:
                continue
            triggered.append((seconds_back, base_price, abs_change, rel_change, dedup_key))

        if not triggered:
            return

        seconds_back, base_price, abs_change, rel_change, _ = triggered[-1]
        direction = "up" if rel_change > 0 else "down"

        # 价格锚 + 方向冷却去重
        if not self.can_slow_alert(market_id, direction, trigger_yes_price, item):
            return

        now_ts = time.time()
        for entry in triggered:
            trend_dedup[entry[4]] = now_ts

        direction_emoji = "📈" if rel_change > 0 else "📉"
        direction_text  = "缓慢上行" if rel_change > 0 else "缓慢下行"
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
        self.mark_slow_alert(market_id, direction, trigger_yes_price, item)
        append_log(f"slow trend alert queued for {market_id} window={seconds_back} rel={rel_change:+.4f} "
                   f"(suppressed {len(triggered)-1} shorter windows)")

    # ── 高概率阈值 ────────────────────────────────────────────────────────

    def check_high_probability_alert(self, market_id: str, item: dict[str, Any],
                                      trigger_yes_price: float,
                                      display_yes: Any, display_no: Any) -> None:
        label    = item.get("label") or item.get("market_title") or market_id
        show_yes = as_float(display_yes, trigger_yes_price)
        show_no  = as_float(display_no, max(0.0, 1.0 - show_yes))
        alerted  = bool(item.get("high_prob_alerted", False))
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

    # ── watchlist ─────────────────────────────────────────────────────────

    def refresh_watchlist(self) -> bool:
        if time.time() - self.last_refresh < REBUILD_WATCHLIST_SECONDS:
            return False
        self.last_refresh = time.time()
        market_map: dict[str, dict[str, Any]] = {}
        for item in load_watchlist():
            slug  = item["event_id"]
            label = item.get("label") or slug
            try:
                markets = fetch_markets_for_slug(slug)
                for m in markets:
                    if not m.get("label") or m["label"] == slug:
                        m["label"] = label
                    m["event_label"] = label
                    token_ids = m.get("token_ids", [])
                    for token_id in token_ids:
                        market_map[str(token_id)] = {**m, "token_id": str(token_id)}
            except Exception as e:
                append_log(f"watchlist refresh failed for {slug}: {e}")
        changed = set(self.market_map.keys()) != set(market_map.keys())
        self.market_map = market_map
        self.state["last_watchlist_refresh"] = now_local().isoformat()
        self.save_state()
        append_log(f"watchlist refreshed, active markets={len(self.market_map)}")
        return changed

    # ── 核心处理 ──────────────────────────────────────────────────────────

    def process_trade(self, token_id: str, price: float, volume_delta: float) -> None:
        meta = self.market_map.get(token_id)
        if not meta:
            return
        market_id    = str(meta.get("market_id") or token_id)
        item         = self.ensure_market_state(market_id, meta)
        yes_token_id = str(item.get("yes_token_id") or "")
        no_token_id  = str(item.get("no_token_id") or "")

        token_type = ("YES" if str(token_id) == yes_token_id
                      else "NO" if str(token_id) == no_token_id else "UNKNOWN")
        append_log(f"process_trade: market={market_id} token={token_id[:20]}... "
                   f"price={price:.4f} type={token_type}")

        display_yes = item.get("display_yes_price")
        display_no  = item.get("display_no_price")
        if display_yes is None or display_no is None:
            display_yes = item.get("last_yes_price")
            display_no  = item.get("last_no_price")

        if str(token_id) == yes_token_id:
            trigger_yes_price = price
            trigger_no_price  = max(0.0, 1.0 - price)
            item["last_trade_yes_price"] = trigger_yes_price
            item["last_trade_no_price"]  = trigger_no_price
        elif str(token_id) == no_token_id:
            trigger_no_price  = price
            trigger_yes_price = max(0.0, 1.0 - price)
            item["last_trade_no_price"]  = trigger_no_price
            item["last_trade_yes_price"] = trigger_yes_price
        else:
            return
        if trigger_yes_price <= 0:
            return

        # 记录历史（始终进行，不受告警过滤影响）
        history = self.prune_history(item.get("history", []))
        history.append({
            "ts":           now_local().isoformat(),
            "price":        trigger_yes_price,
            "volume_delta": 0.0,
        })
        item["history"]    = history
        item["updated_at"] = now_local().isoformat()

        # ── 距到期判断 ───────────────────────────────────────────────────
        days = days_to_expiry(item)

        # 末日模式（≤ 3 天）：只发一次 + 反转告警，其余全静默
        if days is not None and days <= EXPIRY_MODE_DAYS:
            self.check_expiry_mode_alerts(market_id, item, trigger_yes_price,
                                          display_yes, display_no, days)
            self.save_state()
            return

        # 动态信息熵过滤
        entropy_threshold = effective_entropy_threshold(days)
        if market_entropy(trigger_yes_price) < entropy_threshold:
            self.save_state()
            return

        # ── 快异动告警 ───────────────────────────────────────────────────
        baseline = self.get_baseline(history, WINDOW_SECONDS)
        if baseline:
            base_price    = as_float(baseline.get("price"), trigger_yes_price)
            abs_change    = trigger_yes_price - base_price
            rel_change    = 0.0 if base_price == 0 else abs_change / base_price
            window_volume = as_float(item.get("volume_delta_5m"), 0.0)
            hit_relative  = abs(rel_change) >= RELATIVE_THRESHOLD
            hit_absolute  = abs(abs_change) >= ABSOLUTE_THRESHOLD
            hit_volume    = window_volume >= MIN_VOLUME_DELTA
            if hit_absolute and hit_volume:
                direction = "up" if abs_change > 0 else "down"
                if self.can_fast_alert(market_id, direction, trigger_yes_price, item):
                    label    = item.get("label") or item.get("market_title") or market_id
                    show_yes = as_float(display_yes, trigger_yes_price)
                    show_no  = as_float(display_no, max(0.0, 1.0 - show_yes))
                    reasons  = []
                    if hit_relative:
                        reasons.append(f"相对变化 {rel_change:+.2%}")
                    if hit_absolute:
                        reasons.append(f"绝对变化 {abs_change:+.2%}")
                    if hit_volume:
                        reasons.append(f"成交额增量 ${window_volume:,.0f}")
                    market_date = item.get("market_date", "")
                    date_line   = f"\n到期：{market_date}" if market_date else ""
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
                    self.mark_fast_alert(market_id, direction, trigger_yes_price, item)
                    append_log(f"fast alert queued for {market_id} reasons={','.join(reasons)}")

        self.check_slow_trend_alerts(market_id, item, trigger_yes_price, display_yes, display_no)
        self.check_high_probability_alert(market_id, item, trigger_yes_price, display_yes, display_no)
        self.save_state()

    async def snapshot_refresher(self) -> None:
        while True:
            try:
                seen: set[str] = set()
                for meta in list(self.market_map.values()):
                    market_id = str(meta.get("market_id") or "")
                    if not market_id or market_id in seen:
                        continue
                    seen.add(market_id)
                    item = self.ensure_market_state(market_id, meta)
                    try:
                        r = requests.get(HTTP_EVENT_API.format(slug=meta.get("slug")), timeout=10)
                        r.raise_for_status()
                        raw    = r.json()
                        events = raw if isinstance(raw, list) else [raw]
                        for ev in events:
                            for mm in ev.get("markets", []):
                                if str(mm.get("id")) != market_id:
                                    continue
                                op = mm.get("outcomePrices")
                                if isinstance(op, str):
                                    op = json.loads(op)
                                new_volume24 = as_float(
                                    mm.get("volume24hr") or mm.get("volume24hrClob"), 0.0)
                                item["last_volume24hr"] = new_volume24
                                # 成交量快照
                                vol_snapshots = item.setdefault("volume_snapshots", [])
                                vol_snapshots.append({"ts": now_local().isoformat(),
                                                      "volume24hr": new_volume24})
                                cutoff = now_local() - timedelta(minutes=10)
                                vol_snapshots[:] = [
                                    x for x in vol_snapshots
                                    if datetime.fromisoformat(
                                        x["ts"].replace("Z", "+00:00")).astimezone() >= cutoff
                                ]
                                target   = now_local() - timedelta(seconds=WINDOW_SECONDS)
                                base_vol = vol_snapshots[0]["volume24hr"] if vol_snapshots else new_volume24
                                for snap in vol_snapshots:
                                    ts = datetime.fromisoformat(
                                        snap["ts"].replace("Z", "+00:00")).astimezone()
                                    if ts <= target:
                                        base_vol = snap["volume24hr"]
                                    else:
                                        break
                                item["volume_delta_5m"] = max(0.0, new_volume24 - base_vol)
                                if isinstance(op, str):
                                    op = json.loads(op)
                                if isinstance(op, list) and len(op) >= 2:
                                    new_display_yes = as_float(op[0], 0.0)
                                    new_display_no  = as_float(op[1], 0.0)
                                    item["display_yes_price"] = new_display_yes
                                    item["display_no_price"]  = new_display_no
                                    # 同样走末日/熵过滤再决定是否触发告警
                                    days = days_to_expiry(item)
                                    if days is not None and days <= EXPIRY_MODE_DAYS:
                                        self.check_expiry_mode_alerts(
                                            market_id, item, new_display_yes,
                                            new_display_yes, new_display_no, days)
                                    else:
                                        self.check_high_probability_alert(
                                            market_id, item, new_display_yes,
                                            new_display_yes, new_display_no)
                                        self.check_slow_trend_alerts(
                                            market_id, item, new_display_yes,
                                            new_display_yes, new_display_no)
                                break
                    except Exception as e:
                        append_log(f"snapshot refresh failed for {market_id}: {e}")
                        last_trade = item.get("last_trade_yes_price")
                        if last_trade and item.get("display_yes_price") is None:
                            item["display_yes_price"] = last_trade
                            item["display_no_price"]  = max(0.0, 1.0 - last_trade)
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
                async with websockets.connect(
                        WS_URL, ping_interval=20, ping_timeout=20,
                        open_timeout=30, proxy=None) as ws:
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
                            if event_type == "price_change" and isinstance(
                                    item_data.get("price_changes"), list):
                                for change in item_data.get("price_changes", []):
                                    asset_id = str(change.get("asset_id") or "")
                                    if not asset_id:
                                        continue
                                    best_bid = as_float(change.get("best_bid"), 0.0)
                                    best_ask = as_float(change.get("best_ask"), 0.0)
                                    if best_bid > 0 and best_ask > 0:
                                        price = (best_bid + best_ask) / 2.0
                                    elif best_bid > 0:
                                        price = best_bid
                                    elif best_ask > 0:
                                        price = best_ask
                                    else:
                                        price = as_float(change.get("price"), 0.0)
                                    if price <= 0:
                                        continue
                                    volume_delta = as_float(change.get("size") or 0.0)
                                    self.process_trade(asset_id, price, volume_delta)
                                continue

                            asset_id = str(item_data.get("asset_id") or
                                           item_data.get("marketId") or
                                           item_data.get("market_id") or "")
                            if not asset_id:
                                continue
                            best_bid = as_float(
                                item_data.get("best_bid") or item_data.get("bestBid"), 0.0)
                            best_ask = as_float(
                                item_data.get("best_ask") or item_data.get("bestAsk"), 0.0)
                            if best_bid > 0 and best_ask > 0:
                                price = (best_bid + best_ask) / 2.0
                            elif best_bid > 0:
                                price = best_bid
                            elif best_ask > 0:
                                price = best_ask
                            else:
                                price = as_float(
                                    item_data.get("price") or
                                    item_data.get("last_price") or
                                    item_data.get("lastTradePrice"), 0.0)
                            if price <= 0:
                                continue
                            volume_delta = as_float(
                                item_data.get("size") or
                                item_data.get("volume") or
                                item_data.get("amount"), 0.0)
                            self.process_trade(asset_id, price, volume_delta)
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
