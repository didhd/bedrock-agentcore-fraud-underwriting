/**
 * The master adjudication as a formal decision panel.
 *
 * The contract this panel proves: EXACTLY 21 keys, in the customer's order, with
 * no application_id key. The indicator is driven by the server's key_check, which
 * compares against a key list built from prompts.loader.DOMAINS -- so the check
 * cannot drift from the master prompt's own order.
 *
 * The enum conflict between the customer's two artifacts is rendered as a discreet
 * informational alert. It is a real open question for them to answer, not a defect
 * to bury: the prompt file is executable and wins, the PDF says something else.
 */

import * as React from "react"
import { CircleAlert, CircleCheck, Info } from "lucide-react"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { CopyButton, DecisionBadge, Metric, RiskBadge } from "@/components/atoms"
import { cn } from "@/lib/utils"
import { EM_DASH, integer, ms, usd } from "@/lib/format"
import type { EnumConflict, Health, KeyCheck } from "@/lib/types"
import type { SynthesisState } from "@/hooks/useRun"

function KeyCheckBadge({ check, expected }: { check: KeyCheck | null; expected: number }) {
  if (!check) {
    return (
      <Badge variant="outline" className="font-mono text-muted-foreground">
        {expected} keys, not yet validated
      </Badge>
    )
  }
  const Icon = check.valid ? CircleCheck : CircleAlert
  return (
    <Badge
      variant="outline"
      className={cn(
        "gap-1.5 font-mono",
        check.valid
          ? "border-[#0ca30c]/45 text-[#046b04] dark:text-[#4cc94c]"
          : "border-[#d03b3b]/50 text-[#a01c1c] dark:text-[#f08a8a]",
      )}
    >
      <Icon aria-hidden />
      {check.valid
        ? `${check.actual_count} keys, contract-validated`
        : `${check.actual_count}/${check.expected_count} keys - contract violation`}
    </Badge>
  )
}

function KeyCheckDetail({ check }: { check: KeyCheck }) {
  if (check.valid) {
    return (
      <p className="text-xs text-muted-foreground">
        All {check.expected_count} keys present, in the exact order the master prompt requires, with
        no extra keys. The prompt defines no application_id key and none was returned.
      </p>
    )
  }
  return (
    <div className="flex flex-col gap-1 text-xs">
      {check.missing.length > 0 ? (
        <p className="text-[#a01c1c] dark:text-[#f08a8a]">
          Missing: <span className="font-mono">{check.missing.join(", ")}</span>
        </p>
      ) : null}
      {check.unexpected.length > 0 ? (
        <p className="text-[#a01c1c] dark:text-[#f08a8a]">
          Unexpected: <span className="font-mono">{check.unexpected.join(", ")}</span>
        </p>
      ) : null}
      {!check.order_matches && check.missing.length === 0 && check.unexpected.length === 0 ? (
        <p className="text-[#8a5c00] dark:text-[#fac351]">
          All keys are present but their order differs from the master prompt's required order.
        </p>
      ) : null}
    </div>
  )
}

function EnumConflictAlert({ conflict }: { conflict: EnumConflict }) {
  return (
    <Alert>
      <Info aria-hidden />
      <AlertTitle>{conflict.title}</AlertTitle>
      <AlertDescription>
        <div className="flex flex-col gap-2">
          {[conflict.executable, conflict.narrative].map((side) => (
            <div key={side.artifact} className="flex flex-col gap-0.5">
              <span className="font-mono text-xs text-foreground">{side.artifact}</span>
              <span className="text-xs">{side.status}</span>
              <span className="text-xs">
                risk: <span className="font-mono">{side.overall_risk_decision.join(" | ")}</span>
              </span>
              <span className="text-xs">
                recommendation:{" "}
                <span className="font-mono">{side.recommendation_decision.join(" | ")}</span>
              </span>
            </div>
          ))}
          <span className="text-xs">{conflict.action}</span>
        </div>
      </AlertDescription>
    </Alert>
  )
}

function str(value: unknown): string | null {
  if (value === null || value === undefined) return null
  return typeof value === "string" ? value : JSON.stringify(value)
}

export function AdjudicationPanel({
  synthesis,
  health,
}: {
  synthesis: SynthesisState
  health: Health | null
}) {
  const adjudication = synthesis.adjudication
  const domains = health?.domains ?? []
  const expectedKeys = health?.adjudication_keys.length ?? 21

  const overall = adjudication ? str(adjudication.overall_risk_decision) : null
  const recommendation = adjudication ? str(adjudication.recommendation_decision) : null
  const stipulations = adjudication ? str(adjudication.recommended_stipulations) : null
  const topRisk = adjudication ? str(adjudication.top_risk_factors) : null
  const masterSummary = adjudication ? str(adjudication.master_summary) : null

  const rawJson = React.useMemo(
    () => (adjudication ? JSON.stringify(adjudication, null, 2) : ""),
    [adjudication],
  )

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle>Master adjudication</CardTitle>
            <CardDescription>
              The synthesizer's strict-JSON decision, rendered from the same object the pipeline
              persists.
            </CardDescription>
          </div>
          <KeyCheckBadge check={synthesis.key_check} expected={expectedKeys} />
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {!adjudication ? (
          <p className="rounded-lg border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
            No adjudication yet. Run an application to produce a decision.
          </p>
        ) : (
          <Tabs defaultValue="decision">
            <TabsList>
              <TabsTrigger value="decision">Decision</TabsTrigger>
              <TabsTrigger value="dimensions">Per-dimension</TabsTrigger>
              <TabsTrigger value="json">Raw JSON</TabsTrigger>
            </TabsList>

            <TabsContent value="decision" className="flex flex-col gap-4 pt-4">
              <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
                <div className="flex flex-col gap-1">
                  <span className="text-[0.6875rem] tracking-wide text-muted-foreground uppercase">
                    Overall risk
                  </span>
                  <RiskBadge band={overall} className="h-auto px-2.5 py-1 text-sm" />
                </div>
                <div className="flex flex-col gap-1">
                  <span className="text-[0.6875rem] tracking-wide text-muted-foreground uppercase">
                    Recommendation
                  </span>
                  <DecisionBadge decision={recommendation} />
                </div>
                <Metric
                  label="Synthesis latency"
                  value={ms(synthesis.latency_ms)}
                  className="ml-auto"
                />
                <Metric label="Synthesis cost" value={usd(synthesis.cost_usd)} />
              </div>

              <Separator />

              <div className="flex flex-col gap-1.5">
                <span className="text-[0.6875rem] tracking-wide text-muted-foreground uppercase">
                  Master summary
                </span>
                <p className="text-sm leading-relaxed text-foreground/90">
                  {masterSummary ?? EM_DASH}
                </p>
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <div className="flex flex-col gap-1.5">
                  <span className="text-[0.6875rem] tracking-wide text-muted-foreground uppercase">
                    Top risk factors
                  </span>
                  <p className="text-sm leading-relaxed text-foreground/90">
                    {topRisk
                      ? topRisk
                      : overall === "LOW RISK"
                        ? "Empty, as the contract requires for a LOW RISK outcome."
                        : EM_DASH}
                  </p>
                </div>
                <div className="flex flex-col gap-1.5">
                  <span className="text-[0.6875rem] tracking-wide text-muted-foreground uppercase">
                    Recommended stipulations
                  </span>
                  <p className="text-sm leading-relaxed text-foreground/90">
                    {stipulations
                      ? stipulations
                      : recommendation === "REVIEW AND APPLY STIPULATIONS"
                        ? EM_DASH
                        : "Empty. Stipulations apply only to the REVIEW AND APPLY STIPULATIONS outcome."}
                  </p>
                </div>
              </div>

              {synthesis.key_check ? (
                <>
                  <Separator />
                  <KeyCheckDetail check={synthesis.key_check} />
                </>
              ) : null}

              {health?.enum_conflict ? <EnumConflictAlert conflict={health.enum_conflict} /> : null}
            </TabsContent>

            <TabsContent value="dimensions" className="pt-4">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-24">Flag</TableHead>
                    <TableHead className="w-40">Dimension</TableHead>
                    <TableHead>Summary</TableHead>
                    <TableHead className="w-24 text-right">Chars</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {domains.map((domain) => {
                    const summary = str(adjudication[`${domain}_summary`])
                    const flag = adjudication[`${domain}_flag`]
                    const flagged = flag === 1
                    const limit = health?.length_rules.master_summary_max_chars ?? 1200
                    const chars = summary?.length ?? null
                    return (
                      <TableRow key={domain}>
                        <TableCell>
                          <Badge
                            variant="outline"
                            className={cn(
                              "gap-1 font-mono",
                              flagged
                                ? "border-[#d03b3b]/50 text-[#a01c1c] dark:text-[#f08a8a]"
                                : "border-[#0ca30c]/40 text-[#046b04] dark:text-[#4cc94c]",
                            )}
                          >
                            {flagged ? <CircleAlert aria-hidden /> : <CircleCheck aria-hidden />}
                            {flag === 0 || flag === 1 ? String(flag) : EM_DASH}
                          </Badge>
                        </TableCell>
                        <TableCell className="font-mono text-xs">
                          {health?.agent_names[domain] ?? domain}
                        </TableCell>
                        {/* The vendored TableCell defaults to whitespace-nowrap,
                            which clips prose. These summaries are up to 1200
                            characters, so wrapping is explicit. */}
                        <TableCell className="text-sm leading-relaxed whitespace-normal">
                          {summary ?? EM_DASH}
                        </TableCell>
                        <TableCell
                          className={cn(
                            "text-right font-mono text-xs tabular-nums",
                            chars !== null && chars >= limit
                              ? "text-[#a01c1c] dark:text-[#f08a8a]"
                              : "text-muted-foreground",
                          )}
                        >
                          {chars === null ? EM_DASH : `${integer(chars)}/${integer(limit)}`}
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </TabsContent>

            <TabsContent value="json" className="flex flex-col gap-3 pt-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs text-muted-foreground">
                  The object as the pipeline emits it. Key order is preserved, so the {expectedKeys}
                  -key contract is inspectable directly.
                </p>
                <CopyButton text={rawJson} label="Copy JSON" />
              </div>
              <ScrollArea className="h-[26rem] rounded-lg border border-border">
                <pre className="p-4 font-mono text-xs leading-relaxed">{rawJson}</pre>
              </ScrollArea>
            </TabsContent>
          </Tabs>
        )}
      </CardContent>
    </Card>
  )
}
