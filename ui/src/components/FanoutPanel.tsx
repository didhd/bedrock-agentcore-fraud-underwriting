/**
 * The parallel fan-out: eight cards, one per fraud dimension, labelled with the
 * customer's own persona names.
 *
 * The point this panel has to make visually is that all eight START TOGETHER and
 * FINISH AT DIFFERENT TIMES. So each card carries a progress bar that fills while
 * the agent is in flight, and the wall clock above counts up once for the whole
 * fan-out rather than per card.
 *
 * Every number on a card is measured or an em dash. In MOCK_MODE the token and
 * cost rows are em dashes by design, and the header says so.
 */

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import { Separator } from "@/components/ui/separator"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { Metric, RiskBadge, StateChip, StatTile } from "@/components/atoms"
import { cn } from "@/lib/utils"
import { EM_DASH, integer, ms, rateLabel, seconds, shortModel, usd } from "@/lib/format"
import type { AgentCard } from "@/lib/types"
import type { RunState } from "@/hooks/useRun"

function TierBadge({ tier }: { tier: string | null }) {
  if (!tier) return null
  return (
    <Badge variant="secondary" className="font-mono text-[0.6875rem]">
      {tier}
    </Badge>
  )
}

function AgentTile({
  card,
  maxLatency,
  elapsedMs,
}: {
  card: AgentCard
  maxLatency: number | null
  elapsedMs: number | null
}) {
  // While running, the bar is indeterminate-ish: it tracks elapsed wall time
  // against the slowest agent seen so far, so it moves without claiming a
  // prediction. Once done, it is the agent's real share of the fan-out.
  const progress =
    card.state === "done" && card.latency_ms !== null && maxLatency
      ? Math.min(100, (card.latency_ms / maxLatency) * 100)
      : card.state === "running" && elapsedMs !== null && maxLatency
        ? Math.min(96, (elapsedMs / maxLatency) * 100)
        : card.state === "running"
          ? 12
          : 0

  return (
    <Card
      className={cn(
        "gap-3 py-4 transition-colors",
        card.state === "running" && "border-[#2a78d6]/50 dark:border-[#3987e5]/50",
        card.state === "failed" && "border-[#d03b3b]/50",
      )}
    >
      <CardHeader className="gap-1 px-4">
        <div className="flex items-start justify-between gap-2">
          <CardTitle className="font-mono text-[0.8125rem] leading-tight tracking-tight">
            {card.agent_name}
          </CardTitle>
          <StateChip state={card.state} />
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <RiskBadge band={card.risk_band} />
          <TierBadge tier={card.tier} />
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 px-4">
        <div>
          <Progress
            value={progress}
            aria-label={`${card.agent_name} progress`}
            className="h-1.5"
          />
        </div>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              className="cursor-help truncate text-left font-mono text-[0.6875rem] text-muted-foreground"
            >
              {shortModel(card.model)}
            </button>
          </TooltipTrigger>
          <TooltipContent>
            <span className="flex flex-col gap-1">
              <span className="font-mono">{card.model ?? EM_DASH}</span>
              <span>Rate: {rateLabel(card.rate)}</span>
            </span>
          </TooltipContent>
        </Tooltip>
        <div className="grid grid-cols-2 gap-x-3 gap-y-2.5">
          <Metric label="Latency" value={ms(card.latency_ms)} />
          <Metric
            label="Finished at"
            value={ms(card.finished_at_ms)}
            hint="Milliseconds after the fan-out began. The spread across the eight cards is the parallelism."
          />
          <Metric
            label="Tokens in"
            value={integer(card.input_tokens)}
            hint="Bedrock inputTokens: non-cached input only."
          />
          <Metric label="Tokens out" value={integer(card.output_tokens)} />
          <Metric
            label="Cache read"
            value={integer(card.cache_read_tokens)}
            hint="cacheReadInputTokens is optional in the Bedrock usage payload; an em dash means the field was absent."
          />
          <Metric label="Cost" value={usd(card.cost_usd)} />
        </div>
        {card.error ? (
          <p className="rounded-md border border-[#d03b3b]/40 bg-[#d03b3b]/10 p-2 font-mono text-[0.6875rem] text-[#a01c1c] dark:text-[#f08a8a]">
            {card.error}
          </p>
        ) : null}
      </CardContent>
    </Card>
  )
}

export function FanoutPanel({
  state,
  cards,
  elapsedMs,
}: {
  state: RunState
  cards: AgentCard[]
  elapsedMs: number | null
}) {
  const done = cards.filter((card) => card.state === "done").length
  const latencies = cards
    .map((card) => card.latency_ms)
    .filter((value): value is number => value !== null)
  const maxLatency = latencies.length > 0 ? Math.max(...latencies) : null

  const completion = state.completion
  const slowestName = completion?.slowest_agent
    ? (cards.find((card) => card.domain === completion.slowest_agent)?.agent_name ??
      completion.slowest_agent)
    : null

  // Serial-equivalent: the sum of the eight measured latencies. Stated as
  // arithmetic on measurements from this run, never as a benchmark against
  // another platform.
  const speedup =
    completion?.sum_of_agent_ms && completion.fanout_wall_ms
      ? completion.sum_of_agent_ms / completion.fanout_wall_ms
      : null

  return (
    <section className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle>Parallel fan-out</CardTitle>
              <CardDescription>
                Eight specialists dispatched together with asyncio.gather, one fresh Strands Agent
                per invocation. {done} of {cards.length} returned.
                {state.measuredTokens === false
                  ? " This run is offline: latency is measured, token counts and cost are not, and show as em dashes."
                  : ""}
              </CardDescription>
            </div>
            <div className="text-right">
              <div className="text-[0.6875rem] tracking-wide text-muted-foreground uppercase">
                Wall clock
              </div>
              <div className="text-3xl leading-none font-semibold tracking-tight">
                {elapsedMs === null ? EM_DASH : seconds(elapsedMs)}
              </div>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatTile
              label="Slowest agent"
              value={slowestName ?? EM_DASH}
              caption={
                completion?.slowest_agent_ms !== null && completion?.slowest_agent_ms !== undefined
                  ? ms(completion.slowest_agent_ms)
                  : "measured after the run"
              }
            />
            <StatTile
              label="Fan-out wall time"
              value={completion ? seconds(completion.fanout_wall_ms) : EM_DASH}
              caption="all eight, in parallel"
            />
            <StatTile
              label="Synthesis"
              value={completion ? seconds(completion.synthesis_ms) : EM_DASH}
              caption="master adjudication"
            />
            <StatTile
              label="End to end"
              value={completion ? seconds(completion.end_to_end_ms) : EM_DASH}
              caption={
                completion
                  ? `slowest agent + synthesis; sum of the eight measured latencies was ${seconds(
                      completion.sum_of_agent_ms,
                    )}${speedup ? `, so parallelism recovered ${speedup.toFixed(1)}x on this run` : ""}`
                  : "measured after the run"
              }
            />
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => (
          <AgentTile
            key={card.domain}
            card={card}
            maxLatency={maxLatency}
            elapsedMs={elapsedMs}
          />
        ))}
      </div>

      {state.errors.length > 0 ? <Separator /> : null}
    </section>
  )
}
