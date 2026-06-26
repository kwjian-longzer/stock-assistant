#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
site_builder.py - 站点构建器

在报告写入并通过校验后运行，将所有数据转换为 JSON 文件，供 GitHub Pages 网站使用。

输出目录结构:
  docs/data/
  ├── manifest.json               # 总索引
  ├── latest.json                 # 最新完整快照
  ├── archive/{date}_{type}.json  # 按日期归档
  └── history/
      ├── gold_stocks.json        # 金股历史（追加模式）
      └── heat_tracking.json     # 热度趋势（滚动窗口）

用法:
  python site_builder.py                                    # 自动检测最新报告
  python site_builder.py --date 2026-06-26 --type evening   # 指定日期和类型
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.resolve()
DATA_DIR = PROJECT_ROOT / "data"
REPORTS_DIR = PROJECT_ROOT / "reports"
SCORES_DIR = DATA_DIR / "report_scores"
DOCS_DATA_DIR = PROJECT_ROOT / "docs" / "data"
ARCHIVE_DIR = DOCS_DATA_DIR / "archive"
HISTORY_DIR = DOCS_DATA_DIR / "history"

# 报告类型映射: 英文 -> 中文
TYPE_MAP = {
    "morning": "晨报",
    "noon": "午报",
    "evening": "晚报",
    "weekly_sat": "周六复盘",
    "weekly_sun": "周日展望",
}
TYPE_MAP_REVERSE = {v: k for k, v in TYPE_MAP.items()}

# 报告标题
TITLE_MAP = {
    "morning": "多维市场研报（晨报）",
    "noon": "多维市场研报（午报）",
    "evening": "多维市场研报（晚报）",
    "weekly_sat": "多维市场研报（周六复盘）",
    "weekly_sun": "多维市场研报（周日展望）",
}

# 类型优先级（用于判断"最新"报告）
TYPE_PRIORITY = {
    "morning": 1,
    "noon": 2,
    "evening": 3,
    "weekly_sat": 4,
    "weekly_sun": 5,
}

# 热度历史滚动窗口大小（交易日）
HEAT_WINDOW = 30

# 排除的报告文件后缀（非主报告）
EXCLUDE_SUFFIXES = ("VIP信息表", "钱三强选股", "score")


# ---------------------------------------------------------------------------
# 通用工具函数
# ---------------------------------------------------------------------------

def load_json(path):
    """安全加载 JSON 文件，失败时返回空字典并打印警告"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"[警告] 文件不存在: {path}")
        return {}
    except json.JSONDecodeError as e:
        print(f"[警告] JSON 解析失败: {path} -> {e}")
        return {}


def save_json(path, data):
    """安全保存 JSON 文件（带格式化和 UTF-8 编码）"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[写入] {path}")


def read_text(path):
    """安全读取文本文件"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        print(f"[警告] 文件不存在: {path}")
        return ""


def format_index_value(close):
    """格式化指数点位：大数取整，小数保留两位"""
    if close is None:
        return ""
    if abs(close) >= 1000:
        return str(round(close))
    return f"{close:.2f}".rstrip("0").rstrip(".")


def format_change(pct_chg):
    """格式化涨跌幅，带正负号"""
    if pct_chg is None:
        return ""
    return f"{pct_chg:+.2f}%"


# ---------------------------------------------------------------------------
# 报告解析
# ---------------------------------------------------------------------------

def parse_report_markdown(md_text):
    """解析 Markdown 报告，按 H2 (##) 拆分为结构化章节

    返回:
        list[dict]: 每个元素 {"title": str, "content": str}
    """
    chapters = []
    # 按 ## 分割，跳过第一部分（H1 标题 + 引言）
    parts = re.split(r"^## ", md_text, flags=re.MULTILINE)
    for part in parts[1:]:
        lines = part.split("\n")
        title = lines[0].strip()
        content = "\n".join(lines[1:]).strip()
        # 去除章节末尾的分隔线
        content = re.sub(r"\n---\s*$", "", content).strip()
        if title:
            chapters.append({"title": title, "content": content})
    return chapters


def extract_summary(md_text):
    """从报告第一段正文提取一句话总结

    跳过 H1 标题、引用块、分隔线、H2/H3 标题和表格，取第一段正文。
    """
    lines = md_text.split("\n")
    in_code_block = False
    for line in lines:
        stripped = line.strip()

        # 跳过代码块
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        # 跳过空行、标题、引用、分隔线、表格
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith(">"):
            continue
        if stripped.startswith("---"):
            continue
        if stripped.startswith("|"):
            continue

        # 找到第一段正文
        summary = stripped
        # 截断过长内容
        if len(summary) > 120:
            summary = summary[:117] + "..."
        return summary

    return "暂无总结"


def extract_gold_stocks(report_md):
    """从报告 Markdown 中提取金股推荐

    匹配模式:
        金股N: CODE NAME [龙脉定位]
        - What: ...
        - Why:
          - 信号: ...
          - 验证: ...
          - 概率: ...
        - How: ...
        - 力度: ...
        - 时间: ...

    返回:
        list[dict]: [{"name", "code", "reason", "score", "dragon", "strategy", "time"}]
    """
    gold_stocks = []

    # 匹配金股头部行
    header_pattern = re.compile(r"金股(\d+):\s*(\d{6})\s+(.+?)\s+\[(.+?)\]")
    headers = list(header_pattern.finditer(report_md))

    for i, match in enumerate(headers):
        code = match.group(2)
        name = match.group(3).strip()
        dragon = match.group(4).strip()

        # 获取该金股的详细内容块
        start = match.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(report_md)
        detail = report_md[start:end]

        # 解析各字段
        what = _extract_gold_field(detail, "What")
        signal = _extract_gold_field(detail, "信号")
        verify = _extract_gold_field(detail, "验证")
        prob_text = _extract_gold_field(detail, "概率")
        how = _extract_gold_field(detail, "How")
        strength = _extract_gold_field(detail, "力度")
        time_horizon = _extract_gold_field(detail, "时间")

        # 构建 reason：What + 信号
        reason_parts = []
        if what:
            reason_parts.append(what)
        if signal:
            reason_parts.append(signal)
        if reason_parts:
            reason = " + ".join(reason_parts)
        elif strength:
            reason = strength
        else:
            reason = dragon

        # 截断过长的 reason
        if len(reason) > 100:
            reason = reason[:97] + "..."

        # 推导评分
        score = _derive_gold_score(prob_text, strength, dragon)

        gold_stocks.append({
            "name": name,
            "code": code,
            "reason": reason,
            "score": score,
            "dragon": dragon,
            "strategy": how,
            "time": time_horizon,
        })

    return gold_stocks


def _extract_gold_field(detail, field):
    """从金股详情块中提取指定字段内容

    顶层字段（What/How/力度/时间）匹配 '^\\s*- field: value'
    子字段（信号/验证/概率）匹配缩进的 '^\\s+- field: value'
    """
    if field in ("信号", "验证", "概率"):
        pattern = rf"^\s+-\s*{re.escape(field)}:\s*(.+)$"
    else:
        pattern = rf"^\s*-\s*{re.escape(field)}:\s*(.+)$"

    m = re.search(pattern, detail, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return ""


def _derive_gold_score(prob_text, strength_text, dragon_text):
    """从概率、力度和龙脉定位推导金股评分（0-100）

    评分逻辑:
      - 龙脉定位权重: 飞龙在天 +10, 见龙在田 +5, 潜龙在渊 0
      - 力度权重: 强推荐 80, 推荐 72, 关注 62
      - 概率调整: 提取百分比数字微调
    """
    # 基础分（力度）
    if "强推荐" in strength_text:
        base = 80
    elif "推荐" in strength_text:
        base = 72
    elif "关注" in strength_text:
        base = 62
    else:
        base = 70

    # 龙脉加成
    if "飞龙在天" in dragon_text:
        base += 10
    elif "见龙在田" in dragon_text:
        base += 5

    # 概率微调
    prob_match = re.search(r"(\d+)", prob_text)
    if prob_match:
        prob = int(prob_match.group(1))
        if prob >= 70:
            base += 3
        elif prob < 55:
            base -= 3

    return max(50, min(95, base))


def load_v4_gold_stocks(date_str):
    """从 data/gold_stocks.json 读取 v4.0 多维共振金股（扁平格式）。

    v4 gold_stocks.json 结构:
        {"date": "YYYY-MM-DD",
         "gold_stocks": [{code, name, dimensions, score, resonance,
                          dragon_net_buy, dragon_reason, vip_count, cls_titles, ...}],
         "total_candidates": int}

    转换为前端兼容结构（与 extract_gold_stocks 返回一致，并补充 v4 扩展字段：
    dimensions / resonance / catalyst / dragon_vein）。
    日期不匹配或文件缺失时返回空列表，由调用方回退到报告 Markdown 解析。
    """
    gold_path = DATA_DIR / "gold_stocks.json"
    data = load_json(gold_path)
    if not data:
        return []

    # 日期校验：仅当文件日期与目标日期一致时采用
    file_date = data.get("date", "")
    if file_date and date_str and file_date != date_str:
        print(f"[提示] gold_stocks.json 日期({file_date})与目标日期({date_str})不一致，回退到报告解析")
        return []

    gold_list = data.get("gold_stocks", [])
    if not isinstance(gold_list, list):
        return []

    result = []
    for g in gold_list:
        if not isinstance(g, dict):
            continue
        code = g.get("code", "")
        name = g.get("name", "")
        dimensions = g.get("dimensions", []) or []
        score = g.get("score", 0)
        resonance = g.get("resonance", len(dimensions))
        dragon_reason = g.get("dragon_reason", "") or g.get("dragon_vein", "")
        catalyst = g.get("catalyst", "")
        if not catalyst and g.get("cls_titles"):
            catalyst = "; ".join(g.get("cls_titles", []))[:200]

        # 构建 reason：共振维度 + 龙虎榜原因/催化剂
        reason_parts = []
        if dimensions:
            reason_parts.append("、".join(dimensions) + f"({resonance}/5)")
        if dragon_reason:
            reason_parts.append(dragon_reason)
        elif catalyst:
            reason_parts.append(catalyst)
        reason = " | ".join(reason_parts) if reason_parts else "多维共振"
        if len(reason) > 100:
            reason = reason[:97] + "..."

        result.append({
            "name": name,
            "code": code,
            "reason": reason,
            "score": score,
            "dragon": dragon_reason,      # 龙虎榜/龙脉定位
            "strategy": g.get("strategy", ""),
            "time": g.get("time", g.get("time_horizon", "")),
            # v4 扩展字段
            "dimensions": dimensions,
            "resonance": resonance,
            "catalyst": catalyst,
            "dragon_vein": dragon_reason,
        })

    if result:
        print(f"[金股] 从 gold_stocks.json 读取 v4 金股 {len(result)} 只")
    return result


# ---------------------------------------------------------------------------
# 市场数据提取
# ---------------------------------------------------------------------------

def extract_market_snapshot(data_summary):
    """从 data_summary.json 中提取市场指数、涨跌停、成交额、北向资金、板块与全球市场

    兼容 v4.0 扁平格式（顶层 indices / sectors_top / north_money / limit_up /
    dragon_tiger / insights）与 v3.x chapter 格式（chapter1.index_summary /
    chapter2.north_money / chapter4.limit_stats）。v4 优先，v3 兜底，保持向后兼容。

    返回:
        dict: {"indices": [...], "limit_up": int, "limit_down": int,
               "volume": str, "north_flow": str,
               "sectors_top": [...], "global_markets": [...]}
    """
    snapshot = {
        "indices": [],
        "limit_up": 0,
        "limit_down": 0,
        "volume": "",
        "north_flow": "",
        "sectors_top": [],
        "global_markets": [],
    }

    if not data_summary:
        print("[警告] data_summary 为空，市场数据提取失败")
        return snapshot

    # ------------------------------------------------------------------
    # 1) 指数数据：v4 顶层 indices 扁平列表 优先；v3 chapter1.index_summary 兜底
    # ------------------------------------------------------------------
    v4_indices = data_summary.get("indices")
    if isinstance(v4_indices, list) and v4_indices:
        index_list = v4_indices
    else:
        chapter1 = data_summary.get("chapter1", {})
        index_list = chapter1.get("index_summary", []) if isinstance(chapter1, dict) else []

    total_amount_yi = 0.0  # 两市成交额合计（亿元）
    for idx in index_list:
        if not isinstance(idx, dict):
            continue
        name = idx.get("name", "")
        close = idx.get("close")
        pct_chg = idx.get("pct_chg")
        amount = idx.get("amount", 0)

        snapshot["indices"].append({
            "name": name,
            "value": format_index_value(close),
            "change": format_change(pct_chg),
        })

        # 上证指数 + 深证成指 的成交额合计为两市总成交额
        if name in ("上证指数", "深证成指") and amount:
            try:
                amt = float(amount)
            except (TypeError, ValueError):
                continue
            # 成交额单位因数据源而异：
            #   - sina_realtime（盘中实时）: 元  -> /1e8 得到亿
            #   - tushare_close / tushare（收盘）: 千元 -> /1e5 得到亿
            src = str(idx.get("source", "") or "")
            if "sina" in src or "realtime" in src:
                total_amount_yi += amt / 1e8
            else:
                total_amount_yi += amt / 1e5

    if total_amount_yi > 0:
        snapshot["volume"] = f"{total_amount_yi:.0f}亿"

    # ------------------------------------------------------------------
    # 2) 涨跌停：v4 顶层 limit_up 列表（len=涨停家数）优先；v3 chapter4.limit_stats 兜底
    # ------------------------------------------------------------------
    v4_limit_up = data_summary.get("limit_up")
    if isinstance(v4_limit_up, list):
        snapshot["limit_up"] = len(v4_limit_up)
        # v4 暂无独立跌停列表；若 stats 提供更明确的涨停数则采用 stats
        stats = data_summary.get("stats", {})
        if isinstance(stats, dict):
            lu_in_stats = stats.get("limit_up_count")
            if isinstance(lu_in_stats, int) and lu_in_stats > snapshot["limit_up"]:
                snapshot["limit_up"] = lu_in_stats
    else:
        chapter4 = data_summary.get("chapter4", {})
        if isinstance(chapter4, dict):
            limit_stats = chapter4.get("limit_stats", {})
            if isinstance(limit_stats, dict):
                snapshot["limit_up"] = limit_stats.get("limit_up_count", 0)
                snapshot["limit_down"] = limit_stats.get("limit_down_count", 0)

    # ------------------------------------------------------------------
    # 3) 北向资金：v4 顶层 north_money（已是亿元）优先；v3 chapter2.north_money（万元）兜底
    # ------------------------------------------------------------------
    v4_nm = data_summary.get("north_money")
    north_val = None
    north_unit_yi = True  # v4 north_money 字段已为亿元，无需换算
    if isinstance(v4_nm, dict) and v4_nm.get("north_money") is not None:
        north_val = v4_nm.get("north_money")
    else:
        chapter2 = data_summary.get("chapter2", {})
        if isinstance(chapter2, dict):
            nm_v3 = chapter2.get("north_money", {})
            if isinstance(nm_v3, dict) and nm_v3.get("north_money") is not None:
                north_val = nm_v3.get("north_money")
                north_unit_yi = False  # v3 单位为万元

    if north_val is not None:
        try:
            north_num = float(north_val)
            if not north_unit_yi:
                north_num = north_num / 10000.0  # 万元 -> 亿元
            if north_num >= 0:
                snapshot["north_flow"] = f"净流入{north_num:.2f}亿"
            else:
                snapshot["north_flow"] = f"净流出{abs(north_num):.2f}亿"
        except (ValueError, TypeError):
            snapshot["north_flow"] = f"{north_val}"

    # ------------------------------------------------------------------
    # 4) 板块资金（v4 新增）：v4 顶层 sectors_top（net_mf_amount 已为亿元）
    # ------------------------------------------------------------------
    v4_sectors = data_summary.get("sectors_top")
    if isinstance(v4_sectors, list):
        for sec in v4_sectors:
            if not isinstance(sec, dict):
                continue
            industry = sec.get("industry", "")
            net_mf = sec.get("net_mf_amount")
            try:
                net_mf_num = float(net_mf) if net_mf is not None else None
            except (TypeError, ValueError):
                net_mf_num = None
            snapshot["sectors_top"].append({
                "name": industry,
                "net_flow": round(net_mf_num, 2) if net_mf_num is not None else None,
            })

    # ------------------------------------------------------------------
    # 5) 全球市场（v4 新增）：优先 data_summary["global"]，其次解析 insights 海外市场
    # ------------------------------------------------------------------
    snapshot["global_markets"] = _extract_global_markets(data_summary)

    return snapshot


def _extract_global_markets(data_summary):
    """提取全球市场（美股/港股/外汇/商品）行情，供前端展示。

    优先级：
      1. data_summary["global"]（v4 若直接提供结构化全球行情 dict）
      2. data_summary["insights"] 中 category == "海外市场" 的信号（解析 signal_text）

    返回:
        list[dict]: [{"name": str, "value": str, "change": str}]
    """
    if not isinstance(data_summary, dict):
        return []

    # 1) 结构化 global 字段（{名称: {price/value, chg_pct}}）
    g = data_summary.get("global")
    if isinstance(g, dict) and g:
        result = []
        for name, item in g.items():
            if not isinstance(item, dict):
                continue
            price = item.get("price")
            if price is None:
                price = item.get("value")
            chg_pct = item.get("chg_pct")
            if chg_pct is None:
                chg_pct = item.get("change_pct")
            result.append({
                "name": str(name),
                "value": format_index_value(price) if isinstance(price, (int, float)) else ("" if price is None else str(price)),
                "change": format_change(chg_pct) if isinstance(chg_pct, (int, float)) else "",
            })
        if result:
            return result

    # 2) 从 insights 海外市场信号解析
    insights = data_summary.get("insights")
    if not isinstance(insights, list):
        return []

    result = []
    for ins in insights:
        if not isinstance(ins, dict):
            continue
        if ins.get("category") != "海外市场":
            continue
        text = ins.get("signal_text", "") or ""
        # 例: "道琼斯 上涨 0.65%（+299.97点），报46247.29"
        #     "恒生指数 下跌 1.76%（-405.05点），报22671.86"
        #     "美元指数 下跌 0.13%（-0.13点），报101.33"
        m = re.match(
            r"\s*(.+?)\s+(上涨|下跌|涨|跌)\s*([+\-]?[\d.]+)%.*?报\s*([\d.]+)",
            text,
        )
        if not m:
            continue
        name = m.group(1).strip()
        direction = m.group(2)
        try:
            pct = float(m.group(3))
        except ValueError:
            continue
        if "跌" in direction and pct > 0:
            pct = -pct
        price = m.group(4)
        result.append({
            "name": name,
            "value": price,
            "change": format_change(pct),
        })
    return result


# ---------------------------------------------------------------------------
# VIP 股票提取
# ---------------------------------------------------------------------------

def extract_vip_stocks(vip_md_path):
    """从 VIP 信息表 Markdown 中提取股票发现数据

    解析 '## VIP研报搜索发现股票' 章节中的表格:
        | 序号 | 代码 | 名称 | 板块 | 行业 | 主营业务 | 搜索词命中 | 匹配分 | 来源文章 |

    返回:
        list[dict]: [{"code", "name", "board", "industry", "business",
                       "match_keywords", "match_score", "source"}]
    """
    vip_stocks = []

    if not vip_md_path or not Path(vip_md_path).exists():
        print(f"[提示] VIP 信息表不存在: {vip_md_path}，跳过 VIP 股票提取")
        return vip_stocks

    md_text = read_text(vip_md_path)
    if not md_text:
        return vip_stocks

    # 定位 '## VIP研报搜索发现股票' 章节
    section_match = re.search(
        r"##\s*VIP研报搜索发现股票(.*?)(?=^##\s|\Z)",
        md_text,
        re.DOTALL | re.MULTILINE,
    )
    if not section_match:
        print("[提示] VIP 信息表中未找到 '搜索发现股票' 章节")
        return vip_stocks

    section = section_match.group(1)

    # 解析 Markdown 表格行
    for line in section.split("\n"):
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")]
        # 去除首尾空单元格
        cells = [c for c in cells if c != ""]

        # 跳过表头和分隔行
        if len(cells) < 8:
            continue
        if cells[0] in ("序号", ":---") or cells[0].startswith("---") or cells[0].startswith(":"):
            continue
        if not cells[0].isdigit():
            continue

        # | 序号 | 代码 | 名称 | 板块 | 行业 | 主营业务 | 搜索词命中 | 匹配分 | 来源文章 |
        try:
            code = cells[1]
            name = cells[2]
            board = cells[3] if len(cells) > 3 else ""
            industry = cells[4] if len(cells) > 4 else ""
            business = cells[5] if len(cells) > 5 else ""
            match_keywords = cells[6] if len(cells) > 6 else ""
            match_score_str = cells[7] if len(cells) > 7 else "0"
            source = cells[8] if len(cells) > 8 else ""

            match_score = 0
            try:
                match_score = int(match_score_str)
            except ValueError:
                pass

            vip_stocks.append({
                "code": code,
                "name": name,
                "board": board,
                "industry": industry,
                "business": business,
                "match_keywords": match_keywords,
                "match_score": match_score,
                "source": source,
            })
        except (IndexError, ValueError) as e:
            print(f"[警告] VIP 股票行解析失败: {line} -> {e}")
            continue

    return vip_stocks


# ---------------------------------------------------------------------------
# 评分读取
# ---------------------------------------------------------------------------

def get_report_score(date, report_type):
    """读取报告评分

    评分文件路径: data/report_scores/{date}_{type_cn}_score.json
    返回: 评分（整数），读取失败返回 None
    """
    type_cn = TYPE_MAP.get(report_type, report_type)
    score_path = SCORES_DIR / f"{date}_{type_cn}_score.json"
    if not score_path.exists():
        print(f"[提示] 评分文件不存在: {score_path}")
        return None

    score_data = load_json(score_path)
    total_score = score_data.get("total_score")
    if total_score is not None:
        print(f"[评分] 报告评分: {total_score} 分")
    return total_score


# ---------------------------------------------------------------------------
# 归档 JSON 构建
# ---------------------------------------------------------------------------

def build_archive_json(date, report_type, data_summary, heat_data,
                       report_md, qsq_results, vip_stocks, score):
    """构建单个报告的完整归档 JSON

    参数:
        date: 报告日期 (YYYY-MM-DD)
        report_type: 报告类型 (英文)
        data_summary: data_summary.json 数据
        heat_data: heat_data.json 数据 (可为 None)
        report_md: 报告 Markdown 原文
        qsq_results: 钱三强选股结果数据
        vip_stocks: VIP 股票列表
        score: 报告评分

    返回:
        dict: 完整归档数据
    """
    title = TITLE_MAP.get(report_type, f"多维市场研报（{report_type}）")
    summary = extract_summary(report_md)
    chapters = parse_report_markdown(report_md)
    # v4 gold_stocks.json 优先；缺失/日期不匹配时回退到报告 Markdown 解析（v3 金股N 格式）
    gold_stocks = load_v4_gold_stocks(date)
    if not gold_stocks:
        gold_stocks = extract_gold_stocks(report_md)
    market = extract_market_snapshot(data_summary)

    # 热度数据
    heat = None
    if heat_data:
        heat = {
            "trade_dates": heat_data.get("trade_dates", []),
            "date_labels": heat_data.get("date_labels", []),
            "sectors": [],
        }
        for sector in heat_data.get("sectors", []):
            heat["sectors"].append({
                "name": sector.get("name", ""),
                "heat_series": sector.get("heat_series", []),
                "current_heat": sector.get("current_heat"),
                "lifecycle": sector.get("lifecycle", {}),
            })

    # 钱三强选股结果
    qsq_list = []
    if qsq_results:
        qsq_summary = qsq_results.get("summary", {})
        for stock in qsq_results.get("selected_stocks", []):
            qsq_list.append({
                "ts_code": stock.get("ts_code", ""),
                "name": stock.get("name", ""),
                "industry": stock.get("industry", ""),
                "close": stock.get("close"),
                "pct_chg": stock.get("pct_chg"),
                "turnover_rate": stock.get("turnover_rate"),
                "jigou_zijin": stock.get("jigou_zijin"),
                "youzi_zijin": stock.get("youzi_zijin"),
                "ema55_angle": stock.get("ema55_angle"),
            })
        qsq_list = {
            "trade_date": qsq_results.get("trade_date", ""),
            "summary": qsq_summary,
            "selected_stocks": qsq_list,
        }
    else:
        qsq_list = None

    archive = {
        "date": date,
        "type": report_type,
        "title": title,
        "summary": summary,
        "score": score,
        "market": market,
        "heat": heat,
        "gold_stocks": gold_stocks,
        "qsq_results": qsq_list,
        "vip_stocks": vip_stocks,
        "report": {
            "chapters": chapters,
            "full_md": report_md,
        },
    }

    return archive


# ---------------------------------------------------------------------------
# manifest.json 更新
# ---------------------------------------------------------------------------

def update_manifest(date, report_type, title, score, summary):
    """更新或创建 manifest.json 总索引

    参数:
        date: 报告日期
        report_type: 报告类型（英文）
        title: 报告标题
        score: 评分
        summary: 一句话总结
    """
    manifest_path = DOCS_DATA_DIR / "manifest.json"
    manifest = load_json(manifest_path)

    reports = manifest.get("reports", [])

    # 移除同日期同类型的旧条目
    reports = [
        r for r in reports
        if not (r.get("date") == date and r.get("type") == report_type)
    ]

    # 添加新条目
    reports.append({
        "date": date,
        "type": report_type,
        "title": title,
        "score": score,
        "summary": summary,
    })

    # 按日期和类型优先级排序
    reports.sort(
        key=lambda r: (
            r.get("date", ""),
            TYPE_PRIORITY.get(r.get("type", ""), 0),
        )
    )

    # 确定最新报告
    latest = reports[-1] if reports else None

    manifest = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "latest_date": latest.get("date") if latest else date,
        "latest_type": latest.get("type") if latest else report_type,
        "reports": reports,
    }

    save_json(manifest_path, manifest)
    return manifest


# ---------------------------------------------------------------------------
# 金股历史更新
# ---------------------------------------------------------------------------

def update_gold_stock_history(gold_stocks, date, report_type):
    """将金股追加到历史记录，保留已有回测数据

    参数:
        gold_stocks: 本次报告的金股列表
        date: 报告日期
        report_type: 报告类型
    """
    history_path = HISTORY_DIR / "gold_stocks.json"
    history = load_json(history_path)

    stocks = history.get("stocks", [])
    stock_map = {s.get("code"): s for s in stocks}

    for gold in gold_stocks:
        code = gold.get("code", "")
        name = gold.get("name", "")

        if code in stock_map:
            # 已存在的金股：追加推荐记录
            stock = stock_map[code]
            recommendations = stock.get("recommendations", [])

            # 检查是否已有同日期同类型的推荐
            exists = any(
                r.get("date") == date and r.get("type") == report_type
                for r in recommendations
            )
            if not exists:
                recommendations.append({
                    "date": date,
                    "type": report_type,
                    "reason": gold.get("reason", ""),
                    "score": gold.get("score", 0),
                })
                stock["recommendations"] = recommendations
                print(f"[追加] 金股 {name}({code}) 新增推荐记录")
            else:
                print(f"[跳过] 金股 {name}({code}) 今日已存在推荐记录")
        else:
            # 新金股：创建记录
            new_stock = {
                "name": name,
                "code": code,
                "first_recommended": date,
                "recommendations": [
                    {
                        "date": date,
                        "type": report_type,
                        "reason": gold.get("reason", ""),
                        "score": gold.get("score", 0),
                    }
                ],
                "backtest": {
                    "price_at_recommend": None,
                    "current_price": None,
                    "return_1d": None,
                    "return_3d": None,
                    "return_5d": None,
                    "return_10d": None,
                    "return_20d": None,
                    "max_return": None,
                    "max_drawdown": None,
                },
            }
            stocks.append(new_stock)
            stock_map[code] = new_stock
            print(f"[新增] 金股 {name}({code}) 首次推荐，日期 {date}")

    result = {
        "stocks": stocks,
        "last_updated": datetime.now().strftime("%Y-%m-%d"),
    }

    save_json(history_path, result)


# ---------------------------------------------------------------------------
# 热度历史更新
# ---------------------------------------------------------------------------

def update_heat_history(heat_data):
    """更新滚动热度追踪历史，保留最近 30 个交易日

    合并策略:
      1. 读取已有历史和新热度数据
      2. 合并交易日（去重排序，保留最后 30 个）
      3. 各板块数据按日期对齐合并（新数据优先，旧数据补充）

    参数:
        heat_data: heat_data.json 数据
    """
    history_path = HISTORY_DIR / "heat_tracking.json"

    if not heat_data:
        print("[跳过] 热度数据为空，跳过热度历史更新")
        return

    new_dates = heat_data.get("trade_dates", [])
    new_labels = heat_data.get("date_labels", [])
    new_sectors = heat_data.get("sectors", [])

    # 读取已有历史
    if history_path.exists():
        history = load_json(history_path)
        old_dates = history.get("trade_dates", [])
        old_labels = history.get("date_labels", [])
        old_sectors_list = history.get("sectors", [])
        old_sectors = {s.get("name", ""): s for s in old_sectors_list}
    else:
        old_dates = []
        old_labels = []
        old_sectors = {}

    # 构建日期 -> 标签映射
    date_label_map = {}
    for d, l in zip(old_dates, old_labels):
        date_label_map[d] = l
    for d, l in zip(new_dates, new_labels):
        date_label_map[d] = l

    # 合并交易日（去重排序，保留最后 HEAT_WINDOW 个）
    all_dates = sorted(set(old_dates + new_dates))
    all_dates = all_dates[-HEAT_WINDOW:]
    all_labels = [date_label_map.get(d, "") for d in all_dates]

    # 合并各板块数据
    merged_sectors = []
    for sector in new_sectors:
        name = sector.get("name", "")

        # 构建各序列的 日期->值 映射
        series_maps = {}
        for series_key in ("heat_series", "capital_series", "limit_series"):
            series = sector.get(series_key, [])
            series_maps[series_key] = dict(zip(new_dates, series))

        # 合并旧历史数据
        if name in old_sectors:
            old_sector = old_sectors[name]
            for series_key in ("heat_series", "capital_series", "limit_series"):
                old_series = old_sector.get(series_key, [])
                old_map = dict(zip(old_dates, old_series))
                for d, v in old_map.items():
                    if d not in series_maps[series_key]:
                        series_maps[series_key][d] = v

        # 构建对齐到 all_dates 的序列
        merged_sector = {
            "name": name,
            "current_heat": sector.get("current_heat"),
            "lifecycle": sector.get("lifecycle", {}),
        }
        for series_key in ("heat_series", "capital_series", "limit_series"):
            merged_sector[series_key] = [
                series_maps[series_key].get(d) for d in all_dates
            ]

        merged_sectors.append(merged_sector)

    result = {
        "trade_dates": all_dates,
        "date_labels": all_labels,
        "sectors": merged_sectors,
        "last_updated": datetime.now().strftime("%Y-%m-%d"),
    }

    save_json(history_path, result)
    print(f"[完成] 热度历史已更新: {len(all_dates)} 个交易日, {len(merged_sectors)} 个板块")


# ---------------------------------------------------------------------------
# 最新报告检测
# ---------------------------------------------------------------------------

def detect_latest_report():
    """自动检测最新的主报告文件

    扫描 reports/ 目录下所有 YYYY-MM-DD_*.md 文件，
    排除 VIP 信息表、钱三强选股等辅助文件，
    返回最新报告的日期和类型。

    返回:
        tuple: (date_str, report_type) 或 (None, None)
    """
    if not REPORTS_DIR.exists():
        return None, None

    candidates = []
    for f in REPORTS_DIR.glob("*.md"):
        name = f.stem  # e.g. "2026-06-25_晚报"

        # 跳过辅助文件
        if any(suffix in name for suffix in EXCLUDE_SUFFIXES):
            continue

        # 解析日期和类型
        parts = name.split("_", 1)
        if len(parts) != 2:
            continue
        date_str, type_cn = parts
        # 验证日期格式
        if not re.match(r"\d{4}-\d{2}-\d{2}", date_str):
            continue

        report_type = TYPE_MAP_REVERSE.get(type_cn)
        if report_type is None:
            print(f"[提示] 未知报告类型: {type_cn} ({f.name})，跳过")
            continue

        candidates.append((date_str, report_type, f))

    if not candidates:
        return None, None

    # 按日期和类型优先级排序，取最新
    candidates.sort(
        key=lambda x: (x[0], TYPE_PRIORITY.get(x[1], 0))
    )
    latest = candidates[-1]
    print(f"[检测] 最新报告: {latest[2].name}")
    return latest[0], latest[1]


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    """主入口：在报告校验通过后调用，生成站点 JSON 数据"""
    parser = argparse.ArgumentParser(
        description="站点构建器 - 将报告数据转换为 GitHub Pages 站点 JSON"
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="报告日期 (YYYY-MM-DD)，不指定则自动检测最新报告",
    )
    parser.add_argument(
        "--type",
        type=str,
        default=None,
        choices=list(TYPE_MAP.keys()),
        help="报告类型 (morning/noon/evening/weekly_sat/weekly_sun)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("站点构建器 启动")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 确定报告日期和类型
    date_str = args.date
    report_type = args.type

    if not date_str or not report_type:
        auto_date, auto_type = detect_latest_report()
        if not date_str:
            date_str = auto_date
        if not report_type:
            report_type = auto_type

    if not date_str or not report_type:
        print("[错误] 未能确定报告日期和类型，请使用 --date 和 --type 指定")
        sys.exit(1)

    type_cn = TYPE_MAP.get(report_type, report_type)
    title = TITLE_MAP.get(report_type, f"多维市场研报（{type_cn}）")

    print(f"\n[目标] 日期: {date_str} | 类型: {report_type} ({type_cn})")
    print(f"[标题] {title}")

    # 1. 读取 data_summary.json
    print("\n--- 步骤 1/8: 读取数据摘要 ---")
    summary_path = DATA_DIR / "data_summary.json"
    data_summary = load_json(summary_path)
    if not data_summary:
        print("[错误] data_summary.json 为空或不存在，无法继续")
        sys.exit(1)
    meta = data_summary.get("meta", {})
    trade_date = meta.get("trade_date", date_str.replace("-", ""))
    mode = meta.get("mode", report_type)
    print(f"[数据] 交易日: {trade_date} | 模式: {mode}")

    # 2. 读取 heat_data.json
    print("\n--- 步骤 2/8: 读取热度数据 ---")
    heat_path = DATA_DIR / "heat_data.json"
    heat_data = None
    if heat_path.exists():
        heat_data = load_json(heat_path)
        print(f"[数据] 热度数据: {len(heat_data.get('sectors', []))} 个板块, "
              f"{len(heat_data.get('trade_dates', []))} 个交易日")
    else:
        print(f"[跳过] 热度数据文件不存在: {heat_path}")

    # 3. 读取报告 Markdown
    print("\n--- 步骤 3/8: 读取报告原文 ---")
    report_path = REPORTS_DIR / f"{date_str}_{type_cn}.md"
    report_md = read_text(report_path)
    if not report_md:
        print(f"[错误] 报告文件不存在或为空: {report_path}")
        sys.exit(1)
    print(f"[数据] 报告字数: {len(report_md)} 字符")

    # 4. 读取钱三强选股结果
    print("\n--- 步骤 4/8: 读取钱三强选股 ---")
    qsq_path = DATA_DIR / "qian_sanqiang_results.json"
    qsq_results = None
    if qsq_path.exists():
        qsq_results = load_json(qsq_path)
        qsq_summary = qsq_results.get("summary", {})
        print(f"[数据] 三强合一: {qsq_summary.get('pass_all_three', 0)} 只, "
              f"入选: {len(qsq_results.get('selected_stocks', []))} 只")
    else:
        print(f"[跳过] 钱三强选股结果不存在: {qsq_path}")

    # 5. 读取 VIP 信息表
    print("\n--- 步骤 5/8: 读取 VIP 信息表 ---")
    vip_md_path = REPORTS_DIR / f"{date_str}_VIP信息表.md"
    vip_stocks = extract_vip_stocks(vip_md_path)
    print(f"[数据] VIP 发现股票: {len(vip_stocks)} 只")

    # 6. 读取报告评分
    print("\n--- 步骤 6/8: 读取报告评分 ---")
    score = get_report_score(date_str, report_type)

    # 7. 解析报告并构建归档 JSON
    print("\n--- 步骤 7/8: 构建归档数据 ---")
    summary_text = extract_summary(report_md)
    # v4 gold_stocks.json 优先；缺失/日期不匹配时回退到报告 Markdown 解析（v3 金股N 格式）
    gold_stocks = load_v4_gold_stocks(date_str)
    if not gold_stocks:
        gold_stocks = extract_gold_stocks(report_md)
    chapters = parse_report_markdown(report_md)
    market = extract_market_snapshot(data_summary)

    print(f"[解析] 章节数: {len(chapters)}")
    print(f"[解析] 金股数: {len(gold_stocks)}")
    print(f"[解析] 总结: {summary_text[:60]}...")
    print(f"[解析] 指数数: {len(market['indices'])}")
    print(f"[解析] 涨停: {market['limit_up']} | 跌停: {market['limit_down']}")
    print(f"[解析] 成交额: {market['volume']} | 北向: {market['north_flow']}")

    archive = build_archive_json(
        date_str, report_type, data_summary, heat_data,
        report_md, qsq_results, vip_stocks, score
    )

    # 写入归档文件
    archive_path = ARCHIVE_DIR / f"{date_str}_{report_type}.json"
    save_json(archive_path, archive)

    # 如果是最新报告，复制到 latest.json
    manifest_path = DOCS_DATA_DIR / "manifest.json"
    existing_manifest = load_json(manifest_path)
    is_latest = True
    if existing_manifest:
        for r in existing_manifest.get("reports", []):
            r_date = r.get("date", "")
            r_type = r.get("type", "")
            if r_date > date_str or (
                r_date == date_str
                and TYPE_PRIORITY.get(r_type, 0) > TYPE_PRIORITY.get(report_type, 0)
            ):
                is_latest = False
                break

    if is_latest:
        latest_path = DOCS_DATA_DIR / "latest.json"
        save_json(latest_path, archive)
        print("[完成] 已更新 latest.json（当前报告为最新）")

    # 8. 更新索引和历史
    print("\n--- 步骤 8/8: 更新索引与历史 ---")

    # 更新 manifest.json
    update_manifest(date_str, report_type, title, score, summary_text)

    # 更新金股历史
    if gold_stocks:
        update_gold_stock_history(gold_stocks, date_str, report_type)
    else:
        print("[跳过] 本报告无金股推荐，跳过金股历史更新")

    # 更新热度历史
    if heat_data:
        update_heat_history(heat_data)
    else:
        print("[跳过] 无热度数据，跳过热度历史更新")

    # 完成总结
    print("\n" + "=" * 60)
    print("站点构建 完成")
    print(f"  日期: {date_str}")
    print(f"  类型: {report_type} ({type_cn})")
    print(f"  评分: {score}")
    print(f"  章节: {len(chapters)} 个")
    print(f"  金股: {len(gold_stocks)} 只")
    print(f"  VIP股票: {len(vip_stocks)} 只")
    print(f"  归档: {archive_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
