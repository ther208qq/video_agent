"""技能门控：技能的工具默认不给模型看，加载之后才放行。

工具全部注册进 agent（图才执行得了），但每次调用模型之前，属于未加载技能的
工具会被从可见列表里摘掉。注册和可见是两件事，这里只管后者。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any, NotRequired

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command

from skills import SKILL_REGISTRY, SkillDefinition, list_skills

from ._utils import append_to_system_message

LOADED_SKILLS_KEY = "loaded_skills"


def _union(left: list[str], right: list[str]) -> list[str]:
    """合并已加载的技能。保序并集——同一个技能加载两次不该重复。"""
    merged = list(left)
    for name in right:
        if name not in merged:
            merged.append(name)
    return merged


class LoadedSkillsState(AgentState):
    loaded_skills: NotRequired[Annotated[list[str], _union]]


class SkillsMiddleware(AgentMiddleware):
    """让技能的工具按需出现。"""

    state_schema = LoadedSkillsState

    def __init__(self, registry: dict[str, SkillDefinition] | None = None) -> None:
        super().__init__()
        self.registry = registry if registry is not None else SKILL_REGISTRY

        # 工具名 → 哪些技能管着它。一个工具可以同时归多个技能。
        self._tool_to_skills: dict[str, set[str]] = {}
        for skill_name, skill in self.registry.items():
            for tool_name in skill.get_tool_names():
                self._tool_to_skills.setdefault(tool_name, set()).add(skill_name)

        self.tools = [self._load_skill_tool()]

    def _load_skill_tool(self) -> Any:
        """加载技能用的入口工具。真正的状态更新在 awrap_tool_call 里做。"""

        @tool("LoadSkill")
        def load_skill(skill_name: str) -> str:
            """Load a skill to unlock its tools.

            The skill's tools are hidden from you until you load it. Call this
            with a name from the skills list, then call the unlocked tools
            directly like any other tool.

            Args:
                skill_name: Name of the skill to load
            """
            return f"Loading skill: {skill_name}"

        return load_skill

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        if request.tool_call.get("name") != "LoadSkill":
            return await handler(request)

        tool_call_id = request.tool_call.get("id", "")
        name = request.tool_call.get("args", {}).get("skill_name", "")
        skill = self.registry.get(name)

        if skill is None:
            available = ", ".join(self.registry) or "(none)"
            return ToolMessage(
                content=(
                    f"Error: no skill named {name!r}.\n"
                    f"Available skills: {available}"
                ),
                tool_call_id=tool_call_id,
                name="LoadSkill",
                status="error",
            )

        parts = [skill.read_doc().strip()]
        names = skill.get_tool_names()
        if names:
            parts.append("**Tools now available:** " + ", ".join(names))

        return Command(
            update={
                LOADED_SKILLS_KEY: [name],
                "messages": [
                    ToolMessage(
                        content="\n\n".join(p for p in parts if p),
                        tool_call_id=tool_call_id,
                        name="LoadSkill",
                    )
                ],
            }
        )

    # 将没有加载skill的tool剔除
    async def awrap_model_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        loaded = set(request.state.get(LOADED_SKILLS_KEY) or [])

        # 不归技能管的一直可见；归技能管的，等它的技能被加载。
        kept = []
        for tool in request.tools or []:
            name = getattr(tool, "name", None) or (
                tool.get("name") if isinstance(tool, dict) else str(tool)
            )
            owners = self._tool_to_skills.get(name)
            if owners is None or owners & loaded:
                kept.append(tool)
        filtered = request.override(tools=kept)


        lines = []
        for skill in list_skills():
            names = skill.get_tool_names()
            if not names:
                continue
            lines.append(f"- **{skill.name}**: {skill.description} (tools: {', '.join(names)})")

        if lines:
            manifest = (
                "## Skills\n\n"
                "These skills hold tools that stay hidden until loaded. Call "
                "`LoadSkill` with a skill name to unlock its tools.\n\n"
                + "\n".join(lines)
            )
            filtered = filtered.override(
                system_message=append_to_system_message(
                    filtered.system_message, manifest
                )
            )

        return await handler(filtered)
