#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
报告质量评分系统

对股票研报进行10维度量化评分，每维0-10分，总分100分。
评分维度与用户靶标对齐：多维多源信息→结构化→推理洞见→逻辑印证→热点+金股。

用法:
    python report_quality_evaluator.py --report reports/2026-06-25_晚报.md --summary data/data_summary.json
    python report_quality_evaluator.py --report reports/2026-06-25_晚报.md --summary data/data_summary.json --html
"""

import argparse
import json
import re
import os
import sys
from pathlib import Path
from datetime import datetime


# ---------------------------------------------------------------------------
# 评分维度定义
# ---------------------------------------------------------------------------

DIMENSIONS = [
    {"id": "data_truth",      "name": "数据真实性",     "weight": 10, "desc": "无编造、无占位符、数据可追溯"},
    {"id": "signal_depth",    "name": "信号提取深度",   "weight": 10, "desc": "红色电报逐条分析、信号分级、影响判断"},
    {"id": "hotspot_id",      "name": "热点识别精度",   "weight": 10, "desc": "是否识别出当日核心热点（非罗列板块）"},
    {"id": "hotspot_cycle",   "name": "热点生命周期",   "weight": 10, "desc": "是否标注高潮/退烧/崛起状态及切换轨迹"},
    {"id": "reasoning_chain", "name": "推理链完整性",   "weight": 10, "desc": "每个结论是否有信号→验证→概率→策略"},
    {"id": "gold_multi",      "name": "金股多维验证",   "weight": 10, "desc": "是否含政策/资金/龙虎榜/涨停多维度"},
    {"id": "gold_dragon",     "name": "金股龙脉定位",   "weight": 10, "desc": "是否标注潜龙在渊/见龙在田/飞龙在天"},
    {"id": "cross_density",   "name": "交叉验证密度",   "weight": 10, "desc": "信号与数据互印证的次数"},
    {"id": "text_quality",    "name": "文本质量",       "weight": 10, "desc": "简洁/精准/可读/无冗余模板"},
    {"id": "structure",       "name": "结构完整性",     "weight": 10, "desc": "三层递进完整、无遗漏章节"},
]


# ---------------------------------------------------------------------------
# 评分逻辑
# ---------------------------------------------------------------------------

def load_report(report_path):
    with open(report_path, 'r', encoding='utf-8') as f:
        return f.read()


def load_summary(summary_path):
    with open(summary_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_red_telegraph_count(summary):
    """获取红色电报数量。兼容 v4.0 insights 与 v3.x chapter0_cls。"""
    if not isinstance(summary, dict):
        return 0
    # v3.x: chapter0_cls.cls_telegraph.red_count
    ch0 = summary.get('chapter0_cls', {})
    if isinstance(ch0, dict):
        telegraph = ch0.get('cls_telegraph', {})
        if isinstance(telegraph, dict) and 'red_count' in telegraph:
            try:
                return int(telegraph.get('red_count', 0) or 0)
            except (TypeError, ValueError):
                return 0
    # v4.0: 从 insights 中解析"财联社舆情"信号文本（如"红色0条"）
    insights = summary.get('insights')
    if isinstance(insights, list):
        for item in insights:
            if not isinstance(item, dict):
                continue
            cat = item.get('category', '') or ''
            text = item.get('signal_text', '') or ''
            if '财联社' in cat or '舆情' in cat or '电报' in text:
                m = re.search(r'红色\s*(\d+)\s*条', text)
                if not m:
                    m = re.search(r'红色(\d+)', text)
                if m:
                    try:
                        return int(m.group(1))
                    except (TypeError, ValueError):
                        return 0
    return 0


def get_north_money_net(summary):
    """获取北向资金净额（亿元）。兼容 v4.0 扁平 north_money 与 v3.x chapter2。
    v4.0 的 north_money 字段已为亿元，无需从万元转换。
    """
    if not isinstance(summary, dict):
        return None
    # v4.0 扁平结构
    nm = summary.get('north_money')
    if isinstance(nm, dict) and isinstance(nm.get('north_money'), (int, float)):
        return nm['north_money']
    # v3.x chapter2.north_money（可能为万元，数值过大时转换为亿元）
    ch2 = summary.get('chapter2', {})
    if isinstance(ch2, dict):
        nm_v3 = ch2.get('north_money')
        if isinstance(nm_v3, dict) and isinstance(nm_v3.get('north_money'), (int, float)):
            val = nm_v3['north_money']
            return val / 10000.0 if abs(val) >= 1000 else val
        if isinstance(nm_v3, (int, float)):
            return nm_v3 / 10000.0 if abs(nm_v3) >= 1000 else nm_v3
    return None


def _extract_numbers_near(report_text, keywords):
    """在关键词附近 200 字符范围内提取数值（用于数据一致性比对）。"""
    nums = []
    for kw in keywords:
        for m in re.finditer(re.escape(kw), report_text):
            seg = report_text[m.start():m.start() + 200]
            for n in re.findall(r'[\d,]+\.?\d*', seg):
                try:
                    nums.append(float(n.replace(',', '')))
                except ValueError:
                    continue
    return nums


def score_data_truth(report_text, summary):
    """维度1: 数据真实性 (0-10)"""
    score = 10
    issues = []

    # 占位符检测
    placeholders = re.findall(r'(XXX|xxx|×××|？？|待补充|TODO|TBD|待定)', report_text, re.IGNORECASE)
    if placeholders:
        score -= 5
        issues.append(f"发现{len(placeholders)}处占位符")

    # 股票代码真实性
    codes = set(re.findall(r'\b(\d{6})\b', report_text))
    # 过滤日期
    codes = {c for c in codes if not c.startswith(('2024', '2025', '2026'))}

    if codes and summary:
        valid_codes = set()
        def _extract(obj):
            if isinstance(obj, list):
                for item in obj:
                    _extract(item)
            elif isinstance(obj, dict):
                for k, v in obj.items():
                    if k in ('ts_code', 'code', 'stock_code') and isinstance(v, str):
                        valid_codes.add(v.split('.')[0])
                    else:
                        _extract(v)
        _extract(summary)

        invalid = codes - valid_codes
        if invalid:
            score -= min(5, len(invalid))
            issues.append(f"发现{len(invalid)}个不在数据中的股票代码")

    # 数据暂缺比例
    total_chars = len(report_text)
    missing_count = report_text.count("数据暂缺")
    if total_chars > 0:
        missing_ratio = missing_count / (total_chars / 1000)
        if missing_ratio > 5:
            score -= 3
            issues.append(f"'数据暂缺'出现{missing_count}次，比例过高")

    # v4.0: 北向资金净额一致性（north_money 已为亿元，直接比对，无需万元转换）
    expected_nm = get_north_money_net(summary)
    if isinstance(expected_nm, (int, float)):
        found_nums = _extract_numbers_near(report_text, ['北向资金', '北向', '外资'])
        if found_nums:
            tol = max(1.0, abs(expected_nm) * 0.01)
            if not any(abs(x - expected_nm) <= tol for x in found_nums):
                score -= 2
                closest = min(found_nums, key=lambda x: abs(x - expected_nm))
                issues.append(
                    f"北向资金净额不一致: 报告约{closest:.2f}亿，数据{expected_nm:.2f}亿"
                )

    return max(0, score), issues


def score_signal_depth(report_text, summary):
    """维度2: 信号提取深度 (0-10)"""
    score = 0
    issues = []

    # 红色电报分析深度（v4.0: 兼容 insights 与 v3.x chapter0_cls）
    red_telegraph = get_red_telegraph_count(summary) if summary else 0

    # 报告中是否逐条分析红色电报
    if red_telegraph > 0:
        # 检查报告中是否包含电报信号分析关键词
        signal_keywords = ['信号', '催化', '影响', '受益', '受损', '利好', '利空']
        signal_count = sum(report_text.count(kw) for kw in signal_keywords)
        if signal_count >= 20:
            score += 5
        elif signal_count >= 10:
            score += 3
        else:
            score += 1
            issues.append("信号分析关键词不足，可能只是罗列电报而非分析")

    # 信号分级标注
    if re.search(r'L1|L2|L3|官方|权威|传闻', report_text):
        score += 3
    else:
        issues.append("缺少信号分级标注(L1/L2/L3)")

    # 影响判断
    if any(kw in report_text for kw in ['影响方向', '利好', '利空', '受益板块', '受损板块']):
        score += 2
    else:
        issues.append("缺少影响方向判断")

    return min(10, score), issues


def score_hotspot_id(report_text, summary):
    """维度3: 热点识别精度 (0-10)"""
    score = 0
    issues = []

    # 检查是否有热点识别（非简单罗列板块名）
    hotspot_keywords = ['热点', '主线', '核心主题', '市场焦点', '今日主线']
    hotspot_count = sum(report_text.count(kw) for kw in hotspot_keywords)

    if hotspot_count >= 5:
        score += 4
    elif hotspot_count >= 2:
        score += 2
    else:
        issues.append("缺少热点识别关键词")

    # 检查是否有热点催化逻辑描述
    if any(kw in report_text for kw in ['催化', '驱动逻辑', '受益逻辑', '逻辑链']):
        score += 3
    else:
        issues.append("缺少热点催化逻辑描述")

    # 检查是否识别了3-5个热点
    hotspot_mentions = len(re.findall(r'热点[12345一二三四五\d]', report_text))
    if hotspot_mentions >= 3:
        score += 3
    elif hotspot_mentions >= 1:
        score += 1
    else:
        issues.append("未明确编号热点(3-5个)")

    return min(10, score), issues


def score_hotspot_cycle(report_text, summary):
    """维度4: 热点生命周期 (0-10)"""
    score = 0
    issues = []

    states = {'崛起': 0, '高潮': 0, '退烧': 0}
    for state in states:
        states[state] = report_text.count(state)

    found = sum(1 for v in states.values() if v > 0)
    if found >= 3:
        score += 5
    elif found >= 2:
        score += 3
    elif found >= 1:
        score += 1
        issues.append(f"仅标注了{found}种热点生命周期状态")
    else:
        issues.append("完全缺少热点生命周期标注")

    # 切换轨迹描述
    if any(kw in report_text for kw in ['切换', '轮动', '退潮', '升温', '降温', '轨迹']):
        score += 3
    else:
        issues.append("缺少热点切换轨迹描述")

    # 时间-热度曲线概念
    if any(kw in report_text for kw in ['热度', '温度', '曲线', '趋势线']):
        score += 2
    else:
        issues.append("缺少热度量化描述")

    return min(10, score), issues


def score_reasoning_chain(report_text, summary):
    """维度5: 推理链完整性 (0-10)"""
    score = 0
    issues = []

    # 检查推理链要素
    chain_elements = {
        '信号': report_text.count('信号') + report_text.count('电报') + report_text.count('VIP'),
        '验证': report_text.count('验证') + report_text.count('印证') + report_text.count('共振'),
        '概率': report_text.count('概率') + report_text.count('倾向于') + report_text.count('大概率'),
        '策略': report_text.count('策略') + report_text.count('买入') + report_text.count('止盈') + report_text.count('止损'),
    }

    complete = all(v >= 3 for v in chain_elements.values())
    if complete:
        score += 5
    else:
        missing = [k for k, v in chain_elements.items() if v < 3]
        issues.append(f"推理链要素不足: {missing}")
        score += 2

    # 推理链格式（代码块格式）
    if re.search(r'```.*金股', report_text, re.DOTALL) or '金股1:' in report_text or '金股1：' in report_text:
        score += 3
    else:
        issues.append("金股推理链未使用代码块格式")

    # 概率表述规范性
    if re.search(r'大概率|倾向于|值得警惕|尚需验证', report_text):
        score += 2
    else:
        issues.append("缺少规范的概率表述")

    return min(10, score), issues


def score_gold_multi(report_text, summary):
    """维度6: 金股多维验证 (0-10)"""
    score = 0
    issues = []

    # 多维度验证关键词
    dims = {
        '电报信号': report_text.count('电报') + report_text.count('财联社'),
        '龙虎榜': report_text.count('龙虎榜') + report_text.count('机构'),
        '资金流向': report_text.count('资金') + report_text.count('主力'),
        '涨停': report_text.count('涨停') + report_text.count('涨幅'),
        '钱三强': report_text.count('钱三强') + report_text.count('三强') + report_text.count('共振'),
        'VIP': report_text.count('VIP') + report_text.count('研报'),
    }

    active_dims = sum(1 for v in dims.values() if v >= 2)
    if active_dims >= 5:
        score += 5
    elif active_dims >= 3:
        score += 3
    else:
        issues.append(f"金股验证维度不足，仅{active_dims}个维度有引用")
        score += 1

    # 验证维度编号
    if re.search(r'①|②|③|④|⑤', report_text):
        score += 3
    elif re.search(r'1\.|2\.|3\.|4\.', report_text):
        score += 2
    else:
        issues.append("缺少验证维度编号")

    # 力度评估
    if any(kw in report_text for kw in ['强推荐', '推荐', '关注']):
        score += 2
    else:
        issues.append("缺少金股力度评估(强推荐/推荐/关注)")

    return min(10, score), issues


def score_gold_dragon(report_text, summary):
    """维度7: 金股龙脉定位 (0-10)"""
    score = 0
    issues = []

    veins = {
        '潜龙在渊': report_text.count('潜龙在渊') + report_text.count('潜龙'),
        '见龙在田': report_text.count('见龙在田') + report_text.count('见龙'),
        '飞龙在天': report_text.count('飞龙在天') + report_text.count('飞龙'),
    }

    found = sum(1 for v in veins.values() if v > 0)
    if found >= 3:
        score += 6
    elif found >= 2:
        score += 4
    elif found >= 1:
        score += 2
        issues.append(f"仅标注了{found}种龙脉阶段")
    else:
        issues.append("完全缺少龙脉定位")

    # 龙脉特征描述
    if any(kw in report_text for kw in ['未启动', '未反应', '信号已现']):
        score += 2
    if any(kw in report_text for kw in ['趋势确认', '放量', '涨幅']):
        score += 1
    if any(kw in report_text for kw in ['情绪高潮', '连板', '全网热议']):
        score += 1

    return min(10, score), issues


def score_cross_density(report_text, summary):
    """维度8: 交叉验证密度 (0-10)"""
    cross_keywords = ['验证', '印证', '交叉', '呼应', '共振', '一致', '背离', '分歧']
    density = sum(report_text.count(kw) for kw in cross_keywords)

    if density >= 20:
        return 10, []
    elif density >= 15:
        return 8, []
    elif density >= 10:
        return 6, [f"交叉验证密度{density}，建议提升至15+"]
    elif density >= 5:
        return 4, [f"交叉验证密度仅{density}，严重不足"]
    else:
        return 2, [f"交叉验证密度仅{density}，极度不足"]


def score_text_quality(report_text, summary):
    """维度9: 文本质量 (0-10)"""
    score = 10
    issues = []

    # 冗长模板检测
    template_phrases = ['如下所示', '具体如下', '值得注意的是', '需要指出的是',
                         '众所周知', '不可否认', '毫无疑问']
    template_count = sum(report_text.count(p) for p in template_phrases)
    if template_count > 5:
        score -= 2
        issues.append(f"发现{template_count}处冗余模板短语")

    # 重复段落检测（简单检测：连续重复行）
    lines = report_text.split('\n')
    repeated = sum(1 for i in range(1, len(lines)) if lines[i] == lines[i-1] and lines[i].strip())
    if repeated > 3:
        score -= 2
        issues.append(f"发现{repeated}处重复行")

    # 数据表格使用
    table_count = report_text.count('|---')
    if table_count >= 3:
        score += 0  # 已经满分
    elif table_count >= 1:
        score -= 1
        issues.append("表格使用不足，建议多用表格呈现对比数据")
    else:
        score -= 3
        issues.append("未使用表格，数据呈现不直观")

    # 平均段落长度（过长段落可读性差）
    paragraphs = [p for p in report_text.split('\n\n') if len(p.strip()) > 50]
    if paragraphs:
        avg_len = sum(len(p) for p in paragraphs) / len(paragraphs)
        if avg_len > 500:
            score -= 2
            issues.append(f"平均段落长度{avg_len:.0f}字符，过长影响可读性")

    # 禁用词检测
    banned = ['必涨', '必跌', '铁底', '无敌', '稳赚', '包涨']
    found_banned = [w for w in banned if w in report_text]
    if found_banned:
        score -= 3
        issues.append(f"发现禁用词: {found_banned}")

    return max(0, score), issues


def score_structure(report_text, summary):
    """维度10: 结构完整性 (0-10)"""
    score = 0
    issues = []

    # 三层递进结构
    layers = {
        '第一层(信号)': any(kw in report_text for kw in ['信号', '电报', '全景', '指数']),
        '第二层(热点)': any(kw in report_text for kw in ['热点', '资金', '龙虎榜', '涨停']),
        '第三层(金股)': any(kw in report_text for kw in ['金股', '策略', '止盈', '止损']),
    }
    layer_count = sum(layers.values())
    score += layer_count * 2

    if layer_count < 3:
        missing = [k for k, v in layers.items() if not v]
        issues.append(f"缺少结构层: {missing}")

    # 风险声明
    if any(kw in report_text for kw in ['风险', '免责', '不构成投资建议']):
        score += 2
    else:
        issues.append("缺少风险免责声明")

    # 字符数（v4.0: 日报 2500-4000 满分，<2500 扣分；周报 6000+ 满分）
    char_count = len(report_text)
    is_weekly = '周报' in report_text
    if is_weekly:
        if char_count >= 6000:
            score += 2
        else:
            issues.append(f"周报字符数{char_count}，低于要求6000")
    else:
        if 2500 <= char_count <= 4000:
            score += 2
        elif char_count < 2500:
            issues.append(f"日报字符数{char_count}，低于要求2500")
        else:
            issues.append(f"日报字符数{char_count}，超过建议上限4000，建议精简")

    return min(10, score), issues


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def evaluate(report_path, summary_path):
    """执行10维评分"""
    report_text = load_report(report_path)
    summary = load_summary(summary_path) if os.path.exists(summary_path) else {}

    scores = {}
    all_issues = []

    for dim in DIMENSIONS:
        func_name = f"score_{dim['id']}"
        func = globals().get(func_name)
        if func:
            score, issues = func(report_text, summary)
            scores[dim['id']] = {
                'name': dim['name'],
                'score': score,
                'weight': dim['weight'],
                'issues': issues,
            }
            all_issues.extend(issues)

    total_score = sum(s['score'] for s in scores.values())
    max_score = sum(dim['weight'] for dim in DIMENSIONS)

    return {
        'total_score': total_score,
        'max_score': max_score,
        'percentage': round(total_score / max_score * 100, 1),
        'scores': scores,
        'char_count': len(report_text),
        'report_name': Path(report_path).name,
        'eval_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def print_result(result):
    """控制台输出评分结果"""
    print('=' * 60)
    print('  报告质量评分结果')
    print('=' * 60)

    grade = 'A' if result['percentage'] >= 85 else \
            'B' if result['percentage'] >= 70 else \
            'C' if result['percentage'] >= 60 else 'D'

    print(f"\n  总分: {result['total_score']}/{result['max_score']} ({result['percentage']}%) 等级: {grade}")
    print(f"  报告: {result['report_name']}")
    print(f"  字符数: {result['char_count']}")
    print(f"  评分时间: {result['eval_time']}")

    print(f"\n  --- 各维度评分 ---")
    for dim_id, info in result['scores'].items():
        bar = '█' * info['score'] + '░' * (info['weight'] - info['score'])
        print(f"  {info['name']:<12} [{bar}] {info['score']}/{info['weight']}")
        if info['issues']:
            for issue in info['issues']:
                print(f"    → {issue}")

    print(f"\n{'='*60}")


def main():
    parser = argparse.ArgumentParser(description='报告质量评分系统')
    parser.add_argument('--report', required=True, help='报告文件路径')
    parser.add_argument('--summary', required=True, help='数据摘要文件路径')
    parser.add_argument('--html', action='store_true', help='输出HTML格式评分报告')
    args = parser.parse_args()

    result = evaluate(args.report, args.summary)
    print_result(result)

    # 保存评分结果
    scores_dir = os.path.join(os.path.dirname(os.path.abspath(args.report)), '..', 'data', 'report_scores')
    os.makedirs(scores_dir, exist_ok=True)
    score_file = os.path.join(scores_dir, f"{Path(args.report).stem}_score.json")
    with open(score_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"  评分已保存: {score_file}")

    print(f"\n{'='*60}\n")


if __name__ == '__main__':
    main()
