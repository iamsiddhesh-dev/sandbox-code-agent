"""Docker backend: runs untrusted code inside a hardened, network-less container."""

from __future__ import annotations

import base64
import io
import os
import subprocess
import tarfile
import uuid
from pathlib import Path

from sandbox.base import ExecResult

IMAGE_TAG = "sandbox-code-agent:latest"
DOCKERFILE = Path(__file__).with_name("Dockerfile.sandbox")

CODE_PATH = {"python": "/tmp/main.py", "js": "/tmp/main.js"}
INTERPRETER = {"python": "python3", "js": "node"}

TIMEOUT_EXIT_CODE = 124
STARTUP_GRACE_S = 15

HARDENING_FLAGS = [
    "--network=none",
    "--read-only",
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges",
    "--pids-limit=64",
    "--memory=512m",
    "--memory-swap=512m",
    "--cpus=1",
    "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777",
    "--tmpfs=/output:rw,noexec,nosuid,nodev,size=64m,mode=1777",
    "--user=runner",
    "--workdir=/output",
]


class DockerUnavailable(RuntimeError):
    """Raised when the Docker daemon cannot be reached."""


def docker_available() -> bool:
    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


class DockerBackend:
    """Executes code in a throwaway hardened container and extracts /output."""

    def __init__(self, image: str = IMAGE_TAG, auto_build: bool = True):
        self.image = image
        self.auto_build = auto_build

    def ensure_image(self) -> None:
        if not docker_available():
            raise DockerUnavailable("Docker daemon is not reachable")
        probe = subprocess.run(
            ["docker", "image", "inspect", self.image],
            capture_output=True,
        )
        if probe.returncode == 0:
            return
        if not self.auto_build:
            raise DockerUnavailable(f"image {self.image} not built")
        build = subprocess.run(
            [
                "docker", "build",
                "-f", str(DOCKERFILE),
                "-t", self.image,
                str(DOCKERFILE.parent),
            ],
            capture_output=True,
            timeout=900,
        )
        if build.returncode != 0:
            detail = build.stderr.decode(errors="replace")[-2000:]
            raise DockerUnavailable(f"image build failed:\n{detail}")

    def run(
        self, code: str, lang: str = "python", timeout_s: int = 30
    ) -> ExecResult:
        if lang not in INTERPRETER:
            raise ValueError(f"unsupported lang: {lang}")

        self.ensure_image()

        # The nonce makes the framing markers unforgeable by the code under test,
        # which shares the same stdout stream.
        nonce = uuid.uuid4().hex
        container = f"sca-{nonce[:12]}"
        script = self._runner_script(lang, timeout_s, nonce)

        cmd = [
            "docker", "run", "--rm", "-i",
            f"--name={container}",
            *HARDENING_FLAGS,
            self.image,
            "sh", "-c", script,
        ]

        timed_out = False
        try:
            proc = subprocess.run(
                cmd,
                input=code.encode(),
                capture_output=True,
                timeout=timeout_s + STARTUP_GRACE_S,
                env={**os.environ, "DOCKER_CLI_HINTS": "false"},
            )
            raw_stdout = proc.stdout.decode(errors="replace")
            stderr = proc.stderr.decode(errors="replace")
            stdout, files, exit_code = self._unframe(raw_stdout, nonce, proc.returncode)
        except subprocess.TimeoutExpired as exc:
            # The host-side timeout is the belt to the in-container `timeout`
            # braces: it only fires if the container itself stopped responding.
            self._force_remove(container)
            stdout = (exc.stdout or b"").decode(errors="replace")
            stderr = (exc.stderr or b"").decode(errors="replace")
            stdout, files, _ = self._unframe(stdout, nonce, TIMEOUT_EXIT_CODE)
            exit_code = TIMEOUT_EXIT_CODE
            timed_out = True

        if exit_code == TIMEOUT_EXIT_CODE:
            timed_out = True

        return ExecResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            timed_out=timed_out,
            files=files,
        )

    def _runner_script(self, lang: str, timeout_s: int, nonce: str) -> str:
        code_path = CODE_PATH[lang]
        interpreter = INTERPRETER[lang]
        return (
            f"cat > {code_path}; "
            f"timeout -k 2 {timeout_s} {interpreter} {code_path}; "
            "rc=$?; "
            f"printf '\\n{nonce}-FILES\\n'; "
            "tar -cz -C /output . 2>/dev/null | base64 -w0; "
            f"printf '\\n{nonce}-EXIT %s\\n' \"$rc\""
        )

    def _unframe(
        self, raw: str, nonce: str, fallback_exit: int
    ) -> tuple[str, dict[str, bytes], int]:
        files_marker = f"\n{nonce}-FILES\n"
        exit_marker = f"\n{nonce}-EXIT "

        if files_marker not in raw:
            return raw, {}, fallback_exit

        stdout, _, tail = raw.partition(files_marker)
        payload, _, exit_tail = tail.partition(exit_marker)

        exit_code = fallback_exit
        if exit_tail:
            try:
                exit_code = int(exit_tail.split("\n", 1)[0].strip())
            except ValueError:
                pass

        return stdout, self._untar(payload.strip()), exit_code

    def _untar(self, payload: str) -> dict[str, bytes]:
        if not payload:
            return {}
        files: dict[str, bytes] = {}
        try:
            blob = base64.b64decode(payload)
            with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
                for member in tar.getmembers():
                    if not member.isfile():
                        continue
                    handle = tar.extractfile(member)
                    if handle is None:
                        continue
                    files[Path(member.name).name] = handle.read()
        except Exception:
            return {}
        return files

    def _force_remove(self, container: str) -> None:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
