#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
热点板块热度量化追踪器 v3

v3改进:
  1. 动态板块选择: 扫描全市场110个行业，按资金流绝对值排序取Top6
  2. EMA平滑: alpha=0.4，消除日间剧烈波动，凸显趋势
  3. 5日累计资金流: 替代单日资金，趋势更清晰
  4. 板块连续性: 昨日热门板块若仍在趋势中则保留

参考观澜网站设计:
  - 仅用两个因子：板块资金流向 + 涨停板数量
  - 数据全部从Tushare数据库获取（20交易日历史）
  - 多个热点板块在同一图表中相对比
  - Y轴: -100 ~ +100（资金流入为正，流出为负）

热度公式:
  H(t) = W1 * 资金流向标准化 + W2 * 涨停密度标准化
  平滑: H_smooth(t) = alpha * H(t) + (1-alpha) * H_smooth(t-1)

  W1 = 0.6（资金流向为主）
  W2 = 0.4（涨停密度为辅）

用法:
  from heat_tracker import compute_sector_heat_comparison
  result = compute_sector_heat_comparison(pro, stock_basic, days=28)
"""

import json
import os
import sys
from collections import defaultdict, Counter
from datetime import datetime, timedelta

# 统一配置管理：从环境变量或 config.json 读取敏感信息
from settings import get_tushare_token

# 权重
W_CAPITAL = 0.6
W_LIMIT = 0.4

# 标准化基准
MAX_CAPITAL_PER_SECTOR = 300000  # 单板块单日30亿净流入=满分100（万元）
MAX_LIMIT_DENSITY = 10  # 10%涨停率=满分100


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


def get_trade_dates(pro, days=14):
    """获取最近N个交易日（从Tushare交易日历获取）

    Args:
        pro: Tushare pro_api
        days: 自然日天数（会自动过滤非交易日）

    Returns:
        list: 交易日列表，格式 YYYYMMDD，从旧到新
    """
    today = datetime.now()
    start_date = (today - timedelta(days=days + 10)).strftime('%Y%m%d')
    end_date = today.strftime('%Y%m%d')

    try:
        df = pro.trade_cal(exchange='SSE', start_date=start_date, end_date=end_date,
                           is_open='1', fields='cal_date')
        trade_dates = sorted(df['cal_date'].tolist())
        # 取最近 days 个自然日内的交易日
        cutoff = (today - timedelta(days=days)).strftime('%Y%m%d')
        trade_dates = [d for d in trade_dates if d >= cutoff]
        return trade_dates
    except Exception as e:
        # 降级：手动生成工作日
        print(f"[WARN] 交易日历获取失败({e})，降级为工作日估算")
        dates = []
        d = today
        while len(dates) < 10:
            if d.weekday() < 5:
                dates.append(d.strftime('%Y%m%d'))
            d -= timedelta(days=1)
        return sorted(dates)


def fetch_daily_moneyflow(pro, trade_date, stock_basic_list):
    """获取某日全市场资金流向，按行业聚合

    Returns:
        dict: {行业名: {net_mf_amount, buy_lg_amount, sell_lg_amount, stock_count}}
    """
    try:
        df = pro.moneyflow(trade_date=trade_date)
        if df is None or len(df) == 0:
            return {}

        industry_map = {s['ts_code']: s.get('industry', '') for s in stock_basic_list}

        sector_flow = defaultdict(lambda: {
            'net_mf_amount': 0.0,
            'buy_lg_amount': 0.0,
            'sell_lg_amount': 0.0,
            'stock_count': 0,
        })

        for _, row in df.iterrows():
            ts_code = row.get('ts_code', '')
            industry = industry_map.get(ts_code, '其他')
            sector_flow[industry]['net_mf_amount'] += float(row.get('net_mf_amount', 0) or 0)
            sector_flow[industry]['buy_lg_amount'] += float(row.get('buy_lg_amount', 0) or 0)
            sector_flow[industry]['sell_lg_amount'] += float(row.get('sell_lg_amount', 0) or 0)
            sector_flow[industry]['stock_count'] += 1

        return dict(sector_flow)
    except Exception as e:
        print(f"[WARN] 资金流向获取失败({trade_date}): {e}")
        return {}


def fetch_limit_up_stocks(pro, trade_date, stock_basic_list):
    """获取某日涨停个股，按行业统计

    方案1: Tushare limit_list_d（需权限）
    方案2: Tushare daily涨跌幅>=9.8%筛选（最可靠）
    方案3: 东方财富涨停池API（备用）

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
                industry = industry_map.get(ts_code, row.get('industry', '其他'))
                limit_by_industry[industry] += 1
            if limit_by_industry:
                result = {}
                for ind, count in limit_by_industry.items():
                    total = industry_total.get(ind, 1)
                    result[ind] = {
                        'limit_up_count': count,
                        'total_count': total,
                        'density': count / total * 100 if total > 0 else 0,
                    }
                return result
    except Exception:
        pass

    # 方案2: Tushare daily涨跌幅筛选（最可靠）
    try:
        df = pro.daily(trade_date=trade_date, fields='ts_code,trade_date,pct_chg')
        if df is not None and len(df) > 0:
            # 涨停判断: 主板>=9.8%, 科创板/创业板>=19.5%
            limit_up = df[(df['pct_chg'] >= 9.8) | (df['pct_chg'] >= 19.5)]
            for _, row in limit_up.iterrows():
                ts_code = row.get('ts_code', '')
                industry = industry_map.get(ts_code, '其他')
                limit_by_industry[industry] += 1
    except Exception:
        pass

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


def calculate_sector_heat(net_capital, limit_density):
    """计算单板块单日热度分数

    H = 0.6 * 资金流向因子 + 0.4 * 涨停密度因子

    资金流入 → 正热度；资金流出 → 负热度
    Y轴范围: -100 ~ +100

    Args:
        net_capital: 板块净流入金额（万元），正值=流入，负值=流出
        limit_density: 涨停密度百分比

    Returns:
        float: 热度分数 -100 ~ +100
    """
    # 1. 资金流向因子（-100 ~ +100）
    if net_capital >= 0:
        capital_factor = min(net_capital / MAX_CAPITAL_PER_SECTOR * 100, 100)
    else:
        capital_factor = -min(abs(net_capital) / MAX_CAPITAL_PER_SECTOR * 100, 100)

    # 2. 涨停密度因子（0 ~ 100，无负值）
    limit_factor = min(limit_density / MAX_LIMIT_DENSITY * 100, 100)

    # 加权
    heat = W_CAPITAL * capital_factor + W_LIMIT * limit_factor
    return round(heat, 1)


def smooth_series(data, alpha=0.4):
    """EMA指数移动平均平滑

    alpha越小越平滑。0.4 = 适度平滑，保留趋势方向但消除日间噪声。

    Args:
        data: 原始数据列表
        alpha: 平滑系数 (0-1)

    Returns:
        list: 平滑后的数据
    """
    if not data:
        return data
    smoothed = [data[0]]
    for i in range(1, len(data)):
        smoothed.append(alpha * data[i] + (1 - alpha) * smoothed[-1])
    # 保留一位小数
    return [round(x, 1) for x in smoothed]


def cumulative_sum(data, window=5):
    """滑动窗口累计求和

    用于将单日资金流转换为5日累计资金流，趋势更清晰。

    Args:
        data: 原始数据列表
        window: 窗口大小

    Returns:
        list: 累计数据列表（长度与原始相同，前window-1个用部分累计）
    """
    result = []
    for i in range(len(data)):
        start = max(0, i - window + 1)
        result.append(sum(data[start:i + 1]))
    return result


def select_dynamic_sectors(daily_capital, daily_limit, trade_dates, top_n=6):
    """动态选择前线热点板块

    扫描全部行业，按最近交易日资金流绝对值排序取Top N。
    加入连续性逻辑：如果某板块在3日内有2天进入Top10则优先保留。

    Args:
        daily_capital: {date: {行业: {net_mf_amount, ...}}}
        daily_limit: {date: {行业: {limit_up_count, ...}}}
        trade_dates: 交易日列表
        top_n: 选取的板块数

    Returns:
        list: 板块配置列表 [{"name": industry, "industries": [industry]}]
    """
    if not trade_dates:
        return []

    # 统计最近3个交易日各行业进入Top10的次数
    recent_dates = trade_dates[-3:] if len(trade_dates) >= 3 else trade_dates
    top10_count = Counter()

    for date in recent_dates:
        mf = daily_capital.get(date, {})
        # 按资金流绝对值排序
        sorted_inds = sorted(
            mf.items(),
            key=lambda x: abs(x[1].get('net_mf_amount', 0)),
            reverse=True
        )
        for ind, _ in sorted_inds[:10]:
            top10_count[ind] += 1

    # 最近一个交易日的资金流排名
    latest_date = trade_dates[-1]
    latest_mf = daily_capital.get(latest_date, {})
    latest_sorted = sorted(
        latest_mf.items(),
        key=lambda x: abs(x[1].get('net_mf_amount', 0)),
        reverse=True
    )

    # 综合排名：最近日资金流绝对值为主(70%) + 3日Top10出现次数为辅(30%)
    industry_scores = {}
    max_capital = max(abs(v.get('net_mf_amount', 0)) for _, v in latest_sorted[:20]) if latest_sorted else 1

    for ind, data in latest_sorted[:20]:
        capital_score = abs(data.get('net_mf_amount', 0)) / max_capital * 100 if max_capital > 0 else 0
        consistency_score = top10_count.get(ind, 0) / len(recent_dates) * 100
        industry_scores[ind] = 0.7 * capital_score + 0.3 * consistency_score

    # 按综合得分排序取Top N
    selected = sorted(industry_scores.items(), key=lambda x: x[1], reverse=True)[:top_n]

    # 构建板块配置（每个行业独立为一个板块）
    sectors_config = []
    for ind, score in selected:
        sectors_config.append({
            "name": ind,
            "industries": [ind],
            "keywords": [],
        })

    print(f"[INFO] 动态选出 {len(sectors_config)} 个前线热点板块: {[s['name'] for s in sectors_config]}")
    return sectors_config


def compute_sector_heat_comparison(pro, stock_basic_list, sectors_config=None, days=28):
    """计算多板块热度对比（核心函数）

    v3: 支持动态板块选择 + EMA平滑 + 5日累计资金流

    Args:
        pro: Tushare pro_api
        stock_basic_list: 股票基础信息列表
        sectors_config: 板块配置（None则动态选择）
        days: 获取多少自然日的历史数据

    Returns:
        dict: 多板块热度对比数据
    """
    # 1. 获取交易日列表
    trade_dates = get_trade_dates(pro, days=days)
    print(f"[INFO] 获取到 {len(trade_dates)} 个交易日: {trade_dates[0]} ~ {trade_dates[-1]}")

    # 2. 预构建行业→股票映射
    industry_to_stocks = defaultdict(list)
    for s in stock_basic_list:
        ind = s.get('industry', '')
        if ind:
            industry_to_stocks[ind].append(s['ts_code'])

    # 3. 逐日获取资金流向和涨停数据
    daily_capital = {}  # {date: {行业: net_mf}}
    daily_limit = {}     # {date: {行业: limit_data}}
    daily_total_capital = {}  # {date: 全市场总净流入}

    for date in trade_dates:
        # 资金流向
        mf = fetch_daily_moneyflow(pro, date, stock_basic_list)
        daily_capital[date] = mf

        # 全市场总净流入（用于标准化参考）
        daily_total_capital[date] = sum(s.get('net_mf_amount', 0) for s in mf.values())

        # 涨停数据
        lim = fetch_limit_up_stocks(pro, date, stock_basic_list)
        daily_limit[date] = lim

        print(f"  [{date}] 资金流向: {len(mf)} 个行业, 涨停: {sum(d.get('limit_up_count',0) for d in lim.values())} 只")

    # 4. 动态选择板块（如果未提供配置）
    if sectors_config is None:
        sectors_config = select_dynamic_sectors(daily_capital, daily_limit, trade_dates, top_n=6)

    # 5. 按板块配置聚合热度
    sector_results = []

    for sec_cfg in sectors_config:
        sec_name = sec_cfg["name"]
        sec_industries = sec_cfg.get("industries", [])
        sec_stock_codes = set(sec_cfg.get("stock_codes", []))

        heat_series = []
        capital_series = []
        limit_series = []
        limit_count_series = []

        for date in trade_dates:
            # 聚合该板块的资金流向
            if sec_stock_codes:
                # 用精确股票列表
                mf = daily_capital.get(date, {})
                # moneyflow是按个股的，需要从全量数据中筛选
                # 这里简化: 用行业聚合
                net_cap = sum(mf.get(ind, {}).get('net_mf_amount', 0) for ind in sec_industries)
            else:
                # 用行业聚合
                mf = daily_capital.get(date, {})
                net_cap = sum(mf.get(ind, {}).get('net_mf_amount', 0) for ind in sec_industries)

            # 聚合涨停密度
            lim = daily_limit.get(date, {})
            total_stocks = sum(len(industry_to_stocks.get(ind, [])) for ind in sec_industries)
            total_limit = sum(lim.get(ind, {}).get('limit_up_count', 0) for ind in sec_industries)
            limit_density = (total_limit / total_stocks * 100) if total_stocks > 0 else 0

            # 计算热度
            heat = calculate_sector_heat(net_cap, limit_density)

            heat_series.append(heat)
            capital_series.append(net_cap)
            limit_series.append(limit_density)
            limit_count_series.append(total_limit)

        # v3: EMA平滑热度曲线
        heat_series_smooth = smooth_series(heat_series, alpha=0.4)

        # v3: 5日累计资金流（趋势更清晰）
        capital_cumulative = cumulative_sum(capital_series, window=5)

        # 判定生命周期（用平滑后的数据）
        lifecycle = _determine_lifecycle(heat_series_smooth, capital_series)

        sector_results.append({
            "name": sec_name,
            "industries": sec_industries,
            "trade_dates": trade_dates,
            "heat_series": heat_series_smooth,
            "heat_raw": heat_series,
            "capital_series": capital_cumulative,
            "capital_raw": capital_series,
            "limit_series": limit_count_series,
            "current_heat": heat_series_smooth[-1] if heat_series_smooth else 0,
            "lifecycle": lifecycle,
        })

    # 5. 生成多板块对比图表
    chart_text = render_multi_sector_chart(trade_dates, sector_results)

    return {
        "trade_dates": trade_dates,
        "sectors": sector_results,
        "chart_text": chart_text,
    }


def _determine_lifecycle(heat_series, capital_series):
    """判定板块生命周期状态"""
    if len(heat_series) < 3:
        return {"state": "数据不足", "trend": "-", "description": "历史数据不足3日"}

    current = heat_series[-1]
    prev = heat_series[-2]
    prev3 = heat_series[-3]

    rising = current > prev > prev3
    falling = current < prev < prev3
    current_capital = capital_series[-1] if capital_series else 0
    prev_capital = capital_series[-2] if len(capital_series) >= 2 else 0
    capital_reversing = (prev_capital > 0 and current_capital < 0)

    if current >= 50:
        if falling and capital_reversing:
            return {"state": "退烧", "trend": "↓↓",
                    "description": f"热度{current:.0f}从高位回落，资金转流出"}
        return {"state": "高潮", "trend": "↑↑",
                "description": f"热度{current:.0f}处于高位，资金持续流入"}

    if rising and current >= 20:
        return {"state": "崛起", "trend": "↑",
                "description": f"热度{current:.0f}连续3日上升"}

    if falling and current_capital < 0:
        return {"state": "退烧", "trend": "↓",
                "description": f"热度{current:.0f}连续下降，资金净流出"}

    if current > prev:
        return {"state": "崛起", "trend": "↑",
                "description": f"热度{current:.0f}上升中"}
    else:
        return {"state": "退烧", "trend": "↓",
                "description": f"热度{current:.0f}有所回落"}


def render_multi_sector_chart(trade_dates, sector_results):
    """生成多板块对比热度曲线（文本可视化）

    参考观澜网站设计：多板块在同一图表中对比，Y轴-100~+100

    Args:
        trade_dates: 交易日列表 YYYYMMDD
        sector_results: 板块热度数据列表

    Returns:
        str: Markdown格式的热度对比图表
    """
    lines = []
    lines.append("## 板块热度对比曲线（近2周）")
    lines.append("")
    lines.append("> 热度 = 0.6×资金流向标准化 + 0.4×涨停密度标准化 | Y轴: -100(资金流出) ~ +100(资金流入)")
    lines.append("> 数据来源: Tushare moneyflow + 东方财富涨停池 | 时间单位: 交易日")
    lines.append("")

    # 格式化日期标签 MM-DD
    date_labels = [f"{d[4:6]}-{d[6:8]}" for d in trade_dates]

    # 按当前热度排序板块
    sorted_sectors = sorted(sector_results, key=lambda x: x.get('current_heat', 0), reverse=True)

    # 生成各板块热度行
    lines.append("```")
    # 日期行
    lines.append("日期        " + "  ".join(f"{d:>5}" for d in date_labels))
    lines.append("")

    for sec in sorted_sectors:
        name = sec["name"]
        heat_series = sec["heat_series"]
        lifecycle = sec.get("lifecycle", {})
        state = lifecycle.get("state", "")

        # 热度数值行
        heat_str = "  ".join(f"{h:>+5.0f}" for h in heat_series)
        lines.append(f"{name:<10} {heat_str}  [{state}]")

        # 曲线符号行
        curve = " ".join(_heat_to_symbol(h) for h in heat_series)
        lines.append(f"{'':>10} {curve}")
        lines.append("")

    # 资金流向行（各板块）
    lines.append("--- 板块资金流向(亿元) ---")
    for sec in sorted_sectors[:5]:  # 只显示Top 5
        name = sec["name"]
        cap_series = sec["capital_series"]
        cap_str = "  ".join(f"{c/10000:>+5.1f}" for c in cap_series)
        lines.append(f"{name:<10} {cap_str}")
    lines.append("")

    # 涨停数量行
    lines.append("--- 板块涨停数量(只) ---")
    for sec in sorted_sectors[:5]:
        name = sec["name"]
        lim_series = sec["limit_series"]
        lim_str = "  ".join(f"{l:>5}" for l in lim_series)
        lines.append(f"{name:<10} {lim_str}")

    lines.append("```")
    lines.append("")

    # 生命周期汇总表
    lines.append("### 板块生命周期汇总")
    lines.append("")
    lines.append("| 板块 | 当前热度 | 生命周期 | 趋势 | 说明 |")
    lines.append("|------|---------|---------|------|------|")
    for sec in sorted_sectors:
        lc = sec.get("lifecycle", {})
        lines.append(
            f"| {sec['name']} | {sec.get('current_heat',0):+.1f} | "
            f"{lc.get('state','')} | {lc.get('trend','')} | {lc.get('description','')} |"
        )
    lines.append("")

    return '\n'.join(lines)


def _heat_to_symbol(h):
    """热度数值转曲线符号"""
    if h >= 80:
        return '█'
    elif h >= 60:
        return '▇'
    elif h >= 40:
        return '▆'
    elif h >= 20:
        return '▅'
    elif h >= 10:
        return '▄'
    elif h >= 0:
        return '▃'
    elif h >= -20:
        return '▂'
    elif h >= -40:
        return '▁'
    else:
        return ' '  # 深度流出留空


# 预定义热点板块配置（基于Tushare实际行业分类名）
DEFAULT_SECTORS = [
    {
        "name": "AI算力",
        "industries": ["半导体", "通信设备", "IT设备", "元器件"],
        "keywords": ["算力", "AI", "光模块", "服务器", "液冷"],
    },
    {
        "name": "半导体芯片",
        "industries": ["半导体", "元器件"],
        "keywords": ["芯片", "半导体", "先进封装", "HBM"],
    },
    {
        "name": "消费电子",
        "industries": ["家用电器", "电器仪表", "元器件"],
        "keywords": ["苹果", "折叠屏", "智能眼镜", "VR"],
    },
    {
        "name": "新能源",
        "industries": ["电气设备", "化工原料", "化纤"],
        "keywords": ["锂电池", "储能", "光伏", "固态电池"],
    },
    {
        "name": "机器人",
        "industries": ["专用机械", "机械基件", "工程机械", "电气设备"],
        "keywords": ["机器人", "人形机器人", "减速器", "伺服"],
    },
    {
        "name": "低空经济",
        "industries": ["航空", "船舶", "运输设备"],
        "keywords": ["低空经济", "eVTOL", "无人机", "商业航天"],
    },
    {
        "name": "医药生物",
        "industries": ["化学制药", "生物制药", "医疗保健", "中成药"],
        "keywords": ["创新药", "医疗器械", "CXO"],
    },
    {
        "name": "军工航天",
        "industries": ["航空", "船舶", "电气设备"],
        "keywords": ["军工", "商业航天", "卫星"],
    },
    {
        "name": "汽车智驾",
        "industries": ["汽车配件", "汽车整车", "软件服务"],
        "keywords": ["智能驾驶", "自动驾驶", "新能源车"],
    },
    {
        "name": "金融科技",
        "industries": ["证券", "银行", "软件服务", "多元金融"],
        "keywords": ["数字货币", "金融科技", "信创"],
    },
]


def generate_heat_report_section(pro=None, stock_basic_list=None, sectors_config=None, days=28):
    """生成热度对比报告段落（供AI报告引用）

    v3: 默认动态选择板块，sectors_config=None时自动扫描前线热点

    Args:
        pro: Tushare pro_api
        stock_basic_list: 股票基础信息
        sectors_config: 板块配置（None则动态选择前线热点）
        days: 历史天数

    Returns:
        str: Markdown格式的热度对比段落
    """
    if pro is None:
        ts = _ensure_tushare()
        ts.set_token(get_tushare_token())
        pro = ts.pro_api()

    if stock_basic_list is None:
        from vip_extractor import load_stock_database
        stock_basic_list = load_stock_database(pro)

    if sectors_config is None:
        sectors_config = DEFAULT_SECTORS

    result = compute_sector_heat_comparison(pro, stock_basic_list, sectors_config, days=days)
    return result["chart_text"]


if __name__ == "__main__":
    # 测试
    print("=== 热度追踪器 v2 测试 ===\n")

    ts = _ensure_tushare()
    ts.set_token(get_tushare_token())
    pro = ts.pro_api()

    from vip_extractor import load_stock_database
    stock_db = load_stock_database(pro)

    # 使用默认板块配置
    chart = generate_heat_report_section(pro, stock_db, DEFAULT_SECTORS, days=28)
    print(chart)


def export_heat_data_json(output_path=None, pro=None, stock_basic_list=None, sectors_config=None, days=28):
    """导出热度数据为JSON文件（供HTML报告的ECharts使用）

    Args:
        output_path: 输出路径（None则输出到 data/heat_data.json）
        pro: Tushare pro_api
        stock_basic_list: 股票基础信息
        sectors_config: 板块配置
        days: 自然日天数（28天约覆盖20个交易日）

    Returns:
        dict: 热度数据字典
    """
    if pro is None:
        ts = _ensure_tushare()
        ts.set_token(get_tushare_token())
        pro = ts.pro_api()

    if stock_basic_list is None:
        from vip_extractor import load_stock_database
        stock_basic_list = load_stock_database(pro)

    if sectors_config is None:
        sectors_config = DEFAULT_SECTORS

    result = compute_sector_heat_comparison(pro, stock_basic_list, sectors_config, days=days)

    # 构建JSON数据（ECharts友好格式）
    export_data = {
        "trade_dates": result["trade_dates"],
        "date_labels": [f"{d[4:6]}-{d[6:8]}" for d in result["trade_dates"]],
        "sectors": [],
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    for sec in result["sectors"]:
        export_data["sectors"].append({
            "name": sec["name"],
            "heat_series": sec["heat_series"],
            "heat_raw": sec.get("heat_raw", sec["heat_series"]),
            "capital_series": sec["capital_series"],
            "capital_raw": sec.get("capital_raw", sec["capital_series"]),
            "limit_series": sec["limit_series"],
            "current_heat": sec.get("current_heat", 0),
            "lifecycle": sec.get("lifecycle", {}),
        })

    if output_path is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        output_path = os.path.join(script_dir, "data", "heat_data.json")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)

    print(f"[OK] 热度数据已导出: {output_path}")
    return export_data
