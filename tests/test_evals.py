import unittest
import asyncio
from src.evals.runner import run_benchmark


class TestOODEvaluationBenchmark(unittest.TestCase):
    def test_ood_evaluation_benchmark_outperforms_baselines(self):
        results = asyncio.run(run_benchmark())
        single_llm_agg = results["single_llm"][0]
        naive_rag_agg = results["naive_rag"][0]
        carthij_agg = results["carthij_current"][0]

        self.assertEqual(carthij_agg.cases_evaluated, 30)
        self.assertGreater(carthij_agg.mean_composite_accuracy, naive_rag_agg.mean_composite_accuracy)
        self.assertGreater(naive_rag_agg.mean_composite_accuracy, single_llm_agg.mean_composite_accuracy)
        self.assertEqual(carthij_agg.mean_url_grounding_rate, 1.0)
        self.assertEqual(carthij_agg.mean_trap_rejection_rate, 1.0)


if __name__ == "__main__":
    unittest.main()
