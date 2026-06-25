#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gold_stock_backtest.py - 金股推荐回测系统

对历史推荐的金股进行回测，计算推荐后的收益率表现。

回测逻辑:
  1. 读取 docs/data/history/gold_stocks.json
  2. 对每只金股使用 Tushare API:
     - 获取推荐日收盘价作为基准价
     - 获取推荐日后 1、3、5、10、20 个交易日的收盘价
     - 计算收益率: (price_n - price_0) / price_0 * 100
     - 计算区间最大涨幅和最大回撤
  3. 更新每只金股的 backtest 字段
  4. 写回 gold_stocks.json

用法:
  python gold_stock_backtest.py              # 回测所有历史金股
  python gold_stock_backtest.py --days 20     # 仅回测最近 20 天内推荐的金股
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.resolve()
HISTORY_DIR = PROJECT_ROOT / "docs" / "data" / "history"
GOLD_STOCKS_PATH = HISTORY_DIR / "gold_stocks.json"

# Tushare API Token
TUSHARE_TOKEN = os.environ.get(
    "TUSHARE_TOKEN",
    "8eaad9971749da18299f4932a7cabf068a495fdf06ef3aaafebfe365",
)

# 回测周期（交易日）
BACKTEST_DAYS = [1, 3, 5, 10, 20]

# 查询窗口扩展天数（日历日），确保覆盖 20 个交易日
QUERY_BUFFER_DAYS = 45


# ---------------------------------------------------------------------------
# Tushare 初始化
# ---------------------------------------------------------------------------

def init_tushare():
    """初始化 Tushare API，返回 pro 接口对象"""
    try:
        import tushare as ts
    except ImportError:
        print("[安装] 正在安装 tushare...")
        import subprocess
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "tushare", "-q"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        import tushare as ts

    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api()
    print("[初始化] Tushare API 连接成功")
    return pro


# ---------------------------------------------------------------------------
# 代码格式转换
# ---------------------------------------------------------------------------

def convert_ts_code(code):
    """将 6 位股票代码转换为 Tushare 格式

    转换规则:
      - 6xxxxx -> .SH (上海)
      - 0xxxxx, 3xxxxx -> .SZ (深圳)
      - 8xxxxx, 4xxxxx -> .BJ (北京)

    参数:
        code: 6 位股票代码（如 "002085"）或已带后缀的代码（如 "002085.SZ"）

    返回:
        str: Tushare 格式代码（如 "002085.SZ"）
    """
    # 如果已经带后缀，直接返回
    if "." in code:
        return code

    code = code.strip()
    if len(code) != 6 or not code.isdigit():
        print(f"[警告] 无效的股票代码: {code}")
        return code

    first = code[0]
    if first == "6":
        return f"{code}.SH"
    elif first in ("0", "3"):
        return f"{code}.SZ"
    elif first in ("8", "4"):
        return f"{code}.BJ"
    else:
        print(f"[警告] 未知交易所代码: {code}，默认按深圳处理")
        return f"{code}.SZ"


def date_to_tushare(date_str):
    """将 YYYY-MM-DD 格式日期转换为 Tushare 格式 YYYYMMDD"""
    return date_str.replace("-", "")


def tushare_to_date(ts_date):
    """将 Tushare 格式 YYYYMMDD 转换为 YYYY-MM-DD"""
    if len(ts_date) == 8:
        return f"{ts_date[:4]}-{ts_date[4:6]}-{ts_date[6:8]}"
    return ts_date


# ---------------------------------------------------------------------------
# 价格查询
# ---------------------------------------------------------------------------

def get_stock_price(pro, ts_code, trade_date):
    """获取某只股票在指定日期的收盘价

    参数:
        pro: Tushare pro 接口
        ts_code: Tushare 格式代码（如 "002085.SZ"）
        trade_date: 交易日期，YYYYMMDD 格式

    返回:
        float or None: 收盘价，获取失败返回 None
    """
    try:
        df = pro.daily(ts_code=ts_code, trade_date=trade_date)
        if df is None or len(df) == 0:
            return None
        # 取第一条记录的收盘价
        return float(df.iloc[0]["close"])
    except Exception as e:
        print(f"[错误] 查询 {ts_code} 在 {trade_date} 的价格失败: {e}")
        return None


def get_price_series(pro, ts_code, start_date, end_date):
    """获取某只股票在日期区间内的日线收盘价序列

    参数:
        pro: Tushare pro 接口
        ts_code: Tushare 格式代码
        start_date: 起始日期，YYYYMMDD 格式
        end_date: 结束日期，YYYYMMDD 格式

    返回:
        list[dict]: 按交易日期升序排列的日线数据
            [{"trade_date": "YYYYMMDD", "close": float, "pct_chg": float}, ...]
    """
    try:
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is None or len(df) == 0:
            return []

        # 转换为列表并按交易日期升序排列
        records = []
        for _, row in df.iterrows():
            records.append({
                "trade_date": str(row["trade_date"]),
                "close": float(row["close"]),
                "pct_chg": float(row["pct_chg"]) if "pct_chg" in row else None,
            })

        records.sort(key=lambda x: x["trade_date"])
        return records
    except Exception as e:
        print(f"[错误] 查询 {ts_code} 价格序列失败 ({start_date}~{end_date}): {e}")
        return []


def get_base_price(pro, ts_code, recommend_date):
    """获取推荐日的基准收盘价，若推荐日无数据则取下一个交易日

    参数:
        pro: Tushare pro 接口
        ts_code: Tushare 格式代码
        recommend_date: 推荐日期，YYYY-MM-DD 格式

    返回:
        tuple: (base_price, base_trade_date, price_series)
            - base_price: 基准收盘价
            - base_trade_date: 基准交易日（YYYYMMDD）
            - price_series: 推荐日起的完整价格序列
    """
    start_ts = date_to_tushare(recommend_date)
    # 向后扩展查询窗口以覆盖 20 个交易日
    start_dt = datetime.strptime(recommend_date, "%Y-%m-%d")
    end_dt = start_dt + timedelta(days=QUERY_BUFFER_DAYS)
    end_ts = end_dt.strftime("%Y%m%d")

    price_series = get_price_series(pro, ts_code, start_ts, end_ts)

    if not price_series:
        # 尝试向前查找（可能是停牌后复牌）
        pre_start_dt = start_dt - timedelta(days=10)
        pre_start_ts = pre_start_dt.strftime("%Y%m%d")
        price_series = get_price_series(pro, ts_code, pre_start_ts, end_ts)

    if not price_series:
        return None, None, []

    # 基准价：推荐日当天的收盘价，若无则取序列首日
    base_trade_date = start_ts
    base_price = None

    # 先尝试精确匹配推荐日
    for record in price_series:
        if record["trade_date"] == start_ts:
            base_price = record["close"]
            base_trade_date = record["trade_date"]
            break

    # 若推荐日无数据，取推荐日之后的第一个交易日
    if base_price is None:
        first_record = price_series[0]
        base_price = first_record["close"]
        base_trade_date = first_record["trade_date"]
        print(f"[提示] {ts_code} 推荐日 {recommend_date} 无交易数据，"
              f"使用 {tushare_to_date(base_trade_date)} 作为基准日")

    return base_price, base_trade_date, price_series


# ---------------------------------------------------------------------------
# 收益率计算
# ---------------------------------------------------------------------------

def calculate_returns(price_series, base_price):
    """计算各周期收益率、最大涨幅和最大回撤

    参数:
        price_series: 价格序列（升序），首元素为基准日
        base_price: 基准收盘价

    返回:
        dict: {
            "price_at_recommend": float,
            "current_price": float,
            "return_1d": float or None,
            "return_3d": float or None,
            "return_5d": float or None,
            "return_10d": float or None,
            "return_20d": float or None,
            "max_return": float,
            "max_drawdown": float,
        }
    """
    result = {
        "price_at_recommend": round(base_price, 4),
        "current_price": None,
        "return_1d": None,
        "return_3d": None,
        "return_5d": None,
        "return_10d": None,
        "return_20d": None,
        "max_return": None,
        "max_drawdown": None,
    }

    if not price_series or base_price is None or base_price <= 0:
        return result

    # 提取收盘价序列
    closes = [r["close"] for r in price_series]

    # 当前价（序列最后一日）
    result["current_price"] = round(closes[-1], 4)

    # 各周期收益率
    for n in BACKTEST_DAYS:
        if len(closes) > n:
            ret = (closes[n] - base_price) / base_price * 100
            result[f"return_{n}d"] = round(ret, 2)
        else:
            result[f"return_{n}d"] = None

    # 区间最大涨幅：相对于基准价的最大涨幅
    max_price = max(closes)
    result["max_return"] = round((max_price - base_price) / base_price * 100, 2)

    # 区间最大回撤：从历史最高点的最大跌幅
    peak = base_price
    max_drawdown = 0.0
    for close in closes:
        if close > peak:
            peak = close
        drawdown = (close - peak) / peak * 100
        if drawdown < max_drawdown:
            max_drawdown = drawdown
    result["max_drawdown"] = round(max_drawdown, 2)

    return result


# ---------------------------------------------------------------------------
# 单只金股回测
# ---------------------------------------------------------------------------

def backtest_single_stock(pro, stock):
    """对单只金股执行回测

    参数:
        pro: Tushare pro 接口
        stock: 金股历史记录字典

    返回:
        dict: 更新后的 backtest 字段
    """
    name = stock.get("name", "")
    code = stock.get("code", "")
    first_recommended = stock.get("first_recommended", "")

    if not code or not first_recommended:
        print(f"[跳过] 金股 {name}({code}) 缺少代码或推荐日期")
        return stock.get("backtest", {})

    ts_code = convert_ts_code(code)
    print(f"\n[回测] {name}({ts_code}) 推荐日: {first_recommended}")

    # 获取基准价和价格序列
    base_price, base_trade_date, price_series = get_base_price(
        pro, ts_code, first_recommended
    )

    if base_price is None or not price_series:
        print(f"[失败] {name}({ts_code}) 无法获取价格数据，可能已退市或停牌")
        return stock.get("backtest", {})

    print(f"[数据] 基准价: {base_price} | 基准日: {tushare_to_date(base_trade_date)} | "
          f"价格序列: {len(price_series)} 条")

    # 计算收益率
    backtest = calculate_returns(price_series, base_price)

    print(f"[结果] 当前价: {backtest['current_price']} | "
          f"1日: {backtest['return_1d']}% | 3日: {backtest['return_3d']}% | "
          f"5日: {backtest['return_5d']}%")
    print(f"[结果] 10日: {backtest['return_10d']}% | 20日: {backtest['return_20d']}% | "
          f"最大涨幅: {backtest['max_return']}% | 最大回撤: {backtest['max_drawdown']}%")

    return backtest


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run_backtest(days_filter=None):
    """主流程：读取金股历史，回测所有金股，更新文件

    参数:
        days_filter: 仅回测最近 N 天内推荐的金股，None 表示回测全部
    """
    print("=" * 60)
    print("金股回测系统 启动")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if days_filter:
        print(f"筛选: 仅回测最近 {days_filter} 天内推荐的金股")
    print("=" * 60)

    # 检查金股历史文件
    if not GOLD_STOCKS_PATH.exists():
        print(f"[错误] 金股历史文件不存在: {GOLD_STOCKS_PATH}")
        print("[提示] 请先运行 site_builder.py 生成金股历史数据")
        sys.exit(1)

    # 读取金股历史
    history = load_json(GOLD_STOCKS_PATH)
    stocks = history.get("stocks", [])

    if not stocks:
        print("[提示] 金股历史为空，无需回测")
        return

    print(f"[数据] 共 {len(stocks)} 只金股待回测\n")

    # 初始化 Tushare
    pro = init_tushare()

    # 日期筛选
    cutoff_date = None
    if days_filter:
        cutoff_date = (datetime.now() - timedelta(days=days_filter)).strftime("%Y-%m-%d")

    # 逐只回测
    success_count = 0
    fail_count = 0
    skip_count = 0

    for i, stock in enumerate(stocks, 1):
        name = stock.get("name", "")
        code = stock.get("code", "")
        first_recommended = stock.get("first_recommended", "")

        print(f"\n[{i}/{len(stocks)}] 处理 {name}({code})")

        # 日期筛选
        if cutoff_date and first_recommended:
            if first_recommended < cutoff_date:
                print(f"[跳过] 推荐日 {first_recommended} 早于筛选截止日 {cutoff_date}")
                skip_count += 1
                continue

        # 执行回测
        try:
            backtest = backtest_single_stock(pro, stock)
            if backtest and backtest.get("price_at_recommend") is not None:
                stock["backtest"] = backtest
                success_count += 1
            else:
                fail_count += 1
        except Exception as e:
            print(f"[错误] 回测 {name}({code}) 时发生异常: {e}")
            fail_count += 1

    # 更新文件
    history["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    save_json(GOLD_STOCKS_PATH, history)

    # 完成总结
    print("\n" + "=" * 60)
    print("金股回测 完成")
    print(f"  总数: {len(stocks)}")
    print(f"  成功: {success_count}")
    print(f"  失败: {fail_count}")
    print(f"  跳过: {skip_count}")
    print(f"  输出: {GOLD_STOCKS_PATH}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# JSON 读写工具
# ---------------------------------------------------------------------------

def load_json(path):
    """安全加载 JSON 文件"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"[错误] 文件不存在: {path}")
        return {}
    except json.JSONDecodeError as e:
        print(f"[错误] JSON 解析失败: {path} -> {e}")
        return {}


def save_json(path, data):
    """安全保存 JSON 文件"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[写入] {path}")


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="金股推荐回测系统 - 计算历史金股推荐后的收益率表现"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="仅回测最近 N 天内推荐的金股（不指定则回测全部）",
    )
    args = parser.parse_args()

    run_backtest(days_filter=args.days)


if __name__ == "__main__":
    main()
