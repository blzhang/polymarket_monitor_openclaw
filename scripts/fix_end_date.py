#!/usr/bin/env python3
"""
修复 end_date 错误的临时脚本
问题：Polymarket API 返回错误的 endDate 字段
解决方案：从 market title 解析正确的日期
"""
import json
from pathlib import Path
import re
from datetime import datetime

STATE_FILE = Path("poll_state.json")

def parse_date_from_title(title: str) -> str | None:
    """从 market title 解析日期，如 'by April 17, 2026?' → '2026-04-17T00:00:00Z'"""
    # 匹配 "by Month DD, YYYY?" 格式
    match = re.search(r'by (\w+) (\d+), (\d+)', title)
    if not match:
        return None
    
    month_name, day, year = match.groups()
    months = {
        'January': '01', 'February': '02', 'March': '03', 'April': '04',
        'May': '05', 'June': '06', 'July': '07', 'August': '08',
        'September': '09', 'October': '10', 'November': '11', 'December': '12'
    }
    month = months.get(month_name)
    if not month:
        return None
    
    return f"{year}-{month}-{int(day):02d}T00:00:00Z"

with open(STATE_FILE) as f:
    state = json.load(f)

markets = state.get('markets', {})
fixed_count = 0

for mid, m in markets.items():
    end_date = m.get('end_date')
    title = m.get('market_title') or m.get('label', '')
    
    # 如果 end_date 存在，跳过（已修复的市场）
    if end_date and 'T00:00:00Z' in end_date:
        continue
    
    # 从 title 解析日期
    parsed_date = parse_date_from_title(title)
    if parsed_date:
        m['end_date'] = parsed_date
        print(f'Fixed {mid}: {parsed_date}')
        fixed_count += 1

with open(STATE_FILE, 'w') as f:
    json.dump(state, f, ensure_ascii=False, indent=2)

print(f'\n✅ 已修复 {fixed_count} 个市场的 end_date')
