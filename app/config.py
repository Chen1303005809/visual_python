"""Configuration for the public API process."""

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
    api_key: str | None
    runner_url: str
    runner_shared_token: str | None
    runner_timeout_seconds: int

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            api_key=os.getenv("API_KEY"),
            runner_url=os.getenv("RUNNER_URL", "http://sandbox-runner:9000").rstrip("/"),
            runner_shared_token=os.getenv("RUNNER_SHARED_TOKEN"),
            runner_timeout_seconds=_int_env(
                "RUNNER_HTTP_TIMEOUT_SECONDS", 50, minimum=5, maximum=120
            ),
        )
