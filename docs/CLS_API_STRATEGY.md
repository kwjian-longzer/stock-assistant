# 财联社 API 采集策略备忘

> 日期: 2026-06-25  
> 涉及文件: `fetch_data.py` (L947-L1209), `extract_summary.py` (L232-L298), `SKILL.md`  
> Git commit: `e8d4a0a`, `7aa4ad5`

---

## 一、背景与问题

stock-assistant 项目需要从财联社 (cls.cn) 采集四类信息作为研报核心信源：深度头条、VIP 文章、投资日历、首页热门。此前采用浏览器采集方案——用浏览器工具逐页访问，通过 `document.body.innerText` 提取页面文本，保存到 `data/cls_pages.json`，再由 `_parse_cls_page_text()` 做正则解析。

该方案存在三个结构性缺陷：

1. **依赖 JS 渲染** — 财联社页面为单页应用，Python 的 `requests` 库直接抓取只能拿到空壳 HTML，必须借助浏览器工具等待页面加载完成后才能提取内容。
2. **解析脆弱** — `innerText` 返回的是无结构纯文本，`_parse_cls_page_text()` 用正则按行匹配标题和摘要，页面布局微调即导致解析失败。投资日历和首页经常返回空结果。
3. **流程冗长** — 自动任务每次执行都需要在步骤 0.5 单独完成四页浏览器采集，增加 30-60 秒耗时，且浏览器工具本身存在不稳定因素（标签页超时、渲染失败等）。

财联社电报（telegraph）此前已通过无签名 API 直接采集，运行稳定。这提示其余四个页面很可能也存在可用的 API 端点。

---

## 二、逆向过程

### 2.1 抓包观察

使用浏览器工具的 `browser_network_requests` 功能，在访问 `https://www.cls.cn/depth?id=1000` 时捕获页面加载过程中发出的 XHR 请求。筛选 `cls.cn` 域名下的 API 调用，发现页面数据并非来自 HTML 文档，而是由前端 JS 向以下端点发起 GET 请求获取 JSON：

- 深度头条: `GET /v3/depth/home/assembled/1000`
- VIP 文章: `GET /featured/v1/home/assembled`
- 投资日历: `GET /api/calendar/web/list`
- 首页热门: `GET /v2/article/hot/list`

每个请求的 query string 中除业务参数外，还携带一组固定参数和一个 `sign` 字段。`sign` 的值是 32 位十六进制字符串，形如 MD5 输出。

### 2.2 签名算法定位

对比多个请求的参数，发现以下规律：

- 固定参数: `app=CailianpressWeb`, `os=web`, `sv=8.7.9`（版本号可能随前端更新变化）
- 业务参数: 因端点而异，如投资日历附带 `flag=0&type=0`
- `sign` 参数: 32 位 hex，每次请求都不同，与参数内容相关

通过 WebSearch 搜索 "cls.cn sign 算法" 等关键词，找到多篇逆向分析文章，均指向同一算法：

```
1. 将所有参数（除 sign 外）按 key 字典序排序
2. 用 urllib.parse.urlencode 拼接为 query string
3. 对 query string 做 SHA1 哈希，得到 40 位 hex
4. 对 SHA1 结果再做 MD5 哈希，得到 32 位 hex = sign
```

### 2.3 算法验证

从浏览器抓包中提取 4 组真实请求（含参数和对应的 sign 值），用 Python 复现上述算法逐一比对。4 组结果全部完全匹配，确认算法正确。

验证脚本核心逻辑：

```python
import hashlib, urllib.parse

def _cls_sign(params):
    sorted_params = dict(sorted(params.items()))
    query_string = urllib.parse.urlencode(sorted_params)
    sha1_hash = hashlib.sha1(query_string.encode('utf-8')).hexdigest()
    sign = hashlib.md5(sha1_hash.encode('utf-8')).hexdigest()
    return sign
```

---

## 三、API 端点详情

### 3.1 深度头条

| 项目 | 内容 |
|------|------|
| 端点 | `/v3/depth/home/assembled/1000` |
| 方法 | GET |
| 额外参数 | 无 |
| 返回结构 | `data.depth_list[]` + `data.top_article[]` |
| 关键字段 | `title`, `brief`, `ctime`, `article_tag`, `source`, `reading_num` |
| 实测数据量 | 31 篇 |

`depth_list` 为常规深度文章列表，`top_article` 为置顶文章。两者结构略有差异——置顶文章的图片字段为 `img` 而非 `image`，代码中做了兼容处理。

### 3.2 VIP 文章

| 项目 | 内容 |
|------|------|
| 端点 | `/featured/v1/home/assembled` + `/featured/v2/home/recommend/article` |
| 方法 | GET |
| 额外参数 | recommend 端点需 `last_time`（当前时间戳）和 `refresh_Type=1` |
| 返回结构 | `data.recommend_list[]` + `data.free_top_v2[]` + `data.yellow_article[]` + recommend 列表 |
| 关键字段 | `title`, `brief`, `type_name`, `reading_num`, `unlock`, `related_stock`, `label` |
| 实测数据量 | 50 篇 |

VIP 文章需要两个端点配合：`/featured/v1/home/assembled` 返回推荐、免费置顶、黄V 三类文章；`/featured/v2/home/recommend/article` 补充推荐流。`free_top_v2` 中的 `related_stock` 字段直接包含关联股票名称（逗号分隔），是共振分析的重要数据源。

### 3.3 投资日历

| 项目 | 内容 |
|------|------|
| 端点 | `/api/calendar/web/list` |
| 方法 | GET |
| 额外参数 | `flag=0`, `type=0` |
| 返回结构 | `data[]` 数组，每项含 `calendar_day`, `week`, `items[]` |
| 关键字段 | `items[].title`, `items[].type`, `items[].stock` |
| 实测数据量 | 23 条事件 |

返回数据按日期分组，每个日期对象内嵌 `items` 数组。`items` 中的 `stock` 字段包含相关股票名称。这是浏览器采集方案中失败率最高的页面——API 方案彻底解决了该问题。

### 3.4 首页热门

| 项目 | 内容 |
|------|------|
| 端点 | `/v2/article/hot/list` |
| 方法 | GET |
| 额外参数 | 无 |
| 返回结构 | `data[]` 数组 |
| 关键字段 | `title`, `brief`, `ctime`, `readNum`, `author`, `stocks` |
| 实测数据量 | 13 篇 |

`stocks` 字段为逗号分隔的股票名称字符串，与电报的 `stock_list` 格式一致。

---

## 四、实现架构

### 4.1 函数层级

```
fetch_cls_pages(data_quality)          ← 对外接口，API 优先 + 浏览器降级
  ├── fetch_cls_pages_via_api()        ← API 采集（4 个端点）
  │     ├── _cls_api_get(path, params) ← 通用请求（自动签名）
  │     │     └── _cls_sign(params)    ← 签名算法
  │     └── record_quality()           ← 记录采集质量
  └── _parse_cls_page_text()           ← 浏览器降级时的文本解析（保留）
```

### 4.2 API 优先 + 浏览器降级策略

`fetch_cls_pages()` 的执行逻辑：

1. 先调用 `fetch_cls_pages_via_api()` 尝试 API 采集全部 4 个页面。
2. 检查返回结果，逐页判断是否成功（标准：该页面存在于结果中且不含 `error` 键）。
3. 收集失败的页面列表。如果有任何页面失败，读取 `data/cls_pages.json`（浏览器预采集的文本），对失败页面调用 `_parse_cls_page_text()` 做文本解析，补充到结果中。
4. 降级采集的页面 `source` 字段标记为 `browser_fallback`，`record_quality` 记录为 `DEGRADED`。

这一设计确保：API 正常时完全不需要浏览器参与；API 部分失败时只对失败页面降级，不影响已成功的数据；浏览器文本完全缺失时也不报错，仅标记 FAILED。

### 4.3 请求构造

`_cls_api_get()` 统一处理签名和请求：

- 固定参数 `app`/`os`/`sv` 与业务参数合并后送入 `_cls_sign()` 计算
- 计算出的 `sign` 加入参数字典
- 请求头携带 `User-Agent` 和 `Referer: https://www.cls.cn/`（财联社后端校验 Referer）
- 超时设为 15 秒
- 响应 JSON 中 `error==0` 或 `errno==0` 或存在 `data` 键时视为成功，返回 `data` 字段内容

### 4.4 数据格式兼容

`extract_summary.py` 的 `extract_chapter0_cls()` 函数同时兼容两种数据来源：

| 数据项 | API 格式 | 浏览器格式 | 兼容处理 |
|--------|----------|------------|----------|
| VIP 股票 | `related_stock` 字段 | `stocks` 字段 | `art.get("related_stock", "") or art.get("stocks", "")` |
| 投资日历事件 | `events` 键 | `articles` 键 | `calendar.get("events", calendar.get("articles", []))` |
| source 标记 | `cls_api` | `browser_scrape` / `browser_fallback` | `depth.get("source", "browser_scrape")` |

---

## 五、自动任务变更

三个定时任务（晨报 06:00 / 午报 11:30 / 晚报 20:00）的步骤 0.5 已从"必须执行"调整为"可选，API 优先"：

- **变更前**: 步骤 0.5 必须在步骤 1 之前用浏览器采集 4 个页面，保存到 `cls_pages.json`
- **变更后**: 步骤 0.5 仅在步骤 1 执行后日志显示 `[CLS] API采集失败的页面` 时才需要执行，且只需访问失败页面

步骤 1 的描述也同步更新，明确 `fetch_data.py` 会自动通过 API 采集财联社 4 个页面。

---

## 六、风险与维护

### 6.1 签名失效

财联社前端版本更新时，`sv` 参数（当前 `8.7.9`）可能变化。如果签名算法本身不变，只需更新 `_cls_api_get()` 中的 `sv` 值。如果签名算法变更（如改用 HMAC-SHA256），需要重新逆向。

**检测方式**: `fetch_cls_pages_via_api()` 中每个端点失败时会打印 `[CLS API] {path} 返回错误`。如果 4 个端点同时失败且错误信息一致，大概率是签名失效。

### 6.2 接口变更

API 路径或返回结构可能随前端改版而变化。`_cls_api_get()` 返回 `None` 时会触发降级，不会导致整个流程中断。但长期依赖降级会失去 API 的结构化数据优势。

**检测方式**: `record_quality` 记录的 `source` 字段。如果某页面持续显示 `browser_fallback`，说明该端点可能已变更。

### 6.3 频率限制

目前未观察到财联社对 API 请求做频率限制。每次执行 5 个 API 请求（VIP 文章需 2 个），间隔极短。如果未来被限流，可考虑增加请求间隔或缓存结果。

### 6.4 sv 版本号维护

`sv` 参数对应财联社前端版本号。可以通过浏览器访问 `https://www.cls.cn/` 后查看页面源码中的 JS bundle 版本号获取最新值。当前固定为 `8.7.9`，实测可用。

---

## 七、测试验证

API 采集函数独立测试结果（2026-06-25）：

```
[CLS API] 采集深度头条...
    深度头条: 31 篇
[CLS API] 采集VIP文章...
    VIP文章: 50 篇
[CLS API] 采集投资日历...
    投资日历: 23 条事件
[CLS API] 采集首页热门文章...
    首页热门: 13 篇
```

4 个页面全部通过 API 成功采集，无需浏览器降级。`extract_summary.py` 的兼容性修复也已验证，能正确读取 API 返回的 `related_stock` 和 `events` 字段。

诊断函数 `cls_api_diagnostic()` 测试结果（2026-06-25）：

```
[1] sv 版本号检测: 未检测到（首页HTML不含明文版本号），使用默认 8.7.9
[2] 端点测试: 5/5 全部成功
[3] 诊断总结: 所有端点正常
```

---

## 八、Agent 自修复指南

> 本章供自动任务执行 Agent 在遇到 CLS API 采集失败时参照操作。
> 自动任务已授权 Agent 在 CLS API 故障时修改 `fetch_data.py` 中的 CLS 相关函数。

### 8.0 第一步：运行诊断

当步骤 1 的日志出现 `[CLS API] xxx 请求失败` 或 `[CLS] API采集失败的页面` 时，立即运行：

```bash
cd /workspace/stock-assistant && python -c "from fetch_data import cls_api_diagnostic; cls_api_diagnostic()"
```

根据诊断结果中的失败模式，对照下方三种场景执行修复。

### 8.1 场景A：全部端点失败（签名或 sv 问题）

**特征**: 诊断报告显示所有 5 个端点均为 FAILED。

**原因**: 财联社前端版本更新导致 `sv` 参数变化，或签名算法变更。

**修复步骤**:

1. 用浏览器访问 `https://www.cls.cn/`，等待页面加载完成
2. 执行 `browser_network_requests` 获取所有网络请求
3. 在请求列表中筛选 `cls.cn` 域名下携带 `sign` 参数的 API 请求
4. 从该请求的 query string 中提取 `sv` 参数值（如 `sv=8.8.0`）
5. 修改 `fetch_data.py` 中的 `_CLS_DEFAULT_SV` 常量：
   ```python
   _CLS_DEFAULT_SV = '8.8.0'  # 更新为检测到的值
   ```
6. 重新运行诊断函数验证：`python -c "from fetch_data import cls_api_diagnostic; cls_api_diagnostic()"`
7. 如果仍然全部失败，说明签名算法可能已变更。执行步骤 8-12
8. 从浏览器网络请求中提取一个完整的 API 请求 URL（含所有参数和 sign 值）
9. 用 Python 复现签名算法：
   ```python
   import hashlib, urllib.parse
   params = {'app': 'CailianpressWeb', 'os': 'web', 'sv': '新版本号', ...业务参数}
   sorted_params = dict(sorted(params.items()))
   query_string = urllib.parse.urlencode(sorted_params)
   sha1 = hashlib.sha1(query_string.encode()).hexdigest()
   sign = hashlib.md5(sha1.encode()).hexdigest()
   print(sign)  # 与浏览器中的 sign 对比
   ```
10. 如果不匹配，尝试其他哈希组合：`MD5(query_string)` 直接、`SHA256(query_string)` 等
11. 找到匹配的算法后，修改 `_cls_sign()` 函数
12. 重新运行诊断验证

### 8.2 场景B：部分端点失败（接口路径变更）

**特征**: 诊断报告显示 1-3 个端点 FAILED，其余正常。

**原因**: 财联社改版导致某个页面的 API 路径变化。

**修复步骤**:

1. 根据失败的端点名，确定对应的财联社页面：
   - 深度头条 → `https://www.cls.cn/depth?id=1000`
   - VIP文章 → `https://www.cls.cn/vip`
   - 投资日历 → `https://www.cls.cn/investkalendar`
   - 首页热门 → `https://www.cls.cn/`
2. 用浏览器访问该页面，等待加载完成
3. 执行 `browser_network_requests` 获取网络请求
4. 在请求列表中找到返回 JSON 数据的 API 请求（通常路径含 `/v`、`/api`、`/featured` 等）
5. 记录新的 API 路径
6. 修改 `fetch_data.py` 中 `fetch_cls_pages_via_api()` 函数内对应端点的路径：
   ```python
   # 例如深度头条路径从 /v3/depth/home/assembled/1000 变为 /v4/depth/home/assembled/1000
   depth_data = _cls_api_get('/v4/depth/home/assembled/1000')
   ```
7. 如果返回的 JSON 结构也变了，参照场景C
8. 重新运行诊断验证

### 8.3 场景C：API成功但数据解析异常（返回结构变更）

**特征**: 诊断报告显示端点 OK，但 `extract_summary.py` 输出的 `data_summary.json` 中 CLS 相关字段为"数据暂缺"或数据明显不完整。

**原因**: API 返回的 JSON 结构变化，原有字段名不再存在。

**修复步骤**:

1. 单独打印 API 返回的原始数据结构：
   ```python
   from fetch_data import _cls_api_get
   data = _cls_api_get('/v3/depth/home/assembled/1000')
   if data and isinstance(data, dict):
       print("顶层keys:", list(data.keys()))
       for k, v in data.items():
           if isinstance(v, list) and len(v) > 0:
               print(f"  {k}[0] keys:", list(v[0].keys()) if isinstance(v[0], dict) else type(v[0]))
   ```
2. 对比 `fetch_data.py` 中 `fetch_cls_pages_via_api()` 函数使用的字段名（如 `depth_list`、`recommend_list`、`free_top_v2` 等）
3. 如果字段名已变化，更新 `fetch_cls_pages_via_api()` 中的字段引用
4. 同时检查 `extract_summary.py` 中 `extract_chapter0_cls()` 函数是否需要同步修改（如 `articles`、`events`、`related_stock` 等键名）
5. 重新运行完整流程验证：
   ```bash
   python -c "from fetch_data import fetch_cls_pages_via_api; r = fetch_cls_pages_via_api({}); print({k: v.get('article_count', v.get('event_count', 0)) for k, v in r.items()})"
   ```

### 8.4 修复后的必要操作

无论哪种场景，修复完成后必须执行：

```bash
cd /workspace/stock-assistant
git add fetch_data.py extract_summary.py
git commit -m "fix: 修复CLS API采集(原因简述)"
git push origin main
```

然后重新执行步骤 1（`python fetch_data.py morning/noon/evening`）继续后续流程。

### 8.5 无法修复时的降级方案

如果以上修复均不成功，执行浏览器降级采集：

1. 用浏览器依次访问 4 个财联社页面
2. 每个页面用 `browser_evaluate` 执行 `document.body.innerText` 提取文本
3. 保存到 `data/cls_pages.json`，格式：`{"深度头条":"文本...","VIP文章":"文本...","投资日历":"文本...","首页":"文本..."}`
4. 重新执行步骤 1，`fetch_cls_pages()` 会自动读取 `cls_pages.json` 做浏览器降级解析

这是最后的保底方案，数据质量不如 API 结构化数据，但能保证流程不中断。
