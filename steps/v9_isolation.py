"""v9：把 Docker 从业务 Tool 中隔离出来。

v1–v8 验的都是「沙箱里能做什么」，这一步验的是「谁在依赖 Docker」。

现状有两条线把业务工具和 Docker SDK 拴在一起：

    tools/bash.py:15          from sandbox import SandboxRuntime
      └─ sandbox/__init__.py:3   from .docker import ...        ← 模块级、急切
           └─ sandbox/docker.py:14  import docker as docker_sdk

LangAlpha 的切法是加一层 provider 工厂：具体 provider 的 import 写在函数体的
if 分支里，执行到那一行才加载。于是「谁被加载」由运行时的 provider 选择决定，
而不是由 import 图静态决定。

脚本分四段把上面两条线证出来，再把那个机制跑一遍。

注意：这一步只看不动 —— sandbox/ 包一行没改。它证明的是「机制可行」，
不是「已经修好」。
"""

import asyncio
import itertools
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 直接跑 steps/ 下的脚本时，sys.path[0] 是 steps/ 而不是项目根
sys.path.insert(0, str(PROJECT_ROOT))

from sandbox import SandboxRuntime  # noqa: E402
from sandbox.runtime import (  # noqa: E402
    Artifact,
    CodeRunResult,
    ExecResult,
    RuntimeState,
)
from tools.bash import create_execute_bash_tool  # noqa: E402
from tools.code_execution import create_execute_code_tool  # noqa: E402


def probe(code: str) -> str:
    """在全新的解释器里跑一段代码，把输出抓回来。

    本进程一旦 import 过 sandbox，docker 就躺在 sys.modules 里了，之后
    再查什么都恒为 True —— 那个函数已经跑完了。要问「全新启动会怎样」，
    只能开新进程问。
    """
    # Windows 上父进程默认按 GBK 解码子进程输出，而子进程写的是 UTF-8，
    # 会在读取线程里抛 UnicodeDecodeError（然后 stdout 变成 None）。
    # 两头都钉死成 UTF-8。
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    return ((proc.stdout or "") + (proc.stderr or "")).strip()


class InMemoryRuntime(SandboxRuntime):
    """纯内存的假沙箱，一个字节都不碰 Docker。

    用来回答一个问题：把 Docker 拿掉之后，业务工具还能不能工作？
    如果它能跑，说明工具真正依赖的是 SandboxRuntime 这个契约，
    唯一的耦合就只剩 import 那条线。
    """

    def __init__(self, working_dir: str = "/home/workspace") -> None:
        self._id = "memory-1"
        self._working_dir = working_dir
        self._state = RuntimeState.RUNNING
        self._files: dict[str, bytes] = {}
        self.calls: list[str] = []

    @property
    def id(self) -> str:
        return self._id

    @property
    def working_dir(self) -> str:
        return self._working_dir

    # -- Lifecycle --

    async def start(self, timeout: int = 120) -> None:
        self._state = RuntimeState.RUNNING

    async def stop(self, timeout: int = 60, *, force: bool = False) -> None:
        self._state = RuntimeState.STOPPED

    async def delete(self) -> None:
        self._files.clear()

    async def get_state(self) -> RuntimeState:
        return self._state

    # -- Execution --

    async def exec(self, command: str, timeout: int = 60) -> ExecResult:
        self.calls.append(f"exec({command!r})")

        # 只需认识几条命令。认不出来就报 127，专门用来把工具的失败分支
        # 也走一遍 —— 那条分支里有一句写死的「docker 把 stderr 并进了
        # stdout」，正好看看它在没有 docker 时是什么表现。
        if command.startswith("echo "):
            return ExecResult(stdout=command[5:] + "\n", stderr="", exit_code=0)
        if command == "ls":
            listing = "".join(f"{name}\n" for name in sorted(self._files))
            return ExecResult(stdout=listing, stderr="", exit_code=0)
        if command.startswith("python3 "):
            return ExecResult(stdout="memory-runtime: 不真跑脚本\n", stderr="", exit_code=0)
        return ExecResult(
            stdout="",
            stderr=f"memory-runtime: 不认识的命令 {command}",
            exit_code=127,
        )

    async def code_run(
        self,
        code: str,
        env: dict[str, str] | None = None,
        timeout: int = 300,
    ) -> CodeRunResult:
        self.calls.append(f"code_run({len(code)} 字符)")

        # 真的 exec 一个 Python 解释器进来，就变成「假沙箱里套真解释器」，
        # 反而把重点搞糊了。这里直接给固定结果，看的是工具怎么接住它。
        # 顺带塞一个 artifact，把 v8 那条产物通道也走一遍。
        return CodeRunResult(
            stdout="memory-runtime: 已忽略代码，返回预设输出\n",
            stderr="",
            exit_code=0,
            artifacts=[Artifact(type="image/png", data="bWVtcnVudGltZQ==", name="mem.png")],
        )

    # -- File I/O --

    async def upload_file(self, content: bytes, dest_path: str) -> None:
        self._files[dest_path] = content

    async def upload_files(self, files: list[tuple[bytes | str, str]]) -> None:
        for source, dest in files:
            data = source.encode("utf-8") if isinstance(source, str) else source
            self._files[dest] = data

    async def download_file(self, path: str) -> bytes:
        if path not in self._files:
            raise FileNotFoundError(f"No such file in sandbox: {path}")
        return self._files[path]

    async def list_files(self, directory: str) -> list[dict[str, Any]]:
        prefix = directory.rstrip("/") + "/"
        return [
            {"name": path[len(prefix):], "size": len(data), "is_dir": False}
            for path, data in sorted(self._files.items())
            if path.startswith(prefix)
        ]


_CALL_SEQ = itertools.count(1)


async def call_tool(tool: Any, /, **args: Any) -> tuple[Any, Any]:
    """按模型发来的形状调用工具，返回 (正文, 产物)。

    这里有个坑：直接 ainvoke({"code": ...}) 也能跑通，但回来的是裸正文，
    content_and_artifact 的产物会被悄悄丢掉 —— langchain_core 的
    _format_output 在没有 tool_call_id 时只回 content，artifact 就没了。
    带上 type/name/id 走 ToolCall 的形状，才回 ToolMessage，artifact 才在。
    """
    message = await tool.ainvoke(
        {
            "type": "tool_call",
            "name": tool.name,
            "id": f"call_{next(_CALL_SEQ)}",
            "args": args,
        }
    )
    return message.content, getattr(message, "artifact", None)


# ---------------------------------------------------------------------------
# 1. 现状：业务工具拖进了 Docker SDK
# ---------------------------------------------------------------------------

def section_leak() -> None:
    print("=== 1. 现状：业务 Tool 拖进 Docker SDK ===")
    print("  在全新解释器里只 import 业务工具，然后问 docker 在不在:")
    print("   ", probe("import tools.bash, sys; print('docker in sys.modules =', 'docker' in sys.modules)"))

    print()
    print("  肇事的是这几行:")
    for lineno, line in enumerate(
        (PROJECT_ROOT / "sandbox" / "__init__.py").read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if "docker" in line:
            print(f"    sandbox/__init__.py:{lineno}  {line.strip()}")
    print("  业务工具只想拿一个抽象接口，模块级 import 却把整个 Docker SDK 顺了进来。")


# ---------------------------------------------------------------------------
# 2. 抽象层本身是干净的
# ---------------------------------------------------------------------------

def section_abstraction() -> None:
    print()
    print("=== 2. 抽象层本身不脏 ===")
    print("  绕开包的 __init__，用 importlib 直接加载 sandbox/runtime.py:")

    # import sandbox.runtime 是没用的 —— 那会先执行包的 __init__.py，docker
    # 照样被拉进来。要单独看 runtime.py 干不干净，只能从文件路径直接加载。
    code = (
        "import importlib.util, sys;"
        "spec = importlib.util.spec_from_file_location('rt', 'sandbox/runtime.py');"
        "mod = importlib.util.module_from_spec(spec);"
        "spec.loader.exec_module(mod);"
        "print('docker in sys.modules =', 'docker' in sys.modules);"
        "print('导出的符号:', sorted(n for n in dir(mod) if not n.startswith('_'))[:6], '...')"
    )
    for line in probe(code).splitlines():
        print("   ", line)

    print()
    print("  结论：泄漏不在抽象层，就在 sandbox/__init__.py 那一行急切 import。")


# ---------------------------------------------------------------------------
# 3. 工具只依赖契约，不依赖 Docker
# ---------------------------------------------------------------------------

async def section_tools() -> None:
    print()
    print("=== 3. 拿掉 Docker，业务工具照跑 ===")

    runtime = InMemoryRuntime()
    bash_tool = create_execute_bash_tool(runtime)
    code_tool = create_execute_code_tool(runtime)
    print(f"  绑定 {type(runtime).__name__} 到未经修改的 tools/bash.py、tools/code_execution.py")
    print()

    content, _ = await call_tool(bash_tool, command="echo 沙箱说你好")
    print("  Bash        成功路径 →", str(content).strip())

    content, _ = await call_tool(bash_tool, command="curl example.com")
    print("  Bash        失败路径 →", str(content).strip().replace("\n", " | "))

    content, artifact = await call_tool(code_tool, code="print(1)")
    print("  ExecuteCode 正文     →", str(content).strip().replace("\n", " | "))
    print("  ExecuteCode 产物     →", artifact)

    print()
    print("  两个工具的产出都正常，说明它们真正依赖的是 SandboxRuntime 这个契约。")
    print("  唯一的耦合就剩 import 那条线 —— 而那条线是可以在 import 图上切掉的。")
    print()
    print("  假沙箱收到的调用:", runtime.calls)


# ---------------------------------------------------------------------------
# 4. 惰性工厂机制能切断那条线
# ---------------------------------------------------------------------------

# 把具体 provider 的 import 放在函数体的分支里。执行到这个函数、且真的
# 走到那个分支，才会去加载 SDK。
_LAZY = """
import sys

def create_provider(name):
    if name == "docker":
        import docker
        return docker
    raise ValueError("unknown provider: " + repr(name))

print("  刚 import 完模块，还没调工厂   :", "docker" in sys.modules)
try:
    create_provider("nope")
except ValueError as exc:
    print("  create_provider('nope') 抛", exc)
    print("  抛完之后 docker 在不在         :", "docker" in sys.modules)
create_provider("docker")
print("  create_provider('docker') 之后 :", "docker" in sys.modules)
"""

# 看着像「查找表」，其实模块级就求值了 —— 和 sandbox/__init__.py:3 同构。
_EAGER = """
import sys
import docker

PROVIDERS = {"docker": docker}

print("  import 这个模块的瞬间 :", "docker" in sys.modules)
"""


def section_factory() -> None:
    print()
    print("=== 4. 惰性工厂 vs 模块级 import ===")

    print()
    print("  (a) 函数体内 import —— LangAlpha 的写法")
    for line in probe(_LAZY).splitlines():
        print("   ", line)

    print()
    print("  (b) 模块级 import —— 现在的写法")
    for line in probe(_EAGER).splitlines():
        print("   ", line)

    print()
    print("  (a) 里 docker 只在真选中 docker 那一刻才进 sys.modules，")
    print("  (b) 里一 import 就进来了，选择权根本没有。")
    print("  换成 (a) 的写法，业务工具那条 from sandbox import SandboxRuntime")
    print("  就再也拖不动 Docker SDK 了 —— 前提是 sandbox/__init__.py 也照 (a) 改。")


async def main() -> None:
    section_leak()
    section_abstraction()
    await section_tools()
    section_factory()

    print()
    print("=== 小结 ===")
    print("  1. 现状确实漏：业务工具 → sandbox/__init__.py:3 → docker")
    print("  2. 抽象层是干净的，问题只在那行急切 import")
    print("  3. 工具本身不依赖 Docker，只依赖 SandboxRuntime 契约")
    print("  4. 把 provider 选择收进工厂函数的分支体内，就能切断那条线")
    print()
    print("  本步骤只做验证，没有改 sandbox/ —— 要真修，需要把 docker.py 移进")
    print("  sandbox/providers/ 并让 __init__.py 停止急切导出它。")


if __name__ == "__main__":
    asyncio.run(main())
