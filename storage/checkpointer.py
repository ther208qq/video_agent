from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

#: 选后端的配置名。
CHECKPOINTER_ENV = "CHECKPOINTER"

DEFAULT_KIND = "sqlite"

#: sqlite 落盘位置，相对项目根。
DEFAULT_DB_PATH = Path(".langgraph") / "checkpoints.db"

#: 默认会话名。同一个 thread_id 的对话会被接着续写。
DEFAULT_THREAD_ID = "default"

THREAD_ENV = "THREAD_ID"


def resolve_thread_id(thread_id: str | None = None) -> str:
    """定下这次跑哪个会话。参数 > 环境变量 > 默认。"""
    return thread_id or os.getenv(THREAD_ENV) or DEFAULT_THREAD_ID


@asynccontextmanager
async def open_checkpointer(
    kind: str | None = None,
    path: str | Path | None = None,
) -> AsyncIterator[Any]:
    """打开 checkpointer，退出时关掉连接。

    只想跑一次不留痕时设 ``CHECKPOINTER=memory``。
    """
    resolved = (kind or os.getenv(CHECKPOINTER_ENV) or DEFAULT_KIND).lower()

    if resolved == "memory":
        yield InMemorySaver()
        return

    if resolved == "sqlite":
        # 惰性 import：没选 sqlite 就不该碰这个包
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        db_path = Path(path or DEFAULT_DB_PATH)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async with AsyncSqliteSaver.from_conn_string(str(db_path)) as saver:
            await saver.setup()
            yield saver
        return

    raise ValueError(f"Unknown checkpointer: {resolved!r}")
