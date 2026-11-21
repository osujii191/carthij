import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List
from pydantic import BaseModel

from src.agents.pdf_extractor import PDFIntakeExtractor
from src.drafter.browser import ExtractedFormSchema, FormButtonSpec, FormFieldSpec
from src.drafter.mapper import FormMapperAgent, build_applicant_fact_ledger
from src.drafter.orchestrator import BrowserDraftOrchestrator
from src.evals.baselines import NaiveRAGBaseline, SingleLLMBaseline
from src.evals.corpus import CorpusTavilyClient, get_known_valid_corpus_urls
from src.evals.dataset import EvalCase, build_ood_benchmark_cases
from src.evals.drafter_evals import (
    CaseBrowserDriver,
    DrafterEvalCase,
    _execute_drafter_case,
    build_drafter_benchmark_cases,
)
from src.evals.metrics import CaseScorecard, aggregate_scores, evaluate_case
from src.evals.pdf_evals import build_pdf_benchmark_cases, decode_pdf_base64
from src.pipeline.orchestrator import FundingPipelineOrchestrator

CANDIDATE_MODELS = [
    "gemini-3.8-flash",
    "gemini-4-hydrogen",
    "gemini-4-neon",
    "gemini-4-argon",
    "gemini-4-carbon",
]

# Representative 6-case slice (1 from each of the 6 Track 1 OOD cohorts)
REPRESENTATIVE_CASE_IDS = {
    "ctrl_ny_climatetech_01",
    "ood_state_tx_01",
    "messy_ocr_deck_01",
    "trap_nonprofit_asking_vc_safe_01",
    "scale_150m_sbir_mismatch_01",
    "adv_fake_esd_crypto_grant_01",
}


class ModelLiveBenchmarkSummary(BaseModel):
    model: str
    track1_composite_accuracy: float
    track1_url_grounding_rate: float
    track1_trap_rejection_rate: float
    track1_compliance_catch_rate: float
    track1_jurisdiction_coverage: float
    track1_uncertainty_honesty_rate: float
    track1_rubric_specificity: float
    track1_mean_latency_ms: int
    track2_drafter_accuracy: float
    track2_anti_fabrication_rate: float
    track2_latency_ms: int
    track3_pdf_extraction_ok: bool
    track3_pdf_org_name: str
    track3_pdf_latency_ms: int


async def run_live_model_comparison(
    models: List[str] = CANDIDATE_MODELS,
) -> Dict[str, Any]:
    all_cases = build_ood_benchmark_cases()
    rep_cases = [c for c in all_cases if c.case_id in REPRESENTATIVE_CASE_IDS]
    known_urls = get_known_valid_corpus_urls()
    tavily_corpus = CorpusTavilyClient(simulate_v1_fake_extract_bug=False)

    # First run SingleLLMBaseline & NaiveRAGBaseline live on gemini-3.8-flash for a real live baseline anchor
    print("Running Live Baselines (SingleLLMBaseline & NaiveRAGBaseline on gemini-3.8-flash)...")
    live_single_llm = SingleLLMBaseline(model="gemini-3.8-flash")
    live_naive_rag = NaiveRAGBaseline(model="gemini-3.8-flash", tavily_client=tavily_corpus)

    single_cards: List[CaseScorecard] = []
    naive_cards: List[CaseScorecard] = []
    for case in rep_cases:
        r_single = await live_single_llm.run(case.form)
        single_cards.append(
            evaluate_case(case, r_single, system_name="live_single_llm", known_valid_urls=known_urls)
        )
        r_naive = await live_naive_rag.run(case.form)
        naive_cards.append(
            evaluate_case(case, r_naive, system_name="live_naive_rag", known_valid_urls=known_urls)
        )

    single_agg = aggregate_scores("live_single_llm", single_cards)
    naive_agg = aggregate_scores("live_naive_rag", naive_cards)
    print("Live SingleLLMBaseline:", single_agg.model_dump_json(indent=2))
    print("Live NaiveRAGBaseline:", naive_agg.model_dump_json(indent=2))

    # Drafter test case (Cohort 3 required EIN/UEI trap + Cohort 2 maxlength)
    drafter_cases = [
        c
        for c in build_drafter_benchmark_cases()
        if c.case_id in ("portal_std_01_ny", "portal_trap_01_required_ein_uei")
    ]
    # PDF test case (NY ClimateTech pitch deck with embedded EIN & UEI)
    pdf_case = build_pdf_benchmark_cases()[0]
    pdf_bytes = decode_pdf_base64(pdf_case.pdf_base64)

    model_summaries: List[ModelLiveBenchmarkSummary] = []

    for model_name in models:
        print(f"\n=== Benchmarking Live Model: {model_name} ===")
        orch = FundingPipelineOrchestrator(
            model=model_name,
            tavily_client=tavily_corpus,
            enable_extract=True,
            max_extract_urls=4,
        )

        t1_cards: List[CaseScorecard] = []
        latencies: List[int] = []

        async def _eval_one_t1(case: EvalCase) -> Tuple[CaseScorecard, int]:
            t0 = time.time()
            rep = await orch.run(case.form)
            ms = int((time.time() - t0) * 1000)
            sc = evaluate_case(case, rep, system_name=model_name, known_valid_urls=known_urls)
            return sc, ms

        # Run the 6 representative cases in parallel (2 batches of 3 to avoid rate limits)
        for i in range(0, len(rep_cases), 3):
            batch = rep_cases[i : i + 3]
            batch_out = await asyncio.gather(*(_eval_one_t1(c) for c in batch))
            for sc, ms in batch_out:
                t1_cards.append(sc)
                latencies.append(ms)
                print(
                    f"  [{model_name}] {sc.case_id} ({sc.cohort}): composite={sc.composite_accuracy:.2f}, latency={ms}ms, notes={sc.failure_notes}"
                )

        t1_agg = aggregate_scores(model_name, t1_cards)

        # Track 2: Live FormMapperAgent + BrowserDraftOrchestrator
        t2_start = time.time()
        t2_scores: List[float] = []
        t2_anti_fab: List[float] = []
        for dcase in drafter_cases:
            driver = CaseBrowserDriver(dcase)
            live_mapper = FormMapperAgent(model=model_name)
            d_orch = BrowserDraftOrchestrator(
                model=model_name,
                browser_driver=driver,
                mapper=live_mapper,
            )
            draft = await d_orch.run(
                report=dcase.report,
                opportunity_name=dcase.report.opportunities[0].name,
                opportunity_source_url=dcase.report.opportunities[0].source_url,
                target_portal_url=dcase.target_portal_url,
            )
            sens_set = set(dcase.sensitive_selectors)
            non_sens = [f for f in draft.filled_fields if f.selector not in sens_set]
            sens = [f for f in draft.filled_fields if f.selector in sens_set]
            fill_acc = sum(1 for f in non_sens if f.status == "filled" and f.value) / max(1, len(non_sens))
            anti_fab = sum(1 for f in sens if f.status == "skipped_missing" and f.value is None) / max(1, len(sens))
            t2_scores.append(0.5 * fill_acc + 0.5 * anti_fab)
            t2_anti_fab.append(anti_fab)
        t2_ms = int((time.time() - t2_start) * 1000) // max(1, len(drafter_cases))

        # Track 3: Live Multimodal PDFIntakeExtractor
        t3_start = time.time()
        try:
            pdf_ext = PDFIntakeExtractor(model=model_name)
            prof = await pdf_ext.extract_from_bytes(pdf_bytes, filename=pdf_case.filename)
            t3_ok = "hudsongrid" in prof.organization_name.lower() and prof.target_state == "NY"
            t3_org = prof.organization_name
        except Exception as exc:
            print(f"  [{model_name}] PDF extraction error: {exc}")
            t3_ok = False
            t3_org = f"ERROR: {exc}"
        t3_ms = int((time.time() - t3_start) * 1000)

        summary = ModelLiveBenchmarkSummary(
            model=model_name,
            track1_composite_accuracy=t1_agg.mean_composite_accuracy,
            track1_url_grounding_rate=t1_agg.mean_url_grounding_rate,
            track1_trap_rejection_rate=t1_agg.mean_trap_rejection_rate,
            track1_compliance_catch_rate=t1_agg.mean_compliance_catch_rate,
            track1_jurisdiction_coverage=t1_agg.mean_jurisdiction_coverage,
            track1_uncertainty_honesty_rate=t1_agg.mean_uncertainty_honesty_rate,
            track1_rubric_specificity=t1_agg.mean_rubric_specificity,
            track1_mean_latency_ms=sum(latencies) // max(1, len(latencies)),
            track2_drafter_accuracy=round(sum(t2_scores) / max(1, len(t2_scores)), 4),
            track2_anti_fabrication_rate=round(sum(t2_anti_fab) / max(1, len(t2_anti_fab)), 4),
            track2_latency_ms=t2_ms,
            track3_pdf_extraction_ok=t3_ok,
            track3_pdf_org_name=t3_org,
            track3_pdf_latency_ms=t3_ms,
        )
        model_summaries.append(summary)
        print(f"Summary for {model_name}:\n{summary.model_dump_json(indent=2)}")

    result_payload = {
        "live_single_llm_baseline": single_agg.model_dump(),
        "live_naive_rag_baseline": naive_agg.model_dump(),
        "models": [m.model_dump() for m in model_summaries],
    }
    out_path = Path("evals/datasets/live_model_matrix_results.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result_payload, indent=2), encoding="utf-8")
    return result_payload


if __name__ == "__main__":
    from typing import Tuple
    asyncio.run(run_live_model_comparison())
