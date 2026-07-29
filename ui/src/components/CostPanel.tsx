/**
 * Cost, from measured token usage only.
 *
 * This panel is where a demo is most tempted to lie, so the rules are explicit:
 *
 *  - Per-agent cost is shown only when the run measured that agent's tokens. In
 *    MOCK_MODE nothing is measured, so every cost cell is an em dash and the panel
 *    says why. It does NOT fall back to a nominal token table.
 *  - The volume projection is arithmetic the user drives: they set applications per
 *    day, and the output is labelled as multiplication of the measured per-app cost.
 *    It is never described as a saving.
 *  - There is NO platform comparison bar. A comparison would need a number we did
 *    not measure. The one figure the customer themselves reported is shown as a
 *    quotation, attributed, in its own box, and is never arithmetically combined
 *    with our measurements.
 */

import * as React from "react"
import { Info } from "lucide-react"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { StatTile } from "@/components/atoms"
import { EM_DASH, integer, rateLabel, shortModel, usd } from "@/lib/format"
import type { AgentCard } from "@/lib/types"
import type { SynthesisState } from "@/hooks/useRun"

const VOLUME_OPTIONS = [
  { value: "1000", label: "1,000 applications / day" },
  { value: "4500", label: "4,500 applications / day" },
  { value: "10000", label: "10,000 applications / day" },
  { value: "50000", label: "50,000 applications / day" },
]

export function CostPanel({
  cards,
  synthesis,
  measuredTokens,
}: {
  cards: AgentCard[]
  synthesis: SynthesisState
  measuredTokens: boolean | null
}) {
  const [volume, setVolume] = React.useState("4500")

  const rows = React.useMemo(
    () => [
      ...cards.map((card) => ({
        key: card.domain,
        name: card.agent_name,
        model: card.model,
        tier: card.tier,
        rate: card.rate,
        input: card.input_tokens,
        output: card.output_tokens,
        cacheRead: card.cache_read_tokens,
        cacheWrite: card.cache_write_tokens,
        cost: card.cost_usd,
      })),
      {
        key: "synthesis",
        name: "Master synthesis",
        model: synthesis.model,
        tier: synthesis.tier,
        rate: synthesis.rate,
        input: synthesis.input_tokens,
        output: synthesis.output_tokens,
        cacheRead: synthesis.cache_read_tokens,
        cacheWrite: synthesis.cache_write_tokens,
        cost: synthesis.cost_usd,
      },
    ],
    [cards, synthesis],
  )

  // The total is only a real total when EVERY component was measured. One missing
  // component makes the sum meaningless, so it stays an em dash.
  const allMeasured = rows.length > 0 && rows.every((row) => row.cost !== null)
  const perApp = allMeasured ? rows.reduce((sum, row) => sum + (row.cost ?? 0), 0) : null

  const tokensIn = rows.every((row) => row.input !== null)
    ? rows.reduce((sum, row) => sum + (row.input ?? 0), 0)
    : null
  const tokensOut = rows.every((row) => row.output !== null)
    ? rows.reduce((sum, row) => sum + (row.output ?? 0), 0)
    : null

  // Cache-write is part of the prompt and it is billed, so a headline "input" figure
  // that omits it understates the prompt badly. On GPT-5.6 implicit caching is ON by
  // default, so a first call reports almost the whole prompt as cache-write: a
  // measured synthesis showed input=2 against cache-write=3,888 -- i.e. 99.9% of the
  // prompt, and 56% of the call's cost, sitting outside the "In" column. Hence a
  // separate total rather than folding it into `tokensIn`, which must keep matching
  // what the provider labels as input.
  const tokensCacheWrite = rows.every((row) => row.cacheWrite !== null)
    ? rows.reduce((sum, row) => sum + (row.cacheWrite ?? 0), 0)
    : null
  const promptTokens =
    tokensIn === null || tokensCacheWrite === null ? tokensIn : tokensIn + tokensCacheWrite

  const perDay = perApp === null ? null : perApp * Number(volume)
  const perMonth = perDay === null ? null : perDay * 30

  return (
    <Card>
      <CardHeader>
        <CardTitle>Cost</CardTitle>
        <CardDescription>
          Per-agent cost computed from this run's measured token usage at the published Bedrock rate
          for each agent's model. Nothing on this panel is estimated.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {measuredTokens === false ? (
          <Alert>
            <Info aria-hidden />
            <AlertTitle>This run measured no tokens</AlertTitle>
            <AlertDescription>
              The run executed offline, so no Bedrock call was made and no token usage exists to
              cost. Every cost cell below is an em dash by design. Set MOCK_MODE=0 and re-run against
              Bedrock to populate them.
            </AlertDescription>
          </Alert>
        ) : null}

        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <StatTile
            label="Cost per application"
            value={usd(perApp)}
            caption={
              allMeasured
                ? "sum of nine measured calls"
                : "shown once every call in the run reports usage"
            }
          />
          <StatTile
            label="Prompt tokens"
            value={integer(promptTokens)}
            caption={
              tokensCacheWrite
                ? `${integer(tokensIn)} uncached + ${integer(tokensCacheWrite)} cache-write`
                : "uncached input; providers count cached tokens separately"
            }
          />
          <StatTile label="Output tokens" value={integer(tokensOut)} />
          <StatTile
            label="Models in this run"
            value={integer(new Set(rows.map((row) => row.model).filter(Boolean)).size)}
            caption="each agent right-sized independently"
          />
        </div>

        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Agent</TableHead>
              <TableHead>Model</TableHead>
              <TableHead>Tier</TableHead>
              <TableHead>Rate applied</TableHead>
              <TableHead className="text-right">In</TableHead>
              <TableHead className="text-right">Out</TableHead>
              <TableHead className="text-right">Cache read</TableHead>
              <TableHead className="text-right">Cache write</TableHead>
              <TableHead className="text-right">Cost</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.key}>
                <TableCell className="font-mono text-xs whitespace-nowrap">{row.name}</TableCell>
                <TableCell className="font-mono text-xs">{shortModel(row.model)}</TableCell>
                <TableCell>
                  {row.tier ? (
                    <Badge variant="secondary" className="font-mono text-[0.6875rem]">
                      {row.tier}
                    </Badge>
                  ) : (
                    EM_DASH
                  )}
                </TableCell>
                <TableCell className="font-mono text-[0.6875rem] text-muted-foreground">
                  {rateLabel(row.rate)}
                </TableCell>
                <TableCell className="text-right font-mono text-xs tabular-nums">
                  {integer(row.input)}
                </TableCell>
                <TableCell className="text-right font-mono text-xs tabular-nums">
                  {integer(row.output)}
                </TableCell>
                <TableCell className="text-right font-mono text-xs tabular-nums">
                  {integer(row.cacheRead)}
                </TableCell>
                <TableCell className="text-right font-mono text-xs tabular-nums">
                  {integer(row.cacheWrite)}
                </TableCell>
                <TableCell className="text-right font-mono text-xs tabular-nums">
                  {usd(row.cost)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
          <TableFooter>
            <TableRow>
              <TableCell colSpan={8} className="font-medium">
                Total per application
              </TableCell>
              <TableCell className="text-right font-mono tabular-nums">{usd(perApp)}</TableCell>
            </TableRow>
          </TableFooter>
        </Table>

        <Separator />

        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div className="flex flex-col gap-1.5">
              <span className="text-[0.6875rem] tracking-wide text-muted-foreground uppercase">
                Volume projection
              </span>
              <Select value={volume} onValueChange={setVolume}>
                <SelectTrigger className="w-64">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {VOLUME_OPTIONS.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid flex-1 gap-2 sm:grid-cols-2">
              <StatTile label="Per day" value={usd(perDay)} />
              <StatTile label="Per 30 days" value={usd(perMonth)} />
            </div>
          </div>
          <p className="text-xs leading-relaxed text-muted-foreground">
            Projection is arithmetic on the measured per-application cost above:
            per-application cost multiplied by {integer(Number(volume))} applications, then by 30
            days. It is not a saving, not a quote, and assumes this application's token footprint is
            representative -- which it may not be.
          </p>
        </div>

        <Alert>
          <Info aria-hidden />
          <AlertTitle>Customer-reported figures, for their reference only</AlertTitle>
          <AlertDescription>
            <div className="flex flex-col gap-1 text-xs">
              <span>
                Point Predictive reported processing 4,500 applications in more than 48 hours on
                their current platform, and a target of under one minute per application.
              </span>
              <span>
                These are the customer's own numbers, quoted as context for the target. This demo
                does not measure their current platform, so nothing here is compared against it and
                no saving is claimed.
              </span>
            </div>
          </AlertDescription>
        </Alert>
      </CardContent>
    </Card>
  )
}
