"""statistical-analysis 技能：工具门控 + 端到端方法选择。

门控部分不调 LLM，直接拿桩 request 驱动 SkillsMiddleware。
端到端部分会真的调模型（两条自然语言问题），可以用 --skip-e2e 跳过。

用法：python func/test_statistical_skill.py [--skip-e2e]
（也可以 pytest func/test_statistical_skill.py -k gate）
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from langchain_core.tools import tool

from middleware import LOADED_SKILLS_KEY, SkillsMiddleware
from tools.statistics import correlation_analysis, group_comparison

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "source" / "analysis_dataset.json"

#: e2e 只喂这么多条：dataset 参数要从模型上下文里来回走一遍，300 条会把上下文撑爆
SAMPLE_SIZE = 40
FIELDS = ("has_hook", "has_question", "has_conflict", "has_reversal",
          "emotion_score", "question_count", "like_rate", "comment_rate", "share_rate")


@tool("dummy_unowned")
def dummy_unowned() -> str:
    """A tool no skill owns. Used to check the gate only hides skill tools."""
    return "ok"


@tool("read_analysis_dataset")
def read_analysis_dataset() -> list[dict]:
    """Read the analysis dataset: one record per video, content features plus engagement rates."""
    records = json.loads(DATASET.read_text(encoding="utf-8"))["records"]
    return [{k: r[k] for k in FIELDS} for r in records[:SAMPLE_SIZE]]


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
        [group_comparison, correlation_analysis, dummy_unowned], loaded
    )
    seen = {}

    async def handler(req):
        seen["tools"] = sorted(t.name for t in req.tools)
        content = req.system_message.content
        seen["manifest"] = content if isinstance(content, str) else str(content)
        return req

    asyncio.run(SkillsMiddleware().awrap_model_call(request, handler))
    return seen["tools"], seen["manifest"]


def test_gate_hides_both_before_load():
    visible, _ = _gated([])
    assert "group_comparison" not in visible, visible
    assert "correlation_analysis" not in visible, visible
    assert "dummy_unowned" in visible, "不归技能管的工具应该一直可见"


def test_gate_shows_both_after_load():
    visible, manifest = _gated(["statistical-analysis"])
    assert "group_comparison" in visible, visible
    assert "correlation_analysis" in visible, visible
    # 清单里两个工具都得列出，否则模型不知道加载这个技能能解锁什么
    assert "group_comparison" in manifest and "correlation_analysis" in manifest


def test_gate_hides_both_with_other_skill():
    """只加载 short-video-analysis 不放行统计工具——归属是精确匹配。"""
    visible, _ = _gated(["short-video-analysis"])
    assert "group_comparison" not in visible, visible
    assert "correlation_analysis" not in visible, visible


def _tool_calls(messages):
    """按顺序取出模型发起过的工具调用 (name, args)。"""
    calls = []
    for message in messages:
        for call in getattr(message, "tool_calls", None) or []:
            calls.append((call["name"], call.get("args", {})))
    return calls


def _ask(prompt):
    from langchain.chat_models import init_chat_model

    from agent import create_video_agent

    model = init_chat_model(os.getenv("MODEL", "deepseek:deepseek-chat"))
    agent = create_video_agent(model, tools=[read_analysis_dataset])
    result = asyncio.run(agent.ainvoke({"messages": [{"role": "user", "content": prompt}]}))
    return result["messages"]


def _check_route(prompt, expected_tool, forbidden_tool):
    messages = _ask(prompt)
    calls = _tool_calls(messages)
    names = [name for name, _ in calls]

    assert "LoadSkill" in names, f"没有加载技能，实际调用 {names}"
    loaded = [args.get("skill_name") for name, args in calls if name == "LoadSkill"]
    assert "statistical-analysis" in loaded, f"加载了 {loaded} 而不是 statistical-analysis"

    assert expected_tool in names, f"没调 {expected_tool}，实际调用 {names}"
    assert forbidden_tool not in names, f"不该调 {forbidden_tool}，实际调用 {names}"
    assert names.index("LoadSkill") < names.index(expected_tool), "得先加载技能再调工具"

    print(f"    调用顺序: {names}")
    return messages[-1].content


def test_e2e_binary_question_picks_group_comparison():
    """自然语言问二元特征 —— 应挑 group_comparison，不能挑 correlation_analysis。"""
    answer = _check_route(
        "比较有 hook 和没有 hook 的视频，它们的 like rate 是否有差异？",
        expected_tool="group_comparison",
        forbidden_tool="correlation_analysis",
    )
    print(f"    回答: {answer[:200]}")


def test_e2e_numeric_question_picks_correlation():
    """自然语言问两个数值变量 —— 应挑 correlation_analysis，不能挑 group_comparison。"""
    answer = _check_route(
        "emotion_score 和 like_rate 有关系吗？",
        expected_tool="correlation_analysis",
        forbidden_tool="group_comparison",
    )
    print(f"    回答: {answer[:200]}")


GATE_TESTS = (
    test_gate_hides_both_before_load,
    test_gate_shows_both_after_load,
    test_gate_hides_both_with_other_skill,
)
E2E_TESTS = (
    test_e2e_binary_question_picks_group_comparison,
    test_e2e_numeric_question_picks_correlation,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-e2e", action="store_true", help="只跑门控，不调模型")
    args = parser.parse_args()

    load_dotenv(override=True)
    tests = list(GATE_TESTS)
    if not args.skip_e2e:
        if not os.getenv("DEEPSEEK_API_KEY"):
            print("没有 DEEPSEEK_API_KEY，跳过 e2e\n")
        else:
            tests += list(E2E_TESTS)

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
