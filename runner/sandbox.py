"""Run static checks and pytest in disposable, resource-limited Docker containers."""

from __future__ import annotations

import io
import json
import logging
import re
import tarfile
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

import docker
from docker.errors import DockerException, ImageNotFound

from runner.config import Settings

logger = logging.getLogger(__name__)
WRAPPER_PREFIX = "__DIFY_PYTHON_RUN_RESULT__"
OUTPUT_LIMIT_BYTES = 64 * 1024


class SandboxUnavailable(RuntimeError):
    """The Docker engine or the configured sandbox image is unavailable."""


class SandboxOverloaded(RuntimeError):
    """All sandbox execution slots are occupied."""


@dataclass(slots=True)
class SandboxExecution:
    wrapper: dict[str, Any]
    duration_ms: int
    resource_limited: bool = False


class SandboxExecutor:
    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client or docker.from_env(timeout=settings.docker_http_timeout_seconds)
        self._slots = threading.BoundedSemaphore(settings.max_concurrent_jobs)

    def ping(self) -> None:
        self._client.ping()
        self._client.images.get(self._settings.sandbox_image)

    def close(self) -> None:
        self._client.close()

    def check(self, source: str) -> dict[str, Any]:
        execution = self._execute(
            files={"solution.py": source},
            command=["python", "/opt/dify_validator/check_code.py", "/work/solution.py"],
            timeout_seconds=self._settings.check_timeout_seconds,
        )
        wrapper = execution.wrapper
        if wrapper.get("timed_out"):
            return {
                "status": "error",
                "syntax_valid": False,
                "python_version": "3.12",
                "diagnostics": [
                    {
                        "source": "checker",
                        "code": "CHECK_TIMEOUT",
                        "message": "Static checks exceeded their time limit",
                        "severity": "error",
                    }
                ],
                "checked_imports": [],
            }
        if wrapper.get("return_code") != 0:
            message = str(wrapper.get("stderr", "")).strip() or "The static checker failed to start"
            return {
                "status": "error",
                "syntax_valid": False,
                "python_version": "3.12",
                "diagnostics": [
                    {
                        "source": "checker",
                        "code": "CHECKER_FAILED",
                        "message": message[:2048],
                        "severity": "error",
                    }
                ],
                "checked_imports": [],
            }

        try:
            result = json.loads(str(wrapper.get("stdout", "")))
        except json.JSONDecodeError:
            if wrapper.get("output_truncated"):
                return {
                    "status": "error",
                    "syntax_valid": False,
                    "python_version": "3.12",
                    "diagnostics": [
                        {
                            "source": "checker",
                            "code": "OUTPUT_TRUNCATED",
                            "message": "Static-check output exceeded the response limit",
                            "severity": "error",
                        }
                    ],
                    "checked_imports": [],
                }
            logger.error("Sandbox checker returned an unreadable result")
            raise SandboxUnavailable("The sandbox checker returned an invalid result") from None
        if not isinstance(result, dict):
            raise SandboxUnavailable("The sandbox checker returned an invalid result")
        if wrapper.get("output_truncated"):
            result.setdefault("diagnostics", []).append(
                {
                    "source": "checker",
                    "code": "OUTPUT_TRUNCATED",
                    "message": "Static-check output exceeded the response limit",
                    "severity": "error",
                }
            )
            result["status"] = "error"
        return result

    def test(self, source: str, test_code: str) -> dict[str, Any]:
        started_at = time.monotonic()
        execution = self._execute(
            files={"solution.py": source, "test_solution.py": test_code},
            command=[
                "python",
                "-m",
                "pytest",
                "-q",
                "--disable-warnings",
                "--maxfail=1",
                "--tb=short",
                "--noconftest",
                "-p",
                "no:cacheprovider",
                "/work/test_solution.py",
            ],
            timeout_seconds=self._settings.job_timeout_seconds,
        )
        wrapper = execution.wrapper
        stdout = str(wrapper.get("stdout", ""))
        stderr = str(wrapper.get("stderr", ""))
        exit_code = wrapper.get("return_code")
        passed, failed, errors, skipped = _parse_pytest_counts(stdout + "\n" + stderr)
        resource_limited = execution.resource_limited or _looks_resource_limited(
            wrapper, stdout + "\n" + stderr
        )

        if wrapper.get("timed_out"):
            result_status = "timed_out"
        elif resource_limited:
            result_status = "resource_limited"
        elif exit_code == 5 or (exit_code == 0 and passed == 0 and failed == 0 and errors == 0):
            result_status = "no_tests"
        elif errors > 0 or exit_code not in (0, 1):
            result_status = "error"
        elif failed > 0 or exit_code == 1:
            result_status = "failed"
        elif exit_code == 0 and passed > 0:
            result_status = "passed"
        else:
            result_status = "error"

        return {
            "status": result_status,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "skipped": skipped,
            "exit_code": exit_code if isinstance(exit_code, int) else None,
            "duration_ms": max(0, int((time.monotonic() - started_at) * 1000)),
            "stdout": stdout,
            "stderr": stderr,
            "output_truncated": bool(wrapper.get("output_truncated")),
        }

    def _execute(
        self,
        *,
        files: dict[str, str],
        command: list[str],
        timeout_seconds: int,
    ) -> SandboxExecution:
        if not self._slots.acquire(blocking=False):
            raise SandboxOverloaded("No sandbox slots are available")

        container = None
        started_at = time.monotonic()
        job_id = uuid.uuid4().hex
        try:
            self._client.images.get(self._settings.sandbox_image)
            container = self._client.containers.create(
                image=self._settings.sandbox_image,
                command=["python", "-c", "import time; time.sleep(3600)"],
                name=f"dify-python-{job_id[:16]}",
                labels={"app": "dify-python-validator", "job_id": job_id},
                working_dir="/work",
                user="10001:10001",
                environment={
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONNOUSERSITE": "1",
                    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                    "MPLBACKEND": "Agg",
                    "MPLCONFIGDIR": "/tmp/mpl",
                    "HOME": "/tmp",
                },
                network_mode="none",
                read_only=True,
                tmpfs={
                    "/work": "rw,nosuid,nodev,noexec,size=16m,mode=755",
                    "/tmp": "rw,nosuid,nodev,noexec,size=16m,mode=1777",
                },
                mem_limit="512m",
                memswap_limit="512m",
                nano_cpus=1_000_000_000,
                pids_limit=64,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                init=True,
                detach=True,
                auto_remove=False,
                tty=False,
                stdin_open=False,
            )
            container.start()
            if not container.put_archive("/work", _make_tar_archive(files)):
                raise SandboxUnavailable("The source could not be copied into the sandbox")

            wrapped_command = [
                "python",
                "/opt/dify_validator/capped_exec.py",
                "--timeout",
                str(timeout_seconds),
                "--output-limit",
                str(OUTPUT_LIMIT_BYTES),
                "--",
                *command,
            ]
            result = container.exec_run(
                wrapped_command,
                demux=True,
                stdout=True,
                stderr=True,
                user="10001:10001",
                workdir="/work",
            )
            raw_stdout, raw_stderr = _split_exec_output(result.output)
            if result.exit_code != 0:
                logger.error(
                    "Sandbox wrapper exited unexpectedly (job_id=%s, exit_code=%s, stderr=%s)",
                    job_id,
                    result.exit_code,
                    raw_stderr.decode("utf-8", errors="replace")[:2048],
                )
                raise SandboxUnavailable("The sandbox process did not return a valid result")
            try:
                wrapper = json.loads(raw_stdout.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                logger.error("Sandbox wrapper returned invalid JSON (job_id=%s)", job_id)
                raise SandboxUnavailable("The sandbox process returned an invalid result") from None
            if not isinstance(wrapper, dict):
                raise SandboxUnavailable("The sandbox process returned an invalid result")
            resource_limited = False
            try:
                container.reload()
                state = (container.attrs or {}).get("State", {})
                resource_limited = bool(state.get("OOMKilled"))
            except DockerException:
                logger.warning("Could not inspect sandbox container state (job_id=%s)", job_id)
            return SandboxExecution(
                wrapper=wrapper,
                duration_ms=max(0, int((time.monotonic() - started_at) * 1000)),
                resource_limited=resource_limited,
            )
        except ImageNotFound as exc:
            logger.exception("Sandbox image is missing: %s", self._settings.sandbox_image)
            raise SandboxUnavailable("The sandbox image has not been built") from exc
        except DockerException as exc:
            if _container_was_oom_killed(container):
                return SandboxExecution(
                    wrapper={
                        "return_code": 137,
                        "timed_out": False,
                        "stdout": "",
                        "stderr": "The sandbox exceeded its memory limit",
                        "output_truncated": False,
                    },
                    duration_ms=max(0, int((time.monotonic() - started_at) * 1000)),
                    resource_limited=True,
                )
            logger.exception("Docker could not execute a sandbox job (job_id=%s)", job_id)
            raise SandboxUnavailable("The Docker engine could not execute the sandbox job") from exc
        finally:
            if container is not None:
                try:
                    container.remove(force=True, v=True)
                except DockerException:
                    logger.exception("Could not remove sandbox container (job_id=%s)", job_id)
            self._slots.release()


def _make_tar_archive(files: dict[str, str]) -> bytes:
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        for name, contents in files.items():
            data = contents.encode("utf-8")
            entry = tarfile.TarInfo(name=name)
            entry.size = len(data)
            entry.mode = 0o444
            entry.uid = 0
            entry.gid = 0
            entry.mtime = 0
            tar.addfile(entry, io.BytesIO(data))
    return archive.getvalue()


def _split_exec_output(output: Any) -> tuple[bytes, bytes]:
    if isinstance(output, tuple):
        return output[0] or b"", output[1] or b""
    return output or b"", b""


def _parse_pytest_counts(output: str) -> tuple[int, int, int, int]:
    counts = {"passed": 0, "failed": 0, "error": 0, "errors": 0, "skipped": 0}
    for count, label in re.findall(
        r"\b(\d+)\s+(passed|failed|errors?|skipped)\b", output, flags=re.IGNORECASE
    ):
        normalized = label.lower()
        if normalized == "errors":
            normalized = "error"
        counts[normalized] += int(count)
    return counts["passed"], counts["failed"], counts["error"], counts["skipped"]


def _looks_resource_limited(wrapper: dict[str, Any], output: str) -> bool:
    if wrapper.get("return_code") in (-9, 137):
        return True
    return bool(
        re.search(
            r"MemoryError|Cannot allocate memory|Resource temporarily unavailable|Errno 11|No space left on device",
            output,
            flags=re.IGNORECASE,
        )
    )


def _container_was_oom_killed(container: Any | None) -> bool:
    if container is None:
        return False
    try:
        container.reload()
        return bool((container.attrs or {}).get("State", {}).get("OOMKilled"))
    except DockerException:
        return False
