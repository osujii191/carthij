"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import Header from "@/components/Header";
import { ApplicationDraft, BrowserStepEvent } from "@/types";
import {
  ArrowLeft,
  CheckCircle2,
  AlertTriangle,
  AlertCircle,
  Loader2,
  Download,
  ExternalLink,
  RefreshCw,
  Monitor,
  ShieldCheck,
  Copy,
  Check,
} from "lucide-react";

const safeHref = (url: string): string => {
  const trimmed = (url || "").trim();
  return trimmed.startsWith("https://") || trimmed.startsWith("http://") ? trimmed : "#";
};

export default function DraftWorkspacePage({
  params,
}: {
  params: Promise<{ id: string; draftId: string }>;
}) {
  const { id: reportId, draftId } = use(params);
  const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  const [draft, setDraft] = useState<ApplicationDraft | null>(null);
  const [liveSteps, setLiveSteps] = useState<BrowserStepEvent[]>([]);
  const [liveScreenshot, setLiveScreenshot] = useState<string | null>(null);
  const [missingAnswers, setMissingAnswers] = useState<Record<string, string>>({});
  const [isLoading, setIsLoading] = useState(true);
  const [isRevising, setIsRevising] = useState(false);
  const [copiedNarrative, setCopiedNarrative] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [streamGeneration, setStreamGeneration] = useState(0);

  const fetchDraft = async (): Promise<ApplicationDraft | null> => {
    try {
      const res = await fetch(`${apiBase}/api/v1/drafts/${draftId}`, { cache: "no-store" });
      if (!res.ok) {
        setErrorMsg(res.status === 404 ? "Application draft not found." : `Server error ${res.status}`);
        return null;
      }
      const data: ApplicationDraft = await res.json();
      setDraft(data);
      if (data.step_log?.length) setLiveSteps(data.step_log);
      if (data.latest_screenshot_b64) setLiveScreenshot(data.latest_screenshot_b64);
      setMissingAnswers((prev) => {
        const next = { ...prev };
        for (const item of data.missing_inputs || []) {
          if (!(item.field_key in next)) {
            next[item.field_key] = data.user_supplied_facts?.[item.field_key] || "";
          }
        }
        return next;
      });
      return data;
    } catch (err: any) {
      setErrorMsg(err.message || "Failed to fetch application draft.");
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
        const latest = await fetchDraft();
        if (
          latest &&
          (latest.status === "ready_for_review" ||
            latest.status === "awaiting_human_action" ||
            latest.status === "failed")
        ) {
          if (fallbackPoll) {
            clearInterval(fallbackPoll);
            fallbackPoll = null;
          }
        }
      }, 3000);
    };

    const init = async () => {
      const data = await fetchDraft();
      if (cancelled || !data) return;

      if (data.status === "queued" || data.status === "in_progress") {
        eventSource = new EventSource(`${apiBase}/api/v1/drafts/${draftId}/stream`);

        eventSource.addEventListener("browser_step", (evt) => {
          try {
            const step: BrowserStepEvent = JSON.parse((evt as MessageEvent).data);
            setLiveSteps((prev) => {
              const exists = prev.some((s) => s.step_index === step.step_index);
              return exists ? prev : [...prev, step];
            });
            if (step.screenshot_b64) {
              setLiveScreenshot(step.screenshot_b64);
            }
          } catch {
            // ignore malformed frame
          }
        });

        eventSource.addEventListener("complete", () => {
          eventSource?.close();
          if (fallbackPoll) clearInterval(fallbackPoll);
          fetchDraft();
        });

        eventSource.addEventListener("timeout", () => {
          eventSource?.close();
          startFallbackPoll();
        });

        eventSource.addEventListener("error", (evt) => {
          const msgData = (evt as MessageEvent).data;
          if (msgData) {
            eventSource?.close();
            if (fallbackPoll) clearInterval(fallbackPoll);
            fetchDraft();
          } else if (eventSource?.readyState === EventSource.CLOSED) {
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
  }, [draftId, streamGeneration]);

  const handleRevise = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsRevising(true);
    setErrorMsg(null);
    try {
      const cleanedFacts: Record<string, string> = {};
      for (const [k, v] of Object.entries(missingAnswers)) {
        if (v.trim()) cleanedFacts[k] = v.trim();
      }
      const res = await fetch(`${apiBase}/api/v1/drafts/${draftId}/revise`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_supplied_facts: cleanedFacts }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Revision failed (${res.status})`);
      }
      setLiveSteps([]);
      setStreamGeneration((g) => g + 1);
    } catch (err: any) {
      setErrorMsg(err.message || "Failed to re-run browser pre-fill.");
    } finally {
      setIsRevising(false);
    }
  };

  const handleCopyNarrative = async () => {
    if (!draft?.drafted_narrative_markdown) return;
    try {
      await navigator.clipboard.writeText(draft.drafted_narrative_markdown);
      setCopiedNarrative(true);
      setTimeout(() => setCopiedNarrative(false), 2000);
    } catch {
      // Clipboard unavailable
    }
  };

  if (isLoading) {
    return (
      <>
        <Header />
        <main className="flex-1 max-w-5xl mx-auto px-4 py-16 text-center">
          <Loader2 className="w-10 h-10 animate-spin text-blue-600 mx-auto mb-4" />
          <h2 className="text-xl font-bold text-slate-800">Loading Browser-Use Draft Workspace...</h2>
        </main>
      </>
    );
  }

  if (errorMsg && !draft) {
    return (
      <>
        <Header />
        <main className="flex-1 max-w-4xl mx-auto px-4 py-16 text-center">
          <AlertCircle className="w-12 h-12 text-rose-500 mx-auto mb-4" />
          <h2 className="text-2xl font-bold text-slate-900 mb-2">{errorMsg}</h2>
          <Link
            href={`/reports/${reportId}`}
            className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-semibold mt-4"
          >
            <ArrowLeft className="w-4 h-4" />
            <span>Back to Report</span>
          </Link>
        </main>
      </>
    );
  }

  if (!draft) return null;

  const isRunning = draft.status === "queued" || draft.status === "in_progress";
  const isReady = draft.status === "ready_for_review";
  const isBlocked = draft.status === "awaiting_human_action";
  const isFailed = draft.status === "failed";

  return (
    <>
      <Header />
      <main className="flex-1 max-w-7xl mx-auto px-4 py-8 w-full space-y-6">
        {/* Top Bar */}
        <div className="flex flex-wrap items-center justify-between gap-4">
          <Link
            href={`/reports/${reportId}`}
            className="inline-flex items-center gap-2 text-sm font-semibold text-slate-600 hover:text-blue-600 transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            <span>Back to Readiness Report</span>
          </Link>

          <div className="flex flex-wrap items-center gap-2">
            <a
              href={safeHref(draft.target_portal_url)}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-slate-700 bg-white border border-slate-200 rounded-lg hover:bg-slate-50 shadow-sm"
            >
              <span>Open Portal</span>
              <ExternalLink className="w-3.5 h-3.5" />
            </a>
            <a
              href={`${apiBase}/api/v1/drafts/${draft.draft_id}/export`}
              className="inline-flex items-center gap-1.5 px-3.5 py-1.5 text-xs font-semibold text-white bg-slate-900 hover:bg-blue-700 rounded-lg shadow-sm"
            >
              <Download className="w-3.5 h-3.5" />
              <span>Export Markdown</span>
            </a>
          </div>
        </div>

        {/* Header Banner */}
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-2 mb-1">
              <span className="text-[11px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-blue-50 text-blue-700 border border-blue-200">
                Browser-Use Agent &middot; v{draft.version}
              </span>
              <span className="text-xs font-mono text-slate-500">{draft.draft_id.slice(0, 8)}</span>
            </div>
            <h1 className="text-2xl font-extrabold text-slate-900 tracking-tight">
              {draft.opportunity_name}
            </h1>
            <p className="text-xs text-slate-500 mt-1">
              Target Portal: <span className="font-mono text-slate-700">{draft.target_portal_url}</span>
            </p>
          </div>

          <div>
            {isRunning && (
              <span className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200 text-xs font-bold animate-pulse">
                <Loader2 className="w-4 h-4 animate-spin" />
                Live Browser Filling Form...
              </span>
            )}
            {isReady && (
              <span className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200 text-xs font-bold">
                <CheckCircle2 className="w-4 h-4" />
                Paused Before Submit &middot; Ready for Review
              </span>
            )}
            {isBlocked && (
              <span className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-amber-50 text-amber-800 border border-amber-300 text-xs font-bold">
                <AlertTriangle className="w-4 h-4" />
                Awaiting Human Login / Action
              </span>
            )}
            {isFailed && (
              <span className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-rose-50 text-rose-700 border border-rose-200 text-xs font-bold">
                <AlertCircle className="w-4 h-4" />
                Pre-Fill Failed
              </span>
            )}
          </div>
        </div>

        {/* Safety Notice */}
        {isReady && (
          <div className="p-4 rounded-xl bg-emerald-50 border border-emerald-200 flex items-start gap-3">
            <ShieldCheck className="w-5 h-5 text-emerald-600 shrink-0 mt-0.5" />
            <p className="text-xs text-emerald-900">
              <strong>Human-in-the-Loop Gate Active:</strong> The browser agent completed pre-filling
              grounded fields and intentionally stopped before Final Submit. Review the pre-filled
              values, supply any missing compliance IDs on the right, and re-run or export.
            </p>
          </div>
        )}

        {isBlocked && (
          <div className="p-4 rounded-xl bg-amber-50 border border-amber-300 flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-amber-600 shrink-0 mt-0.5" />
            <p className="text-xs text-amber-900">
              <strong>Portal Gate Detected:</strong>{" "}
              {draft.metadata?.blocked_reason || "Portal requires manual sign-in or CAPTCHA verification."}
            </p>
          </div>
        )}

        {errorMsg && (
          <div className="p-4 rounded-xl bg-rose-50 border border-rose-200 text-xs text-rose-700">
            {errorMsg}
          </div>
        )}

        {/* Main Split-Pane Workspace */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          {/* Left: Live Browser Viewport + Action Log */}
          <div className="lg:col-span-7 space-y-6">
            <div className="bg-slate-900 rounded-2xl border border-slate-800 overflow-hidden shadow-md">
              <div className="px-4 py-2.5 bg-slate-800/80 border-b border-slate-700 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="w-2.5 h-2.5 rounded-full bg-rose-400" />
                  <span className="w-2.5 h-2.5 rounded-full bg-amber-400" />
                  <span className="w-2.5 h-2.5 rounded-full bg-emerald-400" />
                  <span className="ml-2 text-xs font-mono text-slate-300 flex items-center gap-1.5">
                    <Monitor className="w-3.5 h-3.5 text-blue-400" />
                    Live Browser Viewport
                  </span>
                </div>
                <span className="text-[11px] font-mono text-slate-400">
                  {liveSteps.length} steps recorded
                </span>
              </div>

              <div className="bg-slate-950 min-h-[340px] flex items-center justify-center p-2">
                {liveScreenshot ? (
                  <img
                    src={liveScreenshot}
                    alt="Live browser agent viewport screenshot"
                    className="w-full h-auto rounded-lg border border-slate-800"
                  />
                ) : (
                  <div className="text-center py-16 text-slate-400 text-xs">
                    <Loader2 className="w-7 h-7 animate-spin mx-auto mb-3 text-blue-400" />
                    Launching browser session and inspecting portal DOM...
                  </div>
                )}
              </div>
            </div>

            {/* Step-by-Step Action Log */}
            <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
              <h2 className="text-sm font-bold uppercase tracking-wider text-slate-500 mb-4">
                Live Browser Action Log ({liveSteps.length})
              </h2>
              {liveSteps.length === 0 ? (
                <p className="text-xs text-slate-500">Waiting for browser steps...</p>
              ) : (
                <ol className="space-y-2 max-h-80 overflow-y-auto pr-1">
                  {liveSteps.map((s) => (
                    <li
                      key={s.step_index}
                      className="p-2.5 rounded-lg border border-slate-100 bg-slate-50/70 flex items-start justify-between gap-3 text-xs"
                    >
                      <div className="flex items-start gap-2.5">
                        <span className="font-mono text-[10px] font-bold px-1.5 py-0.5 rounded bg-slate-200 text-slate-700 shrink-0">
                          #{s.step_index}
                        </span>
                        <span className="text-slate-800">{s.action}</span>
                      </div>
                      <span
                        className={`text-[10px] font-bold uppercase px-2 py-0.5 rounded shrink-0 ${
                          s.status === "ok"
                            ? "bg-emerald-100 text-emerald-700"
                            : s.status === "skipped_missing"
                            ? "bg-amber-100 text-amber-800"
                            : "bg-rose-100 text-rose-700"
                        }`}
                      >
                        {s.status.replace("_", " ")}
                      </span>
                    </li>
                  ))}
                </ol>
              )}
            </div>
          </div>

          {/* Right: Missing Inputs + Pre-Filled Fields + Drafted Narrative */}
          <div className="lg:col-span-5 space-y-6">
            {/* Interactive Missing Inputs Re-Fill Card */}
            <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
              <div className="flex items-center gap-2 mb-3">
                <AlertTriangle className="w-5 h-5 text-amber-600" />
                <h2 className="text-base font-bold text-slate-900">
                  Missing User Inputs ({draft.missing_inputs.length})
                </h2>
              </div>

              {draft.missing_inputs.length === 0 ? (
                <p className="text-xs text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-xl p-3">
                  All portal fields were grounded and pre-filled with zero missing compliance inputs.
                </p>
              ) : (
                <form onSubmit={handleRevise} className="space-y-4">
                  <p className="text-xs text-slate-500">
                    The agent refused to fabricate these compliance identifiers. Enter them below to
                    re-run the browser pre-fill (v{draft.version + 1}).
                  </p>
                  {draft.missing_inputs.map((item) => (
                    <div key={item.field_key}>
                      <label
                        htmlFor={`missing_${item.field_key}`}
                        className="block text-xs font-semibold text-slate-800 mb-1"
                      >
                        {item.question}
                      </label>
                      <input
                        id={`missing_${item.field_key}`}
                        type="text"
                        value={missingAnswers[item.field_key] || ""}
                        onChange={(e) =>
                          setMissingAnswers((prev) => ({
                            ...prev,
                            [item.field_key]: e.target.value,
                          }))
                        }
                        placeholder="Enter verified value..."
                        className="w-full border border-slate-300 rounded-lg px-3 py-2 text-xs focus:ring-2 focus:ring-blue-500 focus:outline-none"
                      />
                    </div>
                  ))}
                  <button
                    type="submit"
                    disabled={isRevising || isRunning}
                    className="w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white text-xs font-bold rounded-xl shadow-sm transition-colors"
                  >
                    <RefreshCw className={`w-3.5 h-3.5 ${isRevising ? "animate-spin" : ""}`} />
                    <span>{isRevising ? "Re-Launching Browser..." : "Re-Fill Portal with My Inputs"}</span>
                  </button>
                </form>
              )}
            </div>

            {/* Pre-Filled Fields Audit Table */}
            <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
              <h2 className="text-sm font-bold uppercase tracking-wider text-slate-500 mb-3">
                Pre-Filled Fields ({draft.filled_fields.length})
              </h2>
              {draft.filled_fields.length === 0 ? (
                <p className="text-xs text-slate-500">No fields filled yet.</p>
              ) : (
                <div className="divide-y divide-slate-100 max-h-72 overflow-y-auto pr-1">
                  {draft.filled_fields.map((f, idx) => (
                    <div key={`${f.selector}-${idx}`} className="py-2.5 text-xs">
                      <div className="flex items-center justify-between gap-2 mb-0.5">
                        <span className="font-semibold text-slate-800">{f.field_label}</span>
                        <span
                          className={`text-[10px] font-bold uppercase px-1.5 py-0.5 rounded ${
                            f.status === "filled"
                              ? "bg-emerald-50 text-emerald-700"
                              : "bg-amber-50 text-amber-800"
                          }`}
                        >
                          {f.status.replace("_", " ")}
                        </span>
                      </div>
                      <p className="text-slate-600 line-clamp-2">
                        {f.value || f.missing_question || "—"}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Drafted Narrative Markdown */}
            {draft.drafted_narrative_markdown && (
              <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
                <div className="flex items-center justify-between mb-3">
                  <h2 className="text-sm font-bold uppercase tracking-wider text-slate-500">
                    Drafted Narrative Copy
                  </h2>
                  <button
                    type="button"
                    onClick={handleCopyNarrative}
                    className="inline-flex items-center gap-1 text-xs font-semibold text-blue-600 hover:underline"
                  >
                    {copiedNarrative ? (
                      <>
                        <Check className="w-3.5 h-3.5 text-emerald-600" />
                        <span className="text-emerald-700">Copied</span>
                      </>
                    ) : (
                      <>
                        <Copy className="w-3.5 h-3.5" />
                        <span>Copy</span>
                      </>
                    )}
                  </button>
                </div>
                <pre className="text-xs text-slate-700 whitespace-pre-wrap font-sans bg-slate-50 p-3.5 rounded-xl border border-slate-100 max-h-60 overflow-y-auto">
                  {draft.drafted_narrative_markdown}
                </pre>
              </div>
            )}
          </div>
        </div>
      </main>
    </>
  );
}
