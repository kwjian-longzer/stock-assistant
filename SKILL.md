# 股票助手技能文件（v3.0 网站化版）

> **本文件是项目的"宪法"，每次自动化任务执行时必须首先读取并严格遵守。**
> v3.0: 项目从纯Markdown报告模式升级为动态网站。GitHub Pages托管，飞书只推链接+简报。
> 违反本文件中任何"红线"规则的行为都是不可接受的。

---

## 一、项目概述

本项目为 A股/港股/美股市场每日研报自动生成与推送系统，支持三种报告类型：
- **晨报**（morning）：侧重隔夜美股映射、开盘策略、预判型金股
- **午报**（noon）：侧重上午盘面验证、下午操作策略、盘中确认型金股
- **晚报**（evening）：侧重全天复盘、次日预判、次日布局型金股

---

## 二、目录结构与文件职责

```
stock-assistant/
├── SKILL.md              # 本文件 - 项目宪法，红线规则，历史教训
├── analysis_prompt.md    # 深度分析提示词 - 报告写作规范
├── fetch_data.py         # 数据采集 - 从Tushare/新浪/媒体网站获取原始数据 + 钱三强选股
├── qian_sanqiang_selector.py  # 钱三强选股公式 - 量化选股引擎（EMA趋势+换手率+资金共振）
├── extract_summary.py    # 数据摘要 - 从原始数据提取精炼摘要（AI写报告的唯一数据来源）
├── validate_report.py    # 报告校验 - v2.0: 12条红线(含热点追踪/龙脉定位/推理链/交叉验证/热度曲线/金股汇总表)
├── report_quality_evaluator.py  # v2.0质量评分系统 - 10维度×10分=100分
├── vip_extractor.py     # v3.0 VIP信息结构化提取器（搜索式发现: stock_company主营业务全文搜索）
├── heat_tracker.py      # v3.0 热度量化追踪器（动态选板块+EMA平滑+5日累计资金流）
├── site_builder.py      # v3.0 网站数据生成 - 将报告+数据转为JSON供GitHub Pages网站使用
├── gold_stock_backtest.py  # v3.0 金股回测 - 追踪历史金股推荐后1/3/5/10/20日收益
├── push_feishu.py        # 飞书推送 - v3.0: 推送链接+简报卡片(不再推全文MD)
├── config.json           # 持久化配置 - 飞书Webhook等（自动化任务可读取）
├── data/                 # 数据目录
│   ├── raw_data_*.json   # 原始采集数据（gitignore排除）
│   ├── data_summary.json # 数据摘要（AI写报告的唯一数据来源）✅纳入git
│   ├── heat_data.json   # v3.0 热度数据JSON（供网站ECharts）✅纳入git
│   ├── qian_sanqiang_results.json  # 钱三强选股结果 ✅纳入git
│   └── cls_telegraph_archive/  # 电报增量归档 ✅纳入git
├── reports/              # 报告输出目录 ✅纳入git
│   └── YYYY-MM-DD_晨报/午报/晚报.md
├── docs/                 # v3.0 GitHub Pages 网站根目录 ✅纳入git
│   ├── index.html       # SPA入口（7页面: 看板/归档/热度/金股/信源/选股/日历）
│   ├── assets/          # JS/CSS（app.js, charts.js, styles.css）
│   ├── _shared/js/      # ECharts库
│   └── data/            # 网站数据JSON（自动生成）
│       ├── manifest.json     # 总索引
│       ├── latest.json       # 最新快照
│       ├── archive/          # 按日期归档
│       │   └── YYYY-MM-DD_type.json
│       └── history/          # 跨日期累积
│           ├── gold_stocks.json  # 金股历史+回测
│           └── heat_tracking.json  # 热度趋势(30日滚动)
└── documentation/        # 项目文档（非网站）
```

---

## 三、执行流程（v3.0: 七步，不可跳过）

```
步骤0:   git clone（拉取最新代码）
         → git clone https://...github.com/kwjian-longzer/stock-assistant.git /workspace/stock-assistant

步骤0.5: 财联社浏览器采集（可选，API优先）
         → fetch_data.py优先使用API直接采集深度头条/VIP/投资日历/首页
         → API采集失败时降级到浏览器采集（保存到 data/cls_pages.json）
         → 电报由fetch_data.py通过API直接获取，无需浏览器
         → CLS API故障时：运行 python -c "from fetch_data import cls_api_diagnostic; cls_api_diagnostic()" 诊断

步骤1:   python fetch_data.py [morning|noon|evening|weekly_sat|weekly_sun]
         → 生成 data/raw_data_YYYYMMDD_mode.json + data/raw_data_latest.json
         → 自动运行钱三强选股，生成 data/qian_sanqiang_results.json
         → VIP文章分页采集（5页），50+篇
         → weekly_sat=周六复盘, weekly_sun=周日展望

步骤2:   python extract_summary.py
         → 读取 raw_data_latest.json + qian_sanqiang_results.json
         → 生成 data/data_summary.json

步骤3:   python heat_tracker.py（生成热度数据）
         → 导出 data/heat_data.json（动态选板块+EMA平滑+20交易日）

步骤4:   读取 data/data_summary.json + analysis_prompt.md，撰写报告
         → 保存到 reports/YYYY-MM-DD_报告类型.md
         → 报告中每个数字必须来自 data_summary.json

步骤5:   python validate_report.py --report reports/YYYY-MM-DD_报告类型.md --summary data/data_summary.json
         → 12条红线校验
         → python report_quality_evaluator.py 质量评分(目标≥80分)

步骤6:   python push_feishu.py --file reports/YYYY-MM-DD_报告类型.md
         → v3.0推送流程（5步）：
           [步骤1] Open API发送报告MD文件到飞书群（保留文件推送）
           [步骤2] Webhook发送交互卡片：一句话总结+Top3金股+网站链接按钮
           [步骤3] 运行 site_builder.py → 生成 docs/data/*.json（网站数据）
           [步骤4] 运行 gold_stock_backtest.py → 更新金股回测数据
           [步骤5] Git commit + push（报告+数据+网站数据自动提交）
         → GitHub Pages 自动部署（push后1-2分钟网站更新）
         → 飞书推送内容：链接+简报，不再推全文

步骤7:   git add -A && git commit -m "..." && git push origin main
         → **仅用于代码文件修改的提交**（报告和数据已在步骤6自动提交）
```

### v3.0 网站架构说明

网站地址: https://kwjian-longzer.github.io/stock-assistant/
托管方式: GitHub Pages（从 main 分支 /docs 目录提供服务）
部署: git push 后自动更新，无需CI/CD

7个页面:
1. 今日看板 - 指数温度计+板块热度+金股速览
2. 日报归档 - 日期选择器+Markdown报告渲染（晨/午/晚/周六复盘/周日展望）
3. 板块热度 - ECharts曲线图(热度对比/资金流/涨停数)+生命周期卡片
4. 金股追踪 - 历史金股表+回测收益(1/3/5/10/20日)+胜率统计
5. 财联社信源 - VIP文章+电报精华+股票发现表
6. 钱三强选股 - 选股结果卡片+历史表现
7. 投资日历 - 月历视图+事件卡片

---

## 四、红线规则（绝对不可违反）

### 红线1：禁止编造任何数据
- 报告中的**每一个数字**都必须来自 `data_summary.json`
- 指数点位、涨跌幅、成交额、股票代码、股票名称、净买入金额等，全部必须与数据一致
- **不允许出现 `XXX`、`xxx`、`688XXX`、`300XXX` 等占位符**
- 不允许出现 `？？`、`待补充`、`TODO`、`TBD`、`待定` 等占位符

### 红线2：数据缺失时的处理
- 如果 `data_summary.json` 中某个字段为"数据暂缺"，报告中必须写"数据暂缺"
- 如果使用了降级数据（标注为 DEGRADED），报告中必须说明数据来源和降级原因
- **宁可留空写"数据暂缺"，也绝不编造**

### 红线3：股票代码必须真实
- 报告中提到的每一只股票的代码和名称必须来自 `data_summary.json`
- 不允许凭空创造不存在的股票

### 红线4：七章结构不可省略
- 晨报/午报/晚报都必须包含完整的七章结构（第零章至第六章）
- 第零章（财联社信源扫描）是报告核心，不可省略
- 每章内容不得为空，如果该章数据全部缺失，也要写明"本章数据暂缺"并说明原因

### 红线5：校验不通过不推送
- `validate_report.py` 返回失败时，必须修复报告后重新校验
- 不得跳过校验步骤直接推送

---

## 五、数据源说明（已验证，禁止随意修改）

> **警告：以下数据源代码和格式已经过实际API验证（2026-06-23）。任何修改必须先调用真实API确认格式正确，禁止凭假设修改。**

| 数据类型 | 主数据源 | 代码/URL | 格式说明 | 降级/替代方案 |
|----------|----------|----------|----------|---------------|
| A股指数日线 | Tushare index_daily | - | - | 无替代，标记 FAILED |
| 美股期货 | 新浪HTTP | hf_YM/hf_NQ/hf_ES | `价格,,昨收,开盘,最高,最低,时间,...,名称,...`，涨跌额=price-pre_close | 无替代，标记 FAILED |
| 美股收盘 | 新浪HTTP | int_dji/int_nasdaq/int_sp500 | `名称,点位,涨跌额,涨跌幅%` | 无替代，标记 FAILED |
| 恒生指数 | 新浪HTTP | int_hangseng | `名称,当前价,涨跌额,涨跌幅%,...` | 无替代，标记 FAILED |
| **恒生科技** | **新浪HTTP** | **rt_hkHSTECH** | `代码,名称,当前价,昨收,当前价,最低,不明,涨跌额,涨跌幅%,...` | hktech无效，rt_hkHSTECH有效 |
| **美元指数** | **新浪HTTP** | **DINIW** | `时间,当前价,当前价重复,昨收,不明,最高,最低,不明,当前价重复,名称,日期`，涨跌额=price-pre_close | 无替代，标记 FAILED |
| **在岸人民币** | **新浪HTTP** | **fx_susdcny** | `时间,当前价,买入价,卖出价,幅度,...,名称,涨跌额,涨跌幅,...` | 无替代，标记 FAILED |
| **黄金** | **新浪HTTP** | **hf_GC** | `当前价,,昨收,开盘,最高,最低,时间,...,名称,...`，涨跌额=price-pre_close | 无替代，标记 FAILED |
| **原油** | **新浪HTTP** | **hf_CL** | 同黄金 | 无替代，标记 FAILED |
| **上海证券报** | **网页抓取** | **https://www.cnstock.com/** | `<li>` 中 `<a>` 标签文本 | 无替代，标记 FAILED |
| **证券时报** | **网页抓取** | **https://www.stcn.com/** | `<a href="...article...">` 标签文本 | 无替代，标记 FAILED |
| **人民日报** | **网页抓取** | **http://paper.people.com.cn/rmrb/pc/layout/YYYYMM/DD/node_01.html** | `<a>` 标签文本，UTF-8编码 | 无替代，标记 FAILED |
| **新闻联播** | **网页抓取** | **https://tv.cctv.cn/lm/xwlb/day/YYYYMMDD.shtml** | `<a title="标题">` 属性提取 | 无替代，标记 FAILED |
| **财联社电报** | **CLS API** | **https://www.cls.cn/api/cache?app=CailianpressWeb&name=telegraph&os=web&sv=8.7.9** | JSON格式，roll_data数组，含title/content/color/stock_list | 无需签名，直接HTTP请求 |
| **财联社深度头条** | **CLS API** | **/v3/depth/home/assembled/1000** | JSON格式，depth_list含title/brief/ctime/article_tag | 浏览器采集降级 |
| **财联社VIP文章** | **CLS API** | **/featured/v1/home/assembled + /featured/v2/home/recommend/article** | JSON格式，recommend_list/free_top_v2/yellow_article | 浏览器采集降级 |
| **财联社投资日历** | **CLS API** | **/api/calendar/web/list** | JSON格式，list含calendar_day/items | 浏览器采集降级 |
| **财联社首页** | **CLS API** | **/v2/article/hot/list** | JSON格式，list含title/brief/ctime/stocks | 浏览器采集降级 |
| 资金流向 | Tushare moneyflow | - | - | 无替代，标记 FAILED |
| 龙虎榜 | Tushare top_list + top_inst | - | - | 无替代，标记 FAILED |
| **融资融券** | **Tushare margin** | - | - | **当天空数据自动回滚前5天** |
| 沪深港通 | Tushare moneyflow_hsgt | - | - | 无替代，标记 FAILED |
| **涨跌停** | **Tushare limit_list_d** | - | - | **东方财富涨停池API替代**（134条），再降级到top_list筛选 |
| 每日指标 | Tushare daily_basic | - | - | 无替代，标记 FAILED |

### 数据源修改规则
1. **禁止修改已验证的新浪代码**（DINIW、fx_susdcny、hf_GC、hf_CL、int_dji、rt_hkHSTECH等）
2. **禁止修改已验证的解析格式**（字段索引、计算逻辑）
3. **禁止修改已验证的新闻URL和提取规则**
4. 如需新增数据源，必须先调用真实API验证返回格式，再修改代码
5. 修改后必须运行 `python fetch_data.py evening` 全量测试
6. 修改后必须运行 `python extract_summary.py` 验证摘要输出
7. 修改后必须运行 `python validate_report.py` 验证校验逻辑

### 已知限制
- Tushare limit_list_d 需5000+积分，当前Token积分不足，使用东方财富涨停池替代
- Tushare trade_cal 可能标记调休日为交易日但实际无数据，margin已实现自动回滚
- 央视网新闻联播页面是JS动态加载，使用 day/YYYYMMDD.shtml 页面提取 title 属性

### 财联社浏览器采集说明（步骤0.5）
财联社深度头条、VIP文章、投资日历、首页均为JS渲染页面，Python requests无法获取内容。
自动化任务中必须使用浏览器工具（browser_navigate + browser_evaluate）采集。

**采集流程：**
1. 依次访问4个URL，使用 browser_navigate 导航到页面
2. 等待页面加载完成（等待2-3秒）
3. 使用 browser_evaluate 执行 `document.body.innerText` 提取页面文本
4. 将4个页面的文本保存为JSON格式到 `data/cls_pages.json`

**cls_pages.json 格式：**
```json
{
  "深度头条": "页面innerText文本...",
  "VIP文章": "页面innerText文本...",
  "投资日历": "页面innerText文本...",
  "首页": "页面innerText文本..."
}
```

**4个URL：**
- 深度头条: `https://www.cls.cn/depth?id=1000`
- VIP文章: `https://www.cls.cn/vip`
- 投资日历: `https://www.cls.cn/investkalendar`
- 首页: `https://www.cls.cn/`

**注意：** 电报（telegraph）由 fetch_data.py 通过 /api/cache 接口直接获取，无需浏览器采集。

---

## 六、报告格式要求

### 通用要求
- 字符数 >= 6000
- 使用 Markdown 格式
- 保存路径: `reports/YYYY-MM-DD_报告类型.md`

### 晨报标题
- "多维市场研报（晨报）"
- 侧重：隔夜美股映射、地缘政治变化、VIP新信号、开盘策略、预判型金股

### 午报标题
- "多维市场研报（午报）"
- 侧重：上午盘面验证、早盘预判验证、连板梯队变化、下午操作策略、盘中确认型金股

### 晚报标题
- "多维市场研报（晚报）"
- 侧重：全天复盘+次日预判、美股盘前动态、官方媒体头条舆情、龙虎榜机构动向、次日布局型金股

---

## 七、历史教训（必须记住）

### 教训1：数据编造事件（2026-06-22）
- **问题**：AI在生成晚报时完全无视了采集到的真实数据，编造了虚假的指数点位（真实4163.10写成3382.28）、虚假的涨跌幅、虚假的股票代码（688XXX占位符）
- **根因**：原始数据文件24万行，AI无法有效读取；缺少中间摘要层；缺少校验机制
- **修复**：新增 extract_summary.py（数据摘要）+ validate_report.py（报告校验）+ SKILL.md红线规则

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
- **修复**：改为 hktech，并添加降级代码列表

### 教训5：新闻采集只保存HTML
- **问题**：只保存了HTML原始文本，没有提取有效标题
- **修复**：新增正则提取 title/h1/h2/h3 标签文本

### 教训6：修复不端到端验证
- **问题**：修改了脚本但忘了同步更新自动化任务的 message，导致修复无效
- **修复**：每次修改后必须端到端验证完整流程，并同步更新所有自动化任务

### 教训7：报告退化为数据罗列（2026-06-23）
- **问题**：研报逐渐偏离初衷，变成简单罗列数据并粗暴摘出涨停板最猛的股票作为金股，缺乏推理链
- **根因**：缺少信源驱动分析框架，没有将财联社信号与市场数据交叉验证
- **修复**：新增第零章（财联社信源扫描），重写analysis_prompt.md强化推理链，金股必须满足信号+验证双重标准

### 教训8：财联社信源遗忘（2026-06-23）
- **问题**：财联社作为研报核心特色信源被完全遗忘，未集成到数据采集流程
- **根因**：开发过程中专注于修复数据源问题，忽略了信源整合
- **修复**：fetch_data.py新增财联社电报API采集+浏览器页面采集，extract_summary.py新增第零章财联社信源扫描

### 教训9：修改未推送GitHub导致自动任务失效（2026-06-25）
- **问题**：在本地修改了代码（钱三强选股集成、推送方式修改等），但未git commit & push到GitHub。自动任务执行git pull时拉取的是旧代码，导致晨报没有钱三强选股功能
- **根因**：修改代码后遗漏了推送到远程仓库这一关键步骤
- **修复**：**每次修改代码后，必须执行 git add → git commit → git push origin main，确保自动任务能拉取到最新代码**
- **红线**：代码修改未推送到GitHub = 等于没修改。本地测试通过不等于自动任务能用

---

## 八、飞书推送配置

- Webhook 地址保存在 `config.json` 中（持久化）
- 首次配置：`python push_feishu.py --config 'https://open.feishu.cn/open-apis/bot/v2/hook/xxx'`
- 自动化任务执行时会自动从 config.json 读取，无需每次重新配置
