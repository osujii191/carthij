"use client";

import { useState, useId } from "react";
import { useRouter } from "next/navigation";
import { WorkflowExecutionResponse, FormSubmissionPayload } from "@/types";
import ResultsPanel from "./ResultsPanel";
import { Loader2, ArrowRight } from "lucide-react";

export default function IntakeForm() {
  const router = useRouter();
  const formHeadingId = useId();
  const [orgType, setOrgType] = useState<"Startup" | "Non-Profit">("Startup");
  const [targetState, setTargetState] = useState<"NY" | "NJ" | "SF">("NY");
  const [isAsync, setIsAsync] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [results, setResults] = useState<WorkflowExecutionResponse | null>(null);

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
    const orgName = (formData.get("org_name") as string) || "";
    const contactEmail = (formData.get("contact_email") as string) || "";
    const mission = (formData.get("mission") as string) || "";
    const fundingType = (formData.get("funding_type") as string) || "";
    const fundingAmount = (formData.get("funding_amount") as string) || "";
    const useOfFunds = (formData.get("use_of_funds") as string) || "";
    const callbackUrl = (formData.get("callback_url") as string) || null;

    const payload: FormSubmissionPayload = {
      form_id: `intake_${orgType.toLowerCase()}_${targetState.toLowerCase()}`,
      submitter_email: contactEmail,
      data: {
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
      const endpoint =
        isAsync && callbackUrl
          ? `${apiBase}/api/v1/webhook/submit?async_mode=true`
          : `${apiBase}/api/v1/webhook/submit`;

      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        throw new Error(errorData.detail || `Server returned error status ${res.status}`);
      }

      const data: WorkflowExecutionResponse = await res.json();
      setResults(data);
      if (data.workflow_id && data.workflow_id !== "pending") {
        router.push(`/reports/${data.workflow_id}`);
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
              <span className="font-semibold text-slate-900 block">Async Background Webhook Dispatch</span>
              <span className="text-xs text-slate-500 block">
                Deliver results to an external callback URL instead of waiting synchronously.
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

      {results && <ResultsPanel results={results} />}
    </div>
  );
}
