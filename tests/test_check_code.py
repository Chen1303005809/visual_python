from __future__ import annotations

from pathlib import Path

from runner.sandbox_tools import check_code


def test_syntax_diagnostic_has_line_and_column(monkeypatch) -> None:
    monkeypatch.setattr(check_code, "_ruff_diagnostics", lambda path: ([], None))

    result = check_code.check_source("def broken(:\n    pass\n", Path("solution.py"))

    assert result["status"] == "failed"
    assert result["syntax_valid"] is False
    diagnostic = result["diagnostics"][0]
    assert diagnostic["source"] == "syntax"
    assert diagnostic["line"] == 1
    assert diagnostic["column"] is not None


def test_import_check_reports_modules_missing_from_the_runtime(monkeypatch) -> None:
    monkeypatch.setattr(check_code, "_ruff_diagnostics", lambda path: ([], None))
    monkeypatch.setattr(check_code, "_module_exists", lambda name: name == "math")

    result = check_code.check_source(
        "import math\nimport not_a_real_runtime_package\n", Path("solution.py")
    )

    assert result["checked_imports"] == ["math", "not_a_real_runtime_package"]
    assert result["status"] == "failed"
    assert result["diagnostics"] == [
        {
            "source": "imports",
            "code": "IMPORT_NOT_FOUND",
            "message": "Module 'not_a_real_runtime_package' is not available in the sandbox image",
            "severity": "error",
            "line": 2,
            "column": 1,
        }
    ]


def test_checker_compiles_but_does_not_execute_user_source(monkeypatch) -> None:
    monkeypatch.setattr(check_code, "_ruff_diagnostics", lambda path: ([], None))
    monkeypatch.setattr(check_code, "_module_exists", lambda name: True)

    result = check_code.check_source(
        "raise RuntimeError('the checker must not run this')\n", Path("solution.py")
    )

    assert result["status"] == "passed"
    assert result["syntax_valid"] is True


def test_ruff_error_is_reported_as_a_checker_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        check_code,
        "_ruff_diagnostics",
        lambda path: (
            [
                {
                    "source": "ruff",
                    "code": "F821",
                    "message": "Undefined name",
                    "severity": "error",
                    "line": 1,
                    "column": 1,
                }
            ],
            None,
        ),
    )

    result = check_code.check_source("answer = missing\n", Path("solution.py"))

    assert result["status"] == "failed"
    assert result["diagnostics"][0]["code"] == "F821"
