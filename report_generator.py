# -*- coding: utf-8 -*-
"""
report_generator.py  —  v4.0 统一报告编排器
=================================================
三阶段流水线：
  prepare:   采集 → 洞见 → 金股 → 组装 data_summary + 报告请求
  generate:  生成报告正文（Agent模式 / LLM API模式）
  finalize:  校验 → 评分 → 写DB → 刷新网站 → 推飞书 → 触发学习

用法:
    # 完整流程（Agent模式，由定时任务调度）
    python report_generator.py --date 2026-06-26 --period morning --prepare
    # ↑ 此时Agent读取 data/report_request.json 按 analysis_prompt.md 写报告到 reports/
    python report_generator.py --date 2026-06-26 --period morning --finalize --report reports/2026-06-26_晨报.md

    # LLM自动模式（config.json 配置 llm_api_key 后可用）
    python report_generator.py --date 2026-06-26 --period morning --auto
"""

import sys
import os
import json
import time
import datetime
import argparse
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PERIOD_MAP = {
    "morning": ("晨报", "morning"),
    "noon": ("午报", "noon"),
    "evening": ("晚报", "evening"),
    "weekly_sat": ("周报(六)", "weekly_sat"),
    "weekly_sun": ("周报(日)", "weekly_sun"),
}


def _run(cmd, cwd=None):
    """运行子进程并打印输出"""
    print(f"  $ {' '.join(cmd)}")
    try:
        r = subprocess.run(cmd, cwd=cwd or os.path.dirname(os.path.abspath(__file__)),
                           capture_output=True, text=True, timeout=300)
        if r.stdout.strip():
            print(r.stdout.strip()[:500])
        if r.returncode != 0 and r.stderr.strip():
            print(f"  [WARN] {r.stderr.strip()[:300]}")
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        print("  [WARN] 子进程超时")
        return False
    except Exception as e:
        print(f"  [WARN] 子进程异常: {e}")
        return False


# ---------------------------------------------------------------------------
# 阶段1: prepare
# ---------------------------------------------------------------------------

def prepare(date_str, period):
    """采集 + 洞见 + 金股，组装报告请求"""
    print(f"\n{'='*60}")
    print(f"  报告编排 - PREPARE  {date_str} / {period}")
    print(f"{'='*60}")

    base = os.path.dirname(os.path.abspath(__file__))

    # 1. 数据采集
    print("\n--- [1/4] 数据采集 ---")
    dc_period = "morning" if period in ("morning", "weekly_sat", "weekly_sun") else \
                "noon" if period == "noon" else "evening"
    _run([sys.executable, "data_collector.py", "--period", dc_period, "--date", date_str], base)

    # 2. 洞见引擎
    print("\n--- [2/4] 洞见引擎 ---")
    _run([sys.executable, "insight_engine.py", "--date", date_str, "--period", dc_period], base)

    # 3. 金股发现
    print("\n--- [3/4] 金股发现 ---")
    _run([sys.executable, "gold_stock_discovery.py", "--date", date_str], base)

    # 4. 组装报告请求
    print("\n--- [4/4] 组装报告请求 ---")
    from db import DB
    db = DB()
    db.init()

    summary_path = os.path.join(base, "data", "data_summary.json")
    with open(summary_path, "r", encoding="utf-8") as f:
        summary = json.load(f)

    gold_path = os.path.join(base, "data", "gold_stocks.json")
    gold = {}
    if os.path.exists(gold_path):
        with open(gold_path, "r", encoding="utf-8") as f:
            gold = json.load(f)

    insights = db.query_insights(date=date_str, period=dc_period)
    type_cn = PERIOD_MAP.get(period, (period, period))[0]

    request = {
        "date": date_str, "period": period, "type_cn": type_cn,
        "data_summary": summary,
        "insights": insights,
        "gold_stocks": gold.get("gold_stocks", []),
        "prompt_file": "analysis_prompt.md",
        "instructions": (
            f"请根据 analysis_prompt.md 的规则，结合 data/report_request.json 中的数据，"
            f"撰写 {date_str} 的{type_cn}。要求：2500-4000字，结构完整，"
            f"数据引用准确，洞见与金股逻辑清晰。写完后保存到 reports/{date_str}_{type_cn}.md"
        ),
    }
    req_path = os.path.join(base, "data", "report_request.json")
    with open(req_path, "w", encoding="utf-8") as f:
        json.dump(request, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n  [完成] 报告请求已生成: data/report_request.json")
    print(f"  洞见 {len(insights)} 条 | 金股 {len(gold.get('gold_stocks', []))} 只")
    print(f"  指数 {summary.get('stats', {}).get('index_count', 0)} | "
          f"板块 {summary.get('stats', {}).get('sector_count', 0)} | "
          f"龙虎榜 {summary.get('stats', {}).get('dragon_tiger_count', 0)}")
    return request


# ---------------------------------------------------------------------------
# 阶段2: generate (LLM API 模式)
# ---------------------------------------------------------------------------

def generate_auto(date_str, period):
    """使用 LLM API 自动生成报告（需要 config.json 配置 llm_api_key）"""
    print(f"\n{'='*60}")
    print(f"  报告编排 - GENERATE(AUTO)  {date_str} / {period}")
    print(f"{'='*60}")

    try:
        import settings
        llm_key = getattr(settings, "CONFIG", {}).get("llm_api_key", "")
    except Exception:
        llm_key = ""

    if not llm_key:
        print("  [跳过] 未配置 llm_api_key，请使用 Agent 模式生成报告")
        print("  提示: Agent 读取 data/report_request.json，按 analysis_prompt.md 撰写报告")
        return False

    base = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(base, "data", "report_request.json"), "r", encoding="utf-8") as f:
        request = json.load(f)
    with open(os.path.join(base, "analysis_prompt.md"), "r", encoding="utf-8") as f:
        prompt = f.read()

    import requests
    print("  调用 LLM API 生成报告...")
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {llm_key}", "Content-Type": "application/json"},
        json={"model": "deepseek-chat", "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(request, ensure_ascii=False, default=str)[:12000]},
        ], "max_tokens": 4096, "temperature": 0.7},
        timeout=120,
    )
    data = resp.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not content:
        print("  [错误] LLM 返回为空")
        return False

    type_cn = PERIOD_MAP.get(period, (period, period))[0]
    report_path = os.path.join(base, "reports", f"{date_str}_{type_cn}.md")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  [完成] 报告已生成: {report_path} ({len(content)} 字)")
    return report_path


# ---------------------------------------------------------------------------
# 阶段3: finalize
# ---------------------------------------------------------------------------

def finalize(report_path, date_str, period):
    """校验 → 评分 → 写DB → 刷新网站 → 推飞书 → 触发学习"""
    print(f"\n{'='*60}")
    print(f"  报告编排 - FINALIZE  {date_str} / {period}")
    print(f"{'='*60}")

    base = os.path.dirname(os.path.abspath(__file__))
    report_path = os.path.join(base, report_path) if not os.path.isabs(report_path) else report_path
    if not os.path.exists(report_path):
        print(f"  [错误] 报告文件不存在: {report_path}")
        return False

    with open(report_path, "r", encoding="utf-8") as f:
        content = f.read()
    char_count = len(content)
    type_cn = PERIOD_MAP.get(period, (period, period))[0]
    title = f"{date_str} 多维市场研报（{type_cn}）"
    print(f"  报告: {len(content)} 字")

    # 1. 校验
    print("\n--- [1/6] 报告校验 ---")
    quality_score = 0.0
    try:
        from validate_report import validate
        summary_path = os.path.join(base, "data", "data_summary.json")
        result = validate(report_path, summary_path)
        if isinstance(result, dict):
            status = "通过" if result.get("valid", False) else "未通过"
            print(f"  校验结果: {status}")
            if not result.get("valid", False):
                print(f"  [警告] 校验未通过: {result.get('errors', [])[:3]}")
            if result.get("warnings"):
                print(f"  [提示] 校验警告: {result['warnings'][:3]}")
    except Exception as e:
        print(f"  [跳过] 校验模块异常: {e}")

    # 2. 评分
    print("\n--- [2/6] 质量评分 ---")
    try:
        from report_quality_evaluator import evaluate
        summary_path = os.path.join(base, "data", "data_summary.json")
        result = evaluate(report_path, summary_path)
        if isinstance(result, dict):
            quality_score = result.get("total_score", result.get("score", 0))
            print(f"  质量评分: {quality_score}")
    except Exception as e:
        print(f"  [跳过] 评分模块异常: {e}")

    # 3. 写入 DB
    print("\n--- [3/6] 写入报告表 ---")
    from db import DB
    db = DB()
    db.init()
    rid = db.upsert_report({
        "date": date_str, "period": period, "title": title,
        "content": content, "char_count": char_count,
        "quality_score": quality_score,
    })
    print(f"  报告ID: {rid}")

    # 4. 刷新网站
    print("\n--- [4/6] 刷新网站数据 ---")
    _run([sys.executable, "site_builder.py", "--date", date_str, "--type", period], base)
    # 导出网站快照到 DB
    try:
        snapshot = {
            "indices": db.query_index_quote(date=date_str),
            "sectors": db.query_sector_moneyflow(date=date_str, top_n=20),
            "north_money": db.query_north_money(date=date_str),
            "gold_stocks": db.query_gold_stock(date=date_str),
            "insights": db.query_insights(date=date_str),
            "latest_report": {"title": title, "char_count": char_count,
                              "quality_score": quality_score, "date": date_str, "period": period},
        }
        db.upsert_website_snapshot(json.dumps(snapshot, ensure_ascii=False, default=str),
                                   date_str, period)
        print("  网站快照已写入 DB")
    except Exception as e:
        print(f"  [跳过] 快照写入异常: {e}")

    # 5. 推飞书
    print("\n--- [5/6] 推送飞书 ---")
    _run([sys.executable, "push_feishu.py", "--file", report_path], base)

    # 6. 触发学习（盘后）
    if period in ("evening", "weekly_sat", "weekly_sun"):
        print("\n--- [6/6] 触发学习闭环 ---")
        try:
            from learning_loop import run as learning_run
            learning_run(db, date_str)
            print("  学习闭环完成")
        except Exception as e:
            print(f"  [跳过] 学习闭环异常: {e}")
    else:
        print("\n--- [6/6] 跳过学习闭环（非盘后） ---")

    print(f"\n{'='*60}")
    print(f"  FINALIZE 完成  报告ID={rid}  评分={quality_score}  字数={char_count}")
    print(f"{'='*60}")
    return True


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="v4.0 报告编排器")
    parser.add_argument("--date", default=None)
    parser.add_argument("--period", required=True,
                        choices=["morning", "noon", "evening", "weekly_sat", "weekly_sun"])
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--finalize", action="store_true")
    parser.add_argument("--report", default=None, help="finalize 时指定报告路径")
    args = parser.parse_args()

    date_str = args.date or datetime.datetime.now().strftime("%Y-%m-%d")

    if args.prepare:
        prepare(date_str, args.period)
    elif args.auto:
        path = generate_auto(date_str, args.period)
        if path:
            finalize(path, date_str, args.period)
    elif args.finalize:
        if not args.report:
            type_cn = PERIOD_MAP.get(args.period, (args.period,))[0]
            args.report = f"reports/{date_str}_{type_cn}.md"
        finalize(args.report, date_str, args.period)
    else:
        print("请指定 --prepare / --auto / --finalize")


if __name__ == "__main__":
    main()
