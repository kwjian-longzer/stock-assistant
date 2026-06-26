#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VIP信息结构化提取器 v3

核心改进（v3）:
  1. 搜索式股票发现：用每篇文章的标题/简介提取业务关键词，
     在Tushare stock_company主营业务中全文搜索，发现最匹配的上市公司
  2. 利用CLS API related_stock字段的板块+数量约束
  3. 三层评分：板块匹配 + 主营业务包含搜索词 + 名称匹配
  4. 处理全部文章（分页采集50+篇）
  5. 板块维度标注：行业/概念/热点/事件四维
  6. 即使0匹配也生成MD文件（含文章清单+催化主题）

用法:
  from vip_extractor import extract_vip_info
  vip_table = extract_vip_info(vip_articles)
"""

import json
import os
import re
import sys
from collections import Counter, defaultdict

# 统一配置管理：从环境变量或 config.json 读取敏感信息
from settings import get_tushare_token

# A股核心概念/赛道关键词词典（用于从文章文本中提取催化主题）
CONCEPT_KEYWORDS = [
    # AI/算力
    "算力", "AI", "人工智能", "大模型", "AIDC", "IDC", "液冷", "光模块",
    "铜缆", "CPO", "硅光", "光通信", "光纤", "光缆", "服务器",
    # 半导体
    "半导体", "芯片", "存储芯片", "先进封装", "HBM", "光刻",
    "功率", "模拟", "氮化镓", "碳化硅", "晶圆", "封测", "MLCC",
    "电子特气", "磷化工", "抛光液",
    # PCB/电子
    "PCB", "覆铜板", "电镀", "铜箔",
    # 电容/电阻
    "铝电解电容", "薄膜电容", "超容", "超级电容", "电阻",
    # 消费电子
    "折叠屏", "iPhone", "苹果", "智能眼镜", "VR", "AR",
    "OLED", "面板", "Mini LED",
    # 机器人
    "机器人", "人形机器人", "减速器", "伺服",
    # 新能源
    "磷酸铁锂", "锂电池", "固态电池", "储能", "光伏",
    "氢能", "钠离子电池", "电池", "充电桩",
    # 航天/军工
    "商业航天", "卫星", "低空经济", "无人机", "eVTOL",
    "航发", "燃机", "核聚变", "军工",
    # 通信
    "5G", "6G", "通信", "基站",
    # 医药
    "创新药", "医疗器械", "CXO", "中药",
    # 金融科技
    "数字货币", "金融科技",
    # 材料
    "芳纶", "粉体", "粉体材料", "铝材", "铜材", "锡粉", "镍粉",
    "氮化铝", "氧化铜", "电子浆料", "铜基粉体",
    # 电力
    "电力", "特高压", "电网", "虚拟电厂",
    # 汽车
    "智能驾驶", "自动驾驶", "新能源车", "汽车",
    # 网络安全
    "网络安全", "信息安全",
    # 超算
    "超级计算机", "超算", "国产替代",
]

# 常见非关键词过滤表
STOP_WORDS = {
    "这家公司", "另一家", "公司", "行业", "板块", "概念",
    "受益", "标的", "龙头", "领域", "方向", "机构", "分析师",
    "需求", "增长", "拉动", "提升", "突破", "爆发", "高景气",
    "量价齐升", "景气周期", "商业化", "量产", "爬坡",
    "这家", "另有", "公司相关", "相关产品",
}

# 板块映射
MARKET_PREFIX = {
    "科创板": "688",
    "创业板": "300",
    "主板": "",  # 沪深主板不限制前缀
    "沪市主板": "60",
    "深市主板": "00",
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


def load_stock_database(pro=None):
    """加载全量股票数据库（stock_basic + stock_company合并）

    Args:
        pro: Tushare pro_api 实例

    Returns:
        list: 合并后的股票信息列表，每个元素含:
            ts_code, symbol, name, industry, main_business, business_scope
    """
    if pro is None:
        ts = _ensure_tushare()
        TUSHARE_TOKEN = get_tushare_token()
        ts.set_token(TUSHARE_TOKEN)
        pro = ts.pro_api()

    try:
        df_basic = pro.stock_basic(exchange='', list_status='L',
                                   fields='ts_code,symbol,name,industry,area,list_date')
        df_company = pro.stock_company(fields='ts_code,main_business,business_scope')
        df_merged = df_basic.merge(df_company, on='ts_code', how='left')

        stocks = df_merged.to_dict('records')
        print(f"[OK] 加载股票数据库: {len(stocks)} 只 (含主营业务描述)")
        return stocks
    except Exception as e:
        print(f"[WARN] 加载股票数据库失败: {e}")
        return []


def extract_search_terms(title, brief=""):
    """从文章标题和简介中提取搜索词

    使用概念词典匹配 + 标点切分短词过滤，
    提取用于在主营业务中搜索的关键词。

    Args:
        title: 文章标题
        brief: 文章摘要

    Returns:
        list: 搜索关键词列表
    """
    text = f"{title} {brief}"
    keywords = []

    # 1. 匹配概念关键词词典
    for kw in CONCEPT_KEYWORDS:
        if kw in text:
            keywords.append(kw)

    # 2. 按标点切分提取短词（2-6字，过滤句子片段）
    parts = re.split(r'[、,，；;。\s！!？?【】\[\]]', text)
    for part in parts:
        part = part.strip()
        if not (2 <= len(part) <= 6):
            continue
        if re.match(r'^[\d.]+$', part):
            continue
        if part in STOP_WORDS:
            continue
        # 过滤包含动词/助词的片段
        if any(w in part for w in ["的", "了", "在", "为", "是", "有", "将",
                                     "已", "正", "可", "能", "会", "被",
                                     "与", "和", "及", "或", "但", "而"]):
            continue
        if part not in keywords:
            keywords.append(part)

    # 去重保持顺序
    seen = set()
    unique = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)

    return unique[:10]


def parse_related_stock(related_stock):
    """解析CLS API的related_stock字段

    格式: [{"market": "科创板", "count": 1}, {"market": "主板", "count": 2}]
    或: "相关股票：科创板1只"

    Returns:
        list: [{"market": "科创板", "count": 1}, ...]
    """
    if not related_stock:
        return []

    # 格式1: 列表（CLS API原生格式）
    if isinstance(related_stock, list):
        result = []
        for item in related_stock:
            if isinstance(item, dict):
                market = item.get("market", "")
                count = item.get("count", 0)
                if market:
                    result.append({"market": market, "count": count})
        return result

    # 格式2: 字符串（旧格式兼容）
    if isinstance(related_stock, str):
        s = related_stock.replace("相关股票：", "").replace("相关股票:", "").strip()
        pattern = r'([\u4e00-\u9fa5]+?)(\d+)只'
        matches = re.findall(pattern, s)
        result = []
        for market_name, count in matches:
            market = _normalize_market(market_name)
            result.append({"market": market, "count": int(count)})
        return result

    return []


def _normalize_market(name):
    """标准化板块名称"""
    if "科创" in name:
        return "科创板"
    if "创业" in name:
        return "创业板"
    if "主板" in name or name in ("沪", "深", "沪深"):
        return "主板"
    if "北交" in name:
        return "北交所"
    return name


def discover_stocks_by_article(title, brief, related_stock, stock_database, max_results=3):
    """用文章内容搜索发现最匹配的上市公司

    三层搜索发现：
    1. 提取搜索词（业务关键词）
    2. 在stock_company.main_business中全文搜索
    3. 利用related_stock的板块+数量约束过滤
    4. 综合评分排序
    5. [v3.1新增] 补充发现报告API搜索研报中提及的股票

    Args:
        title: 文章标题
        brief: 文章摘要
        related_stock: CLS API的related_stock字段
        stock_database: load_stock_database()返回的全量股票列表
        max_results: 最多返回结果数

    Returns:
        list: 匹配的股票列表
    """
    # Step 1: 提取搜索词
    search_terms = extract_search_terms(title, brief)
    if not search_terms:
        return []

    # Step 2: 解析板块约束
    market_constraints = parse_related_stock(related_stock)
    # 收集所有要求的前缀
    required_prefixes = set()
    total_expected = 0
    for mc in market_constraints:
        market = mc["market"]
        total_expected += mc["count"]
        prefix = MARKET_PREFIX.get(market, "")
        if prefix:
            required_prefixes.add(prefix)
        # 主板包含沪市和深市
        if market == "主板":
            required_prefixes.add("60")
            required_prefixes.add("00")

    # Step 3: 在主营业务中全文搜索
    candidates = []
    for stock in stock_database:
        ts_code = stock.get("ts_code", "")
        name = stock.get("name", "")
        main_biz = stock.get("main_business", "") or ""
        biz_scope = stock.get("business_scope", "") or ""
        combined_biz = f"{main_biz} {biz_scope}"

        if not main_biz:
            continue

        # 板块过滤：如果有板块约束，只搜索该板块的股票
        if required_prefixes:
            symbol = stock.get("symbol", ts_code.split(".")[0])
            if not any(symbol.startswith(p) for p in required_prefixes):
                continue

        # 计算匹配分数
        score = 0
        matched_terms = []

        for term in search_terms:
            # 主营业务包含搜索词（高分）
            if term in main_biz:
                score += 5
                matched_terms.append(term)
            # 经营范围包含搜索词（中分）
            elif term in biz_scope:
                score += 3
                matched_terms.append(term)
            # 股票名称包含搜索词（中分）
            elif term in name:
                score += 3
                matched_terms.append(term)
            # 名称包含搜索词的前2字（低分模糊匹配）
            elif len(term) >= 2 and term[:2] in name:
                score += 1
                matched_terms.append(term)

        if score > 0:
            candidates.append({
                "ts_code": ts_code,
                "name": name,
                "industry": stock.get("industry", ""),
                "symbol": stock.get("symbol", ""),
                "main_business": main_biz[:100],
                "match_score": score,
                "matched_terms": matched_terms,
                "market_constraint_applied": bool(required_prefixes),
                "match_source": "tushare",
            })

    # Step 4: 综合评分排序
    candidates.sort(key=lambda x: x["match_score"], reverse=True)

    # 如果有板块约束且有count限制，尝试只取count个
    if market_constraints and total_expected > 0 and len(candidates) > total_expected:
        # 取前count个（但保留更多以防数量不足）
        candidates = candidates[:max(total_expected * 2, max_results)]

    # Step 5: [v3.1新增] 补充发现报告API搜索研报中提及的股票
    # 解决问题：VIP文章关键词（如"PCB镀铜"、"光模块锡粉"）出现在研报和新闻中，
    # 而非公司介绍资料中，Tushare stock_company.main_business搜索匹配不到。
    try:
        from cls_collector import search_fxbaogao
        fxbaogao_stocks = search_fxbaogao(title, brief)
        existing_codes = {c["ts_code"] for c in candidates}
        for stock in fxbaogao_stocks:
            if stock["code"] in existing_codes:
                # 合并来源
                for c in candidates:
                    if c["ts_code"] == stock["code"]:
                        c["match_source"] = "both"
                        c["match_score"] += stock.get("score", 0)
                continue
            # 新增fxbaogao发现
            candidates.append({
                "ts_code": stock["code"],
                "name": stock["name"],
                "industry": stock.get("industry", ""),
                "symbol": stock["code"].split(".")[0] if "." in stock["code"] else "",
                "main_business": "",
                "match_score": stock.get("score", 1),
                "matched_terms": [stock.get("detail", "研报搜索")],
                "market_constraint_applied": bool(required_prefixes),
                "match_source": "fxbaogao",
            })
            existing_codes.add(stock["code"])
    except Exception as e:
        # 发现报告搜索失败不影响主流程
        pass

    # 重新排序（fxbaogao结果可能改变排序）
    candidates.sort(key=lambda x: x["match_score"], reverse=True)
    return candidates[:max_results]


def extract_vip_info(vip_articles, stock_database=None, pro=None):
    """从VIP文章列表中提取结构化信息表（v3搜索式发现）

    主入口函数:
      1. 加载Tushare股票数据库（stock_basic + stock_company合并）
      2. 遍历全部VIP文章
      3. 用文章标题/简介提取搜索词
      4. 在主营业务中全文搜索发现匹配股票
      5. 利用related_stock的板块约束过滤

    Args:
        vip_articles: VIP文章列表（来自CLS API分页采集）
        stock_database: 预加载的股票数据库（可选）
        pro: Tushare pro_api 实例（可选）

    Returns:
        dict: VIP信息表
    """
    if not vip_articles:
        return {
            "vip_stocks": [],
            "total_articles": 0,
            "total_extracted": 0,
            "catalyst_themes": [],
            "article_list": [],
            "note": "无VIP文章数据",
        }

    # 加载股票数据库
    if stock_database is None:
        stock_database = load_stock_database(pro)

    vip_stocks = []
    all_keywords = []
    article_list = []
    sector_stats = defaultdict(lambda: {"articles": 0, "stocks": set()})

    for art in vip_articles:
        title = art.get("title", "")
        brief = art.get("brief", "")
        art_type = art.get("type", "") or art.get("column", "")
        reading_num = art.get("reading_num", 0)
        art_time = art.get("time", "")
        related_stock = art.get("related_stock", "")

        # 提取搜索词（同时作为催化关键词）
        keywords = extract_search_terms(title, brief)
        if keywords:
            all_keywords.extend(keywords)

        # 解析板块约束
        market_constraints = parse_related_stock(related_stock)
        constraint_str = ""
        if market_constraints:
            constraint_str = ", ".join(f"{mc['market']}{mc['count']}只" for mc in market_constraints)

        # 搜索发现匹配股票
        matched = discover_stocks_by_article(
            title, brief, related_stock, stock_database, max_results=3
        )

        # 记录文章结构化摘要
        article_entry = {
            "title": title[:120],
            "type": art_type,
            "reading_num": reading_num,
            "keywords": keywords,
            "related_stock": constraint_str,
            "has_stock_match": bool(matched),
        }

        if matched:
            article_entry["matched_stocks"] = [m["name"] for m in matched]

            for m in matched:
                vip_stocks.append({
                    "stock_name": m["name"],
                    "stock_code": m["ts_code"],
                    "sector": _get_sector_by_code(m["ts_code"]),
                    "industry": m["industry"],
                    "main_business": m["main_business"],
                    "catalyst_keywords": keywords,
                    "matched_terms": m["matched_terms"],
                    "matched_by": "business_search" + ("+market_filter" if m.get("market_constraint_applied") else ""),
                    "match_score": m["match_score"],
                    "source_article": title[:100],
                    "article_type": art_type,
                    "article_time": art_time,
                    "reading_num": reading_num,
                })

        # 板块维度统计
        for mc in market_constraints:
            sector_stats[mc["market"]]["articles"] += 1
        for m in matched:
            sector_stats[m["industry"]]["stocks"].add(m["name"])

        article_list.append(article_entry)

    # 去重（同一股票取匹配分最高的）
    best_by_code = {}
    for s in vip_stocks:
        code = s.get("stock_code", "")
        if code not in best_by_code or s.get("match_score", 0) > best_by_code[code].get("match_score", 0):
            best_by_code[code] = s

    unique_stocks = list(best_by_code.values())

    # 汇总催化主题
    theme_counter = Counter(all_keywords)
    top_themes = [{"keyword": k, "mentions": c}
                  for k, c in theme_counter.most_common(20)]

    # 板块维度汇总
    sector_dimension = []
    for sector_name, stats in sorted(sector_stats.items(), key=lambda x: x[1]["articles"], reverse=True):
        sector_dimension.append({
            "name": sector_name,
            "article_count": stats["articles"],
            "stock_count": len(stats["stocks"]),
            "stocks": list(stats["stocks"])[:5],
        })

    matched_count = sum(1 for a in article_list if a.get("has_stock_match"))

    result = {
        "vip_stocks": unique_stocks[:30],
        "total_articles": len(vip_articles),
        "total_extracted": len(unique_stocks),
        "total_matched_articles": matched_count,
        "catalyst_themes": top_themes,
        "article_list": article_list,
        "sector_dimension": sector_dimension,
        "source": "vip_extractor_v3_search",
    }

    print(f"[OK] VIP信息提取完成(v3搜索式): {len(unique_stocks)} 只股票, "
          f"{matched_count}/{len(vip_articles)} 篇文章匹配到股票, "
          f"{len(top_themes)} 个催化主题, {len(sector_dimension)} 个板块维度")
    return result


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

    v3: 增加板块维度展示 + 主营业务搜索结果
    """
    if not vip_table:
        print("[SKIP] 无VIP信息表数据")
        return None

    script_dir = os.path.dirname(os.path.abspath(__file__))
    reports_dir = os.path.join(script_dir, "reports")
    os.makedirs(reports_dir, exist_ok=True)

    lines = []
    lines.append(f"# VIP研报信息表（{report_date_str}）")
    lines.append("")
    total_articles = vip_table.get("total_articles", 0)
    total_extracted = vip_table.get("total_extracted", 0)
    matched_articles = vip_table.get("total_matched_articles", 0)
    themes = vip_table.get("catalyst_themes", [])
    lines.append(f"> VIP文章: {total_articles} 篇 | "
                 f"搜索发现股票: {total_extracted} 只 | "
                 f"有股票匹配的文章: {matched_articles} 篇 | "
                 f"催化主题: {len(themes)} 个")
    lines.append("")

    # 催化主题汇总
    if themes:
        lines.append("## 催化主题汇总")
        lines.append("")
        lines.append("| 排名 | 关键词 | 出现次数 |")
        lines.append("|------|--------|---------|")
        for i, t in enumerate(themes[:15], 1):
            lines.append(f"| {i} | {t['keyword']} | {t['mentions']} |")
        lines.append("")

    # 板块维度
    sector_dim = vip_table.get("sector_dimension", [])
    if sector_dim:
        lines.append("## 板块维度统计")
        lines.append("")
        lines.append("| 板块/行业 | 涉及文章数 | 匹配股票数 | 代表股票 |")
        lines.append("|----------|-----------|-----------|---------|")
        for s in sector_dim[:10]:
            stocks = ", ".join(s.get("stocks", [])[:3])
            lines.append(f"| {s['name']} | {s['article_count']} | {s['stock_count']} | {stocks} |")
        lines.append("")

    # VIP文章结构化清单
    article_list = vip_table.get("article_list", [])
    if article_list:
        lines.append("## VIP文章结构化清单")
        lines.append("")
        lines.append("| 序号 | 类型 | 阅读量 | 板块约束 | 催化关键词 | 股票匹配 | 文章标题 |")
        lines.append("|------|------|--------|---------|-----------|---------|---------|")
        for i, a in enumerate(article_list, 1):
            kws = ", ".join(a.get("keywords", [])[:5])
            constraint = a.get("related_stock", "-")
            matched = ", ".join(a.get("matched_stocks", [])) if a.get("has_stock_match") else "-"
            title = a.get("title", "")[:50]
            lines.append(
                f"| {i} | {a.get('type','')} | {a.get('reading_num',0)} | "
                f"{constraint} | {kws} | {matched} | {title} |"
            )
        lines.append("")

    # VIP股票信息表（含主营业务匹配）
    stocks = vip_table.get("vip_stocks", [])
    if stocks:
        lines.append("## VIP研报搜索发现股票")
        lines.append("")
        lines.append("| 序号 | 代码 | 名称 | 板块 | 行业 | 主营业务 | 搜索词命中 | 匹配分 | 来源文章 |")
        lines.append("|------|------|------|------|------|---------|-----------|-------|---------|")
        for i, s in enumerate(stocks, 1):
            matched_terms = ", ".join(s.get("matched_terms", [])[:5])
            biz = s.get("main_business", "")[:40]
            lines.append(
                f"| {i} | {s.get('stock_code','')} | {s.get('stock_name','')} | "
                f"{s.get('sector','')} | {s.get('industry','')} | "
                f"{biz} | {matched_terms} | {s.get('match_score',0)} | {s.get('source_article','')[:30]} |"
            )
        lines.append("")

    lines.append("---")
    lines.append("*本文件由 vip_extractor.py v3（搜索式发现）自动生成*")
    lines.append(f"*数据来源: 财联社VIP API(分页) + Tushare stock_company主营业务搜索 | 生成时间: {report_date_str}*")

    md_content = '\n'.join(lines)
    md_path = os.path.join(reports_dir, f"{report_date_str}_VIP信息表.md")
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md_content)

    print(f"[OK] VIP信息表MD文件已生成: {md_path}")
    return md_path


if __name__ == "__main__":
    # 测试: 从data_summary.json读取真实VIP文章
    script_dir = os.path.dirname(os.path.abspath(__file__))
    summary_path = os.path.join(script_dir, "data", "data_summary.json")

    if os.path.exists(summary_path):
        with open(summary_path, 'r', encoding='utf-8') as f:
            summary = json.load(f)
        chapter0 = summary.get("chapter0_cls", {})
        cls_vip = chapter0.get("cls_vip", {})
        if isinstance(cls_vip, dict) and cls_vip.get("articles"):
            articles = cls_vip["articles"]
            print(f"=== VIP信息提取器 v3 测试 ({len(articles)} 篇文章) ===")
            result = extract_vip_info(articles)
            print(json.dumps(result, ensure_ascii=False, indent=2)[:3000])

            md_path = generate_vip_md_report(result, "2026-06-26")
            print(f"\nMD文件: {md_path}")
        else:
            print("未找到VIP文章数据")
    else:
        print(f"未找到 data_summary.json: {summary_path}")
