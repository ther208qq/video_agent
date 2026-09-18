"""v2 — 解决 v1 遗留的「裸句柄」：容器对象没有归属。

v1 里 container 是个裸的 docker 对象，谁拿着它谁就能乱调，而 agent 层
根本不该知道 docker 的 API 长什么样。更麻烦的是生命周期没有归属：
容器建出来之后，什么时候停、什么时候删、现在什么状态，散在各处。

解法：一层薄薄的 DockerRuntime 把它包住，对外只暴露
启动/停止/删除/查状态。上层拿到的是「一个沙箱」，不是「一个 docker 容器」。

另一个细节：现在想拿状态得问一次 daemon。_container.status 是本地缓存，
不 reload 读到的是老值。

对应现在：sandbox/docker.py:39-47（状态映射）、:59-93（DockerRuntime）
          sandbox/runtime.py:52-60（RuntimeState）

跑：python steps/v2_lifecycle.py
"""

import asyncio
from enum import Enum

import docker


class RuntimeState(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    STARTING = "starting"
    ERROR = "error"


# docker 的 status 是字符串，取值比我们关心的多（paused/removing/dead...）。
# 在这里映射一次，上层就永远不用认这些字符串。现在是 docker.py:39-47。
_STATUS_MAP = {
    "running": RuntimeState.RUNNING,
    "created": RuntimeState.STARTING,
    "restarting": RuntimeState.STARTING,
    "paused": RuntimeState.STOPPED,
    "exited": RuntimeState.STOPPED,
    "removing": RuntimeState.STOPPED,
    "dead": RuntimeState.ERROR,
}


class DockerRuntime:
    def __init__(self, container, runtime_id: str) -> None:
        self._container = container
        self._id = runtime_id

    @property
    def id(self) -> str:
        return self._id

    async def start(self) -> None:
        await asyncio.to_thread(self._container.start)

    async def stop(self, timeout: int = 60) -> None:
        await asyncio.to_thread(self._container.stop, timeout=timeout)

    async def delete(self) -> None:
        await asyncio.to_thread(self._container.remove, force=True)

    async def get_state(self) -> RuntimeState:
        # reload 才会去 daemon 拉最新状态；不调它读的是本地缓存
        await asyncio.to_thread(self._container.reload)
        return _STATUS_MAP.get(self._container.status, RuntimeState.ERROR)

    async def exec(self, command: str) -> str:
        def _run() -> str:
            result = self._container.exec_run(["bash", "-c", command])
            return (result.output or b"").decode("utf-8", errors="replace")

        return await asyncio.to_thread(_run)


async def main() -> None:
    client = await asyncio.to_thread(docker.from_env)
    container = await asyncio.to_thread(
        client.containers.run,
        "python:3.12-slim",
        command=["sleep", "infinity"],
        detach=True,
    )

    sandbox = DockerRuntime(container, "demo")
    print("id:   ", sandbox.id)
    print("state:", (await sandbox.get_state()).value)
    print("out:  ", (await sandbox.exec("echo hi")).strip())

    await sandbox.stop()
    print("state:", (await sandbox.get_state()).value)

    await sandbox.delete()
    print("deleted")


if __name__ == "__main__":
    asyncio.run(main())
