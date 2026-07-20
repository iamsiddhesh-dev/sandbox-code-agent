"""E2B backend: runs untrusted code inside a Firecracker microVM."""

from __future__ import annotations

from e2b import CommandExitException, TimeoutException
from e2b_code_interpreter import Sandbox

import config
from sandbox.base import ExecResult

OUTPUT_DIR = "/output"
CODE_PATH = {"python": "/tmp/main.py", "js": "/tmp/main.js"}
INTERPRETER = {"python": "python3", "js": "node"}

# Exit code convention borrowed from GNU coreutils `timeout`, so both backends
# report wall-clock kills identically.
TIMEOUT_EXIT_CODE = 124


class E2BBackend:
    """Executes code in a per-run E2B sandbox and extracts /output artifacts."""

    def __init__(self, api_key: str | None = None, template: str | None = None):
        self.api_key = api_key or config.E2B_API_KEY
        self.template = template
        if not self.api_key:
            raise ValueError("E2B_API_KEY not set in .env")

    def run(
        self, code: str, lang: str = "python", timeout_s: int = 30
    ) -> ExecResult:
        if lang not in INTERPRETER:
            raise ValueError(f"unsupported lang: {lang}")

        sandbox = None
        try:
            sandbox = Sandbox.create(
                template=self.template,
                api_key=self.api_key,
                # The sandbox must not be able to reach anything, and must not
                # inherit a single one of this process's environment variables.
                allow_internet_access=False,
                # allow_internet_access=False was observed to kill DNS but still
                # let TCP connect() succeed, so the egress deny is stated
                # explicitly rather than inferred. See sandbox/SECURITY.md.
                network={"deny_out": ["0.0.0.0/0"]},
                envs={},
                timeout=timeout_s + 30,
            )
            sandbox.files.make_dir(OUTPUT_DIR)
            sandbox.files.write(CODE_PATH[lang], code)

            timed_out = False
            try:
                result = sandbox.commands.run(
                    f"{INTERPRETER[lang]} {CODE_PATH[lang]}",
                    cwd=OUTPUT_DIR,
                    envs={},
                    timeout=timeout_s,
                    request_timeout=timeout_s + 15,
                )
                stdout, stderr, exit_code = result.stdout, result.stderr, result.exit_code
            except CommandExitException as exc:
                stdout, stderr, exit_code = exc.stdout, exc.stderr, exc.exit_code
            except TimeoutException as exc:
                stdout, stderr, exit_code = "", str(exc), TIMEOUT_EXIT_CODE
                timed_out = True

            files = {} if timed_out else self._collect_output(sandbox)

            return ExecResult(
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                timed_out=timed_out,
                files=files,
            )
        finally:
            # Free tier is metered per sandbox-minute, so a leaked sandbox is a
            # leaked bill, not just a leaked process.
            if sandbox is not None:
                try:
                    sandbox.kill()
                except Exception:
                    pass

    def _collect_output(self, sandbox: Sandbox) -> dict[str, bytes]:
        files: dict[str, bytes] = {}
        try:
            entries = sandbox.files.list(OUTPUT_DIR, depth=3)
        except Exception:
            return files

        for entry in entries:
            entry_type = getattr(entry, "type", None)
            if entry_type is not None and "file" not in str(entry_type).lower():
                continue
            try:
                content = sandbox.files.read(entry.path, format="bytes")
            except Exception:
                continue
            files[entry.name] = bytes(content)
        return files
