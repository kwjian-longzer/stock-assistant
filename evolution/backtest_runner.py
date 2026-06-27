#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回测框架 (Backtest Runner)
============================
在历史数据上验证进化假设：
  1. 读取过去N天的market_insight + index_quote
  2. 用新权重/新规则重新计算预判方向
  3. 对比实际涨跌，计算新旧命中率
  4. A/B对照：同一天数据双引擎并行
  5. 统计检验：Fisher精确检验/卡方检验

集成方式：
  from evolution.backtest_runner import run_backtest
  result = run_backtest(db, hypothesis, lookback_days=30)
"""

import json
import os
import sys
import datetime
import math
from collections import defaultdict
from typing import List, Dict, Any, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPT_DIR)

KNOWLEDGE_DIR = os.path.join(SCRIPT_DIR, "knowledge")


# ---------------------------------------------------------------------------
# 常量 —— 方向关键词 / 阈值 / 因子映射
# ---------------------------------------------------------------------------

# 看涨（利好/多头）关键词
BULLISH_KWS: List[str] = [
    "上涨", "利好", "提振", "拉动", "加仓", "回流", "活跃", "偏多",
    "高开", "走强", "领涨", "升温", "强势", "反弹", "突破", "流入",
    "信心", "青睐", "催化", "景气",
]

# 看跌（利空/空头）关键词
BEARISH_KWS: List[str] = [
    "下跌", "承压", "利空", "拖累", "风险", "流出", "压制", "受压",
    "警惕", "退潮", "偏空", "低开", "走弱", "降温", "抛压", "下挫",
    "疲软", "谨慎", "抛弃", "萎缩",
]

# insight.category → factor 因子映射（用于 L1 加权）
# 部分类别（海外市场/资金面）需结合文本进一步细分
FACTOR_KEYWORDS: List[Tuple[str, List[str]]] = [
    ("north_money", ["北向", "外资"]),
    ("us_market", ["美股", "标普", "纳斯达克", "道琼斯"]),
    ("hk_market", ["恒生", "港股"]),
    ("gold_price", ["黄金"]),
    ("oil_price", ["原油", "石油"]),
    ("sector_moneyflow", ["板块资金", "净流入", "净流出"]),
    ("dragon_tiger", ["龙虎榜", "净买入"]),
    ("limit_up", ["涨停"]),
    ("margin", ["融资余额", "杠杆资金"]),
    ("cls_telegraph", ["舆情", "电报"]),
    ("cross_market_map", ["跨市场", "映射", "传导"]),
]

# 实际涨跌幅方向判定阈值（%）：区间内视为中性
ACTUAL_THRESHOLD: float = 0.3

# 市场状态判定阈值（%）：近5天累计涨跌幅
REGIME_THRESHOLD: float = 3.0

# 因子权重压制阈值：权重 <= 该值视为"该因子被关闭"，其信号降级为中性
SUPPRESS_THRESHOLD: float = 0.0

# 最小样本量：低于此值判定回测不通过
MIN_SAMPLE_SIZE: int = 5

# 统计显著性阈值
SIGNIFICANCE_ALPHA: float = 0.1

# 时段优先级（用于按日聚合时选取代表时段）
PERIOD_PRIORITY: Dict[str, int] = {"evening": 3, "noon": 2, "morning": 1, "all": 0}


# ---------------------------------------------------------------------------
# 通用工具函数
# ---------------------------------------------------------------------------

def _safe_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    """安全转 float：None / 空串 / 非数字均返回 default。"""
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (ValueError, TypeError):
        return default


def _dir_to_num(direction: str) -> int:
    """方向转数值：bullish→+1, bearish→-1, 其余→0。"""
    if direction == "bullish":
        return 1
    if direction == "bearish":
        return -1
    return 0


def _num_to_dir(score: float) -> str:
    """数值转方向：>0→bullish, <0→bearish, 否则 neutral。"""
    if score > 0:
        return "bullish"
    if score < 0:
        return "bearish"
    return "neutral"


def _representative_period(periods: List[str]) -> str:
    """从一组时段中选取代表时段（优先 evening > noon > morning）。"""
    if not periods:
        return ""
    return max(periods, key=lambda p: PERIOD_PRIORITY.get(p, 0))


def _get_weight(weights: Optional[Dict[str, Any]], factor: str) -> Optional[float]:
    """从权重表读取某因子权重，兼容两种结构：
      - 扁平: {factor: 15}
      - 嵌套: {factor: {"weight": 15, "description": "..."}}
    """
    if not weights or factor not in weights:
        return None
    val = weights[factor]
    if isinstance(val, dict):
        return val.get("weight", 0)
    return val


def _flatten_weights(weights: Optional[Dict[str, Any]]) -> Dict[str, float]:
    """将权重表扁平化为 {factor: weight_number}。"""
    flat: Dict[str, float] = {}
    if not weights:
        return flat
    for factor, val in weights.items():
        if isinstance(val, dict):
            flat[factor] = val.get("weight", 0)
        else:
            flat[factor] = val
    return flat


def _detect_factor_from_text(text: str) -> Optional[str]:
    """根据信号文本关键词推断所属因子。"""
    if not text:
        return None
    for factor, kws in FACTOR_KEYWORDS:
        if any(k in text for k in kws):
            return factor
    return None


def _category_to_factor(category: str, text: str = "") -> str:
    """将 insight.category 映射到 factor_weights 中的因子名。

    对多义类别（海外市场/资金面）结合文本细分。
    """
    cat = (category or "").strip()
    txt = text or ""
    if cat == "海外市场":
        if "黄金" in txt:
            return "gold_price"
        if "原油" in txt or "石油" in txt:
            return "oil_price"
        if "恒生" in txt or "港股" in txt:
            return "hk_market"
        return "us_market"
    if cat == "资金面":
        if "融资" in txt or "杠杆" in txt:
            return "margin"
        return "north_money"
    if cat == "板块资金":
        return "sector_moneyflow"
    if cat == "龙虎榜":
        return "dragon_tiger"
    if cat == "涨停池":
        return "limit_up"
    if cat == "财联社舆情":
        return "cls_telegraph"
    if cat == "跨市场映射":
        return "cross_market_map"
    if cat == "A股盘面":
        # A股盘面描述指数本身，作为动量代理归入 limit_up
        return "limit_up"
    # 兜底：从文本推断
    detected = _detect_factor_from_text(txt)
    return detected or "cls_telegraph"


def _load_factor_weights() -> Dict[str, Any]:
    """加载当前生产环境的因子权重表（knowledge/factor_weights.json）。"""
    path = os.path.join(KNOWLEDGE_DIR, "factor_weights.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("factors", data) or {}
    except Exception as e:
        print(f"  [警告] 加载 factor_weights.json 失败: {e}")
        return {}


# ---------------------------------------------------------------------------
# 历史数据加载
# ---------------------------------------------------------------------------

def _load_historical_insights(db: Any, date_str: str, lookback_days: int) -> List[Dict[str, Any]]:
    """读取历史 market_insight 记录。

    通过 db._conn() 执行 SQL，查询 [date_str - lookback_days, date_str] 区间内
    的全部洞见，按 date, period 排序返回。

    Args:
        db: DB 实例
        date_str: 回测截止日期 YYYY-MM-DD
        lookback_days: 向前回溯天数

    Returns:
        insight 字典列表
    """
    end_dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    start_dt = end_dt - datetime.timedelta(days=lookback_days)
    start_str = start_dt.strftime("%Y-%m-%d")

    try:
        conn = db._conn()
        cur = conn.execute(
            "SELECT * FROM market_insight WHERE date >= ? AND date <= ? "
            "ORDER BY date, period",
            (start_str, date_str),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        print(f"  [错误] 读取历史 market_insight 失败: {e}")
        return []


def _load_historical_index_quotes(db: Any, date_str: str,
                                  lookback_days: int) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """读取历史指数行情并按日期分组。

    通过 db._conn() 执行 SQL，只取收盘行情（is_realtime=0）。
    同一交易日同一指数可能有多条记录，保留 fetch_time 最新的一条。

    Args:
        db: DB 实例
        date_str: 回测截止日期 YYYY-MM-DD
        lookback_days: 向前回溯天数

    Returns:
        {date_str: {index_name: {row...}, ...}, ...}
    """
    end_dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    start_dt = end_dt - datetime.timedelta(days=lookback_days)
    start_str = start_dt.strftime("%Y-%m-%d")

    try:
        conn = db._conn()
        cur = conn.execute(
            "SELECT * FROM index_quote WHERE trade_date >= ? AND trade_date <= ? "
            "AND is_realtime=0 ORDER BY trade_date, fetch_time",
            (start_str, date_str),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
    except Exception as e:
        print(f"  [错误] 读取历史 index_quote 失败: {e}")
        return {}

    grouped: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    for r in rows:
        trade_date = r.get("trade_date", "")
        name = r.get("name", "")
        if not trade_date or not name:
            continue
        # 同名指数保留 fetch_time 最新的一条
        existing = grouped[trade_date].get(name)
        if existing is None or (r.get("fetch_time", "") or "") >= (existing.get("fetch_time", "") or ""):
            grouped[trade_date][name] = r
    return dict(grouped)


# ---------------------------------------------------------------------------
# 方向判定
# ---------------------------------------------------------------------------

def _determine_prediction_direction(insight_text: str,
                                   factor_weights: Optional[Dict[str, Any]] = None) -> str:
    """从 insight 文本推断预判方向（多/空/中性）。

    解析 a_share_impact 文本：
      - 含"上涨"/"利好"/"提振"/"拉动"等 → bullish
      - 含"下跌"/"承压"/"利空"/"拖累"/"风险"等 → bearish
      - 否则 → neutral

    factor_weights 用于 L1 假设：若该信号所属因子的权重被压制到
    SUPPRESS_THRESHOLD 及以下（例如假设把某因子权重改为 0 关闭），
    则该信号降级为 neutral，体现"调整后权重可能改变方向判断"。

    Args:
        insight_text: a_share_impact 文本
        factor_weights: 权重表（扁平或嵌套均兼容）

    Returns:
        "bullish" / "bearish" / "neutral"
    """
    if not insight_text:
        return "neutral"

    bull = sum(1 for kw in BULLISH_KWS if kw in insight_text)
    bear = sum(1 for kw in BEARISH_KWS if kw in insight_text)
    if bull > bear:
        base = "bullish"
    elif bear > bull:
        base = "bearish"
    else:
        return "neutral"

    # 权重压制：若相关因子被关闭，信号降级为中性
    if factor_weights:
        factor = _detect_factor_from_text(insight_text)
        if factor:
            w = _get_weight(factor_weights, factor)
            if w is not None and w <= SUPPRESS_THRESHOLD:
                return "neutral"
    return base


def _determine_actual_direction(index_quotes: Any) -> str:
    """从指数行情判断实际方向。

    优先读取上证指数 pct_chg：
      >0.3% → bullish
      <-0.3% → bearish
      否则   → neutral

    Args:
        index_quotes: 单日行情。可为 {name: row} 字典或 row 列表。

    Returns:
        "bullish" / "bearish" / "neutral"
    """
    if not index_quotes:
        return "neutral"

    target: Optional[Dict[str, Any]] = None
    if isinstance(index_quotes, dict):
        for name, row in index_quotes.items():
            if name == "上证指数":
                target = row if isinstance(row, dict) else None
                break
        if target is None:
            for row in index_quotes.values():
                if isinstance(row, dict):
                    target = row
                    break
    elif isinstance(index_quotes, list):
        for row in index_quotes:
            if isinstance(row, dict) and row.get("name") == "上证指数":
                target = row
                break
        if target is None and index_quotes:
            target = index_quotes[0] if isinstance(index_quotes[0], dict) else None

    if not target:
        return "neutral"

    pct = _safe_float(target.get("pct_chg"))
    if pct is None:
        return "neutral"
    if pct > ACTUAL_THRESHOLD:
        return "bullish"
    if pct < -ACTUAL_THRESHOLD:
        return "bearish"
    return "neutral"


# ---------------------------------------------------------------------------
# L1 / L2 回测应用
# ---------------------------------------------------------------------------

def _apply_weight_adjustment(insights: List[Dict[str, Any]],
                             old_weights: Any,
                             new_weights: Any) -> List[Dict[str, Any]]:
    """L1 回测：用新权重重新计算 insight 的方向。

    流程：
      1. 按日期聚合全部 insight
      2. 对每条 insight：解析基础方向（受权重压制影响），按其所属因子加权
      3. 分别用旧权重 / 新权重累加得到当日加权得分
      4. 得分 >0 → bullish, <0 → bearish, 否则 neutral
      5. 返回每日一条对比记录（actual 由调用方填入）

    Args:
        insights: 历史 insight 列表
        old_weights: 旧权重表（扁平或嵌套）
        new_weights: 新权重表（扁平或嵌套）

    Returns:
        [{"date", "period", "old_direction", "new_direction", "actual", "regime"}, ...]
    """
    old_flat = _flatten_weights(old_weights)
    new_flat = _flatten_weights(new_weights)

    by_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for ins in insights:
        by_date[ins.get("date", "")].append(ins)

    results: List[Dict[str, Any]] = []
    for date, day_insights in by_date.items():
        old_score = 0.0
        new_score = 0.0
        periods: List[str] = []
        for ins in day_insights:
            text = ins.get("a_share_impact", "") or ""
            category = ins.get("category", "")
            factor = _category_to_factor(category, text)

            old_base = _determine_prediction_direction(text, old_flat)
            new_base = _determine_prediction_direction(text, new_flat)

            old_w = old_flat.get(factor, 0)
            new_w = new_flat.get(factor, 0)
            old_score += _dir_to_num(old_base) * old_w
            new_score += _dir_to_num(new_base) * new_w

            if ins.get("period"):
                periods.append(ins["period"])

        results.append({
            "date": date,
            "period": _representative_period(periods),
            "old_direction": _num_to_dir(old_score),
            "new_direction": _num_to_dir(new_score),
            "actual": None,
            "regime": None,
        })

    results.sort(key=lambda r: r["date"])
    return results


def _apply_rule_patch(insights: List[Dict[str, Any]],
                      patch: Dict[str, Any]) -> List[Dict[str, Any]]:
    """L2 回测：用新规则重新匹配。

    patch 可包含：
      - add_bullish_keywords: 新增看涨关键词列表
      - add_bearish_keywords: 新增看跌关键词列表
      - exclude_keywords: 命中即视为噪声信号（降级为中性，不计分）
      - boost_keywords: {keyword: multiplier} 关键词计票加权

    旧方向使用当前基础关键词集合；新方向使用扩展后关键词集合，
    并应用排除规则与加权。按日期聚合，返回每日一条对比记录。

    Args:
        insights: 历史 insight 列表
        patch: 规则补丁

    Returns:
        [{"date", "period", "old_direction", "new_direction", "actual", "regime"}, ...]
    """
    add_bull = patch.get("add_bullish_keywords", []) or []
    add_bear = patch.get("add_bearish_keywords", []) or []
    exclude_kws = patch.get("exclude_keywords", []) or []
    boost = patch.get("boost_keywords", {}) or {}

    ext_bull = list(BULLISH_KWS) + [k for k in add_bull if k not in BULLISH_KWS]
    ext_bear = list(BEARISH_KWS) + [k for k in add_bear if k not in BEARISH_KWS]

    by_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for ins in insights:
        by_date[ins.get("date", "")].append(ins)

    results: List[Dict[str, Any]] = []
    for date, day_insights in by_date.items():
        old_score = 0.0
        new_score = 0.0
        periods: List[str] = []
        for ins in day_insights:
            text = ins.get("a_share_impact", "") or ""
            if ins.get("period"):
                periods.append(ins["period"])

            # 旧规则：基础关键词计数
            ob = sum(1 for kw in BULLISH_KWS if kw in text)
            obe = sum(1 for kw in BEARISH_KWS if kw in text)
            old_score += (ob - obe)

            # 新规则：排除噪声 → 扩展关键词 + 加权
            if any(k in text for k in exclude_kws):
                continue
            nb = 0.0
            for kw in ext_bull:
                if kw in text:
                    nb += boost.get(kw, 1)
            nbe = 0.0
            for kw in ext_bear:
                if kw in text:
                    nbe += boost.get(kw, 1)
            new_score += (nb - nbe)

        results.append({
            "date": date,
            "period": _representative_period(periods),
            "old_direction": _num_to_dir(old_score),
            "new_direction": _num_to_dir(new_score),
            "actual": None,
            "regime": None,
        })

    results.sort(key=lambda r: r["date"])
    return results


# ---------------------------------------------------------------------------
# 命中率 & 统计检验
# ---------------------------------------------------------------------------

def _calculate_hit_rate(results: List[Dict[str, Any]]) -> float:
    """计算命中率：direction == actual 的比例。

    仅统计 actual 非 neutral 的可判定样本（震荡日无法评判方向）。
    每个 result 需含 "direction" 与 "actual" 字段。

    Args:
        results: [{"direction": "bullish", "actual": "bullish"}, ...]

    Returns:
        命中率 [0, 1]；无可判定样本时返回 0.0
    """
    if not results:
        return 0.0
    judgable = [r for r in results
                if r.get("actual") and r.get("actual") != "neutral"]
    if not judgable:
        return 0.0
    hits = sum(1 for r in judgable if r.get("direction") == r.get("actual"))
    return hits / len(judgable)


def _fisher_exact_test(old_hits: int, old_total: int,
                       new_hits: int, new_total: int) -> Dict[str, Any]:
    """Fisher 精确检验或卡方近似。

    检验"新规则命中率是否显著优于旧规则"。
      - 优先使用 scipy.stats.fisher_exact（alternative='greater'）
      - scipy 不可用时降级为卡方近似（1 自由度）：
            chi2 = N*(ad-bc)^2 / ((a+b)(c+d)(a+c)(b+d))
        p 值通过 erfc(sqrt(chi2/2)) 计算（1 自由度卡方分布的生存函数）
      - p < 0.1 认为显著

    Args:
        old_hits: 旧规则命中数
        old_total: 旧规则可判定样本数
        new_hits: 新规则命中数
        new_total: 新规则可判定样本数

    Returns:
        {"p_value": float, "is_significant": bool, "method": "fisher"/"chi2"}
    """
    old_miss = max(old_total - old_hits, 0)
    new_miss = max(new_total - new_hits, 0)

    # 退化情形：无样本或零方差
    if old_total <= 0 or new_total <= 0:
        return {"p_value": 1.0, "is_significant": False, "method": "chi2"}
    if (old_hits + old_miss) == 0 or (new_hits + new_miss) == 0:
        return {"p_value": 1.0, "is_significant": False, "method": "chi2"}

    # 优先 Fisher 精确检验
    try:
        from scipy.stats import fisher_exact  # type: ignore
        table = [[old_hits, old_miss], [new_hits, new_miss]]
        _, p_value = fisher_exact(table, alternative="greater")
        return {
            "p_value": float(p_value),
            "is_significant": bool(p_value < SIGNIFICANCE_ALPHA),
            "method": "fisher",
        }
    except ImportError:
        pass
    except Exception:
        # scipy 存在但调用异常，降级卡方
        pass

    # 卡方近似
    a, b = float(old_hits), float(old_miss)
    c, d = float(new_hits), float(new_miss)
    n = a + b + c + d
    denom = (a + b) * (c + d) * (a + c) * (b + d)
    if denom <= 0:
        return {"p_value": 1.0, "is_significant": False, "method": "chi2"}
    chi2 = n * (a * d - b * c) ** 2 / denom
    try:
        p_value = math.erfc(math.sqrt(chi2 / 2.0))
    except (ValueError, OverflowError):
        p_value = 1.0
    # 数值保护
    p_value = max(0.0, min(1.0, p_value))
    return {
        "p_value": p_value,
        "is_significant": bool(p_value < SIGNIFICANCE_ALPHA),
        "method": "chi2",
    }


# ---------------------------------------------------------------------------
# 市场状态分类
# ---------------------------------------------------------------------------

def _classify_market_regime(index_quotes: Dict[str, Dict[str, Dict[str, Any]]]) -> str:
    """分类市场状态。

    读取近5天上证指数 pct_chg，累计涨跌幅：
      累计 > 3% → bull
      累计 < -3% → bear
      否则       → sideways

    Args:
        index_quotes: {date_str: {index_name: row}, ...}，取最近5个日期

    Returns:
        "bull" / "bear" / "sideways"
    """
    if not index_quotes:
        return "sideways"

    dates = sorted(index_quotes.keys())
    window_dates = dates[-5:]

    cumulative = 0.0
    count = 0
    for d in window_dates:
        day_quotes = index_quotes.get(d, {})
        target = day_quotes.get("上证指数")
        if target is None and day_quotes:
            # 上证缺失时退化为当日首个指数
            target = next(iter(day_quotes.values()), None)
        if not target or not isinstance(target, dict):
            continue
        pct = _safe_float(target.get("pct_chg"))
        if pct is None:
            continue
        cumulative += pct
        count += 1

    if count == 0:
        return "sideways"
    if cumulative > REGIME_THRESHOLD:
        return "bull"
    if cumulative < -REGIME_THRESHOLD:
        return "bear"
    return "sideways"


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------

def _generate_backtest_report(backtest_result: Dict[str, Any]) -> str:
    """生成回测报告文本（markdown 格式）。

    Args:
        backtest_result: run_backtest 返回的结果字典

    Returns:
        markdown 字符串
    """
    r = backtest_result
    hid = r.get("hypothesis_id", "UNKNOWN")
    desc = r.get("hypothesis_description", "")
    old_rate = r.get("old_hit_rate", 0.0)
    new_rate = r.get("new_hit_rate", 0.0)
    improvement = r.get("improvement", 0.0)
    sample = r.get("sample_size", 0)
    passed = r.get("passed", False)
    stat = r.get("statistical_test", {}) or {}
    p_val = stat.get("p_value", 1.0)
    method = stat.get("method", "-")
    sig = stat.get("is_significant", False)
    per_regime = r.get("per_market_regime", {}) or {}

    lines: List[str] = []
    lines.append(f"# 回测报告 — 假设 {hid}")
    lines.append("")
    if desc:
        lines.append(f"**假设描述**: {desc}")
        lines.append("")
    lines.append("## 总体命中率")
    lines.append("")
    lines.append("| 指标 | 旧规则 | 新规则 | 变化 |")
    lines.append("|------|--------|--------|------|")
    lines.append(f"| 命中率 | {old_rate:.1%} | {new_rate:.1%} | "
                 f"{'+' if improvement >= 0 else ''}{improvement:.1%} |")
    lines.append(f"| 样本量 | {sample} | {sample} | - |")
    lines.append("")
    lines.append(f"- **判定**: {'通过 ✅' if passed else '未通过 ❌'}")
    lines.append(f"- **统计检验**: {method}, p={p_val:.4f}, "
                  f"{'显著' if sig else '不显著'} (α={SIGNIFICANCE_ALPHA})")
    if sample < MIN_SAMPLE_SIZE:
        lines.append(f"- **注意**: 样本量 {sample} < {MIN_SAMPLE_SIZE}，结论可靠性不足")
    lines.append("")

    if per_regime:
        lines.append("## 分市场状态")
        lines.append("")
        lines.append("| 市场状态 | 旧命中率 | 新命中率 | 样本量 |")
        lines.append("|----------|----------|----------|--------|")
        for regime in ("bull", "bear", "sideways"):
            info = per_regime.get(regime)
            if not info:
                continue
            old_r = info.get("old", 0.0)
            new_r = info.get("new", 0.0)
            n = info.get("samples", 0)
            lines.append(f"| {regime} | {old_r:.1%} | {new_r:.1%} | {n} |")
        lines.append("")

    details = r.get("details", []) or []
    if details:
        lines.append("## 逐日明细")
        lines.append("")
        lines.append("| 日期 | 市场状态 | 旧预判 | 新预判 | 实际 | 旧命中 | 新命中 |")
        lines.append("|------|----------|--------|--------|------|--------|--------|")
        for d in details:
            old_hit = "✓" if d.get("old_hit") else "✗"
            new_hit = "✓" if d.get("new_hit") else "✗"
            lines.append(
                f"| {d.get('date','')} | {d.get('regime','')} | "
                f"{d.get('old_direction','')} | {d.get('new_direction','')} | "
                f"{d.get('actual','')} | {old_hit} | {new_hit} |"
            )
        lines.append("")

    lines.append("---")
    lines.append("*由 backtest_runner 自动生成*")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def run_backtest(db: Any, hypothesis: Dict[str, Any],
                 lookback_days: int = 30) -> Dict[str, Any]:
    """主入口：对单个假设在历史数据上回测。

    流程：
      1. 读取过去 lookback_days 天的 market_insight 和 index_quote
      2. 根据假设类型（L1 因子权重 / L2 规则补丁）重新计算预判方向
      3. 对比实际涨跌，计算新旧命中率
      4. 分市场状态统计、Fisher/卡方检验
      5. 返回结构化回测结果

    Args:
        db: DB 实例
        hypothesis: 假设字典，需包含:
            - hypothesis_id: 假设编号
            - level: "L1" / "L2" / "L3"（L3 默认按 L1 处理）
            - description: 假设描述
            - changes:
                L1: {"factor_weights": {factor: {"old": x, "new": y}, ...}}
                L2: {"rule_patch": {"add_bullish_keywords": [...], ...}}
        lookback_days: 回溯天数

    Returns:
        回测结果字典（见模块文档示例）
    """
    hypothesis_id = hypothesis.get("hypothesis_id", "UNKNOWN")
    description = hypothesis.get("description", "")
    changes = hypothesis.get("changes", {}) or {}

    # 回测截止日期默认今天
    date_str = hypothesis.get("date") or datetime.datetime.now().strftime("%Y-%m-%d")

    print(f"\n=== 回测启动: 假设 {hypothesis_id} (lookback={lookback_days}天) ===")
    if description:
        print(f"  描述: {description}")

    # 1. 加载历史数据
    insights = _load_historical_insights(db, date_str, lookback_days)
    quotes_map = _load_historical_index_quotes(db, date_str, lookback_days)
    print(f"  [数据] 历史洞见 {len(insights)} 条，有行情的交易日 {len(quotes_map)} 天")

    if not insights:
        print("  [跳过] 无历史洞见数据，回测终止")
        return {
            "hypothesis_id": hypothesis_id,
            "hypothesis_description": description,
            "old_hit_rate": 0.0,
            "new_hit_rate": 0.0,
            "improvement": 0.0,
            "sample_size": 0,
            "per_market_regime": {},
            "passed": False,
            "statistical_test": {"p_value": 1.0, "is_significant": False, "method": "none"},
            "details": [],
            "reason": "no_historical_insights",
        }

    # 2. 根据假设类型应用规则，得到对比记录
    if "factor_weights" in changes:
        # L1: 因子权重调优
        old_full = _load_factor_weights()
        old_flat = _flatten_weights(old_full)
        new_flat = dict(old_flat)
        for factor, adj in (changes.get("factor_weights") or {}).items():
            if isinstance(adj, dict):
                new_flat[factor] = adj.get("new", old_flat.get(factor, 0))
            else:
                new_flat[factor] = adj
        records = _apply_weight_adjustment(insights, old_flat, new_flat)
        print(f"  [L1] 应用权重调整: {list((changes.get('factor_weights') or {}).keys())}")
    elif "rule_patch" in changes:
        # L2: 规则迭代
        patch = changes.get("rule_patch") or {}
        records = _apply_rule_patch(insights, patch)
        print(f"  [L2] 应用规则补丁: {list(patch.keys())}")
    else:
        # L3 / 无显式变更：以当前权重做基线对照（旧==新）
        old_full = _load_factor_weights()
        old_flat = _flatten_weights(old_full)
        records = _apply_weight_adjustment(insights, old_flat, old_flat)
        print("  [L3/默认] 未识别 changes，按基线权重对照")

    # 3. 填入实际方向与市场状态
    all_quote_dates = sorted(quotes_map.keys())
    for r in records:
        d = r["date"]
        r["actual"] = _determine_actual_direction(quotes_map.get(d, {}))
        # 滚动5天窗口判定该日市场状态
        preceding = [x for x in all_quote_dates if x <= d][-5:]
        window = {x: quotes_map[x] for x in preceding if x in quotes_map}
        r["regime"] = _classify_market_regime(window)

    # 4. 计算命中率（仅 actual 非 neutral 的可判定样本）
    old_projected = [{"direction": r["old_direction"], "actual": r["actual"]} for r in records]
    new_projected = [{"direction": r["new_direction"], "actual": r["actual"]} for r in records]
    old_hit_rate = _calculate_hit_rate(old_projected)
    new_hit_rate = _calculate_hit_rate(new_projected)

    judgable = [r for r in records if r.get("actual") and r["actual"] != "neutral"]
    sample_size = len(judgable)

    old_hits = sum(1 for r in judgable if r["old_direction"] == r["actual"])
    new_hits = sum(1 for r in judgable if r["new_direction"] == r["actual"])

    improvement = new_hit_rate - old_hit_rate

    # 5. 分市场状态统计
    per_market_regime: Dict[str, Dict[str, Any]] = {}
    regime_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in judgable:
        regime_groups[r.get("regime", "sideways")].append(r)
    for regime, group in regime_groups.items():
        n = len(group)
        old_r = sum(1 for r in group if r["old_direction"] == r["actual"]) / n if n else 0.0
        new_r = sum(1 for r in group if r["new_direction"] == r["actual"]) / n if n else 0.0
        per_market_regime[regime] = {"old": old_r, "new": new_r, "samples": n}

    # 6. 统计检验
    stat = _fisher_exact_test(old_hits, sample_size, new_hits, sample_size)

    # 7. 判定通过
    passed = (new_hit_rate > old_hit_rate) and (sample_size >= MIN_SAMPLE_SIZE)

    # 8. 逐日明细
    details = []
    for r in records:
        details.append({
            "date": r["date"],
            "period": r.get("period", ""),
            "regime": r.get("regime", ""),
            "old_direction": r["old_direction"],
            "new_direction": r["new_direction"],
            "actual": r.get("actual", "neutral"),
            "old_hit": r["old_direction"] == r.get("actual") and r.get("actual") != "neutral",
            "new_hit": r["new_direction"] == r.get("actual") and r.get("actual") != "neutral",
        })

    result = {
        "hypothesis_id": hypothesis_id,
        "hypothesis_description": description,
        "old_hit_rate": round(old_hit_rate, 4),
        "new_hit_rate": round(new_hit_rate, 4),
        "improvement": round(improvement, 4),
        "sample_size": sample_size,
        "per_market_regime": per_market_regime,
        "passed": passed,
        "statistical_test": stat,
        "details": details,
    }

    print(f"  [结果] 旧命中率 {old_hit_rate:.1%} → 新命中率 {new_hit_rate:.1%} "
          f"(改进 {improvement:+.1%}, 样本 {sample_size}, "
          f"{stat['method']} p={stat['p_value']:.4f}, "
          f"{'通过' if passed else '未通过'})")
    return result


# ---------------------------------------------------------------------------
# 命令行入口（自测）
# ---------------------------------------------------------------------------

def main():
    """命令行入口：用示例假设跑一次回测。"""
    import argparse
    parser = argparse.ArgumentParser(description="回测框架 — 在历史数据上验证进化假设")
    parser.add_argument("--lookback", type=int, default=30, help="回溯天数")
    parser.add_argument("--level", default="L1", choices=["L1", "L2"], help="假设层级")
    args = parser.parse_args()

    from db import DB
    db = DB()
    db.init()

    if args.level == "L1":
        hypothesis = {
            "hypothesis_id": "H001",
            "level": "L1",
            "description": "北向资金权重 15→20，美股权重 10→5",
            "changes": {
                "factor_weights": {
                    "north_money": {"old": 15, "new": 20},
                    "us_market": {"old": 10, "new": 5},
                }
            },
        }
    else:
        hypothesis = {
            "hypothesis_id": "H002",
            "level": "L2",
            "description": "新增'青睐/催化'为看涨词，排除'澄清/否认'噪声",
            "changes": {
                "rule_patch": {
                    "add_bullish_keywords": ["青睐", "催化"],
                    "exclude_keywords": ["澄清", "否认", "不涉及"],
                    "boost_keywords": {"利好": 2},
                }
            },
        }

    result = run_backtest(db, hypothesis, lookback_days=args.lookback)
    report = _generate_backtest_report(result)
    print("\n" + report)


if __name__ == "__main__":
    main()
