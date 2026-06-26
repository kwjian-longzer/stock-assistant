# 股票助手技能文件（v4.0 数据库驱动版）

> **本文件是项目的"宪法"，每次自动化任务执行时必须首先读取并严格遵守。**
> v4.0: 项目从 v3.2 升级为**数据库驱动架构**。所有数据流经 SQLite（`data/stock.db`，19 张表），
> 新增三阶段报告编排器（prepare→generate→finalize）、洞见引擎、金股共振发现、REST API 服务与学习闭环。
> 违反本文件中任何"红线"规则的行为都是不可接受的。

---

## 一、项目概述

本项目为 A股/港股/美股市场每日研报自动生成与推送系统。v4.0 采用**数据库驱动 + 信号驱动推理链**架构，
核心方法论：**信号→验证→洞见→策略**。报告不是数据搬运工，而是市场翻译官。

四种报告类型（时点规则）：
- **晨报**（morning，08:30）：侧重隔夜信号→今日预判，海外市场映射、开盘策略、预判型金股（侧重"潜龙在渊"）
- **午报**（noon，11:50）：侧重上午盘面验证早盘预判，盘中确认型金股（侧重"见龙在田"）
- **晚报**（evening，15:30）：侧重全天复盘 + 次日布局，龙虎榜机构动向、次日布局型金股（覆盖全生命周期）
- **周报**（weekly，周末）：本周回顾 + 主线叙事演变 + 板块轮动 + 下周展望，≥6000字

**龙脉定位体系**：潜龙在渊（信号已现，市场未反应）/ 见龙在田（多维验证通过，趋势确认中）/ 飞龙在天（市场共识，情绪高潮）。

---

## 二、v4.0 核心架构与数据流

### 架构总览（DB 为中心）

```
                         ┌─────────────────────────────────────────┐
                         │           data/stock.db (19 表)          │
                         │  原始缓存 / 行情 / 资金 / 龙虎榜 / 涨停  │
                         │  北向 / 融资融券 / 电报 / VIP / 金股      │
                         │  洞见 / 报告 / 钱三强 / 热度 / 学习 / …  │
                         └────────────┬───────────────────┬────────┘
                 写入 ↑                │ 查询              │ 读取
   ┌──────────────────────┐   ┌────────┴─────────┐   ┌────┴──────────────┐
   │  data_collector.py   │   │ insight_engine.py │   │  api_server.py    │
   │  三时点采集(DB缓存)  │   │  7 维洞见引擎     │   │  15 REST 端点      │
   │  Sina+Tushare+东财   │   │  → data_summary   │   │  http.server:8765 │
   └──────────┬───────────┘   └────────┬─────────┘   └───────────────────┘
              │                        │
              │                        ↓
              │            ┌────────────────────────┐
              │            │ gold_stock_discovery.py│
              │            │ 多维共振(5维)→gold_stock│
              │            └────────────┬───────────┘
              ↓                         ↓
   ┌──────────────────────────────────────────────────┐
   │            report_generator.py (编排器)           │
   │  prepare: 采集→洞见→金股→组装 report_request.json │
   │  generate: Agent模式 / LLM API模式 撰写报告      │
   │  finalize: 校验→评分→写DB→刷网站→推飞书→触发学习 │
   └──────────────────────┬───────────────────────────┘
                          ↓
   ┌──────────────────────────────────────────────────┐
   │              learning_loop.py (盘后)              │
   │  verify_predictions 预判验证 → learning_record    │
   │  solidify_experience 经验固化 → learning_record   │
   │  gold_stock_backtest 金股回测 → gold_stock        │
   └──────────────────────────────────────────────────┘
```

### 数据流方向

1. **采集层**：`data_collector.py` 通过 `db.get_or_fetch()`（缓存优先）拉取数据，写入对应 DB 表
2. **洞见层**：`insight_engine.py` 从 DB 多表读取，生成结构化洞见写入 `market_insight`，并产出 `data/data_summary.json`
3. **金股层**：`gold_stock_discovery.py` 跨 5 维 DB 表交叉验证，共振金股写入 `gold_stock`，产出 `data/gold_stocks.json`
4. **编排层**：`report_generator.py` 组装 `data/report_request.json`（含 data_summary + insights + gold_stocks），作为报告撰写的唯一数据源
5. **服务层**：`api_server.py` 直接从 DB 读取，为前端网站与外部提供 REST API
6. **学习层**：`learning_loop.py` 盘后验证预判、固化经验、回测金股，形成自我进化闭环

---

## 三、目录结构与文件职责

```
stock-assistant/
├── SKILL.md                  # 本文件 - 项目宪法，红线规则，历史教训
├── analysis_prompt.md        # 深度分析提示词 v4 - 报告写作规范（信号驱动推理链）
├── config.json               # 持久化配置 - 飞书Webhook / LLM API Key 等
├── settings.py               # 全局设置 - Tushare Token、配置读取
│
├── db.py                     # ★v4 SQLite 缓存层 - 19表管理 + get_or_fetch 缓存优先策略
├── data_collector.py         # ★v4 数据采集 - 三时点(morning/noon/evening) DB驱动采集，替代 fetch_data.py
├── insight_engine.py         # ★v4 洞见引擎 - DB驱动 7 维分析，替代 insight_extractor.py
├── gold_stock_discovery.py   # ★v4 金股发现 - 多维共振(钱三强/龙虎榜/涨停/舆情/研报)
├── report_generator.py       # ★v4 报告编排器 - prepare→generate→finalize 三阶段流水线
├── api_server.py             # ★v4 API服务 - 15 REST端点(http.server, 端口8765)
├── learning_loop.py          # ★v4 学习闭环 - 预判验证+经验固化+金股回测
├── cls_collector.py          # ★v4 财联社电报采集 - 结构化写入 cls_telegraph 表
├── backfill_telegraphs.py    # ★v4 电报回填工具
│
├── qian_sanqiang_selector.py # 钱三强选股公式 - 量化选股引擎(EMA趋势+换手率+资金共振)
├── validate_report.py        # 报告校验 - 红线校验(热点追踪/龙脉定位/推理链/交叉验证/金股汇总表)
├── report_quality_evaluator.py # 质量评分系统 - 10维度×10分=100分
├── vip_extractor.py          # VIP信息结构化提取器
├── heat_tracker.py           # 热度量化追踪器(动态选板块+EMA平滑)
├── site_builder.py           # 网站数据生成 - 报告+数据转JSON供GitHub Pages
├── gold_stock_backtest.py    # 金股回测 - 1/3/5/10/20日收益追踪
├── push_feishu.py            # 飞书推送 - 推送链接+简报卡片
│
├── fetch_data.py             # ⚠v3 数据采集 - 已被 data_collector.py 替代(保留向后兼容)
├── insight_extractor.py      # ⚠v3 洞见提取 - 已被 insight_engine.py 替代(保留向后兼容)
├── extract_summary.py        # ⚠v3 数据摘要 - data_summary 现由 insight_engine.py 生成(保留向后兼容)
│
├── data/                     # 数据目录
│   ├── stock.db              # ★v4 SQLite数据库(19表) - 核心数据载体 ✅纳入git
│   ├── report_request.json   # ★v4 报告请求(含data_summary+insights+gold_stocks) - AI写报告唯一数据源
│   ├── data_summary.json     # 数据摘要(insight_engine生成) ✅纳入git
│   ├── gold_stocks.json      # 金股发现结果 ✅纳入git
│   ├── heat_data.json        # 热度数据(供网站ECharts) ✅纳入git
│   ├── qian_sanqiang_results.json  # 钱三强选股结果 ✅纳入git
│   ├── report_scores/        # 报告评分归档
│   └── cls_telegraph_archive/ # 电报增量归档 ✅纳入git
├── reports/                  # 报告输出目录 ✅纳入git
│   └── YYYY-MM-DD_晨报/午报/晚报/周报.md
├── docs/                     # GitHub Pages 网站根目录 ✅纳入git
│   ├── index.html            # SPA入口(看板/归档/热度/金股/信源/选股/日历)
│   ├── assets/               # JS/CSS(app.js, charts.js, styles.css)
│   └── data/                 # 网站数据JSON(自动生成)
│       ├── manifest.json / latest.json
│       ├── archive/YYYY-MM-DD_type.json
│       └── history/gold_stocks.json + heat_tracking.json
└── documentation/            # 项目文档(非网站)
```

**文件状态说明**：★ 标记为 v4.0 新增核心模块；⚠ 标记为 v3 旧模块，已被替代但保留以兼容历史调用，新流程不得依赖。

---

## 四、执行流程（v4.0: 三阶段编排，不可跳过）

v4.0 由 `report_generator.py` 统一编排，分为 prepare / generate / finalize 三阶段。

```
步骤0:   git clone（拉取最新代码）
         → git clone https://...github.com/kwjian-longzer/stock-assistant.git /workspace/stock-assistant

步骤1:   python report_generator.py --date YYYY-MM-DD --period morning --prepare
         PREPARE 阶段（4步）：
           [1/4] 数据采集  → python data_collector.py --period morning --date YYYY-MM-DD
                              （走 DB 缓存，写入 index_quote/sector_moneyflow/north_money 等表）
           [2/4] 洞见引擎  → python insight_engine.py --date YYYY-MM-DD --period morning
                              （从 DB 读取，7 维分析写入 market_insight，生成 data_summary.json）
           [3/4] 金股发现  → python gold_stock_discovery.py --date YYYY-MM-DD
                              （跨 5 维 DB 表共振，写入 gold_stock，生成 gold_stocks.json）
           [4/4] 组装请求  → 合并 data_summary + insights + gold_stocks
                              → 生成 data/report_request.json（AI写报告唯一数据源）

步骤2:   读取 data/report_request.json + analysis_prompt.md，撰写报告
         → Agent模式（定时任务）：按 analysis_prompt.md 规则撰写，保存到 reports/YYYY-MM-DD_报告类型.md
         → LLM自动模式：python report_generator.py --date YYYY-MM-DD --period morning --auto
           （需 config.json 配置 llm_api_key，自动调用 LLM API 生成）
         → 报告中每个数字必须来自 report_request.json

步骤3:   python report_generator.py --date YYYY-MM-DD --period morning --finalize \
           --report reports/YYYY-MM-DD_报告类型.md
         FINALIZE 阶段（6步）：
           [1/6] 报告校验  → validate_report.py 红线校验
           [2/6] 质量评分  → report_quality_evaluator.py（目标≥80分）
           [3/6] 写入 DB   → report 表（content/char_count/quality_score）
           [4/6] 刷新网站  → site_builder.py 生成 docs/data/*.json + 网站快照写入 website_snapshot 表
           [5/6] 推送飞书  → push_feishu.py 推送报告MD文件 + 交互卡片(链接+简报)
           [6/6] 触发学习  → 仅盘后(evening/weekly)运行 learning_loop.py
                              （预判验证→经验固化→金股回测）
         → GitHub Pages 自动部署（push后1-2分钟网站更新）

步骤4:   git add -A && git commit -m "..." && git push origin main
         → **仅用于代码文件修改的提交**（报告和数据已在步骤3自动提交）
```

### 一键完整流程（LLM自动模式）

```bash
python report_generator.py --date YYYY-MM-DD --period evening --auto
# 等价于：prepare → generate(LLM) → finalize 全自动
```

---

## 五、数据采集与时点（data_collector.py）

v4.0 数据采集全部走 `db.get_or_fetch()` 缓存优先策略，采集结果直接写入 DB 表，不再依赖 JSON 文件。

### 三时点采集编排

| 时点 | 时间 | 采集内容 | 写入 DB 表 |
|------|------|---------|-----------|
| 盘前 morning | 08:30 | 隔夜全球市场(Sina) + A股指数收盘(Tushare) + 板块资金 + 北向资金 | index_quote / sector_moneyflow / north_money / raw_cache |
| 盘中 noon | 11:50 | A股实时指数(Sina) + 板块资金 + 北向资金 | index_quote(实时) / sector_moneyflow / north_money |
| 盘后 evening | 15:30 | A股收盘(Tushare) + 涨停池(东财) + 龙虎榜 + 融资融券 + 板块资金 + 北向资金 | index_quote / limit_up / dragon_tiger / margin / sector_moneyflow / north_money |

辅助采集（按需）：
- `--period qian_sanqiang`：钱三强选股，写入 `qian_sanqiang_result` 表
- `--period calendar`：财经日历事件，写入 `calendar_event` 表
- `--period all`：全量采集（测试用）

### 采集数据源明细

| 数据类型 | 主数据源 | 写入表 | 说明 |
|----------|----------|--------|------|
| A股指数(实时) | Sina hq.sinajs.cn | index_quote | sh000001/sz399001/sz399006/sh000688，is_realtime=1 |
| A股指数(收盘) | Tushare index_daily | index_quote | 前收/收盘/涨跌幅，is_realtime=0 |
| 全球市场 | Sina | raw_cache | 美股/港股/外汇/商品，经DB缓存供洞见引擎读取 |
| 板块资金 | Tushare moneyflow | sector_moneyflow | 按行业聚合净流入，Top30 |
| 北向资金 | Tushare moneyflow_hsgt | north_money | north_money/hgt/sgt/south_money |
| 龙虎榜 | Tushare top_list | dragon_tiger | 按代码聚合净买入，Top30 |
| 涨停池 | 东方财富 push2ex.eastmoney.com | limit_up | 替代Tushare limit_list_d(高积分要求) |
| 融资融券 | Tushare margin | margin | rzye/rzche/rqye |
| 钱三强 | qian_sanqiang_selector | qian_sanqiang_result | 三强全中(100分)/两强命中(70分) |
| 财经日历 | 东方财富 datacenter-web | calendar_event | 未来14天经济事件 |
| 财联社电报 | cls_collector.py | cls_telegraph | 持续累积，结构化字段(event_type/sentiment/impact_level/sector_tags) |

---

## 六、数据库 Schema（db.py，19 张表）

数据库路径：`data/stock.db`，SQLite + WAL 模式（并发写入友好）。`db.init()` 幂等建表。

| # | 表名 | 用途 | 关键字段 |
|---|------|------|---------|
| 1 | raw_cache | 原始API数据缓存 | source, api_name, trade_date, params_hash, data_json |
| 2 | index_quote | 指数行情 | name, code, trade_date, close, pct_chg, is_realtime |
| 3 | sector_moneyflow | 板块资金流向 | trade_date, industry, net_mf_amount |
| 4 | limit_up | 涨停股票 | trade_date, ts_code, name, pct_chg, industry, amount |
| 5 | dragon_tiger | 龙虎榜 | trade_date, ts_code, name, net_buy, reason |
| 6 | north_money | 北向资金 | trade_date, north_money, hgt, sgt, south_money |
| 7 | margin | 融资融券 | trade_date, exchange_id, rzye, rzche, rqye |
| 8 | cls_telegraph | 财联社电报 | telegraph_id, content, timestamp, is_red, event_type, sentiment, impact_level, sector_tags |
| 9 | cls_telegraph_stock | 电报关联股票 | telegraph_id, stock_name, stock_code |
| 10 | cls_vip_article | VIP文章 | article_id, title, brief, published_at, related_stock |
| 11 | vip_discovered_stock | VIP发现股票 | article_id, stock_code, industry, match_score, match_source |
| 12 | gold_stock | 金股推荐+回测 | name, code, recommend_date, score, return_1d/3d/5d/10d/20d + v4扩展(catalyst/dragon_vein/verification/signal_source/buy_range/target_price/stop_loss/strength) |
| 13 | market_insight | 市场洞见 | date, period, category, signal_text, a_share_impact, confidence |
| 14 | report | 报告记录 | date, period, title, content, char_count, quality_score |
| 15 | qian_sanqiang_result | 钱三强选股结果 | date, stock_code, stock_name, strategy, score, detail_json |
| 16 | heat_tracking | 热度追踪 | date, sector, heat_score, capital_flow, limit_up_count, lifecycle |
| 17 | learning_record | 学习记录 | date, prediction, actual, gap_analysis, lesson, category |
| 18 | website_snapshot | 网站数据快照 | date, period, snapshot_json |
| 19 | calendar_event | 财经日历事件 | event_date, event_time, title, importance, category, detail |

### 核心机制

- **`get_or_fetch(source, api_name, fetch_func, trade_date, params, ttl_hours)`**：缓存优先策略。先查 `raw_cache`，未过期直接返回；否则调 `fetch_func` 拉取并写入。默认 TTL 12 小时，避免重复请求 Tushare。
- **`query_resonance(date)`**：共振分析，跨数据源（电报/龙虎榜/涨停/VIP）交叉匹配，出现≥2源的股票即为共振信号。
- **幂等扩展**：`gold_stock` 表通过 `_alter_add_columns` 幂等添加 v4 扩展字段，兼容历史数据。

---

## 七、洞见引擎与金股发现

### 洞见引擎（insight_engine.py，7 维分析）

从 DB 各表读取数据，生成多维度结构化洞见，写入 `market_insight` 表，并产出 `data/data_summary.json`。

| 分析函数 | 维度 category | 数据来源 | 输出 |
|---------|--------------|---------|------|
| analyze_global | 海外市场 | raw_cache(全球行情) | 美股/港股/外汇/商品涨跌→A股影响预判 |
| analyze_a_share | A股盘面 | index_quote | 上证/深证/创业板/科创50涨跌分析 |
| analyze_sector | 板块资金 | sector_moneyflow | Top5流入+Bottom5流出板块 |
| analyze_capital | 资金面 | north_money + margin | 北向资金净额+融资余额 |
| analyze_dragon_tiger | 龙虎榜 | dragon_tiger | Top5机构净买入动向 |
| analyze_limit_up | 涨停池 | limit_up | 涨停总数+板块分布+连板梯队 |
| analyze_cls_sentiment | 财联社舆情 + 跨市场映射 | cls_telegraph | 电报密度/红色信号/海外→A股板块传导 |

**洞见字段**：`category / signal_text / a_share_impact / confidence(high/medium/low) / signal_time`
**confidence 判定**：涨跌幅≥1.5%或资金≥50亿为 high；其余按幅度分 medium/low。

### 金股发现引擎（gold_stock_discovery.py，5 维共振）

跨 5 个维度 DB 表交叉验证，命中维度越多共振越强，评分越高。至少命中 2 维才纳入候选。

| 维度 | 权重 | 数据来源表 | 含义 |
|------|------|-----------|------|
| 钱三强 | 35 | qian_sanqiang_result | 基本面三强(EMA趋势+换手率+资金) |
| 龙虎榜 | 25 | dragon_tiger | 机构资金动向 |
| 涨停 | 15 | limit_up | 动量确认 |
| 舆情 | 15 | cls_telegraph_stock | 财联社电报催化 |
| 研报 | 10 | vip_discovered_stock | 机构研报覆盖 |

**评分与力度**：score≥50 且共振≥4维 → 强推荐；共振3维 → 推荐；共振2维 → 关注。
**写入 gold_stock 表**：含 catalyst(催化) / dragon_vein(龙脉) / verification(验证) / signal_source(信号源) / buy_range / target_price / stop_loss / strength。

### 龙脉定位标准（报告引用）

| 阶段 | 定义 | 识别特征 |
|------|------|---------|
| 潜龙在渊 | 信号已现，市场未反应 | 有催化信号 + 资金未流入 + 股价未启动 |
| 见龙在田 | 多维验证通过，趋势确认中 | 信号 + 资金流入 + 放量 + 涨幅3-7% |
| 飞龙在天 | 市场共识，情绪高潮 | 涨停/连板 + 全网热议 + 换手率极高 |

---

## 八、API 服务（api_server.py）

基于 Python 标准库 `http.server`，从 SQLite DB 直接读取数据，为前端网站与外部提供 REST API。

- **启动**：`python api_server.py`（默认端口 8765，监听 0.0.0.0）
- **静态服务**：同时提供 `docs/` 目录静态文件服务（`/docs/index.html` 即网站首页）
- **CORS**：已开启跨域支持

### 15 个 REST 端点

| 端点 | 说明 |
|------|------|
| GET /api/health | 健康检查 + DB 统计 |
| GET /api/dashboard | 综合看板（聚合指数+板块+北向+龙虎榜+洞见+金股+全球） |
| GET /api/indices | 指数行情（支持 ?realtime=1） |
| GET /api/sectors | 板块资金流向（支持 ?top=N） |
| GET /api/north-money | 北向资金 |
| GET /api/dragon-tiger | 龙虎榜（支持 ?limit=N） |
| GET /api/limit-up | 涨停池 |
| GET /api/insights | 市场洞见（按 category 分组） |
| GET /api/gold-stocks | 金股推荐（支持 ?limit=N） |
| GET /api/reports | 报告列表（含最新报告） |
| GET /api/calendar | 财经日历（支持 ?start=&end=） |
| GET /api/global | 全球市场（美股/港股/外汇/商品） |
| GET /api/learning | 学习记录 |
| GET /api/heat | 热度追踪（支持 ?sector=&days=N） |
| GET /api/telegraphs | 财联社电报（支持 ?red=1 只看红色） |

通用查询参数：`?date=YYYY-MM-DD`（或 YYYYMMDD），不传默认今天。

---

## 九、学习闭环（learning_loop.py）

盘后（晚报生成完成后）运行，形成"预判→验证→总结"的自我学习闭环，持续把市场反馈沉淀为可复用经验。
**仅在 evening / weekly 时点由 report_generator.finalize() 触发**。

### 三个核心函数

1. **verify_predictions(db, date_str)** — 盘后预判验证
   - 读取前一交易日（最近有洞见的日期）的 `market_insight.a_share_impact` 视为"对当日的预判"
   - 与当日实际 `index_quote` 指数行情对比，解析多空方向（看涨/看跌/中性）
   - 逐条判定命中/反向/未兑现，写入 `learning_record`（category="盘后验证"）
   - 特殊处理美元/人民币反向关系

2. **solidify_experience(db, date_str)** — 经验固化
   - 分析最近 30 条 `learning_record`（盘后验证类），按因子归纳命中率
   - 产出：整体预判准确率 + 各因子命中率（样本≥3）+ 北向资金分桶规律（样本≥2，如"净流入≥50亿时大盘上涨概率70%"）
   - 以 category="经验总结" 写回 `learning_record`
   - 幂等：重复运行清除当日旧经验总结

3. **run(db, date_str)** — 主入口
   - 顺序执行 verify_predictions → solidify_experience → gold_stock_backtest（容错隔离）

### 因子分类规则

北向资金 / 美股 / 港股 / 黄金 / 原油 / 美元汇率 / 板块资金 / 龙虎榜 / 涨停池 / 融资融券 / 舆情 / 其他

---

## 十、红线规则（绝对不可违反）

### 红线1：禁止编造任何数据
- 报告中的**每一个数字**都必须来自 `data/report_request.json`（含 data_summary + insights + gold_stocks）
- 指数点位、涨跌幅、成交额、股票代码、股票名称、净买入金额等，全部必须与数据一致
- **不允许出现 `XXX`、`xxx`、`688XXX`、`300XXX` 等占位符**
- 不允许出现 `？？`、`待补充`、`TODO`、`TBD`、`待定` 等占位符

### 红线2：数据缺失时的处理
- 如果数据中某个字段为"数据暂缺"，报告中必须写"数据暂缺"
- 如果使用了降级数据（标注为 DEGRADED / FAILED），报告中必须说明数据来源和降级原因
- **宁可留空写"数据暂缺"，也绝不编造**

### 红线3：股票代码必须真实
- 报告中提到的每一只股票的代码和名称必须来自 `gold_stocks` 引擎输出或 `data_summary` 中真实存在
- 不允许凭空创造不存在的股票
- 金股推荐必须基于 `gold_stocks` 的多维共振结果，附完整推理链和龙脉定位

### 红线4：推理链不可断链
- 每条分析必须形成完整推理链：`信号发现 → 影响判断 → 板块映射 → 数据验证 → 概率评估 → 策略建议`
- 必须引用洞见引擎输出（`insights`）：按 category 分组、confidence=high 逐条分析、跨市场映射构建传导逻辑
- 全文至少出现 8 处"信号→数据印证"的交叉验证

### 红线5：校验不通过不推送
- `validate_report.py` 返回失败时，必须修复报告后重新校验
- 不得跳过校验步骤直接推送（report_generator.finalize 会自动校验，未通过需人工修复）

### 红线6：代码修改必须推送 GitHub
- 修改代码后必须执行 `git add → git commit → git push origin main`
- **代码修改未推送到 GitHub = 等于没修改**，自动任务 git pull 拉取的是旧代码

---

## 十一、数据源说明（已验证，禁止随意修改）

> **警告：以下数据源代码和格式已经过实际API验证。v4.0 中由 data_collector.py 统一管理，经 DB 缓存。
> 任何修改必须先调用真实API确认格式正确，禁止凭假设修改。**

### 已验证数据源（v4.0 采集层引用）

| 数据类型 | 代码/URL | 格式说明 |
|----------|----------|----------|
| A股指数(实时) | Sina `sh000001/sz399001/sz399006/sh000688` | parts[2]=昨收, parts[3]=现价, parts[9]=成交额 |
| 美股收盘 | Sina `int_dji/int_nasdaq/int_sp500` | `名称,点位,涨跌额,涨跌幅%` |
| 恒生指数 | Sina `int_hangseng` | `名称,当前价,涨跌额,涨跌幅%,...` |
| 恒生科技 | Sina `rt_hkHSTECH` | `代码,名称,当前价,昨收,...`（hktech无效，rt_hkHSTECH有效） |
| 美元指数 | Sina `DINIW` | `时间,当前价,当前价重复,昨收,...`，涨跌额=price-pre_close |
| 离岸人民币 | Sina `fx_susdcny` | `时间,当前价,买入,卖出,...,名称,涨跌额,涨跌幅` |
| 黄金 | Sina `hf_GC` | `当前价,,昨收,开盘,最高,最低,时间,...,名称` |
| 原油 | Sina `hf_CL` | 同黄金 |
| 财联社电报 | CLS API `https://www.cls.cn/api/cache?...telegraph` | JSON，roll_data数组，含title/content/color/stock_list，无需签名 |
| 涨停池 | 东方财富 `push2ex.eastmoney.com/getTopicZTPool` | JSON，data.pool数组，含c(代码)/n(名称)/zdp(涨幅)/hybk(行业) |
| 财经日历 | 东方财富 `datacenter-web.eastmoney.com` RPT_ECONOMY_CALENDAR | JSON，result.data数组，含RELEASE_TIME/INDICATOR_NAME/IMPORTANCE |
| 板块资金 | Tushare `moneyflow` | 按行业聚合 net_mf_amount |
| 北向资金 | Tushare `moneyflow_hsgt` | north_money/hgt/sgt/south_money |
| 龙虎榜 | Tushare `top_list` | 按代码聚合 net_amount |
| 融资融券 | Tushare `margin` | rzye/rzche/rqye，当天空数据自动回滚前5天 |
| 指数收盘 | Tushare `index_daily` | close/pre_close/amount |

### 数据源修改规则
1. **禁止修改已验证的 Sina 代码**（DINIW、fx_susdcny、hf_GC、hf_CL、int_dji、rt_hkHSTECH 等）
2. **禁止修改已验证的解析格式**（字段索引、计算逻辑），data_collector.py 中封装的解析函数
3. 如需新增数据源，必须先调用真实 API 验证返回格式，再修改 `data_collector.py`
4. 修改后必须运行 `python data_collector.py --period evening` 全量测试
5. 修改后必须运行 `python insight_engine.py --period evening` 验证洞见输出
6. 修改后必须运行 `python report_generator.py --period evening --finalize` 验证校验逻辑
7. **修改后必须 git push 到 GitHub**（见红线6）

### 已知限制
- Tushare `limit_list_d` 需 5000+ 积分，当前积分不足，使用东方财富涨停池替代
- Tushare `trade_cal` 可能标记调休日为交易日但实际无数据，margin 已实现自动回滚
- Sina 期货/外汇数据涨跌额字段常为空，需从 pre_close 计算 change = price - pre_close
- 东方财富涨停池接口参数可能变动，失败时降级为空数据

---

## 十二、报告格式要求（v4）

### 通用要求
- 字符数：日报 2500-4000 字，周报 ≥6000 字
- 使用 Markdown 格式
- 保存路径：`reports/YYYY-MM-DD_报告类型.md`
- 标题：多维市场研报（晨报/午报/晚报/周报）
- 使用表格而非大段文字呈现对比数据
- 金股部分先出结构化汇总表，再逐只展开推理链
- 涨跌幅保留 2 位小数，金额标注单位

### 三层递进结构（见 analysis_prompt.md）
1. **信号与全景**（发生了什么）：洞见引擎输出引用 + 指数与外围全景 + 财联社舆情扫描
2. **热点追踪与逻辑验证**（意味着什么）：热点生命周期(崛起/高潮/退烧) + 资金流向验证 + 涨跌停情绪图谱
3. **金股与策略**（怎么办）：金股推荐(含龙脉定位) + 结构化汇总表 + 策略建议(多空逻辑+概率权重)

### 金股结构化汇总表

| 代码 | 名称 | 龙脉 | 共振维度 | 信号源 | 催化逻辑 | 买入区间 | 止盈 | 止损 | 力度 | 时间 |
|------|------|------|---------|-------|---------|---------|------|------|------|------|

- 共振维度：gold_stocks.dimensions（钱三强/龙虎榜/涨停/舆情/研报）
- 力度：强推荐(4+维度, score≥50) / 推荐(3维度) / 关注(2维度)
- 龙脉：潜龙在渊 / 见龙在田 / 飞龙在天

### 概率表述规范
- 大概率(>70%) / 倾向于(50-70%) / 值得警惕(30-50%) / 尚需验证(<30%)
- 信号分级：L1官方(>90%) / L2权威(70-90%) / L3传闻(<70%)

---

## 十三、历史教训（必须记住）

### 教训1：数据编造事件（2026-06-22）
- **问题**：AI在生成晚报时完全无视了采集到的真实数据，编造了虚假的指数点位、虚假的涨跌幅、虚假的股票代码（688XXX占位符）
- **根因**：原始数据文件24万行，AI无法有效读取；缺少中间摘要层；缺少校验机制
- **修复**：新增数据摘要层 + validate_report.py（报告校验）+ SKILL.md红线规则。v4.0 进一步由 insight_engine 生成结构化 data_summary，report_request.json 成为唯一数据源

### 教训2：飞书推送配置丢失（2026-06-22）
- **问题**：push_feishu.py 只从环境变量读取 Webhook，自动化任务每次启动都是新会话，环境变量丢失
- **根因**：配置没有持久化到文件
- **修复**：push_feishu.py 支持从 config.json 读取配置，`--config` 参数一次性保存

### 教训3：美股期货解析错误（2026-06-22）
- **问题**：新浪期货数据格式中涨跌额和涨跌幅字段为空，代码把昨收价当涨跌幅
- **根因**：未正确理解新浪数据格式
- **修复**：从 raw 字段解析 pre_close，计算 change = price - pre_close

### 教训4：港股恒生科技获取失败
- **问题**：新浪代码 hk_HSTECH 无效
- **修复**：改为 rt_hkHSTECH，并添加降级代码列表

### 教训5：新闻采集只保存HTML
- **问题**：只保存了HTML原始文本，没有提取有效标题
- **修复**：新增正则提取 title/h1/h2/h3 标签文本

### 教训6：修复不端到端验证
- **问题**：修改了脚本但忘了同步更新自动化任务的 message，导致修复无效
- **修复**：每次修改后必须端到端验证完整流程，并同步更新所有自动化任务

### 教训7：报告退化为数据罗列（2026-06-23）
- **问题**：研报逐渐偏离初衷，变成简单罗列数据并粗暴摘出涨停板最猛的股票作为金股，缺乏推理链
- **根因**：缺少信源驱动分析框架，没有将财联社信号与市场数据交叉验证
- **修复**：新增第零章（财联社信源扫描），重写 analysis_prompt.md 强化推理链，金股必须满足信号+验证双重标准。v4.0 由 insight_engine + gold_stock_discovery 引擎化实现

### 教训8：财联社信源遗忘（2026-06-23）
- **问题**：财联社作为研报核心特色信源被完全遗忘，未集成到数据采集流程
- **根因**：开发过程中专注于修复数据源问题，忽略了信源整合
- **修复**：v4.0 由 cls_collector.py 持续采集电报写入 cls_telegraph 表，insight_engine 的 analyze_cls_sentiment 实现跨市场映射

### 教训9：修改未推送GitHub导致自动任务失效（2026-06-25）
- **问题**：在本地修改了代码，但未 git commit & push 到 GitHub。自动任务执行 git pull 时拉取的是旧代码
- **根因**：修改代码后遗漏了推送到远程仓库这一关键步骤
- **修复**：**每次修改代码后，必须执行 git add → git commit → git push origin main**（已列为红线6）

---

## 十四、飞书推送配置

- Webhook 地址保存在 `config.json` 中（持久化）
- 首次配置：`python push_feishu.py --config 'https://open.feishu.cn/open-apis/bot/v2/hook/xxx'`
- 自动化任务执行时会自动从 config.json 读取，无需每次重新配置
- `config.json` 同时存放 `llm_api_key`（LLM自动模式生成报告时使用）
