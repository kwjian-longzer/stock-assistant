# -*- coding: utf-8 -*-
"""
insight_engine.py  —  v4.0 洞见引擎（DB 驱动）
================================================
从 DB 各表读取数据，生成多维度结构化洞见：
  海外市场 / A股盘面 / 板块资金 / 资金面 / 龙虎榜 / 涨停池 / 财联社舆情 / 跨市场映射
每条洞见写入 market_insight 表；同时产出 data_summary 供报告编排器使用。

用法:
    python insight_engine.py --date 2026-06-26 --period morning
"""

import sys
import os
import json
import datetime
import argparse
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 跨市场映射关键词（复用 v3.2 验证逻辑）
OVERSEAS_KWS = ["美股", "标普", "纳斯达克", "道琼斯", "费城半导体", "英伟达", "台积电",
                "苹果", "微软", "OpenAI", "金龙指数", "中概股", "ADR", "铠侠", "SpaceX"]
COMMODITY_KWS = ["黄金", "原油", "布伦特", "石油", "天然气", "铜", "铝"]
MACRO_KWS = ["通胀", "CPI", "PPI", "GDP", "社融", "M2", "消费者信心", "密歇根",
             "非农", "失业率", "利率"]
A_SHARE_KWS = ["涨停", "跌停", "ST", "连板", "公告", "澄清", "否认", "不涉及"]
GEO_KWS = ["地震", "战争", "制裁", "关税", "限制", "禁止"]


def _safe(v, default=None):
    try:
        return float(v) if v not in (None, "", "0.000") else default
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# 各维度分析
# ---------------------------------------------------------------------------

def analyze_global(db, date_str, period):
    """海外市场：从 raw_cache 读取全球行情，生成洞见"""
    global_data = db.get_or_fetch("sina", f"global_{period}",
                                  lambda: {}, trade_date=date_str, params={}, ttl_hours=24)
    insights = []
    for name, info in (global_data or {}).items():
        price = _safe(info.get("price"))
        chg = _safe(info.get("chg"))
        chg_pct = _safe(info.get("chg_pct"))
        if price is None:
            continue
        # 优先使用百分比涨跌幅；无百分比时用绝对变动估算
        pct_val = chg_pct if chg_pct is not None else (chg / price * 100 if chg and price else None)
        if pct_val is not None and abs(pct_val) >= 0.05:
            direction = "上涨" if pct_val > 0 else "下跌"
            impact = "high" if abs(pct_val) >= 1.5 else "medium"
            # 同时显示点数变动和百分比
            chg_str = f"（{'+' if chg and chg > 0 else ''}{chg:.2f}点）" if chg else ""
            insights.append({
                "category": "海外市场",
                "signal_text": f"{name} {direction} {abs(pct_val):.2f}%{chg_str}，报{price:.2f}",
                "a_share_impact": _global_impact(name, pct_val),
                "confidence": impact,
                "signal_time": "",
            })
    return insights


def _global_impact(name, pct_val):
    """海外品种 → A股影响预判"""
    if name in ("道琼斯", "纳斯达克", "标普500"):
        return f"美股{'走强' if pct_val > 0 else '走弱'}，影响A股{'高开' if pct_val > 0 else '低开'}情绪"
    if name in ("恒生指数", "恒生科技"):
        return f"港股{'走强' if pct_val > 0 else '走弱'}，影响A股{'科技' if '科技' in name else '蓝筹'}板块情绪"
    if name == "COMEX黄金":
        return "黄金上涨利好黄金板块，避险情绪升温" if pct_val > 0 else "黄金下跌压制黄金板块"
    if name == "WTI原油":
        return "原油上涨利好石化上游，压制下游用油" if pct_val > 0 else "原油下跌利好下游用油行业"
    if name == "美元指数":
        return "美元走强压制人民币资产" if pct_val > 0 else "美元走弱利好人民币资产"
    if name == "离岸人民币":
        return "人民币贬值压制北向资金流入" if pct_val > 0 else "人民币升值利好北向资金"
    return ""


def analyze_a_share(db, date_str):
    """A股盘面：从 index_quote 读取指数行情"""
    rows = db.query_index_quote(date=date_str)
    insights = []
    for r in rows:
        pct = _safe(r.get("pct_chg"))
        if pct is None:
            continue
        name = r.get("name", "")
        close = _safe(r.get("close"))
        if abs(pct) >= 0.3:
            direction = "上涨" if pct > 0 else "下跌"
            impact = "high" if abs(pct) >= 1.5 else "medium"
            insights.append({
                "category": "A股盘面",
                "signal_text": f"{name} {direction} {abs(pct):.2f}%，收{close:.2f}" if close else f"{name} {direction} {abs(pct):.2f}%",
                "a_share_impact": _index_impact(name, pct),
                "confidence": impact,
                "signal_time": r.get("fetch_time", "")[:5],
            })
    return insights


def _index_impact(name, pct):
    if name == "上证指数":
        return "权重股表现活跃，市场情绪偏多" if pct > 0 else "权重股走弱，市场情绪偏空"
    if name == "创业板指":
        return "成长股领涨，题材活跃" if pct > 0 else "成长股走弱，题材退潮"
    if name == "深证成指":
        return "中小盘表现强势" if pct > 0 else "中小盘承压"
    if name == "科创50":
        return "科创板资金回流" if pct > 0 else "科创板资金流出"
    return ""


def analyze_sector(db, date_str):
    """板块资金：从 sector_moneyflow 读取"""
    rows = db.query_sector_moneyflow(date=date_str, top_n=30)
    insights = []
    if not rows:
        return insights
    top5 = rows[:5]
    bottom5 = rows[-5:] if len(rows) >= 10 else []
    for r in top5:
        net = _safe(r.get("net_mf_amount"))
        if net and net > 0:
            insights.append({
                "category": "板块资金",
                "signal_text": f"{r.get('industry', '')} 净流入{net:.1f}亿",
                "a_share_impact": f"{r.get('industry', '')}板块资金大幅流入，关注龙头",
                "confidence": "high" if net > 10 else "medium",
                "signal_time": "",
            })
    for r in bottom5:
        net = _safe(r.get("net_mf_amount"))
        if net and net < 0:
            insights.append({
                "category": "板块资金",
                "signal_text": f"{r.get('industry', '')} 净流出{abs(net):.1f}亿",
                "a_share_impact": f"{r.get('industry', '')}板块资金大幅流出，注意风险",
                "confidence": "high" if abs(net) > 10 else "medium",
                "signal_time": "",
            })
    return insights


def analyze_capital(db, date_str):
    """资金面：北向资金 + 融资融券"""
    insights = []
    nm = db.query_north_money(date=date_str)
    if nm:
        north = _safe(nm.get("north_money"))
        if north is not None:
            direction = "净流入" if north > 0 else "净流出"
            impact = "high" if abs(north) >= 50 else "medium"
            insights.append({
                "category": "资金面",
                "signal_text": f"北向资金{direction}{abs(north):.1f}亿",
                "a_share_impact": ("外资加仓，提振市场信心" if north > 0
                                   else "外资流出，警惕情绪传导"),
                "confidence": impact,
                "signal_time": "",
            })
    margins = db.query_margin(date=date_str)
    if margins:
        for m in margins[:2]:
            rzye = _safe(m.get("rzye"))
            if rzye:
                insights.append({
                    "category": "资金面",
                    "signal_text": f"融资余额{rzye/1e8:.0f}亿({m.get('exchange_id','')})",
                    "a_share_impact": "杠杆资金维持高位" if rzye > 1.5e12 else "杠杆资金偏谨慎",
                    "confidence": "low",
                    "signal_time": "",
                })
    return insights


def analyze_dragon_tiger(db, date_str):
    """龙虎榜：机构资金动向"""
    rows = db.query_dragon_tiger(date=date_str)
    insights = []
    for r in rows[:5]:
        net = _safe(r.get("net_buy"))
        if net and net > 0:
            insights.append({
                "category": "龙虎榜",
                "signal_text": f"{r.get('name', '')}({r.get('ts_code','')}) 净买入{net:.1f}亿",
                "a_share_impact": f"{r.get('name', '')}获资金关注，{r.get('reason','')[:30]}",
                "confidence": "medium",
                "signal_time": "",
            })
    return insights


def analyze_limit_up(db, date_str):
    """涨停池：连板梯队 + 板块分布"""
    rows = db.query_limit_up(date=date_str)
    insights = []
    if not rows:
        return insights
    industries = Counter(r.get("industry", "未知") for r in rows if r.get("industry"))
    top_ind = industries.most_common(3)
    insights.append({
        "category": "涨停池",
        "signal_text": f"共{len(rows)}只涨停，{', '.join(f'{i}({c}只)' for i, c in top_ind)}",
        "a_share_impact": f"涨停集中在{top_ind[0][0] if top_ind else ''}板块，市场主线明确" if top_ind else "",
        "confidence": "high",
        "signal_time": "",
    })
    return insights


def analyze_cls_sentiment(db, date_str):
    """财联社舆情：从 cls_telegraph 提取信号并跨市场映射"""
    telegraphs = db.query_telegraphs(date=date_str, limit=200)
    signals = {"overseas_market": [], "commodity": [], "macro": [],
               "a_share_individual": [], "geopolitical": []}
    for t in telegraphs:
        title = t.get("title", "") or t.get("content", "")[:60]
        content = t.get("content", "")
        full = f"{title} {content}"
        ts = t.get("timestamp", 0)
        time_str = datetime.datetime.fromtimestamp(ts).strftime("%H:%M") if ts else ""
        sig = {"time": time_str, "title": title[:80],
               "sentiment": t.get("sentiment", ""),
               "impact_level": t.get("impact_level", ""),
               "sector_tags": t.get("sector_tags", "")}
        if any(k in full for k in OVERSEAS_KWS):
            signals["overseas_market"].append(sig)
        elif any(k in full for k in COMMODITY_KWS):
            signals["commodity"].append(sig)
        elif any(k in full for k in MACRO_KWS):
            signals["macro"].append(sig)
        elif any(k in full for k in A_SHARE_KWS):
            signals["a_share_individual"].append(sig)
        elif any(k in full for k in GEO_KWS):
            signals["geopolitical"].append(sig)

    insights = []
    red_count = sum(1 for t in telegraphs if t.get("is_red"))
    if telegraphs:
        insights.append({
            "category": "财联社舆情",
            "signal_text": f"近24h电报{len(telegraphs)}条，红色{red_count}条，"
                           f"海外{len(signals['overseas_market'])}商品{len(signals['commodity'])}"
                           f"宏观{len(signals['macro'])}个股{len(signals['a_share_individual'])}",
            "a_share_impact": "舆情密度较高，注意消息面驱动" if len(telegraphs) > 50 else "舆情平稳",
            "confidence": "medium",
            "signal_time": "",
        })

    # 跨市场映射（复用 v3.2 逻辑）
    for sig in signals["overseas_market"]:
        tags = sig.get("sector_tags", "")
        title = sig["title"]
        if "半导体" in tags or "费城半导体" in title or "铠侠" in title:
            if sig["sentiment"] == "negative":
                insights.append({"category": "跨市场映射", "signal_text": title[:60],
                                 "a_share_impact": "A股半导体/存储芯片板块承压",
                                 "confidence": "high" if sig["impact_level"] == "high" else "medium",
                                 "signal_time": sig["time"]})
        if "光模块" in tags or "Lumentum" in title:
            if sig["sentiment"] in ("negative", "neutral"):
                insights.append({"category": "跨市场映射", "signal_text": title[:60],
                                 "a_share_impact": "A股光模块/光通信板块承压",
                                 "confidence": "medium", "signal_time": sig["time"]})
        if "AI" in tags or "OpenAI" in title:
            if sig["sentiment"] in ("negative", "neutral"):
                insights.append({"category": "跨市场映射", "signal_text": title[:60],
                                 "a_share_impact": "A股AI/算力板块情绪受压",
                                 "confidence": "medium", "signal_time": sig["time"]})
    for sig in signals["commodity"]:
        title = sig["title"]
        if "黄金" in title and sig["sentiment"] == "positive":
            insights.append({"category": "跨市场映射", "signal_text": title[:60],
                             "a_share_impact": "黄金板块利好，避险情绪升温",
                             "confidence": "medium", "signal_time": sig["time"]})
    return insights


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run(db, date_str=None, period="morning"):
    """运行洞见引擎，写入 market_insight，返回 data_summary dict"""
    if date_str is None:
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    print(f"\n=== 洞见引擎 {date_str}/{period} ===")

    all_insights = []
    all_insights += analyze_global(db, date_str, period)
    all_insights += analyze_a_share(db, date_str)
    all_insights += analyze_sector(db, date_str)
    all_insights += analyze_capital(db, date_str)
    all_insights += analyze_dragon_tiger(db, date_str)
    all_insights += analyze_limit_up(db, date_str)
    all_insights += analyze_cls_sentiment(db, date_str)

    # 清除该日期+时段的旧洞见，避免重复
    conn = db._conn()
    conn.execute("DELETE FROM market_insight WHERE date=? AND period=?", (date_str, period))
    conn.commit()
    conn.close()

    # 写入 market_insight
    for ins in all_insights:
        item = {"date": date_str, "period": period, **ins}
        db.upsert_insight(item)
    print(f"  [洞见] 生成 {len(all_insights)} 条，已写入 market_insight")

    # 构建 data_summary 供报告使用
    summary = {
        "date": date_str, "period": period,
        "meta": {"trade_date": date_str.replace("-", ""), "mode": period, "date": date_str},
        "insights": all_insights,
        "stats": {
            "index_count": len(db.query_index_quote(date=date_str)),
            "sector_count": len(db.query_sector_moneyflow(date=date_str)),
            "dragon_tiger_count": len(db.query_dragon_tiger(date=date_str)),
            "limit_up_count": len(db.query_limit_up(date=date_str)),
        },
        "indices": db.query_index_quote(date=date_str),
        "sectors_top": db.query_sector_moneyflow(date=date_str, top_n=10),
        "north_money": db.query_north_money(date=date_str),
        "dragon_tiger": db.query_dragon_tiger(date=date_str)[:10],
        "limit_up": db.query_limit_up(date=date_str)[:20],
    }
    # 保存 data_summary.json 供报告编排器读取
    os.makedirs("data", exist_ok=True)
    with open("data/data_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(f"  [洞见] data_summary.json 已生成")
    return summary


def main():
    parser = argparse.ArgumentParser(description="v4.0 洞见引擎")
    parser.add_argument("--date", default=None)
    parser.add_argument("--period", default="morning",
                        choices=["morning", "noon", "evening"])
    args = parser.parse_args()
    from db import DB
    db = DB()
    db.init()
    summary = run(db, args.date, args.period)
    print(f"\n[洞见] 共 {len(summary['insights'])} 条，统计: {summary['stats']}")


if __name__ == "__main__":
    main()
