import { SubagentResult } from "@/types";
import { CheckCircle2, AlertCircle } from "lucide-react";

interface SubagentCardProps {
  result: SubagentResult;
}

export default function SubagentCard({ result }: SubagentCardProps) {
  const isSuccess = result.status === "completed";
  const formattedType = result.subagent_type.replace(/_/g, " ");

  return (
    <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm flex flex-col justify-between">
      <div>
        <div className="flex items-center justify-between mb-3 pb-2 border-b border-slate-100">
          <h4 className="font-bold text-sm text-slate-800 capitalize flex items-center gap-2">
            {isSuccess ? (
              <CheckCircle2 className="w-4 h-4 text-emerald-500" />
            ) : (
              <AlertCircle className="w-4 h-4 text-rose-500" />
            )}
            {formattedType}
          </h4>
          <span
            className={`text-xs px-2.5 py-0.5 rounded-full font-medium border ${
              isSuccess
                ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                : "bg-rose-50 text-rose-700 border-rose-200"
            }`}
          >
            {result.status}
          </span>
        </div>
        <div className="text-xs text-slate-600 whitespace-pre-line leading-relaxed max-h-64 overflow-y-auto pr-1">
          {result.output || result.error || "No output provided."}
        </div>
      </div>
    </div>
  );
}
