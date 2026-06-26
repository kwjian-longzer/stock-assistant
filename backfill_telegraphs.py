#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
历史电报回填脚本

CLS /api/cache 端点固定返回最新20条，不支持历史分页。
本脚本通过浏览器方式访问财联社电报页面，滚动加载历史电报。

备选方案: 使用CLS深度头条API中的电报内容（含历史时间段的精选电报）。
同时，通过每小时定时采集(cls_collector.py)逐步积累数据库。

用法:
  python backfill_telegraphs.py              # 尝试浏览器回填
  python backfill_telegraphs.py --check      # 检查数据库覆盖情况
  python backfill_telegraphs.py --from-depth  # 从深度头条中提取电报
"""

import argparse
import json
import os
import sys
import time
import datetime
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import DB
from cls_collector import (
    _cls_api_get, _cls_sign, _CLS_DEFAULT_SV, _CLS_HEADERS,
    classify_event_type, classify_sentiment, classify_impact, extract_sector_tags,
)


def check_coverage():
    """检查数据库中电报的时间覆盖情况"""
    db = DB()
    stats = db.query_telegraph_stats()
    print(f"=== 电报覆盖情况 ===")
    print(f"  总数: {stats['total']}")
    print(f"  红色: {stats['red_count']}")
    if stats['earliest_ts']:
        earliest = datetime.datetime.fromtimestamp(stats['earliest_ts'])
        latest = datetime.datetime.fromtimestamp(stats['latest_ts'])
        span = latest - earliest
        print(f"  最早: {earliest.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  最晚: {latest.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  跨度: {span.total_seconds()/3600:.1f} 小时")

    # 检查今日各时段覆盖
    conn = db._conn()
    cur = conn.execute(
        "SELECT strftime('%H', timestamp, 'unixepoch') as hour, COUNT(*) as cnt "
        "FROM cls_telegraph "
        "WHERE date(timestamp, 'unixepoch') = date('now') "
        "GROUP BY hour ORDER BY hour"
    )
    hours = cur.fetchall()
    conn.close()

    if hours:
        print(f"\n  今日各时段电报数量:")
        for h in hours:
            print(f"    {h['hour']}:00 - {h['cnt']} 条")
        # 检查缺失时段
        covered_hours = {int(h['hour']) for h in hours}
        missing = [h for h in range(8, 23) if h not in covered_hours]
        if missing:
            print(f"\n  ⚠️ 缺失时段(8-22点): {missing}")
        else:
            print(f"\n  ✅ 8-22点全覆盖")
    else:
        print(f"\n  ⚠️ 今日无电报数据")


def backfill_from_depth():
    """从深度头条API中提取电报内容

    CLS深度头条API (/v3/depth/home/assembled/1000) 包含精选的历史电报
    """
    print("\n[深度头条回填] 尝试从深度头条中提取电报...")
    db = DB()

    depth_data = _cls_api_get('/v3/depth/home/assembled/1000')
    if not depth_data or not isinstance(depth_data, dict):
        print("[深度头条回填] 无数据")
        return 0

    depth_list = depth_data.get('depth_list', [])
    print(f"[深度头条回填] 获取到 {len(depth_list)} 篇深度头条")

    # 深度头条中可能包含telegraph字段
    new_count = 0
    for article in depth_list:
        # 检查是否有关联电报
        telegraph = article.get('telegraph', {})
        if telegraph and isinstance(telegraph, dict):
            telegraph_id = str(telegraph.get('id', ''))
            if not telegraph_id:
                continue

            title = (telegraph.get('title', '') or '').strip()
            content = (telegraph.get('content', '') or '').strip()
            ctime = int(telegraph.get('ctime', 0))

            if not content or not ctime:
                continue

            is_red = 1 if telegraph.get('color') == 'red' else 0
            full_text = f"{title} {content}"

            telegraph_item = {
                "telegraph_id": f"depth_{telegraph_id}",
                "title": title[:200],
                "content": content[:500],
                "timestamp": ctime,
                "is_red": is_red,
                "event_type": classify_event_type(full_text),
                "sentiment": classify_sentiment(full_text),
                "impact_level": classify_impact(full_text, bool(is_red)),
                "sector_tags": extract_sector_tags(full_text),
            }

            if db.upsert_telegraph(telegraph_item):
                new_count += 1

    print(f"[深度头条回填] 新增 {new_count} 条电报")
    return new_count


def backfill_from_telegraph_page():
    """通过浏览器访问财联社电报页面获取历史电报

    使用CLS网页版的 /telegraph 页面，通过浏览器滚动加载。
    此函数需要浏览器MCP工具支持，在定时任务中暂不可用。
    """
    print("\n[网页回填] 浏览器方式暂未实现")
    print("  原因: CLS /api/cache 端点固定返回最新20条，不支持分页")
    print("  替代方案: 1) 每小时定时采集逐步积累 2) 从深度头条提取 3) 浏览器滚动抓取")
    return 0


def main():
    parser = argparse.ArgumentParser(description="历史电报回填")
    parser.add_argument('--check', action='store_true', help='检查数据库覆盖情况')
    parser.add_argument('--from-depth', action='store_true', help='从深度头条中提取电报')
    args = parser.parse_args()

    if args.check:
        check_coverage()
        return

    if args.from_depth:
        backfill_from_depth()
        check_coverage()
        return

    # 默认: 检查 + 深度头条回填
    check_coverage()
    backfill_from_depth()
    backfill_from_telegraph_page()
    check_coverage()


if __name__ == "__main__":
    main()
