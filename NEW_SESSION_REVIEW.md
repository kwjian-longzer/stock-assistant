# 新Session工作评审与背景对齐

> 审核时间: 2026-06-27
> 审核对象: 新session提交的PENDING_ITEMS.md + 测试报告 + 思考过程
> 结论: 工作量大且质量高，但存在**一个致命问题**需立即修复

---

## 一、致命问题：克隆了错误版本的代码

### 问题

新session克隆仓库时HEAD在 `6ccbf94`，**落后最新代码3个commit**：

```
最新代码（本session）:
0b4eabf docs: v4.0项目规划书           ← 你收到了这个文件
c6be237 docs: 更新SESSION_SUMMARY v3.2 + HANDOVER v3  ← 你没看到这个
40de9b8 feat: 发现CLS红色电报端点       ← ★致命缺失★
49bdd83 fix: CLS电报高频轮询模式
─────────── 你克隆的版本 ───────────
6ccbf94 feat: 洞见逻辑固化到报告流程 + NLP优化
```

### 后果

你的测试报告显示 **"近24h电报20条，红色0条"**——这正是我们上一session花了大量精力解决的问题。

**缺失的 `40de9b8` commit 包含**：

1. **CLS红色电报端点发现**（核心突破）：
   ```python
   # 你的版本（6ccbf94）仍在用：
   url = "/api/cache?app=CailianpressWeb&name=telegraph&os=web&sv=8.7.9"
   # 固定返回最新20条，无向后翻页，无category过滤

   # 最新版本（40de9b8）改为：
   url = "/v1/roll/get_roll_list?category=red&last_time=X&refresh_type=1&rn=50"
   # ✅ category=red: 只返回加红重要电报
   # ✅ last_time + refresh_type=1: 向后翻页
   # ✅ rn=50: 单次50条
   # 3页翻页即可覆盖24小时，获取150+条红色电报
   ```

2. **is_red判断修正**：
   ```python
   # 你的版本（错误）:
   is_red = 1 if (item.get('color', '') == 'red' or item.get('level', '') == 'red') else 0

   # 最新版本（正确）:
   is_red = 1 if level in ('A', 'B') else 0
   # CLS电报的level字段: A/B=加红, C=普通
   # color字段不是判断加红的正确字段
   ```

3. **NLP优化**（sector_tags 40→80+, 否定语境检测, 百分比提取）

### 修复方法

```bash
# 在你的仓库中执行：
git fetch origin
git merge origin/main  # 或 git pull origin main

# 如果远程仓库没有最新代码（因为还没push），则需要手动应用：
# 从 /workspace/stock-assistant/cls_collector.py 复制以下函数到你的版本：
# - _fetch_telegraph_page_red()    # 红色电报端点
# - _fetch_telegraph_page_all()    # 全部电报端点
# - _process_telegraph_items()     # 公共处理逻辑
# - collect_telegraphs()           # 重写后的采集函数（向后翻页）
```

**最新cls_collector.py位于**: `/workspace/stock-assistant/cls_collector.py`（本workspace的版本是最新的）

---

## 二、项目背景：这是在v3.2基础上的升级

### 关键认知

**这个项目不是从零开始的新项目，而是在v3.2运行中的系统上做升级。**

v3.2已有以下可运行组件：

| 组件 | 状态 | 说明 |
|------|------|------|
| `cls_collector.py` | ✅ 运行中 | 每小时定时采集，已通过Schedule配置 |
| `db.py` | ✅ 运行中 | 12表，已积累183条电报+773只VIP股票 |
| `insight_extractor.py` | ✅ 运行中 | 5类信号提取+跨市场映射 |
| `fetch_data.py` | ✅ 运行中 | 采集Tushare全市场数据（写JSON，待改DB） |
| `extract_summary.py` | ✅ 运行中 | 生成data_summary.json，已注入insights |
| `heat_tracker.py` | ✅ 运行中 | 热度量化追踪 |
| `qian_sanqiang_selector.py` | ✅ 运行中 | 钱三强选股 |
| `vip_extractor.py` | ✅ 运行中 | VIP股票发现（含fxbaogao二层搜索） |
| `push_feishu.py` | ✅ 运行中 | 飞书推送（只推卡片+链接） |
| `validate_report.py` | ✅ 运行中 | 12条红线校验 |
| `report_quality_evaluator.py` | ✅ 运行中 | 10维度评分 |
| `site_builder.py` | ✅ 运行中 | 报告→网站JSON |
| `gold_stock_backtest.py` | ✅ 运行中 | 金股T+1回测 |
| 网站 `docs/` | ✅ 运行中 | 7页面SPA，观澜深色主题 |

### 已有的定时任务（6个，正在运行）

| 时间 | 任务 | 说明 |
|------|------|------|
| `0 * * * *` | CLS电报采集 | `python cls_collector.py --poll` 每小时 |
| `0 6 * * 1-5` | 早盘舆情分析 | 周一至五06:00 |
| `30 11 * * 1-5` | 午盘舆情分析 | 11:30 |
| `0 20 * * 1-5` | 晚报舆情分析 | 20:00 |
| `0 20 * * SAT` | 周报(周六) | |
| `0 20 * * SUN` | 周报(周日) | |

**注意**: 这些任务使用的是v3.2的报告流程（Agent写报告），不是v4.0的report_generator.py。v4.0升级时需要逐步替换。

---

## 三、环境初始化信息

### 3.1 config.json（必须创建）

**文件位置**: `/workspace/stock-assistant/config.json`（已在.gitignore中）

```json
{
    "tushare_token": "你的Tushare Pro token",
    "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
    "feishu_app_id": "cli_xxx",
    "feishu_app_secret": "xxx",
    "fxbaogao_api_key": "sk-xxx",
    "site_url": "https://用户名.github.io/stock-assistant"
}
```

**新session已经创建了这个文件**（从思考过程可见），凭证来源正确。

### 3.2 数据库

- 文件: `/workspace/stock-assistant/data/stock.db`
- **已在.gitignore中**，所以git clone后是空的，需要重新初始化
- v3.2已有12张表，v4.0需要新增8表
- 运行 `python db.py` 或 `python -c "from db import DB; DB().init_tables()"` 初始化

### 3.3 Git Push

- 远程仓库: `https://github.com/kwjian-longzer/stock-assistant.git`
- **需要配置GitHub PAT才能push**（新session已发现此问题）
- 配置方法: GitHub → Settings → Developer settings → Personal Access Token → 生成token → `git remote set-url origin https://TOKEN@github.com/kwjian-longzer/stock-assistant.git`

### 3.4 仓库改Public + GitHub Pages

1. GitHub → Settings → General → Danger Zone → Change visibility → Public
2. GitHub → Settings → Pages → Source → Deploy from branch → `main` / `docs`
3. 等待5-10分钟，访问 `https://用户名.github.io/stock-assistant/`

---

## 四、对新session工作状态的评估

### 4.1 做得好的地方

1. **执行速度快**: 在一个session中完成了T1-T6全部任务，25个文件变更
2. **E2E测试通过**: 采集→洞见→金股→报告→校验→评分→推送 全链路验证
3. **质量迭代**: 3轮迭代从80分提升到92分，验证全部通过
4. **api_server.py设计**: 15个REST端点，从DB直接读取数据
5. **learning_loop.py**: 盘后预判验证+经验固化+金股回测
6. **待定事项文档**: 清晰列出了需要人工介入的3件事

### 4.2 存在的问题

| # | 问题 | 严重程度 | 原因 |
|---|------|---------|------|
| 1 | **克隆了旧代码(6ccbf94)** | 🔴致命 | 没有获取最新commit(40de9b8) |
| 2 | **红色电报=0条** | 🔴致命 | 缺失红色电报端点 + is_red判断错误 |
| 3 | **电报仅20条** | 🔴致命 | 仍在用/api/cache固定20条端点 |
| 4 | **未看到HANDOVER v3** | 🟡中等 | 克隆的版本没有c6be237的更新 |
| 5 | **LLM API Key缺失** | 🟡中等 | 不影响当前，但限制全自动生成 |
| 6 | **Git push未配置** | 🟡中等 | 需要用户配置PAT |
| 7 | **金股仅1只** | 🟢低 | 数据不足导致，会随时间改善 |
| 8 | **热度曲线为估算** | 🟢低 | 历史数据未积累，会自然改善 |

### 4.3 报告质量评估

新session生成的晨报（92分）结构完整，包含：
- ✅ 隔夜外盘+跨市场映射
- ✅ A股前日复盘
- ✅ 财联社舆情（但数据不足：仅20条电报，0红色）
- ✅ 5大热点+热度曲线
- ✅ 龙虎榜+北向资金
- ✅ 2只金股（潜龙在渊+飞龙在天）
- ✅ 8处交叉验证
- ✅ 三情景预判

**核心缺陷**: "近24h电报20条，红色0条"——如果修复红色电报端点，电报数据将增加到150+条，红色电报会有50-100条，洞见深度和信号提取质量将大幅提升。

---

## 五、回答新session的问题

### Q1: GitHub推送凭据

**答**: 需要用户配置GitHub Personal Access Token。

```bash
# 用户在GitHub创建PAT后执行：
git remote set-url origin https://PAT_TOKEN@github.com/kwjian-longzer/stock-assistant.git
git push origin main
```

**重要**: push之前，需要先合并本session的最新代码（40de9b8 commit中的红色电报端点），否则push会覆盖远程仓库的最新版本。

### Q2: 仓库改Public + 开启Pages

**答**: 需要用户手动操作：
1. GitHub → Settings → Change visibility → Public
2. GitHub → Settings → Pages → Source: main / docs

### Q3: LLM API Key

**答**: 当前项目的设计是**Agent（TRAE）作为LLM**写报告，不需要外部API Key。

报告生成流程：
1. `report_generator.py --prepare` 准备data_summary.json
2. **Agent（TRAE session）读取data_summary.json + analysis_prompt.md 写报告**
3. `report_generator.py --finalize` 校验+评分+推送

如果需要完全无人值守的自动生成（不通过TRAE Agent），则需要配置LLM API Key。但当前架构下，Schedule任务触发的是TRAE session中的Agent来写报告。

### Q4: Tushare积分不足

**答**: `limit_list_d`（涨停板）需要5000积分。当前用东方财富API替代是合理方案。用户后续可充值积分。

### Q5: 定时任务时间确认

**答**: 当前已有6个Schedule任务在运行（见上方第二节）。v4.0升级时应该**替换**而非新增：

| v3.2任务 | v4.0替换 |
|---------|---------|
| 早盘舆情分析(06:00) | data_collector morning(08:30) + report_generator morning(08:35) |
| 午盘舆情分析(11:30) | data_collector noon(11:50) + report_generator noon(11:55) |
| 晚报舆情分析(20:00) | data_collector evening(15:30) + report_generator evening(15:35) |
| 周报(周六/周日 20:00) | report_generator weekend |
| CLS电报采集(每小时) | 保留不变 |

### Q6: push_feishu.py CLI参数不匹配

**答**: 需要统一参数格式。建议push_feishu.py统一使用 `--file <path>` 参数。

---

## 六、项目目标对齐

### 核心目标

> **用户原话**："每做一步就要回顾是否有利于接近靶标的要求"

**靶标**: 自动化任务按时更新数据 → 生成报告 → 网站可视化交付 → 飞书只推链接+简报

### v4.0升级的本质

v4.0不是推翻重写，而是**在v3.2运行系统上的增量升级**：

| 维度 | v3.2 | v4.0 | 升级方式 |
|------|------|------|---------|
| 数据流 | 脚本→JSON文件 | 脚本→DB | data_collector.py替代fetch_data.py |
| 电报 | /api/cache 20条 | /v1/roll 150+条 | cls_collector.py已升级(40de9b8) |
| 洞见 | 独立脚本 | 集成DB | insight_engine.py替代insight_extractor.py |
| 报告 | 5个独立任务 | 1个多时点入口 | report_generator.py统一入口 |
| 网站 | 静态JSON | API驱动 | api_server.py + 网站重构 |
| 学习 | 无 | 盘后验证 | learning_loop.py |

### 质量宪法（SKILL.md核心原则）

1. **数据唯一来源**: DB优先，API次之，严禁编造
2. **每步可验证**: 每个组件有独立测试入口
3. **推理链完整**: 信号→验证→洞见→策略，不允许断链
4. **概率表述**: 大概率(>70%) / 倾向于(50-70%) / 值得警惕(30-50%) / 尚需验证(<30%)

---

## 七、下一步行动建议

### 立即执行（修复致命问题）

```bash
# 1. 获取最新代码（如果远程已push）
cd /workspace/stock-assistant  # 或你clone的目录
git pull origin main

# 2. 如果远程没有最新代码，从本workspace复制cls_collector.py
cp /workspace/stock-assistant/cls_collector.py ./cls_collector.py

# 3. 重新运行电报采集验证
python cls_collector.py --telegraph
# 应该看到: 150+条红色电报，覆盖24小时

# 4. 重新运行insight_engine
python insight_engine.py --period morning
# 应该看到: 红色电报50+条，信号大幅增加

# 5. 重新生成报告
python report_generator.py --period morning --prepare
# Agent写报告
python report_generator.py --period morning --finalize
```

### 后续优化

1. **合并v3.2现有数据**: 本workspace的data/stock.db有183条电报数据，可以复制过去
2. **验证6个Schedule任务**: 确保v4.0任务与v3.2任务平滑切换
3. **金股数量提升**: 随着多时点采集+数据积累，金股会自然增加到5-8只
4. **热度曲线真实化**: 运行数日后heat_tracking表自动积累历史数据

---

## 八、关于SESSION_SUMMARY和HANDOVER

### 新session是否需要这两个文件？

| 文档 | 是否需要 | 说明 |
|------|---------|------|
| **PROJECT_PLAN_v4.md** | ✅ 需要 | 主执行文档，已有 |
| **HANDOVER.md** | ✅ 需要 | 环境配置+凭证，**需要v3版本**（commit c6be237） |
| **SESSION_SUMMARY.md** | ❌ 不需要 | 仅历史参考，不影响执行 |
| **SKILL.md** | ✅ 需要 | 项目宪法，新session已更新v4 |
| **analysis_prompt.md** | ✅ 需要 | 报告规范，新session已更新v4 |

**关键**: HANDOVER.md的v3版本（包含CLS红色电报端点详情、API对比表、--poll模式说明）在commit c6be237中，新session克隆的6ccbf94版本没有这些内容。

**最新HANDOVER.md位于**: `/workspace/stock-assistant/HANDOVER.md`（本workspace的版本是v3）
