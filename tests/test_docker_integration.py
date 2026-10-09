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
        interface_check = executor.check(
            "from external_head import RHTemplate, VtTickData\n"
            "class Strategy(RHTemplate):\n"
            "    def onTick(self, tick: VtTickData):\n"
            "        super().onTick(tick)\n"
        )
        mocked_engine_test = executor.test(
            "from external_head import RHTemplate, VtTickData\n\n"
            "class Strategy(RHTemplate):\n"
            "    paramMap = {}\n    varMap = {}\n\n"
            "    def __init__(self, pid):\n"
            "        self.npid = pid\n"
            "        self.symbolList = ['y2701']\n"
            "        self.exchangeList = ['DCE']\n"
            "        super().__init__()\n\n"
            "    def onTick(self, tick: VtTickData):\n"
            "        super().onTick(tick)\n"
            "        if tick.vtSymbol == 'y2701' and tick.lastPrice > 8900:\n"
            "            self.output('threshold crossed')\n"
            "            self.putEvent('y2701 above threshold', 'warning')\n",
            "import json\n"
            "from solution import Strategy\n"
            "from external_head import VtTickData\n"
            "from external_baseid import PyToSpiLog, PyToSpiNotification, "
            "PythonGoSubscribeInstrument\n\n"
            "def test_strategy_uses_host_api_with_recording_engine():\n"
            "    strategy = Strategy(9)\n"
            "    strategy.onStart()\n"
            "    tick = VtTickData()\n"
            "    tick.vtSymbol = 'y2701'\n"
            "    tick.lastPrice = 8901\n"
            "    strategy.onTick(tick)\n"
            "    messages = [json.loads(raw) for raw, _ in "
            "strategy.clientEngine.sent_messages]\n"
            "    assert any(m['MsgType'] == PythonGoSubscribeInstrument for m in messages)\n"
            "    assert any(m['MsgType'] == PyToSpiLog and "
            "m.get('Msg') == 'threshold crossed' for m in messages)\n"
            "    assert any(m['MsgType'] == PyToSpiNotification and "
            "m['message'] == 'y2701 above threshold' and "
            "m['level'] == 'warning' for m in messages)\n",
        )
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
        assert interface_check["status"] == "passed"
        assert "external_head" in interface_check["checked_imports"]
        assert mocked_engine_test["status"] == "passed"
        assert mocked_engine_test["passed"] >= 1
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
