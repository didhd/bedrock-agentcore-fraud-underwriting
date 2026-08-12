/**
 * The demo shell.
 *
 * Reading order is the pitch's order: pick an application, watch eight specialists
 * run in parallel, read what they returned, read the adjudication, then inspect the
 * governance and the cost behind it.
 *
 * Errors are never swallowed. A terminal error frame on the SSE channel (which
 * AgentCore emits AFTER HTTP 200) lands in state.errors and is rendered at the top
 * of the page as well as raised as a toast.
 */

import * as React from "react";
import { AlertTriangle, Info } from "lucide-react";
import { toast } from "sonner";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ThemeProvider, ThemeToggle } from "@/components/theme-provider";
import { AdjudicationPanel } from "@/components/AdjudicationPanel";
import { AnalysesPanel } from "@/components/AnalysesPanel";
import { ApplicationSelector } from "@/components/ApplicationSelector";
import { AgentsPanel } from "@/components/AgentsPanel";
import { BackendBar } from "@/components/BackendBar";
import { ChatPanel } from "@/components/ChatPanel";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CostPanel } from "@/components/CostPanel";
import { FanoutPanel } from "@/components/FanoutPanel";
import { LatencyChart } from "@/components/LatencyChart";
import { SignalLayerPanel } from "@/components/SignalLayerPanel";
import { useRun, useWallClock } from "@/hooks/useRun";
import type { BackendConfig } from "@/lib/sse";
import type { ApplicationSummary, Health, PayloadResponse, AgentsResponse } from "@/lib/types";

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${url} returned HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

function Header({ health }: { health: Health | null }) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-4 border-b border-border pb-5">
      <div className="flex flex-col gap-1.5">
        <h1 className="text-xl font-semibold tracking-tight">
          Multi-agent fraud underwriting on Amazon Bedrock AgentCore
        </h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Eight specialist fraud agents and one master synthesizer, running the
          customer's own production prompts byte-for-byte. Each specialist is
          dispatched in parallel on its own right-sized model; the synthesizer
          returns the {health?.adjudication_keys.length ?? 21}-key adjudication
          under its existing contract.
        </p>
      </div>
      <div className="flex items-center gap-2">
        {health ? (
          <Badge variant="outline" className="font-mono text-[0.6875rem]">
            {health.mock_mode ? "MOCK_MODE" : "live"}
          </Badge>
        ) : null}
        <ThemeToggle />
      </div>
    </header>
  );
}

function CapabilityNotice({ health }: { health: Health | null }) {
  if (!health || health.notes.length === 0) return null;
  return (
    <Alert>
      <Info aria-hidden />
      <AlertTitle>Backend capability report</AlertTitle>
      <AlertDescription>
        <div className="flex flex-col gap-1">
          <span className="text-xs">
            This process reported the following about what is actually wired.
            Panels whose source module has not landed say so rather than showing
            a substitute number.
          </span>
          <ul className="ml-4 list-disc text-xs">
            {health.notes.map((note) => (
              <li key={note} className="font-mono">
                {note}
              </li>
            ))}
          </ul>
        </div>
      </AlertDescription>
    </Alert>
  );
}

function Errors({ errors }: { errors: string[] }) {
  if (errors.length === 0) return null;
  return (
    <Alert variant="destructive">
      <AlertTriangle aria-hidden />
      <AlertTitle>
        {errors.length === 1
          ? "The run reported an error"
          : `The run reported ${errors.length} errors`}
      </AlertTitle>
      <AlertDescription>
        <ul className="ml-4 list-disc">
          {errors.map((error, index) => (
            <li key={`${error}-${index}`} className="font-mono text-xs">
              {error}
            </li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  );
}

function Demo() {
  const { agents, agentsError } = useAgents()
  const [health, setHealth] = React.useState<Health | null>(null);
  const [applications, setApplications] = React.useState<ApplicationSummary[]>(
    [],
  );
  const [loadingApps, setLoadingApps] = React.useState(true);
  const [selected, setSelected] = React.useState<string | null>(null);
  const [payload, setPayload] = React.useState<PayloadResponse | null>(null);
  const [loadingPayload, setLoadingPayload] = React.useState(false);
  const [backend, setBackend] = React.useState<BackendConfig>({
    kind: "local",
  });

  const { state, cards, start, reset } = useRun(health);
  const elapsedMs = useWallClock(state);

  React.useEffect(() => {
    getJson<Health>("/api/health")
      .then(setHealth)
      .catch((error: Error) =>
        toast.error(`Could not load /api/health: ${error.message}`),
      );
  }, []);

  React.useEffect(() => {
    getJson<ApplicationSummary[]>("/api/applications")
      .then((rows) => {
        setApplications(rows);
        setSelected((current) => current ?? rows[0]?.application_id ?? null);
      })
      .catch((error: Error) =>
        toast.error(`Could not load /api/applications: ${error.message}`),
      )
      .finally(() => setLoadingApps(false));
  }, []);

  React.useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setLoadingPayload(true);
    getJson<PayloadResponse>(`/api/payload/${encodeURIComponent(selected)}`)
      .then((data) => {
        if (!cancelled) setPayload(data);
      })
      .catch((error: Error) => {
        if (!cancelled) {
          setPayload(null);
          toast.error(`Could not load the signal layer: ${error.message}`);
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingPayload(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selected]);

  // Surface every error frame as a toast exactly once, in addition to the banner.
  const seenErrors = React.useRef(0);
  React.useEffect(() => {
    if (state.errors.length > seenErrors.current) {
      state.errors
        .slice(seenErrors.current)
        .forEach((error) => toast.error(error));
      seenErrors.current = state.errors.length;
    }
    if (state.errors.length === 0) seenErrors.current = 0;
  }, [state.errors]);

  const running = state.status === "running";

  const run = React.useCallback(
    (applicationId: string) => {
      if (backend.kind === "agentcore" && !backend.endpoint) {
        toast.error(
          "Enter the AgentCore Runtime invocation URL before running.",
        );
        return;
      }
      void start(applicationId, backend);
    },
    [backend, start],
  );

  const seats = agents?.agents ?? [];

  return (
    <div className="mx-auto flex max-w-[92rem] flex-col gap-5 px-5 py-6">
      <Header health={health} />

      {/* Three surfaces, and the order is the customer's own: the batch pipeline is the
          product, the seats are how their analysts use it, and the configuration is the
          fidelity proof. */}
      <Tabs defaultValue="batch" className="gap-5">
        <TabsList>
          <TabsTrigger value="batch">Batch underwriting</TabsTrigger>
          <TabsTrigger value="chat">
            Chat
            {seats.length ? (
              <span className="ml-1.5 font-mono text-[0.625rem] text-muted-foreground">
                {seats.length}
              </span>
            ) : null}
          </TabsTrigger>
          <TabsTrigger value="agents">Agents &amp; config</TabsTrigger>
        </TabsList>

        <TabsContent value="chat" className="flex flex-col gap-5">
          {agentsError ? (
            <Errors
              errors={[`Could not load the agent roster: ${agentsError}`]}
            />
          ) : null}
          <ChatPanel seats={seats} />
        </TabsContent>

        <TabsContent value="agents" className="flex flex-col gap-5">
          {agentsError ? (
            <Errors
              errors={[`Could not load the agent roster: ${agentsError}`]}
            />
          ) : null}
          <AgentsPanel
            seats={seats}
            promptIncluded={agents?.prompt_text_included ?? false}
            signalSource={agents?.signal_source ?? null}
          />
        </TabsContent>

        <TabsContent value="batch" className="flex flex-col gap-5">
          <BackendBar
            backend={backend}
            onBackendChange={setBackend}
            applications={applications.map(
              (application) => application.application_id,
            )}
            selected={selected}
            onSelect={setSelected}
            onRun={() => selected && run(selected)}
            onReset={reset}
            running={running}
            health={health}
          />

          <Errors errors={state.errors} />
          <CapabilityNotice health={health} />

          <ApplicationSelector
            applications={applications}
            loading={loadingApps}
            selected={selected}
            onSelect={setSelected}
            onRun={run}
            running={running}
          />

          <Separator />

          <FanoutPanel state={state} cards={cards} elapsedMs={elapsedMs} />

          <div className="grid gap-5 xl:grid-cols-2">
            <LatencyChart cards={cards} />
            <AnalysesPanel cards={cards} health={health} />
          </div>

          <AdjudicationPanel synthesis={state.synthesis} health={health} />

          <div className="grid gap-5 xl:grid-cols-2">
            <SignalLayerPanel
              payload={payload}
              loading={loadingPayload}
              applicationId={selected}
            />
            <CostPanel
              cards={cards}
              synthesis={state.synthesis}
              measuredTokens={
                state.measuredTokens ?? health?.measures_tokens ?? null
              }
            />
          </div>
        </TabsContent>
      </Tabs>

      <footer className="border-t border-border pt-4 pb-2 text-xs text-muted-foreground">
        <p>
          Every measured value on this page came from this run. A field that was
          not measured shows an em dash rather than a placeholder.
          {state.executionPath ? (
            <>
              {" "}
              Execution path:{" "}
              <span className="font-mono">{state.executionPath}</span>.
            </>
          ) : null}
          {state.sessionId ? (
            <>
              {" "}
              Session: <span className="font-mono">{state.sessionId}</span>.
            </>
          ) : null}
        </p>
      </footer>
    </div>
  );
}

/**
 * The nine seats and their configuration. Fetched once: it is static for a given build,
 * so re-requesting it on every tab switch would be noise on the network panel during a
 * demo.
 */
function useAgents() {
  const [data, setData] = React.useState<AgentsResponse | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    fetch("/api/agents")
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((json: AgentsResponse) => !cancelled && setData(json))
      .catch((exc: unknown) => {
        if (!cancelled)
          setError(exc instanceof Error ? exc.message : String(exc));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { agents: data, agentsError: error };
}

export default function App() {;
  return (
    <ThemeProvider>
      <TooltipProvider>
        <Demo />
        <Toaster position="bottom-right" />
      </TooltipProvider>
    </ThemeProvider>
  );
}
