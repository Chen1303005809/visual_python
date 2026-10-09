"""HTTP adapter for the private sandbox-runner process."""

from __future__ import annotations

import httpx

from app.config import Settings


class RunnerUnavailable(RuntimeError):
    """The private execution process could not complete a request."""


class RunnerOverloaded(RuntimeError):
    """The private execution process has reached its concurrency limit."""


class RunnerClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def check(self, source: str) -> dict[str, object]:
        return await self._post("/internal/check", {"source": source})

    async def test(self, source: str, test_code: str) -> dict[str, object]:
        return await self._post(
            "/internal/test", {"source": source, "test_code": test_code}
        )

    async def _post(self, path: str, payload: dict[str, str]) -> dict[str, object]:
        token = self._settings.runner_shared_token
        if not token:
            raise RunnerUnavailable("The internal runner credential is not configured")

        try:
            async with httpx.AsyncClient(
                timeout=self._settings.runner_timeout_seconds,
                trust_env=False,
            ) as client:
                response = await client.post(
                    f"{self._settings.runner_url}{path}",
                    json=payload,
                    headers={"X-Runner-Token": token},
                )
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise RunnerUnavailable("The sandbox runner could not be reached") from exc

        if response.status_code == 429:
            raise RunnerOverloaded("The sandbox runner is at its concurrency limit")
        if response.status_code >= 500:
            raise RunnerUnavailable("The sandbox runner could not complete the request")
        if response.status_code >= 400:
            raise RunnerUnavailable("The sandbox runner rejected the request")

        try:
            result = response.json()
        except ValueError as exc:
            raise RunnerUnavailable("The sandbox runner returned an invalid response") from exc
        if not isinstance(result, dict):
            raise RunnerUnavailable("The sandbox runner returned an invalid response")
        return result
