export type StageName =
  | "queued"
  | "query_planning"
  | "searching"
  | "extracting"
  | "analyzing_specialists"
  | "verifying_and_synthesizing"
  | "completed"
  | "failed";

export interface StageProgress {
  stage: StageName;
  message: string;
  timestamp: number;
}

export interface FundingOpportunity {
  name: string;
  provider: string;
  scope: "state" | "local" | "federal" | "foundation" | "private_capital";
  instrument: string;
  estimated_amount: string;
  deadline: string;
  eligibility_fit: "high" | "medium" | "low";
  why_it_fits: string;
  key_requirements: string[];
  source_url: string;
  source_id: string;
  citation_verified: boolean;
}

export interface ReadinessGap {
  category: "compliance" | "financial" | "narrative" | "jurisdiction" | "eligibility";
  severity: "high" | "medium" | "low";
  description: string;
  remediation: string;
}

export interface ActionItem {
  priority: number;
  timeframe: "immediate_7d" | "short_term_30d" | "medium_term_90d";
  action: string;
  related_opportunity?: string | null;
}

export interface SourceSummary {
  source_id: string;
  url: string;
  title: string;
  excerpt: string;
  score: number;
  published_date?: string | null;
  matched_scopes: string[];
  matched_focus_areas: string[];
  is_extracted: boolean;
}

export interface SpecialistOutput {
  specialist_name: string;
  status: string;
  summary: string;
  opportunities_found?: number;
  gaps_found?: number;
  tool_calls_used?: number;
  error?: string | null;
}

export interface FundingReport {
  report_id: string;
  status: "queued" | "in_progress" | "completed" | "failed";
  owner_uid?: string | null;
  submitter_email?: string | null;
  org_name: string;
  org_type: string;
  target_state: string;
  intake_data: Record<string, any>;
  executive_summary?: string | null;
  readiness_score?: number | null;
  opportunities: FundingOpportunity[];
  readiness_gaps: ReadinessGap[];
  action_plan: ActionItem[];
  specialist_outputs: SpecialistOutput[];
  sources: SourceSummary[];
  stage_history: StageProgress[];
  metadata: Record<string, any>;
  created_at: number;
  updated_at: number;
}

export interface ReportAcceptedResponse {
  report_id: string;
  status: string;
  stream_url: string;
}

export interface SubagentResult {
  subagent_type: string;
  status: string;
  output: string;
  error?: string | null;
}

export interface WorkflowExecutionResponse {
  workflow_id: string;
  status: string;
  summary: string;
  subagent_results: SubagentResult[];
  metadata?: Record<string, any>;
}

export interface FormSubmissionPayload {
  form_id: string;
  submitter_email: string;
  data: {
    organization_name: string;
    organization_type: "Startup" | "Non-Profit";
    target_state: "NY" | "NJ" | "SF";
    mission_and_pitch: string;
    target_funding_instrument: string;
    target_amount_usd: string;
    use_of_funds: string;
  };
  context: string;
  callback_url: string | null;
}
