from __future__ import annotations

import time
import uuid
from typing import Callable, Dict, List, Optional

from .agents import (
    MISSION_CATALOG,
    ArchitectAgent,
    CoderAgent,
    QAAgent,
    ReviewerAgent,
)
from .models import (
    AgentEvent,
    AgentRole,
    ComplexityProfileReport,
    IterationRecord,
    MutationReport,
    PipelineRun,
    PipelineState,
)
from .mutator import ASTMutatorAgent
from .profiler import ProfilerAgent
from .sandbox import SandboxExecutor


class SpecForgeOrchestrator:
    """Coordinates the multi-agent self-healing pipeline with real subprocess verification, AST mutation testing, & Big-O profiling."""

    def __init__(self, timeout_seconds: float = 0.6) -> None:
        self.architect = ArchitectAgent()
        self.coder = CoderAgent()
        self.qa = QAAgent()
        self.reviewer = ReviewerAgent()
        self.mutator = ASTMutatorAgent()
        self.profiler = ProfilerAgent()
        self.sandbox = SandboxExecutor(timeout_seconds=timeout_seconds)

    def list_missions(self) -> List[Dict[str, object]]:
        items: List[Dict[str, object]] = []
        for mid, data in MISSION_CATALOG.items():
            spec = self.architect.create_spec(mid)
            drafts = data["drafts"]  # type: ignore[index]
            items.append(
                {
                    "mission_id": mid,
                    "title": spec.title,
                    "natural_prompt": spec.natural_prompt,
                    "function_name": spec.function_name,
                    "signature": spec.signature,
                    "docstring": spec.docstring,
                    "invariants": spec.invariants,
                    "complexity_target": spec.complexity_target,
                    "edge_case_count": len(spec.edge_cases),
                    "expected_iterations": len(drafts),  # type: ignore[arg-type]
                    "initial_buggy_code": drafts[0][1],  # type: ignore[index]
                    "verified_code": drafts[-1][1],  # type: ignore[index]
                }
            )
        return items

    def run_mutation_only(
        self,
        mission_id: str = "token_bucket_limiter",
        code: Optional[str] = None,
        auto_harden_qa: bool = True,
    ) -> MutationReport:
        spec = self.architect.create_spec(mission_id)
        if not code:
            entry = MISSION_CATALOG.get(spec.mission_id, MISSION_CATALOG["token_bucket_limiter"])
            drafts = entry["drafts"]  # type: ignore[index]
            code = str(drafts[-1][1])  # type: ignore[index]
        return self.mutator.run_audit(
            spec=spec,
            verified_code=code,
            sandbox=self.sandbox,
            auto_harden_qa=auto_harden_qa,
        )

    def run_profile_only(
        self,
        mission_id: str = "token_bucket_limiter",
        code: Optional[str] = None,
    ) -> ComplexityProfileReport:
        spec = self.architect.create_spec(mission_id)
        if not code:
            entry = MISSION_CATALOG.get(spec.mission_id, MISSION_CATALOG["token_bucket_limiter"])
            drafts = entry["drafts"]  # type: ignore[index]
            code = str(drafts[-1][1])  # type: ignore[index]
        return self.profiler.run_profile(spec=spec, verified_code=code)

    def run_pipeline(
        self,
        mission_id: str = "token_bucket_limiter",
        max_iterations: int = 4,
        custom_initial_code: Optional[str] = None,
        auto_heal: bool = True,
        enable_mutation_testing: bool = True,
        enable_profiling: bool = True,
        on_event: Optional[Callable[[AgentEvent], None]] = None,
    ) -> PipelineRun:
        run_id = f"sf-{uuid.uuid4().hex[:8]}"
        step_counter = 0

        def emit(
            role: AgentRole,
            state: PipelineState,
            iteration: int,
            title: str,
            message: str,
            **metadata: object,
        ) -> AgentEvent:
            nonlocal step_counter
            step_counter += 1
            ev = AgentEvent(
                step_id=step_counter,
                timestamp=time.time(),
                role=role,
                state=state,
                iteration=iteration,
                title=title,
                message=message,
                metadata=dict(metadata),
            )
            run.state = state
            run.events.append(ev)
            if on_event:
                on_event(ev)
            return ev

        # Step 1: Architect builds formal spec
        spec = self.architect.create_spec(mission_id)
        run = PipelineRun(
            run_id=run_id,
            mission_id=spec.mission_id,
            state=PipelineState.SPECIFYING,
            spec=spec,
            max_iterations=max_iterations,
        )

        emit(
            AgentRole.ARCHITECT,
            PipelineState.SPECIFYING,
            0,
            f"Formal Spec Contract 수립: {spec.function_name}()",
            f"불변조건(Invariants) {len(spec.invariants)}개 및 엣지 케이스 매트릭스 {len(spec.edge_cases)}건 정의 완료",
            invariants=spec.invariants,
            signature=spec.signature,
        )

        # Step 2: QA synthesizes adversarial test suite
        test_suite_code = self.qa.render_test_suite_code(spec)
        emit(
            AgentRole.QA,
            PipelineState.SPECIFYING,
            0,
            f"Adversarial Test Suite 생성 ({len(spec.edge_cases)} Cases)",
            "정상(Normal), 경계값(Boundary), 불변성(Invariant), 적대적 공격(Adversarial) 테스트 하네스 준비 완료",
            categories=[ec.category for ec in spec.edge_cases],
        )

        previous_code: Optional[str] = None
        last_diagnosis = None

        for iteration in range(1, max_iterations + 1):
            current_state = PipelineState.CODING if iteration == 1 else PipelineState.HEALING
            code, unified_diff, coder_notes = self.coder.generate_iteration_code(
                spec=spec,
                iteration=iteration,
                previous_code=previous_code,
                diagnosis=last_diagnosis,
                custom_initial_code=custom_initial_code,
            )

            emit(
                AgentRole.CODER,
                current_state,
                iteration,
                f"Iteration #{iteration} 코드 {'초안 작성' if iteration == 1 else '자가 수정 패치(Self-Heal) 생성'}",
                coder_notes,
                has_diff=bool(unified_diff),
                line_count=len(code.splitlines()),
            )

            emit(
                AgentRole.SANDBOX,
                PipelineState.TESTING,
                iteration,
                f"Subprocess 격리 샌드박스 실행 (Iteration #{iteration})",
                f"Python 서브프로세스에서 {len(spec.edge_cases)}개 테스트 케이스 및 AST 정적 분석 수행 중...",
            )

            sandbox_res = self.sandbox.execute(
                iteration=iteration,
                code=code,
                function_name=spec.function_name,
                edge_cases=spec.edge_cases,
            )

            status_badge = "ALL PASSED" if sandbox_res.all_passed else f"{sandbox_res.failed_tests} FAILED"
            emit(
                AgentRole.SANDBOX,
                PipelineState.TESTING,
                iteration,
                f"샌드박스 결과: {sandbox_res.passed_tests}/{sandbox_res.total_tests} 통과 ({status_badge})",
                f"실행 시간 {sandbox_res.total_duration_ms:.2f}ms · AST 순환 복잡도 {sandbox_res.ast_metrics.cyclomatic_complexity}",
                passed=sandbox_res.passed_tests,
                failed=sandbox_res.failed_tests,
                duration_ms=sandbox_res.total_duration_ms,
            )

            diagnosis = self.reviewer.diagnose(
                iteration=iteration,
                spec=spec,
                code=code,
                sandbox_result=sandbox_res,
            )

            run.iterations.append(
                IterationRecord(
                    iteration=iteration,
                    code=code,
                    test_code=test_suite_code,
                    unified_diff=unified_diff,
                    coder_notes=coder_notes,
                    sandbox_result=sandbox_res,
                    diagnosis=diagnosis,
                )
            )

            if sandbox_res.all_passed:
                emit(
                    AgentRole.REVIEWER,
                    PipelineState.VERIFIED,
                    iteration,
                    f"코드 검증 1차 승인 (Iteration #{iteration} — 100% Pass)",
                    diagnosis.root_cause_detail,
                    confidence=diagnosis.confidence,
                )

                if enable_mutation_testing:
                    emit(
                        AgentRole.MUTATOR,
                        PipelineState.MUTATING,
                        iteration,
                        "AST 돌연변이 테스트(Mutation Testing) 개시",
                        "검증된 코드의 AST 연산자(비교·산술·함수호출)를 강제로 변이시켜 QA 테스트 스위트의 허점을 역추적합니다.",
                    )
                    mut_report = self.mutator.run_audit(
                        spec=spec,
                        verified_code=code,
                        sandbox=self.sandbox,
                        auto_harden_qa=True,
                    )
                    run.mutation_report = mut_report

                    if mut_report.initial_survived > 0:
                        survived_ids = ", ".join(
                            r.mutant.mutant_id for r in mut_report.initial_results if r.status == "SURVIVED"
                        )
                        emit(
                            AgentRole.MUTATOR,
                            PipelineState.MUTATING,
                            iteration,
                            f"QA 테스트 허점 발견! 생존 돌연변이 {mut_report.initial_survived}건 ({survived_ids})",
                            f"초기 Mutation Score: {mut_report.initial_score_pct}% ({mut_report.initial_killed}/{mut_report.total_mutants} Killed) → QA 에이전트에 Killer Test 생성을 요청합니다.",
                            initial_score_pct=mut_report.initial_score_pct,
                            survived_count=mut_report.initial_survived,
                        )
                        emit(
                            AgentRole.QA,
                            PipelineState.HARDENING,
                            iteration,
                            f"Killer Test {len(mut_report.killer_tests_added)}건 자동 합성 및 테스트 스위트 보강",
                            f"추가된 Killer Tests: {', '.join(k.name for k in mut_report.killer_tests_added)} → 최종 Mutation Score {mut_report.final_score_pct}% 달성!",
                            final_score_pct=mut_report.final_score_pct,
                            killer_tests=[k.name for k in mut_report.killer_tests_added],
                        )
                    else:
                        emit(
                            AgentRole.MUTATOR,
                            PipelineState.VERIFIED,
                            iteration,
                            f"AST 돌연변이 감사 완벽 통과 (Mutation Score {mut_report.final_score_pct}%)",
                            f"생성된 {mut_report.total_mutants}개 AST 돌연변이가 모두 사살(KILLED)되었습니다.",
                        )

                if enable_profiling:
                    emit(
                        AgentRole.PROFILER,
                        PipelineState.PROFILING,
                        iteration,
                        "Big-O 시간·메모리 복잡도 실측 벤치마크 개시 (N=100 → 800)",
                        "tracemalloc 피크 힙 메모리 및 O(N) vs O(N²) 스케일링 회귀 지수(α)를 실측합니다.",
                    )
                    prof_report = self.profiler.run_profile(spec=spec, verified_code=code)
                    run.profile_report = prof_report
                    emit(
                        AgentRole.PROFILER,
                        PipelineState.VERIFIED,
                        iteration,
                        f"복잡도 검증 통과: {prof_report.empirical_time_class} (최대 {prof_report.max_speedup_factor}x 가속)",
                        f"실측 지수 α={prof_report.scaling_exponent} vs Naive O(N²) α={prof_report.naive_scaling_exponent} · 피크 메모리 {prof_report.peak_memory_kb} KiB",
                        empirical_time_class=prof_report.empirical_time_class,
                        max_speedup_factor=prof_report.max_speedup_factor,
                        peak_memory_kb=prof_report.peak_memory_kb,
                    )

                run.state = PipelineState.VERIFIED
                run.completed_at = time.time()
                return run

            emit(
                AgentRole.REVIEWER,
                PipelineState.DIAGNOSING,
                iteration,
                f"원인 진단 완료: {diagnosis.root_cause_title}",
                f"핵심 수정 지시사항 {len(diagnosis.repair_directives)}건 도출 → Coder 에이전트로 Self-Heal 루프 요청",
                faulty_line=diagnosis.faulty_line_hint,
                directives=diagnosis.repair_directives,
            )

            previous_code = code
            last_diagnosis = diagnosis

            if not auto_heal:
                break

        run.state = PipelineState.FAILED
        run.completed_at = time.time()
        emit(
            AgentRole.REVIEWER,
            PipelineState.FAILED,
            len(run.iterations),
            "최대 반복 횟수 도달 또는 자동 복구 중단",
            f"총 {len(run.iterations)}회 시도 후 미해결 테스트가 남아 있습니다.",
        )
        return run
