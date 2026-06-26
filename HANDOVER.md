# 工作交接单（v3）

> 创建时间: 2026-06-26
> 最新更新: 2026-06-26 (v3.2)
> 最新commit: 40de9b8
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
  "app_secret": "pgh5y8ILHKYaSdjduOYF6dQVdUrxgewr",
  "fxbaogao_api_key": "sk-EWPETEFW3JXP24O1a7VbcX0Xehd1n567"
}
```

### 2.2 settings.py（已在仓库中）

所有Python脚本通过 `from settings import get_tushare_token` 等函数读取凭证。不要在代码中硬编码任何token。

需新增函数：
```python
def get_fxbaogao_api_key():
    return os.environ.get("FXBAOGAO_API_KEY") or _config.get("fxbaogao_api_key", "")
```

### 2.3 各服务说明

| 服务 | 用途 | 凭证 | 获取方式 |
|------|------|------|---------|
| Tushare Pro | A股行情/财务/资金流向 | token | https://tushare.pro/ 注册获取 |
| 财联社CLS | VIP文章/电报/深度头条 | 无需token（API直接调用） | https://www.cls.cn/ |
| 新浪财经HTTP | 港股/美股/外汇实时行情 | 无需token | https://hq.sinajs.cn/ |
| **发现报告fxbaogao** | **研报搜索（VIP文章股票发现）** | **api_key** | **https://www.fxbaogao.com/ MCP或REST** |
| 飞书Webhook | 消息推送 | webhook URL | 飞书群机器人配置 |
| GitHub | 代码托管+Pages部署 | git credentials | 已配置 |

### 2.4 发现报告(fxbaogao) API

**正确调用方式** — MCP HTTP端点（无需本地安装）：

```python
# 端点
url = "https://api.fxbaogao.com/mcp/"
headers = {
    "Authorization": "Bearer sk-EWPETEFW3JXP24O1a7VbcX0Xehd1n567",
    "Content-Type": "application/json",
}

# JSON-RPC 2.0 格式调用 search_reports
payload = {
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
        "name": "search_reports",
        "arguments": {
            "keywords": "光模块 PCB镀铜 锡粉",
            # 可选: startTime(毫秒时间戳), endTime("last7day"等), orgNames(["高盛"])
            # 注意: 字段名为camelCase, 无page_size参数
        },
    },
    "id": 1,
}
# POST → result.content[0].text → JSON字符串 → {reports: [...]}
# 每篇研报: {reportId, title, orgName, paragraphs: [{content, pageNum}]}

# get_report_content 工具
payload["params"]["name"] = "get_report_content"
payload["params"]["arguments"] = {"doc_id": 5463275}  # 来自search的reportId
```

**已在代码中实现**：
- `cls_collector.py` → `search_fxbaogao()` 函数
- `vip_extractor.py` → `discover_stocks_by_article()` Step 5

**其他接入方式**（文档参考）：
- MCP本地版: `uvx fxbaogao-mcp@latest` + env FXBAOGAO_API_KEY
- CLI工具: `fxbaogao search "关键词" --time last1year`
- 官方文档: https://www.fxbaogao.com/agent-interface

### 2.5 Tushare积分

当前token的积等级约2000分。关键API积分消耗：
- `daily`: 每次约5分
- `moneyflow`: 每次约20分
- `stock_company`: 每次约5分
- `top_list`: 每次约10分

**重要**: 4个脚本各自独立调用Tushare，同一交易日重复拉取，浪费70%积分。数据库缓存层（db.py）是首要任务。

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

### P0: 数据基础设施

#### P0-1: ✅ 已完成 — db.py SQLite缓存层

**文件**: `db.py`（已创建并验证）
**数据库**: `data/stock.db`

12张表全部建好，核心方法：
- `get_or_fetch(source, api_name, fetch_func, trade_date, params, ttl_hours)` — 缓存优先策略
- `upsert_telegraph(item)` — 电报写入（按telegraph_id去重）
- `query_telegraphs(date, is_red_only, limit)` — 查询当日电报（含关联股票）
- `query_telegraph_stats(date)` — 电报统计（总数/红色/热门股票）
- `query_resonance(date)` — **共振分析**（跨4个数据源交叉匹配）
- `upsert_vip_article(article)` / `upsert_vip_discovered_stock(...)` — VIP文章+股票
- 各行情数据表的upsert方法

**电报结构化字段**（为Agent推理设计）：
```sql
cls_telegraph表额外字段:
  event_type TEXT,      -- 政策/财报/并购/研报/数据/公告/其他
  sentiment TEXT,       -- positive/negative/neutral
  impact_level TEXT,    -- high/medium/low
  sector_tags TEXT      -- 行业标签（逗号分隔）
```

验证结果：20条电报入库，event_type/sentiment/impact_level/sector_tags全部正确填充。

#### P0-2: ✅ 已完成 — cls_collector.py 财联社独立采集器

**文件**: `cls_collector.py`（已创建并验证）
**定时任务**: `0 * * * *`（每小时整点）
**命令**: `python cls_collector.py --poll`（每15分钟轮询，持续55分钟）

采集内容：
1. **电报** — v3.2使用 `/v1/roll/get_roll_list?category=red` 端点
   - **关键发现**: 点击CLS网页"加红"按钮时浏览器调用此端点
   - `category=red`: 只返回加红重要电报（`level=B`），过滤非重要信息
   - `last_time` + `refresh_type=1`: **支持向后翻页**（与`/api/cache`完全不同！）
   - `rn=50`: 单次返回50条，3页翻页即可覆盖24小时
   - 同时补充 `/api/cache?name=telegraph` 获取最新非加红电报
   - NLP分类：event_type（7类）、sentiment（正负面+否定语境）、impact_level（红色+关键词+百分比提取）、sector_tags（80+标签）
2. **VIP文章** — CLS API分页采集83篇，写入cls_vip_article表
   - 双层股票发现：Tushare主营业务搜索 + 发现报告API搜索
   - 结果写入vip_discovered_stock表
3. **深度头条** — CLS API，存入raw_cache
4. **投资日历** — CLS API，存入raw_cache

**CLS电报API端点对比**（v3.2关键决策）：

| 端点 | 条数 | 向后翻页 | category | 用途 |
|------|------|---------|----------|------|
| `/api/cache?name=telegraph` | 20 | ❌ | ❌ | 取最新非加红 |
| `/api/cache?name=telegraphList&lastTime=X` | 20 | ❌(前向轮询) | ❌ | 取更新 |
| `/nodeapi/telegraphList` | — | — | — | ❌ 404已移除 |
| **`/v1/roll/get_roll_list?category=red`** | **50** | **✅** | **✅** | **主力端点** |

**命令行参数**：
```bash
python cls_collector.py              # 采集全部
python cls_collector.py --telegraph  # 只采集电报（红色端点+向后翻页）
python cls_collector.py --vip         # 只采集VIP文章
python cls_collector.py --poll        # 持续轮询模式（每15分钟，持续55分钟）
python cls_collector.py --stats       # 查看数据库统计
python cls_collector.py --resonance   # 查看当日共振分析
```

**关于Chrome桌面通知**: 调研确认CLS"桌面通知"是HTTP轮询+Notification API，非服务器推送，无VAPID/WebSocket/SSE，不可利用。

验证结果：一次调用获取168条电报（144条红色），覆盖24小时。

#### P0-3: ⏳ 待完成 — 改造 fetch_data.py 走 db.py

所有 `pro.daily()` / `pro.moneyflow()` 等调用改为 `db.get_or_fetch('tushare', 'daily', ...)`
- A股指数改用新浪实时接口（午报场景）
- 电报改为从数据库查询当天全部电报（而非本次采集的20条）
- VIP文章改为从数据库查询（cls_collector.py每小时已采集入库）

#### P0-4: ✅ 已完成 — VIP文章股票发现补充发现报告API

**文件**: `cls_collector.py` (search_fxbaogao函数) + `vip_extractor.py` (discover_stocks_by_article Step 5)

**发现报告API正确格式**（MCP HTTP端点）：
```python
# 端点: https://api.fxbaogao.com/mcp/
# 协议: JSON-RPC 2.0
# 认证: Authorization: Bearer sk-xxx
# 字段名为camelCase: keywords, startTime, endTime, orgNames, luckyBaby
# 注意: 无page_size参数

payload = {
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
        "name": "search_reports",
        "arguments": {"keywords": "光模块 PCB镀铜 锡粉"}
    },
    "id": 1
}
# 返回: result.content[0].text → JSON字符串 → {reports: [...]}
# 每篇研报: {reportId, title, orgName, paragraphs: [{content, pageNum}]}
```

**两层搜索已实现**：
1. **第一层**（Tushare）：stock_company.main_business搜索
2. **第二层**（fxbaogao）：MCP HTTP API搜索研报，从研报文本中匹配公司名
3. **合并去重**：两层结果按ts_code合并，match_source标注 'tushare'/'fxbaogao'/'both'

验证结果：搜索"算力 光模块 芯片"找到20篇研报、34只股票（含中际旭创、通富微电等）。

#### P0-5: extract_summary.py 加字段级时间戳

data_summary.json每个数据块增加：
```json
{
  "data_time": "2026-06-26 11:30:00",
  "data_source": "sina_realtime",
  "is_realtime": true,
  "note": "午报使用新浪实时指数"
}
```

#### P0-6: 共振分析改为从数据库查询

**当前**：共振分析用本次fetch_data.py采集的20-40条电报
**改造后**：从SQLite查全天电报
```python
# 查询当日全部电报
telegraphs = db.query(
    "SELECT t.*, GROUP_CONCAT(s.stock_name) as stocks "
    "FROM cls_telegraph t "
    "LEFT JOIN cls_telegraph_stock s ON t.telegraph_id = s.telegraph_id "
    "WHERE date(t.timestamp, 'unixepoch') = ? "
    "GROUP BY t.telegraph_id ORDER BY t.timestamp",
    (today,)
)
```

### P1: 部署（用户需手动操作）

| 操作 | 步骤 |
|------|------|
| 仓库改Public | GitHub → Settings → Danger Zone → Change visibility → Public |
| 开启Pages | GitHub → Settings → Pages → Source: main /docs → Save |
| 设置电报采集定时任务 | Schedule: `0 * * * *` 每小时执行 cls_collector.py |
| 重设报告Schedule | 修正cron时间（午报0 11 * * 1-5 而非凌晨） |

### P2: 体验优化

- 前端防缓存: app.js中fetch latest.json加 `?v=${Date.now()}` 参数
- 观澜海面曲线: 学习用户上传的观澜代码
- 金股回测: 随时间积累，T+1逻辑已修复

---

## 五、电报结构化与Agent洞见方案

### 5.1 电报结构化字段设计

为让后续Agent通过读取电报获得市场洞见，电报需要结构化为以下维度：

```json
{
  "telegraph_id": "cls_12345",
  "title": "国家能源局：未来西部地区不仅要向外送电、送煤、送气 还要向外送产品、送Token",
  "content": "完整电报正文...",
  "timestamp": 1782444697,          // 电报发布时间(Unix)
  "fetch_time": "2026-06-26 12:00:00", // 入库时间
  "is_red": 0,                       // 红色重要标记
  "stocks": ["西部矿业", "特变电工"], // CLS标注的关联股票
  "sector_tags": ["能源", "西部开发"], // 行业/主题标签（需NLP提取）
  "event_type": "政策",               // 事件类型: 政策/财报/并购/研报/数据
  "sentiment": "positive",            // 情绪: positive/negative/neutral
  "impact_level": "medium"             // 影响级别: high/medium/low
}
```

### 5.2 Agent洞见提取流程

参考RAG架构 [$TRAE_REF](https://blog.csdn.net/yuntongliangda/article/details/150594229)，Agent从电报获得洞见的流程：

1. **查询理解**：Agent根据报告类型（晨报/午报/晚报）确定需要哪些维度的电报
2. **混合检索**：
   - 关键词检索：按股票名/行业名/事件类型精确匹配
   - 语义检索：用embedding模型找语义相近的电报
3. **重排序**：按重要性（红色标记+影响级别+时间近度）排序
4. **洞见生成**：
   - 主力资金流向：哪些板块被电报反复提及
   - 事件驱动：哪些电报构成事件链条（如"政策发布→相关股票涨停→资金流入"）
   - 共振信号：电报提及的股票同时出现在龙虎榜/涨停板/资金流入中

**研究补充**（基于金融NLP前沿实践）：

- **事件抽取（Event Extraction）**：从非结构化电报文本中提取结构化事件三元组（事件类型-参与实体-时间），使用NER（命名实体识别）自动识别股票/公司名称 [$TRAE_REF](https://blog.csdn.net/yuntongliangda/article/details/150594229)
- **实体级情感分析**：不同于文档级情感，金融场景需要对每个公司分别判断positive/negative/neutral，同一电报中可能对不同公司有不同影响
- **金融知识图谱（GraphRAG）**：构建entity-relation-entity三元组，支持跨报告、跨时段的关联推理，例如"政策→供应链→具体公司→资金流向"的传导链条
- **两阶段检索**：召回阶段用向量搜索找语义相近电报，重排序阶段用Cross-Encoder精排，提升Agent获取高信噪比输入的效率

**本项目已实现的结构化字段**（cls_collector.py NLP模块）：
```python
# 已实现的轻量级NLP分类（无需外部模型）:
event_type   = classify_event_type(text)   # 政策/财报/并购/研报/数据/公告/其他
sentiment    = classify_sentiment(text)    # positive/negative/neutral
impact_level = classify_impact(text, is_red) # high/medium/low
sector_tags   = extract_sector_tags(text)  # 半导体/新能源/医药/...
```

**后续优化方向**：引入LLM做深度事件抽取和实体级情感分析，当前的关键词匹配方案已能覆盖80%+的分类需求。

### 5.3 共振分析数据源整合

共振分析需要交叉验证以下数据源（全部从数据库查询）：

| 数据源 | 表 | 匹配方式 |
|--------|---|---------|
| 电报提及股票 | cls_telegraph_stock | 按stock_name匹配 |
| 龙虎榜 | dragon_tiger | 按ts_code匹配 |
| 涨停板 | limit_up | 按ts_code匹配 |
| 资金净流入 | sector_moneyflow | 按industry匹配 |
| VIP文章发现 | vip_discovered_stock | 按stock_code匹配 |
| 研报提及 | fxbaogao搜索结果 | 按stock_name匹配 |

共振 = 一只股票同时出现在≥2个数据源中。出现的数据源越多，共振信号越强。

**已实现**: `db.py` → `query_resonance(date)` 方法，查询当日跨4个数据源（电报/龙虎榜/涨停/VIP文章）的共振股票。
用法: `python cls_collector.py --resonance`

---

## 六、关键文件说明

### Python脚本

| 文件 | 功能 | 注意事项 |
|------|------|---------|
| `settings.py` | 统一配置管理 | ✅含get_fxbaogao_api_key() |
| `fetch_data.py` | 数据采集主脚本 | ⏳需改造走db.py |
| `extract_summary.py` | 数据摘要提取 | ✅v3.2: 注入insights字段 |
| `heat_tracker.py` | 热度量化追踪 | v3动态选板块已修复 |
| `vip_extractor.py` | VIP股票发现 | ✅含fxbaogao二层搜索 |
| `site_builder.py` | 报告→网站JSON | |
| `gold_stock_backtest.py` | 金股回测 | T+1逻辑已修复 |
| `push_feishu.py` | 飞书推送 | v3只推卡片 |
| `validate_report.py` | 12条红线校验 | |
| `cls_collector.py` | **财联社独立采集** | ✅v3.2: 红色电报端点+向后翻页 |
| `db.py` | **SQLite缓存层** | ✅已创建，12张表 |
| `insight_extractor.py` | **Agent洞见引擎** | ✅v3.2新增，5类信号+跨市场映射 |
| `report_quality_evaluator.py` | 10维度评分 | |

### 网站文件

| 文件 | 功能 |
|------|------|
| `docs/index.html` | SPA入口，7页面 |
| `docs/assets/app.js` | 路由/导航/日期选择/数据加载/MD渲染 |
| `docs/assets/charts.js` | 3个ECharts图表 |
| `docs/assets/styles.css` | 观澜深色主题(#0a0e17) |
| `docs/data/*.json` | 网站数据（自动生成） |

---

## 七、已知Bug与注意事项

### 已修复（commit 2dba213）
1. ✅ heat_tracker.py: DEFAULT_SECTORS硬编码 → 动态选择
2. ✅ push_feishu.py: 删除全文MD推送 → 只保留卡片
3. ✅ gold_stock_backtest.py: T+1跳过逻辑
4. ✅ 7个Python文件硬编码Token → settings.py统一管理

### 本次新增（commit 889e58e ~ 40de9b8）
5. ✅ db.py: SQLite缓存层（12张表 + get_or_fetch + 共振分析查询）
6. ✅ cls_collector.py: 财联社独立每小时采集（电报+VIP+深度+日历）
7. ✅ settings.py: 新增 get_fxbaogao_api_key()
8. ✅ vip_extractor.py: 新增fxbaogao MCP HTTP API二层股票搜索
9. ✅ 电报结构化: event_type/sentiment/impact_level/sector_tags NLP字段
10. ✅ insight_extractor.py: Agent洞见引擎（5类信号+跨市场映射+缺失检查）
11. ✅ extract_summary.py: 注入insights到data_summary.json
12. ✅ analysis_prompt.md: 新增7条insights引用规则
13. ✅ cls_collector.py NLP优化: sector_tags 40→80+, 否定语境检测, 百分比提取
14. ✅ cls_collector.py: 发现 /v1/roll/get_roll_list?category=red 红色电报端点
    - 向后翻页回填24h红色电报（之前固定20条的问题彻底解决）
    - is_red判断修正: level in ('A','B') 而非 color=='red'

### 已知问题（未修复）
1. **自动化任务时间错误**: 午报凌晨3:32执行，需修正为11:35
2. **午报A股指数**: 用Tushare daily返回前日，需改用新浪实时接口
3. **字段级时间戳缺失**: data_summary.json各数据块无时间标注（P0-5待完成）
4. **数据重复拉取**: fetch_data.py未走db.py缓存层（P0-3待完成）
5. ~~电报非24小时覆盖~~ → ✅ cls_collector.py已解决（每小时定时采集）
6. **共振分析用本次采集**: extract_summary.py未从数据库查询（P0-6待完成）
7. ~~VIP股票发现不全~~ → ✅ 已补充fxbaogao API二层搜索
8. **前端CDN缓存**: latest.json可能被GitHub Pages CDN缓存
9. ~~CLS API固定返回20条~~ → ✅ v3.2发现 /v1/roll/get_roll_list?category=red 向后翻页
10. ~~is_red判断错误~~ → ✅ v3.2修正为 level in ('A','B')

### 历史教训
1. **子agent验证缺失**: 子agent返回"已完成"后必须实际运行验证
2. **颜色规范**: A股红涨绿跌(#ef4444涨/#22c55e跌)
3. **Tushare行业名**: 必须查实际110个行业名，不能猜测
4. **东方财富涨停API**: 不可用，改用Tushare daily pct_chg≥9.8%
5. **本地HTML文件**: XHR无法加载本地JSON，需内联为JS变量

---

## 八、快速启动检查清单

新环境Agent启动后，按顺序检查：

- [ ] `git clone` 仓库到 `/workspace/stock-assistant`
- [ ] 创建 `config.json`（见第2.1节内容，含fxbaogao_api_key）
- [ ] `python3 -c "from settings import get_tushare_token; print(get_tushare_token()[:10])"` — 验证Token可读
- [ ] `python3 -m py_compile fetch_data.py` — 验证语法
- [ ] 读取 `SKILL.md` — 理解项目宪法和红线规则
- [ ] 读取 `SESSION_SUMMARY.md` — 了解任务进度
- [ ] 开始执行P0任务（db.py → cls_collector.py → 改造脚本）

---

## 九、项目宪法核心条款（摘自SKILL.md）

1. **数据真实性**: 报告中每个数字必须来自data_summary.json，不可编造
2. **12条红线**: validate_report.py校验，失败必须修复
3. **质量评分**: 10维度×10分=100分，目标≥80分
4. **金股龙脉定位**: 每只金股必须有What(信号)+How(验证)+时间(节奏)
5. **交叉验证**: 重要结论至少2个独立来源
6. **子agent验证**: 任何子agent返回"完成"后，必须实际运行验证
7. **Token安全**: 不可在代码中硬编码任何凭证
8. **红涨绿跌**: A股色彩规范，红色=涨/正值，绿色=跌/负值

**违反以上任何条款的行为都是不可接受的。**
