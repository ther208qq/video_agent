"""工具结果超阈值就落盘，原位只留路径和首尾预览。

一次 Bash 或 ExecuteCode 能刷出几万行，全塞进上下文当场把窗口撑爆。这里在工具
返回之后拦一道：超阈值的写进沙箱，消息换成文件路径加首尾各 5 行，模型真要用
再用 Read 读回来。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from sandbox import SandboxRuntime

logger = logging.getLogger(__name__)

#: 超过这么多字符就落盘。跟 Read 的 MAX_READ_CHARS 是同一个数——两个上限一起把
#: 单条结果能占的窗口卡死，Read 也就不会把驱逐刚救下来的窗口再撑爆。
MAX_RESULT_CHARS = 160_000

#: 落盘目录，相对沙箱 working_dir。
EVICTION_DIR = ".agents/large_tool_results"

HEAD_LINES = 5
TAIL_LINES = 5

#: 预览里单行的上限，防着一行就是几十万字符的压缩产物
MAX_PREVIEW_LINE_CHARS = 1000

_TOO_LARGE = """Tool result too large. The result of this tool call \
{tool_call_id} was saved in the sandbox at this path: {file_path}
Use the Read tool to read it back — give it that path. Read a slice at a time
rather than the whole thing: pass offset and limit. For example, to read the
first 100 lines, call Read with offset=0 and limit=100.

Below is a preview showing the head and tail of the result. Lines of the form
... [N lines truncated] ... mark what was left out in the middle.

{preview}
"""


class LargeResultEvictionMiddleware(AgentMiddleware):
    """把过大的工具结果挪进沙箱，原位留个能读回来的线索。"""

    def __init__(self, sandbox: SandboxRuntime, max_chars: int = MAX_RESULT_CHARS):
        super().__init__()
        self.sandbox = sandbox
        self.max_chars = max_chars

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        # Read 自己有 160k 上限，而且长单行截起来会出问题，不掺和
        if request.tool_call.get("name") == "Read":
            return await handler(request)

        message = await handler(request)
        text = message.content if isinstance(message, ToolMessage) else None

        # 不是 ToolMessage（比如 LoadSkill 返回的 Command）就原样放过
        if not isinstance(text, str) or len(text) <= self.max_chars:
            return message

        # 工具调用 id 可能带文件名不认的字符，只留安全的一小撮
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", message.tool_call_id) or "unknown"
        rel_path = f"{EVICTION_DIR}/{safe_id}.md"

        try:
            await self.sandbox.upload_file(text.encode("utf-8"), rel_path)
        except Exception:
            # 落盘失败就把原文还给模型——内容丢了比占地方严重得多
            logger.exception("Failed to evict large tool result to %s", rel_path)
            return message

        logger.info("Evicted %d chars of tool result to %s", len(text), rel_path)

        lines = text.splitlines()
        if len(lines) <= HEAD_LINES + TAIL_LINES:
            # 行数少但单行极长（压缩过的 JSON 之类），没有中间段可省
            preview = "\n".join(
                f"{i}\t{line[:MAX_PREVIEW_LINE_CHARS]}"
                for i, line in enumerate(lines, start=1)
            )
        else:
            head = list(enumerate(lines[:HEAD_LINES], start=1))
            tail = list(
                enumerate(lines[-TAIL_LINES:], start=len(lines) - TAIL_LINES + 1)
            )
            preview = "\n".join(
                [
                    *(f"{i}\t{line[:MAX_PREVIEW_LINE_CHARS]}" for i, line in head),
                    f"\n... [{len(lines) - len(head) - len(tail)} lines truncated] ...\n",
                    *(f"{i}\t{line[:MAX_PREVIEW_LINE_CHARS]}" for i, line in tail),
                ]
            )

        # status 和 artifact 不带上就等于丢了——status 丢了会把失败当成功
        return ToolMessage(
            content=_TOO_LARGE.format(
                tool_call_id=message.tool_call_id,
                file_path=rel_path,
                preview=preview,
            ),
            tool_call_id=message.tool_call_id,
            name=message.name,
            id=message.id,
            status=message.status,
            artifact=message.artifact,
        )
