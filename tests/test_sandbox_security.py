"""Boundary tests: the four things the sandbox must never allow.

These run real sandboxes and are slow (and E2B-metered), so they are marked
`slow` and skipped when the backend is unavailable.
"""

import time

import pytest

import config
from sandbox.docker_backend import DockerBackend, docker_available

pytestmark = pytest.mark.slow

TIMEOUT_S = 20
SUCCESS_MARKER = "BOUNDARY_BREACHED"


def _e2b_backend():
    if not config.E2B_API_KEY:
        pytest.skip("E2B_API_KEY not set")
    from sandbox.e2b_backend import E2BBackend

    return E2BBackend()


def _docker_backend():
    if not docker_available():
        pytest.skip("Docker daemon not reachable")
    return DockerBackend()


@pytest.fixture(params=["e2b", "docker"])
def backend(request):
    return {"e2b": _e2b_backend, "docker": _docker_backend}[request.param]()


def test_no_network(backend):
    code = f"""
import urllib.request
try:
    body = urllib.request.urlopen("https://example.com", timeout=5).read()
    print("{SUCCESS_MARKER}", len(body))
except Exception as exc:
    print("blocked:", type(exc).__name__)
    raise SystemExit(1)
"""
    result = backend.run(code, timeout_s=TIMEOUT_S)

    assert SUCCESS_MARKER not in result.stdout
    assert result.exit_code != 0 or result.timed_out


def test_no_raw_socket_response(backend):
    """connect() succeeding is not enough — no bytes may come back over it."""
    code = f"""
import socket
sock = socket.socket()
sock.settimeout(5)
try:
    sock.connect(("1.1.1.1", 80))
    sock.sendall(b"GET / HTTP/1.0\\r\\nHost: one.one.one.one\\r\\n\\r\\n")
    data = sock.recv(256)
    if data:
        print("{SUCCESS_MARKER}", len(data))
    else:
        print("no data returned")
except Exception as exc:
    print("blocked:", type(exc).__name__)
finally:
    sock.close()
"""
    result = backend.run(code, timeout_s=TIMEOUT_S)

    assert SUCCESS_MARKER not in result.stdout


def test_no_filesystem_escape(backend):
    code = f"""
import pathlib

leaked = []
for target in ("/etc/shadow", "../../../../etc/shadow", "../../../../host.txt",
               "/var/run/docker.sock", "C:/Windows/System32/drivers/etc/hosts"):
    try:
        data = pathlib.Path(target).read_bytes()
        if data:
            leaked.append(target)
    except Exception:
        pass

if leaked:
    print("{SUCCESS_MARKER}", leaked)
else:
    print("no host files reachable")
"""
    result = backend.run(code, timeout_s=TIMEOUT_S)

    assert SUCCESS_MARKER not in result.stdout
    assert "root:" not in result.stdout


def test_no_secrets_in_sandbox_env(backend):
    # GPG_KEY ships with the python base image and is not a host secret, so the
    # assertion is about host variables leaking in, not about the name "KEY".
    code = f"""
import os
host_vars = ("GROQ_API_KEY", "E2B_API_KEY", "ANTHROPIC_API_KEY", "AWS_SECRET_ACCESS_KEY",
             "USERPROFILE", "USERNAME", "COMPUTERNAME")
leaked = [k for k in host_vars if os.environ.get(k)]
if leaked:
    print("{SUCCESS_MARKER}", leaked)
else:
    print("env clean")
"""
    result = backend.run(code, timeout_s=TIMEOUT_S)

    assert SUCCESS_MARKER not in result.stdout
    assert "e2b_" not in result.stdout
    assert "gsk_" not in result.stdout


def test_memory_bomb_is_killed(backend):
    code = f"""
chunks = []
for i in range(200):
    chunks.append("x" * (100 * 1024 * 1024))
    print("allocated", i + 1, flush=True)
print("{SUCCESS_MARKER}")
"""
    result = backend.run(code, timeout_s=TIMEOUT_S)

    assert SUCCESS_MARKER not in result.stdout
    assert result.exit_code != 0 or result.timed_out


def test_infinite_loop_is_timed_out(backend):
    started = time.monotonic()
    result = backend.run("while True:\n    pass\n", timeout_s=TIMEOUT_S)
    elapsed = time.monotonic() - started

    assert result.timed_out is True
    # Generous margin because `elapsed` also covers sandbox provisioning, which
    # for E2B is a network round-trip, not part of the kill deadline itself.
    assert elapsed < TIMEOUT_S + 30
