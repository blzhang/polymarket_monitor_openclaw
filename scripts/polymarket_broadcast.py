#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

WORKSPACE = Path("/Users/zhangbeilong/.openclaw/workspace-polymarket-monitor")
STATE_FILE = WORKSPACE / "poll_state.json"
ALERT_OUTBOX = WORKSPACE / "alert_outbox.json"


def now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def as_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def prune(history: list[dict[str, Any]], hours: int = 30) -> list[dict[str, Any]]:
    cutoff = now_local() - timedelta(hours=hours)
    out = []
    for x in history:
        try:
            ts = datetime.fromisoformat(x["ts"].replace("Z", "+00:00")).astimezone()
            if ts >= cutoff:
                out.append(x)
        except Exception:
            continue
    return out


def baseline(history: list[dict[str, Any]], hours: int) -> dict[str, Any] | None:
    if not history:
        return None
    target = now_local() - timedelta(hours=hours)
    chosen = history[0]
    for x in history:
        try:
            ts = datetime.fromisoformat(x["ts"].replace("Z", "+00:00")).astimezone()
            if ts <= target:
                chosen = x
        except Exception:
            continue
    return chosen


def format_change(history: list[dict[str, Any]], current_price: float, hours: int) -> str:
    base = baseline(history, hours)
    if not base:
        return "N/A"
    old = as_float(base.get("price"), current_price)
    diff = current_price - old
    rel = 0.0 if old == 0 else diff / old
    return f"{rel:+.2%} ({diff:+.4f})"


def format_volume_24h(history: list[dict[str, Any]]) -> str:
    target = now_local() - timedelta(hours=24)
    total = 0.0
    for x in history:
        try:
            ts = datetime.fromisoformat(x["ts"].replace("Z", "+00:00")).astimezone()
            if ts >= target:
                total += as_float(x.get("volume_delta"), 0.0)
        except Exception:
            continue
    return f"${total:,.0f}"


def pop_alerts() -> str:
    outbox = load_json(ALERT_OUTBOX, {"messages": []})
    msgs = outbox.get("messages", []) if isinstance(outbox, dict) else []
    if not msgs:
        return ""
    text = "\n\n".join(m.get("text", "") for m in msgs if m.get("text"))
    save_json(ALERT_OUTBOX, {"messages": []})
    return text or ""


def make_summary() -> str:
    state = load_json(STATE_FILE, {"markets": {}})
    markets = state.get("markets", {}) if isinstance(state, dict) else {}
    lines = ["📊 Polymarket 全盘播报", f"时间：{now_local().strftime('%Y-%m-%d %H:%M:%S')}"]
    count = 0
    for _, item in markets.items():
        history = prune(item.get("history", []))
        if not history:
            continue
        label = item.get("label") or item.get("market_title") or item.get("slug") or "未知标的"
        price = as_float(item.get("last_yes_price"), as_float(item.get("last_price"), 0.0))
        no_price = as_float(item.get("last_no_price"), max(0.0, 1.0 - price))
        lines.extend([
            "",
            f"标的：{label}",
            f"YES：{price:.2%}",
            f"NO：{no_price:.2%}",
            f"1小时变化：{format_change(history, price, 1)}",
            f"12小时变化：{format_change(history, price, 12)}",
            f"24小时变化：{format_change(history, price, 24)}",
            f"24小时交易量：{format_volume_24h(history)}",
            f"链接：{item.get('url') or ('https://polymarket.com/zh/event/' + str(item.get('slug') or ''))}",
        ])
        count += 1
    return "\n".join(lines) if count else ""


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "alerts"
    if mode == "alerts":
        print(pop_alerts())
    else:
        print(make_summary())
