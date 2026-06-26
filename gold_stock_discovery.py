# -*- coding: utf-8 -*-
"""
gold_stock_discovery.py  —  v4.0 金股发现（多维度共振）
=========================================================
从 DB 多个维度交叉验证，发现高共振金股：
  维度1: 钱三强选股（基本面强度）
  维度2: 龙虎榜（机构资金）
  维度3: 涨停池（动量）
  维度4: 财联社电报提及（催化剂）
  维度5: VIP研报发现（机构覆盖）
命中维度越多 → 共振越强 → 评分越高。结果写入 gold_stock 表。

用法:
    python gold_stock_discovery.py --date 2026-06-26
"""

import sys
import os
import json
import datetime
import argparse
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 各维度权重
WEIGHTS = {
    "qian_sanqiang": 35,   # 基本面三强
    "dragon_tiger": 25,    # 龙虎榜资金
    "limit_up": 15,        # 涨停动量
    "cls_telegraph": 15,   # 舆情催化
    "vip_research": 10,    # 研报覆盖
}


def _safe(v, default=None):
    try:
        return float(v) if v not in (None, "", "0.000") else default
    except (ValueError, TypeError):
        return default


def discover(db, date_str=None, top_n=5):
    """多维度共振金股发现，写入 gold_stock，返回推荐列表"""
    if date_str is None:
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    print(f"\n=== 金股发现 {date_str} ===")

    # 维度1: 钱三强
    qsq = db.query_qian_sanqiang(date=date_str, limit=50)
    qsq_map = {}
    for r in qsq:
        qsq_map[r.get("stock_code", "")] = r

    # 维度2: 龙虎榜
    dt = db.query_dragon_tiger(date=date_str)
    dt_map = {r.get("ts_code", ""): r for r in dt}

    # 维度3: 涨停池
    lu = db.query_limit_up(date=date_str)
    lu_map = {r.get("ts_code", ""): r for r in lu}

    # 维度4: 财联社电报提及股票
    telegraphs = db.query_telegraphs(date=date_str, limit=200)
    cls_stocks = defaultdict(list)
    for t in telegraphs:
        for s in t.get("stocks", []):
            cls_stocks[s].append(t.get("title", "")[:40])

    # 维度5: VIP研报发现
    vip_stocks = defaultdict(int)
    try:
        vip = db.query_vip_discovered_stocks(limit=100)
        for v in vip:
            vip_stocks[v.get("stock_code", "")] += 1
    except Exception:
        pass

    # 汇总所有候选股票
    all_codes = set(qsq_map) | set(dt_map) | set(lu_map) | set(cls_stocks) | set(vip_stocks)
    all_codes.discard("")

    print(f"  候选股票池: {len(all_codes)} 只")
    print(f"  钱三强:{len(qsq_map)} 龙虎榜:{len(dt_map)} 涨停:{len(lu_map)} "
          f"电报:{len(cls_stocks)} 研报:{len(vip_stocks)}")

    candidates = []
    for code in all_codes:
        dims = []
        score = 0
        detail = {"code": code}

        if code in qsq_map:
            dims.append("钱三强")
            score += WEIGHTS["qian_sanqiang"]
            r = qsq_map[code]
            detail["name"] = r.get("stock_name", "")
            detail["strategy"] = r.get("strategy", "")
            detail["industry"] = ""
            detail["close"] = None
            try:
                d = json.loads(r.get("detail_json", "{}"))
                detail["industry"] = d.get("industry", "")
                detail["close"] = _safe(d.get("close"))
                detail["pct_chg"] = _safe(d.get("pct_chg"))
                detail["turnover"] = _safe(d.get("turnover_rate"))
            except Exception:
                pass

        if code in dt_map:
            dims.append("龙虎榜")
            score += WEIGHTS["dragon_tiger"]
            r = dt_map[code]
            detail.setdefault("name", r.get("name", ""))
            detail["dragon_net_buy"] = _safe(r.get("net_buy"))
            detail["dragon_reason"] = r.get("reason", "")

        if code in lu_map:
            dims.append("涨停")
            score += WEIGHTS["limit_up"]
            r = lu_map[code]
            detail.setdefault("name", r.get("name", ""))
            detail["industry"] = detail.get("industry") or r.get("industry", "")
            detail.setdefault("pct_chg", _safe(r.get("pct_chg")))

        if code in cls_stocks:
            dims.append("舆情")
            score += WEIGHTS["cls_telegraph"]
            detail["cls_titles"] = cls_stocks[code][:3]

        if code in vip_stocks:
            dims.append("研报")
            score += WEIGHTS["vip_research"]
            detail["vip_count"] = vip_stocks[code]

        # 至少命中2个维度才纳入候选
        if len(dims) < 2:
            continue

        detail["dimensions"] = dims
        detail["score"] = score
        detail["resonance"] = len(dims)
        candidates.append(detail)

    # 按评分排序
    candidates.sort(key=lambda x: (x["score"], x["resonance"]), reverse=True)
    gold = candidates[:top_n]

    # 写入 gold_stock 表
    for g in gold:
        close = g.get("close")
        item = {
            "name": g.get("name", ""),
            "code": g["code"],
            "recommend_date": date_str,
            "report_type": "v4_共振",
            "reason": "、".join(g.get("dimensions", [])),
            "score": g["score"],
            "price_at_recommend": close,
            "catalyst": "; ".join(g.get("cls_titles", []))[:200] if g.get("cls_titles") else "",
            "dragon_vein": g.get("dragon_reason", ""),
            "verification": f"共振维度{g.get('resonance',0)}/5",
            "signal_source": "、".join(g.get("dimensions", [])),
            "buy_range": "",
            "target_price": "",
            "stop_loss": "",
            "strength": "重点关注" if g["score"] >= 50 else "关注",
        }
        if close:
            item["buy_range"] = f"{close*0.98:.2f}-{close*1.02:.2f}"
            item["target_price"] = f"{close*1.10:.2f}"
            item["stop_loss"] = f"{close*0.93:.2f}"
        db.upsert_gold_stock(item)

    print(f"  [金股] 命中2维以上候选 {len(candidates)} 只，推荐 Top{len(gold)}:")
    for i, g in enumerate(gold, 1):
        print(f"    {i}. {g.get('name','')}({g['code']}) 评分{g['score']} "
              f"共振{g['resonance']}/5 [{','.join(g['dimensions'])}]")

    # 保存 JSON 供报告引用
    os.makedirs("data", exist_ok=True)
    with open("data/gold_stocks.json", "w", encoding="utf-8") as f:
        json.dump({"date": date_str, "gold_stocks": gold, "total_candidates": len(candidates)},
                  f, ensure_ascii=False, indent=2, default=str)
    return gold


def main():
    parser = argparse.ArgumentParser(description="v4.0 金股发现")
    parser.add_argument("--date", default=None)
    parser.add_argument("--top", type=int, default=5)
    args = parser.parse_args()
    from db import DB
    db = DB()
    db.init()
    gold = discover(db, args.date, args.top)
    print(f"\n[金股] 共推荐 {len(gold)} 只")


if __name__ == "__main__":
    main()
