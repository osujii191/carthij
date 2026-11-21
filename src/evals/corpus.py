import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Set, Tuple
from google.genai import types

from src.agents.planner import PlannedQuery, QueryPlan, QueryUnderstanding
from src.agents.synthesizer import SynthesisOutput
from src.core.tavily import (
    TavilyClient,
    TavilyExtractRequest,
    TavilyExtractResponse,
    TavilyExtractResult,
    TavilyFailedExtract,
    TavilySearchRequest,
    TavilySearchResponse,
    TavilySearchResult,
    TavilyUsage,
)
from src.evals.baselines import BaselineReportSchema, NaiveRAGBaseline, SingleLLMBaseline
from src.evals.dataset import EvalCase, build_ood_benchmark_cases, export_benchmark_jsonl
from src.evals.metrics import CaseScorecard, SuiteAggregate, aggregate_scores, evaluate_case
from src.models.report import (
    ActionItem,
    FundingOpportunity,
    ReadinessGap,
    SpecialistFinding,
    UNVERIFIED_PLACEHOLDER,
)
from src.pipeline.orchestrator import FundingPipelineOrchestrator
from src.pipeline.registry import AUTHORITATIVE_DOMAINS


# ============================================================================
# Curated Multi-Jurisdiction Web Corpus for Reproducible OOD Evaluation
# ============================================================================

CORPUS_SOURCES: Dict[str, Dict[str, str]] = {
    "NY": {
        "state_url": "https://esd.ny.gov/nyserda-clean-energy-and-nystar-grants",
        "state_title": "Empire State Development (ESD) & NYSERDA Innovation Grants (NY)",
        "state_snippet": "New York ESD, NYSTAR, and NYSERDA provide non-dilutive R&D and clean energy grants for NY-registered entities.",
        "state_extract": (
            "Full Program Guidelines: NYSERDA & ESD Innovation Grants. "
            "Award Range: $100,000 - $500,000. Application Deadline: 2027-03-31 (Quarterly Rolling). "
            "Eligibility: Must be registered to do business in New York State with NY nexus and active SAM.gov UEI."
        ),
        "local_url": "https://edc.nyc/programs/nyc-workforce-and-founder-grants",
        "local_title": "NYCEDC Green Economy & Workforce Development Grants",
        "local_snippet": "NYCEDC supports NYC startups and 501(c)(3) non-profits in clean energy, biotech, and workforce training.",
        "local_extract": (
            "NYCEDC Community & Innovation Grants. Award Range: $50,000 - $300,000. "
            "Deadline: 2027-02-15. Requires NYC operational presence."
        ),
    },
    "NJ": {
        "state_url": "https://www.njeda.gov/njcsit-catalyst-seed-grant",
        "state_title": "NJEDA & NJCSIT Catalyst R&D Seed Grant Program (NJ)",
        "state_snippet": "Commission on Science, Innovation and Technology (NJCSIT) seed grants and NJ Angel Investor Tax Credit.",
        "state_extract": (
            "NJCSIT Catalyst R&D Seed Grant Official Rules. "
            "Statutory Award Cap: $75,000 - $150,000 maximum per applicant. Deadline: 2027-04-15. "
            "Strict Requirement: Applicant must have NJ business registration, >=50% employees or R&D nexus in New Jersey."
        ),
        "local_url": "https://www.nj.gov/health/maternal-community-grants",
        "local_title": "New Jersey Department of Health & RWJF Community Grants",
        "local_snippet": "Grants for New Jersey 501(c)(3) maternal health and community equity initiatives.",
        "local_extract": (
            "NJ Maternal Health & RWJF Collaborative Grants. Award Range: $100,000 - $250,000. Deadline: Rolling."
        ),
    },
    "SF": {
        "state_url": "https://calseed.fund/apply-california-clean-energy",
        "state_title": "CalSEED & California Competes (GO-Biz) Innovation Funding",
        "state_snippet": "CalSEED awards up to $150k Concept and $450k Prototype grants for California clean energy startups.",
        "state_extract": (
            "CalSEED & GO-Biz California Competes Program Rules. "
            "Award Range: $150,000 - $600,000. Deadline: 2027-05-01. "
            "Requires active California Secretary of State registration and California facility nexus."
        ),
        "local_url": "https://www.sf.gov/oewd-san-francisco-business-grants",
        "local_title": "San Francisco OEWD & Bay Area Innovation Grants",
        "local_snippet": "SF Office of Economic and Workforce Development (OEWD) municipal grants.",
        "local_extract": "SF OEWD Grants. Award Range: $25,000 - $150,000. Deadline: Rolling.",
    },
    "TX": {
        "state_url": "https://cprit.texas.gov/funding-opportunities/product-development-research",
        "state_title": "CPRIT Texas Product Development Research Grants (TX)",
        "state_snippet": "Cancer Prevention and Research Institute of Texas (CPRIT) funds Texas-based oncology therapeutics startups.",
        "state_extract": (
            "CPRIT Texas Company Product Development Award. "
            "Award Range: $500,000 - $3,000,000. Deadline: 2027-01-30. "
            "Requires Texas headquarters or clinical/R&D operations in Texas and 1:2 matching funds."
        ),
        "local_url": "https://gov.texas.gov/business/page/texas-enterprise-and-tmac-grants",
        "local_title": "Texas Office of the Governor & TMAC Manufacturing Innovation",
        "local_snippet": "State of Texas commercialization and manufacturing incentives.",
        "local_extract": "Texas Innovation & TMAC Program. Award Range: $100,000 - $500,000. Deadline: Rolling.",
    },
    "MA": {
        "state_url": "https://www.massventures.com/start-program-grants",
        "state_title": "MassVentures START & Massachusetts Life Sciences Center (MLSC) Grants",
        "state_snippet": "MassVentures and MLSC provide non-dilutive grants to Massachusetts deep-tech and robotics startups.",
        "state_extract": (
            "MassVentures START & MLSC Bits-to-Bytes Program. "
            "Award Range: $100,000 - $500,000. Deadline: 2027-03-15. "
            "Must be located in the Commonwealth of Massachusetts."
        ),
        "local_url": "https://www.masscec.com/clean-energy-and-tech-catalyst",
        "local_title": "Massachusetts Clean Energy Center (MassCEC) Catalyst Program",
        "local_snippet": "MassCEC prototype grants for Massachusetts tough-tech startups.",
        "local_extract": "MassCEC Catalyst Awards: up to $130,000. Deadline: 2027-04-01.",
    },
    "WA": {
        "state_url": "https://www.commerce.wa.gov/growing-the-economy/clean-energy-fund",
        "state_title": "Washington State Department of Commerce Clean Energy Fund (WA)",
        "state_snippet": "Washington Clean Energy Fund supports maritime electrification, green hydrogen, and grid R&D in WA.",
        "state_extract": (
            "Washington State Clean Energy Fund R&D Demonstration Grants. "
            "Award Range: $250,000 - $1,000,000. Deadline: 2027-02-28. "
            "Requires Washington State registration and Port/utility partnership."
        ),
        "local_url": "https://www.wrfseattle.org/grants/washington-research-foundation",
        "local_title": "Washington Research Foundation (WRF) Technology Commercialization Grants",
        "local_snippet": "WRF supports Washington-based clean tech and life science commercialization.",
        "local_extract": "WRF Phase 1 & 2 Commercialization Grants: $100,000 - $500,000. Deadline: Rolling.",
    },
    "CO": {
        "state_url": "https://oedit.colorado.gov/advanced-industries-early-stage-capital-and-retention-grant",
        "state_title": "Colorado OEDIT Advanced Industries Early-Stage Capital Grant (CO)",
        "state_snippet": "Colorado Office of Economic Development & International Trade (OEDIT) funds quantum, photonics, and deep-tech.",
        "state_extract": (
            "Colorado OEDIT Advanced Industries Grant. "
            "Award Cap: Up to $250,000 non-dilutive grant. Deadline: 2027-02-27. "
            "Requires Colorado headquarters and 1:2 matching requirement."
        ),
        "local_url": "https://elevatequantum.org/colorado-quantum-incubator-grants",
        "local_title": "Elevate Quantum Colorado Regional Tech Hub Grants",
        "local_snippet": "Colorado quantum sensing and photonics commercialization support.",
        "local_extract": "Elevate Quantum Lab Access & Seed Awards: $50,000 - $150,000. Deadline: Rolling.",
    },
    "PR": {
        "state_url": "https://prsciencetrust.org/research-grants-program-puerto-rico",
        "state_title": "Puerto Rico Science, Technology & Research Trust (PRSTRT) & DDEC Grants",
        "state_snippet": "PRSTRT and Puerto Rico DDEC provide R&D, microgrid, and innovation grants in Puerto Rico.",
        "state_extract": (
            "PRSTRT Advanced Research & Microgrid Resilience Grants (Puerto Rico). "
            "Award Range: $150,000 - $450,000. Deadline: 2027-04-10. "
            "Eligible for Puerto Rico non-profits, cooperatives, and startups."
        ),
        "local_url": "https://www.investpr.org/incentives-and-energy-resilience",
        "local_title": "Invest Puerto Rico & DOE Puerto Rico Energy Resilience Fund",
        "local_snippet": "Community solar and battery microgrid incentives for rural clinics in Puerto Rico.",
        "local_extract": "Puerto Rico Energy Resilience Fund (PR-ERF): $100,000 - $500,000. Deadline: Rolling.",
    },
    "FEDERAL": {
        "sbir_url": "https://www.sbir.gov/solicitations/nsf-nih-doe-phase-1",
        "sbir_title": "Federal SBIR / STTR Phase I Solicitation (NSF, NIH, DOE)",
        "sbir_snippet": "Federal SBIR Phase I grants provide R&D funding for US-owned for-profit small businesses (<500 employees).",
        "sbir_extract": (
            "Official Federal SBIR/STTR Phase I Rules: "
            "Statutory Phase I Award Cap: $275,000 - $305,000. Deadline: 2027-03-05. "
            "Strict Statutory Eligibility: Must be an incorporated FOR-PROFIT business (501(c)(3) non-profits are INELIGIBLE for SBIR), "
            ">50% owned and controlled by US citizens/permanent residents, with all R&D performed in the United States, "
            "and active SAM.gov UEI registration."
        ),
        "grants_url": "https://www.grants.gov/federal-clean-energy-and-community-grants",
        "grants_title": "Grants.gov Federal Foundation, DOE LPO & Community Resilience Programs",
        "grants_snippet": "Federal grants for 501(c)(3) non-profits, community cooperatives, and DOE clean energy infrastructure.",
        "grants_extract": (
            "Grants.gov Federal Programs & National Foundations. "
            "Award Range: $100,000 - $2,000,000 (or DOE LPO loans for $50M+ commercial scale). "
            "Deadline: 2027-06-01. Requires SAM.gov UEI and 501(c)(3) determination for charitable grants."
        ),
    },
}


def get_known_valid_corpus_urls() -> Set[str]:
    urls: Set[str] = set()
    for group in CORPUS_SOURCES.values():
        for k, v in group.items():
            if k.endswith("_url"):
                urls.add(v)
    return urls


def _detect_state_key(text: str) -> str:
    upper = (text or "").upper()
    for code, aliases in [
        ("TX", ["TX", "TEXAS", "HOUSTON", "AUSTIN", "CPRIT"]),
        ("MA", ["MA", "MASSACHUSETTS", "SOMERVILLE", "BOSTON", "MASSVENTURES"]),
        ("WA", ["WA", "WASHINGTON", "SEATTLE", "TACOMA", "PUGET"]),
        ("CO", ["CO", "COLORADO", "BOULDER", "OEDIT"]),
        ("PR", ["PR", "PUERTO RICO", "BORINQUEN", "PRSTRT"]),
        ("NJ", ["NJ", "NEW JERSEY", "RUTGERS", "NEWARK", "PRINCETON", "HOBOKEN", "NJEDA"]),
        ("SF", ["SF", "SAN FRANCISCO", "CALIFORNIA", "BAY AREA", "HALF MOON BAY", "CALSEED"]),
        ("NY", ["NY", "NEW YORK", "NYC", "BRONX", "BROOKLYN", "BUFFALO", "HARLEM", "MANHATTAN", "QUEENS"]),
    ]:
        for alias in aliases:
            if re.search(rf"\b{re.escape(alias)}\b", upper):
                return code
    return "NY"


class CorpusTavilyClient(TavilyClient):
    """
    Deterministic multi-jurisdiction Tavily simulator that serves realistic search snippets
    and full extracted page markdown across all 8 jurisdictions (NY, NJ, SF, TX, MA, WA, CO, PR)
    while returning 404/failed_results on adversarial fake URLs.
    """

    def __init__(self, *, simulate_v1_fake_extract_bug: bool = False) -> None:
        super().__init__(api_key="")
        self.simulate_v1_fake_extract_bug = simulate_v1_fake_extract_bug
        self._extract_map: Dict[str, str] = {}
        for group in CORPUS_SOURCES.values():
            if "state_url" in group:
                self._extract_map[group["state_url"]] = group["state_extract"]
                self._extract_map[group["local_url"]] = group["local_extract"]
            if "sbir_url" in group:
                self._extract_map[group["sbir_url"]] = group["sbir_extract"]
                self._extract_map[group["grants_url"]] = group["grants_extract"]

    async def _execute_request(
        self,
        client: Any,
        request: TavilySearchRequest,
    ) -> TavilySearchResponse:
        state_key = _detect_state_key(request.query)
        st = CORPUS_SOURCES.get(state_key, CORPUS_SOURCES["NY"])
        fed = CORPUS_SOURCES["FEDERAL"]

        results = [
            TavilySearchResult(
                title=st["state_title"],
                url=st["state_url"],
                content=st["state_snippet"],
                score=0.88,
                published_date="2026-09-15",
            ),
            TavilySearchResult(
                title=st["local_title"],
                url=st["local_url"],
                content=st["local_snippet"],
                score=0.81,
                published_date="2026-08-20",
            ),
            TavilySearchResult(
                title=fed["sbir_title"],
                url=fed["sbir_url"],
                content=fed["sbir_snippet"],
                score=0.86,
                published_date="2026-10-01",
            ),
            TavilySearchResult(
                title=fed["grants_title"],
                url=fed["grants_url"],
                content=fed["grants_snippet"],
                score=0.80,
                published_date="2026-07-12",
            ),
            # Include a higher-scoring generic SEO blog to test whether authority ranking picks official portals over blogs
            TavilySearchResult(
                title=f"Top 10 Startup Grants Blog Post ({state_key})",
                url=f"https://seo-funding-blog.example.com/{state_key.lower()}-grants-list",
                content="Generic blog post listing startup grants without official deadlines or caps.",
                score=0.90,
                published_date="2026-05-01",
            ),
        ]
        return TavilySearchResponse(
            query=request.query,
            results=results[: request.max_results],
            answer=f"Verified state ({state_key}) and federal funding programs.",
            usage=TavilyUsage(credits=1),
            status_code=200,
        )

    async def extract(
        self,
        urls: Any,
        **kwargs: Any,
    ) -> TavilyExtractResponse:
        url_list = urls.urls if isinstance(urls, TavilyExtractRequest) else list(urls)
        results: List[TavilyExtractResult] = []
        failed: List[TavilyFailedExtract] = []

        for u in url_list:
            clean = u.strip()
            if clean in self._extract_map:
                results.append(
                    TavilyExtractResult(url=clean, raw_content=self._extract_map[clean])
                )
            elif "seo-funding-blog.example.com" in clean:
                results.append(
                    TavilyExtractResult(
                        url=clean,
                        raw_content="Generic blog summary with no verified deadlines or award caps.",
                    )
                )
            elif self.simulate_v1_fake_extract_bug:
                # Reproduces Failure F2 in v1: TavilyClient._simulated_extract_response returned simulated
                # markdown for ANY URL when offline, allowing user-injected fake URLs to enter the registry!
                results.append(
                    TavilyExtractResult(
                        url=clean,
                        raw_content=f"Simulated extracted markdown content for {clean}.",
                    )
                )
            else:
                failed.append(
                    TavilyFailedExtract(url=clean, error="HTTP 404 Not Found: URL does not exist")
                )

        return TavilyExtractResponse(
            results=results,
            failed_results=failed,
            usage=TavilyUsage(credits=1),
            status_code=200,
        )
