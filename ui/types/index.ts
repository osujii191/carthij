export interface SubagentResult {
  subagent_type: string;
  status: string;
  output: string;
  error?: string | null;
}

export interface ReportRecord {
  id: string;
  org_name: string;
  org_type: string;
  target_state?: string | null;
  funding_type: string;
  funding_amount: string;
  status: "in_progress" | "completed" | "failed";
  summary?: string | null;
  subagent_results: SubagentResult[];
  metadata?: Record<string, any>;
  created_at: number;
  updated_at: number;
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

