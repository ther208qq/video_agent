"""Docker 沙箱 provider：把沙箱跑成本机的容器。

本文件是整个仓库里唯一允许 import docker SDK 的地方（`_chart_capture.py`
是它的私有辅助，也不碰 SDK）。业务工具只认 ``..runtime`` 的契约，够不着这里。
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import shlex
import tarfile
import uuid
from pathlib import Path
from typing import Any

import docker as docker_sdk

from ._chart_capture import build_code_wrapper, extract_artifacts
from ..runtime import (
    CodeRunResult,
    ExecResult,
    RuntimeState,
    SandboxFailureKind,
    SandboxProvider,
    SandboxRuntime,
)

DEFAULT_IMAGE = "video-agent-sandbox:latest"
DEFAULT_WORKING_DIR = "/home/workspace"
_NAME_PREFIX = "video-agent-sandbox-"

# docker 的 container.status 到 RuntimeState 的映射
_STATUS_MAP = {
    "running": RuntimeState.RUNNING,
    "created": RuntimeState.STARTING,
    "restarting": RuntimeState.STARTING,
    "paused": RuntimeState.STOPPED,
    "exited": RuntimeState.STOPPED,
    "removing": RuntimeState.STOPPING,
    "dead": RuntimeState.ERROR,
}


def _b64_py(snippet: str) -> str:
    """把一段 Python 源码 base64 后交给容器里的 python3 执行。

    直接拼字符串会在路径含引号/空格时炸掉，base64 绕开整个转义问题。
    """
    payload = base64.b64encode(snippet.encode("utf-8")).decode("ascii")
    return f"python3 -c \"import base64;exec(base64.b64decode('{payload}'))\""


class DockerRuntime(SandboxRuntime):
    """一个 Docker 容器，靠 exec 驱动。"""

    def __init__(
        self,
        container: Any,
        runtime_id: str,
        working_dir: str = DEFAULT_WORKING_DIR,
    ) -> None:
        self._container = container
        self._id = runtime_id
        self._working_dir = working_dir

    @property
    def id(self) -> str:
        return self._id

    @property
    def working_dir(self) -> str:
        return self._working_dir

    # -- Lifecycle --

    async def start(self, timeout: int = 120) -> None:
        await asyncio.to_thread(self._container.start)

    async def stop(self, timeout: int = 60, *, force: bool = False) -> None:
        await asyncio.to_thread(self._container.stop, timeout=timeout)

    async def delete(self) -> None:
        await asyncio.to_thread(self._container.remove, force=True)

    async def get_state(self) -> RuntimeState:
        await asyncio.to_thread(self._container.reload)
        return _STATUS_MAP.get(self._container.status, RuntimeState.ERROR)

    # -- Execution --

    async def exec(self, command: str, timeout: int = 60) -> ExecResult:
        def _run() -> ExecResult:
            # demux=False：stderr 并进 stdout，stderr 恒为空串。上游 docker.py:190
            # 就是这个行为，Bash 工具的错误分支按它写的，保持一致。
            #
            # 超时交给容器里的 timeout(1) 管：它把命令 fork 进自己的进程组，
            # 到点对整组发 SIGTERM，bash -c 拉起来的子进程跟着一起走；
            # -k 5 是兜底，5 秒后还没退就 SIGKILL。只在容器外面 wait_for
            # 是拦不住容器里的进程的 —— 那正是后台残留的来源。
            # argv 直接传，command 不需要再做 shell 转义。
            result = self._container.exec_run(
                cmd=["timeout", "-k", "5", str(timeout), "bash", "-c", command],
                workdir=self._working_dir,
                demux=False,
            )
            exit_code = result.exit_code if result.exit_code is not None else -1
            # timeout(1) 用 124 报「是我杀掉的」。收敛成调用方认识的形状：
            # stderr="timeout" 是既有约定，Bash 工具靠它把超时和普通失败分开。
            # 代价是命令自己恰好返回 124 时会被当成超时，这个歧义暂时留着。
            if exit_code == 124:
                return ExecResult(stdout="", stderr="timeout", exit_code=-1)
            output = result.output or b""
            return ExecResult(
                stdout=output.decode("utf-8", errors="replace"),
                stderr="",
                exit_code=exit_code,
            )

        try:
            # 外层只是兜底：timeout 自己卡死时（比如挂在不可中断的睡眠里）
            # 还得有人收尾，所以留出 -k 5 的余量。
            return await asyncio.wait_for(
                asyncio.to_thread(_run), timeout=timeout + 10
            )
        except asyncio.TimeoutError:
            return ExecResult(stdout="", stderr="timeout", exit_code=-1)

    async def code_run(
        self,
        code: str,
        env: dict[str, str] | None = None,
        timeout: int = 300,
    ) -> CodeRunResult:
        wrapper = build_code_wrapper(code)
        encoded = base64.b64encode(wrapper.encode("utf-8")).decode("ascii")

        script_name = f"_exec_{uuid.uuid4().hex[:8]}.py"
        script_path = f"{self._working_dir}/{script_name}"

        write = await self.exec(
            _b64_py(
                f"import base64;open({script_path!r},'wb')"
                f".write(base64.b64decode({encoded!r}))"
            ),
            timeout=15,
        )
        if write.exit_code != 0:
            return CodeRunResult(
                stdout="",
                stderr=f"Failed to write script: {write.stdout}",
                exit_code=write.exit_code,
            )

        prefix = ""
        if env:
            prefix = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in env.items()) + " "

        run = await self.exec(f"{prefix}python3 {script_path}", timeout=timeout)
        artifacts, clean_stdout = extract_artifacts(run.stdout)

        return CodeRunResult(
            stdout=clean_stdout,
            stderr=run.stderr,
            exit_code=run.exit_code,
            artifacts=artifacts,
        )

    # -- File I/O --

    async def upload_file(self, content: bytes, dest_path: str) -> None:
        path = Path(dest_path)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name=path.name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        buf.seek(0)
        await asyncio.to_thread(
            self._container.put_archive, str(path.parent), buf.getvalue()
        )

    async def upload_files(self, files: list[tuple[bytes | str, str]]) -> None:
        for source, dest in files:
            content = Path(source).read_bytes() if isinstance(source, str) else source
            await self.upload_file(content, dest)

    async def download_file(self, path: str) -> bytes:
        def _get() -> bytes:
            stream, _stat = self._container.get_archive(path)
            raw = b"".join(stream)
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r") as tar:
                member = tar.getmembers()[0]
                extracted = tar.extractfile(member)
                if extracted is None:
                    raise FileNotFoundError(f"Not a regular file: {path}")
                return extracted.read()

        try:
            return await asyncio.to_thread(_get)
        except docker_sdk.errors.NotFoundError as exc:
            raise FileNotFoundError(f"No such file in sandbox: {path}") from exc

    async def list_files(self, directory: str) -> list[dict[str, Any]]:
        snippet = (
            "import os, json\n"
            f"d = {directory!r}\n"
            "out = []\n"
            "for e in os.scandir(d):\n"
            "    s = e.stat()\n"
            "    out.append({'name': e.name, 'size': s.st_size,\n"
            "                'mtime': int(s.st_mtime), 'is_dir': e.is_dir()})\n"
            "print(json.dumps(out))\n"
        )
        result = await self.exec(_b64_py(snippet), timeout=30)
        if result.exit_code != 0:
            raise FileNotFoundError(f"Cannot list {directory}: {result.stdout.strip()}")
        return json.loads(result.stdout.strip().splitlines()[-1])


class DockerProvider(SandboxProvider):
    """创建/重连 Docker 沙箱。"""

    def __init__(
        self,
        image: str = DEFAULT_IMAGE,
        working_dir: str = DEFAULT_WORKING_DIR,
        host_dir: str | Path | None = None,
        memory_limit: str = "2g",
        cpu_count: float = 2.0,
        network_mode: str = "bridge",
        dockerfile: str | Path | None = None,
    ) -> None:
        self.image = image
        self.working_dir = working_dir
        self.host_dir = Path(host_dir) if host_dir else Path.cwd() / "workspace"
        self.memory_limit = memory_limit
        self.cpu_count = cpu_count
        self.network_mode = network_mode
        # 镜像不在时自动构建用的 Dockerfile
        self.dockerfile = Path(dockerfile) if dockerfile else Path.cwd() / "Dockerfile.sandbox"
        self._client: Any = None

    async def _get_client(self) -> Any:
        if self._client is None:
            self._client = await asyncio.to_thread(docker_sdk.from_env)
        return self._client

    async def _ensure_image(self, client: Any) -> None:
        def _check() -> bool:
            try:
                client.images.get(self.image)
                return True
            except docker_sdk.errors.ImageNotFound:
                return False

        if await asyncio.to_thread(_check):
            return
        if not self.dockerfile.exists():
            raise FileNotFoundError(
                f"Image {self.image!r} not found and no Dockerfile at {self.dockerfile}. "
                f"Build it first: docker build -f Dockerfile.sandbox -t {self.image} ."
            )
        await asyncio.to_thread(
            client.images.build,
            path=str(self.dockerfile.parent),
            dockerfile=self.dockerfile.name,
            tag=self.image,
            rm=True,
        )

    async def create(
        self,
        *,
        env_vars: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> DockerRuntime:
        client = await self._get_client()
        await self._ensure_image(client)

        runtime_id = uuid.uuid4().hex[:12]
        self.host_dir.mkdir(parents=True, exist_ok=True)

        container = await asyncio.to_thread(
            client.containers.run,
            self.image,
            command=["sleep", "infinity"],
            name=f"{_NAME_PREFIX}{runtime_id}",
            detach=True,
            # tini 当 PID 1，收僵尸进程
            init=True,
            # 生命周期自己管，不让 docker 自动删
            auto_remove=False,
            mem_limit=self.memory_limit,
            nano_cpus=int(self.cpu_count * 1e9),
            network_mode=self.network_mode,
            working_dir=self.working_dir,
            # 视频文件靠挂载，不靠拷贝
            volumes={str(self.host_dir): {"bind": self.working_dir, "mode": "rw"}},
            environment=env_vars or {},
        )
        return DockerRuntime(container, runtime_id, self.working_dir)

    async def get(self, sandbox_id: str) -> DockerRuntime:
        client = await self._get_client()

        def _get() -> Any:
            try:
                return client.containers.get(f"{_NAME_PREFIX}{sandbox_id}")
            except docker_sdk.errors.NotFoundError as exc:
                raise FileNotFoundError(f"No such sandbox: {sandbox_id}") from exc

        container = await asyncio.to_thread(_get)
        return DockerRuntime(container, sandbox_id, self.working_dir)

    async def close(self) -> None:
        if self._client is not None:
            await asyncio.to_thread(self._client.close)
            self._client = None

    def is_transient_error(self, exc: Exception) -> bool:
        if isinstance(exc, (docker_sdk.errors.APIError,)):
            status = getattr(exc, "response", None)
            code = getattr(status, "status_code", None)
            return code is not None and code >= 500
        return isinstance(exc, (ConnectionError, TimeoutError))

    def classify_error(self, exc: Exception) -> SandboxFailureKind:
        if isinstance(exc, docker_sdk.errors.NotFoundError):
            return SandboxFailureKind.SANDBOX_GONE
        return super().classify_error(exc)
