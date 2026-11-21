"use client";

import { useState, useId } from "react";
import { useRouter } from "next/navigation";
import { FormSubmissionPayload, ReportAcceptedResponse } from "@/types";
import { Loader2, ArrowRight, FileUp, Sparkles, CheckCircle2 } from "lucide-react";

export default function IntakeForm() {
  const router = useRouter();
  const formHeadingId = useId();
  const [orgType, setOrgType] = useState<"Startup" | "Non-Profit">("Startup");
  const [targetState, setTargetState] = useState<"NY" | "NJ" | "SF">("NY");
  const [orgName, setOrgName] = useState("");
  const [contactEmail, setContactEmail] = useState("");
  const [mission, setMission] = useState("");
  const [fundingType, setFundingType] = useState("Equity Investment (Pre-Seed / SAFE / Angel)");
  const [fundingAmount, setFundingAmount] = useState("");
  const [useOfFunds, setUseOfFunds] = useState("");
  const [extractedFacts, setExtractedFacts] = useState<Record<string, string>>({});
  const [isExtractingPdf, setIsExtractingPdf] = useState(false);
  const [pdfNotice, setPdfNotice] = useState<string | null>(null);
  const [isAsync, setIsAsync] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handlePdfUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setErrorMsg(null);
    setPdfNotice(null);
    setIsExtractingPdf(true);

    try {
      const base64DataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ""));
        reader.onerror = () => reject(new Error("Could not read PDF file."));
        reader.readAsDataURL(file);
      });

      const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const res = await fetch(`${apiBase}/api/v1/intake/extract-pdf`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: file.name, pdf_base64: base64DataUrl }),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `PDF extraction failed (${res.status})`);
      }

      const profile = await res.json();
      if (profile.organization_type === "Startup" || profile.organization_type === "Non-Profit") {
        setOrgType(profile.organization_type);
      }
      if (profile.target_state === "NY" || profile.target_state === "NJ" || profile.target_state === "SF") {
        setTargetState(profile.target_state);
      }
      if (profile.organization_name) setOrgName(profile.organization_name);
      if (profile.contact_email) setContactEmail(profile.contact_email);
      if (profile.mission_and_pitch) setMission(profile.mission_and_pitch);
      if (profile.target_funding_instrument) setFundingType(profile.target_funding_instrument);
      if (profile.target_amount_usd) setFundingAmount(profile.target_amount_usd);
      if (profile.use_of_funds) setUseOfFunds(profile.use_of_funds);
      if (Array.isArray(profile.verified_extra_facts)) {
        const mapped: Record<string, string> = {};
        for (const item of profile.verified_extra_facts) {
          if (item?.key && item?.value) mapped[item.key] = String(item.value);
        }
        setExtractedFacts(mapped);
      }
      setPdfNotice(
        profile.source_summary || `Auto-filled intake from ${file.name} via Gemini 3.8 Flash.`
      );
    } catch (err: any) {
      setErrorMsg(err.message || "Failed to extract intake from PDF.");
    } finally {
      setIsExtractingPdf(false);
      e.target.value = "";
    }
  };

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setErrorMsg(null);

    const form = e.currentTarget;
    if (!form.checkValidity()) {
      const firstInvalid = form.querySelector(":invalid") as HTMLElement | null;
      firstInvalid?.focus();
      return;
    }

    const formData = new FormData(form);
    const callbackUrl = (formData.get("callback_url") as string) || null;

    const payload: FormSubmissionPayload = {
      form_id: `intake_${orgType.toLowerCase()}_${targetState.toLowerCase()}`,
      submitter_email: contactEmail,
      data: {
        ...extractedFacts,
        organization_name: orgName,
        organization_type: orgType,
        target_state: targetState,
        mission_and_pitch: mission,
        target_funding_instrument: fundingType,
        target_amount_usd: fundingAmount,
        use_of_funds: useOfFunds,
      },
      context: `The applicant is a ${orgType} operating in ${targetState} seeking ${fundingType} funding of $${fundingAmount}. Evaluate state-level (${targetState}) incentives and regional programs vs. federal programs, market risks, and strategic next steps.`,
      callback_url: callbackUrl,
    };

    setIsSubmitting(true);

    try {
      const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const res = await fetch(`${apiBase}/api/v1/reports`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        throw new Error(errorData.detail || `Server returned error status ${res.status}`);
      }

      const data: ReportAcceptedResponse = await res.json();
      if (data.report_id) {
        router.push(`/reports/${data.report_id}`);
      }
    } catch (err: any) {
      setErrorMsg(err.message || "Failed to submit workflow intake.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div id="intake-form" className="bg-white rounded-2xl shadow-xl border border-slate-200 overflow-hidden">
      <div className="p-6 md:p-8 bg-slate-900 text-white">
        <h2 id={formHeadingId} className="text-2xl font-bold">
          Funding Intake Application
        </h2>
        <p className="text-slate-300 text-sm mt-1">
          Provide your details to initiate the AI evaluation workflow.
        </p>
      </div>

      <form
        onSubmit={handleSubmit}
        className="p-6 md:p-8 space-y-8"
        aria-labelledby={formHeadingId}
        noValidate
      >
        {/* Native Multimodal PDF Pitch Deck Auto-Fill */}
        <div className="p-4 rounded-xl bg-gradient-to-r from-blue-50 to-indigo-50 border border-blue-200 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="flex items-start gap-3">
            <div className="w-9 h-9 rounded-lg bg-blue-600 text-white flex items-center justify-center shrink-0 mt-0.5">
              <Sparkles className="w-4 h-4" />
            </div>
            <div>
              <p className="text-sm font-bold text-slate-900">
                Auto-Fill from Pitch Deck or Org PDF (Gemini 3.8 Flash Multimodal)
              </p>
              <p className="text-xs text-slate-600 mt-0.5">
                Upload a startup pitch deck, 501(c)(3) overview, or 10-K PDF to populate fields &amp; verified facts natively.
              </p>
              {pdfNotice && (
                <p className="text-xs font-semibold text-emerald-700 mt-1.5 flex items-center gap-1.5">
                  <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
                  <span>{pdfNotice}</span>
                </p>
              )}
            </div>
          </div>

          <label className="inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl bg-slate-900 hover:bg-blue-700 text-white text-xs font-bold cursor-pointer transition-colors shadow-sm shrink-0">
            {isExtractingPdf ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>Reading PDF...</span>
              </>
            ) : (
              <>
                <FileUp className="w-4 h-4" />
                <span>Upload PDF</span>
              </>
            )}
            <input
              type="file"
              accept="application/pdf,.pdf"
              onChange={handlePdfUpload}
              disabled={isExtractingPdf}
              className="hidden"
            />
          </label>
        </div>

        {/* Organization Type Radios */}
        <fieldset className="space-y-3">
          <legend className="text-sm font-semibold text-slate-800">
            Organization Type <span className="text-rose-500" aria-hidden="true">*</span>
          </legend>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <label
              className={`relative flex items-center p-4 rounded-xl border cursor-pointer transition-all ${
                orgType === "Startup"
                  ? "bg-blue-50/50 border-blue-600 ring-1 ring-blue-600"
                  : "bg-slate-50/50 border-slate-200 hover:border-blue-400"
              }`}
            >
              <input
                type="radio"
                name="org_type"
                value="Startup"
                checked={orgType === "Startup"}
                onChange={() => setOrgType("Startup")}
                className="accent-blue-600 h-4 w-4"
              />
              <span className="ml-3">
                <span className="block text-sm font-semibold text-slate-900">Startup (For-Profit)</span>
                <span className="block text-xs text-slate-500">Pre-Seed, Seed, Series A, Angel, Venture Capital</span>
              </span>
            </label>

            <label
              className={`relative flex items-center p-4 rounded-xl border cursor-pointer transition-all ${
                orgType === "Non-Profit"
                  ? "bg-emerald-50/50 border-emerald-600 ring-1 ring-emerald-600"
                  : "bg-slate-50/50 border-slate-200 hover:border-emerald-400"
              }`}
            >
              <input
                type="radio"
                name="org_type"
                value="Non-Profit"
                checked={orgType === "Non-Profit"}
                onChange={() => setOrgType("Non-Profit")}
                className="accent-emerald-600 h-4 w-4"
              />
              <span className="ml-3">
                <span className="block text-sm font-semibold text-slate-900">Non-Profit (501(c)(3) / NGO)</span>
                <span className="block text-xs text-slate-500">Philanthropic Grants, Government Programs</span>
              </span>
            </label>
          </div>
        </fieldset>

        {/* Target Jurisdiction (NY, NJ, SF) */}
        <fieldset className="space-y-3">
          <legend className="text-sm font-semibold text-slate-800">
            Target State / Jurisdiction <span className="text-rose-500" aria-hidden="true">*</span>
          </legend>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <label
              className={`relative flex items-center p-3.5 rounded-xl border cursor-pointer transition-all ${
                targetState === "NY"
                  ? "bg-blue-50/50 border-blue-600 ring-1 ring-blue-600"
                  : "bg-slate-50/50 border-slate-200 hover:border-blue-400"
              }`}
            >
              <input
                type="radio"
                name="target_state"
                value="NY"
                checked={targetState === "NY"}
                onChange={() => setTargetState("NY")}
                className="accent-blue-600 h-4 w-4"
              />
              <span className="ml-3">
                <span className="block text-sm font-bold text-slate-900">New York (NY)</span>
                <span className="block text-[11px] text-slate-500 leading-tight mt-0.5">Empire State Dev &amp; NYC Ecosystem</span>
              </span>
            </label>

            <label
              className={`relative flex items-center p-3.5 rounded-xl border cursor-pointer transition-all ${
                targetState === "NJ"
                  ? "bg-blue-50/50 border-blue-600 ring-1 ring-blue-600"
                  : "bg-slate-50/50 border-slate-200 hover:border-blue-400"
              }`}
            >
              <input
                type="radio"
                name="target_state"
                value="NJ"
                checked={targetState === "NJ"}
                onChange={() => setTargetState("NJ")}
                className="accent-blue-600 h-4 w-4"
              />
              <span className="ml-3">
                <span className="block text-sm font-bold text-slate-900">New Jersey (NJ)</span>
                <span className="block text-[11px] text-slate-500 leading-tight mt-0.5">NJEDA &amp; Tech Angel Credits</span>
              </span>
            </label>

            <label
              className={`relative flex items-center p-3.5 rounded-xl border cursor-pointer transition-all ${
                targetState === "SF"
                  ? "bg-blue-50/50 border-blue-600 ring-1 ring-blue-600"
                  : "bg-slate-50/50 border-slate-200 hover:border-blue-400"
              }`}
            >
              <input
                type="radio"
                name="target_state"
                value="SF"
                checked={targetState === "SF"}
                onChange={() => setTargetState("SF")}
                className="accent-blue-600 h-4 w-4"
              />
              <span className="ml-3">
                <span className="block text-sm font-bold text-slate-900">San Francisco, CA (SF)</span>
                <span className="block text-[11px] text-slate-500 leading-tight mt-0.5">Bay Area Grants &amp; CA Competes</span>
              </span>
            </label>
          </div>
        </fieldset>

        {/* Core Organization Details */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
          <div className="flex flex-col">
            <label htmlFor="org-name" className="text-sm font-semibold text-slate-800 mb-1">
              Organization Name <span className="text-rose-500" aria-hidden="true">*</span>
            </label>
            <input
              type="text"
              id="org-name"
              name="org_name"
              required
              value={orgName}
              onChange={(e) => setOrgName(e.target.value)}
              autoComplete="organization"
              enterKeyHint="next"
              placeholder="e.g. Acme Health or CleanWater Global"
              className="border border-slate-300 rounded-lg p-3 text-base min-h-[48px] focus:ring-2 focus:ring-blue-500 focus:outline-none"
            />
          </div>

          <div className="flex flex-col">
            <label htmlFor="contact-email" className="text-sm font-semibold text-slate-800 mb-1">
              Contact Email <span className="text-rose-500" aria-hidden="true">*</span>
            </label>
            <input
              type="email"
              id="contact-email"
              name="contact_email"
              required
              value={contactEmail}
              onChange={(e) => setContactEmail(e.target.value)}
              autoComplete="email"
              enterKeyHint="next"
              placeholder="founder@organization.org"
              className="border border-slate-300 rounded-lg p-3 text-base min-h-[48px] focus:ring-2 focus:ring-blue-500 focus:outline-none"
            />
          </div>
        </div>

        {/* Mission / Pitch */}
        <div className="flex flex-col">
          <label htmlFor="mission" className="text-sm font-semibold text-slate-800 mb-1">
            Mission Statement &amp; Elevator Pitch <span className="text-rose-500" aria-hidden="true">*</span>
          </label>
          <textarea
            id="mission"
            name="mission"
            rows={3}
            required
            maxLength={1000}
            value={mission}
            onChange={(e) => setMission(e.target.value)}
            placeholder="What core problem do you solve, what is your approach, and who benefits?"
            className="border border-slate-300 rounded-lg p-3 text-base focus:ring-2 focus:ring-blue-500 focus:outline-none"
          />
          <span className="text-xs text-slate-400 mt-1">Brief summary (up to 1,000 characters).</span>
        </div>

        {/* Funding Details */}
        <fieldset className="space-y-4 pt-2 border-t border-slate-100">
          <legend className="text-sm font-semibold text-slate-800">Funding Specifications</legend>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
            <div className="flex flex-col">
              <label htmlFor="funding-type" className="text-sm font-semibold text-slate-800 mb-1">
                Primary Funding Instrument <span className="text-rose-500" aria-hidden="true">*</span>
              </label>
              <select
                id="funding-type"
                name="funding_type"
                required
                value={fundingType}
                onChange={(e) => setFundingType(e.target.value)}
                className="border border-slate-300 rounded-lg p-3 text-base min-h-[48px] focus:ring-2 focus:ring-blue-500 focus:outline-none bg-white"
              >
                <option value="Equity Investment (Pre-Seed / SAFE / Angel)">Equity Investment (Pre-Seed / SAFE / Angel)</option>
                <option value="Equity Investment (Seed / Series A)">Equity Investment (Seed / Series A)</option>
                <option value="Government Grant (SBIR / STTR / Horizon)">Government Grant (SBIR / STTR / Horizon)</option>
                <option value="Philanthropic / Foundation Grant">Philanthropic / Foundation Grant</option>
                <option value="Impact Loan / Revenue-Based Financing">Impact Loan / Revenue-Based Financing</option>
                <option value="Corporate Sponsorship / Partnership">Corporate Sponsorship / Partnership</option>
              </select>
            </div>

            <div className="flex flex-col">
              <label htmlFor="funding-amount" className="text-sm font-semibold text-slate-800 mb-1">
                Target Amount (USD $) <span className="text-rose-500" aria-hidden="true">*</span>
              </label>
              <input
                type="text"
                id="funding-amount"
                name="funding_amount"
                required
                value={fundingAmount}
                onChange={(e) => setFundingAmount(e.target.value)}
                inputMode="numeric"
                placeholder="e.g. 500,000"
                className="border border-slate-300 rounded-lg p-3 text-base min-h-[48px] focus:ring-2 focus:ring-blue-500 focus:outline-none"
              />
            </div>
          </div>

          <div className="flex flex-col">
            <label htmlFor="use-of-funds" className="text-sm font-semibold text-slate-800 mb-1">
              Planned Use of Funds <span className="text-rose-500" aria-hidden="true">*</span>
            </label>
            <input
              type="text"
              id="use-of-funds"
              name="use_of_funds"
              required
              value={useOfFunds}
              onChange={(e) => setUseOfFunds(e.target.value)}
              enterKeyHint="done"
              placeholder="e.g. 50% Engineering/Product, 30% Go-to-Market, 20% Operations"
              className="border border-slate-300 rounded-lg p-3 text-base min-h-[48px] focus:ring-2 focus:ring-blue-500 focus:outline-none"
            />
          </div>
        </fieldset>

        {/* Async Background Mode */}
        <div className="bg-slate-50 p-4 rounded-xl border border-slate-200">
          <label htmlFor="async-mode" className="flex items-start gap-3 cursor-pointer">
            <input
              type="checkbox"
              id="async-mode"
              name="async_mode"
              checked={isAsync}
              onChange={(e) => setIsAsync(e.target.checked)}
              className="mt-1 accent-blue-600 rounded"
            />
            <span className="text-sm">
              <span className="font-semibold text-slate-900 block">Optional Webhook Callback Notification</span>
              <span className="text-xs text-slate-500 block">
                Also POST the completed canonical report JSON to an external webhook URL when finished.
              </span>
            </span>
          </label>
          {isAsync && (
            <div className="mt-3">
              <label htmlFor="callback-url" className="block text-xs font-semibold text-slate-700 mb-1">
                Callback URL
              </label>
              <input
                type="url"
                id="callback-url"
                name="callback_url"
                placeholder="https://your-service.com/api/webhook"
                className="w-full border border-slate-300 rounded-md p-2 text-xs focus:ring-2 focus:ring-blue-500 focus:outline-none"
              />
            </div>
          )}
        </div>

        {errorMsg && (
          <div className="p-4 bg-rose-50 border border-rose-200 rounded-xl text-rose-700 text-sm">
            {errorMsg}
          </div>
        )}

        {/* Submit Button */}
        <div>
          <button
            type="submit"
            disabled={isSubmitting}
            className="w-full min-h-[50px] py-3 px-6 bg-blue-600 hover:bg-blue-700 active:bg-blue-800 text-white font-semibold rounded-xl transition-all shadow-md hover:shadow-lg flex items-center justify-center gap-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {isSubmitting ? (
              <>
                <Loader2 className="w-5 h-5 animate-spin" />
                <span>AI Agents Analyzing Intake...</span>
              </>
            ) : (
              <>
                <span>Submit Intake &amp; Run Agent Workflow</span>
                <ArrowRight className="w-4 h-4" />
              </>
            )}
          </button>
        </div>
      </form>
    </div>
  );
}
