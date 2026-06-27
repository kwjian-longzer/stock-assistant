# -*- coding: utf-8 -*-
"""
gold_stock_discovery.py  —  v5.0 共振金股发现引擎
=========================================================
7维共振 + 2加权层 + 1过滤层

核心维度（高权重）:
  1. 研报覆盖（权重40）  ← 专业人员加持，最高
  2. 钱三强（权重30）   ← 量化三强合一

加分维度（强度差异化打分）:
  3. 涨停动量（基础15）  ← 连板数加权
  4. 龙虎榜资金（基础15）← 净买入金额加权
  5. 北向资金（基础10）  ← 净流入金额加权
  6. 舆情催化（基础10）  ← 电报条数+红色标记加权
  7. 主力资金流入（基础15）← 超大单+大单净额加权

加权层:
  W1. 板块热度加权：高潮×1.2，崛起×1.0，退烧×0.5
  W2. 多维共振加成：≥4维+20，≥5维+35

过滤层:
  F1. 周期退烧排除：板块"退烧"→排除（可配置为仅警示）
  F2. ST/退市风险排除

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

# v5 各维度权重
WEIGHTS_V5 = {
    "vip_research": 40,       # 研报（最高）
    "qian_sanqiang": 30,      # 钱三强（第二）
    "main_capital_flow": 15,  # 主力资金流入强度（新增）
    "limit_up": 15,           # 涨停动量
    "dragon_tiger": 15,       # 龙虎榜资金
    "north_money": 10,        # 北向资金
    "cls_telegraph": 10,      # 舆情催化
}


def _safe(v, default=None):
    try:
        return float(v) if v not in (None, "", "0.000") else default
    except (ValueError, TypeError):
        return default


# ======================================================================
# 7个强度打分函数（每个返回(基础分, 加分, 总分, 命中bool)）
# ======================================================================

def score_research(vip_count):
    """研报覆盖：每篇+5，上限+20"""
    base = WEIGHTS_V5["vip_research"]
    bonus = min(vip_count * 5, 20)
    return base, bonus, base + bonus, vip_count > 0


def score_qian_sanqiang(strategy, detail):
    """钱三强：三强全命中+10，两强命中+5"""
    base = WEIGHTS_V5["qian_sanqiang"]
    bonus = 10 if "全中" in (strategy or "") else (5 if "两强" in (strategy or "") else 0)
    return base, bonus, base + bonus, True


def score_main_capital(net_lg, net_elg):
    """主力资金流入强度：(超大单净额+大单净额/2)/亿×2，上限+15"""
    base = WEIGHTS_V5["main_capital_flow"]
    if net_lg is None and net_elg is None:
        return 0, 0, 0, False
    total = (net_elg or 0) + (net_lg or 0) / 2
    if total <= 0:
        return 0, 0, 0, False
    bonus = min(total / 1e8 * 2, 15)
    return base, bonus, base + bonus, True


def score_limit_up(pct_chg, limit_up_data):
    """涨停动量：连板数×5"""
    base = WEIGHTS_V5["limit_up"]
    # 如果有连板信息用连板数，否则用pct_chg判断
    consecutive = limit_up_data.get("consecutive", 1) if limit_up_data else 1
    bonus = min(consecutive * 5, 15)
    return base, bonus, base + bonus, True


def score_dragon_tiger(net_buy):
    """龙虎榜资金：净买入金额/亿×3，上限+15"""
    base = WEIGHTS_V5["dragon_tiger"]
    if not net_buy or net_buy <= 0:
        return base, 0, base, True
    bonus = min(net_buy / 1e8 * 3, 15)
    return base, bonus, base + bonus, True


def score_north_money(north_amount):
    """北向资金：净流入/10亿×2，上限+10"""
    base = WEIGHTS_V5["north_money"]
    if not north_amount or north_amount <= 0:
        return 0, 0, 0, False
    bonus = min(north_amount / 1e8 * 2, 10)
    return base, bonus, base + bonus, True


def score_cls_telegraph(cls_count, red_count):
    """舆情催化：电报条数×1 + 红色标记×3，上限+10"""
    base = WEIGHTS_V5["cls_telegraph"]
    bonus = min(cls_count * 1 + red_count * 3, 10)
    return base, bonus, base + bonus, cls_count > 0


# ======================================================================
# 加权层
# ======================================================================

# 板块名映射（处理heat_tracker与Tushare industry名称差异）
SECTOR_NAME_MAP = {
    "AI算力": ["AI算力", "人工智能", "AI", "算力"],
    "半导体芯片": ["半导体", "芯片", "集成电路"],
    "消费电子": ["消费电子", "电子"],
    "新能源": ["新能源", "光伏", "锂电", "储能"],
    "机器人": ["机器人", "自动化"],
    "低空经济": ["低空经济", "无人机"],
    "医药生物": ["医药", "生物", "医疗"],
    "军工航天": ["军工", "航天", "国防"],
    "汽车智驾": ["汽车", "智驾", "新能源车"],
    "金融科技": ["金融科技", "金融", "券商"],
}


def get_sector_lifecycle(industry, heat_tracking_data):
    """根据股票行业获取板块生命周期状态"""
    if not industry or not heat_tracking_data:
        return "未知"
    for sector_name, aliases in SECTOR_NAME_MAP.items():
        if any(a in industry for a in aliases):
            return heat_tracking_data.get(sector_name, "未知")
    return "未知"


def apply_sector_heat_weight(score, lifecycle):
    """板块热度加权：高潮×1.2，崛起×1.0，退烧×0.5"""
    if lifecycle == "高潮":
        return score * 1.2
    elif lifecycle == "退烧":
        return score * 0.5
    return score


def apply_resonance_bonus(score, dimension_count):
    """多维共振加成：≥4维+20，≥5维+35"""
    if dimension_count >= 5:
        return score + 35
    elif dimension_count >= 4:
        return score + 20
    return score


# ======================================================================
# 过滤层
# ======================================================================

def should_exclude(code, name, lifecycle, exclude_decline=True):
    """过滤层：退烧板块排除 + ST排除"""
    # ST/退市风险排除
    if name and ("ST" in name or "*ST" in name):
        return True, "ST/退市风险股"
    # 周期退烧排除
    if exclude_decline and lifecycle == "退烧":
        return True, "板块退烧"
    return False, ""


# ======================================================================
# 主函数
# ======================================================================

def discover(db, date_str=None, top_n=5, exclude_decline=True):
    """v5 共振金股发现引擎，写入 gold_stock，返回推荐列表

    Args:
        db: DB实例
        date_str: 日期 YYYY-MM-DD（默认今天）
        top_n: 返回Top N
        exclude_decline: 是否排除退烧板块（True=排除，False=仅警示保留）

    Returns:
        list: 推荐金股列表
    """
    if date_str is None:
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    print(f"\n=== v5 共振金股发现 {date_str} ===")

    # ------------------------------------------------------------------
    # 1. 读取7个维度的数据
    # ------------------------------------------------------------------

    # 维度1: 研报覆盖（VIP文章发现）
    vip_map = defaultdict(int)
    try:
        vip = db.query_vip_discovered_stocks(limit=100)
        for v in vip:
            code = v.get("stock_code", "")
            if code:
                vip_map[code] += 1
    except Exception:
        pass

    # 维度2: 钱三强
    qsq = db.query_qian_sanqiang(date=date_str, limit=50)
    qsq_map = {r.get("stock_code", ""): r for r in qsq if r.get("stock_code")}

    # 维度3: 主力资金流入（moneyflow）
    mf_rows = db.query_moneyflow(date=date_str, limit=500)
    mf_map = {}
    for r in mf_rows:
        code = r.get("ts_code", "")
        if code:
            mf_map[code] = r

    # 维度4: 涨停池
    lu = db.query_limit_up(date=date_str)
    lu_map = {r.get("ts_code", ""): r for r in lu if r.get("ts_code")}

    # 维度5: 龙虎榜
    dt = db.query_dragon_tiger(date=date_str)
    dt_map = {r.get("ts_code", ""): r for r in dt if r.get("ts_code")}

    # 维度6: 北向资金（全局，简化处理：净流入>0则所有股票加分）
    north_data = db.query_north_money(date=date_str)
    north_amount_yi = _safe(north_data.get("north_money")) if north_data else None
    # DB存储单位为亿元，转换为元以适配打分函数
    north_amount_yuan = north_amount_yi * 1e8 if north_amount_yi is not None else None
    north_score = score_north_money(north_amount_yuan)  # (base, bonus, total, hit)
    north_hit = north_score[3]

    # 维度7: 舆情催化（财联社电报提及）
    telegraphs = db.query_telegraphs(date=date_str, limit=200)

    # 构建 name -> code 映射（用于解析电报提及的股票名）
    name_to_code = {}
    for r in lu:
        if r.get("name") and r.get("ts_code"):
            name_to_code[r["name"]] = r["ts_code"]
    for r in dt:
        if r.get("name") and r.get("ts_code"):
            name_to_code[r["name"]] = r["ts_code"]
    for r in qsq:
        if r.get("stock_name") and r.get("stock_code"):
            name_to_code[r["stock_name"]] = r["stock_code"]

    # 解析电报提及股票 → 按code聚合
    cls_map = defaultdict(lambda: {"count": 0, "red": 0, "titles": []})
    for t in telegraphs:
        is_red = int(t.get("is_red", 0) or 0)
        for s in t.get("stocks", []):
            code = name_to_code.get(s, "")
            if code:
                cls_map[code]["count"] += 1
                if is_red:
                    cls_map[code]["red"] += 1
                cls_map[code]["titles"].append(t.get("title", "")[:40])

    # 板块热度（生命周期）映射
    heat_rows = db.query_heat_tracking(date=date_str)
    heat_map = {}
    for r in heat_rows:
        sector = r.get("sector", "")
        lifecycle = r.get("lifecycle", "")
        if sector:
            heat_map[sector] = lifecycle

    # 候选股票池
    all_codes = set(qsq_map) | set(dt_map) | set(lu_map) | set(cls_map) | set(vip_map) | set(mf_map)
    all_codes.discard("")

    print(f"  候选股票池: {len(all_codes)} 只")
    print(f"  研报:{len(vip_map)} 钱三强:{len(qsq_map)} 主力资金:{len(mf_map)} "
          f"涨停:{len(lu_map)} 龙虎榜:{len(dt_map)} "
          f"北向:{'有' if north_hit else '无'} 舆情:{len(cls_map)}")
    print(f"  板块热度: {len(heat_map)} 个板块生命周期数据")

    # ------------------------------------------------------------------
    # 2. 对每只候选股票调用7个打分函数
    # ------------------------------------------------------------------
    candidates = []
    filtered_count = 0

    for code in all_codes:
        dim_scores = {}  # dimension_name -> (base, bonus, total, hit)
        raw_score = 0
        hit_count = 0
        core_hit = False
        detail = {"code": code}

        # 维度1: 研报覆盖
        vip_count = vip_map.get(code, 0)
        if vip_count > 0:
            b, bn, t, hit = score_research(vip_count)
            if hit:
                dim_scores["vip_research"] = (b, bn, t, hit)
                raw_score += t
                hit_count += 1
                core_hit = True
                detail["vip_count"] = vip_count

        # 维度2: 钱三强
        if code in qsq_map:
            r = qsq_map[code]
            strategy = r.get("strategy", "")
            detail["name"] = r.get("stock_name", "")
            b, bn, t, hit = score_qian_sanqiang(strategy, r)
            if hit:
                dim_scores["qian_sanqiang"] = (b, bn, t, hit)
                raw_score += t
                hit_count += 1
                core_hit = True
                detail["strategy"] = strategy
                try:
                    d = json.loads(r.get("detail_json", "{}"))
                    detail["industry"] = d.get("industry", "")
                    detail["close"] = _safe(d.get("close"))
                    detail["pct_chg"] = _safe(d.get("pct_chg"))
                except Exception:
                    pass

        # 维度3: 主力资金流入
        if code in mf_map:
            r = mf_map[code]
            # DB存储单位为万元，转换为元以适配打分函数
            net_lg_wan = _safe(r.get("net_lg_amount"))
            net_elg_wan = _safe(r.get("net_elg_amount"))
            net_lg_yuan = net_lg_wan * 1e4 if net_lg_wan is not None else None
            net_elg_yuan = net_elg_wan * 1e4 if net_elg_wan is not None else None
            b, bn, t, hit = score_main_capital(net_lg_yuan, net_elg_yuan)
            if hit:
                dim_scores["main_capital_flow"] = (b, bn, t, hit)
                raw_score += t
                hit_count += 1
                detail.setdefault("name", r.get("name", ""))
                detail.setdefault("close", _safe(r.get("close")))
                detail.setdefault("pct_chg", _safe(r.get("pct_chg")))

        # 维度4: 涨停动量
        if code in lu_map:
            r = lu_map[code]
            detail.setdefault("name", r.get("name", ""))
            detail["industry"] = detail.get("industry") or r.get("industry", "")
            detail.setdefault("pct_chg", _safe(r.get("pct_chg")))
            b, bn, t, hit = score_limit_up(_safe(r.get("pct_chg")), r)
            if hit:
                dim_scores["limit_up"] = (b, bn, t, hit)
                raw_score += t
                hit_count += 1

        # 维度5: 龙虎榜资金
        if code in dt_map:
            r = dt_map[code]
            detail.setdefault("name", r.get("name", ""))
            net_buy_yi = _safe(r.get("net_buy"))
            # DB存储单位为亿元，转换为元以适配打分函数
            net_buy_yuan = net_buy_yi * 1e8 if net_buy_yi is not None else None
            b, bn, t, hit = score_dragon_tiger(net_buy_yuan)
            if hit:
                dim_scores["dragon_tiger"] = (b, bn, t, hit)
                raw_score += t
                hit_count += 1
                detail["dragon_net_buy"] = net_buy_yi
                detail["dragon_reason"] = r.get("reason", "")

        # 维度6: 北向资金（全局加分）
        if north_hit:
            b, bn, t, hit = north_score
            if hit:
                dim_scores["north_money"] = (b, bn, t, hit)
                raw_score += t
                hit_count += 1

        # 维度7: 舆情催化
        if code in cls_map:
            cls_data = cls_map[code]
            b, bn, t, hit = score_cls_telegraph(cls_data["count"], cls_data["red"])
            if hit:
                dim_scores["cls_telegraph"] = (b, bn, t, hit)
                raw_score += t
                hit_count += 1
                detail["cls_count"] = cls_data["count"]
                detail["cls_red"] = cls_data["red"]
                detail["cls_titles"] = cls_data["titles"][:3]

        # 候选门槛：≥2维命中（核心维度至少1个）
        if hit_count < 2 or not core_hit:
            continue

        # 确定行业和生命周期
        industry = detail.get("industry", "")
        name = detail.get("name", "")
        lifecycle = get_sector_lifecycle(industry, heat_map)

        # ------------------------------------------------------------------
        # 3. 应用板块热度加权
        # ------------------------------------------------------------------
        weighted_score = apply_sector_heat_weight(raw_score, lifecycle)

        # ------------------------------------------------------------------
        # 4. 应用多维共振加成
        # ------------------------------------------------------------------
        final_score = apply_resonance_bonus(weighted_score, hit_count)

        # ------------------------------------------------------------------
        # 5. 应用过滤层
        # ------------------------------------------------------------------
        excluded, exclude_reason = should_exclude(code, name, lifecycle,
                                                   exclude_decline=exclude_decline)
        if excluded:
            filtered_count += 1
            continue

        # 候选门槛：评分≥30
        if final_score < 30:
            continue

        detail["name"] = name
        detail["industry"] = industry
        detail["sector_lifecycle"] = lifecycle
        detail["dimensions"] = list(dim_scores.keys())
        detail["score"] = round(final_score, 1)
        detail["raw_score"] = round(raw_score, 1)
        detail["weighted_score"] = round(weighted_score, 1)
        detail["resonance"] = hit_count
        detail["strength_detail"] = {
            k: {"base": v[0], "bonus": round(v[1], 2),
                "total": round(v[2], 2), "hit": v[3]}
            for k, v in dim_scores.items()
        }
        candidates.append(detail)

    # ------------------------------------------------------------------
    # 6. 排序取TopN
    # ------------------------------------------------------------------
    candidates.sort(key=lambda x: (x["score"], x["resonance"]), reverse=True)
    gold = candidates[:top_n]

    # ------------------------------------------------------------------
    # 7. 写入 gold_stock 表
    # ------------------------------------------------------------------
    for g in gold:
        close = g.get("close")
        lifecycle = g.get("sector_lifecycle", "未知")
        item = {
            "name": g.get("name", ""),
            "code": g["code"],
            "recommend_date": date_str,
            "report_type": "v5_共振",
            "reason": "、".join(g.get("dimensions", [])),
            "score": g["score"],
            "price_at_recommend": close,
            "catalyst": "; ".join(g.get("cls_titles", []))[:200] if g.get("cls_titles") else "",
            "dragon_vein": g.get("dragon_reason", ""),
            "verification": f"共振维度{g.get('resonance', 0)}/7 + 板块:{lifecycle}",
            "signal_source": "、".join(g.get("dimensions", [])),
            "buy_range": "",
            "target_price": "",
            "stop_loss": "",
            "strength": "重点推荐" if g["score"] >= 80 else ("重点关注" if g["score"] >= 50 else "关注"),
        }
        if close:
            item["buy_range"] = f"{close*0.98:.2f}-{close*1.02:.2f}"
            item["target_price"] = f"{close*1.10:.2f}"
            item["stop_loss"] = f"{close*0.93:.2f}"
        db.upsert_gold_stock(item)

    print(f"  [金股] 命中2维以上候选 {len(candidates)} 只，过滤 {filtered_count} 只，推荐 Top{len(gold)}:")
    for i, g in enumerate(gold, 1):
        print(f"    {i}. {g.get('name','')}({g['code']}) 评分{g['score']} "
              f"共振{g['resonance']}/7 板块:{g.get('sector_lifecycle','未知')} "
              f"[{','.join(g['dimensions'])}]")

    # ------------------------------------------------------------------
    # 8. 保存 gold_stocks.json（含 sector_lifecycle 和 strength_detail）
    # ------------------------------------------------------------------
    enriched_gold = []
    for g in gold:
        close = g.get("close")
        lifecycle = g.get("sector_lifecycle", "未知")
        item = {
            "name": g.get("name", ""),
            "code": g["code"],
            "recommend_date": date_str,
            "score": g["score"],
            "raw_score": g.get("raw_score", 0),
            "weighted_score": g.get("weighted_score", 0),
            "resonance": g.get("resonance", 0),
            "dimensions": g.get("dimensions", []),
            "sector_lifecycle": lifecycle,
            "strength_detail": g.get("strength_detail", {}),
            "reason": "、".join(g.get("dimensions", [])),
            "verification": f"共振维度{g.get('resonance', 0)}/7 + 板块:{lifecycle}",
            "buy_range": "",
            "target_price": "",
            "stop_loss": "",
            "strength": "重点推荐" if g["score"] >= 80 else ("重点关注" if g["score"] >= 50 else "关注"),
        }
        if close:
            item["buy_range"] = f"{close*0.98:.2f}-{close*1.02:.2f}"
            item["target_price"] = f"{close*1.10:.2f}"
            item["stop_loss"] = f"{close*0.93:.2f}"
        enriched_gold.append(item)

    os.makedirs("data", exist_ok=True)
    with open("data/gold_stocks.json", "w", encoding="utf-8") as f:
        json.dump({"date": date_str, "gold_stocks": enriched_gold,
                   "total_candidates": len(candidates), "filtered": filtered_count,
                   "version": "v5_共振"},
                  f, ensure_ascii=False, indent=2, default=str)
    return gold


def main():
    parser = argparse.ArgumentParser(description="v5.0 共振金股发现引擎")
    parser.add_argument("--date", default=None)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--keep-decline", action="store_true",
                        help="保留退烧板块（仅警示，不排除）")
    args = parser.parse_args()
    from db import DB
    db = DB()
    db.init()
    gold = discover(db, args.date, args.top, exclude_decline=not args.keep_decline)
    print(f"\n[金股] 共推荐 {len(gold)} 只")


if __name__ == "__main__":
    main()
