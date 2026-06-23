#!/usr/bin/env python3
"""
validate_report.py - 报告校验脚本

校验生成的股票研报（晨报/午报/晚报），确保数据真实性和质量。
在校验失败时返回退出码 1，阻止推送。

用法:
    python validate_report.py --report reports/2026-06-22_晚报.md --summary data/data_summary.json
"""

import argparse
import json
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# 常量定义
# ---------------------------------------------------------------------------

PLACEHOLDER_PATTERN = re.compile(
    r'(XXX|xxx|？？|待补充|TODO|TBD|待定|placeholder)',
    re.IGNORECASE,
)

WARNING_KEYWORDS = ['数据暂缺', 'DEGRADED', '降级']

# 六章结构关键词：每个章节至少匹配一个关键词
CHAPTER_KEYWORDS = [
    # 第一章
    ['市场全景', '第一章'],
    # 第二章
    ['信号验证', '第二章'],
    # 第三章
    ['资金流向', '第三章'],
    # 第四章
    ['涨停', '第四章'],
    # 第五章
    ['策略', '第五章'],
    # 第六章
    ['风险', '免责', '第六章'],
]

# 指数名称到 data_summary 中的 key 映射
# data_summary.index_daily 的 key 通常是中文名称
INDEX_NAME_ALIASES = {
    '上证指数': ['上证指数', '上证综指', '沪指', '上证'],
    '深证成指': ['深证成指', '深成指', '深证', '深成'],
    '创业板指': ['创业板指', '创业板', '创业板指数'],
    '科创50': ['科创50', '科创板50', '科创50指数'],
    '沪深300': ['沪深300', '沪深300指数', '沪深三百'],
    '中证500': ['中证500', '中证500指数', '中证五百'],
    '上证50': ['上证50', '上证50指数', '上证五十'],
}

# 用于从报告文本中提取指数名称附近数字的正则
# 匹配：指数名称 后面跟着一些文字，然后出现数字（可能带逗号/小数点）
NUMBER_PATTERN = re.compile(r'[\d,]+\.?\d*')

# 股票代码正则：6位数字，可能带 .SH / .SZ 后缀
STOCK_CODE_PATTERN = re.compile(
    r'\b(\d{6})\b|(?:\b\d{6}\.(SH|SZ|sh|sz)\b)'
)

MIN_CHAR_COUNT = 6000

# 数据一致性允许误差（百分比）
TOLERANCE_PCT = 0.5


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def load_json(filepath: str) -> dict:
    """加载 JSON 文件，返回字典。"""
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_report(filepath: str) -> str:
    """加载报告文件，返回文本内容。"""
    with open(filepath, 'r', encoding='utf-8') as f:
        return f.read()


def extract_numbers_near_index(report_text: str, index_names: list[str]) -> list[float]:
    """
    在报告文本中搜索指数名称附近的数字。
    返回找到的所有浮点数列表。
    """
    found_numbers = []
    for name in index_names:
        # 在文本中查找该指数名称出现的位置
        for match in re.finditer(re.escape(name), report_text):
            start = match.start()
            # 从名称出现位置向后搜索 200 字符范围内的数字
            segment = report_text[start:start + 200]
            numbers = NUMBER_PATTERN.findall(segment)
            for num_str in numbers:
                try:
                    # 去掉千分位逗号
                    cleaned = num_str.replace(',', '')
                    found_numbers.append(float(cleaned))
                except ValueError:
                    continue
    return found_numbers


def extract_pct_near_index(report_text: str, index_names: list[str]) -> list[float]:
    """
    在报告文本中搜索指数名称附近的百分比数字。
    匹配类似 +1.78%、-0.43%、1.78% 等格式。
    """
    pct_pattern = re.compile(r'[+-]?\s*\d+\.?\d*%')
    found_pcts = []
    for name in index_names:
        for match in re.finditer(re.escape(name), report_text):
            start = match.start()
            segment = report_text[start:start + 300]
            pcts = pct_pattern.findall(segment)
            for pct_str in pcts:
                try:
                    cleaned = pct_str.replace('%', '').replace('+', '').replace(' ', '')
                    found_pcts.append(float(cleaned))
                except ValueError:
                    continue
    return found_pcts


def values_match(expected: float, actual: float, tolerance_pct: float = TOLERANCE_PCT) -> bool:
    """
    检查两个数值是否在允许误差范围内一致。
    tolerance_pct 是百分比误差，例如 0.5 表示允许 0.5% 的偏差。
    """
    if expected == 0:
        return abs(actual) < 0.01
    relative_error = abs(actual - expected) / abs(expected) * 100
    return relative_error <= tolerance_pct


def extract_stock_codes_from_report(report_text: str) -> list[str]:
    """
    从报告中提取所有股票代码（6位数字）。
    返回去重后的列表。
    """
    codes = set()
    for match in STOCK_CODE_PATTERN.finditer(report_text):
        code = match.group(1) if match.group(1) else match.group(0).split('.')[0]
        # 过滤掉明显不是股票代码的数字（如日期 20260622、年份等）
        if code.startswith('20') and len(code) == 8:
            continue
        if code.startswith('2026') or code.startswith('2025') or code.startswith('2024'):
            continue
        codes.add(code)
    return list(codes)


def extract_stock_codes_from_summary(summary: dict) -> set[str]:
    """
    从 data_summary 中提取所有合法的股票代码集合。
    兼容 chapter1.chapter2 格式和旧格式。
    """
    valid_codes = set()

    def _extract_from_list(data_list):
        if not isinstance(data_list, list):
            return
        for item in data_list:
            if isinstance(item, dict):
                for field in ['ts_code', 'code', 'stock_code']:
                    if field in item:
                        code = str(item[field]).split('.')[0]
                        if code.isdigit() and len(code) == 6:
                            valid_codes.add(code)

    # 遍历所有顶层 key
    for key in summary:
        data = summary[key]
        if isinstance(data, list):
            _extract_from_list(data)
        elif isinstance(data, dict):
            for sub_key, sub_data in data.items():
                if isinstance(sub_data, list):
                    _extract_from_list(sub_data)
                elif isinstance(sub_data, dict):
                    for sub2_key, sub2_data in sub_data.items():
                        if isinstance(sub2_data, list):
                            _extract_from_list(sub2_data)
                        elif isinstance(sub2_data, dict):
                            # 聚合数据如 top_inst_aggregate
                            for k, v in sub2_data.items():
                                if isinstance(v, list):
                                    _extract_from_list(v)
    return valid_codes


# ---------------------------------------------------------------------------
# 校验规则实现
# ---------------------------------------------------------------------------

def check_placeholder(report_text: str) -> tuple[bool, list[str]]:
    """红线1：检查占位符"""
    errors = []
    matches = PLACEHOLDER_PATTERN.findall(report_text)
    if matches:
        # 去重
        unique = list(set(matches))
        errors.append(f"报告中发现占位符: {', '.join(unique)}")
        return False, errors
    return True, []


def get_index_records(summary: dict) -> list[dict]:
    """从 data_summary 中提取指数记录列表。
    兼容两种格式：
    - summary['chapter1']['index_summary']（extract_summary.py 输出）
    - summary['index_daily']（原始格式）
    """
    # 优先从 chapter1.index_summary 读取
    ch1 = summary.get('chapter1', {})
    index_list = ch1.get('index_summary', [])
    if index_list and isinstance(index_list, list):
        return index_list
    # 降级：从 index_daily 读取（旧格式兼容）
    index_daily = summary.get('index_daily', {})
    if isinstance(index_daily, dict):
        result = []
        for records in index_daily.values():
            if isinstance(records, list) and records:
                result.extend(records)
        return result
    return []


def check_index_consistency(report_text: str, summary: dict) -> tuple[bool, list[str]]:
    """
    红线2：检查指数数据一致性。
    比对报告中的指数点位与 data_summary 中的 close 值。
    """
    errors = []
    index_records = get_index_records(summary)
    if not index_records:
        errors.append("data_summary 中缺少指数数据，无法校验指数一致性")
        return False, errors

    all_consistent = True

    # 构建 name -> record 映射
    for record in index_records:
        name = record.get('name', '')
        aliases = INDEX_NAME_ALIASES.get(name, [name])
        expected_close = record.get('close')

        if expected_close is None:
            continue

        # 在报告中搜索该指数附近的数字
        found_numbers = extract_numbers_near_index(report_text, aliases)

        if not found_numbers:
            continue

        close_match = any(
            values_match(expected_close, num)
            for num in found_numbers
        )

        if not close_match:
            closest = min(found_numbers, key=lambda x: abs(x - expected_close))
            errors.append(
                f"指数 [{name}] 数据不一致: "
                f"报告中最接近的值 {closest:.2f}，"
                f"实际应为 {expected_close:.2f}"
            )
            all_consistent = False

    return all_consistent, errors


def check_pct_consistency(report_text: str, summary: dict) -> tuple[bool, list[str]]:
    """
    红线3：检查涨跌幅一致性。
    比对报告中的涨跌幅百分比与 data_summary 中的 pct_chg 值。
    """
    errors = []
    index_records = get_index_records(summary)
    if not index_records:
        errors.append("data_summary 中缺少指数数据，无法校验涨跌幅一致性")
        return False, errors

    all_consistent = True

    for record in index_records:
        name = record.get('name', '')
        aliases = INDEX_NAME_ALIASES.get(name, [name])
        expected_pct = record.get('pct_chg')

        if expected_pct is None:
            continue

        found_pcts = extract_pct_near_index(report_text, aliases)

        if not found_pcts:
            continue

        pct_match = any(
            values_match(expected_pct, pct)
            for pct in found_pcts
        )

        if not pct_match:
            closest = min(found_pcts, key=lambda x: abs(x - expected_pct))
            errors.append(
                f"指数 [{name}] 涨跌幅不一致: "
                f"报告中最接近的值 {closest:.2f}%，"
                f"实际应为 {expected_pct:.2f}%"
            )
            all_consistent = False

    return all_consistent, errors


def check_stock_codes(report_text: str, summary: dict) -> tuple[bool, list[str]]:
    """红线4：检查股票代码真实性"""
    errors = []
    valid_codes = extract_stock_codes_from_summary(summary)
    report_codes = extract_stock_codes_from_report(report_text)

    if not report_codes:
        return True, []

    invalid_codes = []
    for code in report_codes:
        if code not in valid_codes:
            invalid_codes.append(code)

    if invalid_codes:
        errors.append(
            f"报告中存在不在数据中的股票代码: {', '.join(invalid_codes)}"
        )
        return False, errors

    return True, []


def check_min_length(report_text: str) -> tuple[bool, list[str]]:
    """红线5：检查最小长度"""
    char_count = len(report_text)
    if char_count < MIN_CHAR_COUNT:
        return False, [
            f"报告字符数 {char_count}，低于最小要求 {MIN_CHAR_COUNT} 字符"
        ]
    return True, []


def check_chapter_structure(report_text: str) -> tuple[bool, list[str]]:
    """红线6：检查六章结构完整性"""
    errors = []
    missing_chapters = []

    for i, keywords in enumerate(CHAPTER_KEYWORDS, start=1):
        found = False
        for kw in keywords:
            if kw in report_text:
                found = True
                break
        if not found:
            chapter_label = f"第{i}章"
            missing_chapters.append(chapter_label)

    if missing_chapters:
        errors.append(
            f"报告缺少必要章节: {', '.join(missing_chapters)}"
        )
        return False, errors

    return True, []


def check_warnings(report_text: str) -> list[str]:
    """检查警告关键词（不阻断但记录）"""
    warnings = []
    for kw in WARNING_KEYWORDS:
        if kw in report_text:
            warnings.append(f"报告中发现 '{kw}' 关键词")
    return warnings


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def validate(report_path: str, summary_path: str) -> dict:
    """
    执行所有校验规则，返回结构化结果。
    """
    # 加载文件
    report_text = load_report(report_path)
    summary = load_json(summary_path)

    errors = []
    warnings = []

    # 红线1：占位符
    placeholder_ok, placeholder_errors = check_placeholder(report_text)
    errors.extend(placeholder_errors)

    # 红线2：指数数据一致性
    index_ok, index_errors = check_index_consistency(report_text, summary)
    errors.extend(index_errors)

    # 红线3：涨跌幅一致性
    pct_ok, pct_errors = check_pct_consistency(report_text, summary)
    errors.extend(pct_errors)

    # 红线4：股票代码真实性
    stock_ok, stock_errors = check_stock_codes(report_text, summary)
    errors.extend(stock_errors)

    # 红线5：最小长度
    length_ok, length_errors = check_min_length(report_text)
    errors.extend(length_errors)

    # 红线6：六章结构
    chapter_ok, chapter_errors = check_chapter_structure(report_text)
    errors.extend(chapter_errors)

    # 警告
    warnings.extend(check_warnings(report_text))

    # 统计信息
    stats = {
        'char_count': len(report_text),
        'has_all_chapters': chapter_ok,
        'placeholder_found': not placeholder_ok,
        'data_consistency': index_ok and pct_ok,
        'stock_code_valid': stock_ok,
    }

    valid = len(errors) == 0

    return {
        'valid': valid,
        'errors': errors,
        'warnings': warnings,
        'stats': stats,
    }


def print_human_readable(result: dict):
    """在控制台输出人类可读的校验结果。"""
    print('=' * 60)
    print('  报告校验结果')
    print('=' * 60)

    status = 'PASS' if result['valid'] else 'FAIL'
    print(f'\n  校验状态: {status}')

    stats = result['stats']
    print(f'\n  --- 统计信息 ---')
    print(f'  字符数:           {stats["char_count"]}')
    print(f'  六章结构完整:     {"是" if stats["has_all_chapters"] else "否"}')
    print(f'  占位符检测:       {"发现" if stats["placeholder_found"] else "未发现"}')
    print(f'  数据一致性:       {"通过" if stats["data_consistency"] else "不一致"}')
    print(f'  股票代码有效:     {"是" if stats["stock_code_valid"] else "否"}')

    if result['errors']:
        print(f'\n  --- 错误（{len(result["errors"])} 项）---')
        for i, err in enumerate(result['errors'], 1):
            print(f'  [{i}] {err}')

    if result['warnings']:
        print(f'\n  --- 警告（{len(result["warnings"])} 项）---')
        for i, warn in enumerate(result['warnings'], 1):
            print(f'  [{i}] {warn}')

    print(f'\n{"=" * 60}')

    if result['valid']:
        print('  校验通过，报告可以推送。')
    else:
        print('  校验失败，请修复上述错误后重新校验。')
    print(f'{"=" * 60}\n')


def main():
    parser = argparse.ArgumentParser(
        description='校验生成的晚报报告，确保数据真实性和质量。'
    )
    parser.add_argument(
        '--report', required=True,
        help='报告文件路径，例如 reports/2026-06-22_晚报.md',
    )
    parser.add_argument(
        '--summary', required=True,
        help='数据摘要文件路径，例如 data/data_summary.json',
    )
    parser.add_argument(
        '--json', action='store_true',
        help='以 JSON 格式输出结果（默认同时输出人类可读格式）',
    )
    args = parser.parse_args()

    # 检查文件是否存在
    if not Path(args.report).exists():
        print(f'错误: 报告文件不存在: {args.report}', file=sys.stderr)
        sys.exit(1)

    if not Path(args.summary).exists():
        print(f'错误: 数据摘要文件不存在: {args.summary}', file=sys.stderr)
        sys.exit(1)

    # 执行校验
    result = validate(args.report, args.summary)

    # 输出结果
    if not args.json:
        print_human_readable(result)

    # 始终输出 JSON 结果到 stdout（便于其他脚本解析）
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 根据校验结果返回退出码
    sys.exit(0 if result['valid'] else 1)


if __name__ == '__main__':
    main()
