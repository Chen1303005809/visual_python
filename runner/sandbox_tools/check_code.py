"""Static source checker executed inside the trusted sandbox image."""

from __future__ import annotations

import ast
import json
import importlib.util
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any


def _diagnostic(
    *,
    source: str,
    code: str,
    message: str,
    line: int | None = None,
    column: int | None = None,
    severity: str = "error",
) -> dict[str, Any]:
    return {
        "source": source,
        "code": code,
        "message": message,
        "severity": severity,
        "line": line,
        "column": column,
    }


def _import_names(tree: ast.AST) -> list[tuple[str, int, int]]:
    modules: dict[str, tuple[int, int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.setdefault(alias.name, (node.lineno, node.col_offset + 1))
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.setdefault(node.module, (node.lineno, node.col_offset + 1))
    return [(name, *position) for name, position in sorted(modules.items())]


def _module_exists(name: str) -> bool:
    if name in sys.stdlib_module_names or name == "builtins":
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _ruff_diagnostics(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    try:
        result = subprocess.run(
            [
                "ruff",
                "check",
                "--output-format=json",
                "--target-version",
                "py312",
                "--select",
                "E4,E7,E9,F",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "PYTHONNOUSERSITE": "1"},
        )
    except FileNotFoundError:
        return [], "Ruff is missing from the sandbox image"
    except subprocess.TimeoutExpired:
        return [], "Ruff exceeded its 8-second analysis limit"

    try:
        items = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return [], "Ruff returned invalid JSON"
    if result.returncode not in (0, 1):
        message = result.stderr.strip() or "Ruff could not analyze the source"
        return [], message[:2048]

    diagnostics: list[dict[str, Any]] = []
    for item in items:
        location = item.get("location") or {}
        diagnostics.append(
            _diagnostic(
                source="ruff",
                code=str(item.get("code") or "RUFF"),
                message=str(item.get("message") or "Ruff found a problem"),
                line=location.get("row"),
                column=location.get("column"),
            )
        )
    return diagnostics, None


def check_source(source: str, path: Path) -> dict[str, Any]:
    diagnostics: list[dict[str, Any]] = []
    checked_imports: list[str] = []
    syntax_valid = True

    try:
        tree = ast.parse(source, filename=path.name, mode="exec", type_comments=True)
        compile(source, str(path), "exec", dont_inherit=True)
    except SyntaxError as exc:
        syntax_valid = False
        diagnostics.append(
            _diagnostic(
                source="syntax",
                code="PY-SYNTAX",
                message=exc.msg,
                line=exc.lineno,
                column=exc.offset,
            )
        )
        tree = None

    ruff_items, ruff_error = _ruff_diagnostics(path)
    diagnostics.extend(ruff_items)
    if ruff_error:
        diagnostics.append(
            _diagnostic(
                source="checker",
                code="RUFF_UNAVAILABLE",
                message=ruff_error,
            )
        )

    if tree is not None:
        for module_name, line, column in _import_names(tree):
            checked_imports.append(module_name)
            if not _module_exists(module_name):
                diagnostics.append(
                    _diagnostic(
                        source="imports",
                        code="IMPORT_NOT_FOUND",
                        message=f"Module '{module_name}' is not available in the sandbox image",
                        line=line,
                        column=column,
                    )
                )

    if ruff_error:
        status = "error"
    elif diagnostics:
        status = "failed"
    else:
        status = "passed"
    return {
        "status": status,
        "syntax_valid": syntax_valid,
        "python_version": platform.python_version(),
        "diagnostics": diagnostics,
        "checked_imports": checked_imports,
    }


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"error": "expected one source file path"}))
        return 2
    source_path = Path(sys.argv[1])
    try:
        source = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        print(json.dumps({"error": f"could not read source: {exc}"}, ensure_ascii=False))
        return 2
    try:
        result = check_source(source, source_path)
    except (MemoryError, RecursionError, ValueError) as exc:
        result = {
            "status": "error",
            "syntax_valid": False,
            "python_version": platform.python_version(),
            "diagnostics": [
                _diagnostic(
                    source="checker",
                    code="CHECKER_FAILED",
                    message=f"Static analysis could not process this source: {type(exc).__name__}",
                )
            ],
            "checked_imports": [],
        }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
