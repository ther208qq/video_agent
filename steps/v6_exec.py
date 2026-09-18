import asyncio

import docker

WORK_DIR = "/home/workspace"


async def exec_in(container, command: str, timeout: int = 10) -> tuple[str, int]:
    def _run() -> tuple[str, int]:
        result = container.exec_run(
            cmd=["bash", "-c", command],
            workdir=WORK_DIR,
            demux=False,        # stderr 并进 stdout，于是 stderr 恒为空
        )
        output = result.output or b""
        return output.decode("utf-8", errors="replace"), result.exit_code

    try:
        return await asyncio.wait_for(asyncio.to_thread(_run), timeout=timeout)
    except asyncio.TimeoutError:
        # 只是我们不等了。线程还卡在 exec_run 上，容器里那个进程也还在跑。
        return "timeout", -1


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
        # 1. docker SDK 不做 shell 解析：整串被当成一个可执行文件名
        try:
            naive = await asyncio.to_thread(container.exec_run, ["echo a b c | wc -w"])
            print("naive:", (naive.output or b"").decode().strip()[:70],
                  "| exit", naive.exit_code)
        except docker.errors.APIError as e:
            print("naive: APIError ->", str(e).strip()[:70])

        # 2. bash -c 才有管道、&&、重定向
        out, code = await exec_in(container, "echo a b c | wc -w && pwd")
        print(f"bash -c: [{code}] {out.strip()}")

        # 3. stderr 也在同一份 output 里，判成败只能看 exit_code
        out, code = await exec_in(
            container,
            "python3 -c 'import sys; print(\"往 stderr 写\", file=sys.stderr); sys.exit(3)'",
        )
        print(f"stderr:  [{code}] {out.strip()}")

        # 4. 超时：我们不等了，但进程没死
        out, code = await exec_in(container, "sleep 30", timeout=2)
        print(f"超时:    [{code}] {out}")

        # comm 是 sleep 的：PID 1 的 sleep infinity，加上我们抛下的那个。
        # 数到 1 说明进程被清掉了，数到 2 说明它还活着。
        probe = await asyncio.to_thread(
            container.exec_run,
            ["bash", "-c", "ps -eo comm | grep -cx sleep"],
        )
        print("容器里 sleep 进程数:", (probe.output or b"").decode().strip())
    finally:
        await asyncio.to_thread(container.remove, force=True)


if __name__ == "__main__":
    asyncio.run(main())
