/**
 * Backend selector and run controls.
 *
 * Two backends, switchable at runtime:
 *  - "local": the FastAPI demo server in ui/server.py.
 *  - "agentcore": a deployed AgentCore Runtime invocation URL.
 *
 * Both always reply text/event-stream, so the client parser is identical; the only
 * difference is the URL, the Authorization header, and the session-id header.
 *
 * The bearer token is held in component state and never written to localStorage.
 *
 * The "agentcore" option exists only in the LOCAL build. On the CloudFront build
 * `/api/stream/*` is already an AgentCore call made server-side by a Lambda with
 * SigV4 from its execution role, so a manual invocation URL and a bearer token
 * pasted into a public page would be redundant and unsafe both. The option is
 * compiled out there -- see ui/src/lib/deployment.ts.
 */

import { Play, RotateCcw } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import {
  ALLOW_MANUAL_RUNTIME_BACKEND,
  EDGE_BACKEND_DETAIL,
  EDGE_BACKEND_LABEL,
} from "@/lib/deployment"
import { cn } from "@/lib/utils"
import type { BackendConfig, BackendKind } from "@/lib/sse"
import type { Health } from "@/lib/types"

export function BackendBar({
  backend,
  onBackendChange,
  applications,
  selected,
  onSelect,
  onRun,
  onReset,
  running,
  health,
}: {
  backend: BackendConfig
  onBackendChange: (backend: BackendConfig) => void
  applications: string[]
  selected: string | null
  onSelect: (id: string) => void
  onRun: () => void
  onReset: () => void
  running: boolean
  health: Health | null
}) {
  return (
    <Card className="py-3">
      <CardContent className="flex flex-wrap items-end gap-3 px-4">
        <div className="flex flex-col gap-1.5">
          <label
            htmlFor="backend-kind"
            className="text-[0.6875rem] tracking-wide text-muted-foreground uppercase"
          >
            Backend
          </label>
          {ALLOW_MANUAL_RUNTIME_BACKEND ? (
            <Select
              value={backend.kind}
              onValueChange={(kind) => onBackendChange({ ...backend, kind: kind as BackendKind })}
            >
              <SelectTrigger id="backend-kind" className="w-52">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="local">Local demo server</SelectItem>
                <SelectItem value="agentcore">AgentCore Runtime</SelectItem>
              </SelectContent>
            </Select>
          ) : (
            // Edge build: there is no choice to offer. `/api/stream/*` already IS an
            // AgentCore Runtime call, made server-side. A dropdown with one item and
            // two empty credential boxes described a configuration step that does not
            // exist on this deployment, so it is replaced by a statement of fact.
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge variant="secondary" className="h-8 cursor-help px-2.5 font-mono text-xs">
                  {EDGE_BACKEND_LABEL}
                </Badge>
              </TooltipTrigger>
              <TooltipContent className="max-w-sm">{EDGE_BACKEND_DETAIL}</TooltipContent>
            </Tooltip>
          )}
        </div>

        {ALLOW_MANUAL_RUNTIME_BACKEND && backend.kind === "agentcore" ? (
          <>
            <div className="flex min-w-[22rem] flex-1 flex-col gap-1.5">
              <label
                htmlFor="agentcore-endpoint"
                className="text-[0.6875rem] tracking-wide text-muted-foreground uppercase"
              >
                Runtime invocation URL
              </label>
              <input
                id="agentcore-endpoint"
                type="url"
                value={backend.endpoint ?? ""}
                placeholder="https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/.../invocations"
                onChange={(event) => onBackendChange({ ...backend, endpoint: event.target.value })}
                className="h-8 rounded-lg border border-input bg-transparent px-2.5 font-mono text-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              />
            </div>
            <div className="flex w-56 flex-col gap-1.5">
              <label
                htmlFor="agentcore-token"
                className="text-[0.6875rem] tracking-wide text-muted-foreground uppercase"
              >
                Bearer token
              </label>
              <input
                id="agentcore-token"
                type="password"
                value={backend.bearerToken ?? ""}
                placeholder="not stored"
                onChange={(event) =>
                  onBackendChange({ ...backend, bearerToken: event.target.value })
                }
                className="h-8 rounded-lg border border-input bg-transparent px-2.5 font-mono text-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              />
            </div>
          </>
        ) : null}

        <Separator orientation="vertical" className="hidden h-8 sm:block" />

        <div className="flex flex-col gap-1.5">
          <label
            htmlFor="application-select"
            className="text-[0.6875rem] tracking-wide text-muted-foreground uppercase"
          >
            Application
          </label>
          <Select value={selected ?? ""} onValueChange={onSelect}>
            <SelectTrigger id="application-select" className="w-44 font-mono">
              <SelectValue placeholder="Select" />
            </SelectTrigger>
            <SelectContent>
              {applications.map((id) => (
                <SelectItem key={id} value={id} className="font-mono">
                  {id}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <Button onClick={onRun} disabled={running || !selected}>
          <Play aria-hidden />
          {running ? "Running" : "Run adjudication"}
        </Button>
        <Button variant="outline" onClick={onReset} disabled={running}>
          <RotateCcw aria-hidden />
          Reset
        </Button>

        <div className="ml-auto flex items-center gap-2">
          {health ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge
                  variant="outline"
                  className={cn(
                    "cursor-help font-mono text-[0.6875rem]",
                    health.measures_tokens
                      ? "border-[#0ca30c]/45 text-[#046b04] dark:text-[#4cc94c]"
                      : "border-[#fab219]/50 text-[#8a5c00] dark:text-[#fac351]",
                  )}
                >
                  {health.measures_tokens ? "live bedrock" : "offline / no tokens measured"}
                </Badge>
              </TooltipTrigger>
              <TooltipContent>
                {health.measures_tokens
                  ? "This process invokes Bedrock, so token usage and cost are measured per call."
                  : "MOCK_MODE is on or the execution modules have not landed. Latency is measured; token counts and cost are not, and render as em dashes."}
              </TooltipContent>
            </Tooltip>
          ) : null}
        </div>
      </CardContent>
    </Card>
  )
}
