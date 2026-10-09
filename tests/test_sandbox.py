from __future__ import annotations

import io
import json
import tarfile
from types import SimpleNamespace

import pytest

from runner.config import Settings
from runner.sandbox import SandboxExecutor, SandboxOverloaded, _make_tar_archive


class FakeContainer:
    def __init__(self, wrapped_result: dict[str, object]) -> None:
        self.wrapped_result = wrapped_result
        self.removed = False
        self.started = False
        self.archive: bytes | None = None
        self.exec_command: list[str] | None = None
        self.attrs = {"State": {"OOMKilled": False}}

    def start(self) -> None:
        self.started = True

    def put_archive(self, path: str, data: bytes) -> bool:
        assert path == "/work"
        self.archive = data
        return True

    def exec_run(self, command, **kwargs):
        self.exec_command = command
        return SimpleNamespace(
            exit_code=0,
            output=(json.dumps(self.wrapped_result).encode("utf-8"), b""),
        )

    def remove(self, **kwargs) -> None:
        self.removed = True

    def reload(self) -> None:
        return None


class FakeImages:
    def get(self, image: str) -> str:
        return image


class FakeContainers:
    def __init__(self, container: FakeContainer) -> None:
        self.container = container
        self.create_kwargs: dict[str, object] = {}

    def create(self, **kwargs) -> FakeContainer:
        self.create_kwargs = kwargs
        return self.container


class FakeDockerClient:
    def __init__(self, container: FakeContainer) -> None:
        self.images = FakeImages()
        self.containers = FakeContainers(container)


def settings() -> Settings:
    return Settings(
        shared_token="runner-secret",
        sandbox_image="sandbox:test",
        job_timeout_seconds=30,
        check_timeout_seconds=10,
        docker_http_timeout_seconds=45,
        max_concurrent_jobs=1,
    )


def test_sandbox_has_network_and_resource_restrictions_and_is_removed() -> None:
    wrapped = {
        "return_code": 0,
        "timed_out": False,
        "stdout": json.dumps(
            {
                "status": "passed",
                "syntax_valid": True,
                "python_version": "3.12.13",
                "diagnostics": [],
                "checked_imports": [],
            }
        ),
        "stderr": "",
        "output_truncated": False,
    }
    container = FakeContainer(wrapped)
    client = FakeDockerClient(container)
    executor = SandboxExecutor(settings(), client=client)

    result = executor.check("answer = 42\n")

    options = client.containers.create_kwargs
    assert options["network_mode"] == "none"
    assert options["read_only"] is True
    assert options["user"] == "10001:10001"
    assert options["mem_limit"] == "512m"
    assert options["memswap_limit"] == "512m"
    assert options["pids_limit"] == 64
    assert options["cap_drop"] == ["ALL"]
    assert options["nano_cpus"] == 1_000_000_000
    assert result["status"] == "passed"
    assert container.removed is True
    assert container.archive is not None


def test_test_result_distinguishes_pass_failure_timeout_and_no_tests() -> None:
    cases = [
        ({"return_code": 0, "timed_out": False, "stdout": "2 passed in 0.01s", "stderr": ""}, "passed"),
        ({"return_code": 1, "timed_out": False, "stdout": "1 failed in 0.01s", "stderr": ""}, "failed"),
        ({"return_code": 5, "timed_out": False, "stdout": "no tests ran", "stderr": ""}, "no_tests"),
        ({"return_code": -9, "timed_out": True, "stdout": "", "stderr": ""}, "timed_out"),
        ({"return_code": -9, "timed_out": False, "stdout": "", "stderr": ""}, "resource_limited"),
    ]
    for wrapper, expected_status in cases:
        wrapped = {"output_truncated": False, **wrapper}
        container = FakeContainer(wrapped)
        executor = SandboxExecutor(settings(), client=FakeDockerClient(container))
        result = executor.test("def answer(): return 42\n", "def test_answer(): assert True\n")
        assert result["status"] == expected_status
        assert container.removed is True


def test_runner_refuses_jobs_when_all_slots_are_busy() -> None:
    container = FakeContainer({})
    executor = SandboxExecutor(settings(), client=FakeDockerClient(container))
    assert executor._slots.acquire(blocking=False)
    try:
        with pytest.raises(SandboxOverloaded):
            executor.check("answer = 42\n")
    finally:
        executor._slots.release()


def test_archive_contains_only_fixed_safe_file_names() -> None:
    archive_data = _make_tar_archive({"solution.py": "x = 1\n", "test_solution.py": "def test_x(): assert True\n"})
    with tarfile.open(fileobj=io.BytesIO(archive_data), mode="r:") as archive:
        assert archive.getnames() == ["solution.py", "test_solution.py"]
        assert archive.getmember("solution.py").mode == 0o444
