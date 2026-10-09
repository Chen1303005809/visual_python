"""A recording-only stand-in for the proprietary trading host PyEngine module."""

from __future__ import annotations

from typing import Any


class PyEngine:
    """Record host messages for sandbox tests without connecting to a trading client."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.sent_messages: list[tuple[Any, Any]] = []

    def sendMsg(self, message: Any, extra: Any = "") -> None:
        self.sent_messages.append((message, extra))


class SubInstrument:
    """Compatibility stand-in for the optional subscription helper in examples."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.arguments = _kwargs

    def subIns(self) -> None:
        return None
