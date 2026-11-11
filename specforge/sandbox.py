from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import List, Optional, Tuple

from .models import ASTMetrics, EdgeCaseSpec, SandboxRunResult, TestCaseResult


BANNED_CALLS = {"eval", "exec", "compile", "__import__"}
BANNED_MODULES = {"subprocess", "socket", "shutil", "ctypes"}


class SandboxExecutor:
    """Executes generated Python code & adversarial tests in an isolated subprocess."""

    def __init__(self, timeout_seconds: float = 3.0) -> None:
        self.timeout_seconds = timeout_seconds

    def analyze_ast(self, code: str) -> ASTMetrics:
        lines = [ln for ln in code.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return ASTMetrics(
                line_count=len(lines),
                function_count=0,
                branch_count=0,
                loop_count=0,
                cyclomatic_complexity=1,
                has_type_hints=False,
                security_warnings=[f"SyntaxError at line {exc.lineno}: {exc.msg}"],
            )

        func_count = 0
        branch_count = 0
        loop_count = 0
        bool_ops = 0
        annotated_funcs = 0
        warnings: List[str] = []

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_count += 1
                if node.returns is not None or any(a.annotation is not None for a in node.args.args):
                    annotated_funcs += 1
            elif isinstance(node, (ast.If, ast.IfExp, ast.ExceptHandler)):
                branch_count += 1
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.SetComp, ast.DictComp)):
                loop_count += 1
            elif isinstance(node, ast.BoolOp):
                bool_ops += max(0, len(node.values) - 1)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".")[0]
                    if root_mod in BANNED_MODULES:
                        warnings.append(f"Restricted module import: '{root_mod}'")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_mod = node.module.split(".")[0]
                    if root_mod in BANNED_MODULES:
                        warnings.append(f"Restricted module import: '{root_mod}'")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in BANNED_CALLS:
                    warnings.append(f"Restricted builtin call: '{node.func.id}()'")

        complexity = 1 + branch_count + loop_count + bool_ops
        return ASTMetrics(
            line_count=len(lines),
            function_count=func_count,
            branch_count=branch_count,
            loop_count=loop_count,
            cyclomatic_complexity=complexity,
            has_type_hints=(func_count > 0 and annotated_funcs == func_count),
            security_warnings=warnings,
        )

    def build_harness_script(self, function_name: str, edge_cases: List[EdgeCaseSpec]) -> str:
        cases_payload = [
            {
                "name": ec.name,
                "category": ec.category,
                "input_repr": ec.input_repr,
                "expected_repr": ec.expected_repr,
                "rationale": ec.rationale,
            }
            for ec in edge_cases
        ]
        serialized = json.dumps(cases_payload, ensure_ascii=False)
        harness = f"""\
import json
import time
import traceback
import math
import solution

CASES = json.loads({serialized!r})
FUNC_NAME = {function_name!r}

def _AlmostEqual(a, b):
    if isinstance(a, float) and isinstance(b, (int, float)):
        return math.isclose(a, float(b), rel_tol=1e-6, abs_tol=1e-6)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)) and type(a) is type(b) and len(a) == len(b):
        return all(_AlmostEqual(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return list(a.keys()) == list(b.keys()) and all(_AlmostEqual(a[k], b[k]) for k in a)
    return a == b

def main():
    results = []
    target_fn = getattr(solution, FUNC_NAME, None)
    for case in CASES:
        t0 = time.perf_counter()
        if target_fn is None:
            results.append({{
                "name": case["name"],
                "category": case["category"],
                "passed": False,
                "duration_ms": 0.0,
                "input_summary": case["input_repr"],
                "expected_summary": case["expected_repr"],
                "actual_summary": "<missing function>",
                "error_type": "AttributeError",
                "error_message": f"Function '{{FUNC_NAME}}' not found in solution.py",
                "traceback_snippet": None,
            }})
            continue

        try:
            args, kwargs = eval(case["input_repr"], {{"__builtins__": __builtins__}}, {{}})
            expected = eval(case["expected_repr"], {{"__builtins__": __builtins__}}, {{}})
        except Exception as exc:
            results.append({{
                "name": case["name"],
                "category": case["category"],
                "passed": False,
                "duration_ms": 0.0,
                "input_summary": case["input_repr"],
                "expected_summary": case["expected_repr"],
                "actual_summary": "<harness eval error>",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "traceback_snippet": traceback.format_exc(limit=3),
            }})
            continue

        try:
            if isinstance(expected, type) and issubclass(expected, BaseException):
                try:
                    actual = target_fn(*args, **kwargs)
                    dt = (time.perf_counter() - t0) * 1000.0
                    results.append({{
                        "name": case["name"],
                        "category": case["category"],
                        "passed": False,
                        "duration_ms": round(dt, 3),
                        "input_summary": case["input_repr"],
                        "expected_summary": f"raises {{expected.__name__}}",
                        "actual_summary": repr(actual),
                        "error_type": "AssertionError",
                        "error_message": f"Expected {{expected.__name__}} to be raised, but returned {{actual!r}}",
                        "traceback_snippet": f"solution.py: {{FUNC_NAME}} returned {{actual!r}} instead of raising {{expected.__name__}}",
                    }})
                except expected as raised_exc:
                    dt = (time.perf_counter() - t0) * 1000.0
                    results.append({{
                        "name": case["name"],
                        "category": case["category"],
                        "passed": True,
                        "duration_ms": round(dt, 3),
                        "input_summary": case["input_repr"],
                        "expected_summary": f"raises {{expected.__name__}}",
                        "actual_summary": f"raised {{type(raised_exc).__name__}}({{raised_exc}})",
                        "error_type": None,
                        "error_message": None,
                        "traceback_snippet": None,
                    }})
            else:
                actual = target_fn(*args, **kwargs)
                dt = (time.perf_counter() - t0) * 1000.0
                ok = _AlmostEqual(actual, expected)
                results.append({{
                    "name": case["name"],
                    "category": case["category"],
                    "passed": bool(ok),
                    "duration_ms": round(dt, 3),
                    "input_summary": case["input_repr"],
                    "expected_summary": repr(expected),
                    "actual_summary": repr(actual),
                    "error_type": None if ok else "AssertionError",
                    "error_message": None if ok else f"Expected {{expected!r}}, got {{actual!r}}",
                    "traceback_snippet": None if ok else f"AssertionError in {{case['name']}}: {{FUNC_NAME}}(*{{args!r}}) -> {{actual!r}} != {{expected!r}}",
                }})
        except Exception as exc:
            dt = (time.perf_counter() - t0) * 1000.0
            tb_lines = traceback.format_exc().strip().splitlines()
            snippet = "\\n".join(tb_lines[-4:]) if tb_lines else str(exc)
            results.append({{
                "name": case["name"],
                "category": case["category"],
                "passed": False,
                "duration_ms": round(dt, 3),
                "input_summary": case["input_repr"],
                "expected_summary": repr(expected),
                "actual_summary": f"<raised {{type(exc).__name__}}>",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "traceback_snippet": snippet,
            }})

    print("__SPECFORGE_JSON_START__")
    print(json.dumps(results, ensure_ascii=False))
    print("__SPECFORGE_JSON_END__")

if __name__ == "__main__":
    main()
"""
        return textwrap.dedent(harness)

    def execute(
        self,
        iteration: int,
        code: str,
        function_name: str,
        edge_cases: List[EdgeCaseSpec],
    ) -> SandboxRunResult:
        ast_metrics = self.analyze_ast(code)
        if ast_metrics.security_warnings:
            failed_case = TestCaseResult(
                name="sandbox_static_gate",
                category="invariant",
                passed=False,
                duration_ms=0.1,
                input_summary="<static AST check>",
                expected_summary="0 security/syntax errors",
                actual_summary="; ".join(ast_metrics.security_warnings),
                error_type="SecurityOrSyntaxError",
                error_message="; ".join(ast_metrics.security_warnings),
                traceback_snippet="; ".join(ast_metrics.security_warnings),
            )
            return SandboxRunResult(
                iteration=iteration,
                all_passed=False,
                total_tests=len(edge_cases),
                passed_tests=0,
                failed_tests=len(edge_cases),
                total_duration_ms=0.1,
                stdout="",
                stderr="\n".join(ast_metrics.security_warnings),
                test_results=[failed_case],
                ast_metrics=ast_metrics,
            )

        harness_code = self.build_harness_script(function_name, edge_cases)
        t_start = time.perf_counter()

        with tempfile.TemporaryDirectory(prefix="specforge_sbx_") as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / "solution.py").write_text(code, encoding="utf-8")
            (tmp_path / "run_harness.py").write_text(harness_code, encoding="utf-8")

            try:
                proc = subprocess.run(
                    [sys.executable, "run_harness.py"],
                    cwd=tmpdir,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                )
                total_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
                stdout = proc.stdout
                stderr = proc.stderr
            except subprocess.TimeoutExpired as exc:
                total_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
                timeout_case = TestCaseResult(
                    name="sandbox_timeout_guard",
                    category="invariant",
                    passed=False,
                    duration_ms=total_ms,
                    input_summary="<all cases>",
                    expected_summary=f"completion < {self.timeout_seconds}s",
                    actual_summary="TimeoutExpired (probable infinite loop)",
                    error_type="TimeoutError",
                    error_message=f"Execution exceeded {self.timeout_seconds}s sandbox budget",
                    traceback_snippet="TimeoutExpired in subprocess (check while-loop termination invariants)",
                )
                return SandboxRunResult(
                    iteration=iteration,
                    all_passed=False,
                    total_tests=len(edge_cases),
                    passed_tests=0,
                    failed_tests=len(edge_cases),
                    total_duration_ms=total_ms,
                    stdout=exc.stdout or "",
                    stderr=f"TimeoutExpired after {self.timeout_seconds}s",
                    test_results=[timeout_case],
                    ast_metrics=ast_metrics,
                )

        test_results: List[TestCaseResult] = []
        if "__SPECFORGE_JSON_START__" in stdout and "__SPECFORGE_JSON_END__" in stdout:
            raw_json = stdout.split("__SPECFORGE_JSON_START__", 1)[1].split("__SPECFORGE_JSON_END__", 1)[0].strip()
            parsed = json.loads(raw_json)
            for item in parsed:
                test_results.append(TestCaseResult(**item))
        else:
            test_results.append(
                TestCaseResult(
                    name="runtime_import_check",
                    category="invariant",
                    passed=False,
                    duration_ms=total_ms,
                    input_summary="import solution",
                    expected_summary="clean module import",
                    actual_summary="Runtime crash during import",
                    error_type="RuntimeError",
                    error_message=stderr.strip() or "Subprocess exited abnormally",
                    traceback_snippet=stderr.strip()[-400:] if stderr else None,
                )
            )

        passed_count = sum(1 for tr in test_results if tr.passed)
        failed_count = len(test_results) - passed_count
        return SandboxRunResult(
            iteration=iteration,
            all_passed=(failed_count == 0 and len(test_results) > 0),
            total_tests=len(test_results),
            passed_tests=passed_count,
            failed_tests=failed_count,
            total_duration_ms=total_ms,
            stdout=stdout,
            stderr=stderr,
            test_results=test_results,
            ast_metrics=ast_metrics,
        )
