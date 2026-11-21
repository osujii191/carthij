"use client";

import { useEffect, useState, use } from "react";
import Link from "next/link";
import Header from "@/components/Header";
import ResultsPanel from "@/components/ResultsPanel";
import { ReportRecord } from "@/types";
import {
  ArrowLeft,
  Clock,
  CheckCircle2,
  AlertCircle,
  Copy,
  Check,
  Bot,
  Layers,
  Network,
  Share2,
} from "lucide-react";

export default function ReportDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [report, setReport] = useState<ReportRecord | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isCopied, setIsCopied] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const fetchReport = async () => {
    try {
      const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const res = await fetch(`${apiBase}/api/v1/reports/${id}`, { cache: "no-store" });
      if (!res.ok) {
        if (res.status === 404) {
          setErrorMsg("Assessment report not found.");
        } else {
          setErrorMsg(`Server error ${res.status}`);
        }
        return;
      }
      const data: ReportRecord = await res.json();
      setReport(data);
    } catch (err: any) {
      setErrorMsg(err.message || "Failed to load report.");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchReport();

    // If report is in_progress, poll every 3 seconds
    const interval = setInterval(() => {
      if (!report || report.status === "in_progress") {
        fetchReport();
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [id, report?.status]);

  const handleCopyLink = () => {
    navigator.clipboard.writeText(window.location.href);
    setIsCopied(true);
    setTimeout(() => setIsCopied(false), 2000);
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

  const isInProgress = report.status === "in_progress";
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
        {/* Navigation & Actions */}
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
              <div className="flex items-center gap-3 mb-2">
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
                Targeting <span className="font-semibold text-slate-800">{report.funding_type}</span> of{" "}
                <span className="font-bold text-slate-900">${report.funding_amount}</span>
              </p>
            </div>

            <div className="flex items-center gap-2">
              {isInProgress ? (
                <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200 text-xs font-semibold animate-pulse">
                  <Clock className="w-4 h-4 animate-spin text-amber-600" />
                  <span>AI Agents Analyzing Intake</span>
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
            <span>Report ID: <code className="font-mono text-slate-600">{report.id}</code></span>
            <span>Submitted on {formattedDate}</span>
          </div>
        </div>

        {/* In-Progress Live Status Card */}
        {isInProgress ? (
          <div className="bg-white rounded-2xl border border-amber-200 shadow-md p-8 md:p-12 text-center">
            <div className="w-16 h-16 rounded-2xl bg-amber-50 text-amber-600 flex items-center justify-center mx-auto mb-6 shadow-inner">
              <Clock className="w-8 h-8 animate-spin" />
            </div>
            <h2 className="text-2xl font-bold text-slate-900 mb-2">
              Multi-Agent Analysis in Progress
            </h2>
            <p className="text-slate-600 max-w-md mx-auto text-sm mb-8">
              The Gemini Coordinator is decomposing your intake and running parallel subagents to evaluate
              market readiness, compliance, and risk factors. This page automatically updates when ready.
            </p>

            {/* Step Indicators */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 max-w-2xl mx-auto text-left">
              <div className="p-4 rounded-xl border border-emerald-200 bg-emerald-50/50 flex items-start gap-3">
                <CheckCircle2 className="w-5 h-5 text-emerald-600 shrink-0 mt-0.5" />
                <div>
                  <h3 className="text-xs font-bold text-emerald-900">1. Intake Received</h3>
                  <p className="text-xs text-emerald-700 mt-0.5">Pitch &amp; targets validated.</p>
                </div>
              </div>

              <div className="p-4 rounded-xl border border-amber-300 bg-amber-50/80 flex items-start gap-3 animate-pulse">
                <Network className="w-5 h-5 text-amber-600 shrink-0 mt-0.5" />
                <div>
                  <h3 className="text-xs font-bold text-amber-900">2. Parallel Subagents</h3>
                  <p className="text-xs text-amber-700 mt-0.5">Evaluating market &amp; risks.</p>
                </div>
              </div>

              <div className="p-4 rounded-xl border border-slate-200 bg-slate-50 flex items-start gap-3 opacity-60">
                <Layers className="w-5 h-5 text-slate-400 shrink-0 mt-0.5" />
                <div>
                  <h3 className="text-xs font-bold text-slate-700">3. Final Synthesis</h3>
                  <p className="text-xs text-slate-500 mt-0.5">Compiling readiness report.</p>
                </div>
              </div>
            </div>
          </div>
        ) : (
          /* Completed Report Panel */
          <ResultsPanel
            results={{
              workflow_id: report.id,
              status: report.status,
              summary: report.summary || "No summary available.",
              subagent_results: report.subagent_results as any,
              metadata: report.metadata,
            }}
          />
        )}
      </main>
    </>
  );
}
