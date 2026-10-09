from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.runner_client import RunnerUnavailable


class FakeRunnerClient:
    async def check(self, source: str) -> dict[str, object]:
        return {
            "status": "passed",
            "syntax_valid": True,
            "python_version": "3.12.13",
            "diagnostics": [],
            "checked_imports": [],
        }

    async def test(self, source: str, test_code: str) -> dict[str, object]:
        return {
            "status": "passed",
            "passed": 1,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
            "exit_code": 0,
            "duration_ms": 14,
            "stdout": "1 passed in 0.01s",
            "stderr": "",
            "output_truncated": False,
        }


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("API_KEY", "test-api-key")
    monkeypatch.setenv("RUNNER_SHARED_TOKEN", "test-runner-token")
    with TestClient(app) as test_client:
        app.state.runner_client = FakeRunnerClient()
        yield test_client


def test_tools_require_bearer_api_key(client: TestClient) -> None:
    response = client.post("/v1/python/check", json={"source": "answer = 42"})

    assert response.status_code == 401


def test_check_and_test_tool_calls_return_structured_results(client: TestClient) -> None:
    headers = {"Authorization": "Bearer test-api-key"}
    check = client.post("/v1/python/check", json={"source": "answer = 42"}, headers=headers)
    test = client.post(
        "/v1/python/test",
        json={"source": "def answer(): return 42", "test_code": "def test_answer(): assert True"},
        headers=headers,
    )

    assert check.status_code == 200
    assert check.json()["status"] == "passed"
    assert test.status_code == 200
    assert test.json()["status"] == "passed"
    assert test.json()["passed"] == 1


def test_combined_code_and_test_input_limit_is_enforced(client: TestClient) -> None:
    response = client.post(
        "/v1/python/test",
        json={"source": "x" * (256 * 1024), "test_code": "def test_x(): pass"},
        headers={"Authorization": "Bearer test-api-key"},
    )

    assert response.status_code == 422


def test_http_body_limit_is_enforced_before_json_parsing(client: TestClient) -> None:
    response = client.post(
        "/v1/python/check",
        content=b"x" * (1024 * 1024 + 1),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413


def test_runner_unavailable_is_returned_as_503(client: TestClient) -> None:
    class DownRunner(FakeRunnerClient):
        async def check(self, source: str) -> dict[str, object]:
            raise RunnerUnavailable("private implementation detail")

    app.state.runner_client = DownRunner()
    response = client.post(
        "/v1/python/check",
        json={"source": "answer = 42"},
        headers={"Authorization": "Bearer test-api-key"},
    )

    assert response.status_code == 503
    assert "private implementation detail" not in response.text


def test_openapi_is_fixed_to_openapi_30_and_defines_two_agent_tools(client: TestClient) -> None:
    document = client.get("/openapi.json").json()

    assert document["openapi"] == "3.0.3"
    assert document["paths"]["/v1/python/check"]["post"]["operationId"] == "python_static_check"
    assert document["paths"]["/v1/python/test"]["post"]["operationId"] == "python_assertion_test"
    assert document["components"]["securitySchemes"]["BearerAuth"]["scheme"] == "bearer"
