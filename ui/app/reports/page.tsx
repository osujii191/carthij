"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import Header from "@/components/Header";
import { FundingReport } from "@/types";
import {
  FileText,
  Clock,
  CheckCircle2,
  AlertCircle,
  ArrowRight,
  PlusCircle,
  RefreshCw,
  Search,
  Award,
} from "lucide-react";

export default function ReportsDashboard() {
  const [reports, setReports] = useState<FundingReport[]>([]);
  const [filter, setFilter] = useState<"all" | "in_progress" | "completed">("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const fetchReports = async (showLoadingState = false) => {
    if (showLoadingState) setIsRefreshing(true);
    try {
      const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const res = await fetch(`${apiBase}/api/v1/reports`, { cache: "no-store" });
      if (res.ok) {
        const data: FundingReport[] = await res.json();
        setReports(data);
      }
    } catch (err) {
      console.error("Failed to fetch reports:", err);
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  };

  useEffect(() => {
    fetchReports();
    const interval = setInterval(() => {
      fetchReports();
    }, 4000);
    return () => clearInterval(interval);
  }, []);

  const isRunning = (status: FundingReport["status"]) =>
    status === "queued" || status === "in_progress";

  const filteredReports = reports.filter((r) => {
    const matchesFilter =
      filter === "all"
        ? true
        : filter === "in_progress"
        ? isRunning(r.status)
        : r.status === "completed";
    const fundingType = String(r.intake_data?.target_funding_instrument || "");
    const matchesSearch =
      r.org_name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      fundingType.toLowerCase().includes(searchQuery.toLowerCase());
    return matchesFilter && matchesSearch;
  });

  const inProgressCount = reports.filter((r) => isRunning(r.status)).length;
  const completedCount = reports.filter((r) => r.status === "completed").length;

  return (
    <>
      <Header />
      <main className="flex-1 max-w-6xl mx-auto px-4 py-8 md:py-12 w-full">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8">
          <div>
            <h1 className="text-3xl font-extrabold text-slate-900 tracking-tight">
              Assessment Reports Dashboard
            </h1>
            <p className="text-slate-500 text-sm mt-1">
              Track active multi-agent analyses and review grounded readiness reports.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={() => fetchReports(true)}
              disabled={isRefreshing}
              className="flex items-center gap-1.5 px-3 py-2 text-xs font-semibold text-slate-600 bg-white border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors shadow-sm"
              title="Refresh reports"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${isRefreshing ? "animate-spin text-blue-600" : ""}`} />
              <span>Refresh</span>
            </button>
            <Link
              href="/#intake-form"
              className="flex items-center gap-1.5 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-semibold shadow-sm transition-colors"
            >
              <PlusCircle className="w-4 h-4" />
              <span>New Assessment</span>
            </Link>
          </div>
        </div>

        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-4 mb-6">
          <div className="flex items-center gap-1.5 bg-slate-100 p-1 rounded-xl text-xs font-semibold">
            <button
              onClick={() => setFilter("all")}
              className={`px-3 py-1.5 rounded-lg transition-all ${
                filter === "all" ? "bg-white text-slate-900 shadow-sm" : "text-slate-600 hover:text-slate-900"
              }`}
            >
              All Reports ({reports.length})
            </button>
            <button
              onClick={() => setFilter("in_progress")}
              className={`px-3 py-1.5 rounded-lg transition-all flex items-center gap-1.5 ${
                filter === "in_progress"
                  ? "bg-white text-amber-700 shadow-sm"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              {inProgressCount > 0 && <span className="w-2 h-2 rounded-full bg-amber-500 animate-pulse" />}
              In Progress ({inProgressCount})
            </button>
            <button
              onClick={() => setFilter("completed")}
              className={`px-3 py-1.5 rounded-lg transition-all ${
                filter === "completed"
                  ? "bg-white text-emerald-700 shadow-sm"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              Ready ({completedCount})
            </button>
          </div>

          <div className="relative">
            <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search by organization or instrument..."
              className="pl-9 pr-4 py-1.5 text-sm border border-slate-200 rounded-lg w-full sm:w-72 bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
        </div>

        {isLoading ? (
          <div className="p-16 text-center bg-white rounded-2xl border border-slate-200 shadow-sm">
            <RefreshCw className="w-8 h-8 animate-spin text-blue-600 mx-auto mb-3" />
            <p className="text-sm font-semibold text-slate-700">Loading reports...</p>
          </div>
        ) : filteredReports.length === 0 ? (
          <div className="p-16 text-center bg-white rounded-2xl border border-slate-200 shadow-sm">
            <FileText className="w-12 h-12 text-slate-300 mx-auto mb-3" />
            <h3 className="text-lg font-bold text-slate-800">No reports found</h3>
            <p className="text-sm text-slate-500 max-w-sm mx-auto mt-1 mb-6">
              {searchQuery
                ? "No assessments match your search query."
                : filter === "in_progress"
                ? "There are no analyses currently in progress."
                : "Submit a funding intake application to trigger an AI agent assessment."}
            </p>
            <Link
              href="/#intake-form"
              className="inline-flex items-center gap-1.5 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-semibold shadow-sm hover:bg-blue-700 transition-colors"
            >
              <PlusCircle className="w-4 h-4" />
              <span>Start First Intake</span>
            </Link>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4">
            {filteredReports.map((report) => {
              const running = isRunning(report.status);
              const completed = report.status === "completed";
              const fundingType = String(report.intake_data?.target_funding_instrument || "Funding Intake");
              const fundingAmount = String(report.intake_data?.target_amount_usd || "—").replace(/^\$/, "");
              const formattedDate = new Date(report.created_at * 1000).toLocaleString(undefined, {
                month: "short",
                day: "numeric",
                year: "numeric",
                hour: "numeric",
                minute: "2-digit",
              });

              return (
                <Link
                  key={report.report_id}
                  href={`/reports/${report.report_id}`}
                  className="group bg-white p-5 md:p-6 rounded-2xl border border-slate-200 hover:border-blue-400 hover:shadow-md transition-all flex flex-col md:flex-row md:items-center justify-between gap-4"
                >
                  <div className="flex items-start gap-4">
                    <div
                      className={`w-11 h-11 rounded-xl flex items-center justify-center shrink-0 ${
                        running
                          ? "bg-amber-50 text-amber-600 border border-amber-200"
                          : completed
                          ? "bg-emerald-50 text-emerald-600 border border-emerald-200"
                          : "bg-rose-50 text-rose-600 border border-rose-200"
                      }`}
                    >
                      {running ? (
                        <Clock className="w-5 h-5 animate-spin" />
                      ) : completed ? (
                        <CheckCircle2 className="w-5 h-5" />
                      ) : (
                        <AlertCircle className="w-5 h-5" />
                      )}
                    </div>
                    <div>
                      <div className="flex flex-wrap items-center gap-2 mb-1">
                        <h2 className="text-lg font-bold text-slate-900 group-hover:text-blue-600 transition-colors">
                          {report.org_name}
                        </h2>
                        <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-slate-100 text-slate-700">
                          {report.org_type}
                        </span>
                        {report.target_state && (
                          <span className="text-xs px-2.5 py-0.5 rounded-full font-bold bg-blue-50 text-blue-700 border border-blue-200">
                            {report.target_state}
                          </span>
                        )}
                        {running ? (
                          <span className="inline-flex items-center gap-1.5 text-xs font-semibold px-2.5 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200 animate-pulse">
                            <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
                            Agents In Progress
                          </span>
                        ) : completed ? (
                          <span className="text-xs font-semibold px-2.5 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">
                            Ready
                          </span>
                        ) : (
                          <span className="text-xs font-semibold px-2.5 py-0.5 rounded-full bg-rose-50 text-rose-700 border border-rose-200">
                            Failed
                          </span>
                        )}
                        {completed && report.readiness_score != null && (
                          <span className="inline-flex items-center gap-1 text-xs font-bold px-2 py-0.5 rounded-full bg-slate-900 text-white">
                            <Award className="w-3 h-3" />
                            {report.readiness_score}/100
                          </span>
                        )}
                      </div>
                      <p className="text-sm text-slate-600">
                        <span className="font-medium text-slate-700">{fundingType}</span> &middot;{" "}
                        <span className="font-semibold text-slate-900">${fundingAmount}</span>
                        {completed && (
                          <span className="text-xs text-slate-500 ml-2">
                            ({report.opportunities.length} verified opportunities)
                          </span>
                        )}
                      </p>
                      <p className="text-xs text-slate-400 mt-1">Submitted on {formattedDate}</p>
                    </div>
                  </div>

                  <div className="flex items-center gap-2 text-sm font-semibold text-blue-600 group-hover:translate-x-1 transition-transform self-end md:self-center">
                    <span>{running ? "View Live Progress" : "Read Full Report"}</span>
                    <ArrowRight className="w-4 h-4" />
                  </div>
                </Link>
              );
            })}
          </div>
        )}
      </main>
    </>
  );
}
