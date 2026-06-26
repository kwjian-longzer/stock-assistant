# -*- coding: utf-8 -*-
"""
data_collector.py  —  v4.0 数据采集层（DB 驱动）
=================================================
三时点采集：morning / noon / evening，全部走 db.get_or_fetch() 缓存，
采集结果直接写入对应 DB 表，不再依赖 JSON 文件。

用法:
    python data_collector.py --period morning
    python data_collector.py --period noon
    python data_collector.py --period evening
    python data_collector.py --period qian_sanqiang
    python data_collector.py --period calendar
    python data_collector.py --period all       # 采集全部（测试用）
"""

import sys
import os
import json
import time
import datetime
import argparse
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from settings import get_tushare_token

SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn",
}
REQUEST_TIMEOUT = 12

# A 股指数 Sina 实时代码
A_INDEX_CODES = ["sh000001", "sz399001", "sz399006", "sh000688"]
A_INDEX_NAMES = {
    "sh000001": "上证指数", "sz399001": "深证成指",
    "sz399006": "创业板指", "sh000688": "科创50",
}
# 美股/港股/外汇/商品 Sina 代码
US_HK_FX_CODES = {
    "int_dji": "道琼斯", "int_nasdaq": "纳斯达克", "int_sp500": "标普500",
    "int_hangseng": "恒生指数", "rt_hkHSTECH": "恒生科技",
    "DINIW": "美元指数", "fx_susdcny": "离岸人民币", "hf_GC": "COMEX黄金", "hf_CL": "WTI原油",
}


def safe_float(v, default=None):
    try:
        if v in (None, "", "0.000", "00:00:00"):
            return default
        return float(v)
    except (ValueError, TypeError):
        return default


def parse_sina_hq(text):
    """解析新浪行情字符串 hq_str_xxx="字段1,字段2,..."  → (name, fields_list)"""
    if not text or "=" not in text:
        return None, []
    try:
        body = text.split('="', 1)[1].rsplit('"', 1)[0]
        parts = body.split(",")
        name = parts[0] if parts else ""
        return name, parts
    except Exception:
        return None, []


def fetch_sina(codes, timeout=REQUEST_TIMEOUT):
    """批量获取新浪行情，返回 {code: parts_list}"""
    url = "https://hq.sinajs.cn/list=" + ",".join(codes)
    try:
        resp = requests.get(url, headers=SINA_HEADERS, timeout=timeout)
        resp.encoding = "gbk"
        result = {}
        for line in resp.text.strip().split("\n"):
            line = line.strip()
            if "=" not in line:
                continue
            code = line.split("hq_str_")[-1].split("=")[0].strip()
            name, parts = parse_sina_hq(line)
            if parts:
                result[code] = parts
        return result
    except Exception as e:
        print(f"  [WARN] Sina请求失败: {e}")
        return {}


# ---------------------------------------------------------------------------
# Tushare 封装（走 DB 缓存，缓存存储为 list-of-dicts）
# ---------------------------------------------------------------------------

_pro = None


def get_pro():
    global _pro
    if _pro is None:
        import tushare as ts
        ts.set_token(get_tushare_token())
        _pro = ts.pro_api()
    return _pro


def get_latest_trade_date(pro):
    """获取最新交易日 YYYYMMDD"""
    today = datetime.datetime.now().strftime("%Y%m%d")
    df = pro.trade_cal(exchange="SSE", end_date=today, is_open=1, fields="cal_date")
    if df is not None and len(df) > 0:
        return sorted(df["cal_date"].tolist())[-1]
    return today


# ---------------------------------------------------------------------------
# 各数据源采集
# ---------------------------------------------------------------------------

def collect_a_index_sina(db, date_str, is_realtime=False):
    """通过 Sina 获取 A 股指数实时行情，写入 index_quote"""
    data = fetch_sina(A_INDEX_CODES)
    code_map = {"sh000001": "000001.SH", "sz399001": "399001.SZ",
                "sz399006": "399006.SZ", "sh000688": "000688.SH"}
    n = 0
    for sina_code, ts_code in code_map.items():
        parts = data.get(sina_code)
        if not parts or len(parts) < 6:
            continue
        price = safe_float(parts[3])
        pre_close = safe_float(parts[2])
        pct = round((price - pre_close) / pre_close * 100, 3) if price and pre_close else None
        item = {
            "name": A_INDEX_NAMES.get(sina_code, parts[0]),
            "code": ts_code, "trade_date": date_str,
            "source": "sina_realtime", "close": price,
            "pct_chg": pct, "pre_close": pre_close,
            "amount": safe_float(parts[9]) if len(parts) > 9 else None,
            "is_realtime": 1 if is_realtime else 0,
        }
        db.upsert_index_quote(item)
        n += 1
    print(f"  [指数-Sina] 写入 {n} 条 ({date_str})")
    return n


def collect_global_sina(db, date_str, period):
    """获取美股/港股/外汇/商品，经 DB 缓存写入 raw_cache 供洞见引擎读取"""
    def _fetch():
        data = fetch_sina(list(US_HK_FX_CODES.keys()))
        result = {}
        for code, parts in data.items():
            if not parts or len(parts) < 3:
                continue
            price, chg, chg_pct = None, None, None
            if code.startswith("int_"):
                # int_dji: ['道琼斯','46247.29','299.97','0.65'] → price,chg,chg%
                price = safe_float(parts[1])
                chg = safe_float(parts[2])
                chg_pct = safe_float(parts[3])
            elif code.startswith("rt_"):
                # rt_hkHSTECH: ['HSTECH','恒生科技指数','4371.540','4405.920'] → price,prev_close
                price = safe_float(parts[1])
                if price is None and len(parts) > 2:
                    price = safe_float(parts[2])
                prev = safe_float(parts[3]) if len(parts) > 3 else None
                if price and prev:
                    chg = round(price - prev, 2)
                    chg_pct = round(chg / prev * 100, 2)
            elif code.startswith("fx_"):
                # fx_susdcny: ['01:08:22','6.7868','6.8153','6.7992'] → price,prev_close,open
                price = safe_float(parts[1])
                prev = safe_float(parts[7]) if len(parts) > 7 else safe_float(parts[2])
                if price and prev:
                    chg = round(price - prev, 4)
                    chg_pct = round(chg / prev * 100, 2)
            elif code.startswith("hf_"):
                # hf_GC: ['4098.344','','4097.200','4097.400'] → price,_,prev_close,open
                price = safe_float(parts[0])
                prev = safe_float(parts[2]) if len(parts) > 2 else None
                if price and prev:
                    chg = round(price - prev, 2)
                    chg_pct = round(chg / prev * 100, 2)
            else:
                # DINIW(美元指数): ['01:28:16','101.3274','101.3274','101.4559'] → time,price,price,prev
                price = safe_float(parts[1])
                prev = safe_float(parts[3]) if len(parts) > 3 else None
                if price and prev:
                    chg = round(price - prev, 4)
                    chg_pct = round(chg / prev * 100, 2)
            result[US_HK_FX_CODES.get(code, code)] = {
                "price": price, "chg": chg, "chg_pct": chg_pct}
        return result

    result = db.get_or_fetch("sina", f"global_{period}", _fetch,
                             trade_date=date_str, params={}, ttl_hours=6)
    print(f"  [全球] 写入 {len(result) if result else 0} 项 ({date_str}/{period})")
    return result or {}


def collect_a_index_tushare(db, date_str, trade_date_yyyymmdd):
    """通过 Tushare index_daily 获取 A 股指数收盘行情（前一日或当日收盘）"""
    pro = get_pro()
    indices = [("000001.SH", "上证指数"), ("399001.SZ", "深证成指"),
               ("399006.SZ", "创业板指"), ("000688.SH", "科创50")]
    n = 0
    for ts_code, name in indices:
        try:
            df = pro.index_daily(ts_code=ts_code, trade_date=trade_date_yyyymmdd)
            if df is None or len(df) == 0:
                df = pro.index_daily(ts_code=ts_code, start_date="20260101",
                                     end_date=trade_date_yyyymmdd)
                if df is not None and len(df) > 0:
                    df = df.head(1)
            if df is None or len(df) == 0:
                continue
            r = df.iloc[0].to_dict()
            pre = safe_float(r.get("pre_close"))
            close = safe_float(r.get("close"))
            pct = round((close - pre) / pre * 100, 3) if pre and close else None
            item = {
                "name": name, "code": ts_code, "trade_date": date_str,
                "source": "tushare_close", "close": close, "pct_chg": pct,
                "pre_close": pre, "amount": safe_float(r.get("amount")),
                "is_realtime": 0,
            }
            db.upsert_index_quote(item)
            n += 1
        except Exception as e:
            print(f"  [指数-Tushare] {ts_code} 失败: {e}")
    print(f"  [指数-Tushare] 写入 {n} 条 ({date_str})")
    return n


def collect_sector_moneyflow(db, date_str, trade_date_yyyymmdd):
    """获取板块资金流向，聚合写入 sector_moneyflow"""
    pro = get_pro()

    def _fetch_basic():
        df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,industry")
        return df.to_dict("records") if df is not None and len(df) > 0 else []
    sb_list = db.get_or_fetch("tushare", "stock_basic", _fetch_basic,
                              trade_date=None, params={}, ttl_hours=24)
    industry_map = {r["ts_code"]: r.get("industry", "") for r in (sb_list or [])}

    def _fetch_mf():
        df = pro.moneyflow(trade_date=trade_date_yyyymmdd)
        return df.to_dict("records") if df is not None and len(df) > 0 else []
    mf_list = db.get_or_fetch("tushare", "moneyflow", _fetch_mf,
                              trade_date=date_str, params={"td": trade_date_yyyymmdd},
                              ttl_hours=12)
    if not mf_list:
        print(f"  [板块资金] 无数据 ({trade_date_yyyymmdd})")
        return 0
    agg = {}
    for row in mf_list:
        ind = industry_map.get(row.get("ts_code"), "") or "其他"
        agg.setdefault(ind, 0.0)
        agg[ind] += safe_float(row.get("net_mf_amount"), 0) or 0
    items = [{"trade_date": date_str, "industry": ind,
              "net_mf_amount": round(v / 1e4, 2)} for ind, v in agg.items()]
    items.sort(key=lambda x: x["net_mf_amount"], reverse=True)
    items = items[:30]
    n = db.upsert_sector_moneyflow(items)
    print(f"  [板块资金] 写入 {n} 行业 ({date_str})")
    return n


def collect_north_money(db, date_str, trade_date_yyyymmdd):
    """获取北向资金，写入 north_money"""
    pro = get_pro()

    def _fetch():
        df = pro.moneyflow_hsgt(trade_date=trade_date_yyyymmdd)
        return df.to_dict("records") if df is not None and len(df) > 0 else []
    rows = db.get_or_fetch("tushare", "moneyflow_hsgt", _fetch,
                           trade_date=date_str, params={"td": trade_date_yyyymmdd},
                           ttl_hours=12)
    if not rows:
        print(f"  [北向] 无数据 ({trade_date_yyyymmdd})")
        return 0
    r = rows[0]
    north = safe_float(r.get("north_money"))
    if north:
        item = {
            "trade_date": date_str, "north_money": round(north / 1e4, 2),
            "hgt": round(safe_float(r.get("hgt"), 0) / 1e4, 2),
            "sgt": round(safe_float(r.get("sgt"), 0) / 1e4, 2),
            "south_money": round(safe_float(r.get("south_money"), 0) / 1e4, 2),
        }
        db.upsert_north_money(item)
        print(f"  [北向] 北向净流入 {item['north_money']}亿 ({date_str})")
        return 1
    return 0


def collect_dragon_tiger(db, date_str, trade_date_yyyymmdd):
    """获取龙虎榜，写入 dragon_tiger"""
    pro = get_pro()

    def _fetch():
        df = pro.top_list(trade_date=trade_date_yyyymmdd)
        return df.to_dict("records") if df is not None and len(df) > 0 else []
    rows = db.get_or_fetch("tushare", "top_list", _fetch,
                           trade_date=date_str, params={"td": trade_date_yyyymmdd},
                           ttl_hours=12)
    if not rows:
        print(f"  [龙虎榜] 无数据 ({trade_date_yyyymmdd})")
        return 0
    agg = {}
    for row in rows:
        code = row.get("ts_code", "")
        if not code:
            continue
        agg.setdefault(code, {"name": row.get("name", ""), "net": 0.0, "reason": set()})
        agg[code]["net"] += safe_float(row.get("net_amount"), 0) or 0
        rsn = row.get("exalter") or row.get("reason", "")
        if rsn:
            agg[code]["reason"].add(str(rsn))
    items = [{"trade_date": date_str, "ts_code": c,
              "name": v["name"], "net_buy": round(v["net"] / 1e8, 2),
              "reason": ";".join(list(v["reason"])[:3])}
             for c, v in agg.items()]
    items.sort(key=lambda x: x["net_buy"], reverse=True)
    items = items[:30]
    n = db.upsert_dragon_tiger(items)
    print(f"  [龙虎榜] 写入 {n} 条 ({date_str})")
    return n


def collect_limit_up_eastmoney(db, date_str):
    """涨停池（东方财富，替代 Tushare limit_list_d 高积分要求）"""
    url = ("https://push2ex.eastmoney.com/getTopicZTPool"
           "?ut=7eea3edcaed734bea9c&dpt=wz.ztzt&Ession=128424500"
           f"&date={date_str.replace('-', '')}&_=1")
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT,
                            headers={"User-Agent": SINA_HEADERS["User-Agent"]})
        data = resp.json() or {}
        pool = (data.get("data") or {}).get("pool", []) or []
        items = []
        for s in pool:
            code = s.get("c", "")
            items.append({
                "trade_date": date_str,
                "ts_code": f"{code}.SZ" if code and code[0] in "03" else f"{code}.SH",
                "name": s.get("n", ""), "pct_chg": safe_float(s.get("zdp")),
                "industry": s.get("hybk", ""), "amount": safe_float(s.get("amount")),
            })
        if items:
            n = db.upsert_limit_up(items)
            print(f"  [涨停池-东财] 写入 {n} 条 ({date_str})")
            return n
        print(f"  [涨停池-东财] 无数据 ({date_str})")
        return 0
    except Exception as e:
        print(f"  [涨停池-东财] 失败: {e}")
        return 0


def collect_margin(db, date_str, trade_date_yyyymmdd):
    """获取融资融券，写入 margin"""
    pro = get_pro()

    def _fetch():
        df = pro.margin(trade_date=trade_date_yyyymmdd)
        return df.to_dict("records") if df is not None and len(df) > 0 else []
    rows = db.get_or_fetch("tushare", "margin", _fetch,
                           trade_date=date_str, params={"td": trade_date_yyyymmdd},
                           ttl_hours=12)
    if not rows:
        print(f"  [融资融券] 无数据 ({trade_date_yyyymmdd})")
        return 0
    items = [{"trade_date": date_str, "exchange_id": r.get("exchange_id", ""),
              "rzye": safe_float(r.get("rzye")), "rzche": safe_float(r.get("rzche")),
              "rqye": safe_float(r.get("rqye"))} for r in rows]
    n = db.upsert_margin(items)
    print(f"  [融资融券] 写入 {n} 条 ({date_str})")
    return n


def collect_qian_sanqiang(db, date_str):
    """钱三强选股，写入 qian_sanqiang_result（T1.5）"""
    try:
        from qian_sanqiang_selector import QianSanQiangSelector, display_results
        selector = QianSanQiangSelector()
        df, latest_date, mf_date = selector.run()
        output = display_results(df, latest_date, mf_date)
        items = []
        selected_codes = set()
        for s in output.get("selected_stocks", []):
            items.append({
                "date": date_str, "stock_code": s.get("ts_code"),
                "stock_name": s.get("name"), "strategy": "三强全中",
                "score": 100, "detail_json": json.dumps(s, ensure_ascii=False, default=str),
            })
            selected_codes.add(s.get("ts_code"))
        for s in output.get("two_of_three_stocks", []):
            if s.get("ts_code") in selected_codes:
                continue
            items.append({
                "date": date_str, "stock_code": s.get("ts_code"),
                "stock_name": s.get("name"), "strategy": "两强命中",
                "score": 70, "detail_json": json.dumps(s, ensure_ascii=False, default=str),
            })
        n = db.upsert_qian_sanqiang(items)
        os.makedirs("data", exist_ok=True)
        with open("data/qian_sanqiang_results.json", "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2, default=str)
        print(f"  [钱三强] 写入 {n} 条 ({date_str})")
        return n
    except Exception as e:
        print(f"  [钱三强] 失败: {e}")
        return 0


def collect_calendar(db):
    """财经日历事件，写入 calendar_event（T1.6）
    尝试东方财富财经日历 API，失败则降级。"""
    today = datetime.datetime.now()
    end = (today + datetime.timedelta(days=14)).strftime("%Y-%m-%d")
    start = today.strftime("%Y-%m-%d")
    items = []
    try:
        url = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
               "?reportName=RPT_ECONOMY_CALENDAR"
               "&sortColumns=RELEASE_TIME&sortTypes=1"
               f"&filter=(RELEASE_TIME>='{start}')(RELEASE_TIME<='{end} 23:59:59')"
               "&pageNumber=1&pageSize=80")
        resp = requests.get(url, timeout=REQUEST_TIMEOUT,
                            headers={"User-Agent": SINA_HEADERS["User-Agent"]})
        data = resp.json() or {}
        rows = (data.get("result") or {}).get("data", []) or []
        for r in rows:
            rt = r.get("RELEASE_TIME", "")
            items.append({
                "event_date": rt[:10], "event_time": rt[11:16],
                "title": r.get("INDICATOR_NAME", ""),
                "importance": {1: "high", 2: "medium", 3: "low"}.get(
                    r.get("IMPORTANCE"), "medium"),
                "category": r.get("COUNTRY", ""),
                "detail": r.get("COMMENT", "") or "",
            })
    except Exception as e:
        print(f"  [日历] 东方财富接口失败({e})，降级为空")
    if items:
        n = db.upsert_calendar_event(items)
        print(f"  [日历] 写入 {n} 条事件")
    else:
        print("  [日历] 无可用事件")
    return len(items)


# ---------------------------------------------------------------------------
# 三时点编排
# ---------------------------------------------------------------------------

def collect_morning(db, date_str=None):
    """盘前采集（08:30）：隔夜全球市场 + A股指数(前收) + 板块资金 + 北向"""
    if date_str is None:
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    print(f"\n=== 盘前采集 {date_str} ===")
    pro = get_pro()
    trade_date = get_latest_trade_date(pro)
    collect_global_sina(db, date_str, "morning")
    collect_a_index_tushare(db, date_str, trade_date)
    collect_sector_moneyflow(db, date_str, trade_date)
    collect_north_money(db, date_str, trade_date)
    print("--- 盘前采集完成 ---")


def collect_noon(db, date_str=None):
    """盘中采集（11:50）：A股实时指数 + 板块资金 + 北向"""
    if date_str is None:
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    print(f"\n=== 盘中采集 {date_str} ===")
    pro = get_pro()
    trade_date = get_latest_trade_date(pro)
    collect_a_index_sina(db, date_str, is_realtime=True)
    collect_sector_moneyflow(db, date_str, trade_date)
    collect_north_money(db, date_str, trade_date)
    print("--- 盘中采集完成 ---")


def collect_evening(db, date_str=None):
    """盘后采集（15:30）：A股收盘 + 涨停池 + 龙虎榜 + 融资融券 + 板块资金 + 北向"""
    if date_str is None:
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    print(f"\n=== 盘后采集 {date_str} ===")
    pro = get_pro()
    trade_date = get_latest_trade_date(pro)
    collect_a_index_tushare(db, date_str, trade_date)
    collect_limit_up_eastmoney(db, date_str)
    collect_dragon_tiger(db, date_str, trade_date)
    collect_margin(db, date_str, trade_date)
    collect_sector_moneyflow(db, date_str, trade_date)
    collect_north_money(db, date_str, trade_date)
    print("--- 盘后采集完成 ---")


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="v4.0 数据采集器")
    parser.add_argument("--period", required=True,
                        choices=["morning", "noon", "evening", "qian_sanqiang",
                                 "calendar", "all"],
                        help="采集时段")
    parser.add_argument("--date", default=None, help="日期 YYYY-MM-DD（默认今天）")
    args = parser.parse_args()

    from db import DB
    db = DB()
    db.init()
    date_str = args.date or datetime.datetime.now().strftime("%Y-%m-%d")

    if args.period == "morning":
        collect_morning(db, date_str)
    elif args.period == "noon":
        collect_noon(db, date_str)
    elif args.period == "evening":
        collect_evening(db, date_str)
    elif args.period == "qian_sanqiang":
        collect_qian_sanqiang(db, date_str)
    elif args.period == "calendar":
        collect_calendar(db)
    elif args.period == "all":
        collect_morning(db, date_str)
        collect_noon(db, date_str)
        collect_evening(db, date_str)
        collect_calendar(db)

    stats = db.get_stats()
    print("\n[DB统计]", json.dumps(stats, ensure_ascii=False)[:300])


if __name__ == "__main__":
    main()
