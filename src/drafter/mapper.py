import json
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

from src.core.agent import BaseAgent
from src.core.client import DEFAULT_GEMINI_MODEL
from src.drafter.browser import ExtractedFormSchema
from src.models.report import FundingOpportunity, FundingReport


class FieldFillAction(BaseModel):
    selector: str = Field(..., description="Exact CSS selector from ExtractedFormSchema.fields.")
    field_label: str = Field(..., description="Human-readable label of the field.")
    action_type: Literal["fill", "select", "check", "skip_missing"] = Field(
        ...,
        description="Use 'skip_missing' whenever a factual compliance/org value is NOT in ApplicantFactLedger.",
    )
    value: Optional[str] = Field(
        default=None,
        description="Value to fill/select (or 'true' for checkboxes). Must be null if action_type='skip_missing'.",
    )
    missing_question: Optional[str] = Field(
        default=None,
        description="Specific question to ask the user when action_type='skip_missing'.",
    )
    reasoning: str = Field(default="", description="Brief rationale for the mapping.")


class FormFillPlan(BaseModel):
    actions: List[FieldFillAction] = Field(default_factory=list)
    narrative_summary_markdown: str = Field(
        default="",
        description="Clean Markdown summary of any narratives/budget drafted on this page.",
    )


def build_applicant_fact_ledger(
    report: FundingReport,
    user_supplied_facts: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Constructs the strict verified-facts ledger for the applicant.
    Only facts explicitly present here may be filled into compliance/factual fields.
    """
    return {
        "organization_name": report.org_name,
        "organization_type": report.org_type,
        "target_state": report.target_state,
        "submitter_email": report.submitter_email,
        "intake_data": report.intake_data,
        "user_supplied_facts": user_supplied_facts or {},
    }


class FormMapperAgent:
    """
    Grounded Gemini 3.8 Flash agent that maps ApplicantFactLedger + FundingOpportunity context
    onto a live ExtractedFormSchema without ever fabricating missing compliance facts.
    """

    def __init__(self, model: str = DEFAULT_GEMINI_MODEL) -> None:
        self.model = model
        self.agent = BaseAgent(
            name="form_mapper_agent",
            role="Grounded Grant Application Form Mapper & Drafter",
            system_instruction=(
                "You are an expert grant writer and precision form-filling planner. "
                "Given an ExtractedFormSchema from a live grant portal, an ApplicantFactLedger, "
                "and target FundingOpportunity details, produce a FormFillPlan with one FieldFillAction "
                "for every field in ExtractedFormSchema.fields.\n\n"
                "STRICT ANTI-FABRICATION RULES:\n"
                "1. NEVER invent factual identifiers not present in ApplicantFactLedger — including "
                "Federal EIN / Tax ID, SAM.gov UEI, DUNS, bank account numbers, exact historical revenue, "
                "headcount, or legal signer names. For any such missing field, set `action_type='skip_missing'`, "
                "`value=None`, and provide a clear `missing_question` for the user.\n"
                "2. For `select` fields, `value` MUST match one of the exact strings in `field.options`.\n"
                "3. For narrative `textarea` fields (e.g. Project Abstract, Budget Breakdown), draft compelling, "
                "program-tailored copy grounded strictly in `intake_data` (mission_and_pitch, use_of_funds, "
                "target_amount_usd) and the target FundingOpportunity, respecting `maxlength` and word limits.\n"
                "4. For standard eligibility/non-lobbying attestation checkboxes, set `action_type='check'` and "
                "`value='true'` ONLY if consistent with the applicant's entity type and jurisdiction.\n"
                "5. Copy each field's exact `selector` and `label` verbatim from ExtractedFormSchema."
            ),
            model=self.model,
            response_schema=FormFillPlan,
        )

    async def plan_fill(
        self,
        *,
        form_schema: ExtractedFormSchema,
        fact_ledger: Dict[str, Any],
        opportunity_name: str,
        opportunity_source_url: str,
        opportunity_context: Optional[FundingOpportunity] = None,
    ) -> FormFillPlan:
        prompt = (
            f"Target Funding Opportunity: {opportunity_name} ({opportunity_source_url})\n"
            f"Opportunity Context:\n"
            f"{opportunity_context.model_dump_json(indent=2) if opportunity_context else 'N/A'}\n\n"
            f"ApplicantFactLedger (ONLY verified facts allowed):\n"
            f"{json.dumps(fact_ledger, indent=2)}\n\n"
            f"Live ExtractedFormSchema (map every field):\n"
            f"{form_schema.model_dump_json(indent=2)}"
        )
        raw_json = await self.agent.run(prompt)
        return FormFillPlan.model_validate_json(raw_json)
