from src.evals.baselines import NaiveRAGBaseline, SingleLLMBaseline
from src.evals.dataset import EvalCase, ExpectedEvalSignals, build_ood_benchmark_cases
from src.evals.metrics import CaseScorecard, SuiteAggregate, evaluate_case, aggregate_scores


def __getattr__(name: str):
    if name == "run_benchmark":
        from src.evals.runner import run_benchmark

        return run_benchmark
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "EvalCase",
    "ExpectedEvalSignals",
    "build_ood_benchmark_cases",
    "SingleLLMBaseline",
    "NaiveRAGBaseline",
    "CaseScorecard",
    "SuiteAggregate",
    "evaluate_case",
    "aggregate_scores",
    "run_benchmark",
]
