#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
外部学习器 (External Learner)
==============================
向"外部世界"学习，补充内部验证的不足：

1. 盘后复盘：涨停板→逆推主线→与预判对比→偏差分析
2. 信号盲区：次日涨停→前日是否提及→补入信号库
3. 外部观点：fxbaogao研报→机构逻辑→与系统对比
4. 模式发现：聚类成功/失败案例→组合因子模式

集成方式：
  from evolution.external_learner import run as run_external_learning
  # 在进化引擎诊断后调用
  external_lessons = run_external_learning(db, date_str)
"""

import json
import os
import re
import sys
import datetime
from collections import defaultdict, Counter
from typing import List, Dict, Any, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPT_DIR)

KNOWLEDGE_DIR = os.path.join(SCRIPT_DIR, "knowledge")
EXTERNAL_LESSONS_PATH = os.path.join(KNOWLEDGE_DIR, "external_lessons.md")


# ---------------------------------------------------------------------------
# 常量：因子识别 / 行业关键词 / 机构逻辑关键词
# ---------------------------------------------------------------------------

# 因子关键词 → 英文因子键（与 knowledge/factor_weights.json 对齐）
# 顺序敏感：先命中先归类（与 learning_loop.FACTOR_RULES 保持一致）
FACTOR_KEYWORDS: List[Tuple[str, List[str]]] = [
    ("north_money", ["北向", "外资"]),
    ("us_market", ["美股", "标普", "纳斯达克", "道琼斯"]),
    ("hk_market", ["恒生", "港股"]),
    ("gold_price", ["黄金"]),
    ("oil_price", ["原油", "石油"]),
    ("usd_cny", ["美元", "人民币"]),
    ("sector_moneyflow", ["板块资金", "净流入", "净流出"]),
    ("dragon_tiger", ["龙虎榜", "净买入"]),
    ("limit_up", ["涨停"]),
    ("margin", ["融资余额", "杠杆资金"]),
    ("cls_telegraph", ["舆情", "电报"]),
]

# A股行业/主题关键词（用于从洞见文本中逆推系统预判的主线）
SECTOR_KEYWORDS: List[str] = [
    "半导体", "芯片", "封测", "光刻", "存储芯片", "MCU", "功率半导体",
    "HBM", "先进封装",
    "AI算力", "算力", "AIDC", "IDC", "服务器", "GPU", "NPU",
    "光通信", "光模块", "光纤", "CPO", "硅光", "光缆",
    "PCB", "覆铜板", "铜箔", "MLCC", "电容", "电阻",
    "新能源", "锂电池", "磷酸铁锂", "固态电池", "光伏", "储能",
    "充电桩", "氢能", "钠离子电池",
    "机器人", "人形机器人", "减速器",
    "军工", "航天", "商业航天", "卫星", "低空经济", "无人机", "eVTOL",
    "新材料", "粉体", "粉体材料", "3D打印", "导热", "散热",
    "黄金", "有色", "稀土", "锂",
    "医药", "生物", "创新药", "医疗器械", "CXO",
    "白酒", "食品饮料", "消费",
    "银行", "证券", "保险", "金融",
    "房地产", "建材", "钢铁",
    "煤炭", "石化", "化工",
    "汽车", "零部件", "智能驾驶",
    "传媒", "游戏", "教育", "电商",
    "电力", "电网", "环保",
]

# 机构研报核心逻辑关键词（用于对齐外部观点）
LOGIC_KEYWORDS: List[str] = [
    "国产替代", "国产化", "进口替代", "自主可控",
    "需求爆发", "需求拉动", "需求回暖", "需求向好",
    "产能扩张", "产能释放", "产能爬坡", "量产", "达产", "扩产",
    "技术突破", "技术迭代", "产品升级", "产品突破",
    "客户验证", "送样", "认证", "审核",
    "切入供应链", "进入供应链", "打入供应链", "供货", "定点",
    "订单增长", "订单放量", "中标", "签订合同",
    "业绩拐点", "业绩反转", "高增长", "超预期", "创新高",
    "政策利好", "政策支持", "补贴", "扶持", "规划",
    "行业景气", "高景气", "量价齐升", "供需改善",
    "供不应求", "紧缺", "缺口", "涨价",
    "渗透率提升", "市占率提升", "份额提升",
    "出海", "海外扩张", "国际化",
    "3D打印", "3D打印粉体", "金属粉体",
    "AI需求", "算力需求", "数据中心需求",
]


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _safe_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    """安全转 float：None / 空串 / 非数字均返回 default。"""
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (ValueError, TypeError):
        return default


def _prev_date(date_str: str) -> Optional[str]:
    """返回 date_str 前一天（自然日）的日期字符串 YYYY-MM-DD。"""
    try:
        d = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        return (d - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _re_clean(text: Any) -> str:
    """去除研报文本中的 <em></em> 高亮标签并合并空白。"""
    if not text:
        return ""
    cleaned = re.sub(r"</?em>", "", str(text))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _classify_factor(text: str) -> Optional[str]:
    """根据预判文本归类影响因子（首个命中），返回英文因子键。

    与 learning_loop.classify_factor 逻辑一致，但返回与
    knowledge/factor_weights.json 对齐的英文键。首个命中即返回，
    避免"北向资金净流入"同时误判为 sector_moneyflow。
    """
    if not text:
        return None
    for factor_key, kws in FACTOR_KEYWORDS:
        if any(kw in text for kw in kws):
            return factor_key
    return None


def _query_market_insights(db: Any, date_str: str) -> List[dict]:
    """直接通过 db._conn() 查询指定日期的 market_insight 记录。

    失败时降级到 db.query_insights()，确保主流程不被中断。
    """
    try:
        conn = db._conn()
        cur = conn.execute(
            "SELECT * FROM market_insight WHERE date=? ORDER BY id",
            (date_str,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        print(f"  [警告] 直接查询 market_insight 失败({date_str}): {e}")
        try:
            return db.query_insights(date=date_str)
        except Exception as e2:
            print(f"  [警告] 降级查询 insights 也失败: {e2}")
            return []


def _query_learning_records_by_date(db: Any, start_date: str,
                                    end_date: str,
                                    fallback_limit: int = 200) -> List[dict]:
    """按日期窗口查询 learning_record，失败时降级取最近 N 条。"""
    try:
        conn = db._conn()
        cur = conn.execute(
            "SELECT * FROM learning_record WHERE date>=? AND date<=? "
            "ORDER BY id DESC",
            (start_date, end_date),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        print(f"  [模式] 按日期查询 learning_record 失败: {e}")
        try:
            return db.query_learning_records(limit=fallback_limit)
        except Exception:
            return []


def _extract_industry_from_limit_up(limit_up_items: List[dict]) -> Dict[str, List[dict]]:
    """从涨停列表中按行业聚类统计。

    Args:
        limit_up_items: db.query_limit_up() 返回的涨停股列表，
            每条含 ts_code / name / industry / pct_chg / amount 等字段。

    Returns:
        dict: {行业名: [该行业的涨停股票信息dict, ...]}，
        industry 为空的归入 "未分类"。
    """
    clusters: Dict[str, List[dict]] = defaultdict(list)
    for item in limit_up_items:
        industry = (item.get("industry") or "").strip() or "未分类"
        clusters[industry].append(item)
    return dict(clusters)


def _check_stock_in_insights(stock_name: str, stock_code: str,
                             insights: List[dict]) -> bool:
    """检查股票是否在洞见文本中被提及。

    遍历每条洞见的 a_share_impact 与 signal_text，若股票名称或代码
    作为子串出现，即视为被提及。

    Args:
        stock_name: 股票名称（如 "有研粉材"）
        stock_code: 股票代码（如 "688456" 或 "688456.SH"）
        insights: market_insight 记录列表

    Returns:
        bool: True=被提及, False=未被提及（信号盲区）
    """
    name = (stock_name or "").strip()
    code = (stock_code or "").strip()
    # 去掉代码后缀，保留纯数字用于匹配
    code_digits = code.split(".")[0] if code else ""
    targets = [t for t in (name, code, code_digits) if t]
    if not targets:
        return False

    for ins in insights:
        text = " ".join([
            ins.get("a_share_impact") or "",
            ins.get("signal_text") or "",
        ])
        if not text:
            continue
        for t in targets:
            if t in text:
                return True
    return False


def _stock_in_telegraphs(stock_name: str, stock_code: str,
                         telegraphs: List[dict]) -> bool:
    """检查股票是否在电报标题/正文/关联股票中被提及。"""
    name = (stock_name or "").strip()
    code = (stock_code or "").strip()
    code_digits = code.split(".")[0] if code else ""
    targets = [t for t in (name, code, code_digits) if t]
    if not targets:
        return False
    for t in telegraphs:
        text = " ".join([
            t.get("title") or "",
            t.get("content") or "",
        ])
        for tgt in targets:
            if tgt in text:
                return True
        # 关联股票列表
        for s in (t.get("stocks") or []):
            if name and name == s:
                return True
    return False


def _extract_predicted_sectors(insights: List[dict]) -> List[str]:
    """从洞见文本中逆推系统预判的板块/主线。"""
    sectors: List[str] = []
    seen = set()
    for ins in insights:
        text = " ".join([
            ins.get("a_share_impact") or "",
            ins.get("signal_text") or "",
        ])
        if not text:
            continue
        for kw in SECTOR_KEYWORDS:
            if kw in text and kw not in seen:
                seen.add(kw)
                sectors.append(kw)
    return sectors


def _sector_match(actual_industry: str, predicted_sectors: List[str]) -> bool:
    """判断实际主线行业是否与预判板块匹配（子串模糊匹配）。"""
    if not actual_industry or actual_industry == "N/A":
        return False
    for sec in predicted_sectors:
        if sec in actual_industry or actual_industry in sec:
            return True
    return False


def _build_deviation(actual_mainline: dict, predicted_sectors: List[str],
                     match: bool) -> str:
    """构建偏差描述文本。"""
    ind = actual_mainline.get("industry", "N/A")
    cnt = actual_mainline.get("limit_up_count", 0)
    if match:
        return f"系统预判与实际主线一致（{ind}，涨停{cnt}只），方向命中"
    if predicted_sectors:
        return (f"系统预判{'+'.join(predicted_sectors[:3])}，"
                f"实际主线为{ind}（涨停{cnt}只），方向偏差")
    return f"系统未给出明确主线预判，实际主线为{ind}（涨停{cnt}只）"


def _build_missed_sectors(clusters: Dict[str, List[dict]],
                          predicted_sectors: List[str]) -> List[str]:
    """找出实际涨停较多但系统未预判的板块（遗漏板块）。"""
    missed: List[str] = []
    ranked = sorted(clusters.items(), key=lambda kv: len(kv[1]), reverse=True)
    for industry, stocks in ranked[:5]:
        if not _sector_match(industry, predicted_sectors):
            missed.append(f"{industry}({len(stocks)}只)")
    return missed


def _extract_logic_keywords(text: str) -> List[str]:
    """从研报文本中提取核心逻辑关键词。"""
    if not text:
        return []
    found: List[str] = []
    seen = set()
    for kw in LOGIC_KEYWORDS:
        if kw in text and kw not in seen:
            seen.add(kw)
            found.append(kw)
    return found


def _safe_search_fxbaogao(company_name: str) -> List[dict]:
    """安全调用 fxbaogao 研报搜索，失败返回空列表。"""
    if not company_name:
        return []
    try:
        from vip_search_v4 import search_fxbaogao_reports
    except Exception as e:
        print(f"  [外部] 导入 search_fxbaogao_reports 失败: {e}")
        return []
    try:
        reports = search_fxbaogao_reports(company_name)
        return reports if isinstance(reports, list) else []
    except Exception as e:
        print(f"  [外部] fxbaogao 搜索失败({company_name}): {e}")
        return []


def _system_logic_for_stock(rep: dict, insights: List[dict]) -> str:
    """提取系统对该股票/行业的逻辑描述。"""
    name = rep.get("name", "")
    industry = rep.get("industry", "")
    # 优先找提及该股的洞见
    for ins in insights:
        text = " ".join([
            ins.get("a_share_impact") or "",
            ins.get("signal_text") or "",
        ])
        if name and name in text:
            return (ins.get("a_share_impact") or ins.get("signal_text") or "")[:60]
    # 其次找提及该行业的洞见
    for ins in insights:
        text = " ".join([
            ins.get("a_share_impact") or "",
            ins.get("signal_text") or "",
        ])
        if industry and industry in text:
            return (ins.get("a_share_impact") or ins.get("signal_text") or "")[:60]
    return "系统当日未提及该股/行业"


def _combination_recommendation(factors: List[str], rate: float) -> str:
    """根据组合命中率给出使用建议。"""
    n = len(factors)
    if n >= 3 and rate >= 0.6:
        return f"{n}因子共振时高置信度，可提升组合权重"
    if rate >= 0.6:
        return "组合命中率较高，可重点参考"
    if rate >= 0.5:
        return "组合命中率中等，需结合其他信号确认"
    if rate <= 0.2:
        return "组合命中率低，建议降权或反向参考"
    return "组合命中率偏低，谨慎使用"


# ---------------------------------------------------------------------------
# 1. 盘后复盘
# ---------------------------------------------------------------------------

def post_market_review(db: Any, date_str: str) -> dict:
    """盘后复盘：读取当日涨停板，逆推主线叙事，与系统预判对比。

    流程：
      1. db.query_limit_up(date=date_str) 获取当日涨停股
      2. 按行业聚类，涨停最多的行业即当日实际主线
      3. 读取 market_insight 当日记录（无则回溯前一交易日）
      4. 从洞见 a_share_impact 文本逆推系统预判的主线
      5. 对比实际主线 vs 预判主线，输出偏差与遗漏板块

    Args:
        db: DB 实例
        date_str: 复盘日期 YYYY-MM-DD

    Returns:
        dict: 包含 actual_mainline / predicted_mainline /
              deviation / missed_sectors 四个字段
    """
    # 1. 涨停板
    try:
        limit_up_items = db.query_limit_up(date=date_str)
    except Exception as e:
        print(f"  [复盘] 读取涨停板失败: {e}")
        limit_up_items = []

    clusters = _extract_industry_from_limit_up(limit_up_items)

    # 2. 实际主线（涨停最多的行业）
    actual_mainline: Dict[str, Any] = {
        "industry": "N/A", "limit_up_count": 0, "top_stocks": []
    }
    if clusters:
        mainline_industry = max(clusters, key=lambda k: len(clusters[k]))
        mainline_stocks = clusters[mainline_industry]
        actual_mainline = {
            "industry": mainline_industry,
            "limit_up_count": len(mainline_stocks),
            "top_stocks": [s.get("ts_code", "") for s in mainline_stocks[:10]],
        }

    # 3. 系统洞见（当日，回溯前一交易日）
    insights = _query_market_insights(db, date_str)
    if not insights:
        prev_date = _prev_date(date_str)
        for _ in range(10):
            if not prev_date:
                break
            insights = _query_market_insights(db, prev_date)
            if insights:
                print(f"  [复盘] 当日无洞见，回溯至 {prev_date}")
                break
            prev_date = _prev_date(prev_date)

    # 4. 逆推预判主线
    predicted_sectors = _extract_predicted_sectors(insights)
    match = _sector_match(actual_mainline["industry"], predicted_sectors) if clusters else False
    predicted_mainline = {
        "from_insights": predicted_sectors,
        "match": match,
    }

    # 5. 偏差 & 遗漏板块
    deviation = _build_deviation(actual_mainline, predicted_sectors, match)
    missed_sectors = _build_missed_sectors(clusters, predicted_sectors)

    return {
        "actual_mainline": actual_mainline,
        "predicted_mainline": predicted_mainline,
        "deviation": deviation,
        "missed_sectors": missed_sectors,
    }


# ---------------------------------------------------------------------------
# 2. 信号盲区扫描
# ---------------------------------------------------------------------------

def scan_signal_blind_spots(db: Any, date_str: str) -> dict:
    """信号盲区扫描：检查当日涨停股是否在前日洞见中被提及。

    流程：
      1. 读取 date_str 的涨停股（limit_up）
      2. 读取 date_str 前一天（datetime 减 1 天）的 market_insight
      3. 检查每只涨停股名称/代码是否出现在前日洞见 a_share_impact 文本中
      4. 未被提及即为信号盲区
      5. 对盲区股票，尝试从 db.query_telegraphs() 搜索相关电报
      6. 汇总盲区行业分布

    Args:
        db: DB 实例
        date_str: 扫描日期 YYYY-MM-DD

    Returns:
        dict: 包含 blind_spot_stocks / total_limit_up / missed_count /
              miss_rate / missed_industries 五个字段
    """
    # 1. 当日涨停
    try:
        limit_up_items = db.query_limit_up(date=date_str)
    except Exception as e:
        print(f"  [盲区] 读取涨停板失败: {e}")
        limit_up_items = []

    total_limit_up = len(limit_up_items)

    # 2. 前日洞见
    prev_date = _prev_date(date_str)
    prev_insights: List[dict] = []
    if prev_date:
        prev_insights = _query_market_insights(db, prev_date)

    # 3. 电报池（当日 + 前日，用于盲区补搜）
    telegraph_pool: List[dict] = []
    for d in (date_str, prev_date):
        if not d:
            continue
        try:
            telegraph_pool.extend(db.query_telegraphs(date=d, limit=300))
        except Exception as e:
            print(f"  [盲区] 读取电报失败({d}): {e}")

    # 4. 逐只检查是否被前日洞见提及
    blind_spot_stocks: List[dict] = []
    missed_industries: Dict[str, int] = defaultdict(int)
    for item in limit_up_items:
        name = item.get("name", "")
        code = item.get("ts_code", "")
        industry = (item.get("industry") or "").strip() or "未分类"
        mentioned = _check_stock_in_insights(name, code, prev_insights)
        if mentioned:
            continue
        # 盲区：补搜电报
        had_telegraph = _stock_in_telegraphs(name, code, telegraph_pool)
        blind_spot_stocks.append({
            "ts_code": code,
            "name": name,
            "industry": industry,
            "had_telegraph": had_telegraph,
        })
        missed_industries[industry] += 1

    missed_count = len(blind_spot_stocks)
    miss_rate = round(missed_count / total_limit_up, 2) if total_limit_up else 0.0

    return {
        "blind_spot_stocks": blind_spot_stocks,
        "total_limit_up": total_limit_up,
        "missed_count": missed_count,
        "miss_rate": miss_rate,
        "missed_industries": dict(missed_industries),
    }


# ---------------------------------------------------------------------------
# 3. 外部观点对齐
# ---------------------------------------------------------------------------

def align_external_views(db: Any, date_str: str) -> dict:
    """外部观点对齐：搜索当日热门研报，提取机构核心逻辑。

    流程：
      1. 读取当日涨停板，取前 3 个行业的代表性股票（各行业涨幅最高一只）
      2. 对每只股票调用 vip_search_v4.search_fxbaogao_reports
      3. 从研报标题/正文提取核心逻辑关键词
      4. 与系统当日洞见对比，找出逻辑缺口与可补入的新信号

    注意：fxbaogao 调用可能因 API 限制失败，全程 try/except 隔离，
    单只股票失败不影响其它股票与整体流程。

    Args:
        db: DB 实例
        date_str: 对齐日期 YYYY-MM-DD

    Returns:
        dict: 包含 external_logics / logic_gaps / new_signals_to_add 三个字段
    """
    # 1. 涨停板 + 代表性股票
    try:
        limit_up_items = db.query_limit_up(date=date_str)
    except Exception as e:
        print(f"  [外部] 读取涨停板失败: {e}")
        limit_up_items = []

    clusters = _extract_industry_from_limit_up(limit_up_items)
    ranked_industries = sorted(
        clusters.items(), key=lambda kv: len(kv[1]), reverse=True
    )[:3]

    # 每个行业取涨幅最高的一只（query_limit_up 已按 pct_chg DESC 排序）
    rep_stocks: List[dict] = []
    for industry, stocks in ranked_industries:
        if not stocks:
            continue
        rep = stocks[0]
        rep_stocks.append({
            "name": rep.get("name", ""),
            "ts_code": rep.get("ts_code", ""),
            "industry": industry,
        })

    # 2. 系统当日洞见（用于对比）
    system_insights = _query_market_insights(db, date_str)
    system_text = " ".join([
        " ".join([
            (i.get("a_share_impact") or ""),
            (i.get("signal_text") or ""),
        ]) for i in system_insights
    ])

    # 3. 逐只搜索研报并提取逻辑
    external_logics: List[dict] = []
    all_external_signals: List[str] = []
    for rep in rep_stocks:
        reports = _safe_search_fxbaogao(rep["name"])
        if not reports:
            continue
        # 取首篇作为代表
        first = reports[0]
        para_texts: List[str] = []
        for p in (first.get("paragraphs") or []):
            if isinstance(p, dict):
                para_texts.append(_re_clean(p.get("content", "")))
            elif isinstance(p, str):
                para_texts.append(_re_clean(p))
        report_text = " ".join(
            [_re_clean(first.get("title", "")), _re_clean(first.get("content", ""))]
            + para_texts
        )
        core_logic = _extract_logic_keywords(report_text)
        system_logic = _system_logic_for_stock(rep, system_insights)
        external_logics.append({
            "stock": rep["name"],
            "source": first.get("org") or "fxbaogao",
            "core_logic": "、".join(core_logic) if core_logic else "未提取到明确逻辑",
            "system_logic": system_logic,
        })
        all_external_signals.extend(core_logic)

    # 4. 逻辑缺口 & 新信号
    new_signals_to_add: List[str] = []
    seen = set()
    for sig in all_external_signals:
        if sig not in system_text and sig not in seen:
            seen.add(sig)
            new_signals_to_add.append(sig)

    logic_gaps: List[str] = []
    if not external_logics:
        logic_gaps.append("当日未获取到有效外部研报（fxbaogao 调用受限或无涨停数据）")
    else:
        for sig in new_signals_to_add:
            logic_gaps.append(f"系统未提及'{sig}'逻辑")
        # 系统未覆盖的赛道（外部提及但系统当日洞见未涉及的板块）
        covered_industries = set()
        for ins in system_insights:
            text = " ".join([
                ins.get("a_share_impact") or "",
                ins.get("signal_text") or "",
            ])
            for rep in rep_stocks:
                if rep["industry"] and rep["industry"] in text:
                    covered_industries.add(rep["industry"])
        for rep in rep_stocks:
            ind = rep["industry"]
            if ind and ind not in covered_industries:
                logic_gaps.append(f"系统未覆盖'{ind}'赛道")

    return {
        "external_logics": external_logics,
        "logic_gaps": logic_gaps,
        "new_signals_to_add": new_signals_to_add,
    }


# ---------------------------------------------------------------------------
# 4. 模式发现
# ---------------------------------------------------------------------------

def discover_patterns(db: Any, lookback_days: int = 30) -> dict:
    """模式发现：聚类历史成功/失败案例，发现组合因子模式。

    流程：
      1. 读取最近 lookback_days 天的 learning_record（仅 category="盘后验证"）
      2. 按命中/未命中分组（gap_analysis 含"命中"且不含"反向"→命中；
         含"反向"/"失误"→未命中；"未兑现"不计入方向性样本）
      3. 按交易日聚合：同一天触发的因子构成"组合因子"；
         该日方向命中需"命中数 > 未命中数"（严格多数，平局不计为命中），
         全为"未兑现"的震荡日不计入样本
      4. 统计各组合的命中率（样本量 ≥ 3 才纳入）
      5. 输出成功组合（命中率 ≥ 0.5）与失败组合（命中率 < 0.5）

    组合因子语义：当多个单因子在同一个交易日同时触发（多个维度的
    洞见同时存在），即构成"共振"组合，与 knowledge/factor_weights.json
    的 combination_factors 概念一致。

    Args:
        db: DB 实例
        lookback_days: 回溯天数，默认 30

    Returns:
        dict: 包含 successful_combinations / failing_combinations /
              sample_size 三个字段
    """
    # 1. 读取 learning_record（按日期窗口）
    end_date = datetime.datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.datetime.now()
                  - datetime.timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    records = _query_learning_records_by_date(db, start_date, end_date)

    # 仅分析盘后验证记录（排除历史经验总结，避免元归纳噪声）
    verify_records = [r for r in records if r.get("category") == "盘后验证"]
    sample_size = len(verify_records)

    # 2. 按交易日聚合，判定每日方向是否命中
    by_date: Dict[str, List[dict]] = defaultdict(list)
    for r in verify_records:
        by_date[r.get("date", "")].append(r)

    # combo_stats: frozenset(factors) -> {hits, total}
    combo_stats: Dict[frozenset, Dict[str, int]] = defaultdict(
        lambda: {"hits": 0, "total": 0}
    )
    for date, recs in by_date.items():
        if not date:
            continue
        factors = set()
        day_hits = 0
        day_misses = 0
        for r in recs:
            factor = _classify_factor(r.get("prediction", "") or "")
            if factor:
                factors.add(factor)
            gap = r.get("gap_analysis", "") or ""
            if "命中" in gap and "反向" not in gap:
                day_hits += 1
            elif "反向" in gap or "失误" in gap:
                day_misses += 1
        if not factors:
            continue
        # 仅当存在方向性记录时才计入样本（全为"未兑现"的震荡日不计）
        if day_hits == 0 and day_misses == 0:
            continue
        # 严格多数：命中数 > 未命中数 才算该日命中（平局不计为命中，更保守）
        day_hit = day_hits > day_misses
        key = frozenset(factors)
        combo_stats[key]["total"] += 1
        if day_hit:
            combo_stats[key]["hits"] += 1

    # 3. 计算组合命中率
    successful: List[dict] = []
    failing: List[dict] = []
    for key, st in combo_stats.items():
        if st["total"] < 3:
            continue  # 样本量不足，不轻易下结论
        rate = round(st["hits"] / st["total"], 2)
        factors_list = sorted(key)
        entry = {
            "factors": factors_list,
            "hits": st["hits"],
            "total": st["total"],
            "rate": rate,
            "recommendation": _combination_recommendation(factors_list, rate),
        }
        if rate >= 0.5:
            successful.append(entry)
        else:
            failing.append(entry)

    successful.sort(key=lambda x: (x["rate"], x["total"]), reverse=True)
    failing.sort(key=lambda x: (x["rate"], x["total"]))

    return {
        "successful_combinations": successful,
        "failing_combinations": failing,
        "sample_size": sample_size,
    }


# ---------------------------------------------------------------------------
# 5. 成果固化
# ---------------------------------------------------------------------------

def _format_lessons_to_md(lessons_dict: dict) -> str:
    """将外部学习成果格式化为 markdown 文本。

    Args:
        lessons_dict: run() 返回的 results，并附带 _date 字段

    Returns:
        str: 可直接追加写入 external_lessons.md 的 markdown 文本
    """
    lines: List[str] = []
    date_str = lessons_dict.get("_date", "")
    lines.append(f"## {date_str} 外部学习成果")
    lines.append("")
    lines.append(
        f"> 生成时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    lines.append("")

    # 1. 盘后复盘
    review = lessons_dict.get("review", {}) or {}
    lines.append("### 1. 盘后复盘")
    if "error" in review:
        lines.append(f"- 执行失败: {review['error']}")
    else:
        am = review.get("actual_mainline", {}) or {}
        pm = review.get("predicted_mainline", {}) or {}
        lines.append(
            f"- 实际主线: {am.get('industry', 'N/A')}"
            f"（涨停{am.get('limit_up_count', 0)}只）"
        )
        pred_str = ", ".join(pm.get("from_insights", [])) or "无"
        lines.append(
            f"- 预判主线: {pred_str}（匹配: {pm.get('match', False)}）"
        )
        lines.append(f"- 偏差分析: {review.get('deviation', 'N/A')}")
        missed = review.get("missed_sectors", [])
        if missed:
            lines.append(f"- 遗漏板块: {', '.join(missed)}")
    lines.append("")

    # 2. 信号盲区
    bs = lessons_dict.get("blind_spots", {}) or {}
    lines.append("### 2. 信号盲区")
    if "error" in bs:
        lines.append(f"- 执行失败: {bs['error']}")
    else:
        lines.append(
            f"- 涨停{bs.get('total_limit_up', 0)}只，"
            f"遗漏{bs.get('missed_count', 0)}只，"
            f"遗漏率{bs.get('miss_rate', 0):.0%}"
        )
        missed_ind = bs.get("missed_industries", {}) or {}
        if missed_ind:
            ind_str = "、".join(
                f"{k}({v}只)" for k, v in
                sorted(missed_ind.items(), key=lambda x: -x[1])[:5]
            )
            lines.append(f"- 遗漏行业分布: {ind_str}")
        blind = bs.get("blind_spot_stocks", []) or []
        if blind:
            sample = "、".join(
                f"{s.get('name', '')}"
                f"({'有电报' if s.get('had_telegraph') else '无电报'})"
                for s in blind[:5]
            )
            lines.append(f"- 盲区样本: {sample}")
    lines.append("")

    # 3. 外部观点
    ev = lessons_dict.get("external_views", {}) or {}
    lines.append("### 3. 外部观点对齐")
    if "error" in ev:
        lines.append(f"- 执行失败: {ev['error']}")
    else:
        for el in ev.get("external_logics", []) or []:
            lines.append(
                f"- {el.get('stock', '')}: "
                f"机构({el.get('source', '')})逻辑「{el.get('core_logic', '')}」"
                f" vs 系统「{el.get('system_logic', '')}」"
            )
        for gap in ev.get("logic_gaps", []) or []:
            lines.append(f"- 逻辑缺口: {gap}")
        new_sigs = ev.get("new_signals_to_add", []) or []
        if new_sigs:
            lines.append(f"- 建议补入信号: {', '.join(new_sigs)}")
    lines.append("")

    # 4. 模式发现
    pt = lessons_dict.get("patterns", {}) or {}
    lines.append("### 4. 组合因子模式")
    if "error" in pt:
        lines.append(f"- 执行失败: {pt['error']}")
    else:
        lines.append(f"- 样本量: {pt.get('sample_size', 0)} 条盘后验证记录")
        for sc in pt.get("successful_combinations", []) or []:
            lines.append(
                f"- [成功] {'+'.join(sc['factors'])}: "
                f"命中{sc['hits']}/{sc['total']}（{sc['rate']:.0%}）— "
                f"{sc['recommendation']}"
            )
        for fc in pt.get("failing_combinations", []) or []:
            lines.append(
                f"- [失败] {'+'.join(fc['factors'])}: "
                f"命中{fc['hits']}/{fc['total']}（{fc['rate']:.0%}）— "
                f"{fc['recommendation']}"
            )
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _persist_lessons(results: dict, date_str: str) -> None:
    """将外部学习成果追加写入 knowledge/external_lessons.md。

    Args:
        results: run() 返回的结果 dict
        date_str: 学习日期 YYYY-MM-DD
    """
    try:
        os.makedirs(KNOWLEDGE_DIR, exist_ok=True)
        payload = dict(results)
        payload["_date"] = date_str
        md = _format_lessons_to_md(payload)
        with open(EXTERNAL_LESSONS_PATH, "a", encoding="utf-8") as f:
            f.write(md)
        print(f"  [固化] 外部学习成果已写入 {EXTERNAL_LESSONS_PATH}")
    except Exception as e:
        print(f"  [固化] 写入 external_lessons.md 失败: {e}")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def run(db: Any, date_str: Optional[str] = None) -> dict:
    """外部学习器主入口。

    顺序执行盘后复盘、信号盲区、外部观点对齐、模式发现四项任务，
    并将成果固化到 knowledge/external_lessons.md。每项任务独立 try/except
    隔离，单项失败不影响其它任务与整体流程。

    Args:
        db: DB 实例
        date_str: 学习日期 YYYY-MM-DD，默认今天

    Returns:
        dict: 包含 review / blind_spots / external_views / patterns 四个字段
    """
    if date_str is None:
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")

    print("=" * 60)
    print(f"外部学习器 启动 @ {date_str}")
    print("=" * 60)

    results: Dict[str, Any] = {}

    # 1. 盘后复盘
    try:
        review = post_market_review(db, date_str)
        results["review"] = review
        print(f"  [复盘] 实际主线: {review.get('actual_mainline', {}).get('industry', 'N/A')}")
    except Exception as e:
        print(f"  [复盘] 失败: {e}")
        results["review"] = {"error": str(e)}

    # 2. 信号盲区
    try:
        blind_spots = scan_signal_blind_spots(db, date_str)
        results["blind_spots"] = blind_spots
        print(f"  [盲区] 涨停{blind_spots.get('total_limit_up', 0)}只, 遗漏{blind_spots.get('missed_count', 0)}只")
    except Exception as e:
        print(f"  [盲区] 失败: {e}")
        results["blind_spots"] = {"error": str(e)}

    # 3. 外部观点对齐
    try:
        external = align_external_views(db, date_str)
        results["external_views"] = external
        print(f"  [外部] 新信号: {len(external.get('new_signals_to_add', []))}个")
    except Exception as e:
        print(f"  [外部] 失败: {e}")
        results["external_views"] = {"error": str(e)}

    # 4. 模式发现
    try:
        patterns = discover_patterns(db)
        results["patterns"] = patterns
        print(f"  [模式] 成功组合: {len(patterns.get('successful_combinations', []))}个")
    except Exception as e:
        print(f"  [模式] 失败: {e}")
        results["patterns"] = {"error": str(e)}

    # 5. 固化到 external_lessons.md
    _persist_lessons(results, date_str)

    print("=" * 60)
    return results


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

def main() -> None:
    """命令行入口：python external_learner.py [--date YYYY-MM-DD]"""
    import argparse

    parser = argparse.ArgumentParser(description="外部学习器（盘后复盘/盲区/外部观点/模式发现）")
    parser.add_argument(
        "--date", default=None,
        help="学习日期 YYYY-MM-DD，默认今天",
    )
    args = parser.parse_args()

    from db import DB
    db = DB()
    db.init()
    run(db, args.date)


if __name__ == "__main__":
    main()
