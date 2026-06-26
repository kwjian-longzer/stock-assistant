#!/usr/bin/env python3
"""
钱三强选股公式 - Python实现
将通达信(TDX)选股公式翻译为Python代码，使用Tushare API获取股票数据

公式结构:
  第一强: 多条件创新高选股 (EMA趋势 + 突破 + 换手率 + 55日均线斜率)
  第二强: 增强版智能换手率指标 (换手率放大 + 涨幅确认)
  第三强: 四级资金监控 (机构资金+游资资金共振, 用moneyflow替代L2数据)
  选股4 = 第一强 AND 第二强 AND 第三强
"""

import tushare as ts
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import json
import sys

# 统一配置管理：从环境变量或 config.json 读取敏感信息
from settings import get_tushare_token

# ============================================================================
# 配置
# ============================================================================
TUSHARE_TOKEN = get_tushare_token()
NEED_DAYS = 60          # 获取60个交易日日线数据(需>=56天用于EMA55计算)
API_DELAY = 0.35        # API调用间隔(秒)，避免频率限制


# ============================================================================
# TDX函数翻译
# ============================================================================
def tdx_cross(a, b):
    """CROSS(A, B): A上穿B = 今日A>B AND 昨日A<=B"""
    return (a > b) & (a.shift(1) <= b.shift(1))

def tdx_every(series, n):
    """EVERY(cond, N): 最近N天(含今日)条件全部为真"""
    return series.rolling(n).apply(lambda x: x.all(), raw=True)

def tdx_exist(series, n):
    """EXIST(cond, N): 最近N天(含今日)至少有一天条件为真"""
    return series.rolling(n).apply(lambda x: x.any(), raw=True)

def tdx_ref(series, n):
    """REF(X, N): N天前的值"""
    return series.shift(n)

def tdx_hhv(series, n):
    """HHV(X, N): N天最高值"""
    return series.rolling(n).max()

def tdx_ma(series, n):
    """MA(X, N): N日简单移动平均"""
    return series.rolling(n).mean()

def tdx_ema(series, n):
    """EMA(X, N): N日指数移动平均"""
    return series.ewm(span=n, adjust=False).mean()


# ============================================================================
# 选股引擎
# ============================================================================
class QianSanQiangSelector:

    def __init__(self):
        ts.set_token(TUSHARE_TOKEN)
        self.pro = ts.pro_api()

    # --- 数据获取 ---

    def get_latest_trade_date(self):
        """获取最新交易日"""
        today = datetime.now().strftime('%Y%m%d')
        df = self.pro.trade_cal(exchange='SSE', end_date=today, is_open=1)
        if df is not None and len(df) > 0:
            dates = sorted(df['cal_date'].tolist())
            return dates[-1]
        return today

    def get_trade_dates(self, end_date, count=60):
        """获取最近count个交易日列表"""
        start = (datetime.strptime(end_date, '%Y%m%d') - timedelta(days=count * 2 + 30)).strftime('%Y%m%d')
        df = self.pro.trade_cal(exchange='SSE', start_date=start, end_date=end_date, is_open=1)
        dates = sorted(df['cal_date'].tolist())
        return dates[-count:]

    def get_stock_list(self):
        """获取在册股票列表，排除ST/退市"""
        df = self.pro.stock_basic(exchange='', list_status='L',
                                  fields='ts_code,symbol,name,industry,list_date')
        df = df[~df['name'].str.contains('ST', na=False)]
        df = df[~df['name'].str.contains('退', na=False)]
        return df

    def fetch_daily_data(self, trade_dates):
        """批量获取所有交易日的日线数据"""
        all_data = []
        total = len(trade_dates)
        for i, date in enumerate(trade_dates):
            try:
                df = self.pro.daily(trade_date=date)
                if df is not None and len(df) > 0:
                    all_data.append(df)
            except Exception as e:
                print(f"  [WARN] 获取 {date} 日线失败: {e}")
            if (i + 1) % 10 == 0 or i == total - 1:
                print(f"  日线数据: {i+1}/{total} 天, 累计{sum(len(d) for d in all_data)}条")
                time.sleep(API_DELAY)
        if not all_data:
            return pd.DataFrame()
        return pd.concat(all_data, ignore_index=True)

    def fetch_daily_basic(self, trade_dates):
        """获取每日指标(换手率等)，只需最近3天"""
        all_data = []
        for date in trade_dates[-3:]:
            try:
                df = self.pro.daily_basic(trade_date=date)
                if df is not None and len(df) > 0:
                    all_data.append(df)
            except Exception as e:
                print(f"  [WARN] 获取 {date} daily_basic失败: {e}")
            time.sleep(API_DELAY)
        if not all_data:
            return pd.DataFrame()
        return pd.concat(all_data, ignore_index=True)

    def fetch_moneyflow(self, trade_date):
        """获取资金流向数据(替代LEVEL-2 L2_AMO)"""
        # 尝试当日，空则回滚前5天
        for i in range(6):
            try_date = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=i)).strftime('%Y%m%d')
            try:
                df = self.pro.moneyflow(trade_date=try_date)
                if df is not None and len(df) > 0:
                    if i > 0:
                        print(f"  [INFO] moneyflow({trade_date})为空，回滚到 {try_date}")
                    return df
            except Exception as e:
                print(f"  [WARN] moneyflow({try_date})失败: {e}")
                break
        return pd.DataFrame()

    # --- 条件计算 ---

    def evaluate_stock(self, stock_df, turnover_vals, mf_row):
        """
        对单只股票计算钱三强全部条件
        返回: (第一强, 第二强, 第三强, 详情字典)
        """
        if len(stock_df) < 56:
            return False, False, False, {}

        stock_df = stock_df.sort_values('trade_date').reset_index(drop=True)

        # 提取价格序列
        close = stock_df['close'].astype(float)
        open_ = stock_df['open'].astype(float)
        high = stock_df['high'].astype(float)
        low = stock_df['low'].astype(float)
        vol = stock_df['vol'].astype(float)
        pct_chg = stock_df['pct_chg'].astype(float)
        pre_close = stock_df['pre_close'].astype(float)

        # 最新日数据
        c = close.iloc[-1]
        o = open_.iloc[-1]
        h = high.iloc[-1]
        l = low.iloc[-1]
        v = vol.iloc[-1]
        pct = pct_chg.iloc[-1]
        pc = pre_close.iloc[-1]

        # 计算EMA
        ema5 = tdx_ema(close, 5)
        ema8 = tdx_ema(close, 8)
        ema13 = tdx_ema(close, 13)
        ema21 = tdx_ema(close, 21)
        ema55 = tdx_ema(close, 55)

        ma_vol5 = tdx_ma(vol, 5)

        # ============================
        # 第一强: 多条件创新高选股
        # ============================

        # 光头K线: ((H-C)/H<0.03 AND C>O AND (C-L)/(H-L)>0.7) OR (V>MA(V,5)*1.2)
        hl_range = h - l
        tou_part1 = ((h - c) / h < 0.03) and (c > o) and \
                    ((c - l) / hl_range > 0.7 if hl_range > 0 else False)
        tou_part2 = v > ma_vol5.iloc[-1] * 1.2
        tou = tou_part1 or tou_part2

        # 震荡: REF(EVERY(ABS(pct_chg)<6, 3), 1) — 昨天往前3天每日涨跌幅<6%
        abs_pct_lt6 = (pct_chg.abs() < 6)
        every_3 = tdx_every(abs_pct_lt6, 3)
        zhendang = tdx_ref(every_3, 1).iloc[-1] if len(every_3) > 4 else False
        if pd.isna(zhendang):
            zhendang = False

        # 突破: CLOSE=HHV(CLOSE,20) AND CLOSE>OPEN
        hhv20 = tdx_hhv(close, 20)
        tupuo = (close.iloc[-1] >= hhv20.iloc[-1]) and (c > o)

        # 大趋势1: EXIST(CROSS(EMA5,EMA13),21) OR EVERY(EMA5>EMA13 AND EMA13>EMA21,5)
        cross_5_13 = tdx_cross(ema5, ema13)
        exist_cross_5_13 = tdx_exist(cross_5_13, 21)
        cond_trend = (ema5 > ema13) & (ema13 > ema21)
        every_trend_5 = tdx_every(cond_trend, 5)
        daqushi1 = bool(exist_cross_5_13.iloc[-1]) if not pd.isna(exist_cross_5_13.iloc[-1]) else False
        daqushi1 = daqushi1 or (bool(every_trend_5.iloc[-1]) if not pd.isna(every_trend_5.iloc[-1]) else False)

        # 大趋势2: EXIST(CROSS(EMA8,EMA21),55) OR EVERY(EMA5>EMA13 AND EMA13>EMA21,8)
        cross_8_21 = tdx_cross(ema8, ema21)
        exist_cross_8_21 = tdx_exist(cross_8_21, 55)
        every_trend_8 = tdx_every(cond_trend, 8)
        daqushi2 = bool(exist_cross_8_21.iloc[-1]) if not pd.isna(exist_cross_8_21.iloc[-1]) else False
        daqushi2 = daqushi2 or (bool(every_trend_8.iloc[-1]) if not pd.isna(every_trend_8.iloc[-1]) else False)

        daqushi = daqushi1 or daqushi2

        # 换手率(来自daily_basic)
        if turnover_vals is None or len(turnover_vals) < 3:
            return False, False, False, {}
        tr_today = float(turnover_vals[-1])
        tr_yest = float(turnover_vals[-2])
        tr_before = float(turnover_vals[-3])

        # 条件1: 换手率连续两天放大
        tiaojian1 = (tr_today > tr_yest) and (tr_yest > tr_before)

        # 条件2: 涨幅>1.5%
        tiaojian2 = (c > pc) and (pct > 1.5)

        # 强进场信号
        qiangjinchang = tiaojian1 and tiaojian2

        # 强势突破1: 强进场信号 AND CROSS(C,EMA5) AND CROSS(C,EMA13) AND CROSS(C,EMA21) AND C>EMA55 AND V>MA(V,5)*1.68
        cross_c_ema5 = tdx_cross(close, ema5)
        cross_c_ema13 = tdx_cross(close, ema13)
        cross_c_ema21 = tdx_cross(close, ema21)
        qiangshitupo1 = (qiangjinchang and
                         bool(cross_c_ema5.iloc[-1]) and
                         bool(cross_c_ema13.iloc[-1]) and
                         bool(cross_c_ema21.iloc[-1]) and
                         c > ema55.iloc[-1] and
                         v > ma_vol5.iloc[-1] * 1.68)

        # 强势突破2: 突破 AND 强进场信号
        qiangshitupo2 = tupuo and qiangjinchang

        # 强势突破
        qiangshitupo = qiangshitupo1 or qiangshitupo2

        # 均线强穿: CROSS(EMA5,EMA13) AND CROSS(EMA5,EMA21) AND CROSS(EMA5,EMA55)
        cross_5_21 = tdx_cross(ema5, ema21)
        cross_5_55 = tdx_cross(ema5, ema55)
        junxianqiangchuan = (bool(cross_5_13.iloc[-1]) and
                             bool(cross_5_21.iloc[-1]) and
                             bool(cross_5_55.iloc[-1]))

        # 冲锋: 震荡 AND 突破 AND 光头 AND 大趋势
        chongfeng = zhendang and tupuo and tou and daqushi

        # 选股: 冲锋 OR 强势突破 OR 均线强穿
        xuangu = chongfeng or qiangshitupo or junxianqiangchuan

        # 55日均线斜率角度
        man_today = ema55.iloc[-1]
        man_yest = ema55.iloc[-2]
        if man_yest > 0:
            xielv = (man_today / man_yest - 1) * 100
            jiaodu = np.arctan(xielv) * 180 / np.pi
        else:
            jiaodu = 0
        shangsheng = jiaodu > 6

        # 第一强 = 选股 AND 上升趋势
        di_yi_qiang = xuangu and shangsheng

        # ============================
        # 第二强: 增强版智能换手率指标
        # ============================

        # 条件11: 换手率连续两天放大(同条件1)
        tiaojian11 = tiaojian1

        # 条件12: 换手率>前一天*3
        tiaojian12 = tr_today > tr_yest * 3

        # 条件A
        tiaojianA = tiaojian11 or tiaojian12

        # 条件B: (C-O)/O*100 > 1.5
        tiaojianB = ((c - o) / o * 100) > 1.5 if o > 0 else False

        # 强进场信号2
        qiangjinchang2 = tiaojianA and tiaojianB

        # 弱进场信号: 换手率>前一天*1.3 AND C>REF(C,1)
        ruojinchang = (tr_today > tr_yest * 1.3) and (c > pc)

        # 第二强
        di_er_qiang = qiangjinchang2 or ruojinchang

        # ============================
        # 第三强: 四级资金监控(用moneyflow替代L2)
        # ============================

        jigou_zijin = 0.0
        youzi_zijin = 0.0
        di_san_qiang = False

        if mf_row is not None:
            # 超大单
            chaoB = float(mf_row.get('buy_elg_amount', 0) or 0)
            chaoS = float(mf_row.get('sell_elg_amount', 0) or 0)
            chao_jing = chaoB - chaoS

            # 大单
            daB = float(mf_row.get('buy_lg_amount', 0) or 0)
            daS = float(mf_row.get('sell_lg_amount', 0) or 0)
            da_jing = daB - daS

            # 中单
            zhongB = float(mf_row.get('buy_md_amount', 0) or 0)
            zhongS = float(mf_row.get('sell_md_amount', 0) or 0)
            zhong_jing = zhongB - zhongS

            # 小单
            xiaoB = float(mf_row.get('buy_sm_amount', 0) or 0)
            xiaoS = float(mf_row.get('sell_sm_amount', 0) or 0)
            xiao_jing = xiaoB - xiaoS

            # 机构资金净额 = 超大单净额 + 大单净额/2
            jigou_zijin = chao_jing + da_jing / 2

            # 游资资金净额 = 中单净额 + 大单净额/2
            youzi_zijin = zhong_jing + da_jing / 2

            # 共振: 机构资金>0 AND 游资资金>0
            di_san_qiang = (jigou_zijin > 0) and (youzi_zijin > 0)

        details = {
            'tou': tou, 'zhendang': zhendang, 'tupuo': tupuo, 'daqushi': daqushi,
            'chongfeng': chongfeng, 'qiangshitupo': qiangshitupo,
            'junxianqiangchuan': junxianqiangchuan, 'xuangu': xuangu,
            'shangsheng': shangsheng, 'jiaodu': round(jiaodu, 2),
            'qiangjinchang2': qiangjinchang2, 'ruojinchang': ruojinchang,
            'jigou_zijin': round(jigou_zijin, 2),
            'youzi_zijin': round(youzi_zijin, 2),
            'tr_today': round(tr_today, 2),
            'tiaojian1': tiaojian1, 'tiaojian2': tiaojian2,
            'tiaojian12': tr_today > tr_yest * 3 if tr_yest > 0 else False,
        }

        return di_yi_qiang, di_er_qiang, di_san_qiang, details

    # --- 主流程 ---

    def run(self):
        print("=" * 70)
        print("  钱三强选股公式 - Python实现")
        print("  第一强: 多条件创新高 | 第二强: 智能换手率 | 第三强: 资金共振")
        print("=" * 70)

        # 1. 获取最新交易日
        print("\n[1/8] 获取最新交易日...")
        latest_date = self.get_latest_trade_date()
        print(f"  最新交易日: {latest_date}")

        # 2. 获取交易日历
        print(f"\n[2/8] 获取交易日历({NEED_DAYS}天)...")
        trade_dates = self.get_trade_dates(latest_date, NEED_DAYS)
        print(f"  获取 {len(trade_dates)} 个交易日: {trade_dates[0]} ~ {trade_dates[-1]}")

        # 3. 获取股票列表
        print("\n[3/8] 获取股票列表...")
        stock_list = self.get_stock_list()
        print(f"  有效股票: {len(stock_list)} 只(已排除ST/退市)")

        # 4. 获取日线数据
        print(f"\n[4/8] 获取日线数据({len(trade_dates)}天)...")
        daily_data = self.fetch_daily_data(trade_dates)
        print(f"  日线记录: {len(daily_data)} 条, 涉及 {daily_data['ts_code'].nunique()} 只股票")

        # 5. 获取每日指标(换手率)
        print("\n[5/8] 获取每日指标(换手率)...")
        daily_basic = self.fetch_daily_basic(trade_dates)
        print(f"  每日指标记录: {len(daily_basic)} 条")

        # 6. 获取资金流向
        print("\n[6/8] 获取资金流向数据(替代LEVEL-2)...")
        moneyflow = self.fetch_moneyflow(latest_date)
        mf_date = moneyflow['trade_date'].iloc[0] if len(moneyflow) > 0 else 'N/A'
        print(f"  资金流向记录: {len(moneyflow)} 条 (日期: {mf_date})")

        # 7. 计算选股条件
        print("\n[7/8] 计算钱三强选股条件...")
        results = []
        valid_stocks = daily_data['ts_code'].unique()
        total = len(valid_stocks)

        # 预处理: 将daily_basic按ts_code分组，取最近3天的turnover_rate
        basic_dict = {}
        if len(daily_basic) > 0:
            for ts_code, grp in daily_basic.groupby('ts_code'):
                grp = grp.sort_values('trade_date')
                if len(grp) >= 3:
                    basic_dict[ts_code] = grp['turnover_rate'].values

        # 预处理: moneyflow按ts_code索引
        mf_dict = {}
        if len(moneyflow) > 0:
            for _, row in moneyflow.iterrows():
                mf_dict[row['ts_code']] = row

        for idx, ts_code in enumerate(valid_stocks):
            if (idx + 1) % 500 == 0:
                print(f"  计算进度: {idx+1}/{total}")

            stock_df = daily_data[daily_data['ts_code'] == ts_code]
            if len(stock_df) < 56:
                continue

            # 获取股票信息
            info = stock_list[stock_list['ts_code'] == ts_code]
            if len(info) == 0:
                continue
            stock_name = info.iloc[0]['name']
            industry = info.iloc[0].get('industry', '数据暂缺')

            # 换手率
            turnover_vals = basic_dict.get(ts_code)

            # 资金流向
            mf_row = mf_dict.get(ts_code)

            # 计算条件
            d1, d2, d3, details = self.evaluate_stock(stock_df, turnover_vals, mf_row)

            if not details:
                continue

            # 最新日数据
            latest = stock_df.sort_values('trade_date').iloc[-1]
            selected = d1 and d2 and d3

            result = {
                'ts_code': ts_code,
                'name': stock_name,
                'industry': industry,
                'close': float(latest['close']),
                'pct_chg': float(latest['pct_chg']),
                'turnover_rate': details.get('tr_today', 0),
                'di_yi_qiang': d1,
                'di_er_qiang': d2,
                'di_san_qiang': d3,
                'selected': selected,
                'jigou_zijin': details.get('jigou_zijin', 0),
                'youzi_zijin': details.get('youzi_zijin', 0),
                'ema55_angle': details.get('jiaodu', 0),
                'details': details,
            }
            results.append(result)

        # 8. 输出结果
        print(f"\n[8/8] 选股结果")
        print("=" * 70)

        df = pd.DataFrame(results)
        return df, latest_date, mf_date


# ============================================================================
# 结果展示
# ============================================================================
def display_results(df, latest_date, mf_date):
    if len(df) == 0:
        print(f"\n  无有效数据，请检查API连接")
        return {'trade_date': latest_date, 'summary': {}, 'selected_stocks': []}

    total = len(df)
    p1 = df['di_yi_qiang'].sum()
    p2 = df['di_er_qiang'].sum()
    p3 = df['di_san_qiang'].sum()
    p_all = df['selected'].sum()

    print(f"\n  交易日: {latest_date}  |  资金流向日期: {mf_date}")
    print(f"  参与计算股票: {total} 只\n")
    print(f"  ┌─────────────────────────────┐")
    print(f"  │ 第一强(多条件创新高): {p1:>5} 只 │")
    print(f"  │ 第二强(智能换手率):   {p2:>5} 只 │")
    print(f"  │ 第三强(资金共振):     {p3:>5} 只 │")
    print(f"  │ 三强合一(最终选股):   {p_all:>5} 只 │")
    print(f"  └─────────────────────────────┘")

    # 最终选股
    if p_all > 0:
        selected = df[df['selected'] == True].sort_values('pct_chg', ascending=False)
        print(f"\n{'='*70}")
        print(f"  ★ 最终选股结果 ({len(selected)} 只) ★")
        print(f"{'='*70}")
        for i, (_, row) in enumerate(selected.iterrows(), 1):
            print(f"\n  [{i}] {row['ts_code']} {row['name']} [{row['industry']}]")
            print(f"      收盘: {row['close']:.2f}  涨幅: {row['pct_chg']:.2f}%  换手率: {row['turnover_rate']:.2f}%")
            print(f"      机构资金: {row['jigou_zijin']:.2f}万元  游资资金: {row['youzi_zijin']:.2f}万元")
            print(f"      EMA55角度: {row['ema55_angle']:.2f}°")
            d = row['details']
            triggers = []
            if d.get('chongfeng'): triggers.append("冲锋")
            if d.get('qiangshitupo'): triggers.append("强势突破")
            if d.get('junxianqiangchuan'): triggers.append("均线强穿")
            print(f"      触发信号: {', '.join(triggers) if triggers else '无'}")
    else:
        print(f"\n  今日无股票满足三强合一条件")

    # 满足两强的股票
    df['pass_count'] = df['di_yi_qiang'].astype(int) + df['di_er_qiang'].astype(int) + df['di_san_qiang'].astype(int)
    two_of_three = df[df['pass_count'] >= 2].sort_values('pct_chg', ascending=False)

    if len(two_of_three) > 0:
        print(f"\n{'='*70}")
        print(f"  满足两强条件的股票 ({len(two_of_three)} 只, 展示前20只):")
        print(f"{'='*70}")
        for _, row in two_of_three.head(20).iterrows():
            conds = []
            if row['di_yi_qiang']: conds.append("第一强")
            if row['di_er_qiang']: conds.append("第二强")
            if row['di_san_qiang']: conds.append("第三强")
            print(f"  {row['ts_code']} {row['name']:6s} | {', '.join(conds):14s} | "
                  f"涨幅:{row['pct_chg']:>6.2f}% 换手:{row['turnover_rate']:>5.2f}% "
                  f"机构:{row['jigou_zijin']:>10.2f}万 游资:{row['youzi_zijin']:>10.2f}万")

    # 各条件TOP10
    for cond, label in [('di_yi_qiang', '第一强(多条件创新高)'),
                         ('di_er_qiang', '第二强(智能换手率)'),
                         ('di_san_qiang', '第三强(资金共振)')]:
        passed = df[df[cond] == True].sort_values('pct_chg', ascending=False)
        if len(passed) > 0:
            print(f"\n  --- {label} ({len(passed)}只, 展示前10只) ---")
            for _, row in passed.head(10).iterrows():
                print(f"    {row['ts_code']} {row['name']:6s} | 涨幅:{row['pct_chg']:>6.2f}% "
                      f"换手:{row['turnover_rate']:>5.2f}% 行业:{row['industry']}")

    # 保存JSON
    output = {
        'trade_date': latest_date,
        'moneyflow_date': mf_date,
        'summary': {
            'total_stocks': int(total),
            'pass_di_yi_qiang': int(p1),
            'pass_di_er_qiang': int(p2),
            'pass_di_san_qiang': int(p3),
            'pass_all_three': int(p_all),
        },
        'selected_stocks': df[df['selected'] == True][
            ['ts_code', 'name', 'industry', 'close', 'pct_chg', 'turnover_rate',
             'jigou_zijin', 'youzi_zijin', 'ema55_angle']
        ].to_dict('records'),
        'two_of_three_stocks': df[df['pass_count'] >= 2][
            ['ts_code', 'name', 'industry', 'close', 'pct_chg', 'turnover_rate',
             'jigou_zijin', 'youzi_zijin', 'di_yi_qiang', 'di_er_qiang', 'di_san_qiang']
        ].to_dict('records'),
    }
    return output


# ============================================================================
# 主入口
# ============================================================================
if __name__ == '__main__':
    selector = QianSanQiangSelector()
    df, latest_date, mf_date = selector.run()
    output = display_results(df, latest_date, mf_date)

    # 保存结果到项目data目录
    import os
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(data_dir, exist_ok=True)
    output_path = os.path.join(data_dir, 'qian_sanqiang_results.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  结果已保存: {output_path}")
    print(f"\n{'='*70}")
    print("  钱三强选股完成")
    print(f"{'='*70}")
