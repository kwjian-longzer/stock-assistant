#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票数据采集脚本
支持: morning(早报) / noon(午报) / evening(晚报) / weekend(周末)

功能模块:
  - A股指数日线 (Tushare)
  - 美股盘前期货 + 收盘指数 (新浪财经HTTP)
  - 港股指数 (新浪财经HTTP)
  - 外汇商品 (新浪财经HTTP)
  - 官方媒体头条 (网页抓取 + 降级)
  - 财联社电报 (CLS API /api/cache)
  - 财联社深度/VIP/投资日历/首页 (浏览器采集 cls_pages.json)
  - 资金流向、龙虎榜、龙虎榜机构明细 (Tushare)
  - 融资融券、沪深港通 (Tushare)
  - 涨跌停、每日指标 (Tushare)

每个数据源均有降级/替代机制，采集完成后运行数据质量校验。
"""

import sys
import os
import json
import re
import datetime
import time
import traceback
import subprocess
import hashlib
import urllib.parse

import requests

# ---------------------------------------------------------------------------
# 依赖自动安装（确保新会话中也能运行）
# ---------------------------------------------------------------------------
def _ensure_package(package, import_name=None):
    """确保Python包已安装，未安装则自动pip install"""
    import_name = import_name or package
    try:
        __import__(import_name)
    except ImportError:
        print(f"[INFO] 自动安装依赖: {package} ...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", package, "--break-system-packages", "-q"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        print(f"[INFO] {package} 安装完成")

_ensure_package("tushare")
_ensure_package("requests")

# ---------------------------------------------------------------------------
# 全局配置
# ---------------------------------------------------------------------------

TUSHARE_TOKEN = "8eaad9971749da18299f4932a7cabf068a495fdf06ef3aaafebfe365"

SINA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.sina.com.cn",
}

WEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

REQUEST_TIMEOUT = 15  # 秒

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def safe_float(val, default=0.0):
    """安全转换为浮点数"""
    try:
        if val is None or val == "" or val == "-":
            return default
        return float(val)
    except (ValueError, TypeError):
        return default


def record_quality(data_quality, source_name, status, source_type,
                   record_count=0, notes=""):
    """记录单个数据源的质量信息"""
    data_quality[source_name] = {
        "status": status,          # OK | DEGRADED | FAILED
        "source": source_type,     # tushare | sina_http | web_scrape
        "record_count": record_count,
        "notes": notes,
    }


def parse_sina_hq(text):
    """解析新浪行情接口返回的文本，返回字段列表。
    格式: var hq_str_XXXX="字段1,字段2,..."
    """
    if not text or len(text) < 10:
        return None
    try:
        data_part = text.split('"')[1] if '"' in text else ""
        if not data_part:
            return None
        return data_part.split(",")
    except (IndexError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Tushare 接口
# ---------------------------------------------------------------------------


def get_tushare_pro():
    """初始化 Tushare Pro API"""
    try:
        import tushare as ts
        ts.set_token(TUSHARE_TOKEN)
        return ts.pro_api()
    except Exception as e:
        print(f"[ERROR] Tushare初始化失败: {e}")
        return None


def get_trade_date(pro, date_str):
    """获取最近交易日：如果 date_str 不是交易日则回退到前一个交易日"""
    try:
        df = pro.trade_cal(exchange='SSE', start_date=date_str, end_date=date_str)
        if df is not None and len(df) > 0 and df.iloc[0]['is_open'] == 1:
            return date_str
        # 今天不是交易日，往前找
        df = pro.trade_cal(exchange='SSE', end_date=date_str, is_open=1)
        if df is not None and len(df) > 0:
            return df.iloc[-1]['cal_date']
    except Exception as e:
        print(f"[WARN] 交易日判断异常: {e}")
    return date_str


def fetch_index_daily(pro, ts_code, start_date, end_date):
    """获取指数日线数据"""
    try:
        df = pro.index_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is not None and len(df) > 0:
            return df.to_dict('records')
    except Exception as e:
        print(f"[ERROR] 获取指数 {ts_code} 日线失败: {e}")
    return []


def _fetch_with_rollback(pro, api_name, trade_date, **kwargs):
    """通用Tushare数据获取，支持空数据自动回滚前5天"""
    from datetime import datetime, timedelta
    dt = datetime.strptime(trade_date, '%Y%m%d')
    for i in range(6):
        try_date = (dt - timedelta(days=i)).strftime('%Y%m%d')
        try:
            api = getattr(pro, api_name)
            if api_name == 'moneyflow_hsgt':
                df = api(start_date=try_date, end_date=try_date, **kwargs)
            else:
                df = api(trade_date=try_date, **kwargs)
            if df is not None and len(df) > 0:
                if i > 0:
                    print(f"[INFO] {api_name}({trade_date}) 返回空，回滚到 {try_date}")
                return df.to_dict('records')
        except Exception as e:
            print(f"[WARN] {api_name}({try_date}) 失败: {e}")
            break  # 接口错误直接退出（如权限问题），不回滚
    return []


def fetch_moneyflow(pro, trade_date):
    """获取资金流向数据"""
    return _fetch_with_rollback(pro, 'moneyflow', trade_date)


def fetch_top_list(pro, trade_date):
    """获取龙虎榜数据"""
    return _fetch_with_rollback(pro, 'top_list', trade_date)


def fetch_top_inst(pro, trade_date):
    """获取龙虎榜机构明细"""
    return _fetch_with_rollback(pro, 'top_inst', trade_date)


def fetch_hsgt(pro, trade_date):
    """获取沪深港通资金流向"""
    return _fetch_with_rollback(pro, 'moneyflow_hsgt', trade_date)


def fetch_limit_list(pro, trade_date):
    """获取涨停跌停数据
    优先使用 limit_list_d（需要5000积分），降级到 limit_list_ths（需要8000积分）
    如果都无权限，返回空列表，由调用方使用 top_list 降级
    """
    # 方案1: limit_list_d
    try:
        df = pro.limit_list_d(trade_date=trade_date, limit_type='U')
        if df is not None and len(df) > 0:
            return df.to_dict('records')
    except Exception as e:
        print(f"[INFO] limit_list_d 失败: {e}")

    # 方案2: limit_list_ths
    try:
        df = pro.limit_list_ths(trade_date=trade_date, limit_type='涨停池')
        if df is not None and len(df) > 0:
            return df.to_dict('records')
    except Exception as e:
        print(f"[INFO] limit_list_ths 失败: {e}")

    return []


def fetch_margin(pro, trade_date):
    """获取融资融券数据（支持空数据自动回滚）"""
    return _fetch_with_rollback(pro, 'margin', trade_date)


def fetch_daily_basic(pro, trade_date):
    """获取每日指标（支持空数据自动回滚）"""
    return _fetch_with_rollback(pro, 'daily_basic', trade_date)


def fetch_limit_list_eastmoney(trade_date):
    """东方财富涨停池数据（替代方案）
    返回格式: {"data": {"pool": [{"c": "股票代码", "n": "名称", "p": 价格, "zdp": 涨跌幅}, ...]}}
    """
    import urllib.request, json
    try:
        url = f"https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pageSize=500&sort=fbt%3Aasc&date={trade_date}"
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://quote.eastmoney.com'
        })
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode('utf-8'))
        pool = data.get('data', {}).get('pool', [])
        if pool:
            return pool
    except Exception as e:
        print(f"[INFO] 东方财富涨停池获取失败: {e}")
    return []


def fetch_limit_list_fallback(pro, trade_date, top_list_data=None):
    """涨跌停降级方案：
    1. 东方财富涨停池（首选替代）
    2. 从 top_list 中筛选涨跌幅 >= 19% 作为替代
    3. 如果都无数据，标记失败
    """
    # 方案1: 东方财富涨停池
    em_pool = fetch_limit_list_eastmoney(trade_date)
    if em_pool:
        # 东方财富格式: {"c": "股票代码", "n": "名称", "p": 价格, "zdp": 涨跌幅}
        limit_up = []
        limit_down = []
        for item in em_pool:
            zdp = item.get('zdp', 0)
            if zdp >= 9.8:
                limit_up.append({
                    "ts_code": item.get('c', ''),
                    "name": item.get('n', ''),
                    "close": item.get('p', 0),
                    "pct_chg": zdp,
                    "source": "eastmoney_zt_pool",
                })
            elif zdp <= -9.8:
                limit_down.append({
                    "ts_code": item.get('c', ''),
                    "name": item.get('n', ''),
                    "close": item.get('p', 0),
                    "pct_chg": zdp,
                    "source": "eastmoney_zt_pool",
                })
        records = {
            "note": "DEGRADED: 来自东方财富涨停池替代数据",
            "limit_up_count": len(limit_up),
            "limit_down_count": len(limit_down),
            "limit_up_sample": limit_up[:30],
            "limit_down_sample": limit_down[:30],
        }
        return records

    # 方案2: 从 top_list 中筛选
    if top_list_data and isinstance(top_list_data, list) and len(top_list_data) > 0:
        limit_up = [r for r in top_list_data if r.get('pct_change', 0) >= 19.0]
        limit_down = [r for r in top_list_data if r.get('pct_change', 0) <= -19.0]
        records = {
            "note": "DEGRADED: 来自龙虎榜top_list替代数据（涨跌幅>=19%），非真实涨跌停",
            "limit_up_count": len(limit_up),
            "limit_down_count": len(limit_down),
            "limit_up_sample": limit_up[:20],
            "limit_down_sample": limit_down[:20],
        }
        return records

    return None


# ---------------------------------------------------------------------------
# 新浪财经 HTTP 接口
# ---------------------------------------------------------------------------


def fetch_sina_hq(code):
    """获取单个新浪行情代码的原始文本"""
    try:
        url = f"https://hq.sinajs.cn/list={code}"
        resp = requests.get(url, headers=SINA_HEADERS, timeout=REQUEST_TIMEOUT)
        resp.encoding = 'gb2312'
        return resp.text
    except Exception as e:
        print(f"[ERROR] 新浪行情 {code} 请求失败: {e}")
        return None


def fetch_us_premarket(data_quality):
    """
    获取美股盘前期货 + 收盘指数

    新浪期货数据格式:
      var hq_str_hf_YM="当前价,,昨收,开盘,最高,最低,时间,...,名称,..."
    涨跌额和涨跌幅字段为空，需从 当前价-昨收 计算。

    新浪美股指数格式:
      var hq_str_int_dji="名称,当前点位,涨跌额,涨跌幅%,..."
    """
    result = {}

    # --- 期货 ---
    futures = {
        "道琼斯期货": "hf_YM",
        "纳斯达克期货": "hf_NQ",
        "标普期货": "hf_ES",
    }

    for name, code in futures.items():
        try:
            text = fetch_sina_hq(code)
            if not text or len(text) < 20:
                raise ValueError(f"返回数据为空或过短: {text}")

            parts = parse_sina_hq(text)
            if not parts or len(parts) < 7:
                raise ValueError(f"字段不足: {len(parts) if parts else 0}")

            # parts[0]=当前价, parts[1]=涨跌额(空), parts[2]=涨跌幅(空),
            # parts[3]=昨收, parts[4]=开盘, parts[5]=最高, parts[6]=最低
            price = safe_float(parts[0])
            pre_close = safe_float(parts[3])
            high = safe_float(parts[5])
            low = safe_float(parts[6])

            change = round(price - pre_close, 4) if pre_close != 0 else 0.0
            change_pct = round(change / pre_close * 100, 2) if pre_close != 0 else 0.0

            result[name] = {
                "price": price,
                "change": change,
                "change_pct": change_pct,
                "pre_close": pre_close,
                "high": high,
                "low": low,
                "raw": text[:300],
            }
            record_quality(data_quality, name, "OK", "sina_http", 1)
        except Exception as e:
            print(f"[WARN] 获取 {name} 期货失败: {e}")
            result[name] = {"error": str(e)}
            record_quality(data_quality, name, "FAILED", "sina_http", 0,
                           f"获取失败: {e}")

    # --- 美股收盘指数 ---
    # 格式: "名称,当前点位,涨跌额,涨跌幅%,时间, ..."
    index_symbols = {
        "道琼斯": "int_dji",
        "纳斯达克": "int_nasdaq",
        "标普500": "int_sp500",
    }

    for name, code in index_symbols.items():
        try:
            text = fetch_sina_hq(code)
            if not text or len(text) < 20:
                raise ValueError(f"返回数据为空或过短: {text}")

            parts = parse_sina_hq(text)
            if not parts or len(parts) < 4:
                raise ValueError(f"字段不足: {len(parts) if parts else 0}")

            # parts[0]=名称, parts[1]=当前点位, parts[2]=涨跌额, parts[3]=涨跌幅%
            idx_name = parts[0].strip()
            price = safe_float(parts[1])
            change = safe_float(parts[2])
            change_pct = safe_float(parts[3])

            result[f"{name}_收盘"] = {
                "name": idx_name,
                "price": price,
                "change": change,
                "change_pct": change_pct,
                "raw": text[:300],
            }
            record_quality(data_quality, f"{name}_收盘", "OK", "sina_http", 1)
        except Exception as e:
            print(f"[WARN] 获取 {name} 收盘指数失败: {e}")
            result[f"{name}_收盘"] = {"error": str(e)}
            record_quality(data_quality, f"{name}_收盘", "FAILED", "sina_http",
                           0, f"获取失败: {e}")

    return result


def fetch_hk_index(data_quality):
    """
    获取港股指数（通过新浪财经）

    恒生指数: int_hangseng
    恒生科技: hktech  (注意: hk_HSTECH 无效)
    """
    result = {}

    symbols = {
        "恒生指数": "int_hangseng",
        "恒生科技": "hktech",
    }

    # 恒生科技降级代码列表（已验证：rt_hkHSTECH 有效，hktech 无效）
    tech_fallback_codes = ["rt_hkHSTECH", "hktech"]

    for name, code in symbols.items():
        success = False
        last_error = None

        # 如果是恒生科技，先尝试主代码，再尝试降级代码
        codes_to_try = [code]
        if name == "恒生科技":
            codes_to_try = tech_fallback_codes

        for try_code in codes_to_try:
            try:
                text = fetch_sina_hq(try_code)
                if not text or len(text) < 20:
                    raise ValueError(f"返回数据为空或过短")

                parts = parse_sina_hq(text)
                if not parts or len(parts) < 2:
                    raise ValueError(f"字段不足: {len(parts) if parts else 0}")

                # 恒生指数 int_hangseng 格式: "名称,当前价,涨跌额,涨跌幅%,..."
                # 恒生科技 rt_hkHSTECH 格式: "代码,名称,当前价,昨收,当前价,最低,不明,涨跌额,涨跌幅%,..."
                if try_code == "rt_hkHSTECH":
                    # rt_hkHSTECH 格式: [0]=代码, [1]=名称, [2]=当前价, [7]=涨跌额, [8]=涨跌幅%
                    idx_name = parts[1].strip() if len(parts) > 1 else parts[0].strip()
                    price = safe_float(parts[2])
                    change = safe_float(parts[7]) if len(parts) > 7 else 0.0
                    change_pct = safe_float(parts[8]) if len(parts) > 8 else 0.0
                else:
                    # 标准格式: [0]=名称, [1]=当前价, [2]=涨跌额, [3]=涨跌幅%
                    idx_name = parts[0].strip()
                    price = safe_float(parts[1])
                    change = safe_float(parts[2]) if len(parts) > 2 else 0.0
                    change_pct = safe_float(parts[3]) if len(parts) > 3 else 0.0

                result[name] = {
                    "name": idx_name,
                    "price": price,
                    "change": change,
                    "change_pct": change_pct,
                    "code_used": try_code,
                    "raw": text[:300],
                }

                status = "OK" if try_code == code else "DEGRADED"
                notes = ("" if try_code == code
                         else f"主代码 {code} 无效，使用降级代码 {try_code}")
                record_quality(data_quality, name, status, "sina_http", 1, notes)
                success = True
                break
            except Exception as e:
                last_error = e
                print(f"[WARN] 获取 {name} (代码={try_code}) 失败: {e}")
                continue

        if not success:
            result[name] = {"error": str(last_error)}
            record_quality(data_quality, name, "FAILED", "sina_http", 0,
                           f"所有代码均失败: {last_error}")

    return result


def fetch_fx_commodity(data_quality):
    """
    获取外汇和大宗商品（通过新浪财经）

    美元指数: DINIW (格式: 时间,当前价,涨跌额,涨跌幅,不明,最高,最低,昨收,当前价,名称,日期)
    在岸人民币: fx_susdcny (格式: 时间,当前价,买入价,卖出价,幅度,...,名称,涨跌额,涨跌幅,...)
    黄金期货: hf_GC (格式: 当前价,,昨收,开盘,最高,最低,时间,...,名称,...)
    原油期货: hf_CL (同上)
    """
    result = {}

    # --- 外汇 ---
    # 在岸人民币: fx_susdcny
    # 格式: "时间,当前价,买入价,卖出价,幅度,...,名称,涨跌额,涨跌幅,..."
    try:
        text = fetch_sina_hq("fx_susdcny")
        if text and len(text) > 20:
            parts = parse_sina_hq(text)
            if parts and len(parts) >= 2:
                name = parts[0].strip() if parts[0] else "在岸人民币"
                price = safe_float(parts[1])
                # 涨跌额在索引11, 涨跌幅在索引12
                change = safe_float(parts[11]) if len(parts) > 11 else 0.0
                change_pct = safe_float(parts[12]) if len(parts) > 12 else 0.0
                result["在岸人民币"] = {
                    "name": name,
                    "price": price,
                    "change": change,
                    "change_pct": change_pct,
                    "raw": text[:300],
                }
                record_quality(data_quality, "在岸人民币", "OK", "sina_http", 1)
            else:
                raise ValueError(f"字段不足: {len(parts) if parts else 0}")
        else:
            raise ValueError("返回数据为空或过短")
    except Exception as e:
        print(f"[WARN] 获取 在岸人民币 失败: {e}")
        result["在岸人民币"] = {"error": str(e)}
        record_quality(data_quality, "在岸人民币", "FAILED", "sina_http", 0,
                       f"获取失败: {e}")

    # 美元指数: DINIW
    # 格式: "时间,当前价,当前价重复,昨收,不明,最高,最低,不明,当前价重复,名称,日期"
    # 已验证: 2026-06-23 实际返回 "01:59:41,101.0018,101.0018,100.7529,2994,100.8731,101.0576,100.7582,101.0018,美元指数,2026-06-23"
    try:
        text = fetch_sina_hq("DINIW")
        if text and len(text) > 20:
            parts = parse_sina_hq(text)
            if parts and len(parts) >= 9:
                price = safe_float(parts[1])
                pre_close = safe_float(parts[3])
                high = safe_float(parts[5])
                low = safe_float(parts[6])
                # 涨跌额和涨跌幅需要计算（DINIW不直接提供）
                change = round(price - pre_close, 4) if price and pre_close else None
                change_pct = round(change / pre_close * 100, 4) if change and pre_close else None
                name = parts[9].strip() if len(parts) > 9 else "美元指数"
                result["美元指数"] = {
                    "name": name,
                    "price": price,
                    "change": change,
                    "change_pct": change_pct,
                    "pre_close": pre_close,
                    "high": high,
                    "low": low,
                    "raw": text[:300],
                }
                record_quality(data_quality, "美元指数", "OK", "sina_http", 1)
            else:
                raise ValueError(f"字段不足: {len(parts) if parts else 0}")
        else:
            raise ValueError("返回数据为空或过短")
    except Exception as e:
        print(f"[WARN] 获取 美元指数 失败: {e}")
        result["美元指数"] = {"error": str(e)}
        record_quality(data_quality, "美元指数", "FAILED", "sina_http", 0,
                       f"获取失败: {e}")

    # --- 大宗商品期货 ---
    # 黄金 hf_GC: "当前价,,昨收,开盘,最高,最低,时间,...,名称,..."
    # 原油 hf_CL: 同上
    commodities = {
        "黄金": "hf_GC",
        "原油": "hf_CL",
    }
    for name, code in commodities.items():
        try:
            text = fetch_sina_hq(code)
            if not text or len(text) < 20:
                raise ValueError("返回数据为空或过短")

            parts = parse_sina_hq(text)
            if not parts or len(parts) < 4:
                raise ValueError(f"字段不足: {len(parts) if parts else 0}")

            price = safe_float(parts[0])
            pre_close = safe_float(parts[2])
            high = safe_float(parts[4]) if len(parts) > 4 else None
            low = safe_float(parts[5]) if len(parts) > 5 else None
            change = round(price - pre_close, 2) if price and pre_close else None
            change_pct = round(change / pre_close * 100, 2) if change and pre_close else None

            result[name] = {
                "name": name,
                "price": price,
                "change": change,
                "change_pct": change_pct,
                "pre_close": pre_close,
                "high": high,
                "low": low,
                "raw": text[:300],
            }
            record_quality(data_quality, name, "OK", "sina_http", 1)
        except Exception as e:
            print(f"[WARN] 获取 {name} 失败: {e}")
            result[name] = {"error": str(e)}
            record_quality(data_quality, name, "FAILED", "sina_http", 0,
                           f"获取失败: {e}")

    return result


# ---------------------------------------------------------------------------
# 新闻采集
# ---------------------------------------------------------------------------


def _extract_titles_from_html(html_text, source_type="generic"):
    """从 HTML 文本中提取新闻标题

    source_type:
      - "cnstock": 上海证券报，从 class="list" 的 li 中提取
      - "stcn": 证券时报，从 class="main-news" 中提取
      - "rmrb": 人民日报，从 class="text" 的 a 标签中提取
      - "xwlbtv": 新闻联播，从 class="title" 中提取
      - "generic": 通用提取
    """
    titles = []

    if source_type == "cnstock":
        # 上海证券报: 首页新闻标题在 class="list" 的 li > a 中
        matches = re.findall(
            r'<li[^>]*>.*?<a[^>]*href="[^"]*"[^>]*>(.*?)</a>.*?</li>',
            html_text, re.IGNORECASE | re.DOTALL
        )
        for m in matches:
            clean = re.sub(r'<[^>]+>', '', m).strip()
            if clean and len(clean) > 8 and len(clean) < 100 and clean not in titles:
                titles.append(clean)

    elif source_type == "stcn":
        # 证券时报: 标题在 href 包含 article 的 <a> 标签中
        matches = re.findall(
            r'<a[^>]*href="[^"]*article[^"]*"[^>]*>([^<]{10,80})</a>',
            html_text, re.IGNORECASE
        )
        for m in matches:
            clean = m.strip()
            # 过滤导航栏文字
            if clean in ['快讯', '数据', '专题', '视频', '评论', '公司', '市场', '基金', '保险', '银行', '地产', '汽车', '科技', '消费']:
                continue
            if clean and len(clean) > 8 and len(clean) < 100 and clean not in titles:
                titles.append(clean)

    elif source_type == "rmrb":
        # 人民日报数字报: 标题在 <a> 标签的文本中（非图片版）
        # URL: http://paper.people.com.cn/rmrb/pc/layout/YYYYMM/DD/node_01.html
        matches = re.findall(
            r'<a[^>]*>([^<]{8,80})</a>',
            html_text, re.IGNORECASE
        )
        skip_words = ['人民日报图文数据库', '第', '版', '返回', '下一版']
        for m in matches:
            clean = m.strip()
            if any(sw in clean for sw in skip_words):
                continue
            if clean and len(clean) > 8 and len(clean) < 100 and clean not in titles:
                titles.append(clean)

    elif source_type == "baidu_news":
        # 百度新闻搜索结果
        matches = re.findall(
            r'<a[^>]*href="[^"]*"[^>]*>([^<]{10,80})</a>',
            html_text, re.IGNORECASE
        )
        for m in matches:
            clean = m.strip()
            # 过滤导航栏和无关文字
            skip_words = ['登录', '注册', '首页', '新闻', '网页', '贴吧', '知道', '音乐', '图片', '视频', '地图', '文库', '更多']
            if any(sw in clean for sw in skip_words):
                continue
            if clean and len(clean) > 10 and len(clean) < 100 and clean not in titles:
                titles.append(clean)

    elif source_type == "xwlb":
        # 新闻联播: 标题在 <a> 标签的 title 属性中
        # URL: https://tv.cctv.cn/lm/xwlb/day/YYYYMMDD.shtml
        matches = re.findall(
            r'<a[^>]*title="([^"]{8,80})"[^>]*>',
            html_text, re.IGNORECASE
        )
        seen = set()
        for m in matches:
            clean = m.strip()
            # 去重（同一标题出现多次）
            if clean in seen:
                continue
            seen.add(clean)
            if clean and len(clean) > 8 and len(clean) < 100 and clean not in titles:
                titles.append(clean)

    else:
        # 通用提取
        title_match = re.search(r'<title[^>]*>(.*?)</title>', html_text,
                                re.IGNORECASE | re.DOTALL)
        if title_match:
            titles.append(title_match.group(1).strip())

        for pattern in [
            r'<h1[^>]*>(.*?)</h1>',
            r'<h2[^>]*>(.*?)</h2>',
            r'<h3[^>]*>(.*?)</h3>',
        ]:
            matches = re.findall(pattern, html_text, re.IGNORECASE | re.DOTALL)
            for m in matches:
                clean = re.sub(r'<[^>]+>', '', m).strip()
                if clean and len(clean) > 4 and clean not in titles:
                    titles.append(clean)

    return titles[:30]  # 最多返回30条


def fetch_single_news(url, source_name, data_quality):
    """采集单个新闻源，返回提取的标题列表"""
    try:
        resp = requests.get(url, headers=WEB_HEADERS, timeout=REQUEST_TIMEOUT)
        status_code = resp.status_code

        if status_code == 403:
            raise PermissionError(f"HTTP 403 Forbidden")

        if status_code != 200:
            raise ValueError(f"HTTP {status_code}")

        titles = _extract_titles_from_html(resp.text)
        if not titles:
            raise ValueError("未能从页面提取到有效标题")

        record_quality(data_quality, source_name, "OK", "web_scrape",
                       len(titles))
        return {
            "status": status_code,
            "titles": titles,
            "title_count": len(titles),
        }
    except PermissionError as e:
        record_quality(data_quality, source_name, "DEGRADED", "web_scrape",
                       0, f"403 Forbidden，将尝试替代源")
        return {"error": str(e), "status": 403, "needs_fallback": True}
    except Exception as e:
        record_quality(data_quality, source_name, "FAILED", "web_scrape",
                       0, f"采集失败: {e}")
        return {"error": str(e), "status": getattr(e, 'status', 0),
                "needs_fallback": False}


def fetch_news_fallback_baidu(keyword, data_quality, source_name):
    """
    降级：用百度新闻搜索关键词作为替代新闻源
    """
    try:
        search_url = f"https://news.baidu.com/ns?word={keyword}&tn=news"
        resp = requests.get(search_url, headers=WEB_HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            raise ValueError(f"百度新闻返回 HTTP {resp.status_code}")

        titles = _extract_titles_from_html(resp.text)
        if not titles:
            raise ValueError("百度新闻也未提取到有效标题")

        record_quality(data_quality, f"{source_name}_百度降级", "DEGRADED",
                       "web_scrape", len(titles),
                       f"原源失败，使用百度新闻搜索 '{keyword}' 替代")
        return {
            "status": resp.status_code,
            "titles": titles,
            "title_count": len(titles),
            "fallback": True,
            "fallback_source": "baidu_news",
        }
    except Exception as e:
        record_quality(data_quality, f"{source_name}_百度降级", "FAILED",
                       "web_scrape", 0, f"百度新闻降级也失败: {e}")
        return {"error": str(e), "fallback": True, "fallback_failed": True}


def fetch_news_headlines(data_quality, trade_date):
    """
    获取官方媒体头条，带降级机制
    数据源：上海证券报、证券时报、人民日报、新闻联播
    新闻联播和人民日报需要日期回滚：当天页面可能还没发布
    """
    result = {}
    from datetime import datetime, timedelta

    # 人民日报和新闻联播需要尝试多个日期（当天可能未发布）
    dt = datetime.strptime(trade_date, '%Y%m%d')

    # 上海证券报、证券时报（首页，不需要日期）
    static_sources = {
        "上海证券报": ("https://www.cnstock.com/", "cnstock"),
        "证券时报": ("https://www.stcn.com/", "stcn"),
    }

    for source_name, (url, source_type) in static_sources.items():
        try:
            resp = requests.get(url, headers=WEB_HEADERS, timeout=REQUEST_TIMEOUT)
            status_code = resp.status_code
            if status_code != 200:
                raise ValueError(f"HTTP {status_code}")
            resp.encoding = 'utf-8'
            titles = _extract_titles_from_html(resp.text, source_type)
            if not titles:
                raise ValueError("未能从页面提取到有效标题")
            record_quality(data_quality, source_name, "OK", "web_scrape", len(titles))
            result[source_name] = {"status": status_code, "titles": titles, "title_count": len(titles)}
        except Exception as e:
            print(f"[WARN] {source_name} 采集失败: {e}")
            record_quality(data_quality, source_name, "FAILED", "web_scrape", 0, f"采集失败: {e}")
            result[source_name] = {"error": str(e), "status": 0}

    # 人民日报（需要日期回滚，最多尝试3天）
    rmrb_success = False
    for i in range(3):
        try_date = (dt - timedelta(days=i)).strftime('%Y%m%d')
        rmrb_date_path = f"{try_date[:4]}{try_date[4:6]}/{try_date[6:8]}"
        url = f"http://paper.people.com.cn/rmrb/pc/layout/{rmrb_date_path}/node_01.html"
        try:
            resp = requests.get(url, headers=WEB_HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                continue
            resp.encoding = 'utf-8'
            titles = _extract_titles_from_html(resp.text, "rmrb")
            if not titles:
                continue
            record_quality(data_quality, "人民日报", "OK", "web_scrape", len(titles))
            result["人民日报"] = {"status": 200, "titles": titles, "title_count": len(titles), "date_used": try_date}
            rmrb_success = True
            break
        except Exception as e:
            continue
    if not rmrb_success:
        print(f"[WARN] 人民日报 采集失败: 3天内均无有效页面")
        record_quality(data_quality, "人民日报", "FAILED", "web_scrape", 0, "3天内均无有效页面")
        result["人民日报"] = {"error": "3天内均无有效页面", "status": 0}

    # 新闻联播（需要日期回滚，最多尝试3天）
    xwlb_success = False
    for i in range(3):
        try_date = (dt - timedelta(days=i)).strftime('%Y%m%d')
        url = f"https://tv.cctv.cn/lm/xwlb/day/{try_date}.shtml"
        try:
            resp = requests.get(url, headers=WEB_HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                continue
            resp.encoding = 'utf-8'
            titles = _extract_titles_from_html(resp.text, "xwlb")
            if not titles:
                continue
            record_quality(data_quality, "新闻联播", "OK", "web_scrape", len(titles))
            result["新闻联播"] = {"status": 200, "titles": titles, "title_count": len(titles), "date_used": try_date}
            xwlb_success = True
            break
        except Exception as e:
            continue
    if not xwlb_success:
        print(f"[WARN] 新闻联播 采集失败: 3天内均无有效页面")
        record_quality(data_quality, "新闻联播", "FAILED", "web_scrape", 0, "3天内均无有效页面")
        result["新闻联播"] = {"error": "3天内均无有效页面", "status": 0}

    return result


# ---------------------------------------------------------------------------
# 数据质量校验
# ---------------------------------------------------------------------------


def fetch_cls_telegraph(data_quality):
    """获取财联社电报（24小时加红）
    使用 /api/cache 接口，无需签名

    v2.0: 采集后自动归档到 data/cls_telegraph_archive/YYYY-MM-DD.json，
    与之前归档中的电报去重合并，实现全天电报覆盖。
    """
    result = {}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://www.cls.cn/',
    }

    try:
        url = "https://www.cls.cn/api/cache?app=CailianpressWeb&name=telegraph&os=web&sv=8.7.9"
        resp = requests.get(url, headers=headers, timeout=15)
        data = resp.json()
        roll_data = data.get('data', {}).get('roll_data', [])

        if roll_data:
            # 提取标题和内容
            items = []
            for item in roll_data:
                title = item.get('title', '').strip()
                content = item.get('content', '').strip()
                ctime = item.get('ctime', 0)
                is_red = item.get('color', '') == 'red' or item.get('level', '') == 'red'
                stock_list = item.get('stock_list', [])

                # 如果标题为空，用内容前50字作为标题
                if not title and content:
                    title = content[:50]

                items.append({
                    'title': title[:100],
                    'content': content[:300],
                    'time': ctime,
                    'is_red': is_red,
                    'stocks': [s.get('name', '') for s in stock_list if isinstance(s, dict)],
                })

            # v2.0: 归档电报（增量去重合并）
            archive_stats = archive_cls_telegraph(items)

            result['items'] = items
            result['count'] = len(items)
            # v2.0: 附加归档信息
            result['archive'] = archive_stats
            record_quality(data_quality, "财联社电报", "OK", "cls_api", len(items))
        else:
            raise ValueError("电报数据为空")
    except Exception as e:
        print(f"[WARN] 财联社电报采集失败: {e}")
        record_quality(data_quality, "财联社电报", "FAILED", "cls_api", 0, str(e))
        result['error'] = str(e)

    return result


def archive_cls_telegraph(new_items):
    """将新采集的电报归档到 data/cls_telegraph_archive/YYYY-MM-DD.json

    v2.0: 增量归档机制
    - 读取当日已有归档（从git仓库中获取，跨任务共享）
    - 按 ctime 时间戳去重合并
    - 写回归档文件，供后续 push 时 git commit + push 到 GitHub

    Args:
        new_items: 本次采集的电报列表，每个元素含 time(ctime) 字段

    Returns:
        dict: 归档统计信息
            - archive_date: 归档日期 YYYY-MM-DD
            - new_count: 本次新增条数
            - archive_total: 当日归档总条数
            - archive_earliest: 当日最早电报时间
            - archive_latest: 当日最新电报时间
            - archive_path: 归档文件路径
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    archive_dir = os.path.join(script_dir, "data", "cls_telegraph_archive")
    os.makedirs(archive_dir, exist_ok=True)

    today_str = datetime.datetime.now().strftime('%Y-%m-%d')
    archive_path = os.path.join(archive_dir, f"{today_str}.json")

    # 读取已有归档
    existing_items = []
    if os.path.exists(archive_path):
        try:
            with open(archive_path, 'r', encoding='utf-8') as f:
                archive_data = json.load(f)
                existing_items = archive_data.get('items', [])
        except (json.JSONDecodeError, OSError) as e:
            print(f"[WARN] 读取电报归档失败，将重新创建: {e}")
            existing_items = []

    # 按 ctime 去重合并
    existing_times = {item.get('time', 0) for item in existing_items}
    truly_new = [item for item in new_items if item.get('time', 0) not in existing_times]

    merged_items = existing_items + truly_new
    # 按时间排序
    merged_items.sort(key=lambda x: x.get('time', 0))

    # 写回归档
    archive_data = {
        'date': today_str,
        'total_count': len(merged_items),
        'items': merged_items,
        'last_updated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }
    try:
        with open(archive_path, 'w', encoding='utf-8') as f:
            json.dump(archive_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] 写入电报归档失败: {e}")

    # 计算统计信息
    all_times = [item.get('time', 0) for item in merged_items if item.get('time', 0)]
    earliest = min(all_times) if all_times else 0
    latest = max(all_times) if all_times else 0

    def ts_to_str(ts):
        if not ts:
            return "N/A"
        try:
            return datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            return str(ts)

    stats = {
        'archive_date': today_str,
        'new_count': len(truly_new),
        'archive_total': len(merged_items),
        'archive_earliest': ts_to_str(earliest),
        'archive_latest': ts_to_str(latest),
        'archive_path': archive_path,
    }

    print(f"[OK] 电报归档: 新增 {len(truly_new)} 条, 当日累计 {len(merged_items)} 条")
    print(f"     时间范围: {stats['archive_earliest']} ~ {stats['archive_latest']}")

    return stats


def _cls_sign(params):
    """财联社API签名算法: sort params by key -> urlencode -> SHA1 -> MD5"""
    sorted_params = dict(sorted(params.items()))
    query_string = urllib.parse.urlencode(sorted_params)
    sha1_hash = hashlib.sha1(query_string.encode('utf-8')).hexdigest()
    sign = hashlib.md5(sha1_hash.encode('utf-8')).hexdigest()
    return sign


# CLS API 默认参数（sv 可能随前端版本更新而变化）
_CLS_DEFAULT_SV = '8.7.9'
_cls_detected_sv = None  # 缓存自动检测到的 sv


def _cls_detect_sv():
    """从财联社首页自动检测当前 sv 版本号

    通过获取 cls.cn 首页 HTML，在 script 标签的 src 属性中
    查找版本号格式的字符串（如 8.7.9, 8.8.0 等）。

    Returns:
        str: 检测到的 sv 版本号，失败返回 None
    """
    global _cls_detected_sv
    if _cls_detected_sv:
        return _cls_detected_sv

    import re
    try:
        resp = requests.get('https://www.cls.cn/', timeout=10, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        })
        html = resp.text

        # 方式1: 在 script src 中查找版本号 (如 /dist/main.8.7.9.js 或 ?v=8.7.9)
        script_srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html)
        for src in script_srcs:
            # 查找 x.y.z 格式的版本号
            ver_match = re.search(r'(\d+\.\d+\.\d+)', src)
            if ver_match:
                _cls_detected_sv = ver_match.group(1)
                print(f"  [CLS] 自动检测到 sv 版本号: {_cls_detected_sv} (来源: {src})")
                return _cls_detected_sv

        # 方式2: 在内联 JS 中查找 sv 赋值
        sv_match = re.search(r'["\']sv["\']\s*[:=]\s*["\'](\d+\.\d+\.\d+)["\']', html)
        if sv_match:
            _cls_detected_sv = sv_match.group(1)
            print(f"  [CLS] 自动检测到 sv 版本号: {_cls_detected_sv} (来源: 内联JS)")
            return _cls_detected_sv

        # 方式3: 在 meta 标签中查找版本号
        meta_match = re.search(r'content=["\'](\d+\.\d+\.\d+)["\']', html)
        if meta_match:
            _cls_detected_sv = meta_match.group(1)
            print(f"  [CLS] 自动检测到 sv 版本号: {_cls_detected_sv} (来源: meta标签)")
            return _cls_detected_sv

        print("  [CLS] 未能从首页自动检测 sv 版本号")
        return None
    except Exception as e:
        print(f"  [CLS] sv 自动检测失败: {e}")
        return None


def _cls_api_get(path, extra_params=None, base='https://www.cls.cn'):
    """调用财联社API（自动签名，支持 sv 自动降级）

    优先使用默认 sv，失败后尝试自动检测 sv 并重试。

    Args:
        path: API路径，如 /v3/depth/home/assembled/1000
        extra_params: 额外参数dict
        base: 基础URL

    Returns:
        dict: API返回的JSON数据，失败返回None
    """
    def _try_request(sv_value):
        params = {'app': 'CailianpressWeb', 'os': 'web', 'sv': sv_value}
        if extra_params:
            params.update(extra_params)
        sign = _cls_sign(params)
        params['sign'] = sign

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.cls.cn/',
            'Accept': 'application/json, text/plain, */*',
        }

        try:
            resp = requests.get(f"{base}{path}", params=params, headers=headers, timeout=15)
            data = resp.json()
            if data.get('error') == 0 or data.get('errno') == 0 or 'data' in data:
                return data.get('data')
            else:
                return None
        except Exception:
            return None

    # 第一次尝试：使用默认 sv
    result = _try_request(_CLS_DEFAULT_SV)
    if result is not None:
        return result

    # 默认 sv 失败，尝试自动检测 sv
    detected_sv = _cls_detect_sv()
    if detected_sv and detected_sv != _CLS_DEFAULT_SV:
        print(f"  [CLS API] 默认sv={_CLS_DEFAULT_SV}失败，尝试检测到的sv={detected_sv}")
        result = _try_request(detected_sv)
        if result is not None:
            print(f"  [CLS API] sv={detected_sv} 成功! 建议更新 _CLS_DEFAULT_SV")
            return result

    # 两种 sv 都失败
    print(f"  [CLS API] {path} 请求失败 (sv={_CLS_DEFAULT_SV})")
    return None


def cls_api_diagnostic():
    """CLS API 诊断函数 — 测试所有端点并输出报告

    当 CLS API 采集失败时，Agent 可调用此函数快速定位问题。
    返回 dict 包含每个端点的状态和错误信息。
    """
    print("=" * 60)
    print("CLS API 诊断报告")
    print("=" * 60)

    # 1. 检测 sv
    print("\n[1] sv 版本号检测:")
    detected = _cls_detect_sv()
    if detected:
        print(f"    检测到: {detected}, 代码默认: {_CLS_DEFAULT_SV}")
        if detected != _CLS_DEFAULT_SV:
            print(f"    ⚠ 版本不一致! 需更新 fetch_data.py 中 _CLS_DEFAULT_SV = '{detected}'")
    else:
        print(f"    自动检测失败, 使用默认值: {_CLS_DEFAULT_SV}")

    # 2. 测试每个端点
    endpoints = [
        ('深度头条', '/v3/depth/home/assembled/1000', None),
        ('VIP文章-首页', '/featured/v1/home/assembled', None),
        ('VIP文章-推荐', '/featured/v2/home/recommend/article',
         {'last_time': str(int(__import__('time').time())), 'refresh_Type': '1'}),
        ('投资日历', '/api/calendar/web/list', {'flag': '0', 'type': '0'}),
        ('首页热门', '/v2/article/hot/list', None),
    ]

    print("\n[2] 端点测试:")
    report = {'sv_default': _CLS_DEFAULT_SV, 'sv_detected': detected, 'endpoints': {}}
    for name, path, params in endpoints:
        print(f"\n  测试 {name}: {path}")
        data = _cls_api_get(path, params)
        if data is not None:
            if isinstance(data, dict):
                keys = list(data.keys())[:5]
                count = sum(len(data.get(k, [])) for k in keys if isinstance(data.get(k), list))
                print(f"    ✓ 成功 - 顶层keys: {keys}, 数据量约: {count}")
                report['endpoints'][name] = {'status': 'OK', 'keys': keys}
            elif isinstance(data, list):
                print(f"    ✓ 成功 - 返回列表, 长度: {len(data)}")
                report['endpoints'][name] = {'status': 'OK', 'count': len(data)}
        else:
            print(f"    ✗ 失败")
            report['endpoints'][name] = {'status': 'FAILED'}

    # 3. 总结
    print("\n[3] 诊断总结:")
    failed = [n for n, r in report['endpoints'].items() if r['status'] == 'FAILED']
    if not failed:
        print("    所有端点正常")
    elif len(failed) == len(endpoints):
        print("    所有端点失败 → 可能是签名算法变更或 sv 版本不匹配")
        print(f"    修复建议: 参照 docs/CLS_API_STRATEGY.md 第八章修复指南")
    else:
        print(f"    部分端点失败: {failed} → 可能是接口路径变更")
        print(f"    修复建议: 用浏览器访问对应页面, 通过 browser_network_requests 捕获新API路径")

    print("=" * 60)
    return report


def fetch_cls_pages_via_api(data_quality):
    """通过财联社API直接采集深度头条、VIP文章、投资日历、首页热门文章
    替代浏览器采集方案，无需JS渲染

    API端点:
      - 深度头条: /v3/depth/home/assembled/1000
      - VIP文章: /featured/v1/home/assembled + /featured/v2/home/recommend/article
      - 投资日历: /api/calendar/web/list
      - 首页热门: /v2/article/hot/list
    """
    result = {}

    # === 1. 深度头条 ===
    print("  [CLS API] 采集深度头条...")
    depth_data = _cls_api_get('/v3/depth/home/assembled/1000')
    if depth_data and isinstance(depth_data, dict):
        depth_list = depth_data.get('depth_list', [])
        top_articles = depth_data.get('top_article', [])
        articles = []
        for art in depth_list:
            articles.append({
                'title': art.get('title', ''),
                'brief': art.get('brief', '')[:300],
                'ctime': art.get('ctime', 0),
                'tag': art.get('article_tag', ''),
                'source': art.get('source', ''),
                'reading_num': art.get('reading_num', 0),
                'image': art.get('image', ''),
            })
        for art in top_articles:
            articles.append({
                'title': art.get('title', ''),
                'brief': art.get('brief', '')[:300],
                'ctime': art.get('ctime', 0),
                'tag': '置顶',
                'source': art.get('source', ''),
                'reading_num': art.get('reading_num', 0),
                'image': art.get('img', ''),
            })
        result['深度头条'] = {
            'articles': articles,
            'article_count': len(articles),
            'source': 'cls_api',
        }
        record_quality(data_quality, "财联社-深度头条", "OK", "cls_api", len(articles))
        print(f"    深度头条: {len(articles)} 篇")
    else:
        result['深度头条'] = {'error': 'API采集失败'}
        record_quality(data_quality, "财联社-深度头条", "FAILED", "cls_api", 0, "API返回空")

    # === 2. VIP文章（分页采集，目标50+篇）===
    print("  [CLS API] 采集VIP文章（分页采集）...")
    vip_data = _cls_api_get('/featured/v1/home/assembled')
    vip_articles = []
    seen_ids = set()  # 用于去重

    def _add_vip_article(art, source_tag=''):
        """添加VIP文章到列表，保留related_stock字段"""
        art_id = art.get('id', art.get('title', ''))
        if art_id and art_id in seen_ids:
            return
        seen_ids.add(art_id)
        vip_articles.append({
            'title': art.get('title', ''),
            'brief': art.get('brief', '')[:300] if art.get('brief') else '',
            'type': art.get('type_name', ''),
            'reading_num': art.get('reading_num', 0),
            'unlock': art.get('unlock', False),
            'label': art.get('label', ''),
            'related_stock': art.get('related_stock', ''),
            'ctime': art.get('ctime', 0),
            'source': source_tag,
        })

    if vip_data and isinstance(vip_data, dict):
        # recommend_list: 推荐文章（含related_stock）
        for art in vip_data.get('recommend_list', []):
            _add_vip_article(art, 'recommend_list')
        # free_top_v2: 免费置顶
        for art in vip_data.get('free_top_v2', []):
            _add_vip_article(art, 'free_top_v2')
        # yellow_article: 黄V文章
        for art in vip_data.get('yellow_article', []):
            _add_vip_article(art, 'yellow_article')

    # VIP推荐文章分页采集（Page 2-5，每页15篇）
    import time as _time
    last_time = str(int(_time.time()))
    for page in range(2, 6):
        recommend_data = _cls_api_get('/featured/v2/home/recommend/article',
                                       {'last_time': last_time, 'refresh_Type': '1'})
        if recommend_data and isinstance(recommend_data, list):
            if len(recommend_data) == 0:
                break
            for art in recommend_data:
                _add_vip_article(art, f'recommend_p{page}')
            # 下一页用本页最旧文章的ctime
            oldest_ctime = min(a.get('ctime', _time.time()) for a in recommend_data)
            last_time = str(int(oldest_ctime))
        else:
            break

    if vip_articles:
        # related_stock 统计
        has_stock_info = sum(1 for a in vip_articles if a.get('related_stock'))

        result['VIP文章'] = {
            'articles': vip_articles[:50],
            'article_count': len(vip_articles),
            'articles_with_stock': has_stock_info,
            'source': 'cls_api_paginated',
        }
        record_quality(data_quality, "财联社-VIP文章", "OK", "cls_api", len(vip_articles))
        print(f"    VIP文章: {len(vip_articles)} 篇 (去重后), 其中 {has_stock_info} 篇含板块信息, 截取前50篇")
    else:
        result['VIP文章'] = {'error': 'API采集失败'}
        record_quality(data_quality, "财联社-VIP文章", "FAILED", "cls_api", 0, "API返回空")

    # === 3. 投资日历 ===
    print("  [CLS API] 采集投资日历...")
    calendar_data = _cls_api_get('/api/calendar/web/list', {'flag': '0', 'type': '0'})
    if calendar_data and isinstance(calendar_data, list):
        events = []
        for day_data in calendar_data:
            calendar_day = day_data.get('calendar_day', '')
            week = day_data.get('week', '')
            day_items = day_data.get('items', [])
            for item in day_items:
                events.append({
                    'date': calendar_day,
                    'week': week,
                    'event': item.get('title', item.get('content', ''))[:200],
                    'type': item.get('type', ''),
                    'stock': item.get('stock', ''),
                })
        result['投资日历'] = {
            'events': events,
            'event_count': len(events),
            'source': 'cls_api',
        }
        record_quality(data_quality, "财联社-投资日历", "OK", "cls_api", len(events))
        print(f"    投资日历: {len(events)} 条事件")
    else:
        result['投资日历'] = {'error': 'API采集失败'}
        record_quality(data_quality, "财联社-投资日历", "FAILED", "cls_api", 0, "API返回空")

    # === 4. 首页热门文章 ===
    print("  [CLS API] 采集首页热门文章...")
    hot_data = _cls_api_get('/v2/article/hot/list')
    if hot_data and isinstance(hot_data, list):
        articles = []
        for art in hot_data:
            articles.append({
                'title': art.get('title', ''),
                'brief': art.get('brief', '')[:200],
                'ctime': art.get('ctime', 0),
                'readNum': art.get('readNum', 0),
                'author': art.get('author', ''),
                'stocks': art.get('stocks', ''),
            })
        result['首页'] = {
            'articles': articles,
            'article_count': len(articles),
            'source': 'cls_api',
        }
        record_quality(data_quality, "财联社-首页", "OK", "cls_api", len(articles))
        print(f"    首页热门: {len(articles)} 篇")
    else:
        result['首页'] = {'error': 'API采集失败'}
        record_quality(data_quality, "财联社-首页", "FAILED", "cls_api", 0, "API返回空")

    return result


def fetch_cls_pages(data_quality):
    """获取财联社深度头条、VIP文章、投资日历、首页

    优先使用API直接采集（无需浏览器），降级到浏览器采集的cls_pages.json
    """
    # 优先尝试API采集
    print("  [CLS] 尝试API直接采集...")
    api_result = fetch_cls_pages_via_api(data_quality)

    # 检查API采集结果，如果有任何一项失败，尝试浏览器降级
    failed_pages = []
    for page_name in ['深度头条', 'VIP文章', '投资日历', '首页']:
        if page_name not in api_result or 'error' in api_result.get(page_name, {}):
            failed_pages.append(page_name)

    if failed_pages:
        print(f"  [CLS] API采集失败的页面: {failed_pages}，尝试浏览器降级...")
        cls_pages_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "data", "cls_pages.json")
        if os.path.exists(cls_pages_file):
            try:
                with open(cls_pages_file, 'r', encoding='utf-8') as f:
                    pages = json.load(f)

                for page_name, content in pages.items():
                    if page_name in failed_pages and content and len(content) > 100:
                        articles = _parse_cls_page_text(content, page_name)
                        api_result[page_name] = {
                            'raw_text': content[:8000],
                            'articles': articles,
                            'article_count': len(articles),
                            'source': 'browser_fallback',
                        }
                        record_quality(data_quality, f"财联社-{page_name}", "DEGRADED",
                                       "browser_fallback", len(articles),
                                       "API失败，降级到浏览器采集")
                        print(f"    {page_name}: 浏览器降级 {len(articles)} 条")
            except Exception as e:
                print(f"  [CLS] 浏览器降级也失败: {e}")

    return api_result


def _parse_cls_page_text(text, page_type):
    """从财联社页面文本中解析文章列表"""
    articles = []
    lines = text.split('\n')

    if page_type == '深度头条':
        # 深度头条格式：标题 + ①②③简介 + 时间前阅X.XW
        current = None
        for line in lines:
            line = line.strip()
            if not line or len(line) < 5:
                continue
            # 跳过导航栏文字
            if any(skip in line for skip in ['关于我们', '网站声明', '联系方式', '用户反馈',
                                               '网站地图', '帮助', '注册', '登录', '首页电报',
                                               '话题盯盘', 'FM投研', '下载', '加载更多',
                                               '热门话题', '已关注', '关注', '热门文章排行榜']):
                continue

            # 检测时间标记（X分钟前/X小时前）
            time_match = any(t in line for t in ['分钟前', '小时前', '天前'])

            if time_match and current:
                current['time'] = line
                articles.append(current)
                current = None
            elif line.startswith('①') or line.startswith('②') or line.startswith('③'):
                if current:
                    current['brief'] = (current.get('brief', '') + ' ' + line).strip()[:300]
            elif not current:
                current = {'title': line[:100], 'brief': ''}
            elif not current.get('brief') and len(line) > 20:
                current['brief'] = line[:300]

        if current:
            articles.append(current)

    elif page_type == 'VIP文章':
        # VIP格式：时间 + 【栏目】标题 + 简介 + 相关股票
        current = None
        for line in lines:
            line = line.strip()
            if not line or len(line) < 5:
                continue
            if any(skip in line for skip in ['关于我们', '网站声明', '注册', '登录',
                                               '首页电报', '话题盯盘', 'FM投研',
                                               '下载', '加载更多', 'VIP试读',
                                               '热门文章排行榜', '最新文章',
                                               '热门话题推荐']):
                continue

            # 检测时间格式 HH:MM + 【栏目】或[栏目]
            import re as _re
            time_match = _re.match(r'^(\d{2}:\d{2})[【\[](.+?)[】\]]', line)
            if time_match:
                if current:
                    articles.append(current)
                current = {
                    'time': time_match.group(1),
                    'title': time_match.group(2)[:100],
                    'brief': '',
                    'stocks': '',
                    'column': '',
                }
            elif current:
                if '相关股票' in line:
                    current['stocks'] = line[:50]
                elif '所属专栏' in line:
                    current['column'] = line.replace('所属专栏：', '')[:30]
                elif '人已读' in line:
                    current['reads'] = line
                elif len(line) > 20 and not current.get('brief'):
                    current['brief'] = line[:300]

        if current:
            articles.append(current)

    elif page_type == '投资日历':
        # 投资日历格式：日期 + 事件描述
        import re as _re
        current_date = None
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # 检测日期格式 YYYY-MM-DD
            date_match = _re.match(r'^(\d{4}-\d{2}-\d{2})$', line)
            if date_match:
                current_date = date_match.group(1)
                continue
            # 检测事件
            if current_date and len(line) > 10 and line not in ['星期一', '星期二', '星期三',
                                                                   '星期四', '星期五', '星期六',
                                                                   '星期天', '星期日', '事件',
                                                                   '共', '条', '筛选', '全部',
                                                                   '今天以后', '上月', '本月']:
                if not any(skip in line for skip in ['板块名称', '涨跌幅', '资金流入',
                                                       '股票名称', '涨跌价', '行业板块',
                                                       '概念板块', '地域板块', '个股涨幅',
                                                       '个股跌幅', '排序']):
                    articles.append({
                        'date': current_date,
                        'event': line[:200],
                    })

    elif page_type == '首页':
        # 首页：提取所有标题
        for line in lines:
            line = line.strip()
            if len(line) > 10 and len(line) < 100:
                if not any(skip in line for skip in ['关于我们', '网站声明', '注册', '登录',
                                                       '首页', '电报', '话题', '盯盘',
                                                       'FM', '投研', '下载', '©']):
                    articles.append({'title': line})

    return articles[:30]  # 最多30条


def run_data_quality_check(data, data_quality):
    """
    采集完成后运行数据质量检查

    检查项:
      1. 核心指数数据是否存在
      2. 数值是否在合理范围（指数>0, 涨跌幅在-20%~20%之间）
    """
    report = {
        "overall_status": "OK",
        "checks": [],
        "warnings": [],
        "errors": [],
    }

    # --- 检查 A 股核心指数 ---
    index_daily = data.get("index_daily", {})
    core_indices = ["上证指数", "深证成指", "创业板指", "沪深300"]
    for idx_name in core_indices:
        records = index_daily.get(idx_name, [])
        if not records:
            report["errors"].append(f"核心指数 {idx_name} 数据缺失")
        else:
            latest = records[-1]
            close = safe_float(latest.get("close", 0))
            pct_chg = safe_float(latest.get("pct_chg", 0))
            if close <= 0:
                report["errors"].append(
                    f"{idx_name} 收盘价异常: {close}")
            if abs(pct_chg) > 20:
                report["warnings"].append(
                    f"{idx_name} 涨跌幅异常: {pct_chg}%")

    # --- 检查美股期货 ---
    us_premarket = data.get("us_premarket", {})
    for future_name in ["道琼斯期货", "纳斯达克期货", "标普期货"]:
        item = us_premarket.get(future_name, {})
        if "error" in item:
            report["warnings"].append(f"{future_name} 数据获取失败")
        else:
            price = safe_float(item.get("price", 0))
            if price <= 0:
                report["warnings"].append(
                    f"{future_name} 价格异常: {price}")

    # --- 检查美股收盘 ---
    for idx_name in ["道琼斯_收盘", "纳斯达克_收盘", "标普500_收盘"]:
        key = f"{idx_name}" if f"{idx_name}" in us_premarket else idx_name.replace("_", "")
        # 兼容 key 名
        item = us_premarket.get(f"{idx_name}", {})
        if not item:
            # 尝试另一种 key
            for k in us_premarket:
                if idx_name.replace("_", "") in k.replace("_", ""):
                    item = us_premarket[k]
                    break
        if not item:
            report["warnings"].append(f"美股收盘 {idx_name} 数据缺失")
        elif "error" not in item:
            price = safe_float(item.get("price", 0))
            if price <= 0:
                report["warnings"].append(
                    f"美股收盘 {idx_name} 价格异常: {price}")

    # --- 检查港股 ---
    hk_index = data.get("hk_index", {})
    for hk_name in ["恒生指数", "恒生科技"]:
        item = hk_index.get(hk_name, {})
        if "error" in item:
            report["warnings"].append(f"港股 {hk_name} 数据获取失败")
        else:
            price = safe_float(item.get("price", 0))
            if price <= 0:
                report["warnings"].append(
                    f"港股 {hk_name} 价格异常: {price}")

    # --- 检查外汇商品 ---
    fx = data.get("fx_commodity", {})
    for fx_name in ["美元指数", "黄金", "原油"]:
        item = fx.get(fx_name, {})
        if "error" in item:
            report["warnings"].append(f"{fx_name} 数据获取失败")
        else:
            price = safe_float(item.get("price", 0))
            if price <= 0:
                report["warnings"].append(
                    f"{fx_name} 价格异常: {price}")

    # --- 检查新闻 ---
    news = data.get("news_headlines", {})
    news_ok_count = sum(
        1 for v in news.values()
        if isinstance(v, dict) and v.get("titles") and len(v.get("titles", [])) > 0
    )
    if news_ok_count == 0:
        report["errors"].append("所有新闻源均未获取到有效标题")

    # --- 汇总状态 ---
    if report["errors"]:
        report["overall_status"] = "ERRORS"
    elif report["warnings"]:
        report["overall_status"] = "WARNINGS"

    report["checks"].append({
        "check": "核心指数完整性",
        "passed": len(report["errors"]) == 0,
    })
    report["checks"].append({
        "check": "数值范围合理性",
        "passed": len([w for w in report["warnings"] if "异常" in w]) == 0,
    })
    report["checks"].append({
        "check": "新闻数据可用性",
        "passed": news_ok_count > 0,
    })

    return report


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def fetch_weekly_data():
    """v2.0: 周报数据采集

    采集两部分数据:
    A. 周末实时数据（财联社电报/页面、美股、港股、外汇商品、官媒头条）
       → 周末政策常在周六周日发布，美股周五夜盘影响周一A股
    B. 本周归档数据（每日报告、电报归档、最新数据摘要、钱三强选股）

    输出: data/raw_data_weekly.json
    """
    print("=== 周报数据采集 [weekly] ===")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(script_dir, "data")
    reports_dir = os.path.join(script_dir, "reports")
    archive_dir = os.path.join(data_dir, "cls_telegraph_archive")

    today = datetime.datetime.now()
    # 计算本周一到今天的日期范围
    week_start = today - datetime.timedelta(days=today.weekday())  # 周一
    week_dates = [(week_start + datetime.timedelta(days=i)).strftime('%Y-%m-%d')
                  for i in range(7)]

    print(f"本周日期范围: {week_dates[0]} ~ {week_dates[-1]}")

    data_quality = {}
    weekly_data = {
        "mode": "weekly",
        "fetch_time": today.strftime('%Y-%m-%d %H:%M:%S'),
        "week_start": week_dates[0],
        "week_end": week_dates[-1],
        # A. 周末实时数据
        "weekend_realtime": {},
        # B. 本周归档数据
        "daily_reports": {},
        "telegraph_archive": {},
        "latest_summary": {},
        "latest_qsq": {},
    }

    # ================================================================
    # A. 周末实时数据采集（市场休市但消息面不休）
    # ================================================================

    # --- A1. 财联社电报（实时采集+归档）---
    print("\n[A1/6] 周末实时财联社电报采集...")
    try:
        cls_telegraph = fetch_cls_telegraph(data_quality)
        weekly_data["weekend_realtime"]["cls_telegraph"] = cls_telegraph
        print(f"  采集到 {cls_telegraph.get('count', 0)} 条电报")
    except Exception as e:
        print(f"  [WARN] 电报采集失败: {e}")
        weekly_data["weekend_realtime"]["cls_telegraph"] = {"error": str(e)}

    # --- A2. 财联社页面（深度/VIP/日历/首页）---
    print("\n[A2/6] 周末实时财联社页面采集...")
    try:
        cls_pages = fetch_cls_pages(data_quality)
        weekly_data["weekend_realtime"]["cls_pages"] = cls_pages
        # VIP信息提取
        try:
            from vip_extractor import extract_vip_info
            vip_data_section = cls_pages.get("VIP文章", {}) if isinstance(cls_pages, dict) else {}
            vip_articles = vip_data_section.get("articles", []) if isinstance(vip_data_section, dict) else []
            if vip_articles:
                # 尝试获取Tushare pro实例
                pro = None
                try:
                    import tushare as ts
                    ts.set_token("8eaad9971749da18299f4932a7cabf068a495fdf06ef3aaafebfe365")
                    pro = ts.pro_api()
                except Exception:
                    pass
                vip_info = extract_vip_info(vip_articles, pro=pro)
                weekly_data["weekend_realtime"]["vip_info"] = vip_info
        except Exception as e:
            print(f"  [WARN] VIP提取失败: {e}")
    except Exception as e:
        print(f"  [WARN] 页面采集失败: {e}")
        weekly_data["weekend_realtime"]["cls_pages"] = {"error": str(e)}

    # --- A3. 美股盘前/收盘（周五夜盘 = 周六凌晨北京时间）---
    print("\n[A3/6] 周末实时美股数据采集...")
    try:
        us_premarket = fetch_us_premarket(data_quality)
        weekly_data["weekend_realtime"]["us_premarket"] = us_premarket
        print(f"  美股数据采集完成")
    except Exception as e:
        print(f"  [WARN] 美股采集失败: {e}")
        weekly_data["weekend_realtime"]["us_premarket"] = {"error": str(e)}

    # --- A4. 港股指数（周末可能有隔夜变动）---
    print("\n[A4/6] 周末实时港股数据采集...")
    try:
        hk_index = fetch_hk_index(data_quality)
        weekly_data["weekend_realtime"]["hk_index"] = hk_index
        print(f"  港股数据采集完成")
    except Exception as e:
        print(f"  [WARN] 港股采集失败: {e}")
        weekly_data["weekend_realtime"]["hk_index"] = {"error": str(e)}

    # --- A5. 外汇/商品（美元/人民币/原油/黄金）---
    print("\n[A5/6] 周末实时外汇商品采集...")
    try:
        fx_commodity = fetch_fx_commodity(data_quality)
        weekly_data["weekend_realtime"]["fx_commodity"] = fx_commodity
        print(f"  外汇商品采集完成")
    except Exception as e:
        print(f"  [WARN] 外汇商品采集失败: {e}")
        weekly_data["weekend_realtime"]["fx_commodity"] = {"error": str(e)}

    # --- A6. 官方媒体头条（上海证券报/证券时报/人民日报/新闻联播）---
    print("\n[A6/6] 周末实时官方媒体头条采集...")
    try:
        # 周末用今天的日期
        today_str = today.strftime('%Y%m%d')
        news_headlines = fetch_news_headlines(data_quality, today_str)
        weekly_data["weekend_realtime"]["news_headlines"] = news_headlines
        print(f"  官媒头条采集完成")
    except Exception as e:
        print(f"  [WARN] 官媒头条采集失败: {e}")
        weekly_data["weekend_realtime"]["news_headlines"] = {"error": str(e)}

    # ================================================================
    # B. 本周归档数据读取
    # ================================================================

    # --- B1. 读取本周每日报告 ---
    print("\n[B1/4] 读取本周每日报告...")
    for date_str in week_dates:
        # 查找该日期的所有报告文件
        for report_type in ["晨报", "午报", "晚报"]:
            report_path = os.path.join(reports_dir, f"{date_str}_{report_type}.md")
            if os.path.exists(report_path):
                try:
                    with open(report_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    if date_str not in weekly_data["daily_reports"]:
                        weekly_data["daily_reports"][date_str] = {}
                    weekly_data["daily_reports"][date_str][report_type] = {
                        "content_length": len(content),
                        "preview": content[:500],  # 前500字预览
                        "path": report_path,
                    }
                    print(f"  找到: {date_str}_{report_type}.md ({len(content)} 字符)")
                except Exception as e:
                    print(f"  [WARN] 读取报告失败 {report_path}: {e}")

    report_count = sum(len(v) for v in weekly_data["daily_reports"].values())
    print(f"  本周报告总数: {report_count} 篇")

    # --- B2. 读取本周电报归档 ---
    print("\n[B2/4] 读取本周电报归档...")
    total_telegraph = 0
    for date_str in week_dates:
        archive_path = os.path.join(archive_dir, f"{date_str}.json")
        if os.path.exists(archive_path):
            try:
                with open(archive_path, 'r', encoding='utf-8') as f:
                    archive = json.load(f)
                items = archive.get('items', [])
                # 统计红色电报
                red_count = sum(1 for it in items if it.get('is_red'))
                # 提取热门股票
                from collections import Counter
                all_stocks = []
                for it in items:
                    stocks = it.get('stocks', [])
                    if isinstance(stocks, list):
                        all_stocks.extend(stocks)
                hot_stocks = Counter(all_stocks).most_common(20)

                weekly_data["telegraph_archive"][date_str] = {
                    "total_count": len(items),
                    "red_count": red_count,
                    "hot_stocks": [{"name": s, "mentions": c} for s, c in hot_stocks],
                    "earliest": items[0].get('time', 0) if items else 0,
                    "latest": items[-1].get('time', 0) if items else 0,
                }
                total_telegraph += len(items)
                print(f"  {date_str}: {len(items)} 条电报, {red_count} 条红色")
            except Exception as e:
                print(f"  [WARN] 读取归档失败 {archive_path}: {e}")

    print(f"  本周电报总数: {total_telegraph} 条")

    # --- B3. 读取最新数据摘要 ---
    print("\n[B3/4] 读取最新数据摘要...")
    summary_path = os.path.join(data_dir, "data_summary.json")
    if os.path.exists(summary_path):
        try:
            with open(summary_path, 'r', encoding='utf-8') as f:
                summary = json.load(f)
            # 只保留关键章节，不保留全部（避免数据过大）
            weekly_data["latest_summary"] = {
                "meta": summary.get("meta", {}),
                "chapter1_index": summary.get("chapter1", {}).get("index_summary", []),
                "chapter0_cls_telegraph_archive": summary.get("chapter0_cls", {}).get("cls_telegraph", {}).get("archive", {}),
            }
            print(f"  最新摘要交易日: {summary.get('meta', {}).get('trade_date', 'N/A')}")
        except Exception as e:
            print(f"  [WARN] 读取摘要失败: {e}")

    # --- B4. 读取最新钱三强选股结果 ---
    print("\n[B4/4] 读取最新钱三强选股结果...")
    qsq_path = os.path.join(data_dir, "qian_sanqiang_results.json")
    if os.path.exists(qsq_path):
        try:
            with open(qsq_path, 'r', encoding='utf-8') as f:
                qsq = json.load(f)
            weekly_data["latest_qsq"] = {
                "trade_date": qsq.get("trade_date", ""),
                "summary": qsq.get("summary", {}),
                "selected_stocks_count": len(qsq.get("selected_stocks", [])),
                "selected_stocks_preview": qsq.get("selected_stocks", [])[:10],
            }
            print(f"  钱三强选股日期: {qsq.get('trade_date', 'N/A')}")
            print(f"  三强合一股票: {len(qsq.get('selected_stocks', []))} 只")
        except Exception as e:
            print(f"  [WARN] 读取钱三强结果失败: {e}")

    # 保存
    os.makedirs(data_dir, exist_ok=True)
    output_path = os.path.join(data_dir, "raw_data_weekly.json")
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(weekly_data, f, ensure_ascii=False, indent=2)

    # 同时保存为latest
    latest_path = os.path.join(data_dir, "raw_data_latest.json")
    with open(latest_path, 'w', encoding='utf-8') as f:
        json.dump(weekly_data, f, ensure_ascii=False, indent=2)

    print(f"\n周报数据已保存: {output_path}")
    print(f"数据大小: {len(json.dumps(weekly_data, ensure_ascii=False))} 字符")

    return output_path


def main():
    if len(sys.argv) < 2:
        print("Usage: python fetch_data.py [morning|noon|evening|weekend|weekly]")
        sys.exit(1)

    mode = sys.argv[1]
    if mode not in ("morning", "noon", "evening", "weekend", "weekly"):
        print(f"无效模式: {mode}，请使用 morning/noon/evening/weekend/weekly")
        sys.exit(1)

    # v2.0: 周报模式 - 不采集实时数据，读取本周归档+报告
    if mode == "weekly":
        weekly_file = fetch_weekly_data()
        print(f"\n=== 周报数据采集完成 ===")
        print(f"输出文件: {weekly_file}")
        return weekly_file

    today = datetime.datetime.now()
    today_str = today.strftime('%Y%m%d')

    print(f"=== 股票数据采集 [{mode}] ===")
    print(f"当前日期: {today_str}")

    # 初始化 Tushare
    pro = get_tushare_pro()
    if pro is None:
        print("[WARN] Tushare初始化失败，仅采集新浪/网页数据...")

    # 获取最近交易日
    trade_date = today_str
    if pro:
        try:
            trade_date = get_trade_date(pro, today_str)
            if trade_date != today_str:
                print(f"今天非交易日，使用最近交易日: {trade_date}")
            else:
                print(f"交易日: {trade_date}")
        except Exception as e:
            print(f"[WARN] 交易日判断失败: {e}")

    # 数据容器
    data = {
        "mode": mode,
        "fetch_time": today.strftime('%Y-%m-%d %H:%M:%S'),
        "trade_date": trade_date,
    }
    data_quality = {}

    # ===================================================================
    # 1. A股核心指数日线
    # ===================================================================
    if pro:
        print("[1/13] 获取A股核心指数日线...")
        index_codes = {
            "上证指数": "000001.SH",
            "深证成指": "399001.SZ",
            "创业板指": "399006.SZ",
            "科创50": "000688.SH",
            "沪深300": "000300.SH",
            "中证500": "000905.SH",
            "上证50": "000016.SH",
        }
        start_date = (
            datetime.datetime.strptime(trade_date, '%Y%m%d')
            - datetime.timedelta(days=10)
        ).strftime('%Y%m%d')

        data["index_daily"] = {}
        for name, code in index_codes.items():
            records = fetch_index_daily(pro, code, start_date, trade_date)
            if records:
                data["index_daily"][name] = records
                record_quality(data_quality, f"A股指数_{name}", "OK",
                               "tushare", len(records))
            else:
                data["index_daily"][name] = []
                record_quality(data_quality, f"A股指数_{name}", "FAILED",
                               "tushare", 0, "Tushare返回空数据")
            time.sleep(0.2)
    else:
        record_quality(data_quality, "A股指数_全部", "FAILED", "tushare",
                       0, "Tushare未初始化")
        data["index_daily"] = {}

    # ===================================================================
    # 2. 美股盘前期货 + 收盘指数
    # ===================================================================
    print("[2/13] 获取美股盘前期货 + 收盘指数...")
    data["us_premarket"] = fetch_us_premarket(data_quality)

    # ===================================================================
    # 3. 港股指数
    # ===================================================================
    print("[3/13] 获取港股指数...")
    data["hk_index"] = fetch_hk_index(data_quality)

    # ===================================================================
    # 4. 外汇商品
    # ===================================================================
    print("[4/13] 获取外汇商品...")
    data["fx_commodity"] = fetch_fx_commodity(data_quality)

    # ===================================================================
    # 5. 官方媒体头条
    # ===================================================================
    print("[5/13] 获取官方媒体头条...")
    data["news_headlines"] = fetch_news_headlines(data_quality, trade_date)

    # ===================================================================
    # 6. 财联社电报（24小时加红）
    # ===================================================================
    print("[6/13] 获取财联社电报...")
    data["cls_telegraph"] = fetch_cls_telegraph(data_quality)

    # ===================================================================
    # 7. 财联社深度/VIP/投资日历/首页（浏览器采集）
    # ===================================================================
    print("[7/13] 获取财联社深度/VIP/投资日历/首页...")
    data["cls_pages"] = fetch_cls_pages(data_quality)

    # ===================================================================
    # 7.5 VIP信息结构化提取（v2.0）
    # ===================================================================
    print("[7.5/13] VIP信息结构化提取...")
    try:
        from vip_extractor import extract_vip_info
        cls_pages = data.get("cls_pages", {})
        vip_data_section = cls_pages.get("VIP文章", {}) if isinstance(cls_pages, dict) else {}
        vip_articles = vip_data_section.get("articles", []) if isinstance(vip_data_section, dict) else []
        if vip_articles:
            vip_info = extract_vip_info(vip_articles, pro=pro)
            data["vip_info"] = vip_info
        else:
            print("  [SKIP] 无VIP文章，跳过VIP信息提取")
    except Exception as e:
        print(f"[WARN] VIP信息提取失败: {e}")

    # ===================================================================
    # 8. 资金流向
    # ===================================================================
    if pro:
        print("[8/13] 获取资金流向...")
        records = fetch_moneyflow(pro, trade_date)
        data["moneyflow"] = records
        record_quality(data_quality, "资金流向",
                       "OK" if records else "FAILED",
                       "tushare", len(records),
                       "" if records else "Tushare返回空数据")
        time.sleep(0.2)
    else:
        data["moneyflow"] = []
        record_quality(data_quality, "资金流向", "FAILED", "tushare", 0,
                       "Tushare未初始化")

    # ===================================================================
    # 7. 龙虎榜
    # ===================================================================
    if pro:
        print("[9/13] 获取龙虎榜...")
        records = fetch_top_list(pro, trade_date)
        data["top_list"] = records
        record_quality(data_quality, "龙虎榜",
                       "OK" if records else "FAILED",
                       "tushare", len(records),
                       "" if records else "Tushare返回空数据")
        time.sleep(0.2)

        print("[9.1/13] 获取龙虎榜机构明细...")
        records = fetch_top_inst(pro, trade_date)
        data["top_inst"] = records
        record_quality(data_quality, "龙虎榜机构明细",
                       "OK" if records else "FAILED",
                       "tushare", len(records),
                       "" if records else "Tushare返回空数据")
        time.sleep(0.2)
    else:
        data["top_list"] = []
        data["top_inst"] = []
        record_quality(data_quality, "龙虎榜", "FAILED", "tushare", 0,
                       "Tushare未初始化")
        record_quality(data_quality, "龙虎榜机构明细", "FAILED", "tushare", 0,
                       "Tushare未初始化")

    # ===================================================================
    # 8. 融资融券
    # ===================================================================
    if pro:
        print("[10/13] 获取融资融券...")
        records = fetch_margin(pro, trade_date)
        data["margin"] = records
        record_quality(data_quality, "融资融券",
                       "OK" if records else "FAILED",
                       "tushare", len(records),
                       "" if records else "Tushare返回空数据")
        time.sleep(0.2)
    else:
        data["margin"] = []
        record_quality(data_quality, "融资融券", "FAILED", "tushare", 0,
                       "Tushare未初始化")

    # ===================================================================
    # 9. 沪深港通
    # ===================================================================
    if pro:
        print("[11/13] 获取沪深港通...")
        records = fetch_hsgt(pro, trade_date)
        data["hsgt"] = records
        record_quality(data_quality, "沪深港通",
                       "OK" if records else "FAILED",
                       "tushare", len(records),
                       "" if records else "Tushare返回空数据")
        time.sleep(0.2)
    else:
        data["hsgt"] = []
        record_quality(data_quality, "沪深港通", "FAILED", "tushare", 0,
                       "Tushare未初始化")

    # ===================================================================
    # 10. 涨跌停（含降级机制）
    # ===================================================================
    if pro:
        print("[12/13] 获取涨跌停...")
        records = fetch_limit_list(pro, trade_date)
        if records:
            data["limit_list"] = records
            record_quality(data_quality, "涨跌停", "OK", "tushare",
                           len(records))
        else:
            # 降级：从 top_list 中筛选涨跌幅>=19%作为替代
            print("[INFO] 涨跌停数据为空，尝试从 top_list 降级...")
            top_list_data = data.get("top_list", [])
            fallback = fetch_limit_list_fallback(pro, trade_date, top_list_data)
            if fallback:
                data["limit_list"] = fallback
                record_quality(data_quality, "涨跌停", "DEGRADED", "tushare",
                               fallback.get("limit_up_count", 0)
                               + fallback.get("limit_down_count", 0),
                               "limit_list_d接口无权限，使用东方财富涨停池/top_list替代")
            else:
                data["limit_list"] = []
                record_quality(data_quality, "涨跌停", "FAILED", "tushare",
                               0, "limit_list_d和东方财富涨停池降级均失败")
        time.sleep(0.2)
    else:
        data["limit_list"] = []
        record_quality(data_quality, "涨跌停", "FAILED", "tushare", 0,
                       "Tushare未初始化")

    # ===================================================================
    # 11. 每日指标
    # ===================================================================
    if pro:
        print("[13/13] 获取每日指标...")
        records = fetch_daily_basic(pro, trade_date)
        data["daily_basic"] = records
        record_quality(data_quality, "每日指标",
                       "OK" if records else "FAILED",
                       "tushare", len(records),
                       "" if records else "Tushare返回空数据")
    else:
        data["daily_basic"] = []
        record_quality(data_quality, "每日指标", "FAILED", "tushare", 0,
                       "Tushare未初始化")

    # ===================================================================
    # 数据质量校验
    # ===================================================================
    print("\n=== 数据质量校验 ===")
    quality_report = run_data_quality_check(data, data_quality)
    data["data_quality"] = data_quality
    data["data_quality_report"] = quality_report

    # 打印质量报告摘要
    print(f"整体状态: {quality_report['overall_status']}")
    for check in quality_report["checks"]:
        status_str = "PASS" if check["passed"] else "FAIL"
        print(f"  [{status_str}] {check['check']}")
    if quality_report["errors"]:
        print("错误:")
        for e in quality_report["errors"]:
            print(f"  - {e}")
    if quality_report["warnings"]:
        print("警告:")
        for w in quality_report["warnings"]:
            print(f"  - {w}")

    # 打印各数据源状态
    print("\n各数据源状态:")
    for src_name, info in data_quality.items():
        print(f"  [{info['status']}] {src_name}: "
              f"{info['record_count']}条记录"
              f"{' - ' + info['notes'] if info['notes'] else ''}")

    # ===================================================================
    # 保存数据
    # ===================================================================
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(data_dir, exist_ok=True)

    filename = os.path.join(data_dir, f"raw_data_{trade_date}_{mode}.json")
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n数据已保存: {filename}")
    print(f"数据大小: {len(json.dumps(data, ensure_ascii=False))} 字符")

    # 同时保存为最新文件
    latest_file = os.path.join(data_dir, "raw_data_latest.json")
    with open(latest_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"最新副本: {latest_file}")

    # ===================================================================
    # 钱三强选股（量化选股，生成共振分析数据）
    # ===================================================================
    print("\n=== 钱三强选股 ===")
    try:
        from qian_sanqiang_selector import QianSanQiangSelector, display_results
        selector = QianSanQiangSelector()
        qsq_df, qsq_date, qsq_mf_date = selector.run()
        qsq_output = display_results(qsq_df, qsq_date, qsq_mf_date)

        qsq_path = os.path.join(data_dir, "qian_sanqiang_results.json")
        with open(qsq_path, 'w', encoding='utf-8') as f:
            json.dump(qsq_output, f, ensure_ascii=False, indent=2, default=str)
        print(f"[OK] 钱三强选股结果已保存: {qsq_path}")
    except Exception as e:
        print(f"[WARN] 钱三强选股失败: {e}")
        import traceback
        traceback.print_exc()

    return filename


if __name__ == "__main__":
    main()
