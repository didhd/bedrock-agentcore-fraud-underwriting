/**
 * Signal-layer transparency: the answer to "did you preserve our semantic-layer
 * governance?".
 *
 * What this panel has to demonstrate, per domain:
 *  - signals are PRECOMPUTED, not re-queried per agent;
 *  - each agent receives ONLY its own domain's projection;
 *  - the paired `*_context` fields it must weigh before escalating;
 *  - the alert IDs it is entitled to (General Rule 3 categories plus its own doc's
 *    extras), the ones that actually fired, their categories and finding text;
 *  - the guardrail rule IDs applied.
 *
 * Where the projection is not yet computable (signal_layer.registry still landing),
 * the panel says exactly that rather than showing a plausible-looking count. An
 * invented signal count here would undermine the very claim the panel makes.
 */

import { Info, Lock } from "lucide-react"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { StatTile } from "@/components/atoms"
import { EM_DASH, integer } from "@/lib/format"
import type { DomainPayloadView, PayloadResponse } from "@/lib/types"

function scalar(value: unknown): string {
  if (value === null || value === undefined) return "null"
  if (typeof value === "object") return JSON.stringify(value)
  return String(value)
}

function DomainDetail({ view }: { view: DomainPayloadView }) {
  const contextEntries = Object.entries(view.paired_context)
  const signalEntries = view.signals ? Object.entries(view.signals) : null

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-2 sm:grid-cols-3">
        <StatTile
          label="Signals in view"
          value={view.signal_count === null ? EM_DASH : integer(view.signal_count)}
          caption="projected for this domain only"
        />
        <StatTile
          label="Alert IDs entitled"
          value={view.entitled_alert_count === null ? EM_DASH : integer(view.entitled_alert_count)}
          caption="General Rule 3 categories plus this doc's extras"
        />
        <StatTile
          label="Alerts fired"
          value={integer(view.alerts_fired.length)}
          caption="on this application, within this domain's entitlement"
        />
      </div>

      {view.signals_unavailable_reason ? (
        <Alert>
          <Info aria-hidden />
          <AlertTitle>Per-domain signal projection not computed</AlertTitle>
          <AlertDescription>{view.signals_unavailable_reason}</AlertDescription>
        </Alert>
      ) : null}

      {signalEntries && signalEntries.length > 0 ? (
        <div className="flex flex-col gap-2">
          <span className="text-[0.6875rem] tracking-wide text-muted-foreground uppercase">
            Signals visible to {view.agent_name}
          </span>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Signal</TableHead>
                <TableHead className="text-right">Value</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {signalEntries.map(([name, value]) => (
                <TableRow key={name}>
                  <TableCell className="font-mono text-xs">{name}</TableCell>
                  <TableCell className="text-right font-mono text-xs tabular-nums">
                    {scalar(value)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : null}

      <div className="flex flex-col gap-2">
        <span className="text-[0.6875rem] tracking-wide text-muted-foreground uppercase">
          Paired context fields
        </span>
        {contextEntries.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No paired context field on this application for this domain.
          </p>
        ) : (
          contextEntries.map(([name, value]) => (
            <div key={name} className="rounded-lg border border-border/70 p-3">
              <p className="font-mono text-[0.6875rem] text-muted-foreground">{name}</p>
              <p className="mt-1 text-sm leading-relaxed text-foreground/90">{scalar(value)}</p>
            </div>
          ))
        )}
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-[0.6875rem] tracking-wide text-muted-foreground uppercase">
          Alerts fired
        </span>
        {view.alerts_fired.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No alerts within this domain's entitlement fired on this application.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-20">Alert</TableHead>
                <TableHead className="w-56">Categories</TableHead>
                <TableHead>Finding</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {view.alerts_fired.map((alert, index) => (
                <TableRow key={`${alert.alert_id}-${index}`}>
                  <TableCell className="font-mono text-xs">
                    {alert.alert_id === null ? EM_DASH : alert.alert_id}
                  </TableCell>
                  <TableCell>
                    <span className="flex flex-wrap gap-1">
                      {alert.categories.length === 0
                        ? EM_DASH
                        : alert.categories.map((category) => (
                            <Badge key={category} variant="secondary" className="text-[0.6875rem]">
                              {category}
                            </Badge>
                          ))}
                    </span>
                  </TableCell>
                  {/* whitespace-normal: TableCell defaults to nowrap, which would
                      clip an alert's finding text. */}
                  <TableCell className="text-sm leading-relaxed whitespace-normal">
                    {alert.finding_text || EM_DASH}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-[0.6875rem] tracking-wide text-muted-foreground uppercase">
          Guardrails applied
        </span>
        {view.guardrails_applied.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No guardrail rule IDs are recorded on this application for this domain.
          </p>
        ) : (
          <span className="flex flex-wrap gap-1.5">
            {view.guardrails_applied.map((rule) => (
              <Badge key={rule} variant="outline" className="gap-1 font-mono text-[0.6875rem]">
                <Lock aria-hidden />
                {rule}
              </Badge>
            ))}
          </span>
        )}
      </div>
    </div>
  )
}

export function SignalLayerPanel({
  payload,
  loading,
  applicationId,
}: {
  payload: PayloadResponse | null
  loading: boolean
  applicationId: string | null
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Signal layer</CardTitle>
        <CardDescription>
          Signals are computed once, before any agent runs, and each specialist is handed only its
          own domain's projection -- so eight agents never re-issue the same query. Alert
          entitlements come from the General Rule 3 categories plus each domain's own orchestration
          document.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {!applicationId ? (
          <p className="rounded-lg border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
            Select an application to inspect its signal layer.
          </p>
        ) : loading ? (
          <div className="flex flex-col gap-2">
            {Array.from({ length: 4 }).map((_, index) => (
              <Skeleton key={index} className="h-12 w-full" />
            ))}
          </div>
        ) : !payload ? (
          <p className="rounded-lg border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
            The signal-layer view could not be loaded for {applicationId}.
          </p>
        ) : (
          <>
            <div className="grid gap-2 sm:grid-cols-3">
              <StatTile
                label="Signals on application"
                value={integer(payload.total_signal_count)}
                caption="precomputed before fan-out"
              />
              <StatTile label="Alerts fired" value={integer(payload.total_alert_count)} />
              <StatTile
                label="Projection source"
                value={
                  <span className="font-mono text-xs leading-snug break-all">
                    {payload.projection_source}
                  </span>
                }
              />
            </div>
            <Separator />
            <Accordion type="multiple">
              {payload.domains.map((view) => (
                <AccordionItem key={view.domain} value={view.domain}>
                  <AccordionTrigger className="pr-2">
                    <span className="flex flex-1 flex-wrap items-center gap-2 pr-3">
                      <span className="font-mono text-[0.8125rem]">{view.agent_name}</span>
                      <Badge variant="secondary" className="font-mono text-[0.6875rem]">
                        {view.signal_count === null
                          ? `${EM_DASH} signals`
                          : `${view.signal_count} signals`}
                      </Badge>
                      <Badge variant="outline" className="font-mono text-[0.6875rem]">
                        {view.alerts_fired.length} alerts
                      </Badge>
                      {Object.keys(view.paired_context).length > 0 ? (
                        <Badge variant="outline" className="font-mono text-[0.6875rem]">
                          {Object.keys(view.paired_context).length} context
                        </Badge>
                      ) : null}
                    </span>
                  </AccordionTrigger>
                  <AccordionContent>
                    <DomainDetail view={view} />
                  </AccordionContent>
                </AccordionItem>
              ))}
            </Accordion>
          </>
        )}
      </CardContent>
    </Card>
  )
}
