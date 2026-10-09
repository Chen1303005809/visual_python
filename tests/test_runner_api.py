from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from runner import main as runner_main


class FakeExecutor:
    def ping(self) -> None:
        return None

    def close(self) -> None:
        return None

    def check(self, source: str) -> dict[str, object]:
        return {
            "status": "passed",
            "syntax_valid": True,
            "python_version": "3.12.13",
            "diagnostics": [],
            "checked_imports": [],
        }

    def test(self, source: str, test_code: str) -> dict[str, object]:
        return {
            "status": "passed",
            "passed": 1,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
            "exit_code": 0,
            "duration_ms": 10,
            "stdout": "1 passed",
            "stderr": "",
            "output_truncated": False,
        }


@pytest.fixture
def runner_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RUNNER_SHARED_TOKEN", "runner-secret")
    monkeypatch.setattr(runner_main, "SandboxExecutor", lambda settings: FakeExecutor())
    with TestClient(runner_main.app) as test_client:
        yield test_client


def test_internal_runner_requires_private_token(runner_client: TestClient) -> None:
    response = runner_client.post("/internal/check", json={"source": "answer = 42"})

    assert response.status_code == 401


def test_internal_runner_accepts_authenticated_tool_calls(runner_client: TestClient) -> None:
    headers = {"X-Runner-Token": "runner-secret"}
    check = runner_client.post(
        "/internal/check", json={"source": "answer = 42"}, headers=headers
    )
    test = runner_client.post(
        "/internal/test",
        json={"source": "answer = 42", "test_code": "def test_answer(): assert True"},
        headers=headers,
    )

    assert check.status_code == 200
    assert check.json()["status"] == "passed"
    assert test.status_code == 200
    assert test.json()["passed"] == 1
