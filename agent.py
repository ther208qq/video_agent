
from langchain.agents import create_agent

from sandbox import SandboxRuntime
from tools import get_tools
from tools.bash import create_execute_bash_tool
from tools.code_execution import create_execute_code_tool


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
    if sandbox is not None:
        resolved = [
            *resolved,
            create_execute_bash_tool(sandbox),
            create_execute_code_tool(sandbox),
        ]

    return create_agent(
        model,
        system_prompt=system_prompt,
        tools=resolved,
        middleware=middleware or [],
        checkpointer=checkpointer,
        store=store,
    ).with_config({"recursion_limit": 2000})


if __name__ == "__main__":
    import os

    from dotenv import load_dotenv
    from langchain.chat_models import init_chat_model
    from langgraph.checkpoint.memory import InMemorySaver

    load_dotenv(override=True)

    model = init_chat_model(os.getenv("MODEL", "deepseek:deepseek-chat"))

    agent = create_video_agent(model, checkpointer=InMemorySaver())

    result = agent.invoke(
        {"messages": [{"role": "user", "content": "说一句你好"}]},
        {"configurable": {"thread_id": "t1"}},
    )
    print(result["messages"][-1].content)
