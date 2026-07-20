"""Fake sandbox backend for testing without E2B/Docker."""

from sandbox.base import ExecResult, SandboxBackend


class FakeEchoBackend:
    """A fake backend that echoes input for testing."""

    def run(self, code: str, lang: str = "python", timeout_s: int = 30) -> ExecResult:
        """Echo the code back as stdout."""
        return ExecResult(
            stdout=f"[Fake echo]\n{code}\n[End fake output]",
            stderr="",
            exit_code=0,
            timed_out=False,
            files={},
        )
