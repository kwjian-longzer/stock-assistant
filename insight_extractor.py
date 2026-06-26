#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent洞见提取器 — 从结构化电报中提取市场推理信号

验证场景: 盘前已知消息 → 预判A股走势 → 盘后验证

输入: db.py 数据库中的电报数据
输出: 结构化洞见报告（信号链 + 跨市场映射 + 风险预警）
"""

import sys
import os
import datetime
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import DB


def extract_market_signals(telegraphs: list) -> dict:
    """从电报列表中提取结构化市场信号

    信号分类:
    1. 海外市场信号: 美股走势、板块表现
    2. 商品信号: 黄金、原油等
    3. 宏观信号: 通胀、消费者信心等
    4. A股个股信号: 公告、ST、涨停
    5. 地缘信号: 地震、战争、制裁
    """
    signals = {
        "overseas_market": [],    # 海外市场
        "commodity": [],          # 商品
        "macro": [],              # 宏观
        "a_share_individual": [], # A股个股
        "geopolitical": [],       # 地缘
    }

    for t in telegraphs:
        title = t.get("title", "")
        content = t.get("content", "")
        full_text = f"{title} {content}"
        ts = t.get("timestamp", 0)
        time_str = datetime.datetime.fromtimestamp(ts).strftime("%H:%M") if ts else "N/A"

        signal = {
            "time": time_str,
            "title": title[:80],
            "event_type": t.get("event_type", ""),
            "sentiment": t.get("sentiment", ""),
            "impact_level": t.get("impact_level", ""),
            "sector_tags": t.get("sector_tags", ""),
        }

        # 分类到信号类型
        overseas_kws = ["美股", "标普", "纳斯达克", "道琼斯", "费城半导体",
                       "英伟达", "台积电", "苹果", "微软", "OpenAI",
                       "金龙指数", "中概股", "ADR", "铠侠", "SpaceX"]
        commodity_kws = ["黄金", "原油", "布伦特", "石油", "天然气", "铜", "铝"]
        macro_kws = ["通胀", "CPI", "PPI", "GDP", "社融", "M2", "消费者信心",
                     "密歇根", "非农", "失业率", "利率"]
        a_share_kws = ["涨停", "跌停", "ST", "连板", "6连板", "公告", "澄清",
                       "否认", "不涉及"]
        geo_kws = ["地震", "战争", "制裁", "关税", "限制", "禁止"]

        if any(kw in full_text for kw in overseas_kws):
            signal["category"] = "海外市场"
            signals["overseas_market"].append(signal)
        elif any(kw in full_text for kw in commodity_kws):
            signal["category"] = "商品"
            signals["commodity"].append(signal)
        elif any(kw in full_text for kw in macro_kws):
            signal["category"] = "宏观"
            signals["macro"].append(signal)
        elif any(kw in full_text for kw in a_share_kws):
            signal["category"] = "A股个股"
            signals["a_share_individual"].append(signal)
        elif any(kw in full_text for kw in geo_kws):
            signal["category"] = "地缘"
            signals["geopolitical"].append(signal)

    return signals


def generate_cross_market_mapping(signals: dict) -> list:
    """跨市场映射: 海外信号 → A股影响预判

    例如: 费城半导体-5% → A股半导体承压
         铠侠-14% → 存储芯片板块下跌
    """
    mappings = []

    for sig in signals.get("overseas_market", []):
        title = sig["title"]
        tags = sig.get("sector_tags", "")

        # 半导体映射
        if "半导体" in tags or "费城半导体" in title or "铠侠" in title:
            if sig["sentiment"] == "negative":
                mappings.append({
                    "signal": title[:60],
                    "a_share_impact": "A股半导体/存储芯片板块承压",
                    "confidence": "high" if sig["impact_level"] == "high" else "medium",
                    "time": sig["time"],
                })

        # 光模块映射
        if "光模块" in tags or "光通信" in tags or "Lumentum" in title:
            if sig["sentiment"] in ("negative", "neutral"):
                mappings.append({
                    "signal": title[:60],
                    "a_share_impact": "A股光模块/光通信板块承压",
                    "confidence": "medium",
                    "time": sig["time"],
                })

        # AI映射
        if "AI" in tags or "OpenAI" in tags:
            if sig["sentiment"] in ("negative", "neutral"):
                mappings.append({
                    "signal": title[:60],
                    "a_share_impact": "A股AI/算力板块情绪受压",
                    "confidence": "medium",
                    "time": sig["time"],
                })

        # 中概股映射
        if "金龙指数" in title or "中概股" in title:
            mappings.append({
                "signal": title[:60],
                "a_share_impact": "中概股走势影响A股科技板块情绪",
                "confidence": "low",
                "time": sig["time"],
            })

    # 商品映射
    for sig in signals.get("commodity", []):
        title = sig["title"]
        if "黄金" in title and sig["sentiment"] == "positive":
            mappings.append({
                "signal": title[:60],
                "a_share_impact": "黄金板块利好，避险情绪升温",
                "confidence": "medium",
                "time": sig["time"],
            })
        if ("原油" in title or "布伦特" in title) and sig["sentiment"] == "negative":
            mappings.append({
                "signal": title[:60],
                "a_share_impact": "石油石化板块承压，但下游用油行业受益",
                "confidence": "medium",
                "time": sig["time"],
            })

    return mappings


def generate_insight_report(date: str = None) -> str:
    """生成完整的洞见报告"""
    if date is None:
        date = datetime.datetime.now().strftime("%Y-%m-%d")

    db = DB()
    telegraphs = db.query_telegraphs(date=date, limit=500)

    if not telegraphs:
        return f"⚠️ {date} 无电报数据，请先运行 cls_collector.py 采集"

    signals = extract_market_signals(telegraphs)
    mappings = generate_cross_market_mapping(signals)

    # 统计
    total = len(telegraphs)
    all_signals = sum(len(v) for v in signals.values())

    report = []
    report.append(f"{'='*70}")
    report.append(f"Agent洞见报告 — {date}")
    report.append(f"{'='*70}")
    report.append(f"电报总数: {total} | 提取信号: {all_signals} | 跨市场映射: {len(mappings)}")

    # 1. 信号分类统计
    report.append(f"\n{'─'*50}")
    report.append("【一、信号分类统计】")
    report.append(f"{'─'*50}")
    for cat, items in signals.items():
        if items:
            report.append(f"  {cat}: {len(items)} 条")

    # 2. 海外市场信号
    report.append(f"\n{'─'*50}")
    report.append("【二、海外市场信号（影响A股开盘情绪）】")
    report.append(f"{'─'*50}")
    for sig in signals.get("overseas_market", []):
        impact_icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(sig["impact_level"], "⚪")
        report.append(f"  {impact_icon} [{sig['time']}] {sig['title']}")
        report.append(f"      情感:{sig['sentiment']} 影响:{sig['impact_level']} 板块:{sig['sector_tags']}")

    # 3. 商品信号
    report.append(f"\n{'─'*50}")
    report.append("【三、商品市场信号】")
    report.append(f"{'─'*50}")
    for sig in signals.get("commodity", []):
        impact_icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(sig["impact_level"], "⚪")
        report.append(f"  {impact_icon} [{sig['time']}] {sig['title']}")

    # 4. 宏观信号
    report.append(f"\n{'─'*50}")
    report.append("【四、宏观信号】")
    report.append(f"{'─'*50}")
    for sig in signals.get("macro", []):
        report.append(f"  [{sig['time']}] {sig['title']}")

    # 5. 跨市场映射
    report.append(f"\n{'─'*50}")
    report.append("【五、跨市场映射预判（核心洞见）】")
    report.append(f"{'─'*50}")
    if mappings:
        for m in mappings:
            conf_icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(m["confidence"], "⚪")
            report.append(f"  {conf_icon} {m['signal']}")
            report.append(f"      → A股影响: {m['a_share_impact']} (置信度:{m['confidence']})")
    else:
        report.append("  无跨市场映射信号")

    # 6. 关键缺失检查
    report.append(f"\n{'─'*50}")
    report.append("【六、关键信号缺失检查】")
    report.append(f"{'─'*50}")
    all_titles = " ".join(t.get("title", "") for t in telegraphs)
    critical_kws = {
        "韩国/熔断": ["韩国", "KOSPI", "熔断"],
        "亚太股市": ["亚太", "日经", "恒生"],
        "社融/票据": ["社融", "票据", "信贷"],
        "苹果涨价": ["苹果", "iPhone", "涨价"],
        "OpenAI": ["OpenAI", "IPO"],
        "英伟达": ["英伟达", "NVIDIA"],
    }
    for name, kws in critical_kws.items():
        found = any(kw in all_titles for kw in kws)
        status = "✅ 已覆盖" if found else "❌ 未覆盖"
        report.append(f"  {name}: {status}")

    # 7. A股个股信号
    report.append(f"\n{'─'*50}")
    report.append("【七、A股个股信号】")
    report.append(f"{'─'*50}")
    for sig in signals.get("a_share_individual", []):
        report.append(f"  [{sig['time']}] {sig['title']}")
        report.append(f"      事件:{sig['event_type']} 情感:{sig['sentiment']}")

    report.append(f"\n{'='*70}")
    return "\n".join(report)


if __name__ == "__main__":
    report = generate_insight_report()
    print(report)
