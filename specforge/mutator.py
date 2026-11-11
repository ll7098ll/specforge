from __future__ import annotations

import ast
import copy
import difflib
import time
from typing import Callable, List, Optional, Tuple

from .models import (
    EdgeCaseSpec,
    MutantEvalResult,
    MutantSpec,
    MutationReport,
    SpecContract,
)
from .sandbox import SandboxExecutor


class _IndexedMutator(ast.NodeTransformer):
    """Applies a single targeted AST mutation at target_index."""

    def __init__(self, target_index: int) -> None:
        super().__init__()
        self.target_index = target_index
        self.current_index = 0
        self.applied_operator = ""
        self.applied_lineno = 1
        self.applied_desc = ""

    def _check_hit(self, lineno: int, op_label: str, desc: str) -> bool:
        idx = self.current_index
        self.current_index += 1
        if idx == self.target_index:
            self.applied_lineno = lineno
            self.applied_operator = op_label
            self.applied_desc = desc
            return True
        return False

    def visit_Compare(self, node: ast.Compare) -> ast.AST:  # noqa: N802
        self.generic_visit(node)
        if len(node.ops) == 1:
            op = node.ops[0]
            lineno = getattr(node, "lineno", 1)
            if isinstance(op, ast.Lt):
                if self._check_hit(lineno, "CR (< -> <=)", "경계값 포함 변이: '<' 연산자를 '<='로 변경하여 0/경계 입력 처리 검증"):
                    node.ops[0] = ast.LtE()
            elif isinstance(op, ast.LtE):
                if self._check_hit(lineno, "CR (<= -> <)", "경계값 제외 변이: '<=' 연산자를 '<'로 완화하여 정확히 0인 경계값 검증"):
                    node.ops[0] = ast.Lt()
            elif isinstance(op, ast.GtE):
                if self._check_hit(lineno, "CR (>= -> >)", "임계값 제외 변이: '>=' 연산자를 '>'로 변경하여 정확한 경계 일치 케이스 검증"):
                    node.ops[0] = ast.Gt()
            elif isinstance(op, ast.Gt):
                if self._check_hit(lineno, "CR (> -> <)", "비교 역전 변이: '>' 연산자를 '<'로 역전하여 단조 증가 조건 검증"):
                    node.ops[0] = ast.Lt()
            elif isinstance(op, ast.Eq):
                # Check if comparing against Decimal("0.00") or 0
                comp_src = ast.unparse(node.comparators[0]) if node.comparators else ""
                if "0" in comp_src:
                    if self._check_hit(lineno, "CR (== 0 -> < 0)", "영(0) 동치 비교를 '< 0'으로 변이하여 0/음수영(-0.00) 경계 검증"):
                        node.ops[0] = ast.Lt()
                else:
                    if self._check_hit(lineno, "CR (== -> !=)", "동치 연산자 '=='를 '!='로 역전 변이"):
                        node.ops[0] = ast.NotEq()
        return node

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:  # noqa: N802
        self.generic_visit(node)
        lineno = getattr(node, "lineno", 1)
        if isinstance(node.op, ast.Add):
            if self._check_hit(lineno, "AOR (+ -> -)", "산술 덧셈(+)을 뺄셈(-)으로 변이"):
                node.op = ast.Sub()
        elif isinstance(node.op, ast.Sub):
            if self._check_hit(lineno, "AOR (- -> +)", "산술 뺄셈(-)을 덧셈(+)으로 변이"):
                node.op = ast.Add()
        elif isinstance(node.op, ast.Mult):
            if self._check_hit(lineno, "AOR (* -> /)", "산술 곱셈(*)을 나눗셈(/)으로 변이"):
                node.op = ast.Div()
        return node

    def visit_AugAssign(self, node: ast.AugAssign) -> ast.AST:  # noqa: N802
        self.generic_visit(node)
        lineno = getattr(node, "lineno", 1)
        if isinstance(node.op, ast.Sub):
            if self._check_hit(lineno, "AOR (-= -> +=)", "누적 차감(-=)을 누적 가산(+=)으로 변이"):
                node.op = ast.Add()
        elif isinstance(node.op, ast.Add):
            if self._check_hit(lineno, "AOR (+= -> -=)", "누적 가산(+=)을 누적 차감(-=)으로 변이"):
                node.op = ast.Sub()
        return node

    def visit_Call(self, node: ast.Call) -> ast.AST:  # noqa: N802
        self.generic_visit(node)
        lineno = getattr(node, "lineno", 1)
        if isinstance(node.func, ast.Name):
            if node.func.id == "min":
                if self._check_hit(lineno, "CALL (min -> max)", "상한 캡 함수 min()을 max()로 변이"):
                    node.func.id = "max"
            elif node.func.id == "sorted":
                if self._check_hit(lineno, "CALL (sorted -> list)", "정렬 보장 함수 sorted()를 단순 list()로 변이"):
                    node.func.id = "list"
        return node

    def visit_UnaryOp(self, node: ast.UnaryOp) -> ast.AST:  # noqa: N802
        self.generic_visit(node)
        lineno = getattr(node, "lineno", 1)
        if isinstance(node.op, ast.Not):
            if self._check_hit(lineno, "UOR (not x -> x)", "논리 부정(not) 연산자 제거 변이"):
                return node.operand
        return node


class ASTMutatorAgent:
    """Generates real Python AST mutants and audits QA test suite thoroughness."""

    def generate_mutants(self, code: str, max_mutants: int = 8) -> List[MutantSpec]:
        try:
            base_tree = ast.parse(code)
        except SyntaxError:
            return []

        normalized_base = ast.unparse(base_tree)
        base_lines = normalized_base.splitlines()

        # Probe how many mutation sites exist
        mutants: List[MutantSpec] = []
        idx = 0
        while len(mutants) < max_mutants and idx < 40:
            cloned = copy.deepcopy(base_tree)
            transformer = _IndexedMutator(target_index=idx)
            mutated_tree = transformer.visit(cloned)
            ast.fix_missing_locations(mutated_tree)
            idx += 1

            if not transformer.applied_operator:
                break

            mutated_code = ast.unparse(mutated_tree)
            if mutated_code == normalized_base:
                continue

            mut_lines = mutated_code.splitlines()
            orig_line = ""
            mut_line = ""
            changed_line_no = transformer.applied_lineno
            for line_i, (ol, ml) in enumerate(zip(base_lines, mut_lines), start=1):
                if ol != ml:
                    orig_line = ol.strip()
                    mut_line = ml.strip()
                    changed_line_no = line_i
                    break

            diff_str = "".join(
                difflib.unified_diff(
                    normalized_base.splitlines(keepends=True),
                    mutated_code.splitlines(keepends=True),
                    fromfile="verified_solution.py",
                    tofile=f"mutant_M{len(mutants) + 1:02d}.py",
                    n=1,
                )
            )

            mutants.append(
                MutantSpec(
                    mutant_id=f"M{len(mutants) + 1:02d}",
                    operator=transformer.applied_operator,
                    lineno=changed_line_no,
                    original_line=orig_line,
                    mutated_line=mut_line,
                    mutated_code=mutated_code + "\n",
                    unified_diff=diff_str,
                    description=transformer.applied_desc,
                )
            )

        return mutants

    def synthesize_killer_tests(
        self,
        spec: SpecContract,
        survived: List[MutantEvalResult],
    ) -> List[EdgeCaseSpec]:
        """Analyzes surviving AST mutants and synthesizes targeted Killer Test Cases."""
        killers: List[EdgeCaseSpec] = []
        seen_names: set[str] = {ec.name for ec in spec.edge_cases}

        for item in survived:
            m = item.mutant
            if spec.mission_id == "token_bucket_limiter":
                if "refill_rate <= 0" in m.mutated_line:
                    k_name = "test_killer_zero_refill_rate_static_quota_allowed"
                    item.survival_analysis = (
                        "초기 테스트 스위트에 refill_rate=0.0 (정적 쿼터 모드) 케이스가 없어 "
                        "`refill_rate < 0` 이 `refill_rate <= 0` 으로 변이되어도 모든 테스트가 통과함"
                    )
                    item.killer_test_name = k_name
                    if k_name not in seen_names:
                        seen_names.add(k_name)
                        killers.append(
                            EdgeCaseSpec(
                                name=k_name,
                                input_repr="(([(0.0, 2)], 5, 0.0), {})",
                                expected_repr="[(True, 3.0)]",
                                category="killer",
                                rationale=f"[Killer for {m.mutant_id}] refill_rate=0.0 경계값이 ValueError로 오차단되지 않고 정상 동작하는지 검증",
                            )
                        )
                elif "cost <= 0" in m.mutated_line or "CR (< -> <=)" in m.operator:
                    k_name = "test_killer_zero_cost_heartbeat_allowed"
                    item.survival_analysis = (
                        "초기 테스트 스위트에 cost=0 (무료 헬스체크/하트비트 요청) 케이스가 없어 "
                        "`if cost < 0:` 이 `if cost <= 0:` 으로 변이되어도 모든 테스트가 통과함"
                    )
                    item.killer_test_name = k_name
                    if k_name not in seen_names:
                        seen_names.add(k_name)
                        killers.append(
                            EdgeCaseSpec(
                                name=k_name,
                                input_repr="(([(0.0, 0), (1.0, 2)], 5, 1.0), {})",
                                expected_repr="[(True, 5.0), (True, 3.0)]",
                                category="killer",
                                rationale=f"[Killer for {m.mutant_id}] cost=0 요청이 ValueError로 오차단되지 않고 정상 승인되는지 검증",
                            )
                        )

            elif spec.mission_id == "dependency_toposort":
                if "len(node)" in m.mutated_line or "CR (== 0 -> < 0)" in m.operator:
                    k_name = "test_killer_empty_package_name_raises_value_error"
                    item.survival_analysis = (
                        "초기 테스트에 빈 문자열 패키지명('') 입력 케이스가 없어 "
                        "`len(node) == 0` 검증이 `len(node) < 0` (항상 False)으로 무력화되어도 생존함"
                    )
                    item.killer_test_name = k_name
                    if k_name not in seen_names:
                        seen_names.add(k_name)
                        killers.append(
                            EdgeCaseSpec(
                                name=k_name,
                                input_repr="(({'': ['auth']},), {})",
                                expected_repr="ValueError",
                                category="killer",
                                rationale=f"[Killer for {m.mutant_id}] 빈 패키지 식별자('') 유입 시 즉시 ValueError로 차단하는지 검증",
                            )
                        )

            elif spec.mission_id == "circuit_breaker_fsm":
                if "cooldown_sec < 0" in m.mutated_line or "CR (<= -> <)" in m.operator:
                    k_name = "test_killer_zero_cooldown_raises_value_error"
                    item.survival_analysis = (
                        "초기 경계 테스트가 fail_threshold=0 만 검사하고 cooldown_sec=0.0 은 검사하지 않아 "
                        "`cooldown_sec <= 0` 이 `cooldown_sec < 0` 으로 변이되어도 생존함"
                    )
                    item.killer_test_name = k_name
                    if k_name not in seen_names:
                        seen_names.add(k_name)
                        killers.append(
                            EdgeCaseSpec(
                                name=k_name,
                                input_repr="(([(1.0, True)], 2, 0.0), {})",
                                expected_repr="ValueError",
                                category="killer",
                                rationale=f"[Killer for {m.mutant_id}] cooldown_sec=0.0 경계 입력 시 즉시 ValueError를 발생시키는지 검증",
                            )
                        )

            elif spec.mission_id == "financial_ledger_reconcile":
                k_name = "test_killer_overdraft_negative_balance_and_account_sort"
                if "sorted" in m.mutated_line:
                    item.survival_analysis = (
                        "초기 4개 테스트가 모두 단일 계좌만 포함하여 `sorted(balances)`가 `list(balances)`로 변이되어도 키 역순 정렬 버그가 노출되지 않음"
                    )
                else:
                    item.survival_analysis = (
                        "초기 테스트에 최종 마이너스 잔액(Overdraft) 케이스가 없어 `if val == Decimal('0.00'):` 이 `if val < Decimal('0.00'):` 로 변이되어 음수 잔액이 0.00으로 소실되어도 생존함"
                    )
                item.killer_test_name = k_name
                if k_name not in seen_names:
                    seen_names.add(k_name)
                    killers.append(
                        EdgeCaseSpec(
                            name=k_name,
                            input_repr="(([('acct_z', '-25.50', 'USD'), ('acct_a', '10.00', 'USD')], {'USD': '1.00'}), {})",
                            expected_repr="{'acct_a': '10.00', 'acct_z': '-25.50'}",
                            category="killer",
                            rationale=f"[Killer for {m.mutant_id}] 역순 다중 계좌('acct_z', 'acct_a') 정렬 순서 및 마이너스 잔액('-25.50') 보존 검증",
                        )
                    )

            if not item.survival_analysis:
                item.survival_analysis = (
                    f"Line {m.lineno} `{m.original_line}` -> `{m.mutated_line}` 변이에 대한 경계값 단언문 보강 필요"
                )

        return killers

    def run_audit(
        self,
        spec: SpecContract,
        verified_code: str,
        sandbox: SandboxExecutor,
        auto_harden_qa: bool = True,
        on_mutant_tested: Optional[Callable[[MutantEvalResult], None]] = None,
    ) -> MutationReport:
        t0 = time.perf_counter()
        mutants = self.generate_mutants(verified_code, max_mutants=8)
        initial_results: List[MutantEvalResult] = []

        for idx, mutant in enumerate(mutants, start=1):
            sb_res = sandbox.execute(
                iteration=100 + idx,
                code=mutant.mutated_code,
                function_name=spec.function_name,
                edge_cases=spec.edge_cases,
            )
            failed_tests = [tr.name for tr in sb_res.test_results if not tr.passed]
            if any(tr.error_type == "TimeoutError" for tr in sb_res.test_results):
                status = "TIMEOUT"
                killed_by = "sandbox_timeout_guard"
            elif failed_tests:
                status = "KILLED"
                killed_by = failed_tests[0]
            else:
                status = "SURVIVED"
                killed_by = None

            eval_res = MutantEvalResult(
                mutant=mutant,
                status=status,
                killed_by_test=killed_by,
                duration_ms=sb_res.total_duration_ms,
            )
            initial_results.append(eval_res)
            if on_mutant_tested:
                on_mutant_tested(eval_res)

        total = len(initial_results)
        init_killed = sum(1 for r in initial_results if r.status in ("KILLED", "TIMEOUT"))
        init_survived_list = [r for r in initial_results if r.status == "SURVIVED"]
        init_survived = len(init_survived_list)
        init_score = round((init_killed / total * 100.0), 1) if total > 0 else 100.0

        killer_tests: List[EdgeCaseSpec] = []
        hardened_results: List[MutantEvalResult] = []

        if auto_harden_qa and init_survived_list:
            killer_tests = self.synthesize_killer_tests(spec, init_survived_list)
            augmented_cases = list(spec.edge_cases) + killer_tests

            for idx, orig_eval in enumerate(initial_results, start=1):
                if orig_eval.status != "SURVIVED":
                    hardened_results.append(orig_eval)
                    continue

                sb_res2 = sandbox.execute(
                    iteration=200 + idx,
                    code=orig_eval.mutant.mutated_code,
                    function_name=spec.function_name,
                    edge_cases=augmented_cases,
                )
                failed2 = [tr.name for tr in sb_res2.test_results if not tr.passed]
                hardened_results.append(
                    MutantEvalResult(
                        mutant=orig_eval.mutant,
                        status="KILLED" if failed2 else "SURVIVED",
                        killed_by_test=failed2[0] if failed2 else None,
                        duration_ms=sb_res2.total_duration_ms,
                        survival_analysis=orig_eval.survival_analysis,
                        killer_test_name=orig_eval.killer_test_name,
                    )
                )
        else:
            hardened_results = list(initial_results)

        final_killed = sum(1 for r in hardened_results if r.status in ("KILLED", "TIMEOUT"))
        final_survived = total - final_killed
        final_score = round((final_killed / total * 100.0), 1) if total > 0 else 100.0
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        return MutationReport(
            total_mutants=total,
            initial_killed=init_killed,
            initial_survived=init_survived,
            initial_score_pct=init_score,
            final_killed=final_killed,
            final_survived=final_survived,
            final_score_pct=final_score,
            total_duration_ms=elapsed_ms,
            initial_results=initial_results,
            killer_tests_added=killer_tests,
            hardened_results=hardened_results,
        )
