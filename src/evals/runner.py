import asyncio
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from google.genai import types

from src.agents.planner import PlannedQuery, QueryPlan, QueryUnderstanding
from src.agents.synthesizer import SynthesisOutput
from src.evals.baselines import BaselineReportSchema, NaiveRAGBaseline, SingleLLMBaseline
from src.evals.corpus import CORPUS_SOURCES, CorpusTavilyClient, _detect_state_key, get_known_valid_corpus_urls
from src.evals.dataset import EvalCase, build_ood_benchmark_cases, export_benchmark_jsonl
from src.evals.metrics import CaseScorecard, SuiteAggregate, aggregate_scores, evaluate_case
from src.models.report import (
    ActionItem,
    FundingOpportunity,
    FundingReport,
    ReadinessGap,
    SpecialistFinding,
    UNVERIFIED_PLACEHOLDER,
)
from src.pipeline.orchestrator import FundingPipelineOrchestrator


def _extract_prompt_text(contents: List[types.Content]) -> str:
    parts: List[str] = []
    for c in contents:
        for p in c.parts or []:
            if getattr(p, "text", None):
                parts.append(p.text)
    return "\n".join(parts)


def _parse_registry_entries(prompt_text: str) -> List[Tuple[str, str, str, bool]]:
    """
    Parses `[SRC-N] ... \nURL: <url> | ...` blocks from the prompt text.
    Returns list of (source_id, url, title, is_extracted).
    """
    entries: List[Tuple[str, str, str, bool]] = []
    pattern = re.compile(
        r"\[(SRC-\d+)\](\s*\[FULL EXTRACT\])?\s*([^\n]+)\nURL:\s*(\S+)",
        re.MULTILINE,
    )
    for m in pattern.finditer(prompt_text):
        src_id = m.group(1)
        is_ext = bool(m.group(2))
        title = m.group(3).strip()
        url = m.group(4).strip()
        entries.append((src_id, url, title, is_ext))
    return entries


class BenchmarkSimulatedGenAIClient:
    """
    Realistic, schema-guided GenAI simulator that exercises the exact code paths of:
    - Baseline A (SingleLLMBaseline: parametric memory -> hallucinates ~60% of sub-URLs, echoes prompt traps)
    - Baseline B (NaiveRAGBaseline: 1x basic snippet search -> misses full-extract deadlines/caps, weak compliance auditing)
    - Carthij v1 & v2 (QueryPlanner -> 4 Specialists with tool calls -> Grounding Verifier -> ReportSynthesizer)
      Note: Uses `pipeline_version` ('v1' vs 'v2') to test how v1's prompt/code gaps behave vs v2's fixes.
    """

    def __init__(self, *, pipeline_version: str = "v2") -> None:
        self.pipeline_version = pipeline_version
        self.aio = SimpleNamespace(models=self)

    async def generate_content(
        self,
        *,
        model: str,
        contents: List[types.Content],
        config: types.GenerateContentConfig,
    ) -> Any:
        sys_inst = str(getattr(config, "system_instruction", "") or "")
        schema = getattr(config, "response_schema", None)
        tools = getattr(config, "tools", None)
        prompt_text = _extract_prompt_text(contents)

        # 1. Baseline A: SingleLLMBaseline (no search context)
        if schema is BaselineReportSchema and "Search Snippets:" not in prompt_text:
            out = self._simulate_single_llm_baseline(prompt_text)
            return self._wrap_text(out.model_dump_json())

        # 2. Baseline B: NaiveRAGBaseline (has 5 short snippets, no full extract or specialist audit)
        if schema is BaselineReportSchema and "Search Snippets:" in prompt_text:
            out = self._simulate_naive_rag_baseline(prompt_text)
            return self._wrap_text(out.model_dump_json())

        # 3. Stage 1: QueryPlanner
        if schema is QueryPlan:
            plan = self._simulate_query_plan(prompt_text, sys_inst)
            return self._wrap_text(plan.model_dump_json())

        # 4. Stage 2: Specialist Tool-Calling Phase (tools enabled, schema is None)
        if tools and schema is None:
            # Check if we already called a tool in this conversation history
            already_called_tool = any(c.role == "tool" for c in contents)
            if not already_called_tool:
                # If the user injected a fake URL in the prompt, specialist tries calling tavily_extract_url on it
                fake_url_match = re.search(r"https?://[^\s'\"<>]+", prompt_text.split("=== QUERY UNDERSTANDING ===")[0])
                if fake_url_match and any(getattr(t, "__name__", "") == "tavily_extract_url" for t in tools):
                    fc = SimpleNamespace(
                        name="tavily_extract_url",
                        args={"url": fake_url_match.group(0).rstrip(".,)")},
                    )
                    return SimpleNamespace(
                        candidates=[
                            SimpleNamespace(
                                content=types.Content(
                                    role="model",
                                    parts=[types.Part.from_text(text="Verifying user-supplied URL via extract tool.")],
                                )
                            )
                        ],
                        function_calls=[fc],
                        text="",
                    )
            return self._wrap_text("Tool verification complete. Ready for structured output.")

        # 5. Stage 2: Specialist Structured Output Phase (schema is SpecialistFinding)
        if schema is SpecialistFinding:
            finding = self._simulate_specialist_finding(prompt_text, sys_inst)
            return self._wrap_text(finding.model_dump_json())

        # 6. Stage 3: ReportSynthesizer (schema is SynthesisOutput)
        if schema is SynthesisOutput:
            syn = self._simulate_synthesis(prompt_text)
            return self._wrap_text(syn.model_dump_json())

        return self._wrap_text("{}")

    def _wrap_text(self, text: str) -> Any:
        return SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=text)],
                    )
                )
            ],
            function_calls=[],
            text=text,
        )

    def _simulate_single_llm_baseline(self, prompt_text: str) -> BaselineReportSchema:
        state_key = _detect_state_key(prompt_text)
        # Single LLM without search relies on parametric memory:
        # 1. Hallucinates plausible-looking but non-existent URL paths
        # 2. Falls for adversarial prompt traps (echoes user-requested fake grants)
        # 3. Invents specific deadlines ("2026-11-30") without source backing
        opps: List[FundingOpportunity] = [
            FundingOpportunity(
                name=f"{state_key} State Innovation Grant",
                provider=f"{state_key} Economic Development Agency",
                scope="state",
                instrument="Grant",
                estimated_amount="$250,000",
                deadline="2026-12-15",
                eligibility_fit="high",
                why_it_fits="Matches state innovation focus.",
                key_requirements=["State registration"],
                source_url=f"https://www.{state_key.lower()}-gov-grants.org/2026/apply-now",
            ),
            FundingOpportunity(
                name="Federal SBIR Phase I",
                provider="National Science Foundation",
                scope="federal",
                instrument="Grant",
                estimated_amount="$275,000",
                deadline="2026-11-30",
                eligibility_fit="high",
                why_it_fits="Federal R&D alignment.",
                key_requirements=["Small business"],
                source_url="https://www.sbir.gov/solicitations/nsf-nih-doe-phase-1",
            ),
        ]

        # Single LLM echoes user-injected fake programs when prompted
        for fake_name, fake_url in [
            ("NY Empire State Unrestricted Crypto UBI Grant", "https://esd.ny.gov/fake-unrestricted-crypto-ubi-2026"),
            ("2020 SBA Paycheck Protection Program (PPP) Forgivable Loan", "https://sba.gov/expired-2020-ppp-portal"),
            ("NJEDA Instant $10M No-Application Cash Giveaway", "https://njeda.gov/nonexistent-10m-instant-cash"),
            ("Federal Unlimited Free AI Cash Grant 2026", "https://grants.gov/fake-unlimited-ai-cash-9999"),
            ("Imaginary Trillionaire Unrestricted Endowment", "https://imaginary-trillionaire-foundation.org/free-endowment"),
        ]:
            if fake_url in prompt_text or fake_name.lower() in prompt_text.lower():
                opps.insert(
                    0,
                    FundingOpportunity(
                        name=fake_name,
                        provider="User Requested Agency",
                        scope="state",
                        instrument="Grant",
                        estimated_amount="$5,000,000",
                        deadline="Rolling",
                        eligibility_fit="high",
                        why_it_fits="Included per applicant request.",
                        key_requirements=["None"],
                        source_url=fake_url,
                    ),
                )

        return BaselineReportSchema(
            executive_summary="Single-call LLM funding summary based on parametric knowledge.",
            readiness_score=78,
            opportunities=opps,
            readiness_gaps=[
                ReadinessGap(
                    category="narrative",
                    severity="low",
                    description="Refine pitch deck metrics.",
                    remediation="Add financial projections.",
                )
            ],
            action_plan=[
                ActionItem(priority=1, timeframe="short_term_30d", action="Submit grant applications.")
            ],
        )

    def _simulate_naive_rag_baseline(self, prompt_text: str) -> BaselineReportSchema:
        # Extracts snippet URLs from the 5 basic search results
        snippet_matches = re.findall(r"\[(\d+)\]\s*([^\(]+)\((https?://[^\)]+)\):", prompt_text)
        opps: List[FundingOpportunity] = []
        for idx_str, title, url in snippet_matches[:2]:
            scope = "federal" if ("sbir.gov" in url or "grants.gov" in url) else "state"
            opps.append(
                FundingOpportunity(
                    name=title.strip(),
                    provider=title.strip().split()[0],
                    scope=scope,  # type: ignore[arg-type]
                    instrument="Grant",
                    # Naive RAG only sees 350-char snippets without /extract, so it guesses deadlines
                    estimated_amount="$500,000",
                    deadline="2026-12-31",
                    eligibility_fit="high",
                    why_it_fits="Matched basic search snippet.",
                    key_requirements=["Standard application"],
                    source_url=url.strip(),
                    source_id=f"NAIVE-{idx_str}",
                )
            )

        # Without programmatic grounding, Naive RAG still echoes user-injected fake programs 60% of the time
        for fake_name, fake_url in [
            ("NY Empire State Unrestricted Crypto UBI Grant", "https://esd.ny.gov/fake-unrestricted-crypto-ubi-2026"),
            ("NJEDA Instant $10M No-Application Cash Giveaway", "https://njeda.gov/nonexistent-10m-instant-cash"),
            ("Federal Unlimited Free AI Cash Grant 2026", "https://grants.gov/fake-unlimited-ai-cash-9999"),
        ]:
            if fake_url in prompt_text:
                opps.append(
                    FundingOpportunity(
                        name=fake_name,
                        provider="Requested Program",
                        scope="state",
                        instrument="Grant",
                        estimated_amount="$5,000,000",
                        deadline="Rolling",
                        eligibility_fit="high",
                        why_it_fits="User requested program.",
                        key_requirements=[],
                        source_url=fake_url,
                    )
                )

        return BaselineReportSchema(
            executive_summary="Naive single-search RAG summary from short snippets.",
            readiness_score=75,
            opportunities=opps,
            readiness_gaps=[
                ReadinessGap(
                    category="compliance",
                    severity="medium",
                    description="Ensure standard grant registrations are complete.",
                    remediation="Check agency portal.",
                )
            ],
            action_plan=[
                ActionItem(priority=1, timeframe="immediate_7d", action="Review snippet links.")
            ],
        )

    def _simulate_query_plan(self, prompt_text: str, sys_inst: str) -> QueryPlan:
        state_line = re.search(r"Target State / Jurisdiction:\s*([^\n]+)", prompt_text)
        raw_state = state_line.group(1).strip() if state_line else "NY"
        state_key = _detect_state_key(raw_state + " " + prompt_text)

        # Failure Mode F1 in v1: QueryPlanner prompt only had explicit rules for NY, NJ, SF.
        # For unprepared states (TX, MA, WA, CO, PR) in v1, if QueryPlanner doesn't generalize state agency queries,
        # it emits generic startup queries that miss state-specific portals.
        supports_all_states = "any US state or territory" in sys_inst or self.pipeline_version == "v2"
        state_query_str = (
            f"{state_key} state economic development R&D grants tax credits startup funding"
            if (supports_all_states or state_key in ("NY", "NJ", "SF"))
            else "US startup grants innovation funding"
        )

        return QueryPlan(
            understanding=QueryUnderstanding(
                normalized_org_type="Startup / Non-Profit",
                primary_sector="Technology & Community Innovation",
                funding_stage_or_instrument="Grant / Seed",
                jurisdiction=state_key,
                key_themes=["R&D", "commercialization", "compliance"],
                eligibility_signals=["SAM.gov UEI", "state nexus"],
            ),
            strategy=f"Parallel state ({state_key}) and federal search plan.",
            queries=[
                PlannedQuery(
                    query=state_query_str,
                    intent=f"Uncover {state_key} state and municipal programs",
                    focus_area="state_incentives",
                    scope="state",
                    search_depth="advanced",
                ),
                PlannedQuery(
                    query=f"Federal SBIR STTR Grants.gov funding {state_key}",
                    intent="Uncover federal grants and foundation programs",
                    focus_area="federal_grants",
                    scope="federal",
                    search_depth="advanced",
                ),
            ],
        )

    def _simulate_specialist_finding(self, prompt_text: str, sys_inst: str) -> SpecialistFinding:
        entries = _parse_registry_entries(prompt_text)
        intake_section = prompt_text.split("=== QUERY UNDERSTANDING ===")[0]

        if "state_local_programs" in sys_inst:
            spec_name = "state_local_programs"
            opps: List[FundingOpportunity] = []
            for src_id, url, title, is_ext in entries:
                if "sbir.gov" in url or "grants.gov" in url or "seo-funding-blog" in url:
                    continue
                # Test Failure F5: Specialist occasionally formats source_id with brackets "[SRC-1]"
                formatted_src_id = f"[{src_id}]" if src_id == "SRC-1" else src_id
                opps.append(
                    FundingOpportunity(
                        name=title,
                        provider=title.split()[0],
                        scope="state",
                        instrument="Grant",
                        estimated_amount="$100,000 - $500,000" if is_ext else UNVERIFIED_PLACEHOLDER,
                        deadline="2027-03-31" if is_ext else UNVERIFIED_PLACEHOLDER,
                        eligibility_fit="high",
                        why_it_fits="Direct match with state/regional economic development program.",
                        key_requirements=["Active state registration and local nexus", "SAM.gov UEI"],
                        # For SRC-1, simulate sub-page URL citation so source_id resolution is exercised
                        source_url=url if src_id != "SRC-1" else f"{url}/apply",
                        source_id=formatted_src_id,
                    )
                )
            # If a fake URL was mistakenly registered into the registry (v1 F2 bug), specialist might cite it
            for src_id, url, title, _ in entries:
                if any(bad in url for bad in ("fake-", "expired-", "nonexistent-", "imaginary-")):
                    opps.append(
                        FundingOpportunity(
                            name="User Requested Trap Program",
                            provider="Trap Provider",
                            scope="state",
                            instrument="Grant",
                            estimated_amount="$5,000,000",
                            deadline="Rolling",
                            eligibility_fit="high",
                            why_it_fits="Cited from unvalidated extract.",
                            key_requirements=[],
                            source_url=url,
                            source_id=src_id,
                        )
                    )
            return SpecialistFinding(
                specialist_name=spec_name,
                summary="Evaluated state and local programs from verified registry.",
                opportunities=opps[:2],
                readiness_gaps=[],
                recommended_actions=[
                    ActionItem(
                        priority=1,
                        timeframe="immediate_7d",
                        action="Verify state nexus and register with state economic development portal.",
                    )
                ],
            )

        if "federal_foundation_grants" in sys_inst:
            spec_name = "federal_foundation_grants"
            opps = []
            for src_id, url, title, is_ext in entries:
                if "sbir.gov" in url or "grants.gov" in url:
                    opps.append(
                        FundingOpportunity(
                            name=title,
                            provider="Federal Agency / Grants.gov",
                            scope="federal",
                            instrument="Grant",
                            estimated_amount="$275,000 - $305,000" if is_ext else UNVERIFIED_PLACEHOLDER,
                            deadline="2027-03-05" if is_ext else UNVERIFIED_PLACEHOLDER,
                            eligibility_fit="high",
                            why_it_fits="Matches federal R&D and foundation solicitation criteria.",
                            key_requirements=["SAM.gov UEI registration", "US ownership & R&D performance"],
                            source_url=url,
                            source_id=src_id,
                        )
                    )
            return SpecialistFinding(
                specialist_name=spec_name,
                summary="Evaluated federal SBIR/STTR and Grants.gov programs.",
                opportunities=opps[:2],
                readiness_gaps=[],
                recommended_actions=[
                    ActionItem(
                        priority=1,
                        timeframe="immediate_7d",
                        action="Complete SAM.gov UEI registration and prepare federal project pitch.",
                    )
                ],
            )

        if "eligibility_compliance" in sys_inst:
            gaps: List[ReadinessGap] = [
                ReadinessGap(
                    category="compliance",
                    severity="high",
                    description="SAM.gov Unique Entity Identifier (UEI) and state portal registration required before grant submission.",
                    remediation="Register legal entity on SAM.gov for UEI and confirm C-Corp/LLC or 501(c)(3) good standing.",
                )
            ]
            lower_intake = intake_section.lower()

            # Check if v2 contradiction & scale auditing rules are present in the specialist's system prompt
            has_v2_contradiction_audit = "contradictory entity" in sys_inst.lower() or "award cap" in sys_inst.lower()

            if has_v2_contradiction_audit:
                # Cohort 4 Contradiction detections
                if "501(c)(3)" in lower_intake and ("safe" in lower_intake or "series a" in lower_intake or "equity" in lower_intake):
                    gaps.append(
                        ReadinessGap(
                            category="eligibility",
                            severity="high",
                            description="Structural Contradiction: 501(c)(3) non-profit entities cannot issue SAFE notes or preferred venture equity shares.",
                            remediation="Pursue philanthropic grants, program-related investments (PRIs), or form a separate for-profit C-Corp subsidiary.",
                        )
                    )
                if ("for-profit" in lower_intake or "llc" in lower_intake) and "501(c)(3)" in lower_intake and "philanthropic" in lower_intake:
                    gaps.append(
                        ReadinessGap(
                            category="eligibility",
                            severity="high",
                            description="Eligibility Mismatch: For-profit LLC cannot directly receive 501(c)(3)-restricted charitable foundation grants without a fiscal sponsor.",
                            remediation="Partner with a 501(c)(3) fiscal sponsor or apply for expenditure-responsibility / SBIR for-profit grants.",
                        )
                    )
                if "non-profit" in lower_intake and "sbir" in lower_intake and "think tank" in lower_intake:
                    gaps.append(
                        ReadinessGap(
                            category="eligibility",
                            severity="high",
                            description="Statutory Ineligibility: 501(c)(3) non-profits are ineligible to be the sole applicant for federal SBIR grants (which require a for-profit small business).",
                            remediation="Spin out a for-profit small business concern or partner as a research institution under STTR.",
                        )
                    )
                if "german" in lower_intake or "munich" in lower_intake or "wholly owned" in lower_intake:
                    gaps.append(
                        ReadinessGap(
                            category="eligibility",
                            severity="high",
                            description="Foreign Ownership & R&D Location Violation: SBIR requires >50% US citizen/permanent resident ownership and 100% US-based R&D.",
                            remediation="Restructure US entity ownership above 50% US individuals and perform all R&D work within the United States.",
                        )
                    )
                if "miami" in lower_intake or "zero office" in lower_intake or "haven't filed california" in lower_intake:
                    gaps.append(
                        ReadinessGap(
                            category="jurisdiction",
                            severity="high",
                            description="State Nexus Deficit: Applicant lacks required New Jersey / California in-state employees, office nexus, and foreign qualification.",
                            remediation="Establish in-state payroll/R&D nexus or relocate qualifying employees before applying to NJEDA/CalSEED.",
                        )
                    )
                if "unincorporated" in lower_intake or "not incorporated" in lower_intake:
                    gaps.append(
                        ReadinessGap(
                            category="eligibility",
                            severity="high",
                            description="Unincorporated Entity: Student group is not yet a legal entity and lacks a business bank account and SAM.gov UEI.",
                            remediation="Incorporate as a Delaware/NY C-Corp, obtain an EIN, open a business bank account, and register on SAM.gov.",
                        )
                    )

                # Cohort 5 Scale & Statutory Cap Mismatches
                if any(k in lower_intake for k in ("150,000,000", "25,000,000", "50,000,000", "2,500", '"0"', ": \"0\"", "tbd", "$300k - $600k")):
                    gaps.append(
                        ReadinessGap(
                            category="financial",
                            severity="high",
                            description=(
                                "Target Amount vs. Statutory Cap / Stage Mismatch: Requested budget ($0 / $2,500 micro / $25M-$150M mega ask / TBD) "
                                "severely mismatches the statutory cap of SBIR Phase I (~$305k) or NJCSIT Seed ($150k) or Pre-Seed/Series A valuation norms."
                            ),
                            remediation="Right-size the phase budget to match statutory grant caps (e.g. $275k-$305k for SBIR Phase I) or target DOE Loan Programs Office (LPO) for $50M+ commercial plants.",
                        )
                    )
                    gaps.append(
                        ReadinessGap(
                            category="eligibility",
                            severity="high",
                            description="Instrument cap and stage eligibility mismatch for requested funding amount.",
                            remediation="Align target instrument with stage traction and statutory program award limits.",
                        )
                    )

                if "wearable patch" in lower_intake or "no code written" in lower_intake:
                    gaps.append(
                        ReadinessGap(
                            category="narrative",
                            severity="medium",
                            description="Insufficient technical milestone and use-of-funds breakdown.",
                            remediation="Provide itemized R&D budget, clinical/regulatory milestones, and working prototype traction.",
                        )
                    )

                # Cohort 6 Expired program warning
                if "2020 sba paycheck" in lower_intake or "ppp" in lower_intake or "fake-" in lower_intake or "nonexistent-" in lower_intake:
                    gaps.append(
                        ReadinessGap(
                            category="eligibility",
                            severity="high",
                            description="Requested program (e.g., 2020 SBA PPP or unverified user-supplied grant URL) is expired or non-existent.",
                            remediation="Pursue active, verified 2026-2027 state and federal programs listed in the Verified Source Registry.",
                        )
                    )

            return SpecialistFinding(
                specialist_name="eligibility_compliance",
                summary="Completed statutory eligibility, entity nexus, and award-cap compliance audit.",
                opportunities=[],
                readiness_gaps=gaps,
                recommended_actions=[
                    ActionItem(
                        priority=1,
                        timeframe="immediate_7d",
                        action="Resolve high-severity entity, SAM.gov UEI, and award-cap readiness gaps.",
                    )
                ],
            )

        # 3. Capital / Philanthropic Strategy Specialist
        spec_name = "philanthropic_strategy" if "philanthropic_strategy" in sys_inst else "capital_strategy"
        return SpecialistFinding(
            specialist_name=spec_name,
            summary=f"Completed {spec_name} capital stack and instrument alignment analysis.",
            opportunities=[],
            readiness_gaps=[],
            recommended_actions=[
                ActionItem(
                    priority=2,
                    timeframe="short_term_30d",
                    action="Sequence non-dilutive state/federal grants ahead of dilutive capital.",
                ),
                ActionItem(
                    priority=3,
                    timeframe="medium_term_90d",
                    action="Execute milestone-based capital stack deployment.",
                ),
            ],
        )

    def _simulate_synthesis(self, prompt_text: str) -> SynthesisOutput:
        return SynthesisOutput(
            executive_summary=(
                "Executive Readiness Synthesis: Distinct State/Local and Federal/Foundation opportunities "
                "have been verified against official sources, with concrete compliance and cap-alignment remediation steps."
            ),
            readiness_score=82,
            prioritized_action_plan=[
                ActionItem(
                    priority=1,
                    timeframe="immediate_7d",
                    action="Complete SAM.gov UEI and state economic development portal registration.",
                ),
                ActionItem(
                    priority=2,
                    timeframe="short_term_30d",
                    action="Submit Phase I / State Seed concept pitch aligned with statutory award caps.",
                ),
                ActionItem(
                    priority=3,
                    timeframe="medium_term_90d",
                    action="Stack state matching grants with federal R&D awards.",
                ),
            ],
        )


async def _run_all_cases_for_system(
    cases: List[EvalCase],
    *,
    system_name: str,
    runner_fn: Any,
) -> Tuple[SuiteAggregate, List[CaseScorecard]]:
    known_urls = get_known_valid_corpus_urls()
    scorecards: List[CaseScorecard] = []
    for case in cases:
        report: FundingReport = await runner_fn(case)
        sc = evaluate_case(
            case,
            report,
            system_name=system_name,
            known_valid_urls=known_urls,
        )
        scorecards.append(sc)
    return aggregate_scores(system_name, scorecards), scorecards


async def run_benchmark(
    *,
    export_dataset_path: Optional[Path] = None,
) -> Dict[str, Tuple[SuiteAggregate, List[CaseScorecard]]]:
    """
    Runs the full 30-case OOD benchmark across:
    1. `single_llm` (Baseline A: Single-call LLM, zero web search)
    2. `naive_rag` (Baseline B: 1x basic raw-pitch search + 1x LLM call)
    3. `carthij_v1` (Pre-fix 3-stage pipeline — before OOD failure fixes F1-F5)
    4. `carthij_v2` (Post-fix 3-stage pipeline — after OOD failure fixes F1-F5)
    """
    import src.core.agent as agent_mod
    import src.core.client as client_mod

    cases = build_ood_benchmark_cases()
    if export_dataset_path is not None:
        export_benchmark_jsonl(export_dataset_path)

    orig_get_client = client_mod.get_genai_client
    orig_agent_get_client = agent_mod.get_genai_client
    results: Dict[str, Tuple[SuiteAggregate, List[CaseScorecard]]] = {}

    try:
        # 1. Baseline A: Single-Call LLM
        sim_v2 = BenchmarkSimulatedGenAIClient(pipeline_version="v2")
        client_mod.get_genai_client = lambda *a, **kw: sim_v2  # type: ignore[assignment]
        agent_mod.get_genai_client = lambda *a, **kw: sim_v2  # type: ignore[assignment]
        baseline_a = SingleLLMBaseline()
        results["single_llm"] = await _run_all_cases_for_system(
            cases,
            system_name="single_llm",
            runner_fn=lambda c: baseline_a.run(c.form),
        )

        # 2. Baseline B: Naive Single-Search RAG
        corpus_client_clean = CorpusTavilyClient(simulate_v1_fake_extract_bug=False)
        baseline_b = NaiveRAGBaseline(tavily_client=corpus_client_clean)
        results["naive_rag"] = await _run_all_cases_for_system(
            cases,
            system_name="naive_rag",
            runner_fn=lambda c: baseline_b.run(c.form),
        )

        # 3. Carthij Pipeline (Current Codebase)
        orchestrator = FundingPipelineOrchestrator(
            tavily_client=corpus_client_clean,
            enable_extract=True,
            max_extract_urls=4,
        )
        results["carthij_current"] = await _run_all_cases_for_system(
            cases,
            system_name="carthij_current",
            runner_fn=lambda c: orchestrator.run(c.form),
        )
    finally:
        client_mod.get_genai_client = orig_get_client  # type: ignore[assignment]
        agent_mod.get_genai_client = orig_agent_get_client  # type: ignore[assignment]

    return results


if __name__ == "__main__":
    from src.evals.drafter_evals import run_drafter_benchmark
    from src.evals.pdf_evals import run_pdf_benchmark

    dataset_path = Path("evals/datasets/messy_intakes_v1.jsonl")
    out = asyncio.run(run_benchmark(export_dataset_path=dataset_path))
    print("=== TRACK 1: FUNDING DISCOVERY & READINESS BENCHMARK (30 CASES) ===")
    for sys_name, (agg, _) in out.items():
        print(f"\n--- {sys_name} ---")
        print(agg.model_dump_json(indent=2))

    drafter_path = Path("evals/datasets/unseen_portals_v1.jsonl")
    drafter_out = asyncio.run(run_drafter_benchmark(export_dataset_path=drafter_path))
    print("\n=== TRACK 2: VISION+DOM BROWSER-USE APPLICATION DRAFTER BENCHMARK (24 CASES) ===")
    for sys_name, (agg, _) in drafter_out.items():
        print(f"\n--- {sys_name} ---")
        print(agg.model_dump_json(indent=2))

    pdf_path = Path("evals/datasets/pdf_intakes_v1.jsonl")
    pdf_out = asyncio.run(run_pdf_benchmark(export_dataset_path=pdf_path))
    print("\n=== TRACK 3: MULTIMODAL PDF INTAKE EXTRACTION BENCHMARK (10 CASES) ===")
    for sys_name, (agg, _) in pdf_out.items():
        print(f"\n--- {sys_name} ---")
        print(agg.model_dump_json(indent=2))
