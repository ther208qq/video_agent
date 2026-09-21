# 未提交改动说明

最后一次提交停在 **2026-09-19**（`fd277f0 加子代理：Task 工具 + 按类型派活 + 技能预装`），
之后的工作全部留在工作区。这些改动是**两条独立的工作线**，混在一起了：

| 工作线 | 内容 | 状态 |
|---|---|---|
| 一 | 子代理后台化 + Write 工具 | 上次提交之后的收尾，**仍未跑测** |
| 二 | 短视频分析流水线 | 已打通：数据构建 → 统计工具 → 技能路由 → 可视化 → 摘要 → 多结果汇总 |

建议分开提交，混在一起历史会很难读。

---

## 一、子代理与沙箱收尾（未跑测的 WIP）

> ⚠️ 这块**未经跑测**，不是本次流水线的一部分。以下 6 个文件的 `M` 标记在流水线开工前就已存在。

### `middleware/subagent.py`（+166 / −10，最大的一块）

后台子代理闭环，四处改动：

1. **`Task` 改异步** —— 从 `await subagent.ainvoke()` 同步等结果，改成
   `asyncio.create_task(...)` 后台跑，立刻返回
   `Task-8kQ2mZ started in the background`，不再阻塞主 agent。
   `self._tasks` 字典兼做强引用持有（asyncio 自己只持弱引用，不留一份任务会被 GC 掉）。

2. **新增 `TaskOutput` 工具** —— `task_id=None` 时列出全部任务状态；
   传 `task_id` 时取结果，可用 `timeout` 等待。覆盖 running / cancelled /
   failed / finished 四种结局，取不到不报错、只说明还在跑。

3. **新增 `SubAgentOrchestrator`** —— 包在 graph 外面。主 agent 收尾时后台任务
   常常还在跑，结论就没人接。这里等一轮、写一条 `HumanMessage` 通知回 state、
   再把主 agent 叫起来续跑一次，让它自己去调 `TaskOutput`。
   **只守一轮**，取不到就放行，不空转。

   有 checkpointer 时走 `aupdate_state(..., as_node="__start__")`，
   没有时直接拼 `{"messages": [...]}`，两条路径都已分支处理。

4. `_Task` dataclass 带 `seen` 标记 —— 没这个标记，同一次收工会被反复播报。
   通知只报「去 TaskOutput 取」，不夹带结果正文，避免两份结果对不上。

### `tools/file_ops.py`（+34）

新增 `Write` 工具，经 `sandbox.upload_file` 落盘，返回**展开后的绝对路径**
（模型后面拿这个路径去 `Bash` 里跑，写相对路径时得知道实际落在哪）。
文档里明确写了没有 append 模式。

### `skills/registry.py` / `skills/docs/sandbox-exec.md`

`sandbox-exec` 的 `tool_names` 从 `("Bash", "ExecuteCode")` 扩到
`("Bash", "ExecuteCode", "Write")`，文档从「Two tools」改成「Three tools」，
补上 Write 的用法和覆盖语义。

### `middleware/__init__.py`（+2）、`agent.py` 的编排部分

导出 `SubAgentOrchestrator`；`agent.py` 里 `subagent_middleware` 改名
`subagent_stack`（它现在只是中间件**列表**，返回值是真正的中间件对象），
并给 `create_video_agent` 的返回值包上 `SubAgentOrchestrator`。

> 注意：包了之后返回的**不是裸 graph**。拿它当 graph 用（`.ainvoke` / `.astream`）
> 没问题，但绕开它就绕开了轮末收尾这一步。

---

## 二、短视频分析流水线（已完成）

### 交付清单

| # | 文件 | 产出 / 说明 |
|---|---|---|
| 1 | `func/build_content_dataset.py` | `source/content_dataset.json` — 300 条 × 16 字段，分层随机抽样（时长桶 × 播放量分位）、创作者上限 3 条、仅英文、SEED=42，产出 `content_dataset_report.json` |
| 2 | `tools/content.py`（85 行） | `ContentFeatures` Pydantic 模型 + `extract_features()` + `analyze_content` Tool，`temperature=0` 走 `model_copy` |
| 3 | `skills/docs/short-video-analysis.md` | 第一个业务 Skill，`tool_names=("analyze_content",)` |
| 4 | `func/build_content_features.py` | `source/content_features.json` — 300 条 × 8 字段，300/300 成功、0 失败，逐条落盘可断点续跑，产出 `content_features_report.json` |
| 5 | `func/build_analysis_dataset.py` | `source/analysis_dataset.json` — 按 video_id join，300 条 × 22 字段，重复/缺失/多余一律报错不静默丢 |
| 6 | `tools/statistics.py`（204 行） | 两个统计工具，LLM 全程不参与计算（详见下节） |
| 7 | `skills/docs/statistical-analysis.md` | 第二个业务 Skill，双方法路由（详见下节） |
| 8 | `func/test_correlation_analysis.py`、`func/correlation_analysis_demo.py` | 前者 7/7；后者读固定数据集跑相关，不调 LLM |
| 9 | `tools/visualization.py`（54 行） | `VisualizationSpec` + `visualize_group_comparison()`，统计 artifact → 柱状图描述（详见下节） |
| 10 | `tools/summary.py`（115 行） | `AnalysisSummary` + `summarize_group_comparison()` / `summarize_correlation()` 两个纯函数，统计 artifact → 自然语言摘要，纯模板 |
| 11 | 同上（`@tool` 包装） | `summarize_group_comparison` / `summarize_correlation` 两个 Tool，把第 10 项暴露给 agent，一个 artifact 转一个摘要（详见下节） |
| 12 | `func/test_multi_summary.py` | 11 项：两种 artifact → summary、数值来自 artifact、显著性口径、不重算、门控、注册，**不调 LLM** |

### `tools/statistics.py` 的两个工具

| 工具 | 输入 | 方法 | artifact |
|---|---|---|---|
| `group_comparison` | 二元 feature + 数值 target | pandas 分组 + `scipy.stats.ttest_ind(equal_var=False)` | `GroupComparison` |
| `correlation_analysis` | 数值 feature + 数值 target | 逐对删除缺失 + `scipy.stats.pearsonr` | `CorrelationAnalysis` |

两者都是 `response_format="content_and_artifact"`：content 是紧凑数值摘要，
artifact 是结构化结果。校验都在算之前做：字段存在、dtype 是数值、二元/常量检查、
缺失逐对删除（**不把缺失当 0**）、样本不足报错。

`correlation_analysis` 有两个刻意的下限：`MIN_PAIRS = 3`（n < 3 时 r 恒为 ±1，
是算术必然而非测量结果）；常量列显式拦截（scipy 1.18 对常量输入只返回 `nan`
加一个 `ConstantInputWarning`，不抛异常，不拦就会把 nan 当相关系数返回）。

### 统计 artifact 的两层消费：可视化与摘要

两个下游模块彼此独立，都只吃 artifact，**都不读数据集、不调 LLM、不重算任何统计量**：

| 模块 | 入口 | 产出 | 覆盖 |
|---|---|---|---|
| `tools/visualization.py` | `visualize_group_comparison()` | `VisualizationSpec`（`chart_type="bar"`） | 只有 `GroupComparison` |
| `tools/summary.py` | 纯函数 `summarize_group_comparison()` / `summarize_correlation()`；agent 侧是 `@tool` 包装的同名工具 | `AnalysisSummary` | 两种 artifact 都覆盖 |

```python
VisualizationSpec:  chart_type / title / x_label / y_label / series[] / metadata
    BarSeries:      name / value / n          # 两根柱子：true 在前 false 在后
AnalysisSummary:    analysis_type / feature / target / sample_size /
                    key_result / statistical_result / interpretation
```

**`statistical_result` 是 artifact 的 `model_dump()` 裸拷贝**，纯函数这一层原始浮点
一位没变——百分比、`0.4f` 这些格式化只活在 `key_result` 这个展示字符串里。
（agent 走 Tool 那条路时数值会先经模型复制一次，见下面「多结果汇总」的注记。）

**摘要用 Python 模板而非 LLM。** 输入就是 4~5 个标量（两个均值、差异、p、n），
内容空间小到模板能完全覆盖；交给 LLM 只会引入改错小数位、
把「未达到显著」润色成「边际显著」、编造效应量三类风险。摘要里
`key_result + interpretation` 拼接即为自然语言结论，没有第三个冗余字段。

显著性口径由 `ALPHA = 0.05` 单点驱动，`p < ALPHA` 才算显著（`p = 0.05` 恰好算不显著，
测试覆盖该边界）。比率按百分比展示的判定依据是**字段名以 `_rate` 结尾**——
这是约定式启发规则，不是类型信息，是目前唯一依赖命名约定的地方。

`correlation_analysis` **暂时没有可视化**：`CorrelationAnalysis` 只留了
`correlation` / `p_value` / `n` 三个标量，配对数据在 `correlate()` 里算完就丢了，
散点图的每个点都需要一对 `(x, y)`，靠这三个数在数学上无法反推，硬凑等于编数据。
要做散点图必须先决定要不要把配对数据纳入 artifact，属于单独一轮的设计决定。

### 多结果汇总：把每个 artifact 转成一个摘要

一轮综合问题会产生 6~18 个 artifact，agent 需要把**每一个**转成 `AnalysisSummary`。
两个 Tool 就是上面那两个纯函数的 `@tool` 包装：

| Tool | 入参 | 产出 |
|---|---|---|
| `summarize_group_comparison` | `artifact: GroupComparison` | 文本 + `AnalysisSummary` |
| `summarize_correlation` | `artifact: CorrelationAnalysis` | 同上 |

**参数里只有 `artifact`，没有 `dataset`。** 工具读不到数据，也不 import
`compare_groups` / `correlate`（`test_multi_summary.py` 把这两个入口换成炸弹来反证），
所以统计量不可能被重算——数值只能来自模型回填的那份 artifact。

> ❗ **数值会被模型复制一次，这是本轮已知的代价。** 模型看到的是 `content`
> （`_describe` 的 6 位小数文本），artifact 的原始浮点进不了模型上下文。
> 实测 `mean=0.13216946666666668` 经这一趟出来是 `0.132169`，
> `p=0.2716516267610478` 出来是 `0.271652`。`AnalysisSummary.statistical_result`
> 因此**只在把真 artifact 直接递给工具时**才与源 artifact 逐位相同
> （`test_multi_summary.py` 覆盖的是这一路）。显著性判定不受影响——同一个
> 0.05 分界、同一侧——但「原始浮点一位没变」这条保证在线上不成立。
> 要一位不差得让工具用 `tool_call_id` 从消息历史取回 artifact 对象，需要
> 引入 `ToolRuntime` 注入；且长会话里旧消息被 `SummarizationMiddleware` 压缩后
> artifact 会一起消失，属于下一轮的设计决定。

**本轮明确不做**：跨组合的 synthesis、最终报告、多重比较校正、新统计方法。
只解决「一个 artifact → 一个摘要」，重复 N 次。

### `statistical-analysis` Skill 的双方法路由

方法选择**由 agent 读正文决定**，没有 Python 路由器，正文里也不含自动判断代码。
`tool_names=("group_comparison", "correlation_analysis")`。

| 问题形状 | 方法 | 工具 |
|---|---|---|
| 「有 X 的 和 没有 X 的视频有差异吗」→ 布尔特征 | 分组比较 | `group_comparison` |
| 「两个数值变量有关系吗」→ 双数值 | 相关 | `correlation_analysis` |

判据写进了正文：`has_*` 走分组比较，`emotion_score` / `question_count` 走相关；
并注明「传错形状会被工具拒绝，报错就说明方法选错了」。

正文的 Rules 同时钉住了：不许自己算统计量、artifact 是唯一真源、不许编数据、
**`p >= 0.05` 不许说成显著或「有趋势」**、相关不等于因果（且不许建议创作者改内容）、
分类的 `topic` 不许当数值变量（它近乎全唯一，编码成 0/1 没意义）。
regression / visualization / 多变量分析仍列 out of scope。

### 数据产物流向

```
source/metadata.parquet  (278 MiB, 上游原始数据, 57,960 行, 不要提交)
        │  build_content_dataset.py   过滤 + 分层抽样
        ▼
source/content_dataset.json          300 条 × 16 字段   (323 KiB)
        │  build_content_features.py  analyze_content × 300（唯一一次 LLM 调用）
        ▼
source/content_features.json         300 条 × 8 字段（已冻结）(83 KiB)
        │  build_analysis_dataset.py  按 video_id join
        ▼
source/analysis_dataset.json         ★ 统计分析的唯一数据源，300 条 × 22 字段 (349 KiB)
        │
        ├── group_comparison      4 个布尔特征 × 3 个指标
        │        └── GroupComparison ──┬── visualization.py → VisualizationSpec（柱状图）
        │                              └── summary.py       → AnalysisSummary
        └── correlation_analysis  2 个数值特征 × 3 个指标
                 └── CorrelationAnalysis → summary.py       → AnalysisSummary
                                         （暂无可视化，原因见上节）
```

统计、可视化、摘要三个职责不混：`visualization.py` 和 `summary.py` 都只 import
`tools/statistics.py` 里的 artifact 类型，彼此不互相 import，也不碰 `compare_groups` / `correlate`。

> ⚠️ **`visualization.py` 仍然只是库，没有接进 agent。** 它既不在 `agent.py` 的
> `resolved` 里，也不归任何 Skill 的 `tool_names` 管，只能从
> `func/visualization_demo.py` 这类脚本调用，agent 既看不到也调不动；
> `statistical-analysis` 正文里 visualization 仍列 out of scope，与当前状态自洽。
> 要暴露给它只需同样的三步：`@tool` 包装 + 进 `resolved` + 归
> `statistical-analysis`，但会连带改动 Skill 正文的 out-of-scope 列表。
>
> ✅ **`summary.py` 本轮已经接进去了**，步骤就是上一条说的三步。

**`content_features.json` 是冻结的**——统计分析阶段严禁重新调 LLM 抽特征。
特征是 LLM 抽的，每次现算都会漂（早期 demo 的分组在 45/15 和 44/16 之间跳），
固化后统计结论才可比。

### 技能门控

注册与可见是两件事，`SkillsMiddleware` 只管后者：

1. **注册** —— `agent.py` 把 `analyze_content` / `group_comparison` /
   `correlation_analysis` / `summarize_group_comparison` / `summarize_correlation`
   加进 `resolved`，注册进图才执行得了。
2. **归类** —— 中间件遍历 `SKILL_REGISTRY`，建 `工具名 → 技能名` 反查表。
3. **可见** —— 每次调模型前，属于「未加载技能」的工具被从 `request.tools` 摘掉。

实测（`func/test_statistical_skill.py` 的门控部分，**不调 LLM**——用桩 request
直接驱动 `awrap_model_call`，可重复跑）：

| 加载状态 | `group_comparison` / `correlation_analysis` | `summarize_*` | 不归技能管的工具 |
|---|---|---|---|
| 加载前（空） | 不可见 | 不可见 | 可见 |
| 加载 `statistical-analysis` | 可见 | 可见 | 可见 |
| 只加载 `short-video-analysis` | 不可见 | 不可见 | 可见 |

归属是**精确匹配**，不是「加载过任何技能就全放」。加载时 system message 里的
skills 清单会列出该技能解锁的四个工具名。

> 清单列了 `summarize_*`，但 `statistical-analysis.md` 正文没提它们
> （本轮不允许改 Skill 文档）。模型是靠清单 + 工具描述发现这两个工具的，
> 实测够用（见下）。下次动文档时补一段，成本很低。

子代理**不受技能门控**（`SubAgentMiddleware` 的中间件栈里没有 `SkillsMiddleware`），
所以 `data-analysis` 子代理能直接看到包含两个统计工具在内的全部工具。这是既有行为。

### 固化下来的结论

**分组比较**：4 个内容特征 × 3 个互动指标，12 个组合：

| feature | like_rate | comment_rate | share_rate |
|---|---|---|---|
| has_hook | −0.0108 / p=0.182 | +0.0006 / p=0.178 | −0.0004 / p=0.795 |
| has_question | +0.0086 / p=0.235 | +0.0007 / p=0.150 | −0.0012 / p=0.401 |
| has_conflict | +0.0127 / p=0.068 | +0.0008 / p=0.265 | −0.0009 / p=0.511 |
| has_reversal | +0.0057 / p=0.428 | +0.0010 / p=0.223 | −0.0026 / p=0.060 |

（每格为 mean_difference / p_value；n 分别为 223/77、184/116、127/173、112/188。）

**相关**：2 个数值特征 × 3 个指标，6 个组合：

| feature | like_rate | comment_rate | share_rate |
|---|---|---|---|
| emotion_score | +0.1042 / p=0.071 | +0.0924 / p=0.110 | −0.0351 / p=0.545 |
| question_count | +0.0496 / p=0.392 | +0.0903 / p=0.118 | −0.0340 / p=0.558 |

（每格为 correlation / p_value，n 均为 300。）

**18 个组合里没有一个 p < 0.05。** 最小的两个是 `has_reversal→share_rate`
p=0.060 和 `has_conflict→like_rate` p=0.068；相关里最小的是
`emotion_score→like_rate` p=0.071。300 条样本、效应量在 0.01（均值差）和
0.1（r）量级，够不到「有趋势」的说法。要下结论就得诚实说「都没有统计显著差异」。

### 测试与演示

| 文件 | 覆盖 | 结果 |
|---|---|---|
| `func/test_correlation_analysis.py` | 7 项：手算基准（r=9/10=0.9、p 走 t 变换独立推出）、完全正相关、非 numeric、缺失逐对删除、样本不足、常量变量、字段缺失 | 7/7 |
| `func/test_statistical_skill.py` | 3 项门控（无 LLM）+ 2 项 e2e 方法选择（真实 LLM 调用） | 5/5 |
| `func/test_visualization.py` | 6 项：bar spec 形状、均值映射、feature/target 保留、metadata 保留、**没有被重算**、入参不被改动且可序列化 | 6/6 |
| `func/test_summary.py` | 15 项：正/负/非显著、均值与差异进文本、p 值进文本、样本量、原始浮点不变、入参不被改动、显著性口径、无趋势与因果措辞 | 15/15 |
| `func/test_multi_summary.py` | 11 项：两种 artifact → `AnalysisSummary`、数值一位不差来自 artifact、文本里是 artifact 的数而不是数据集真实值、产物与旧函数逐字节相同、p 口径与边界 0.05、统计入口换成炸弹仍正常、参数只有 artifact、`ToolMessage` 可被 agent 消费、门控、图里已注册。**不调 LLM** | 11/11 |
| `func/test_multi_analysis.py` | 7 项：综合问题 → 多工具流程（真实 LLM 调用；只跑一次，全部断言共用同一份 trace） | 7/7 |
| `func/correlation_analysis_demo.py` | 读固定数据集跑 `emotion_score` / `question_count` → `like_rate` | 正常 |
| `func/visualization_demo.py` | `has_conflict` / `has_hook` → `like_rate`，打印 `VisualizationSpec` | 正常 |
| `func/summary_demo.py` | `has_conflict` → `like_rate`、`emotion_score` → `like_rate`，各打印 artifact / `AnalysisSummary` / 自然语言摘要 | 正常 |

这些测试文件的设计原则是**让断言有鉴别力**，各自都做过反证：

- `test_multi_summary.py` 喂进去的 artifact 数值（`0.987654` / `p=0.001234`）
  跟真实数据集的答案（`0.164162` / `p=0.068301`）没有任何关系，而且断言里
  显式写了「`16.42%` 不许出现在摘要文本里」——重算或偷偷读 dataset 都会露馅。
  另外把 `compare_groups` / `correlate` 换成抛异常的炸弹再跑一遍，工具照常工作。

- `test_visualization.py` 的测试数据里 `mean_difference` 特意取 `7.5`，而
  `true.mean - false.mean` 是 `124.69`——重算过的实现不可能吐出 7.5。
- `test_summary.py` 里「p ≥ 0.05 不许声称显著」**不能**写成
  `assert "统计显著" not in text`：正确措辞「未达到统计显著」本身就含这四个字，
  那样写会把对的判成错的。改写成
  `text.count("统计显著") == text.count("未达到统计显著")`，
  即每一处「统计显著」都必须是「未达到统计显著」的一部分。
- `test_correlation_analysis.py` 的手算基准曾被我算错（`sum(xy)` 末项漏乘，
  算成 7 而实际是 9），是拿 scipy 对照后才发现——**基准本身也需要交叉验证**。

e2e 两条自然语言问题（prompt 里不含任何工具名或技能名）：

```
「比较有 hook 和没有 hook 的视频，它们的 like rate 是否有差异？」
  → LoadSkill(statistical-analysis) → read_analysis_dataset → group_comparison

「emotion_score 和 like_rate 有关系吗？」
  → LoadSkill(statistical-analysis) → read_analysis_dataset → correlation_analysis
```

两条都排除了另一个工具，说明路由是 Skill 正文在起作用。

综合问题（`func/test_multi_analysis.py`，20 条样本）走完整条链：

```text
「分析短视频内容特征和 like_rate 之间有哪些关系？」
  → LoadSkill(statistical-analysis) → read_analysis_dataset
  → 4×group_comparison + 2×correlation_analysis        （6 个 artifact）
  → 同一条消息里 6×summarize_*                          （6 个 AnalysisSummary）
  → 最终回答按 6 组结果组织，数值与 artifact 一致
```

最后那一步没人提示——工具描述 + 加载技能时那份清单就够了，6 个 artifact
各转了一次，没有漏也没有重复。

> **运行注记**：
> 1. 测试必须用项目虚拟环境 `.venv/Scripts/python.exe`。全局 `D:\Python\python3.13.5`
>    里没有 langchain（`ModuleNotFoundError: No module named 'langchain_core.memory'`）。
>    `test_correlation_analysis.py` 只用 scipy/pandas，全局恰好也有，容易误判环境是对的。
> 2. `test_statistical_skill.py` 的结论行之后还会打出 asyncio 的清理警告，
>    所以 `tail -1` 取不到「5/5 通过」——要按退出码判断，退出码 0 才是通过。

---

## 三、当前待做

1. **子代理后台化的 6 个文件改动仍未跑测。** 见第一节。它是独立主题，
   应当先补验证再单独提交。

2. **两条工作线分开提交。** 子代理后台化是一个完整主题，短视频流水线是另一个。

3. **`source/*.json` 要不要进版本库？**
   倾向**进**——它们是「唯一真源」的定位，重跑一次特征要花几分钟 LLM 调用
   而且结果会漂。`metadata.parquet` 已由 `.gitignore` 排除。

4. **`group_comparison` 仍然没有独立测试文件。** 现存的
   `func/test_correlation_analysis.py` 只覆盖相关部分。若要恢复到
   「两个工具都有手算校验」的覆盖度，需新补一个 `group_comparison` 的测试
   （验证过它与 `scipy.stats.ttest_ind(equal_var=False)` 逐位一致，
   但那个校验没有落成文件）。

5. **跨组合的 synthesis 与最终报告仍未实现。** 「每个 artifact → 一个摘要」这一层
   本轮已经打通（agent 会自己一个个转），剩下的是把这些摘要**合起来**看：
   18 个组合怎么组织成一段结论、要不要做多重比较校正、多个结果互相矛盾时怎么说。
   这一层涉及判断而非搬运，是唯一可能值得引入 LLM 的地方——但需要先明确口径，
   不要顺手扩范围。

6. **`correlation_analysis` 的可视化待定。** 见「统计 artifact 的两层消费」一节，
   前置条件是把配对数据纳入 artifact，属于设计决定。

7. **可视化还没接进 agent。** 摘要那层本轮已经接完（三步：`@tool` 包装、进
   `resolved`、归 `statistical-analysis`）；可视化只剩同样三步，见第二节警告框。
   `SkillsMiddleware` 本身不用动——它读的是 `tool_names`，机制是现成的。

8. **摘要里的数值要不要一位不差？** 现在模型回填 artifact，数值经 `content`
   （6 位小数）复制一次，`statistical_result` 不是源 artifact 的逐位拷贝
   （见第二节的注记）。换成按 `tool_call_id` 取回原对象可以彻底解决，
   代价是引入 `ToolRuntime` 注入，且消息被摘要压缩后 artifact 就取不回来了。
   这是个设计决定，别默默改掉——当前行为是**已知且测试覆盖过**的。

9. **`PENDING_CHANGES.md` 本身**（本文件）提交后即过期，建议提交时删除或改写为正式文档。

---

## 四、历史 / 不再适用

本文件早先版本里列出过、但**当前磁盘上并不存在**的文件，相关条目一律作废：

```
func/analyze_content_demo.py      func/test_content_features.py
func/group_comparison_demo.py     func/test_group_comparison.py
                                  func/test_skill_unlock.py
```

早先版本曾把它们记为「已跑过、6/6、2/2」等，**这些记录无法复现**——
文件不在工作区，既没有提交记录也没有本地副本。`func/` 当前实际是这 12 个文件：

```
func/build_analysis_dataset.py     func/test_multi_analysis.py
func/build_content_dataset.py      func/test_multi_summary.py
func/build_content_features.py     func/test_statistical_skill.py
func/correlation_analysis_demo.py  func/test_summary.py
func/summary_demo.py               func/test_visualization.py
func/test_correlation_analysis.py  func/visualization_demo.py
```

（若需要 `analyze_content` 的单条 demo 或 `content_features` 的测试，
按第三节第 4 条一并补，不要照抄旧记录里的结果数字。）

其余作废项：

- ~~`statistical-analysis` Skill 只挂 `group_comparison` 单一工具~~ —— 已升级为双方法路由。
- ~~`workspace/` 需要加进 `.gitignore`~~ —— 已在 `.gitignore` 中（容器运行时产物，不是源码），无需处理。
- ~~`tools/statistics.py` 117 行、单一 `group_comparison`~~ —— 现为 204 行、两个工具。
- ~~`source/metadata.parquet` 未忽略，是提交前的硬阻塞~~ —— 已在 `.gitignore`
  加上 `source/metadata.parquet`（精确路径，不用 `*.parquet` 通配，
  免得将来任何 parquet 被静默忽略）。`git check-ignore` 命中，
  `git add -A --dry-run` 里已无 parquet，本地文件保留未删。

---

## 附：当前未跟踪文件清单

```
func/build_analysis_dataset.py         func/test_multi_analysis.py
func/build_content_dataset.py          func/test_multi_summary.py
func/build_content_features.py         func/test_statistical_skill.py
func/correlation_analysis_demo.py      func/test_summary.py
func/summary_demo.py                   func/test_visualization.py
func/test_correlation_analysis.py      func/visualization_demo.py
PENDING_CHANGES.md

skills/docs/short-video-analysis.md    tools/content.py
skills/docs/statistical-analysis.md    tools/statistics.py
                                       tools/summary.py
                                       tools/visualization.py

source/analysis_dataset.json       349 KiB   ← 统计分析的唯一数据源
source/content_dataset.json        323 KiB
source/content_features.json        83 KiB   ← 已冻结
source/content_dataset_report.json   3 KiB
source/content_features_report.json  1 KiB
source/metadata.parquet            278 MiB   ← 已被 .gitignore 排除
```

工作区已修改（`M`）：`.gitignore`、`agent.py`、`middleware/__init__.py`、
`middleware/subagent.py`、`skills/registry.py`、`skills/docs/sandbox-exec.md`、
`tools/file_ops.py`。
