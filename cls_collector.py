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
  python cls_collector.py --poll        # 持续轮询模式（每15分钟采集，持续55分钟）

定时任务设置:
  Schedule cron: 0 * * * * (每小时整点)
  命令: python /workspace/stock-assistant/cls_collector.py --poll
  说明: --poll 模式内部循环55分钟，每15分钟调用一次 collect_telegraphs()
        collect_telegraphs() 使用 /v1/roll/get_roll_list?category=red 端点
        向后翻页回填24小时红色电报（通常2-3页即可覆盖全天）
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

# 事件类型关键词（按优先级排序，先匹配的优先）
EVENT_KEYWORDS = {
    "数据": ["PMI", "CPI", "PPI", "GDP", "社融", "M2", "进出口",
             "贸易顺差", "发电量", "用电量", "固定资产投资",
             "通胀", "消费者信心", "密歇根", "非农", "失业率",
             "指数", "标普", "纳斯达克", "道琼斯", "恒生", "日经",
             "金龙指数", "跌幅", "涨幅", "收盘", "低开", "高开",
             "扩大至", "收窄"],
    "政策": ["政策", "规划", "方案", "意见", "通知", "条例", "补贴", "减税",
             "国务院", "发改委", "工信部", "财政部", "央行", "证监会",
             "支持", "鼓励", "推进", "加快", "促进", "改革",
             "征税", "税率", "关税", "制裁", "限制", "禁止",
             "重组", "实施"],
    "财报": ["业绩", "营收", "净利润", "同比增长", "环比", "季报", "年报",
             "半年报", "预增", "预减", "扭亏", "亏损", "分红", "送转",
             "财报", "每股收益", "毛利率", "营收增长"],
    "并购": ["收购", "并购", "合并", "增持", "减持", "回购",
             "股权转让", "定增", "增发", "入股", "重组"],
    "研报": ["研报", "评级", "买入", "推荐", "目标价",
             "券商", "机构预计", "分析师", "高盛", "摩根", "巴克莱",
             "策略师", "看好", "看空"],
    "公告": ["公告", "进展", "签订", "中标", "合同", "项目",
             "投产", "达产", "获批", "批准", "许可",
             "ST", "风险警示", "澄清", "否认", "回应",
             "递表", "IPO", "上市"],
}

# 情感关键词（更全面）
POSITIVE_KEYWORDS = [
    "利好", "增长", "上涨", "突破", "超预期", "创新高", "大涨",
    "涨停", "爆发", "景气", "回暖", "复苏", "强劲", "加速",
    "获批", "中标", "签订", "合作", "增持", "回购", "分红",
    "支持", "鼓励", "补贴", "减税", "优惠",
    "走高", "拉升", "扩大涨幅", "逆势上涨", "逆势走高",
    "转涨", "收窄", "涨幅扩大", "回升", "反弹",
]
NEGATIVE_KEYWORDS = [
    "利空", "下跌", "暴跌", "熔断", "崩盘", "闪崩",
    "亏损", "预减", "下降", "萎缩", "下滑", "疲软", "低迷",
    "违规", "处罚", "警示", "立案", "退市", "ST", "减持",
    "违约", "爆雷", "爆仓", "质押", "平仓",
    "走低", "下挫", "跌幅扩大", "集体低开", "集体下跌",
    "承压", "跳水", "重挫", "大跌", "连跌",
    "下调", "下调预期", "削减", "裁员",
    "造假", "虚假", "违规", "警告", "处分",
]

# 高影响关键词（与红色标记组合判断impact_level）
HIGH_IMPACT_KEYWORDS = [
    "熔断", "暂停", "限制", "制裁", "关税", "降息", "加息",
    "国务院", "央行", "证监会", "重大", "突发", "紧急",
    "暴跌", "崩盘", "闪崩", "跌停",
    "跌幅扩大至", "跌幅扩大",
    "退市", "ST", "造假", "立案",
]

# 中等影响关键词
MEDIUM_IMPACT_KEYWORDS = [
    "下跌", "下挫", "走低", "承压", "跳水",
    "集体低开", "集体下跌",
    "扩大", "收窄", "逆势",
    "连续", "连跌",
    "上调", "下调", "涨价", "降价",
    "IPO", "递表", "重组",
    "标普", "纳斯达克", "道琼斯",
    "费城半导体", "英伟达", "台积电",
]

# 行业/主题标签（更全面）
SECTOR_TAGS = [
    # 半导体/芯片
    "半导体", "芯片", "存储芯片", "功率", "模拟", "氮化镓", "碳化硅",
    "晶圆", "封测", "MLCC", "光刻", "台积电", "英伟达", "铠侠",
    "费城半导体",
    # AI/算力
    "AI", "人工智能", "算力", "光模块", "CPO", "硅光", "光通信",
    "服务器", "AIDC", "IDC", "液冷", "OpenAI",
    # 新能源
    "新能源", "光伏", "储能", "锂电", "风电", "氢能", "钠离子",
    # 医药
    "创新药", "医疗器械", "中药", "CXO",
    # 军工/航天
    "军工", "航天", "卫星", "无人机", "eVTOL", "低空经济",
    "商业航天",
    # 金融
    "证券", "银行", "保险", "金融",
    # 地产/基建
    "房地产", "建材", "基建",
    # 消费
    "消费", "白酒", "食品", "旅游", "零售",
    # 资源/商品
    "黄金", "原油", "石油", "布伦特", "天然气", "煤炭",
    "有色", "钢铁", "化工", "铝", "铜",
    # 电力
    "电力", "电网", "特高压", "虚拟电厂",
    # 汽车
    "汽车", "智能驾驶", "新能源车", "机器人",
    # 通信
    "通信", "5G", "6G",
    # 电子
    "PCB", "消费电子", "面板", "苹果",
    # 其他
    "中概股", "金龙指数", "纳斯达克",
    "韩国", "日本", "亚太", "港股",
    "通胀", "CPI", "PPI", "社融",
]


def classify_event_type(text: str) -> str:
    """分类事件类型（按优先级匹配，数据类优先于其他）"""
    scores = {}
    for event_type, keywords in EVENT_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scores[event_type] = score
    if scores:
        # 按score降序排序，取最高分
        return max(scores, key=scores.get)
    return "其他"


def classify_sentiment(text: str) -> str:
    """分类情感倾向（带否定语境检测）"""
    # 否定语境检测："不涉及"、"否认"、"否认"等会使正面词变负面
    negation_contexts = ["不涉及", "否认", "澄清", "尚无", "尚未", "未确定",
                         "不包含", "不包括", "无相关"]
    has_negation = any(neg in text for neg in negation_contexts)

    pos = sum(1 for kw in POSITIVE_KEYWORDS if kw in text)
    neg = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text)

    # 如果有否定语境，正面词减半
    if has_negation:
        pos = pos // 2

    if pos > neg:
        return "positive"
    elif neg > pos:
        return "negative"
    return "neutral"


def classify_impact(text: str, is_red: bool) -> str:
    """分类影响级别（三级判断：high > medium > low）"""
    if is_red:
        return "high"

    high_kw = sum(1 for kw in HIGH_IMPACT_KEYWORDS if kw in text)
    medium_kw = sum(1 for kw in MEDIUM_IMPACT_KEYWORDS if kw in text)

    # 数值检测：提取跌幅/涨幅数字
    import re
    pct_matches = re.findall(r'跌(?:幅|超)?(\d+(?:\.\d+)?)%|下(?:跌|挫)(\d+(?:\.\d+)?)%|跌超(\d+(?:\.\d+)?)', text)
    max_pct = 0
    for groups in pct_matches:
        for g in groups:
            if g:
                max_pct = max(max_pct, float(g))

    if high_kw >= 2 or max_pct >= 5:
        return "high"
    elif high_kw >= 1 or medium_kw >= 2 or max_pct >= 3:
        return "medium"
    elif medium_kw >= 1 or max_pct >= 1:
        return "medium"
    return "low"


def extract_sector_tags(text: str) -> str:
    """从文本中提取行业/主题标签（使用模块级SECTOR_TAGS）"""
    tags = [s for s in SECTOR_TAGS if s in text]
    return ",".join(tags[:8]) if tags else ""


# ---------------------------------------------------------------------------
# 电报采集
# ---------------------------------------------------------------------------

def _fetch_telegraph_page_red(last_time: int = None, rn: int = 50) -> list:
    """获取一页红色（加红）电报数据

    使用 /v1/roll/get_roll_list?category=red 端点：
    - category=red: 只返回加红的重要电报
    - last_time + refresh_type=1: 向后翻页（获取last_time之前的电报）
    - rn: 每页条数（最大50）

    Args:
        last_time: 时间戳，获取该时间之前的电报。None=获取最新一批
        rn: 每页条数（默认50）

    Returns:
        list: 电报原始数据列表
    """
    params = {
        'category': 'red',
        'refresh_type': '1',
        'rn': str(rn),
        'last_time': str(last_time if last_time else int(time.time())),
    }
    data = _cls_api_get('/v1/roll/get_roll_list', params)
    if data is None:
        return []
    if isinstance(data, dict):
        return data.get('roll_data', [])
    return []


def _fetch_telegraph_page_all(last_time: int = None) -> list:
    """获取一页全部电报数据（含非加红）

    使用 /api/cache?name=telegraph 端点（固定返回最新20条，无向后翻页）。
    仅用于补充最新电报，历史数据靠红色电报端点覆盖。

    Returns:
        list: 电报原始数据列表
    """
    data = _cls_api_get('/api/cache', {'name': 'telegraph'})
    if data is None:
        return []
    if isinstance(data, dict):
        return data.get('roll_data', [])
    return []


def _process_telegraph_items(db: DB, items: list) -> dict:
    """处理电报列表：结构化分析 + 写入数据库

    Args:
        db: 数据库实例
        items: 电报原始数据列表

    Returns:
        dict: {new_count, skipped_count, red_count}
    """
    new_count = 0
    skipped = 0
    red_count = 0

    for item in items:
        telegraph_id = str(item.get('id', ''))
        if not telegraph_id:
            continue

        title = (item.get('title', '') or '').strip()
        content = (item.get('content', '') or '').strip()
        ctime = int(item.get('ctime', 0))
        # level=B 表示加红重要电报
        level = item.get('level', '')
        is_red = 1 if level in ('A', 'B') else 0
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

    return {
        "new_count": new_count,
        "skipped_count": skipped,
        "red_count": red_count,
    }


def collect_telegraphs(db: DB, lookback_hours: int = 24) -> dict:
    """采集财联社电报，结构化后写入数据库

    使用 /v1/roll/get_roll_list?category=red 端点：
    - category=red 只采集加红重要电报（过滤非重要信息）
    - last_time + refresh_type=1 支持向后翻页，一次调用回填全天数据
    - rn=50 每页50条，通常2-3页即可覆盖24小时

    同时补充最新全部电报（/api/cache?name=telegraph）以覆盖非加红但刚发布的电报。

    Args:
        db: 数据库实例
        lookback_hours: 回看小时数（默认24小时）

    Returns:
        dict: {fetched, new_count, skipped_count, red_count}
    """
    print(f"\n[电报] 开始采集（红色电报向后翻页，回看{lookback_hours}h）...")

    all_items = []
    seen_ids = set()

    # === 第一步：采集红色重要电报（向后翻页） ===
    current_time = int(time.time())
    cutoff_time = current_time - lookback_hours * 3600
    last_time = current_time
    page = 0

    while last_time > cutoff_time:
        page += 1
        items = _fetch_telegraph_page_red(last_time=last_time, rn=50)

        if not items:
            print(f"[电报] 红色电报第{page}页: 无数据，停止翻页")
            break

        new_items = [it for it in items if str(it.get('id')) not in seen_ids]
        for it in items:
            seen_ids.add(str(it.get('id')))

        all_items.extend(new_items)

        # 获取最旧时间戳
        ctimes = [int(it.get('ctime', 0)) for it in items if it.get('ctime')]
        if not ctimes:
            break
        oldest = min(ctimes)
        newest = max(ctimes)

        print(f"[电报] 红色电报第{page}页: {len(items)}条 ({len(new_items)}新), "
              f"{time.strftime('%H:%M', time.localtime(oldest))}~{time.strftime('%H:%M', time.localtime(newest))}")

        if len(new_items) == 0:
            print(f"[电报] 无新条目，停止翻页")
            break

        last_time = oldest
        time.sleep(0.5)  # 礼貌间隔

    print(f"[电报] 红色电报共 {len(all_items)} 条")

    # === 第二步：补充最新全部电报（含非加红） ===
    latest_items = _fetch_telegraph_page_all()
    latest_new = [it for it in latest_items if str(it.get('id')) not in seen_ids]
    if latest_new:
        print(f"[电报] 最新全部电报补充: {len(latest_new)} 条新（非加红）")
        all_items.extend(latest_new)

    if not all_items:
        print("[电报] 无数据")
        return {"fetched": 0, "new_count": 0, "skipped_count": 0, "red_count": 0}

    print(f"[电报] 总计 {len(all_items)} 条待处理")

    # === 第三步：结构化处理 + 写入数据库 ===
    result = _process_telegraph_items(db, all_items)

    print(f"[电报] 采集完成: 新增 {result['new_count']} 条, "
          f"跳过 {result['skipped_count']} 条(已存在), 红色 {result['red_count']} 条")

    return {
        "fetched": len(all_items),
        "new_count": result['new_count'],
        "skipped_count": result['skipped_count'],
        "red_count": result['red_count'],
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
    """v4多源股票发现（Bug#1修复：接入v4引擎替代v3）

    优先使用 vip_search_v4 的多源动态搜索（东财公告+WebSearch+fxbaogao+CLS电报），
    降级时回退到v3的Tushare主营业务+fxbaogao两层搜索。

    Args:
        article: VIP文章dict
        db: 数据库实例

    Returns:
        list: [{name, code, industry, score, source, detail}, ...]
    """
    title = article.get('title', '')
    brief = article.get('brief', '')
    related_stock = article.get('related_stock', '')

    # Bug#1修复: 优先使用v4多源搜索
    try:
        from vip_search_v4 import discover_stocks_v4
        kept, excluded = discover_stocks_v4(
            title, brief, market_filter=related_stock
        )
        results = []
        for r in kept:
            sources = r.get('source_counts', {})
            source_str = "+".join(k for k, v in sources.items() if v > 0) or "v4_multi"
            results.append({
                'name': r.get('stock_name', ''),
                'code': r.get('stock_code', ''),
                'industry': r.get('industry', ''),
                'score': r.get('total_score', 0),
                'source': source_str,
                'detail': f"匹配率{r.get('match_rate',0):.0%}, 线索: {', '.join(r.get('matched_clues', [])[:3])}",
            })
        if results:
            return results[:10]
        print("  [v4] 无结果，回退到v3搜索")
    except ImportError:
        print("  [WARN] vip_search_v4未安装，使用v3搜索")
    except Exception as e:
        print(f"  [v4异常] {e}，回退到v3搜索")

    # v3降级：Tushare主营业务 + 发现报告API
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
# 持续轮询模式 — 定时采集 + 全量回填
# ---------------------------------------------------------------------------

def run_poll_mode(db: DB, interval: int = 900, duration: int = 3300,
                  collect_vip_too: bool = False) -> None:
    """持续轮询模式：每隔 interval 秒采集一次电报，持续 duration 秒

    新方案：使用 /v1/roll/get_roll_list?category=red 向后翻页，
    每次调用即可回填24小时红色电报，无需高频轮询。

    默认间隔15分钟（900秒），因为：
    - 红色电报频率约5-10条/小时
    - 每次调用回填24h，不会遗漏
    - 15分钟间隔足够及时，且对服务器友好

    Args:
        db: 数据库实例
        interval: 轮询间隔秒数（默认900=15分钟）
        duration: 总持续时间秒数（默认3300=55分钟，留5分钟缓冲）
        collect_vip_too: 是否在首次轮询时也采集VIP/深度/日历
    """
    start_time = time.time()
    poll_count = 0
    total_new = 0
    total_red = 0

    # 首次轮询：可选采集其他数据源
    vip_done = not collect_vip_too

    while True:
        elapsed = time.time() - start_time
        if elapsed >= duration:
            break

        poll_count += 1
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        remaining_min = int((duration - elapsed) / 60)

        print(f"\n{'='*60}")
        print(f"轮询 #{poll_count} | {now_str} | 已运行 {int(elapsed//60)}min | 剩余 ~{remaining_min}min")
        print(f"累计新增: {total_new} 条 | 红色: {total_red} 条")
        print(f"{'='*60}")

        # 采集电报（向后翻页回填24h红色电报 + 最新全部电报补充）
        result = collect_telegraphs(db, lookback_hours=24)
        total_new += result['new_count']
        total_red += result['red_count']

        # 首次轮询时采集其他数据源
        if not vip_done:
            try:
                collect_vip_articles(db)
                collect_depth_articles(db)
                collect_investment_calendar(db)
            except Exception as e:
                print(f"[其他数据源] 采集失败: {e}")
            vip_done = True

        # 显示今日电报统计
        stats = db.query_telegraph_stats()
        if stats['total'] > 0:
            if stats['earliest_ts']:
                earliest = datetime.datetime.fromtimestamp(stats['earliest_ts'])
                latest = datetime.datetime.fromtimestamp(stats['latest_ts'])
                span_h = (stats['latest_ts'] - stats['earliest_ts']) / 3600
                print(f"[统计] 今日电报: {stats['total']} 条 | "
                      f"红色: {stats['red_count']} | "
                      f"覆盖: {earliest.strftime('%H:%M')}~{latest.strftime('%H:%M')} ({span_h:.1f}h)")
            else:
                print(f"[统计] 今日电报: {stats['total']} 条 | 红色: {stats['red_count']}")

        # 计算睡眠时间
        sleep_time = interval - (time.time() - start_time - elapsed)
        remaining = duration - (time.time() - start_time)
        if remaining <= 0:
            break
        sleep_time = min(max(sleep_time, 10), remaining)

        next_time = datetime.datetime.now() + datetime.timedelta(seconds=int(sleep_time))
        print(f"[等待] {int(sleep_time)}秒后下次轮询 (预计 {next_time.strftime('%H:%M:%S')})")
        time.sleep(sleep_time)

    # 最终统计
    print(f"\n{'='*60}")
    print(f"轮询结束 | 共 {poll_count} 次 | 新增 {total_new} 条 | 红色 {total_red} 条")
    final_stats = db.query_telegraph_stats()
    print(f"今日电报总计: {final_stats['total']} 条 | 红色: {final_stats['red_count']}")
    if final_stats['earliest_ts']:
        earliest = datetime.datetime.fromtimestamp(final_stats['earliest_ts'])
        latest = datetime.datetime.fromtimestamp(final_stats['latest_ts'])
        span_h = (final_stats['latest_ts'] - final_stats['earliest_ts']) / 3600
        print(f"覆盖时间: {earliest.strftime('%H:%M')}~{latest.strftime('%H:%M')} ({span_h:.1f}h)")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="财联社数据独立采集器")
    parser.add_argument('--telegraph', action='store_true', help='只采集电报')
    parser.add_argument('--vip', action='store_true', help='只采集VIP文章')
    parser.add_argument('--depth', action='store_true', help='只采集深度头条')
    parser.add_argument('--calendar', action='store_true', help='只采集投资日历')
    parser.add_argument('--discover-vip', action='store_true',
                        help='v5: 仅对新加入的VIP文章逐篇执行v4股票发现引擎')
    parser.add_argument('--poll', action='store_true',
                        help='持续轮询模式：每15分钟采集电报，持续55分钟'
                             '（红色电报向后翻页回填24h）')
    parser.add_argument('--interval', type=int, default=900,
                        help='轮询间隔秒数（默认900=15分钟）')
    parser.add_argument('--duration', type=int, default=3300,
                        help='轮询持续秒数（默认3300=55分钟）')
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

    # 轮询模式：高频采集电报（解决CLS API固定返回20条的问题）
    if args.poll:
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"{'='*60}")
        print(f"财联社电报高频轮询模式 — {now_str}")
        print(f"间隔: {args.interval}秒 | 持续: {args.duration}秒 ({args.duration//60}分钟)")
        print(f"{'='*60}")
        run_poll_mode(db, interval=args.interval, duration=args.duration,
                      collect_vip_too=True)
        return

    # v5: --discover-vip 模式 — 仅对新VIP文章执行股票发现
    if args.discover_vip:
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"{'='*60}")
        print(f"v5 VIP股票发现 — {now_str}")
        print(f"{'='*60}")

        # 先采集VIP文章
        vip_result = collect_vip_articles(db)
        # collect_vip_articles内部已对新文章调用discover_stocks_for_article
        print(f"\n[VIP发现] 采集 {vip_result.get('fetched', 0)} 篇, "
              f"新增 {vip_result.get('new_count', 0)} 篇, "
              f"发现股票 {vip_result.get('stocks_discovered', 0)} 只")
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
