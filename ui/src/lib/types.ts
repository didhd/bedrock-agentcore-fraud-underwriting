/**
 * The wire contract between ui/server.py and this app.
 *
 * Every measured numeric field is `number | null`. `null` means "this run did not
 * measure it" and MUST render as an em dash, never as 0 and never as a plausible
 * placeholder. That rule is the reason these types are nullable rather than
 * optional -- an optional field invites `?? 0`, which is how a fabricated number
 * gets into a customer demo.
 */

export type RiskBand = "LOW RISK" | "POSSIBLE RISK" | "HIGH RISK"
export type OverallRisk = "LOW RISK" | "MEDIUM RISK" | "HIGH RISK"
export type Recommendation = "APPROVE" | "REVIEW AND APPLY STIPULATIONS" | "DECLINE"

export type Domain =
  | "identity"
  | "dealer"
  | "straw"
  | "employment"
  | "income"
  | "synthetic"
  | "bustout"
  | "rings"

export interface Rate {
  input_per_mtok?: number
  output_per_mtok?: number
  cache_read_per_mtok?: number
}

export interface EnumSide {
  artifact: string
  status: string
  overall_risk_decision: string[]
  recommendation_decision: string[]
}

export interface EnumConflict {
  title: string
  executable: EnumSide
  narrative: EnumSide
  action: string
}

export interface Health {
  mock_mode: boolean
  measures_tokens: boolean
  capabilities: Record<string, boolean>
  notes: string[]
  domains: Domain[]
  agent_names: Record<string, string>
  analysis_titles: Record<Domain, string | null>
  risk_bands: RiskBand[]
  length_rules: {
    low_risk_max_sentences: number
    low_risk_min_sentences: number
    possible_high_max_chars: number
    straw_no_coborrower_max_sentences: number
    master_summary_max_chars: number
  }
  adjudication_keys: string[]
  overall_risk_values: OverallRisk[]
  recommendation_values: Recommendation[]
  enum_conflict: EnumConflict
  synthesizer_model: string | null
}

export interface ApplicationSummary {
  application_id: string
  scenario: string
  anti_false_positive: boolean
  loan_amount: number | null
  vehicle: string | null
  fraud_score: number | null
  fraud_score_band: string | null
  dealer_consortium_score: number | null
  dealer_score_band: string | null
  signal_count: number
  alert_count: number
}

export interface AlertFired {
  alert_id: number | null
  categories: string[]
  finding_text: string
}

export interface DomainPayloadView {
  domain: Domain
  agent_name: string
  signal_count: number | null
  signals: Record<string, unknown> | null
  signals_unavailable_reason: string | null
  paired_context: Record<string, unknown>
  entitled_alert_count: number | null
  alerts_fired: AlertFired[]
  guardrails_applied: string[]
  analysis_title: string | null
  model: string | null
}

export interface PayloadResponse {
  application_id: string
  projection_source: string
  precomputed: boolean
  total_signal_count: number
  total_alert_count: number
  domains: DomainPayloadView[]
}

/** Frames on the SSE channel. Discriminated on `event`. */
export interface RunStarted {
  event: "run_started"
  application_id: string
  session_id: string
  backend: string
  mock_mode: boolean
  measured_tokens: boolean
  execution_path: string
  synthesizer_model: string | null
  agents: {
    domain: Domain
    agent_name: string
    model: string | null
    tier: string | null
    rate: Rate | null
    analysis_title: string | null
  }[]
}

export interface AgentStarted {
  event: "agent_started"
  domain: Domain
  at_ms: number
}

export interface AgentCompleted {
  event: "agent_completed"
  domain: Domain
  agent_name: string
  analysis: string
  analysis_title: string | null
  risk_band: RiskBand | null
  char_count: number
  sentence_count: number
  model: string | null
  tier: string | null
  rate: Rate | null
  latency_ms: number | null
  finished_at_ms: number | null
  measured: boolean
  source: string
  input_tokens: number | null
  output_tokens: number | null
  cache_read_tokens: number | null
  cache_write_tokens: number | null
  cost_usd: number | null
}

export interface AgentFailed {
  event: "agent_failed"
  domain: Domain
  agent_name: string
  error: string
}

export interface SynthesisStarted {
  event: "synthesis_started"
  model: string | null
  tier: string | null
}

export interface KeyCheck {
  expected_count: number
  actual_count: number
  order_matches: boolean
  missing: string[]
  unexpected: string[]
  valid: boolean
}

export interface SynthesisCompleted {
  event: "synthesis_completed"
  adjudication: Record<string, unknown>
  key_check: KeyCheck
  model: string | null
  tier: string | null
  rate: Rate | null
  latency_ms: number | null
  measured: boolean
  source: string
  input_tokens: number | null
  output_tokens: number | null
  cache_read_tokens: number | null
  cache_write_tokens: number | null
  cost_usd: number | null
}

export interface RunCompleted {
  event: "run_completed"
  fanout_wall_ms: number
  synthesis_ms: number
  end_to_end_ms: number
  slowest_agent: Domain | null
  slowest_agent_ms: number | null
  sum_of_agent_ms: number | null
  agent_cost_usd: number | null
  source: string
}

/**
 * A terminal error frame. AgentCore emits mid-stream errors AFTER HTTP 200, so
 * this shape can arrive on a "successful" response and must be surfaced.
 */
export interface ErrorFrame {
  event?: "error"
  error: string
}

export type StreamFrame =
  | RunStarted
  | AgentStarted
  | AgentCompleted
  | AgentFailed
  | SynthesisStarted
  | SynthesisCompleted
  | RunCompleted
  | ErrorFrame

/** Per-agent state held by the reducer while a run is in flight. */
export type AgentState = "idle" | "pending" | "running" | "done" | "failed"

export interface AgentCard {
  domain: Domain
  agent_name: string
  state: AgentState
  model: string | null
  tier: string | null
  rate: Rate | null
  analysis: string | null
  analysis_title: string | null
  risk_band: RiskBand | null
  char_count: number | null
  sentence_count: number | null
  latency_ms: number | null
  started_at_ms: number | null
  finished_at_ms: number | null
  input_tokens: number | null
  output_tokens: number | null
  cache_read_tokens: number | null
  cache_write_tokens: number | null
  cost_usd: number | null
  measured: boolean
  source: string | null
  error: string | null
}
