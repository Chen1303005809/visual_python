"""Run a fixed child command while draining and bounding its output."""

from __future__ import annotations

import argparse
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from collections.abc import Sequence


def execute(
    command: Sequence[str],
    timeout: float,
    output_limit: int,
    *,
    cwd: str = "/work",
) -> dict[str, object]:
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "MPLBACKEND": "Agg",
            "MPLCONFIGDIR": "/tmp/mpl",
            "HOME": "/tmp",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        close_fds=True,
    )
    assert process.stdout is not None
    assert process.stderr is not None

    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    per_stream_limit = max(1, output_limit // 2)
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = {"stdout": False, "stderr": False}
    deadline = time.monotonic() + timeout
    timed_out = False
    kill_deadline: float | None = None

    while selector.get_map() or process.poll() is None:
        now = time.monotonic()
        if not timed_out and now >= deadline:
            timed_out = True
            kill_deadline = now + 1.0
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if kill_deadline is not None and now >= kill_deadline:
            for key in list(selector.get_map().values()):
                selector.unregister(key.fileobj)
                key.fileobj.close()
            break

        wait_for = 0.1 if timed_out else max(0.0, min(0.1, deadline - now))
        if selector.get_map():
            events = selector.select(wait_for)
        else:
            time.sleep(wait_for)
            events = []
        for key, _ in events:
            stream_name: str = key.data
            try:
                chunk = os.read(key.fileobj.fileno(), 8192)
            except OSError:
                chunk = b""
            if not chunk:
                selector.unregister(key.fileobj)
                key.fileobj.close()
                continue
            buffer = captured[stream_name]
            remaining = per_stream_limit - len(buffer)
            if remaining > 0:
                buffer.extend(chunk[:remaining])
            if len(chunk) > max(remaining, 0):
                truncated[stream_name] = True

    try:
        return_code = process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return_code = process.wait(timeout=1)
        timed_out = True

    stdout, stdout_decode_truncated = _decode_capped(
        bytes(captured["stdout"]), per_stream_limit
    )
    stderr, stderr_decode_truncated = _decode_capped(
        bytes(captured["stderr"]), per_stream_limit
    )

    return {
        "return_code": return_code,
        "timed_out": timed_out,
        "stdout": stdout,
        "stderr": stderr,
        "output_truncated": any(truncated.values())
        or stdout_decode_truncated
        or stderr_decode_truncated,
    }


def _decode_capped(value: bytes, limit: int) -> tuple[str, bool]:
    """Keep the JSON string's UTF-8 size within its share of the output budget."""
    decoded = value.decode("utf-8", errors="replace")
    encoded = decoded.encode("utf-8")
    if len(encoded) <= limit:
        return decoded, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("--output-limit", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print(json.dumps({"error": "a child command is required"}))
        return 2

    result = execute(command, args.timeout, args.output_limit)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
