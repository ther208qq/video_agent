"""v1 — 解决 v0 的问题 1：同步调用阻塞事件循环。

为什么必须改：tools/bash.py 里的 Bash 工具是 `async def`。在 async 函数里
直接调用 docker SDK，它发起 HTTP 请求后就阻塞在 socket 上 —— 整个事件循环
停摆。别的工具调用、流式输出、超时回调全部卡住，直到这条命令跑完。

解法：asyncio.to_thread 把阻塞调用丢进线程池。容器对象在别的线程里创建
没问题，它只是个 handle，不绑线程。

顺带做了 v0 漏掉的收尾（问题 2 的第一步）。

对应现在：sandbox/docker.py:235 (_get_client)、:273 (create)、:83 (start)

跑：python steps/v1_async.py
"""

import asyncio

import docker


async def main() -> None:
    # docker.from_env() 本身也在做 IO（找 socket、握手），一样要丢线程池
    # docker.py:233-236 就是把它缓存起来 + to_thread
    client = await asyncio.to_thread(docker.from_env)

    container = await asyncio.to_thread(
        client.containers.run,
        "python:3.12-slim",
        command="sleep infinity",
        detach=True,
    )
    print("Sandbox created:", container.id)

    def _run() -> str:
        result = container.exec_run(["python", "-c", "print(1 + 2)"])
        return result.output.decode()

    print(await asyncio.to_thread(_run))

    # v0 漏掉的那步
    await asyncio.to_thread(container.remove, force=True)
    print("removed")


if __name__ == "__main__":
    asyncio.run(main())
