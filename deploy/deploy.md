# Deploying this into your own AWS account

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
