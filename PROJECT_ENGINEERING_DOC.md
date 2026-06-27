# 观澜踏浪 — 工程白皮书 v4.0

> **本文档是「观澜踏浪」项目的阶段性工程总结，涵盖项目宪法、架构设计、开发历程、Bug修复记录与自我进化机制。**
> 版本：v4.0（含进化引擎） ｜ 日期：2026-06-28 ｜ 仓库：github.com/kwjian-longzer/stock-assistant

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
│  docs/ — 观澜看板 | 踏浪表单 | 数据页 | 历史回溯          │
├─────────────────────────────────────────────────────────┤
│  Layer 3: 后端服务层                                      │
│  api_server.py — 15个REST端点，HTTP:8765                 │
├─────────────────────────────────────────────────────────┤
│  Layer 2: 分析引擎层                                      │
│  insight_engine.py | report_generator.py |              │
│  gold_stock_discovery.py | vip_search_v4.py |            │
│  heat_tracker.py | qian_sanqiang_selector.py             │
├─────────────────────────────────────────────────────────┤
│  Layer 1: 数据采集层                                      │
│  data_collector.py (3时点) | cls_collector.py (每小时)   │
├─────────────────────────────────────────────────────────┤
│  Foundation: 数据库层                                     │
│  db.py — SQLite 19表 + get_or_fetch缓存                  │
└─────────────────────────────────────────────────────────┘
```

### 2.2 完整目录结构

```
stock-assistant/
├── SKILL.md                    # 项目宪法 — 红线规则、历史教训（Agent首先必读）
├── PROJECT_ENGINEERING_DOC.md  # 本文件 — 工程白皮书
├── analysis_prompt.md          # 深度分析提示词 v4 — 报告写作规范
├── settings.py                 # 统一配置管理（环境变量/config.json）
│
├── db.py                       # SQLite数据库层（19表 + get_or_fetch缓存）
├── data_collector.py           # 三时点数据采集（Sina+Tushare+东财）
├── cls_collector.py            # 财联社电报采集（每小时 + VIP文章 + v4股票发现）
├── insight_engine.py           # 洞见引擎（7维度分析 → market_insight + data_summary）
├── gold_stock_discovery.py     # 金股共振发现（5维交叉 → gold_stock）
├── vip_search_v4.py            # v4多源VIP股票发现（东财+Web+研报+CLS）
├── vip_extractor.py            # VIP信息提取器（v4兼容入口）
├── heat_tracker.py             # 板块热度追踪器
├── qian_sanqiang_selector.py   # 钱三强选股器
├── report_generator.py         # 报告编排器（prepare→generate→finalize）
├── validate_report.py          # 报告校验器（10条红线校验）
├── report_quality_evaluator.py # 报告质量评分器
├── site_builder.py             # 网站数据生成器（MD→JSON→GitHub Pages）
├── push_feishu.py              # 飞书推送器（Webhook卡片+链接）
├── api_server.py               # REST API服务（15端点，:8765）
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
│   ├── index.html             # 主页面（SPA）
│   ├── assets/                # 前端资源
│   │   ├── app.js             # 应用逻辑
│   │   ├── charts.js          # 图表（ECharts）
│   │   └── styles.css         # 样式
│   └── data/                  # 网站数据（site_builder生成）
│       ├── latest.json        # 最新报告数据
│       ├── manifest.json      # 报告清单
│       └── archive/           # 历史报告归档
│
├── data/                       # 运行时数据
│   ├── stock.db               # SQLite数据库（19表）
│   ├── data_summary.json      # 洞见引擎产出的数据摘要
│   ├── gold_stocks.json       # 金股JSON
│   └── cls_telegraph_archive/ # 电报归档
│
└── templates/                  # 模板
    └── html_report/           # HTML报告模板
```

---

## 三、网站架构

### 3.1 前端（docs/）

**技术栈**：原生HTML + JavaScript + ECharts图表库 + CSS3

**页面结构**：
- **观澜看板**：最新报告全文展示，支持Markdown渲染
- **踏浪表单**：金股推荐表，含买入区间/目标价/止损位
- **数据页**：指数行情、板块资金、龙虎榜、涨停池数据可视化
- **历史回溯**：日历选择器，按日期回溯历史报告
- **时间前瞻**：未来重要事件时间线

**数据来源**：前端通过 `fetch('/api/...')` 调用后端REST API获取实时数据，同时 `docs/data/` 下的静态JSON作为GitHub Pages降级方案。

**部署方式**：GitHub Pages自动部署。`site_builder.py` 将报告转为JSON写入 `docs/data/`，git push后GitHub Pages自动更新。

### 3.2 后端（api_server.py）

**技术栈**：Python `http.server`（轻量HTTP服务，无需额外依赖）

**15个REST端点**：

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

### 4.3 金股共振发现（gold_stock_discovery.py）

**功能**：跨5维DB表交叉验证，发现多维共振金股

**5个维度**：

| 维度 | 权重 | 数据源 |
|------|------|--------|
| 涨停 | 25 | limit_up表 |
| 龙虎榜 | 25 | dragon_tiger表 |
| 北向资金 | 20 | north_money表 |
| 舆情 | 15 | cls_telegraph表 |
| 研报 | 15 | vip_discovered_stock表 |

**流程**：全市场扫描 → 5维命中 → ≥2维共振纳入候选 → 按评分排序 → Top N写入gold_stock表 + gold_stocks.json

### 4.4 报告编排器（report_generator.py）

**三阶段编排**：

```
prepare(date, period)
  ├── data_collector.py    # 数据采集
  ├── insight_engine.py    # 洞见生成
  ├── gold_stock_discovery.py  # 金股发现
  └── 组装 data/report_request.json  # 报告数据包

generate(date, period)
  ├── Agent模式（主路径）: TRAE内置最强模型撰写
  └── LLM API模式（降级）: generate_auto() 调用外部API

finalize(report_path, date, period)
  ├── validate_report.py     # 1.校验（10条红线）
  ├── report_quality_evaluator.py  # 2.评分
  ├── db.upsert_report()      # 3.写入DB
  ├── site_builder.py         # 4.刷新网站
  ├── push_feishu.py          # 5.推送飞书
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
db.query_insights(date=...)        # 查询洞见
db.query_resonance(date=...)       # 共振分析查询
```

**19张表**：raw_cache / index_quote / sector_moneyflow / limit_up / dragon_tiger / north_money / margin / cls_telegraph / cls_telegraph_stock / vip_article / vip_discovered_stock / gold_stock / market_insight / report / qian_sanqiang / sector_heat / learning_record / website_snapshot / report_score

### 5.2 定时任务接口

| 时间 | 任务 | 命令 |
|------|------|------|
| 08:30 | 晨报采集+生成 | `python report_generator.py --period morning --auto` |
| 11:50 | 午报采集+生成 | `python report_generator.py --period noon --auto` |
| 15:30 | 晚报采集+生成 | `python report_generator.py --period evening --auto` |
| 每小时 | 财联社电报采集 | `python cls_collector.py` |
| 盘后 | 学习闭环 | `python learning_loop.py` (由finalize自动触发) |
| 周末 | 周报生成 | `python report_generator.py --period weekly_sat/sun --auto` |

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
- **网站URL**：默认 `https://kwjian-longzer.github.io/stock-assistant/`

敏感信息存储在 `config.json`（.gitignore排除），确保仓库可安全公开。

---

## 六、开发、部署、运维与自我迭代机制

### 6.1 开发机制

**开发环境**：TRAE Work沙盒环境，Python 3.x，SQLite，无外部数据库依赖

**代码管理**：GitHub仓库 `kwjian-longzer/stock-assistant`，所有代码和知识文件版本控制

**开发流程**：
1. 需求分析 → 设计文档（如PROJECT_PLAN_v4.md）
2. 编码实现 → 语法验证（py_compile）
3. 端到端测试 → 验证输出正确性
4. Git commit + push → 代码同步
5. 运行验证 → 确认生产流程可用

### 6.2 部署机制

**GitHub Pages部署**：
1. `site_builder.py` 将报告MD转为JSON，写入 `docs/data/`
2. `git push` 后GitHub Pages自动部署
3. 前端通过静态JSON + REST API双通道获取数据

**定时任务部署**：
- TRAE Work Schedule定时任务触发报告生成
- 每小时电报采集通过独立定时任务
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

**故障恢复**：
- 每个组件try/except包裹，局部失败不阻断整体
- get_or_fetch缓存优先，API故障时使用缓存数据
- v4降级v3：vip_search_v4不可用时自动回退到v3搜索
- 进化引擎shadow模式：新规则先在shadow环境验证，不影响生产

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
| 数据库 | SQLite（内置，无需安装） |
| 外部API | Tushare Pro Token（必需）、飞书Webhook（可选） |
| MCP | mcp_tushareMcp, mcp_fxbaogao, integrated_browser |
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

**从脚本到系统**：v3.2是"脚本拼凑"（各自调API→JSON文件），v4.0升级为"系统工程"（数据库驱动 + 信号驱动推理链）。

**核心设计决策**：
1. **DB中心化**：所有数据流经SQLite，避免文件I/O竞争和格式不一致
2. **三阶段编排**：prepare→generate→finalize，每个阶段独立可测试
3. **Agent为主路径**：TRAE内置最强模型撰写报告，外部LLM API仅作降级
4. **OpenClaw式进化**：学习成果持久化到GitHub，实现跨会话继承
5. **MCP双模式**：Agent模式（MCP传入数据）和独立模式（HTTP降级），兼顾性能和可用性

### 8.2 开发过程

**v3.2 → v4.0 演进**：
1. 数据库重构：12表→19表，新增9张业务表，全量填充
2. 采集层重构：独立3时点定时 + CLS每小时
3. 洞见引擎：从独立脚本升级为DB集成，7维度分析
4. 金股发现：新增5维共振交叉验证
5. 报告编排：5个独立任务 → 1个多时点编排器
6. 学习闭环：新增盘后验证+经验固化
7. 进化引擎：六阶段闭环 + 外部学习 + 知识持久化

**v4 VIP股票发现**：
1. v3：Tushare主营业务 + fxbaogao两层搜索
2. v4：新增东财公告API + WebSearch + CLS电报 + 加权验证 + 排除逻辑
3. MCP双模式：Agent传入MCP数据 vs HTTP API降级

### 8.3 发现的Bug与修复过程

本轮审计发现10个Bug（1个误判已撤回），全部修复：

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

**修复原则**：
- 每个修复都保留v3降级路径，确保可用性
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

---

## 九、未来展望

1. **L3逻辑迭代深化**：引入组合因子检测（如北向+涨停共振时额外加分），自适应阈值调整
2. **前端增强**：实时数据推送（WebSocket），交互式图表，移动端适配
3. **多市场扩展**：港股、美股市场数据接入和跨市场关联分析
4. **Agent协同**：多个专项Agent协同工作（数据采集Agent / 分析Agent / 报告撰写Agent）
5. **知识图谱**：将lessons_learned结构化为知识图谱，支持语义查询和自动推理

---

> 本文档由「观澜踏浪」项目团队于 2026-06-28 编制，作为 v4.0 阶段性工程总结。
> 仓库地址：https://github.com/kwjian-longzer/stock-assistant
