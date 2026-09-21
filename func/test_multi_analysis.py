"""综合问题 → 多工具分析流程的端到端测试。

一个自然语言问题（不是单个统计问题）进去，看 agent 会不会按
statistical-analysis Skill 的指示自己拆成多次统计调用：
二元特征走 group_comparison，数值特征走 correlation_analysis。

所有断言读同一份 trace——只跑一次真实模型调用，不重复烧 token。

用法：python func/test_multi_analysis.py
（也可以 pytest func/test_multi_analysis.py）
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool

from tools.statistics import CorrelationAnalysis, GroupComparison

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "source" / "analysis_dataset.json"

#: 综合问题：故意模糊，不点名任何工具，也不说要做几个分析
QUESTION = "分析短视频内容特征和 like_rate 之间有哪些关系？"

#: 特征按类型分。方法选错的话断言会直接抓出来
BINARY_FEATURES = ("has_hook", "has_question", "has_conflict", "has_reversal")
NUMERIC_FEATURES = ("emotion_score", "question_count")
RATE_FIELDS = ("like_rate",)

STATS_TOOLS = ("group_comparison", "correlation_analysis")

#: 只喂这么多条。dataset 是整个塞在 tool call 的 arguments 里来回走的：模型每分析
#: 一个特征就要把整份数据重发一遍。40 条时单次调用是 6087 字符，模型把三个调用放进
#: 同一条消息就撞上 deepseek 8192 的输出上限（finish_reason=length），被截断的那个
#: 调用会落进 invalid_tool_calls——它照样进下一个请求，但工具没执行过，于是
#: 「4 个 tool_call 只配到 3 条 tool 消息」，请求被 API 400 掉。
#: 20 条是下限：再少 has_reversal 的 true 组只剩 1 条，够不上 MIN_GROUP_SIZE
SAMPLE_SIZE = 20
#: 只投影本次问题用得上的字段。comment_rate / share_rate 留着会让模型往别的 target 上跑
FIELDS = ("has_hook", "has_question", "has_conflict", "has_reversal",
          "emotion_score", "question_count", "like_rate")


# --- 测试辅助：不是最终的数据访问架构 ---------------------------------------
# 这里把固定数据集裁一小份直接返回给模型，纯粹是为了让 dataset 参数能在
# 上下文里传递、跑通「多工具流程」这条链路。真实的取数方式（沙箱读文件等）
# 不在这轮范围内，别拿这个 fixture 当架构参考。
@tool("read_analysis_dataset")
def read_analysis_dataset() -> list[dict]:
    """Read the analysis dataset: one record per video, content features plus engagement rates."""
    records = json.loads(DATASET.read_text(encoding="utf-8"))["records"]
    return [{k: r[k] for k in FIELDS} for r in records[:SAMPLE_SIZE]]


#: 一次模型调用，全部断言共用。放模块级是为了 pytest 收集时也只跑一次
_TRACE = {}


def _ask(prompt):
    from langchain.chat_models import init_chat_model

    from agent import create_video_agent

    model = init_chat_model(os.getenv("MODEL", "deepseek:deepseek-chat"))
    agent = create_video_agent(model, tools=[read_analysis_dataset])
    result = asyncio.run(agent.ainvoke({"messages": [{"role": "user", "content": prompt}]}))
    return result["messages"]


def trace():
    if "messages" not in _TRACE:
        load_dotenv(override=True)
        _TRACE["messages"] = _ask(QUESTION)
    return _TRACE["messages"]


def tool_calls():
    """按发起顺序取出 (工具名, 参数)。"""
    calls = []
    for message in trace():
        for call in getattr(message, "tool_calls", None) or []:
            calls.append((call["name"], call.get("args", {})))
    return calls


def stats_calls(tool_name):
    return [args for name, args in tool_calls() if name == tool_name]


def artifacts():
    """收出所有统计工具成功返回时挂在 ToolMessage 上的 artifact。"""
    collected = []
    for message in trace():
        if not isinstance(message, ToolMessage):
            continue
        if getattr(message, "status", None) == "error":
            continue
        artifact = getattr(message, "artifact", None)
        if artifact is not None:
            collected.append((message.name, artifact))
    return collected


def show_trace():
    """把这轮 agent 的实际行为打出来——本轮的结论主要靠看这个。"""
    print(f"问题: {QUESTION}\n")
    print("调用顺序:")
    for index, (name, args) in enumerate(tool_calls(), 1):
        if name == "read_analysis_dataset":
            detail = f"({SAMPLE_SIZE} 条)"
        elif name in STATS_TOOLS:
            detail = f"feature={args.get('feature')!r} target={args.get('target')!r}"
        else:
            detail = json.dumps(args, ensure_ascii=False)
        print(f"  {index}. {name} {detail}")

    print("\nArtifact:")
    for name, artifact in artifacts():
        if isinstance(artifact, GroupComparison):
            detail = (f"n={artifact.true_group.n}+{artifact.false_group.n} "
                      f"p={artifact.p_value:.4f}")
        elif isinstance(artifact, CorrelationAnalysis):
            detail = f"r={artifact.correlation:.4f} p={artifact.p_value:.4f} n={artifact.n}"
        else:
            detail = type(artifact).__name__
        print(f"  - {name}: {artifact.feature} -> {artifact.target}  {detail}")
    print()


def test_loads_statistical_skill():
    """1. 先加载 statistical-analysis，再动手分析。"""
    calls = tool_calls()
    names = [name for name, _ in calls]
    assert "LoadSkill" in names, f"没有加载任何技能，实际调用 {names}"

    loaded = [args.get("skill_name") for name, args in calls if name == "LoadSkill"]
    assert "statistical-analysis" in loaded, f"加载了 {loaded}，不是 statistical-analysis"

    first_stats = min(
        (i for i, (name, _) in enumerate(calls) if name in STATS_TOOLS), default=None
    )
    assert first_stats is not None, f"一次统计工具都没调，实际调用 {names}"
    assert names.index("LoadSkill") < first_stats, "得先加载技能再调统计工具"


def test_calls_multiple_statistical_tools():
    """2. 综合问题要拆成多次调用，不是挑一个工具草草了事。"""
    calls = [name for name, _ in tool_calls() if name in STATS_TOOLS]
    assert len(calls) >= 2, f"统计工具只调了 {calls}"
    assert len(set(calls)) == 2, f"两个方法都该用上，实际只有 {set(calls)}"


def test_binary_features_go_to_group_comparison():
    """3. 至少一个 has_* 二元特征走分组比较。"""
    features = [args.get("feature") for args in stats_calls("group_comparison")]
    binary = [name for name in features if name in BINARY_FEATURES]
    assert binary, f"group_comparison 的 feature 是 {features}，没有 has_* 特征"


def test_numeric_features_go_to_correlation():
    """4. 至少一个数值特征走相关分析。"""
    features = [args.get("feature") for args in stats_calls("correlation_analysis")]
    numeric = [name for name in features if name in NUMERIC_FEATURES]
    assert numeric, f"correlation_analysis 的 feature 是 {features}，没有数值特征"


def test_no_method_misuse():
    """5. 方法不能选错：分类变量别当数值算相关，数值变量别当分组切。"""
    for args in stats_calls("group_comparison"):
        feature = args.get("feature")
        assert feature in BINARY_FEATURES, (
            f"group_comparison 是给二元特征的，却拿了 {feature!r}"
        )
    for args in stats_calls("correlation_analysis"):
        feature = args.get("feature")
        assert feature in NUMERIC_FEATURES, (
            f"correlation_analysis 是给数值变量的，却拿了 {feature!r}"
        )
    for name, args in tool_calls():
        if name in STATS_TOOLS:
            target = args.get("target")
            assert target in RATE_FIELDS, f"{name} 的 target 是 {target!r}，不是 like_rate"


def test_produces_multiple_artifacts():
    """6. 拿到多个结构化统计 artifact，不是只有一个。

    summarize_* 产出的 AnalysisSummary 也挂 artifact，但那是统计结果的下游产物，
    不是统计结果本身，这里只认统计工具自己产的两种；断言形状而不是集合相等，
    免得下游再长出新的 artifact 类型就误判成统计流程坏了。
    """
    collected = [
        (name, artifact)
        for name, artifact in artifacts()
        if isinstance(artifact, (GroupComparison, CorrelationAnalysis))
    ]
    assert len(collected) >= 2, f"只收到 {len(collected)} 个统计 artifact"

    kinds = {type(artifact) for _, artifact in collected}
    assert kinds == {GroupComparison, CorrelationAnalysis}, (
        f"两种统计 artifact 都该出现，实际只有 {kinds}"
    )

    distinct = {(type(a).__name__, a.feature, a.target) for _, a in collected}
    assert len(distinct) >= 2, f"artifact 不重复才有意义，实际 {distinct}"


def test_no_final_report_this_round():
    """7. 本轮只验证多工具流程，不要求汇总报告；但最终回答不能是空的。"""
    answer = trace()[-1].content
    assert isinstance(answer, str) and answer.strip(), "最后一轮没有文字回答"

    # 这一轮要观察的是主 agent 自己拆解、自己多次调工具。真派给子代理的话，
    # 分析过程发生在子代理的独立上下文里，主 trace 上只剩一个 Task，看不到拆解过程，
    # 也就无从验证——这种情况要明确报出来，不能当成通过
    delegated = [name for name, _ in tool_calls() if name in ("Task", "TaskOutput")]
    assert not delegated, (
        f"agent 把这轮分析派给子代理了（{delegated}），主 trace 观察不到多工具拆解。"
        f"SubAgentMiddleware 的提示词写着「3+ 工具调用就派活」，本轮正好落在这一档"
    )
    print(f"    最终回答: {answer[:200]}")


def main():
    load_dotenv(override=True)
    if not os.getenv("DEEPSEEK_API_KEY"):
        print("没有 DEEPSEEK_API_KEY，跳过（这个测试必须真实调用模型）")
        return 0

    tests = [
        test_loads_statistical_skill,
        test_calls_multiple_statistical_tools,
        test_binary_features_go_to_group_comparison,
        test_numeric_features_go_to_correlation,
        test_no_method_misuse,
        test_produces_multiple_artifacts,
        test_no_final_report_this_round,
    ]

    print("=== 跑一次 agent，记录 trace（真实模型调用）===")
    try:
        show_trace()
    except Exception as e:
        print(f"agent 调用失败: {type(e).__name__}: {e}")
        return 1

    failed = []
    for test in tests:
        print(f"--- {test.__name__}")
        try:
            test()
        except AssertionError as e:
            failed.append(test.__name__)
            print(f"FAIL: {e}")
        else:
            print("PASS")

    print(f"\n{len(tests) - len(failed)}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
