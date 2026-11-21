import asyncio
import base64
import logging
import os
from typing import Any, Dict, List, Literal, Optional, Protocol, runtime_checkable
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class FormFieldSpec(BaseModel):
    selector: str
    tag: str
    input_type: str = "text"
    label: str
    required: bool = False
    maxlength: Optional[int] = None
    options: List[str] = Field(default_factory=list)
    current_value: str = ""


class FormButtonSpec(BaseModel):
    selector: str
    label: str
    kind: Literal["next", "save_draft", "submit", "other"] = "other"


class ExtractedFormSchema(BaseModel):
    page_url: str
    page_title: str = ""
    heading: str = ""
    fields: List[FormFieldSpec] = Field(default_factory=list)
    buttons: List[FormButtonSpec] = Field(default_factory=list)
    auth_or_captcha_detected: Optional[str] = None


@runtime_checkable
class BrowserSession(Protocol):
    async def inspect_form(self) -> ExtractedFormSchema: ...

    async def fill_field(
        self,
        selector: str,
        value: str,
        action_type: Literal["fill", "select", "check"],
    ) -> None: ...

    async def click_button(self, selector: str) -> None: ...

    async def capture_screenshot(self, label: str = "") -> str: ...

    async def close(self) -> None: ...


@runtime_checkable
class BrowserDriver(Protocol):
    async def open_session(self, url: str) -> BrowserSession: ...


_DOM_INSPECT_JS = """
() => {
  const result = {
    page_url: window.location.href,
    page_title: document.title || "",
    heading: (document.querySelector("h1, h2")?.innerText || "").trim(),
    fields: [],
    buttons: [],
    auth_or_captcha_detected: null
  };

  // Detect password login walls or CAPTCHA frames (never bypass)
  const passwordInput = document.querySelector('input[type="password"]');
  if (passwordInput && passwordInput.offsetParent !== null) {
    result.auth_or_captcha_detected = "Portal requires user login (password input detected).";
    return result;
  }
  const captchaFrame = document.querySelector(
    'iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="turnstile"], .g-recaptcha, .h-captcha'
  );
  if (captchaFrame) {
    result.auth_or_captcha_detected = "Portal is protected by a CAPTCHA / anti-bot challenge.";
    return result;
  }

  const labelFor = (el) => {
    if (el.id) {
      const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lbl) return lbl.innerText.trim().replace(/\\s+/g, " ");
    }
    const parentLabel = el.closest("label");
    if (parentLabel) return parentLabel.innerText.trim().replace(/\\s+/g, " ");
    return (el.getAttribute("aria-label") || el.getAttribute("placeholder") || el.name || el.id || "").trim();
  };

  const selectorFor = (el) => {
    if (el.id) return `#${el.id}`;
    if (el.name) return `${el.tagName.toLowerCase()}[name="${el.name}"]`;
    return "";
  };

  const controls = document.querySelectorAll("input, textarea, select");
  for (const el of controls) {
    if (el.disabled || el.type === "hidden" || el.offsetParent === null) continue;
    const selector = selectorFor(el);
    if (!selector) continue;
    const tag = el.tagName.toLowerCase();
    const input_type = tag === "input" ? (el.type || "text").toLowerCase() : tag;
    if (input_type === "submit" || input_type === "button") continue;
    const options = tag === "select"
      ? Array.from(el.options).map(o => o.value || o.text.trim()).filter(Boolean)
      : [];
    result.fields.push({
      selector,
      tag,
      input_type,
      label: labelFor(el),
      required: Boolean(el.required),
      maxlength: el.maxLength && el.maxLength > 0 ? el.maxLength : null,
      options,
      current_value: input_type === "checkbox" ? String(Boolean(el.checked)) : (el.value || "")
    });
  }

  const btns = document.querySelectorAll('button, input[type="submit"]');
  for (const btn of btns) {
    if (btn.offsetParent === null) continue;
    const text = (btn.innerText || btn.value || "").trim();
    if (!text) continue;
    const lower = text.toLowerCase();
    let kind = "other";
    if (lower.includes("submit")) kind = "submit";
    else if (lower.includes("save draft")) kind = "save_draft";
    else if (lower.includes("next") || lower.includes("continue")) kind = "next";
    const selector = btn.id ? `#${btn.id}` : `button:has-text("${text.replace(/"/g, '\\"')}")`;
    result.buttons.push({ selector, label: text, kind });
  }
  return result;
}
"""


class _PlaywrightSession:
    def __init__(self, context: Any, page: Any, semaphore: asyncio.Semaphore) -> None:
        self._context = context
        self._page = page
        self._semaphore = semaphore
        self._closed = False

    async def inspect_form(self) -> ExtractedFormSchema:
        raw = await self._page.evaluate(_DOM_INSPECT_JS)
        return ExtractedFormSchema.model_validate(raw)

    async def fill_field(
        self,
        selector: str,
        value: str,
        action_type: Literal["fill", "select", "check"],
    ) -> None:
        locator = self._page.locator(selector).first
        await locator.scroll_into_view_if_needed()
        # Visual highlight for live screenshot clarity
        await locator.evaluate(
            "(el) => { el.dataset._prevOutline = el.style.outline; el.style.outline = '3px solid #2563eb'; }"
        )
        if action_type == "select":
            await locator.select_option(value)
        elif action_type == "check":
            if value.lower() in ("true", "1", "yes", "checked"):
                await locator.check()
        else:
            await locator.fill(value)

    async def click_button(self, selector: str) -> None:
        locator = self._page.locator(selector).first
        await locator.scroll_into_view_if_needed()
        await locator.click()
        await self._page.wait_for_timeout(350)

    async def capture_screenshot(self, label: str = "") -> str:
        raw_bytes = await self._page.screenshot(type="jpeg", quality=65, full_page=False)
        b64 = base64.b64encode(raw_bytes).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._context.close()
        finally:
            self._semaphore.release()


class PlaywrightBrowserDriver:
    """
    Production BrowserDriver backed by a single lazily-initialized Chromium instance
    guarded by asyncio.Semaphore(2) with isolated browser contexts per draft job.
    """

    def __init__(self, *, max_concurrent_contexts: int = 2, headful: Optional[bool] = None) -> None:
        self._semaphore = asyncio.Semaphore(max(1, max_concurrent_contexts))
        self._init_lock = asyncio.Lock()
        self._playwright = None
        self._browser = None
        self._headful = (
            headful
            if headful is not None
            else os.getenv("CARTHIJ_BROWSER_HEADFUL", "").lower() in ("true", "1", "yes")
        )

    async def _ensure_browser(self) -> Any:
        async with self._init_lock:
            if self._browser is None:
                from playwright.async_api import async_playwright  # type: ignore[import-untyped]

                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=not self._headful)
            return self._browser

    async def open_session(self, url: str) -> BrowserSession:
        await self._semaphore.acquire()
        try:
            browser = await self._ensure_browser()
            context = await browser.new_context(viewport={"width": 1280, "height": 800})
            page = await context.new_page()
            await page.goto(url, wait_until="networkidle", timeout=30000)
            return _PlaywrightSession(context=context, page=page, semaphore=self._semaphore)
        except Exception:
            self._semaphore.release()
            raise


class _SimulatedSession:
    """Deterministic 3-step portal simulation matching /demo-portal for unit tests & offline CI."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.step = 1
        self.values: Dict[str, str] = {}
        self.draft_saved = False

    async def inspect_form(self) -> ExtractedFormSchema:
        if "login" in self.url.lower():
            return ExtractedFormSchema(
                page_url=self.url,
                page_title="Portal Sign In",
                heading="Sign In Required",
                auth_or_captcha_detected="Portal requires user login (password input detected).",
            )
        if self.step == 1:
            return ExtractedFormSchema(
                page_url=self.url,
                page_title="Demo Grant Portal - Step 1",
                heading="Step 1: Organization & Compliance Profile",
                fields=[
                    FormFieldSpec(selector="#org_legal_name", tag="input", input_type="text", label="Organization Legal Name *", required=True, current_value=self.values.get("#org_legal_name", "")),
                    FormFieldSpec(selector="#org_type", tag="select", input_type="select", label="Organization Entity Type *", required=True, options=["For-Profit Startup", "501(c)(3) Non-Profit", "Public Benefit Corporation"], current_value=self.values.get("#org_type", "")),
                    FormFieldSpec(selector="#jurisdiction", tag="select", input_type="select", label="Primary State Jurisdiction *", required=True, options=["NY", "NJ", "SF"], current_value=self.values.get("#jurisdiction", "")),
                    FormFieldSpec(selector="#contact_email", tag="input", input_type="email", label="Primary Contact Email *", required=True, current_value=self.values.get("#contact_email", "")),
                    FormFieldSpec(selector="#federal_ein", tag="input", input_type="text", label="Federal EIN (Tax ID) (Required before final submit)", required=False, current_value=self.values.get("#federal_ein", "")),
                    FormFieldSpec(selector="#sam_uei", tag="input", input_type="text", label="SAM.gov Unique Entity ID (UEI) (Required before final submit)", required=False, current_value=self.values.get("#sam_uei", "")),
                ],
                buttons=[FormButtonSpec(selector="#next_step_btn", label="Next: Proposal Narrative", kind="next")],
            )
        if self.step == 2:
            return ExtractedFormSchema(
                page_url=self.url,
                page_title="Demo Grant Portal - Step 2",
                heading="Step 2: Proposal Narrative & Budget Breakdown",
                fields=[
                    FormFieldSpec(selector="#target_program_name", tag="input", input_type="text", label="Target Funding Program Name *", required=True, current_value=self.values.get("#target_program_name", "")),
                    FormFieldSpec(selector="#requested_amount_usd", tag="input", input_type="text", label="Requested Funding Amount (USD) *", required=True, current_value=self.values.get("#requested_amount_usd", "")),
                    FormFieldSpec(selector="#project_title", tag="input", input_type="text", label="Project Title *", required=True, maxlength=140, current_value=self.values.get("#project_title", "")),
                    FormFieldSpec(selector="#project_abstract", tag="textarea", input_type="textarea", label="Executive Project Abstract (Max 250 Words) *", required=True, maxlength=2000, current_value=self.values.get("#project_abstract", "")),
                    FormFieldSpec(selector="#budget_breakdown", tag="textarea", input_type="textarea", label="Budget & Use of Funds Justification *", required=True, maxlength=1200, current_value=self.values.get("#budget_breakdown", "")),
                    FormFieldSpec(selector="#attest_eligibility", tag="input", input_type="checkbox", label="Eligibility Attestation", required=False, current_value=self.values.get("#attest_eligibility", "false")),
                    FormFieldSpec(selector="#attest_non_lobbying", tag="input", input_type="checkbox", label="Non-Lobbying Certification", required=False, current_value=self.values.get("#attest_non_lobbying", "false")),
                ],
                buttons=[FormButtonSpec(selector="#review_step_btn", label="Continue to Review", kind="next")],
            )
        return ExtractedFormSchema(
            page_url=self.url,
            page_title="Demo Grant Portal - Step 3 Review",
            heading="Step 3: Review Application Before Submission",
            fields=[],
            buttons=[
                FormButtonSpec(selector="#save_draft_btn", label="Save Draft", kind="save_draft"),
                FormButtonSpec(selector="#final_submit_btn", label="Submit Final Application", kind="submit"),
            ],
        )

    async def fill_field(
        self,
        selector: str,
        value: str,
        action_type: Literal["fill", "select", "check"],
    ) -> None:
        self.values[selector] = value

    async def click_button(self, selector: str) -> None:
        if selector == "#next_step_btn":
            self.step = 2
        elif selector == "#review_step_btn":
            self.step = 3
        elif selector == "#save_draft_btn":
            self.draft_saved = True

    async def capture_screenshot(self, label: str = "") -> str:
        safe_label = (label or f"Step {self.step}").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="450" viewBox="0 0 800 450">'
            '<rect width="800" height="450" fill="#f1f5f9"/>'
            '<rect width="800" height="64" fill="#0f172a"/>'
            '<text x="28" y="40" fill="#ffffff" font-family="sans-serif" font-size="18" font-weight="bold">'
            f'Simulated Portal Browser — Step {self.step} of 3</text>'
            '<rect x="28" y="90" width="744" height="320" rx="12" fill="#ffffff" stroke="#cbd5e1"/>'
            '<text x="52" y="135" fill="#1e293b" font-family="sans-serif" font-size="16" font-weight="bold">'
            f'{safe_label}</text>'
            '<text x="52" y="170" fill="#475569" font-family="sans-serif" font-size="13">'
            f'Fields filled so far: {len(self.values)} | Draft saved: {self.draft_saved}</text>'
            '</svg>'
        )
        b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
        return f"data:image/svg+xml;base64,{b64}"

    async def close(self) -> None:
        return None


class SimulatedBrowserDriver:
    """Offline/CI fallback BrowserDriver."""

    async def open_session(self, url: str) -> BrowserSession:
        return _SimulatedSession(url=url)


def get_browser_driver() -> BrowserDriver:
    mode = os.getenv("CARTHIJ_BROWSER_DRIVER", "").strip().lower()
    if mode == "simulated":
        return SimulatedBrowserDriver()
    try:
        import playwright  # noqa: F401
        return PlaywrightBrowserDriver()
    except ImportError:
        logger.warning("Playwright is not installed; falling back to SimulatedBrowserDriver.")
        return SimulatedBrowserDriver()
