/**
 * Small shared display pieces.
 *
 * Two conventions here are load-bearing:
 *
 *  - Risk and decision labels are rendered as the customer's exact strings. No
 *    title-casing, no "Possible", no abbreviation. LOW RISK | POSSIBLE RISK |
 *    HIGH RISK for specialists; LOW/MEDIUM/HIGH RISK for the adjudication.
 *  - Status colour never carries meaning alone: every risk badge pairs its colour
 *    with an icon AND the text label, per the dataviz status rule.
 */

import * as React from "react"
import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  CircleAlert,
  Clipboard,
  ClipboardCheck,
  Loader2,
  XCircle,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { EM_DASH } from "@/lib/format"
import type { AgentState } from "@/lib/types"

/**
 * Status token classes. Drawn from the dataviz status palette (good #0ca30c,
 * warning #fab219, critical #d03b3b) expressed through Tailwind arbitrary values
 * so both themes use the same hexes the validator checked, and always paired with
 * an icon + text label so hue is never the only channel.
 */
const RISK_STYLES: Record<string, { className: string; Icon: React.ElementType }> = {
  "LOW RISK": {
    className:
      "border-[#0ca30c]/45 bg-[#0ca30c]/12 text-[#046b04] dark:text-[#4cc94c]",
    Icon: CheckCircle2,
  },
  "POSSIBLE RISK": {
    className:
      "border-[#fab219]/50 bg-[#fab219]/14 text-[#8a5c00] dark:text-[#fac351]",
    Icon: CircleAlert,
  },
  "MEDIUM RISK": {
    className:
      "border-[#fab219]/50 bg-[#fab219]/14 text-[#8a5c00] dark:text-[#fac351]",
    Icon: CircleAlert,
  },
  "HIGH RISK": {
    className:
      "border-[#d03b3b]/50 bg-[#d03b3b]/14 text-[#a01c1c] dark:text-[#f08a8a]",
    Icon: AlertTriangle,
  },
}

export function RiskBadge({
  band,
  className,
}: {
  band: string | null
  className?: string
}) {
  if (!band) {
    return (
      <Badge variant="outline" className={cn("font-mono text-muted-foreground", className)}>
        {EM_DASH}
      </Badge>
    )
  }
  const style = RISK_STYLES[band]
  if (!style) {
    return (
      <Badge variant="outline" className={className}>
        {band}
      </Badge>
    )
  }
  const { className: tone, Icon } = style
  return (
    <Badge variant="outline" className={cn("gap-1 font-semibold tracking-tight", tone, className)}>
      <Icon aria-hidden />
      {band}
    </Badge>
  )
}

const DECISION_STYLES: Record<string, string> = {
  APPROVE: "border-[#0ca30c]/45 bg-[#0ca30c]/12 text-[#046b04] dark:text-[#4cc94c]",
  "REVIEW AND APPLY STIPULATIONS":
    "border-[#fab219]/50 bg-[#fab219]/14 text-[#8a5c00] dark:text-[#fac351]",
  DECLINE: "border-[#d03b3b]/50 bg-[#d03b3b]/14 text-[#a01c1c] dark:text-[#f08a8a]",
}

export function DecisionBadge({ decision }: { decision: string | null }) {
  if (!decision) {
    return (
      <Badge variant="outline" className="font-mono text-muted-foreground">
        {EM_DASH}
      </Badge>
    )
  }
  const Icon =
    decision === "APPROVE" ? CheckCircle2 : decision === "DECLINE" ? XCircle : CircleAlert
  return (
    <Badge
      variant="outline"
      className={cn(
        "h-auto gap-1.5 px-2.5 py-1 text-sm font-semibold whitespace-normal",
        DECISION_STYLES[decision] ?? "",
      )}
    >
      <Icon aria-hidden />
      {decision}
    </Badge>
  )
}

const STATE_META: Record<AgentState, { label: string; Icon: React.ElementType; className: string }> =
  {
    idle: { label: "Idle", Icon: Circle, className: "text-muted-foreground" },
    pending: { label: "Queued", Icon: Circle, className: "text-muted-foreground" },
    // #256abf is step 500 of the palette's blue ramp: 5.39:1 on the light card
    // surface, where slot-1 #2a78d6 measured only 4.42:1 and fails AA for text.
    running: { label: "Running", Icon: Loader2, className: "text-[#256abf] dark:text-[#6da7ec]" },
    done: { label: "Done", Icon: CheckCircle2, className: "text-[#046b04] dark:text-[#4cc94c]" },
    failed: { label: "Failed", Icon: XCircle, className: "text-[#a01c1c] dark:text-[#f08a8a]" },
  }

export function StateChip({ state }: { state: AgentState }) {
  const { label, Icon, className } = STATE_META[state]
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-xs font-medium", className)}>
      <Icon aria-hidden className={cn("size-3.5", state === "running" && "animate-spin")} />
      {label}
    </span>
  )
}

/**
 * A label/value pair. `value` is pre-formatted by the caller, so an unmeasured
 * field arrives here already as an em dash and this component never has to guess.
 */
export function Metric({
  label,
  value,
  hint,
  mono = true,
  className,
}: {
  label: string
  value: React.ReactNode
  hint?: string
  mono?: boolean
  className?: string
}) {
  const body = (
    <div className={cn("flex flex-col gap-0.5", className)}>
      <span className="text-[0.6875rem] leading-none tracking-wide text-muted-foreground uppercase">
        {label}
      </span>
      <span
        className={cn(
          "text-sm leading-tight font-medium text-foreground",
          mono && "font-mono tabular-nums",
        )}
      >
        {value}
      </span>
    </div>
  )
  if (!hint) return body
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button type="button" className="cursor-help text-left">
          {body}
        </button>
      </TooltipTrigger>
      <TooltipContent>{hint}</TooltipContent>
    </Tooltip>
  )
}

/** A hero/stat figure: proportional digits, per the dataviz figure contract. */
export function StatTile({
  label,
  value,
  caption,
  className,
}: {
  label: string
  value: React.ReactNode
  caption?: React.ReactNode
  className?: string
}) {
  return (
    <div className={cn("flex flex-col gap-1 rounded-lg border border-border/70 p-3", className)}>
      <span className="text-[0.6875rem] tracking-wide text-muted-foreground uppercase">
        {label}
      </span>
      <span className="text-2xl leading-none font-semibold text-foreground">{value}</span>
      {caption ? (
        <span className="text-xs leading-snug text-muted-foreground">{caption}</span>
      ) : null}
    </div>
  )
}

export function CopyButton({
  text,
  label = "Copy",
  onCopied,
}: {
  text: string
  label?: string
  onCopied?: () => void
}) {
  const [copied, setCopied] = React.useState(false)

  const copy = React.useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      onCopied?.()
      window.setTimeout(() => setCopied(false), 1800)
    } catch {
      // Clipboard is permission-gated over plain HTTP on some browsers; fall back
      // to a selection prompt rather than silently doing nothing.
      window.prompt("Copy with Cmd+C:", text)
    }
  }, [text, onCopied])

  return (
    <Button variant="outline" size="sm" onClick={copy}>
      {copied ? <ClipboardCheck aria-hidden /> : <Clipboard aria-hidden />}
      {copied ? "Copied" : label}
    </Button>
  )
}

/** A one-line "measured vs not measured" provenance marker. */
export function SourceNote({
  measured,
  source,
}: {
  measured: boolean
  source: string | null
}) {
  if (!source) return null
  return (
    <span className="text-[0.6875rem] text-muted-foreground">
      {measured
        ? `measured (${source})`
        : `${source}: no model invoked, so tokens and cost are unmeasured`}
    </span>
  )
}
