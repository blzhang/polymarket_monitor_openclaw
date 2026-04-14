#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import sys
from pathlib import Path

WORKSPACE = Path('/Users/zhangbeilong/.openclaw/workspace-polymarket-monitor')


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else 'scan'
    if mode == 'scan':
        from polymarket_broadcast import pop_alerts
        text = pop_alerts()
        if text:
            print(text)
        return
    if mode == 'summary':
        from polymarket_broadcast import make_summary
        text = make_summary()
        if text:
            print(text)
        return
    raise SystemExit(f'unknown mode: {mode}')


if __name__ == '__main__':
    main()
