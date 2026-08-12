/**
 * Run state: the reducer that turns SSE frames into what the eight cards render.
 *
 * Honest-empty-state rule, enforced here rather than in each component: `reset()`
 * puts every measured field back to `null`, so before a run the UI has nothing to
 * show but em dashes. Fields are only ever populated from a frame.
 */

import * as React from "react"
import { frameError, streamRun, type BackendConfig } from "@/lib/sse"
import type {
  AgentCard,
  AgentCompleted,
  Domain,
  Health,
  KeyCheck,
  Rate,
  RunCompleted,
  StreamFrame,
  SynthesisCompleted,
} from "@/lib/types"

export interface SynthesisState {
  state: "idle" | "running" | "done"
  model: string | null
  tier: string | null
  rate: Rate | null
  latency_ms: number | null
  input_tokens: number | null
  output_tokens: number | null
  cache_read_tokens: number | null
  cache_write_tokens: number | null
  cost_usd: number | null
  measured: boolean
  source: string | null
  adjudication: Record<string, unknown> | null
  key_check: KeyCheck | null
}

export interface RunState {
  status: "idle" | "running" | "complete" | "error"
  applicationId: string | null
  sessionId: string | null
  backend: string | null
  mockMode: boolean | null
  measuredTokens: boolean | null
  executionPath: string | null
  cards: Record<Domain, AgentCard>
  synthesis: SynthesisState
  completion: RunCompleted | null
  errors: string[]
  /** Wall clock start, from performance.now(), for the live timer. */
  startedAt: number | null
}

const DOMAIN_ORDER: Domain[] = [
  "identity",
  "dealer",
  "straw",
  "employment",
  "income",
  "synthetic",
  "bustout",
  "rings",
]

function blankCard(domain: Domain, agentName: string): AgentCard {
  return {
    domain,
    agent_name: agentName,
    state: "idle",
    model: null,
    tier: null,
    rate: null,
    analysis: null,
    analysis_title: null,
    risk_band: null,
    char_count: null,
    sentence_count: null,
    latency_ms: null,
    started_at_ms: null,
    finished_at_ms: null,
    input_tokens: null,
    output_tokens: null,
    cache_read_tokens: null,
    cache_write_tokens: null,
    cost_usd: null,
    measured: false,
    source: null,
    error: null,
    config: null,
  }
}

const BLANK_SYNTHESIS: SynthesisState = {
  state: "idle",
  model: null,
  tier: null,
  rate: null,
  latency_ms: null,
  input_tokens: null,
  output_tokens: null,
  cache_read_tokens: null,
  cache_write_tokens: null,
  cost_usd: null,
  measured: false,
  source: null,
  adjudication: null,
  key_check: null,
}

function blankCards(agentNames: Record<string, string>): Record<Domain, AgentCard> {
  return Object.fromEntries(
    DOMAIN_ORDER.map((d) => [d, blankCard(d, agentNames[d] ?? d.toUpperCase())]),
  ) as Record<Domain, AgentCard>
}

export function initialRunState(agentNames: Record<string, string> = {}): RunState {
  return {
    status: "idle",
    applicationId: null,
    sessionId: null,
    backend: null,
    mockMode: null,
    measuredTokens: null,
    executionPath: null,
    cards: blankCards(agentNames),
    synthesis: { ...BLANK_SYNTHESIS },
    completion: null,
    errors: [],
    startedAt: null,
  }
}

type Action =
  | { type: "reset"; agentNames: Record<string, string> }
  | { type: "begin"; applicationId: string; agentNames: Record<string, string> }
  | { type: "frame"; frame: StreamFrame }
  | { type: "transport_error"; message: string }
  | { type: "finish" }

function reducer(state: RunState, action: Action): RunState {
  switch (action.type) {
    case "reset":
      return initialRunState(action.agentNames)

    case "begin":
      return {
        ...initialRunState(action.agentNames),
        status: "running",
        applicationId: action.applicationId,
        startedAt: performance.now(),
        cards: Object.fromEntries(
          DOMAIN_ORDER.map((d) => [
            d,
            { ...blankCard(d, action.agentNames[d] ?? d.toUpperCase()), state: "pending" as const },
          ]),
        ) as Record<Domain, AgentCard>,
      }

    case "transport_error":
      return { ...state, status: "error", errors: [...state.errors, action.message] }

    case "finish":
      // A stream that closed without a run_completed frame is not a success.
      if (state.status === "running" && !state.completion) {
        return {
          ...state,
          status: state.errors.length > 0 ? "error" : "error",
          errors: [
            ...state.errors,
            "The stream closed before a run_completed frame arrived; the run did not finish.",
          ],
        }
      }
      return state

    case "frame":
      return applyFrame(state, action.frame)

    default:
      return state
  }
}

function applyFrame(state: RunState, frame: StreamFrame): RunState {
  // Checked first, because an error can ride on any frame and a terminal error frame
  // arrives after HTTP 200 has already been committed.
  //
  // EXCEPT on agent_failed, which is the one non-terminal failure in the protocol.
  // Without this exemption a single specialist dropping out aborts the entire run and
  // no adjudication is ever shown -- the opposite of the 7-of-8 degradation the
  // pipeline is built for. ui/server.py used to emit `error` on that frame, which
  // tripped exactly this guard.
  if (frame.event !== "agent_failed") {
    const error = frameError(frame)
    if (error) {
      return { ...state, status: "error", errors: [...state.errors, error] }
    }
  }

  switch (frame.event) {
    case "run_started":
      return {
        ...state,
        applicationId: frame.application_id,
        sessionId: frame.session_id,
        backend: frame.backend,
        mockMode: frame.mock_mode,
        measuredTokens: frame.measured_tokens,
        executionPath: frame.execution_path,
        synthesis: { ...state.synthesis, model: frame.synthesizer_model },
        cards: Object.fromEntries(
          frame.agents.map((a) => [
            a.domain,
            {
              ...(state.cards[a.domain] ?? blankCard(a.domain, a.agent_name)),
              agent_name: a.agent_name,
              model: a.model,
              tier: a.tier,
              rate: a.rate,
              analysis_title: a.analysis_title,
              config: a.config ?? null,
              state: "pending" as const,
            },
          ]),
        ) as Record<Domain, AgentCard>,
      }

    case "agent_started":
      return {
        ...state,
        cards: {
          ...state.cards,
          [frame.domain]: {
            ...state.cards[frame.domain],
            state: "running",
            started_at_ms: frame.at_ms,
          },
        },
      }

    case "agent_completed":
      return { ...state, cards: { ...state.cards, [frame.domain]: mergeCompleted(state, frame) } }

    case "agent_failed":
      return {
        ...state,
        // An agent failing is surfaced on its card AND in the error list; it is
        // never absorbed into a green run.
        // `failure` first: that is what the runtime emits. Falling back to `error`
        // keeps older streams readable, and the literal string keeps a missing field
        // from rendering as "undefined" in front of a customer.
        errors: [
          ...state.errors,
          `${frame.agent_name}: ${frame.failure ?? frame.error ?? "no reason reported"}`,
        ],
        cards: {
          ...state.cards,
          [frame.domain]: {
            ...state.cards[frame.domain],
            state: "failed",
            error: frame.failure ?? frame.error ?? "no reason reported",
          },
        },
      }

    case "synthesis_started":
      return {
        ...state,
        synthesis: {
          ...state.synthesis,
          state: "running",
          model: frame.model,
          tier: frame.tier,
        },
      }

    case "synthesis_completed":
      return { ...state, synthesis: mergeSynthesis(state.synthesis, frame) }

    case "run_completed":
      return {
        ...state,
        status: state.errors.length > 0 ? "error" : "complete",
        completion: frame,
      }

    default:
      return state
  }
}

function mergeCompleted(state: RunState, frame: AgentCompleted): AgentCard {
  const prior = state.cards[frame.domain] ?? blankCard(frame.domain, frame.agent_name)
  return {
    ...prior,
    state: "done",
    agent_name: frame.agent_name,
    analysis: frame.analysis,
    analysis_title: frame.analysis_title,
    risk_band: frame.risk_band,
    char_count: frame.char_count,
    sentence_count: frame.sentence_count,
    model: frame.model ?? prior.model,
    tier: frame.tier ?? prior.tier,
    rate: frame.rate ?? prior.rate,
    latency_ms: frame.latency_ms,
    finished_at_ms: frame.finished_at_ms,
    input_tokens: frame.input_tokens,
    output_tokens: frame.output_tokens,
    cache_read_tokens: frame.cache_read_tokens,
    cache_write_tokens: frame.cache_write_tokens,
    cost_usd: frame.cost_usd,
    measured: frame.measured,
    source: frame.source,
    error: null,
    config: null,
  }
}

function mergeSynthesis(prior: SynthesisState, frame: SynthesisCompleted): SynthesisState {
  return {
    ...prior,
    state: "done",
    model: frame.model ?? prior.model,
    tier: frame.tier ?? prior.tier,
    rate: frame.rate ?? prior.rate,
    latency_ms: frame.latency_ms,
    input_tokens: frame.input_tokens,
    output_tokens: frame.output_tokens,
    cache_read_tokens: frame.cache_read_tokens,
    cache_write_tokens: frame.cache_write_tokens,
    cost_usd: frame.cost_usd,
    measured: frame.measured,
    source: frame.source,
    adjudication: frame.adjudication,
    key_check: frame.key_check,
  }
}

export function useRun(health: Health | null) {
  const agentNames = React.useMemo(() => health?.agent_names ?? {}, [health])
  const [state, dispatch] = React.useReducer(reducer, agentNames, initialRunState)
  const abortRef = React.useRef<AbortController | null>(null)

  const start = React.useCallback(
    async (applicationId: string, backend: BackendConfig) => {
      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller
      dispatch({ type: "begin", applicationId, agentNames })
      await streamRun(applicationId, backend, {
        signal: controller.signal,
        onFrame: (frame) => dispatch({ type: "frame", frame }),
        onTransportError: (message) => dispatch({ type: "transport_error", message }),
      })
      dispatch({ type: "finish" })
    },
    [agentNames],
  )

  const reset = React.useCallback(() => {
    abortRef.current?.abort()
    dispatch({ type: "reset", agentNames })
  }, [agentNames])

  React.useEffect(() => () => abortRef.current?.abort(), [])

  const cards = React.useMemo(
    () => DOMAIN_ORDER.map((d) => state.cards[d]).filter(Boolean),
    [state.cards],
  )

  return { state, cards, start, reset }
}

/** A ticking wall clock while a run is in flight; frozen once it completes. */
export function useWallClock(state: RunState): number | null {
  const [now, setNow] = React.useState<number | null>(null)

  React.useEffect(() => {
    if (state.status !== "running" || state.startedAt === null) return
    let raf = 0
    const tick = () => {
      setNow(performance.now())
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [state.status, state.startedAt])

  if (state.startedAt === null) return null
  // Once the run reports its own end-to-end measurement, show that instead of the
  // browser's clock: the server's number is the one we quote.
  if (state.completion) return state.completion.end_to_end_ms
  if (now === null) return 0
  return now - state.startedAt
}

export { DOMAIN_ORDER }
