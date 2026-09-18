"""沙箱 provider 工厂。

这个模块唯一的职责是「选谁来干活」。要点在于具体 provider 的 import 写在
``create_provider`` 函数体的 if 分支里，而不是模块级：

    import sandbox.providers          → 一个 SDK 都不加载
    create_provider("nope")           → 抛 ValueError，docker 依然没进来
    create_provider("docker")         → 到这一行 docker 才进 sys.modules

换成模块级 ``from .docker import DockerProvider``，上面三行的结果就会变成
「都一样，一 import 就加载」。那样 provider 的选择权就不再由运行时决定，
而是被 import 图静态钉死了 —— v9 里量化的就是这个差别。
"""

from __future__ import annotations

import os
from typing import Any

from ..runtime import SandboxProvider

#: 不指定时用的 provider。
DEFAULT_PROVIDER = "docker"

#: 选择 provider 的环境变量名。目前只有一个候选，但选择权归运行时。
PROVIDER_ENV = "SANDBOX_PROVIDER"


def create_provider(name: str | None = None, **kwargs: Any) -> SandboxProvider:
    """按名字创建 provider，多出来的关键字参数原样交给具体实现。

    Args:
        name: provider 名字；留空则读 ``SANDBOX_PROVIDER``，再退回
            ``DEFAULT_PROVIDER``。名字在这里合法时才 import 对应的模块。
        **kwargs: 具体 provider 的构造参数（镜像、工作目录、资源上限等）。

    Returns:
        一个 ``SandboxProvider`` 实例。

    Raises:
        ValueError: 名字不认识。注意抛错路径上不会加载任何 SDK。
    """
    resolved = name or os.getenv(PROVIDER_ENV) or DEFAULT_PROVIDER

    if resolved == "docker":
        # 惰性：只有真的选中 docker，SDK 才进 sys.modules
        from .docker import DockerProvider

        return DockerProvider(**kwargs)

    raise ValueError(f"Unknown sandbox provider: {resolved!r}")
