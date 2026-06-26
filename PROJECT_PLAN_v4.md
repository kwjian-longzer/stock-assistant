# 项目规划书 v4.0 — 数据库驱动的全链路自动化分析与网站系统

> 创建时间: 2026-06-27
> 上一版本: v3.2 (commit 40de9b8)
> 规划依据: 用户后续任务指示（2026-06-26）+ SKILL.md项目宪法 + SESSION_SUMMARY.md现状分析
> 执行方式: 新session按本规划书从零执行

---

## 〇、执行摘要

### 核心诉求

构建一套**数据库驱动**的自动化股票分析系统：定时采集全市场数据→AI推理分析→洞察信号生成→金股发现→网站自动刷新→盘后学习闭环。从"脚本拼凑"升级为"系统工程"。

### v3.2→v4.0 核心变化

| 维度 | v3.2 现状 | v4.0 目标 |
|------|----------|----------|
| 数据流 | 脚本各自调API→JSON文件 | 统一走DB缓存层，全量入库 |
| 数据库 | 12表，9表空置 | 重构为20表，全量填充 |
| 分析引擎 | insight_extractor独立脚本 | insight_engine集成DB，洞见持久化 |
| 报告生成 | 5个独立Schedule任务 | 1个多时点触发任务，盘前/盘中/盘后侧重不同 |
| 网站 | 静态SPA读JSON文件 | 前后端分离，API驱动+自动刷新 |
| 学习闭环 | 无 | 盘后预判验证+经验固化 |
| 数据采集 | CLS每小时 + 手动触发 | 独立3时点定时(8:30/11:50/15:30)+CLS每小时 |

### 质量宪法（继承SKILL.md）

1. **数据唯一来源**: DB优先，API次之，严禁编造
2. **每步可验证**: 每个组件有独立测试入口
3. **推理链完整**: 信号→验证→洞见→策略，不允许断链
4. **概率表述**: 大概率(>70%) / 倾向于(50-70%) / 值得警惕(30-50%) / 尚需验证(<30%)

---

## 一、顶层架构设计

### 1.1 五层架构

```
┌─────────────────────────────────────────────────────────┐
│  Layer 5: 学习闭环 (盘后)                                │
│  learning_loop.py — 预判验证 + 经验固化                   │
├─────────────────────────────────────────────────────────┤
│  Layer 4: 网站前端 (重构)                                │
│  观澜看板 | 踏浪表单 | 数据页 | 历史回溯 | 时间前瞻       │
├─────────────────────────────────────────────────────────┤
│  Layer 3: 网站后端 (新增)                                │
│  api_server.py — 轻量HTTP API，从DB读取数据              │
├─────────────────────────────────────────────────────────┤
│  Layer 2: 分析引擎 (重构)                                │
│  insight_engine.py | report_generator.py | gold_stock_discovery.py │
├─────────────────────────────────────────────────────────┤
│  Layer 1: 数据采集 (重构)                                │
│  data_collector.py (3时点) | cls_collector.py (每小时)   │
├─────────────────────────────────────────────────────────┤
│  Foundation: 数据库 (db.py重构)                          │
│  SQLite 20表 + get_or_fetch缓存 + 时间戳全程追溯         │
└─────────────────────────────────────────────────────────┘
```

### 1.2 数据流总图

```
                    Schedule定时任务
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
   08:30采集    11:50采集    15:30采集
        │           │           │
        ▼           ▼           ▼
   ┌─────────────────────────────────┐
   │         data_collector.py        │
   │  Tushare全市场+钱三强+新浪指数  │
   └───────────┬─────────────────────┘
               │ 写入
               ▼
   ┌─────────────────────────────────┐
   │           SQLite DB (20表)       │  ← cls_collector.py每小时写入
   │  指数/资金/龙虎/涨跌/北向/融资   │  ← data_collector.py 3时点写入
   │  电报/VIP/金股/洞见/报告/钱三强  │  ← insight_engine写入
   └───────────┬─────────────────────┘
               │ 读取
   ┌───────────┼───────────┐
   ▼           ▼           ▼
 盘前分析    盘中分析    盘后分析
   │           │           │
   ▼           ▼           ▼
   ┌─────────────────────────────────┐
   │       report_generator.py        │
   │  读取DB → AI推理 → 生成报告      │
   │  → 洞见写入DB → 金股写入DB       │
   │  → 刷新网站数据 → 推送飞书        │
   └───────────┬─────────────────────┘
               │
   ┌───────────┼───────────┐
   ▼           ▼           ▼
 网站刷新    飞书推送    盘后学习
 (API响应)  (卡片+链接)  (验证+固化)
```

### 1.3 时序图（交易日）

```
时间     事件                          组件
─────────────────────────────────────────────
06:00    CLS电报采集(已运行中)           cls_collector.py --poll
08:30    ★数据采集(盘前)                data_collector.py --period morning
08:35    ★盘前分析+报告                 report_generator.py --period morning
08:40    ★网站刷新+飞书推送             api_server.py + push_feishu.py
         │
11:00    CLS电报采集(自动,每小时)
11:50    ★数据采集(盘中)                data_collector.py --period noon
11:55    ★盘中分析+报告                 report_generator.py --period noon
12:00    ★网站刷新+飞书推送
         │
15:00    CLS电报采集(自动)
15:30    ★数据采集(盘后)                data_collector.py --period evening
15:35    ★盘后分析+报告                 report_generator.py --period evening
15:40    ★网站刷新+飞书推送
16:00    ★盘后学习闭环                  learning_loop.py
         │ 验证盘前预判 vs 实际走势
         │ 差距分析 → 经验固化
         │
每小时   CLS电报采集(持续)              cls_collector.py --poll
```

---

## 二、数据库重构

### 2.1 新表设计（在现有12表基础上新增8表+改造3表）

#### 新增表

```sql
-- 1. 市场洞见（分析引擎输出）
CREATE TABLE market_insight (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,                    -- 2026-06-27
    period TEXT NOT NULL,                  -- morning/noon/evening
    category TEXT,                         -- overseas/commodity/macro/stock/geopolitical
    signal_text TEXT,                      -- 信号描述
    a_share_impact TEXT,                   -- A股影响预判
    confidence TEXT,                       -- high/medium/low
    signal_time TEXT,                      -- 信号来源时间
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

-- 2. 报告记录
CREATE TABLE report (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    period TEXT NOT NULL,                  -- morning/noon/evening/weekend
    title TEXT,
    content TEXT,                          -- 完整报告Markdown
    char_count INTEGER,
    quality_score REAL,                    -- 质量评分
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

-- 3. 钱三强选股结果
CREATE TABLE qian_sanqiang_result (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    stock_code TEXT,
    stock_name TEXT,
    strategy TEXT,                         -- 信号/资金/形态
    score REAL,
    detail_json TEXT,                      -- 完整选股逻辑
    fetch_time TEXT DEFAULT (datetime('now','localtime'))
);

-- 4. 热度追踪
CREATE TABLE heat_tracking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    sector TEXT,
    heat_score REAL,                       -- -100~+100
    capital_flow REAL,                     -- 资金净流入(万元)
    limit_up_count INTEGER,
    lifecycle TEXT,                       -- 崛起/高潮/退烧
    fetch_time TEXT DEFAULT (datetime('now','localtime'))
);

-- 5. 金股扩展字段（在gold_stock表上ALTER或新建关联表）
-- gold_stock已有表，需ALTER ADD:
--   catalyst TEXT,         -- 催化逻辑
--   dragon_vein TEXT,      -- 龙脉(潜龙在渊/见龙在田/飞龙在天)
--   verification TEXT,     -- 验证维度(电报/VIP/龙虎/资金/涨停/钱三强)
--   signal_source TEXT,    -- 信号来源
--   buy_range TEXT,         -- 买入区间
--   target_price TEXT,     -- 止盈
--   stop_loss TEXT,         -- 止损
--   strength TEXT           -- 强推荐/推荐/关注

-- 6. 学习记录
CREATE TABLE learning_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    prediction TEXT,                       -- 盘前预判
    actual TEXT,                           -- 实际走势
    gap_analysis TEXT,                     -- 差距分析
    lesson TEXT,                           -- 经验教训
    category TEXT,                         -- 分类(板块预判/金股验证/情绪判断)
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

-- 7. 网站数据快照（每次分析后刷新）
CREATE TABLE website_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    period TEXT,
    snapshot_json TEXT,                    -- 完整网站数据JSON
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

-- 8. 财经日历事件
CREATE TABLE calendar_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_date TEXT NOT NULL,
    event_time TEXT,
    title TEXT,
    importance TEXT,                      -- high/medium/low
    category TEXT,                        -- 经济数据/央行/财报/政策
    detail TEXT,
    fetch_time TEXT DEFAULT (datetime('now','localtime'))
);
```

#### 改造现有表

```sql
-- cls_telegraph: 已有11字段，无需改造
-- gold_stock: ALTER ADD 8个字段（催化/龙脉/验证维度/信号源/买卖点/力度）
-- raw_cache: 保持不变（API缓存层）
```

### 2.2 数据库完整清单（v4.0 20表）

| # | 表名 | 用途 | 数据写入时机 | 状态 |
|---|------|------|-------------|------|
| 1 | raw_cache | API原始数据缓存 | 任何API调用 | ✅ 已有 |
| 2 | cls_telegraph | 财联社电报 | 每小时 | ✅ 已有(183行) |
| 3 | cls_telegraph_stock | 电报关联股票 | 随电报 | ✅ 已有(84行) |
| 4 | cls_vip_article | VIP文章 | 每小时 | ✅ 已有(83行) |
| 5 | vip_discovered_stock | VIP股票发现 | 随VIP采集 | ✅ 已有(773行) |
| 6 | index_quote | 指数行情 | 3时点+实时 | ⚠️ 空,待填充 |
| 7 | sector_moneyflow | 板块资金 | 3时点 | ⚠️ 空,待填充 |
| 8 | dragon_tiger | 龙虎榜 | 盘后 | ⚠️ 空,待填充 |
| 9 | limit_up | 涨停板 | 盘后 | ⚠️ 空,待填充 |
| 10 | north_money | 北向资金 | 3时点 | ⚠️ 空,待填充 |
| 11 | margin | 融资融券 | 盘后 | ⚠️ 空,待填充 |
| 12 | gold_stock | 金股+回测 | 分析后 | ⚠️ 空,待改造 |
| 13 | **market_insight** | 市场洞见 | 分析后 | 🆕 新增 |
| 14 | **report** | 报告记录 | 分析后 | 🆕 新增 |
| 15 | **qian_sanqiang_result** | 钱三强选股 | 数据采集后 | 🆕 新增 |
| 16 | **heat_tracking** | 热度追踪 | 分析后 | 🆕 新增 |
| 17 | **learning_record** | 学习记录 | 盘后 | 🆕 新增 |
| 18 | **website_snapshot** | 网站快照 | 分析后 | 🆕 新增 |
| 19 | **calendar_event** | 财经日历 | 随CLS采集 | 🆕 新增 |
| 20 | gold_stock_v2 | 金股扩展(或ALTER) | 分析后 | 🔄 改造 |

---

## 三、组件设计

### 3.1 Layer 1: 数据采集

#### 3.1.1 data_collector.py（新增，核心）

**职责**: 独立3时点采集全市场数据，全部写入DB

```python
"""
data_collector.py — 全市场数据采集器

用法:
  python data_collector.py --period morning   # 08:30 盘前采集
  python data_collector.py --period noon      # 11:50 盘中采集
  python data_collector.py --period evening   # 15:30 盘后采集
  python data_collector.py --period all       # 全部采集

采集内容（按时点不同）:
  morning(盘前):
    - 隔夜美股收盘(新浪HTTP) → index_quote
    - 港股指数(新浪) → index_quote
    - 外汇商品(新浪) → raw_cache
    - A股指数(Tushare daily, 前日数据) → index_quote
    - 钱三强选股 → qian_sanqiang_result
    - 板块资金流向(Tushare) → sector_moneyflow
    - 北向资金(Tushare) → north_money

  noon(盘中):
    - A股实时指数(新浪HTTP) → index_quote(is_realtime=1)
    - 板块资金流向(Tushare) → sector_moneyflow
    - 北向资金(Tushare) → north_money
    - 钱三强选股(盘中更新) → qian_sanqiang_result

  evening(盘后):
    - A股收盘指数(Tushare daily) → index_quote
    - 龙虎榜(Tushare) → dragon_tiger
    - 涨跌停(Tushare) → limit_up
    - 融资融券(Tushare) → margin
    - 板块资金流向(Tushare, 最终) → sector_moneyflow
    - 北向资金(Tushare, 最终) → north_money
    - 钱三强选股(最终) → qian_sanqiang_result
"""
```

**关键设计**:
- 所有API调用通过 `db.get_or_fetch()` 缓存，避免重复请求
- 新浪实时指数直接HTTP请求（Tushare daily午间返回前日收盘）
- 钱三强选股调用 `qian_sanqiang_selector.py` 结果写入DB
- 采集完成后打印统计摘要

#### 3.1.2 cls_collector.py（保留，已有v3.2）

**不变**: 继续每小时 `--poll` 模式，红色电报端点 + 向后翻页

### 3.2 Layer 2: 分析引擎

#### 3.2.1 insight_engine.py（重构insight_extractor.py）

**职责**: 从DB读取全部数据，生成结构化洞见，写入DB

```python
"""
insight_engine.py — 市场洞见引擎

用法:
  python insight_engine.py --date 2026-06-27 --period morning
  python insight_engine.py  # 默认今日盘前

数据流:
  DB读取(电报+指数+资金+龙虎+涨停+VIP) → 信号提取 → 跨市场映射 → 洞见生成 → 写入market_insight表

核心函数:
  generate_insights(date, period) -> dict:
    1. 读取当日全部DB数据
    2. extract_signals(): 5类信号分类
    3. cross_market_mapping(): 海外→A股映射
    4. sentiment_baseline(): 情绪基调
    5. hot_sectors(): 热门板块
    6. missing_check(): 关键信号缺失检查
    7. 写入market_insight表
    8. 返回结构化dict（供report_generator使用）

盘前/盘中/盘后侧重:
  morning: 隔夜海外信号→A股开盘预判（核心）
  noon:    盘中资金验证+实时信号
  evening: 全天复盘+次日预判
"""
```

#### 3.2.2 report_generator.py（新增，合并5个报告任务）

**职责**: 读取DB+洞见→AI推理→生成报告→写入DB→刷新网站→推送飞书

```python
"""
report_generator.py — 报告生成器（统一入口）

用法:
  python report_generator.py --period morning   # 盘前报告
  python report_generator.py --period noon      # 盘中报告
  python report_generator.py --period evening   # 盘后报告
  python report_generator.py --period weekend   # 周末报告

执行流程:
  1. 调用 insight_engine.generate_insights(date, period)
  2. 从DB读取全部数据 → 生成 data_summary.json（内存中）
  3. 读取 analysis_prompt.md 规则
  4. 调用AI模型生成报告（使用最强模型+最大算力）
  5. validate_report.py 校验12条红线
  6. report_quality_evaluator.py 10维度评分
  7. 报告写入report表
  8. 金股写入gold_stock表
  9. 热度写入heat_tracking表
  10. website_snapshot写入
  11. 飞书推送（卡片+链接）

盘前侧重: 隔夜美股映射+电报红色信号+投资日历+开盘策略
盘中侧重: 盘面验证+实时信号+连板梯队+下午操作
盘后侧重: 全天复盘+信号汇总+龙虎榜+次日金股(5-8只)+学习闭环
"""
```

**关键设计**:
- 合并当前5个Schedule任务为1个多时点触发
- 每个时点自动调用data_collector.py完成数据采集后触发
- 报告完成后自动刷新website_snapshot
- 盘后额外触发learning_loop.py

#### 3.2.3 gold_stock_discovery.py（新增）

**职责**: 信号→验证→概率评估→金股推荐

```python
"""
gold_stock_discovery.py — 金股发现引擎

从多维度信号交叉验证发现金股:
  1. 财联社电报提及 (cls_telegraph_stock)
  2. VIP文章发现 (vip_discovered_stock)
  3. 龙虎榜机构买入 (dragon_tiger)
  4. 主力资金流入 (sector_moneyflow)
  5. 涨停板 (limit_up)
  6. 钱三强选股 (qian_sanqiang_result)

共振定义: ≥2个维度命中
力度: 4+维度=强推荐, 3=推荐, 2=关注

输出写入gold_stock表(含催化/龙脉/验证维度/买卖点/力度)
"""
```

### 3.3 Layer 3: 网站后端

#### 3.3.1 api_server.py（新增）

**职责**: 轻量HTTP API服务器，从DB读取数据响应前端

```python
"""
api_server.py — 网站API服务器

用法:
  python api_server.py --port 8080

API端点:
  GET /api/dashboard          # 观澜看板(核心数据+要事+洞见)
  GET /api/waves              # 踏浪表单(热点/金股/催化/龙脉)
  GET /api/qian-sanqiang      # 钱三强选股结果
  GET /api/heat-tracking      # 热度波浪轮动图
  GET /api/vip-stocks         # VIP股票列表
  GET /api/calendar            # 财经日历
  GET /api/archive/:date      # 历史数据(按日期)
  GET /api/latest-report      # 最新报告
  GET /api/insights/:period   # 指定时点洞见

技术: Python标准库http.server（无外部依赖）
数据: 全部从SQLite读取，不依赖JSON文件
"""
```

### 3.4 Layer 4: 网站前端

#### 3.4.1 页面重构

```
docs/
├── index.html              # SPA入口（重构）
├── assets/
│   ├── app.js              # 路由+API调用（重构）
│   ├── charts.js           # ECharts图表（保留+扩展）
│   └── styles.css          # 观澜深色主题（保留）
└── pages/
    ├── dashboard.html      # 观澜看板（新增）
    ├── waves.html           # 踏浪表单（新增）
    ├── data.html            # 数据页（重构）
    ├── history.html         # 历史回溯（新增）
    └── calendar.html        # 时间前瞻（新增）
```

#### 3.4.2 观澜看板设计

```
┌─────────────────────────────────────────────────┐
│  观澜看板                          [日期选择器]  │
├──────────────┬──────────────┬──────────────────┤
│  核心数据      │  要事提醒    │  核心洞见         │
│              │              │                  │
│ 上证 +0.52%  │ 🔴 15:32    │ [盘前]            │
│ 深证 +1.23%  │ 特朗普:美军  │ 隔夜费半-5%→A股   │
│ 创业 +2.15%  │ 击落伊朗3架  │ 半导体承压(高置信) │
│ 北向 +85亿   │ 无人机       │                  │
│ 成交 1.2万亿 │              │ [盘中]            │
│              │ 15:15       │ 主力资金流入半导体 │
│ 美股 昨收    │ 布伦特跌至72 │ +12亿(验证盘前)   │
│ 标普 -0.31%  │              │                  │
│ 纳指 -0.20%  │ ...          │ [盘后]            │
│              │              │ 全天复盘+次日预判  │
└──────────────┴──────────────┴──────────────────┘
```

#### 3.4.3 踏浪表单设计

```
┌─────────────────────────────────────────────────────────┐
│  踏浪表单 — 热点·金股·催化强度·龙脉分析                    │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  热点追踪                                               │
│  ┌──────────┬────────┬────────┬────────┬────────┐      │
│  │ 板块      │ 生命周期│ 热度   │ 资金   │ 涨停数  │      │
│  ├──────────┼────────┼────────┼────────┼────────┤      │
│  │ 半导体    │ 高潮    │ +90    │ +12亿  │ 8只    │      │
│  │ AI算力   │ 高潮    │ +83    │ +8.5亿 │ 5只    │      │
│  │ 消费电子  │ 退烧    │ -43    │ -3.2亿 │ 1只    │      │
│  └──────────┴────────┴────────┴────────┴────────┘      │
│                                                         │
│  金股推荐                                               │
│  ┌──────┬──────┬────────┬──────────┬──────┬──────┐     │
│  │ 代码  │ 名称  │ 龙脉    │ 验证维度  │ 力度  │ 时间  │     │
│  ├──────┼──────┼────────┼──────────┼──────┼──────┤     │
│  │ 688456│ 有研  │ 见龙在田│ VIP+龙虎  │ 推荐  │ 中线  │     │
│  │       │ 粉材  │        │ +钱三强   │       │      │     │
│  └──────┴──────┴────────┴──────────┴──────┴──────┘     │
│                                                         │
│  热度波浪轮动图（多板块10日对比，文本可视化）              │
│  日期   06-16 06-17 06-18 06-19 06-22 06-23 06-24 06-25 │
│  半导体  +100  +98   +78   -39   -51   +96   +90   ---  │
│         █    █    ▇    ▁         █    █          │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 3.5 Layer 5: 学习闭环

#### 3.5.1 learning_loop.py（新增）

```python
"""
learning_loop.py — 盘后学习闭环

执行时机: 盘后报告完成后（约16:00）

流程:
  1. 读取今日盘前预判(market_insight where period=morning)
  2. 读取今日实际走势(index_quote where is_realtime=1)
  3. 对比分析: 预判方向 vs 实际方向
  4. 差距分析: 哪些信号被忽略?哪些预判错误?
  5. 经验固化:
     - 信号可靠性更新(某类信号的历史准确率)
     - 板块映射置信度调整
     - 写入learning_record表
  6. 金股回测:
     - 历史推荐金股的收益统计
     - gold_stock_backtest.py已有逻辑
  7. 输出学习报告(写入report表, period=learning)
"""
```

---

## 四、任务分解

### 4.1 Phase 1: 数据库重构与填充（P0, 2天）

| 任务编号 | 任务 | 依赖 | 验收标准 |
|---------|------|------|---------|
| T1.1 | db.py新增8表+ALTER gold_stock | 无 | 20表全部可建，py_compile通过 |
| T1.2 | data_collector.py实现morning采集 | T1.1 | index_quote/north_money/sector_moneyflow有数据 |
| T1.3 | data_collector.py实现noon采集 | T1.2 | 实时指数写入(is_realtime=1) |
| T1.4 | data_collector.py实现evening采集 | T1.3 | dragon_tiger/limit_up/margin有数据 |
| T1.5 | 钱三强选股结果写入DB | T1.1 | qian_sanqiang_result有数据 |
| T1.6 | 财经日历写入DB | T1.1 | calendar_event有数据 |

### 4.2 Phase 2: 分析引擎重构（P0, 2天）

| 任务编号 | 任务 | 依赖 | 验收标准 |
|---------|------|------|---------|
| T2.1 | insight_engine.py重构(从DB读全部数据) | T1.1 | py_compile通过，可输出insights dict |
| T2.2 | 洞见写入market_insight表 | T2.1 | DB有洞见记录 |
| T2.3 | gold_stock_discovery.py实现 | T1.4, T2.1 | gold_stock表有数据，含催化/龙脉/验证维度 |
| T2.4 | report_generator.py实现(合并5任务) | T2.1, T2.3 | 可生成3时点报告，写入report表 |
| T2.5 | analysis_prompt.md更新v4规则 | T2.4 | 盘前/盘中/盘后侧重规则明确 |

### 4.3 Phase 3: 网站重构（P1, 3天）

| 任务编号 | 任务 | 依赖 | 验收标准 |
|---------|------|------|---------|
| T3.1 | api_server.py实现8个API端点 | T1.1 | curl可获取JSON数据 |
| T3.2 | 观澜看板页面实现 | T3.1 | 显示核心数据+要事+洞见3栏 |
| T3.3 | 踏浪表单页面实现 | T3.1 | 热点/金股/热度曲线表格 |
| T3.4 | 数据页重构(钱三强/热度/VIP) | T3.1 | 3个子页面可用 |
| T3.5 | 历史回溯页面(日历选日期) | T3.1 | 可查看历史任意日数据 |
| T3.6 | 时间前瞻(财经日历) | T3.1 | 显示未来事件 |
| T3.7 | 网站自动刷新机制 | T3.2, T3.3 | 数据更新后前端30秒内刷新 |

### 4.4 Phase 4: 自动化部署（P1, 1天）

| 任务编号 | 任务 | 依赖 | 验收标准 |
|---------|------|------|---------|
| T4.1 | data_collector.py定时任务配置 | T1.4 | 8:30/11:50/15:30自动执行 |
| T4.2 | report_generator.py定时任务配置 | T2.4, T4.1 | 数据采集后自动触发分析 |
| T4.3 | learning_loop.py定时任务配置 | T2.4 | 盘后自动执行学习 |
| T4.4 | CLS采集任务更新(--poll间隔调整) | 无 | 每15分钟轮询 |

### 4.5 Phase 5: 学习闭环（P2, 1天）

| 任务编号 | 任务 | 依赖 | 验收标准 |
|---------|------|------|---------|
| T5.1 | learning_loop.py实现 | T2.2 | 可对比预判vs实际 |
| T5.2 | 信号可靠性统计 | T5.1 | learning_record有记录 |
| T5.3 | 金股回测集成 | T5.1 | gold_stock有回测数据 |

### 4.6 Phase 6: 质量保证（P2, 1天）

| 任务编号 | 任务 | 依赖 | 验收标准 |
|---------|------|------|---------|
| T6.1 | 各组件单元测试 | T1-T5 | 每个组件可独立运行 |
| T6.2 | 端到端集成测试 | T6.1 | data_collect→analyze→report→website全链路通 |
| T6.3 | SKILL.md更新v4 | T6.2 | 宪法反映v4架构 |

---

## 五、接口定义

### 5.1 db.py 新增方法

```python
class DB:
    # --- 已有方法(保留) ---
    def get_or_fetch(self, source, api_name, trade_date, params_hash, fetch_fn, ttl_hours=12)
    def upsert_telegraph(self, item) -> bool
    def upsert_telegraph_stocks(self, telegraph_id, stocks)
    def query_telegraphs(self, date=None, limit=500, red_only=False) -> list
    def query_telegraph_stats(self) -> dict
    def query_resonance(self, date=None) -> list

    # --- 新增方法 ---
    def upsert_index_quote(self, item: dict) -> bool
    def query_index_quote(self, date=None, realtime_only=False) -> list

    def upsert_sector_moneyflow(self, items: list) -> int
    def query_sector_moneyflow(self, date=None, top_n=20) -> list

    def upsert_dragon_tiger(self, items: list) -> int
    def query_dragon_tiger(self, date=None) -> list

    def upsert_limit_up(self, items: list) -> int
    def query_limit_up(self, date=None) -> list

    def upsert_north_money(self, item: dict) -> bool
    def query_north_money(self, date=None) -> dict

    def upsert_margin(self, items: list) -> int
    def query_margin(self, date=None) -> list

    def upsert_qian_sanqiang(self, items: list) -> int
    def query_qian_sanqiang(self, date=None) -> list

    def upsert_gold_stock(self, item: dict) -> bool
    def query_gold_stock(self, date=None) -> list
    def update_gold_stock_backtest(self, stock_id, backtest_data)

    def upsert_insight(self, item: dict) -> bool
    def query_insights(self, date=None, period=None) -> list

    def upsert_report(self, item: dict) -> int
    def query_latest_report(self, period=None) -> dict

    def upsert_heat_tracking(self, items: list) -> int
    def query_heat_tracking(self, date=None, sector=None) -> list

    def upsert_learning_record(self, item: dict) -> bool
    def query_learning_records(self, limit=30) -> list

    def upsert_website_snapshot(self, snapshot_json: str, date: str, period: str) -> bool
    def query_website_snapshot(self, date=None, period=None) -> dict

    def upsert_calendar_event(self, items: list) -> int
    def query_calendar_events(self, start_date=None, end_date=None) -> list
```

### 5.2 api_server.py API规范

| 端点 | 方法 | 参数 | 返回 | 数据源表 |
|------|------|------|------|---------|
| `/api/dashboard` | GET | date? | 核心数据+要事+洞见 | index_quote, cls_telegraph, market_insight |
| `/api/waves` | GET | date? | 热点+金股+热度曲线 | heat_tracking, gold_stock, sector_moneyflow |
| `/api/qian-sanqiang` | GET | date? | 钱三强选股结果 | qian_sanqiang_result |
| `/api/heat-tracking` | GET | date?, days? | 热度波浪数据 | heat_tracking |
| `/api/vip-stocks` | GET | date? | VIP股票列表 | vip_discovered_stock, cls_vip_article |
| `/api/calendar` | GET | start?, end? | 财经日历事件 | calendar_event |
| `/api/archive/:date` | GET | date | 指定日全部数据 | website_snapshot |
| `/api/latest-report` | GET | period? | 最新报告 | report |
| `/api/insights/:period` | GET | period | 指定时点洞见 | market_insight |

### 5.3 组件间调用接口

```
data_collector.py
  ├── 调用 db.get_or_fetch() (缓存API调用)
  ├── 调用 qian_sanqiang_selector.py (钱三强选股)
  ├── 写入 6张DB表
  └── 完成后触发 report_generator.py

report_generator.py
  ├── 调用 insight_engine.generate_insights(date, period)
  ├── 调用 gold_stock_discovery.discover(date, period)
  ├── 调用 extract_summary.py (从DB生成data_summary)
  ├── 调用 AI模型生成报告
  ├── 调用 validate_report.py (校验)
  ├── 调用 report_quality_evaluator.py (评分)
  ├── 写入 report, gold_stock, heat_tracking, website_snapshot表
  ├── 调用 push_feishu.py (推送)
  └── 盘后触发 learning_loop.py

insight_engine.py
  ├── 读取 DB 全部当日数据
  ├── 生成 5类信号 + 跨市场映射
  ├── 写入 market_insight表
  └── 返回 insights dict

learning_loop.py
  ├── 读取 market_insight (morning) — 盘前预判
  ├── 读取 index_quote (realtime) — 实际走势
  ├── 读取 gold_stock — 金股验证
  ├── 生成差距分析
  └── 写入 learning_record表
```

---

## 六、Schedule任务配置（v4.0）

### 6.1 定时任务清单

| 时间 | 任务 | 命令 | 说明 |
|------|------|------|------|
| `0 * * * *` | CLS电报采集 | `python cls_collector.py --poll` | 每小时,已运行 |
| `30 8 * * 1-5` | 盘前数据采集 | `python data_collector.py --period morning` | 周一至周五 |
| `35 8 * * 1-5` | 盘前分析报告 | `python report_generator.py --period morning` | 采集后5分钟 |
| `50 11 * * 1-5` | 盘中数据采集 | `python data_collector.py --period noon` | |
| `55 11 * * 1-5` | 盘中分析报告 | `python report_generator.py --period noon` | |
| `30 15 * * 1-5` | 盘后数据采集 | `python data_collector.py --period evening` | |
| `35 15 * * 1-5` | 盘后分析报告 | `python report_generator.py --period evening` | |
| `0 16 * * 1-5` | 盘后学习闭环 | `python learning_loop.py` | 报告后30分钟 |
| `0 20 * * 6` | 周末报告 | `python report_generator.py --period weekend` | 周六20:00 |
| `0 20 * * 0` | 周末报告 | `python report_generator.py --period weekend` | 周日20:00 |

### 6.2 GitHub Pages部署

- 仓库改Public → 开启Pages → Source: main /docs
- api_server.py需在服务器常驻运行（或改用静态JSON导出方案）

**静态方案（推荐，无需服务器）**:
- report_generator.py完成后，将website_snapshot导出为JSON文件到docs/data/
- 前端直接读取JSON文件（与当前方案兼容）
- api_server.py仅用于开发调试

---

## 七、代码重构计划

### 7.1 文件变更矩阵

| 文件 | 操作 | 说明 |
|------|------|------|
| `db.py` | 改造 | 新增8表+12个upsert/query方法 |
| `data_collector.py` | **新增** | 从fetch_data.py提取+重构，全部走DB |
| `fetch_data.py` | 废弃 | 功能迁移到data_collector.py |
| `extract_summary.py` | 改造 | 从DB读取而非raw_data JSON |
| `insight_extractor.py` | 重构→`insight_engine.py` | 增强信号+写入DB |
| `report_generator.py` | **新增** | 合并5个报告任务的统一入口 |
| `gold_stock_discovery.py` | **新增** | 多维度金股发现 |
| `learning_loop.py` | **新增** | 盘后学习闭环 |
| `api_server.py` | **新增** | 网站API服务 |
| `cls_collector.py` | 保留 | v3.2已完善 |
| `qian_sanqiang_selector.py` | 改造 | 结果写入DB |
| `heat_tracker.py` | 改造 | 结果写入DB |
| `vip_extractor.py` | 保留 | 已完善 |
| `validate_report.py` | 保留 | 已完善 |
| `report_quality_evaluator.py` | 保留 | 已完善 |
| `push_feishu.py` | 保留 | 已完善 |
| `site_builder.py` | 改造 | 从website_snapshot表导出JSON |
| `gold_stock_backtest.py` | 集成 | 被learning_loop.py调用 |
| `backfill_telegraphs.py` | 废弃 | 红色电报端点已解决 |
| `analysis_prompt.md` | 改造 | 新增v4盘前/盘中/盘后侧重规则 |
| `SKILL.md` | 改造 | 反映v4架构 |
| `docs/*` | 重构 | 5页面SPA+API调用 |

### 7.2 数据采集重构（fetch_data.py → data_collector.py）

**核心变化**: 从"采集→写JSON文件"变为"采集→写DB"

```python
# 旧模式（fetch_data.py）
def fetch_morning_data():
    data = {}
    data['index'] = tushare_api.daily(...)  # 直接调API
    json.dump(data, open('raw_data_morning.json', 'w'))
    # extract_summary.py 再读JSON

# 新模式（data_collector.py）
def collect_morning_data(db):
    # 通过DB缓存层调API（避免重复请求）
    index_data = db.get_or_fetch(
        source='tushare',
        api_name='daily',
        trade_date=today,
        params_hash='...',
        fetch_fn=lambda: tushare_api.daily(...)
    )
    # 直接写入DB表
    db.upsert_index_quote(index_data)
    db.upsert_north_money(north_data)
    db.upsert_sector_moneyflow(sector_data)
```

### 7.3 报告生成重构（5任务→1入口）

```python
# 旧模式：5个独立Schedule任务，各自采集+分析+报告
# 晨报任务: fetch_data → extract_summary → AI写报告 → push
# 午报任务: fetch_data → extract_summary → AI写报告 → push
# ...

# 新模式：1个report_generator.py，3时点触发
def main():
    period = args.period  # morning/noon/evening/weekend

    # 1. 数据采集（data_collector已在前序步骤完成）
    # 2. 洞见生成
    insights = insight_engine.generate_insights(date, period)
    # 3. 金股发现
    gold_stocks = gold_stock_discovery.discover(date, period)
    # 4. 数据摘要
    summary = build_data_summary_from_db(date, period, insights, gold_stocks)
    # 5. AI报告
    report = ai_generate_report(summary, analysis_prompt, period)
    # 6. 质量校验
    validate_report(report)
    quality_score = evaluate_report(report)
    # 7. 写入DB
    db.upsert_report(report_data)
    db.upsert_gold_stock(gold_stocks)
    db.upsert_heat_tracking(heat_data)
    db.upsert_website_snapshot(website_data)
    # 8. 导出JSON（静态网站方案）
    export_website_json(website_data)
    # 9. 飞书推送
    push_feishu(report_url)
    # 10. 盘后触发学习
    if period == 'evening':
        learning_loop.run(date)
```

---

## 八、测试计划

### 8.1 单元测试（每个组件独立）

| 组件 | 测试命令 | 验收标准 |
|------|---------|---------|
| db.py | `python -c "from db import DB; db=DB(); db.init_tables()"` | 20表无错误 |
| data_collector.py | `python data_collector.py --period morning` | 6表有数据 |
| insight_engine.py | `python insight_engine.py --period morning` | market_insight有数据 |
| gold_stock_discovery.py | `python gold_stock_discovery.py` | gold_stock有数据 |
| report_generator.py | `python report_generator.py --period morning` | report表有数据 |
| api_server.py | `curl http://localhost:8080/api/dashboard` | 返回JSON |
| learning_loop.py | `python learning_loop.py` | learning_record有数据 |

### 8.2 端到端集成测试

```bash
# 完整流程测试（手动触发）
python data_collector.py --period morning      # 1. 数据采集
python insight_engine.py --period morning       # 2. 洞见生成
python gold_stock_discovery.py                  # 3. 金股发现
python report_generator.py --period morning     # 4. 报告生成
python learning_loop.py                         # 5. 学习闭环
# 验证: DB全表有数据 + docs/data/*.json已刷新
```

### 8.3 质量校验（继承已有）

- `validate_report.py`: 12条红线校验
- `report_quality_evaluator.py`: 10维度评分（≥7分为合格）

---

## 九、新Session启动指南

### 9.1 环境准备

**必须创建的文件**: `/workspace/stock-assistant/config.json`
```json
{
    "tushare_token": "你的Tushare Pro token",
    "feishu_webhook": "你的飞书Webhook URL",
    "feishu_app_id": "你的飞书App ID",
    "feishu_app_secret": "你的飞书App Secret",
    "fxbaogao_api_key": "你的发现报告API Key",
    "site_url": "https://你的用户名.github.io/stock-assistant"
}
```

### 9.2 新Session需要的文档

| 文档 | 用途 | 是否需要更新 | 说明 |
|------|------|-------------|------|
| **PROJECT_PLAN_v4.md** | 本文件，执行指南 | ❌ 不需要 | 主执行文档 |
| **HANDOVER.md** | 环境配置+凭证 | ✅ 需更新v4 | 保留凭证信息+已知问题 |
| **SESSION_SUMMARY.md** | 历史任务记录 | ❌ 不需要 | 仅作历史参考 |
| **SKILL.md** | 项目宪法 | ✅ 需更新v4 | 反映新架构 |
| **analysis_prompt.md** | 报告规范 | ✅ 需更新v4 | 盘前/盘中/盘后侧重 |

**新Session第一步**: 阅读本规划书 → 创建config.json → 执行T1.1(db.py重构)

### 9.3 新Session启动Prompt建议

```
阅读 /workspace/stock-assistant/PROJECT_PLAN_v4.md 项目规划书。
按照规划书第四章节任务分解执行，从 Phase 1 (T1.1数据库重构) 开始。
每完成一个任务，运行对应的验收测试命令，确认通过后再进入下一个任务。
遇到规划书中未覆盖的设计决策，遵循SKILL.md项目宪法。
```

### 9.4 关键约束（新Session必须遵守）

1. **DB优先原则**: 所有数据通过db.py写入/读取，禁止直接操作JSON文件（网站导出除外）
2. **组件独立性**: 每个组件可独立运行 + 独立测试，不硬依赖其他组件的运行时状态
3. **时间戳全程追溯**: 每条DB记录都有fetch_time/created_at
4. **get_or_fetch缓存**: 所有Tushare API调用通过db.get_or_fetch()，避免重复请求
5. **新浪实时接口**: 午盘指数用新浪HTTP（Tushare daily午间返回前日收盘）
6. **CLS红色电报端点**: 使用 /v1/roll/get_roll_list?category=red 向后翻页（不要用/api/cache）
7. **is_red判断**: level in ('A','B') 为加红（不要检查color字段）
8. **签名算法**: MD5(SHA1(排序后urlencode查询串))
9. **最强模型+最大算力**: 报告生成时使用DeepSeek-V4-Pro等最强模型
10. **推理链完整**: 信号→验证→洞见→策略，不允许断链

### 9.5 现有可复用资产

| 资产 | 文件 | 复用方式 |
|------|------|---------|
| CLS红色电报采集 | cls_collector.py | 直接保留，v3.2已完善 |
| NLP分类逻辑 | cls_collector.py内函数 | 保留，已优化(80+标签/否定语境/百分比提取) |
| 钱三强选股 | qian_sanqiang_selector.py | 改造结果写入DB |
| 热度追踪 | heat_tracker.py | 改造结果写入DB |
| VIP股票发现 | vip_extractor.py | 保留，已完善 |
| 报告校验 | validate_report.py | 保留 |
| 质量评分 | report_quality_evaluator.py | 保留 |
| 飞书推送 | push_feishu.py | 保留 |
| 金股回测 | gold_stock_backtest.py | 集成到learning_loop |
| 观澜深色主题CSS | docs/assets/styles.css | 保留，扩展 |
| ECharts图表 | docs/assets/charts.js | 保留，扩展 |
| DB缓存策略 | db.py get_or_fetch() | 保留，扩展 |

---

## 十、风险与对策

| 风险 | 概率 | 影响 | 对策 |
|------|------|------|------|
| Tushare API频率限制 | 中 | 数据采集失败 | get_or_fetch缓存+合理间隔 |
| CLS API端点变更 | 低 | 电报采集失败 | 保留/api/cache兜底+定期验证 |
| AI模型token超限 | 中 | 报告生成失败 | data_summary.json控制500行+分段生成 |
| GitHub Pages CDN缓存 | 低 | 网站不刷新 | 文件名加版本号?v=时间戳 |
| 数据库并发写入 | 低 | 数据不一致 | SQLite单写入者模式+wal模式 |
| 新浪接口不稳定 | 中 | 实时指数缺失 | 降级到Tushare daily |

---

## 十一、里程碑

| 里程碑 | 完成标志 | 预计时间 |
|--------|---------|---------|
| M1: 数据库就绪 | 20表创建+data_collector可采集 | 第2天 |
| M2: 分析引擎就绪 | insight_engine+report_generator可用 | 第4天 |
| M3: 网站重构完成 | 5页面+API可用 | 第7天 |
| M4: 自动化部署 | 全部Schedule任务配置完成 | 第8天 |
| M5: 学习闭环 | learning_loop可运行 | 第9天 |
| M6: 质量验收 | 端到端测试通过 | 第10天 |

---

## 附录A: 现有数据库状态（v3.2 baseline）

```
cls_telegraph:          183 rows ✅ (v3.2红色电报端点)
cls_telegraph_stock:     84 rows ✅
cls_vip_article:         83 rows ✅
vip_discovered_stock:   773 rows ✅
index_quote:              0 rows ⚠️ (待data_collector填充)
sector_moneyflow:         0 rows ⚠️
dragon_tiger:             0 rows ⚠️
limit_up:                 0 rows ⚠️
north_money:              0 rows ⚠️
margin:                  0 rows ⚠️
gold_stock:               0 rows ⚠️ (待改造+填充)
raw_cache:                2 rows ✅
```

## 附录B: CLS电报API端点速查（v3.2验证）

| 端点 | 用途 | 签名 | 向后翻页 | category过滤 |
|------|------|------|---------|-------------|
| `/v1/roll/get_roll_list?category=red` | **红色电报主力端点** | 需要 | ✅ last_time+refresh_type=1 | ✅ red/all |
| `/api/cache?name=telegraph` | 最新全部电报 | 需要 | ❌ | ❌ |
| `/api/cache?name=telegraphList&lastTime=X` | 前向轮询(取更新) | 需要 | ❌(ctime>X) | ❌ |
| `/nodeapi/telegraphList` | ~~旧端点~~ | — | — | ❌ 404已移除 |

**签名算法**: `MD5(SHA1(排序后urlencode查询串))`
**is_red判断**: `level in ('A','B')` 为加红（不是color字段）
