
from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware

from middleware import (
    LargeResultEvictionMiddleware,
    SkillsMiddleware,
)
from sandbox import SandboxRuntime
from tools import get_tools
from tools.bash import create_execute_bash_tool
from tools.code_execution import create_execute_code_tool
from tools.file_ops import create_read_tool


def create_video_agent(
    model,
    system_prompt: str = "You are a helpful assistant.",
    tools=None,
    middleware=None,
    checkpointer=None,
    store=None,
    sandbox: SandboxRuntime | None = None,
):

    resolved = get_tools() if tools is None else tools
    resolved_middleware = (
        list(middleware) if middleware is not None else [SkillsMiddleware()]
    )
    if sandbox is not None:
        resolved = [
            *resolved,
            create_read_tool(sandbox),
            create_execute_bash_tool(sandbox),
            create_execute_code_tool(sandbox),
        ]

        resolved_middleware.insert(0, LargeResultEvictionMiddleware(sandbox))

    resolved_middleware.append(
        SummarizationMiddleware(
            model,
            trigger=("tokens", 100_000),
            trim_tokens_to_summarize=32_000,
        )
    )

    return create_agent(
        model,
        system_prompt=system_prompt,
        tools=resolved,
        middleware=resolved_middleware,
        checkpointer=checkpointer,
        store=store,
    ).with_config({"recursion_limit": 2000})


async def main() -> None:
    import os
    import sys

    from dotenv import load_dotenv
    from langchain.chat_models import init_chat_model

    from sandbox.providers import create_provider
    from storage import open_checkpointer, resolve_thread_id

    load_dotenv(override=True)

    model = init_chat_model(os.getenv("MODEL", "deepseek:deepseek-chat"))
    prompt = " ".join(sys.argv[1:]) or "生成一段python代码，用于计算数字计算，并计算3*8的结果"
    thread_id = resolve_thread_id()

    provider = create_provider()
    runtime = await provider.create()

    try:
        async with open_checkpointer() as checkpointer:
            agent = create_video_agent(
                model,
                checkpointer=checkpointer,
                sandbox=runtime,
            )
            result = await agent.ainvoke(
                {"messages": [{"role": "user", "content": prompt}]},
                {"configurable": {"thread_id": thread_id}},
            )
            print(result["messages"][-1].content)
    finally:
        await runtime.delete()
        await provider.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
