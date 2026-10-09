from __future__ import annotations

import os
from dataclasses import replace

import docker
import pytest

from runner.config import Settings
from runner.sandbox import SandboxExecutor

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DOCKER_INTEGRATION") != "1",
    reason="set RUN_DOCKER_INTEGRATION=1 to run real Docker sandbox checks",
)


def test_real_sandbox_checks_and_executes_with_network_disabled() -> None:
    settings = Settings(
        shared_token="integration-only",
        sandbox_image="python-code-sandbox:3.12",
        job_timeout_seconds=30,
        check_timeout_seconds=10,
        docker_http_timeout_seconds=45,
        max_concurrent_jobs=1,
    )
    executor = SandboxExecutor(settings)
    timeout_executor = SandboxExecutor(replace(settings, job_timeout_seconds=1))
    executor.ping()

    try:
        check = executor.check("raise RuntimeError('static checks must not execute source')\n")
        syntax_error = executor.check("def broken(:\n    pass\n")
        lint_error = executor.check("answer = undefined_name\n")
        missing_import = executor.check("import definitely_missing_validator_package\n")
        passed = executor.test(
            "def add(left, right): return left + right\n",
            "from solution import add\n\ndef test_add(): assert add(2, 3) == 5\n",
        )
        failed = executor.test(
            "def answer(): return 42\n",
            "from solution import answer\n\ndef test_answer(): assert answer() == 0\n",
        )
        no_tests = executor.test("answer = 42\n", "value = 1\n")
        timeout = timeout_executor.test(
            "answer = 42\n", "def test_hangs():\n    while True: pass\n"
        )
        resource_limited = executor.test(
            "answer = 42\n",
            "def test_exceeds_memory_limit():\n"
            "    bytearray(768 * 1024 * 1024)\n",
        )
        network_blocked = executor.test(
            "import socket\n",
            "import socket\n\ndef test_network_is_blocked():\n"
            "    client = socket.socket()\n    client.settimeout(1)\n"
            "    try:\n        client.connect(('1.1.1.1', 80))\n"
            "    except OSError:\n        return\n"
            "    raise AssertionError('sandbox unexpectedly reached the network')\n",
        )

        assert check["syntax_valid"] is True
        assert syntax_error["status"] == "failed"
        assert any(item["source"] == "syntax" for item in syntax_error["diagnostics"])
        assert lint_error["status"] == "failed"
        assert any(item["source"] == "ruff" for item in lint_error["diagnostics"])
        assert missing_import["status"] == "failed"
        assert any(item["source"] == "imports" for item in missing_import["diagnostics"])
        assert passed["status"] == "passed"
        assert failed["status"] == "failed"
        assert no_tests["status"] == "no_tests"
        assert timeout["status"] == "timed_out"
        assert resource_limited["status"] == "resource_limited"
        assert network_blocked["status"] == "passed"
    finally:
        executor.close()
        timeout_executor.close()


def test_docker_engine_is_available_for_integration_suite() -> None:
    client = docker.from_env(timeout=5)
    try:
        assert client.ping()
        client.images.get("python-code-sandbox:3.12")
    finally:
        client.close()
