from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from .models import AgentEvent, AgentRole, PipelineRun
from .orchestrator import SpecForgeOrchestrator


ANSI = {
    "RESET": "\033[0m",
    "BOLD": "\033[1m",
    "DIM": "\033[2m",
    "CYAN": "\033[38;5;81m",
    "GREEN": "\033[38;5;78m",
    "AMBER": "\033[38;5;214m",
    "RED": "\033[38;5;203m",
    "BLUE": "\033[38;5;75m",
    "GRAY": "\033[38;5;245m",
}

ROLE_COLOR = {
    AgentRole.ARCHITECT: ANSI["CYAN"],
    AgentRole.CODER: ANSI["BLUE"],
    AgentRole.QA: ANSI["AMBER"],
    AgentRole.SANDBOX: ANSI["GRAY"],
    AgentRole.REVIEWER: ANSI["GREEN"],
    AgentRole.MUTATOR: ANSI["RED"],
    AgentRole.PROFILER: ANSI["CYAN"],
}


def print_banner() -> None:
    c = ANSI["CYAN"]
    b = ANSI["BOLD"]
    r = ANSI["RESET"]
    d = ANSI["DIM"]
    print(f"{c}{b}╭──────────────────────────────────────────────────────────────────────────────────────╮{r}")
    print(f"{c}{b}│  SPECFORGE :: Autonomous 6-Agent Factory, AST Mutation Arena & Big-O Profiler (TUI)  │{r}")
    print(f"{c}{b}╰──────────────────────────────────────────────────────────────────────────────────────╯{r}")
    print(f"{d}  Roles: [ARCHITECT] -> [QA] -> [CODER] -> [SANDBOX] -> [REVIEWER] -> [MUTATOR] -> [PROFILER]{r}\n")


def render_event(ev: AgentEvent) -> None:
    color = ROLE_COLOR.get(ev.role, ANSI["CYAN"])
    b = ANSI["BOLD"]
    r = ANSI["RESET"]
    d = ANSI["DIM"]
    iter_tag = f"Iter #{ev.iteration}" if ev.iteration > 0 else "Setup  "
    print(f"  {d}[Step {ev.step_id:02d} | {iter_tag}]{r} {color}{b}[{ev.role.value:<9}]{r} {b}{ev.title}{r}")
    print(f"                     {d}└─ {ev.message}{r}")


def render_run_summary(run: PipelineRun) -> None:
    b = ANSI["BOLD"]
    r = ANSI["RESET"]
    g = ANSI["GREEN"]
    red = ANSI["RED"]
    amb = ANSI["AMBER"]
    c = ANSI["CYAN"]
    d = ANSI["DIM"]

    print(f"\n{b}━━━ Phase 1: Self-Healing Code Synthesis ({run.spec.title}) ━━━{r}")
    for it in run.iterations:
        sb = it.sandbox_result
        status_str = f"{g}PASS ({sb.passed_tests}/{sb.total_tests}){r}" if sb.all_passed else f"{red}FAIL ({sb.passed_tests}/{sb.total_tests}){r}"
        print(
            f"\n  {b}● Iteration #{it.iteration}{r} :: {status_str} "
            f"{d}[{sb.total_duration_ms:.2f}ms | AST CC={sb.ast_metrics.cyclomatic_complexity} | LOC={sb.ast_metrics.line_count}]{r}"
        )
        for tr in sb.test_results:
            icon = f"{g}✓{r}" if tr.passed else f"{red}✗{r}"
            detail = "" if tr.passed else f" -> {red}{tr.error_type}: {tr.error_message}{r}"
            print(f"    {icon} [{tr.category:<11}] {tr.name:<44} ({tr.duration_ms:.2f}ms){detail}")

        if it.unified_diff:
            print(f"\n    {amb}{b}┌── Self-Healing Unified Diff (Iter #{it.iteration - 1} -> #{it.iteration}) ──┐{r}")
            for dline in it.unified_diff.splitlines():
                if dline.startswith("+") and not dline.startswith("+++"):
                    print(f"    {g}{dline}{r}")
                elif dline.startswith("-") and not dline.startswith("---"):
                    print(f"    {red}{dline}{r}")
                else:
                    print(f"    {d}{dline}{r}")

        if it.diagnosis and not sb.all_passed:
            print(f"    {amb}⚡ Reviewer Diagnosis:{r} {it.diagnosis.root_cause_title}")
            for directive in it.diagnosis.repair_directives:
                print(f"       {d}• {directive}{r}")

    if run.mutation_report:
        mr = run.mutation_report
        print(f"\n{b}━━━ Phase 2: AST Mutation Testing Arena & QA Hardening ({mr.total_mutants} Mutants) ━━━{r}")
        print(
            f"  {d}Initial Mutation Score:{r} {amb}{b}{mr.initial_score_pct}%{r} ({mr.initial_killed}/{mr.total_mutants} Killed, {red}{mr.initial_survived} Survived{r}) "
            f"-> {d}After QA Killer Tests:{r} {g}{b}{mr.final_score_pct}%{r} ({mr.final_killed}/{mr.total_mutants} Killed)"
        )
        for res in mr.initial_results:
            m = res.mutant
            if res.status == "SURVIVED":
                badge = f"{red}{b}SURVIVED ✗{r}"
                who = f"{amb}Blind Spot! Killed after hardening by -> {res.killer_test_name}{r}"
            else:
                badge = f"{g}KILLED   ✓{r}"
                who = f"{d}caught by {res.killed_by_test}{r}"
            print(f"    [{m.mutant_id}] {badge} | {c}{m.operator:<18}{r} (L{m.lineno:02d}) `{m.original_line}` -> `{m.mutated_line}`")
            print(f"         └─ {who}")
            if res.survival_analysis:
                print(f"         └─ {amb}왜 생존했는가:{r} {d}{res.survival_analysis}{r}")

        if mr.killer_tests_added:
            print(f"\n  {c}{b}✚ QAAgent Auto-Synthesized Killer Tests ({len(mr.killer_tests_added)} added):{r}")
            for kt in mr.killer_tests_added:
                print(f"    {g}• {kt.name}{r}: {kt.rationale}")
                print(f"      {d}input={kt.input_repr} -> expected={kt.expected_repr}{r}")

    if run.profile_report:
        pr = run.profile_report
        print(f"\n{b}━━━ Phase 3: Empirical Big-O Complexity & Memory Benchmark (tracemalloc) ━━━{r}")
        print(
            f"  {d}Verified Complexity:{r} {g}{b}{pr.empirical_time_class}{r} (slope α={pr.scaling_exponent}) "
            f"vs {d}Naive Bottleneck:{r} {red}{b}{pr.naive_time_class}{r} (slope α={pr.naive_scaling_exponent}) "
            f"| {d}Max Speedup:{r} {c}{b}{pr.max_speedup_factor}x{r} | {d}Peak Heap:{r} {pr.peak_memory_kb} KiB"
        )
        print(f"  {d}Optimization Insight:{r} {pr.bottleneck_eliminated}")
        for sp in pr.scale_points:
            print(
                f"    • N={sp.n:<4} | Verified {g}{sp.optimized_ms:6.3f}ms{r} vs Naive O(N²) {red}{sp.naive_quadratic_ms:7.3f}ms{r} "
                f"({c}{b}{sp.speedup_factor:5.1f}x faster{r}) | Heap={sp.peak_memory_kb:5.1f} KiB | {sp.ops_per_sec:,} ops/s"
            )

    final_color = g if run.state.value == "VERIFIED" else red
    total_ms = ((run.completed_at or run.started_at) - run.started_at) * 1000.0
    mut_str = f"Mutation: {run.mutation_report.final_score_pct:5.1f}%" if run.mutation_report else "Mutation: N/A"
    prof_str = f"Big-O: {run.profile_report.empirical_time_class} ({run.profile_report.max_speedup_factor}x)" if run.profile_report else ""
    print(
        f"\n{final_color}{b}╭──────────────────────────────────────────────────────────────────────────────────────╮{r}"
    )
    print(
        f"{final_color}{b}│  STATE: {run.state.value:<8} | Iters: {len(run.iterations)} | {mut_str} | {prof_str:<20} | {total_ms:6.1f}ms  │{r}"
    )
    print(
        f"{final_color}{b}╰──────────────────────────────────────────────────────────────────────────────────────╯{r}\n"
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="SpecForge Autonomous Multi-Agent CLI/TUI")
    parser.add_argument(
        "--mission",
        default="token_bucket_limiter",
        help="Mission ID (token_bucket_limiter, dependency_toposort, circuit_breaker_fsm, financial_ledger_reconcile)",
    )
    parser.add_argument("--all", action="store_true", help="Run all 4 engineering missions sequentially")
    parser.add_argument("--json", action="store_true", help="Output structured JSON")
    args = parser.parse_args(argv)

    orch = SpecForgeOrchestrator()
    if args.all:
        runs = [orch.run_pipeline(str(m["mission_id"])) for m in orch.list_missions()]
        if args.json:
            print(json.dumps([r.to_dict() for r in runs], ensure_ascii=False, indent=2))
            return 0
        print_banner()
        for r in runs:
            for ev in r.events:
                render_event(ev)
            render_run_summary(r)
        return 0

    if args.json:
        run = orch.run_pipeline(mission_id=args.mission)
        print(json.dumps(run.to_dict(), ensure_ascii=False, indent=2))
        return 0

    print_banner()
    run = orch.run_pipeline(mission_id=args.mission, on_event=render_event)
    render_run_summary(run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
