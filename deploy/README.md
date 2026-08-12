# Deploying this into your own AWS account

> This is the path from nothing to two working URLs. For the full runbook -- naming
> constraints the schema enforces, what `agentcore.json` requires of `app/`, the
> memory configuration, CloudWatch Transaction Search setup, and the failure modes of
> every CLI verb -- see [`deploy.md`](deploy.md) in this directory.

Two things get deployed, and they are deliberately separate stacks:

| | What | Deployed by | Stack |
|---|---|---|---|
| **Agents** | 2 AgentCore runtimes (9 model calls) + 1 memory | `agentcore deploy` | `AgentCore-ppFraud-default` |
| **Websites** | 2 CloudFront endpoints + 1 streaming Lambda | `deploy/deploy-sites.sh` | `PpFraudSites` |

They are separate because their lifecycles are: prompt or model changes redeploy
agents, prose or UI changes redeploy websites, and neither should force the other.
The website stack reads the runtime ARN back from the agent deployment, so the
order below is not optional.

---

## Prerequisites

```bash
node --version        # >= 20
python3 --version     # 3.12.x  (3.14 will not work: fastapi/pydantic wheels)
aws sts get-caller-identity        # the account you intend to deploy into
npm install -g @aws/agentcore     # 1.0.0-preview.22
```

**Bedrock model access.** Enable these in the Bedrock console for your account,
or the agents deploy and then fail on their first call:

| Model | Used by | Path |
|---|---|---|
| `anthropic.claude-sonnet-5` | 6 specialists | Converse |
| `anthropic.claude-haiku-4-5` | dealer, straw | Converse |
| `openai.gpt-5.6-luna` | master synthesizer, Francis | bedrock-mantle |

**CDK bootstrap**, once per account/region:

```bash
npx cdk bootstrap aws://<account>/<region>
```

---

## Step 1 — the agents

```bash
# Point the deployment at your account. The target MUST be named "default":
# `agentcore deploy` with no --target looks for exactly that name.
cat > agentcore/aws-targets.json <<'EOF'
[{ "name": "default", "description": "...", "account": "<ACCOUNT>", "region": "us-west-2" }]
EOF

./deploy/vendor.sh                       # REQUIRED — see the warning below
(cd agentcore/cdk && npm install)
agentcore validate --json
agentcore deploy --target default --yes
```

> **`./deploy/vendor.sh` is not optional, and skipping it fails misleadingly.**
> `agentcore package` roots the zip at each runtime's `codeLocation`, so the shared
> packages one level up (`prompts/`, `signal_layer/`, `agents/`, `fixtures/`) are
> not included. The container then dies on
> `ModuleNotFoundError: No module named 'app'`, which the service reports as
> *"Runtime initialization time exceeded. Please make sure that initialization
> completes in 30s."* — a timeout message for a missing-module problem. Run
> `vendor.sh` before **every** deploy.

### Verify

`agentcore invoke` prints an empty response for this runtime — it renders text
deltas and our frames are JSON objects. Read the stream directly instead:

```bash
python3 - <<'EOF'
import boto3, json, uuid
c = boto3.client("bedrock-agentcore", region_name="us-west-2")
arn = "<UNDERWRITER_RUNTIME_ARN>"
r = c.invoke_agent_runtime(agentRuntimeArn=arn, runtimeSessionId=str(uuid.uuid4()),
    qualifier="DEFAULT", payload=json.dumps({"application_id": "APP-1004"}).encode())
raw = r["response"].read()
for line in raw.decode().splitlines():
    if line.startswith("data: "):
        f = json.loads(line[6:])
        print(f.get("event"), f.get("domain") or f.get("model") or "")
EOF
```

A healthy run shows `mock_mode: false` in the first frame, eight `agent_done`
frames, and a terminal `done` frame whose `key_check.valid` is `true`.

**If the first frame says `mock_mode: true`, nothing called Bedrock.**
`MOCK_MODE` defaults to `"1"`, so a runtime with no env block returns a complete,
plausible stream having made zero model calls. `agentcore/agentcore.json` sets
`MOCK_MODE=0` on both runtimes; check it survived.

---

## Step 2 — the websites

```bash
PYTHON=$(which python3) ./deploy/deploy-sites.sh
```

One command. It builds the docs static export, builds the demo UI, bakes the three
constant API responses, deploys the stack, and prints both URLs. First run takes
~15 minutes because CloudFront distributions are slow to create; subsequent runs
are a couple of minutes.

What you get:

```
docs : https://<id>.cloudfront.net     the engagement record, 40 pages
demo : https://<id>.cloudfront.net     the live demo
```

### Why the demo needs one Lambda

Only `/api/stream/*` does. The other three endpoints the UI calls are constant for
a given build, so they are baked into S3 objects at the exact paths the UI fetches
and served with no compute. A live run cannot be baked, and CloudFront cannot
reach the runtime by itself: Origin Access Control signs SigV4 for S3, Lambda
function URLs and VPC origins — not for `bedrock-agentcore` — and a browser holds
no credentials to sign with. So one Lambda, on one path, in `RESPONSE_STREAM`
mode. API Gateway is not an option here: its 29-second integration timeout is
shorter than a healthy run.

That Lambda also translates between the two SSE vocabularies in this repo. The
runtime emits `start / agent_start / agent_done / synthesis_start / done`; the UI
consumes `run_started / agent_started / agent_completed / synthesis_started /
synthesis_completed / run_completed`. A plain proxy yields a UI that connects,
streams, and displays nothing.

---

## Two fan-out topologies

The eight specialists can run in one container or in eight. Both are deployed; one
environment variable on the `underwriter` runtime chooses which executes.

| | `FANOUT_MODE=inprocess` | `FANOUT_MODE=distributed` |
|---|---|---|
| AgentCore invocations per application | 1 | 9 |
| Bedrock model calls | 9 | 9 |
| Concurrency primitive | `asyncio.gather` in one container | `asyncio.gather` over 9 `InvokeAgentRuntime` calls |
| Deploy / scale / IAM granularity | all eight together | each specialist on its own |
| Log groups and span trees | 1 | 9 |

**Parallelism is identical.** It comes from `asyncio`, not from process separation, so
splitting the specialists adds nine AgentCore invocations and nine cold-start surfaces
without adding concurrency. What it buys is operational: a specialist can be redeployed,
rate-limited, IAM-scoped, or rolled back without touching the other seven, and each gets
its own metrics and log group.

**The cost is measurable, not theoretical.** Every `agent_done` frame in distributed mode
carries `remote_elapsed_ms` (wall clock inside the specialist runtime) alongside
`latency_ms` (the orchestrator's view), and their difference is reported as
`invocation_overhead_ms`. `deploy/verify-tracing.py` prints the min/median/max across the
eight. Compare against the in-process baseline recorded in
`evals/results/deployed_runtime_e2e.json` before choosing.

`inprocess` is the code default, so a project whose specialist runtimes are not deployed
still works. `agentcore.json` sets `distributed` on the deployed orchestrator.

### One source tree, eight runtimes

The eight specialist runtimes all declare `codeLocation: app/specialist/` and differ only
in their name and `SPECIALIST_DOMAIN`. The schema permits a shared `codeLocation`
(verified with `agentcore validate`), so there is one file to maintain rather than eight
copies that drift. The CLI packages the directory once per runtime, so a deploy uploads
ten zips of about 42 MB each — expect roughly ten minutes.

The orchestrator finds its specialists through `SPECIALIST_ARN_<DOMAIN>` environment
variables that **CDK** writes from the sibling runtimes' own ARNs
(`agentcore/cdk/lib/cdk-stack.ts`). Those ARNs do not exist until deploy time and are
created by the same stack, so there is no point at which anyone could put them in a
config file by hand — and a renamed runtime updates the orchestrator automatically
instead of leaving a stale ARN that fails at request time.

A specialist that cannot be reached is reported as a **failed specialist**, not as a
crashed run: seven of eight still produces an adjudication, with the missing domain named
in `degraded_domains`.

---

## Observability

Native AgentCore Observability, no third-party collector. Three things make it work, and
two of them are easy to miss.

**1. CloudWatch Transaction Search must be on.** It is account-and-region wide, one time.
`agentcore deploy` enables it for you; verify with:

```bash
aws xray get-trace-segment-destination --region us-west-2
# {"Destination": "CloudWatchLogs", "Status": "ACTIVE"}
```

Without it, spans are not indexed and AgentCore cannot deliver them to an agent's own log
group.

**2. ADOT must be in the dependency closure.** Every runtime here sets
`instrumentation.enableOtel: true`, which makes AgentCore wrap the entrypoint with
`opentelemetry-instrument`. That wrapper cannot start without
`aws-opentelemetry-distro>=0.18.0`, which is why it is pinned in every
`app/*/pyproject.toml` and not only in the root `requirements.txt`. The `>=0.18.0` floor
is also what lets spans go to the agent's own log group instead of the shared
`aws/spans`.

**3. Trace context must be passed explicitly across runtimes.** This is the one that
does not happen by itself. Eight independent runtimes produce eight unrelated traces
unless the parent context travels with each request. `InvokeAgentRuntime` carries it as
request parameters:

| Parameter | Header | Purpose |
|---|---|---|
| `traceParent` | `traceparent` | W3C trace context — what joins the traces |
| `traceState` | `tracestate` | vendor state |
| `baggage` | `baggage` | user-defined context propagation |
| `traceId` | `X-Amzn-Trace-Id` | X-Ray format, `Root=1-...;Parent=...;Sampled=1` |
| `runtimeSessionId` | `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` | groups all nine into one session |

`app/distributed_fanout.py` injects the active span with the configured OTel propagator
rather than formatting a `traceparent` by hand, and reuses the orchestrator's own session
id for all eight children. A fresh session id per child would scatter one application
across nine sessions.

### Where the data lands

| | Location |
|---|---|
| Logs | `/aws/bedrock-agentcore/runtimes/<agent-id>-DEFAULT`, stream `runtime-logs` |
| Spans | the same log group, stream `spans` |
| Metrics | namespace `bedrock-agentcore` |
| Dashboard | CloudWatch → **GenAI Observability** |

Spans go to each agent's own log group, not the shared `aws/spans`, because us-west-2
defaults new agents to the unified destination. Flip it per agent with
`UNIFIED_TRACES_DESTINATION_ENABLED=false` if you would rather query one place; the
trade-off is per-agent access control and encryption scope against a single query target.

### Prove it

```bash
python3 deploy/verify-tracing.py
```

It starts its own trace, invokes the orchestrator as that trace's child, then asks X-Ray
whether the specialists reported under the **same trace id**. It also prints the
per-specialist AgentCore invocation overhead. A pass means one span tree rooted at the
orchestrator; a fail means nine disconnected traces, which looks like working tracing
until someone tries to follow a request.

---

## Manual path (no CDK)

If you would rather not run the website stack, everything works without it.

**The demo, locally, against real Bedrock:**

```bash
cd ui && npm install && npm run build && cd ..
MOCK_MODE=0 AWS_REGION=us-west-2 python3 app.py --serve 8080
# http://127.0.0.1:8080/
```

This path runs the fan-out in-process with your local credentials rather than
going through AgentCore Runtime. Same models, same prompts, same 21-key contract,
same measured cost — it just is not the deployed topology.

**The docs, locally:**

```bash
cd site && npm install && npm run dev -- --port 3002
# http://localhost:3002/docs
```

**The docs, as one PDF** (387 pages, with bookmarks):

```bash
cd site && node scripts/build-pdf.mjs --base http://localhost:3002
```

**The docs, as static files you can host anywhere:**

```bash
cd site && npm run build      # -> site/out/, pure static
```

`site/out` is a complete static site: 40 pages, OG images, and a client-side
search index. Drop it on any static host. Note that clean URLs need the host to
try `<path>.html` — the CDK stack does that with a CloudFront Function; S3 website
hosting does it natively; a plain S3+CloudFront origin does not.

---

## Costs

Measured, not estimated — one full run against the deployed runtime on
2026-08-11, recorded in `evals/results/deployed_runtime_e2e.json`:

| | |
|---|---|
| Per adjudication | **$0.154764** (8 specialists $0.152043 + synthesis $0.002721) |
| End to end | **41.86 s** |
| Fan-out speedup | **4.64×** (141.3 s of agent time in 30.4 s of wall clock) |

`n=1`, on one HIGH RISK fixture. It is an existence proof for the deployed path,
not a distribution — no percentile is published from it. Run
`python3 -m evals.bench --live` for that.

Idle cost of the two websites is an S3 GET plus CloudFront requests. The demo
costs the figure above every time someone presses Run.

---

## Tearing it down

```bash
(cd deploy/cdk && npx cdk destroy -c account=<ACCOUNT> -c runtimeArn=<ARN>)
aws cloudformation delete-stack --stack-name AgentCore-ppFraud-default --region us-west-2
```

Both site buckets have `autoDeleteObjects`, so they empty themselves. The
AgentCore memory holds Francis's conversation history and is deleted with its
stack.

> **Renaming a resource in `agentcore.json` changes its CloudFormation logical ID,
> which destroys and recreates it.** That is fine for a runtime and not fine for a
> memory, whose contents do not survive.
