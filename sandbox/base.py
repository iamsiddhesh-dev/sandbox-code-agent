from typing import Protocol, Literal
from pydantic import BaseModel


class ExecResult(BaseModel):
    """Result of executing code in a sandbox."""
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    files: dict[str, bytes] = {}  # produced artifacts, path -> content


class SandboxBackend(Protocol):
    """Protocol for swappable sandbox backends (E2B, Docker, etc)."""

    def run(
        self, code: str, lang: Literal["python", "js"], timeout_s: int = 30
    ) -> ExecResult:
        """Execute code in the sandbox.

        Args:
            code: The code to execute.
            lang: Language ("python" or "js").
            timeout_s: Wall-clock timeout in seconds.

        Returns:
            ExecResult with stdout, stderr, exit code, and produced files.
        """
        ...
