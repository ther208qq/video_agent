import logging
from typing import Any

from langchain_core.tools import BaseTool, tool

from sandbox import SandboxRuntime

logger = logging.getLogger(__name__)


def create_execute_bash_tool(sandbox: SandboxRuntime) -> BaseTool:
    """创建绑定到 ``sandbox`` 的 Bash 工具。"""

    # 事后翻日志用：少了这两个字段，分不出命令落到了哪个 runtime
    where = {"sandbox_id": sandbox.id, "runtime": type(sandbox).__name__}

    @tool("Bash", response_format="content_and_artifact")
    async def Bash(
        command: str,
        description: str | None = None,
        timeout: int | None = 120000,
    ) -> tuple[str, dict[str, Any]]:
        """Execute bash commands in a persistent shell session.

        Use for: system commands, directory operations, and running Python
        scripts written to files.

        Args:
            command: The bash command to execute. Quote paths containing spaces.
            description: Brief description (5-10 words, active voice)
            timeout: Milliseconds (default: 120000, max: 600000)

        Returns:
            Combined stdout and stderr, or an ERROR message.
        """
        try:
            logger.debug(
                "Executing bash command", extra={**where, "command": command[:100]}
            )

            # 沙箱接口收秒，工具契约给的是毫秒
            timeout_seconds = int(timeout / 1000) if timeout else 120

            result = await sandbox.exec(command, timeout=timeout_seconds)

            if result.exit_code == 0:
                output = result.stdout
                if result.stderr:
                    output += f"\n{result.stderr}" if output else result.stderr
                return (output or "Command completed successfully"), {}

            # docker 路径把 stderr 并进了 stdout，所以错误输出从两者里挑非空的
            error_output = result.stderr or result.stdout or "Command execution failed (no output)"
            logger.warning(
                "Bash command failed",
                extra={**where, "command": command[:50], "exit_code": result.exit_code},
            )
            return (
                f"ERROR: Command failed (exit code {result.exit_code})\n{error_output}",
                {},
            )

        except Exception as e:
            logger.exception("Failed to execute bash command", extra=where)
            return f"ERROR: {e!s}", {}

    return Bash
