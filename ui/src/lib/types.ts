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
  /** Loan-to-value percentage. Read from the record's `application` block. */
  ltv_pct: number | null
  credit_score: number | null
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
    config?: AgentRuntimeConfig | null
  }[]
  synthesizer_config?: AgentRuntimeConfig | null
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
  /**
   * `failure`, NOT `error`, and the distinction is load-bearing. Any frame carrying
   * `error` aborts the whole run (see `frameError` in lib/sse.ts) because a terminal
   * mid-stream exception arrives behind an already-committed HTTP 200. One specialist
   * dropping out is survivable -- seven of eight still adjudicates -- so it must not
   * look terminal. The runtime emits `failure` for exactly this reason.
   */
  failure: string
  /** Older streams used `error` here. Read as a fallback, never emitted. */
  error?: string
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

/**
 * One agent's request-time configuration, as reported by `agents/agent_config.py`.
 *
 * Read from the modules that actually build the agent, never restated, so this cannot
 * drift from what runs. Note what is deliberately ABSENT: the prompt TEXT. It is
 * customer-confidential and this object reaches a browser on a public URL, so only its
 * provenance and size travel.
 */
export interface AgentReasoning {
  enabled: boolean
  effort: string | null
  transport: string
  request_field: string
  why: string
}

export interface AgentPromptInfo {
  source: string
  chars: number
  estimated_tokens: number
  estimated: boolean
  sha256_verified: boolean
  orchestration_notes_chars: number | null
  verbatim: string
}

export interface AgentPromptCache {
  model_id?: string
  prompt_tokens?: number
  prompt_tokens_estimated?: boolean
  min_cacheable_tokens?: number
  will_cache?: boolean
  reason?: string
}

export interface AgentRuntimeConfig {
  role: string
  agent_name: string
  model_id: string
  tier: string
  max_output_tokens: number
  reasoning: AgentReasoning
  prompt: AgentPromptInfo
  instance_lifetime: string
  conversation_manager: string
  prompt_cache?: AgentPromptCache | null
}

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
  /** Request-time config for the hover tooltip. Null when the reporter was unavailable. */
  config: AgentRuntimeConfig | null
}


// ---------------------------------------------------------------------------
// Chat seats and agent inspection (/api/agents)
// ---------------------------------------------------------------------------

export interface AgentToolInfo {
  count: number | null
  max_per_query?: number
  why_capped?: string
  why_none?: string
  tools: { name: string; calls?: string; signature?: string }[]
}

export interface AgentSeat {
  id: string
  kind: "orchestrator" | "specialist"
  agent_name: string
  label: string
  description: string
  /** Deployed runtime ARN, or null when that runtime does not exist yet. */
  runtime: string | null
  /** Francis holds session context; a specialist analyses one domain of one application. */
  conversational: boolean
  config: AgentRuntimeConfig | null
  tools: AgentToolInfo | null
  /**
   * The verbatim customer prompt. Present ONLY when the server chose to include it --
   * the local build does, the public CloudFront build does not, because these files are
   * customer-confidential. The decision is the server's, not the client's.
   */
  prompt_text?: string | null
}

export interface SignalSourceMode {
  id: string
  label: string
  detail: string
  available: boolean
  requires?: string[]
}

export interface SignalSourceInfo {
  mocked: boolean
  active: string
  swap_point: string
  note: string
  modes: SignalSourceMode[]
}

export interface AgentsResponse {
  agents: AgentSeat[]
  prompt_text_included: boolean
  signal_source: SignalSourceInfo
}
