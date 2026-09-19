from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DOCS_DIR = Path(__file__).parent / "docs"


@dataclass
class SkillDefinition:
    name: str
    description: str
    tools: list = field(default_factory=list)
    #: 只有名字、拿不到对象的工具（沙箱工具是每个 runtime 现造的）。
    tool_names: tuple[str, ...] = ()
    doc: str | None = None

    def get_tool_names(self) -> list[str]:
        """这个技能管住的所有工具名，对象声明的和纯名字声明的都算上。"""
        return [getattr(t, "name", str(t)) for t in self.tools] + list(self.tool_names)

    def read_doc(self) -> str:
        """技能的正文。加载技能时发给模型看的就是这段。"""
        if not self.doc:
            return ""
        return (DOCS_DIR / self.doc).read_text(encoding="utf-8")


SKILL_REGISTRY: dict[str, SkillDefinition] = {
    "sandbox-exec": SkillDefinition(
        name="sandbox-exec",
        description="在沙箱里执行 bash 命令和 Python 代码。",
        tool_names=("Bash", "ExecuteCode"),
        doc="sandbox-exec.md",
    ),
    # 不挂工具，纯粹是一套做法。给子代理预装用（见 SubAgent.skills），
    # 主 agent 也能加载，但不会因此多出任何工具。
    "data-analysis": SkillDefinition(
        name="data-analysis",
        description="分析一组数据该怎么做：先看形状、处理缺失、报告口径。",
        doc="data-analysis.md",
    ),
}


def skill_tool_names() -> set[str]:
    """所有技能管住的工具名。中间件用它判断一个工具归不归技能管。"""
    names: set[str] = set()
    for skill in SKILL_REGISTRY.values():
        names.update(skill.get_tool_names())
    return names


def list_skills() -> list[SkillDefinition]:
    """可以加载的技能。中间件拼清单时用。"""
    return list(SKILL_REGISTRY.values())
