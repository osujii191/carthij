"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { DraftAcceptedResponse, FundingOpportunity, FundingReport } from "@/types";
import SubagentCard from "./SubagentCard";
import {
  CheckCircle,
  ShieldCheck,
  ExternalLink,
  AlertTriangle,
  ListChecks,
  BookOpen,
  Award,
  Sparkles,
  Loader2,
} from "lucide-react";

interface ResultsPanelProps {
  report: FundingReport;
}

const SCOPE_LABELS: Record<string, string> = {
  state: "State",
  local: "Local",
  federal: "Federal",
  foundation: "Foundation",
  private_capital: "Private Capital",
};

const TIMEFRAME_LABELS: Record<string, string> = {
  immediate_7d: "Next 7 Days",
  short_term_30d: "Next 30 Days",
  medium_term_90d: "Next 90 Days",
};

const safeHref = (url: string): string => {
  const trimmed = (url || "").trim();
  return trimmed.startsWith("https://") || trimmed.startsWith("http://") ? trimmed : "#";
};

export default function ResultsPanel({ report }: ResultsPanelProps) {
  const router = useRouter();
  const [scopeFilter, setScopeFilter] = useState<string>("all");
  const [draftingOpp, setDraftingOpp] = useState<string | null>(null);
  const [draftError, setDraftError] = useState<string | null>(null);

  const handleDraftApplication = async (opp: FundingOpportunity) => {
    setDraftError(null);
    setDraftingOpp(opp.name);
    try {
      const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const res = await fetch(`${apiBase}/api/v1/reports/${report.report_id}/drafts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          opportunity_name: opp.name,
          opportunity_source_url: opp.source_url,
        }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `Failed to start draft (${res.status})`);
      }
      const accepted: DraftAcceptedResponse = await res.json();
      router.push(`/reports/${report.report_id}/drafts/${accepted.draft_id}`);
    } catch (err: any) {
      setDraftError(err.message || "Could not launch browser-use application drafter.");
      setDraftingOpp(null);
    }
  };

  const filteredOpportunities = report.opportunities.filter((opp) => {
    if (scopeFilter === "all") return true;
    if (scopeFilter === "state_local") return opp.scope === "state" || opp.scope === "local";
    if (scopeFilter === "federal_foundation")
      return opp.scope === "federal" || opp.scope === "foundation";
    return opp.scope === scopeFilter;
  });

  const score = report.readiness_score ?? null;
  const scoreColor =
    score === null
      ? "bg-slate-100 text-slate-700"
      : score >= 75
      ? "bg-emerald-50 text-emerald-700 border-emerald-200"
      : score >= 50
      ? "bg-amber-50 text-amber-700 border-amber-200"
      : "bg-rose-50 text-rose-700 border-rose-200";

  return (
    <section className="space-y-8">
      {/* Executive Summary + Readiness Score */}
      <div className="bg-white p-6 md:p-8 rounded-2xl border border-slate-200 shadow-sm">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-5 mb-5 border-b border-slate-100">
          <div className="flex items-center gap-2.5">
            <ShieldCheck className="w-6 h-6 text-blue-600" />
            <div>
              <h3 className="text-lg font-bold text-slate-900">
                Executive Synthesis &amp; Strategic Readiness
              </h3>
              <p className="text-xs text-slate-500">
                {report.opportunities.length} verified opportunities &middot;{" "}
                {report.sources.length} grounded web sources
              </p>
            </div>
          </div>

          {score !== null && (
            <div
              className={`inline-flex items-center gap-2 px-4 py-2 rounded-xl border text-sm font-bold ${scoreColor}`}
            >
              <Award className="w-4 h-4" />
              <span>Readiness Score: {score}/100</span>
            </div>
          )}
        </div>

        <div className="text-slate-700 leading-relaxed whitespace-pre-line text-sm">
          {report.executive_summary || "No executive summary available."}
        </div>
      </div>

      {/* Verified Funding Opportunities */}
      <div className="bg-white p-6 md:p-8 rounded-2xl border border-slate-200 shadow-sm">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-6">
          <div className="flex items-center gap-2">
            <CheckCircle className="w-5 h-5 text-emerald-600" />
            <h3 className="text-lg font-bold text-slate-900">
              Verified Funding Opportunities ({filteredOpportunities.length})
            </h3>
          </div>

          <div className="flex flex-wrap items-center gap-1.5 bg-slate-100 p-1 rounded-xl text-xs font-semibold">
            {[
              { id: "all", label: "All" },
              { id: "state_local", label: "State & Local" },
              { id: "federal_foundation", label: "Federal & Foundation" },
              { id: "private_capital", label: "Private Capital" },
            ].map((tab) => (
              <button
                key={tab.id}
                onClick={() => setScopeFilter(tab.id)}
                className={`px-3 py-1 rounded-lg transition-all ${
                  scopeFilter === tab.id
                    ? "bg-white text-slate-900 shadow-sm"
                    : "text-slate-600 hover:text-slate-900"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        {draftError && (
          <div className="mb-4 p-3 rounded-xl bg-rose-50 border border-rose-200 text-xs text-rose-700">
            {draftError}
          </div>
        )}

        {filteredOpportunities.length === 0 ? (
          <p className="text-sm text-slate-500 py-6 text-center">
            No verified opportunities matched this filter.
          </p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {filteredOpportunities.map((opp, idx) => (
              <article
                key={`${opp.name}-${opp.source_id}-${idx}`}
                className="p-5 rounded-xl border border-slate-200 bg-slate-50/40 flex flex-col justify-between gap-4"
              >
                <div>
                  <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                    <span className="text-[11px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-blue-50 text-blue-700 border border-blue-200">
                      {SCOPE_LABELS[opp.scope] || opp.scope} &middot; {opp.instrument}
                    </span>
                    <span
                      className={`text-[11px] font-bold uppercase px-2 py-0.5 rounded ${
                        opp.eligibility_fit === "high"
                          ? "bg-emerald-100 text-emerald-800"
                          : opp.eligibility_fit === "medium"
                          ? "bg-amber-100 text-amber-800"
                          : "bg-slate-200 text-slate-700"
                      }`}
                    >
                      {opp.eligibility_fit} fit
                    </span>
                  </div>

                  <h4 className="text-base font-bold text-slate-900">{opp.name}</h4>
                  <p className="text-xs text-slate-500 mb-3">{opp.provider}</p>

                  <div className="grid grid-cols-2 gap-2 text-xs mb-3 bg-white p-2.5 rounded-lg border border-slate-100">
                    <div>
                      <span className="block text-[10px] uppercase font-bold text-slate-400">
                        Est. Amount
                      </span>
                      <span className="font-semibold text-slate-800">{opp.estimated_amount}</span>
                    </div>
                    <div>
                      <span className="block text-[10px] uppercase font-bold text-slate-400">
                        Deadline
                      </span>
                      <span className="font-semibold text-slate-800">{opp.deadline}</span>
                    </div>
                  </div>

                  <p className="text-xs text-slate-700 leading-relaxed mb-3">{opp.why_it_fits}</p>

                  {opp.key_requirements.length > 0 && (
                    <ul className="text-xs text-slate-600 list-disc pl-4 space-y-1">
                      {opp.key_requirements.slice(0, 4).map((req, i) => (
                        <li key={i}>{req}</li>
                      ))}
                    </ul>
                  )}
                </div>

                <div className="pt-3 border-t border-slate-200/70 flex flex-wrap items-center justify-between gap-2 text-xs">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[11px] text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded border border-emerald-200">
                      {opp.citation_verified ? `Verified ${opp.source_id}` : opp.source_id}
                    </span>
                    <a
                      href={safeHref(opp.source_url)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 font-semibold text-blue-600 hover:underline"
                    >
                      <span>Source</span>
                      <ExternalLink className="w-3.5 h-3.5" />
                    </a>
                  </div>
                  <button
                    type="button"
                    onClick={() => handleDraftApplication(opp)}
                    disabled={draftingOpp !== null}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-900 hover:bg-blue-700 disabled:opacity-60 text-white text-[11px] font-semibold transition-colors shadow-sm"
                  >
                    {draftingOpp === opp.name ? (
                      <>
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        <span>Launching Agent...</span>
                      </>
                    ) : (
                      <>
                        <Sparkles className="w-3.5 h-3.5" />
                        <span>Draft &amp; Pre-Fill</span>
                      </>
                    )}
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}
      </div>

      {/* Readiness Gaps & Prioritized Action Plan */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Readiness Gaps */}
        <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm">
          <div className="flex items-center gap-2 mb-4">
            <AlertTriangle className="w-5 h-5 text-amber-600" />
            <h3 className="text-base font-bold text-slate-900">
              Readiness Gaps &amp; Risks ({report.readiness_gaps.length})
            </h3>
          </div>
          {report.readiness_gaps.length === 0 ? (
            <p className="text-xs text-slate-500">No critical readiness gaps flagged.</p>
          ) : (
            <div className="space-y-3">
              {report.readiness_gaps.map((gap, i) => (
                <div
                  key={i}
                  className="p-4 rounded-xl border border-slate-200 bg-slate-50/50 space-y-1.5"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500">
                      {gap.category}
                    </span>
                    <span
                      className={`text-[10px] font-bold uppercase px-2 py-0.5 rounded ${
                        gap.severity === "high"
                          ? "bg-rose-100 text-rose-700"
                          : gap.severity === "medium"
                          ? "bg-amber-100 text-amber-700"
                          : "bg-slate-200 text-slate-700"
                      }`}
                    >
                      {gap.severity}
                    </span>
                  </div>
                  <p className="text-xs font-semibold text-slate-800">{gap.description}</p>
                  <p className="text-xs text-slate-600">
                    <span className="font-semibold text-slate-700">Fix:</span> {gap.remediation}
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Prioritized Action Plan */}
        <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm">
          <div className="flex items-center gap-2 mb-4">
            <ListChecks className="w-5 h-5 text-blue-600" />
            <h3 className="text-base font-bold text-slate-900">
              Prioritized Action Plan ({report.action_plan.length})
            </h3>
          </div>
          {report.action_plan.length === 0 ? (
            <p className="text-xs text-slate-500">No action items generated.</p>
          ) : (
            <ol className="space-y-3">
              {report.action_plan.map((item, i) => (
                <li
                  key={i}
                  className="p-4 rounded-xl border border-slate-200 bg-slate-50/50 flex items-start gap-3"
                >
                  <span className="w-6 h-6 rounded-full bg-blue-600 text-white text-xs font-bold flex items-center justify-center shrink-0 mt-0.5">
                    {item.priority}
                  </span>
                  <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[11px] font-bold uppercase px-2 py-0.5 rounded bg-blue-50 text-blue-700">
                        {TIMEFRAME_LABELS[item.timeframe] || item.timeframe}
                      </span>
                      {item.related_opportunity && (
                        <span className="text-[11px] text-slate-500">
                          {item.related_opportunity}
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-slate-800 leading-relaxed">{item.action}</p>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>

      {/* Specialist Analyses */}
      {report.specialist_outputs.length > 0 && (
        <div className="bg-slate-50 p-6 rounded-2xl border border-slate-200">
          <h4 className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-4">
            Opinionated Specialist Deep-Dives ({report.specialist_outputs.length} Executed)
          </h4>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {report.specialist_outputs.map((spec, idx) => (
              <SubagentCard
                key={`${spec.specialist_name}-${idx}`}
                result={{
                  subagent_type: spec.specialist_name.replace(/_/g, " "),
                  status: spec.status,
                  output: spec.summary || "",
                  error: spec.error,
                }}
              />
            ))}
          </div>
        </div>
      )}

      {/* Grounded Sources Registry */}
      {report.sources.length > 0 && (
        <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm">
          <div className="flex items-center gap-2 mb-4">
            <BookOpen className="w-5 h-5 text-slate-600" />
            <h3 className="text-base font-bold text-slate-900">
              Verified Source Registry ({report.sources.length})
            </h3>
          </div>
          <div className="divide-y divide-slate-100">
            {report.sources.map((src) => (
              <div key={src.source_id} className="py-3 flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className="font-mono text-[11px] font-bold text-slate-600 bg-slate-100 px-1.5 py-0.5 rounded">
                      {src.source_id}
                    </span>
                    {src.is_extracted && (
                      <span className="text-[10px] font-semibold text-emerald-700 bg-emerald-50 px-1.5 py-0.5 rounded">
                        Full Extract
                      </span>
                    )}
                    <a
                      href={safeHref(src.url)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs font-semibold text-blue-600 hover:underline truncate"
                    >
                      {src.title || src.url}
                    </a>
                  </div>
                  <p className="text-xs text-slate-500 line-clamp-2">{src.excerpt}</p>
                </div>
                <span className="text-[11px] font-mono text-slate-400 shrink-0">
                  {src.score.toFixed(2)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
