/**
 * Application selector.
 *
 * Anti-false-positive scenarios sort first and carry an explicit marker, because
 * "reduce false positives" is the customer's stated quality goal -- a demo that
 * only shows the platform catching fraud proves the easy half.
 *
 * Every score's band comes from signal_layer.bands via the API. Nothing is banded
 * in the browser.
 */

import * as React from "react"
import { ShieldCheck } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Skeleton } from "@/components/ui/skeleton"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { dash, integer, money0 } from "@/lib/format"
import type { ApplicationSummary } from "@/lib/types"

/** Band label -> tone. The words are the customer's ("low"/"medium"/"high"/"very high"). */
const BAND_TONE: Record<string, string> = {
  low: "border-[#0ca30c]/40 text-[#046b04] dark:text-[#4cc94c]",
  moderate: "border-[#fab219]/45 text-[#8a5c00] dark:text-[#fac351]",
  medium: "border-[#fab219]/45 text-[#8a5c00] dark:text-[#fac351]",
  elevated: "border-[#ec835a]/45 text-[#8d4520] dark:text-[#f0a686]",
  high: "border-[#d03b3b]/45 text-[#a01c1c] dark:text-[#f08a8a]",
  "very high": "border-[#d03b3b]/60 text-[#a01c1c] dark:text-[#f08a8a]",
}

function BandBadge({ score, band }: { score: number | null; band: string | null }) {
  if (score === null || score === undefined) return <span className="text-muted-foreground">—</span>
  return (
    <span className="inline-flex items-center gap-2">
      <span className="font-mono tabular-nums">{integer(score)}</span>
      {band ? (
        <Badge variant="outline" className={cn("uppercase", BAND_TONE[band] ?? "")}>
          {band}
        </Badge>
      ) : null}
    </span>
  )
}

export function ApplicationSelector({
  applications,
  loading,
  selected,
  onSelect,
  onRun,
  running,
}: {
  applications: ApplicationSummary[]
  loading: boolean
  selected: string | null
  onSelect: (id: string) => void
  onRun: (id: string) => void
  running: boolean
}) {
  // Anti-false-positive first, then by descending fraud score so the two ends of
  // the risk range are both immediately reachable during a live demo.
  const ordered = React.useMemo(() => {
    return [...applications].sort((a, b) => {
      if (a.anti_false_positive !== b.anti_false_positive) return a.anti_false_positive ? -1 : 1
      return (b.fraud_score ?? -1) - (a.fraud_score ?? -1)
    })
  }, [applications])

  const antiCount = ordered.filter((a) => a.anti_false_positive).length

  return (
    <Card>
      <CardHeader>
        <CardTitle>Applications</CardTitle>
        <CardDescription>
          {loading
            ? "Loading the fixture catalog."
            : `${ordered.length} scenarios from fixtures/. ${antiCount} are anti-false-positive tests, listed first: benign explanations that a naive rule engine escalates.`}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="flex flex-col gap-2">
            {Array.from({ length: 5 }).map((_, index) => (
              <Skeleton key={index} className="h-10 w-full" />
            ))}
          </div>
        ) : ordered.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No applications were returned by /api/applications.
          </p>
        ) : (
          <ScrollArea className="h-[22rem]">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Application</TableHead>
                  <TableHead>Scenario</TableHead>
                  <TableHead className="text-right">Loan</TableHead>
                  <TableHead>Vehicle</TableHead>
                  <TableHead>Fraud score</TableHead>
                  <TableHead>Dealer score</TableHead>
                  <TableHead className="text-right">Signals</TableHead>
                  <TableHead className="text-right">Alerts</TableHead>
                  <TableHead className="w-0" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {ordered.map((application) => {
                  const isSelected = application.application_id === selected
                  return (
                    <TableRow
                      key={application.application_id}
                      data-state={isSelected ? "selected" : undefined}
                      className="cursor-pointer"
                      onClick={() => onSelect(application.application_id)}
                    >
                      <TableCell className="font-mono font-medium whitespace-nowrap">
                        {application.application_id}
                      </TableCell>
                      {/* whitespace-normal: TableCell defaults to nowrap, so a long
                          scenario label would push the row instead of wrapping. */}
                      <TableCell className="max-w-[20rem] whitespace-normal">
                        <span className="flex items-start gap-1.5">
                          {application.anti_false_positive ? (
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <span className="mt-0.5 shrink-0 text-[#046b04] dark:text-[#4cc94c]">
                                  <ShieldCheck aria-label="Anti-false-positive scenario" className="size-4" />
                                </span>
                              </TooltipTrigger>
                              <TooltipContent>
                                Anti-false-positive scenario: the signals look alarming but the
                                paired context explains them. The correct outcome is a clear, not a
                                catch.
                              </TooltipContent>
                            </Tooltip>
                          ) : null}
                          <span className="text-sm">{dash(application.scenario)}</span>
                        </span>
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {money0(application.loan_amount)}
                      </TableCell>
                      <TableCell className="text-sm whitespace-nowrap">
                        {dash(application.vehicle)}
                      </TableCell>
                      <TableCell>
                        <BandBadge
                          score={application.fraud_score}
                          band={application.fraud_score_band}
                        />
                      </TableCell>
                      <TableCell>
                        <BandBadge
                          score={application.dealer_consortium_score}
                          band={application.dealer_score_band}
                        />
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {integer(application.signal_count)}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {integer(application.alert_count)}
                      </TableCell>
                      <TableCell>
                        <Button
                          size="sm"
                          variant={isSelected ? "default" : "outline"}
                          disabled={running}
                          onClick={(event) => {
                            event.stopPropagation()
                            onSelect(application.application_id)
                            onRun(application.application_id)
                          }}
                        >
                          Run
                        </Button>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </ScrollArea>
        )}
      </CardContent>
    </Card>
  )
}
