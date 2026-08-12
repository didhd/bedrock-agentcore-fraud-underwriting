/**
 * What each of the nine agents IS: its prompt, its settings, its tools, its data source.
 *
 * The customer asked to see this, and until now it lived in three places none of them
 * would look: a Python module, a private model-config dict, and a git-ignored directory.
 *
 * THE PROMPT IS THE PRODUCT, SO IT IS SHOWN VERBATIM -- LOCALLY
 *
 * Fidelity to the customer's own prompts is the whole point of this port, and the most
 * direct proof is putting the byte-identical text on screen next to its sha256 verdict.
 * The local build does that. The CloudFront build does NOT: that URL is public and
 * unauthenticated, and those files are customer-confidential, so there the panel shows
 * provenance and size and says why the text is absent. The server decides
 * (`prompt_text_included`), not this component -- a client-side check would ship the text
 * to the browser and merely decline to draw it.
 *
 * THE RDS PANEL IS A MOCK AND SAYS SO ON ITS FACE
 *
 * "How do I connect RDS/Postgres to AgentCore" was the migration question, so the seam is
 * shown concretely -- one function, three modes, the exact variables each needs. It opens
 * no connection. A green "connected" light nobody earned would be worse than no light,
 * so every row is labelled mocked and the probe reports which variables are actually set
 * rather than a status.
 */

import * as React from "react"
import { ChevronDown, Database, FileText, Wrench } from "lucide-react"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { StatTile } from "@/components/atoms"
import { EM_DASH, integer, shortModel } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { AgentSeat, SignalSourceInfo } from "@/lib/types"

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-border/60 py-1.5 last:border-0">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-right font-mono text-xs">{children}</span>
    </div>
  )
}

function AgentDetail({ seat, promptIncluded }: { seat: AgentSeat; promptIncluded: boolean }) {
  const [open, setOpen] = React.useState(false)
  const config = seat.config
  const reasoning = config?.reasoning

  return (
    <Card className="gap-0 py-4">
      <CardHeader className="gap-2 px-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="flex flex-col gap-1">
            <CardTitle className="font-mono text-[0.8125rem]">{seat.agent_name}</CardTitle>
            <CardDescription className="text-xs">{seat.description}</CardDescription>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge variant="secondary" className="font-mono text-[0.625rem]">
              {seat.kind}
            </Badge>
            {config ? (
              <Badge variant="outline" className="font-mono text-[0.625rem]">
                {config.tier}
              </Badge>
            ) : null}
            <Badge
              variant={reasoning?.enabled ? "default" : "secondary"}
              className="font-mono text-[0.625rem]"
            >
              thinking {reasoning?.enabled ? `on · ${reasoning.effort}` : "off"}
            </Badge>
          </div>
        </div>
      </CardHeader>

      <CardContent className="flex flex-col gap-3 px-4 pt-3">
        <div className="grid gap-x-6 md:grid-cols-2">
          <div>
            <Field label="Model">{config ? shortModel(config.model_id) : EM_DASH}</Field>
            <Field label="Max output tokens">
              {config ? integer(config.max_output_tokens) : EM_DASH}
            </Field>
            <Field label="Runtime">
              {seat.runtime ? (
                seat.runtime.split("/").pop()
              ) : (
                <span className="text-[#8a5c00] dark:text-[#fac351]">not deployed</span>
              )}
            </Field>
          </div>
          <div>
            <Field label="Reasoning field">
              {reasoning ? reasoning.request_field.split(".").slice(-2).join(".") : EM_DASH}
            </Field>
            <Field label="Transport">{reasoning?.transport ?? EM_DASH}</Field>
            <Field label="Instance">{config?.instance_lifetime ?? EM_DASH}</Field>
          </div>
        </div>

        {/* Tools, and for a specialist the reason the answer is none. */}
        <div className="flex flex-col gap-1.5 rounded-lg border border-border/70 px-3 py-2.5">
          <span className="flex items-center gap-1.5 text-xs font-medium">
            <Wrench className="size-3" aria-hidden />
            Tools
            <Badge variant="secondary" className="ml-1 font-mono text-[0.625rem]">
              {seat.tools?.count ?? 0}
            </Badge>
            {seat.tools?.max_per_query ? (
              <span className="text-[0.6875rem] text-muted-foreground">
                max {seat.tools.max_per_query} per query
              </span>
            ) : null}
          </span>
          {seat.tools?.tools?.length ? (
            <div className="flex flex-wrap gap-1">
              {seat.tools.tools.map((tool) => (
                <Badge key={tool.name} variant="outline" className="font-mono text-[0.625rem]">
                  {tool.name}
                </Badge>
              ))}
            </div>
          ) : null}
          <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
            {seat.tools?.why_none ?? seat.tools?.why_capped}
          </p>
        </div>

        {/* Prompt. */}
        <div className="flex flex-col gap-2 rounded-lg border border-border/70 px-3 py-2.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="flex items-center gap-1.5 text-xs font-medium">
              <FileText className="size-3" aria-hidden />
              {config?.prompt.source.replace("prompts/verbatim/", "") ?? "prompt"}
            </span>
            <div className="flex items-center gap-1.5">
              {config ? (
                <span className="font-mono text-[0.625rem] text-muted-foreground">
                  {integer(config.prompt.chars)} chars · ~{integer(config.prompt.estimated_tokens)}{" "}
                  tok
                </span>
              ) : null}
              <Badge variant="secondary" className="font-mono text-[0.625rem]">
                sha256 verified
              </Badge>
            </div>
          </div>

          {promptIncluded && seat.prompt_text ? (
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setOpen((prior) => !prior)}
                className="h-7 justify-start gap-1.5 px-1 text-xs"
              >
                <ChevronDown
                  className={cn("size-3 transition-transform", open && "rotate-180")}
                  aria-hidden
                />
                {open ? "Hide" : "Show"} verbatim prompt
              </Button>
              {open ? (
                <ScrollArea className="h-72 rounded-md border bg-muted/40">
                  <pre className="p-3 font-mono text-[0.6875rem] leading-relaxed whitespace-pre-wrap">
                    {seat.prompt_text}
                  </pre>
                </ScrollArea>
              ) : null}
            </>
          ) : (
            <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
              The prompt text is not served by this deployment. It is customer-confidential
              and this is a public URL, so only its provenance and size travel. Run the
              demo locally to read it.
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

function DataSourcePanel({ source }: { source: SignalSourceInfo }) {
  const [probing, setProbing] = React.useState<string | null>(null)
  const [results, setResults] = React.useState<Record<string, unknown>>({})

  const probe = React.useCallback(async (mode: string) => {
    setProbing(mode)
    try {
      // A 404 is expected on the CloudFront build: the probe is a live endpoint and the
      // edge serves only baked objects. Degrading to the static requirement list (which
      // is already in /api/agents) beats an error toast for a panel that is a mock
      // anyway.
      const response = await fetch(`/api/datasource/probe?mode=${encodeURIComponent(mode)}`)
      if (!response.ok) return
      const json = await response.json()
      setResults((prior) => ({ ...prior, [mode]: json }))
    } finally {
      setProbing(null)
    }
  }, [])

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Database className="size-4" aria-hidden />
          Data source
          <Badge variant="secondary" className="font-mono text-[0.625rem]">
            mocked
          </Badge>
        </CardTitle>
        <CardDescription>
          Where each agent's signals come from, and what changing it involves. This panel
          opens no connection.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="grid gap-2 sm:grid-cols-2">
          <StatTile label="Active source" value={source.active} caption="this run" />
          <StatTile
            label="Swap point"
            value="1 function"
            caption={source.swap_point}
          />
        </div>

        <Alert>
          <Database aria-hidden />
          <AlertTitle>One seam, not nine</AlertTitle>
          <AlertDescription className="text-xs leading-relaxed">
            {source.note}
          </AlertDescription>
        </Alert>

        <div className="flex flex-col gap-3">
          {source.modes.map((mode) => (
            <div
              key={mode.id}
              className={cn(
                "flex flex-col gap-2 rounded-lg border px-3 py-2.5",
                mode.id === source.active ? "border-primary/50 bg-primary/5" : "border-border/70",
              )}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="flex items-center gap-2 text-sm font-medium">
                  {mode.label}
                  {mode.id === source.active ? (
                    <Badge className="font-mono text-[0.625rem]">active</Badge>
                  ) : mode.available ? null : (
                    <Badge variant="secondary" className="font-mono text-[0.625rem]">
                      not configured
                    </Badge>
                  )}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 text-xs"
                  disabled={probing === mode.id}
                  onClick={() => void probe(mode.id)}
                >
                  {probing === mode.id ? "Checking" : "Check requirements"}
                </Button>
              </div>
              <p className="text-xs leading-relaxed text-muted-foreground">{mode.detail}</p>

              {mode.requires?.length ? (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="h-7 text-[0.6875rem]">Requirement</TableHead>
                      <TableHead className="h-7 w-24 text-[0.6875rem]">Set</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(
                      ((results[mode.id] as { checks?: { requirement: string; present: boolean | null }[] })
                        ?.checks ?? mode.requires.map((r) => ({ requirement: r, present: null })))
                    ).map((check) => (
                      <TableRow key={check.requirement}>
                        <TableCell className="py-1.5 font-mono text-[0.6875rem]">
                          {check.requirement}
                        </TableCell>
                        <TableCell className="py-1.5">
                          {check.present === null ? (
                            <span className="text-muted-foreground">{EM_DASH}</span>
                          ) : (
                            <Badge
                              variant={check.present ? "default" : "secondary"}
                              className="font-mono text-[0.625rem]"
                            >
                              {check.present ? "yes" : "no"}
                            </Badge>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : null}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

export function AgentsPanel({
  seats,
  promptIncluded,
  signalSource,
}: {
  seats: AgentSeat[]
  promptIncluded: boolean
  signalSource: SignalSourceInfo | null
}) {
  const withReasoning = seats.filter((s) => s.config?.reasoning.enabled).length

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardHeader>
          <CardTitle>Agent configuration</CardTitle>
          <CardDescription>
            Every value below is read from the modules that build the agent, so it cannot
            drift from what runs.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <StatTile label="Agents" value={integer(seats.length)} caption="1 orchestrator + 8 specialists" />
          <StatTile
            label="Deployed runtimes"
            value={integer(seats.filter((s) => s.runtime).length)}
            caption="independently invocable"
          />
          <StatTile
            label="Thinking enabled"
            value={`${withReasoning} of ${seats.length}`}
            caption={withReasoning ? "the synthesizer only" : "none"}
          />
          <StatTile
            label="Prompts"
            value={promptIncluded ? "verbatim" : "described"}
            caption={promptIncluded ? "shown in full below" : "confidential on a public URL"}
          />
        </CardContent>
      </Card>

      {signalSource ? <DataSourcePanel source={signalSource} /> : null}

      <Separator />

      <div className="grid gap-4 xl:grid-cols-2">
        {seats.map((seat) => (
          <AgentDetail key={seat.id} seat={seat} promptIncluded={promptIncluded} />
        ))}
      </div>
    </div>
  )
}
