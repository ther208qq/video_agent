from typing import Any

from .think import think_tool
from .todo import TodoWrite

TOOLS: list[Any] = [think_tool, TodoWrite]


def get_tools() -> list[Any]:
    """返回要绑定给 agent 的工具列表。"""
    return list(TOOLS)
