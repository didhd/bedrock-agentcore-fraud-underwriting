# Demo interface

Customer-facing UI for the AgentCore replacement: Vite + React + TypeScript +
Tailwind v4 + shadcn/ui, served by a FastAPI process that streams one adjudication
run over Server-Sent Events.

## Quick start

```bash
cd ui
npm install
npm run build            # tsc -b && vite build  -> ui/dist
MOCK_MODE=1 python server.py   # http://127.0.0.1:8080
```

`MOCK_MODE=1` is the default and needs **zero AWS**: the eight domains still run
concurrently and the wall clock is still measured, but no Bedrock call is made.

To run against Bedrock for real (this is the only mode in which token usage and
cost exist):

```bash
cd ui
MOCK_MODE=0 python server.py
```

### Front-end development against a live API

```bash
# terminal 1
cd ui && MOCK_MODE=1 python server.py
# terminal 2
cd ui && npm run dev          # http://127.0.0.1:5173, /api proxied to :8080
```

Point the dev proxy elsewhere with `DEMO_API=http://host:port npm run dev`.

## npm scripts

| Script | What it does |
|---|---|
| `npm run dev` | Vite dev server on :5173, `/api` proxied to the FastAPI process |
| `npm run build` | `tsc -b && vite build` -> `ui/dist` |
| `npm run preview` | Serve the built bundle with Vite (no API) |
| `npm run typecheck` | `tsc -b` only |
| `npm run lint` | oxlint |
| `npm run demo:serve` | `python server.py` (expects `dist/` to exist) |
| `npm run demo` | build, then serve |

## The one rule this UI is built around

**No number is displayed unless this run measured it.**

Every measured field in the wire contract is `number | null`, and `null` renders as
an em dash (`—`). There is no `?? 0`, no nominal-latency table, no illustrative
token count, and no platform-comparison chart. Before a run, every measured field
on the page is an em dash; in `MOCK_MODE` the token and cost columns stay em dashes
after the run too, and the panels say why.

The single figure sourced from outside this process is the customer's own reported
"4,500 applications in more than 48 hours", which appears once, quoted and
attributed to them, and is never combined arithmetically with anything we measured.

## What the page shows

1. **Applications** — the fixture catalog. Anti-false-positive scenarios sort first
   and carry a marker, because "reduce false positives" is the customer's stated
   quality goal. Fraud- and dealer-score bands come from `signal_layer.bands`, not
   from the browser.
2. **Parallel fan-out** — eight cards under the customer's own persona names
   (ISAAC_IDENTITY, DIANA_DEALER, SAMMY_STRAW, EMILY_EMPLOYMENT, IRIS_INCOME,
   SYLVIA_SYNTHETIC, BRUCE_BUSTOUT, RENEE_RINGS), each streaming state, model,
   tier, measured latency, real token usage, real cost and risk band. All eight
   start together and finish at different times; the summary tiles report the
   slowest agent, the fan-out wall time, the synthesis time and the end-to-end
   total, plus the sum of the eight measured latencies for comparison.
3. **Measured latency by agent** — one bar chart, one series, one hue. Risk is
   *not* encoded as bar colour. A table view twin is always one click away.
4. **Specialist analyses** — titled prose in an accordion, with the exact title
   where the prompt defines one and **no title for bustout/rings** (their prompts
   say "Return only the final analysis." / "Return only final analysis."). Each
   entry reports its measured length against the prompt's budget (LOW = 3-5
   sentences, POSSIBLE/HIGH <= 4000 chars, straw with no co-borrower 2-3 sentences).
   Violations are reported, never clamped.
5. **Master adjudication** — the 21 keys as a decision panel, a per-dimension
   summary/flag table, a raw-JSON tab with a copy button, a "21 keys,
   contract-validated" indicator driven by a server-side order check, and the
   PDF-vs-prompt-file enum conflict as a discreet informational alert.
6. **Signal layer** — per domain: the signal count, the paired `*_context` fields,
   the alert IDs fired with categories and finding text, and the guardrail rule IDs.
7. **Cost** — per-agent cost from measured tokens at the rate actually applied, with
   a user-set volume projection labelled as arithmetic on the measured per-app cost.

## Backends

Selectable at runtime from the toolbar.

- **Local demo server** — `ui/server.py`.
- **AgentCore Runtime** — paste the runtime invocation URL and a bearer token. The
  token lives in component state only and is never written to storage.

Both always reply `text/event-stream`, so there is one parser
(`src/lib/sse.ts`). Two consequences are handled explicitly:

- A mid-stream failure arrives as a terminal `data: {"error": ...}` frame **after**
  HTTP 200 has been committed, so a 200 never means success. Every frame is checked
  for an `error` key and any error is raised into a banner and a toast — never
  swallowed.
- `runtimeSessionId` must be at least 33 characters. `crypto.randomUUID()` returns
  36; `uuid4().hex` would be 32 and is not used.

## HTTP API

| Endpoint | Returns |
|---|---|
| `GET /api/health` | capability report: which modules are actually wired, the customer's output contracts, the enum conflict |
| `GET /api/applications` | fixture catalog with banded scores |
| `GET /api/payload/{id}` | the signal-layer view, per domain |
| `GET /api/stream/{id}` | SSE: `run_started`, `agent_started` x8, `agent_completed` x8, `synthesis_started`, `synthesis_completed`, `run_completed` |

`/api/health` is rendered in the UI as a capability banner. While sibling modules
(`fixtures/`, `signal_layer.registry`, `agents.fanout`, `agents.synthesize`) are
still landing from other workstreams, the server reports what it could not import
and the affected panels say "not computed" instead of showing a substitute number.

## Theme and charts

Dark by default (this gets projected in a meeting room), light and system available
from the toolbar, persisted to `localStorage`. Standard shadcn CSS variables with a
`.dark` class on `<html>`.

The chart palette follows the `dataviz` skill: a single categorical hue
(`#2a78d6` light / `#3987e5` dark) validated with that skill's
`validate_palette.js` against this app's real card surfaces (`#ffffff` /
`#171717`) — all six checks pass. Status colours are the skill's reserved status
tokens and always ship with an icon **and** a text label, so hue never carries
meaning alone. Status text colours were separately checked for WCAG AA against both
surfaces (lowest measured 5.39:1).

No emoji anywhere: the customer's own prompts forbid emoji output, so the surface
stays consistent with them.

## Layout

```
ui/
  server.py                     FastAPI: static assets + 4 endpoints
  index.html                    Vite entry (dark class pre-applied)
  components.json               shadcn config (radix-nova, css variables)
  src/
    App.tsx                     page composition
    lib/types.ts                the wire contract; measured fields are `| null`
    lib/sse.ts                  SSE client for both backends
    lib/format.ts               formatters; `dash()` is the honesty rule
    lib/utils.ts                shadcn cn()
    hooks/useRun.ts             frame reducer + wall clock
    components/ui/              vendored shadcn primitives (Radix + CVA)
    components/*.tsx            the seven panels
```
