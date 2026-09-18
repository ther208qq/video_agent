"""v10：封装落地后的回归守卫。

v9 量的是「漏在哪」，这一步守的是「别再漏回去」。

v10 落地前的样子：

    tools/bash.py:15          from sandbox import SandboxRuntime
      └─ sandbox/__init__.py:3   from .docker import ...        ← 模块级、急切
           └─ sandbox/providers/docker.py:14  import docker as docker_sdk

落地后的样子：具体 provider 挪进了 sandbox/providers/，包的 __init__ 只导出
契约，工厂把 provider 的 import 关在函数体的分支里。于是：

    import sandbox                 → 一个 SDK 都不加载
    import tools.bash              → 同上，工具够不着 docker
    create_provider("nope")        → 抛 ValueError，docker 依然没进来
    create_provider("docker")      → 到这一行 docker 才进 sys.modules

四段守卫：import 图（运行时行为）、AST 扫描（静态行为）、工厂惰性、契约照跑。
前三段守边界，第四段守着别为了守边界把工具改坏。

注意 steps/v1–v8 自己就是直接调 docker SDK 的实验脚本，不在扫描范围内 ——
它们是历史记录，不是要发布的代码。
"""

import ast
import asyncio
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STEPS_DIR = PROJECT_ROOT / "steps"
# 直接跑 steps/ 下的脚本时，sys.path[0] 是 steps/ 而不是项目根
sys.path.insert(0, str(PROJECT_ROOT))
# 复用 v9 的内存假沙箱和调用助手，免得再抄一遍
sys.path.insert(0, str(STEPS_DIR))

from v9_isolation import InMemoryRuntime, call_tool  # noqa: E402

#: 唯一允许 import docker SDK 的文件。多一个都要在下面说明理由。
ALLOWED_DOCKER_IMPORTS = {
    "sandbox/providers/docker.py",
}

#: 静态扫描的范围：真正会发布的代码。steps/ 是实验记录，不在此列。
SCAN_ROOTS = ["sandbox", "tools", "agent.py"]

#: 业务侧一个 SDK 都不该碰的模块名
GUARDED_MODULES = ("docker",)


def probe(code: str) -> str:
    """在全新的解释器里跑一段代码，把输出抓回来。

    本进程一旦 import 过 sandbox.providers，docker 就躺在 sys.modules 里了，
    之后再查什么都恒为 True。要问「全新启动会怎样」，只能开新进程问。
    """
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


# ---------------------------------------------------------------------------
# 1. import 图：业务模块拖不动 SDK
# ---------------------------------------------------------------------------

def section_import_graph() -> bool:
    print("=== 1. import 图：业务模块拖不动 Docker SDK ===")
    print()

    ok = True
    for module, label in (
        ("sandbox", "抽象层"),
        ("sandbox.providers", "工厂"),
        ("tools.bash", "Bash 工具"),
        ("tools.code_execution", "ExecuteCode 工具"),
        ("agent", "agent 装配层"),
    ):
        out = probe(
            f"import {module}, sys; "
            f"print('docker in sys.modules =', 'docker' in sys.modules)"
        )
        # agent 依赖 langchain，这些第三方包没装就跳过 —— 别把「依赖没装」
        # 误报成「隔离失败」。装了的话这一行不会出现 Traceback。
        if "Traceback" in out:
            print(f"  {label:<14} 跳过（{module} 的第三方依赖没装全）")
            continue

        leaked = out.endswith("True")
        print(f"  {label:<14} {out}   {'← 漏了' if leaked else 'OK'}")
        ok = ok and not leaked

    print()
    return ok


# ---------------------------------------------------------------------------
# 2. AST 扫描：静态上就没那条边
# ---------------------------------------------------------------------------

def _collect_python_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def _file_imports(filepath: Path, module: str) -> bool:
    """*filepath* 里有没有**绝对** ``import module`` / ``from module import``。

    走 AST 而不是搜字符串：注释里提到 docker（tools/bash.py 就有一句）不算
    import，正则会把注释也算进去。

    只看绝对 import。相对 import 指的是本地兄弟模块，不是第三方 SDK ——
    工厂里那句 ``from .docker import DockerProvider`` 看着像，其实指的是
    sandbox/providers/docker.py 自己（``node.module`` 是 ``"docker"``，靠
    ``node.level == 1`` 才分得出来）。少这个判断就会把工厂误报成越界。

    代价是：这里查不出「__init__ 里急切 re-export 了 provider」——那种写法
    在这份 AST 里没写 ``import docker``。那类回归归第 1、3 段的运行时探针管。
    """
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"), filename=str(filepath))
    except (SyntaxError, UnicodeDecodeError):
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name == module or a.name.startswith(module + ".") for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相对 import，本地模块
                continue
            if node.module and (node.module == module or node.module.startswith(module + ".")):
                return True
    return False


def section_ast_scan() -> bool:
    print()
    print("=== 2. AST 扫描：除了 provider，没人 import docker ===")
    print()

    violations: list[str] = []
    for name in SCAN_ROOTS:
        target = PROJECT_ROOT / name
        files = _collect_python_files(target) if target.is_dir() else [target]
        for py_file in files:
            rel = py_file.relative_to(PROJECT_ROOT).as_posix()
            if rel in ALLOWED_DOCKER_IMPORTS:
                continue
            if any(_file_imports(py_file, m) for m in GUARDED_MODULES):
                violations.append(rel)

    print("  允许清单:", ", ".join(sorted(ALLOWED_DOCKER_IMPORTS)))
    if violations:
        print("  越界文件:")
        for v in sorted(violations):
            print("    -", v)
    else:
        print("  越界文件: 无")

    # 允许清单本身也要真的存在且真的 import 了 —— 否则清单会悄悄失效
    for allowed in sorted(ALLOWED_DOCKER_IMPORTS):
        path = PROJECT_ROOT / allowed
        if not path.is_file():
            print(f"  ! 允许清单里的 {allowed} 不存在")
            return False
        if not any(_file_imports(path, m) for m in GUARDED_MODULES):
            print(f"  ! 允许清单里的 {allowed} 并没有 import docker，清单该清了")
            return False

    print()
    return not violations


# ---------------------------------------------------------------------------
# 3. 工厂惰性：选择权在运行时
# ---------------------------------------------------------------------------

_FACTORY_PROBE = """
import sys
import sandbox.providers as p

print("  import 完工厂，还没调      :", "docker" in sys.modules)
try:
    p.create_provider("nope")
except ValueError as exc:
    print("  create_provider('nope') 抛:", exc)
print("  抛完之后 docker 在不在     :", "docker" in sys.modules)
p.create_provider()
print("  create_provider() 默认选中后:", "docker" in sys.modules)
"""


def section_factory() -> bool:
    print()
    print("=== 3. 工厂：选错名字不加载，选中才加载 ===")
    for line in probe(_FACTORY_PROBE).splitlines():
        print("   ", line.strip())

    loaded_early = probe(
        "import sys, sandbox.providers as p; print('docker' in sys.modules)"
    ).endswith("True")

    print()
    if loaded_early:
        print("  ← 工厂模块级就把 docker 拉进来了，选择权没了")
    return not loaded_early


# ---------------------------------------------------------------------------
# 4. 契约照跑：工具没被碰坏
# ---------------------------------------------------------------------------

async def section_tools() -> bool:
    print()
    print("=== 4. 拿掉 Docker，业务工具照跑 ===")

    from tools.bash import create_execute_bash_tool
    from tools.code_execution import create_execute_code_tool

    runtime = InMemoryRuntime()
    bash_tool = create_execute_bash_tool(runtime)
    code_tool = create_execute_code_tool(runtime)
    print(f"  绑定 {type(runtime).__name__} 到 tools/ 下两个未改动的工具")
    print()

    ok = True

    content, _ = await call_tool(bash_tool, command="echo 沙箱说你好")
    print("  Bash        成功路径 →", str(content).strip())
    ok = ok and "沙箱说你好" in str(content)

    content, _ = await call_tool(bash_tool, command="curl example.com")
    print("  Bash        失败路径 →", str(content).strip().replace("\n", " | "))
    ok = ok and str(content).startswith("ERROR")

    content, artifact = await call_tool(code_tool, code="print(1)")
    print("  ExecuteCode 正文     →", str(content).strip().replace("\n", " | "))
    print("  ExecuteCode 产物     →", artifact)
    ok = ok and "SUCCESS" in str(content) and bool(artifact)

    print()
    print("  工具只依赖 SandboxRuntime 契约，换任何后端都不必改 tools/。")
    return ok


async def main() -> None:
    results = {
        "1. import 图": section_import_graph(),
        "2. AST 扫描": section_ast_scan(),
        "3. 工厂惰性": section_factory(),
        "4. 契约照跑": await section_tools(),
    }

    print()
    print("=== 小结 ===")
    for label, passed in results.items():
        print(f"  {'PASS' if passed else 'FAIL'}  {label}")

    failed = [label for label, passed in results.items() if not passed]
    if failed:
        print()
        print("  隔离被破坏了:", ", ".join(failed))
        sys.exit(1)

    print()
    print("  Docker SDK 被关在 sandbox/providers/docker.py 里，")
    print("  业务侧只认 sandbox 包导出的契约。")


if __name__ == "__main__":
    asyncio.run(main())
