import { WorkflowExecutionResponse } from "@/types";
import SubagentCard from "./SubagentCard";
import { CheckCircle, ShieldCheck } from "lucide-react";

interface ResultsPanelProps {
  results: WorkflowExecutionResponse;
}

export default function ResultsPanel({ results }: ResultsPanelProps) {
  return (
    <section className="mt-8 p-6 md:p-8 bg-slate-50 border-t border-slate-200 rounded-b-2xl">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-6">
        <div className="flex items-center gap-2">
          <CheckCircle className="w-6 h-6 text-emerald-500" />
          <h3 className="text-xl font-bold text-slate-900">Coordinator Agent Synthesis</h3>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono bg-slate-200 text-slate-700 px-2.5 py-1 rounded-md">
            ID: {results.workflow_id}
          </span>
          <span className="text-xs font-semibold px-2 py-0.5 bg-emerald-100 text-emerald-800 rounded">
            {results.status}
          </span>
        </div>
      </div>

      {/* Executive Overview Card */}
      <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm mb-8">
        <div className="flex items-center gap-2 mb-3">
          <ShieldCheck className="w-5 h-5 text-blue-600" />
          <h4 className="text-xs font-bold uppercase tracking-wider text-slate-500">
            Executive Synthesis &amp; Strategic Readiness
          </h4>
        </div>
        <div className="text-slate-700 leading-relaxed whitespace-pre-line text-sm">
          {results.summary}
        </div>
      </div>

      {/* Parallel Subagents Grid */}
      <div className="mb-2">
        <h4 className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-3">
          Parallel Subagent Deep-Dives ({results.subagent_results.length} Executed)
        </h4>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {results.subagent_results.map((subagent, index) => (
            <SubagentCard key={`${subagent.subagent_type}-${index}`} result={subagent} />
          ))}
        </div>
      </div>
    </section>
  );
}
