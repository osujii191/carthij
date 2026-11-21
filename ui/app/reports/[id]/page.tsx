"use client";

import { useEffect, useState, use } from "react";
import Link from "next/link";
import Header from "@/components/Header";
import ResultsPanel from "@/components/ResultsPanel";
import { FundingReport, StageName, StageProgress } from "@/types";
import {
  ArrowLeft,
  Clock,
  CheckCircle2,
  AlertCircle,
  Check,
  Share2,
  Loader2,
} from "lucide-react";

const PIPELINE_STEPS: { stage: StageName; label: string; description: string }[] = [
  { stage: "query_planning", label: "Query Understanding", description: "Normalizing intake & planning searches" },
  { stage: "searching", label: "Parallel Web Search", description: "Querying Tavily across jurisdictions" },
  { stage: "extracting", label: "Source Extraction", description: "Reading top authoritative program pages" },
  { stage: "analyzing_specialists", label: "Specialist Agents", description: "Running 4 opinionated specialists in parallel" },
  { stage: "verifying_and_synthesizing", label: "Grounding & Synthesis", description: "Verifying citations & compiling report" },
];

export default function ReportDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [report, setReport] = useState<FundingReport | null>(null);
  const [liveStages, setLiveStages] = useState<StageProgress[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isCopied, setIsCopied] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  const fetchReport = async (): Promise<FundingReport | null> => {
    try {
      const res = await fetch(`${apiBase}/api/v1/reports/${id}`, { cache: "no-store" });
      if (!res.ok) {
        setErrorMsg(res.status === 404 ? "Assessment report not found." : `Server error ${res.status}`);
        return null;
      }
      const data: FundingReport = await res.json();
      setReport(data);
      if (data.stage_history?.length) {
        setLiveStages(data.stage_history);
      }
      return data;
    } catch (err: any) {
      setErrorMsg(err.message || "Failed to load report.");
      return null;
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    let eventSource: EventSource | null = null;
    let fallbackPoll: ReturnType<typeof setInterval> | null = null;
    let cancelled = false;

    const startFallbackPoll = () => {
      if (fallbackPoll || cancelled) return;
      fallbackPoll = setInterval(async () => {
        const latest = await fetchReport();
        if (latest && (latest.status === "completed" || latest.status === "failed")) {
          if (fallbackPoll) {
            clearInterval(fallbackPoll);
            fallbackPoll = null;
          }
        }
      }, 4000);
    };

    const init = async () => {
      const data = await fetchReport();
      if (cancelled || !data) return;

      if (data.status === "queued" || data.status === "in_progress") {
        eventSource = new EventSource(`${apiBase}/api/v1/reports/${id}/stream`);

        eventSource.addEventListener("stage", (evt) => {
          try {
            const parsed: StageProgress = JSON.parse((evt as MessageEvent).data);
            setLiveStages((prev) => {
              const exists = prev.some(
                (s) => s.stage === parsed.stage && s.timestamp === parsed.timestamp
              );
              return exists ? prev : [...prev, parsed];
            });
          } catch {
            // ignore malformed event
          }
        });

        eventSource.addEventListener("complete", () => {
          eventSource?.close();
          if (fallbackPoll) clearInterval(fallbackPoll);
          fetchReport();
        });

        eventSource.addEventListener("timeout", () => {
          eventSource?.close();
          startFallbackPoll();
        });

        eventSource.addEventListener("error", (evt) => {
          const msgData = (evt as MessageEvent).data;
          if (msgData) {
            // Explicit server-sent terminal error event
            eventSource?.close();
            if (fallbackPoll) clearInterval(fallbackPoll);
            fetchReport();
          } else if (eventSource?.readyState === EventSource.CLOSED) {
            // Browser gave up reconnecting -> switch seamlessly to polling fallback
            startFallbackPoll();
          }
        });
      }
    };

    init();

    return () => {
      cancelled = true;
      eventSource?.close();
      if (fallbackPoll) clearInterval(fallbackPoll);
    };
  }, [id]);

  const handleCopyLink = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), 2000);
    } catch {
      // Clipboard API unavailable (non-HTTPS or restricted iframe)
    }
  };

  if (isLoading) {
    return (
      <>
        <Header />
        <main className="flex-1 max-w-4xl mx-auto px-4 py-16 text-center">
          <Clock className="w-10 h-10 animate-spin text-blue-600 mx-auto mb-4" />
          <h2 className="text-xl font-bold text-slate-800">Retrieving Assessment Report...</h2>
        </main>
      </>
    );
  }

  if (errorMsg || !report) {
    return (
      <>
        <Header />
        <main className="flex-1 max-w-4xl mx-auto px-4 py-16 text-center">
          <AlertCircle className="w-12 h-12 text-rose-500 mx-auto mb-4" />
          <h2 className="text-2xl font-bold text-slate-900 mb-2">{errorMsg || "Report Not Found"}</h2>
          <p className="text-slate-600 mb-6">
            The assessment report you requested may have expired or does not exist.
          </p>
          <Link
            href="/reports"
            className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-semibold hover:bg-blue-700 transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            <span>Back to Dashboard</span>
          </Link>
        </main>
      </>
    );
  }

  const isRunning = report.status === "queued" || report.status === "in_progress";
  const isFailed = report.status === "failed";
  const fundingType = String(report.intake_data?.target_funding_instrument || "Funding Intake");
  const fundingAmount = String(report.intake_data?.target_amount_usd || "—").replace(/^\$/, "");
  const seenStages = new Set(liveStages.map((s) => s.stage));
  const latestStage = liveStages[liveStages.length - 1];
  const formattedDate = new Date(report.created_at * 1000).toLocaleString(undefined, {
    month: "long",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });

  return (
    <>
      <Header />
      <main className="flex-1 max-w-5xl mx-auto px-4 py-8 md:py-12 w-full">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8">
          <Link
            href="/reports"
            className="inline-flex items-center gap-2 text-sm font-semibold text-slate-600 hover:text-blue-600 transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            <span>Back to Reports Dashboard</span>
          </Link>

          <button
            onClick={handleCopyLink}
            className="inline-flex items-center gap-2 px-3 py-1.5 text-xs font-semibold text-slate-700 bg-white border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors shadow-sm self-start sm:self-auto"
          >
            {isCopied ? (
              <>
                <Check className="w-3.5 h-3.5 text-emerald-600" />
                <span className="text-emerald-700">Link Copied!</span>
              </>
            ) : (
              <>
                <Share2 className="w-3.5 h-3.5 text-slate-500" />
                <span>Share Permlink</span>
              </>
            )}
          </button>
        </div>

        {/* Overview Header Banner */}
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 md:p-8 mb-8">
          <div className="flex flex-wrap items-center justify-between gap-4 pb-6 border-b border-slate-100">
            <div>
              <div className="flex flex-wrap items-center gap-3 mb-2">
                <h1 className="text-3xl font-extrabold text-slate-900 tracking-tight">
                  {report.org_name}
                </h1>
                <span className="text-xs px-2.5 py-0.5 rounded-full font-semibold bg-slate-100 text-slate-700">
                  {report.org_type}
                </span>
                {report.target_state && (
                  <span className="text-xs px-2.5 py-0.5 rounded-full font-bold bg-blue-50 text-blue-700 border border-blue-200">
                    {report.target_state} Jurisdiction
                  </span>
                )}
              </div>
              <p className="text-sm text-slate-500">
                Targeting <span className="font-semibold text-slate-800">{fundingType}</span> of{" "}
                <span className="font-bold text-slate-900">${fundingAmount}</span>
              </p>
            </div>

            <div className="flex items-center gap-2">
              {isRunning ? (
                <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200 text-xs font-semibold animate-pulse">
                  <Loader2 className="w-4 h-4 animate-spin text-amber-600" />
                  <span>Live Analysis Running</span>
                </div>
              ) : isFailed ? (
                <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-rose-50 text-rose-700 border border-rose-200 text-xs font-semibold">
                  <AlertCircle className="w-4 h-4 text-rose-600" />
                  <span>Assessment Failed</span>
                </div>
              ) : (
                <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200 text-xs font-semibold">
                  <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                  <span>Assessment Completed</span>
                </div>
              )}
            </div>
          </div>

          <div className="pt-4 flex flex-wrap items-center justify-between text-xs text-slate-400 gap-2">
            <span>
              Report ID: <code className="font-mono text-slate-600">{report.report_id}</code>
            </span>
            <span>Submitted on {formattedDate}</span>
          </div>
        </div>

        {/* Live SSE Progress View vs. Failed vs. Completed Canonical Report */}
        {isRunning ? (
          <div className="bg-white rounded-2xl border border-amber-200 shadow-md p-8 md:p-10">
            <div className="text-center mb-8">
              <div className="w-14 h-14 rounded-2xl bg-amber-50 text-amber-600 flex items-center justify-center mx-auto mb-4">
                <Loader2 className="w-7 h-7 animate-spin" />
              </div>
              <h2 className="text-2xl font-bold text-slate-900 mb-1">
                Multi-Agent Pipeline Running
              </h2>
              <p className="text-slate-600 text-sm">
                {latestStage?.message || "Streaming live progress from the backend..."}
              </p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
              {PIPELINE_STEPS.map((step, index) => {
                const isCurrent = latestStage?.stage === step.stage;
                const isDone =
                  seenStages.has(step.stage) &&
                  !isCurrent &&
                  PIPELINE_STEPS.findIndex((s) => s.stage === latestStage?.stage) > index;

                return (
                  <div
                    key={step.stage}
                    className={`p-4 rounded-xl border transition-all ${
                      isDone
                        ? "border-emerald-200 bg-emerald-50/50"
                        : isCurrent
                        ? "border-amber-300 bg-amber-50/80 animate-pulse"
                        : "border-slate-200 bg-slate-50 opacity-60"
                    }`}
                  >
                    <div className="flex items-center gap-2 mb-1">
                      {isDone ? (
                        <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
                      ) : isCurrent ? (
                        <Loader2 className="w-4 h-4 text-amber-600 animate-spin shrink-0" />
                      ) : (
                        <Clock className="w-4 h-4 text-slate-400 shrink-0" />
                      )}
                      <h3 className="text-xs font-bold text-slate-900">{step.label}</h3>
                    </div>
                    <p className="text-[11px] text-slate-600 leading-snug">{step.description}</p>
                  </div>
                );
              })}
            </div>
          </div>
        ) : isFailed ? (
          <div className="bg-white rounded-2xl border border-rose-200 shadow-sm p-8 text-center">
            <AlertCircle className="w-10 h-10 text-rose-500 mx-auto mb-3" />
            <h2 className="text-xl font-bold text-slate-900 mb-2">Pipeline Execution Failed</h2>
            <p className="text-sm text-rose-700 max-w-xl mx-auto">
              {report.executive_summary || "An unexpected error occurred during analysis."}
            </p>
          </div>
        ) : (
          <ResultsPanel report={report} />
        )}
      </main>
    </>
  );
}
