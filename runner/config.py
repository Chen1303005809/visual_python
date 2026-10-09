"""Configuration for the private sandbox runner."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(slots=True)
class Settings:
    shared_token: str | None
    sandbox_image: str
    job_timeout_seconds: int
    check_timeout_seconds: int
    docker_http_timeout_seconds: int
    max_concurrent_jobs: int

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            shared_token=os.getenv("RUNNER_SHARED_TOKEN"),
            sandbox_image=os.getenv("SANDBOX_IMAGE", "python-code-sandbox:3.12"),
            job_timeout_seconds=_int_env("JOB_TIMEOUT_SECONDS", 30, minimum=1, maximum=120),
            check_timeout_seconds=_int_env("CHECK_TIMEOUT_SECONDS", 10, minimum=1, maximum=30),
            docker_http_timeout_seconds=_int_env(
                "DOCKER_HTTP_TIMEOUT_SECONDS", 45, minimum=5, maximum=180
            ),
            max_concurrent_jobs=_int_env("MAX_CONCURRENT_JOBS", 2, minimum=1, maximum=16),
        )
