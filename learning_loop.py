#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
learning_loop.py  —  v4.0 学习闭环（盘后预判验证 + 经验固化）
============================================================
晚报生成完成后运行，形成"预判 → 验证 → 总结"的自我学习闭环，
持续把市场反馈沉淀为可复用的经验规则。

三个核心函数:
  1. verify_predictions(db, date_str)   盘后预判验证
       读取历史 market_insight 中的 a_share_impact 预判文本，
       对比 date_str 当日实际 index_quote 指数行情，
       将"预判 vs 实际"的偏差写入 learning_record。
  2. solidify_experience(db, date_str) 经验固化
       分析最近 30 条 learning_record，按因子归纳命中率，
       提炼出类似"北向资金净流入≥50亿时大盘上涨概率70%"的规律，
       以 category="经验总结" 写回 learning_record。
  3. run(db, date_str)                 主入口
       顺序执行 verify_predictions → solidify_experience，
       并附带调用 gold_stock_backtest.run_backtest(days_filter=20)。

设计原则:
  - 仅依赖 Python 标准库 + 既有模块 (db / gold_stock_backtest)。
  - 所有外部调用（DB 查询、回测）均 try/except 包裹，局部失败不阻断整体。
  - verify_predictions / solidify_experience 均幂等：同一天重复运行不会堆积脏数据。

用法:
  python learning_loop.py                       # 验证今天
  python learning_loop.py --date 2026-06-26     # 指定日期
"""

import os
import re
import sys
import argparse
import datetime
from collections import defaultdict

# 确保能 import 同目录下的 db / gold_stock_backtest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# 方向关键词 —— 用于解析 a_share_impact 预判文本的多空倾向
# ---------------------------------------------------------------------------

# 看涨（利好/多头）关键词
BULLISH_KWS = [
    "利好", "加仓", "提振", "回流", "活跃", "偏多", "高开", "走强",
    "领涨", "升温", "强势", "反弹", "突破", "上涨", "流入", "信心",
]

# 看跌（利空/空头）关键词
BEARISH_KWS = [
    "利空", "流出", "压制", "受压", "警惕", "承压", "退潮", "偏空",
    "低开", "走弱", "降温", "抛压", "下挫", "下跌", "疲软", "谨慎", "风险",
]

# 因子分类规则（顺序敏感：先命中先归类）
FACTOR_RULES = [
    ("北向资金", ["北向", "外资"]),
    ("美股", ["美股", "标普", "纳斯达克", "道琼斯"]),
    ("港股", ["恒生", "港股"]),
    ("黄金", ["黄金"]),
    ("原油", ["原油", "石油"]),
    ("美元汇率", ["美元", "人民币"]),
    ("板块资金", ["板块资金", "净流入", "净流出"]),
    ("龙虎榜", ["龙虎榜", "净买入"]),
    ("涨停池", ["涨停"]),
    ("融资融券", ["融资余额", "杠杆资金"]),
    ("舆情", ["舆情", "电报"]),
]

# 实际涨跌幅方向判定阈值（单位:%），区间内视为"震荡"
FLAT_THRESHOLD = 0.05


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _safe_float(v, default=None):
    """安全转 float：None / 空串 / 非数字均返回 default。"""
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (ValueError, TypeError):
        return default


def parse_direction(text):
    """解析预判文本的方向倾向。

    特殊处理美元 / 人民币的反向关系（美元跌→利好A股，人民币升→利好A股），
    其余按多空关键词计数打分。

    Returns:
        "看涨" / "看跌" / "中性"
    """
    if not text:
        return "中性"

    # --- 特殊：美元指数反向 ---
    # 注意：当文本同时含"美元"与"人民币"时（如"美元走强压制人民币资产"），
    # 动词描述的是"美元"，因此美元分支须优先于人民币分支命中。
    if "美元" in text:
        if any(k in text for k in ("走弱", "下跌", "贬值", "回落", "走低")):
            return "看涨"  # 美元跌 → 利好人民币资产
        if any(k in text for k in ("走强", "上涨", "升值", "走高")):
            return "看跌"  # 美元涨 → 压制人民币资产

    # --- 特殊：人民币汇率（仅当未提及美元时） ---
    if "人民币" in text:
        if any(k in text for k in ("升值", "走强", "上涨", "走高")):
            return "看涨"
        if any(k in text for k in ("贬值", "走弱", "下跌", "走低")):
            return "看跌"

    # --- 通用关键词打分 ---
    bull = sum(1 for kw in BULLISH_KWS if kw in text)
    bear = sum(1 for kw in BEARISH_KWS if kw in text)
    if bull > bear:
        return "看涨"
    if bear > bull:
        return "看跌"
    return "中性"


def parse_actual_direction(quotes):
    """根据当日指数行情判定大盘实际方向。

    优先采用"上证指数"，缺失时取全部指数涨跌幅均值。

    Args:
        quotes: db.query_index_quote 返回的 list[dict]

    Returns:
        (direction, summary): direction ∈ {"看涨","看跌","震荡"}，
        summary 为可读的行情描述字符串。
    """
    if not quotes:
        return "震荡", "无指数行情数据"

    # 优先上证指数
    target = None
    for q in quotes:
        if q.get("name") == "上证指数":
            target = q
            break
    if target is None:
        target = quotes[0]

    pct = _safe_float(target.get("pct_chg"))
    name = target.get("name", "")

    # 目标指数无涨跌幅时，退化为全样本均值
    if pct is None:
        pcts = [_safe_float(q.get("pct_chg")) for q in quotes]
        pcts = [p for p in pcts if p is not None]
        if not pcts:
            return "震荡", "无可用涨跌幅数据"
        pct = sum(pcts) / len(pcts)
        name = "综合均值"

    close = _safe_float(target.get("close"))
    close_str = f"，收{close:.2f}" if close is not None else ""
    verb = "上涨" if pct > 0 else ("下跌" if pct < 0 else "震荡")
    summary = f"{name} {verb} {abs(pct):.2f}%{close_str}"

    if pct > FLAT_THRESHOLD:
        return "看涨", summary
    if pct < -FLAT_THRESHOLD:
        return "看跌", summary
    return "震荡", summary


def classify_factor(prediction_text):
    """根据预判文本归类影响因子（北向资金/美股/板块资金 等）。"""
    for factor, kws in FACTOR_RULES:
        if any(k in prediction_text for k in kws):
            return factor
    return "其他"


def _extract_north_amount(text):
    """从信号文本中提取北向资金净额并分桶。

    典型文本: "北向资金净流入52.3亿" / "北向资金净流出30.0亿"

    Returns:
        (amount, bucket): amount 带符号（流入为正，流出为负），
        bucket ∈ {"净流入≥50亿","净流入0~50亿","净流出0~50亿","净流出≥50亿"}。
        无法提取时返回 (None, None)。
    """
    if not text:
        return None, None
    m = re.search(r"北向资金.*?净(?:流入|流出)\s*([0-9]+(?:\.[0-9]+)?)\s*亿", text)
    if not m:
        return None, None
    amount = float(m.group(1))
    if "流出" in text:
        amount = -amount

    if amount >= 50:
        bucket = "净流入≥50亿"
    elif amount > 0:
        bucket = "净流入0~50亿"
    elif amount <= -50:
        bucket = "净流出≥50亿"
    else:
        bucket = "净流出0~50亿"
    return amount, bucket


def _factor_tip(factor, rate):
    """根据命中率给出该因子的使用建议。"""
    if rate >= 70:
        return "该因子信号可信度高，后续可重点参考。"
    if rate >= 50:
        return "该因子信号具有一定参考价值，需结合其他信号共振确认。"
    return "该因子信号失效或反向概率较高，建议降低权重或反向参考。"


def _derive_lesson(ins, pred_dir, actual_dir, hit):
    """根据单条验证结果生成经验教训文本。"""
    cat = ins.get("category", "")
    if hit is True:
        return f"【命中·{cat}】预判{pred_dir}与实际一致，该类信号有效。"
    if hit is False:
        return (f"【失误·{cat}】预判{pred_dir}但实际{actual_dir}，"
                f"需复盘信号是否被其他因素对冲。")
    return f"【未兑现·{cat}】预判{pred_dir}但实际震荡，信号方向暂未兑现。"


def _judge(pred_dir, actual_dir):
    """判定预判与实际的方向关系。

    Returns:
        (hit, gap_desc): hit ∈ {True, False, None}，gap_desc 为可读描述。
    """
    if actual_dir == "震荡":
        return None, "实际震荡，方向未明显兑现"
    if pred_dir == actual_dir:
        return True, "预判命中"
    return False, "预判与实际反向（失误）"


# ---------------------------------------------------------------------------
# DB 辅助（幂等控制）
# ---------------------------------------------------------------------------

def _prev_insight_date(db, date_str, lookback=10):
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
        print(f"  [警告] 查找历史洞见日期失败: {e}")
        return None


def _already_verified(db, date_str, source_date):
    """判断 source_date 的洞见是否已在 date_str 验证过（幂等去重）。"""
    try:
        conn = db._conn()
        cur = conn.execute(
            "SELECT COUNT(*) AS cnt FROM learning_record "
            "WHERE date=? AND category=? AND gap_analysis LIKE ?",
            (date_str, "盘后验证", f"%源洞见日期={source_date}%"),
        )
        row = cur.fetchone()
        conn.close()
        return bool(row and row["cnt"] > 0)
    except Exception:
        return False


def _clear_experience_summary(db, date_str):
    """清除当日已写入的"经验总结"记录，保证 solidify_experience 幂等。"""
    try:
        conn = db._conn()
        conn.execute(
            "DELETE FROM learning_record WHERE date=? AND category=?",
            (date_str, "经验总结"),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  [警告] 清理旧经验总结失败: {e}")


# ---------------------------------------------------------------------------
# 1. 盘后预判验证
# ---------------------------------------------------------------------------

def verify_predictions(db, date_str):
    """盘后预判验证。

    读取 date_str 前一交易日（最近有洞见的日期）的 market_insight 记录，
    将其中 a_share_impact 视为"对 date_str 的预判"，与 date_str 当日实际
    index_quote 指数行情对比，把偏差逐条写入 learning_record（category="盘后验证"）。

    Args:
        db: DB 实例
        date_str: 验证日期 YYYY-MM-DD（通常是当天，盘后运行）

    Returns:
        int: 本次写入的验证记录条数
    """
    print(f"\n=== 盘后预判验证 {date_str} ===")

    # 1. 定位可验证的历史洞见日期
    source_date = _prev_insight_date(db, date_str)
    if not source_date:
        print("  [跳过] 未找到可验证的历史洞见日期")
        return 0

    # 幂等：已验证过则跳过
    if _already_verified(db, date_str, source_date):
        print(f"  [跳过] {source_date} 的预判已于 {date_str} 验证过")
        return 0

    # 2. 读取历史洞见
    try:
        insights = db.query_insights(date=source_date)
    except Exception as e:
        print(f"  [错误] 读取 market_insight 失败: {e}")
        return 0
    if not insights:
        print(f"  [跳过] {source_date} 无洞见记录")
        return 0

    # 3. 读取当日实际指数行情
    try:
        quotes = db.query_index_quote(date=date_str)
    except Exception as e:
        print(f"  [错误] 读取 index_quote 失败: {e}")
        return 0
    if not quotes:
        print(f"  [跳过] {date_str} 无指数行情数据，无法验证")
        return 0

    actual_dir, actual_summary = parse_actual_direction(quotes)
    print(f"  [实际] {actual_summary}（方向: {actual_dir}）")

    # 4. 逐条对比并写入
    n = 0
    skipped_neutral = 0
    for ins in insights:
        impact = (ins.get("a_share_impact") or "").strip()
        if not impact:
            continue

        pred_dir = parse_direction(impact)
        if pred_dir == "中性":
            # 无方向性预判，无法验证，跳过
            skipped_neutral += 1
            continue

        hit, gap_desc = _judge(pred_dir, actual_dir)

        prediction_text = (
            f"[{ins.get('category', '')}] {ins.get('signal_text', '')} → {impact}"
        )
        actual_text = f"{actual_summary}（实际方向: {actual_dir}）"
        gap_text = f"[源洞见日期={source_date}] 预判{pred_dir}，实际{actual_dir}，{gap_desc}"
        lesson = _derive_lesson(ins, pred_dir, actual_dir, hit)

        try:
            db.upsert_learning_record({
                "date": date_str,
                "prediction": prediction_text,
                "actual": actual_text,
                "gap_analysis": gap_text,
                "lesson": lesson,
                "category": "盘后验证",
            })
            n += 1
        except Exception as e:
            print(f"  [错误] 写入学习记录失败: {e}")

    print(f"  [验证] 源日期 {source_date}，共 {len(insights)} 条洞见，"
          f"方向性预判 {n} 条已记录，中性 {skipped_neutral} 条跳过")
    return n


# ---------------------------------------------------------------------------
# 2. 经验固化
# ---------------------------------------------------------------------------

def solidify_experience(db, date_str):
    """经验固化。

    分析最近 30 条 learning_record（仅取 category="盘后验证"），
    按影响因子归纳命中率，提炼规律性结论，以 category="经验总结"
    写回 learning_record。

    产出包含:
      - 整体预判准确率
      - 各因子命中率（样本≥3）
      - 北向资金分桶规律（样本≥2，例如"净流入≥50亿时大盘上涨概率70%"）

    Args:
        db: DB 实例
        date_str: 固化日期 YYYY-MM-DD

    Returns:
        int: 本次写入的经验总结条数
    """
    print(f"\n=== 经验固化 {date_str} ===")

    try:
        records = db.query_learning_records(limit=30)
    except Exception as e:
        print(f"  [错误] 读取 learning_record 失败: {e}")
        return 0

    # 仅分析盘后验证类记录（排除历史经验总结，避免元归纳噪声）
    verify_records = [r for r in records if r.get("category") == "盘后验证"]
    if not verify_records:
        print("  [跳过] 暂无盘后验证记录可供归纳")
        return 0

    # 幂等：清除当日旧经验总结
    _clear_experience_summary(db, date_str)

    # 1. 整体准确率
    directional = []  # 有明确命中/失误判定的记录
    hits = 0
    misses = 0
    for r in verify_records:
        gap = r.get("gap_analysis", "") or ""
        if "命中" in gap and "反向" not in gap:
            hits += 1
            directional.append(r)
        elif "反向" in gap or "失误" in gap:
            misses += 1
            directional.append(r)
        # "未兑现" 不计入方向性样本

    total = len(directional)
    accuracy = (hits / total * 100) if total else 0.0

    lessons = []
    if total > 0:
        verdict = (
            "方向性预判能力较强，可继续依赖。"
            if accuracy >= 60
            else "方向性预判偏差较大，需谨慎参考，重点复盘失误案例。"
        )
        lessons.append(
            f"【整体预判准确率】近 {len(verify_records)} 条盘后验证记录中，"
            f"方向性预判 {total} 条，命中 {hits} 条，反向失误 {misses} 条，"
            f"准确率 {accuracy:.1f}%。{verdict}"
        )

    # 2. 按因子分组统计命中率
    factor_stats = defaultdict(lambda: {"total": 0, "hits": 0})
    for r in directional:
        pred = r.get("prediction", "") or ""
        factor = classify_factor(pred)
        factor_stats[factor]["total"] += 1
        if "命中" in (r.get("gap_analysis") or "") and "反向" not in (r.get("gap_analysis") or ""):
            factor_stats[factor]["hits"] += 1

    for factor, st in sorted(factor_stats.items(), key=lambda x: -x[1]["total"]):
        if st["total"] < 3:
            continue  # 样本不足，不轻易下结论
        rate = st["hits"] / st["total"] * 100
        lessons.append(
            f"【{factor}·规律】历史样本 {st['total']} 次，预判命中 {st['hits']} 次，"
            f"命中率 {rate:.0f}%。{_factor_tip(factor, rate)}"
        )

    # 3. 北向资金分桶分析（高价值规律，贴近"净流入>50亿时大盘上涨概率70%"）
    north_buckets = defaultdict(lambda: {"total": 0, "hits": 0})
    for r in directional:
        pred = r.get("prediction", "") or ""
        if "北向" not in pred and "外资" not in pred:
            continue
        _, bucket = _extract_north_amount(pred)
        if not bucket:
            continue
        north_buckets[bucket]["total"] += 1
        gap = r.get("gap_analysis", "") or ""
        if "命中" in gap and "反向" not in gap:
            north_buckets[bucket]["hits"] += 1

    for bucket, st in north_buckets.items():
        if st["total"] < 2:
            continue
        rate = st["hits"] / st["total"] * 100
        # 流入桶对应"上涨概率"，流出桶对应"下跌概率"
        prob_dir = "上涨" if "流入" in bucket else "下跌"
        lessons.append(
            f"【北向资金·{bucket}】样本 {st['total']} 次，预判命中 {st['hits']} 次，"
            f"命中率 {rate:.0f}%。即北向{bucket}时大盘{prob_dir}概率约 {rate:.0f}%。"
        )

    # 4. 写入经验总结
    n = 0
    window_desc = f"统计窗口：最近 {len(verify_records)} 条盘后验证记录"
    for lesson in lessons:
        try:
            db.upsert_learning_record({
                "date": date_str,
                "prediction": "（经验总结，无单条预判）",
                "actual": window_desc,
                "gap_analysis": "由 solidify_experience 自动归纳",
                "lesson": lesson,
                "category": "经验总结",
            })
            n += 1
        except Exception as e:
            print(f"  [错误] 写入经验总结失败: {e}")

    print(f"  [固化] 归纳出 {n} 条经验总结，已写入 learning_record")
    return n


# ---------------------------------------------------------------------------
# 3. 主入口
# ---------------------------------------------------------------------------

def run(db, date_str=None):
    """学习闭环主入口。

    顺序执行:
      1. verify_predictions  盘后预判验证
      2. solidify_experience 经验固化
      3. gold_stock_backtest.run_backtest(days_filter=20)  金股回测（try/except 包裹）

    Args:
        db: DB 实例
        date_str: 验证日期 YYYY-MM-DD，默认今天

    Returns:
        dict: {"verified": int, "lessons": int}
    """
    if date_str is None:
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")

    print("=" * 60)
    print(f"v4.0 学习闭环 启动 @ {date_str}")
    print(f"时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. 盘后预判验证
    n_verified = verify_predictions(db, date_str)

    # 2. 经验固化
    n_lessons = solidify_experience(db, date_str)

    # 3. 金股回测（外部模块，容错隔离）
    print("\n=== 金股回测（最近 20 天）===")
    try:
        from gold_stock_backtest import run_backtest
        run_backtest(days_filter=20)
    except Exception as e:
        # 回测依赖 Tushare / 历史文件，失败不影响学习闭环主流程
        print(f"  [警告] 金股回测失败，已跳过: {e}")

    print("\n" + "=" * 60)
    print("学习闭环 完成")
    print(f"  预判验证: {n_verified} 条")
    print(f"  经验固化: {n_lessons} 条")
    print("=" * 60)
    return {"verified": n_verified, "lessons": n_lessons}


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="v4.0 学习闭环（盘后预判验证 + 经验固化 + 金股回测）"
    )
    parser.add_argument(
        "--date", default=None,
        help="验证日期 YYYY-MM-DD，默认今天",
    )
    args = parser.parse_args()

    from db import DB
    db = DB()
    db.init()
    run(db, args.date)


if __name__ == "__main__":
    main()
