from dataclasses import dataclass
from typing import List, Optional

from src.agents.specialists.tools import BoundedTavilyToolSuite
from src.core.agent import BaseAgent
from src.core.tavily import TavilyClient
from src.models.report import SpecialistFinding, UNVERIFIED_PLACEHOLDER
from src.pipeline.registry import RetrievedSourceRegistry


GROUNDING_RULES = (
    "\n\nStrict Grounding & Citation Rules:\n"
    "1. Every FundingOpportunity you output MUST cite a real source from the Verified Source Registry "
    "(or a source returned by your follow-up Tavily tool). Copy the exact `source_url` and `source_id` (e.g. 'SRC-1').\n"
    "2. Never invent program URLs, deadlines, or award caps.\n"
    f"3. If a deadline or award amount is not explicitly stated in the source text, set it to '{UNVERIFIED_PLACEHOLDER}'.\n"
    "4. Only use your follow-up Tavily tool if a critical deadline, cap, or eligibility requirement is missing from the registry."
)

STATE_GUIDANCE = {
    "NY": (
        "Focus on New York State and NYC programs: Empire State Development (ESD), NYSTAR, "
        "NYSERDA, NYC Economic Development Corporation (NYCEDC), Excelsior Jobs Program, and regional incubators."
    ),
    "NJ": (
        "Focus on New Jersey state and local programs: NJEDA, NJCSIT Catalyst R&D/Seed Grants, "
        "Technology Business Tax Certificate Transfer (NOL) Program, Angel Investor Tax Credit, and NJ Accelerate."
    ),
    "SF": (
        "Focus on California and San Francisco Bay Area programs: California Competes Tax Credit/Grant (GO-Biz), "
        "SF Office of Economic and Workforce Development (OEWD), CalSEED, California IBank, and Bay Area municipal grants."
    ),
}


@dataclass
class SpecialistSpec:
    name: str
    agent: BaseAgent
    tool_suite: Optional[BoundedTavilyToolSuite] = None


def _is_nonprofit(org_type: str) -> bool:
    normalized = (org_type or "").lower().replace(" ", "").replace("-", "")
    return "nonprofit" in normalized or "501c3" in normalized or "ngo" in normalized


STATE_ALIASES = {
    "NY": "NY",
    "NEW YORK": "NY",
    "NYC": "NY",
    "NJ": "NJ",
    "NEW JERSEY": "NJ",
    "SF": "SF",
    "SAN FRANCISCO": "SF",
    "CA": "SF",
    "CALIFORNIA": "SF",
    "BAY AREA": "SF",
}


def _normalize_state(target_state: str) -> str:
    raw = (target_state or "NY").strip().upper()
    return STATE_ALIASES.get(raw, raw)


def build_specialists(
    *,
    org_type: str,
    target_state: str,
    model: str,
    tavily_client: TavilyClient,
    registry: RetrievedSourceRegistry,
) -> List[SpecialistSpec]:
    """
    Constructs the deterministic, opinionated roster of 4 specialist agents routed by
    `org_type` (Startup vs. Non-Profit) and `target_state` (NY / NJ / SF).
    """
    state_code = _normalize_state(target_state)
    state_hint = STATE_GUIDANCE.get(
        state_code,
        f"Focus on {state_code} state economic development agencies, tax credits, and municipal grant programs.",
    )

    specs: List[SpecialistSpec] = []

    # 1. State & Local Programs Specialist
    state_tools = BoundedTavilyToolSuite(
        specialist_name="state_local_programs",
        tavily_client=tavily_client,
        registry=registry,
        max_calls=2,
        default_scope="state",
    )
    specs.append(
        SpecialistSpec(
            name="state_local_programs",
            tool_suite=state_tools,
            agent=BaseAgent(
                name="state_local_programs",
                role="State & Local Funding Specialist",
                system_instruction=(
                    "You are a specialist in state and municipal funding, grants, and tax credits. "
                    f"{state_hint} "
                    "Identify concrete state/local opportunities matching the applicant's sector, stage, and entity type, "
                    "along with any state-nexus or local registration gaps. Set `specialist_name` to 'state_local_programs'."
                    + GROUNDING_RULES
                ),
                model=model,
                tools=state_tools.as_tools(),
                response_schema=SpecialistFinding,
                max_tool_iterations=4,
            ),
        )
    )

    # 2. Federal & Foundation Grants Specialist
    federal_tools = BoundedTavilyToolSuite(
        specialist_name="federal_foundation_grants",
        tavily_client=tavily_client,
        registry=registry,
        max_calls=2,
        default_scope="federal",
    )
    specs.append(
        SpecialistSpec(
            name="federal_foundation_grants",
            tool_suite=federal_tools,
            agent=BaseAgent(
                name="federal_foundation_grants",
                role="Federal & National Grants Specialist",
                system_instruction=(
                    "You are a specialist in US federal grants and national funding programs "
                    "(e.g. SBIR/STTR, NSF, NIH, DOE/ARPA-E, SBA, Grants.gov, and major national foundations). "
                    "Identify relevant federal/foundation opportunities, distinguish them clearly from state programs, "
                    "and note solicitation windows and eligibility criteria. Set `specialist_name` to 'federal_foundation_grants'."
                    + GROUNDING_RULES
                ),
                model=model,
                tools=federal_tools.as_tools(),
                response_schema=SpecialistFinding,
                max_tool_iterations=4,
            ),
        )
    )

    # 3. Routed Capital vs. Philanthropic Strategy Specialist
    if _is_nonprofit(org_type):
        phil_tools = BoundedTavilyToolSuite(
            specialist_name="philanthropic_strategy",
            tavily_client=tavily_client,
            registry=registry,
            max_calls=1,
            default_scope="market",
        )
        specs.append(
            SpecialistSpec(
                name="philanthropic_strategy",
                tool_suite=phil_tools,
                agent=BaseAgent(
                    name="philanthropic_strategy",
                    role="Philanthropic & Foundation Strategy Specialist",
                    system_instruction=(
                        "You are a specialist in non-profit fundraising strategy: philanthropic foundations, "
                        "corporate sponsorships, fiscal sponsorship, impact loans, and multi-year grant stacking. "
                        "Evaluate mission alignment, donor/foundation fit, and sustainable capital mix. "
                        "Set `specialist_name` to 'philanthropic_strategy'."
                        + GROUNDING_RULES
                    ),
                    model=model,
                    tools=phil_tools.as_tools(),
                    response_schema=SpecialistFinding,
                    max_tool_iterations=3,
                ),
            )
        )
    else:
        cap_tools = BoundedTavilyToolSuite(
            specialist_name="capital_strategy",
            tavily_client=tavily_client,
            registry=registry,
            max_calls=1,
            default_scope="market",
        )
        specs.append(
            SpecialistSpec(
                name="capital_strategy",
                tool_suite=cap_tools,
                agent=BaseAgent(
                    name="capital_strategy",
                    role="Startup Capital & Investor Strategy Specialist",
                    system_instruction=(
                        "You are a specialist in startup capital strategy: Pre-Seed/Seed SAFE and equity rounds, "
                        "angel tax credit leverage, venture benchmarks, revenue-based financing, and stacking non-dilutive grants "
                        "with dilutive capital. Evaluate the requested amount and use of funds against market norms. "
                        "Set `specialist_name` to 'capital_strategy'."
                        + GROUNDING_RULES
                    ),
                    model=model,
                    tools=cap_tools.as_tools(),
                    response_schema=SpecialistFinding,
                    max_tool_iterations=3,
                ),
            )
        )

    # 4. Eligibility & Compliance Validator (pure analysis over registry; no extra Tavily calls)
    specs.append(
        SpecialistSpec(
            name="eligibility_compliance",
            tool_suite=None,
            agent=BaseAgent(
                name="eligibility_compliance",
                role="Eligibility & Compliance Readiness Specialist",
                system_instruction=(
                    "You are a funding eligibility and compliance auditor for startups and 501(c)(3) non-profits. "
                    "Analyze the applicant's entity type, jurisdiction, funding instrument, and use of funds against "
                    "the retrieved program sources. Surface concrete readiness gaps (e.g. SAM.gov/UEI registration, "
                    "state nexus/registration, matching funds, 501(c)(3) determination, C-Corp conversion for VC, "
                    "audit/financial readiness) and prioritized remediation actions. "
                    "Set `specialist_name` to 'eligibility_compliance'."
                    + GROUNDING_RULES
                ),
                model=model,
                tools=None,
                response_schema=SpecialistFinding,
            ),
        )
    )

    return specs
