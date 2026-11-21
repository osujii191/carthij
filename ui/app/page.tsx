import Header from "@/components/Header";
import IntakeForm from "@/components/IntakeForm";
import { Bot, Network, Layers } from "lucide-react";

export default function Home() {
  return (
    <>
      <Header />
      <main className="flex-1">
        {/* Hero Section */}
        <section className="max-w-6xl mx-auto px-4 py-12 md:py-16 text-center">
          <div className="inline-flex items-center gap-2 px-3.5 py-1 rounded-full bg-slate-100 border border-slate-200 text-xs font-semibold text-slate-700 mb-6">
            <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
            AI-Augmented Funding Intake Workflow
          </div>
          <h1 className="text-4xl md:text-5xl font-extrabold tracking-tight text-slate-900 max-w-3xl mx-auto leading-tight">
            Funding readiness assessment for <span className="text-blue-600">Startups</span> &amp;{" "}
            <span className="text-emerald-600">Non-Profits</span>
          </h1>
          <p className="mt-4 text-lg md:text-xl text-slate-600 max-w-2xl mx-auto">
            Complete your organization's intake below. Our coordinating AI supervisor decomposes your
            pitch and deploys specialized subagents in parallel to evaluate your market, risk, and funding alignment.
          </p>
        </section>

        {/* Intake Form Section */}
        <section className="max-w-4xl mx-auto px-4 pb-16">
          <IntakeForm />
        </section>

        {/* How it works */}
        <section id="how-it-works" className="max-w-5xl mx-auto px-4 py-12 border-t border-slate-200">
          <h2 className="text-2xl font-bold text-center text-slate-900 mb-10">
            How the Agentic Workflow Works
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
            <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
              <div className="w-10 h-10 rounded-lg bg-blue-100 text-blue-700 font-bold flex items-center justify-center mb-4">
                <Bot className="w-5 h-5" />
              </div>
              <h3 className="text-lg font-bold text-slate-900 mb-2">Form Intake Webhook</h3>
              <p className="text-sm text-slate-600">
                The Next.js client dispatches your submission to FastAPI (`/api/v1/webhook/submit`), validating
                organization payload and funding targets.
              </p>
            </div>
            <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
              <div className="w-10 h-10 rounded-lg bg-emerald-100 text-emerald-700 font-bold flex items-center justify-center mb-4">
                <Network className="w-5 h-5" />
              </div>
              <h3 className="text-lg font-bold text-slate-900 mb-2">Parallel Subagent Fan-Out</h3>
              <p className="text-sm text-slate-600">
                The Gemini Coordinator decomposes the request into distinct subtasks and runs worker subagents in
                parallel with Python's `asyncio.gather()`.
              </p>
            </div>
            <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
              <div className="w-10 h-10 rounded-lg bg-purple-100 text-purple-700 font-bold flex items-center justify-center mb-4">
                <Layers className="w-5 h-5" />
              </div>
              <h3 className="text-lg font-bold text-slate-900 mb-2">Synthesized Readiness Report</h3>
              <p className="text-sm text-slate-600">
                Findings are collected and synthesized into an actionable readiness summary detailing strengths, gaps,
                and targeted funding targets.
              </p>
            </div>
          </div>
        </section>
      </main>

      <footer className="bg-slate-900 text-slate-400 py-8 border-t border-slate-800 mt-auto">
        <div className="max-w-6xl mx-auto px-4 text-center text-sm">
          <p>Carthij AI Workflow System &middot; Next.js + Google Gemini + FastAPI</p>
        </div>
      </footer>
    </>
  );
}
