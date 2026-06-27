# 进化引擎设计文档 (Evolution Engine)

## 一、核心定位

进化引擎是stock-assistant系统的"自我进化层"，在每次盘后学习闭环后执行。
它不只是**记录**命中率，更要**诊断**失败模式、**生成**改进假设、**回测验证**、**部署**有效改进，
实现"每次运行后系统变得更强"的OpenClaw式进化机制。

## 二、进化三层次

| 层次 | 改什么 | 方法 | 风险 | 收益 |
|------|--------|------|------|------|
| L1参数调优 | factor_weights.json中的权重/阈值 | 命中率统计+自动调整 | 低（可回滚） | 递减 |
| L2规则迭代 | 匹配规则/排除策略/同义词表 | 失败模式分析+规则补丁 | 中（需回测） | 中等 |
| L3逻辑迭代 | 洞见引擎算法/组合因子/自适应阈值 | 范式设计+A/B对比+统计检验 | 高（需严格验证） | 高 |

## 三、六阶段闭环

```
1.诊断(Diagnose) → 2.假设(Hypothesize) → 3.实验(Experiment)
→ 4.验证(Validate) → 5.部署(Deploy) → 6.监控(Monitor)
                                              ↓
                                    退化→自动回滚→重新诊断
```

### 3.1 诊断 (Diagnose)
- 读取learning_record中的验证结果
- 按因子/置信度/市场状态分类统计命中率
- 识别失败模式（连续失败、高置信度失败、特定因子失败）
- 信号盲区扫描（次日涨停股是否在前日洞见中提及）

### 3.2 假设 (Hypothesize)
- L1: "北向资金命中率75%但美股权重过高→北向15→20,美股10→5"
- L2: "'光模块锡粉'匹配失败→研报实际用'锡焊粉'→SYNONYMS补入"
- L3: "单因子独立打分忽略共振→引入组合因子叠加"

### 3.3 实验 (Experiment)
- 历史回测：对过去N天用新规则重新打分，对比旧规则
- A/B对照：同一天数据双引擎并行，比较命中率
- 蒙特卡洛：参数微调敏感性分析

### 3.4 验证 (Validate)
- 样本量≥10个交易日
- 改进幅度≥10%
- 统计显著性p<0.1（Fisher精确检验）
- 分市场检验（牛/熊/震荡都有效）
- 防退化（新规则在旧数据上不退化）

### 3.5 部署 (Deploy)
- 验证通过→写入knowledge/目录
- shadow模式运行3天（不影响生产）
- shadow优于production→切换
- 否则→回滚，记录失败假设

### 3.6 监控 (Monitor)
- 部署后每日跟踪命中率
- 连续3天退化>5%→自动回滚
- 回滚原因写入rollback_log.md

## 四、外部学习器 (External Learner)

仅靠内部验证不够，需向外部学习：

1. **盘后复盘**：读取当日涨停板→逆推当日主线叙事→与系统预判对比
2. **信号盲区扫描**：次日涨停股→前日洞见是否提及→未提及则搜索催化事件→补入信号库
3. **外部观点对齐**：fxbaogao MCP搜索当日热门研报→提取机构核心逻辑→与系统对比
4. **模式发现**：聚类历史成功/失败案例→发现"三共振时成功率85%"等组合模式

## 五、文件结构

```
knowledge/
├── factor_weights.json      # L1因子权重（insight_engine读取）
├── accuracy_benchmark.json  # 预判准确率基线
├── lessons_learned.md        # 失败案例库（Agent读取）
├── external_lessons.md       # 外部学习成果
├── engine_changelog.md       # L3逻辑迭代记录
├── failed_hypotheses.md      # 失败假设归档（避免重复尝试）
├── rollback_log.md           # 回滚记录
└── prompt_evolution.md       # analysis_prompt.md的优化补丁

evolution/
├── evolution_engine.py       # 进化核心（诊断→假设→实验→验证→部署→监控）
├── external_learner.py       # 外部学习器
├── backtest/
│   ├── backtest_runner.py    # 回测框架
│   └── results/              # 回测结果
└── knowledge_persistor.py    # 固化层（写回knowledge/ + git push）
```

## 六、集成点

| 现有模块 | 集成方式 |
|---------|---------|
| learning_loop.py | run()末尾调用evolution_engine.run() |
| insight_engine.py | 读取factor_weights.json动态调整权重 |
| vip_search_v4.py | 读取rules_patches.json更新同义词/排除表 |
| analysis_prompt.md | 读取prompt_evolution.md增量补丁 |
| report_generator.py | finalize阶段触发进化引擎 |
