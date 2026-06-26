# 工作交接单

> 创建时间: 2026-06-26
> 最新commit: 2dba213
> 交接对象: 全新环境中的Agent
> 前置文档: SKILL.md（项目宪法）、SESSION_SUMMARY.md（任务对照表）

---

## 零、紧急提醒

**在执行任何任务之前，你必须先读取以下两个文件：**
1. `SKILL.md` — 项目宪法，每做一步都要回顾是否违反红线规则
2. `SESSION_SUMMARY.md` — 全部任务指示与执行情况对照表

**项目宪法的核心要求（用户原话提炼）：**
- "每做一步就要回顾是否有利于接近靶标的要求" — 靶标是：动态更新的网站，自动化任务按时更新数据，飞书只发链接+简报
- 违反SKILL.md中任何"红线"规则的行为都是不可接受的
- 所有报告中的数字必须来自`data/data_summary.json`，不可编造
- 子agent返回"已完成"后，必须实际运行验证（grep/运行/py_compile），不可仅凭声明标注"✅完成"

---

## 一、项目概述

A股/港股/美股市场每日研报自动生成与推送系统。v3.0已从纯Markdown报告升级为GitHub Pages动态网站。

**仓库地址**: `https://github.com/kwjian-longzer/stock-assistant.git`
**网站地址**: `https://kwjian-longzer.github.io/stock-assistant/`（需用户先开启Pages，见第六节）

---

## 二、凭证与权限

### 2.1 config.json（已被.gitignore排除，不在仓库中）

新环境必须在 `/workspace/stock-assistant/config.json` 创建此文件：

```json
{
  "tushare_token": "8eaad9971749da18299f4932a7cabf068a495fdf06ef3aaafebfe365",
  "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/bf897c28-ab6c-4da0-9926-dc214a5f1c0b",
  "app_id": "cli_aabeb7dc9a78dcb5",
  "app_secret": "pgh5y8ILHKYaSdjduOYF6dQVdUrxgewr"
}
```

### 2.2 settings.py（已在仓库中）

所有Python脚本通过 `from settings import get_tushare_token` 等函数读取凭证。不要在代码中硬编码任何token。

### 2.3 各服务说明

| 服务 | 用途 | 凭证 | 获取方式 |
|------|------|------|---------|
| Tushare Pro | A股行情/财务/资金流向 | token | https://tushare.pro/ 注册获取 |
| 财联社CLS | VIP文章/电报/深度头条 | 无需token（API直接调用） | https://www.cls.cn/ |
| 新浪财经HTTP | 港股/美股/外汇实时行情 | 无需token | https://hq.sinajs.cn/ |
| 飞书Webhook | 消息推送 | webhook URL | 飞书群机器人配置 |
| 飞书Open API | 文件上传（v3.0已废弃） | app_id + app_secret | 飞书开放平台 |
| GitHub | 代码托管+Pages部署 | git credentials | 已配置 |

### 2.4 Tushare积分

当前token的积等级约2000分。关键API积分消耗：
- `daily`: 每次约5分
- `moneyflow`: 每次约20分
- `stock_company`: 每次约5分
- `top_list`: 每次约10分

**重要**: 4个脚本（fetch_data/heat_tracker/vip_extractor/qian_sanqiang）各自独立调用Tushare，同一交易日重复拉取stock_basic/daily/moneyflow，浪费70%积分。数据库缓存层（db.py）是首要任务。

---

## 三、执行流程（v3.0 七步）

```
步骤0:   git clone仓库到 /workspace/stock-assistant
步骤0.5: 确保 config.json 存在（见第2.1节）
步骤1:   python fetch_data.py [morning|noon|evening|weekly_sat|weekly_sun]
         → data/raw_data_*.json + data/qian_sanqiang_results.json
步骤2:   python extract_summary.py
         → data/data_summary.json
步骤3:   python heat_tracker.py
         → data/heat_data.json
步骤4:   读取 data/data_summary.json + analysis_prompt.md，AI撰写报告
         → reports/YYYY-MM-DD_报告类型.md
步骤5:   python validate_report.py + python report_quality_evaluator.py
         → 12条红线校验 + 10维度评分(目标≥80分)
步骤6:   python push_feishu.py --file reports/YYYY-MM-DD_报告类型.md
         → [步骤1] Webhook发送链接+简报卡片
         → [步骤2] site_builder.py → docs/data/*.json
         → [步骤3] gold_stock_backtest.py → 更新回测
         → [步骤4] Git commit + push → GitHub Pages自动部署
```

### 报告类型与执行时间

| 类型 | 参数 | 正确执行时间 | 说明 |
|------|------|-------------|------|
| 晨报 | morning | 08:30 | 开盘前，用电报+美股收盘+隔夜消息 |
| 午报 | noon | 11:35 | 上午收盘后，A股有半日数据 |
| 晚报 | evening | 15:05 | 全天收盘后，所有数据完整 |
| 周六复盘 | weekly_sat | 周六15:05 | 本周复盘 |
| 周日展望 | weekly_sun | 周日18:00 | 下周展望 |

**当前问题**: 自动化任务在凌晨3:32执行午报，导致数据全部是前日的。必须修正Schedule cron时间。

---

## 四、待办任务清单（按优先级排序）

### P0: 数据正确性（必须先做）

#### P0-1: 创建 db.py — SQLite缓存层
**目标**: 避免重复拉取Tushare + 字段级时间戳

表结构设计（9张表）：
- `raw_cache`: 原始API数据缓存(source, api_name, trade_date, params_hash, data_json)
- `index_quote`: 指数行情(name, trade_date, close, pct_chg, is_realtime, fetch_time)
- `sector_moneyflow`: 板块资金流向(trade_date, industry, net_mf_amount)
- `limit_up`: 涨停股票(trade_date, ts_code, name, pct_chg, industry)
- `dragon_tiger`: 龙虎榜(trade_date, ts_code, name, net_buy, reason)
- `north_money`: 北向资金(trade_date, north_money, hgt, sgt)
- `margin`: 融资融券(trade_date, exchange_id, rzye, rqye)
- `cls_telegraph`: 电报(telegraph_id, timestamp, content, is_important)
- `gold_stock`: 金股+回测(name, code, recommend_date, return_1d/3d/5d/10d/20d, max_return, max_drawdown)

核心方法: `get_or_fetch(source, api_name, trade_date, fetch_func, params, ttl_hours=12)`
查到直接返回缓存，查不到才调API并存入。

#### P0-2: 改造 fetch_data.py 走 db.py
所有 `pro.daily()` / `pro.moneyflow()` 等调用改为 `db.get_or_fetch('tushare', 'daily', ...)`

#### P0-3: 改造 heat_tracker / vip_extractor / qian_sanqiang 走 db.py
同上模式，命中缓存零API消耗。

#### P0-4: extract_summary.py 加字段级时间戳
data_summary.json每个数据块增加：
```json
{
  "data_time": "2026-06-26 11:30:00",
  "data_source": "sina_realtime",
  "is_realtime": true,
  "note": "午报使用新浪实时指数"
}
```
或 `is_realtime: false, note: "融资融券为前一交易日数据"`

#### P0-5: fetch_data.py 午报A股指数改用新浪实时接口
当前用Tushare daily（返回前日收盘）。午报需要实时半日数据。
新浪接口: `https://hq.sinajs.cn/list=sh000001,sz399001,sz399006`
返回实时价，无需Tushare积分。

### P1: 部署（用户需手动操作）

| 操作 | 步骤 |
|------|------|
| 仓库改Public | GitHub → Settings → Danger Zone → Change visibility → Public |
| 开启Pages | GitHub → Settings → Pages → Source: main /docs → Save |
| 重设Schedule | 修正cron时间（午报0 11 * * 1-5 而非凌晨） |

### P2: 体验优化

- 前端防缓存: app.js中fetch latest.json加 `?v=${Date.now()}` 参数
- 观澜海面曲线: 学习用户上传的观澜代码(`交易中枢.html`)，考虑更高级的可视化
- 金股回测: 随时间积累，T+1逻辑已修复，后续推荐的金股会有回测数据

---

## 五、关键文件说明

### Python脚本

| 文件 | 行数 | 功能 | 注意事项 |
|------|------|------|---------|
| `settings.py` | 60 | 统一配置管理 | 从config.json或环境变量读取凭证 |
| `fetch_data.py` | 1800+ | 数据采集主脚本 | 含Tushare+新浪+CLS+钱三强选股 |
| `extract_summary.py` | 1300+ | 数据摘要提取 | AI写报告的唯一数据来源 |
| `heat_tracker.py` | 750+ | 热度量化追踪 | v3: 动态选板块+EMA平滑，DEFAULT_SECTORS仅作fallback |
| `vip_extractor.py` | 400+ | VIP股票发现 | 搜索stock_company.main_business全文 |
| `qian_sanqiang_selector.py` | 300+ | 钱三强选股 | EMA趋势+换手率+资金共振 |
| `site_builder.py` | 1050+ | 报告→网站JSON | 解析MD+日期归档+历史累积 |
| `gold_stock_backtest.py` | 500+ | 金股回测 | T+1逻辑: 跳过当日推荐 |
| `push_feishu.py` | 800+ | 飞书推送 | v3: 只推Webhook卡片(链接+简报) |
| `validate_report.py` | 500+ | 12条红线校验 | |
| `report_quality_evaluator.py` | 300+ | 10维度评分 | 目标≥80分 |

### 网站文件

| 文件 | 功能 |
|------|------|
| `docs/index.html` | SPA入口，7页面(看板/归档/热度/金股/信源/选股/日历) |
| `docs/assets/app.js` | 路由/导航/日期选择/数据加载/Markdown渲染 |
| `docs/assets/charts.js` | 3个ECharts图表(热度/资金流/涨停) |
| `docs/assets/styles.css` | 观澜深色主题(#0a0e17) |
| `docs/data/manifest.json` | 总索引(所有日期+报告类型) |
| `docs/data/latest.json` | 最新一期完整快照 |
| `docs/data/archive/*.json` | 按日期归档 |
| `docs/data/history/gold_stocks.json` | 金股历史+回测 |
| `docs/data/history/heat_tracking.json` | 热度趋势(30日滚动) |

### 配置文件

| 文件 | git追踪 | 内容 |
|------|---------|------|
| `config.json` | ❌ gitignore | tushare_token/feishu_webhook/app_id/app_secret |
| `.gitignore` | ✅ | 排除raw_data/config.json/__pycache__ |
| `SKILL.md` | ✅ | 项目宪法，红线规则 |
| `analysis_prompt.md` | ✅ | 报告写作规范，AI必须遵循 |

---

## 六、数据架构现状与目标

### 现状（JSON文件覆盖模式）

```
fetch_data.py → data_summary.json（每次覆盖）
heat_tracker.py → heat_data.json（每次覆盖）
site_builder.py → docs/data/*.json（部分归档，部分覆盖）
```

问题：无历史查询能力、无字段级时间戳、重复拉取API。

### 目标（SQLite缓存+归档模式）

```
所有脚本 → db.py.get_or_fetch() → SQLite缓存（按trade_date+api_name去重）
site_builder.py → 从数据库读取历史 → docs/data/*.json（网站数据）
```

数据库文件: `/workspace/stock-assistant/data/stock.db`（gitignore排除）

---

## 七、已知Bug与注意事项

### 已修复（commit 2dba213）
1. ✅ heat_tracker.py: DEFAULT_SECTORS硬编码 → 改为None触发动态选择
2. ✅ push_feishu.py: 删除Open API全文MD推送 → 只保留Webhook卡片
3. ✅ gold_stock_backtest.py: 当日推荐无后续数据 → T+1跳过逻辑
4. ✅ 7个Python文件硬编码Token → settings.py统一管理

### 已知问题（未修复）
1. **自动化任务时间错误**: 午报凌晨3:32执行，需修正为11:35
2. **午报A股指数**: 用Tushare daily返回前日，需改用新浪实时接口
3. **字段级时间戳缺失**: data_summary.json各数据块无时间标注
4. **数据重复拉取**: 4个脚本各自调Tushare，需db.py缓存层
5. **前端CDN缓存**: latest.json可能被GitHub Pages CDN缓存
6. **金股回测数据空**: 当前18只金股推荐日=当日，T+1后才有数据

### 历史教训
1. **子agent验证缺失**: 子agent返回"已完成"后必须实际运行验证，不能仅凭声明标✅
2. **颜色规范**: A股红涨绿跌(#ef4444涨/#22c55e跌)，与国际相反
3. **Tushare行业名**: 必须查实际110个行业名，不能猜测
4. **东方财富涨停API**: 不可用，改用Tushare daily pct_chg≥9.8%
5. **本地HTML文件**: XHR无法加载本地JSON，需内联为JS变量

---

## 八、观澜网站参考

用户上传了观澜网站完整代码（`交易中枢.html`），可从以下路径读取参考：
- 解压路径: `/data/user/work/guanlan/`
- 核心文件: `交易中枢.html`（约2000行，含完整CSS+JS）
- 数据文件: `app_data.json`、`calendar_data.json`

设计要点：
- 深色主题 `#0a0e17`（已采用）
- 侧边栏导航220px（已采用）
- 三层结构: L1资金/L2行为/L3情绪
- CSS变量体系（已采用）
- 红涨绿跌配色（已采用）

---

## 九、Git操作

```bash
# 克隆
git clone https://github.com/kwjian-longzer/stock-assistant.git

# 拉取最新
cd stock-assistant && git pull origin main

# 提交
git add -A && git commit -m "描述" && git push origin main

# 查看v2.0备份
git tag -l  # 应看到 v2.0-final
git show v2.0-final --stat
```

**重要**: push到main分支后，GitHub Pages自动从`/docs`目录部署网站（需用户先开启Pages）。

---

## 十、快速启动检查清单

新环境Agent启动后，按顺序检查：

- [ ] `git clone` 仓库到 `/workspace/stock-assistant`
- [ ] 创建 `config.json`（见第2.1节内容）
- [ ] `python3 -c "from settings import get_tushare_token; print(get_tushare_token()[:10])"` — 验证Token可读
- [ ] `python3 -m py_compile fetch_data.py` — 验证语法
- [ ] 读取 `SKILL.md` — 理解项目宪法和红线规则
- [ ] 读取 `SESSION_SUMMARY.md` — 了解任务进度
- [ ] 读取 `data/data_summary.json` 的 `meta` 字段 — 了解最近一次采集状态
- [ ] 检查 `docs/data/manifest.json` — 了解网站已有报告
- [ ] 开始执行P0任务（db.py数据库缓存层）

---

## 十一、项目宪法核心条款（摘自SKILL.md）

1. **数据真实性**: 报告中每个数字必须来自data_summary.json，不可编造
2. **12条红线**: validate_report.py校验，失败必须修复
3. **质量评分**: 10维度×10分=100分，目标≥80分
4. **金股龙脉定位**: 每只金股必须有What(信号)+How(验证)+时间(节奏)
5. **交叉验证**: 重要结论至少2个独立来源
6. **子agent验证**: 任何子agent返回"完成"后，必须实际运行验证
7. **Token安全**: 不可在代码中硬编码任何凭证
8. **红涨绿跌**: A股色彩规范，红色=涨/正值，绿色=跌/负值

**违反以上任何条款的行为都是不可接受的。**
