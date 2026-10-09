"""Request and response contracts exposed to Dify."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.limits import MAX_INPUT_BYTES


class CodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(
        min_length=1,
        description="完整的单个 Python 3.12 源码文件内容。",
    )

    @model_validator(mode="after")
    def validate_input_size(self) -> CodeRequest:
        if len(self.source.encode("utf-8")) > MAX_INPUT_BYTES:
            raise ValueError(f"source exceeds the {MAX_INPUT_BYTES}-byte input limit")
        return self


class TestRequest(CodeRequest):
    test_code: str = Field(
        min_length=1,
        description=(
            "pytest 测试源码。请至少包含一个 test_* 函数；测试可从 solution 模块导入被测代码。"
        ),
    )

    @model_validator(mode="after")
    def validate_input_size(self) -> TestRequest:
        total_bytes = len(self.source.encode("utf-8")) + len(self.test_code.encode("utf-8"))
        if total_bytes > MAX_INPUT_BYTES:
            raise ValueError(f"source and test_code exceed the {MAX_INPUT_BYTES}-byte input limit")
        return self


class Diagnostic(BaseModel):
    source: Literal["syntax", "ruff", "imports", "checker"]
    code: str
    message: str
    severity: Literal["error", "warning"]
    line: int | None = None
    column: int | None = None


class CheckResponse(BaseModel):
    status: Literal["passed", "failed", "error"]
    syntax_valid: bool
    python_version: str
    diagnostics: list[Diagnostic]
    checked_imports: list[str]


class TestResponse(BaseModel):
    status: Literal["passed", "failed", "error", "timed_out", "resource_limited", "no_tests"]
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    exit_code: int | None = None
    duration_ms: int
    stdout: str
    stderr: str
    output_truncated: bool = False
