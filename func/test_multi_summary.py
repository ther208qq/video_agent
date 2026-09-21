"""多结果汇总：把已经算好的统计 artifact 交给 summarize_* 工具，拿回 AnalysisSummary。

全程不调 LLM、不读 dataset。测试里的数值故意造得跟真实数据集对不上——
工具一旦回头重算或去读数据，断言立刻挂。

用法：python func/test_multi_summary.py
（也可以 pytest func/test_multi_summary.py）
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import ToolMessage

import tools.statistics as statistics
import tools.summary as summary_module
from middleware import LOADED_SKILLS_KEY, SkillsMiddleware
from tools.summary import (
    AnalysisSummary,
    summarize_correlation_tool,
    summarize_group_comparison_tool,
)

#: 造出来的 artifact 数值。真实数据集的 has_conflict → like_rate 是
#: true 16.42% / false 15.15% / diff 1.27 个百分点 / p=0.0683，
#: 下面这几个数一个都不沾边——重算或读数据都会露馅
TRUE_MEAN = 0.987654
FALSE_MEAN = 0.123456
DIFFERENCE = 0.864198
P_VALUE = 0.001234
T_STAT = 7.654321

R_COEF = -0.987654
R_P_VALUE = 0.000321
R_N = 42

#: 模型发起工具调用时看到的就是这种 JSON，不是 python 对象——照原样喂进去
GROUP_ARGS = {
    "artifact": {
        "feature": "has_conflict",
        "target": "like_rate",
        "true_group": {"n": 3, "mean": TRUE_MEAN, "median": 0.999999},
        "false_group": {"n": 5, "mean": FALSE_MEAN, "median": 0.111111},
        "mean_difference": DIFFERENCE,
        "t_statistic": T_STAT,
        "p_value": P_VALUE,
    }
}

CORRELATION_ARGS = {
    "artifact": {
        "feature": "emotion_score",
        "target": "like_rate",
        "n": R_N,
        "correlation": R_COEF,
        "p_value": R_P_VALUE,
    }
}


def call(tool, args):
    """按模型发起工具调用的方式执行，拿回带 artifact 的 ToolMessage。"""
    return tool.invoke(
        {"type": "tool_call", "name": tool.name, "id": "call_test", "args": args}
    )


def with_p_value(base, p_value):
    args = {"artifact": dict(base["artifact"])}
    args["artifact"]["p_value"] = p_value
    return args


def test_group_artifact_becomes_summary():
    message = call(summarize_group_comparison_tool, GROUP_ARGS)
    summary = message.artifact

    assert isinstance(summary, AnalysisSummary), type(summary)
    assert summary.analysis_type == "group_comparison"
    assert summary.feature == "has_conflict"
    assert summary.target == "like_rate"
    assert summary.sample_size == 8, "3 + 5，两组的 n 相加"


def test_correlation_artifact_becomes_summary():
    message = call(summarize_correlation_tool, CORRELATION_ARGS)
    summary = message.artifact

    assert isinstance(summary, AnalysisSummary), type(summary)
    assert summary.analysis_type == "correlation"
    assert summary.feature == "emotion_score"
    assert summary.target == "like_rate"
    assert summary.sample_size == R_N


def test_summary_numbers_come_from_artifact():
    """原始浮点必须一位不差地来自入参 artifact，不能被重算或四舍五入。"""
    group = call(summarize_group_comparison_tool, GROUP_ARGS).artifact
    assert group.statistical_result["true_group"]["mean"] == TRUE_MEAN
    assert group.statistical_result["false_group"]["mean"] == FALSE_MEAN
    assert group.statistical_result["mean_difference"] == DIFFERENCE
    assert group.statistical_result["t_statistic"] == T_STAT
    assert group.statistical_result["p_value"] == P_VALUE

    correlation = call(summarize_correlation_tool, CORRELATION_ARGS).artifact
    assert correlation.statistical_result["correlation"] == R_COEF
    assert correlation.statistical_result["p_value"] == R_P_VALUE
    assert correlation.statistical_result["n"] == R_N


def test_summary_text_shows_artifact_numbers():
    group = call(summarize_group_comparison_tool, GROUP_ARGS).artifact
    assert "98.77%" in group.key_result
    assert "12.35%" in group.key_result
    assert "86.42 个百分点" in group.key_result
    assert "0.0012" in group.interpretation
    # 数据集里 has_conflict 的真实结果是 16.42% / p=0.0683，出现就说明回头算了
    assert "16.42%" not in group.key_result
    assert "0.0683" not in group.interpretation

    correlation = call(summarize_correlation_tool, CORRELATION_ARGS).artifact
    assert "-0.9877" in correlation.key_result
    assert "0.0003" in correlation.key_result
    assert f"n={R_N}" in correlation.key_result


def test_tool_output_matches_existing_summary_rules():
    """工具只是包装：产出的 AnalysisSummary 跟直接调旧函数一模一样。"""
    from tools.statistics import CorrelationAnalysis, GroupComparison

    group = call(summarize_group_comparison_tool, GROUP_ARGS).artifact
    assert group.model_dump() == summary_module.summarize_group_comparison(
        GroupComparison(**GROUP_ARGS["artifact"])
    ).model_dump()

    correlation = call(summarize_correlation_tool, CORRELATION_ARGS).artifact
    assert correlation.model_dump() == summary_module.summarize_correlation(
        CorrelationAnalysis(**CORRELATION_ARGS["artifact"])
    ).model_dump()


def test_significance_rule_unchanged():
    """p < 0.05 显著、p >= 0.05 不显著，边界 0.05 算不显著。"""
    for p_value, expected, forbidden in (
        (0.001234, "达到统计显著", "未达到"),
        (0.0499, "达到统计显著", "未达到"),
        (0.05, "未达到统计显著", None),
        (0.068301, "未达到统计显著", None),
    ):
        summaries = (
            call(summarize_group_comparison_tool, with_p_value(GROUP_ARGS, p_value)).artifact,
            call(summarize_correlation_tool, with_p_value(CORRELATION_ARGS, p_value)).artifact,
        )
        for summary in summaries:
            assert expected in summary.interpretation, summary.interpretation
            if forbidden:
                assert forbidden not in summary.interpretation, summary.interpretation


def test_tool_does_not_recompute():
    """把统计入口整个换成炸弹：工具还是得正常出摘要。"""
    def boom(*args, **kwargs):
        raise AssertionError("摘要工具不该回头算统计量")

    original = statistics.compare_groups, statistics.correlate
    statistics.compare_groups, statistics.correlate = boom, boom
    try:
        assert call(summarize_group_comparison_tool, GROUP_ARGS).artifact.sample_size == 8
        assert call(summarize_correlation_tool, CORRELATION_ARGS).artifact.sample_size == R_N
    finally:
        statistics.compare_groups, statistics.correlate = original

    # summary.py 连算统计量需要的入口都没 import，也没有 dataset 参数可传
    assert "pandas" not in vars(summary_module)
    assert not hasattr(summary_module, "compare_groups")
    assert not hasattr(summary_module, "correlate")


def test_tool_takes_only_the_artifact():
    """参数里只有 artifact——没有 dataset，模型想重算也无从下手。"""
    for tool in (summarize_group_comparison_tool, summarize_correlation_tool):
        schema = tool.args_schema.model_json_schema()
        assert set(schema["properties"]) == {"artifact"}, schema["properties"]

    group_fields = summarize_group_comparison_tool.args_schema.model_json_schema()[
        "$defs"
    ]["GroupComparison"]["properties"]
    assert {"feature", "target", "true_group", "false_group", "p_value"} <= set(group_fields)


def test_tool_output_is_agent_consumable():
    """模型拿到的是文本，结构化结果挂在 artifact 上——跟统计工具同一个形状。"""
    for tool, args in (
        (summarize_group_comparison_tool, GROUP_ARGS),
        (summarize_correlation_tool, CORRELATION_ARGS),
    ):
        message = call(tool, args)
        assert isinstance(message, ToolMessage)
        assert getattr(message, "status", None) != "error"
        assert isinstance(message.content, str) and message.content.strip()
        assert message.content.startswith(message.artifact.key_result)

        rebuilt = AnalysisSummary(**message.artifact.model_dump())
        assert rebuilt.model_dump() == message.artifact.model_dump()


class _StubRequest:
    """awrap_model_call 只用到 state / tools / system_message / override 四样。"""

    def __init__(self, tools, loaded, system_message=None):
        self.tools = tools
        self.state = {LOADED_SKILLS_KEY: list(loaded)}
        self.system_message = system_message

    def override(self, *, tools=None, system_message=None):
        return _StubRequest(
            self.tools if tools is None else tools,
            self.state[LOADED_SKILLS_KEY],
            self.system_message if system_message is None else system_message,
        )


def _gated(loaded):
    """跑一遍模型调用前的过滤，返回 (可见工具名, 拼好的 system message)。"""
    request = _StubRequest(
        [summarize_group_comparison_tool, summarize_correlation_tool], loaded
    )
    seen = {}

    async def handler(req):
        seen["tools"] = sorted(t.name for t in req.tools)
        content = req.system_message.content
        seen["manifest"] = content if isinstance(content, str) else str(content)
        return req

    asyncio.run(SkillsMiddleware().awrap_model_call(request, handler))
    return seen["tools"], seen["manifest"]


def test_tools_hidden_until_statistical_skill_loaded():
    visible, _ = _gated([])
    assert visible == [], f"技能没加载就看得见摘要工具: {visible}"

    visible, manifest = _gated(["statistical-analysis"])
    assert "summarize_group_comparison" in visible, visible
    assert "summarize_correlation" in visible, visible
    assert "summarize_group_comparison" in manifest and "summarize_correlation" in manifest


def test_tools_registered_in_agent_graph():
    """注册进图才执行得了——没注册的话模型调了也只会报 tool not found。"""
    import os

    from dotenv import load_dotenv

    from agent import create_video_agent

    load_dotenv(override=True)
    from langchain.chat_models import init_chat_model

    model = init_chat_model(os.getenv("MODEL", "deepseek:deepseek-chat"))
    agent = create_video_agent(model)
    # 走子代理图里那个 tools 节点，它持有的就是 agent.py 拼出来的 resolved
    node = agent.middleware.subagent_graphs["general-purpose"].nodes["tools"].bound
    names = set(node.tools_by_name)

    assert {"summarize_group_comparison", "summarize_correlation"} <= names, sorted(names)
    # 统计工具还在，注册是追加不是顶替
    assert {"group_comparison", "correlation_analysis"} <= names, sorted(names)


def main():
    tests = [
        test_group_artifact_becomes_summary,
        test_correlation_artifact_becomes_summary,
        test_summary_numbers_come_from_artifact,
        test_summary_text_shows_artifact_numbers,
        test_tool_output_matches_existing_summary_rules,
        test_significance_rule_unchanged,
        test_tool_does_not_recompute,
        test_tool_takes_only_the_artifact,
        test_tool_output_is_agent_consumable,
        test_tools_hidden_until_statistical_skill_loaded,
        test_tools_registered_in_agent_graph,
    ]
    failed = []
    for test in tests:
        try:
            test()
        except AssertionError as e:
            failed.append(test.__name__)
            print(f"FAIL {test.__name__}: {e}")
        else:
            print(f"PASS {test.__name__}")

    print(f"\n{len(tests) - len(failed)}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
