/**
 * A seat on any one of the nine agents.
 *
 * The customer's own production UI works this way: the orchestrator is what most people
 * use, and behind it each specialist is a seat an analyst on that discipline can take.
 * This reproduces that, against the DEPLOYED runtimes -- Francis is `ppFraud_francis`
 * and each specialist is its own `ppFraud_<domain>`.
 *
 * TWO KINDS OF SEAT, AND THE DIFFERENCE IS NOT COSMETIC
 *
 * Francis is a conversation: she routes to at most two specialists, streams her answer
 * token by token, and holds session context.
 *
 * A specialist is not a chatbot, and the composer reflects that rather than papering over
 * it. `agents.fanout.fan_out_streaming` accepts NO question -- a specialist's entire input
 * is its projected domain view -- so a text box would invite a question, discard it, and
 * then fail on anything that did not happen to contain an application id. It really did:
 * typing anything conversational returned "no application id in this request". So a
 * specialist seat offers an APPLICATION PICKER and an Analyse button, and says on the face
 * of it that this agent takes no question and why.
 *
 * NOTHING IS SIMULATED. If a runtime is not deployed, the seat says so and refuses
 * rather than answering from somewhere else. An answer that cannot be traced to a
 * runtime would make the whole panel unfalsifiable.
 */

import * as React from "react";
import { Bot, CornerDownLeft, Loader2, User, Wrench } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Markdown } from "@/components/Markdown";
import { EM_DASH, integer, ms, usd } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { AgentSeat } from "@/lib/types";

/**
 * How many of a turn's tool calls count against the two-specialist cap.
 *
 * A specialist call is `call_<domain>_agent`; a Gateway tool is `<Target>___<tool>`. The two
 * are counted differently on purpose -- see the comment at the render site.
 */
function specialistToolCount(tools: string[]): number {
  return tools.filter((tool) => /^call_[a-z]+_agent$/.test(tool)).length;
}

interface Turn {
  role: "user" | "agent";
  text: string;
  agentId?: string;
  agentName?: string;
  tools?: string[];
  usage?: {
    model?: string | null;
    latency_ms?: number | null;
    input_tokens?: number | null;
    output_tokens?: number | null;
    cost_usd?: number | null;
    risk_band?: string | null;
  };
  error?: string;
  streaming?: boolean;
}

export function ChatPanel({
  seats,
  applications,
  analystId,
  onSessionChange,
}: {
  seats: AgentSeat[];
  /** Fixture ids, for the specialist seats. A specialist needs one; Francis does not. */
  applications: string[];
  /**
   * The memory actor. Sent as `analyst_id`, which `ui/chat.py` turns into a signed
   * `X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Id` header. Without it every conversation
   * pooled into the shared default actor and the actor-scoped memory that is deployed on
   * Francis was unreachable from this page.
   */
  analystId: string;
  /** Lets the header render the live session id; a ref inside this component cannot be. */
  onSessionChange: (
    session: {
      seatId: string;
      sessionId: string;
      appliedAnalystId?: string | null;
    } | null,
  ) => void;
}) {
  const [agentId, setAgentId] = React.useState<string>("francis");
  const [input, setInput] = React.useState("");
  const [application, setApplication] = React.useState<string>("");
  const [turns, setTurns] = React.useState<Turn[]>([]);
  const [busy, setBusy] = React.useState(false);
  // One session id per seat, so Francis keeps context across turns and switching seats
  // does not blend two agents' sessions.
  const sessions = React.useRef<Record<string, string>>({});
  const bottom = React.useRef<HTMLDivElement>(null);

  const seat = seats.find((s) => s.id === agentId);

  React.useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  // Default to the first fixture so a specialist seat is usable on arrival rather than
  // erroring on the first thing anyone types.
  React.useEffect(() => {
    if (!application && applications.length) setApplication(applications[0]);
  }, [application, applications]);

  const send = React.useCallback(async () => {
    if (busy || !seat) return;
    const conversational = seat.conversational;
    const message = input.trim();
    // A specialist takes an application and no question; Francis takes a question.
    if (conversational ? !message : !application) return;

    // What the user SEES as their turn. For a specialist that is the request they made
    // -- "analyse APP-1004 for identity" -- not an empty bubble, and not a question that
    // was going to be discarded.
    const shown = conversational
      ? message
      : `Analyse ${application} — ${seat.id}`;

    setInput("");
    setBusy(true);
    setTurns((prior) => [
      ...prior,
      { role: "user", text: shown },
      {
        role: "agent",
        text: "",
        agentId: seat.id,
        agentName: seat.agent_name,
        streaming: true,
      },
    ]);

    const update = (patch: Partial<Turn>) =>
      setTurns((prior) => {
        const next = [...prior];
        next[next.length - 1] = { ...next[next.length - 1], ...patch };
        return next;
      });

    try {
      const params = new URLSearchParams();
      if (conversational) params.set("message", message);
      else params.set("application_id", application);
      const existing = sessions.current[seat.id];
      if (existing) params.set("session_id", existing);
      if (analystId) params.set("analyst_id", analystId);

      const response = await fetch(`/api/chat/${seat.id}?${params}`, {
        headers: { Accept: "text/event-stream" },
      });
      if (!response.ok || !response.body) {
        throw new Error(`HTTP ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let text = "";
      const tools: string[] = [];

      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let split: number;
        // Hold the tail: an SSE frame split across two chunks must not be parsed twice.
        while ((split = buffer.indexOf("\n\n")) !== -1) {
          const raw = buffer.slice(0, split);
          buffer = buffer.slice(split + 2);
          const line = raw.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          let frame: Record<string, unknown>;
          try {
            frame = JSON.parse(line.slice(6));
          } catch {
            continue;
          }
          switch (frame.event) {
            case "chat_started":
              sessions.current[seat.id] = String(frame.session_id);
              // Published so the header shows the id the runtime actually assigned, not one
              // the client guessed. On a first turn the server mints it, so this is the only
              // place the real value is known.
              onSessionChange({
                seatId: seat.id,
                sessionId: String(frame.session_id),
                // What the BACKEND says the memory actor is, not what we sent. Null means the
                // turn was attributed to the shared actor, and the header renders that
                // difference rather than hiding it.
                appliedAnalystId: frame.analyst_id
                  ? String(frame.analyst_id)
                  : null,
              });
              break;
            case "delta":
              text += String(frame.text ?? "");
              update({ text });
              break;
            case "tool":
              if (frame.phase === "tool_start" && frame.tool) {
                tools.push(String(frame.tool));
                update({ tools: [...tools] });
              }
              break;
            case "chat_completed":
              update({
                text: String(frame.answer ?? text),
                tools: [...tools],
                streaming: false,
                usage: {
                  model: (frame.model as string) ?? null,
                  latency_ms: (frame.latency_ms as number) ?? null,
                  input_tokens: (frame.input_tokens as number) ?? null,
                  output_tokens: (frame.output_tokens as number) ?? null,
                  cost_usd: (frame.cost_usd as number) ?? null,
                  risk_band: (frame.risk_band as string) ?? null,
                },
              });
              break;
            case "error":
              update({ error: String(frame.error), streaming: false });
              break;
          }
        }
      }
    } catch (error) {
      update({
        error: error instanceof Error ? error.message : String(error),
        streaming: false,
      });
    } finally {
      setBusy(false);
    }
  }, [analystId, application, busy, input, onSessionChange, seat]);

  // Each seat keeps its own session, so switching seats must switch what the header shows --
  // otherwise it would keep displaying the previous seat's id and the operator would copy the
  // wrong one into `agentcore traces get`.
  React.useEffect(() => {
    if (!seat) return;
    const existing = sessions.current[seat.id];
    onSessionChange(existing ? { seatId: seat.id, sessionId: existing } : null);
  }, [onSessionChange, seat]);

  return (
    <Card className="flex h-[46rem] flex-col">
      <CardHeader className="gap-2">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <CardTitle>Chat with an agent</CardTitle>
            <CardDescription>
              Every reply comes from a deployed AgentCore runtime. Nothing on
              this tab is simulated; a seat whose runtime is not deployed
              refuses rather than answering.
            </CardDescription>
          </div>
          <div className="flex flex-col gap-1.5">
            <span className="text-[0.6875rem] tracking-wide text-muted-foreground uppercase">
              Seat
            </span>
            <Select value={agentId} onValueChange={setAgentId}>
              <SelectTrigger className="w-72 font-mono text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {seats.map((s) => (
                  <SelectItem
                    key={s.id}
                    value={s.id}
                    className="font-mono text-xs"
                  >
                    {s.label}
                    {s.runtime ? "" : "  (not deployed)"}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {seat ? (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Badge
              variant={seat.conversational ? "default" : "secondary"}
              className="font-mono text-[0.625rem]"
            >
              {seat.conversational ? "conversation" : "single-domain analysis"}
            </Badge>
            {seat.runtime ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Badge
                    variant="outline"
                    className="cursor-help font-mono text-[0.625rem]"
                  >
                    {seat.runtime.split("/").pop()}
                  </Badge>
                </TooltipTrigger>
                <TooltipContent className="max-w-sm font-mono text-[0.6875rem] break-all">
                  {seat.runtime}
                </TooltipContent>
              </Tooltip>
            ) : (
              <Badge
                variant="destructive"
                className="font-mono text-[0.625rem]"
              >
                runtime not deployed
              </Badge>
            )}
            <span className="text-muted-foreground">{seat.description}</span>
          </div>
        ) : null}
      </CardHeader>

      <Separator />

      <CardContent className="flex min-h-0 flex-1 flex-col gap-3 pt-4">
        <ScrollArea className="min-h-0 flex-1 pr-3">
          {turns.length === 0 ? (
            <div className="flex flex-col gap-2 py-10 text-center text-sm text-muted-foreground">
              <p>
                {seat?.conversational
                  ? "Ask anything about an application, a lender, or a trend."
                  : "Pick an application below and this specialist analyses its own domain."}
              </p>
              <p className="font-mono text-xs">
                {seat?.conversational
                  ? '"Is there income fraud on APP-1003?"'
                  : `${seat?.agent_name ?? ""} reads only ${seat?.id ?? ""} signals`}
              </p>
            </div>
          ) : null}

          <div className="flex flex-col gap-4">
            {turns.map((turn, index) => (
              <div key={index} className="flex gap-2.5">
                <div
                  className={cn(
                    "mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full",
                    turn.role === "user" ? "bg-muted" : "bg-primary/10",
                  )}
                >
                  {turn.role === "user" ? (
                    <User className="size-3.5" aria-hidden />
                  ) : (
                    <Bot className="size-3.5" aria-hidden />
                  )}
                </div>
                <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                  <span className="font-mono text-[0.6875rem] text-muted-foreground">
                    {turn.role === "user" ? "You" : turn.agentName}
                  </span>

                  {turn.tools?.length ? (
                    <div className="flex flex-wrap items-center gap-1.5">
                      {turn.tools.map((tool) => (
                        <Badge
                          key={tool}
                          variant="secondary"
                          className="font-mono text-[0.625rem]"
                        >
                          <Wrench className="mr-1 size-2.5" aria-hidden />
                          {tool}
                        </Badge>
                      ))}
                      {/* ONLY the specialist calls count. `ToolCapHook` counts only the eight
                          `call_<domain>_agent` tools, deliberately: the customer's "MAXIMUM 2
                          tools per query" is a rule about specialist FAN-OUT, written for
                          latency. A Gateway lookup is not fan-out, and capping it would make
                          Francis choose between answering "what is alert 164" and consulting a
                          specialist.

                          This counted every tool, so a legitimate turn using one specialist plus
                          two Gateway tools rendered "3 of max 2 specialists" -- a contract
                          violation on screen, from a turn that violated nothing. */}
                      <span className="text-[0.6875rem] text-muted-foreground">
                        {specialistToolCount(turn.tools)} of max 2 specialists
                        {turn.tools.length > specialistToolCount(turn.tools)
                          ? ` · ${turn.tools.length - specialistToolCount(turn.tools)} gateway (uncapped)`
                          : ""}
                      </span>
                    </div>
                  ) : null}

                  {turn.error ? (
                    <p className="rounded-md border border-[#d03b3b]/40 bg-[#d03b3b]/5 px-2.5 py-2 text-xs">
                      {turn.error}
                    </p>
                  ) : turn.role === "agent" ? (
                    <div className="text-sm leading-relaxed">
                      {turn.text ? (
                        <Markdown>{turn.text}</Markdown>
                      ) : turn.streaming ? (
                        <span className="flex items-center gap-2 text-xs text-muted-foreground">
                          <Loader2
                            className="size-3 animate-spin"
                            aria-hidden
                          />
                          working
                        </span>
                      ) : null}
                    </div>
                  ) : (
                    <p className="text-sm leading-relaxed">{turn.text}</p>
                  )}

                  {/* Measured, per turn. A chat that hides its cost is the one place a
                      demo quietly stops being comparable to the batch path. */}
                  {turn.usage && !turn.streaming ? (
                    <div className="flex flex-wrap gap-x-3 gap-y-1 font-mono text-[0.625rem] text-muted-foreground">
                      {turn.usage.risk_band ? (
                        <span>{turn.usage.risk_band}</span>
                      ) : null}
                      <span>{turn.usage.model ?? EM_DASH}</span>
                      <span>{ms(turn.usage.latency_ms ?? null)}</span>
                      <span>
                        in {integer(turn.usage.input_tokens ?? null)} / out{" "}
                        {integer(turn.usage.output_tokens ?? null)}
                      </span>
                      <span>{usd(turn.usage.cost_usd ?? null)}</span>
                    </div>
                  ) : null}
                </div>
              </div>
            ))}
            <div ref={bottom} />
          </div>
        </ScrollArea>

        {/* Two composers, because the two seat kinds take different inputs. A specialist
            accepts NO question -- fan_out_streaming has no parameter for one -- so offering
            a text box would invite a question, discard it, and then fail on anything that
            did not happen to contain an application id. */}
        {seat?.conversational ? (
          <div className="flex items-end gap-2">
            <textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void send();
                }
              }}
              rows={2}
              placeholder="Ask Francis about an application, a lender, or a trend..."
              disabled={busy || !seat?.runtime}
              className="min-h-[3.25rem] flex-1 resize-none rounded-lg border border-input bg-transparent px-3 py-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
            />
            <Button
              onClick={() => void send()}
              disabled={busy || !input.trim() || !seat?.runtime}
            >
              {busy ? (
                <Loader2 className="animate-spin" aria-hidden />
              ) : (
                <CornerDownLeft aria-hidden />
              )}
              {busy ? "Working" : "Send"}
            </Button>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <div className="flex items-end gap-2">
              <div className="flex flex-col gap-1.5">
                <span className="text-[0.6875rem] tracking-wide text-muted-foreground uppercase">
                  Application
                </span>
                <Select value={application} onValueChange={setApplication}>
                  <SelectTrigger className="w-44 font-mono text-xs">
                    <SelectValue placeholder="Select" />
                  </SelectTrigger>
                  <SelectContent>
                    {applications.map((id) => (
                      <SelectItem
                        key={id}
                        value={id}
                        className="font-mono text-xs"
                      >
                        {id}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Button
                onClick={() => void send()}
                disabled={busy || !application || !seat?.runtime}
                className="h-9"
              >
                {busy ? (
                  <Loader2 className="animate-spin" aria-hidden />
                ) : (
                  <CornerDownLeft aria-hidden />
                )}
                {busy
                  ? "Analysing"
                  : `Analyse with ${seat?.agent_name ?? "agent"}`}
              </Button>
            </div>
            <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
              This agent takes no question. Its input is its own projected
              signals for the application you pick — which is what keeps eight
              specialists from re-issuing the same query. Use the Francis seat
              to ask something in words.
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
