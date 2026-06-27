#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQLite 缓存层 — 数据库管理与查询

功能:
  1. 11张表：原始缓存 + 指数行情 + 板块资金 + 涨停 + 龙虎榜 + 北向 + 融资融券
     + 财联社电报 + 电报关联股票 + VIP文章 + VIP发现股票 + 金股
  2. get_or_fetch(): 缓存优先，避免重复拉取Tushare
  3. 电报结构化字段: event_type / sentiment / impact_level / sector_tags
     为后续Agent市场推理提供结构化输入
  4. 共振分析查询: 跨数据源交叉匹配，发现共振金股

用法:
  from db import DB
  db = DB()
  db.init()                          # 建表
  db.upsert_telegraph(item)          # 写入电报
  telegraphs = db.query_telegraphs(date='2026-06-26')  # 查询当日电报
  resonance = db.query_resonance(date='2026-06-26')    # 共振分析
"""

import os
import json
import sqlite3
import hashlib
import datetime
from typing import Optional, List, Dict, Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "data", "stock.db")


class DB:
    """SQLite 数据库管理器"""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")  # 并发写入友好
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init(self):
        """创建全部表（幂等）"""
        conn = self._conn()
        cur = conn.cursor()

        # 1. 原始API数据缓存
        cur.execute("""
            CREATE TABLE IF NOT EXISTS raw_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                api_name TEXT NOT NULL,
                trade_date TEXT,
                fetch_time TEXT NOT NULL,
                params_hash TEXT NOT NULL,
                data_json TEXT NOT NULL,
                UNIQUE(source, api_name, trade_date, params_hash)
            )
        """)

        # 2. 指数行情（带时间戳+是否实时标记）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS index_quote (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                code TEXT,
                trade_date TEXT NOT NULL,
                fetch_time TEXT NOT NULL,
                source TEXT NOT NULL,
                close REAL, pct_chg REAL, pre_close REAL, amount REAL,
                is_realtime INTEGER DEFAULT 0,
                UNIQUE(name, trade_date, source)
            )
        """)

        # 3. 板块资金流向
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sector_moneyflow (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                industry TEXT NOT NULL,
                net_mf_amount REAL,
                fetch_time TEXT NOT NULL,
                UNIQUE(trade_date, industry)
            )
        """)

        # 4. 涨停股票
        cur.execute("""
            CREATE TABLE IF NOT EXISTS limit_up (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                ts_code TEXT NOT NULL,
                name TEXT, pct_chg REAL, industry TEXT, amount REAL,
                fetch_time TEXT NOT NULL,
                UNIQUE(trade_date, ts_code)
            )
        """)

        # 5. 龙虎榜
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dragon_tiger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                ts_code TEXT NOT NULL, name TEXT,
                net_buy REAL, reason TEXT,
                fetch_time TEXT NOT NULL,
                UNIQUE(trade_date, ts_code)
            )
        """)

        # 6. 北向资金
        cur.execute("""
            CREATE TABLE IF NOT EXISTS north_money (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                north_money REAL, hgt REAL, sgt REAL, south_money REAL,
                fetch_time TEXT NOT NULL,
                UNIQUE(trade_date)
            )
        """)

        # 7. 融资融券
        cur.execute("""
            CREATE TABLE IF NOT EXISTS margin (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                exchange_id TEXT NOT NULL,
                rzye REAL, rzche REAL, rqye REAL,
                fetch_time TEXT NOT NULL,
                UNIQUE(trade_date, exchange_id)
            )
        """)

        # 8. 财联社电报（持续累积，每小时采集）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cls_telegraph (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegraph_id TEXT UNIQUE,
                title TEXT,
                content TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                is_red INTEGER DEFAULT 0,
                fetch_time TEXT NOT NULL,
                event_type TEXT,
                sentiment TEXT,
                impact_level TEXT,
                sector_tags TEXT,
                UNIQUE(telegraph_id)
            )
        """)

        # 9. 电报关联股票（一条电报可关联多只）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cls_telegraph_stock (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegraph_id TEXT NOT NULL,
                stock_name TEXT NOT NULL,
                stock_code TEXT,
                FOREIGN KEY(telegraph_id) REFERENCES cls_telegraph(telegraph_id)
            )
        """)

        # 10. VIP文章
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cls_vip_article (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id TEXT UNIQUE,
                title TEXT NOT NULL,
                brief TEXT,
                published_at TEXT,
                related_stock TEXT,
                fetch_time TEXT NOT NULL,
                UNIQUE(article_id)
            )
        """)

        # 11. VIP文章发现的股票
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vip_discovered_stock (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id TEXT NOT NULL,
                stock_name TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                industry TEXT,
                match_score INTEGER,
                match_source TEXT,
                match_detail TEXT,
                fetch_time TEXT NOT NULL,
                FOREIGN KEY(article_id) REFERENCES cls_vip_article(article_id)
            )
        """)

        # 12. 金股推荐+回测
        cur.execute("""
            CREATE TABLE IF NOT EXISTS gold_stock (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, code TEXT NOT NULL,
                recommend_date TEXT NOT NULL,
                report_type TEXT, reason TEXT, score INTEGER,
                price_at_recommend REAL,
                current_price REAL,
                return_1d REAL, return_3d REAL, return_5d REAL,
                return_10d REAL, return_20d REAL,
                max_return REAL, max_drawdown REAL,
                backtest_time TEXT,
                UNIQUE(code, recommend_date, report_type)
            )
        """)

        # 13. 市场洞见（分析引擎输出）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS market_insight (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                period TEXT NOT NULL,
                category TEXT,
                signal_text TEXT,
                a_share_impact TEXT,
                confidence TEXT,
                signal_time TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)

        # 14. 报告记录
        cur.execute("""
            CREATE TABLE IF NOT EXISTS report (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                period TEXT NOT NULL,
                title TEXT,
                content TEXT,
                char_count INTEGER,
                quality_score REAL,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)

        # 15. 钱三强选股结果
        cur.execute("""
            CREATE TABLE IF NOT EXISTS qian_sanqiang_result (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                stock_code TEXT,
                stock_name TEXT,
                strategy TEXT,
                score REAL,
                detail_json TEXT,
                fetch_time TEXT DEFAULT (datetime('now','localtime'))
            )
        """)

        # 16. 热度追踪
        cur.execute("""
            CREATE TABLE IF NOT EXISTS heat_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                sector TEXT,
                heat_score REAL,
                capital_flow REAL,
                limit_up_count INTEGER,
                lifecycle TEXT,
                fetch_time TEXT DEFAULT (datetime('now','localtime'))
            )
        """)

        # 17. 学习记录
        cur.execute("""
            CREATE TABLE IF NOT EXISTS learning_record (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                prediction TEXT,
                actual TEXT,
                gap_analysis TEXT,
                lesson TEXT,
                category TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)

        # 18. 网站数据快照
        cur.execute("""
            CREATE TABLE IF NOT EXISTS website_snapshot (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                period TEXT,
                snapshot_json TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)

        # 19. 财经日历事件
        cur.execute("""
            CREATE TABLE IF NOT EXISTS calendar_event (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_date TEXT NOT NULL,
                event_time TEXT,
                title TEXT,
                importance TEXT,
                category TEXT,
                detail TEXT,
                fetch_time TEXT DEFAULT (datetime('now','localtime'))
            )
        """)

        # gold_stock 扩展字段（ALTER ADD，幂等：仅添加尚不存在的列）
        self._alter_add_columns(cur, "gold_stock", {
            "catalyst": "TEXT",
            "dragon_vein": "TEXT",
            "verification": "TEXT",
            "signal_source": "TEXT",
            "buy_range": "TEXT",
            "target_price": "TEXT",
            "stop_loss": "TEXT",
            "strength": "TEXT",
        })

        # 创建索引
        cur.execute("CREATE INDEX IF NOT EXISTS idx_telegraph_ts ON cls_telegraph(timestamp)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_telegraph_red ON cls_telegraph(is_red, timestamp)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_telegraph_stock_name ON cls_telegraph_stock(stock_name)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_vip_article_id ON cls_vip_article(article_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_vip_stock_code ON vip_discovered_stock(stock_code)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_gold_stock_date ON gold_stock(recommend_date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sector_mf_date ON sector_moneyflow(trade_date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_insight_date ON market_insight(date, period)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_report_date ON report(date, period)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_qsq_date ON qian_sanqiang_result(date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_heat_date ON heat_tracking(date, sector)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_calendar_date ON calendar_event(event_date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_date ON website_snapshot(date, period)")

        conn.commit()
        conn.close()
        print(f"[DB] 初始化完成: {self.db_path} (v4.0, 19表)")

    def _alter_add_columns(self, cur, table: str, columns: dict):
        """幂等地为表添加列（已存在的列跳过）"""
        cur.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in cur.fetchall()}
        for col, coltype in columns.items():
            if col not in existing:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
                print(f"[DB] ALTER {table} ADD {col} {coltype}")

    # ------------------------------------------------------------------
    # 通用缓存: get_or_fetch
    # ------------------------------------------------------------------

    def get_or_fetch(self, source: str, api_name: str, fetch_func,
                     trade_date: str = None, params: dict = None,
                     ttl_hours: float = 12) -> Any:
        """缓存优先策略：先查库，未过期则直接返回；否则调fetch_func拉取并写入

        Args:
            source: 'tushare' / 'sina' / 'cls' / 'fxbaogao'
            api_name: 'daily' / 'moneyflow' / 'stock_basic' 等
            fetch_func: 无参数callable，返回原始数据(list/dict)
            trade_date: 数据所属交易日 (YYYY-MM-DD)
            params: 请求参数dict，用于生成params_hash
            ttl_hours: 缓存有效期（小时），默认12小时

        Returns:
            原始数据 (list/dict)
        """
        params_hash = hashlib.md5(
            json.dumps(params or {}, sort_keys=True).encode()
        ).hexdigest()

        conn = self._conn()
        cur = conn.cursor()

        # 查缓存
        cur.execute(
            "SELECT fetch_time, data_json FROM raw_cache "
            "WHERE source=? AND api_name=? AND trade_date IS ? AND params_hash=?",
            (source, api_name, trade_date, params_hash)
        )
        row = cur.fetchone()

        if row:
            fetch_time_str = row["fetch_time"]
            try:
                fetch_dt = datetime.datetime.strptime(fetch_time_str, "%Y-%m-%d %H:%M:%S")
                age = (datetime.datetime.now() - fetch_dt).total_seconds() / 3600
                if age < ttl_hours:
                    conn.close()
                    data = json.loads(row["data_json"])
                    print(f"[DB] 缓存命中: {source}/{api_name} ({age:.1f}h < {ttl_hours}h)")
                    return data
            except (ValueError, TypeError):
                pass

        # 缓存未命中或已过期，拉取新数据
        conn.close()
        print(f"[DB] 缓存未命中: {source}/{api_name}, 调用fetch_func...")
        data = fetch_func()

        if data is not None:
            self._save_raw_cache(source, api_name, trade_date, params_hash, data)

        return data

    def _save_raw_cache(self, source, api_name, trade_date, params_hash, data):
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO raw_cache "
            "(source, api_name, trade_date, fetch_time, params_hash, data_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (source, api_name, trade_date, now_str, params_hash, json.dumps(data, ensure_ascii=False))
        )
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # 财联社电报
    # ------------------------------------------------------------------

    def upsert_telegraph(self, item: dict) -> bool:
        """写入或更新一条电报（按telegraph_id去重）

        Args:
            item: 电报数据，必须包含:
                - telegraph_id: CLS电报ID
                - content: 电报正文
                - timestamp: 发布时间(Unix时间戳) ← 主时间戳
              可选:
                - title: 标题
                - is_red: 红色重要标记 (0/1)
                - event_type: 事件类型 (政策/财报/并购/研报/数据)
                - sentiment: 情感 (positive/negative/neutral)
                - impact_level: 影响级别 (high/medium/low)
                - sector_tags: 行业/主题标签 (逗号分隔)

        Returns:
            True=新写入, False=已存在(跳过)
        """
        telegraph_id = str(item.get("telegraph_id", ""))
        if not telegraph_id:
            return False

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = self._conn()

        # 检查是否已存在
        cur = conn.execute(
            "SELECT id FROM cls_telegraph WHERE telegraph_id=?",
            (telegraph_id,)
        )
        if cur.fetchone():
            conn.close()
            return False

        conn.execute(
            "INSERT OR IGNORE INTO cls_telegraph "
            "(telegraph_id, title, content, timestamp, is_red, fetch_time, "
            " event_type, sentiment, impact_level, sector_tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                telegraph_id,
                item.get("title", ""),
                item.get("content", ""),
                int(item.get("timestamp", 0)),
                int(item.get("is_red", 0)),
                now_str,
                item.get("event_type", ""),
                item.get("sentiment", ""),
                item.get("impact_level", ""),
                item.get("sector_tags", ""),
            )
        )
        conn.commit()
        conn.close()
        return True

    def upsert_telegraph_stocks(self, telegraph_id: str, stocks: list):
        """写入电报关联的股票列表

        Args:
            telegraph_id: 电报ID
            stocks: [{"name": "贵州茅台", "code": "600519"}, ...]
        """
        conn = self._conn()
        for stock in stocks:
            name = stock.get("name", "") if isinstance(stock, dict) else str(stock)
            code = stock.get("code", "") if isinstance(stock, dict) else ""
            if name:
                conn.execute(
                    "INSERT INTO cls_telegraph_stock (telegraph_id, stock_name, stock_code) "
                    "VALUES (?, ?, ?)",
                    (telegraph_id, name, code)
                )
        conn.commit()
        conn.close()

    def query_telegraphs(self, date: str = None, is_red_only: bool = False,
                         limit: int = 200) -> List[dict]:
        """查询电报列表

        Args:
            date: 日期 (YYYY-MM-DD)，默认今天
            is_red_only: 只查红色重要电报
            limit: 最多返回条数

        Returns:
            list: 电报列表，每条含 stocks 字段(关联股票)
        """
        if date is None:
            date = datetime.datetime.now().strftime("%Y-%m-%d")

        conn = self._conn()
        sql = (
            "SELECT t.*, GROUP_CONCAT(s.stock_name, '|') as stocks_str "
            "FROM cls_telegraph t "
            "LEFT JOIN cls_telegraph_stock s ON t.telegraph_id = s.telegraph_id "
            "WHERE date(t.timestamp, 'unixepoch') = ? "
        )
        params = [date]

        if is_red_only:
            sql += "AND t.is_red = 1 "

        sql += "GROUP BY t.telegraph_id ORDER BY t.timestamp DESC LIMIT ?"
        params.append(limit)

        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        conn.close()

        result = []
        for row in rows:
            item = dict(row)
            # stocks_str → list
            stocks_str = item.pop("stocks_str", "") or ""
            item["stocks"] = [s for s in stocks_str.split("|") if s]
            result.append(item)
        return result

    def query_telegraphs_recent(self, hours: int = 24, is_red_only: bool = False,
                                limit: int = 500) -> List[dict]:
        """查询最近N小时的电报（跨天查询，用于晨报覆盖隔夜信号）

        Args:
            hours: 查询最近多少小时
            is_red_only: 只查红色重要电报
            limit: 最多返回条数

        Returns:
            list: 电报列表，按时间倒序
        """
        cutoff_ts = int((datetime.datetime.now() -
                         datetime.timedelta(hours=hours)).timestamp())
        conn = self._conn()
        sql = (
            "SELECT t.*, GROUP_CONCAT(s.stock_name, '|') as stocks_str "
            "FROM cls_telegraph t "
            "LEFT JOIN cls_telegraph_stock s ON t.telegraph_id = s.telegraph_id "
            "WHERE t.timestamp >= ? "
        )
        params = [cutoff_ts]
        if is_red_only:
            sql += "AND t.is_red = 1 "
        sql += "GROUP BY t.telegraph_id ORDER BY t.timestamp DESC LIMIT ?"
        params.append(limit)
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        conn.close()
        result = []
        for row in rows:
            item = dict(row)
            stocks_str = item.pop("stocks_str", "") or ""
            item["stocks"] = [s for s in stocks_str.split("|") if s]
            result.append(item)
        return result

    def query_telegraph_stats(self, date: str = None) -> dict:
        """查询当日电报统计

        Returns:
            dict: {total, red_count, earliest_ts, latest_ts, hot_stocks: [...]}
        """
        if date is None:
            date = datetime.datetime.now().strftime("%Y-%m-%d")

        conn = self._conn()
        # 总数和红色数
        cur = conn.execute(
            "SELECT COUNT(*) as total, "
            "SUM(CASE WHEN is_red=1 THEN 1 ELSE 0 END) as red_count, "
            "MIN(timestamp) as earliest, MAX(timestamp) as latest "
            "FROM cls_telegraph WHERE date(timestamp, 'unixepoch') = ?",
            (date,)
        )
        row = cur.fetchone()
        total = row["total"] if row else 0
        red_count = row["red_count"] if row else 0
        earliest = row["earliest"] if row else 0
        latest = row["latest"] if row else 0

        # 热门股票（提及频次Top20）
        cur = conn.execute(
            "SELECT s.stock_name, COUNT(*) as cnt "
            "FROM cls_telegraph_stock s "
            "JOIN cls_telegraph t ON s.telegraph_id = t.telegraph_id "
            "WHERE date(t.timestamp, 'unixepoch') = ? "
            "GROUP BY s.stock_name ORDER BY cnt DESC LIMIT 20",
            (date,)
        )
        hot_stocks = [{"name": r["stock_name"], "mentions": r["cnt"]} for r in cur.fetchall()]

        conn.close()
        return {
            "total": total,
            "red_count": red_count,
            "earliest_ts": earliest,
            "latest_ts": latest,
            "hot_stocks": hot_stocks,
        }

    # ------------------------------------------------------------------
    # VIP文章
    # ------------------------------------------------------------------

    def upsert_vip_article(self, article: dict) -> bool:
        """写入VIP文章（按article_id去重）

        Args:
            article: {article_id, title, brief, published_at, related_stock}

        Returns:
            True=新写入, False=已存在
        """
        article_id = str(article.get("article_id", ""))
        if not article_id:
            return False

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = self._conn()
        cur = conn.execute(
            "SELECT id FROM cls_vip_article WHERE article_id=?", (article_id,)
        )
        if cur.fetchone():
            conn.close()
            return False

        conn.execute(
            "INSERT OR IGNORE INTO cls_vip_article "
            "(article_id, title, brief, published_at, related_stock, fetch_time) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                article_id,
                article.get("title", ""),
                article.get("brief", ""),
                article.get("published_at", ""),
                article.get("related_stock", ""),
                now_str,
            )
        )
        conn.commit()
        conn.close()
        return True

    def upsert_vip_discovered_stock(self, article_id: str, stock_name: str,
                                     stock_code: str, industry: str = "",
                                     match_score: int = 0,
                                     match_source: str = "",
                                     match_detail: str = ""):
        """写入VIP文章发现的股票"""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = self._conn()
        conn.execute(
            "INSERT INTO vip_discovered_stock "
            "(article_id, stock_name, stock_code, industry, match_score, "
            " match_source, match_detail, fetch_time) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (article_id, stock_name, stock_code, industry, match_score,
             match_source, match_detail, now_str)
        )
        conn.commit()
        conn.close()

    def query_vip_articles(self, date: str = None, limit: int = 50) -> List[dict]:
        """查询VIP文章"""
        conn = self._conn()
        sql = (
            "SELECT a.*, GROUP_CONCAT(s.stock_name||':'||s.stock_code, '|') as stocks_str "
            "FROM cls_vip_article a "
            "LEFT JOIN vip_discovered_stock s ON a.article_id = s.article_id "
        )
        params = []
        if date:
            sql += "WHERE date(a.published_at) = ? OR date(a.fetch_time) = ? "
            params = [date, date]
        sql += f"GROUP BY a.article_id ORDER BY a.fetch_time DESC LIMIT ?"
        params.append(limit)

        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        conn.close()

        result = []
        for row in rows:
            item = dict(row)
            stocks_str = item.pop("stocks_str", "") or ""
            stocks = []
            for pair in stocks_str.split("|"):
                if ":" in pair:
                    name, code = pair.split(":", 1)
                    stocks.append({"name": name, "code": code})
            item["stocks"] = stocks
            result.append(item)
        return result

    def query_vip_discovered_stocks(self, date: str = None, limit: int = 100) -> List[dict]:
        """查询VIP文章发现的股票（按 stock_code 聚合）"""
        conn = self._conn()
        sql = ("SELECT stock_code, stock_name, industry, COUNT(*) as cnt "
               "FROM vip_discovered_stock ")
        params = []
        if date:
            sql += ("WHERE article_id IN (SELECT article_id FROM cls_vip_article "
                    "WHERE date(fetch_time)=?) ")
            params.append(date)
        sql += "GROUP BY stock_code ORDER BY cnt DESC LIMIT ?"
        params.append(limit)
        cur = conn.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    # ------------------------------------------------------------------
    # 指数行情（dict 签名）
    # ------------------------------------------------------------------

    def upsert_index_quote(self, item: dict) -> bool:
        """写入指数行情。item: {name, code, trade_date, source, close, pct_chg,
        pre_close, amount, is_realtime}"""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO index_quote "
            "(name, code, trade_date, fetch_time, source, close, pct_chg, pre_close, amount, is_realtime) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (item.get("name", ""), item.get("code", ""), item.get("trade_date", ""),
             now_str, item.get("source", ""), item.get("close"), item.get("pct_chg"),
             item.get("pre_close"), item.get("amount"), int(item.get("is_realtime", 0)))
        )
        conn.commit()
        conn.close()
        return True

    def query_index_quote(self, date: str = None, realtime_only: bool = False) -> List[dict]:
        """查询指数行情（按日期，默认取每只指数最新一条）"""
        if date is None:
            date = datetime.datetime.now().strftime("%Y-%m-%d")
        conn = self._conn()
        sql = ("SELECT i.* FROM index_quote i "
               "INNER JOIN (SELECT name, MAX(fetch_time) ft FROM index_quote "
               "WHERE trade_date=? GROUP BY name) t ON i.name=t.name AND i.fetch_time=t.ft "
               "WHERE i.trade_date=?")
        params = [date, date]
        if realtime_only:
            sql += " AND i.is_realtime=1"
        cur = conn.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    # ------------------------------------------------------------------
    # 板块资金 / 涨停 / 龙虎榜 / 北向 / 融资融券（list 批量签名）
    # ------------------------------------------------------------------

    def upsert_sector_moneyflow(self, items: list) -> int:
        """items: [{trade_date, industry, net_mf_amount}, ...]"""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = self._conn()
        n = 0
        for it in items:
            conn.execute(
                "INSERT OR REPLACE INTO sector_moneyflow "
                "(trade_date, industry, net_mf_amount, fetch_time) VALUES (?, ?, ?, ?)",
                (it.get("trade_date", ""), it.get("industry", ""), it.get("net_mf_amount"), now_str)
            )
            n += 1
        conn.commit()
        conn.close()
        return n

    def query_sector_moneyflow(self, date: str = None, top_n: int = 20) -> List[dict]:
        if date is None:
            date = datetime.datetime.now().strftime("%Y-%m-%d")
        conn = self._conn()
        cur = conn.execute(
            "SELECT * FROM sector_moneyflow WHERE trade_date=? ORDER BY net_mf_amount DESC LIMIT ?",
            (date, top_n)
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    def upsert_limit_up(self, items: list) -> int:
        """items: [{trade_date, ts_code, name, pct_chg, industry, amount}, ...]"""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = self._conn()
        n = 0
        for it in items:
            conn.execute(
                "INSERT OR REPLACE INTO limit_up "
                "(trade_date, ts_code, name, pct_chg, industry, amount, fetch_time) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (it.get("trade_date", ""), it.get("ts_code", ""), it.get("name", ""),
                 it.get("pct_chg"), it.get("industry", ""), it.get("amount"), now_str)
            )
            n += 1
        conn.commit()
        conn.close()
        return n

    def query_limit_up(self, date: str = None) -> List[dict]:
        if date is None:
            date = datetime.datetime.now().strftime("%Y-%m-%d")
        conn = self._conn()
        cur = conn.execute(
            "SELECT * FROM limit_up WHERE trade_date=? ORDER BY pct_chg DESC", (date,)
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    def upsert_dragon_tiger(self, items: list) -> int:
        """items: [{trade_date, ts_code, name, net_buy, reason}, ...]"""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = self._conn()
        n = 0
        for it in items:
            conn.execute(
                "INSERT OR REPLACE INTO dragon_tiger "
                "(trade_date, ts_code, name, net_buy, reason, fetch_time) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (it.get("trade_date", ""), it.get("ts_code", ""), it.get("name", ""),
                 it.get("net_buy"), it.get("reason", ""), now_str)
            )
            n += 1
        conn.commit()
        conn.close()
        return n

    def query_dragon_tiger(self, date: str = None) -> List[dict]:
        if date is None:
            date = datetime.datetime.now().strftime("%Y-%m-%d")
        conn = self._conn()
        cur = conn.execute(
            "SELECT * FROM dragon_tiger WHERE trade_date=? ORDER BY net_buy DESC", (date,)
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    def upsert_north_money(self, item: dict) -> bool:
        """item: {trade_date, north_money, hgt, sgt, south_money}"""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO north_money "
            "(trade_date, north_money, hgt, sgt, south_money, fetch_time) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (item.get("trade_date", ""), item.get("north_money"), item.get("hgt"),
             item.get("sgt"), item.get("south_money"), now_str)
        )
        conn.commit()
        conn.close()
        return True

    def query_north_money(self, date: str = None) -> dict:
        if date is None:
            date = datetime.datetime.now().strftime("%Y-%m-%d")
        conn = self._conn()
        cur = conn.execute("SELECT * FROM north_money WHERE trade_date=?", (date,))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else {}

    def upsert_margin(self, items: list) -> int:
        """items: [{trade_date, exchange_id, rzye, rzche, rqye}, ...]"""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = self._conn()
        n = 0
        for it in items:
            conn.execute(
                "INSERT OR REPLACE INTO margin "
                "(trade_date, exchange_id, rzye, rzche, rqye, fetch_time) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (it.get("trade_date", ""), it.get("exchange_id", ""), it.get("rzye"),
                 it.get("rzche"), it.get("rqye"), now_str)
            )
            n += 1
        conn.commit()
        conn.close()
        return n

    def query_margin(self, date: str = None) -> List[dict]:
        if date is None:
            date = datetime.datetime.now().strftime("%Y-%m-%d")
        conn = self._conn()
        cur = conn.execute("SELECT * FROM margin WHERE trade_date=?", (date,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    # ------------------------------------------------------------------
    # 金股（dict 签名 + v4 扩展字段）
    # ------------------------------------------------------------------

    def upsert_gold_stock(self, item: dict) -> bool:
        """写入金股推荐。item 含基础字段+扩展字段(catalyst/dragon_vein/verification/
        signal_source/buy_range/target_price/stop_loss/strength)"""
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO gold_stock "
            "(name, code, recommend_date, report_type, reason, score, price_at_recommend, "
            " catalyst, dragon_vein, verification, signal_source, buy_range, "
            " target_price, stop_loss, strength) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (item.get("name", ""), item.get("code", ""), item.get("recommend_date", ""),
             item.get("report_type", ""), item.get("reason", ""), item.get("score", 0),
             item.get("price_at_recommend"), item.get("catalyst", ""),
             item.get("dragon_vein", ""), item.get("verification", ""),
             item.get("signal_source", ""), item.get("buy_range", ""),
             item.get("target_price", ""), item.get("stop_loss", ""),
             item.get("strength", "关注"))
        )
        conn.commit()
        conn.close()
        return True

    def update_gold_stock_backtest(self, code, recommend_date, current_price,
                                    return_1d=None, return_3d=None, return_5d=None,
                                    return_10d=None, return_20d=None,
                                    max_return=None, max_drawdown=None):
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = self._conn()
        conn.execute(
            "UPDATE gold_stock SET current_price=?, return_1d=?, return_3d=?, "
            "return_5d=?, return_10d=?, return_20d=?, max_return=?, max_drawdown=?, "
            "backtest_time=? WHERE code=? AND recommend_date=?",
            (current_price, return_1d, return_3d, return_5d, return_10d, return_20d,
             max_return, max_drawdown, now_str, code, recommend_date)
        )
        conn.commit()
        conn.close()

    def query_gold_stock(self, date: str = None, limit: int = 100) -> List[dict]:
        """查询金股（按推荐日期，默认最新）"""
        conn = self._conn()
        if date:
            cur = conn.execute(
                "SELECT * FROM gold_stock WHERE recommend_date=? ORDER BY score DESC LIMIT ?",
                (date, limit)
            )
        else:
            cur = conn.execute(
                "SELECT * FROM gold_stock ORDER BY recommend_date DESC, score DESC LIMIT ?",
                (limit,)
            )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    # 旧别名（向后兼容）
    def query_gold_stocks(self, limit=100) -> List[dict]:
        return self.query_gold_stock(limit=limit)

    # ------------------------------------------------------------------
    # 钱三强选股结果
    # ------------------------------------------------------------------

    def upsert_qian_sanqiang(self, items: list) -> int:
        """items: [{date, stock_code, stock_name, strategy, score, detail_json}, ...]"""
        conn = self._conn()
        n = 0
        for it in items:
            conn.execute(
                "INSERT INTO qian_sanqiang_result "
                "(date, stock_code, stock_name, strategy, score, detail_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (it.get("date", ""), it.get("stock_code", ""), it.get("stock_name", ""),
                 it.get("strategy", ""), it.get("score", 0),
                 it.get("detail_json", ""))
            )
            n += 1
        conn.commit()
        conn.close()
        return n

    def query_qian_sanqiang(self, date: str = None, limit: int = 50) -> List[dict]:
        if date is None:
            date = datetime.datetime.now().strftime("%Y-%m-%d")
        conn = self._conn()
        cur = conn.execute(
            "SELECT * FROM qian_sanqiang_result WHERE date=? ORDER BY score DESC LIMIT ?",
            (date, limit)
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    # ------------------------------------------------------------------
    # 市场洞见
    # ------------------------------------------------------------------

    def upsert_insight(self, item: dict) -> bool:
        """item: {date, period, category, signal_text, a_share_impact, confidence, signal_time}"""
        conn = self._conn()
        conn.execute(
            "INSERT INTO market_insight "
            "(date, period, category, signal_text, a_share_impact, confidence, signal_time) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (item.get("date", ""), item.get("period", ""), item.get("category", ""),
             item.get("signal_text", ""), item.get("a_share_impact", ""),
             item.get("confidence", "medium"), item.get("signal_time", ""))
        )
        conn.commit()
        conn.close()
        return True

    def query_insights(self, date: str = None, period: str = None) -> List[dict]:
        if date is None:
            date = datetime.datetime.now().strftime("%Y-%m-%d")
        conn = self._conn()
        sql = "SELECT * FROM market_insight WHERE date=?"
        params = [date]
        if period:
            sql += " AND period=?"
            params.append(period)
        sql += " ORDER BY id"
        cur = conn.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    # ------------------------------------------------------------------
    # 报告记录
    # ------------------------------------------------------------------

    def upsert_report(self, item: dict) -> int:
        """item: {date, period, title, content, char_count, quality_score}"""
        conn = self._conn()
        cur = conn.execute(
            "INSERT INTO report (date, period, title, content, char_count, quality_score) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (item.get("date", ""), item.get("period", ""), item.get("title", ""),
             item.get("content", ""), item.get("char_count", 0), item.get("quality_score", 0))
        )
        rid = cur.lastrowid
        conn.commit()
        conn.close()
        return rid

    def query_latest_report(self, period: str = None) -> dict:
        conn = self._conn()
        sql = "SELECT * FROM report"
        params = []
        if period:
            sql += " WHERE period=?"
            params.append(period)
        sql += " ORDER BY id DESC LIMIT 1"
        cur = conn.execute(sql, params)
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else {}

    def query_reports(self, date: str = None, limit: int = 50) -> List[dict]:
        conn = self._conn()
        if date:
            cur = conn.execute(
                "SELECT id, date, period, title, char_count, quality_score, created_at "
                "FROM report WHERE date=? ORDER BY id DESC LIMIT ?", (date, limit))
        else:
            cur = conn.execute(
                "SELECT id, date, period, title, char_count, quality_score, created_at "
                "FROM report ORDER BY id DESC LIMIT ?", (limit,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    # ------------------------------------------------------------------
    # 热度追踪
    # ------------------------------------------------------------------

    def upsert_heat_tracking(self, items: list) -> int:
        """items: [{date, sector, heat_score, capital_flow, limit_up_count, lifecycle}, ...]"""
        conn = self._conn()
        n = 0
        for it in items:
            conn.execute(
                "INSERT INTO heat_tracking "
                "(date, sector, heat_score, capital_flow, limit_up_count, lifecycle) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (it.get("date", ""), it.get("sector", ""), it.get("heat_score", 0),
                 it.get("capital_flow"), it.get("limit_up_count", 0), it.get("lifecycle", ""))
            )
            n += 1
        conn.commit()
        conn.close()
        return n

    def query_heat_tracking(self, date: str = None, sector: str = None,
                            days: int = 0) -> List[dict]:
        conn = self._conn()
        if days > 0:
            end = date or datetime.datetime.now().strftime("%Y-%m-%d")
            start = (datetime.datetime.strptime(end, "%Y-%m-%d")
                     - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
            sql = "SELECT * FROM heat_tracking WHERE date>=? AND date<=?"
            params = [start, end]
            if sector:
                sql += " AND sector=?"
                params.append(sector)
            sql += " ORDER BY date, sector"
            cur = conn.execute(sql, params)
        else:
            if date is None:
                date = datetime.datetime.now().strftime("%Y-%m-%d")
            sql = "SELECT * FROM heat_tracking WHERE date=?"
            params = [date]
            if sector:
                sql += " AND sector=?"
                params.append(sector)
            sql += " ORDER BY heat_score DESC"
            cur = conn.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    # ------------------------------------------------------------------
    # 学习记录
    # ------------------------------------------------------------------

    def upsert_learning_record(self, item: dict) -> bool:
        """item: {date, prediction, actual, gap_analysis, lesson, category}"""
        conn = self._conn()
        conn.execute(
            "INSERT INTO learning_record "
            "(date, prediction, actual, gap_analysis, lesson, category) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (item.get("date", ""), item.get("prediction", ""), item.get("actual", ""),
             item.get("gap_analysis", ""), item.get("lesson", ""), item.get("category", ""))
        )
        conn.commit()
        conn.close()
        return True

    def query_learning_records(self, limit: int = 30) -> List[dict]:
        conn = self._conn()
        cur = conn.execute(
            "SELECT * FROM learning_record ORDER BY id DESC LIMIT ?", (limit,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    # ------------------------------------------------------------------
    # 网站数据快照
    # ------------------------------------------------------------------

    def upsert_website_snapshot(self, snapshot_json: str, date: str, period: str = None) -> bool:
        conn = self._conn()
        conn.execute(
            "INSERT INTO website_snapshot (date, period, snapshot_json) VALUES (?, ?, ?)",
            (date, period, snapshot_json)
        )
        conn.commit()
        conn.close()
        return True

    def query_website_snapshot(self, date: str = None, period: str = None) -> dict:
        conn = self._conn()
        sql = "SELECT * FROM website_snapshot"
        params = []
        clauses = []
        if date:
            clauses.append("date=?")
            params.append(date)
        if period:
            clauses.append("period=?")
            params.append(period)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT 1"
        cur = conn.execute(sql, params)
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else {}

    # ------------------------------------------------------------------
    # 财经日历事件
    # ------------------------------------------------------------------

    def upsert_calendar_event(self, items: list) -> int:
        """items: [{event_date, event_time, title, importance, category, detail}, ...]"""
        conn = self._conn()
        n = 0
        for it in items:
            conn.execute(
                "INSERT INTO calendar_event "
                "(event_date, event_time, title, importance, category, detail) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (it.get("event_date", ""), it.get("event_time", ""), it.get("title", ""),
                 it.get("importance", "medium"), it.get("category", ""), it.get("detail", ""))
            )
            n += 1
        conn.commit()
        conn.close()
        return n

    def query_calendar_events(self, start_date: str = None, end_date: str = None) -> List[dict]:
        conn = self._conn()
        sql = "SELECT * FROM calendar_event"
        params = []
        clauses = []
        if start_date:
            clauses.append("event_date>=?")
            params.append(start_date)
        if end_date:
            clauses.append("event_date<=?")
            params.append(end_date)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY event_date, event_time"
        cur = conn.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    # ------------------------------------------------------------------
    # 共振分析 — 跨数据源交叉匹配
    # ------------------------------------------------------------------

    def query_resonance(self, date: str = None) -> List[dict]:
        """共振分析：查找同时出现在多个数据源中的股票

        数据源:
          1. 电报提及 (cls_telegraph_stock)
          2. 龙虎榜 (dragon_tiger)
          3. 涨停板 (limit_up)
          4. VIP文章发现 (vip_discovered_stock)

        共振 = 一只股票同时出现在 >=2 个数据源中。
        出现的数据源越多，共振信号越强。

        Returns:
            list: [{stock_name, stock_code, sources: [...], source_count, details: {...}}]
        """
        if date is None:
            date = datetime.datetime.now().strftime("%Y-%m-%d")

        conn = self._conn()
        resonance = {}

        # 1. 电报提及
        cur = conn.execute(
            "SELECT s.stock_name, COUNT(DISTINCT t.telegraph_id) as mention_cnt "
            "FROM cls_telegraph_stock s "
            "JOIN cls_telegraph t ON s.telegraph_id = t.telegraph_id "
            "WHERE date(t.timestamp, 'unixepoch') = ? "
            "GROUP BY s.stock_name",
            (date,)
        )
        for row in cur.fetchall():
            name = row["stock_name"]
            if name not in resonance:
                resonance[name] = {"stock_name": name, "stock_code": "", "sources": [], "details": {}}
            resonance[name]["sources"].append("电报提及")
            resonance[name]["details"]["telegraph_mentions"] = row["mention_cnt"]

        # 2. 龙虎榜
        cur = conn.execute(
            "SELECT ts_code, name, net_buy, reason FROM dragon_tiger WHERE trade_date=?",
            (date,)
        )
        for row in cur.fetchall():
            name = row["name"] or row["ts_code"]
            if name not in resonance:
                resonance[name] = {"stock_name": name, "stock_code": row["ts_code"], "sources": [], "details": {}}
            resonance[name]["stock_code"] = resonance[name]["stock_code"] or row["ts_code"]
            resonance[name]["sources"].append("龙虎榜")
            resonance[name]["details"]["dragon_tiger_net_buy"] = row["net_buy"]
            if row["reason"]:
                resonance[name]["details"]["dragon_tiger_reason"] = row["reason"]

        # 3. 涨停板
        cur = conn.execute(
            "SELECT ts_code, name, pct_chg, industry, amount FROM limit_up WHERE trade_date=?",
            (date,)
        )
        for row in cur.fetchall():
            name = row["name"] or row["ts_code"]
            if name not in resonance:
                resonance[name] = {"stock_name": name, "stock_code": row["ts_code"], "sources": [], "details": {}}
            resonance[name]["stock_code"] = resonance[name]["stock_code"] or row["ts_code"]
            resonance[name]["sources"].append("涨停板")
            resonance[name]["details"]["limit_up_industry"] = row["industry"]
            resonance[name]["details"]["limit_up_amount"] = row["amount"]

        # 4. VIP文章发现
        cur = conn.execute(
            "SELECT stock_name, stock_code, industry, match_source, match_detail, article_id "
            "FROM vip_discovered_stock "
            "WHERE article_id IN (SELECT article_id FROM cls_vip_article WHERE date(fetch_time)=?)",
            (date,)
        )
        for row in cur.fetchall():
            name = row["stock_name"]
            if name not in resonance:
                resonance[name] = {"stock_name": name, "stock_code": row["stock_code"], "sources": [], "details": {}}
            resonance[name]["stock_code"] = resonance[name]["stock_code"] or row["stock_code"]
            resonance[name]["sources"].append("VIP文章")
            resonance[name]["details"]["vip_industry"] = row["industry"]
            resonance[name]["details"]["vip_match_source"] = row["match_source"]
            if row["match_detail"]:
                resonance[name]["details"]["vip_match_detail"] = row["match_detail"]

        conn.close()

        # 过滤：只保留出现在>=2个数据源中的股票
        result = []
        for name, info in resonance.items():
            unique_sources = list(set(info["sources"]))
            if len(unique_sources) >= 2:
                info["sources"] = unique_sources
                info["source_count"] = len(unique_sources)
                result.append(info)

        # 按数据源数量降序
        result.sort(key=lambda x: x["source_count"], reverse=True)
        return result

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """数据库统计信息"""
        conn = self._conn()
        stats = {}
        tables = [
            "raw_cache", "index_quote", "sector_moneyflow", "limit_up",
            "dragon_tiger", "north_money", "margin", "cls_telegraph",
            "cls_telegraph_stock", "cls_vip_article", "vip_discovered_stock", "gold_stock",
            "market_insight", "report", "qian_sanqiang_result", "heat_tracking",
            "learning_record", "website_snapshot", "calendar_event"
        ]
        for table in tables:
            cur = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}")
            stats[table] = cur.fetchone()["cnt"]
        conn.close()
        return stats

    def vacuum(self):
        """压缩数据库"""
        conn = self._conn()
        conn.execute("VACUUM")
        conn.close()
        print("[DB] VACUUM 完成")


if __name__ == "__main__":
    db = DB()
    db.init()
    stats = db.get_stats()
    print("\n=== 数据库统计 ===")
    for table, count in stats.items():
        print(f"  {table}: {count} 条")
