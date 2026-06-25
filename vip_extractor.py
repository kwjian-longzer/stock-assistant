#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VIP信息结构化提取器

从财联社VIP文章中提取"风口研报·公司"类研报，解析其相关股票和催化概念，
生成结构化VIP信息表，作为研报综合推理挖掘的重要数据维度。

工作流程:
  1. 筛选"风口研报"类VIP文章
  2. 解析"相关股票：XX板块X只"描述，确定板块和数量
  3. 从文章标题/摘要提取催化关键词
  4. 用Tushare stock_basic按板块+关键词匹配具体股票
  5. 输出结构化VIP信息表

用法:
  from vip_extractor import extract_vip_info
  vip_table = extract_vip_info(vip_articles)
"""

import json
import os
import re
import sys

# 板块匹配规则: 关键词 → Tushare 代码前缀
SECTOR_PREFIX = {
    "科创板": "688",
    "创业板": "300",
    "沪市主板": "60",
    "深市主板": "00",
    "北交所": "8",
}

# 板块关键词到交易所后缀的映射
SECTOR_SUFFIX = {
    "科创板": ".SH",
    "创业板": ".SZ",
    "沪市主板": ".SH",
    "深市主板": ".SZ",
    "北交所": ".BJ",
}


def _ensure_tushare():
    """确保 tushare 已安装"""
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


def load_stock_basic(pro=None):
    """加载 Tushare 全量股票基础信息

    Args:
        pro: Tushare pro_api 实例，None则自动初始化

    Returns:
        list: 股票基础信息列表，每个元素含 ts_code, name, industry, symbol 等
    """
    if pro is None:
        ts = _ensure_tushare()
        TUSHARE_TOKEN = "8eaad9971749da18299f4932a7cabf068a495fdf06ef3aaafebfe365"
        ts.set_token(TUSHARE_TOKEN)
        pro = ts.pro_api()

    try:
        df = pro.stock_basic(exchange='', list_status='L',
                             fields='ts_code,symbol,name,area,industry,list_date')
        stocks = df.to_dict('records')
        print(f"[OK] 加载股票基础信息: {len(stocks)} 只")
        return stocks
    except Exception as e:
        print(f"[WARN] 加载股票基础信息失败: {e}")
        return []


def parse_related_stock(related_str):
    """解析"相关股票：XX板块X只"格式

    示例:
      "相关股票：科创板1只" → [{"sector": "科创板", "count": 1}]
      "相关股票：沪深2只，创业板1只" → [{"sector": "沪深", "count": 2}, {"sector": "创业板", "count": 1}]
      "相关股票：有研粉材" → [{"stock_name": "有研粉材"}]

    Args:
        related_str: related_stock 字段值

    Returns:
        list: 解析结果，每个元素可能含 sector+count 或 stock_name
    """
    if not related_str:
        return []

    # 清理前缀
    s = related_str.replace("相关股票：", "").replace("相关股票:", "").strip()

    results = []

    # 尝试匹配 "板块X只" 格式
    pattern = r'([\u4e00-\u9fa5]+?)(\d+)只'
    matches = re.findall(pattern, s)

    if matches:
        for sector_name, count in matches:
            # 标准化板块名称
            sector = _normalize_sector(sector_name)
            results.append({
                "sector": sector,
                "count": int(count),
                "raw": f"{sector_name}{count}只",
            })

    # 如果没有匹配到"X只"格式，检查是否包含具体股票名称
    if not results and s and not s.isdigit():
        # 可能直接是股票名称
        for name in s.replace(",", "，").split("，"):
            name = name.strip()
            if name and len(name) >= 2 and len(name) <= 8:
                results.append({"stock_name": name})

    return results


def _normalize_sector(name):
    """标准化板块名称"""
    name = name.strip()
    # "沪深"可能指沪市或深市，保持原样
    if "科创" in name:
        return "科创板"
    if "创业" in name:
        return "创业板"
    if "沪市" in name or name == "沪":
        return "沪市主板"
    if "深市" in name or name == "深":
        return "深市主板"
    if "北交" in name:
        return "北交所"
    return name


def extract_catalyst_keywords(title, brief=""):
    """从文章标题和摘要中提取催化关键词

    研报标题通常包含多个催化主线，以顿号或逗号分隔。
    例如: "算力芯片、光模块、先进封装拉动粉体材料需求"
    → ["算力芯片", "光模块", "先进封装", "粉体材料"]

    Args:
        title: 文章标题
        brief: 文章摘要

    Returns:
        list: 催化关键词列表
    """
    text = f"{title} {brief}"

    # 提取顿号/逗号分隔的关键词
    parts = re.split(r'[、,，；;]', text)
    keywords = []
    for part in parts:
        part = part.strip()
        # 过滤: 太短/太长/包含动词/纯数字
        if len(part) < 2 or len(part) > 12:
            continue
        if re.match(r'^[\d.]+$', part):
            continue
        # 过滤常见非关键词
        if part in ("拉动", "需求", "增长", "公司", "行业", "板块",
                     "概念", "受益", "标的", "龙头", "领域", "方向"):
            continue
        keywords.append(part)

    # 去重保持顺序
    seen = set()
    unique = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)

    return unique[:10]


def match_stocks_by_keywords(keywords, stock_basic_list, sectors=None, max_matches=5):
    """用关键词匹配 Tushare 股票基础信息

    匹配策略:
      1. 股票名称完全包含关键词
      2. 股票行业完全包含关键词
      3. 关键词部分匹配股票名称（如"粉体"→"有研粉材"）
      4. 如果指定板块，进一步过滤

    Args:
        keywords: 催化关键词列表
        stock_basic_list: Tushare 股票基础信息列表
        sectors: 板块过滤列表（如 ["科创板", "创业板"]），None则不过滤
        max_matches: 最多返回匹配数量

    Returns:
        list: 匹配的股票列表，按匹配分数排序
    """
    if not keywords or not stock_basic_list:
        return []

    # 构建板块过滤条件
    sector_prefixes = set()
    if sectors:
        for sec in sectors:
            prefix = SECTOR_PREFIX.get(sec)
            if prefix:
                sector_prefixes.add(prefix)

    matches = []
    for stock in stock_basic_list:
        ts_code = stock.get("ts_code", "")
        name = stock.get("name", "")
        industry = stock.get("industry", "")
        symbol = stock.get("symbol", "")

        # 板块过滤
        if sector_prefixes:
            if not any(symbol.startswith(p) for p in sector_prefixes):
                continue

        # 关键词匹配（三级匹配策略）
        score = 0
        matched_keywords = []
        for kw in keywords:
            kw_clean = kw.strip()
            if not kw_clean or len(kw_clean) < 2:
                continue

            # 1. 股票名称完全包含关键词
            if kw_clean in name:
                score += 5
                matched_keywords.append(kw_clean)
            # 2. 关键词包含股票名称（如"有研粉材"在"粉体材料"关键词中——不匹配，但"粉体"可匹配）
            elif name in kw_clean and len(name) >= 2:
                score += 4
                matched_keywords.append(kw_clean)
            # 3. 股票名称包含关键词的前2个字（模糊匹配）
            elif len(kw_clean) >= 2 and kw_clean[:2] in name:
                score += 2
                matched_keywords.append(kw_clean)
            # 4. 行业完全包含关键词
            elif industry and kw_clean in industry:
                score += 1
                matched_keywords.append(kw_clean)
            # 5. 关键词包含行业名称
            elif industry and len(industry) >= 2 and industry in kw_clean:
                score += 1
                matched_keywords.append(kw_clean)

        if score > 0:
            matches.append({
                "ts_code": ts_code,
                "name": name,
                "industry": industry,
                "match_score": score,
                "matched_keywords": matched_keywords,
            })

    # 按匹配分数排序
    matches.sort(key=lambda x: x["match_score"], reverse=True)
    return matches[:max_matches]


def extract_vip_info(vip_articles, stock_basic_list=None, pro=None):
    """从VIP文章列表中提取结构化信息表

    主入口函数:
      1. 筛选"风口研报"类文章
      2. 解析相关股票描述
      3. 提取催化关键词
      4. 匹配具体股票（需要 stock_basic_list 或 pro）

    Args:
        vip_articles: VIP文章列表（来自 fetch_data.py 的 API 或浏览器采集）
        stock_basic_list: 预加载的股票基础信息（可选，未提供则用 pro 加载）
        pro: Tushare pro_api 实例（可选）

    Returns:
        dict: VIP信息表
            - vip_stocks: 结构化股票列表
            - total_articles: 研报类文章总数
            - total_extracted: 提取出的股票总数
            - catalyst_themes: 催化主题汇总
    """
    if not vip_articles:
        return {
            "vip_stocks": [],
            "total_articles": 0,
            "total_extracted": 0,
            "catalyst_themes": [],
            "note": "无VIP文章数据",
        }

    # 加载股票基础信息
    if stock_basic_list is None:
        stock_basic_list = load_stock_basic(pro)

    # 筛选"风口研报"类文章
    research_articles = []
    for art in vip_articles:
        title = art.get("title", "")
        art_type = art.get("type", "") or art.get("column", "")
        combined = f"{title} {art_type}"

        if "风口研报" in combined or "研报" in combined or "公司" in art_type:
            research_articles.append(art)

    print(f"[INFO] 筛选到研报类VIP文章: {len(research_articles)} / {len(vip_articles)} 篇")

    vip_stocks = []
    catalyst_themes = []

    for art in research_articles:
        title = art.get("title", "")
        brief = art.get("brief", "")
        related_stock = art.get("related_stock", "") or art.get("stocks", "")
        art_type = art.get("type", "") or art.get("column", "")
        art_time = art.get("time", "")

        # 提取催化关键词
        keywords = extract_catalyst_keywords(title, brief)
        if keywords:
            catalyst_themes.extend(keywords)

        # 解析相关股票
        parsed = parse_related_stock(related_stock)

        if not parsed:
            # 没有相关股票信息，仅用关键词匹配
            matched = match_stocks_by_keywords(keywords, stock_basic_list, max_matches=3)
            for m in matched:
                vip_stocks.append({
                    "stock_name": m["name"],
                    "stock_code": m["ts_code"],
                    "sector": _get_sector_by_code(m["ts_code"]),
                    "industry": m["industry"],
                    "catalyst_keywords": keywords,
                    "matched_by": "keyword_only",
                    "source_article": title[:80],
                    "article_type": art_type,
                    "article_time": art_time,
                })
        else:
            for p in parsed:
                if "stock_name" in p:
                    # 直接是股票名称，尝试查找代码
                    stock_name = p["stock_name"]
                    stock_info = _find_stock_by_name(stock_name, stock_basic_list)
                    if stock_info:
                        vip_stocks.append({
                            "stock_name": stock_info["name"],
                            "stock_code": stock_info["ts_code"],
                            "sector": _get_sector_by_code(stock_info["ts_code"]),
                            "industry": stock_info.get("industry", ""),
                            "catalyst_keywords": keywords,
                            "matched_by": "name_direct",
                            "source_article": title[:80],
                            "article_type": art_type,
                            "article_time": art_time,
                        })
                elif "sector" in p:
                    # 板块+数量格式，用关键词在该板块内匹配
                    sectors = [p["sector"]]
                    expected_count = p["count"]
                    matched = match_stocks_by_keywords(
                        keywords, stock_basic_list, sectors=sectors,
                        max_matches=max(expected_count, 3)
                    )
                    for m in matched:
                        vip_stocks.append({
                            "stock_name": m["name"],
                            "stock_code": m["ts_code"],
                            "sector": _get_sector_by_code(m["ts_code"]),
                            "industry": m["industry"],
                            "catalyst_keywords": keywords,
                            "matched_by": f"sector+keyword({p['raw']})",
                            "source_article": title[:80],
                            "article_type": art_type,
                            "article_time": art_time,
                        })

    # 去重（同一股票可能来自多篇文章）
    seen_codes = set()
    unique_stocks = []
    for s in vip_stocks:
        code = s.get("stock_code", "")
        if code and code not in seen_codes:
            seen_codes.add(code)
            unique_stocks.append(s)
        elif not code:
            unique_stocks.append(s)

    # 汇总催化主题
    from collections import Counter
    theme_counter = Counter(catalyst_themes)
    top_themes = [{"keyword": k, "mentions": c}
                  for k, c in theme_counter.most_common(20)]

    result = {
        "vip_stocks": unique_stocks[:30],
        "total_articles": len(research_articles),
        "total_extracted": len(unique_stocks),
        "catalyst_themes": top_themes,
        "source": "vip_extractor",
    }

    print(f"[OK] VIP信息提取完成: {len(unique_stocks)} 只股票, {len(top_themes)} 个催化主题")
    return result


def _find_stock_by_name(name, stock_basic_list):
    """按名称查找股票"""
    for stock in stock_basic_list:
        if stock.get("name") == name:
            return stock
    # 模糊匹配
    for stock in stock_basic_list:
        if name in stock.get("name", "") or stock.get("name", "") in name:
            return stock
    return None


def _get_sector_by_code(ts_code):
    """根据 ts_code 判断板块"""
    code = ts_code.split(".")[0] if "." in ts_code else ts_code
    if code.startswith("688"):
        return "科创板"
    elif code.startswith("300"):
        return "创业板"
    elif code.startswith("60"):
        return "沪市主板"
    elif code.startswith("00"):
        return "深市主板"
    elif code.startswith("8"):
        return "北交所"
    return "其他"


def generate_vip_md_report(vip_table, report_date_str):
    """生成VIP信息表的MD文件

    Args:
        vip_table: extract_vip_info 返回的VIP信息表
        report_date_str: 报告日期，如 "2026-06-25"

    Returns:
        str: MD文件路径，失败返回 None
    """
    if not vip_table or not vip_table.get("vip_stocks"):
        print("[SKIP] 无VIP信息表数据，跳过MD生成")
        return None

    script_dir = os.path.dirname(os.path.abspath(__file__))
    reports_dir = os.path.join(script_dir, "reports")
    os.makedirs(reports_dir, exist_ok=True)

    lines = []
    lines.append(f"# VIP研报信息表（{report_date_str}）")
    lines.append("")
    lines.append(f"> 研报类文章: {vip_table.get('total_articles', 0)} 篇 | "
                 f"提取股票: {vip_table.get('total_extracted', 0)} 只 | "
                 f"催化主题: {len(vip_table.get('catalyst_themes', []))} 个")
    lines.append("")

    # 催化主题汇总
    themes = vip_table.get("catalyst_themes", [])
    if themes:
        lines.append("## 催化主题汇总")
        lines.append("")
        lines.append("| 关键词 | 出现次数 |")
        lines.append("|--------|---------|")
        for t in themes[:15]:
            lines.append(f"| {t['keyword']} | {t['mentions']} |")
        lines.append("")

    # VIP股票信息表
    stocks = vip_table.get("vip_stocks", [])
    if stocks:
        lines.append("## VIP研报相关股票")
        lines.append("")
        lines.append("| 序号 | 代码 | 名称 | 板块 | 行业 | 催化关键词 | 匹配方式 | 来源研报 |")
        lines.append("|------|------|------|------|------|-----------|---------|---------|")
        for i, s in enumerate(stocks, 1):
            keywords = ", ".join(s.get("catalyst_keywords", [])[:5])
            lines.append(
                f"| {i} | {s.get('stock_code','')} | {s.get('stock_name','')} | "
                f"{s.get('sector','')} | {s.get('industry','')} | "
                f"{keywords} | {s.get('matched_by','')} | {s.get('source_article','')[:40]} |"
            )
        lines.append("")

    lines.append("---")
    lines.append("*本文件由 vip_extractor.py 自动生成，数据来源: 财联社VIP API + Tushare*")
    lines.append(f"*生成时间: {report_date_str}*")

    md_content = '\n'.join(lines)
    md_path = os.path.join(reports_dir, f"{report_date_str}_VIP信息表.md")
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md_content)

    print(f"[OK] VIP信息表MD文件已生成: {md_path}")
    return md_path


if __name__ == "__main__":
    # 测试用例
    test_articles = [
        {
            "title": "算力芯片、光模块、先进封装拉动粉体材料需求",
            "brief": "随着AI算力需求爆发，粉体材料作为关键上游环节迎来量价齐升",
            "related_stock": "相关股票：科创板1只",
            "type": "风口研报·公司",
            "time": "10:11",
        },
        {
            "title": "新能源车销量超预期，锂电材料产业链受益",
            "brief": "6月新能源车销量同比增长40%，锂电池材料需求旺盛",
            "related_stock": "相关股票：沪深2只，创业板1只",
            "type": "风口研报·公司",
            "time": "11:30",
        },
    ]

    print("=== VIP信息提取器测试 ===")
    result = extract_vip_info(test_articles)
    print(json.dumps(result, ensure_ascii=False, indent=2))
