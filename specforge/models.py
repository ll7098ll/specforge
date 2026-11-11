from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
import time


class AgentRole(str, Enum):
    ARCHITECT = "ARCHITECT"  # PM / Formal Spec Designer
    CODER = "CODER"          # Implementation & Patch Engineer
    QA = "QA"                # Adversarial Test & Property Engineer
    REVIEWER = "REVIEWER"    # AST Static Analyzer & Root-Cause Critic
    SANDBOX = "SANDBOX"      # Isolated Subprocess Test Runner
    MUTATOR = "MUTATOR"      # AST Mutation Testing & Test-Suite Auditor
    PROFILER = "PROFILER"    # Empirical Big-O Time & Memory Complexity Profiler


class PipelineState(str, Enum):
    IDLE = "IDLE"
    SPECIFYING = "SPECIFYING"
    CODING = "CODING"
    TESTING = "TESTING"
    DIAGNOSING = "DIAGNOSING"
    HEALING = "HEALING"
    MUTATING = "MUTATING"
    HARDENING = "HARDENING"
    PROFILING = "PROFILING"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"


@dataclass
class EdgeCaseSpec:
    name: str
    input_repr: str
    expected_repr: str
    category: str  # "normal", "boundary", "adversarial", "invariant", "killer"
    rationale: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SpecContract:
    mission_id: str
    title: str
    natural_prompt: str
    function_name: str
    signature: str
    docstring: str
    invariants: List[str]
    edge_cases: List[EdgeCaseSpec]
    complexity_target: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ASTMetrics:
    line_count: int
    function_count: int
    branch_count: int
    loop_count: int
    cyclomatic_complexity: int
    has_type_hints: bool
    security_warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TestCaseResult:
    name: str
    category: str
    passed: bool
    duration_ms: float
    input_summary: str
    expected_summary: str
    actual_summary: str
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    traceback_snippet: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SandboxRunResult:
    iteration: int
    all_passed: bool
    total_tests: int
    passed_tests: int
    failed_tests: int
    total_duration_ms: float
    stdout: str
    stderr: str
    test_results: List[TestCaseResult]
    ast_metrics: ASTMetrics

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DiagnosisReport:
    iteration: int
    severity: str  # "CRITICAL", "WARNING", "CLEAN"
    root_cause_title: str
    root_cause_detail: str
    faulty_line_hint: Optional[int]
    faulty_snippet: Optional[str]
    repair_directives: List[str]
    confidence: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MutantSpec:
    mutant_id: str
    operator: str          # e.g., "CR (<= -> <)", "AOR (- -> +)", "LCR (or -> and)", "CALL (min -> max)"
    lineno: int
    original_line: str
    mutated_line: str
    mutated_code: str
    unified_diff: str
    description: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MutantEvalResult:
    mutant: MutantSpec
    status: str            # "KILLED", "SURVIVED", "TIMEOUT"
    killed_by_test: Optional[str]
    duration_ms: float
    survival_analysis: Optional[str] = None
    killer_test_name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mutant": self.mutant.to_dict(),
            "status": self.status,
            "killed_by_test": self.killed_by_test,
            "duration_ms": self.duration_ms,
            "survival_analysis": self.survival_analysis,
            "killer_test_name": self.killer_test_name,
        }


@dataclass
class MutationReport:
    total_mutants: int
    initial_killed: int
    initial_survived: int
    initial_score_pct: float
    final_killed: int
    final_survived: int
    final_score_pct: float
    total_duration_ms: float
    initial_results: List[MutantEvalResult]
    killer_tests_added: List[EdgeCaseSpec]
    hardened_results: List[MutantEvalResult]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_mutants": self.total_mutants,
            "initial_killed": self.initial_killed,
            "initial_survived": self.initial_survived,
            "initial_score_pct": self.initial_score_pct,
            "final_killed": self.final_killed,
            "final_survived": self.final_survived,
            "final_score_pct": self.final_score_pct,
            "total_duration_ms": self.total_duration_ms,
            "initial_results": [r.to_dict() for r in self.initial_results],
            "killer_tests_added": [k.to_dict() for k in self.killer_tests_added],
            "hardened_results": [r.to_dict() for r in self.hardened_results],
        }


@dataclass
class ScalePointMetric:
    n: int
    optimized_ms: float
    naive_quadratic_ms: float
    speedup_factor: float
    peak_memory_kb: float
    ops_per_sec: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ComplexityProfileReport:
    empirical_time_class: str     # e.g., "O(N)" or "O(N log N)"
    naive_time_class: str         # e.g., "O(N²)"
    scaling_exponent: float       # log-log slope alpha (~1.0 for linear, ~2.0 for quadratic)
    naive_scaling_exponent: float
    space_complexity_class: str   # e.g., "O(N)" or "O(K)"
    max_speedup_factor: float
    peak_memory_kb: float
    sla_passed: bool
    bottleneck_eliminated: str
    scale_points: List[ScalePointMetric]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "empirical_time_class": self.empirical_time_class,
            "naive_time_class": self.naive_time_class,
            "scaling_exponent": self.scaling_exponent,
            "naive_scaling_exponent": self.naive_scaling_exponent,
            "space_complexity_class": self.space_complexity_class,
            "max_speedup_factor": self.max_speedup_factor,
            "peak_memory_kb": self.peak_memory_kb,
            "sla_passed": self.sla_passed,
            "bottleneck_eliminated": self.bottleneck_eliminated,
            "scale_points": [sp.to_dict() for sp in self.scale_points],
        }


@dataclass
class IterationRecord:
    iteration: int
    code: str
    test_code: str
    unified_diff: str
    coder_notes: str
    sandbox_result: SandboxRunResult
    diagnosis: Optional[DiagnosisReport] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AgentEvent:
    step_id: int
    timestamp: float
    role: AgentRole
    state: PipelineState
    iteration: int
    title: str
    message: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["role"] = self.role.value
        d["state"] = self.state.value
        return d


@dataclass
class PipelineRun:
    run_id: str
    mission_id: str
    state: PipelineState
    spec: SpecContract
    max_iterations: int
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    iterations: List[IterationRecord] = field(default_factory=list)
    events: List[AgentEvent] = field(default_factory=list)
    mutation_report: Optional[MutationReport] = None
    profile_report: Optional[ComplexityProfileReport] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "mission_id": self.mission_id,
            "state": self.state.value,
            "spec": self.spec.to_dict(),
            "max_iterations": self.max_iterations,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "total_duration_ms": round(
                ((self.completed_at or time.time()) - self.started_at) * 1000, 2
            ),
            "iterations": [it.to_dict() for it in self.iterations],
            "events": [ev.to_dict() for ev in self.events],
            "mutation_report": self.mutation_report.to_dict() if self.mutation_report else None,
            "profile_report": self.profile_report.to_dict() if self.profile_report else None,
        }
