from __future__ import annotations

import sys

from runner.sandbox_tools.capped_exec import execute


def test_child_output_is_capped_without_stopping_pipe_drain() -> None:
    result = execute(
        [sys.executable, "-c", "print('x' * 100_000)"],
        timeout=5,
        output_limit=1000,
        cwd=".",
    )

    assert result["return_code"] == 0
    assert result["output_truncated"] is True
    assert len(str(result["stdout"]).encode("utf-8")) <= 500


def test_utf8_output_respects_the_byte_limit_after_decoding() -> None:
    result = execute(
        [sys.executable, "-c", "print('汉' * 100_000)"],
        timeout=5,
        output_limit=1000,
        cwd=".",
    )

    assert result["output_truncated"] is True
    assert len(str(result["stdout"]).encode("utf-8")) + len(
        str(result["stderr"]).encode("utf-8")
    ) <= 1000


def test_child_process_group_is_killed_after_timeout() -> None:
    result = execute(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        timeout=0.1,
        output_limit=1000,
        cwd=".",
    )

    assert result["timed_out"] is True
    assert result["return_code"] is not None


def test_child_that_closes_output_can_still_finish_before_deadline() -> None:
    result = execute(
        [sys.executable, "-c", "import os,time; os.close(1); os.close(2); time.sleep(0.2)"],
        timeout=2,
        output_limit=1000,
        cwd=".",
    )

    assert result["timed_out"] is False
    assert result["return_code"] == 0
