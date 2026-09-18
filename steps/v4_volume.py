import asyncio
from pathlib import Path

import docker

HOST_DIR = Path(__file__).resolve().parent.parent / "workspace"
WORK_DIR = "/home/workspace"


async def main() -> None:
    HOST_DIR.mkdir(parents=True, exist_ok=True)
    client = await asyncio.to_thread(docker.from_env)

    container = await asyncio.to_thread(
        client.containers.run,
        "video-agent-sandbox:latest",
        command=["sleep", "infinity"],
        detach=True,
        working_dir=WORK_DIR,
        volumes={str(HOST_DIR): {"bind": WORK_DIR, "mode": "rw"}},
    )

    try:
        # 容器里写 -> 宿主机立刻看得见
        await asyncio.to_thread(
            container.exec_run,
            ["bash", "-c", "echo 容器写的 > note.txt"],
            workdir=WORK_DIR,
        )
        print("容器写完后，宿主机读到：", (HOST_DIR / "note.txt").read_text(encoding="utf-8").strip())

        # 宿主机写 -> 容器里立刻看得见
        (HOST_DIR / "from-host.srt").write_text(
            "1\n00:00:01,000 --> 00:00:03,000\n你好\n", encoding="utf-8"
        )

        def _cat() -> str:
            return container.exec_run(
                ["bash", "-c", "cat from-host.srt"], workdir=WORK_DIR
            ).output.decode()

        print("宿主机写完后，容器读到：", (await asyncio.to_thread(_cat)).replace("\n", " | ").strip())
    finally:
        await asyncio.to_thread(container.remove, force=True)


if __name__ == "__main__":
    asyncio.run(main())
