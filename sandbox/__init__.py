"""沙箱抽象层：只有契约，没有 SDK。

业务工具写 ``from sandbox import SandboxRuntime``，拿到的是纯接口。这里
一行 SDK 都不 import —— 具体实现（Docker 等）挂在 ``sandbox.providers`` 下，
由 ``create_provider()`` 在运行时按名字挑，SDK 到那一刻才进 ``sys.modules``。

这条边界的意义：换一个后端（Daytona、E2B）不需要动 ``tools/`` 一行代码，
也不会因为在没装 Docker SDK 的机器上 import 业务工具就当场 ImportError。
"""

from .runtime import (
    Artifact,
    CodeRunResult,
    ExecResult,
    RuntimeState,
    SandboxFailureKind,
    SandboxGoneError,
    SandboxProvider,
    SandboxRuntime,
    SandboxTransientError,
)

__all__ = [
    "Artifact",
    "CodeRunResult",
    "ExecResult",
    "RuntimeState",
    "SandboxFailureKind",
    "SandboxGoneError",
    "SandboxProvider",
    "SandboxRuntime",
    "SandboxTransientError",
]
