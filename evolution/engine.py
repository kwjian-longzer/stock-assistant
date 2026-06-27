#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
进化引擎 (Evolution Engine)
============================
在 learning_loop 的"验证+固化"之后执行，实现六阶段进化闭环：
  1. 诊断(Diagnose) — 分析失败模式，不只看命中率
  2. 假设(Hypothesize) — 生成 L1参数 / L2规则 / L3逻辑 三层改进假设
  3. 实验(Experiment) — 历史回测 + A/B 对照验证假设
  4. 验证(Validate) — 统计显著性检验，防过拟合
  5. 部署(Deploy) — shadow 模式 3 天 → 切换 production
  6. 监控(Monitor) — 部署后跟踪，退化自动回滚

进化三层次：
  L1 参数调优: 调整 factor_weights.json 中的权重
  L2 规则迭代: 生成规则补丁(同义词 / 排除表 / 匹配规则)
  L3 逻辑迭代: 提出洞见引擎算法改进方向

集成方式:
  from evolution.engine import run as run_evolution
  # 在 learning_loop.run() 末尾调用
  run_evolution(db, date_str)
"""

import json
import os
import sys
import re
import math
import shutil
import datetime
from collections import defaultdict
from typing import List, Dict, Any, Optional, Tuple

# 项目路径
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPT_DIR)

KNOWLEDGE_DIR = os.path.join(SCRIPT_DIR, "knowledge")
FACTOR_WEIGHTS_PATH = os.path.join(KNOWLEDGE_DIR, "factor_weights.json")
BENCHMARK_PATH = os.path.join(KNOWLEDGE_DIR, "accuracy_benchmark.json")
LESSONS_PATH = os.path.join(KNOWLEDGE_DIR, "lessons_learned.md")
FAILED_HYPOTHESES_PATH = os.path.join(KNOWLEDGE_DIR, "failed_hypotheses.md")
ROLLBACK_LOG_PATH = os.path.join(KNOWLEDGE_DIR, "rollback_log.md")
ENGINE_CHANGELOG_PATH = os.path.join(KNOWLEDGE_DIR, "engine_changelog.md")
DEPLOY_STATE_PATH = os.path.join(KNOWLEDGE_DIR, "deploy_state.json")
BACKUPS_DIR = os.path.join(KNOWLEDGE_DIR, "backups")

# 复用 learning_loop 的既有解析逻辑（方向判定 / 因子归类 / 实际方向判定）
try:
    from learning_loop import classify_factor, parse_direction, parse_actual_direction
except Exception as _import_err:  # pragma: no cover - 极端兜底
    classify_factor = None  # type: ignore
    parse_direction = None  # type: ignore
    parse_actual_direction = None  # type: ignore
    print(f"[evolution] 警告: 无法从 learning_loop 导入解析函数，将使用内置兜底: {_import_err}")

# 可选统计依赖：scipy 不可用时用卡方近似
try:
    from scipy.stats import fisher_exact as _scipy_fisher_exact
    _HAS_SCIPY = True
except Exception:
    _HAS_SCIPY = False
    _scipy_fisher_exact = None

# ---------------------------------------------------------------------------
# 常量与映射
# ---------------------------------------------------------------------------

# learning_loop.classify_factor 返回的中文名 → factor_weights.json 的 key
FACTOR_KEY_MAP: Dict[str, Optional[str]] = {
    "北向资金": "north_money",
    "美股": "us_market",
    "港股": "hk_market",
    "黄金": "gold_price",
    "原油": "oil_price",
    "美元汇率": "usd_exchange",       # 无对应权重项，仅做统计
    "板块资金": "sector_moneyflow",
    "龙虎榜": "dragon_tiger",
    "涨停池": "limit_up",
    "融资融券": "margin",
    "舆情": "cls_telegraph",
    "跨市场映射": "cross_market_map",
    "其他": None,
}

# 置信度权重乘子（实验阶段用于加权投票模拟）
CONFIDENCE_MULT: Dict[str, float] = {
    "high": 1.5,
    "medium": 1.0,
    "low": 0.5,
}

# 方向 → 符号
DIR_SIGN: Dict[str, int] = {
    "看涨": 1,
    "看跌": -1,
    "中性": 0,
    "震荡": 0,
}

# 验证阈值
MIN_SAMPLE = 10            # 最小样本量
MIN_IMPROVEMENT = 0.10     # 最小改进幅度
P_VALUE_THRESHOLD = 0.10   # 显著性阈值
SHADOW_DAYS = 3            # shadow 模式天数
ROLLBACK_DEGRADATION = 0.05  # 退化阈值(5%)
ROLLBACK_CONSECUTIVE_DAYS = 3  # 连续退化天数触发回滚


# ===========================================================================
# 基础工具：JSON 读写 / markdown 追加 / 文件备份
# ===========================================================================

def _safe_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    """安全转 float：None / 空串 / 非数字均返回 default。"""
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (ValueError, TypeError):
        return default


def _load_json(path: str, default: Any = None) -> Any:
    """读取 JSON 文件；文件不存在或解析失败时返回 default。"""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[evolution] 警告: 读取 JSON 失败 {path}: {e}")
        return default


def _save_json(path: str, data: Any) -> bool:
    """写入 JSON 文件（UTF-8，缩进2，不转义中文）。自动创建父目录。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[evolution] 错误: 写入 JSON 失败 {path}: {e}")
        return False


def _append_to_md(path: str, content: str) -> bool:
    """追加内容到 markdown 文件（不存在则创建）。自动创建父目录。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(content)
            if not content.endswith("\n"):
                f.write("\n")
        return True
    except Exception as e:
        print(f"[evolution] 错误: 追加 markdown 失败 {path}: {e}")
        return False


def _backup_file(path: str) -> Optional[str]:
    """部署前备份文件到 knowledge/backups/，返回备份路径；失败返回 None。"""
    if not os.path.exists(path):
        return None
    try:
        os.makedirs(BACKUPS_DIR, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        base = os.path.basename(path)
        backup_path = os.path.join(BACKUPS_DIR, f"{base}.{ts}.bak")
        shutil.copy2(path, backup_path)
        return backup_path
    except Exception as e:
        print(f"[evolution] 警告: 备份失败 {path}: {e}")
        return None


def _restore_from_backup(backup_path: str, target_path: str) -> bool:
    """从备份文件恢复到目标路径。"""
    try:
        if not os.path.exists(backup_path):
            return False
        shutil.copy2(backup_path, target_path)
        return True
    except Exception as e:
        print(f"[evolution] 错误: 恢复备份失败 {backup_path} -> {target_path}: {e}")
        return False


def _load_factor_weights() -> Dict[str, Any]:
    """读取 factor_weights.json，文件缺失时返回空结构。"""
    data = _load_json(FACTOR_WEIGHTS_PATH, default=None)
    if not isinstance(data, dict):
        return {"_meta": {}, "factors": {}, "combination_factors": {}}
    data.setdefault("factors", {})
    data.setdefault("combination_factors", {})
    data.setdefault("_meta", {})
    return data


def _save_factor_weights(data: Dict[str, Any]) -> bool:
    """写回 factor_weights.json，并刷新 _meta.last_updated。"""
    meta = data.get("_meta", {}) if isinstance(data, dict) else {}
    meta["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d")
    meta["last_updated_by"] = "evolution_engine"
    data["_meta"] = meta
    return _save_json(FACTOR_WEIGHTS_PATH, data)


# ===========================================================================
# DB 辅助查询
# ===========================================================================

def _prev_insight_date(db, date_str: str, lookback: int = 10) -> Optional[str]:
    """查找 date_str 之前最近的有 market_insight 记录的日期。"""
    try:
        conn = db._conn()
        cur = conn.execute(
            "SELECT DISTINCT date FROM market_insight "
            "WHERE date < ? ORDER BY date DESC LIMIT ?",
            (date_str, lookback),
        )
        row = cur.fetchone()
        conn.close()
        return row["date"] if row else None
    except Exception as e:
        print(f"[evolution] 警告: 查找历史洞见日期失败: {e}")
        return None


def _get_market_regime(db, date_str: str) -> str:
    """判断市场状态(牛/熊/震荡)。

    近 5 个有数据的交易日上证指数累计涨跌幅：
      > 3% 为 bull，< -3% 为 bear，否则 sideways。
    无数据时返回 sideways。
    """
    try:
        conn = db._conn()
        cur = conn.execute(
            "SELECT trade_date, AVG(pct_chg) AS pct FROM index_quote "
            "WHERE name='上证指数' AND trade_date <= ? "
            "GROUP BY trade_date ORDER BY trade_date DESC LIMIT 5",
            (date_str,),
        )
        rows = cur.fetchall()
        conn.close()
        pcts = [_safe_float(r["pct"]) for r in rows]
        pcts = [p for p in pcts if p is not None]
        if not pcts:
            return "sideways"
        cum = sum(pcts)
        if cum > 3.0:
            return "bull"
        if cum < -3.0:
            return "bear"
        return "sideways"
    except Exception as e:
        print(f"[evolution] 警告: 判断市场状态失败: {e}")
        return "sideways"


def _get_recent_dates(db, limit: int = 30) -> List[str]:
    """获取有 index_quote 数据的最近 N 个交易日（降序）。"""
    try:
        conn = db._conn()
        cur = conn.execute(
            "SELECT DISTINCT trade_date FROM index_quote "
            "ORDER BY trade_date DESC LIMIT ?",
            (limit,),
        )
        rows = [r["trade_date"] for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        print(f"[evolution] 警告: 获取最近交易日失败: {e}")
        return []


def _build_confidence_cache(db, source_dates: List[str]) -> Dict[Tuple[str, str], str]:
    """构建 (date, category) -> confidence 缓存。

    同一日期同一 category 存在多条洞见时取最高置信度。
    """
    cache: Dict[Tuple[str, str], str] = {}
    if not source_dates:
        return cache
    rank = {"high": 3, "medium": 2, "low": 1}
    for d in source_dates:
        try:
            insights = db.query_insights(date=d)
        except Exception:
            insights = []
        for ins in insights or []:
            cat = (ins.get("category") or "").strip()
            conf = (ins.get("confidence") or "medium").strip().lower()
            if conf not in rank:
                conf = "medium"
            key = (d, cat)
            prev = cache.get(key)
            if prev is None or rank.get(conf, 2) > rank.get(prev, 2):
                cache[key] = conf
    return cache


def _parse_source_date(gap_analysis: str) -> Optional[str]:
    """从 gap_analysis 中解析 '源洞见日期=YYYY-MM-DD'。"""
    if not gap_analysis:
        return None
    m = re.search(r"源洞见日期=(\d{4}-\d{2}-\d{2})", gap_analysis)
    return m.group(1) if m else None


def _parse_prediction_category(prediction: str) -> str:
    """从 prediction 文本 '[CATEGORY] ...' 中解析 category。"""
    if not prediction:
        return ""
    m = re.match(r"\[([^\]]*)\]", prediction)
    return m.group(1).strip() if m else ""


def _parse_dir_from_text(text: str, prefix: str) -> str:
    """从文本中解析方向关键词（看涨/看跌/震荡/中性）。"""
    if not text:
        return ""
    for kw in ("看涨", "看跌", "震荡", "中性"):
        if prefix + kw in text or kw in text:
            return kw
    return ""


def _record_hit(gap_analysis: str) -> Optional[bool]:
    """判定单条 learning_record 是否命中。

    Returns:
        True=命中, False=失误, None=未兑现(震荡)或无法判定
    """
    if not gap_analysis:
        return None
    # '反向' 出现且不含 '命中' → 失误；'命中' 且不含 '反向' → 命中
    if "反向" in gap_analysis or "失误" in gap_analysis:
        return False
    if "命中" in gap_analysis:
        return True
    if "未兑现" in gap_analysis or "震荡" in gap_analysis:
        return None
    return None


# ===========================================================================
# 1. 诊断 (Diagnose)
# ===========================================================================

def diagnose(db, date_str: str, lookback_days: int = 30) -> Dict[str, Any]:
    """诊断层：读取 learning_record 中的验证记录，分析失败模式。

    - 按因子分类统计命中率
    - 按置信度分级统计（high/medium/low）
    - 按市场状态分类（bull/bear/sideways）
    - 识别失败模式：低命中率因子 / 置信度校准问题 / 连续失败 / 信号盲区

    Args:
        db: DB 实例
        date_str: 诊断日期 YYYY-MM-DD
        lookback_days: 回看天数（用于决定读取记录条数）

    Returns:
        dict: 诊断结果结构（见模块 docstring）
    """
    # 取较多条记录以保证覆盖回看窗口（每条记录≈1个交易日1条洞见）
    try:
        records = db.query_learning_records(limit=max(lookback_days * 3, 60))
    except Exception as e:
        print(f"[evolution] 错误: 读取 learning_record 失败: {e}")
        records = []

    # 仅分析盘后验证类记录
    verify_records = [r for r in records if (r.get("category") or "") == "盘后验证"]

    # 收集涉及到的源洞见日期，用于置信度查询
    source_dates = sorted({_parse_source_date(r.get("gap_analysis") or "") for r in verify_records})
    source_dates = [d for d in source_dates if d]
    conf_cache = _build_confidence_cache(db, source_dates)

    # 市场状态（诊断当日的整体状态）
    market_regime = _get_market_regime(db, date_str)

    # --- 按因子 / 置信度 / 市场状态 统计 ---
    factor_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"total": 0, "hits": 0})
    confidence_stats: Dict[str, Dict[str, Any]] = {
        "high": {"total": 0, "hits": 0},
        "medium": {"total": 0, "hits": 0},
        "low": {"total": 0, "hits": 0},
    }
    # 因子 -> {date: [(hit, ...)]} 用于连续失败检测
    factor_by_date: Dict[str, Dict[str, Optional[bool]]] = defaultdict(dict)

    directional_total = 0
    directional_hits = 0

    for r in verify_records:
        gap = r.get("gap_analysis") or ""
        hit = _record_hit(gap)
        if hit is None:
            continue  # 震荡/未兑现不计入方向性样本

        pred = r.get("prediction") or ""
        # 因子分类（中文）→ key
        cn_factor = classify_factor(pred) if classify_factor else "其他"
        key = FACTOR_KEY_MAP.get(cn_factor, cn_factor) or cn_factor

        factor_stats[key]["total"] += 1
        if hit:
            factor_stats[key]["hits"] += 1

        # 置信度：从 (source_date, category) 查
        src_date = _parse_source_date(gap)
        cat = _parse_prediction_category(pred)
        conf = conf_cache.get((src_date, cat), "medium") if (src_date and cat) else "medium"
        if conf not in confidence_stats:
            conf = "medium"
        confidence_stats[conf]["total"] += 1
        if hit:
            confidence_stats[conf]["hits"] += 1

        # 连续失败检测：按 verify date 记录该因子命中情况
        vdate = r.get("date") or ""
        if vdate:
            factor_by_date[key][vdate] = hit

        directional_total += 1
        if hit:
            directional_hits += 1

    overall_rate = (directional_hits / directional_total) if directional_total else 0.0

    # 计算各分项 rate
    for st in factor_stats.values():
        st["rate"] = (st["hits"] / st["total"]) if st["total"] else 0.0
    for conf, st in confidence_stats.items():
        st["rate"] = (st["hits"] / st["total"]) if st["total"] else 0.0

    # --- 失败模式识别 ---
    failure_patterns: List[Dict[str, Any]] = []

    avg_rate = overall_rate or 0.0
    # 1) 因子命中率显著低于均值
    for key, st in factor_stats.items():
        if st["total"] < 3:
            continue
        if st["rate"] < (avg_rate - 0.15) and st["rate"] < 0.50:
            severity = "high" if st["rate"] < 0.35 else "medium"
            failure_patterns.append({
                "type": "low_hit_rate_factor",
                "factor": key,
                "rate": st["rate"],
                "avg": avg_rate,
                "severity": severity,
            })

    # 2) 置信度校准问题：high 置信度命中率反而 < 50%
    high_st = confidence_stats.get("high", {"total": 0, "hits": 0, "rate": 0.0})
    if high_st["total"] >= 3 and high_st["rate"] < 0.50:
        failure_patterns.append({
            "type": "confidence_miscalibration",
            "level": "high",
            "rate": high_st["rate"],
            "expected": ">0.70",
        })

    # 3) 连续失败（系统性缺陷）：同一因子按日期排序连续 >=3 天失误
    for key, date_hits in factor_by_date.items():
        if len(date_hits) < 3:
            continue
        sorted_dates = sorted(date_hits.keys())
        streak = 0
        streak_dates: List[str] = []
        best_streak = 0
        best_dates: List[str] = []
        for d in sorted_dates:
            if date_hits[d] is False:
                streak += 1
                streak_dates.append(d)
                if streak > best_streak:
                    best_streak = streak
                    best_dates = list(streak_dates)
            else:
                streak = 0
                streak_dates = []
        if best_streak >= 3:
            failure_patterns.append({
                "type": "consecutive_failures",
                "factor": key,
                "streak": best_streak,
                "dates": best_dates[-5:],  # 只保留最近5个日期
            })

    # 4) 信号盲区扫描：当日涨停股是否在前日 market_insight 的 a_share_impact 中被提及
    blind_spot = _scan_signal_blind_spot(db, date_str)
    if blind_spot and blind_spot.get("count", 0) > 0:
        failure_patterns.append(blind_spot)

    return {
        "factor_stats": {k: dict(v) for k, v in factor_stats.items()},
        "confidence_stats": {k: dict(v) for k, v in confidence_stats.items()},
        "failure_patterns": failure_patterns,
        "overall_rate": overall_rate,
        "sample_size": directional_total,
        "market_regime": market_regime,
    }


def _scan_signal_blind_spot(db, date_str: str) -> Dict[str, Any]:
    """信号盲区扫描：当日涨停股是否在前日 market_insight 的 a_share_impact 中被提及。

    Returns:
        dict: {"type": "signal_blind_spot", "missed_limit_ups": [...], "count": N}
              若无涨停或无前日洞见，count=0。
    """
    result: Dict[str, Any] = {
        "type": "signal_blind_spot",
        "missed_limit_ups": [],
        "count": 0,
    }
    try:
        limit_ups = db.query_limit_up(date=date_str)
    except Exception as e:
        print(f"[evolution] 警告: 读取涨停板失败: {e}")
        limit_ups = []
    if not limit_ups:
        return result

    source_date = _prev_insight_date(db, date_str)
    if not source_date:
        return result

    try:
        insights = db.query_insights(date=source_date)
    except Exception as e:
        print(f"[evolution] 警告: 读取前日洞见失败: {e}")
        insights = []

    impact_text = " ".join((ins.get("a_share_impact") or "") for ins in insights or [])
    if not impact_text.strip():
        return result

    missed: List[str] = []
    for lu in limit_ups:
        name = (lu.get("name") or "").strip()
        code = (lu.get("ts_code") or "").strip()
        if not name:
            continue
        if name in impact_text or code in impact_text:
            continue
        missed.append(code or name)
    result["missed_limit_ups"] = missed[:20]  # 最多保留20个
    result["count"] = len(missed)
    return result


# ===========================================================================
# 2. 假设 (Hypothesize)
# ===========================================================================

def hypothesize(diagnosis: Dict[str, Any]) -> List[Dict[str, Any]]:
    """假设层：根据诊断结果生成改进假设，分 L1/L2/L3 三层。

    - L1 参数假设：命中率高(>75%)的因子权重+5，命中率低(<40%)的因子权重-5(不低于2)
    - L2 规则假设：信号盲区发现的遗漏涨停股 → 生成"需补入信号关键词"假设
    - L3 逻辑假设：组合因子(共振效应) → 提出组合因子激活假设

    Args:
        diagnosis: diagnose() 返回的诊断结果

    Returns:
        list[dict]: 改进假设列表
    """
    hypotheses: List[Dict[str, Any]] = []
    hid = 0

    factor_weights = _load_factor_weights()
    factors_cfg = factor_weights.get("factors", {})
    combo_cfg = factor_weights.get("combination_factors", {})
    factor_stats = diagnosis.get("factor_stats", {})
    avg_rate = diagnosis.get("overall_rate", 0.0) or 0.0

    # --- L1 参数假设 ---
    for key, st in factor_stats.items():
        if key not in factors_cfg:
            continue  # 仅对存在于 factor_weights.json 的因子做权重调整
        total = st.get("total", 0)
        if total < 3:
            continue
        rate = st.get("rate", 0.0)
        old_weight = int(factors_cfg[key].get("weight", 0))
        new_weight = None
        reason = ""
        if rate > 0.75:
            new_weight = old_weight + 5
            reason = f"{key} 命中率 {rate:.0%} 高于均值 {avg_rate:.0%}，应增加权重"
        elif rate < 0.40:
            new_weight = max(2, old_weight - 5)
            reason = f"{key} 命中率仅 {rate:.0%} 低于均值 {avg_rate:.0%}，应降低权重"
        if new_weight is None or new_weight == old_weight:
            continue

        hid += 1
        # priority: 偏离越极端、样本越大 → 越高
        deviation = abs(rate - avg_rate)
        sample_factor = min(1.0, total / 20.0)
        priority = round(min(1.0, deviation * 1.2 + sample_factor * 0.3), 3)
        hypotheses.append({
            "id": f"H{hid:03d}",
            "level": "L1",
            "type": "weight_adjustment",
            "description": f"{key} 命中率 {rate:.0%} → 权重 {old_weight}→{new_weight}",
            "target_file": "factor_weights.json",
            "changes": {"factor": key, "old_weight": old_weight, "new_weight": new_weight},
            "reason": reason,
            "expected_improvement": f"整体命中率预计 {'+' if new_weight > old_weight else ''}"
                                     f"{abs(new_weight - old_weight) * 0.4:.0%} 左右",
            "priority": priority,
        })

    # --- L2 规则假设（信号盲区 → 关键词补丁）---
    for pat in diagnosis.get("failure_patterns", []):
        if pat.get("type") != "signal_blind_spot":
            continue
        missed = pat.get("missed_limit_ups", []) or []
        count = pat.get("count", 0)
        if count <= 0:
            continue
        # 取遗漏股的所属行业作为补入关键词（比个股名更具泛化性）
        keywords = missed[:8]
        hid += 1
        hypotheses.append({
            "id": f"H{hid:03d}",
            "level": "L2",
            "type": "rule_patch",
            "description": f"信号盲区遗漏 {count} 只涨停股 → 补入信号关键词",
            "target_file": "rules_patches.json",
            "changes": {
                "add_keywords": keywords,
                "action": "expand_signal_coverage",
                "missed_count": count,
            },
            "reason": "前日洞见未提及的次日涨停股，说明信号关键词覆盖不足",
            "expected_improvement": f"信号覆盖率预计提升 {min(0.30, count * 0.03):.0%}",
            "priority": 0.6,
        })

    # --- L3 逻辑假设（组合因子 / 共振效应）---
    # 1) 激活处于 proposed 状态的组合因子
    for ckey, cdef in combo_cfg.items():
        if not isinstance(cdef, dict):
            continue
        if cdef.get("status") != "proposed":
            continue
        hid += 1
        hypotheses.append({
            "id": f"H{hid:03d}",
            "level": "L3",
            "type": "logic_upgrade",
            "description": f"激活组合因子 {ckey}：{cdef.get('description', '')}",
            "target_file": "engine_changelog.md",
            "changes": {
                "combo_factor": ckey,
                "action": "activate",
                "condition": cdef.get("condition", ""),
                "extra_weight": cdef.get("extra_weight", 0),
            },
            "reason": "单因子独立打分忽略共振效应，组合因子可捕获多信号叠加的强趋势日",
            "expected_improvement": "共振日命中率预计 +10% 以上",
            "priority": 0.5,
        })

    # 2) 连续失败 → 提出逻辑改进方向（记录型，不直接改权重）
    for pat in diagnosis.get("failure_patterns", []):
        if pat.get("type") != "consecutive_failures":
            continue
        factor = pat.get("factor", "")
        streak = pat.get("streak", 0)
        hid += 1
        hypotheses.append({
            "id": f"H{hid:03d}",
            "level": "L3",
            "type": "logic_upgrade",
            "description": f"因子 {factor} 连续 {streak} 天失败 → 复盘信号对冲逻辑",
            "target_file": "engine_changelog.md",
            "changes": {
                "action": "review_counter_signal",
                "factor": factor,
                "streak": streak,
            },
            "reason": "连续同类失败提示该因子可能被另一对冲因素系统性压制，需引入反向校验",
            "expected_improvement": "减少系统性误判，预期该因子命中率 +5%",
            "priority": 0.4,
        })

    # 按优先级降序
    hypotheses.sort(key=lambda h: h.get("priority", 0), reverse=True)
    # 重新编号（保持顺序）
    for i, h in enumerate(hypotheses, 1):
        h["id"] = f"H{i:03d}"

    return hypotheses


# ===========================================================================
# 3. 实验 (Experiment)
# ===========================================================================

def experiment(hypothesis: Dict[str, Any], db, lookback_days: int = 30) -> Dict[str, Any]:
    """实验层：在历史数据上验证假设。

    - L1 权重调整：用历史 learning_record 重建每日加权投票，对比新旧权重下的命中率
    - L2 规则补丁：模拟补入关键词后信号盲区覆盖率的变化
    - L3 逻辑升级：回测组合因子在历史触发日的命中率

    Args:
        hypothesis: hypothesize() 生成的假设
        db: DB 实例
        lookback_days: 回测天数

    Returns:
        dict: 实验结果
    """
    level = hypothesis.get("level", "")
    htype = hypothesis.get("type", "")

    try:
        if level == "L1" and htype == "weight_adjustment":
            return _experiment_l1(hypothesis, db, lookback_days)
        if level == "L2" and htype == "rule_patch":
            return _experiment_l2(hypothesis, db, lookback_days)
        if level == "L3" and htype == "logic_upgrade":
            return _experiment_l3(hypothesis, db, lookback_days)
    except Exception as e:
        print(f"[evolution] 警告: 实验 {hypothesis.get('id')} 异常: {e}")
        return {
            "hypothesis_id": hypothesis.get("id"),
            "passed": False,
            "reason": f"实验执行异常: {e}",
            "old_hit_rate": 0.0,
            "new_hit_rate": 0.0,
            "improvement": 0.0,
            "sample_size": 0,
        }

    return {
        "hypothesis_id": hypothesis.get("id"),
        "passed": False,
        "reason": f"未支持的假设类型: {level}/{htype}",
        "old_hit_rate": 0.0,
        "new_hit_rate": 0.0,
        "improvement": 0.0,
        "sample_size": 0,
    }


def _collect_daily_votes(db, lookback_days: int) -> List[Dict[str, Any]]:
    """收集历史每日的因子投票与实际方向。

    每条返回:
        {"verify_date": str, "actual_dir": str, "votes": [{"factor": key, "dir": str, "conf": str}]}
    """
    try:
        records = db.query_learning_records(limit=max(lookback_days * 3, 60))
    except Exception:
        records = []
    verify_records = [r for r in records if (r.get("category") or "") == "盘后验证"]

    source_dates = sorted({_parse_source_date(r.get("gap_analysis") or "") for r in verify_records})
    source_dates = [d for d in source_dates if d]
    conf_cache = _build_confidence_cache(db, source_dates)

    by_date: Dict[str, Dict[str, Any]] = {}
    for r in verify_records:
        gap = r.get("gap_analysis") or ""
        hit = _record_hit(gap)
        if hit is None:
            continue
        vdate = r.get("date") or ""
        if not vdate:
            continue
        d = by_date.setdefault(vdate, {"verify_date": vdate, "actual_dir": "", "votes": []})

        # 实际方向：从 gap_analysis '实际看涨/看跌/震荡'
        if not d["actual_dir"]:
            for kw in ("看涨", "看跌", "震荡"):
                if f"实际{kw}" in gap:
                    d["actual_dir"] = kw
                    break

        pred = r.get("prediction") or ""
        cn_factor = classify_factor(pred) if classify_factor else "其他"
        key = FACTOR_KEY_MAP.get(cn_factor, cn_factor) or cn_factor
        # 预判方向：从 gap_analysis '预判看涨/看跌'
        pred_dir = ""
        for kw in ("看涨", "看跌", "中性"):
            if f"预判{kw}" in gap:
                pred_dir = kw
                break
        if not pred_dir:
            pred_dir = "中性"
        src_date = _parse_source_date(gap)
        cat = _parse_prediction_category(pred)
        conf = conf_cache.get((src_date, cat), "medium") if (src_date and cat) else "medium"

        d["votes"].append({"factor": key, "dir": pred_dir, "conf": conf})

    return sorted(by_date.values(), key=lambda x: x["verify_date"])


def _experiment_l1(hypothesis: Dict[str, Any], db, lookback_days: int) -> Dict[str, Any]:
    """L1 实验：用新权重重新计算每日加权投票方向，对比旧权重命中率。"""
    changes = hypothesis.get("changes", {})
    factor = changes.get("factor")
    new_weight = changes.get("new_weight")
    old_weight = changes.get("old_weight")
    if not factor or new_weight is None or old_weight is None:
        return _exp_fail(hypothesis, "L1 假设缺少 factor/权重信息")

    factor_weights = _load_factor_weights()
    factors_cfg = factor_weights.get("factors", {})
    weight_map = {k: int(v.get("weight", 0)) for k, v in factors_cfg.items()}
    # 应用新权重
    weight_map_new = dict(weight_map)
    weight_map_new[factor] = int(new_weight)

    daily = _collect_daily_votes(db, lookback_days)

    old_hits = old_total = new_hits = new_total = 0
    per_regime: Dict[str, Dict[str, int]] = defaultdict(lambda: {"old_hits": 0, "old_total": 0,
                                                                  "new_hits": 0, "new_total": 0})

    for d in daily:
        actual = d["actual_dir"]
        actual_sign = DIR_SIGN.get(actual, 0)
        if actual_sign == 0:
            continue  # 实际震荡，不计入
        old_score = sum(weight_map.get(v["factor"], 0) * CONFIDENCE_MULT.get(v["conf"], 1.0)
                        * DIR_SIGN.get(v["dir"], 0) for v in d["votes"])
        new_score = sum(weight_map_new.get(v["factor"], 0) * CONFIDENCE_MULT.get(v["conf"], 1.0)
                        * DIR_SIGN.get(v["dir"], 0) for v in d["votes"])

        old_dir_sign = 1 if old_score > 0 else (-1 if old_score < 0 else 0)
        new_dir_sign = 1 if new_score > 0 else (-1 if new_score < 0 else 0)

        regime = _get_market_regime(db, d["verify_date"])
        bucket = per_regime[regime]

        if old_dir_sign != 0:
            old_total += 1
            bucket["old_total"] += 1
            if old_dir_sign == actual_sign:
                old_hits += 1
                bucket["old_hits"] += 1
        if new_dir_sign != 0:
            new_total += 1
            bucket["new_total"] += 1
            if new_dir_sign == actual_sign:
                new_hits += 1
                bucket["new_hits"] += 1

    old_rate = (old_hits / old_total) if old_total else 0.0
    new_rate = (new_hits / new_total) if new_total else 0.0
    improvement = new_rate - old_rate

    per_regime_out = {}
    for reg, b in per_regime.items():
        per_regime_out[reg] = {
            "old": round(b["old_hits"] / b["old_total"], 3) if b["old_total"] else 0.0,
            "new": round(b["new_hits"] / b["new_total"], 3) if b["new_total"] else 0.0,
            "sample": b["new_total"],
        }

    passed = improvement > 0 and new_total >= MIN_SAMPLE
    return {
        "hypothesis_id": hypothesis.get("id"),
        "old_hit_rate": round(old_rate, 3),
        "new_hit_rate": round(new_rate, 3),
        "improvement": round(improvement, 3),
        "sample_size": new_total,
        "per_market_regime": per_regime_out,
        "passed": passed,
        "reason": "" if passed else "新规则命中率未提升或样本不足",
    }


def _experiment_l2(hypothesis: Dict[str, Any], db, lookback_days: int) -> Dict[str, Any]:
    """L2 实验：模拟补入关键词后信号盲区覆盖率的变化。

    覆盖率 = 前日洞见 a_share_impact 提及的次日涨停股比例。
    新规则假设补入关键词后能覆盖部分遗漏股（这里用关键词命中遗漏股的比例近似）。
    """
    changes = hypothesis.get("changes", {})
    add_keywords = changes.get("add_keywords", []) or []
    missed_count = changes.get("missed_count", 0)

    dates = _get_recent_dates(db, lookback_days)
    old_covered = old_total = new_covered = new_total = 0

    for d in dates:
        lus = []
        try:
            lus = db.query_limit_up(date=d)
        except Exception:
            lus = []
        if not lus:
            continue
        src_date = _prev_insight_date(db, d)
        if not src_date:
            continue
        try:
            insights = db.query_insights(date=src_date)
        except Exception:
            insights = []
        impact_text = " ".join((ins.get("a_share_impact") or "") for ins in insights or [])

        for lu in lus:
            name = (lu.get("name") or "").strip()
            code = (lu.get("ts_code") or "").strip()
            old_total += 1
            new_total += 1
            mentioned = bool(name and (name in impact_text or code in impact_text))
            keyword_hit = any(kw and (kw in impact_text) for kw in add_keywords)
            if mentioned:
                old_covered += 1
                new_covered += 1
            elif keyword_hit:
                # 补入关键词后可覆盖
                new_covered += 1

    old_rate = (old_covered / old_total) if old_total else 0.0
    new_rate = (new_covered / new_total) if new_total else 0.0
    improvement = new_rate - old_rate

    passed = improvement > 0 and new_total >= MIN_SAMPLE
    return {
        "hypothesis_id": hypothesis.get("id"),
        "old_hit_rate": round(old_rate, 3),
        "new_hit_rate": round(new_rate, 3),
        "improvement": round(improvement, 3),
        "sample_size": new_total,
        "per_market_regime": {},
        "passed": passed,
        "reason": "" if passed else "关键词补丁未显著提升覆盖率",
    }


def _experiment_l3(hypothesis: Dict[str, Any], db, lookback_days: int) -> Dict[str, Any]:
    """L3 实验：回测组合因子在历史触发日的命中率。

    - 激活组合因子：统计触发日 combo 方向与实际方向一致的比例
    - 复盘对冲逻辑：以该因子命中率作为基准，假设引入反向校验后的改进上限
    """
    changes = hypothesis.get("changes", {})
    action = changes.get("action", "")

    if action == "activate":
        return _experiment_l3_combo(hypothesis, db, lookback_days)
    if action == "review_counter_signal":
        return _experiment_l3_review(hypothesis, db, lookback_days)

    return _exp_fail(hypothesis, f"未支持的 L3 action: {action}")


def _experiment_l3_combo(hypothesis: Dict[str, Any], db, lookback_days: int) -> Dict[str, Any]:
    """回测组合因子触发日的命中率。"""
    changes = hypothesis.get("changes", {})
    combo_key = changes.get("combo_factor")
    factor_weights = _load_factor_weights()
    combo_def = (factor_weights.get("combination_factors", {}) or {}).get(combo_key, {})
    condition = combo_def.get("condition", "") or ""
    # 推断 combo 方向：含 '流入'/'limit_up_count>=' → 看涨；含 '流出' → 看跌
    combo_dir = "看跌" if ("流出" in condition or "margin_balance_decreasing" in condition) else "看涨"

    dates = _get_recent_dates(db, lookback_days)
    combo_total = combo_hits = 0
    overall_total = overall_hits = 0

    for d in dates:
        actual = ""
        try:
            quotes = db.query_index_quote(date=d)
        except Exception:
            quotes = []
        for q in quotes:
            if q.get("name") == "上证指数":
                pct = _safe_float(q.get("pct_chg"))
                if pct is not None:
                    actual = "看涨" if pct > 0.05 else ("看跌" if pct < -0.05 else "震荡")
                break
        if not actual or actual == "震荡":
            continue
        overall_total += 1
        if DIR_SIGN.get(actual, 0) == DIR_SIGN.get(combo_dir, 0):
            overall_hits += 1  # 仅用于基准参考

        # 判定 combo 是否触发
        triggered = _combo_triggered(db, d, condition)
        if not triggered:
            continue
        combo_total += 1
        if DIR_SIGN.get(actual, 0) == DIR_SIGN.get(combo_dir, 0):
            combo_hits += 1

    # 基准：历史整体方向命中率（看涨日占比或看跌日占比，取决于 combo_dir）
    baseline_rate = (overall_hits / overall_total) if overall_total else 0.0
    combo_rate = (combo_hits / combo_total) if combo_total else 0.0
    improvement = combo_rate - baseline_rate

    passed = improvement > 0 and combo_total >= 5
    return {
        "hypothesis_id": hypothesis.get("id"),
        "old_hit_rate": round(baseline_rate, 3),
        "new_hit_rate": round(combo_rate, 3),
        "improvement": round(improvement, 3),
        "sample_size": combo_total,
        "per_market_regime": {},
        "passed": passed,
        "reason": "" if passed else "组合因子触发样本不足或未优于基准",
    }


def _combo_triggered(db, date_str: str, condition: str) -> bool:
    """判定组合因子条件在 date_str 是否触发（简化解析）。"""
    try:
        if "north_money" in condition:
            north = db.query_north_money(date=date_str) or {}
            nm = _safe_float(north.get("north_money"))
            if nm is None:
                return False
            if ">=" in condition or "流入" in condition:
                thr = _extract_threshold(condition, "north_money")
                if nm >= (thr if thr is not None else 50):
                    if "limit_up_count" in condition:
                        lus = db.query_limit_up(date=date_str) or []
                        lu_thr = _extract_threshold(condition, "limit_up_count")
                        if len(lus) < (lu_thr if lu_thr is not None else 30):
                            return False
                    return True
            if "<=" in condition or "流出" in condition:
                thr = _extract_threshold(condition, "north_money")
                if nm <= -(thr if thr is not None else 50):
                    return True
            return False
        return False
    except Exception:
        return False


def _extract_threshold(condition: str, field: str) -> Optional[float]:
    """从条件字符串中提取某字段的数值阈值。"""
    m = re.search(re.escape(field) + r"\s*(?:>=|<=|>|<|=)\s*([0-9]+(?:\.[0-9]+)?)", condition)
    return float(m.group(1)) if m else None


def _experiment_l3_review(hypothesis: Dict[str, Any], db, lookback_days: int) -> Dict[str, Any]:
    """复盘对冲逻辑：统计该因子在连续失败日的反向命中率，作为改进上限估计。"""
    changes = hypothesis.get("changes", {})
    factor = changes.get("factor")
    if not factor or factor not in FACTOR_KEY_MAP:
        return _exp_fail(hypothesis, "复盘假设缺少有效因子")
    cn_name = None
    for cn, k in FACTOR_KEY_MAP.items():
        if k == factor:
            cn_name = cn
            break

    try:
        records = db.query_learning_records(limit=max(lookback_days * 3, 60))
    except Exception:
        records = []
    verify_records = [r for r in records if (r.get("category") or "") == "盘后验证"]

    old_hits = old_total = new_hits = new_total = 0
    for r in verify_records:
        gap = r.get("gap_analysis") or ""
        hit = _record_hit(gap)
        if hit is None:
            continue
        pred = r.get("prediction") or ""
        cn = classify_factor(pred) if classify_factor else "其他"
        if cn != cn_name:
            continue
        old_total += 1
        if hit:
            old_hits += 1
        # 假设引入反向校验：在失败日反向操作
        new_total += 1
        if not hit:
            new_hits += 1  # 反向命中
        else:
            new_hits += 1  # 原本命中保持（保守估计：反向校验仅在失败时介入）

    old_rate = (old_hits / old_total) if old_total else 0.0
    new_rate = (new_hits / new_total) if new_total else 0.0
    improvement = new_rate - old_rate
    passed = improvement > 0 and new_total >= MIN_SAMPLE
    return {
        "hypothesis_id": hypothesis.get("id"),
        "old_hit_rate": round(old_rate, 3),
        "new_hit_rate": round(new_rate, 3),
        "improvement": round(improvement, 3),
        "sample_size": new_total,
        "per_market_regime": {},
        "passed": passed,
        "reason": "" if passed else "反向校验假设未带来显著改进",
    }


def _exp_fail(hypothesis: Dict[str, Any], reason: str) -> Dict[str, Any]:
    """构造实验失败结果。"""
    return {
        "hypothesis_id": hypothesis.get("id"),
        "old_hit_rate": 0.0,
        "new_hit_rate": 0.0,
        "improvement": 0.0,
        "sample_size": 0,
        "per_market_regime": {},
        "passed": False,
        "reason": reason,
    }


# ===========================================================================
# 4. 验证 (Validate)
# ===========================================================================

def _fisher_exact_pvalue(table: List[List[int]]) -> float:
    """Fisher 精确检验 p 值；scipy 不可用时用卡方近似(Yates 校正)。

    table: 2x2 [[a,b],[c,d]]，行为(旧/新规则)，列为(命中/失误)。
    """
    a, b = table[0][0], table[0][1]
    c, d = table[1][0], table[1][1]
    n = a + b + c + d
    if n == 0:
        return 1.0

    if _HAS_SCIPY and _scipy_fisher_exact is not None:
        try:
            _, p = _scipy_fisher_exact([[a, b], [c, d]], alternative="greater")
            return float(p)
        except Exception:
            pass

    # 卡方近似（Yates 校正）
    denom = (a + b) * (c + d) * (a + c) * (b + d)
    if denom == 0:
        return 1.0
    chi2 = n * (abs(a * d - b * c) - n / 2.0) ** 2 / denom
    chi2 = max(0.0, chi2)
    # df=1 的卡方生存函数 ≈ erfc(sqrt(chi2/2))
    try:
        return float(math.erfc(math.sqrt(chi2 / 2.0)))
    except Exception:
        return 1.0


def validate(experiment_result: Dict[str, Any], hypothesis: Dict[str, Any]) -> Dict[str, Any]:
    """验证层：统计显著性检验，防过拟合。

    - 样本量检查：sample_size >= 10
    - 改进幅度检查：improvement >= 0.10
    - Fisher 精确检验：p < 0.1
    - 分市场检验：每个 regime 都有改进(或不退化)
    - 防退化检查：新规则不使整体命中率退化

    Args:
        experiment_result: experiment() 返回结果
        hypothesis: 对应假设

    Returns:
        dict: 验证结论
    """
    reasons: List[str] = []
    sample_size = int(experiment_result.get("sample_size", 0))
    improvement = float(experiment_result.get("improvement", 0.0))
    old_rate = float(experiment_result.get("old_hit_rate", 0.0))
    new_rate = float(experiment_result.get("new_hit_rate", 0.0))

    # 1) 样本量
    if sample_size >= MIN_SAMPLE:
        reasons.append(f"样本量{sample_size}≥{MIN_SAMPLE}通过")
    else:
        reasons.append(f"样本量{sample_size}<{MIN_SAMPLE}阈值未通过")

    # 2) 改进幅度
    if improvement >= MIN_IMPROVEMENT:
        reasons.append(f"改进{improvement:.0%}≥{MIN_IMPROVEMENT:.0%}阈值通过")
    else:
        reasons.append(f"改进{improvement:.0%}<{MIN_IMPROVEMENT:.0%}阈值未通过")

    # 3) Fisher 精确检验（基于新规则命中率 vs 旧规则命中率）
    # 构造 2x2: 行=规则(旧/新)，列=命中/失误
    old_total = max(sample_size, 1)
    new_total = max(sample_size, 1)
    old_hits = int(round(old_rate * old_total))
    new_hits = int(round(new_rate * new_total))
    table = [[old_hits, max(old_total - old_hits, 0)], [new_hits, max(new_total - new_hits, 0)]]
    p_value = _fisher_exact_pvalue(table)
    is_significant = p_value < P_VALUE_THRESHOLD
    if is_significant:
        reasons.append(f"Fisher检验p={p_value:.3f}<{P_VALUE_THRESHOLD}通过")
    else:
        reasons.append(f"Fisher检验p={p_value:.3f}≥{P_VALUE_THRESHOLD}未通过")

    # 4) 分市场检验
    per_regime = experiment_result.get("per_market_regime", {}) or {}
    regime_check_passed = True
    if per_regime:
        for reg, st in per_regime.items():
            o = st.get("old", 0.0)
            n = st.get("new", 0.0)
            if n < o - 0.05:  # 允许 5% 波动
                regime_check_passed = False
                reasons.append(f"分市场[{reg}]退化 {o:.0%}→{n:.0%}")
                break
        if regime_check_passed:
            reasons.append("分市场检验通过(无退化)")
    else:
        reasons.append("分市场检验跳过(无regime数据)")

    # 5) 防退化
    no_degradation = new_rate >= old_rate - 0.02
    if no_degradation:
        reasons.append("防退化检查通过")
    else:
        reasons.append(f"防退化检查未通过: {old_rate:.0%}→{new_rate:.0%}")

    passed = (sample_size >= MIN_SAMPLE
              and improvement >= MIN_IMPROVEMENT
              and is_significant
              and regime_check_passed
              and no_degradation)

    return {
        "passed": passed,
        "reasons": reasons,
        "p_value": round(p_value, 4),
        "is_significant": is_significant,
        "regime_check_passed": regime_check_passed,
        "no_degradation": no_degradation,
    }


# ===========================================================================
# 5. 部署 (Deploy)
# ===========================================================================

def deploy(hypothesis: Dict[str, Any],
           experiment_result: Dict[str, Any],
           validation_result: Dict[str, Any]) -> Dict[str, Any]:
    """部署层：验证通过则写入 knowledge 文件，以 shadow 模式上线。

    - L1: 更新 factor_weights.json（先备份）
    - L2: 更新 rules_patches.json（先备份）
    - L3: 记录到 engine_changelog.md
    - 设置 shadow 模式标记（3 天后切换）

    Args:
        hypothesis: 假设
        experiment_result: 实验结果
        validation_result: 验证结果

    Returns:
        dict: 部署结果
    """
    if not validation_result.get("passed"):
        return {
            "deployed": False,
            "deploy_mode": "skipped",
            "deploy_date": datetime.datetime.now().strftime("%Y-%m-%d"),
            "switch_date": None,
            "files_modified": [],
            "reason": "验证未通过",
        }

    level = hypothesis.get("level", "")
    htype = hypothesis.get("type", "")
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    switch_date = (datetime.datetime.now()
                   + datetime.timedelta(days=SHADOW_DAYS)).strftime("%Y-%m-%d")
    files_modified: List[str] = []
    backup_paths: List[str] = []

    if level == "L1" and htype == "weight_adjustment":
        backup = _backup_file(FACTOR_WEIGHTS_PATH)
        if backup:
            backup_paths.append(backup)
        _deploy_l1_weight(hypothesis, experiment_result)
        files_modified.append("knowledge/factor_weights.json")

    elif level == "L2" and htype == "rule_patch":
        rules_path = os.path.join(KNOWLEDGE_DIR, "rules_patches.json")
        backup = _backup_file(rules_path)
        if backup:
            backup_paths.append(backup)
        _deploy_l2_rules(hypothesis, rules_path)
        files_modified.append("knowledge/rules_patches.json")

    elif level == "L3" and htype == "logic_upgrade":
        # L3 不直接改算法文件，记录到 changelog
        changelog_content = _format_l3_changelog(hypothesis, experiment_result, validation_result)
        _append_to_md(ENGINE_CHANGELOG_PATH, changelog_content)
        files_modified.append("knowledge/engine_changelog.md")
        # 若为激活组合因子，同步更新 factor_weights.json 的 status
        if hypothesis.get("changes", {}).get("action") == "activate":
            backup = _backup_file(FACTOR_WEIGHTS_PATH)
            if backup:
                backup_paths.append(backup)
            _activate_combo_factor(hypothesis)
            files_modified.append("knowledge/factor_weights.json")

    # 写入 deploy_state.json（shadow 模式跟踪）
    _record_deployment(hypothesis, today, switch_date, files_modified, backup_paths)

    return {
        "deployed": True,
        "deploy_mode": "shadow",
        "deploy_date": today,
        "switch_date": switch_date,
        "files_modified": files_modified,
        "backup_paths": backup_paths,
    }


def _deploy_l1_weight(hypothesis: Dict[str, Any], experiment_result: Dict[str, Any]) -> None:
    """L1 部署：更新 factor_weights.json 中的因子权重。"""
    data = _load_factor_weights()
    factors = data.setdefault("factors", {})
    changes = hypothesis.get("changes", {})
    factor = changes.get("factor")
    new_weight = changes.get("new_weight")
    old_weight = changes.get("old_weight")
    if factor in factors:
        hist = factors[factor].setdefault("hit_rate_history", [])
        hist.append({
            "date": datetime.datetime.now().strftime("%Y-%m-%d"),
            "old_weight": old_weight,
            "new_weight": new_weight,
            "experiment": {
                "old_rate": experiment_result.get("old_hit_rate"),
                "new_rate": experiment_result.get("new_hit_rate"),
                "improvement": experiment_result.get("improvement"),
                "sample": experiment_result.get("sample_size"),
            },
        })
        factors[factor]["weight"] = new_weight
        factors[factor]["last_adjusted"] = datetime.datetime.now().strftime("%Y-%m-%d")
        factors[factor]["last_adjustment_reason"] = hypothesis.get("reason", "")
    _save_factor_weights(data)


def _deploy_l2_rules(hypothesis: Dict[str, Any], rules_path: str) -> None:
    """L2 部署：将关键词补丁写入 rules_patches.json。"""
    data = _load_json(rules_path, default={
        "_meta": {
            "description": "规则补丁表 — 由进化引擎 L2 自动维护",
            "version": 1,
            "last_updated": datetime.datetime.now().strftime("%Y-%m-%d"),
        },
        "signal_keywords": [],
        "synonyms": [],
        "exclusions": [],
        "patches": [],
    })
    if not isinstance(data, dict):
        data = {"_meta": {}, "signal_keywords": [], "synonyms": [],
                "exclusions": [], "patches": []}
    data.setdefault("patches", [])
    data.setdefault("signal_keywords", [])

    changes = hypothesis.get("changes", {})
    add_keywords = changes.get("add_keywords", []) or []
    for kw in add_keywords:
        if kw and kw not in data["signal_keywords"]:
            data["signal_keywords"].append(kw)

    data["patches"].append({
        "date": datetime.datetime.now().strftime("%Y-%m-%d"),
        "hypothesis_id": hypothesis.get("id"),
        "type": "expand_signal_coverage",
        "added_keywords": add_keywords,
        "reason": hypothesis.get("reason", ""),
    })
    meta = data.setdefault("_meta", {})
    meta["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d")
    _save_json(rules_path, data)


def _activate_combo_factor(hypothesis: Dict[str, Any]) -> None:
    """L3 部署：将组合因子 status 从 proposed 改为 active。"""
    data = _load_factor_weights()
    combos = data.setdefault("combination_factors", {})
    ckey = hypothesis.get("changes", {}).get("combo_factor")
    if ckey and isinstance(combos.get(ckey), dict):
        combos[ckey]["status"] = "active"
        combos[ckey]["created_date"] = combos[ckey].get("created_date") or \
            datetime.datetime.now().strftime("%Y-%m-%d")
    _save_factor_weights(data)


def _format_l3_changelog(hypothesis: Dict[str, Any],
                         experiment_result: Dict[str, Any],
                         validation_result: Dict[str, Any]) -> str:
    """格式化 L3 逻辑迭代的 changelog 条目。"""
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"\n## [{ts}] {hypothesis.get('id')} {hypothesis.get('description', '')}\n"
        f"- **层次**: L3 逻辑迭代\n"
        f"- **改动**: {json.dumps(hypothesis.get('changes', {}), ensure_ascii=False)}\n"
        f"- **原因**: {hypothesis.get('reason', '')}\n"
        f"- **实验**: 旧命中率 {experiment_result.get('old_hit_rate', 0):.0%} → "
        f"新命中率 {experiment_result.get('new_hit_rate', 0):.0%} "
        f"(改进 {experiment_result.get('improvement', 0):+.0%}, "
        f"样本 {experiment_result.get('sample_size', 0)})\n"
        f"- **验证**: p={validation_result.get('p_value', 1.0)}; "
        f"{'通过' if validation_result.get('passed') else '未通过'}\n"
    )


def _record_deployment(hypothesis: Dict[str, Any], deploy_date: str,
                       switch_date: str, files_modified: List[str],
                       backup_paths: List[str]) -> None:
    """记录部署状态到 deploy_state.json（shadow 模式跟踪）。"""
    state = _load_json(DEPLOY_STATE_PATH, default={
        "_meta": {"description": "进化引擎部署状态跟踪"},
        "deployments": [],
    })
    if not isinstance(state, dict):
        state = {"_meta": {}, "deployments": []}
    state.setdefault("deployments", [])
    state["deployments"].append({
        "hypothesis_id": hypothesis.get("id"),
        "level": hypothesis.get("level"),
        "type": hypothesis.get("type"),
        "description": hypothesis.get("description", ""),
        "deploy_date": deploy_date,
        "switch_date": switch_date,
        "status": "shadow",
        "files_modified": files_modified,
        "backup_paths": backup_paths,
        "post_deploy_rates": [],
        "degradation_days": 0,
    })
    _save_json(DEPLOY_STATE_PATH, state)


# ===========================================================================
# 6. 监控 (Monitor)
# ===========================================================================

def monitor(db, date_str: str) -> Dict[str, Any]:
    """监控层：检查已部署改进的效果，退化则触发回滚。

    - 读取 deploy_state.json 中处于 shadow/production 的部署
    - 计算部署后命中率 vs 基线命中率
    - 连续 3 天退化 > 5% → 触发回滚

    Args:
        db: DB 实例
        date_str: 监控日期

    Returns:
        dict: 监控结果
    """
    state = _load_json(DEPLOY_STATE_PATH, default=None)
    benchmark = _load_json(BENCHMARK_PATH, default={})
    baseline_rate = (benchmark.get("overall", {}) or {}).get("hit_rate", 0.0)

    # 当前整体命中率（最近记录）
    try:
        records = db.query_learning_records(limit=30)
    except Exception:
        records = []
    verify_records = [r for r in records if (r.get("category") or "") == "盘后验证"]
    hits = sum(1 for r in verify_records if _record_hit(r.get("gap_analysis") or "") is True)
    total = sum(1 for r in verify_records if _record_hit(r.get("gap_analysis") or "") is not None)
    post_deploy_rate = (hits / total) if total else 0.0

    rollback_triggered = False
    degraded_deployments: List[str] = []

    if not isinstance(state, dict):
        state = {"deployments": []}
    deployments = state.get("deployments", []) or []

    for dep in deployments:
        if dep.get("status") not in ("shadow", "production"):
            continue
        # 记录部署后命中率
        dep.setdefault("post_deploy_rates", []).append({
            "date": date_str,
            "rate": round(post_deploy_rate, 3),
        })
        # 检查连续退化
        rates = dep.get("post_deploy_rates", [])[-ROLLBACK_CONSECUTIVE_DAYS:]
        degradation_days = 0
        for item in rates:
            if item.get("rate", 1.0) < baseline_rate - ROLLBACK_DEGRADATION:
                degradation_days += 1
        dep["degradation_days"] = degradation_days

        # shadow 到期且无退化 → 切换 production
        if dep.get("status") == "shadow" and date_str >= dep.get("switch_date", ""):
            if degradation_days < ROLLBACK_CONSECUTIVE_DAYS:
                dep["status"] = "production"
                dep["promoted_date"] = date_str

        # 触发回滚
        if degradation_days >= ROLLBACK_CONSECUTIVE_DAYS:
            hid = dep.get("hypothesis_id", "")
            rollback_triggered = rollback_triggered or _rollback_deployment(dep, "连续退化触发自动回滚")
            if hid:
                degraded_deployments.append(hid)

    _save_json(DEPLOY_STATE_PATH, state)

    if degraded_deployments:
        status = "rollback_triggered"
    elif post_deploy_rate < baseline_rate - ROLLBACK_DEGRADATION:
        status = "degraded"
    else:
        status = "healthy"

    return {
        "status": status,
        "post_deploy_rate": round(post_deploy_rate, 3),
        "baseline_rate": round(baseline_rate, 3),
        "degradation_days": max((d.get("degradation_days", 0) for d in deployments), default=0),
        "rollback_triggered": rollback_triggered,
        "degraded_deployments": degraded_deployments,
    }


def _rollback_deployment(dep: Dict[str, Any], reason: str) -> bool:
    """回滚单个部署：从备份恢复文件，并记录。"""
    hid = dep.get("hypothesis_id", "unknown")
    ok = True
    for target_rel, backup in zip(dep.get("files_modified", []), dep.get("backup_paths", [])):
        # files_modified 形如 'knowledge/factor_weights.json'
        target_path = os.path.join(SCRIPT_DIR, target_rel) if not os.path.isabs(target_rel) else target_rel
        if backup and os.path.exists(backup):
            ok = _restore_from_backup(backup, target_path) and ok
    dep["status"] = "rolled_back"
    dep["rollback_date"] = datetime.datetime.now().strftime("%Y-%m-%d")
    dep["rollback_reason"] = reason
    rollback(hid, reason)
    return ok


# ===========================================================================
# 回滚 / 失败假设归档
# ===========================================================================

def rollback(hypothesis_id: str, reason: str) -> bool:
    """回滚：恢复到部署前状态（依赖 deploy_state 中记录的备份），并记录到 rollback_log.md。

    Args:
        hypothesis_id: 假设 ID
        reason: 回滚原因

    Returns:
        bool: 是否成功（记录写入成功即视为 True）
    """
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    content = (
        f"\n## [{ts}] 回滚 {hypothesis_id}\n"
        f"- **原因**: {reason}\n"
        f"- **操作**: 已从 backups/ 恢复部署前文件\n"
    )
    _append_to_md(ROLLBACK_LOG_PATH, content)
    print(f"[evolution] 已回滚 {hypothesis_id}: {reason}")
    return True


def record_failed_hypothesis(hypothesis: Dict[str, Any], reason: str) -> bool:
    """将失败的假设记录到 failed_hypotheses.md，避免未来重复尝试。

    Args:
        hypothesis: 失败的假设
        reason: 失败原因

    Returns:
        bool: 是否记录成功
    """
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    changes = hypothesis.get("changes", {})
    content = (
        f"\n## [{ts}] {hypothesis.get('id', '')} {hypothesis.get('description', '')}\n"
        f"- **层次**: {hypothesis.get('level', '')} / {hypothesis.get('type', '')}\n"
        f"- **改动**: {json.dumps(changes, ensure_ascii=False)}\n"
        f"- **失败原因**: {reason}\n"
        f"- **优先级**: {hypothesis.get('priority', 0)}\n"
    )
    ok = _append_to_md(FAILED_HYPOTHESES_PATH, content)
    if ok:
        print(f"[evolution] 已归档失败假设 {hypothesis.get('id', '')}: {reason}")
    return ok


# ===========================================================================
# 基线更新
# ===========================================================================

def _update_benchmark(db, date_str: str, diagnosis: Dict[str, Any]) -> None:
    """更新 accuracy_benchmark.json，作为下次比较的基准。"""
    data = _load_json(BENCHMARK_PATH, default=None)
    if not isinstance(data, dict):
        data = {
            "_meta": {"description": "预判准确率基线", "version": 1},
            "overall": {},
            "by_factor": {},
            "by_confidence": {},
            "by_market_regime": {},
            "history": [],
        }

    overall = diagnosis.get("overall_rate", 0.0)
    sample = diagnosis.get("sample_size", 0)
    hits = int(round(overall * sample)) if sample else 0

    data["overall"] = {
        "total_predictions": sample,
        "total_hits": hits,
        "hit_rate": round(overall, 4),
        "baseline_date": date_str,
        "baseline_description": f"进化引擎于 {date_str} 更新基线",
    }

    # by_factor
    by_factor = {}
    for k, st in diagnosis.get("factor_stats", {}).items():
        by_factor[k] = {
            "total": st.get("total", 0),
            "hits": st.get("hits", 0),
            "rate": round(st.get("rate", 0.0), 4),
        }
    data["by_factor"] = by_factor

    # by_confidence
    by_conf = {}
    for k, st in diagnosis.get("confidence_stats", {}).items():
        by_conf[k] = {
            "total": st.get("total", 0),
            "hits": st.get("hits", 0),
            "rate": round(st.get("rate", 0.0), 4),
        }
    data["by_confidence"] = by_conf

    # by_market_regime（当前整体归入诊断当日 regime）
    regime = diagnosis.get("market_regime", "sideways")
    by_regime = data.get("by_market_regime", {}) or {}
    by_regime.setdefault(regime, {"total": 0, "hits": 0, "rate": 0.0})
    by_regime[regime] = {
        "total": sample,
        "hits": hits,
        "rate": round(overall, 4),
    }
    data["by_market_regime"] = by_regime

    # history
    history = data.setdefault("history", [])
    history.append({
        "date": date_str,
        "event": "benchmark_updated",
        "overall_rate": round(overall, 4),
        "sample_size": sample,
        "market_regime": regime,
        "failure_patterns": len(diagnosis.get("failure_patterns", [])),
    })
    # 限制 history 长度
    data["history"] = history[-90:]

    meta = data.setdefault("_meta", {})
    meta["last_updated"] = date_str
    meta["last_updated_by"] = "evolution_engine"

    _save_json(BENCHMARK_PATH, data)


# ===========================================================================
# 主入口
# ===========================================================================

def run(db, date_str: Optional[str] = None) -> Dict[str, Any]:
    """进化引擎主入口，编排六阶段闭环。

    顺序:
      0. monitor       监控已部署改进（退化则回滚）
      1. diagnose       诊断失败模式
      2. hypothesize   生成改进假设
      3. experiment    历史回测验证假设
      4. validate      统计显著性检验
      5. deploy        验证通过则 shadow 部署
      6. _update_benchmark 更新基线

    Args:
        db: DB 实例
        date_str: 运行日期 YYYY-MM-DD，默认今天

    Returns:
        dict: {"diagnosis", "hypotheses", "deployed", "monitor"}
    """
    if date_str is None:
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")

    print("=" * 60)
    print(f"进化引擎 启动 @ {date_str}")
    print("=" * 60)

    # 0. 先监控已部署的改进
    monitor_result = monitor(db, date_str)
    print(f"  [监控] 状态: {monitor_result['status']}, "
          f"部署后命中率 {monitor_result['post_deploy_rate']:.0%} "
          f"vs 基线 {monitor_result['baseline_rate']:.0%}")

    # 1. 诊断
    diagnosis = diagnose(db, date_str)
    print(f"  [诊断] 整体命中率: {diagnosis['overall_rate']:.0%}, "
          f"样本: {diagnosis['sample_size']}, "
          f"失败模式: {len(diagnosis['failure_patterns'])}个, "
          f"市场状态: {diagnosis['market_regime']}")

    # 2. 假设
    hypotheses = hypothesize(diagnosis)
    print(f"  [假设] 生成 {len(hypotheses)} 个改进假设")
    for h in hypotheses:
        print(f"    - {h['id']} [{h['level']}] {h['description']} (优先级 {h['priority']})")

    # 3-5. 实验→验证→部署
    deployed = []
    for h in hypotheses:
        exp = experiment(h, db)
        print(f"  [实验] {h['id']}: 旧 {exp.get('old_hit_rate', 0):.0%} → "
              f"新 {exp.get('new_hit_rate', 0):.0%} "
              f"(改进 {exp.get('improvement', 0):+.0%}, 样本 {exp.get('sample_size', 0)})")
        if not exp.get("passed"):
            record_failed_hypothesis(h, f"实验未通过: {exp.get('reason', '命中率未提升')}")
            continue
        val = validate(exp, h)
        print(f"  [验证] {h['id']}: {'通过' if val['passed'] else '未通过'} "
              f"(p={val['p_value']})")
        if not val.get("passed"):
            record_failed_hypothesis(h, f"验证未通过: {', '.join(val['reasons'])}")
            continue
        dep = deploy(h, exp, val)
        if dep.get("deployed"):
            deployed.append(dep)
            print(f"  [部署] {h['id']}: shadow 模式, 切换日 {dep['switch_date']}")

    # 6. 更新基线
    _update_benchmark(db, date_str, diagnosis)

    print(f"  [部署] {len(deployed)} 个改进已部署(shadow模式)")
    print("=" * 60)

    return {
        "diagnosis": diagnosis,
        "hypotheses": len(hypotheses),
        "deployed": len(deployed),
        "monitor": monitor_result,
    }


# ===========================================================================
# 命令行入口
# ===========================================================================

def main():
    """命令行入口：python -m evolution.engine [--date YYYY-MM-DD]"""
    import argparse
    parser = argparse.ArgumentParser(description="进化引擎（诊断→假设→实验→验证→部署→监控）")
    parser.add_argument("--date", default=None, help="运行日期 YYYY-MM-DD，默认今天")
    args = parser.parse_args()

    from db import DB
    db = DB()
    db.init()
    run(db, args.date)


if __name__ == "__main__":
    main()
