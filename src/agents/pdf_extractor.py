import base64
from typing import List, Literal
from google.genai import types
from pydantic import BaseModel, Field

from src.core.agent import BaseAgent
from src.core.client import DEFAULT_GEMINI_MODEL

MAX_PDF_BYTES = 15 * 1024 * 1024  # 15 MiB limit

FUNDING_INSTRUMENTS = (
    "Equity Investment (Pre-Seed / SAFE / Angel)",
    "Equity Investment (Seed / Series A)",
    "Government Grant (SBIR / STTR / Horizon)",
    "Philanthropic / Foundation Grant",
    "Impact Loan / Revenue-Based Financing",
    "Corporate Sponsorship / Partnership",
)

_UNVERIFIED_FACT_TOKENS = {
    "",
    "tbd",
    "n/a",
    "na",
    "none",
    "unknown",
    "applied for",
    "pending",
    "00-0000000",
    "xx-xxxxxxx",
    "12-3456789 (estimated)",
}


def normalize_extracted_profile(profile: "ExtractedIntakeProfile") -> "ExtractedIntakeProfile":
    """
    Programmatic post-normalization for ExtractedIntakeProfile:
    1. Clamps `mission_and_pitch` to <= 900 chars at word boundary.
    2. Normalizes `target_funding_instrument` to one of the 6 canonical UI <select> options.
    3. Strips placeholder/unverified values ('TBD', 'Applied For', fake emails) from
       `contact_email` and `verified_extra_facts`.
    """
    pitch = (profile.mission_and_pitch or "").strip()
    if len(pitch) > 900:
        clipped = pitch[:897]
        last_space = clipped.rfind(" ")
        pitch = (clipped[:last_space] if last_space > 600 else clipped).rstrip() + "..."

    inst = (profile.target_funding_instrument or "").strip()
    if inst not in FUNDING_INSTRUMENTS:
        lower_inst = inst.lower()
        if "philanthrop" in lower_inst or "foundation" in lower_inst or "501" in lower_inst:
            inst = "Philanthropic / Foundation Grant"
        elif "seed" in lower_inst and ("series a" in lower_inst or "venture" in lower_inst):
            inst = "Equity Investment (Seed / Series A)"
        elif "safe" in lower_inst or "pre-seed" in lower_inst or "angel" in lower_inst or "equity" in lower_inst:
            inst = "Equity Investment (Pre-Seed / SAFE / Angel)"
        elif "loan" in lower_inst or "revenue" in lower_inst or "debt" in lower_inst:
            inst = "Impact Loan / Revenue-Based Financing"
        elif "sponsor" in lower_inst or "corporate" in lower_inst:
            inst = "Corporate Sponsorship / Partnership"
        else:
            inst = "Government Grant (SBIR / STTR / Horizon)"

    email = (profile.contact_email or "").strip()
    if "@" not in email or email.lower() in _UNVERIFIED_FACT_TOKENS or email.lower().endswith("@example.com"):
        email = ""

    clean_facts: List[ExtractedFactItem] = []
    seen_keys: set[str] = set()
    for item in profile.verified_extra_facts:
        k = (item.key or "").strip().lower().replace(" ", "_").replace("-", "_")
        v = (item.value or "").strip()
        if not k or not v or v.lower() in _UNVERIFIED_FACT_TOKENS:
            continue
        if k not in seen_keys:
            seen_keys.add(k)
            clean_facts.append(ExtractedFactItem(key=k, value=v))

    return profile.model_copy(
        update={
            "mission_and_pitch": pitch,
            "target_funding_instrument": inst,
            "contact_email": email,
            "verified_extra_facts": clean_facts,
        }
    )


class ExtractedFactItem(BaseModel):
    key: str = Field(..., description="Short snake_case fact key (e.g. annual_revenue, headcount, federal_ein).")
    value: str = Field(..., description="Verified fact value explicitly stated in the PDF.")


class ExtractedIntakeProfile(BaseModel):
    """Structured funding intake profile extracted natively from a PDF via Gemini 3.8 Flash."""

    organization_name: str = Field(default="", description="Legal or primary organization name.")
    organization_type: Literal["Startup", "Non-Profit"] = Field(
        default="Startup",
        description="Classify as 'Non-Profit' if 501(c)(3)/NGO/charity, otherwise 'Startup'.",
    )
    target_state: Literal["NY", "NJ", "SF"] = Field(
        default="NY",
        description="Best matching jurisdiction ('NY', 'NJ', or 'SF' for California/Bay Area). Default 'NY' if unclear.",
    )
    contact_email: str = Field(default="", description="Contact email if explicitly present in the PDF, else ''.")
    mission_and_pitch: str = Field(
        default="",
        description="Concise 2-4 sentence mission statement & elevator pitch grounded in the PDF (under 900 chars).",
    )
    target_funding_instrument: str = Field(
        default="Government Grant (SBIR / STTR / Horizon)",
        description=f"Closest match from: {', '.join(FUNDING_INSTRUMENTS)}.",
    )
    target_amount_usd: str = Field(
        default="",
        description="Target funding ask in USD (e.g. '500,000') if stated, else sensible estimate or ''.",
    )
    use_of_funds: str = Field(
        default="",
        description="Concise summary of planned use of funds / R&D / capital priorities from the document.",
    )
    verified_extra_facts: List[ExtractedFactItem] = Field(
        default_factory=list,
        description=(
            "Additional factual attributes explicitly stated in the PDF (e.g., annual_revenue, headcount, "
            "federal_ein, sam_uei, founded_year). NEVER invent values not in the document."
        ),
    )
    source_summary: str = Field(
        default="",
        description="1-sentence description of what document was analyzed.",
    )


class PDFExtractRequest(BaseModel):
    filename: str = Field(default="document.pdf")
    pdf_base64: str = Field(..., min_length=10, description="Base64-encoded PDF bytes (optional data URI prefix).")


def decode_pdf_base64(raw_b64: str) -> bytes:
    payload = raw_b64.strip()
    if "," in payload and payload.lower().startswith("data:"):
        payload = payload.split(",", 1)[1]
    try:
        pdf_bytes = base64.b64decode(payload, validate=True)
    except Exception as exc:
        raise ValueError(f"Invalid base64 PDF payload: {exc}") from exc

    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise ValueError(f"PDF exceeds maximum allowed size of {MAX_PDF_BYTES // (1024 * 1024)} MB.")
    if not pdf_bytes.startswith(b"%PDF"):
        raise ValueError("Uploaded file is not a valid PDF document.")
    return pdf_bytes


class PDFIntakeExtractor:
    """
    Native Multimodal PDF Ingestion powered by Gemini 3.8 Flash.
    Passes raw PDF bytes directly to Gemini without brittle OCR/text scraping.
    """

    def __init__(self, model: str = DEFAULT_GEMINI_MODEL) -> None:
        self.model = model
        self.agent = BaseAgent(
            name="pdf_intake_extractor",
            role="Multimodal Pitch Deck & Financial Document Analyst",
            system_instruction=(
                "You analyze uploaded startup pitch decks, 501(c)(3) annual reports, or financial filings (PDFs) "
                "using Gemini's native multimodal PDF understanding and extract a structured funding intake profile.\n\n"
                "Rules:\n"
                "1. Ground every extracted field strictly in the attached PDF.\n"
                "2. NEVER fabricate contact emails, EINs, SAM.gov UEIs, or revenue numbers not present in the PDF.\n"
                "3. Keep `mission_and_pitch` crisp, compelling, and under 900 characters.\n"
                f"4. Pick `target_funding_instrument` from: {', '.join(FUNDING_INSTRUMENTS)}."
            ),
            model=self.model,
            response_schema=ExtractedIntakeProfile,
        )

    async def extract_from_bytes(self, pdf_bytes: bytes, filename: str = "document.pdf") -> ExtractedIntakeProfile:
        pdf_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
        prompt = (
            f"Analyze the attached PDF ('{filename}') and extract the structured funding intake profile "
            "and any verified factual attributes explicitly stated in the document."
        )
        raw_json = await self.agent.run(prompt, extra_parts=[pdf_part])
        parsed = ExtractedIntakeProfile.model_validate_json(raw_json)
        return normalize_extracted_profile(parsed)
