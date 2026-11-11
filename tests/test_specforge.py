from __future__ import annotations

import unittest

from specforge.models import PipelineState
from specforge.orchestrator import SpecForgeOrchestrator


class TestSpecForgePipeline(unittest.TestCase):
    def setUp(self) -> None:
        self.orch = SpecForgeOrchestrator(timeout_seconds=0.5)

    def test_token_bucket_limiter_self_heals_in_3_iterations(self) -> None:
        run = self.orch.run_pipeline(
            "token_bucket_limiter", enable_mutation_testing=False, enable_profiling=False
        )
        self.assertEqual(run.state, PipelineState.VERIFIED)
        self.assertEqual(len(run.iterations), 3)
        self.assertFalse(run.iterations[0].sandbox_result.all_passed)
        self.assertFalse(run.iterations[1].sandbox_result.all_passed)
        self.assertTrue(run.iterations[2].sandbox_result.all_passed)
        self.assertIn("@@", run.iterations[1].unified_diff)
        self.assertIn("@@", run.iterations[2].unified_diff)

    def test_dependency_toposort_self_heals_in_3_iterations(self) -> None:
        run = self.orch.run_pipeline(
            "dependency_toposort", enable_mutation_testing=False, enable_profiling=False
        )
        self.assertEqual(run.state, PipelineState.VERIFIED)
        self.assertEqual(len(run.iterations), 3)
        self.assertTrue(run.iterations[-1].sandbox_result.all_passed)

    def test_circuit_breaker_fsm_self_heals_in_3_iterations(self) -> None:
        run = self.orch.run_pipeline(
            "circuit_breaker_fsm", enable_mutation_testing=False, enable_profiling=False
        )
        self.assertEqual(run.state, PipelineState.VERIFIED)
        self.assertEqual(len(run.iterations), 3)
        self.assertTrue(run.iterations[-1].sandbox_result.all_passed)

    def test_financial_ledger_reconcile_self_heals_in_2_iterations(self) -> None:
        run = self.orch.run_pipeline(
            "financial_ledger_reconcile", enable_mutation_testing=False, enable_profiling=False
        )
        self.assertEqual(run.state, PipelineState.VERIFIED)
        self.assertEqual(len(run.iterations), 2)
        self.assertTrue(run.iterations[-1].sandbox_result.all_passed)

    def test_security_ast_gate_blocks_banned_imports(self) -> None:
        malicious_code = """\
import subprocess
def evaluate_rate_limit(events, capacity, refill_rate):
    return []
"""
        run = self.orch.run_pipeline(
            "token_bucket_limiter",
            custom_initial_code=malicious_code,
            auto_heal=False,
        )
        self.assertEqual(run.state, PipelineState.FAILED)
        self.assertFalse(run.iterations[0].sandbox_result.all_passed)
        self.assertTrue(len(run.iterations[0].sandbox_result.ast_metrics.security_warnings) > 0)

    def test_custom_buggy_code_auto_heals_on_iteration_2(self) -> None:
        broken_code = """\
def evaluate_rate_limit(events, capacity, refill_rate):
    return [(False, 0.0) for _ in events]
"""
        run = self.orch.run_pipeline(
            "token_bucket_limiter",
            custom_initial_code=broken_code,
            auto_heal=True,
        )
        self.assertEqual(run.state, PipelineState.VERIFIED)
        self.assertEqual(len(run.iterations), 2)
        self.assertFalse(run.iterations[0].sandbox_result.all_passed)
        self.assertTrue(run.iterations[1].sandbox_result.all_passed)

    def test_ast_mutator_discovers_survived_mutants_and_qa_kills_them(self) -> None:
        for mission_id in (
            "token_bucket_limiter",
            "dependency_toposort",
            "circuit_breaker_fsm",
            "financial_ledger_reconcile",
        ):
            with self.subTest(mission_id=mission_id):
                report = self.orch.run_mutation_only(mission_id=mission_id, auto_harden_qa=True)
                self.assertGreaterEqual(report.total_mutants, 4)
                self.assertGreaterEqual(report.initial_survived, 1)
                self.assertLess(report.initial_score_pct, 100.0)
                self.assertGreaterEqual(len(report.killer_tests_added), 1)
                self.assertEqual(report.final_survived, 0)
                self.assertEqual(report.final_score_pct, 100.0)

    def test_profiler_empirical_big_o_and_memory_scaling(self) -> None:
        for mission_id in (
            "token_bucket_limiter",
            "dependency_toposort",
            "circuit_breaker_fsm",
            "financial_ledger_reconcile",
        ):
            with self.subTest(mission_id=mission_id):
                prof = self.orch.run_profile_only(mission_id=mission_id)
                self.assertEqual(len(prof.scale_points), 4)
                self.assertTrue(prof.sla_passed)
                self.assertGreater(prof.max_speedup_factor, 2.0)
                self.assertGreater(prof.peak_memory_kb, 0.0)


if __name__ == "__main__":
    unittest.main()
