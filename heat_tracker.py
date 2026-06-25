#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
热点热度量化追踪器

基于三个维度计算市场热点的按日热度，绘制时间-热度变化曲线：

H(t) = W1 * 资金流向因子 + W2 * 信息密度因子 + W3 * 涨停密度因子

权重: W1=0.5（资金最重要）, W2=0.3, W3=0.2

数据源:
  - Tushare moneyflow: 个股资金流向（聚合到板块）
  - 电报归档: 信息密度（关键词提及次数）
  - Tushare limit_list_d / daily: 涨停个股统计

生命周期判定:
  - 崛起: 连续3日热度上升 + H>50
  - 高潮: H>70 且当日≥近3日峰值
  - 退烧: 热度连续2日下降 + 资金净流出
  - 冷却: H<30（不纳入追踪）

资金流向生命周期:
  主力开始进 → 其他资金跟进 → 主力退出 → 散户站岗
"""

import json
import os
import sys
import re
from collections import defaultdict, Counter
from datetime import datetime, timedelta

# 热度计算权重
W_CAPITAL = 0.5  # 资金流向
W_INFO = 0.3     # 信息密度
W_LIMIT = 0.2    # 涨停密度

# 热度阈值
HEAT_THRESHOLD_CLIMAX = 70
HEAT_THRESHOLD_RISING = 50
HEAT_THRESHOLD_COOL = 30


def _ensure_tushare():
    try:
        import tushare as ts
        return ts
    except ImportError:
        import subprocess
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "tushare", "--break-system-packages", "-q"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        import tushare as ts
        return ts


def compute_sector_capital_flow(pro, trade_date, stock_basic_list):
    """计算各行业的资金流向（聚合个股moneyflow到行业维度）

    Args:
        pro: Tushare pro_api
        trade_date: 交易日期 YYYYMMDD
        stock_basic_list: 股票基础信息列表

    Returns:
        dict: {行业名: {net_mf_amount, stock_count, buy_lg_amount, sell_lg_amount}}
    """
    try:
        df_mf = pro.moneyflow(trade_date=trade_date)
        if df_mf is None or len(df_mf) == 0:
            return {}

        # 合并行业信息
        industry_map = {s['ts_code']: s.get('industry', '') for s in stock_basic_list}

        sector_flow = defaultdict(lambda: {
            'net_mf_amount': 0.0,
            'buy_lg_amount': 0.0,
            'sell_lg_amount': 0.0,
            'buy_el_amount': 0.0,
            'sell_el_amount': 0.0,
            'stock_count': 0,
        })

        for _, row in df_mf.iterrows():
            ts_code = row.get('ts_code', '')
            industry = industry_map.get(ts_code, '未知')
            sector_flow[industry]['net_mf_amount'] += float(row.get('net_mf_amount', 0) or 0)
            sector_flow[industry]['buy_lg_amount'] += float(row.get('buy_lg_amount', 0) or 0)
            sector_flow[industry]['sell_lg_amount'] += float(row.get('sell_lg_amount', 0) or 0)
            sector_flow[industry]['buy_el_amount'] += float(row.get('buy_el_amount', 0) or 0)
            sector_flow[industry]['sell_el_amount'] += float(row.get('sell_el_amount', 0) or 0)
            sector_flow[industry]['stock_count'] += 1

        return dict(sector_flow)
    except Exception as e:
        print(f"[WARN] 资金流向计算失败: {e}")
        return {}


def compute_info_density(telegraph_archive_dir, keywords, target_date_str):
    """计算某热点的信息密度（电报提及次数）

    Args:
        telegraph_archive_dir: 电报归档目录
        keywords: 热点关键词列表
        target_date_str: 目标日期 YYYY-MM-DD

    Returns:
        int: 当日电报中包含关键词的条数
    """
    archive_path = os.path.join(telegraph_archive_dir, f"{target_date_str}.json")
    if not os.path.exists(archive_path):
        return 0

    try:
        with open(archive_path, 'r', encoding='utf-8') as f:
            telegraphs = json.load(f)

        count = 0
        for tel in telegraphs:
            title = tel.get('title', '') + ' ' + tel.get('content', '')
            for kw in keywords:
                if kw in title:
                    count += 1
                    break
        return count
    except Exception:
        return 0


def compute_limit_up_density(pro, trade_date, stock_basic_list):
    """计算各行业的涨停密度

    优先使用Tushare limit_list_d，无权限时降级到东方财富涨停池API。

    Args:
        pro: Tushare pro_api
        trade_date: 交易日期
        stock_basic_list: 股票基础信息

    Returns:
        dict: {行业名: {limit_up_count, total_count, density}}
    """
    industry_map = {s['ts_code']: s.get('industry', '') for s in stock_basic_list}
    industry_total = Counter(industry_map.values())

    limit_by_industry = defaultdict(int)

    # 方案1: Tushare limit_list_d
    try:
        df_limit = pro.limit_list_d(trade_date=trade_date)
        if df_limit is not None and len(df_limit) > 0:
            for _, row in df_limit.iterrows():
                ts_code = row.get('ts_code', '')
                industry = industry_map.get(ts_code, row.get('industry', '未知'))
                limit_by_industry[industry] += 1
    except Exception:
        # 方案2: 东方财富涨停池API（无需权限）
        try:
            import requests
            # 东方财富涨停池API
            url = "https://push2ex.eastmoney.com/getTopicZTPool"
            params = {
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "dpt": "wz.ztzt",
                "Ession": "128940000",
                "date": trade_date,
                "fields": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f21",
                "sort": "f3",
                "order": "1",
                "size": "500",
            }
            r = requests.get(url, params=params, timeout=10)
            data = r.json()
            pool = data.get('data', {}).get('pool', [])
            for item in pool:
                # 东方财富代码: 1.600xxx(沪), 0.000xxx(深)
                raw_code = item.get('c', '')
                market = item.get('m', 0)
                if market == 1:
                    ts_code = f"{raw_code}.SH"
                else:
                    ts_code = f"{raw_code}.SZ"
                industry = industry_map.get(ts_code, '未知')
                limit_by_industry[industry] += 1
        except Exception:
            pass  # 两个方案都失败，返回空

    if not limit_by_industry:
        return {}

    result = {}
    for ind, count in limit_by_industry.items():
        total = industry_total.get(ind, 1)
        result[ind] = {
            'limit_up_count': count,
            'total_count': total,
            'density': count / total * 100 if total > 0 else 0,
        }
    return result


def calculate_heat_score(capital_flow, info_density, limit_density, max_capital=None):
    """计算单日热度分数

    H(t) = W1 * 资金流向因子 + W2 * 信息密度因子 + W3 * 涨停密度因子

    资金流出会降低热度但不会让热度为负（资金流出=热度归零而非负值）。

    Args:
        capital_flow: 板块净流入金额（万元）
        info_density: 电报提及次数
        limit_density: 涨停密度百分比
        max_capital: 标准化用最大资金额（None则自动估算）

    Returns:
        float: 热度分数 0-100
    """
    # 1. 资金流向因子（标准化到0-100）
    if max_capital is None:
        max_capital = 500000  # 默认50亿作为标准化基准（万元）
    # 资金流入: 正向贡献热度；资金流出: 热度归零（不贡献负热度）
    if capital_flow > 0:
        capital_factor = min(capital_flow / max_capital * 100, 100)
    else:
        capital_factor = 0  # 资金流出时，资金维度热度为0

    # 2. 信息密度因子（标准化到0-100）
    info_factor = min(info_density / 20 * 100, 100)  # 20条电报=满分

    # 3. 涨停密度因子（标准化到0-100）
    limit_factor = min(limit_density * 10, 100)  # 10%涨停率=满分

    # 加权计算
    heat = W_CAPITAL * capital_factor + W_INFO * info_factor + W_LIMIT * limit_factor
    return round(heat, 1)


def determine_lifecycle(heat_series, capital_series):
    """判定热点生命周期状态

    Args:
        heat_series: 近N日热度列表 [h1, h2, ..., hN]（旧→新）
        capital_series: 近N日资金流向列表

    Returns:
        dict: {lifecycle, trend, description}
    """
    if len(heat_series) < 2:
        return {"lifecycle": "未知", "trend": "无数据", "description": "数据不足"}

    current_heat = heat_series[-1]
    prev_heat = heat_series[-2]

    # 趋势判断
    rising_count = sum(1 for i in range(1, len(heat_series))
                       if heat_series[i] > heat_series[i-1])
    falling_count = sum(1 for i in range(1, len(heat_series))
                        if heat_series[i] < heat_series[i-1])

    # 资金趋势
    current_capital = capital_series[-1] if capital_series else 0
    prev_capital = capital_series[-2] if len(capital_series) >= 2 else 0
    capital_turning_negative = (prev_capital > 0 and current_capital < 0)

    # 生命周期判定
    if current_heat < HEAT_THRESHOLD_COOL:
        return {"lifecycle": "冷却", "trend": "↓", "description": f"热度{current_heat}低于阈值{HEAT_THRESHOLD_COOL}"}

    if current_heat >= HEAT_THRESHOLD_CLIMAX:
        # 高潮期：检查是否在退烧
        if current_heat < prev_heat and capital_turning_negative:
            return {"lifecycle": "退烧", "trend": "↓↓",
                    "description": f"热度{current_heat}从峰值回落，资金转流出"}
        return {"lifecycle": "高潮", "trend": "↑↑",
                "description": f"热度{current_heat}处于高位"}

    if rising_count >= 2 and current_heat >= HEAT_THRESHOLD_RISING:
        return {"lifecycle": "崛起", "trend": "↑",
                "description": f"热度{current_heat}连续上升{rising_count}日"}

    if falling_count >= 2 and current_capital < 0:
        return {"lifecycle": "退烧", "trend": "↓",
                "description": f"热度{current_heat}连续下降，资金净流出"}

    if current_heat > prev_heat:
        return {"lifecycle": "崛起", "trend": "↑",
                "description": f"热度{current_heat}上升中"}
    else:
        return {"lifecycle": "退烧", "trend": "↓",
                "description": f"热度{current_heat}有所回落"}


def determine_capital_phase(capital_series, large_buy_series, large_sell_series):
    """判定资金流向生命周期阶段

    主力开始进 → 其他资金跟进 → 主力退出 → 散户站岗

    Args:
        capital_series: 近N日净流入金额
        large_buy_series: 近N日大单买入
        large_sell_series: 近N日大单卖出

    Returns:
        str: 生命周期阶段描述
    """
    if len(capital_series) < 3:
        return "数据不足"

    current_net = capital_series[-1]
    prev_net = capital_series[-2]
    prev2_net = capital_series[-3] if len(capital_series) >= 3 else 0

    # 判断连续流入/流出
    inflow_3d = all(n > 0 for n in capital_series[-3:]) if len(capital_series) >= 3 else False
    outflow_2d = all(n < 0 for n in capital_series[-2:]) if len(capital_series) >= 2 else False

    # 大单趋势
    current_lg_buy = large_buy_series[-1] if large_buy_series else 0
    current_lg_sell = large_sell_series[-1] if large_sell_series else 0
    large_net = current_lg_buy - current_lg_sell

    if inflow_3d and large_net > 0:
        if current_net > prev_net:
            return "主力持续流入，其他资金跟进"
        else:
            return "主力开始进"
    elif outflow_2d and large_net < 0:
        if current_net < prev_net:
            return "主力退出"
        else:
            return "散户站岗"
    elif prev_net > 0 and current_net < 0:
        return "主力退出（资金由正转负）"
    elif current_net > 0 and prev_net < 0:
        return "主力开始进（资金由负转正）"
    else:
        return "资金观望"


def render_heat_curve(heat_series, capital_series, dates, hotspot_name, lifecycle_info):
    """生成文本可视化的时间-热度变化曲线

    Args:
        heat_series: 热度列表
        capital_series: 资金流向列表
        dates: 日期列表
        hotspot_name: 热点名称
        lifecycle_info: 生命周期信息

    Returns:
        str: 文本格式的热度曲线
    """
    # 热度符号映射
    def heat_to_symbol(h):
        if h < 0:
            h = abs(h)
        if h < 15:
            return '▁'
        elif h < 30:
            return '▂'
        elif h < 45:
            return '▃'
        elif h < 55:
            return '▄'
        elif h < 65:
            return '▅'
        elif h < 75:
            return '▆'
        elif h < 85:
            return '▇'
        else:
            return '█'

    lines = []
    lines.append(f"### {hotspot_name} 热度追踪")
    lines.append("")
    lines.append(f"**生命周期**: {lifecycle_info['lifecycle']} {lifecycle_info['trend']} | "
                 f"{lifecycle_info['description']}")
    lines.append("")

    # 日期行
    date_labels = "  ".join(d[5:] for d in dates)  # MM-DD
    # 热度行
    heat_values = "  ".join(f"{h:.0f}" for h in heat_series)
    # 曲线行
    curve = " ".join(heat_to_symbol(h) for h in heat_series)
    # 资金行
    capital_values = "  ".join(f"{c/10000:+.1f}亿" for c in capital_series)

    lines.append("```")
    lines.append(f"日期   {date_labels}")
    lines.append(f"热度   {heat_values}")
    lines.append(f"曲线   {curve}")
    lines.append(f"资金   {capital_values}")
    lines.append("```")
    lines.append("")

    return '\n'.join(lines)


def compute_hotspot_heat(pro, stock_basic_list, telegraph_dir, hotspot_name,
                         keywords, trade_dates, sector_stocks=None):
    """计算单个热点的多日热度曲线

    Args:
        pro: Tushare pro_api
        stock_basic_list: 股票基础信息
        telegraph_dir: 电报归档目录
        hotspot_name: 热点名称
        keywords: 热点关键词列表
        trade_dates: 交易日列表 ['2026-06-20', '2026-06-23', ...]
        sector_stocks: 该热点涉及的股票代码列表（用于精确资金计算）

    Returns:
        dict: 热点热度数据
    """
    heat_series = []
    capital_series = []
    info_series = []
    limit_series = []
    capital_phase_series = []
    large_buy_series = []
    large_sell_series = []

    for date_str in trade_dates:
        date_compact = date_str.replace('-', '')

        # 1. 资金流向
        sector_flow = compute_sector_capital_flow(pro, date_compact, stock_basic_list)

        # 如果有精确股票列表，用个股聚合；否则用行业聚合
        if sector_stocks:
            try:
                df_mf = pro.moneyflow(trade_date=date_compact)
                if df_mf is not None and len(df_mf) > 0:
                    stock_set = set(sector_stocks)
                    df_hot = df_mf[df_mf['ts_code'].isin(stock_set)]
                    net_capital = float(df_hot['net_mf_amount'].sum()) if len(df_hot) > 0 else 0
                    buy_lg = float(df_hot['buy_lg_amount'].sum()) if len(df_hot) > 0 else 0
                    sell_lg = float(df_hot['sell_lg_amount'].sum()) if len(df_hot) > 0 else 0
                else:
                    net_capital, buy_lg, sell_lg = 0, 0, 0
            except Exception:
                net_capital, buy_lg, sell_lg = 0, 0, 0
        else:
            # 行业聚合
            total_net = sum(s.get('net_mf_amount', 0) for s in sector_flow.values())
            total_buy_lg = sum(s.get('buy_lg_amount', 0) for s in sector_flow.values())
            total_sell_lg = sum(s.get('sell_lg_amount', 0) for s in sector_flow.values())
            net_capital = total_net
            buy_lg = total_buy_lg
            sell_lg = total_sell_lg

        capital_series.append(net_capital)
        large_buy_series.append(buy_lg)
        large_sell_series.append(sell_lg)

        # 2. 信息密度
        info_count = compute_info_density(telegraph_dir, keywords, date_str)
        info_series.append(info_count)

        # 3. 涨停密度
        limit_data = compute_limit_up_density(pro, date_compact, stock_basic_list)
        # 取相关行业的平均涨停密度
        if sector_stocks and limit_data:
            # 用个股所在行业的平均涨停密度
            related_industries = set()
            for s in stock_basic_list:
                if s['ts_code'] in (sector_stocks or []):
                    if s.get('industry'):
                        related_industries.add(s['industry'])
            avg_density = sum(limit_data.get(ind, {}).get('density', 0) for ind in related_industries) / max(len(related_industries), 1)
        elif limit_data:
            avg_density = sum(d.get('density', 0) for d in limit_data.values()) / max(len(limit_data), 1)
        else:
            avg_density = 0
        limit_series.append(avg_density)

        # 计算单日热度
        heat = calculate_heat_score(net_capital, info_count, avg_density)
        heat_series.append(heat)

        # 资金阶段
        phase = determine_capital_phase(capital_series, large_buy_series, large_sell_series)
        capital_phase_series.append(phase)

    # 生命周期判定
    lifecycle_info = determine_lifecycle(heat_series, capital_series)

    # 生成热度曲线
    curve_text = render_heat_curve(
        heat_series, capital_series, trade_dates,
        hotspot_name, lifecycle_info
    )

    # 当前资金阶段
    current_capital_phase = capital_phase_series[-1] if capital_phase_series else "数据不足"

    return {
        "hotspot_name": hotspot_name,
        "keywords": keywords,
        "trade_dates": trade_dates,
        "heat_series": heat_series,
        "capital_series": capital_series,
        "info_series": info_series,
        "limit_series": limit_series,
        "capital_phase": current_capital_phase,
        "lifecycle": lifecycle_info,
        "heat_curve_text": curve_text,
        "current_heat": heat_series[-1] if heat_series else 0,
    }


if __name__ == "__main__":
    # 测试
    ts = _ensure_tushare()
    ts.set_token("8eaad9971749da18299f4932a7cabf068a495fdf06ef3aaafebfe365")
    pro = ts.pro_api()

    from vip_extractor import load_stock_database
    stock_db = load_stock_database(pro)

    # 测试: 计算AI算力热点近5日热度
    script_dir = os.path.dirname(os.path.abspath(__file__))
    telegraph_dir = os.path.join(script_dir, "data", "cls_telegraph_archive")

    # 生成近5个交易日
    today = datetime.now()
    trade_dates = []
    for i in range(7, 0, -1):
        d = today - timedelta(days=i)
        if d.weekday() < 5:  # 周一到周五
            trade_dates.append(d.strftime('%Y-%m-%d'))

    print(f"测试交易日: {trade_dates}")

    result = compute_hotspot_heat(
        pro, stock_db, telegraph_dir,
        "AI算力产业链",
        ["算力", "AI", "光模块", "服务器", "液冷"],
        trade_dates,
        sector_stocks=None  # 用行业聚合
    )

    print(result["heat_curve_text"])
    print(f"当前热度: {result['current_heat']}")
    print(f"生命周期: {result['lifecycle']}")
    print(f"资金阶段: {result['capital_phase']}")
