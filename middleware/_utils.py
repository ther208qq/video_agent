"""中间件共用的小工具。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import SystemMessage


def append_to_system_message(system_message: Any, text: str) -> SystemMessage:
    """把一段文字接在 system message 后面。

    ``system_message`` 可能是 None（还没设）、str，或 content blocks 列表，
    三种都得处理——中间件拿到的未必是原始那段提示词。
    """
    if system_message is None:
        return SystemMessage(content=text)

    content = system_message.content
    if isinstance(content, str):
        merged = f"{content}\n\n{text}"
    else:
        merged = [*content, {"type": "text", "text": text}]

    return system_message.model_copy(update={"content": merged})
