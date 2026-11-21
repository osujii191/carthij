import Link from "next/link";
import { Sparkles, FileText, PlusCircle } from "lucide-react";

export default function Header() {
  return (
    <header className="border-b border-slate-200 bg-white/90 backdrop-blur sticky top-0 z-50">
      <div className="max-w-6xl mx-auto px-4 h-16 flex items-center justify-between">
        <Link href="/" className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-blue-600 text-white flex items-center justify-center font-bold text-lg shadow-sm">
            C
          </div>
          <div className="flex items-center gap-2">
            <span className="font-bold text-lg tracking-tight text-slate-900">Carthij</span>
            <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-blue-50 text-blue-700 border border-blue-200 flex items-center gap-1">
              <Sparkles className="w-3 h-3" />
              Gemini Agents
            </span>
          </div>
        </Link>
        <nav className="flex items-center gap-3 text-sm font-medium">
          <Link
            href="/reports"
            className="flex items-center gap-1.5 px-3 py-2 text-slate-600 hover:text-blue-600 hover:bg-slate-50 rounded-lg transition-colors"
          >
            <FileText className="w-4 h-4" />
            <span>Reports Dashboard</span>
          </Link>
          <Link
            href="/#intake-form"
            className="flex items-center gap-1.5 px-3.5 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors shadow-sm"
          >
            <PlusCircle className="w-4 h-4" />
            <span>New Intake</span>
          </Link>
        </nav>
      </div>
    </header>
  );
}
