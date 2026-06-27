# VIP股票发现 v4 升级文档

> 创建时间：2026-06-27
> 状态：已完成验证，已集成到项目
> 协作Agent：请git pull获取最新代码

---

## 一、升级背景

### 问题：v3搜索思路的致命缺陷

用豆包案例测试v3代码发现三个致命问题：

**案例标题**："算力芯片、光模块、先进封装拉动粉体材料需求，这家公司产品已经切入PCB镀铜+光模块锡粉+芯片散热领域，未来有望在MLCC镍粉突破"

**正确答案**：有研粉材（688456），科创板，4条业务线索全部匹配

**v3结果**：

| 指标 | v3结果 | 豆包结果 |
|------|--------|---------|
| 有研粉材排名 | 第6位(score=10) | 第1位 |
| 排第1的股票 | 博迁新材(score=16) | 有研粉材 |
| fxbaogao能否找到 | 3组关键词全部未找到 | 直接搜索到 |
| 线索验证 | 无 | 逐条验证+证据 |

### 根因分析

1. **提取概念关键词而非业务线索**：v3提取"光模块""芯片""粉体"等泛概念，而非"PCB镀铜""光模块锡粉"等具体业务线索
2. **fxbaogao搜索用概念关键词**：用概念关键词搜索返回泛行业研报，找不到目标公司
3. **Tushare main_business太泛**：有研粉材的main_business是"有色金属粉体材料的设计、研发、生产和销售"，不含具体业务线索
4. **评分基于关键词出现次数**：博迁新材因含"粉体""锡粉""镍粉"得16分排第一，但没有PCB镀铜和光模块业务

---

## 二、v4改进方案

### 核心改进：四层架构

```
VIP文章(标题+简介)
    │
    ▼
① 结构化解析：领域/热点 → 业务线索 → 事件类型
    │  提取"PCB镀铜""光模块锡粉"等复合短语
    │  识别产品突破/切入供应链等事件类型
    │
    ▼
② 多源动态搜索（5个数据源）
    │  东方财富公告API(5分) ← 公司官方披露，含全文
    │  WebSearch(5分)         ← 互动易Q&A，最权威
    │  CLS电报(4分)           ← 实时采集
    │  fxbaogao调研纪要(4分)  ← 公司最新动态
    │  fxbaogao研报(3分)      ← 深度分析
    │
    ▼
③ 加权线索验证
    │  逐条线索在多源中验证
    │  同义词匹配(锡粉↔锡膏)
    │  复合线索拆分(光模块锡粉→光模块+锡粉)
    │  多源交叉验证加分
    │
    ▼
④ 排除逻辑 + 匹配率排序
    │  匹配率<25%排除
    │  仅匹配通用概念排除
    │  证据来自目录/图表降权
```

### 新增数据源

#### 1. 东方财富公告API（免费，无需认证）

```python
# API端点
url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
# 参数
params = {"stock_list": "688456", "page_size": "10"}
# 返回：公告列表+全文内容（通过art_code获取详情）
# 详情API
detail_url = f"https://np-cnotice-stock.eastmoney.com/api/content/ann?art_code={art_code}"
```

**验证结果**：有研粉材获取到10条公告，其中投资者关系活动记录表直接包含"电子级氧化铜粉下游应用于PCB""散热铜粉已成功应用于AI算力服务器"等关键业务线索。

**替代方案**：Tushare的anns_d/irm_qa_sh/news/major_news需要5000+积分，东方财富API已完全覆盖公告获取需求。

#### 2. WebSearch集成（Agent调用模式）

WebSearch无法在Python脚本中直接调用，采用**Agent调用+脚本验证**模式：

```python
# Agent主流程
from vip_search_v4 import build_web_search_queries, discover_stocks_v4

# Step 1: 构建查询
queries = build_web_search_queries("有研粉材", ["PCB镀铜", "光模块锡粉"])
# 返回: ["有研粉材 互动易 投资者问答 PCB镀铜 光模块锡粉", ...]

# Step 2: Agent调用WebSearch工具（在TRAE会话中）
web_results = []
for q in queries:
    results = WebSearch(q)  # Agent调用WebSearch工具
    web_results.extend(results)

# Step 3: 传入脚本验证
kept, excl = discover_stocks_v4(title, brief, web_search_results=web_results)
```

**验证结果**：WebSearch为有研粉材找到7条匹配结果，包括：
- 上证e互动投资者问答（确认"PCB互连、芯片互连"）
- 查股网董秘爆料（确认"散热铜粉已应用于昇腾910芯片"）
- 投资者关系活动记录表（确认"与华为联合开发散热铜粉"）

---

## 三、验证结果对比

### 有研粉材案例

| 指标 | v3 | v4 | 提升 |
|------|----|----|------|
| 排名 | 第6位 | **第1位** | +5位 |
| 分数 | 10 | **189** | 18倍 |
| 匹配率 | N/A | **80%** | - |
| 数据源数 | 1 | **5** | +4 |
| 证据可追溯 | 否 | **是** | - |

### v4线索验证详情

```
有研粉材(688456) 总分=189 匹配率=80%
  [算力]        分=17  来源=fxbaogao_ir,fxbaogao_report,cls_telegraph
  [光模块]      分=11  来源=fxbaogao_ir,fxbaogao_report,cls_telegraph
  [芯片]        分=11  来源=fxbaogao_ir,fxbaogao_report
  [粉体]        分=20  来源=fxbaogao_ir,fxbaogao_report,eastmoney_ann,web_search
  [PCB]         分=20  来源=fxbaogao_ir,fxbaogao_report,eastmoney_ann,web_search
  [锡粉]        分=8   来源=fxbaogao_report
  [镍粉]        分=10  来源=fxbaogao_report,cls_telegraph
  [散热]        分=12  来源=fxbaogao_ir,fxbaogao_report
```

---

## 四、文件变更清单

### 新增文件

| 文件 | 说明 |
|------|------|
| `vip_search_v4.py` | v4发现引擎主模块（独立运行+Agent集成） |

### 修改文件

| 文件 | 变更 | 说明 |
|------|------|------|
| `vip_extractor.py` | +33行 | 末尾添加`discover_stocks_v4()`兼容入口 |

### 未修改文件

| 文件 | 说明 |
|------|------|
| `db.py` | 无需修改，v4结果可写入现有vip_discovered_stock表 |
| `cls_collector.py` | 无需修改，v4复用CLS电报数据 |
| `settings.py` | 无需修改，复用现有fxbaogao_api_key |

---

## 五、协作Agent接入指南

### 新Session执行步骤

```bash
# 1. 拉取最新代码
git pull origin main

# 2. 验证v4模块可用
python -c "from vip_search_v4 import discover_stocks_v4; print('OK')"

# 3. 运行测试
python vip_search_v4.py
```

### 在Agent流程中集成

```python
# 方式1：直接调用v4（推荐）
from vip_search_v4 import discover_stocks_v4, build_web_search_queries

# 构建WebSearch查询
queries = build_web_search_queries(company_name, clues)

# Agent执行WebSearch（在TRAE会话中）
web_results = []
for q in queries:
    results = WebSearch(q)  # Agent工具调用
    web_results.extend(results)

# 传入v4发现引擎
kept, excl = discover_stocks_v4(title, brief, web_search_results=web_results)

# 方式2：通过vip_extractor兼容入口
from vip_extractor import discover_stocks_v4
kept, excl = discover_stocks_v4(title, brief, web_search_results=web_results)
```

### 数据源权重配置

```python
# vip_search_v4.py 中的 SOURCE_WEIGHTS
SOURCE_WEIGHTS = {
    "web_search": 5,       # WebSearch（互动易Q&A、新闻）
    "eastmoney_ann": 5,    # 东方财富公告（公司官方披露）
    "cls_telegraph": 4,    # CLS电报（实时采集）
    "fxbaogao_ir": 4,     # fxbaogao调研纪要
    "fxbaogao_report": 3, # fxbaogao研报
    "tushare_mainbiz": 1, # Tushare主营业务（静态）
}
```

---

## 六、已知限制

1. **WebSearch需Agent调用**：Python脚本无法直接调用WebSearch，需在TRAE Agent会话中通过WebSearch工具执行后传入结果
2. **Tushare新闻/公告API不可用**：news/major_news/anns_d/irm_qa_sh需5000+积分，已用东方财富API+WebSearch替代
3. **同义词表需扩充**：当前SYNONYMS覆盖11个常见词对，特定行业可能需要补充
4. **fxbaogao搜索次数限制**：每个候选公司调用2-3次API（search_reports+get_paragraphs），Top15候选约45次调用

---

## 七、后续改进方向

1. **互动易API直连**：研究上证e互动(sns.sseinfo.com)和深交所互动易的API接口，实现自动化Q&A获取
2. **公告分类过滤**：东方财富API返回所有公告，需增加类型过滤（投资者活动/业绩预告/重大合同）
3. **历史线索积累**：将验证过的线索-股票关系存入数据库，形成知识图谱
4. **NLP事件提取增强**：当前EVENT_PATTERNS用正则匹配，可升级为LLM提取更复杂的事件关系

---

> 本文档由 Planner Session 创建于 2026-06-27
> 协作Agent如有疑问，请先阅读此文档和 HANDOVER.md
