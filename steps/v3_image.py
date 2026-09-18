
import asyncio
from pathlib import Path

import docker

IMAGE = "video-agent-sandbox:latest"
DOCKERFILE = Path(__file__).resolve().parent.parent / "Dockerfile.sandbox"


async def ensure_image(client) -> None:
    def _check() -> bool:
        try:
            client.images.get(IMAGE)
            return True
        except docker.errors.ImageNotFound:
            return False

    if await asyncio.to_thread(_check):
        print("镜像已在本地")
        return

    if not DOCKERFILE.exists():
        # 现在这版是直接抛 FileNotFoundError，并且消息里带上手动构建命令
        # （docker.py:248-252）
        raise FileNotFoundError(f"没有 Dockerfile: {DOCKERFILE}")

    print(f"构建 {IMAGE}（第一次比较慢）...")
    await asyncio.to_thread(
        client.images.build,
        path=str(DOCKERFILE.parent),   # 构建上下文 = Dockerfile 所在目录
        dockerfile=DOCKERFILE.name,
        tag=IMAGE,
        rm=True,                       # 清掉中间层容器
    )
    print("构建完成")


async def main() -> None:
    client = await asyncio.to_thread(docker.from_env)
    await ensure_image(client)

    container = await asyncio.to_thread(
        client.containers.run,
        IMAGE,
        command=["sleep", "infinity"],
        detach=True,
    )

    try:
        def _probe() -> str:
            return container.exec_run(
                ["bash", "-c", "git --version && jq --version && python3 -V && pip -V"]
            ).output.decode()

        print(await asyncio.to_thread(_probe))
    finally:
        await asyncio.to_thread(container.remove, force=True)


if __name__ == "__main__":
    asyncio.run(main())
