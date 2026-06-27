#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VIP股票发现 v4：结构化解析 + 多源动态搜索

核心改进（对标豆包方法论，基于用户反馈迭代）：
  1. VIP文章结构化解析：领域/热点 → 业务线索 → 事件类型
     - 每篇VIP文章都是"热点题材下的公司"或"业务重大突破"
     - 内容简单，可直接全文加工成结构化信号
  2. 多源动态搜索：
     - 东方财富公告API（免费，含全文）← 最权威公司官方披露
     - WebSearch（互动易Q&A、新闻）  ← 最及时，Agent调用后传入
     - fxbaogao调研纪要+研报         ← 深度分析+公司最新动态
     - CLS电报（本地DB）             ← 实时采集
     - Tushare主营业务（静态）        ← 基础参考
  3. 加权线索验证：动态信息权重最高，多源交叉验证加分
  4. 排除逻辑：仅匹配通用概念的标的被排除

版本历史:
  v3:   概念关键词提取 → Tushare main_business全文搜索 → fxbaogao研报补充
  v4.0: 业务线索提取 + fxbaogao原文验证 + 匹配率排序
  v4.1: 增加东方财富公告API + WebSearch互动易集成（本版本）

用法:
  # 独立运行（不调用WebSearch，仅用HTTP API）
  python vip_search_v4.py

  # Agent集成模式（传入WebSearch结果）
  from vip_search_v4 import discover_stocks_v4
  web_results = [...]  # Agent通过WebSearch工具获取
  kept, excl = discover_stocks_v4(title, brief, web_search_results=web_results)

  # 从vip_extractor调用（v3兼容入口）
  from vip_search_v4 import discover_stocks_from_vip_article
  result = discover_stocks_from_vip_article(title, brief, related_stock)
"""

import json
import re
import os
import sys
import time
import requests
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from settings import get_fxbaogao_api_key, get_tushare_token


# ========== 第一层：VIP文章结构化解析 ==========

DOMAIN_MAP = {
    "算力": "AI算力", "AI": "AI算力", "人工智能": "AI算力",
    "大模型": "AI算力", "AIDC": "AI算力", "IDC": "AI算力",
    "GPU": "AI算力", "NPU": "AI算力", "服务器": "AI算力",
    "半导体": "半导体", "芯片": "半导体", "存储芯片": "半导体",
    "先进封装": "半导体", "HBM": "半导体", "光刻": "半导体",
    "封测": "半导体", "晶圆": "半导体",
    "PCB": "PCB/电子", "覆铜板": "PCB/电子", "铜箔": "PCB/电子",
    "MLCC": "PCB/电子", "电容": "PCB/电子", "电阻": "PCB/电子",
    "光模块": "光通信", "光通信": "光通信", "光纤": "光通信",
    "CPO": "光通信", "硅光": "光通信", "光缆": "光通信",
    "锂电池": "新能源", "磷酸铁锂": "新能源", "固态电池": "新能源",
    "储能": "新能源", "光伏": "新能源", "充电桩": "新能源",
    "氢能": "新能源", "钠离子电池": "新能源",
    "机器人": "机器人", "人形机器人": "机器人", "减速器": "机器人",
    "商业航天": "航天军工", "卫星": "航天军工", "低空经济": "航天军工",
    "无人机": "航天军工", "eVTOL": "航天军工", "军工": "航天军工",
    "粉体": "新材料", "粉体材料": "新材料", "锡粉": "新材料",
    "镍粉": "新材料", "铜粉": "新材料", "散热": "新材料",
    "导热": "新材料", "3D打印": "新材料",
}

EVENT_PATTERNS = [
    ("产品突破", r'(?:突破|攻克|实现|完成)(?:了)?(?:重大)?(?:技术|工艺|产品)', 5),
    ("切入供应链", r'(?:切入|进入|打入|供货|供应)(?:.*?)(?:供应链|产业链|客户|头部)', 5),
    ("产能达产", r'(?:达产|投产|量产|爬产|放量|扩产)', 4),
    ("客户验证", r'(?:验证|送样|测试|认证|审核)(?:中|阶段|通过)', 3),
    ("联合开发", r'(?:联合|合作|共同)(?:开发|研制|研发)', 4),
    ("获得订单", r'(?:获得|斩获|签订|中标)(?:.*?)(?:订单|合同|项目)', 4),
    ("业绩拐点", r'(?:拐点|反转|高增长|超预期|创新高)', 3),
    ("政策利好", r'(?:政策|补贴|支持|扶持|规划)(?:利好|受益|拉动)', 2),
    ("行业景气", r'(?:景气|高景气|需求爆发|需求拉动|量价齐升)', 3),
]


def parse_vip_article(title, brief=""):
    """将VIP文章解析为结构化信号

    输出一篇文章的完整结构化信息：
    {
        "domain": "新材料",           # 主领域/热点
        "all_domains": ["AI算力", "半导体", "PCB/电子", "光通信", "新材料"],
        "business_clues": [           # 业务线索（复合短语）
            "PCB镀铜", "光模块锡粉", "芯片散热", "MLCC镍粉"
        ],
        "event_types": [              # 事件类型+权重
            {"type": "产品突破", "weight": 5},
            {"type": "切入供应链", "weight": 5}
        ],
        "market_constraint": "科创板", # 板块约束
    }
    """
    text = f"{title} {brief}"

    # 领域/热点识别
    domain_counts = defaultdict(int)
    for keyword, domain in DOMAIN_MAP.items():
        if keyword in text:
            domain_counts[domain] += 1
    primary_domain = max(domain_counts, key=domain_counts.get) if domain_counts else "未分类"
    all_domains = set(domain_counts.keys())

    # 业务线索提取
    business_clues = _extract_business_clues(title, brief)

    # 事件类型识别
    event_types = []
    event_keywords = []
    for event_name, pattern, weight in EVENT_PATTERNS:
        matches = re.findall(pattern, text)
        if matches:
            event_types.append({"type": event_name, "weight": weight, "count": len(matches)})
            event_keywords.extend(matches)

    # 板块约束
    market_constraint = ""
    if "科创板" in text:
        market_constraint = "科创板"
    elif "创业板" in text:
        market_constraint = "创业板"
    elif "主板" in text:
        market_constraint = "主板"

    return {
        "domain": primary_domain,
        "all_domains": list(all_domains),
        "business_clues": business_clues,
        "event_types": event_types,
        "event_keywords": event_keywords,
        "market_constraint": market_constraint,
        "raw_text": text,
    }


def _extract_business_clues(title, brief=""):
    """提取业务线索（复合短语）

    与v3的区别：
    - v3: 提取概念关键词（"光模块"、"芯片"、"粉体"）
    - v4: 提取业务线索（"PCB镀铜"、"光模块锡粉"、"芯片散热"、"MLCC镍粉"）
    """
    text = f"{title} {brief}"
    raw_clues = []

    # 1. "XX+XX+XX"并列结构
    compound_matches = re.findall(
        r'([\u4e00-\u9fa5A-Za-z0-9]{2,8}[+#][\u4e00-\u9fa5A-Za-z0-9]{2,8}(?:[+#][\u4e00-\u9fa5A-Za-z0-9]{2,8})*)',
        text
    )
    for compound in compound_matches:
        parts = re.split(r'[+#、,，]', compound)
        raw_clues.extend([p.strip() for p in parts if len(p.strip()) >= 2])

    # 2. "切入/应用于/用于+XX"结构
    action_matches = re.findall(
        r'(?:切入|应用于|用于|开拓|布局|量产|送样|验证|拉动|突破)([\u4e00-\u9fa5A-Za-z0-9]{2,10})',
        text
    )
    raw_clues.extend(action_matches)

    # 3. 带后缀的业务名词
    suffix_matches = re.findall(
        r'([\u4e00-\u9fa5]{2,6}(?:领域|制程|环节|场景|产线|材料|产品|市场|赛道))',
        text
    )
    raw_clues.extend(suffix_matches)

    # 4. 清理线索
    clues = []
    for c in raw_clues:
        cleaned = re.sub(r'^(?:经|已|的|在|为|是|有|将|正|可|能|会|被|与|和|及|或|但|而|这家公司产品|产品)', '', c)
        cleaned = re.sub(r'(?:领域|制程|环节|场景|产线|需求|市场|赛道)$', '', cleaned)
        if len(cleaned) >= 2:
            clues.append(cleaned)

    # 5. 兜底：概念词典补充
    try:
        from vip_extractor import CONCEPT_KEYWORDS
        for kw in CONCEPT_KEYWORDS:
            if kw in text and not any(kw in c or c in kw for c in clues):
                clues.append(kw)
    except ImportError:
        pass

    # 去重
    seen = set()
    unique = []
    for c in clues:
        if c not in seen and len(c) >= 2:
            seen.add(c)
            unique.append(c)

    return unique[:15]


# ========== 第二层：多源动态搜索 ==========

# 源权重：动态信息权重最高
SOURCE_WEIGHTS = {
    "web_search": 5,       # WebSearch（互动易Q&A、新闻）← 最及时最权威
    "eastmoney_ann": 5,    # 东方财富公告（公司官方披露）← 最权威
    "cls_telegraph": 4,    # CLS电报（实时采集）
    "fxbaogao_ir": 4,     # fxbaogao调研纪要（公司最新动态）
    "fxbaogao_report": 3, # fxbaogao研报（深度分析）
    "tushare_mainbiz": 1, # Tushare主营业务（静态信息）
}


# ----- 数据源1: 东方财富公告API（免费，含全文）-----

def search_eastmoney_announcements(ts_code, page_size=10):
    """通过东方财富公告API获取公司最新公告

    API: https://np-anotice-stock.eastmoney.com/api/security/ann
    免费无需认证，返回公告列表+全文内容

    Args:
        ts_code: 股票代码 (如 "688456" 或 "688456.SH")
        page_size: 返回公告数量

    Returns:
        list: [{"source", "title", "date", "content", "art_code"}, ...]
    """
    code = ts_code.split(".")[0] if "." in ts_code else ts_code

    url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
    params = {
        "sr": "-1",
        "page_size": str(page_size),
        "page_index": "1",
        "ann_type": "A",
        "client_source": "web",
        "stock_list": code,
        "f_node": "0",
        "s_node": "0",
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        result = resp.json()
        data_list = result.get("data", {}).get("list", [])

        announcements = []
        for item in data_list:
            title = item.get("title", "")
            notice_date = item.get("notice_date", "")
            art_code = item.get("art_code", "")

            content = ""
            if art_code:
                detail_url = f"https://np-cnotice-stock.eastmoney.com/api/content/ann?art_code={art_code}&client_source=web&page_index=1"
                try:
                    detail_resp = requests.get(detail_url, timeout=10)
                    detail = detail_resp.json()
                    raw_content = detail.get("data", {}).get("notice_content", "")
                    if raw_content:
                        content = re.sub(r'<[^>]+>', '', raw_content)
                        content = re.sub(r'\s+', ' ', content).strip()
                except:
                    pass

            announcements.append({
                "source": "eastmoney_ann",
                "title": title,
                "date": notice_date,
                "content": content,
                "art_code": art_code,
            })

        return announcements
    except Exception as e:
        print(f"    [东财公告] 获取失败: {e}")
        return []


# ----- 数据源2: WebSearch（互动易Q&A、新闻）-----

def build_web_search_queries(company_name, clues):
    """构建WebSearch查询关键词

    Agent在主流程中通过WebSearch工具执行这些查询，
    将结果通过web_search_results参数传入discover_stocks_v4()。

    使用示例:
      from vip_search_v4 import build_web_search_queries, discover_stocks_v4
      queries = build_web_search_queries("有研粉材", ["PCB镀铜", "光模块锡粉"])
      results = []
      for q in queries:
          web_results = WebSearch(q)  # Agent调用WebSearch工具
          results.extend(web_results)
      kept, excl = discover_stocks_v4(title, brief, web_search_results=results)
    """
    core_clues = [c for c in clues if len(c) >= 3][:3]

    queries = [
        # 互动易Q&A（最权威的公司动态确认）
        f"{company_name} 互动易 投资者问答 {' '.join(core_clues[:2])}",
        # 公司新闻+业务线索
        f"{company_name} {' '.join(core_clues)} 最新消息",
        # 公告+业务线索
        f"{company_name} 公告 {core_clues[0] if core_clues else ''}",
    ]

    return queries


def parse_web_search_results(web_results):
    """将WebSearch返回的结果解析为标准格式"""
    parsed = []
    for result in web_results:
        if isinstance(result, dict):
            title = result.get("title", "")
            snippet = result.get("snippet", "") or result.get("content", "")
            url = result.get("url", "") or result.get("website_link", "")
            parsed.append({
                "source": "web_search",
                "title": title,
                "content": f"{title} {snippet}",
                "url": url,
            })
        elif isinstance(result, str):
            parsed.append({
                "source": "web_search",
                "title": "",
                "content": result,
                "url": "",
            })

    return parsed


# ----- 数据源3: CLS电报（本地DB）-----

def search_cls_telegraph(clues, db_path=None):
    """在CLS电报数据库中搜索与业务线索相关的电报"""
    if db_path is None:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "stock.db")
    if not os.path.exists(db_path):
        return []

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    results = []
    for clue in clues:
        if len(clue) < 2:
            continue
        cur.execute(
            "SELECT telegraph_id, title, is_red, timestamp "
            "FROM cls_telegraph WHERE title LIKE ? "
            "ORDER BY timestamp DESC LIMIT 5",
            (f'%{clue}%',)
        )
        for row in cur.fetchall():
            results.append({
                "source": "cls_telegraph",
                "title": row[1],
                "content": row[1],
                "is_red": bool(row[2]),
                "timestamp": row[3],
            })

    conn.close()

    # 去重
    seen_titles = set()
    unique = []
    for r in results:
        if r["title"] not in seen_titles:
            seen_titles.add(r["title"])
            unique.append(r)

    return unique


# ----- 数据源4: fxbaogao研报+调研纪要 -----
# 支持两种模式：
#   1. MCP模式（推荐）：Agent通过run_mcp调用mcp_fxbaogao，将结果传入mcp_fxbaogao_results
#   2. HTTP降级模式：脚本独立运行时通过HTTP API直接调用

def search_fxbaogao_reports(company_name, mcp_results=None):
    """搜索fxbaogao研报和调研纪要

    Args:
        company_name: 公司名称
        mcp_results: Agent通过run_mcp调用mcp_fxbaogao的search_reports工具返回的结果
            格式: [{"reportId": 123, "title": "...", "orgName": "...",
                    "pubTimeStr": "...", "paragraphs": [...]}, ...]
            如果传入此参数，将跳过HTTP API调用

    Agent调用示例:
      # Agent在主流程中通过run_mcp调用
      run_mcp("mcp_fxbaogao", "search_reports", {"keywords": "有研粉材"})
      # 将返回结果传入
      discover_stocks_v4(title, brief, mcp_fxbaogao_results=results)
    """
    # MCP模式：使用Agent传入的结果
    if mcp_results is not None:
        return _parse_fxbaogao_mcp_results(mcp_results)

    # HTTP降级模式：直接调用API
    api_key = get_fxbaogao_api_key()
    if not api_key:
        print(f"    [fxbaogao] 无API key，跳过（建议使用MCP模式）")
        return []

    url = "https://api.fxbaogao.com/mcp/"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "search_reports",
            "arguments": {"keywords": company_name},
        },
        "id": 1,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        data = resp.json()
        result = data.get("result", {})
        if result.get("isError"):
            return []

        content_list = result.get("content", [])
        reports_text = ""
        for item in content_list:
            if isinstance(item, dict) and item.get("type") == "text":
                reports_text += item.get("text", "")

        if not reports_text:
            return []

        reports_data = json.loads(reports_text)
        reports = reports_data if isinstance(reports_data, list) else reports_data.get("reports", [])
        return _parse_fxbaogao_mcp_results(reports)
    except Exception as e:
        print(f"    [fxbaogao] HTTP搜索失败: {e}")
        return []


def _parse_fxbaogao_mcp_results(reports):
    """解析fxbaogao搜索结果（MCP和HTTP共用）"""
    if not isinstance(reports, list):
        return []

    categorized = []
    for report in reports[:10]:
        title = re.sub(r'</?em>', '', report.get("title", ""))
        report_type = "fxbaogao_report"
        if "调研" in title or "纪要" in title or "问答" in title:
            report_type = "fxbaogao_ir"

        categorized.append({
            "source": report_type,
            "report_id": report.get("reportId"),
            "title": title,
            "content": title,
            "org": report.get("orgName", ""),
            "pub_time": report.get("pubTimeStr", ""),
            "paragraphs": report.get("paragraphs", []),
        })

    return categorized


def get_fxbaogao_paragraphs(report_id, keyword, mcp_paragraphs=None):
    """获取研报正文命中段落

    Args:
        report_id: 研报ID
        keyword: 搜索关键词
        mcp_paragraphs: Agent通过run_mcp调用mcp_fxbaogao的get_paragraphs工具返回的结果
            如果传入此参数，将跳过HTTP API调用
    """
    # MCP模式
    if mcp_paragraphs is not None:
        return mcp_paragraphs if isinstance(mcp_paragraphs, list) else []

    # HTTP降级模式
    api_key = get_fxbaogao_api_key()
    if not api_key or not report_id:
        return []

    url = "https://api.fxbaogao.com/mcp/"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "get_paragraphs",
            "arguments": {"reportId": report_id, "keyword": keyword},
        },
        "id": 1,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        data = resp.json()
        result = data.get("result", {})
        content_list = result.get("content", [])
        text = ""
        for item in content_list:
            if isinstance(item, dict) and item.get("type") == "text":
                text += item.get("text", "")
        if text:
            paragraphs = json.loads(text)
            return paragraphs if isinstance(paragraphs, list) else []
        return []
    except:
        return []


# ========== 第三层：加权线索验证 ==========

SYNONYMS = {
    "锡粉": ["锡膏", "锡基焊粉", "焊锡粉", "锡焊粉"],
    "锡膏": ["锡粉", "锡基焊粉", "焊锡粉"],
    "散热": ["热管理", "导热", "液冷", "风冷", "散热器"],
    "镍粉": ["镍", "纳米镍", "MLCC镍粉"],
    "PCB镀铜": ["镀铜制程", "电镀铜", "化学沉铜", "氧化铜粉"],
    "光模块": ["光通信", "光器件", "光引擎"],
    "粉体": ["粉体材料", "金属粉体", "粉末"],
    "芯片": ["半导体", "晶圆", "封装"],
    "算力": ["AI", "GPU", "NPU", "服务器"],
    "先进封装": ["封装", "3D封装", "Chiplet"],
    "MLCC": ["多层陶瓷电容"],
}


def verify_clues_weighted(clues, sources_data):
    """加权线索验证

    对每条业务线索，在多个来源中验证：
    - 不同来源命中同一线索 → 权重叠加
    - 动态信息源权重高于静态信息
    - 同义词匹配（如"锡粉"↔"锡膏"）
    - 复合线索拆分匹配（如"光模块锡粉"→"光模块"+"锡粉"）
    """
    # 合并各来源文本
    source_texts = {}
    for source, items in sources_data.items():
        text = ""
        if source == "tushare_mainbiz":
            text = f"{items.get('main_business', '')} {items.get('business_scope', '')} {items.get('name', '')}"
        elif isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    text += item.get("title", "") + " "
                    text += item.get("content", "") + " "
                    for para in item.get("paragraphs", []):
                        if isinstance(para, dict):
                            text += re.sub(r'</?em>', '', para.get("content", "")) + " "
                        else:
                            text += str(para) + " "
        source_texts[source] = text

    clue_results = []
    total_score = 0

    for clue in clues:
        clean_clue = re.sub(r'^(?:切入|经|已)', '', clue)
        clue_score = 0
        clue_sources = []
        evidence = {}

        for source, text in source_texts.items():
            if not text:
                continue

            matched = False
            match_text = ""

            # 直接匹配
            if clean_clue in text:
                matched = True
                match_text = clean_clue
            elif clue in text:
                matched = True
                match_text = clue
            else:
                # 复合线索拆分匹配
                cn_parts = re.findall(r'[\u4e00-\u9fa5]{2,4}', clean_clue)
                en_parts = re.findall(r'[A-Za-z]{2,8}', clean_clue)
                parts = en_parts + cn_parts

                if len(parts) >= 2:
                    found_parts = [p for p in parts if p in text]
                    if len(found_parts) >= max(1, len(parts) // 2):
                        matched = True
                        match_text = found_parts[0] if found_parts else clean_clue

                # 同义词匹配
                if not matched:
                    for part in parts:
                        syns = SYNONYMS.get(part, [])
                        for syn in syns:
                            if syn in text:
                                matched = True
                                match_text = syn
                                break
                        if matched:
                            break

            if matched:
                weight = SOURCE_WEIGHTS.get(source, 1)
                clue_score += weight
                clue_sources.append(source)

                # 提取证据
                idx = text.find(match_text)
                if idx >= 0:
                    start = max(0, idx - 20)
                    end = min(len(text), idx + len(match_text) + 50)
                    ev = text[start:end].replace("\n", " ").strip()
                    evidence[source] = ev[:100]

        if clue_score > 0:
            multi_source_bonus = len(clue_sources) * 2
            clue_score += multi_source_bonus

            clue_results.append({
                "clue": clue,
                "matched": True,
                "score": clue_score,
                "sources": clue_sources,
                "source_count": len(clue_sources),
                "evidence": evidence,
            })
            total_score += clue_score
        else:
            clue_results.append({
                "clue": clue,
                "matched": False,
                "score": 0,
                "sources": [],
                "source_count": 0,
                "evidence": {},
            })

    matched_count = sum(1 for r in clue_results if r["matched"])
    match_rate = matched_count / len(clues) if clues else 0

    return {
        "clue_results": clue_results,
        "total_score": total_score,
        "matched_count": matched_count,
        "total_clues": len(clues),
        "match_rate": match_rate,
    }


# ========== 第四层：候选股票发现 + 排除逻辑 ==========

def discover_candidates(parsed_article, stock_db):
    """发现候选股票（Tushare初筛）"""
    clues = parsed_article["business_clues"]
    market_constraint = parsed_article["market_constraint"]

    if market_constraint == "科创板":
        candidates = [s for s in stock_db if s.get("symbol", "").startswith("688")]
    elif market_constraint == "创业板":
        candidates = [s for s in stock_db if s.get("symbol", "").startswith("300")]
    elif market_constraint == "主板":
        candidates = [s for s in stock_db if s.get("symbol", "").startswith(("60", "00"))]
    else:
        candidates = stock_db

    preliminary = []
    for stock in candidates:
        main_biz = stock.get("main_business", "") or ""
        biz_scope = stock.get("business_scope", "") or ""
        name = stock.get("name", "")
        industry = stock.get("industry", "") or ""
        combined = f"{main_biz} {biz_scope} {name} {industry}"

        score = 0
        matched_any = False

        for clue in clues:
            clean_clue = re.sub(r'^(?:切入|经|已)', '', clue)

            if clean_clue in combined or clue in combined:
                score += 5
                matched_any = True
                continue

            cn_parts = re.findall(r'[\u4e00-\u9fa5]{2,4}', clean_clue)
            en_parts = re.findall(r'[A-Za-z]{2,8}', clean_clue)
            parts = en_parts + cn_parts

            if parts:
                partial = sum(1 for p in parts if p in combined)
                if partial > 0:
                    score += partial * 2
                    matched_any = True

            for part in parts:
                for syn in SYNONYMS.get(part, []):
                    if syn in combined:
                        score += 3
                        matched_any = True
                        break

            if len(clean_clue) >= 2 and clean_clue[:2] in name:
                score += 1
                matched_any = True

        if matched_any:
            preliminary.append((stock, score))

    preliminary.sort(key=lambda x: x[1], reverse=True)
    return preliminary[:30]


def check_exclusion_v4(stock_name, clue_verification, parsed_article):
    """排除逻辑

    排除规则：
    1. 匹配率 < 25% → 排除
    2. 仅匹配通用概念，未匹配任何具体业务线索 → 排除
    """
    match_rate = clue_verification["match_rate"]
    matched = [r for r in clue_verification["clue_results"] if r["matched"]]

    if match_rate < 0.25:
        return True, f"匹配率过低({match_rate:.0%})"

    generic_words = {"粉体", "粉体材料", "芯片", "算力", "光模块",
                     "半导体", "PCB", "MLCC", "材料", "产品", "市场",
                     "先进封装", "封装"}
    specific_matched = [r for r in matched if r["clue"] not in generic_words
                        and not any(g in r["clue"] for g in generic_words)]
    if not specific_matched and clue_verification["total_clues"] > 3:
        return True, f"仅匹配通用概念: {[r['clue'] for r in matched]}"

    # 证据质量检查 - 证据来自目录/图表
    for r in matched:
        for src, ev in r.get("evidence", {}).items():
            if re.search(r'\.{5,}|图\d+|表\d+', ev):
                r["score"] = max(0, r["score"] - 2)

    return False, ""


# ========== 主函数 ==========

def discover_stocks_v4(title, brief="", market_filter="",
                       web_search_results=None, mcp_fxbaogao_results=None,
                       mcp_tushare_stock_db=None):
    """v4股票发现完整流程

    MCP集成参数（Agent调用时传入，优先于HTTP API）:
        web_search_results: WebSearch工具搜索结果
            格式: [{"title": "...", "snippet": "...", "url": "..."}, ...]
        mcp_fxbaogao_results: mcp_fxbaogao的search_reports结果
            格式: {"公司名": [研报列表], ...}
            或单个公司的研报列表 [{"reportId": ..., "title": ...}, ...]
        mcp_tushare_stock_db: mcp_tushareMcp获取的股票数据库
            格式: [{"ts_code": "...", "name": "...", "main_business": "...", ...}, ...]
            如果传入，将跳过tushare Python库调用

    数据源优先级:
        1. MCP传入结果（Agent模式，推荐）
        2. HTTP API直接调用（独立运行模式，降级）

    流程:
    1. 解析VIP文章 → 结构化信号
    2. Tushare初筛候选股票（MCP或Python库）
    3. 对每个候选，多源搜索：
       a. 东方财富公告API（自动，无MCP）
       b. WebSearch结果（Agent传入）
       c. fxbaogao研报+调研纪要（MCP或HTTP）
       d. CLS电报（本地DB）
    4. 加权线索验证
    5. 排除逻辑 + 排序
    """
    print(f"\n{'='*70}")
    print(f"v4 VIP股票发现（多源动态搜索）")
    print(f"标题: {title[:80]}")
    print(f"{'='*70}")

    # Step 1: 解析VIP文章
    parsed = parse_vip_article(title, brief)
    if not market_filter:
        market_filter = parsed.get("market_constraint", "")

    print(f"\n[Step 1] VIP文章结构化解析:")
    print(f"  领域/热点: {parsed['domain']}")
    print(f"  全部领域: {parsed['all_domains']}")
    print(f"  业务线索: {parsed['business_clues']}")
    print(f"  事件类型: {[e['type'] for e in parsed['event_types']]}")
    print(f"  板块约束: {market_filter}")

    clues = parsed["business_clues"]

    # Step 2: Tushare初筛候选（MCP优先，Python库降级）
    if mcp_tushare_stock_db:
        stock_db = mcp_tushare_stock_db
        print(f"\n[Step 2] Tushare MCP数据: {len(stock_db)} 只股票")
    else:
        from vip_extractor import load_stock_database
        stock_db = load_stock_database()
    candidates = discover_candidates(parsed, stock_db)
    print(f"  初筛: {len(candidates)} 只候选")
    for stock, score in candidates[:10]:
        print(f"  {stock.get('name'):10s} {stock.get('ts_code'):12s} 初筛分={score}")

    # 解析WebSearch结果
    web_results_parsed = []
    if web_search_results:
        web_results_parsed = parse_web_search_results(web_search_results)
        print(f"\n[WebSearch] 接收到 {len(web_results_parsed)} 条搜索结果")

    # Step 3: 对Top候选，多源搜索 + 线索验证
    results = []
    for stock, prelim_score in candidates[:15]:
        name = stock.get("name", "")
        ts_code = stock.get("ts_code", "")

        print(f"\n  [{name} {ts_code}] 多源搜索中...")

        # 3a: 东方财富公告API（自动）
        eastmoney_anns = search_eastmoney_announcements(ts_code, page_size=10)
        print(f"    东财公告: {len(eastmoney_anns)} 条")

        # 3b: WebSearch结果（Agent传入）
        company_web_results = [
            r for r in web_results_parsed
            if name in r.get("content", "") or name in r.get("title", "")
        ]
        if company_web_results:
            print(f"    WebSearch匹配: {len(company_web_results)} 条")

        # 3c: fxbaogao研报搜索（MCP优先，HTTP降级）
        # 如果Agent传入了按公司名索引的MCP结果，直接使用
        company_fxbaogao_mcp = None
        if mcp_fxbaogao_results:
            if isinstance(mcp_fxbaogao_results, dict):
                company_fxbaogao_mcp = mcp_fxbaogao_results.get(name)
            elif isinstance(mcp_fxbaogao_results, list):
                company_fxbaogao_mcp = mcp_fxbaogao_results

        fxbaogao_results = search_fxbaogao_reports(name, mcp_results=company_fxbaogao_mcp)
        fxbaogao_ir = [r for r in fxbaogao_results if r["source"] == "fxbaogao_ir"]
        fxbaogao_reports = [r for r in fxbaogao_results if r["source"] == "fxbaogao_report"]
        mcp_mode = "MCP" if company_fxbaogao_mcp is not None else "HTTP"
        print(f"    fxbaogao({mcp_mode}): {len(fxbaogao_ir)} 篇调研纪要, {len(fxbaogao_reports)} 篇研报")

        # 获取更多段落
        all_fxbaogao = fxbaogao_ir + fxbaogao_reports
        if all_fxbaogao and all_fxbaogao[0].get("report_id"):
            report_id = all_fxbaogao[0]["report_id"]
            core_clues = [c for c in clues if len(c) >= 3][:4]
            for clue in core_clues:
                extra = get_fxbaogao_paragraphs(report_id, clue)
                if extra:
                    all_fxbaogao[0]["paragraphs"].extend(extra)

        # 3d: CLS电报搜索
        cls_results = search_cls_telegraph(clues)
        if cls_results:
            print(f"    CLS电报: {len(cls_results)} 条相关电报")

        # 3e: 准备多源数据
        sources_data = {
            "eastmoney_ann": eastmoney_anns,
            "web_search": company_web_results,
            "fxbaogao_ir": fxbaogao_ir,
            "fxbaogao_report": fxbaogao_reports,
            "cls_telegraph": cls_results,
            "tushare_mainbiz": {
                "main_business": stock.get("main_business", ""),
                "business_scope": stock.get("business_scope", ""),
                "name": name,
            },
        }

        # 3f: 加权线索验证
        verification = verify_clues_weighted(clues, sources_data)

        if verification["total_score"] > 0:
            should_exclude, exclude_reason = check_exclusion_v4(
                name, verification, parsed
            )

            results.append({
                "stock_name": name,
                "stock_code": ts_code,
                "industry": stock.get("industry", ""),
                "domain": parsed["domain"],
                "event_types": [e["type"] for e in parsed["event_types"]],
                "match_rate": verification["match_rate"],
                "total_score": verification["total_score"],
                "matched_clues": [r["clue"] for r in verification["clue_results"] if r["matched"]],
                "clue_details": verification["clue_results"],
                "source_summary": {
                    r["clue"]: r["sources"]
                    for r in verification["clue_results"] if r["matched"]
                },
                "source_counts": {
                    "eastmoney_ann": len(eastmoney_anns),
                    "web_search": len(company_web_results),
                    "fxbaogao_ir": len(fxbaogao_ir),
                    "fxbaogao_report": len(fxbaogao_reports),
                    "cls_telegraph": len(cls_results),
                },
                "excluded": should_exclude,
                "exclude_reason": exclude_reason,
                "prelim_score": prelim_score,
            })

            status = "排除" if should_exclude else "保留"
            print(f"    → 匹配率: {verification['match_rate']:.0%}, 总分: {verification['total_score']} ({status})")

    # Step 4: 排序
    results.sort(key=lambda x: x["total_score"], reverse=True)
    kept = [r for r in results if not r["excluded"]]
    excluded = [r for r in results if r["excluded"]]

    # 输出结果
    print(f"\n{'='*70}")
    print(f"最终结果: 保留 {len(kept)} 只, 排除 {len(excluded)} 只")
    print(f"{'='*70}")

    for r in kept:
        print(f"\n  ★ {r['stock_name']} ({r['stock_code']})")
        print(f"    领域: {r['domain']} | 事件: {r['event_types']}")
        print(f"    匹配率: {r['match_rate']:.0%} | 总分: {r['total_score']}")
        print(f"    数据源: {r['source_counts']}")
        print(f"    已匹配线索: {r['matched_clues']}")
        for detail in r["clue_details"]:
            if detail["matched"]:
                sources_str = ", ".join(detail["sources"])
                print(f"      [{detail['clue']}] 分={detail['score']} 来源={sources_str}")
                for src, ev in detail["evidence"].items():
                    print(f"        ({src}) {ev[:80]}...")

    if excluded:
        print(f"\n  排除的股票:")
        for r in excluded:
            print(f"  ✗ {r['stock_name']} - {r['exclude_reason']}")

    return kept, excluded


def discover_stocks_from_vip_article(title, brief="", related_stock="",
                                       web_search_results=None):
    """v3兼容入口：从VIP文章发现股票

    保持与vip_extractor.discover_stocks_by_article()相同的调用方式，
    但内部使用v4多源搜索逻辑。

    可直接替换vip_extractor.py中的discover_stocks_by_article()。
    """
    return discover_stocks_v4(title, brief, web_search_results=web_search_results)


# ========== 测试 ==========

if __name__ == "__main__":
    title = ("算力芯片、光模块、先进封装拉动粉体材料需求，"
             "这家公司产品已经切入PCB镀铜+光模块锡粉+芯片散热领域，"
             "未来有望在MLCC镍粉突破")

    print("\n" + "="*70)
    print("测试: 有研粉材案例")
    print("="*70)
    kept, excl = discover_stocks_v4(title, market_filter="科创板")
