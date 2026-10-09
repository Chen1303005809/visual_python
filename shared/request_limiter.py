"""Bound JSON request bodies before FastAPI parses them."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from shared.limits import MAX_HTTP_BODY_BYTES

ASGIApp = Callable[
    [dict[str, Any], Callable[..., Awaitable[Any]], Callable[..., Awaitable[Any]]],
    Awaitable[None],
]


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_body_bytes: int = MAX_HTTP_BODY_BYTES) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[Any]],
        send: Callable[..., Awaitable[Any]],
    ) -> None:
        path = scope.get("path", "")
        if scope.get("type") != "http" or not (
            path.startswith("/v1/python/") or path.startswith("/internal/")
        ):
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_body_bytes:
                    await self._too_large(send)
                    return
            except ValueError:
                await self._too_large(send)
                return

        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message.get("type") == "http.disconnect":
                return
            body = message.get("body", b"")
            total += len(body)
            if total > self.max_body_bytes:
                await self._too_large(send)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break

        buffered_body = b"".join(chunks)
        delivered = False

        async def replay_receive() -> dict[str, Any]:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": buffered_body, "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)

    async def _too_large(self, send: Callable[..., Awaitable[Any]]) -> None:
        payload = json.dumps({"detail": "Request body exceeds the 1 MiB transport limit"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})
