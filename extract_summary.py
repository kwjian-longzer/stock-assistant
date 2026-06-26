#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据摘要提取脚本

从 fetch_data.py 采集的 raw_data_*.json（可能24万行+）中提取关键指标，
生成一个精炼的 data_summary.json（控制在500行以内），供AI写报告时直接使用。

用法:
  python extract_summary.py                     # 默认读取 data/raw_data_latest.json
  python extract_summary.py --file data/raw_data_20260622_evening.json
  python extract_summary.py --output /tmp/summary.json

输出: data/data_summary.json
"""

import argparse
import glob
import json
import math
import os
import sys
from collections import defaultdict, Counter

# 统一配置管理：从环境变量或 config.json 读取敏感信息
from settings import get_tushare_token

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
ARCHIVE_DIR = os.path.join(DATA_DIR, "cls_telegraph_archive")


def _read_telegraph_archive():
    """读取今日电报归档的统计信息

    v2.0: 从 data/cls_telegraph_archive/YYYY-MM-DD.json 读取归档信息，
    供摘要生成时提供全天电报覆盖的时间范围标注。

    Returns:
        dict: 归档统计信息，读取失败返回空 dict
    """
    import datetime as _dt
    today_str = _dt.datetime.now().strftime('%Y-%m-%d')
    archive_path = os.path.join(ARCHIVE_DIR, f"{today_str}.json")

    if not os.path.exists(archive_path):
        return {}

    try:
        with open(archive_path, 'r', encoding='utf-8') as f:
            archive_data = json.load(f)
        return {
            'archive_date': archive_data.get('date', today_str),
            'archive_total': archive_data.get('total_count', 0),
            'archive_earliest': _ts_to_str(_min_ts(archive_data.get('items', []))),
            'archive_latest': _ts_to_str(_max_ts(archive_data.get('items', []))),
            'last_updated': archive_data.get('last_updated', ''),
        }
    except Exception:
        return {}


def _get_archive_items():
    """读取今日电报归档的完整电报列表

    Returns:
        list: 归档中的电报列表，读取失败返回空列表
    """
    import datetime as _dt
    today_str = _dt.datetime.now().strftime('%Y-%m-%d')
    archive_path = os.path.join(ARCHIVE_DIR, f"{today_str}.json")

    if not os.path.exists(archive_path):
        return []

    try:
        with open(archive_path, 'r', encoding='utf-8') as f:
            archive_data = json.load(f)
        return archive_data.get('items', [])
    except Exception:
        return []


def _ts_to_str(ts):
    """时间戳转字符串"""
    if not ts:
        return "N/A"
    try:
        import datetime as _dt
        return _dt.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return str(ts)


def _min_ts(items):
    """从电报列表中获取最早时间戳"""
    times = [it.get('time', 0) for it in items if it.get('time', 0)]
    return min(times) if times else 0


def _max_ts(items):
    """从电报列表中获取最晚时间戳"""
    times = [it.get('time', 0) for it in items if it.get('time', 0)]
    return max(times) if times else 0


def parse_sina_raw(raw_text):
    """从新浪 raw 字段中解析逗号分隔的数据"""
    if not raw_text or len(raw_text) < 10:
        return []
    try:
        data_part = raw_text.split('"')[1] if '"' in raw_text else ""
        if not data_part:
            return []
        return data_part.split(",")
    except (IndexError, ValueError):
        return []


def safe_float(val, default=0.0):
    """安全转换为浮点数"""
    try:
        if val is None or val == "" or val == "-":
            return default
        if isinstance(val, float) and math.isnan(val):
            return default
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_str(val):
    """安全转换为字符串"""
    if val is None:
        return "数据暂缺"
    return str(val)


def round2(val):
    """保留2位小数"""
    return round(safe_float(val), 2)


def find_latest_raw_file(data_dir):
    """在 data/ 目录下找到最新的 raw_data_*.json 文件"""
    pattern = os.path.join(data_dir, "raw_data_*.json")
    files = glob.glob(pattern)
    # 排除 raw_data_latest.json
    files = [f for f in files if not f.endswith("raw_data_latest.json")]
    if not files:
        return None
    # 按修改时间排序，取最新的
    files.sort(key=os.path.getmtime, reverse=True)
    return files[0]


def load_raw_data(file_path):
    """加载原始数据文件"""
    print(f"[INFO] 加载数据文件: {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"[INFO] 数据加载成功，顶层字段: {list(data.keys())}")
    return data


def extract_news_titles(news_data):
    """
    从新闻数据中提取标题列表。
    兼容多种格式:
      1. fetch_data.py 旧格式: {"status": 200, "titles": [...], ...}
      2. 新格式: {"status": 200, "preview": "<html>..."} (需要从HTML中提取)
      3. 错误格式: {"status": 403, "preview": "..."}
    """
    if not isinstance(news_data, dict):
        return "数据暂缺"

    # 检查 HTTP 状态码
    status = news_data.get("status", 0)
    if status == 403:
        return "FAILED: 403 Forbidden"

    # 格式1: 直接有 titles 字段
    if "titles" in news_data and isinstance(news_data["titles"], list):
        return news_data["titles"][:10]

    # 格式2: 有 preview 字段（HTML），需要提取标题
    if "preview" in news_data:
        import re
        html = news_data["preview"]
        titles = []

        # 提取 <title> 标签
        title_match = re.search(r'<title[^>]*>(.*?)</title>', html,
                                re.IGNORECASE | re.DOTALL)
        if title_match:
            titles.append(title_match.group(1).strip())

        # 提取 og:title
        og_match = re.search(
            r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\'](.*?)["\']',
            html, re.IGNORECASE)
        if og_match:
            t = og_match.group(1).strip()
            if t and t not in titles:
                titles.append(t)

        # 提取 meta description
        desc_match = re.search(
            r'<meta[^>]*name=["\']description["\'][^>]*content=["\'](.*?)["\']',
            html, re.IGNORECASE)
        if desc_match:
            t = desc_match.group(1).strip()
            if t and len(t) > 10 and t not in titles:
                titles.append(t)

        # 提取 h1/h2/h3 标签
        for pattern in [r'<h1[^>]*>(.*?)</h1>',
                        r'<h2[^>]*>(.*?)</h2>',
                        r'<h3[^>]*>(.*?)</h3>']:
            matches = re.findall(pattern, html, re.IGNORECASE | re.DOTALL)
            for m in matches:
                clean = re.sub(r'<[^>]+>', '', m).strip()
                if clean and len(clean) > 4 and clean not in titles:
                    titles.append(clean)

        # 提取含 title/headline 的 class
        tag_pattern = (
            r'<(?:div|span|a|li)[^>]*class=["\'][^"\']*(?:title|headline|news|'
            r'heading)["\'][^>]*>(.*?)</(?:div|span|a|li)>'
        )
        matches = re.findall(tag_pattern, html, re.IGNORECASE | re.DOTALL)
        for m in matches:
            clean = re.sub(r'<[^>]+>', '', m).strip()
            if clean and len(clean) > 4 and clean not in titles:
                titles.append(clean)

        if titles:
            return titles[:10]

    # 检查是否有 error
    if "error" in news_data:
        return f"FAILED: {news_data['error']}"

    return "数据暂缺"


# ---------------------------------------------------------------------------
# 第零章: 财联社信源扫描（核心信源，推理链起点）
# ---------------------------------------------------------------------------

def extract_chapter0_cls(data):
    """提取财联社数据摘要 - 作为研报的核心信源
    
    财联社五大数据源对应的分析维度:
    - 电报(加红): 世界正在发生什么 → 即时信号
    - 投资日历: 未来要发生什么 → 预期事件
    - 深度头条: 编辑认为最重要的信息 → 市场焦点
    - 首页: 市场在关注什么 → 热点方向
    - VIP文章: 交易员都在看哪些热门标的 → 机构视角
    """
    chapter = {}

    # --- 0.1 财联社电报（24小时加红）---
    cls_telegraph = data.get("cls_telegraph", {})
    if isinstance(cls_telegraph, dict) and "items" in cls_telegraph:
        items = cls_telegraph["items"]
        # 分离红色重要电报和普通电报
        red_items = [it for it in items if it.get("is_red")]
        all_stocks = []
        for it in items:
            stocks = it.get("stocks", [])
            if isinstance(stocks, list):
                all_stocks.extend(stocks)

        # 统计股票提及频次
        from collections import Counter
        stock_counter = Counter(all_stocks)
        hot_stocks = [{"name": s, "mentions": c} for s, c in stock_counter.most_common(20) if s]

        # v2.0: 读取电报归档，获取全天覆盖信息
        archive_info = cls_telegraph.get("archive", {})
        if not archive_info:
            # 尝试直接读取归档文件
            archive_info = _read_telegraph_archive()

        # v2.0: 从归档中补充全天红色电报（归档可能比本次采集覆盖更长时间段）
        archive_all_items = _get_archive_items()
        if archive_all_items:
            archive_red = [it for it in archive_all_items if it.get("is_red")]
            # 补充本次采集中没有的归档红色电报
            existing_red_titles = {it.get("title", "") for it in red_items}
            extra_red = [it for it in archive_red
                         if it.get("title", "") not in existing_red_titles]
            all_red = red_items + extra_red
            # 按时间排序，取最近30条
            all_red.sort(key=lambda x: x.get("time", 0), reverse=True)
        else:
            all_red = red_items

        chapter["cls_telegraph"] = {
            "total_count": cls_telegraph.get("count", len(items)),
            "red_count": len(all_red),
            "red_items": [
                {
                    "title": it.get("title", ""),
                    "content": it.get("content", ""),
                    "stocks": it.get("stocks", []),
                    "time": it.get("time", 0),
                }
                for it in all_red[:30]  # 最多30条红色电报
            ],
            "all_items_sample": [
                {
                    "title": it.get("title", ""),
                    "content": it.get("content", "")[:100],
                    "is_red": it.get("is_red", False),
                    "stocks": it.get("stocks", []),
                    "time": it.get("time", 0),
                }
                for it in items[:50]  # 前50条作为样本
            ],
            "hot_stocks": hot_stocks,
            # v2.0: 归档信息（全天覆盖统计）
            "archive": archive_info if archive_info else "数据暂缺",
            "source": "cls_api",
        }
    else:
        chapter["cls_telegraph"] = "数据暂缺"

    # --- 0.2 财联社深度头条 ---
    cls_pages = data.get("cls_pages", {})
    if isinstance(cls_pages, dict):
        # 深度头条
        depth = cls_pages.get("深度头条", {})
        if isinstance(depth, dict) and "articles" in depth:
            chapter["cls_depth"] = {
                "articles": depth["articles"][:20],
                "article_count": depth.get("article_count", 0),
                "source": depth.get("source", "browser_scrape"),
            }
        else:
            chapter["cls_depth"] = "数据暂缺"

        # VIP文章
        vip = cls_pages.get("VIP文章", {})
        if isinstance(vip, dict) and "articles" in vip:
            # 提取VIP文章中提到的股票（兼容API的related_stock和浏览器的stocks）
            vip_stocks = []
            for art in vip["articles"]:
                # API格式: related_stock 字段
                stocks_str = art.get("related_stock", "") or art.get("stocks", "")
                if stocks_str:
                    # 按逗号或空格分割（兼容中英文逗号）
                    for s in stocks_str.replace("相关股票：", "").replace("相关股票:", "").replace(",", "，").split("，"):
                        s = s.strip()
                        if s and len(s) < 10:
                            vip_stocks.append(s)
            vip_stock_counter = Counter(vip_stocks)
            vip_hot_stocks = [{"name": s, "mentions": c} for s, c in vip_stock_counter.most_common(15) if s]
            # 如果API已提供hot_stocks，优先使用
            if vip.get("hot_stocks"):
                vip_hot_stocks = vip["hot_stocks"][:15]

            chapter["cls_vip"] = {
                "articles": vip["articles"][:20],
                "article_count": vip.get("article_count", 0),
                "hot_stocks": vip_hot_stocks,
                "source": vip.get("source", "browser_scrape"),
            }
        else:
            chapter["cls_vip"] = "数据暂缺"

        # 投资日历（兼容API的events键和浏览器的articles键）
        calendar = cls_pages.get("投资日历", {})
        if isinstance(calendar, dict):
            cal_events = calendar.get("events", calendar.get("articles", []))
            if cal_events:
                chapter["cls_calendar"] = {
                    "events": cal_events[:30],
                    "event_count": calendar.get("event_count", calendar.get("article_count", 0)),
                    "source": calendar.get("source", "browser_scrape"),
                }
            else:
                chapter["cls_calendar"] = "数据暂缺"
        else:
            chapter["cls_calendar"] = "数据暂缺"

        # 首页
        homepage = cls_pages.get("首页", {})
        if isinstance(homepage, dict) and "articles" in homepage:
            chapter["cls_homepage"] = {
                "titles": [a.get("title", "") for a in homepage["articles"][:20]],
                "article_count": homepage.get("article_count", 0),
                "source": homepage.get("source", "browser_scrape"),
            }
        else:
            chapter["cls_homepage"] = "数据暂缺"
    else:
        chapter["cls_depth"] = "数据暂缺"
        chapter["cls_vip"] = "数据暂缺"
        chapter["cls_calendar"] = "数据暂缺"
        chapter["cls_homepage"] = "数据暂缺"

    return {"chapter0_cls": chapter}


# ---------------------------------------------------------------------------
# 第一章: 大盘概览
# ---------------------------------------------------------------------------

def extract_chapter1(data):
    """提取第一章需要的数据: 指数、美股、港股、外汇商品、新闻"""
    chapter = {}

    # --- 1.1 A股指数摘要 ---
    index_daily = data.get("index_daily", {})
    index_summary = []
    index_code_map = {
        "上证指数": "000001.SH",
        "深证成指": "399001.SZ",
        "创业板指": "399006.SZ",
        "科创50": "000688.SH",
        "沪深300": "000300.SH",
        "中证500": "000905.SH",
        "上证50": "000016.SH",
    }
    for name, code in index_code_map.items():
        records = index_daily.get(name, [])
        if records:
            latest = records[0]  # 第一条即为最新交易日
            index_summary.append({
                "name": name,
                "ts_code": code,
                "close": safe_float(latest.get("close")),
                "pct_chg": round2(latest.get("pct_chg")),
                "vol": safe_float(latest.get("vol")),
                "amount": safe_float(latest.get("amount")),
                "high": safe_float(latest.get("high")),
                "low": safe_float(latest.get("low")),
                "pre_close": safe_float(latest.get("pre_close")),
                "source": "tushare",
            })
        else:
            index_summary.append({
                "name": name,
                "ts_code": code,
                "close": "数据暂缺",
                "pct_chg": "数据暂缺",
                "vol": "数据暂缺",
                "amount": "数据暂缺",
                "high": "数据暂缺",
                "low": "数据暂缺",
                "pre_close": "数据暂缺",
                "source": "tushare",
            })
    chapter["index_summary"] = index_summary

    # --- 1.2 美股盘前期货 ---
    us_premarket = data.get("us_premarket", {})
    us_premarket_summary = {}
    futures_map = {
        "道琼斯期货": "道琼斯期货",
        "纳斯达克期货": "纳斯达克期货",
        "标普期货": "标普期货",
    }
    for display_name, key in futures_map.items():
        item = us_premarket.get(key, {})
        if isinstance(item, dict) and "error" not in item:
            price = safe_float(item.get("price"))
            pre_close = safe_float(item.get("pre_close"))
            # fetch_data.py 的期货解析有bug: pre_close 为 None，change_pct 实际存的是 pre_close
            # 从 raw 字段正确解析
            if pre_close == 0.0 and "raw" in item:
                parts = parse_sina_raw(item["raw"])
                # 新浪期货格式: [0]=当前价, [1]=涨跌额(空), [2]=昨收, [3]=开盘, [4]=最高, [5]=最低
                if len(parts) >= 3:
                    pre_close = safe_float(parts[2])
            # 始终从 price 和 pre_close 手动计算 change 和 change_pct
            change = 0.0
            change_pct = 0.0
            if price != 0.0 and pre_close != 0.0:
                change = round(price - pre_close, 4)
                change_pct = round(change / pre_close * 100, 4)
            us_premarket_summary[display_name] = {
                "price": safe_str(price) if price != 0.0 else "数据暂缺",
                "change": safe_str(change) if change != 0.0 else "数据暂缺",
                "change_pct": safe_str(change_pct) if change_pct != 0.0 else "数据暂缺",
                "pre_close": safe_str(pre_close) if pre_close != 0.0 else "数据暂缺",
                "source": "sina_http",
            }
        else:
            us_premarket_summary[display_name] = "数据暂缺"
    chapter["us_premarket"] = us_premarket_summary

    # --- 1.3 美股收盘指数 ---
    us_close_summary = {}
    close_map = {
        "道琼斯": "道琼斯_收盘",
        "纳斯达克": "纳斯达克_收盘",
        "标普500": "标普500_收盘",
    }
    for display_name, key in close_map.items():
        item = us_premarket.get(key, {})
        if isinstance(item, dict) and "error" not in item:
            price = safe_float(item.get("price"))
            change = safe_float(item.get("change"))
            change_pct = safe_float(item.get("change_pct"))
            # 如果 change/change_pct 为 None 或 0，尝试从 raw 字段解析
            if (change == 0.0 or change_pct == 0.0) and "raw" in item:
                parts = parse_sina_raw(item["raw"])
                # 格式: "名称,当前点位,涨跌额,涨跌幅%"
                if len(parts) >= 4:
                    if change == 0.0:
                        change = safe_float(parts[2])
                    if change_pct == 0.0:
                        change_pct = safe_float(parts[3])
            us_close_summary[display_name] = {
                "price": safe_str(price) if price != 0.0 else "数据暂缺",
                "change": safe_str(change) if change != 0.0 else "数据暂缺",
                "change_pct": safe_str(change_pct) if change_pct != 0.0 else "数据暂缺",
                "source": "sina_http",
            }
        else:
            us_close_summary[display_name] = "数据暂缺"
    chapter["us_close"] = us_close_summary

    # --- 1.4 港股指数 ---
    hk_index = data.get("hk_index", {})
    hk_summary = {}
    for hk_name in ["恒生指数", "恒生科技"]:
        item = hk_index.get(hk_name, {})
        if isinstance(item, dict) and "error" not in item:
            price = safe_float(item.get("price"))
            change = safe_float(item.get("change"))
            change_pct = safe_float(item.get("change_pct"))
            # 如果 change/change_pct 为 None 或 0，尝试从 raw 字段解析
            if (change == 0.0 or change_pct == 0.0) and "raw" in item:
                parts = parse_sina_raw(item["raw"])
                # 格式: "名称,当前价,涨跌额,涨跌幅%"
                if len(parts) >= 4:
                    if change == 0.0:
                        change = safe_float(parts[2])
                    if change_pct == 0.0:
                        change_pct = safe_float(parts[3])
            hk_summary[hk_name] = {
                "price": safe_str(price) if price != 0.0 else "数据暂缺",
                "change": safe_str(change) if change != 0.0 else "数据暂缺",
                "change_pct": safe_str(change_pct) if change_pct != 0.0 else "数据暂缺",
                "source": "sina_http",
            }
        else:
            hk_summary[hk_name] = "数据暂缺"
    chapter["hk_index"] = hk_summary

    # --- 1.5 外汇商品 ---
    fx_commodity = data.get("fx_commodity", {})
    fx_summary = {}

    # 美元指数: DINIW 格式，fetch_data.py 已分开获取
    usd_item = fx_commodity.get("美元指数", {})
    if isinstance(usd_item, dict) and "price" in usd_item and "error" not in usd_item:
        price = usd_item.get("price")
        change = usd_item.get("change")
        change_pct = usd_item.get("change_pct")
        fx_summary["美元指数"] = {
            "price": str(price) if price else "数据暂缺",
            "change": str(change) if change else "数据暂缺",
            "change_pct": str(change_pct) if change_pct else "数据暂缺",
            "source": "sina_http",
        }
    else:
        fx_summary["美元指数"] = "数据暂缺"

    # 在岸人民币
    cny_item = fx_commodity.get("在岸人民币", {})
    if isinstance(cny_item, dict) and "price" in cny_item and "error" not in cny_item:
        price = cny_item.get("price")
        change = cny_item.get("change")
        change_pct = cny_item.get("change_pct")
        fx_summary["在岸人民币"] = {
            "price": str(price) if price else "数据暂缺",
            "change": str(change) if change else "数据暂缺",
            "change_pct": str(change_pct) if change_pct else "数据暂缺",
            "source": "sina_http",
        }
    else:
        fx_summary["在岸人民币"] = "数据暂缺"

    # 黄金
    gold_item = fx_commodity.get("黄金", {})
    if isinstance(gold_item, dict) and "error" not in gold_item:
        price = gold_item.get("price")
        change = gold_item.get("change")
        change_pct = gold_item.get("change_pct")
        fx_summary["黄金"] = {
            "price": str(price) if price else "数据暂缺",
            "change": str(change) if change else "数据暂缺",
            "change_pct": str(change_pct) if change_pct else "数据暂缺",
            "source": "sina_http",
        }
    else:
        fx_summary["黄金"] = "数据暂缺"

    # 原油
    oil_item = fx_commodity.get("原油", {})
    if isinstance(oil_item, dict) and "error" not in oil_item:
        price = oil_item.get("price")
        change = oil_item.get("change")
        change_pct = oil_item.get("change_pct")
        fx_summary["原油"] = {
            "price": str(price) if price else "数据暂缺",
            "change": str(change) if change else "数据暂缺",
            "change_pct": str(change_pct) if change_pct else "数据暂缺",
            "source": "sina_http",
        }
    else:
        fx_summary["原油"] = "数据暂缺"

    chapter["fx_commodity"] = fx_summary

    # --- 1.6 新闻头条 ---
    news_headlines = data.get("news_headlines", {})
    news_summary = {}
    for source_name in ["上海证券报", "证券时报", "人民日报"]:
        source_data = news_headlines.get(source_name, {})
        titles = extract_news_titles(source_data)
        news_summary[source_name] = titles
    chapter["news_headlines"] = news_summary

    return {"chapter1": chapter}


# ---------------------------------------------------------------------------
# 第二章: 龙虎榜与资金动向
# ---------------------------------------------------------------------------

def extract_chapter2(data):
    """提取第二章需要的数据: 机构买卖、北向资金、融资融券、龙虎榜个股"""
    chapter = {}

    # --- 2.1 机构净买入/卖出 TOP5 ---
    top_inst = data.get("top_inst", [])
    if top_inst:
        # 按 ts_code + side 聚合机构净买卖
        inst_by_code = defaultdict(lambda: {"buy": 0.0, "sell": 0.0, "net_buy": 0.0})
        name_map = {}
        reason_map = {}

        for item in top_inst:
            ts_code = item.get("ts_code", "")
            name = item.get("exalter", "")
            if ts_code and "name" not in inst_by_code[ts_code]:
                # 尝试从 top_list 获取股票名称
                inst_by_code[ts_code]["name"] = ""
            net_buy = safe_float(item.get("net_buy", 0))
            inst_by_code[ts_code]["net_buy"] += net_buy
            inst_by_code[ts_code]["buy"] += safe_float(item.get("buy", 0))
            inst_by_code[ts_code]["sell"] += safe_float(item.get("sell", 0))
            if ts_code not in reason_map:
                reason_map[ts_code] = item.get("reason", "")

        # 从 top_list 获取股票名称映射
        top_list = data.get("top_list", [])
        for item in top_list:
            ts_code = item.get("ts_code", "")
            stock_name = item.get("name", "")
            if ts_code and stock_name:
                name_map[ts_code] = stock_name

        # 排序: 净买入 TOP5
        sorted_by_net = sorted(
            inst_by_code.items(),
            key=lambda x: x[1]["net_buy"],
            reverse=True
        )

        top5_buy = []
        top5_sell = []
        for ts_code, agg in sorted_by_net:
            entry = {
                "ts_code": ts_code,
                "name": name_map.get(ts_code, "数据暂缺"),
                "net_buy": round(agg["net_buy"], 2),
                "reason": reason_map.get(ts_code, ""),
                "source": "tushare",
            }
            if len(top5_buy) < 5 and agg["net_buy"] > 0:
                top5_buy.append(entry)
            elif len(top5_sell) < 5 and agg["net_buy"] < 0:
                top5_sell.append(entry)
            if len(top5_buy) >= 5 and len(top5_sell) >= 5:
                break

        chapter["top_inst_aggregate"] = {
            "机构净买入TOP5": top5_buy,
            "机构净卖出TOP5": top5_sell,
        }
    else:
        chapter["top_inst_aggregate"] = {
            "机构净买入TOP5": "数据暂缺",
            "机构净卖出TOP5": "数据暂缺",
        }

    # --- 2.2 沪深港通 ---
    hsgt = data.get("hsgt", [])
    if hsgt and len(hsgt) > 0:
        record = hsgt[0]
        chapter["north_money"] = {
            "north_money": safe_str(record.get("north_money", "数据暂缺")),
            "ggt_ss": safe_str(record.get("ggt_ss", "数据暂缺")),
            "ggt_sz": safe_str(record.get("ggt_sz", "数据暂缺")),
            "hgt": safe_str(record.get("hgt", "数据暂缺")),
            "sgt": safe_str(record.get("sgt", "数据暂缺")),
            "south_money": safe_str(record.get("south_money", "数据暂缺")),
            "note": "单位：万元",
            "source": "tushare",
        }
    else:
        chapter["north_money"] = "数据暂缺"

    # --- 2.3 融资融券 ---
    margin = data.get("margin", [])
    if margin and len(margin) > 0:
        # 取前5条作为摘要
        chapter["margin"] = margin[:5]
        for item in chapter["margin"]:
            item["source"] = "tushare"
    else:
        chapter["margin"] = "数据暂缺"

    # --- 2.4 龙虎榜个股 TOP10 (按涨跌幅绝对值) ---
    if top_list:
        # 先去重（同一只股票可能因不同原因出现多次）
        seen = {}
        for item in top_list:
            ts_code = item.get("ts_code", "")
            if ts_code not in seen:
                seen[ts_code] = item

        # 按涨跌幅绝对值排序
        deduped = list(seen.values())
        deduped.sort(
            key=lambda x: abs(safe_float(x.get("pct_change", 0))),
            reverse=True
        )

        top10 = []
        for item in deduped[:10]:
            top10.append({
                "ts_code": item.get("ts_code", ""),
                "name": item.get("name", "数据暂缺"),
                "close": safe_float(item.get("close")),
                "pct_change": round2(item.get("pct_change")),
                "reason": item.get("reason", ""),
                "source": "tushare",
            })
        chapter["top_list_stocks"] = top10
    else:
        chapter["top_list_stocks"] = "数据暂缺"

    return {"chapter2": chapter}


# ---------------------------------------------------------------------------
# 第三章: 资金流向分析
# ---------------------------------------------------------------------------

def extract_chapter3(data):
    """提取第三章需要的数据: 主力资金流向汇总、大小单流向"""
    chapter = {}

    moneyflow = data.get("moneyflow", [])
    if not moneyflow:
        chapter["moneyflow_aggregate"] = "数据暂缺"
        chapter["big_small_order_flow"] = "数据暂缺"
        return {"chapter3": chapter}

    # --- 3.1 资金流向汇总 ---
    total_net_buy = 0.0
    total_buy_elg = 0.0
    total_sell_elg = 0.0
    total_buy_lg = 0.0
    total_sell_lg = 0.0
    total_buy_md = 0.0
    total_sell_md = 0.0
    total_buy_sm = 0.0
    total_sell_sm = 0.0

    # 行业汇总: 用 ts_code 前缀推断市场/板块
    # SH=上海主板, SZ=深圳主板, 300xxx=创业板, 688xxx=科创板
    industry_flow = defaultdict(lambda: {"net_inflow": 0.0, "count": 0})

    for item in moneyflow:
        net_mf = safe_float(item.get("net_mf_amount", 0))
        total_net_buy += net_mf

        total_buy_elg += safe_float(item.get("buy_elg_amount", 0))
        total_sell_elg += safe_float(item.get("sell_elg_amount", 0))
        total_buy_lg += safe_float(item.get("buy_lg_amount", 0))
        total_sell_lg += safe_float(item.get("sell_lg_amount", 0))
        total_buy_md += safe_float(item.get("buy_md_amount", 0))
        total_sell_md += safe_float(item.get("sell_md_amount", 0))
        total_buy_sm += safe_float(item.get("buy_sm_amount", 0))
        total_sell_sm += safe_float(item.get("sell_sm_amount", 0))

        # 按市场分类
        ts_code = item.get("ts_code", "")
        if ts_code.endswith(".SH"):
            code_num = ts_code.split(".")[0]
            if code_num.startswith("688"):
                market = "科创板"
            elif code_num.startswith("000"):
                market = "上证指数"  # 上证指数本身
            else:
                market = "沪市主板"
        elif ts_code.endswith(".SZ"):
            code_num = ts_code.split(".")[0]
            if code_num.startswith("300"):
                market = "创业板"
            elif code_num.startswith("00"):
                market = "深市主板"
            else:
                market = "深市其他"
        else:
            market = "其他"

        industry_flow[market]["net_inflow"] += net_mf
        industry_flow[market]["count"] += 1

    # 行业净流入 TOP5 / 净流出 TOP5
    sorted_industries = sorted(
        industry_flow.items(),
        key=lambda x: x[1]["net_inflow"],
        reverse=True
    )
    top_inflow = [
        {
            "market": name,
            "net_inflow": round(info["net_inflow"], 2),
            "stock_count": info["count"],
        }
        for name, info in sorted_industries[:5]
    ]
    top_outflow = [
        {
            "market": name,
            "net_inflow": round(info["net_inflow"], 2),
            "stock_count": info["count"],
        }
        for name, info in sorted_industries[-5:]
        if info["net_inflow"] < 0
    ]
    # 如果净流出不足5个，用 "数据暂缺" 补齐
    while len(top_outflow) < 5:
        top_outflow.append("数据暂缺")

    chapter["moneyflow_aggregate"] = {
        "total_net_buy_amount": round(total_net_buy, 2),
        "total_net_buy_unit": "万元",
        "top_net_inflow_industries": top_inflow,
        "top_net_outflow_industries": top_outflow,
        "note": "moneyflow数据为个股级别，行业汇总基于ts_code前缀推断(沪市主板/深市主板/创业板/科创板)",
        "source": "tushare",
    }

    # --- 3.2 大小单流向 ---
    chapter["big_small_order_flow"] = {
        "超大单净流入": round(total_buy_elg - total_sell_elg, 2),
        "大单净流入": round(total_buy_lg - total_sell_lg, 2),
        "中单净流入": round(total_buy_md - total_sell_md, 2),
        "小单净流入": round(total_buy_sm - total_sell_sm, 2),
        "unit": "万元",
        "note": "从moneyflow中buy_elg_amount/sell_elg_amount等字段汇总",
        "source": "tushare",
    }

    return {"chapter3": chapter}


# ---------------------------------------------------------------------------
# 第四章: 涨跌停与市场情绪
# ---------------------------------------------------------------------------

def extract_chapter4(data):
    """提取第四章需要的数据: 涨跌停统计、市场涨跌家数"""
    chapter = {}

    limit_list = data.get("limit_list", [])
    daily_basic = data.get("daily_basic", [])

    # --- 4.1 涨跌停统计 ---
    limit_up_stocks = []
    limit_down_stocks = []
    limit_source = "tushare_limit_list"

    if isinstance(limit_list, list) and len(limit_list) > 0:
        # 正常涨跌停数据
        for item in limit_list:
            limit_up_stocks.append({
                "ts_code": item.get("ts_code", ""),
                "name": item.get("name", "数据暂缺"),
                "close": safe_float(item.get("close")),
                "pct_chg": round2(item.get("pct_chg")),
                "industry": item.get("industry", "数据暂缺"),
                "source": "tushare",
            })
        # 注意: limit_list 中涨跌停混在一起，需要根据 up/down 或 pct_chg 区分
        # tushare limit_list 有 limit 字段: 'U' 或 'D'
        limit_up_stocks = [
            s for s in limit_up_stocks
            if limit_list[next(
                i for i, it in enumerate(limit_list)
                if it.get("ts_code") == s["ts_code"]
            )].get("limit") == "U"
        ] if any(it.get("limit") for it in limit_list) else limit_up_stocks

        limit_up_count = len(limit_up_stocks)
        limit_down_count = len(limit_list) - limit_up_count

    elif isinstance(limit_list, dict):
        # 降级数据 (from daily_basic fallback)
        limit_source = "DEGRADED: 从daily_basic推断(pct_chg>=9.8%)"
        limit_up_count = limit_list.get("limit_up_count", 0)
        limit_down_count = limit_list.get("limit_down_count", 0)

        for item in limit_list.get("limit_up_sample", []):
            limit_up_stocks.append({
                "ts_code": item.get("ts_code", ""),
                "name": item.get("name", "数据暂缺"),
                "close": safe_float(item.get("close")),
                "pct_chg": round2(item.get("pct_chg")),
                "industry": "数据暂缺",
                "source": "tushare",
                "note": "DEGRADED: 从daily_basic推断",
            })
        for item in limit_list.get("limit_down_sample", []):
            limit_down_stocks.append({
                "ts_code": item.get("ts_code", ""),
                "name": item.get("name", "数据暂缺"),
                "close": safe_float(item.get("close")),
                "pct_chg": round2(item.get("pct_chg")),
                "industry": "数据暂缺",
                "source": "tushare",
                "note": "DEGRADED: 从daily_basic推断",
            })
    else:
        # limit_list 为空，尝试从 daily_basic 推断
        limit_source = "DEGRADED: 从daily_basic推断(pct_chg>=9.8%)"
        limit_up_count = 0
        limit_down_count = 0

    chapter["limit_stats"] = {
        "limit_up_count": limit_up_count,
        "limit_down_count": limit_down_count,
        "source": limit_source,
    }
    chapter["limit_up_stocks"] = limit_up_stocks[:30]  # 最多30只
    chapter["limit_down_stocks"] = limit_down_stocks[:30]

    # --- 4.2 每日指标统计 ---
    if daily_basic:
        total_stocks = len(daily_basic)
        up_count = 0
        down_count = 0
        flat_count = 0
        turnover_sum = 0.0
        turnover_count = 0
        limit_up_from_basic = 0

        for item in daily_basic:
            # daily_basic 没有 pct_chg 字段，需要从 close 和其他字段推断
            # 但实际上 daily_basic 只有 close，没有 pct_chg
            # 所以这里只能统计换手率等
            tr = safe_float(item.get("turnover_rate"))
            if tr > 0:
                turnover_sum += tr
                turnover_count += 1

        avg_turnover = round(turnover_sum / turnover_count, 4) if turnover_count > 0 else 0

        # 如果 limit_list 为空，从 daily_basic 推断涨停数
        # 但 daily_basic 没有 pct_chg，所以无法推断
        # 标注为数据暂缺
        chapter["daily_basic_stats"] = {
            "total_stocks": total_stocks,
            "up_count": "数据暂缺",
            "down_count": "数据暂缺",
            "flat_count": "数据暂缺",
            "avg_turnover_rate": avg_turnover,
            "limit_up_count_from_basic": "数据暂缺",
            "note": "daily_basic不含pct_chg字段，涨跌家数需从其他数据源获取",
            "source": "tushare",
        }
    else:
        chapter["daily_basic_stats"] = "数据暂缺"

    return {"chapter4": chapter}


# ---------------------------------------------------------------------------
# 第六章: 数据质量报告
# ---------------------------------------------------------------------------

def extract_chapter6(data):
    """提取第六章需要的数据: 数据质量报告"""
    chapter = {}

    # 从原始数据中提取 data_quality 和 data_quality_report
    data_quality = data.get("data_quality", {})
    data_quality_report = data.get("data_quality_report", {})

    if data_quality:
        chapter["data_quality_report"] = {
            "data_sources": data_quality,
            "quality_report": data_quality_report if data_quality_report else "原始数据中未包含 data_quality_report",
            "note": "从 raw_data 中提取的数据质量信息",
        }
    else:
        chapter["data_quality_report"] = "数据暂缺"

    # 补充: 摘要生成时的数据可用性检查
    availability = {}
    availability["index_daily"] = bool(data.get("index_daily", {}))
    availability["us_premarket"] = bool(data.get("us_premarket", {}))
    availability["hk_index"] = bool(data.get("hk_index", {}))
    availability["fx_commodity"] = bool(data.get("fx_commodity", {}))
    availability["news_headlines"] = bool(data.get("news_headlines", {}))
    availability["cls_telegraph"] = bool(data.get("cls_telegraph", {}).get("items"))
    availability["cls_pages"] = bool(data.get("cls_pages"))
    availability["moneyflow"] = bool(data.get("moneyflow", []))
    availability["top_list"] = bool(data.get("top_list", []))
    availability["top_inst"] = bool(data.get("top_inst", []))
    availability["margin"] = bool(data.get("margin", []))
    availability["hsgt"] = bool(data.get("hsgt", []))
    availability["limit_list"] = bool(data.get("limit_list", []))
    availability["daily_basic"] = bool(data.get("daily_basic", []))
    chapter["data_availability"] = availability

    return {"chapter6": chapter}


# ---------------------------------------------------------------------------
# 钱三强选股共振分析
# ---------------------------------------------------------------------------

def extract_chapter_qsq(data):
    """提取钱三强选股结果并生成共振分析数据

    读取 data/qian_sanqiang_results.json，对选出的股票与财联社电报、龙虎榜、
    涨停列表、资金流向进行多源交叉验证，生成共振分析数据。

    共振维度（共4项）:
      - in_cls_telegraph: 是否出现在财联社电报热门股票中（按名称匹配）
      - in_top_list: 是否出现在龙虎榜（按 ts_code 匹配）
      - in_limit_up: 是否在涨停列表中（兼容 list/dict 两种格式）
      - in_moneyflow_positive: 资金流向是否净流入（net_mf_amount > 0）
      - resonance_count: 以上4项中满足几项
    """
    qsq_file = os.path.join(DATA_DIR, "qian_sanqiang_results.json")

    # 文件不存在时优雅返回
    if not os.path.isfile(qsq_file):
        return {"chapter_qsq": "数据暂缺"}

    try:
        with open(qsq_file, "r", encoding="utf-8") as f:
            qsq_data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[WARN] 读取钱三强结果失败: {e}")
        return {"chapter_qsq": "数据暂缺"}

    # --- 构建交叉验证索引 ---
    # 1. 财联社电报热门股票（stocks 字段为股票名称列表，按名称匹配）
    cls_telegraph = data.get("cls_telegraph", {})
    cls_stock_names = set()
    if isinstance(cls_telegraph, dict):
        items = cls_telegraph.get("items", [])
        if isinstance(items, list):
            for it in items:
                stocks = it.get("stocks", [])
                if isinstance(stocks, list):
                    for s in stocks:
                        if s:
                            cls_stock_names.add(s)

    # 2. 龙虎榜股票（按 ts_code 匹配）
    top_list = data.get("top_list", [])
    top_list_codes = set()
    if isinstance(top_list, list):
        for item in top_list:
            ts_code = item.get("ts_code", "")
            if ts_code:
                top_list_codes.add(ts_code)

    # 3. 涨停列表（兼容 list 和 dict 两种格式）
    limit_list = data.get("limit_list", [])
    limit_up_codes = set()
    if isinstance(limit_list, list):
        for item in limit_list:
            # 仅统计涨停（limit == 'U'）；若无 limit 字段则全部计入
            if item.get("limit") in ("U", None, ""):
                ts_code = item.get("ts_code", "")
                if ts_code:
                    limit_up_codes.add(ts_code)
    elif isinstance(limit_list, dict):
        # 降级格式: limit_up_sample 中 ts_code 无交易所后缀
        for item in limit_list.get("limit_up_sample", []):
            ts_code = item.get("ts_code", "")
            if ts_code:
                limit_up_codes.add(ts_code)

    # 4. 资金流向净流入（按 ts_code 匹配，net_mf_amount > 0）
    moneyflow = data.get("moneyflow", [])
    moneyflow_positive_codes = set()
    if isinstance(moneyflow, list):
        for item in moneyflow:
            if safe_float(item.get("net_mf_amount", 0)) > 0:
                ts_code = item.get("ts_code", "")
                if ts_code:
                    moneyflow_positive_codes.add(ts_code)

    # --- 共振分析辅助函数 ---
    def _normalize_code(ts_code):
        """提取 ts_code 的数字部分（去掉交易所后缀 .SH/.SZ）"""
        if not ts_code:
            return ""
        return ts_code.split(".")[0]

    # 预构建数字部分索引，兼容有无后缀两种情况
    top_list_codes_num = {_normalize_code(c) for c in top_list_codes}
    limit_up_codes_num = {_normalize_code(c) for c in limit_up_codes}
    moneyflow_positive_codes_num = {
        _normalize_code(c) for c in moneyflow_positive_codes
    }

    def _analyze_resonance(stock):
        ts_code = stock.get("ts_code", "")
        name = stock.get("name", "")
        code_num = _normalize_code(ts_code)

        in_cls = bool(name) and name in cls_stock_names
        in_top = (ts_code in top_list_codes) or (code_num in top_list_codes_num)
        in_limit = (ts_code in limit_up_codes) or (code_num in limit_up_codes_num)
        in_money = (
            (ts_code in moneyflow_positive_codes)
            or (code_num in moneyflow_positive_codes_num)
        )

        count = sum([in_cls, in_top, in_limit, in_money])

        return {
            "in_cls_telegraph": in_cls,
            "in_top_list": in_top,
            "in_limit_up": in_limit,
            "in_moneyflow_positive": in_money,
            "resonance_count": count,
        }

    # --- 处理三强合一股票（取前20只）---
    selected_stocks = qsq_data.get("selected_stocks", [])[:20]
    selected_result = []
    for stock in selected_stocks:
        entry = dict(stock)
        entry.update(_analyze_resonance(stock))
        selected_result.append(entry)

    # --- 处理两强股票（取前30只）---
    two_of_three = qsq_data.get("two_of_three_stocks", [])[:30]
    two_of_three_result = []
    for stock in two_of_three:
        entry = dict(stock)
        entry.update(_analyze_resonance(stock))
        two_of_three_result.append(entry)

    # --- 统计汇总 ---
    qsq_summary = qsq_data.get("summary", {})
    summary_stats = {
        "three_strong_count": len(selected_stocks),
        "two_of_three_count": len(two_of_three),
        "raw_summary": qsq_summary,
        "three_strong_pass_cls": sum(
            1 for s in selected_result if s["in_cls_telegraph"]),
        "three_strong_pass_top_list": sum(
            1 for s in selected_result if s["in_top_list"]),
        "three_strong_pass_limit_up": sum(
            1 for s in selected_result if s["in_limit_up"]),
        "three_strong_pass_moneyflow": sum(
            1 for s in selected_result if s["in_moneyflow_positive"]),
        "two_of_three_pass_cls": sum(
            1 for s in two_of_three_result if s["in_cls_telegraph"]),
        "two_of_three_pass_top_list": sum(
            1 for s in two_of_three_result if s["in_top_list"]),
        "two_of_three_pass_limit_up": sum(
            1 for s in two_of_three_result if s["in_limit_up"]),
        "two_of_three_pass_moneyflow": sum(
            1 for s in two_of_three_result if s["in_moneyflow_positive"]),
    }

    # --- 行业统计（按选出股票数量取前10）---
    industry_counter = Counter()
    for stock in selected_stocks:
        industry = stock.get("industry", "")
        if industry:
            industry_counter[industry] += 1
    top_industries = [
        {"industry": ind, "count": cnt}
        for ind, cnt in industry_counter.most_common(10)
    ]

    return {
        "chapter_qsq": {
            "trade_date": qsq_data.get("trade_date", "数据暂缺"),
            "selected_stocks": selected_result,
            "two_of_three_stocks": two_of_three_result,
            "summary": summary_stats,
            "top_industries": top_industries,
            "note": "钱三强选股共振分析: 对选出股票与财联社电报/龙虎榜/涨停列表/资金流向进行交叉验证",
            "source": "qian_sanqiang_results.json + raw_data 交叉验证",
        }
    }


# ---------------------------------------------------------------------------
# VIP信息表（v2.0: 研报催化概念结构化提取）
# ---------------------------------------------------------------------------

def extract_chapter_vip(data):
    """提取VIP信息表

    v2.0: 从 fetch_data.py 的VIP提取结果中读取结构化信息表。
    如果raw_data中已包含vip_info（fetch_data.py调用vip_extractor生成），
    直接使用（即使vip_stocks为空，也包含article_list和catalyst_themes）；
    否则尝试从VIP文章中现场提取。

    Args:
        data: raw_data 字典

    Returns:
        dict: {"chapter_vip": vip_info_table}
    """
    # 1. 检查 raw_data 中是否已有 vip_info（v2格式，含article_list）
    vip_info = data.get("vip_info", None)

    if vip_info and isinstance(vip_info, dict) and vip_info.get("total_articles", 0) > 0:
        stocks_count = len(vip_info.get("vip_stocks", []))
        print(f"  [VIP] 从raw_data读取VIP信息表: {stocks_count} 只股票, "
              f"{vip_info.get('total_articles', 0)} 篇文章")
        return {"chapter_vip": vip_info}

    # 2. 如果没有预提取的VIP信息表，从cls_pages的VIP文章中现场提取
    cls_pages = data.get("cls_pages", {})
    if isinstance(cls_pages, dict):
        vip_data = cls_pages.get("VIP文章", {})
        if isinstance(vip_data, dict) and vip_data.get("articles"):
            vip_articles = vip_data["articles"]
            print(f"  [VIP] 从VIP文章现场提取: {len(vip_articles)} 篇文章")
            try:
                from vip_extractor import extract_vip_info
                # 尝试获取Tushare pro实例
                pro = None
                try:
                    import tushare as ts
                    ts.set_token(get_tushare_token())
                    pro = ts.pro_api()
                except Exception:
                    pass

                vip_table = extract_vip_info(vip_articles, pro=pro)
                # v2: 无论是否匹配到股票，都返回完整结构（含article_list）
                return {"chapter_vip": vip_table}
            except Exception as e:
                print(f"  [WARN] VIP信息提取失败: {e}")
                return {"chapter_vip": "数据暂缺"}
        else:
            return {"chapter_vip": "数据暂缺"}
    else:
        return {"chapter_vip": "数据暂缺"}


# ---------------------------------------------------------------------------
# 周报摘要提取（v2.0）
# ---------------------------------------------------------------------------

def extract_weekly_summary(data):
    """从周报数据中提取周报摘要

    v2.0: 周报模式专用，聚合本周每日报告、电报归档、最新选股结果。

    Args:
        data: raw_data_weekly.json 的数据

    Returns:
        dict: 周报摘要
    """
    import datetime as _dt
    summary_time = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    summary = {
        "meta": {
            "source_file": "raw_data_weekly.json",
            "mode": "weekly",
            "fetch_time": data.get("fetch_time", ""),
            "week_start": data.get("week_start", ""),
            "week_end": data.get("week_end", ""),
            "summary_time": summary_time,
        },
    }

    # 1. 本周每日报告汇总
    daily_reports = data.get("daily_reports", {})
    report_summary = []
    total_reports = 0
    for date_str, reports in sorted(daily_reports.items()):
        for report_type, info in reports.items():
            report_summary.append({
                "date": date_str,
                "type": report_type,
                "content_length": info.get("content_length", 0),
                "preview": info.get("preview", "")[:200],
            })
            total_reports += 1

    summary["weekly_reports"] = {
        "total_reports": total_reports,
        "reports": report_summary,
        "note": f"本周共{total_reports}篇报告，包含每日晨报/午报/晚报的前500字预览",
    }
    print(f"  [周报] 本周报告: {total_reports} 篇")

    # 2. 本周电报归档汇总
    telegraph_archive = data.get("telegraph_archive", {})
    telegraph_summary = []
    total_telegraph = 0
    total_red = 0
    from collections import Counter
    weekly_hot_stocks = Counter()

    for date_str, info in sorted(telegraph_archive.items()):
        count = info.get("total_count", 0)
        red = info.get("red_count", 0)
        telegraph_summary.append({
            "date": date_str,
            "total": count,
            "red": red,
        })
        total_telegraph += count
        total_red += red
        for stock in info.get("hot_stocks", []):
            weekly_hot_stocks[stock["name"]] += stock.get("mentions", 1)

    summary["weekly_telegraph"] = {
        "total_count": total_telegraph,
        "total_red": total_red,
        "daily_breakdown": telegraph_summary,
        "weekly_hot_stocks": [
            {"name": s, "mentions": c}
            for s, c in weekly_hot_stocks.most_common(30)
        ],
        "note": f"本周共归档{total_telegraph}条电报，其中{total_red}条红色重要电报",
    }
    print(f"  [周报] 本周电报: {total_telegraph} 条, 红色: {total_red} 条")

    # 3. 最新数据摘要（周五收盘数据）
    latest_summary = data.get("latest_summary", {})
    summary["latest_daily_summary"] = latest_summary
    print(f"  [周报] 最新摘要交易日: {latest_summary.get('meta', {}).get('trade_date', 'N/A')}")

    # 4. 最新钱三强选股结果
    latest_qsq = data.get("latest_qsq", {})
    summary["latest_qsq"] = latest_qsq
    print(f"  [周报] 钱三强选股: {latest_qsq.get('selected_stocks_count', 0)} 只三强合一股票")

    # 5. 周末实时数据（v2.0: 周末消息面+外围市场）
    weekend_realtime = data.get("weekend_realtime", {})
    weekend_summary = {}

    # 5.1 周末财联社电报
    wk_cls_telegraph = weekend_realtime.get("cls_telegraph", {})
    if isinstance(wk_cls_telegraph, dict) and wk_cls_telegraph.get("items"):
        wk_items = wk_cls_telegraph["items"]
        wk_red = [it for it in wk_items if it.get("is_red")]
        weekend_summary["cls_telegraph"] = {
            "count": wk_cls_telegraph.get("count", len(wk_items)),
            "red_count": len(wk_red),
            "red_items": [
                {"title": it.get("title", ""), "content": it.get("content", "")[:150],
                 "stocks": it.get("stocks", [])}
                for it in wk_red[:20]
            ],
            "archive": wk_cls_telegraph.get("archive", {}),
        }
        print(f"  [周报] 周末电报: {len(wk_items)} 条, 红色 {len(wk_red)} 条")
    else:
        weekend_summary["cls_telegraph"] = "数据暂缺"

    # 5.2 周末财联社页面
    wk_cls_pages = weekend_realtime.get("cls_pages", {})
    if isinstance(wk_cls_pages, dict) and wk_cls_pages:
        weekend_summary["cls_pages"] = {
            "depth_articles": len(wk_cls_pages.get("深度头条", {}).get("articles", [])) if isinstance(wk_cls_pages.get("深度头条"), dict) else 0,
            "vip_articles": len(wk_cls_pages.get("VIP文章", {}).get("articles", [])) if isinstance(wk_cls_pages.get("VIP文章"), dict) else 0,
            "calendar_events": len(wk_cls_pages.get("投资日历", {}).get("events", [])) if isinstance(wk_cls_pages.get("投资日历"), dict) else 0,
        }
        # VIP信息表
        wk_vip = weekend_realtime.get("vip_info", {})
        if isinstance(wk_vip, dict) and wk_vip.get("vip_stocks"):
            weekend_summary["vip_info"] = {
                "total_extracted": wk_vip.get("total_extracted", 0),
                "catalyst_themes": wk_vip.get("catalyst_themes", []),
                "vip_stocks_count": len(wk_vip.get("vip_stocks", [])),
            }
        print(f"  [周报] 周末CLS页面采集完成")
    else:
        weekend_summary["cls_pages"] = "数据暂缺"

    # 5.3 周末美股数据
    wk_us = weekend_realtime.get("us_premarket", {})
    if isinstance(wk_us, dict) and wk_us:
        weekend_summary["us_market"] = {
            "us_close": wk_us.get("us_close", {}),
            "us_premarket": wk_us.get("us_premarket", {}),
        }
        print(f"  [周报] 周末美股数据采集完成")
    else:
        weekend_summary["us_market"] = "数据暂缺"

    # 5.4 周末港股数据
    wk_hk = weekend_realtime.get("hk_index", {})
    if isinstance(wk_hk, dict) and wk_hk:
        weekend_summary["hk_market"] = wk_hk
        print(f"  [周报] 周末港股数据采集完成")
    else:
        weekend_summary["hk_market"] = "数据暂缺"

    # 5.5 周末外汇商品
    wk_fx = weekend_realtime.get("fx_commodity", {})
    if isinstance(wk_fx, dict) and wk_fx:
        weekend_summary["fx_commodity"] = wk_fx
        print(f"  [周报] 周末外汇商品采集完成")
    else:
        weekend_summary["fx_commodity"] = "数据暂缺"

    # 5.6 周末官媒头条
    wk_news = weekend_realtime.get("news_headlines", {})
    if isinstance(wk_news, dict) and wk_news:
        weekend_summary["news_headlines"] = wk_news
        print(f"  [周报] 周末官媒头条采集完成")
    else:
        weekend_summary["news_headlines"] = "数据暂缺"

    summary["weekend_realtime"] = weekend_summary

    # 6. 周报分析指引
    summary["weekly_analysis_guide"] = {
        "structure": "周报应包含: 本周市场回顾/主线叙事演变/电报信号追踪/板块轮动/钱三强选股表现/周末消息面/下周策略展望",
        "data_reference": "weekly_reports(每日报告预览) + weekly_telegraph(电报热点) + weekend_realtime(周末实时消息面+外围市场) + latest_daily_summary(最新收盘数据) + latest_qsq(选股结果)",
        "note": "周报不限于单日视角，应从周维度分析主线叙事的演变和板块轮动趋势。周末消息面（政策发布/外围市场变动）是下周策略展望的重要依据",
    }

    return summary


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="从 raw_data_*.json 提取精炼摘要，生成 data_summary.json"
    )
    parser.add_argument(
        "--file",
        type=str,
        default=None,
        help="指定输入文件路径（默认读取 data/raw_data_latest.json）",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="指定输出文件路径（默认保存到 data/data_summary.json）",
    )
    args = parser.parse_args()

    # 确定输入文件
    if args.file:
        input_file = args.file
        if not os.path.isfile(input_file):
            print(f"[ERROR] 指定的输入文件不存在: {input_file}")
            sys.exit(1)
    else:
        # 优先读取 raw_data_latest.json
        latest_file = os.path.join(DATA_DIR, "raw_data_latest.json")
        if os.path.isfile(latest_file):
            input_file = latest_file
        else:
            # 回退到最新的 raw_data_*.json
            found = find_latest_raw_file(DATA_DIR)
            if found:
                input_file = found
            else:
                print("[ERROR] 未找到任何 raw_data_*.json 文件")
                sys.exit(1)

    # 加载数据
    data = load_raw_data(input_file)

    # v2.0: 检查是否为周报模式
    is_weekly = data.get("mode") == "weekly"

    if is_weekly:
        print("\n[INFO] 检测到周报模式，生成周报摘要...")
        summary = extract_weekly_summary(data)
    else:
        # 提取各章摘要
        print("\n[INFO] 开始提取摘要数据...")
        summary = {}

        # 元信息
        summary["meta"] = {
            "source_file": os.path.basename(input_file),
            "mode": data.get("mode", "数据暂缺"),
            "fetch_time": data.get("fetch_time", "数据暂缺"),
            "trade_date": data.get("trade_date", "数据暂缺"),
            "summary_time": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        # 第零章: 财联社信源扫描（核心信源，推理链起点）
        print("  [0/5] 提取第零章: 财联社信源扫描...")
        summary.update(extract_chapter0_cls(data))

        # 第一章: 大盘概览
        print("  [1/5] 提取第一章: 大盘概览...")
        summary.update(extract_chapter1(data))

        # 第二章: 龙虎榜与资金动向
        print("  [2/5] 提取第二章: 龙虎榜与资金动向...")
        summary.update(extract_chapter2(data))

        # 第三章: 资金流向分析
        print("  [3/5] 提取第三章: 资金流向分析...")
        summary.update(extract_chapter3(data))

        # 第四章: 涨跌停与市场情绪
        print("  [4/5] 提取第四章: 涨跌停与市场情绪...")
        summary.update(extract_chapter4(data))

        # 第六章: 数据质量报告
        print("  [5/5] 提取第六章: 数据质量报告...")
        summary.update(extract_chapter6(data))

        # 钱三强选股结果（共振分析）
        print("  [5.5/7] 提取钱三强选股结果...")
        summary.update(extract_chapter_qsq(data))

        # VIP信息表（v2.0: 从fetch_data.py的VIP提取结果中读取）
        print("  [5.6/7] 提取VIP信息表...")
        summary.update(extract_chapter_vip(data))

        # 第五章说明: 直接引用前面各章数据
        summary["chapter5"] = {
            "note": "第五章（次日策略预判与金股）引用 chapter3 资金流向 + chapter_qsq 钱三强选股共振分析 + chapter_vip VIP信息表",
            "data_reference": "chapter3.moneyflow_aggregate + chapter_qsq.selected_stocks + chapter_qsq.two_of_three_stocks + chapter_vip.vip_stocks",
        }

    # 确定输出文件
    if args.output:
        output_file = args.output
    else:
        output_file = os.path.join(DATA_DIR, "data_summary.json")

    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    # 保存
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # 统计行数
    with open(output_file, "r", encoding="utf-8") as f:
        line_count = sum(1 for _ in f)

    print(f"\n[INFO] 摘要数据已保存: {output_file}")
    print(f"[INFO] 输出行数: {line_count}")
    print(f"[INFO] 交易日期: {data.get('trade_date', 'N/A')}")

    if line_count > 500:
        print(f"[WARN] 输出超过500行 ({line_count}行)，建议检查数据量")
    else:
        print(f"[OK] 输出在500行以内")

    return output_file


if __name__ == "__main__":
    main()
