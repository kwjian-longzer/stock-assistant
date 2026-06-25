#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VIP信息结构化提取器 v2

从财联社VIP文章中提取催化主题和关联股票，生成结构化VIP信息表。

核心改进:
  1. 处理全部VIP文章（电报解读/风口研报/盘中宝等8类），不再只筛"风口研报"
  2. 关键词提取基于行业词典匹配，不再按标点切分产生句子片段
  3. 不依赖related_stock字段（真实API不返回此字段）
  4. 即使0匹配也生成MD文件（含文章清单+催化主题）

工作流程:
  1. 加载Tushare stock_basic，构建 行业→股票 索引
  2. 遍历全部VIP文章，用行业词典+概念词典从标题/摘要提取催化关键词
  3. 按关键词匹配Tushare股票（行业匹配+名称匹配）
  4. 输出结构化VIP信息表 + MD文件

用法:
  from vip_extractor import extract_vip_info
  vip_table = extract_vip_info(vip_articles)
"""

import json
import os
import re
import sys
from collections import Counter, defaultdict

# 板块匹配规则: 关键词 → Tushare 代码前缀
SECTOR_PREFIX = {
    "科创板": "688",
    "创业板": "300",
    "沪市主板": "60",
    "深市主板": "00",
    "北交所": "8",
}

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
    "芳纶", "粉体材料", "铝材", "铜材", "锡粉", "镍粉",
    "氮化铝", "氧化铜", "电子浆料",
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


def build_industry_index(stock_basic_list):
    """构建 行业→股票列表 索引

    Args:
        stock_basic_list: Tushare 股票基础信息列表

    Returns:
        dict: {行业名: [股票信息, ...]}
        set: 全部唯一行业名集合
    """
    industry_index = defaultdict(list)
    all_industries = set()

    for stock in stock_basic_list:
        industry = stock.get("industry", "")
        if industry:
            industry_index[industry].append(stock)
            all_industries.add(industry)

    return dict(industry_index), all_industries


def extract_catalyst_keywords(title, brief="", known_industries=None):
    """从文章标题和摘要中提取催化关键词

    改进版: 使用行业词典+概念词典进行已知关键词匹配，
    而非按标点切分（避免产生句子片段）。

    Args:
        title: 文章标题
        brief: 文章摘要
        known_industries: 已知的Tushare行业名集合（用于匹配）

    Returns:
        list: 催化关键词列表（去重，保持出现顺序）
    """
    text = f"{title} {brief}"

    keywords = []

    # 1. 匹配概念关键词词典
    for kw in CONCEPT_KEYWORDS:
        if kw in text:
            keywords.append(kw)

    # 2. 匹配Tushare已知行业名
    if known_industries:
        for ind in known_industries:
            if ind and len(ind) >= 2 and ind in text:
                if ind not in keywords:
                    keywords.append(ind)

    # 3. 按标点切分后提取短关键词（2-6字名词，过滤句子片段）
    parts = re.split(r'[、,，；;。\s！!？?]', text)
    for part in parts:
        part = part.strip()
        # 只保留2-6字的短词（长的大概率是句子片段）
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

    # 去重保持顺序，限制数量
    seen = set()
    unique = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)

    return unique[:12]


def match_stocks_by_keywords(keywords, stock_basic_list, industry_index=None, max_matches=5):
    """用关键词匹配 Tushare 股票

    匹配策略（三级）:
      1. 行业完全匹配: 关键词 == 股票行业（高分）
      2. 股票名称包含关键词（中分）
      3. 关键词包含股票名称（中低分）

    Args:
        keywords: 催化关键词列表
        stock_basic_list: 全量股票列表（用于名称匹配）
        industry_index: 行业→股票索引（加速行业匹配）
        max_matches: 最多返回匹配数量

    Returns:
        list: 匹配的股票列表，按匹配分数排序
    """
    if not keywords:
        return []

    matches = []
    matched_codes = set()

    # 1. 行业匹配: 关键词直接等于行业名
    if industry_index:
        for kw in keywords:
            if kw in industry_index:
                for stock in industry_index[kw]:
                    ts_code = stock.get("ts_code", "")
                    if ts_code in matched_codes:
                        continue
                    matched_codes.add(ts_code)
                    # 统计该股票匹配的关键词数
                    kw_count = sum(1 for k in keywords if k == stock.get("industry", ""))
                    matches.append({
                        "ts_code": ts_code,
                        "name": stock.get("name", ""),
                        "industry": stock.get("industry", ""),
                        "symbol": stock.get("symbol", ""),
                        "match_score": 5 + kw_count,
                        "matched_keywords": [kw],
                        "match_type": "industry_exact",
                    })

    # 2. 名称匹配: 遍历全量股票做名称包含
    for stock in stock_basic_list:
        ts_code = stock.get("ts_code", "")
        name = stock.get("name", "")
        industry = stock.get("industry", "")

        if ts_code in matched_codes:
            continue
        if not name or len(name) < 2:
            continue

        score = 0
        matched_kws = []

        for kw in keywords:
            kw_clean = kw.strip()
            if not kw_clean or len(kw_clean) < 2:
                continue
            # 股票名称包含关键词
            if kw_clean in name:
                score += 4
                matched_kws.append(kw_clean)
            # 关键词包含股票名称
            elif len(name) >= 2 and name in kw_clean:
                score += 3
                matched_kws.append(kw_clean)

        if score > 0:
            matched_codes.add(ts_code)
            matches.append({
                "ts_code": ts_code,
                "name": name,
                "industry": industry,
                "symbol": stock.get("symbol", ""),
                "match_score": score,
                "matched_keywords": matched_kws,
                "match_type": "name_match",
            })

    # 按匹配分数排序
    matches.sort(key=lambda x: x["match_score"], reverse=True)
    return matches[:max_matches]


def extract_vip_info(vip_articles, stock_basic_list=None, pro=None):
    """从VIP文章列表中提取结构化信息表

    主入口函数:
      1. 加载Tushare股票基础信息 + 构建行业索引
      2. 遍历全部VIP文章（不筛选类型）
      3. 用行业词典+概念词典提取催化关键词
      4. 按关键词匹配具体股票
      5. 输出结构化信息表（即使0匹配也返回有效结构）

    Args:
        vip_articles: VIP文章列表（来自CLS API）
        stock_basic_list: 预加载的股票基础信息（可选）
        pro: Tushare pro_api 实例（可选）

    Returns:
        dict: VIP信息表
            - vip_stocks: 结构化股票列表
            - total_articles: 处理的文章总数
            - total_extracted: 提取出的股票总数
            - catalyst_themes: 催化主题汇总（关键词+出现次数）
            - article_list: 全部文章的结构化摘要
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

    # 加载股票基础信息
    if stock_basic_list is None:
        stock_basic_list = load_stock_basic(pro)

    # 构建行业索引
    industry_index, all_industries = build_industry_index(stock_basic_list)
    print(f"[INFO] 构建行业索引: {len(all_industries)} 个行业")

    vip_stocks = []
    all_keywords = []
    article_list = []

    for art in vip_articles:
        title = art.get("title", "")
        brief = art.get("brief", "")
        art_type = art.get("type", "") or art.get("column", "")
        reading_num = art.get("reading_num", 0)
        art_time = art.get("time", "")

        # 提取催化关键词
        keywords = extract_catalyst_keywords(title, brief, all_industries)
        if keywords:
            all_keywords.extend(keywords)

        # 记录文章结构化摘要
        article_list.append({
            "title": title[:120],
            "type": art_type,
            "reading_num": reading_num,
            "keywords": keywords,
            "has_stock_match": False,
        })

        # 匹配具体股票
        matched = match_stocks_by_keywords(
            keywords, stock_basic_list, industry_index, max_matches=3
        )

        if matched:
            # 更新文章的匹配状态
            article_list[-1]["has_stock_match"] = True
            article_list[-1]["matched_stocks"] = [m["name"] for m in matched]

            for m in matched:
                vip_stocks.append({
                    "stock_name": m["name"],
                    "stock_code": m["ts_code"],
                    "sector": _get_sector_by_code(m["ts_code"]),
                    "industry": m["industry"],
                    "catalyst_keywords": keywords,
                    "matched_by": m["match_type"],
                    "source_article": title[:100],
                    "article_type": art_type,
                    "article_time": art_time,
                    "reading_num": reading_num,
                })

    # 去重（同一股票可能来自多篇文章，保留匹配分最高的）
    best_by_code = {}
    for s in vip_stocks:
        code = s.get("stock_code", "")
        if code not in best_by_code or len(s.get("catalyst_keywords", [])) > len(best_by_code[code].get("catalyst_keywords", [])):
            best_by_code[code] = s

    unique_stocks = list(best_by_code.values())

    # 汇总催化主题
    theme_counter = Counter(all_keywords)
    top_themes = [{"keyword": k, "mentions": c}
                  for k, c in theme_counter.most_common(20)]

    matched_count = sum(1 for a in article_list if a.get("has_stock_match"))

    result = {
        "vip_stocks": unique_stocks[:30],
        "total_articles": len(vip_articles),
        "total_extracted": len(unique_stocks),
        "total_matched_articles": matched_count,
        "catalyst_themes": top_themes,
        "article_list": article_list,
        "source": "vip_extractor_v2",
    }

    print(f"[OK] VIP信息提取完成: {len(unique_stocks)} 只股票, "
          f"{matched_count}/{len(vip_articles)} 篇文章匹配到股票, "
          f"{len(top_themes)} 个催化主题")
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

    v2: 即使vip_stocks为空也生成MD文件（含文章清单+催化主题）

    Args:
        vip_table: extract_vip_info 返回的VIP信息表
        report_date_str: 报告日期，如 "2026-06-25"

    Returns:
        str: MD文件路径（始终生成）
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
                 f"匹配股票: {total_extracted} 只 | "
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

    # VIP文章结构化清单
    article_list = vip_table.get("article_list", [])
    if article_list:
        lines.append("## VIP文章结构化清单")
        lines.append("")
        lines.append("| 序号 | 类型 | 阅读量 | 催化关键词 | 股票匹配 | 文章标题 |")
        lines.append("|------|------|--------|-----------|---------|---------|")
        for i, a in enumerate(article_list, 1):
            kws = ", ".join(a.get("keywords", [])[:5])
            matched = ", ".join(a.get("matched_stocks", [])) if a.get("has_stock_match") else "-"
            title = a.get("title", "")[:60]
            lines.append(
                f"| {i} | {a.get('type','')} | {a.get('reading_num',0)} | "
                f"{kws} | {matched} | {title} |"
            )
        lines.append("")

    # VIP股票信息表
    stocks = vip_table.get("vip_stocks", [])
    if stocks:
        lines.append("## VIP研报关联股票")
        lines.append("")
        lines.append("| 序号 | 代码 | 名称 | 板块 | 行业 | 催化关键词 | 匹配方式 | 来源文章 |")
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
    lines.append("*本文件由 vip_extractor.py v2 自动生成*")
    lines.append(f"*数据来源: 财联社VIP API + Tushare | 生成时间: {report_date_str}*")

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
            print(f"=== VIP信息提取器 v2 测试 ({len(articles)} 篇文章) ===")
            result = extract_vip_info(articles)
            print(json.dumps(result, ensure_ascii=False, indent=2)[:3000])

            # 生成MD文件
            md_path = generate_vip_md_report(result, "2026-06-25")
            print(f"\nMD文件: {md_path}")
        else:
            print("未找到VIP文章数据")
    else:
        print(f"未找到 data_summary.json: {summary_path}")
