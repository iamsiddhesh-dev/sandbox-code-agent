"""Round-trip tests: both backends run code and hand artifacts back out."""

import json

import pytest

import config
from sandbox import get_backend
from sandbox.docker_backend import docker_available

pytestmark = pytest.mark.slow

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

CHART_CODE = """
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.plot([1, 2, 3], [2, 4, 9])
plt.savefig("/output/chart.png")
print(json.dumps({"type": "chart", "data": None, "artifact_path": "/output/chart.png"}))
"""


@pytest.fixture(params=["e2b", "docker"])
def backend(request):
    if request.param == "e2b" and not config.E2B_API_KEY:
        pytest.skip("E2B_API_KEY not set")
    if request.param == "docker" and not docker_available():
        pytest.skip("Docker daemon not reachable")
    return get_backend(request.param)


def test_hello_world_roundtrip(backend):
    result = backend.run('print("hello from the vault")')

    assert result.exit_code == 0
    assert result.timed_out is False
    assert "hello from the vault" in result.stdout


def test_chart_artifact_comes_back_as_bytes(backend):
    result = backend.run(CHART_CODE, timeout_s=60)

    assert result.exit_code == 0, result.stderr
    envelope = json.loads(result.stdout.strip().splitlines()[-1])
    assert envelope["type"] == "chart"
    assert "chart.png" in result.files
    assert result.files["chart.png"].startswith(PNG_MAGIC)


def test_nonzero_exit_is_reported(backend):
    result = backend.run('raise SystemExit(3)')

    assert result.exit_code == 3
    assert result.timed_out is False
