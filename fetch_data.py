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

            result['items'] = items
            result['count'] = len(items)
            record_quality(data_quality, "财联社电报", "OK", "cls_api", len(items))
        else:
            raise ValueError("电报数据为空")
    except Exception as e:
        print(f"[WARN] 财联社电报采集失败: {e}")
        record_quality(data_quality, "财联社电报", "FAILED", "cls_api", 0, str(e))
        result['error'] = str(e)

    return result


def fetch_cls_pages(data_quality):
    """获取财联社深度头条、VIP文章、投资日历
    这些页面是JS渲染，需要浏览器工具提取
    自动化任务中AI会使用浏览器工具保存内容到 data/cls_pages.json
    此函数读取该文件，如果不存在则标记为FAILED
    """
    result = {}
    cls_pages_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "data", "cls_pages.json")

    if os.path.exists(cls_pages_file):
        try:
            with open(cls_pages_file, 'r', encoding='utf-8') as f:
                pages = json.load(f)

            for page_name, content in pages.items():
                if content and len(content) > 100:
                    # 提取文章列表
                    articles = _parse_cls_page_text(content, page_name)
                    result[page_name] = {
                        'raw_text': content[:8000],  # 保留前8000字符
                        'articles': articles,
                        'article_count': len(articles),
                    }
                    record_quality(data_quality, f"财联社-{page_name}", "OK",
                                   "browser_scrape", len(articles))
                else:
                    result[page_name] = {'error': '内容为空或过短'}
                    record_quality(data_quality, f"财联社-{page_name}", "FAILED",
                                   "browser_scrape", 0, "内容为空")
        except Exception as e:
            print(f"[WARN] 财联社页面数据读取失败: {e}")
            record_quality(data_quality, "财联社页面", "FAILED", "browser_scrape",
                           0, str(e))
            result['error'] = str(e)
    else:
        print("[INFO] 财联社浏览器采集数据不存在（data/cls_pages.json），跳过")
        for page_name in ['深度头条', 'VIP文章', '投资日历', '首页']:
            result[page_name] = {'error': '浏览器采集数据不存在'}
            record_quality(data_quality, f"财联社-{page_name}", "FAILED",
                           "browser_scrape", 0, "cls_pages.json不存在")

    return result


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


def main():
    if len(sys.argv) < 2:
        print("Usage: python fetch_data.py [morning|noon|evening|weekend]")
        sys.exit(1)

    mode = sys.argv[1]
    if mode not in ("morning", "noon", "evening", "weekend"):
        print(f"无效模式: {mode}，请使用 morning/noon/evening/weekend")
        sys.exit(1)

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
