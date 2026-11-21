from typing import Dict, List, Optional, Set
from urllib.parse import urlparse
from pydantic import BaseModel, Field

from src.evals.dataset import CohortName, EvalCase
from src.models.report import FundingReport, UNVERIFIED_PLACEHOLDER
from src.pipeline.registry import _authority_bonus, normalize_url


class CaseScorecard(BaseModel):
    """Per-case quantitative evaluation metrics."""

    case_id: str
    cohort: CohortName
    system_name: str
    total_opportunities: int = 0
    url_grounding_rate: float = 0.0          # 0.0 - 1.0: % of cited URLs verified in retrieved sources & known valid corpus
    authority_domain_rate: float = 0.0       # 0.0 - 1.0: % of cited URLs on official .gov/.edu/agency domains
    trap_rejection_rate: float = 1.0         # 0.0 - 1.0: 1.0 if 0 fabricated/expired trap programs were recommended
    compliance_catch_rate: float = 0.0       # 0.0 - 1.0: % of required gap categories + trap keywords surfaced
    jurisdiction_coverage: float = 0.0       # 0.0 - 1.0: state/local keyword match + state vs federal split
    uncertainty_honesty_rate: float = 0.0    # 0.0 - 1.0: honest 'Unverified — check source' vs fabricated dates
    rubric_specificity_score: float = 0.0    # 1.0 - 5.0 calibrated rubric score
    composite_accuracy: float = 0.0          # 0.0 - 1.0 weighted overall accuracy score
    failure_notes: List[str] = Field(default_factory=list)


class SuiteAggregate(BaseModel):
    """Aggregated benchmark metrics across all 30 cases and per-cohort breakdowns."""

    system_name: str
    cases_evaluated: int
    mean_url_grounding_rate: float
    mean_authority_domain_rate: float
    mean_trap_rejection_rate: float
    mean_compliance_catch_rate: float
    mean_jurisdiction_coverage: float
    mean_uncertainty_honesty_rate: float
    mean_rubric_specificity: float
    mean_composite_accuracy: float
    by_cohort: Dict[str, float] = Field(default_factory=dict)


def _is_authoritative_url(url: str) -> bool:
    if not url:
        return False
    host = urlparse(url.strip()).netloc.lower()
    if _authority_bonus(url) > 0:
        return True
    # Also recognize state/territory economic development portals (e.g. massventures.com, investpr.org)
    known_ood_portals = (
        "cprit.texas.gov",
        "gov.texas.gov",
        "massventures.com",
        "masslifesciences.com",
        "masscec.com",
        "commerce.wa.gov",
        "oedit.colorado.gov",
        "investpr.org",
        "prsciencetrust.org",
        "ddec.pr.gov",
    )
    return any(host == d or host.endswith(f".{d}") for d in known_ood_portals)


def evaluate_case(
    case: EvalCase,
    report: FundingReport,
    *,
    system_name: str,
    known_valid_urls: Optional[Set[str]] = None,
) -> CaseScorecard:
    """
    Scores a single FundingReport against an EvalCase's ground-truth signals.
    """
    notes: List[str] = []
    opps = report.opportunities
    total_opps = len(opps)

    # Build set of normalized URLs actually retrieved during this report's run
    retrieved_urls: Set[str] = {
        normalize_url(s.get("url", ""))
        for s in report.sources
        if s.get("url")
    }
    valid_corpus_norms: Set[str] = (
        {normalize_url(u) for u in known_valid_urls} if known_valid_urls else set()
    )

    # 1. URL Grounding Rate & Authority Domain Rate
    if total_opps == 0:
        url_grounding_rate = 0.0
        authority_domain_rate = 0.0
        notes.append("Zero verified funding opportunities returned.")
    else:
        grounded_count = 0
        auth_count = 0
        for opp in opps:
            norm = normalize_url(opp.source_url) if opp.source_url else ""
            # Must be in retrieved sources (and if known_valid_urls provided, must not be a fake 404 URL)
            in_retrieved = norm in retrieved_urls
            in_valid = (norm in valid_corpus_norms) if valid_corpus_norms else in_retrieved
            if in_retrieved and in_valid:
                grounded_count += 1
            else:
                notes.append(f"Ungrounded or hallucinated URL cited: '{opp.source_url}' for '{opp.name}'")

            if _is_authoritative_url(opp.source_url):
                auth_count += 1

        url_grounding_rate = grounded_count / total_opps
        authority_domain_rate = auth_count / total_opps

    # 2. Adversarial / Hallucination Trap Rejection Rate
    trap_hits = 0
    total_traps = len(case.expected.must_not_recommend_names) + len(case.expected.must_not_recommend_urls)
    if total_traps > 0:
        bad_names = [n.lower() for n in case.expected.must_not_recommend_names]
        bad_urls = {normalize_url(u) for u in case.expected.must_not_recommend_urls}
        for opp in opps:
            opp_name_lower = opp.name.lower()
            opp_norm_url = normalize_url(opp.source_url) if opp.source_url else ""
            if any(bn in opp_name_lower for bn in bad_names) or opp_norm_url in bad_urls:
                trap_hits += 1
                notes.append(f"Trap failure: recommended forbidden program '{opp.name}' ({opp.source_url})")
        trap_rejection_rate = 0.0 if trap_hits > 0 else 1.0
    else:
        trap_rejection_rate = 1.0

    # 3. Compliance & Contradiction Catch Rate
    req_cats = case.expected.must_flag_gap_categories
    req_kw = [k.lower() for k in case.expected.must_flag_keywords_in_gaps]
    found_cats = {g.category for g in report.readiness_gaps}
    gaps_blob = " ".join(f"{g.description} {g.remediation}".lower() for g in report.readiness_gaps)

    cat_score = (
        sum(1 for c in req_cats if c in found_cats) / len(req_cats)
        if req_cats
        else 1.0
    )
    if req_cats and cat_score < 1.0:
        missing_c = [c for c in req_cats if c not in found_cats]
        notes.append(f"Missed required ReadinessGap categories: {missing_c}")

    if req_kw:
        kw_hits = sum(1 for k in req_kw if k in gaps_blob)
        kw_score = min(1.0, kw_hits / max(1, (len(req_kw) + 1) // 2))
        if kw_score < 1.0:
            notes.append(f"ReadinessGaps missed key risk signals from {req_kw}")
        compliance_catch_rate = 0.5 * cat_score + 0.5 * kw_score
    else:
        compliance_catch_rate = cat_score

    # 4. Jurisdictional Coverage (State/Local keyword match + State/Federal split)
    has_state_scope = any(o.scope in ("state", "local") for o in opps)
    has_fed_scope = any(o.scope in ("federal", "foundation") for o in opps)
    split_score = (0.5 if has_state_scope else 0.0) + (0.5 if has_fed_scope else 0.0)

    opps_blob = " ".join(
        f"{o.name} {o.provider} {o.why_it_fits} {o.source_url}".lower() for o in opps
    )
    jur_kw = [k.lower() for k in case.expected.expected_jurisdiction_keywords]
    if jur_kw:
        has_jur_match = any(k in opps_blob for k in jur_kw)
        if not has_jur_match:
            notes.append(
                f"Missed expected jurisdiction programs matching {case.expected.expected_jurisdiction_keywords}"
            )
        jurisdiction_coverage = 0.5 * split_score + (0.5 if has_jur_match else 0.0)
    else:
        jurisdiction_coverage = split_score

    # 5. Explicit Uncertainty Honesty Rate
    if total_opps == 0:
        uncertainty_honesty_rate = 0.0
    else:
        honest_count = 0
        for opp in opps:
            # Deadlines/amounts are honest if they either use UNVERIFIED_PLACEHOLDER or come from an extracted source
            dl = (opp.deadline or "").strip()
            amt = (opp.estimated_amount or "").strip()
            is_extracted_src = any(
                s.get("source_id") == opp.source_id and s.get("is_extracted")
                for s in report.sources
            )
            if dl == UNVERIFIED_PLACEHOLDER or amt == UNVERIFIED_PLACEHOLDER or is_extracted_src:
                honest_count += 1
            elif dl.lower() not in ("tbd", "varies", "unknown", ""):
                # Claimed a specific deadline without full page extraction or placeholder
                honest_count += 0.4
        uncertainty_honesty_rate = min(1.0, honest_count / total_opps)

    # 6. Calibrated Rubric Specificity Score (1.0 - 5.0)
    score_pts = 1.0
    if len(report.opportunities) >= case.expected.min_verified_opportunities and url_grounding_rate >= 0.8:
        score_pts += 1.0
    if compliance_catch_rate >= 0.75:
        score_pts += 1.0
    if jurisdiction_coverage >= 0.75:
        score_pts += 1.0
    if len(report.action_plan) >= 3 and any(a.timeframe == "immediate_7d" for a in report.action_plan):
        score_pts += 0.5
    if trap_rejection_rate == 1.0 and uncertainty_honesty_rate >= 0.7:
        score_pts += 0.5

    # Composite Accuracy (0.0 - 1.0)
    composite = (
        0.30 * url_grounding_rate
        + 0.20 * trap_rejection_rate
        + 0.20 * compliance_catch_rate
        + 0.20 * jurisdiction_coverage
        + 0.10 * uncertainty_honesty_rate
    )

    return CaseScorecard(
        case_id=case.case_id,
        cohort=case.cohort,
        system_name=system_name,
        total_opportunities=total_opps,
        url_grounding_rate=round(url_grounding_rate, 4),
        authority_domain_rate=round(authority_domain_rate, 4),
        trap_rejection_rate=round(trap_rejection_rate, 4),
        compliance_catch_rate=round(compliance_catch_rate, 4),
        jurisdiction_coverage=round(jurisdiction_coverage, 4),
        uncertainty_honesty_rate=round(uncertainty_honesty_rate, 4),
        rubric_specificity_score=round(score_pts, 2),
        composite_accuracy=round(composite, 4),
        failure_notes=notes,
    )


def aggregate_scores(system_name: str, scorecards: List[CaseScorecard]) -> SuiteAggregate:
    n = max(1, len(scorecards))
    cohorts: Dict[str, List[float]] = {}
    for sc in scorecards:
        cohorts.setdefault(sc.cohort, []).append(sc.composite_accuracy)

    by_cohort = {
        c: round(sum(vals) / len(vals), 4)
        for c, vals in cohorts.items()
    }

    return SuiteAggregate(
        system_name=system_name,
        cases_evaluated=len(scorecards),
        mean_url_grounding_rate=round(sum(s.url_grounding_rate for s in scorecards) / n, 4),
        mean_authority_domain_rate=round(sum(s.authority_domain_rate for s in scorecards) / n, 4),
        mean_trap_rejection_rate=round(sum(s.trap_rejection_rate for s in scorecards) / n, 4),
        mean_compliance_catch_rate=round(sum(s.compliance_catch_rate for s in scorecards) / n, 4),
        mean_jurisdiction_coverage=round(sum(s.jurisdiction_coverage for s in scorecards) / n, 4),
        mean_uncertainty_honesty_rate=round(sum(s.uncertainty_honesty_rate for s in scorecards) / n, 4),
        mean_rubric_specificity=round(sum(s.rubric_specificity_score for s in scorecards) / n, 2),
        mean_composite_accuracy=round(sum(s.composite_accuracy for s in scorecards) / n, 4),
        by_cohort=by_cohort,
    )
