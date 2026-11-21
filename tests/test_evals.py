import unittest
import asyncio
from src.evals.drafter_evals import run_drafter_benchmark
from src.evals.runner import run_benchmark


class TestOODEvaluationBenchmark(unittest.TestCase):
    def test_track1_ood_discovery_benchmark_outperforms_baselines(self):
        results = asyncio.run(run_benchmark())
        single_llm_agg = results["single_llm"][0]
        naive_rag_agg = results["naive_rag"][0]
        carthij_agg = results["carthij_current"][0]

        self.assertEqual(carthij_agg.cases_evaluated, 30)
        self.assertGreater(carthij_agg.mean_composite_accuracy, naive_rag_agg.mean_composite_accuracy)
        self.assertGreater(naive_rag_agg.mean_composite_accuracy, single_llm_agg.mean_composite_accuracy)
        self.assertEqual(carthij_agg.mean_url_grounding_rate, 1.0)
        self.assertEqual(carthij_agg.mean_trap_rejection_rate, 1.0)

    def test_track2_browser_drafter_benchmark_outperforms_baseline_and_v1(self):
        results = asyncio.run(run_drafter_benchmark())
        baseline_agg = results["naive_baseline"][0]
        v1_agg = results["carthij_v1"][0]
        v2_agg = results["carthij_v2"][0]

        self.assertEqual(v2_agg.cases_evaluated, 20)
        self.assertGreater(v2_agg.mean_composite_drafter_accuracy, v1_agg.mean_composite_drafter_accuracy)
        self.assertGreater(v1_agg.mean_composite_drafter_accuracy, baseline_agg.mean_composite_drafter_accuracy)
        self.assertEqual(v2_agg.mean_anti_fabrication_rate, 1.0)
        self.assertEqual(v2_agg.mean_revision_recovery_rate, 1.0)
        self.assertEqual(v2_agg.mean_constraint_adherence_rate, 1.0)
        self.assertEqual(v2_agg.mean_safety_gate_score, 1.0)


if __name__ == "__main__":
    unittest.main()
