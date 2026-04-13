#!/usr/bin/env python3
import json
from pathlib import Path
workspace = Path('/Users/zhangbeilong/.openclaw/workspace-polymarket-monitor')
state_path = workspace / 'poll_state.json'
alert_path = workspace / 'alert_outbox.json'
summary_path = workspace / 'summary_outbox.json'
state = {
  'markets': {},
  'last_watchlist_refresh': None
}
state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
alert_path.write_text(json.dumps({'messages': []}, ensure_ascii=False, indent=2, sort_keys=True))
summary_path.write_text(json.dumps({'messages': []}, ensure_ascii=False, indent=2, sort_keys=True))
print('reset_done')
