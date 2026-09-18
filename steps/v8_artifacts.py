import asyncio
import base64
import sys
import uuid
from pathlib import Path

import docker

# 直接跑 steps/ 下的脚本时，sys.path[0] 是 steps/ 而不是项目根
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sandbox.providers._chart_capture import build_code_wrapper, extract_artifacts

WORK_DIR = "/home/workspace"

CODE = """\
import matplotlib.pyplot as plt

plt.plot([1, 2, 3, 4], [1, 4, 9, 16])
plt.title('中文标题一')
plt.xlabel('横轴')
plt.show()

plt.bar(['甲', '乙'], [3, 5])
plt.title('第二张')
plt.show()

print('两张图都画完了')
"""


async def run_python(container, source: str, tag: str) -> str:
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    script = f"{WORK_DIR}/_{tag}_{uuid.uuid4().hex[:6]}.py"

    def _work() -> str:
        container.exec_run(
            cmd=["bash", "-c",
                 f"python3 -c \"import base64;open('{script}','wb')"
                 f".write(base64.b64decode('{encoded}'))\""],
            workdir=WORK_DIR,
        )
        r = container.exec_run(cmd=["bash", "-c", f"python3 {script}"], workdir=WORK_DIR)
        return (r.output or b"").decode("utf-8", errors="replace")

    return await asyncio.to_thread(_work)


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
        print("装 matplotlib ...")
        await asyncio.to_thread(
            container.exec_run,
            cmd=["bash", "-c", "pip install -q matplotlib"],
            workdir=WORK_DIR,
        )

        print()
        print("=== 不加 wrapper ===")
        bare = "import matplotlib\nmatplotlib.use('Agg')\n" + CODE
        out = await run_python(container, bare, "bare")
        print("  stdout:", out.strip().replace("\n", " | "))

        print()
        print("=== 加 wrapper ===")
        out = await run_python(container, build_code_wrapper(CODE), "wrapped")
        artifacts, clean = extract_artifacts(out)
        print("  原始 stdout:", len(out), "字符")
        for a in artifacts:
            print(f"  产物: {a.name}  {a.type}  {len(a.data)} 字节 base64")
        print("  给模型看的正文:", clean.strip().replace("\n", " | "))
    finally:
        await asyncio.to_thread(container.remove, force=True)


if __name__ == "__main__":
    asyncio.run(main())
