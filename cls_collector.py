#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
财联社数据独立采集器 — 每小时定时任务

与报告生成流程解耦，独立运行：
  - 电报（telegraph）: /api/cache?name=telegraph
  - VIP文章: /featured/v1/home/assembled + /featured/v2/home/recommend/article
  - 深度头条: /v3/depth/home/assembled/1000
  - 投资日历: /api/calendar/web/list

每小时执行一次，增量去重写入数据库。
电报主时间戳使用电报发布时间（ctime），非采集时间。

结构化处理：
  - event_type: 政策/财报/并购/研报/数据/公告 (关键词匹配)
  - sentiment: positive/negative/neutral (关键词匹配)
  - impact_level: high/medium/low (红色标记+关键词)
  - sector_tags: 行业标签提取

用法:
  python cls_collector.py              # 采集全部
  python cls_collector.py --telegraph  # 只采集电报
  python cls_collector.py --vip         # 只采集VIP文章+股票发现
  python cls_collector.py --stats       # 查看数据库统计

定时任务设置:
  Schedule cron: 0 * * * * (每小时整点)
  命令: python /workspace/stock-assistant/cls_collector.py
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import datetime

import requests

# 项目内模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import DB
from settings import get_fxbaogao_api_key

# ---------------------------------------------------------------------------
# CLS API 签名与请求（与 fetch_data.py 独立，避免循环导入）
# ---------------------------------------------------------------------------

_CLS_DEFAULT_SV = '8.7.9'
_cls_detected_sv = None

_CLS_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://www.cls.cn/',
    'Accept': 'application/json, text/plain, */*',
}


def _cls_sign(params: dict) -> str:
    """CLS API签名: sort → urlencode → SHA1 → MD5"""
    sorted_params = dict(sorted(params.items()))
    query_string = urllib.parse.urlencode(sorted_params)
    sha1_hash = hashlib.sha1(query_string.encode('utf-8')).hexdigest()
    return hashlib.md5(sha1_hash.encode('utf-8')).hexdigest()


def _cls_detect_sv() -> str:
    """从财联社首页自动检测当前 sv 版本号"""
    global _cls_detected_sv
    if _cls_detected_sv:
        return _cls_detected_sv
    try:
        resp = requests.get('https://www.cls.cn/', timeout=10, headers=_CLS_HEADERS)
        script_srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', resp.text)
        for src in script_srcs:
            m = re.search(r'(\d+\.\d+\.\d+)', src)
            if m:
                _cls_detected_sv = m.group(1)
                return _cls_detected_sv
    except Exception:
        pass
    return _CLS_DEFAULT_SV


def _cls_api_get(path: str, extra_params: dict = None,
                 base: str = 'https://www.cls.cn'):
    """调用CLS API（自动签名+sv降级）"""
    def _try(sv_value):
        params = {'app': 'CailianpressWeb', 'os': 'web', 'sv': sv_value}
        if extra_params:
            params.update(extra_params)
        params['sign'] = _cls_sign(params)
        try:
            resp = requests.get(f"{base}{path}", params=params,
                                headers=_CLS_HEADERS, timeout=15)
            data = resp.json()
            if data.get('error') == 0 or data.get('errno') == 0 or 'data' in data:
                return data.get('data')
        except Exception:
            pass
        return None

    result = _try(_CLS_DEFAULT_SV)
    if result is not None:
        return result
    detected = _cls_detect_sv()
    if detected and detected != _CLS_DEFAULT_SV:
        result = _try(detected)
        if result is not None:
            return result
    return None


# ---------------------------------------------------------------------------
# 电报结构化 NLP（轻量级，无需外部模型）
# ---------------------------------------------------------------------------

# 事件类型关键词
EVENT_KEYWORDS = {
    "政策": ["政策", "规划", "方案", "意见", "通知", "条例", "补贴", "减税",
             "国务院", "发改委", "工信部", "财政部", "央行", "证监会",
             "支持", "鼓励", "推进", "加快", "促进"],
    "财报": ["业绩", "营收", "净利润", "同比增长", "环比", "季报", "年报",
             "半年报", "预增", "预减", "扭亏", "亏损", "分红", "送转"],
    "并购": ["收购", "并购", "重组", "合并", "增持", "减持", "回购",
             "股权转让", "定增", "增发", "入股"],
    "研报": ["研报", "评级", "买入", "增持", "推荐", "目标价",
             "券商", "机构预计", "分析师"],
    "数据": ["PMI", "CPI", "PPI", "GDP", "社融", "M2", "进出口",
             "贸易顺差", "发电量", "用电量", "固定资产投资"],
    "公告": ["公告", "进展", "签订", "中标", "合同", "项目",
             "投产", "达产", "获批", "批准", "许可"],
}

# 情感关键词
POSITIVE_KEYWORDS = [
    "利好", "增长", "上涨", "突破", "超预期", "创新高", "大涨",
    "涨停", "爆发", "景气", "回暖", "复苏", "强劲", "加速",
    "获批", "中标", "签订", "合作", "增持", "回购", "分红",
    "支持", "鼓励", "补贴", "减税", "优惠",
]
NEGATIVE_KEYWORDS = [
    "利空", "下跌", "下跌", "暴跌", "熔断", "崩盘", "闪崩",
    "亏损", "预减", "下降", "萎缩", "下滑", "疲软", "低迷",
    "违规", "处罚", "警示", "立案", "退市", "st", "减持",
    "违约", "爆雷", "爆仓", "质押", "平仓",
]

# 高影响关键词（与红色标记组合判断impact_level）
HIGH_IMPACT_KEYWORDS = [
    "熔断", "暂停", "限制", "制裁", "关税", "降息", "加息",
    "国务院", "央行", "证监会", "重大", "突发", "紧急",
]


def classify_event_type(text: str) -> str:
    """分类事件类型"""
    scores = {}
    for event_type, keywords in EVENT_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scores[event_type] = score
    if scores:
        return max(scores, key=scores.get)
    return "其他"


def classify_sentiment(text: str) -> str:
    """分类情感倾向"""
    pos = sum(1 for kw in POSITIVE_KEYWORDS if kw in text)
    neg = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text)
    if pos > neg:
        return "positive"
    elif neg > pos:
        return "negative"
    return "neutral"


def classify_impact(text: str, is_red: bool) -> str:
    """分类影响级别"""
    if is_red:
        return "high"
    high_kw = sum(1 for kw in HIGH_IMPACT_KEYWORDS if kw in text)
    if high_kw >= 2:
        return "high"
    elif high_kw >= 1:
        return "medium"
    return "low"


def extract_sector_tags(text: str) -> str:
    """从文本中提取行业/主题标签"""
    SECTORS = [
        "半导体", "芯片", "AI", "人工智能", "算力", "光模块", "CPO",
        "新能源", "光伏", "储能", "锂电", "风电", "氢能",
        "医药", "创新药", "医疗器械", "中药",
        "军工", "航天", "卫星", "无人机",
        "房地产", "建材", "基建",
        "金融", "银行", "证券", "保险",
        "汽车", "智能驾驶", "机器人",
        "消费", "白酒", "食品", "旅游",
        "煤炭", "有色", "钢铁", "化工",
        "电力", "电网", "特高压",
        "农业", "种业", "化肥",
        "通信", "5G", "6G",
        "PCB", "消费电子", "面板",
        "商业航天", "低空经济", "eVTOL",
    ]
    tags = [s for s in SECTORS if s in text]
    return ",".join(tags[:5]) if tags else ""


# ---------------------------------------------------------------------------
# 电报采集
# ---------------------------------------------------------------------------

def collect_telegraphs(db: DB) -> dict:
    """采集财联社电报，结构化后写入数据库

    Returns:
        dict: {fetched, new_count, skipped_count, red_count}
    """
    print("\n[电报] 开始采集...")
    try:
        url = "https://www.cls.cn/api/cache?app=CailianpressWeb&name=telegraph&os=web&sv=" + _CLS_DEFAULT_SV
        resp = requests.get(url, headers=_CLS_HEADERS, timeout=15)
        data = resp.json()
        roll_data = data.get('data', {}).get('roll_data', [])
    except Exception as e:
        print(f"[电报] 采集失败: {e}")
        return {"fetched": 0, "new_count": 0, "skipped_count": 0, "red_count": 0}

    if not roll_data:
        print("[电报] 无数据")
        return {"fetched": 0, "new_count": 0, "skipped_count": 0, "red_count": 0}

    print(f"[电报] API返回 {len(roll_data)} 条")

    new_count = 0
    skipped = 0
    red_count = 0

    for item in roll_data:
        # 提取字段
        telegraph_id = str(item.get('id', ''))
        if not telegraph_id:
            continue

        title = (item.get('title', '') or '').strip()
        content = (item.get('content', '') or '').strip()
        ctime = int(item.get('ctime', 0))
        is_red = 1 if (item.get('color', '') == 'red' or
                        item.get('level', '') == 'red') else 0
        stock_list = item.get('stock_list', [])

        if not title and content:
            title = content[:50]

        # 结构化分析
        full_text = f"{title} {content}"
        event_type = classify_event_type(full_text)
        sentiment = classify_sentiment(full_text)
        impact_level = classify_impact(full_text, bool(is_red))
        sector_tags = extract_sector_tags(full_text)

        # 写入数据库
        telegraph_item = {
            "telegraph_id": telegraph_id,
            "title": title[:200],
            "content": content[:500],
            "timestamp": ctime,
            "is_red": is_red,
            "event_type": event_type,
            "sentiment": sentiment,
            "impact_level": impact_level,
            "sector_tags": sector_tags,
        }

        is_new = db.upsert_telegraph(telegraph_item)
        if is_new:
            new_count += 1
            if is_red:
                red_count += 1

            # 写入关联股票
            if stock_list:
                stocks = []
                for s in stock_list:
                    if isinstance(s, dict):
                        stocks.append({
                            "name": s.get('name', ''),
                            "code": s.get('code', ''),
                        })
                if stocks:
                    db.upsert_telegraph_stocks(telegraph_id, stocks)
        else:
            skipped += 1

    print(f"[电报] 采集完成: 新增 {new_count} 条, 跳过 {skipped} 条(已存在), 红色 {red_count} 条")
    return {
        "fetched": len(roll_data),
        "new_count": new_count,
        "skipped_count": skipped,
        "red_count": red_count,
    }


# ---------------------------------------------------------------------------
# VIP文章采集 + 股票发现
# ---------------------------------------------------------------------------

# 模块级缓存：Tushare股票数据库（避免每篇文章重复加载5530只股票）
_tushare_stock_cache = None
_tushare_pro_cache = None


def _get_tushare_stock_db():
    """获取Tushare股票数据库（带缓存）"""
    global _tushare_stock_cache, _tushare_pro_cache
    if _tushare_stock_cache is not None:
        return _tushare_stock_cache, _tushare_pro_cache
    try:
        from vip_extractor import load_stock_database
        import tushare as ts
        from settings import get_tushare_token
        ts.set_token(get_tushare_token())
        _tushare_pro_cache = ts.pro_api()
        _tushare_stock_cache = load_stock_database(_tushare_pro_cache)
        return _tushare_stock_cache, _tushare_pro_cache
    except Exception as e:
        print(f"  [股票发现] Tushare股票库加载失败: {e}")
        return None, None


def collect_vip_articles(db: DB) -> dict:
    """采集VIP文章，写入数据库，并运行股票发现

    Returns:
        dict: {fetched, new_count, stocks_discovered}
    """
    print("\n[VIP] 开始采集...")
    vip_data = _cls_api_get('/featured/v1/home/assembled')
    vip_articles = []
    seen_ids = set()

    def _add_article(art, source_tag=''):
        art_id = str(art.get('id', art.get('title', '')))
        if art_id and art_id in seen_ids:
            return
        seen_ids.add(art_id)
        vip_articles.append({
            'article_id': art_id,
            'title': art.get('title', ''),
            'brief': (art.get('brief', '') or '')[:300],
            'published_at': datetime.datetime.fromtimestamp(
                art.get('ctime', 0)).strftime('%Y-%m-%d %H:%M:%S') if art.get('ctime') else '',
            'related_stock': art.get('related_stock', ''),
            'source': source_tag,
            '_raw': art,
        })

    if vip_data and isinstance(vip_data, dict):
        for art in vip_data.get('recommend_list', []):
            _add_article(art, 'recommend_list')
        for art in vip_data.get('free_top_v2', []):
            _add_article(art, 'free_top_v2')
        for art in vip_data.get('yellow_article', []):
            _add_article(art, 'yellow_article')

    # 分页采集 (Page 2-5)
    last_time = str(int(time.time()))
    for page in range(2, 6):
        recommend_data = _cls_api_get(
            '/featured/v2/home/recommend/article',
            {'last_time': last_time, 'refresh_Type': '1'}
        )
        if recommend_data and isinstance(recommend_data, list):
            if len(recommend_data) == 0:
                break
            for art in recommend_data:
                _add_article(art, f'recommend_p{page}')
            oldest_ctime = min(a['_raw'].get('ctime', int(time.time()))
                              for a in vip_articles if a.get('_raw', {}).get('ctime'))
            last_time = str(int(oldest_ctime))
        else:
            break

    print(f"[VIP] API返回 {len(vip_articles)} 篇文章")

    new_count = 0
    stocks_discovered = 0

    for article in vip_articles:
        art_id = article['article_id']

        # 写入文章
        is_new = db.upsert_vip_article({
            'article_id': art_id,
            'title': article['title'],
            'brief': article['brief'],
            'published_at': article['published_at'],
            'related_stock': str(article['related_stock']),
        })

        if is_new:
            new_count += 1
            # 股票发现（两层搜索）
            stocks = discover_stocks_for_article(article, db)
            for stock in stocks:
                db.upsert_vip_discovered_stock(
                    article_id=art_id,
                    stock_name=stock['name'],
                    stock_code=stock['code'],
                    industry=stock.get('industry', ''),
                    match_score=stock.get('score', 0),
                    match_source=stock.get('source', ''),
                    match_detail=stock.get('detail', ''),
                )
                stocks_discovered += 1

    print(f"[VIP] 采集完成: 新增 {new_count} 篇, 发现股票 {stocks_discovered} 只")
    return {
        "fetched": len(vip_articles),
        "new_count": new_count,
        "stocks_discovered": stocks_discovered,
    }


def discover_stocks_for_article(article: dict, db: DB) -> list:
    """两层股票发现：Tushare主营业务 + 发现报告API

    Args:
        article: VIP文章dict
        db: 数据库实例

    Returns:
        list: [{name, code, industry, score, source, detail}, ...]
    """
    title = article.get('title', '')
    brief = article.get('brief', '')
    related_stock = article.get('related_stock', '')

    results = []

    # 第一层: Tushare主营业务搜索（通过缓存的股票数据库）
    stock_db, pro = _get_tushare_stock_db()
    if stock_db and pro:
        try:
            from vip_extractor import extract_search_terms, parse_related_stock, MARKET_PREFIX

            search_terms = extract_search_terms(title, brief)
            market_constraints = parse_related_stock(related_stock)
            required_prefixes = set()
            for mc in market_constraints:
                prefix = MARKET_PREFIX.get(mc["market"], "")
                if prefix:
                    required_prefixes.add(prefix)
                if mc["market"] == "主板":
                    required_prefixes.add("60")
                    required_prefixes.add("00")

            # 在主营业务中搜索
            candidates = []
            for stock in stock_db:
                main_biz = (stock.get('main_business', '') or '').lower()
                biz_scope = (stock.get('business_scope', '') or '').lower()
                combined = f"{main_biz} {biz_scope}"
                match_count = sum(1 for kw in search_terms if kw.lower() in combined)
                if match_count == 0:
                    continue
                # 板块过滤
                symbol = stock.get('symbol', '')
                if required_prefixes and not any(symbol.startswith(p) for p in required_prefixes):
                    continue
                candidates.append({
                    'name': stock.get('name', ''),
                    'code': stock.get('ts_code', ''),
                    'industry': stock.get('industry', ''),
                    'score': match_count,
                    'source': 'tushare',
                    'detail': f"主营业务匹配 {match_count} 个关键词",
                })

            candidates.sort(key=lambda x: x['score'], reverse=True)
            results.extend(candidates[:5])
        except Exception as e:
            print(f"  [股票发现] Tushare层失败: {e}")

    # 第二层: 发现报告API搜索
    fxbaogao_stocks = search_fxbaogao(title, brief)
    for stock in fxbaogao_stocks:
        # 检查是否已在结果中
        existing_codes = {r['code'] for r in results}
        if stock['code'] in existing_codes:
            # 合并来源
            for r in results:
                if r['code'] == stock['code']:
                    r['source'] = 'both'
                    r['score'] += stock.get('score', 0)
            continue
        results.append(stock)

    return results[:10]


def search_fxbaogao(title: str, brief: str) -> list:
    """通过发现报告MCP HTTP API搜索研报中提及的股票

    发现报告提供MCP协议的HTTP端点 (https://api.fxbaogao.com/mcp/)，
    使用JSON-RPC 2.0格式调用 search_reports 工具。
    从研报标题和段落中提取公司名，再反查Tushare获取股票代码。

    Args:
        title: 文章标题
        brief: 文章简介

    Returns:
        list: [{name, code, industry, score, source, detail}, ...]
    """
    api_key = get_fxbaogao_api_key()
    if not api_key:
        print("  [发现报告] API key未配置，跳过")
        return []

    # 提取搜索关键词
    from vip_extractor import extract_search_terms
    search_terms = extract_search_terms(title, brief)
    if not search_terms:
        return []

    # 用前5个关键词组合搜索（覆盖更具体的业务关键词）
    keywords = " ".join(search_terms[:5])
    print(f"  [发现报告] 搜索关键词: {keywords}")

    try:
        # 调用发现报告MCP HTTP端点 (JSON-RPC 2.0)
        # 字段名为camelCase: keywords, startTime, endTime, orgNames, luckyBaby
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
                "arguments": {
                    "keywords": keywords,
                },
            },
            "id": 1,
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=20)
        data = resp.json()

        # MCP返回格式: {"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"..."}],"isError":false}}
        result = data.get("result", {})
        if result.get("isError"):
            error_text = ""
            for item in result.get("content", []):
                if isinstance(item, dict) and item.get("type") == "text":
                    error_text += item.get("text", "")
            print(f"  [发现报告] MCP错误: {error_text[:200]}")
            return []

        content_list = result.get("content", [])

        # 提取文本内容
        reports_text = ""
        for item in content_list:
            if isinstance(item, dict) and item.get("type") == "text":
                reports_text += item.get("text", "")

        if not reports_text:
            print("  [发现报告] MCP返回无内容")
            return []

        # 尝试解析为JSON
        try:
            reports_data = json.loads(reports_text)
        except (json.JSONDecodeError, TypeError):
            print(f"  [发现报告] 返回非JSON: {reports_text[:100]}")
            return []

        # reports_data可能是dict或list
        if isinstance(reports_data, list):
            reports = reports_data
        elif isinstance(reports_data, dict):
            reports = reports_data.get("reports", []) or reports_data.get("list", []) or reports_data.get("data", [])
        else:
            reports = []
        if not reports:
            print(f"  [发现报告] 无搜索结果 (返回keys: {list(reports_data.keys())})")
            return []

        print(f"  [发现报告] 搜索到 {len(reports)} 篇研报")

        # 从研报标题和段落中搜索公司名
        # 清理 <em> 标签
        def _clean_em(text):
            return re.sub(r'</?em>', '', text or '')

        # 收集所有研报文本
        all_text = ""
        for report in reports:
            report_title = _clean_em(report.get("title", ""))
            all_text += report_title + " "
            for para in report.get("paragraphs", []):
                all_text += _clean_em(para.get("content", "")) + " "

        # 在Tushare股票库中搜索公司名（使用缓存）
        stock_db, _ = _get_tushare_stock_db()
        if not stock_db:
            print("  [发现报告] Tushare股票库未加载，跳过公司名匹配")
            return []

        stocks = []
        for stock in stock_db:
            name = stock.get('name', '')
            # 公司名在研报文本中出现
            if name and name in all_text:
                stocks.append({
                    'name': name,
                    'code': stock.get('ts_code', ''),
                    'industry': stock.get('industry', ''),
                    'score': 1,
                    'source': 'fxbaogao',
                    'detail': f"研报搜索匹配: {name} 在 {len(reports)} 篇研报中出现",
                })

        # 按公司名长度降序（优先匹配更具体的公司名）
        stocks.sort(key=lambda x: len(x['name']), reverse=True)
        print(f"  [发现报告] 从研报中发现 {len(stocks)} 只股票")
        return stocks[:10]

    except Exception as e:
        print(f"  [发现报告] 搜索失败: {e}")
        return []


# ---------------------------------------------------------------------------
# 深度头条 + 投资日历（简单采集，存入raw_cache）
# ---------------------------------------------------------------------------

def collect_depth_articles(db: DB) -> dict:
    """采集深度头条"""
    print("\n[深度] 开始采集...")
    try:
        depth_data = _cls_api_get('/v3/depth/home/assembled/1000')
        if not depth_data or not isinstance(depth_data, dict):
            print("[深度] 无数据")
            return {"fetched": 0}

        depth_list = depth_data.get('depth_list', [])
        print(f"[深度] 采集到 {len(depth_list)} 篇")
        # 存入raw_cache供后续使用
        db._save_raw_cache('cls', 'depth_articles', None, 'latest', depth_data)
        return {"fetched": len(depth_list)}
    except Exception as e:
        print(f"[深度] 采集失败: {e}")
        return {"fetched": 0}


def collect_investment_calendar(db: DB) -> dict:
    """采集投资日历"""
    print("\n[日历] 开始采集...")
    try:
        cal_data = _cls_api_get('/api/calendar/web/list',
                                  {'flag': '0', 'type': '0'})
        if not cal_data:
            print("[日历] 无数据")
            return {"fetched": 0}

        events = cal_data if isinstance(cal_data, list) else cal_data.get('list', [])
        print(f"[日历] 采集到 {len(events)} 个事件")
        db._save_raw_cache('cls', 'investment_calendar', None, 'latest', cal_data)
        return {"fetched": len(events)}
    except Exception as e:
        print(f"[日历] 采集失败: {e}")
        return {"fetched": 0}


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="财联社数据独立采集器")
    parser.add_argument('--telegraph', action='store_true', help='只采集电报')
    parser.add_argument('--vip', action='store_true', help='只采集VIP文章')
    parser.add_argument('--depth', action='store_true', help='只采集深度头条')
    parser.add_argument('--calendar', action='store_true', help='只采集投资日历')
    parser.add_argument('--stats', action='store_true', help='查看数据库统计')
    parser.add_argument('--resonance', action='store_true', help='查看当日共振分析')
    args = parser.parse_args()

    db = DB()
    db.init()

    if args.stats:
        stats = db.get_stats()
        print("\n=== 数据库统计 ===")
        for table, count in stats.items():
            print(f"  {table}: {count} 条")

        # 电报统计
        telegraph_stats = db.query_telegraph_stats()
        print(f"\n=== 今日电报统计 ===")
        print(f"  总数: {telegraph_stats['total']}")
        print(f"  红色: {telegraph_stats['red_count']}")
        if telegraph_stats['earliest_ts']:
            earliest = datetime.datetime.fromtimestamp(telegraph_stats['earliest_ts'])
            latest = datetime.datetime.fromtimestamp(telegraph_stats['latest_ts'])
            print(f"  时间范围: {earliest.strftime('%H:%M')} ~ {latest.strftime('%H:%M')}")
        if telegraph_stats['hot_stocks']:
            print(f"\n=== 今日热门股票 ===")
            for s in telegraph_stats['hot_stocks'][:10]:
                print(f"  {s['name']}: {s['mentions']} 次提及")
        return

    if args.resonance:
        resonance = db.query_resonance()
        print(f"\n=== 今日共振分析 ({len(resonance)} 只共振股票) ===")
        for stock in resonance:
            print(f"\n  {stock['stock_name']} ({stock['stock_code']})")
            print(f"    数据源: {', '.join(stock['sources'])} ({stock['source_count']}个)")
            for k, v in stock['details'].items():
                print(f"    {k}: {v}")
        return

    # 默认采集全部
    run_all = not any([args.telegraph, args.vip, args.depth, args.calendar])

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{'='*60}")
    print(f"财联社数据采集 — {now_str}")
    print(f"{'='*60}")

    summary = {"timestamp": now_str}

    if run_all or args.telegraph:
        summary['telegraph'] = collect_telegraphs(db)

    if run_all or args.vip:
        summary['vip'] = collect_vip_articles(db)

    if run_all or args.depth:
        summary['depth'] = collect_depth_articles(db)

    if run_all or args.calendar:
        summary['calendar'] = collect_investment_calendar(db)

    # 最终统计
    print(f"\n{'='*60}")
    print("采集完成，数据库统计:")
    stats = db.get_stats()
    for table, count in stats.items():
        if count > 0:
            print(f"  {table}: {count} 条")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
