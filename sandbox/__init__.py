"""Sandbox backends for secure code execution."""

import config
from sandbox.base import ExecResult, SandboxBackend


def get_backend(name: str | None = None) -> SandboxBackend:
    """Return the backend named by SANDBOX_BACKEND (or the explicit override)."""
    name = (name or config.SANDBOX_BACKEND).lower()

    if name == "e2b":
        from sandbox.e2b_backend import E2BBackend

        return E2BBackend()
    if name == "docker":
        from sandbox.docker_backend import DockerBackend

        return DockerBackend()
    if name == "fake":
        from sandbox.fake import FakeEchoBackend

        return FakeEchoBackend()

    raise ValueError(f"unknown sandbox backend: {name}")


__all__ = ["ExecResult", "SandboxBackend", "get_backend"]
