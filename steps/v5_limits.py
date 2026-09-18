import asyncio

import docker

NAME = "video-agent-sandbox-demo"
WORK_DIR = "/home/workspace"


async def main() -> None:
    client = await asyncio.to_thread(docker.from_env)

    # 名字必须唯一，不然 run 会报冲突
    def _cleanup() -> None:
        try:
            client.containers.get(NAME).remove(force=True)
        except docker.errors.NotFound:
            pass

    await asyncio.to_thread(_cleanup)

    container = await asyncio.to_thread(
        client.containers.run,
        "video-agent-sandbox:latest",
        command=["sleep", "infinity"],
        name=NAME,
        detach=True,
        init=True,                  # tini 当 PID 1，负责收僵尸
        auto_remove=False,          # 生命周期自己管，不让 docker 抢着删
        mem_limit="2g",             # 超了 OOM 掉的是它自己，不是宿主机
        nano_cpus=int(2.0 * 1e9),   # 2 核
        network_mode="bridge",      # 联网策略显式写出来，别吃默认值
        working_dir=WORK_DIR,
    )

    try:
        def _probe(cmd: str) -> str:
            return container.exec_run(cmd=["bash", "-c", cmd]).output.decode()

        print("PID 1 =", (await asyncio.to_thread(_probe, "cat /proc/1/comm")).strip())
        print("nproc =", (await asyncio.to_thread(_probe, "nproc")).strip())

        # 名字固定，「重连」这件事才成立（真实实现把 id 拼进名字）
        found = await asyncio.to_thread(client.containers.get, NAME)
        print("按名字重连到:", found.id[:12])
    finally:
        await asyncio.to_thread(container.remove, force=True)


if __name__ == "__main__":
    asyncio.run(main())
