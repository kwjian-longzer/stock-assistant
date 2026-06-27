# 两Session协同工作指南

> 创建时间: 2026-06-27
> 适用场景: 规划session（当前）与执行session（新）通过Git仓库协同

---

## 一、Workspace隔离说明

两个TRAE云端session的workspace**完全隔离**，不能互访文件。唯一同步通道是 **GitHub远程仓库**。

```
规划session (当前)                      执行session (新)
/workspace/stock-assistant/              /workspace/stock-assistant/ (各自独立)
commit: cc30b08 (最新)                   commit: 44018ac (基于6ccbf94的v4.0改动)
     │                                       │
     │ git push origin main ✅ 已完成         │ git pull origin main ← 执行此命令同步
     └───────────┬───────────────────────────┘
                 ▼
          GitHub: github.com/kwjian-longzer/stock-assistant
          远程main: cc30b08 (已含红色电报端点+规划书+评审)
```

---

## 二、新Session立即执行的操作

### 步骤1：同步最新代码

```bash
cd /workspace/stock-assistant  # 新session的工作目录
git pull origin main
```

**可能遇到的冲突**：
- 如果新session改过 `cls_collector.py`，会与 `40de9b8` 冲突
- **解决原则**：冲突时保留远程版本（含红色电报端点），新session的其他改动重新应用

```bash
# 如果冲突，保留远程版本的cls_collector.py：
git checkout --theirs cls_collector.py
git add cls_collector.py
git commit -m "merge: 同步远程红色电报端点修复"
```

### 步骤2：验证红色电报端点

```bash
# 重新采集电报，验证红色电报端点工作
python cls_collector.py --telegraph

# 预期结果（之前是20条+0红色）：
# 红色电报: 150+条（向后翻页3-4页）
# is_red: level in ('A','B') 判断正确
# 时间覆盖: 24小时
```

### 步骤3：重新初始化数据库（如果需要）

当前session的workspace中有积累的数据：
```
cls_telegraph: 183 rows
cls_telegraph_stock: 84 rows
cls_vip_article: 83 rows
vip_discovered_stock: 773 rows
```

这些数据在.gitignore中，不会通过Git同步。新session需重新采集：
```bash
python cls_collector.py --telegraph   # 电报数据
python cls_collector.py --vip          # VIP数据
python data_collector.py --period all  # 全市场数据
```

---

## 三、协同工作模式

### 3.1 角色分工

| 角色 | Session | 职责 |
|------|---------|------|
| **规划者** | 当前session | 架构设计、代码审查、问题诊断、文档维护 |
| **执行者** | 新session | 代码实现、测试验证、迭代优化 |

### 3.2 协同流程

```
规划者 → push文档/规划/评审 → GitHub → 新session pull阅读
                                                    │
新session → push代码/成果 → GitHub → 规划者pull评审
                                                    │
规划者 → push问题/修复 → GitHub → 新session pull修复
```

### 3.3 具体操作

**规划者（当前session）要做的**：
1. ✅ 已push最新代码（含红色电报端点）
2. ✅ 已创建PROJECT_PLAN_v4.md规划书
3. ✅ 已创建NEW_SESSION_REVIEW.md评审
4. 后续：新session提交代码后，pull下来评审

**执行者（新session）要做的**：
1. `git pull origin main` 同步最新代码
2. 阅读NEW_SESSION_REVIEW.md了解评审意见
3. 修复红色电报端点问题（如果未自动修复）
4. 继续v4.0剩余任务
5. `git push origin main` 提交成果
6. 规划者pull评审

### 3.4 文档约定

| 文件 | 维护者 | 用途 |
|------|--------|------|
| `PROJECT_PLAN_v4.md` | 规划者 | 项目规划（不频繁更新） |
| `NEW_SESSION_REVIEW.md` | 规划者 | 对执行者的评审和指导 |
| `PENDING_ITEMS.md` | 执行者 | 待办事项和阻塞点 |
| `HANDOVER.md` | 规划者 | 环境配置和交接信息 |
| `SKILL.md` | 双方 | 项目宪法（规划者定，执行者遵守） |
| `SESSION_SUMMARY.md` | 规划者 | 历史记录（仅供参考） |

---

## 四、关键信息传递清单

### 新session必须知道的信息

1. **代码版本问题**：之前克隆在6ccbf94，现在远程已更新到cc30b08，含红色电报端点（40de9b8）
2. **CLS红色电报端点**：`/v1/roll/get_roll_list?category=red` + 向后翻页
3. **is_red判断**：`level in ('A','B')` 不是 `color == 'red'`
4. **签名算法**：`MD5(SHA1(排序后urlencode查询串))`
5. **LLM角色**：Agent（TRAE session）就是LLM，不需要外部API Key
6. **已有6个Schedule任务**：v4.0应替换而非新增
7. **数据库在.gitignore**：新session需要重新采集数据
8. **完整评审**：见NEW_SESSION_REVIEW.md

### 新session的待解决项（按优先级）

| 优先级 | 事项 | 解决方法 |
|--------|------|---------|
| P0 | 同步最新代码 | `git pull origin main` |
| P0 | 修复红色电报端点 | pull后自动修复，验证即可 |
| P0 | 重新采集数据 | cls_collector + data_collector |
| P1 | Git push凭据 | 需用户配置PAT |
| P1 | 仓库改Public+Pages | 需用户手动操作 |
| P2 | push_feishu.py CLI参数 | 统一参数格式 |
| P2 | 金股数量偏少 | 随数据积累改善 |
