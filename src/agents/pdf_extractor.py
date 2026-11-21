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
        return ExtractedIntakeProfile.model_validate_json(raw_json)
