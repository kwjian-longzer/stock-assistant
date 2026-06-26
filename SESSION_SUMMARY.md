# 会话压缩总结（v3 最终版）

> 生成时间: 2026-06-26
> 涉及版本: v2.0-final → v3.0 网站化 → v3.1 数据基础设施
> 最新commit: 31ba4df
> 仓库: https://github.com/kwjian-longzer/stock-assistant.git

---

## 一、项目靶标

> 用户原话："每做一步就要回顾是否有利于接近靶标的要求"

**靶标**：自动化任务按时更新数据 → 生成报告 → 网站可视化交付 → 飞书只推链接+简报。

---

## 二、任务指示与执行结果

### 第一阶段：v2.0 功能完善（前序会话）

| # | 用户要求 | 执行结果 | 状态 |
|---|---------|---------|------|
| 1 | VIP文章从内容搜索发现股票 | vip_extractor.py: stock_company.main_business全文搜索 | ✅ |
| 2 | 增加时间-热度变化曲线 | heat_tracker.py: 动态选板块+EMA(alpha=0.4)平滑 | ✅ |
| 3 | 金股结构化呈现+回测 | analysis_prompt.md + gold_stock_backtest.py | ✅ |
| 4 | 每日50篇VIP文章采集 | fetch_data.py分页5页89篇取50 | ✅ |
| 5 | 热度按天计，资金流向为主 | 双因子模型(资金净流入+涨停数量) | ✅ |
| 6 | 参考观澜网站 | 深色主题#0a0e17+CSS变量 | ✅ |

### 第二阶段：v3.0 网站化重构

| # | 用户要求 | 执行结果 | 状态 |
|---|---------|---------|------|
| 7 | 报告可视化，互动网页交付 | 7页面SPA(index.html+app.js+charts.js) | ✅ |
| 8 | 动态选前线热点板块（非固定10个） | **修复**: 3处DEFAULT_SECTORS硬编码→None触发动态选择 | ✅ commit 2dba213 |
| 9 | 数据平滑减少波动 | EMA平滑+5日累计，export含heat_raw/capital_raw | ✅ |
| 10 | 按日期查看早中晚报 | 日期选择器+5种报告类型 | ✅ |
| 11 | 查看累积历史数据（金股等） | 页面完成，数据随时间积累 | ⚠️ 积累中 |
| 12 | 飞书只推链接+简报 | **修复**: 删除全文MD推送，只保留Webhook卡片 | ✅ commit 2dba213 |
| 13 | 金股追踪必须跑回测 | **修复**: T+1逻辑(跳过当日推荐+1条数据不计算) | ✅ commit 2dba213 |
| 14 | GitHub Pages部署 | 公开仓库免费可行 | ⏳ 待用户开启 |

### 第三阶段：数据基础设施（v3.1）

| # | 用户要求 | 执行结果 | 状态 |
|---|---------|---------|------|
| 15 | 采用数据库+时间戳防止误用 | db.py: SQLite 12张表 + get_or_fetch缓存策略 | ✅ commit 31ba4df |
| 16 | 财联社数据独立每小时采集 | cls_collector.py: 电报+VIP+深度+日历，每小时定时 | ✅ commit 31ba4df |
| 17 | 电报结构化+时间戳(电报头时间) | timestamp(发布时间)+fetch_time(入库时间)+NLP 4字段 | ✅ commit 31ba4df |
| 18 | 设计让Agent推理洞见的结构化数据 | event_type/sentiment/impact_level/sector_tags + 共振分析 | ✅ commit 31ba4df |
| 19 | 补充发现报告API定位VIP文章股票 | fxbaogao MCP HTTP API二层搜索(已验证) | ✅ commit 31ba4df |

---

## 三、问题清单与解决方式

### 3.1 已解决问题

| 问题 | 根因 | 解决方式 | Commit |
|------|------|---------|--------|
| 动态选板块未接线 | 3处代码硬编码DEFAULT_SECTORS | 改为None，触发select_dynamic_sectors() | 2dba213 |
| 飞书仍推全文MD | Open API推送块未删除 | 删除整个send_file_via_open_api | 2dba213 |
| 金股回测全返回None | 推荐日=今天，无后续数据 | T+1跳过逻辑+1条数据不计算收益 | 2dba213 |
| Token硬编码(7个文件) | 历史遗留 | settings.py + config.json(gitignored) | 2e9f7f6 |
| 电报非24小时覆盖 | 仅任务触发时拉取 | cls_collector.py每小时独立采集 | 31ba4df |
| VIP股票发现不全 | 仅搜Tushare main_business | 补充fxbaogao MCP API二层搜索 | 31ba4df |
| 无数据库缓存 | 4脚本各自调Tushare | db.py: get_or_fetch缓存策略 | 31ba4df |

### 3.2 未解决问题（待后续处理）

| 问题 | 根因 | 影响等级 | 待办编号 |
|------|------|---------|---------|
| 自动化任务凌晨3:32执行午报 | Schedule cron设置错误 | 高 | P1 |
| 午报A股指数显示前日数据 | Tushare daily返回前日收盘 | 高 | P0-3 |
| data_summary.json无字段级时间戳 | extract_summary.py未改造 | 中 | P0-5 |
| fetch_data.py未走db.py缓存 | 仍直接调Tushare API | 中 | P0-3 |
| 共振分析用本次采集非查库 | extract_summary.py未改造 | 中 | P0-6 |
| 前端CDN缓存 | GitHub Pages CDN | 低 | P2 |
| GitHub Pages未开启 | 仓库仍为私有 | 高 | P1 |

---

## 四、关键技术决策

### 4.1 数据库设计（db.py）

12张表，核心设计原则：

| 表 | 用途 | 时间戳策略 |
|---|------|-----------|
| raw_cache | 原始API数据缓存 | fetch_time + TTL(12h) |
| cls_telegraph | 财联社电报 | timestamp(发布时间) + fetch_time(入库) |
| cls_telegraph_stock | 电报关联股票 | 外键关联telegraph_id |
| cls_vip_article | VIP文章 | published_at + fetch_time |
| vip_discovered_stock | VIP股票发现 | match_source(tushare/fxbaogao/both) |
| gold_stock | 金股+回测 | recommend_date + backtest_time |
| index_quote | 指数行情 | trade_date + is_realtime标记 |
| sector_moneyflow | 板块资金 | trade_date |
| limit_up / dragon_tiger | 涨停/龙虎榜 | trade_date |
| north_money / margin | 北向/融资融券 | trade_date |

### 4.2 电报结构化NLP（cls_collector.py）

轻量级关键词匹配，无需外部模型：

| 字段 | 分类逻辑 | 覆盖率 |
|------|---------|--------|
| event_type | 6类关键词(政策/财报/并购/研报/数据/公告) | ~80% |
| sentiment | 正负面关键词计数对比 | ~75% |
| impact_level | 红色标记 + 高影响关键词 | ~85% |
| sector_tags | 40+行业标签文本匹配 | ~70% |

### 4.3 发现报告API（fxbaogao）

正确调用格式（经实际验证）：
- 端点: `https://api.fxbaogao.com/mcp/`
- 协议: JSON-RPC 2.0 (`tools/call` 方法)
- 认证: `Authorization: Bearer sk-xxx`
- 字段名: camelCase (`keywords`, `startTime`, `endTime`, `orgNames`)
- 返回: `result.content[0].text` → JSON字符串 → 研报列表

### 4.4 共振分析（db.query_resonance）

跨4个数据源交叉匹配：
1. 电报提及股票 (cls_telegraph_stock)
2. 龙虎榜 (dragon_tiger)
3. 涨停板 (limit_up)
4. VIP文章发现 (vip_discovered_stock)

**共振定义**: 同一股票出现在 ≥2 个数据源中。数据源越多，信号越强。

---

## 五、用户需手动操作

| # | 操作 | 步骤 | 优先级 |
|---|------|------|--------|
| 1 | 仓库改Public | GitHub → Settings → Danger Zone → Change visibility | 高 |
| 2 | 开启Pages | GitHub → Settings → Pages → Source: main /docs | 高 |
| 3 | 重设报告Schedule | 午报 0 11 * * 1-5（当前凌晨3:32错误） | 高 |

**已自动设置**:
- 财联社每小时采集: `0 * * * *` → `python cls_collector.py`

---

## 六、待办任务清单

### P0: 数据基础设施（3项待完成）

| 编号 | 任务 | 依赖 | 说明 |
|------|------|------|------|
| P0-3 | 改造fetch_data.py走db.py | db.py ✅ | 所有Tushare调用改get_or_fetch |
| P0-5 | extract_summary.py加字段级时间戳 | 无 | data_summary.json每块加data_time |
| P0-6 | 共振分析改为从数据库查询 | db.py ✅ | 替换extract_summary.py中现抓逻辑 |

### P1: 部署

| 编号 | 任务 | 说明 |
|------|------|------|
| P1-1 | 仓库改Public | 用户手动操作 |
| P1-2 | 开启GitHub Pages | 用户手动操作 |
| P1-3 | 重设报告Schedule cron时间 | 修正午报等时间 |

### P2: 体验优化

| 编号 | 任务 |
|------|------|
| P2-1 | 前端防缓存 (app.js fetch加?v=时间戳) |
| P2-2 | 观澜海面曲线样式 |
| P2-3 | 金股回测数据积累 |

---

## 七、历史教训

| # | 教训 | 后果 | 防范 |
|---|------|------|------|
| 1 | 子agent返回"✅完成"未实际验证 | 7项假完成 | 必须grep/运行/py_compile验证 |
| 2 | 硬编码Token | 安全风险 | settings.py + config.json |
| 3 | 固定板块列表 | 数据拥挤 | 动态选择Top6 |
| 4 | Tushare daily午间返回前日 | 数据错误 | 午报改用新浪实时接口 |
| 5 | 仅搜公司介绍找股票 | 遗漏研报提及公司 | 补充fxbaogao二层搜索 |
| 6 | 任务触发才采集电报 | 非24小时覆盖 | 独立每小时采集任务 |

---

## 八、文件清单

### 核心脚本（14个Python文件）

| 文件 | 功能 | 状态 |
|------|------|------|
| `settings.py` | 统一配置管理 | ✅ 含5个getter函数 |
| `db.py` | SQLite缓存层(12表) | ✅ 新增 |
| `cls_collector.py` | 财联社独立采集器 | ✅ 新增 |
| `fetch_data.py` | 数据采集主脚本 | ⏳ 待改造走db.py |
| `extract_summary.py` | 数据摘要提取 | ⏳ 待加时间戳+查库 |
| `heat_tracker.py` | 热度量化追踪 | ✅ 已修复 |
| `vip_extractor.py` | VIP股票发现 | ✅ 含fxbaogao二层搜索 |
| `qian_sanqiang_selector.py` | 钱三强选股 | ✅ |
| `site_builder.py` | 报告→网站JSON | ✅ |
| `gold_stock_backtest.py` | 金股回测 | ✅ T+1已修复 |
| `push_feishu.py` | 飞书推送 | ✅ 只推卡片 |
| `validate_report.py` | 12条红线校验 | ✅ |
| `report_quality_evaluator.py` | 10维度评分 | ✅ |

### 配置与文档

| 文件 | 用途 |
|------|------|
| `config.json` | 凭证(gitignored) |
| `SKILL.md` | 项目宪法 |
| `HANDOVER.md` | 工作交接单 |
| `SESSION_SUMMARY.md` | 本文件 |
| `analysis_prompt.md` | 报告写作规范 |

### 网站文件

| 文件 | 用途 |
|------|------|
| `docs/index.html` | SPA入口(7页面) |
| `docs/assets/app.js` | 路由/导航/数据加载 |
| `docs/assets/charts.js` | 3个ECharts图表 |
| `docs/assets/styles.css` | 观澜深色主题 |
| `docs/data/*.json` | 网站数据(自动生成) |

---

## 九、凭证一览

| 服务 | 凭证 | 获取方式 |
|------|------|---------|
| Tushare Pro | token (config.json) | tushare.pro注册 |
| 财联社CLS | 无需token | API直接调用 |
| 新浪财经 | 无需token | HTTP直接调用 |
| 发现报告fxbaogao | api_key (config.json) | fxbaogao.com高级VIP |
| 飞书Webhook | URL (config.json) | 群机器人配置 |
| 飞书Open API | app_id+app_secret (config.json) | 飞书开放平台 |

**新环境必须创建 `/workspace/stock-assistant/config.json`**，内容见 HANDOVER.md 第2.1节。
