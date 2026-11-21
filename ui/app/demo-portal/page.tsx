"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  Shield,
  CheckCircle2,
  AlertTriangle,
  ArrowRight,
  ArrowLeft,
  Save,
  Send,
  Building2,
  FileText,
  ClipboardCheck,
} from "lucide-react";

function DemoPortalContent() {
  const searchParams = useSearchParams();
  const initialProgram = searchParams.get("program") || "State Innovation & Workforce Grant Program";

  const [step, setStep] = useState<1 | 2 | 3>(1);

  // Step 1: Organization & Compliance
  const [orgLegalName, setOrgLegalName] = useState("");
  const [orgType, setOrgType] = useState("For-Profit Startup");
  const [jurisdiction, setJurisdiction] = useState("NY");
  const [contactEmail, setContactEmail] = useState("");
  const [federalEin, setFederalEin] = useState("");
  const [samUei, setSamUei] = useState("");

  // Step 2: Proposal Narrative & Budget
  const [targetProgramName, setTargetProgramName] = useState(initialProgram);
  const [projectTitle, setProjectTitle] = useState("");
  const [projectAbstract, setProjectAbstract] = useState("");
  const [requestedAmountUsd, setRequestedAmountUsd] = useState("");
  const [budgetBreakdown, setBudgetBreakdown] = useState("");
  const [attestEligibility, setAttestEligibility] = useState(false);
  const [attestNonLobbying, setAttestNonLobbying] = useState(false);

  // Step 3 status flags
  const [draftSaved, setDraftSaved] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const abstractWordCount = projectAbstract.trim()
    ? projectAbstract.trim().split(/\s+/).length
    : 0;

  const missingForFinalSubmit: string[] = [];
  if (!orgLegalName.trim()) missingForFinalSubmit.push("Organization Legal Name");
  if (!contactEmail.trim()) missingForFinalSubmit.push("Primary Contact Email");
  if (!federalEin.trim()) missingForFinalSubmit.push("Federal EIN (Tax ID)");
  if (!samUei.trim()) missingForFinalSubmit.push("SAM.gov Unique Entity ID (UEI)");
  if (!projectTitle.trim()) missingForFinalSubmit.push("Project Title");
  if (!projectAbstract.trim()) missingForFinalSubmit.push("Executive Project Abstract");
  if (!requestedAmountUsd.trim()) missingForFinalSubmit.push("Requested Funding Amount");

  return (
    <div className="min-h-screen bg-slate-100 text-slate-900">
      {/* Official-Style Portal Header */}
      <header className="bg-slate-900 text-white border-b border-slate-800">
        <div className="max-w-4xl mx-auto px-6 py-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-blue-600 flex items-center justify-center">
              <Shield className="w-5 h-5 text-white" />
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-widest text-blue-300 font-bold">
                Public Grant Application Portal (Demo Sandbox)
              </p>
              <h1 className="text-lg font-extrabold tracking-tight">{targetProgramName}</h1>
            </div>
          </div>
          <span className="text-xs font-mono bg-slate-800 text-slate-300 px-3 py-1 rounded-full border border-slate-700">
            FY2026 Cycle &middot; Step {step} of 3
          </span>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-6 py-8">
        {/* Progress Stepper */}
        <nav aria-label="Application steps" className="grid grid-cols-3 gap-3 mb-8">
          {[
            { num: 1 as const, label: "1. Organization & Compliance", icon: Building2 },
            { num: 2 as const, label: "2. Narrative & Budget", icon: FileText },
            { num: 3 as const, label: "3. Review & Submit", icon: ClipboardCheck },
          ].map((s) => {
            const Icon = s.icon;
            const active = step === s.num;
            const done = step > s.num;
            return (
              <button
                key={s.num}
                type="button"
                onClick={() => setStep(s.num)}
                className={`p-3.5 rounded-xl border text-left flex items-center gap-2.5 transition-all ${
                  active
                    ? "bg-white border-blue-600 ring-2 ring-blue-100 text-blue-900 shadow-sm"
                    : done
                    ? "bg-emerald-50 border-emerald-200 text-emerald-800"
                    : "bg-white/70 border-slate-200 text-slate-500"
                }`}
              >
                <Icon className="w-4 h-4 shrink-0" />
                <span className="text-xs font-bold">{s.label}</span>
              </button>
            );
          })}
        </nav>

        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
          {step === 1 && (
            <section className="p-6 md:p-8 space-y-6" aria-label="Step 1 Organization and Compliance">
              <div className="border-b border-slate-100 pb-4">
                <h2 className="text-xl font-bold text-slate-900">
                  Step 1: Organization &amp; Compliance Profile
                </h2>
                <p className="text-xs text-slate-500 mt-1">
                  You may continue to Step 2 if Federal EIN or SAM.gov UEI are not yet on hand;
                  missing compliance IDs will be flagged on the final Review screen.
                </p>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                <div>
                  <label htmlFor="org_legal_name" className="block text-sm font-semibold text-slate-800 mb-1">
                    Organization Legal Name *
                  </label>
                  <input
                    id="org_legal_name"
                    name="org_legal_name"
                    type="text"
                    required
                    value={orgLegalName}
                    onChange={(e) => setOrgLegalName(e.target.value)}
                    placeholder="e.g. Hudson Grid Bio, Inc."
                    className="w-full border border-slate-300 rounded-lg px-3.5 py-2.5 text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none"
                  />
                </div>

                <div>
                  <label htmlFor="org_type" className="block text-sm font-semibold text-slate-800 mb-1">
                    Organization Entity Type *
                  </label>
                  <select
                    id="org_type"
                    name="org_type"
                    value={orgType}
                    onChange={(e) => setOrgType(e.target.value)}
                    className="w-full border border-slate-300 rounded-lg px-3.5 py-2.5 text-sm bg-white focus:ring-2 focus:ring-blue-500 focus:outline-none"
                  >
                    <option value="For-Profit Startup">For-Profit Startup</option>
                    <option value="501(c)(3) Non-Profit">501(c)(3) Non-Profit</option>
                    <option value="Public Benefit Corporation">Public Benefit Corporation</option>
                  </select>
                </div>

                <div>
                  <label htmlFor="jurisdiction" className="block text-sm font-semibold text-slate-800 mb-1">
                    Primary State Jurisdiction *
                  </label>
                  <select
                    id="jurisdiction"
                    name="jurisdiction"
                    value={jurisdiction}
                    onChange={(e) => setJurisdiction(e.target.value)}
                    className="w-full border border-slate-300 rounded-lg px-3.5 py-2.5 text-sm bg-white focus:ring-2 focus:ring-blue-500 focus:outline-none"
                  >
                    <option value="NY">New York (NY)</option>
                    <option value="NJ">New Jersey (NJ)</option>
                    <option value="SF">California / San Francisco (SF)</option>
                  </select>
                </div>

                <div>
                  <label htmlFor="contact_email" className="block text-sm font-semibold text-slate-800 mb-1">
                    Primary Contact Email *
                  </label>
                  <input
                    id="contact_email"
                    name="contact_email"
                    type="email"
                    required
                    value={contactEmail}
                    onChange={(e) => setContactEmail(e.target.value)}
                    placeholder="grants@organization.org"
                    className="w-full border border-slate-300 rounded-lg px-3.5 py-2.5 text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none"
                  />
                </div>

                <div>
                  <label htmlFor="federal_ein" className="block text-sm font-semibold text-slate-800 mb-1">
                    Federal EIN (Tax ID)
                    <span className="ml-1.5 text-[11px] font-normal text-amber-700">
                      (Required before final submit)
                    </span>
                  </label>
                  <input
                    id="federal_ein"
                    name="federal_ein"
                    type="text"
                    value={federalEin}
                    onChange={(e) => setFederalEin(e.target.value)}
                    placeholder="XX-XXXXXXX"
                    className="w-full border border-slate-300 rounded-lg px-3.5 py-2.5 text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none"
                  />
                </div>

                <div>
                  <label htmlFor="sam_uei" className="block text-sm font-semibold text-slate-800 mb-1">
                    SAM.gov Unique Entity ID (UEI)
                    <span className="ml-1.5 text-[11px] font-normal text-amber-700">
                      (Required before final submit)
                    </span>
                  </label>
                  <input
                    id="sam_uei"
                    name="sam_uei"
                    type="text"
                    value={samUei}
                    onChange={(e) => setSamUei(e.target.value)}
                    placeholder="12-character alphanumeric UEI"
                    className="w-full border border-slate-300 rounded-lg px-3.5 py-2.5 text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none"
                  />
                </div>
              </div>

              <div className="pt-4 border-t border-slate-100 flex justify-end">
                <button
                  id="next_step_btn"
                  type="button"
                  onClick={() => setStep(2)}
                  className="inline-flex items-center gap-2 px-5 py-2.5 bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold rounded-xl shadow-sm transition-colors"
                >
                  <span>Next: Proposal Narrative</span>
                  <ArrowRight className="w-4 h-4" />
                </button>
              </div>
            </section>
          )}

          {step === 2 && (
            <section className="p-6 md:p-8 space-y-6" aria-label="Step 2 Proposal Narrative and Budget">
              <div className="border-b border-slate-100 pb-4">
                <h2 className="text-xl font-bold text-slate-900">
                  Step 2: Proposal Narrative &amp; Budget Breakdown
                </h2>
                <p className="text-xs text-slate-500 mt-1">
                  Provide a concise, grounded project summary (maximum 250 words) and requested budget allocation.
                </p>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                <div>
                  <label htmlFor="target_program_name" className="block text-sm font-semibold text-slate-800 mb-1">
                    Target Funding Program Name *
                  </label>
                  <input
                    id="target_program_name"
                    name="target_program_name"
                    type="text"
                    value={targetProgramName}
                    onChange={(e) => setTargetProgramName(e.target.value)}
                    className="w-full border border-slate-300 rounded-lg px-3.5 py-2.5 text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none"
                  />
                </div>

                <div>
                  <label htmlFor="requested_amount_usd" className="block text-sm font-semibold text-slate-800 mb-1">
                    Requested Funding Amount (USD) *
                  </label>
                  <input
                    id="requested_amount_usd"
                    name="requested_amount_usd"
                    type="text"
                    value={requestedAmountUsd}
                    onChange={(e) => setRequestedAmountUsd(e.target.value)}
                    placeholder="e.g. 250,000"
                    className="w-full border border-slate-300 rounded-lg px-3.5 py-2.5 text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none"
                  />
                </div>
              </div>

              <div>
                <label htmlFor="project_title" className="block text-sm font-semibold text-slate-800 mb-1">
                  Project Title *
                </label>
                <input
                  id="project_title"
                  name="project_title"
                  type="text"
                  maxLength={140}
                  value={projectTitle}
                  onChange={(e) => setProjectTitle(e.target.value)}
                  placeholder="Descriptive title for the proposed project"
                  className="w-full border border-slate-300 rounded-lg px-3.5 py-2.5 text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none"
                />
              </div>

              <div>
                <div className="flex items-center justify-between mb-1">
                  <label htmlFor="project_abstract" className="text-sm font-semibold text-slate-800">
                    Executive Project Abstract (Max 250 Words) *
                  </label>
                  <span
                    className={`text-xs font-mono font-semibold ${
                      abstractWordCount > 250 ? "text-rose-600" : "text-slate-500"
                    }`}
                  >
                    {abstractWordCount} / 250 words
                  </span>
                </div>
                <textarea
                  id="project_abstract"
                  name="project_abstract"
                  rows={5}
                  maxLength={2000}
                  value={projectAbstract}
                  onChange={(e) => setProjectAbstract(e.target.value)}
                  placeholder="Describe the core problem, technical or programmatic approach, target beneficiaries, and measurable outcomes."
                  className="w-full border border-slate-300 rounded-lg px-3.5 py-2.5 text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none"
                />
              </div>

              <div>
                <label htmlFor="budget_breakdown" className="block text-sm font-semibold text-slate-800 mb-1">
                  Budget &amp; Use of Funds Justification *
                </label>
                <textarea
                  id="budget_breakdown"
                  name="budget_breakdown"
                  rows={3}
                  maxLength={1200}
                  value={budgetBreakdown}
                  onChange={(e) => setBudgetBreakdown(e.target.value)}
                  placeholder="Itemize how requested funds will be allocated across personnel, equipment, pilots, and compliance."
                  className="w-full border border-slate-300 rounded-lg px-3.5 py-2.5 text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none"
                />
              </div>

              <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-3">
                <label htmlFor="attest_eligibility" className="flex items-start gap-2.5 text-xs text-slate-700 cursor-pointer">
                  <input
                    id="attest_eligibility"
                    name="attest_eligibility"
                    type="checkbox"
                    checked={attestEligibility}
                    onChange={(e) => setAttestEligibility(e.target.checked)}
                    className="mt-0.5 accent-blue-600"
                  />
                  <span>
                    I attest that the applicant organization operates within the selected jurisdiction and meets baseline eligibility rules.
                  </span>
                </label>

                <label htmlFor="attest_non_lobbying" className="flex items-start gap-2.5 text-xs text-slate-700 cursor-pointer">
                  <input
                    id="attest_non_lobbying"
                    name="attest_non_lobbying"
                    type="checkbox"
                    checked={attestNonLobbying}
                    onChange={(e) => setAttestNonLobbying(e.target.checked)}
                    className="mt-0.5 accent-blue-600"
                  />
                  <span>
                    I certify that requested grant funds will not be used for prohibited lobbying or political activities.
                  </span>
                </label>
              </div>

              <div className="pt-4 border-t border-slate-100 flex items-center justify-between">
                <button
                  type="button"
                  onClick={() => setStep(1)}
                  className="inline-flex items-center gap-1.5 px-4 py-2 text-sm font-semibold text-slate-600 hover:text-slate-900"
                >
                  <ArrowLeft className="w-4 h-4" />
                  <span>Back to Step 1</span>
                </button>
                <button
                  id="review_step_btn"
                  type="button"
                  onClick={() => setStep(3)}
                  className="inline-flex items-center gap-2 px-5 py-2.5 bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold rounded-xl shadow-sm transition-colors"
                >
                  <span>Continue to Review</span>
                  <ArrowRight className="w-4 h-4" />
                </button>
              </div>
            </section>
          )}

          {step === 3 && (
            <section className="p-6 md:p-8 space-y-6" aria-label="Step 3 Review and Submit">
              <div className="border-b border-slate-100 pb-4 flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-xl font-bold text-slate-900">
                    Step 3: Review Application Before Submission
                  </h2>
                  <p className="text-xs text-slate-500 mt-1">
                    Human sign-off required. Verify all fields and complete any missing compliance identifiers before clicking Submit.
                  </p>
                </div>
                {draftSaved && (
                  <span className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-1 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    Draft Saved
                  </span>
                )}
              </div>

              {missingForFinalSubmit.length > 0 && (
                <div className="p-4 rounded-xl bg-amber-50 border border-amber-200 flex items-start gap-3">
                  <AlertTriangle className="w-5 h-5 text-amber-600 shrink-0 mt-0.5" />
                  <div className="text-xs text-amber-900">
                    <p className="font-bold mb-1">
                      Required before final submission ({missingForFinalSubmit.length} missing):
                    </p>
                    <ul className="list-disc pl-4 space-y-0.5">
                      {missingForFinalSubmit.map((m) => (
                        <li key={m}>{m}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              )}

              <dl className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs bg-slate-50 p-5 rounded-xl border border-slate-200">
                <div>
                  <dt className="font-bold uppercase text-[10px] text-slate-400">Organization</dt>
                  <dd className="text-slate-900 font-semibold mt-0.5">{orgLegalName || "—"}</dd>
                </div>
                <div>
                  <dt className="font-bold uppercase text-[10px] text-slate-400">Entity / Jurisdiction</dt>
                  <dd className="text-slate-900 font-semibold mt-0.5">
                    {orgType} &middot; {jurisdiction}
                  </dd>
                </div>
                <div>
                  <dt className="font-bold uppercase text-[10px] text-slate-400">Contact Email</dt>
                  <dd className="text-slate-900 font-semibold mt-0.5">{contactEmail || "—"}</dd>
                </div>
                <div>
                  <dt className="font-bold uppercase text-[10px] text-slate-400">Federal EIN / SAM.gov UEI</dt>
                  <dd className="text-slate-900 font-semibold mt-0.5">
                    {federalEin || "Missing EIN"} &middot; {samUei || "Missing UEI"}
                  </dd>
                </div>
                <div className="md:col-span-2">
                  <dt className="font-bold uppercase text-[10px] text-slate-400">Project Title &amp; Request</dt>
                  <dd className="text-slate-900 font-semibold mt-0.5">
                    {projectTitle || "—"} (${requestedAmountUsd.replace(/^\$/, "") || "—"})
                  </dd>
                </div>
                <div className="md:col-span-2">
                  <dt className="font-bold uppercase text-[10px] text-slate-400">Executive Abstract</dt>
                  <dd className="text-slate-700 mt-1 whitespace-pre-line">{projectAbstract || "—"}</dd>
                </div>
                <div className="md:col-span-2">
                  <dt className="font-bold uppercase text-[10px] text-slate-400">Budget Breakdown</dt>
                  <dd className="text-slate-700 mt-1 whitespace-pre-line">{budgetBreakdown || "—"}</dd>
                </div>
              </dl>

              {submitted && (
                <div className="p-4 rounded-xl bg-emerald-50 border border-emerald-200 text-emerald-900 text-sm font-semibold flex items-center gap-2">
                  <CheckCircle2 className="w-5 h-5 text-emerald-600" />
                  <span>Demo application submitted successfully!</span>
                </div>
              )}

              <div className="pt-4 border-t border-slate-100 flex flex-wrap items-center justify-between gap-3">
                <button
                  type="button"
                  onClick={() => setStep(2)}
                  className="inline-flex items-center gap-1.5 px-4 py-2 text-sm font-semibold text-slate-600 hover:text-slate-900"
                >
                  <ArrowLeft className="w-4 h-4" />
                  <span>Back to Narrative</span>
                </button>

                <div className="flex items-center gap-3">
                  <button
                    id="save_draft_btn"
                    type="button"
                    onClick={() => setDraftSaved(true)}
                    className="inline-flex items-center gap-2 px-4 py-2.5 bg-white border border-slate-300 hover:bg-slate-50 text-slate-800 text-sm font-semibold rounded-xl shadow-sm"
                  >
                    <Save className="w-4 h-4" />
                    <span>Save Draft</span>
                  </button>
                  <button
                    id="final_submit_btn"
                    type="button"
                    disabled={missingForFinalSubmit.length > 0}
                    onClick={() => setSubmitted(true)}
                    className="inline-flex items-center gap-2 px-5 py-2.5 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-semibold rounded-xl shadow-sm"
                  >
                    <Send className="w-4 h-4" />
                    <span>Submit Final Application</span>
                  </button>
                </div>
              </div>
            </section>
          )}
        </div>
      </main>
    </div>
  );
}

export default function DemoGrantPortalPage() {
  return (
    <Suspense fallback={<div className="p-12 text-center text-sm text-slate-500">Loading Demo Portal...</div>}>
      <DemoPortalContent />
    </Suspense>
  );
}
