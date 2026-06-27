# 观澜踏浪 v5.0 升级计划书

> 编制时间：2026-06-28 ｜ 版本：v5.0 ｜ 状态：**待审查**
> 审查通过后方可执行

---

## 〇、执行摘要

### 审查发现的核心问题

通过对全部代码库的逐文件审查、模拟每个定时任务的业务流程、对照设计文档，确认以下三个重大问题：

| 编号 | 问题 | 严重度 | 现状 |
|------|------|--------|------|
| P1 | 自动化任务未按新架构重组 | **严重** | 6个旧v3.2任务在运行，v4.0任务未部署，时点全部错误 |
| P2 | 共振选股逻辑严重缺陷 | **严重** | 维度缺失、权重倒挂、无强度打分、无板块热度/退烧过滤 |
| P3 | 板块热度追踪代码未固化 | **中等** | 算法已实现但DB表为空、Guanlan项目可视化未整合 |

### 升级目标

将系统从"代码已写但未接入生产"升级为"全链路自动化运行 + 共振引擎重构 + 热度追踪闭环"。

---

## 一、问题 P1：自动化任务未按新架构重组

### 1.1 现状审查（模拟每个任务触发流程）

#### 当前6个定时任务（全部是v3.2架构）

| 任务 | cron | 触发内容 | 问题 |
|------|------|----------|------|
| 财联社数据采集 | `0 * * * *`（每小时） | `python cls_collector.py` | 频率应为每2小时；当前--poll模式内部循环55分钟 |
| 早盘舆情分析 | `0 6 * * 1-5` | Agent写报告（v3.2流程） | 时点错误：应为08:30；走v3.2而非v4.0流程 |
| 午盘舆情分析 | `30 11 * * 1-5` | Agent写报告（v3.2流程） | 时点错误：应为12:30；走v3.2而非v4.0流程 |
| 晚报舆情分析 | `0 20 * * 1-5` | Agent写报告（v3.2流程） | 时点错误：应为16:00；走v3.2而非v4.0流程 |
| 周报(周六) | `0 20 * * SAT` | Agent写报告 | 时点可保留 |
| 周报(周日) | `0 20 * * SUN` | Agent写报告 | 时点可保留 |

#### 关键缺失项

| 缺失 | 影响 |
|------|------|
| **insight_engine 无独立任务** | 洞见只在report_generator.prepare内部调用，无法独立触发 |
| **qian_sanqiang 无定时任务** | 选股结果不会自动更新，gold_stock_discovery读到的是过期数据 |
| **report_generator --auto 不可用** | 缺llm_api_key，generate_auto()直接返回False |
| **push_feishu 不转飞书文档** | 仅发Webhook卡片，不创建飞书Docx |
| **网站首页结构不符** | 无"观澜洞见/闲看潮涌/踏浪分金"三栏布局 |

### 1.2 升级方案：两类任务体系

#### A类：洞见生成类 — 观澜踏浪纪

**三个时点触发，每个时点执行完整流程：**

```
08:30 盘前观澜纪 → 采集 → 洞见引擎 → 金股发现 → Agent写报告 → 入库 → 转飞书文档 → 发飞书消息
12:30 盘中观澜纪 → 增量采集 → 洞见引擎更新 → 金股发现 → Agent写报告 → 入库 → 转飞书文档 → 发飞书消息
16:00 盘后观澜纪 → 全量采集 → 洞见引擎 → 金股发现 → Agent写报告 → 入库 → 学习闭环 → 转飞书文档 → 发飞书消息
```

**任务编排器重构（report_generator.py）：**

当前流程 `prepare → generate → finalize` 需要调整为：

```
prepare(date, period)
  ├── data_collector.py          # 数据采集（按period选择采集范围）
  ├── insight_engine.py          # 洞见引擎 → market_insight表 + data_summary.json
  ├── gold_stock_discovery.py    # 共振金股发现 → gold_stock表（v5重构版）
  └── 组装 data/report_request.json

generate(date, period)
  ├── Agent模式（主路径）：TRAE内置最强模型撰写报告
  └── 写入 reports/{date}_{type_cn}.md

finalize(report_path, date, period)
  ├── validate_report.py         # 校验
  ├── report_quality_evaluator.py # 评分
  ├── db.upsert_report()         # 写入DB
  ├── site_builder.py            # 刷新网站数据
  ├── push_feishu.py             # 推送（v5新增：转飞书文档 + 发消息）
  └── learning_loop.py           # 盘后触发学习闭环（仅evening）
```

**push_feishu.py 改造：**

新增飞书文档创建功能：
1. 调用 `lark-cli drive +import --type docx` 将报告MD转为飞书在线文档
2. 放入「观澜踏浪项目」飞书文件夹（token: `XJm7f2TlGliK0fdXCPLctUIpnMg`）
3. 通过Webhook发送飞书消息卡片（含文档链接 + 网站链接 + 洞见摘要）

**任务描述模板（Schedule message）：**

```
【观澜踏浪纪-盘前/盘中/盘后】执行v5报告生成全流程。
1. 运行 cd /workspace/stock-assistant && python report_generator.py --period morning/noon/evening --prepare
2. 使用TRAE内置最强模型，基于 data/report_request.json 撰写盘前/盘中/盘后报告
3. 报告完成后运行 python report_generator.py --period morning/noon/evening --finalize --report <路径>
4. finalize会自动完成：校验→评分→入库→刷网站→推飞书(文档+消息)
```

#### B类：数据采集分析类

| 任务 | cron | 命令 | 说明 |
|------|------|------|------|
| 财联社增量采集 | `0 */2 * * *`（每2小时） | `python cls_collector.py --poll --interval 900 --duration 3300` | 首页/电报/头条/投资日历/VIP |
| VIP股票发现 | 财联社采集后触发 | `python cls_collector.py --discover-vip` | 逐篇解析VIP文章→v4股票发现→入库 |
| 钱三强选股(盘中) | `50 11 * * 1-5` | `python data_collector.py --period qian_sanqiang` | 盘中选股 |
| 钱三强选股(盘后) | `30 15 * * 1-5` | `python data_collector.py --period qian_sanqiang` | 盘后选股 |

**cls_collector.py 改造：**

1. 新增 `--discover-vip` 参数：仅对新增VIP文章逐篇调用v4股票发现引擎
2. `--poll` 模式调整为每2小时触发一次（非每小时）
3. VIP文章解析后自动调用 `discover_stocks_for_article()` 并写DB

### 1.3 网站首页改版方案

#### 当前7页面布局 → 新3+4布局

**首页三大栏目（重构）：**

```
┌─────────────────────────────────────────────────────┐
│  观澜洞见（最优先展示）                                │
│  ┌─────────┬─────────┬─────────┐                   │
│  │ 盘前洞见 │ 盘中洞见 │ 盘后洞见 │  ← 三Tab切换      │
│  │ 08:30   │ 12:30   │ 16:00   │  ← 拉取DB最新洞见   │
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
│  │名称│代码│维度│评分│入库时间│  ← 增量金股表格       │
│  └────┴────┴────┴────┴────┘                       │
│  按入库时间倒序排列，展示近期增量金股                   │
└─────────────────────────────────────────────────────┘
```

**保留的子页面（侧边栏）：**
- 日报归档（已有，保留）
- 板块热度（已有，保留，v5增强）
- 金股追踪（已有，保留详情页）
- 财联社信源（已有，保留）
- 钱三强选股（已有，保留）
- 投资日历（已有，保留）

**实现方式：**
- `docs/index.html` 新增首页三栏布局结构
- `docs/assets/app.js` 新增 `renderInsights()` / `renderMarketDashboard()` / `renderGoldStocksTable()` 函数
- `docs/assets/styles.css` 新增三栏样式
- 首页数据通过 `/api/insights/latest` / `/api/index/{date}` / `/api/gold-stocks/recent` 获取

### 1.4 任务迁移清单

| 旧任务 | 操作 | 新任务 |
|--------|------|--------|
| 财联社每小时采集 | **更新**cron为每2小时 | 财联社每2小时增量采集 |
| 早盘舆情分析(06:00) | **删除**，新建 | 观澜踏浪纪-盘前(08:30) |
| 午盘舆情分析(11:30) | **删除**，新建 | 观澜踏浪纪-盘中(12:30) |
| 晚报舆情分析(20:00) | **删除**，新建 | 观澜踏浪纪-盘后(16:00) |
| 周报(周六) | **更新**message | 观澜踏浪纪-周报(周六) |
| 周报(周日) | **更新**message | 观澜踏浪纪-周报(周日) |
| 无 | **新建** | VIP股票发现（财联社后触发） |
| 无 | **新建** | 钱三强选股-盘中(11:50) |
| 无 | **新建** | 钱三强选股-盘后(15:30) |

---

## 二、问题 P2：共振选股逻辑严重缺陷

### 2.1 现状审查

#### 当前权重体系（gold_stock_discovery.py 第27-33行）

```python
WEIGHTS = {
    "qian_sanqiang": 35,    # 钱三强
    "dragon_tiger": 25,     # 龙虎榜
    "limit_up": 15,         # 涨停
    "cls_telegraph": 15,    # 舆情
    "vip_research": 10,     # 研报
}
```

#### 问题清单

| 问题 | 现状 | 应有 |
|------|------|------|
| 研报权重倒挂 | 最低(10) | **最高**（专业人员加持） |
| 钱三强权重 | 最高(35) | **第二**（量化筛选有效但非最终决策） |
| 缺主力资金流入强度 | 无此维度 | 应有（龙虎榜净额/金额加权） |
| 无板块热度加权 | heat_tracker未接入 | 退烧板块降权/排除，高潮板块加分 |
| 无周期退烧排除 | 无逻辑 | 应排除或警示 |
| 打分无强度差异 | 命中即固定加分 | 应按金额/次数/强度差异化 |

### 2.2 重构方案：共振金股发现引擎 v5

#### 新维度体系（7维 + 2加权 + 1过滤）

```
┌──────────────────────────────────────────────────────┐
│              共振金股发现引擎 v5                        │
│                                                      │
│  ┌────────── 核心维度（一票否决或高权重）──────────┐    │
│  │  1. 研报覆盖（权重40）  ← 专业加持，最高        │    │
│  │  2. 钱三强（权重30）   ← 量化三强合一          │    │
│  └──────────────────────────────────────────────┘    │
│                                                      │
│  ┌────────── 加分维度（强度差异化打分）──────────┐    │
│  │  3. 涨停动量（权重15）  ← 连板数/封单强度      │    │
│  │  4. 龙虎榜资金（权重15）← 净买入金额强度       │    │
│  │  5. 北向资金（权重10）  ← 净流入金额强度        │    │
│  │  6. 舆情催化（权重10）  ← 电报条数/红色标记数   │    │
│  │  7. 主力资金流入强度（权重15）← 新增维度        │    │
│  └──────────────────────────────────────────────┘    │
│                                                      │
│  ┌────────── 加权层 ──────────────────────────┐      │
│  │  W1. 板块热度加权：高潮板块×1.2，退烧板块×0.5│      │
│  │  W2. 多维共振加成：≥4维命中额外+20分         │      │
│  └─────────────────────────────────────────────┘      │
│                                                      │
│  ┌────────── 过滤层 ──────────────────────────┐      │
│  │  F1. 周期退烧排除：板块处于"退烧"状态→排除    │      │
│  │      或标记⚠️警示（可配置）                   │      │
│  │  F2. ST/退市风险股排除                        │      │
│  └─────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────┘
```

#### 新权重表

| 维度 | 基础权重 | 强度打分规则 | 说明 |
|------|----------|-------------|------|
| 研报覆盖 | 40 | 每篇研报+5，上限+20（总60） | 专业人员加持，权重最高 |
| 钱三强 | 30 | 三强全命中+10，两强命中+5 | 量化筛选基础分 |
| 涨停动量 | 15 | 连板数×5（1板5分/2板10分/3板+15分） | 动量加分 |
| 龙虎榜资金 | 15 | 净买入金额/亿×3（上限+15） | 强度差异化 |
| 北向资金 | 10 | 净流入/10亿×2（上限+10） | 强度差异化 |
| 舆情催化 | 10 | 电报条数×1 + 红色标记×3（上限+10） | 密度+烈度 |
| 主力资金流入 | 15 | (超大单净额+大单净额/2)/亿×2（上限+15） | 新增维度 |

#### 加权层

| 加权项 | 规则 |
|--------|------|
| 板块热度加权 | 候选股所属板块处于"高潮"→ 总分×1.2；"崛起"→ ×1.0；"退烧"→ ×0.5 |
| 多维共振加成 | 命中≥4维 → 额外+20分；命中≥5维 → 额外+35分 |

#### 过滤层

| 过滤项 | 规则 |
|--------|------|
| 周期退烧 | 板块"退烧"状态下：默认排除（可配置为仅警示⚠️） |
| ST/风险 | ST/*ST/退市风险警示股直接排除 |

#### 候选门槛

| 条件 | 阈值 |
|------|------|
| 最低共振维度 | ≥2维（核心维度至少命中1个） |
| 最低评分 | ≥30分 |
| Top N | 默认5只 |

### 2.3 实现方案

#### gold_stock_discovery.py 重构

```python
# 新权重表
WEIGHTS_V5 = {
    "vip_research": 40,       # 研报（最高）
    "qian_sanqiang": 30,      # 钱三强（第二）
    "main_capital_flow": 15,  # 主力资金流入强度（新增）
    "limit_up": 15,           # 涨停动量
    "dragon_tiger": 15,       # 龙虎榜资金
    "north_money": 10,        # 北向资金
    "cls_telegraph": 10,      # 舆情催化
}

# 强度打分函数
def score_research(stock, vip_data): # 研报篇数加权
def score_qian_sanqiang(stock, qsq_data): # 三强命中数加权
def score_main_capital(stock, moneyflow_data): # 主力资金净额加权（新增）
def score_limit_up(stock, limit_data): # 连板数加权
def score_dragon_tiger(stock, dt_data): # 净买入金额加权
def score_north_money(stock, nm_data): # 净流入金额加权
def score_cls_telegraph(stock, cls_data): # 电报条数+红色加权

# 加权层
def apply_sector_heat_weight(score, sector_lifecycle):
    # 高潮×1.2, 崛起×1.0, 退烧×0.5
def apply_resonance_bonus(score, dimension_count):
    # ≥4维+20, ≥5维+35

# 过滤层
def filter_stocks(candidate, sector_lifecycle):
    # 退烧板块排除, ST排除
```

#### 新增数据源接入

1. **主力资金流入**：Tushare `moneyflow` 接口（个股资金流向）
   - `db.py` 新增 `moneyflow` 表
   - `data_collector.py` 新增采集逻辑
   - `gold_stock_discovery.py` 新增 `score_main_capital()`

2. **板块热度接入**：
   - `heat_tracker.py` 新增 `write_heat_to_db()` 将热度数据写入 `heat_tracking` 表
   - `gold_stock_discovery.py` 读取 `heat_tracking` 表获取板块生命周期
   - `apply_sector_heat_weight()` 根据生命周期加权

3. **板块映射**：
   - 候选股的 `industry` 字段映射到 heat_tracker 的板块名
   - 新增 `SECTOR_NAME_MAP` 字典处理板块名差异

---

## 三、问题 P3：板块热度追踪代码未固化

### 3.1 现状审查

#### 已实现部分（代码存在但未完全接入）

| 组件 | 状态 | 文件 |
|------|------|------|
| 热度计算公式 | ✅ 已实现 | heat_tracker.py:199-225 `calculate_sector_heat()` |
| EMA平滑 | ✅ 已实现 | heat_tracker.py:228-246 `smooth_series(alpha=0.4)` |
| 5日累计资金流 | ✅ 已实现 | heat_tracker.py:249-265 `cumulative_sum(window=5)` |
| 动态板块选择 | ✅ 已实现 | heat_tracker.py:268-332 `select_dynamic_sectors()` |
| 生命周期判定 | ✅ 已实现 | heat_tracker.py:455-490 `_determine_lifecycle()` |
| 文本曲线图 | ✅ 已实现 | heat_tracker.py:493-572 `render_multi_sector_chart()` |
| JSON导出 | ✅ 已实现 | heat_tracker.py:696-749 `export_heat_data_json()` |
| ECharts前端图表 | ✅ 已实现 | docs/assets/charts.js:113-213 |
| 历史滚动存储 | ✅ 已实现 | site_builder.py:955-1041 `update_heat_history()` |
| 独立报告页 | ✅ 已实现 | templates/html_report/sector-heat-tracker.html |

#### 未固化/未接入部分

| 缺失 | 影响 | 原因 |
|------|------|------|
| **heat_tracking DB表始终为空** | api_server读取返回空，前端依赖JSON降级 | `upsert_heat_tracking()`全项目无人调用 |
| **gold_stock_discovery未接入热度** | 金股不受板块热度加权/退烧过滤 | 无调用关系 |
| **无定时任务触发heat_tracker** | 热度数据不自动更新 | 无Schedule条目 |
| **Guanlan项目可视化未整合** | 缺少潮汐波浪式可视化 | 独立项目，未迁移 |

### 3.2 代码未留存原因分析

**根因分析：**

1. **开发流程缺陷**：heat_tracker.py 在开发时只写了 `export_heat_data_json()` 导出JSON，没有同时写DB。DB层的 `upsert_heat_tracking()` 在 db.py 中定义了但无人调用——这是"接口先行、实现缺失"的典型模式。

2. **集成遗漏**：heat_tracker.py 作为独立模块开发，完成后未集成到 data_collector.py 的定时采集流程中，也未集成到 gold_stock_discovery.py 的选股流程中。

3. **测试覆盖不足**：没有端到端测试验证 heat_tracking 表是否有数据，导致空表问题未被发现。

4. **文档与代码脱节**：PROJECT_PLAN_v4.md 中规划了热度追踪的定时任务（`0 15 * * 1-5` 运行heat_tracker），但实际从未创建该定时任务。

### 3.3 防止再次发生的机制

**新增"集成检查清单"机制（写入SKILL.md）：**

每个新模块开发完成后，必须确认以下集成点：
- [ ] DB写入：是否有函数调用 `db.upsert_*()` 将数据写入对应表
- [ ] 定时任务：是否在 Schedule 中创建了触发任务
- [ ] 下游消费：是否被gold_stock_discovery / report_generator等模块引用
- [ ] API暴露：是否在 api_server.py 中有对应的REST端点
- [ ] 前端展示：是否在 app.js 中有渲染函数
- [ ] 端到端测试：是否验证了从采集到展示的完整链路

### 3.4 升级方案

#### 3.4.1 heat_tracker.py 补全

新增 `write_heat_to_db()` 函数：
```python
def write_heat_to_db(db, heat_data):
    """将热度数据写入 heat_tracking 表（补全缺失的DB写入）"""
    for sector in heat_data["sectors"]:
        for i, date in enumerate(heat_data["trade_dates"]):
            db.upsert_heat_tracking({
                "trade_date": date,
                "sector_name": sector["name"],
                "heat_score": sector["heat_series"][i],
                "capital_flow": sector["capital_series"][i],
                "limit_up_count": sector["limit_series"][i],
                "lifecycle": sector.get("lifecycle", {}).get("state", ""),
            })
```

在 `export_heat_data_json()` 末尾调用 `write_heat_to_db()`。

#### 3.4.2 定时任务

新增热度追踪定时任务：
```
cron: 30 15 * * 1-5  （每日15:30盘后）
command: python heat_tracker.py --export
```

#### 3.4.3 整合Guanlan项目可视化

参考 Guanlan 项目（https://github.com/kwjian-longzer/Guanlan）的潮汐波浪式可视化：

| Guanlan特性 | 迁移方案 |
|-------------|----------|
| OceanWaveChart 潮汐波浪图 | 作为板块热度页的第二种可视化模式（切换查看） |
| 日/周/月时间切换 | 新增时间维度切换按钮 |
| 键盘快捷键(←→↑↓) | 在板块热度页实现 |
| 板块标签点击显隐 | 已有此功能，保留 |
| 9板块配置 | 对齐到heat_tracker的10板块DEFAULT_SECTORS |

**实现方式**：不引入React/Vite构建链，而是将Guanlan的核心可视化逻辑用原生JS重写，整合到现有 `docs/assets/charts.js` 中，保持SPA轻量架构。

#### 3.4.4 热度数据流闭环

```
heat_tracker.py --export
  ├── 计算10板块热度（EMA平滑 + 5日累计）
  ├── 判定生命周期（高潮/崛起/退烧）
  ├── 写入 heat_tracking DB表           ← 补全
  ├── 导出 data/heat_data.json
  └── site_builder.py 更新历史滚动窗口

gold_stock_discovery.py（v5）
  ├── 读取 heat_tracking DB表获取板块生命周期  ← 新增
  ├── apply_sector_heat_weight() 加权
  └── filter_stocks() 退烧板块排除

api_server.py
  └── /api/sectors/heat/{date} 从DB读取      ← 已有，修复空表问题

docs/assets/app.js
  └── renderSectorsPage() 从API/JSON读取      ← 已有
```

---

## 四、工作流程

### 4.1 执行阶段划分

```
阶段1: 基础设施修复（P3 + P2数据层）
  ├── 4.1.1 heat_tracker.py 补全DB写入
  ├── 4.1.2 db.py 新增 moneyflow 表
  ├── 4.1.3 data_collector.py 新增主力资金采集
  └── 4.1.4 语法验证

阶段2: 共振引擎重构（P2核心）
  ├── 4.2.1 gold_stock_discovery.py 全面重构
  ├── 4.2.2 板块热度加权集成
  ├── 4.2.3 退烧过滤集成
  ├── 4.2.4 强度打分函数
  └── 4.2.5 端到端测试

阶段3: 网站首页改版（P1前端）
  ├── 4.3.1 index.html 三栏布局
  ├── 4.3.2 app.js 新增渲染函数
  ├── 4.3.3 styles.css 三栏样式
  ├── 4.3.4 api_server.py 新增端点
  └── 4.3.5 前端验证

阶段4: 飞书文档集成（P1推送）
  ├── 4.4.1 push_feishu.py 新增飞书文档创建
  ├── 4.4.2 push_feishu.py 新增飞书消息发送
  └── 4.4.3 集成测试

阶段5: 任务体系重建（P1调度）
  ├── 4.5.1 cls_collector.py 改造（每2小时+VIP发现）
  ├── 4.5.2 report_generator.py 确认流程
  ├── 4.5.3 删除旧6个定时任务
  ├── 4.5.4 创建新9个定时任务
  └── 4.5.5 全链路验证

阶段6: Guanlan可视化整合（P3增强）
  ├── 4.6.1 charts.js 新增潮汐波浪图
  ├── 4.6.2 时间维度切换
  └── 4.6.3 前端验证

阶段7: 文档更新与提交
  ├── 4.7.1 更新 SKILL.md 集成检查清单
  ├── 4.7.2 更新 PROJECT_ENGINEERING_DOC.md
  ├── 4.7.3 git commit + push
  └── 4.7.4 飞书文档同步
```

### 4.2 执行顺序与依赖关系

```
阶段1（基础设施）→ 阶段2（共振引擎）→ 阶段3（网站首页）
                                       ↓
阶段4（飞书集成）→ 阶段5（任务重建）→ 阶段6（可视化）→ 阶段7（文档）
```

阶段1和阶段4可以并行，阶段3依赖阶段2（金股数据格式变化），阶段5依赖阶段1-4全部完成。

### 4.3 验证标准

| 阶段 | 验证方法 | 通过标准 |
|------|----------|----------|
| 1 | `python heat_tracker.py --export` + 检查DB表 | heat_tracking表有数据 |
| 2 | `python gold_stock_discovery.py --date 2026-06-27` | 7维打分，退烧板块被过滤 |
| 3 | 浏览器打开 docs/index.html | 三栏布局正常显示 |
| 4 | 手动触发push_feishu | 飞书文件夹中出现文档 |
| 5 | 等待定时任务触发 | 任务按新时点执行 |
| 6 | 板块热度页切换波浪图 | 潮汐波浪图正常渲染 |

---

## 五、风险与应对

| 风险 | 概率 | 影响 | 应对 |
|------|------|------|------|
| 旧任务删除期间数据中断 | 中 | 中 | 先创建新任务，确认运行正常后再删除旧任务 |
| 共振引擎重构后选股结果变化 | 高 | 中 | 保留v4旧引擎作为降级，新旧对比验证 |
| 飞书文档创建权限不足 | 低 | 高 | 提前验证lark-cli权限 |
| 主力资金数据API限制 | 中 | 中 | Tushare moneyflow需积分，降级用龙虎榜净额替代 |
| 网站改版影响现有功能 | 中 | 中 | 保留旧页面路径，新布局作为首页增强 |

---

## 六、文件变更清单

### 新增文件
| 文件 | 用途 |
|------|------|
| 无新增独立文件 | 所有改造在现有文件上进行 |

### 修改文件

| 文件 | 改动内容 | 阶段 |
|------|----------|------|
| `heat_tracker.py` | 新增`write_heat_to_db()`，在export末尾调用 | 1 |
| `db.py` | 新增`moneyflow`表+`upsert_moneyflow()`+`query_moneyflow()` | 1 |
| `data_collector.py` | 新增主力资金采集；新增`--period qian_sanqiang`定时调用 | 1,5 |
| `gold_stock_discovery.py` | 全面重构：7维+加权+过滤+强度打分 | 2 |
| `docs/index.html` | 首页三栏布局重构 | 3 |
| `docs/assets/app.js` | 新增renderInsights/renderMarketDashboard/renderGoldStocksTable | 3 |
| `docs/assets/styles.css` | 三栏样式 | 3 |
| `docs/assets/charts.js` | 新增潮汐波浪图可视化 | 6 |
| `api_server.py` | 新增`/api/insights/latest`、`/api/gold-stocks/recent`端点 | 3 |
| `push_feishu.py` | 新增飞书文档创建+消息发送 | 4 |
| `cls_collector.py` | `--poll`改2小时；新增`--discover-vip` | 5 |
| `report_generator.py` | 确认prepare→generate→finalize流程适配 | 5 |
| `SKILL.md` | 新增集成检查清单机制 | 7 |
| `PROJECT_ENGINEERING_DOC.md` | 更新v5架构说明 | 7 |

### 定时任务变更

| 操作 | 任务 | cron |
|------|------|------|
| 更新 | 财联社采集 | `0 */2 * * *` |
| 删除 | 早盘舆情分析(06:00) | - |
| 删除 | 午盘舆情分析(11:30) | - |
| 删除 | 晚报舆情分析(20:00) | - |
| 新建 | 观澜踏浪纪-盘前 | `30 8 * * 1-5` |
| 新建 | 观澜踏浪纪-盘中 | `30 12 * * 1-5` |
| 新建 | 观澜踏浪纪-盘后 | `0 16 * * 1-5` |
| 更新 | 观澜踏浪纪-周报(周六) | `0 20 * * SAT` |
| 更新 | 观澜踏浪纪-周报(周日) | `0 20 * * SUN` |
| 新建 | VIP股票发现 | `30 */2 * * *`（财联社后30分钟） |
| 新建 | 钱三强选股-盘中 | `50 11 * * 1-5` |
| 新建 | 钱三强选股-盘后 | `30 15 * * 1-5` |
| 新建 | 板块热度追踪 | `30 15 * * 1-5` |

---

> **本文档待用户审查通过后方可执行。请逐条确认方案，标注需调整的部分。**
