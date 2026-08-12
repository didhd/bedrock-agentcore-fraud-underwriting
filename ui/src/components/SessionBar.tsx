/**
 * Session identity, in the header where it is always visible.
 *
 * WHY IT MOVED HERE
 *
 * The batch session id was rendered in the footer, in a sentence, below every panel on the
 * page -- so during a demo nobody ever saw it. Three different things are keyed on these
 * ids and all three are questions people ask out loud while watching:
 *
 *   - AgentCore Runtime isolates sessions. A session id is what decides whether a follow-up
 *     question lands in the same conversation or a fresh one.
 *   - Long-term memory is scoped by ACTOR, and short-term by actor + session. "Does it
 *     remember me" is answerable only if you can see which actor you are.
 *   - Traces are listed by session id (`agentcore traces list` prints Session ID beside
 *     each trace). Without the id on screen, finding the trace for what you just watched
 *     means guessing from timestamps.
 *
 * WHAT IT REFUSES TO DO
 *
 * It never invents a value. A session id exists only after a run or a chat turn has started,
 * so before that it says so instead of rendering a plausible uuid. Same rule as every other
 * panel in this UI: an unmeasured field is an em dash, never a substitute.
 *
 * The analyst id is labelled "demo identity" on purpose -- it is not authenticated. See
 * ui/src/lib/analyst.ts for why that label matters.
 */

import * as React from "react"
import { Check, Copy, Fingerprint, Pencil } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { normaliseAnalystId } from "@/lib/analyst"

/** Enough of a uuid to be recognisable next to a trace listing, without eating the header. */
function shorten(value: string): string {
  return value.length <= 13 ? value : `${value.slice(0, 8)}…${value.slice(-4)}`
}

function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = React.useState(false)

  const copy = React.useCallback(() => {
    // `navigator.clipboard` is undefined on a non-secure origin, which includes the
    // http://127.0.0.1 the local demo runs on in some browsers. Failing silently would look
    // like a dead button, so the copied state is only set when the write resolves.
    void navigator.clipboard
      ?.writeText(value)
      .then(() => {
        setCopied(true)
        window.setTimeout(() => setCopied(false), 1200)
      })
      .catch(() => setCopied(false))
  }, [value])

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="size-5"
          onClick={copy}
          aria-label={`Copy ${label}`}
        >
          {copied ? (
            <Check aria-hidden className="size-3" />
          ) : (
            <Copy aria-hidden className="size-3" />
          )}
        </Button>
      </TooltipTrigger>
      <TooltipContent>Copy {label}</TooltipContent>
    </Tooltip>
  )
}

function Row({
  label,
  value,
  absent,
  hint,
}: {
  label: string
  value: string | null
  absent: string
  hint: string
}) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <span className="text-muted-foreground">{label}</span>
      {value ? (
        <span className="flex items-center gap-1">
          <span className="font-mono text-[0.6875rem] break-all">{value}</span>
        </span>
      ) : (
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="cursor-help text-muted-foreground italic">{absent}</span>
          </TooltipTrigger>
          <TooltipContent className="max-w-xs">{hint}</TooltipContent>
        </Tooltip>
      )}
    </div>
  )
}

export function SessionBar({
  analystId,
  onAnalystIdChange,
  batchSessionId,
  chatSessionId,
  chatSeatId,
  appliedAnalystId,
}: {
  analystId: string
  onAnalystIdChange: (value: string) => void
  /** From the batch run's `start` frame. Null until a run has begun. */
  batchSessionId: string | null
  /** From the chat seat's `chat_started` frame. Null until a turn has been sent. */
  chatSessionId: string | null
  /** Which seat that chat session belongs to, so switching seats is not confusing. */
  chatSeatId: string | null
  /**
   * The actor the BACKEND reported for the last turn, not the one we sent.
   *
   * These diverge for a real reason. The header only takes effect if the calling layer
   * actually injects it, and the deployed CloudFront proxy did not -- so every conversation
   * was attributed to the shared actor while this badge displayed an analyst id. The visible
   * symptom was Francis greeting the wrong person by name out of someone else's stored facts.
   * Rendering what the backend reported, rather than what we sent, is what makes that state
   * legible instead of misleading.
   */
  appliedAnalystId: string | null
}) {
  const [editing, setEditing] = React.useState(false)
  const [draft, setDraft] = React.useState(analystId)

  React.useEffect(() => setDraft(analystId), [analystId])

  const commit = React.useCallback(() => {
    const next = normaliseAnalystId(draft)
    // An empty box must not silently wipe the actor -- memory written under the old one
    // would become unreachable with no indication why.
    if (next) onAnalystIdChange(next)
    else setDraft(analystId)
    setEditing(false)
  }, [analystId, draft, onAnalystIdChange])

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge
          variant="outline"
          className="h-8 cursor-help gap-1.5 px-2.5 font-mono text-[0.6875rem]"
        >
          <Fingerprint aria-hidden className="size-3" />
          <span>{shorten(analystId)}</span>
          {chatSessionId || batchSessionId ? (
            <>
              <Separator orientation="vertical" className="h-3" />
              <span className="text-muted-foreground">
                {shorten(chatSessionId ?? batchSessionId ?? "")}
              </span>
            </>
          ) : null}
        </Badge>
      </TooltipTrigger>
      <TooltipContent className="w-[26rem] p-0" side="bottom" align="end">
        <div className="flex flex-col gap-2 p-3 text-xs">
          <div className="flex items-center justify-between gap-2">
            <span className="font-medium">Session identity</span>
            <span className="text-[0.625rem] text-muted-foreground uppercase">
              demo identity — not authenticated
            </span>
          </div>

          <Separator />

          <div className="flex items-baseline justify-between gap-4">
            <span className="text-muted-foreground">Analyst (memory actor)</span>
            {editing ? (
              <input
                autoFocus
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onBlur={commit}
                onKeyDown={(event) => {
                  if (event.key === "Enter") commit()
                  if (event.key === "Escape") {
                    setDraft(analystId)
                    setEditing(false)
                  }
                }}
                className="h-6 w-48 rounded border border-input bg-transparent px-1.5 font-mono text-[0.6875rem] outline-none focus-visible:border-ring"
                aria-label="Analyst id"
              />
            ) : (
              <span className="flex items-center gap-1">
                <span className="font-mono text-[0.6875rem] break-all">{analystId}</span>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-5"
                  onClick={() => setEditing(true)}
                  aria-label="Edit analyst id"
                >
                  <Pencil aria-hidden className="size-3" />
                </Button>
                <CopyButton value={analystId} label="analyst id" />
              </span>
            )}
          </div>

          <Row
            label={chatSeatId ? `Chat session (${chatSeatId})` : "Chat session"}
            value={chatSessionId}
            absent="no turn sent yet"
            hint="Set by the runtime on the first chat turn. Follow-up turns reuse it, which is what keeps context; switching seats starts a separate one."
          />

          <div className="flex items-baseline justify-between gap-4">
            <span className="text-muted-foreground">Memory scope</span>
            {chatSessionId === null ? (
              <span className="text-muted-foreground italic">not yet established</span>
            ) : appliedAnalystId ? (
              <span className="font-mono text-[0.6875rem]">
                /analysts/{appliedAnalystId}/facts
              </span>
            ) : (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="cursor-help text-[#8a5c00] dark:text-[#fac351]">
                    shared actor — NOT per-analyst
                  </span>
                </TooltipTrigger>
                <TooltipContent className="max-w-xs">
                  The backend did not report an actor for the last turn, so it was stored
                  against the shared default. Facts another analyst stated can come back to
                  you. The calling layer has to inject the custom user-id header for
                  per-analyst memory to take effect.
                </TooltipContent>
              </Tooltip>
            )}
          </div>

          <Row
            label="Batch session"
            value={batchSessionId}
            absent="no run yet"
            hint="Set when an adjudication run starts. The fan-out across the specialist runtimes joins under this id, which is how the nine runtimes appear as one trace."
          />

          {chatSessionId ? (
            <div className="flex justify-end">
              <CopyButton value={chatSessionId} label="chat session id" />
            </div>
          ) : null}

          <Separator />

          <p className="text-[0.6875rem] leading-snug text-muted-foreground">
            The analyst id is sent as a signed
            <span className="font-mono">
              {" "}
              X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Id{" "}
            </span>
            header and scopes long-term memory. The session id scopes the conversation and is
            what <span className="font-mono">agentcore traces list</span> prints beside each
            trace. Change the analyst id to see per-actor memory isolation; the same id in a
            second browser resumes the same recalled facts.
          </p>
        </div>
      </TooltipContent>
    </Tooltip>
  )
}
