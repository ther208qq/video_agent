from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SandboxTransientError(RuntimeError):
    """Transient sandbox transport error.

    Raised when an operation fails due to transient transport issues and cannot be
    safely retried automatically.
    """


class SandboxGoneError(RuntimeError):
    """The sandbox no longer exists (deleted, expired, or in an unrecoverable state).

    Callers should create a fresh sandbox and restore files from backup.
    """

    def __init__(self, sandbox_id: str, message: str = ""):
        self.sandbox_id = sandbox_id
        full_msg = f"Sandbox {sandbox_id} is gone"
        if message:
            full_msg += f": {message}"
        super().__init__(full_msg)


class SandboxFailureKind(str, Enum):
    """What a failed sandbox operation actually means.

    Exists so callers never have to infer "the file isn't there" from "the call
    didn't work". ``UNKNOWN`` is a first-class outcome, not a fallback bucket:
    a status-less transport failure is genuinely undecidable from the exception
    alone and must be confirmed against the runtime rather than guessed at.
    """

    PATH_ABSENT = "path_absent"  # positively identified per-path not-found
    SANDBOX_GONE = "sandbox_gone"  # the sandbox itself is not there
    TRANSIENT = "transient"  # transport-level, may succeed on retry
    UNKNOWN = "unknown"  # undecidable — never treat as absence


class RuntimeState(str, Enum):
    """Possible states of a sandbox runtime."""

    RUNNING = "running"
    STOPPED = "stopped"
    STARTING = "starting"
    STOPPING = "stopping"
    ARCHIVED = "archived"
    ERROR = "error"


@dataclass
class ExecResult:
    """Result of a shell command execution."""

    stdout: str
    stderr: str
    exit_code: int


@dataclass
class Artifact:
    """An artifact produced by code execution (e.g. a chart image)."""

    type: str  # MIME type, e.g. "image/png"
    data: str  # base64-encoded content
    name: str | None = None


@dataclass
class CodeRunResult:
    """Result of a code execution with optional artifacts."""

    stdout: str
    stderr: str
    exit_code: int
    artifacts: list[Artifact] = field(default_factory=list)


class SandboxRuntime(ABC):
    """Primitive operations that vary per sandbox provider."""

    @property
    @abstractmethod
    def id(self) -> str:
        """Unique identifier for this runtime instance."""
        ...

    @property
    @abstractmethod
    def working_dir(self) -> str:
        """Default working directory inside the sandbox."""
        ...

    # -- Lifecycle --

    @abstractmethod
    async def start(self, timeout: int = 120) -> None:
        """Start the runtime."""
        ...

    @abstractmethod
    async def stop(self, timeout: int = 60, *, force: bool = False) -> None:
        """Stop the runtime, forcefully when the provider supports it."""
        ...

    @abstractmethod
    async def delete(self) -> None:
        """Permanently delete the runtime."""
        ...

    @abstractmethod
    async def get_state(self) -> RuntimeState:
        """Return the current lifecycle state."""
        ...

    # -- Execution --

    @abstractmethod
    async def exec(self, command: str, timeout: int = 60) -> ExecResult:
        """Run a shell command and return the result."""
        ...

    @abstractmethod
    async def code_run(
        self,
        code: str,
        env: dict[str, str] | None = None,
        timeout: int = 300,
    ) -> CodeRunResult:
        """Execute code (Python) and return the result with artifacts."""
        ...

    # -- File I/O --

    @abstractmethod
    async def upload_file(self, content: bytes, dest_path: str) -> None:
        """Upload a single file to the sandbox."""
        ...

    @abstractmethod
    async def upload_files(self, files: list[tuple[bytes | str, str]]) -> None:
        """Upload multiple files in one operation.

        Each tuple is (source, destination_path) where source is either
        bytes content or a local file path string.
        """
        ...

    @abstractmethod
    async def download_file(self, path: str) -> bytes:
        """Download a file from the sandbox."""
        ...

    @abstractmethod
    async def list_files(self, directory: str) -> list[dict[str, Any]]:
        """List files in a directory."""
        ...

    # -- Capabilities & metadata --

    @property
    def capabilities(self) -> set[str]:
        """Set of capability strings supported by this runtime."""
        return {"exec", "code_run", "file_io"}

    async def get_metadata(self) -> dict[str, Any]:
        """Return provider-specific metadata about the runtime."""
        return {"id": self.id, "working_dir": self.working_dir}


class SandboxProvider(ABC):
    """Factory that creates and reconnects to sandbox runtime instances."""

    @abstractmethod
    async def create(
        self,
        *,
        env_vars: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> SandboxRuntime:
        """Create a new sandbox runtime."""
        ...

    @abstractmethod
    async def get(self, sandbox_id: str) -> SandboxRuntime:
        """Reconnect to an existing sandbox runtime by ID."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release provider resources (HTTP clients, etc.)."""
        ...

    def is_transient_error(self, exc: Exception) -> bool:
        """Return True if *exc* is a transient error that may be retried. """
        return False

    def classify_error(self, exc: Exception) -> SandboxFailureKind:
        """Classify *exc* into a failure kind for callers to act on.

        ``FileNotFoundError`` is the one absence signal a provider can raise
        without an SDK of its own, and it is checked before
        ``is_transient_error`` because that is a message scan: a path like
        ``/data/connection.log`` would otherwise read as a connection fault.
        """
        if isinstance(exc, FileNotFoundError):
            return SandboxFailureKind.PATH_ABSENT
        if self.is_transient_error(exc):
            return SandboxFailureKind.TRANSIENT
        return SandboxFailureKind.UNKNOWN
