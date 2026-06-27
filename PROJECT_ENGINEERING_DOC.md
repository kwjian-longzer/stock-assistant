# 观澜踏浪 — 工程白皮书 v5.0

> **本文档是「观澜踏浪」项目的阶段性工程总结，涵盖项目宪法、架构设计、开发历程、Bug修复记录与自我进化机制。**
> 版本：v5.0（含7维共振引擎+热度追踪闭环+飞书文档集成+三栏首页） ｜ 日期：2026-06-28 ｜ 仓库：github.com/kwjian-longzer/stock-assistant

---

## 一、终极目标与项目宪法

### 1.1 终极目标

构建一套**自我进化的A股市场分析系统**，实现从数据采集到洞见推理、金股发现、报告生成、盘后验证、经验固化的全链路自动化。系统不是"数据搬运工"，而是"市场翻译官"——将海量市场信号转化为可验证的投资洞见，并通过持续学习让每一次运行都产生"更好的自己"。

核心方法论：**信号 → 验证 → 洞见 → 策略**

四大报告类型：
- **晨报**（08:30）：隔夜信号 → 今日预判，侧重"潜龙在渊"
- **午报**（11:50）：上午盘面验证，侧重"见龙在田"
- **晚报**（15:30）：全天复盘 + 次日布局，覆盖全生命周期
- **周报**（周末）：主线叙事 + 板块轮动 + 下周展望，≥6000字

龙脉定位体系：**潜龙在渊**（信号已现，市场未反应）/ **见龙在田**（多维验证通过）/ **飞龙在天**（市场共识，情绪高潮）

#### v5.0 升级说明

在 v4.0 进化引擎的基础上，v5.0 完成五项关键升级，使系统从"代码已写但未接入生产"升级为"全链路自动化运行 + 共振引擎重构 + 热度追踪闭环 + 飞书文档集成 + 三栏首页"：

1. **7维共振金股发现引擎**：在原 5 维基础上重构为 7 维体系，权重重新校准——研报 40（最高）/ 钱三强 30 / 主力资金流入 15 / 涨停 15 / 龙虎榜 15 / 北向 10 / 舆情 10。新增"主力资金流入强度"维度（Tushare `moneyflow` 接口），并将"命中即固定加分"升级为"按金额/次数/连板的强度差异化打分"。
2. **板块热度加权与退烧过滤**：金股打分接入 `heat_tracking` 板块生命周期数据——高潮板块 ×1.2 加分、崛起板块 ×1.0、退烧板块 ×0.5 降权，并通过过滤层直接排除退烧板块与 ST 风险股，避免推荐已进入退潮期的标的。
3. **热度追踪闭环**：补全 `heat_tracker.py` 缺失的 DB 写入链路（`write_heat_to_db()` → `heat_tracking` 表），并新增 15:25 盘后定时任务（`heat_tracker.py --export`），让板块热度数据每日自动入库、自动被金股引擎消费，形成"采集 → 计算 → 入库 → 加权 → 过滤"的完整闭环。
4. **飞书文档集成**：报告生成后由 `push_feishu.py` 调用 `lark-cli drive +import --type docx` 自动将 Markdown 报告转为飞书在线文档，放入「观澜踏浪项目」飞书文件夹，并通过消息卡片推送文档链接 + 网站链接 + 洞见摘要，实现"报告自动转飞书文档 + 消息推送"。
5. **网站首页三栏布局**：首页从"今日看板"重构为三大栏目——**观澜洞见**（盘前/盘中/盘后三 Tab 切换，拉取最新洞见）/ **闲看潮涌**（隔夜美股、亚太、A股三栏实时数据 + 市场温度计 + 板块热度排行）/ **踏浪分金**（按入库时间倒序的增量金股速览表）。

### 1.2 项目宪法（红线规则）

| 编号 | 规则 | 说明 |
|------|------|------|
| C1 | 数据唯一来源 | DB优先，API次之，严禁编造数据 |
| C2 | 每步可验证 | 每个组件有独立测试入口和退出码 |
| C3 | 推理链完整 | 信号→验证→洞见→策略，不允许断链 |
| C4 | 概率表述 | 大概率(>70%) / 倾向于(50-70%) / 值得警惕(30-50%) / 尚需验证(<30%) |
| C5 | 时间戳全程追溯 | 每条数据都有fetch_time，每条洞见有date+period |
| C6 | 幂等设计 | 同一天重复运行不堆积脏数据 |
| C7 | 局部失败不阻断 | 所有外部调用try/except包裹 |
| C8 | 知识持久化 | 学习成果写入knowledge/目录并git push，确保跨会话继承 |

### 1.3 OpenClaw式进化理念

系统的核心理念源自OpenClaw的自我进化机制：每次运行后，系统不仅是"记录"命中率，更要**诊断**失败模式、**生成**改进假设、**回测验证**、**部署**有效改进。学习成果通过GitHub文件持久化，确保每次沙盒中启动的Agent能继承全部历史经验。

---

## 二、项目层次结构

### 2.1 五层架构

```
┌─────────────────────────────────────────────────────────┐
│  Layer 5: 进化层 (盘后)                                  │
│  evolution/ — 六阶段闭环 + 外部学习 + 知识持久化          │
│  learning_loop.py — 预判验证 + 经验固化                   │
├─────────────────────────────────────────────────────────┤
│  Layer 4: 前端展示层                                      │
│  docs/ — 三栏首页（观澜洞见/闲看潮涌/踏浪分金）           │
│        + 日报归档/板块热度/金股追踪/财联社/钱三强/投资日历  │
├─────────────────────────────────────────────────────────┤
│  Layer 3: 后端服务层                                      │
│  api_server.py — 17个REST端点，HTTP:8765                 │
├─────────────────────────────────────────────────────────┤
│  Layer 2: 分析引擎层                                      │
│  insight_engine.py | report_generator.py |              │
│  gold_stock_discovery.py（7维共振金股引擎v5）|           │
│  vip_search_v4.py | heat_tracker.py |                   │
│  qian_sanqiang_selector.py                              │
├─────────────────────────────────────────────────────────┤
│  Layer 1: 数据采集层                                      │
│  data_collector.py (3时点+主力资金+钱三强) |             │
│  cls_collector.py (每2小时 + VIP文章 + v4股票发现)        │
├─────────────────────────────────────────────────────────┤
│  Foundation: 数据库层                                     │
│  db.py — SQLite 20表 + get_or_fetch缓存                  │
└─────────────────────────────────────────────────────────┘
```

### 2.2 完整目录结构

```
stock-assistant/
├── SKILL.md                    # 项目宪法 — 红线规则、历史教训（Agent首先必读）
├── PROJECT_ENGINEERING_DOC.md  # 本文件 — 工程白皮书
├── UPGRADE_PLAN_v5.md          # v5升级计划书（P1/P2/P3问题与方案）
├── analysis_prompt.md          # 深度分析提示词 v4 — 报告写作规范
├── settings.py                 # 统一配置管理（环境变量/config.json）
│
├── db.py                       # SQLite数据库层（20表 + get_or_fetch缓存）
├── data_collector.py           # 三时点数据采集（Sina+Tushare+东财）+ 主力资金 + 钱三强
├── cls_collector.py            # 财联社电报采集（每2小时 + VIP文章 + v4股票发现）
├── insight_engine.py           # 洞见引擎（7维度分析 → market_insight + data_summary）
├── gold_stock_discovery.py     # v5共振金股发现引擎（7维+加权+过滤+强度打分）
├── vip_search_v4.py            # v4多源VIP股票发现（东财+Web+研报+CLS）
├── vip_extractor.py            # VIP信息提取器（v4兼容入口）
├── heat_tracker.py             # 板块热度追踪器（计算+入库+导出）
├── qian_sanqiang_selector.py   # 钱三强选股器
├── report_generator.py         # 报告编排器（prepare→generate→finalize）
├── validate_report.py          # 报告校验器（10条红线校验）
├── report_quality_evaluator.py # 报告质量评分器
├── site_builder.py             # 网站数据生成器（MD→JSON→GitHub Pages）
├── push_feishu.py              # 飞书推送器（Webhook卡片+飞书文档+消息卡片）
├── api_server.py               # REST API服务（17端点，:8765）
├── gold_stock_backtest.py     # 金股回测引擎
├── learning_loop.py            # v4学习闭环（验证→固化→回测→进化→外部学习→持久化）
├── backfill_telegraphs.py      # 电报回填工具
├── extract_summary.py          # 摘要提取工具
│
├── evolution/                 # 进化引擎系统
│   ├── __init__.py
│   ├── engine.py              # 六阶段进化引擎（诊断→假设→实验→验证→部署→监控）
│   ├── external_learner.py    # 外部学习器（盘后复盘+盲区扫描+观点对齐+模式发现）
│   ├── backtest_runner.py     # 回测框架（历史回测+Fisher精确检验+A/B对比）
│   └── knowledge_persistor.py  # 知识持久化（JSON/MD→git push→跨会话继承）
│
├── knowledge/                  # 知识库（Agent自我意识的载体）
│   ├── factor_weights.json    # L1因子权重表（insight_engine动态读取）
│   ├── accuracy_benchmark.json # 预判准确率基线
│   ├── lessons_learned.md      # 失败案例库
│   ├── external_lessons.md     # 外部学习成果
│   ├── engine_changelog.md     # L3逻辑迭代记录
│   ├── failed_hypotheses.md    # 失败假设归档
│   ├── deploy_state.json       # 部署状态（shadow/production）
│   └── EVOLUTION_ENGINE_DESIGN.md # 进化引擎设计文档
│
├── docs/                       # 前端网站（GitHub Pages托管）
│   ├── index.html             # 主页面（SPA，三栏首页）
│   ├── assets/                # 前端资源
│   │   ├── app.js             # 应用逻辑（renderInsights/renderMarketDashboard/renderGoldStocksTable）
│   │   ├── charts.js          # 图表（ECharts）
│   │   └── styles.css         # 样式
│   └── data/                  # 网站数据（site_builder生成）
│       ├── latest.json        # 最新报告数据
│       ├── manifest.json      # 报告清单
│       └── archive/           # 历史报告归档
│
├── data/                       # 运行时数据
│   ├── stock.db               # SQLite数据库（20表）
│   ├── data_summary.json      # 洞见引擎产出的数据摘要
│   ├── gold_stocks.json       # 金股JSON（含v5共振维度+板块生命周期）
│   ├── heat_data.json         # 板块热度数据
│   └── cls_telegraph_archive/ # 电报归档
│
└── templates/                  # 模板
    └── html_report/           # HTML报告模板
```

---

## 三、网站架构

### 3.1 前端（docs/）

**技术栈**：原生HTML + JavaScript + ECharts图表库 + CSS3

**首页三栏布局（v5重构）**：

```
┌─────────────────────────────────────────────────────┐
│  观澜洞见（最优先展示）                                │
│  ┌─────────┬─────────┬─────────┐                   │
│  │ 盘前洞见 │ 盘中洞见 │ 盘后洞见 │  ← 三Tab切换      │
│  └─────────┴─────────┴─────────┘                   │
├─────────────────────────────────────────────────────┤
│  闲看潮涌（市场数据仪表盘）                            │
│  ┌──────────┬──────────┬──────────┐                │
│  │ 隔夜美股  │ 亚太市场  │ A股最新  │  ← 三栏实时数据  │
│  ├──────────┴──────────┴──────────┤                │
│  │ 市场温度计  │  板块热度排行     │  ← 仪表盘+排行    │
│  └───────────────────────────────┘                │
├─────────────────────────────────────────────────────┤
│  踏浪分金（金股速览）                                  │
│  ┌────┬────┬────┬────┬────┐                       │
│  │名称│代码│维度│评分│入库时间│  ← 增量金股表格        │
│  └────┴────┴────┴────┴────┘                       │
└─────────────────────────────────────────────────────┘
```

**保留的子页面（侧边栏）**：
- 日报归档、板块热度、金股追踪、财联社信源、钱三强选股、投资日历

**数据来源**：前端通过 `fetch('/api/...')` 调用后端REST API获取实时数据，同时 `docs/data/` 下的静态JSON作为GitHub Pages降级方案。首页三栏分别通过 `/api/insights/latest`、`/api/index/{date}`、`/api/gold-stocks/recent` 获取数据。

**部署方式**：GitHub Pages自动部署。`site_builder.py` 将报告转为JSON写入 `docs/data/`，git push后GitHub Pages自动更新。

### 3.2 后端（api_server.py）

**技术栈**：Python `http.server`（轻量HTTP服务，无需额外依赖）

**17个REST端点**（v5新增 `/api/insights/latest` 与 `/api/gold-stocks/recent` 两个端点以支撑三栏首页）：

| 端点 | 方法 | 功能 |
|------|------|------|
| `/` | GET | 首页（重定向到index.html） |
| `/api/health` | GET | 健康检查 |
| `/api/stats` | GET | 数据库统计概览 |
| `/api/index/{date}` | GET | 指数行情 |
| `/api/sectors/{date}` | GET | 板块资金流向 |
| `/api/north-money/{date}` | GET | 北向资金 |
| `/api/dragon-tiger/{date}` | GET | 龙虎榜 |
| `/api/limit-up/{date}` | GET | 涨停池 |
| `/api/gold-stocks/{date}` | GET | 金股推荐 |
| `/api/insights/{date}` | GET | 市场洞见 |
| `/api/insights/latest` | GET | 最新洞见（不限日期，自动取最近有数据的日期） ★v5新增 |
| `/api/gold-stocks/recent` | GET | 近期金股（最近N个推荐日，按入库时间排序） ★v5新增 |
| `/api/reports/{date}` | GET | 报告列表 |
| `/api/report/{date}/{period}` | GET | 指定报告内容 |
| `/api/telegraphs` | GET | 最新电报 |
| `/api/learning/{date}` | GET | 学习记录 |
| `/api/website/snapshot/{date}/{period}` | GET | 网站快照 |

**运行方式**：`python api_server.py --port 8765`，监听 `0.0.0.0:8765`

---

## 四、关键引擎与工作流

### 4.1 洞见引擎（insight_engine.py）

**功能**：从DB多表读取数据，生成7维度结构化洞见

**7个分析维度**：

| 维度 | 数据源 | 产出 |
|------|--------|------|
| 海外市场 | raw_cache (Sina全球行情) | 美股/港股/商品 → A股映射 |
| A股盘面 | index_quote | 指数涨跌 → 情绪判断 |
| 板块资金 | sector_moneyflow | Top5流入/流出 → 主线判断 |
| 资金面 | north_money + margin | 北向+杠杆 → 资金动向 |
| 龙虎榜 | dragon_tiger | 机构净买入 → 资金关注 |
| 涨停池 | limit_up | 连板梯队 → 市场主线 |
| 财联社舆情 | cls_telegraph | 跨市场映射 → 板块影响 |

**输出**：写入 `market_insight` 表 + 生成 `data/data_summary.json`

**v4进化集成**：insight_engine读取 `knowledge/factor_weights.json` 动态调整信号权重，而非硬编码。

### 4.2 v4多源VIP股票发现（vip_search_v4.py）

**功能**：从VIP文章中发现关联股票，使用多源动态搜索

**数据源优先级**：
1. MCP传入结果（Agent模式，推荐）— WebSearch + fxbaogao + Tushare MCP
2. HTTP API直接调用（独立运行模式，降级）

**5步流程**：
1. 解析VIP文章 → 结构化信号（领域/线索/事件类型/板块约束）
2. Tushare初筛候选股票（MCP或Python库）
3. 对Top15候选，多源搜索：东财公告API + WebSearch + fxbaogao研报 + CLS电报
4. 加权线索验证（多源交叉验证加分 + 同义词匹配 + 复合线索拆分）
5. 排除逻辑（匹配率<25%排除 + 仅通用概念排除 + 证据质量降权）→ 排序

**MCP双模式测试结果**：
- HTTP模式：60.5秒完成，有研粉材排名#1（140分）
- MCP模式：49.8秒完成，有研粉材排名#1（140分）
- MCP模式快17.8%，结果一致性100%

### 4.3 金股共振发现引擎 v5（gold_stock_discovery.py）

> **v5 完全重写**：从 v4 的"5维命中即固定加分"升级为"7维体系 + 2加权层 + 1过滤层 + 强度差异化打分"。

**功能**：跨7维DB表交叉验证，发现多维共振金股，写入 `gold_stock` 表与 `data/gold_stocks.json`。

#### 4.3.1 七维权重表

| # | 维度 | 基础权重 | 数据源 | 强度打分规则 |
|---|------|----------|--------|--------------|
| 1 | 研报覆盖 | **40**（最高） | vip_discovered_stock | 每篇研报 +5，上限 +20（总60） |
| 2 | 钱三强 | **30** | qian_sanqiang_result | 三强全命中 +10，两强命中 +5 |
| 3 | 主力资金流入 | 15 | moneyflow（★v5新增） | (超大单净额+大单净额/2)/亿×2，上限 +15 |
| 4 | 涨停动量 | 15 | limit_up | 连板数×5（1板5/2板10/3板+15） |
| 5 | 龙虎榜资金 | 15 | dragon_tiger | 净买入金额/亿×3，上限 +15 |
| 6 | 北向资金 | 10 | north_money | 净流入/10亿×2，上限 +10 |
| 7 | 舆情催化 | 10 | cls_telegraph | 电报条数×1 + 红色标记×3，上限 +10 |

> 维度 1、2 为**核心维度**（高权重，候选股至少命中 1 个核心维度才纳入候选）。
> 维度 3-7 为**加分维度**，采用强度差异化打分（命中仅得基础分，金额/次数/连板越多加分越高）。

#### 4.3.2 加权层（2层）

| 加权项 | 规则 | 说明 |
|--------|------|------|
| W1. 板块热度加权 | 高潮板块 ×1.2 / 崛起板块 ×1.0 / 退烧板块 ×0.5 | 读取 `heat_tracking` 表板块生命周期，通过 `SECTOR_NAME_MAP` 映射股票行业→板块 |
| W2. 多维共振加成 | 命中 ≥4维 → 额外 +20；命中 ≥5维 → 额外 +35 | 鼓励多维度共振标的 |

#### 4.3.3 过滤层（1层）

| 过滤项 | 规则 |
|--------|------|
| F1. 周期退烧排除 | 候选股所属板块处于"退烧"状态 → 默认排除（可配置 `--keep-decline` 仅警示保留⚠️） |
| F2. ST/退市风险排除 | ST / *ST / 退市风险警示股直接排除 |

#### 4.3.4 候选门槛

| 条件 | 阈值 |
|------|------|
| 最低共振维度 | ≥2 维命中，且核心维度至少命中 1 个 |
| 最低评分 | 加权后 ≥30 分 |
| Top N | 默认 5 只 |

#### 4.3.5 打分流水线

```
全市场扫描 → 7维数据读取
  ↓
逐股调用7个强度打分函数 → raw_score（基础分+加分）+ hit_count（命中维度数）+ core_hit
  ↓
候选门槛：hit_count≥2 且 core_hit=True → 否则丢弃
  ↓
加权层1：apply_sector_heat_weight(raw_score, lifecycle)   ← 高潮×1.2/退烧×0.5
  ↓
加权层2：apply_resonance_bonus(score, hit_count)           ← ≥4维+20 / ≥5维+35
  ↓
过滤层：should_exclude(code, name, lifecycle)              ← 退烧/ST排除
  ↓
评分门槛：final_score≥30 → 否则丢弃
  ↓
按 (score, resonance) 排序 → Top N 写入 gold_stock 表 + gold_stocks.json
```

**输出增强**：`gold_stocks.json` 每只金股携带 `raw_score` / `weighted_score` / `resonance`（命中维度数）/ `dimensions`（命中维度列表）/ `sector_lifecycle` / `strength_detail`（每维度 base/bonus/total 明细），便于前端与回测消费。

### 4.4 报告编排器（report_generator.py）

**三阶段编排**：

```
prepare(date, period)
  ├── data_collector.py    # 数据采集
  ├── insight_engine.py    # 洞见生成
  ├── gold_stock_discovery.py  # 金股发现（v5共振引擎）
  └── 组装 data/report_request.json  # 报告数据包

generate(date, period)
  ├── Agent模式（主路径）: TRAE内置最强模型撰写
  └── LLM API模式（降级）: generate_auto() 调用外部API

finalize(report_path, date, period)
  ├── validate_report.py     # 1.校验（10条红线）
  ├── report_quality_evaluator.py  # 2.评分
  ├── db.upsert_report()      # 3.写入DB
  ├── site_builder.py         # 4.刷新网站
  ├── push_feishu.py          # 5.推送飞书（v5：转飞书文档+消息卡片）
  └── learning_loop.py        # 6.触发学习（盘后）
```

### 4.5 学习闭环（learning_loop.py）

**6步盘后流程**：

```
run(db, date_str)
  ├── 1. verify_predictions    # 预判验证（历史洞见 vs 实际行情）
  ├── 2. solidify_experience   # 经验固化（因子命中率归纳）
  ├── 3. gold_stock_backtest   # 金股回测
  ├── 4. evolution_engine.run  # 进化引擎（六阶段闭环）    [v4新增]
  ├── 5. external_learner.run  # 外部学习（盘后复盘+盲区）  [v4新增]
  └── 6. knowledge_persistor   # 知识持久化（git push）     [v4新增]
```

### 4.6 进化引擎（evolution/engine.py）

**六阶段闭环**：

```
1.诊断(Diagnose)
  └── 读取learning_record → 按因子/置信度/市场状态分类统计 → 识别失败模式

2.假设(Hypothesize)
  ├── L1: 因子权重调整（"北向命中率75%但美股权重过高→北向15→20,美股10→5"）
  ├── L2: 规则补丁（"光模块锡粉匹配失败→研报实际用锡焊粉→SYNONYMS补入"）
  └── L3: 逻辑迭代（"单因子独立打分忽略共振→引入组合因子叠加"）

3.实验(Experiment)
  └── backtest_runner.py 历史回测：新规则 vs 旧规则，对比命中率

4.验证(Validate)
  ├── 样本量≥10个交易日
  ├── 改进幅度≥10%
  ├── 统计显著性 p<0.1（Fisher精确检验）
  ├── 分市场检验（牛/熊/震荡都有效）
  └── 防退化（新规则在旧数据上不退化）

5.部署(Deploy)
  ├── 验证通过 → 写入knowledge/目录
  ├── shadow模式运行3天（不影响生产）
  ├── shadow优于production → 切换
  └── 否则 → 回滚，记录失败假设

6.监控(Monitor)
  ├── 部署后每日跟踪命中率
  └── 连续3天退化>5% → 自动回滚
```

**三层进化**：

| 层次 | 改什么 | 方法 | 风险 | 收益 |
|------|--------|------|------|------|
| L1参数 | factor_weights.json权重/阈值 | 命中率统计+自动调整 | 低（可回滚） | 递减 |
| L2规则 | 匹配规则/排除策略/同义词表 | 失败模式分析+规则补丁 | 中（需回测） | 中等 |
| L3逻辑 | 洞见引擎算法/组合因子 | 范式设计+A/B对比+统计检验 | 高（需严格验证） | 高 |

### 4.7 外部学习器（evolution/external_learner.py）

**4种外部学习方式**：

1. **盘后复盘**：读取当日涨停板 → 逆推主线叙事 → 与系统预判对比
2. **信号盲区扫描**：次日涨停股 → 前日洞见是否提及 → 未提及则搜索催化事件
3. **外部观点对齐**：fxbaogao MCP搜索热门研报 → 提取机构核心逻辑 → 与系统对比
4. **模式发现**：聚类历史成功/失败案例 → 发现组合模式

### 4.8 知识持久化（evolution/knowledge_persistor.py）

**核心函数**：

| 函数 | 功能 |
|------|------|
| `persist_evolution_results()` | 将进化引擎结果写入knowledge/目录 |
| `persist_external_lessons()` | 将外部学习成果写入external_lessons.md |
| `persist_and_push()` | 主入口：写入知识文件 → git add → git commit → git push |
| `load_knowledge_for_agent()` | 为Agent提供结构化知识包（权重/基准/教训/变更日志） |

**跨会话继承机制**：
1. 每次盘后运行后，knowledge_persistor将进化成果写入knowledge/目录
2. 自动git commit + push到GitHub
3. 下次沙盒启动时，Agent首先读取SKILL.md（宪法），再读取knowledge/目录
4. Agent获得：因子权重表、准确率基线、历史教训、引擎变更日志
5. 实现"每次启动的Agent都继承已知的自我意识"

---

## 五、关键接口与工具

### 5.1 DB层接口（db.py）

**核心方法**：

```python
db = DB()
db.init()                          # 建表（幂等）
db.get_or_fetch(source, api_name,  # 缓存优先的数据获取
    fetcher, trade_date, params, ttl_hours)
db.upsert_insight(item)            # 写入洞见
db.upsert_gold_stock(item)         # 写入金股
db.upsert_learning_record(item)    # 写入学习记录
db.upsert_moneyflow(items)         # 写入主力资金流向（v5新增）
db.upsert_heat_tracking(items)     # 写入板块热度追踪
db.query_insights(date=...)        # 查询洞见
db.query_moneyflow(date=...)       # 查询主力资金（v5新增）
db.query_heat_tracking(date=...)   # 查询板块热度
db.query_resonance(date=...)       # 共振分析查询
```

**20张表**（v5新增 `moneyflow`）：raw_cache / index_quote / sector_moneyflow / limit_up / dragon_tiger / north_money / margin / cls_telegraph / cls_telegraph_stock / cls_vip_article / vip_discovered_stock / gold_stock / market_insight / report / qian_sanqiang_result / heat_tracking / learning_record / website_snapshot / calendar_event / **moneyflow**（v5新增）

### 5.2 定时任务接口

v5 任务体系由 v3.2 的 6 个旧任务重建为 **10 个 v5 任务**（详见第九节升级记录），分 A 类（洞见生成类，观澜踏浪纪）与 B 类（数据采集分析类）：

| 时间 | 任务 | 类型 | 触发内容 / 命令 |
|------|------|------|------|
| 08:30（工作日） | 观澜踏浪纪-盘前 | A | `report_generator.py --period morning` 全流程（采集→洞见→金股→Agent写报告→入库→转飞书文档→发消息） |
| 12:30（工作日） | 观澜踏浪纪-盘中 | A | `report_generator.py --period noon` 全流程 |
| 16:00（工作日） | 观澜踏浪纪-盘后 | A | `report_generator.py --period evening` 全流程 + 盘后学习闭环 |
| 11:50（工作日） | 钱三强选股-盘中 | B | `python data_collector.py --period qian_sanqiang` |
| 15:30（工作日） | 钱三强选股-盘后 | B | `python data_collector.py --period qian_sanqiang` |
| 15:25（工作日） | 板块热度追踪 | B | `python heat_tracker.py --export`（写入 heat_tracking 表）★v5新增 |
| 每2小时 | 财联社增量采集 | B | `python cls_collector.py --poll --interval 900 --duration 3300` |
| 财联社后30分钟 | VIP股票发现 | B | `python cls_collector.py --discover-vip` ★v5新增 |
| 20:00（周六） | 观澜踏浪纪-周报 | A | `report_generator.py --period weekly_sat` |
| 20:00（周日） | 观澜踏浪纪-周报 | A | `report_generator.py --period weekly_sun` |

> 热度追踪任务安排在 15:25，先于 15:30 的钱三强选股与 16:00 的盘后金股发现，确保 `heat_tracking` 表已写入板块生命周期，金股引擎可读取并执行热度加权与退烧过滤。

### 5.3 MCP工具集成

| MCP Server | 用途 | 调用方式 |
|------------|------|----------|
| mcp_tushareMcp | Tushare全市场数据 | `run_mcp("mcp_tushareMcp", "stock_basic", {...})` |
| mcp_fxbaogao | 发现报告研报搜索 | `run_mcp("mcp_fxbaogao", "search_reports", {...})` |
| integrated_browser | 浏览器自动化 | 用于网页数据抓取 |

### 5.4 配置管理（settings.py）

- **Tushare Token**：环境变量 `TUSHARE_TOKEN` 或 `config.json`
- **飞书Webhook**：环境变量 `FEISHU_WEBHOOK` 或 `config.json`
- **飞书App**：`FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_CHAT_ID`
- **飞书文档文件夹**：`XJm7f2TlGliK0fdXCPLctUIpnMg`（「观澜踏浪项目」文件夹，v5新增）
- **网站URL**：默认 `https://kwjian-longzer.github.io/stock-assistant/`

敏感信息存储在 `config.json`（.gitignore排除），确保仓库可安全公开。

---

## 六、开发、部署、运维与自我迭代机制

### 6.1 开发机制

**开发环境**：TRAE Work沙盒环境，Python 3.x，SQLite，无外部数据库依赖

**代码管理**：GitHub仓库 `kwjian-longzer/stock-assistant`，所有代码和知识文件版本控制

**开发流程**：
1. 需求分析 → 设计文档（如UPGRADE_PLAN_v5.md）
2. 编码实现 → 语法验证（py_compile）
3. 端到端测试 → 验证输出正确性
4. Git commit + push → 代码同步
5. 运行验证 → 确认生产流程可用

### 6.2 部署机制

**GitHub Pages部署**：
1. `site_builder.py` 将报告MD转为JSON，写入 `docs/data/`
2. `git push` 后GitHub Pages自动部署
3. 前端通过静态JSON + REST API双通道获取数据

**飞书文档部署（v5新增）**：
1. 报告 `finalize` 阶段触发 `push_feishu.create_feishu_doc()`
2. 调用 `lark-cli drive +import --type docx` 将 Markdown 报告转为飞书在线文档
3. 文档放入「观澜踏浪项目」飞书文件夹（token: `XJm7f2TlGliK0fdXCPLctUIpnMg`）
4. 通过 `send_feishu_message_with_doc()` 发送消息卡片（含文档链接 + 网站链接 + 洞见摘要）
5. `lark-cli` 未安装时自动降级为仅 Webhook 卡片推送，不阻断流程

**定时任务部署**：
- TRAE Work Schedule定时任务触发报告生成（v5重建为10个任务）
- 财联社采集每2小时触发一次
- 盘后学习由报告finalize自动触发

**API服务部署**：
- `api_server.py` 作为后台服务运行在 `0.0.0.0:8765`
- 前端通过相对路径 `/api/...` 调用

### 6.3 运维机制

**监控点**：
- DB文件大小监控（`data/stock.db`）
- 报告质量评分趋势（`report_score`表）
- 预判准确率趋势（`accuracy_benchmark.json`）
- 进化引擎运行日志（stdout输出）
- git push 成功/失败日志
- **`heat_tracking` 表监控（v5新增）**：每日 15:25 后检查板块热度是否正常入库（非空 + 板块数≥10），空表则告警，避免金股引擎因缺热度数据退化为无加权模式

**故障恢复**：
- 每个组件try/except包裹，局部失败不阻断整体
- get_or_fetch缓存优先，API故障时使用缓存数据
- v4降级v3：vip_search_v4不可用时自动回退到v3搜索
- 进化引擎shadow模式：新规则先在shadow环境验证，不影响生产
- **金股引擎降级**（v5）：`heat_tracking` 表为空时跳过板块热度加权，仅按7维原始分排序，不阻断选股

### 6.4 自我迭代升级机制

**六阶段进化闭环**（详见4.6节）：
```
诊断 → 假设 → 实验 → 验证 → 部署 → 监控
                                         ↓
                               退化 → 自动回滚 → 重新诊断
```

**三层进化**：
- L1参数调优：自动调整factor_weights.json中的权重
- L2规则迭代：更新匹配规则/同义词表/排除策略
- L3逻辑迭代：洞见引擎算法/组合因子/自适应阈值

**知识持久化与继承**：
1. 进化成果写入 `knowledge/` 目录（JSON + MD）
2. `knowledge_persistor.py` 自动 git commit + push
3. 新会话Agent读取 `knowledge/` 继承全部历史经验
4. 实现"每次运行后系统变得更强"的OpenClaw式进化

---

## 七、启动、运行环境与Agent自我意识继承

### 7.1 运行环境

| 组件 | 要求 |
|------|------|
| Python | 3.8+ |
| 依赖 | tushare, requests（标准库优先） |
| 数据库 | SQLite（内置，无需安装，20表 + get_or_fetch缓存） |
| 外部API | Tushare Pro Token（必需）、飞书Webhook（可选） |
| MCP | mcp_tushareMcp, mcp_fxbaogao, integrated_browser |
| lark-cli | 飞书文档集成（v5，可选，未安装时降级Webhook） |
| 托管 | GitHub Pages（免费） |

### 7.2 启动流程

**首次启动**：
```bash
# 1. 克隆仓库
git clone https://github.com/kwjian-longzer/stock-assistant.git
cd stock-assistant

# 2. 配置
cp config.example.json config.json  # 填入Tushare Token等

# 3. 初始化数据库
python -c "from db import DB; DB().init()"

# 4. 采集数据
python data_collector.py --date $(date +%Y-%m-%d)

# 5. 生成洞见
python insight_engine.py --date $(date +%Y-%m-%d) --period morning

# 6. 生成报告
python report_generator.py --period morning --auto

# 7. 启动API服务
python api_server.py --port 8765 &
```

**日常运行**：由TRAE Work定时任务自动触发，无需手动干预。

### 7.3 Agent自我意识继承机制

**核心问题**：每次沙盒中启动的Agent是一个全新的实例，没有前一次会话的记忆。如何让它继承历史经验？

**解决方案**：

```
┌─────────────────────────────────────────────────────┐
│  会话 N (今天)                                       │
│  1. Agent启动                                        │
│  2. 读取 SKILL.md (宪法/红线规则)                    │
│  3. 读取 knowledge/ 目录:                            │
│     ├── factor_weights.json  → 当前最优因子权重      │
│     ├── accuracy_benchmark   → 历史预判准确率基线     │
│     ├── lessons_learned.md  → 历史失败案例与教训     │
│     ├── engine_changelog.md → 引擎迭代变更日志       │
│     └── external_lessons.md → 外部学习成果          │
│  4. 基于继承的知识执行任务                           │
│  5. 盘后运行进化引擎                                 │
│  6. 更新knowledge/ + git push                       │
│                                                     │
│  ┌─────────────────────────────────────────────┐    │
│  │  knowledge_persistor.load_knowledge_for_agent()│   │
│  │  返回结构化知识包:                            │    │
│  │  {                                          │    │
│  │    "weights": {...},       # 因子权重       │    │
│  │    "benchmark": {...},     # 准确率基线     │    │
│  │    "lessons": [...],       # 失败教训       │    │
│  │    "changelog": [...],     # 引擎变更       │    │
│  │    "failed_hypotheses": [...] # 避免重复尝试 │    │
│  │  }                                          │    │
│  └─────────────────────────────────────────────┘    │
└──────────────────────┬──────────────────────────────┘
                       │ git push
                       ▼
┌─────────────────────────────────────────────────────┐
│  GitHub (知识持久化层)                               │
│  knowledge/ 目录 = Agent的"长期记忆"                 │
└──────────────────────┬──────────────────────────────┘
                       │ git pull (自动)
                       ▼
┌─────────────────────────────────────────────────────┐
│  会话 N+1 (明天)                                     │
│  新Agent启动 → 读取knowledge/ → 继承全部历史经验     │
│  "站在昨天的自己肩膀上"                              │
└─────────────────────────────────────────────────────┘
```

**关键设计**：
- `knowledge/` 目录是Agent的"长期记忆"，通过GitHub持久化
- `SKILL.md` 是"宪法"，定义红线规则和历史教训
- `knowledge_persistor.load_knowledge_for_agent()` 提供结构化知识包
- 进化引擎的 `failed_hypotheses.md` 避免重复尝试已证伪的假设
- `deploy_state.json` 跟踪shadow/production部署状态

---

## 八、开发思路、过程、问题与经验

### 8.1 开发思路

**从脚本到系统**：v3.2是"脚本拼凑"（各自调API→JSON文件），v4.0升级为"系统工程"（数据库驱动 + 信号驱动推理链），v5.0进一步升级为"全链路自动化 + 共振引擎重构 + 闭环补全"。

**核心设计决策**：
1. **DB中心化**：所有数据流经SQLite，避免文件I/O竞争和格式不一致
2. **三阶段编排**：prepare→generate→finalize，每个阶段独立可测试
3. **Agent为主路径**：TRAE内置最强模型撰写报告，外部LLM API仅作降级
4. **OpenClaw式进化**：学习成果持久化到GitHub，实现跨会话继承
5. **MCP双模式**：Agent模式（MCP传入数据）和独立模式（HTTP降级），兼顾性能和可用性
6. **闭环优先（v5）**：算法实现后必须补全 DB 写入与定时任务，杜绝"接口先行、实现缺失"

### 8.2 开发过程

**v3.2 → v4.0 演进**：
1. 数据库重构：12表→19表，新增9张业务表，全量填充
2. 采集层重构：独立3时点定时 + CLS每小时
3. 洞见引擎：从独立脚本升级为DB集成，7维度分析
4. 金股发现：新增5维共振交叉验证
5. 报告编排：5个独立任务 → 1个多时点编排器
6. 学习闭环：新增盘后验证+经验固化
7. 进化引擎：六阶段闭环 + 外部学习 + 知识持久化

**v4.0 → v5.0 演进**：
1. 数据库扩展：19表→20表，新增 `moneyflow` 主力资金流向表
2. 共振引擎重构：5维→7维（新增主力资金流入维度），权重重排（研报10→40），打分方式从固定加分升级为强度差异化打分
3. 板块热度闭环：补全 `heat_tracker.py` 的 `write_heat_to_db()`，新增 15:25 定时任务，金股引擎接入热度加权与退烧过滤
4. 飞书文档集成：`push_feishu.py` 新增 `create_feishu_doc()` + `send_feishu_message_with_doc()`
5. 网站首页改版：三栏布局（观澜洞见/闲看潮涌/踏浪分金）+ 新增 2 个 API 端点
6. 任务体系重建：6 个旧 v3.2 任务 → 10 个 v5 新任务

### 8.3 发现的Bug与修复过程

**v4 审计**：本轮审计发现10个Bug（1个误判已撤回），全部修复：

| Bug# | 级别 | 文件 | 问题 | 修复方案 |
|------|------|------|------|----------|
| #1 | P0 | cls_collector.py | v3 discover_stocks_for_article未接入v4 | 优先调用v4多源搜索，降级v3 |
| #3 | P0 | report_generator.py | 校验key用"pass"但validate返回"valid" | 改为result.get("valid", False) |
| #4 | P1 | vip_search_v4.py | check_exclusion_v4降权后未重算total_score | 惩罚后重新计算total_score |
| #5 | P1 | vip_extractor.py | v4 wrapper未传related_stock→market_filter | 传递market_filter=related_stock |
| #6 | P1 | heat_tracker.py | 涨停判定用`\|`导致19.5%条件被9.8%覆盖 | 按板块代码区分阈值 |
| #7 | P1 | data_summary.json | v3/v4潜在冲突 | 验证：insight_engine为唯一写入者，无冲突 |
| #8 | P2 | vip_search_v4.py | discover_stocks_v4未写DB | 由Bug#1修复附带解决（cls_collector写DB） |
| #9 | P2 | push_feishu.py | site_builder被重复调用 | 去除push_feishu中的重复调用 |
| #10 | P2 | gold_stock_discovery.py | JSON写入原始candidate缺buy_range | 改为写入enriched item |
| #12 | P3 | api_server.py | log_message中args[0]/args[1]可能越界 | 增加len检查 |

**v5 修复项**（针对 UPGRADE_PLAN_v5 审查发现的 P1/P2/P3 三大问题）：

| Bug# | 级别 | 文件/模块 | 问题 | 修复方案 |
|------|------|-----------|------|----------|
| v5-1 | P0 | heat_tracker.py | `heat_tracking` DB表始终为空（`upsert_heat_tracking` 全项目无人调用） | 新增 `write_heat_to_db()`，在 `export_heat_data_json()` 末尾调用，写入板块热度+生命周期 |
| v5-2 | P0 | gold_stock_discovery.py | 5维权重倒挂（研报最低10应为最高）+ 无强度打分 + 无板块热度/退烧过滤 | 全面重构为7维共振引擎v5（权重重排+强度差异化打分+加权层+过滤层） |
| v5-3 | P1 | db.py | 缺主力资金数据源 | 新增 `moneyflow` 表 + `upsert_moneyflow()` / `query_moneyflow()` |
| v5-4 | P1 | data_collector.py | 钱三强无定时任务、选股结果过期 | 新增 `--period qian_sanqiang` 定时调用（盘中11:50+盘后15:30） |
| v5-5 | P1 | push_feishu.py | 仅发Webhook卡片，不创建飞书Docx | 新增 `create_feishu_doc()`（lark-cli drive +import）+ `send_feishu_message_with_doc()` |
| v5-6 | P1 | api_server.py | 缺首页三栏所需端点 | 新增 `/api/insights/latest` + `/api/gold-stocks/recent` |
| v5-7 | P1 | cls_collector.py | `--poll`每小时触发过频、VIP文章发现未独立 | `--poll` 改为每2小时 + 新增 `--discover-vip` 独立触发 |
| v5-8 | P1 | 定时任务体系 | 6个旧v3.2任务时点错误、缺热度/钱三强/VIP任务 | 重建为10个v5新任务（A类洞见+B类采集） |

**修复原则**：
- 每个修复都保留降级路径，确保可用性
- 修复后全部通过py_compile语法验证
- 修复代码内嵌注释标注Bug编号，便于追溯

### 8.4 关键经验总结

1. **DB优先设计**：所有数据流经SQLite，避免了文件I/O竞争和格式不一致问题。get_or_fetch缓存优先策略有效降低了API调用频率。

2. **降级策略至关重要**：v4不可用时自动降级v3，MCP不可用时降级HTTP API。确保系统在任何环境下都能运行。

3. **知识持久化是进化的基础**：仅靠SQLite存储学习结果无法跨会话继承。通过knowledge/目录 + git push，实现了Agent的"长期记忆"。

4. **进化闭环必须包含"改进"环节**：仅有验证和记录是不够的，必须将验证结果转化为对下次执行规则的修改，才能实现真正的进化。

5. **Agent为主路径的正确性**：使用TRAE内置模型作为主路径，外部LLM API仅作降级。这避免了外部API依赖和成本，同时利用了Agent的推理能力。

6. **统计显著性检验防过拟合**：进化引擎使用Fisher精确检验（p<0.1）+ 样本量≥10 + 改进幅度≥10%的三重门槛，有效防止了过拟合。

7. **Shadow部署降低风险**：新规则先在shadow模式运行3天，确认优于production后才切换，退化时自动回滚。

8. **集成检查清单机制（v5）**：每个新模块开发完成后，必须通过 6 项集成检查——①DB写入（是否有函数调用 `db.upsert_*()`）②定时任务（是否在 Schedule 中创建）③下游消费（是否被 gold_stock_discovery / report_generator 引用）④API暴露（是否在 api_server.py 有端点）⑤前端展示（是否在 app.js 有渲染函数）⑥端到端测试（从采集到展示的完整链路）。heat_tracker"算法已实现但 DB 表为空"的教训正是缺少此清单所致。

9. **热度追踪闭环（v5）**：算法实现后必须同步补全 DB 写入与定时任务，否则上游算得再准也无人消费。v5 通过 `write_heat_to_db()` + 15:25 定时任务 + 金股引擎读取 `heat_tracking`，将"独立模块"接入"采集→计算→入库→加权→过滤"完整闭环。

10. **飞书文档集成（v5）**：借助 `lark-cli drive +import --type docx` 实现报告自动转飞书在线文档，配合消息卡片推送文档链接，使报告从"网站查看"扩展为"飞书群直达"，提升触达率；lark-cli 未安装时自动降级为 Webhook 卡片，保证可用性。

---

## 九、v5.0升级记录

本节记录 v4.0 → v5.0 的完整升级内容，对应 `UPGRADE_PLAN_v5.md` 审查发现的 P1（自动化任务未重组）、P2（共振选股逻辑缺陷）、P3（板块热度追踪未固化）三大问题。

### 9.1 升级项对照表

| 升级项 | 旧版（v4.0） | 新版（v5.0） |
|--------|------|------|
| 金股维度 | 5维 | 7维（新增主力资金流入） |
| 研报权重 | 10（最低） | 40（最高） |
| 打分方式 | 固定加分 | 强度差异化打分 |
| 板块热度 | 未接入 | 高潮×1.2 / 退烧×0.5 |
| 退烧过滤 | 无 | 退烧板块排除 + ST排除 |
| heat_tracking表 | 空表 | 补全DB写入（write_heat_to_db） |
| 定时任务 | 6个旧v3.2 | 10个v5新任务 |
| 飞书推送 | 仅Webhook卡片 | 飞书文档+消息卡片 |
| 网站首页 | 今日看板 | 观澜洞见/闲看潮涌/踏浪分金（三栏） |
| DB表数 | 19表 | 20表（新增moneyflow） |
| API路由 | 15个 | 17个（新增insights/latest + gold-stocks/recent） |

### 9.2 升级要点说明

1. **7维共振金股发现引擎**：新增"主力资金流入强度"维度（Tushare `moneyflow` 接口 → DB `moneyflow` 表），并引入板块热度加权层（高潮×1.2/崛起×1.0/退烧×0.5）与多维共振加成层（≥4维+20 / ≥5维+35），过滤层排除退烧板块与ST股。候选门槛：≥2维命中（核心至少1个）+ 评分≥30。

2. **热度追踪闭环**：`heat_tracker.py` 新增 `write_heat_to_db()`，在 `export_heat_data_json()` 末尾将板块热度+生命周期写入 `heat_tracking` 表；新增 15:25 盘后定时任务触发 `heat_tracker.py --export`，确保金股引擎（16:00 盘后）可读取最新板块生命周期执行加权与过滤。

3. **飞书文档集成**：`push_feishu.py` 新增 `create_feishu_doc()`（`lark-cli drive +import --type docx`）与 `send_feishu_message_with_doc()`，报告 finalize 阶段自动转飞书在线文档并推送消息卡片；lark-cli 缺失时降级 Webhook。

4. **网站首页三栏**：`docs/index.html` 重构为观澜洞见/闲看潮涌/踏浪分金三栏，`app.js` 新增 `renderInsights()` / `renderMarketDashboard()` / `renderGoldStocksTable()`，对应新增 `/api/insights/latest` 与 `/api/gold-stocks/recent` 端点。

5. **任务体系重建**：6 个旧 v3.2 任务（时点错误、走旧流程）重建为 10 个 v5 新任务，分 A 类洞见生成（观澜踏浪纪盘前/盘中/盘后 + 周六/周日周报）与 B 类数据采集（财联社每2小时、VIP股票发现、钱三强盘中/盘后、板块热度追踪）。

### 9.3 v5 新增/修改文件清单

| 文件 | 改动内容 |
|------|----------|
| `gold_stock_discovery.py` | 全面重构：7维 + 2加权层 + 1过滤层 + 强度打分函数 |
| `heat_tracker.py` | 新增 `write_heat_to_db()`，补全 heat_tracking DB写入 |
| `db.py` | 新增 `moneyflow` 表 + `upsert_moneyflow()` / `query_moneyflow()` |
| `data_collector.py` | 新增主力资金采集 + `--period qian_sanqiang` |
| `push_feishu.py` | 新增 `create_feishu_doc()` + `send_feishu_message_with_doc()` |
| `api_server.py` | 新增 `/api/insights/latest` + `/api/gold-stocks/recent`（17端点） |
| `cls_collector.py` | `--poll` 改2小时 + 新增 `--discover-vip` |
| `docs/index.html` | 首页三栏布局重构 |
| `docs/assets/app.js` | 新增三栏渲染函数 |
| `docs/assets/styles.css` | 三栏样式 |
| `PROJECT_ENGINEERING_DOC.md` | 更新v5架构说明（本文件） |

---

## 十、未来展望

1. **L3逻辑迭代深化**：引入组合因子检测（如北向+涨停共振时额外加分），自适应阈值调整
2. **前端增强**：实时数据推送（WebSocket），交互式图表，移动端适配，潮汐波浪式板块热度可视化
3. **多市场扩展**：港股、美股市场数据接入和跨市场关联分析
4. **Agent协同**：多个专项Agent协同工作（数据采集Agent / 分析Agent / 报告撰写Agent）
5. **知识图谱**：将lessons_learned结构化为知识图谱，支持语义查询和自动推理
6. **共振引擎持续进化**：基于金股回测结果动态调整7维权重与加权/过滤参数（接入进化引擎L1参数调优）

---

> 本文档由「观澜踏浪」项目团队于 2026-06-28 编制，作为 v5.0 阶段性工程总结。
> 仓库地址：https://github.com/kwjian-longzer/stock-assistant
