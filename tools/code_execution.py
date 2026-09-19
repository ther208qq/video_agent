"""在沙箱里跑 Python 代码。

搬自 LangAlpha 的 src/ptc_agent/agent/tools/code_execution.py，改动：
- 删掉 memory 路径拦截（同 bash.py，没有那两层）。
- 去掉 mcp_registry 参数和 docstring 里的 MCP import 说明 —— video_agent 没有 MCP。
"""

import logging
from typing import Any

from langchain_core.tools import BaseTool, tool

from sandbox import SandboxRuntime

logger = logging.getLogger(__name__)


def create_execute_code_tool(sandbox: SandboxRuntime) -> BaseTool:
    """创建绑定到 ``sandbox`` 的 ExecuteCode 工具。"""

    # 事后翻日志用：少了这两个字段，分不出代码落到了哪个 runtime
    where = {"sandbox_id": sandbox.id, "runtime": type(sandbox).__name__}

    @tool("ExecuteCode", response_format="content_and_artifact")
    async def execute_code(
        code: str,
        description: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Execute Python code directly.

        Use for: disposable one-shots — quick calls, small transforms, sanity checks.
        Do not use for iterative or reusable code - write to a file and run via Bash instead.

        Args:
            code: Python code to execute. Print a summary to stdout. Use RELATIVE
                paths (work/<task>/, data/), never a leading slash.
            description: Brief description (5-10 words, active voice)

        Returns:
            SUCCESS with stdout, or ERROR with stderr.
        """
        try:
            logger.info(
                "Executing code in sandbox",
                extra={**where, "code_length": len(code)},
            )

            result = await sandbox.code_run(code)

            # 图表产物走 artifact 通道，不占模型上下文
            artifact = {
                "artifacts": [
                    {"type": a.type, "name": a.name, "data": a.data}
                    for a in result.artifacts
                ]
            }

            if result.exit_code == 0:
                parts = ["SUCCESS"]
                if result.stdout:
                    parts.append(result.stdout)
                return "\n".join(parts), artifact

            # Python 的 traceback 有时走 stdout，两个都看
            error_output = result.stderr or result.stdout
            logger.warning(
                "Code execution failed",
                extra={**where, "exit_code": result.exit_code},
            )
            return f"ERROR\n{error_output}", artifact

        except Exception as e:
            logger.exception("Code execution exception", extra=where)
            return f"ERROR: {e!s}", {}

    return execute_code
