"""子代理：上下文隔离的一次性 agent。

主 agent 的探索过程会挤占它自己的窗口，这里让它能按类型把活儿甩出去，负责的子代理
另开一个会话干活，中间的几十轮留在那边，只把结论带回来。
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool, tool

from skills import SKILL_REGISTRY

from ._utils import append_to_system_message

DEFAULT_SUBAGENT_PROMPT = (
    "In order to complete the objective that the user asks of you, you have "
    "access to a number of standard tools."
)


@dataclass
class SubAgent:
    """一个具名子代理。"""

    name: str
    description: str
    """干什么用的。主 agent 靠这句话决定派不派给它，写具体点。"""

    system_prompt: str

    skills: tuple[str, ...] = ()
    """预装的技能名。正文直接进 system prompt——子代理没有 LoadSkill 可调。"""


#: 不指定类型时的兜底，隐式保留，对应上游的 general_purpose_agent=True
GENERAL_PURPOSE = SubAgent(
    name="general-purpose",
    description="通用子代理，什么活都能接。拿不准该派给谁就用它。",
    system_prompt=DEFAULT_SUBAGENT_PROMPT,
)


def create_subagent(
    *,
    model: str | BaseChatModel,
    spec: SubAgent,
    tools: Sequence[BaseTool | Callable | dict[str, Any]],
    middleware: list[AgentMiddleware] | None = None,
) -> Any:
    """按规格建一个子代理：只跑一次，不留会话。

    不给 checkpointer——一次调用就是一次完整执行，跑挂了不续，所以也不需要
    checkpoint_ns 那套隔离。
    """
    prompt = spec.system_prompt
    # 预装：子代理没有 LoadSkill 可调，技能正文只能一进厂就写死进提示词。
    # 名字写错就当场炸——这是建图时的配置，不是运行期输入，不该拖到 Task 调用才暴露
    for name in spec.skills:
        skill = SKILL_REGISTRY.get(name)
        if skill is None:
            raise ValueError(
                f"subagent {spec.name!r}: unknown skill {name!r}. "
                f"Available: {', '.join(SKILL_REGISTRY)}"
            )
        doc = skill.read_doc().strip()
        if doc:
            prompt = f"{prompt}\n\n{doc}"

    return create_agent(
        model,
        system_prompt=prompt,
        tools=list(tools),
        middleware=list(middleware or []),
        name=spec.name,
    )


#: 主 agent 照着这段决定派不派、派给谁。分流规则的措辞取自 LangAlpha 的
#: subagent_coordination.md.j2——上游验证过的一套，没必要自己另编。
_ROSTER_HEADER = """## Subagents

Delegate via the `Task` tool. Each subagent runs with its own isolated context,
in the background. `Task` hands you back an ID immediately; collect the report
with `TaskOutput(task_id=...)` before you finish your turn.

"""

_ROSTER_FOOTER = """
Delegate when: multi-step tool chains, bulk data processing, independent work
that can run in parallel, or anything needing 3+ tool calls.
Do directly: single tool calls, simple lookups or calculations, tasks you
already have the context for.
"""


@dataclass
class _Task:
    """一个后台 Task 调用的全部状态。"""

    task_id: str
    subagent_type: str
    running: asyncio.Task
    seen: bool = False
    """通知过主 agent 没有。没这个标记，同一次收工会被反复播报。"""


class SubAgentMiddleware(AgentMiddleware):
    """给主 agent 挂两个工具：Task 按类型把活儿甩给子代理后台跑，TaskOutput 取结果。"""

    def __init__(
        self,
        *,
        model: str | BaseChatModel,
        tools: Sequence[BaseTool | Callable | dict[str, Any]],
        middleware: list[AgentMiddleware] | None = None,
        subagents: Sequence[SubAgent] = (),
        timeout: float = 60.0,
    ) -> None:
        super().__init__()
        self.specs = [GENERAL_PURPOSE, *subagents]
        #: 主 agent 收尾后最多等后台任务多久。
        self.timeout = timeout
        self.subagent_graphs = {
            spec.name: create_subagent(
                model=model, spec=spec, tools=tools, middleware=middleware
            )
            for spec in self.specs
        }
        # task_id -> 在跑的后台任务。字典持的就是强引用（asyncio 自己只持弱引用，
        # 不留一份任务会被 GC 掉），也是主 agent 取结果时查的那张表
        self._tasks: dict[str, _Task] = {}

        @tool("Task")
        async def task(description: str, prompt: str, subagent_type: str) -> str:
            """Launch a subagent for complex, multi-step tasks.

            Use for: complex multi-task work, isolated research, context-heavy
            operations. Do NOT use for simple 1-2 tool operations — do those
            yourself.

            The subagent sees nothing of this conversation and its intermediate
            steps stay hidden. It runs in the background: this returns as soon
            as the subagent is started, not when it finishes, so you can keep
            working. If you need several independent pieces of work done, call
            this once per piece in the same message and they run concurrently.

            Args:
                description: Short 1-2 sentence title of the task, in active voice.
                prompt: The subagent's complete instructions. Include all the
                    context, data and expected output format it needs — it
                    cannot ask follow-up questions.
                subagent_type: Which subagent to use. Must be one of the names
                    listed in the Subagents section of your instructions.

            Returns:
                The task's ID, not its work. Collect the result with
                TaskOutput(task_id=...).
            """
            subagent = self.subagent_graphs.get(subagent_type)
            if subagent is None:
                # 返错文本而不是抛异常：这条错误是自纠的，模型看到合法清单会改
                return (
                    f"ERROR: unknown subagent_type {subagent_type!r}. "
                    f"Available: {', '.join(self.subagent_graphs)}"
                )

            # 只给 messages：子代理要的一切都在 prompt 里，父对话不该漏进去，
            # 否则它的上下文就不再独立
            state = {"messages": [HumanMessage(content=prompt)]}
            task_id = secrets.token_urlsafe(4)[:6]
            self._tasks[task_id] = _Task(
                task_id=task_id,
                subagent_type=subagent_type,
                running=asyncio.create_task(subagent.ainvoke(state)),
            )
            return (
                f"Task-{task_id} started in the background ({subagent_type}): "
                f"{description}\n"
                f"Collect its result with TaskOutput(task_id={task_id!r})."
            )

        @tool("TaskOutput")
        async def task_output(task_id: str | None = None, timeout: float = 0) -> str:
            """Collect the result of a background subagent started with Task.

            Args:
                task_id: The ID Task returned, e.g. "8kQ2mZ". Omit to list
                    every task started so far.
                timeout: Seconds to wait for the task to finish. Default 0
                    returns straight away with whatever is there.

            Returns:
                The subagent's report once it has finished, otherwise how far
                along it is.
            """
            if task_id is None:
                if not self._tasks:
                    return "No background tasks have been started yet."
                rows = []
                for tid, task in self._tasks.items():
                    running = task.running
                    if not running.done():
                        rows.append(f"- Task-{tid}: running")
                    elif running.cancelled():
                        rows.append(f"- Task-{tid}: cancelled")
                    elif running.exception() is not None:
                        rows.append(f"- Task-{tid}: failed")
                    else:
                        rows.append(f"- Task-{tid}: finished")
                return "Background tasks:\n" + "\n".join(rows)

            task = self._tasks.get(task_id)
            if task is None:
                known = ", ".join(self._tasks) or "none"
                return (
                    f"ERROR: no task {task_id!r}. Known task IDs: {known}"
                )
            running = task.running

            if timeout > 0 and not running.done():
                await asyncio.wait([running], timeout=timeout)
            if not running.done():
                return (
                    f"Task-{task_id} is still running. Call again with a "
                    f"larger timeout to wait for it."
                )

            if running.cancelled():
                return f"Task-{task_id} was cancelled before it produced a result."
            if (err := running.exception()) is not None:
                return f"Task-{task_id} failed: {err!r}"

            # 尾随空白会让 Anthropic 拒掉这条消息
            return running.result()["messages"][-1].text.rstrip()

        self.tools = [task, task_output]

    def has_pending_tasks(self) -> bool:
        return any(not task.running.done() for task in self._tasks.values())

    async def wait_for_all(self) -> None:
        """等后台任务都收工，最多等 timeout 秒。等不到不算错，主 agent 照常收尾。"""
        pending = [t.running for t in self._tasks.values() if not t.running.done()]
        if pending:
            await asyncio.wait(pending, timeout=self.timeout)

    def check_notification(self) -> str | None:
        """已经收工、还没跟主 agent 说过的任务，凑成一条通知。

        通知只报「去 TaskOutput 取」，不夹带结果正文——结果只有一个出口，
        在通知里再抄一份，两份迟早对不上。
        """
        unseen = [t for t in self._tasks.values() if t.running.done() and not t.seen]
        if not unseen:
            return None
        for task in unseen:
            task.seen = True

        named = ", ".join(
            f"**Task-{t.task_id}** ({t.subagent_type})" for t in unseen
        )
        if len(unseen) == 1:
            return (
                f"Your background subagent task has finished: {named}.\n\n"
                f'Call `TaskOutput(task_id="{unseen[0].task_id}")` to see the result.'
            )
        return (
            f"Your background subagent tasks have finished: {named}.\n\n"
            f"Call `TaskOutput()` to see every result."
        )

    async def awrap_model_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        # 少了这段清单，模型不知道 subagent_type 该填什么，路由就只能靠撞
        lines = [f"- **{spec.name}**: {spec.description}" for spec in self.specs]
        manifest = _ROSTER_HEADER + "\n".join(lines) + "\n" + _ROSTER_FOOTER

        filtered = request.override(
            system_message=append_to_system_message(request.system_message, manifest)
        )
        return await handler(filtered)


class SubAgentOrchestrator:
    """把 agent 包一层：主 agent 收尾后，替它把后台任务的结果接回来。

    主 agent 收尾时后台任务常常还在跑，它的结论就没人接。这里在 graph 外面守一轮：
    等任务收工，把一条通知写回对话，再把主 agent 叫起来续跑一次——它看到通知会
    自己去调 TaskOutput。只守一轮，取不到就放行，不在这里空转。
    """

    def __init__(self, agent: Any, middleware: SubAgentMiddleware) -> None:
        self.agent = agent
        self.middleware = middleware

    async def ainvoke(
        self, input_state: Any, config: dict[str, Any] | None = None
    ) -> Any:
        config = config or {}
        result = await self.agent.ainvoke(input_state, config)


        note = self.middleware.check_notification()
        if note is None and self.middleware.has_pending_tasks():
            await self.middleware.wait_for_all()
            note = self.middleware.check_notification()
        if note is None:
            return result

        message = HumanMessage(content=note, name="orchestrator")
        # 将消息通知追加到主agent的state中
        if getattr(self.agent, "checkpointer", None) is None:
            state: Any = {"messages": [*result["messages"], message]}
        else:
            await self.agent.aupdate_state(
                config, {"messages": [message]}, as_node="__start__"
            )
            state = None
        return await self.agent.ainvoke(state, config)
