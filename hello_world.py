#!/usr/bin/env python3
"""Hello-world end-to-end stub: hardcoded code → sandbox → printed result."""

from sandbox.fake import FakeEchoBackend


def main():
    """Run a hello-world example through the sandbox."""
    backend = FakeEchoBackend()

    code = 'print("hello from sandbox")'
    print(f"Running code:\n{code}\n")

    result = backend.run(code, lang="python", timeout_s=30)

    print("=== Execution Result ===")
    print(f"Exit code: {result.exit_code}")
    print(f"Timed out: {result.timed_out}")
    print(f"Stdout:\n{result.stdout}")
    if result.stderr:
        print(f"Stderr:\n{result.stderr}")
    if result.files:
        print(f"Files: {list(result.files.keys())}")


if __name__ == "__main__":
    main()
