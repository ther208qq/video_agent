"""从沙箱读写文件。

工具输出超阈值会被驱逐成文件，模型得有个读回来的口子——否则拿着路径没工具可用。
"""

import logging

from langchain_core.tools import BaseTool, tool

from sandbox import SandboxRuntime

logger = logging.getLogger(__name__)

#: 单次返回的字符上限，保证读一次不会自己把窗口撑爆。
MAX_READ_CHARS = 160_000

DEFAULT_LIMIT = 2000


def create_read_tool(sandbox: SandboxRuntime) -> BaseTool:
    """创建绑定到 ``sandbox`` 的 Read 工具。"""

    # 事后翻日志用：少了这两个字段，分不出读的是哪个 runtime
    where = {"sandbox_id": sandbox.id, "runtime": type(sandbox).__name__}

    @tool("Read")
    async def Read(
        file_path: str,
        offset: int = 0,
        limit: int = DEFAULT_LIMIT,
    ) -> str:
        """Read a file from the sandbox.

        Results are numbered like ``cat -n``. Large tool results are evicted to
        files, so you often need this to read them back — pass the path you were
        given. Read a slice at a time: use ``offset`` (0-indexed line) and
        ``limit``. Output is capped at ~160k characters; when the cap fires the
        last line tells you the offset to continue from.

        Args:
            file_path: Path to the file, relative to the working directory or absolute.
            offset: Line offset to start from (0-indexed). Default 0.
            limit: Maximum number of lines to return. Default 2000.
        """
        try:
            logger.debug("Reading file", extra={**where, "path": file_path})

            raw = await sandbox.download_file(file_path)

            lines = raw.decode("utf-8", errors="replace").splitlines()
            window = lines[offset : offset + limit]
            text = "\n".join(
                f"{i}\t{line}" for i, line in enumerate(window, start=offset + 1)
            )

            if len(text) > MAX_READ_CHARS:
                # 退到最后一个完整行，再从它的行号算出续读位置
                text = text[:MAX_READ_CHARS].rsplit("\n", 1)[0]
                last = text.rsplit("\n", 1)[-1].split("\t", 1)[0].strip()
                text += f"\n[truncated. Continue with offset={last}.]"

            return text or "(empty file)"

        except FileNotFoundError:
            logger.warning("File not found", extra={**where, "path": file_path})
            return f"ERROR: File not found: {file_path}"
        except Exception as e:
            logger.exception("Failed to read file", extra={**where, "path": file_path})
            return f"ERROR: {e!s}"

    return Read


def create_write_tool(sandbox: SandboxRuntime) -> BaseTool:
    """创建绑定到 ``sandbox`` 的 Write 工具。"""

    where = {"sandbox_id": sandbox.id, "runtime": type(sandbox).__name__}

    @tool("Write")
    async def Write(file_path: str, content: str) -> str:
        """Write a file to the sandbox. Overwrites an existing file.

        Args:
            file_path: Path to the file, relative to the working directory or
                absolute. Missing parent directories are created.
            content: The complete file contents. There is no append mode — to add
                to an existing file, Read it and write back the full text.

        Returns:
            How many bytes landed where, or an ERROR message.
        """
        try:
            logger.debug("Writing file", extra={**where, "path": file_path})

            data = content.encode("utf-8")
            await sandbox.upload_file(data, file_path)
            # 回给模型的是展开后的路径：它后面拿这个路径去 Bash 里跑，写相对路径
            # 时得知道实际落在哪
            return f"Wrote {len(data)} bytes to {sandbox.resolve_path(file_path)}"

        except Exception as e:
            logger.exception("Failed to write file", extra={**where, "path": file_path})
            return f"ERROR: {e!s}"

    return Write
