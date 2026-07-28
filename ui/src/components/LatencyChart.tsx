/**
 * Measured per-agent latency, horizontal bars.
 *
 * Design decisions, per the dataviz method:
 *  - FORM: the job is "compare magnitude, low to high" across eight named
 *    categories, so it is a horizontal bar chart (long category names), not a
 *    line and not eight categorical hues.
 *  - COLOR: ONE series, therefore ONE hue -- categorical slot 1 blue (#2a78d6
 *    light / #3987e5 dark), validated with scripts/validate_palette.js against
 *    this app's real card surfaces (#ffffff / #171717): all checks PASS. Risk band
 *    is NOT encoded as bar colour: colouring bars by risk would be a status colour
 *    doing a series' job, and length already carries the only quantity here.
 *  - MARKS: bars capped at 18px, 4px rounded data-end and square at the baseline,
 *    hairline solid grid on the value axis only, no gridline on the category axis.
 *  - LABELS: value at the tip of every bar is legal here because there are only
 *    eight and each is the point of its own row; the axis carries the scale.
 *  - No legend: a single series is named by the card title.
 *  - A table view is always available, so no value is gated behind a hover.
 *  - Empty state: before a run there is nothing measured, so the chart renders an
 *    explicit "no measurements yet" panel rather than a zeroed axis.
 */

import * as React from "react"
import { Bar, BarChart, CartesianGrid, LabelList, XAxis, YAxis } from "recharts"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { EM_DASH, integer, ms, shortModel, usd } from "@/lib/format"
import type { AgentCard } from "@/lib/types"

const CHART_CONFIG = {
  latency_ms: {
    label: "Measured latency",
    theme: { light: "#2a78d6", dark: "#3987e5" },
  },
} satisfies ChartConfig

export function LatencyChart({ cards }: { cards: AgentCard[] }) {
  const [showTable, setShowTable] = React.useState(false)

  const measured = React.useMemo(
    () =>
      cards
        .filter((card) => card.latency_ms !== null)
        .map((card) => ({
          agent: card.agent_name,
          latency_ms: card.latency_ms as number,
          model: shortModel(card.model),
          tier: card.tier ?? EM_DASH,
          band: card.risk_band ?? EM_DASH,
          tokens_in: card.input_tokens,
          tokens_out: card.output_tokens,
          cost_usd: card.cost_usd,
        }))
        .sort((a, b) => b.latency_ms - a.latency_ms),
    [cards],
  )

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <CardTitle>Measured latency by agent</CardTitle>
            <CardDescription>
              Wall-clock milliseconds for each specialist on this run. All eight were dispatched at
              once, so the longest bar -- not the sum -- is the fan-out cost.
            </CardDescription>
          </div>
          {measured.length > 0 ? (
            <Button variant="outline" size="sm" onClick={() => setShowTable((value) => !value)}>
              {showTable ? "Show chart" : "Show table"}
            </Button>
          ) : null}
        </div>
      </CardHeader>
      <CardContent>
        {measured.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
            No latency has been measured yet. Run an application to populate this chart.
          </p>
        ) : showTable ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Agent</TableHead>
                <TableHead className="text-right">Latency</TableHead>
                <TableHead>Model</TableHead>
                <TableHead>Tier</TableHead>
                <TableHead>Risk band</TableHead>
                <TableHead className="text-right">Tokens in</TableHead>
                <TableHead className="text-right">Tokens out</TableHead>
                <TableHead className="text-right">Cost</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {measured.map((row) => (
                <TableRow key={row.agent}>
                  <TableCell className="font-mono">{row.agent}</TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {ms(row.latency_ms)}
                  </TableCell>
                  <TableCell className="font-mono text-xs">{row.model}</TableCell>
                  <TableCell className="font-mono text-xs">{row.tier}</TableCell>
                  <TableCell className="text-xs">{row.band}</TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {integer(row.tokens_in)}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {integer(row.tokens_out)}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {usd(row.cost_usd)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <ChartContainer
            config={CHART_CONFIG}
            // Height covers the plot AND the x-axis band, so the card never gets a
            // nested scrollbar.
            className="h-[22rem] w-full"
          >
            <BarChart
              accessibilityLayer
              data={measured}
              layout="vertical"
              margin={{ top: 4, right: 64, bottom: 4, left: 8 }}
              barCategoryGap={6}
            >
              {/* Hairline, solid, one step off the surface; value axis only. */}
              <CartesianGrid horizontal={false} stroke="var(--border)" strokeWidth={1} />
              <XAxis
                type="number"
                dataKey="latency_ms"
                tickLine={false}
                axisLine={false}
                tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
                tickFormatter={(value: number) => `${Math.round(value)}`}
                label={{
                  value: "milliseconds",
                  position: "insideBottom",
                  offset: -2,
                  fill: "var(--muted-foreground)",
                  fontSize: 11,
                }}
              />
              <YAxis
                type="category"
                dataKey="agent"
                width={132}
                tickLine={false}
                axisLine={false}
                tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
              />
              <ChartTooltip
                cursor={{ fill: "var(--muted)", fillOpacity: 0.4 }}
                content={
                  <ChartTooltipContent
                    formatter={(value) => `${Math.round(Number(value))} ms`}
                    labelFormatter={(label) => String(label)}
                  />
                }
              />
              <Bar
                dataKey="latency_ms"
                name="Measured latency"
                fill="var(--color-latency_ms)"
                maxBarSize={18}
                // 4px rounded data-end, square at the baseline.
                radius={[0, 4, 4, 0]}
              >
                <LabelList
                  dataKey="latency_ms"
                  position="right"
                  offset={8}
                  className="fill-muted-foreground"
                  fontSize={11}
                  // Recharts types the label value as RenderableText (which
                  // includes undefined), so narrow before formatting.
                  formatter={(value) =>
                    value === undefined || value === null ? "" : `${Math.round(Number(value))} ms`
                  }
                />
              </Bar>
            </BarChart>
          </ChartContainer>
        )}
      </CardContent>
    </Card>
  )
}
