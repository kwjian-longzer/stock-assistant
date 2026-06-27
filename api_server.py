#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api_server.py — v4.0 API 服务（标准库 http.server）
=====================================================
从 SQLite DB 直接读取数据，为前端网站提供 REST API。

9 个核心端点 + 辅助端点:
  GET /api/health           — 健康检查 + DB统计
  GET /api/dashboard        — 综合看板（聚合多表数据）
  GET /api/indices          — 指数行情
  GET /api/sectors          — 板块资金流向
  GET /api/north-money      — 北向资金
  GET /api/dragon-tiger     — 龙虎榜
  GET /api/limit-up         — 涨停池
  GET /api/insights         — 市场洞见
  GET /api/gold-stocks      — 金股推荐
  GET /api/reports          — 报告列表
  GET /api/calendar         — 财经日历
  GET /api/global           — 全球市场（美股/港股/外汇/商品）
  GET /api/learning         — 学习记录
  GET /api/heat             — 热度追踪

用法:
  python api_server.py                    # 默认端口 8765
  python api_server.py --port 9000        # 指定端口
"""

import sys
import os
import json
import argparse
import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import DB

# ---------------------------------------------------------------------------
# 全局 DB 实例
# ---------------------------------------------------------------------------
_db = None


def get_db():
    global _db
    if _db is None:
        _db = DB()
        _db.init()
    return _db


def get_today_str():
    return datetime.datetime.now().strftime("%Y-%m-%d")


def safe_float(v, default=None):
    try:
        if v in (None, "", "0.000"):
            return default
        return float(v)
    except (ValueError, TypeError):
        return default


def parse_date_param(query_params, default=None):
    """从查询参数获取日期，支持 ?date=2026-06-26 或 ?date=20260626"""
    d = query_params.get("date", [None])[0]
    if not d:
        return default or get_today_str()
    # 统一为 YYYY-MM-DD
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return d


def parse_int_param(query_params, key, default):
    v = query_params.get(key, [str(default)])[0]
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# API 处理函数
# ---------------------------------------------------------------------------

def api_health(query_params):
    """健康检查 + DB 统计"""
    db = get_db()
    stats = db.get_stats()
    return {"status": "ok", "timestamp": datetime.datetime.now().isoformat(),
            "db_stats": stats}


def api_dashboard(query_params):
    """综合看板：聚合指数+板块+北向+龙虎榜+洞见+金股"""
    db = get_db()
    date = parse_date_param(query_params)
    period = query_params.get("period", ["morning"])[0]

    indices = db.query_index_quote(date=date)
    sectors = db.query_sector_moneyflow(date=date, top_n=10)
    north = db.query_north_money(date=date)
    dragon_tiger = db.query_dragon_tiger(date=date)
    limit_up = db.query_limit_up(date=date)
    insights = db.query_insights(date=date, period=period)
    gold_stocks = db.query_gold_stock(date=date)

    # 全球市场（从 raw_cache）
    global_data = db.get_or_fetch("sina", f"global_{period}",
                                  lambda: {}, trade_date=date, params={}, ttl_hours=24)

    return {
        "date": date,
        "period": period,
        "indices": indices,
        "sectors_top": sectors,
        "north_money": north,
        "dragon_tiger": dragon_tiger[:10] if dragon_tiger else [],
        "limit_up": limit_up,
        "insights": insights,
        "gold_stocks": gold_stocks,
        "global": global_data,
        "stats": {
            "index_count": len(indices),
            "sector_count": len(sectors),
            "dragon_tiger_count": len(dragon_tiger),
            "limit_up_count": len(limit_up),
            "insight_count": len(insights),
            "gold_stock_count": len(gold_stocks),
        },
    }


def api_indices(query_params):
    """指数行情"""
    db = get_db()
    date = parse_date_param(query_params)
    realtime = query_params.get("realtime", ["0"])[0] == "1"
    rows = db.query_index_quote(date=date, realtime_only=realtime)
    return {"date": date, "count": len(rows), "data": rows}


def api_sectors(query_params):
    """板块资金流向"""
    db = get_db()
    date = parse_date_param(query_params)
    top_n = parse_int_param(query_params, "top", 20)
    rows = db.query_sector_moneyflow(date=date, top_n=top_n)
    return {"date": date, "count": len(rows), "data": rows}


def api_north_money(query_params):
    """北向资金"""
    db = get_db()
    date = parse_date_param(query_params)
    row = db.query_north_money(date=date)
    return {"date": date, "data": row}


def api_dragon_tiger(query_params):
    """龙虎榜"""
    db = get_db()
    date = parse_date_param(query_params)
    limit = parse_int_param(query_params, "limit", 30)
    rows = db.query_dragon_tiger(date=date)
    return {"date": date, "count": len(rows), "data": rows[:limit]}


def api_limit_up(query_params):
    """涨停池"""
    db = get_db()
    date = parse_date_param(query_params)
    rows = db.query_limit_up(date=date)
    return {"date": date, "count": len(rows), "data": rows}


def api_insights(query_params):
    """市场洞见"""
    db = get_db()
    date = parse_date_param(query_params)
    period = query_params.get("period", [None])[0]
    rows = db.query_insights(date=date, period=period)
    # 按 category 分组
    by_category = {}
    for r in rows:
        cat = r.get("category", "其他")
        by_category.setdefault(cat, []).append(r)
    return {"date": date, "count": len(rows), "by_category": by_category, "data": rows}


def api_gold_stocks(query_params):
    """金股推荐"""
    db = get_db()
    date = parse_date_param(query_params)
    limit = parse_int_param(query_params, "limit", 20)
    rows = db.query_gold_stock(date=date, limit=limit)
    return {"date": date, "count": len(rows), "data": rows}


def api_reports(query_params):
    """报告列表"""
    db = get_db()
    date = parse_date_param(query_params, default=None)
    limit = parse_int_param(query_params, "limit", 50)
    if date:
        rows = db.query_reports(date=date, limit=limit)
    else:
        rows = db.query_reports(limit=limit)
    latest = db.query_latest_report()
    return {"count": len(rows), "latest": latest, "data": rows}


def api_calendar(query_params):
    """财经日历"""
    db = get_db()
    start = query_params.get("start", [None])[0]
    end = query_params.get("end", [None])[0]
    # 默认查询前后7天
    if not start:
        today = datetime.date.today()
        start = (today - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    if not end:
        end = (datetime.date.today() + datetime.timedelta(days=30)).strftime("%Y-%m-%d")
    rows = db.query_calendar_events(start_date=start, end_date=end)
    return {"start": start, "end": end, "count": len(rows), "data": rows}


def api_global(query_params):
    """全球市场（美股/港股/外汇/商品）"""
    db = get_db()
    date = parse_date_param(query_params)
    period = query_params.get("period", ["morning"])[0]
    data = db.get_or_fetch("sina", f"global_{period}",
                           lambda: {}, trade_date=date, params={}, ttl_hours=24)
    return {"date": date, "period": period, "data": data}


def api_learning(query_params):
    """学习记录"""
    db = get_db()
    limit = parse_int_param(query_params, "limit", 30)
    rows = db.query_learning_records(limit=limit)
    return {"count": len(rows), "data": rows}


def api_heat(query_params):
    """热度追踪"""
    db = get_db()
    date = parse_date_param(query_params, default=None)
    sector = query_params.get("sector", [None])[0]
    days = parse_int_param(query_params, "days", 20)
    rows = db.query_heat_tracking(date=date, sector=sector, days=days)
    return {"count": len(rows), "data": rows}


def api_telegraphs(query_params):
    """财联社电报"""
    db = get_db()
    date = parse_date_param(query_params, default=None)
    is_red = query_params.get("red", ["0"])[0] == "1"
    limit = parse_int_param(query_params, "limit", 200)
    rows = db.query_telegraphs(date=date, is_red_only=is_red, limit=limit)
    stats = db.query_telegraph_stats(date=date)
    return {"count": len(rows), "stats": stats, "data": rows}


# ---------------------------------------------------------------------------
# 路由表
# ---------------------------------------------------------------------------

ROUTES = {
    "/api/health": api_health,
    "/api/dashboard": api_dashboard,
    "/api/indices": api_indices,
    "/api/sectors": api_sectors,
    "/api/north-money": api_north_money,
    "/api/dragon-tiger": api_dragon_tiger,
    "/api/limit-up": api_limit_up,
    "/api/insights": api_insights,
    "/api/gold-stocks": api_gold_stocks,
    "/api/reports": api_reports,
    "/api/calendar": api_calendar,
    "/api/global": api_global,
    "/api/learning": api_learning,
    "/api/heat": api_heat,
    "/api/telegraphs": api_telegraphs,
}


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

class APIHandler(BaseHTTPRequestHandler):
    """REST API 请求处理器"""

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, message, status=400):
        self._send_json({"error": message}, status=status)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query_params = parse_qs(parsed.query)

        # 根路径 → API 信息
        if path == "/" or path == "":
            self._send_json({
                "name": "股票助手 v4.0 API",
                "endpoints": list(ROUTES.keys()),
                "timestamp": datetime.datetime.now().isoformat(),
            })
            return

        # 静态文件服务 (docs/ 目录)
        if path.startswith("/docs/") or path == "/index.html":
            self._serve_static(path)
            return

        # API 路由
        handler = ROUTES.get(path)
        if handler:
            try:
                result = handler(query_params)
                self._send_json(result)
            except Exception as e:
                import traceback
                traceback.print_exc()
                self._send_error(f"内部错误: {e}", status=500)
        else:
            self._send_error(f"未知路径: {path}", status=404)

    def _serve_static(self, path):
        """提供 docs/ 目录下的静态文件"""
        docs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
        if path == "/index.html":
            file_path = os.path.join(docs_dir, "index.html")
        else:
            file_path = os.path.join(docs_dir, path.lstrip("/docs/"))

        if not os.path.exists(file_path) or not os.path.isfile(file_path):
            self._send_error(f"文件不存在: {path}", status=404)
            return

        # 根据扩展名设置 Content-Type
        ext = os.path.splitext(file_path)[1].lower()
        ct_map = {".html": "text/html; charset=utf-8", ".js": "application/javascript",
                  ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
                  ".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml"}
        content_type = ct_map.get(ext, "application/octet-stream")

        with open(file_path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        """简化日志输出"""
        print(f"  [{self.command}] {args[0]} → {args[1]}")


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="股票助手 v4.0 API 服务")
    parser.add_argument("--port", type=int, default=8765, help="监听端口 (默认 8765)")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址 (默认 0.0.0.0)")
    args = parser.parse_args()

    # 初始化 DB
    db = get_db()
    stats = db.get_stats()
    print(f"\n{'='*60}")
    print(f"  股票助手 v4.0 API 服务")
    print(f"  监听: http://{args.host}:{args.port}")
    print(f"  DB: {db.db_path}")
    print(f"  表数: {stats.get('table_count', '?')}")
    print(f"  端点数: {len(ROUTES)}")
    print(f"{'='*60}\n")
    print("可用端点:")
    for route in ROUTES:
        print(f"  GET {route}")
    print(f"\n  GET /              — API 信息")
    print(f"  GET /docs/index.html — 网站首页")
    print(f"\n按 Ctrl+C 停止\n")

    server = HTTPServer((args.host, args.port), APIHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\n服务已停止")
        server.server_close()


if __name__ == "__main__":
    main()
