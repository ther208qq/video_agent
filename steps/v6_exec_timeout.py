import asyncio

import docker

WORK_DIR = "/home/workspace"


async def exec_in(container, command: str, timeout: int = 60) -> tuple[str, str, int]:
    def _run() -> tuple[str, str, int]:
        # 超时交给容器里的 timeout(1)：它把命令 fork 进自己的进程组，
        # 到点对整组发 SIGTERM，bash -c 拉起来的子进程跟着走；
        # -k 5 是兜底，5 秒后还没退就 SIGKILL。
        # 只在容器外面 wait_for 是拦不住容器里的进程的。
        r = container.exec_run(
            cmd=["timeout", "-k", "5", str(timeout), "bash", "-c", command],
            workdir=WORK_DIR,
            demux=False,
        )
        exit_code = r.exit_code if r.exit_code is not None else -1
        # 124 是 timeout(1) 报「是我杀掉的」。代价：命令自己恰好返回 124
        # 也会被当成超时，这个歧义留着。
        if exit_code == 124:
            return "", "timeout", -1
        return (r.output or b"").decode("utf-8", errors="replace"), "", exit_code

    try:
        # 外层只是兜底：timeout 自己卡死时还得有人收尾，所以留出 -k 5 的余量
        return await asyncio.wait_for(asyncio.to_thread(_run), timeout=timeout + 10)
    except asyncio.TimeoutError:
        return "", "timeout", -1


async def count(container, pattern: str) -> int:
    """数还有几个进程的 cmdline 匹配 pattern。

    方括号是为了不匹配到这条探测命令自己 —— 它的命令行里带着同样的字符串。
    """
    def _probe() -> str:
        r = container.exec_run(
            cmd=["bash", "-c",
                 f"grep -l '{pattern}' /proc/[0-9]*/cmdline 2>/dev/null | wc -l"],
            workdir=WORK_DIR,
        )
        return (r.output or b"").decode().strip()

    return int(await asyncio.to_thread(_probe) or "0")


async def main() -> None:
    client = await asyncio.to_thread(docker.from_env)
    container = await asyncio.to_thread(
        client.containers.run,
        "video-agent-sandbox:latest",
        command=["sleep", "infinity"],
        detach=True,
        working_dir=WORK_DIR,
    )

    try:
        out, err, code = await exec_in(container, "echo hi")
        print(f"普通命令   exit={code} stderr={err!r} stdout={out.strip()!r}")

        out, err, code = await exec_in(container, "exit 3")
        print(f"非零退出   exit={code} stderr={err!r}   <- 不能被当成超时")

        print()
        # 命令自己拉起一个后台子进程，再看超时时它俩会不会一起被收掉
        command = "sleep 986543 & sleep 987654"
        main_re, child_re = "sleep 9865[4]3", "sleep 9876[5]4"
        print("命令:", command)

        task = asyncio.create_task(exec_in(container, command, timeout=3))
        await asyncio.sleep(1.5)
        during = (await count(container, main_re), await count(container, child_re))
        print(f"超时前     main={during[0]} child={during[1]}")

        out, err, code = await task
        print(f"超时返回   exit={code} stderr={err!r}")

        await asyncio.sleep(1.0)
        after = (await count(container, main_re), await count(container, child_re))
        print(f"超时后     main={after[0]} child={after[1]}")
    finally:
        await asyncio.to_thread(container.remove, force=True)


if __name__ == "__main__":
    asyncio.run(main())
