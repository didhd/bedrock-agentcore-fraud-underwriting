# Architecture

Every box below exists in AWS today, in `us-west-2`, account `269550163595`. Where something
is designed but not deployed it says so.

## The whole system

```
                                  BROWSER
                                     │
                    ┌────────────────┴────────────────┐
                    │                                 │
        https://dt1seuxrjcujz          https://deta5vmdgigii
           .cloudfront.net                .cloudfront.net
              (docs site)                     (demo)
                    │                                 │
              ┌─────┴─────┐              ┌────────────┴────────────┐
              │  S3 + OAC │              │  S3 + OAC   │  /api/*   │
              │ 40 pages  │              │  prerender  │  behaviour│
              └───────────┘              └─────────────┴─────┬─────┘
                                                             │
                                    ┌────────────────────────┴─────────────────────┐
                                    │ baked objects              live paths        │
                                    │ /api/health                /api/stream/*     │
                                    │ /api/agents                /api/chat/*       │
                                    │ /api/applications                 │          │
                                    └───────────────────────────────────┼──────────┘
                                                                        │
                                                      Lambda function URL (AWS_IAM + OAC)
                                                          StreamProxy, RESPONSE_STREAM
                                                        ┌───────────────┴───────────────┐
                                                        │ VPC-ATTACHED (private subnets)│
                                                        │ translates SSE vocabularies   │
                                                        │ injects the memory actor hdr  │
                                                        └───────────────┬───────────────┘
                                                                        │ SigV4
                                                                        │ InvokeAgentRuntime
    ═══════════════════════════════════════════════════════════════════ │ ═══════════════
                                                                        ▼
                                       AGENTCORE RUNTIME  ·  10 runtimes  ·  networkMode PUBLIC

                    ┌──────────────────────────────┐        ┌───────────────────────────┐
                    │ ppFraud_underwriter          │        │ ppFraud_francis           │
                    │ batch adjudication (SSE)     │        │ interactive router        │
                    │ asyncio.gather over 8        │        │ 8 agents-as-tools, max 2  │
                    └──────────────┬───────────────┘        └─────────┬─────────────────┘
                                   │                                  │
              ┌────────────────────┴────────────────┐                 │
              │ FANOUT_MODE=inprocess │ =distributed│                 │
              │  8 Agents in-process  │  8 Invoke   │                 │
              └───────────────────────┴──────┬──────┘                 │
                                             │                        │
   ┌─────────┬─────────┬─────────┬───────────┴──┬─────────┬─────────┬─────────┐
   ▼         ▼         ▼         ▼              ▼         ▼         ▼         ▼
identity   dealer    straw   employment      income  synthetic  bustout    rings
ISAAC     DIANA     SAMMY     EMILY           IRIS    SYLVIA     BRUCE     RENEE
Sonnet 5  Haiku 4.5 Haiku 4.5 Sonnet 5      Sonnet 5  Sonnet 5  Sonnet 5  Sonnet 5
   │         │         │         │              │         │         │         │
   └─────────┴─────────┴────┬────┴──────────────┴─────────┴─────────┴─────────┘
                            │ eight titled-prose analyses
                            ▼
                  MASTER SYNTHESIS  ·  GPT-5.6 Luna via bedrock-mantle
                  21 keys, the customer's order, structured_output_model
```

Each specialist is an independently invocable runtime, so a single discipline can be called on
its own — that is what the chat tab's nine seats are.

## Where the data comes from — one seam, three sources

```
                     app.runtime_support.build_signal_payload(application_id)
                                        │
                     SIGNAL_MODE ───────┼──────────────────┬──────────────────┐
                          │             │                  │                  │
                     "fixtures"     "aurora"           "cortex"          (anything else)
                          │             │                  │                  │
                          ▼             ▼                  ▼                  ▼
                14 committed      RDS Data API      Cortex Analyst        raises
                JSON fixtures     signal_layer/     PAT + 2 calls:        (never silently
                no network        sources/aurora    /message → SQL,        falls back)
                                       │            /statements → rows
                                       │
                                       ▼
                          Aurora PostgreSQL Serverless v2
                          ppfrauddata-clustereb0386a7
                          Data API ON · private subnets
                          application_signals  840 rows / 14 apps  ✔ live read
                          underwriting_report_output              ✔ live write
```

**Neither non-fixture mode falls back.** A misconfigured `aurora` raises rather than quietly
serving demo data — an adjudication computed from 14 fixtures while everyone believes it came
from the cluster is the one failure this port must never produce.

Then the split that the whole design rests on:

```
   SIGNALS                                     ANALYSIS
   precomputed, per domain                     eight specialists
   ────────────────────────                    ────────────────────
   signal_layer/registry.py    ─ project ─▶    each agent sees ONLY its
   ~75 signals + _context                      own domain's view
   29 General Rules                            and has NO data tool
   10 alert categories / 148 ids
                                               "Agents interpret;
   read BEFORE any model runs                    they never query."
```

Enforced by `tests/test_rds_connection.py` in two directions. A specialist with a SQL tool
could widen its own scope and would put a text-to-SQL round trip inside the sub-minute budget —
the exact cost this migration removes.

## Network: what is in the VPC and what is not

```
        ┌──────────────────────────── vpc-05d5e1cb0bfcf14a8 ────────────────────────────┐
        │                                                                               │
        │  PRIVATE_ISOLATED · 2 AZs · NO NAT · NO internet route                        │
        │  subnet-0a916a2e318168a39   subnet-0b913cc9ad8edf7a7                          │
        │                                                                               │
        │   ┌─────────────────┐   ┌──────────────────┐   ┌──────────────────────────┐   │
        │   │ StreamProxy     │   │ UnderwritingSql  │   │ Aurora PostgreSQL        │   │
        │   │ Lambda          │   │ Gateway Lambda   │   │ Serverless v2 0.5–4 ACU  │   │
        │   │ (CloudFront)    │   │ read-only SQL    │   │ Data API enabled         │   │
        │   └────────┬────────┘   └────────┬─────────┘   └────────────┬─────────────┘   │
        │            │                     │      sg-072327180fd8c1a1a│ :5432           │
        │            └──────────┬──────────┴──────────────────────────┘                 │
        │                       │                                                       │
        │      ┌────────────────┴──────────────── 12 VPC endpoints ────────────────┐    │
        │      │ bedrock-runtime          bedrock-agentcore                        │    │
        │      │ bedrock-mantle           bedrock-agentcore-control                │    │
        │      │ secretsmanager           bedrock-agentcore.gateway                │    │
        │      │ rds-data                 logs   sts   ecr.api   ecr.dkr           │    │
        │      │ + S3 gateway endpoint (free; ECR layers are read from S3)          │    │
        │      └───────────────────────────────────────────────────────────────────┘    │
        └───────────────────────────────────────────────────────────────────────────────┘

        OUTSIDE the VPC, on purpose:
        ┌───────────────────────────────────────────────────────────────────────────────┐
        │ the 10 AgentCore runtimes · networkMode PUBLIC                                │
        └───────────────────────────────────────────────────────────────────────────────┘
```

**Why the runtimes are PUBLIC and the Lambdas are not — measured, not chosen.**

The CLI supports `networkMode: VPC` with a `networkConfig` block, and all ten runtimes were
deployed that way and came up `READY`. They then failed at request time:

```
{"event":"error","error":"APITimeoutError: Request timed out."}
```

Francis synthesizes on GPT-5.6 Luna through `bedrock-mantle`, and a private-isolated subnet with
no NAT cannot reach it — the `bedrock-mantle` interface endpoint does not cover the
bearer-token path that `OpenAIResponsesModel` uses. Attaching compute to a VPC removes its
default internet access, and the failure surfaces as a runtime timeout rather than as a missing
route.

So: **the runtimes stay PUBLIC and only the Lambdas hold the network path.** That is enough,
because a Lambda is what needs to reach the private database, and invoking a runtime is a
public SigV4 API call either way. In-VPC runtimes remain available — `deploy/one-shot.sh` writes
the config — but they require either a NAT Gateway or a verified endpoint for every model the
agents call. Not verified, so not on.

## Memory, and how the actor gets there

```
   browser                  calling layer                    runtime
   ────────                 ─────────────                    ───────
   analyst id          ?analyst_id=...          X-Amzn-Bedrock-AgentCore-
   localStorage    ──▶  StreamProxy Lambda  ──▶ Runtime-Custom-User-Id     ──▶ Francis
   (demo identity,      middleware at             (signed: added at
    NOT auth)           step:'build'               step 'build', BEFORE
                        per-COMMAND, never          SigV4 signs it)
                        per-client                       │
                                                         ▼
                                        AgentCore Memory  ppFraud_francisMemory
                                        SEMANTIC       /analysts/{actorId}/facts
                                        SUMMARIZATION  /summaries/{actorId}/{sessionId}
                                        30-day expiry
```

Three things here are the result of a real failure:

- **`runtimeUserId` does not work.** It is a first-class parameter and the service consumes it
  before the entrypoint sees it. That is why the workshop invents a custom header, and why the
  runtime allowlists it.
- **The header must be added BEFORE signing.** Added after, the signed value differs from the
  sent value and the call returns 403 — measured, in the traceparent attempt.
- **Per-command, not per-client.** The Lambda client lives for the container's lifetime, so a
  client-level middleware sends the first analyst's identity on every later request in that
  container. One warm Lambda would pool every user into one memory.

Without the injection, everything lands on the shared default actor. The visible symptom was
Francis greeting the wrong person by name out of another analyst's stored facts. The UI now
renders the actor the **backend reported**, not the one it sent, and says "shared actor — NOT
per-analyst" when they differ.

## The Gateway, and the tools behind it

```
                             Francis (interactive agent only)
                                        │ MCP, streamable HTTP
                                        ▼
                      AgentCore Gateway  ppfraud-reference
                      ┌─────────────────┴──────────────────┐
                      ▼                                    ▼
              AlertDictionary                       UnderwritingSql
              ┌──────────────────┐                  ┌──────────────────────────┐
              │ describe_alert   │                  │ get_schema               │
              │ list_alert_      │                  │ query_underwriting_data  │
              │   categories     │                  │ list_use_cases           │
              │                  │                  │ run_use_case             │
              │ 148 alert ids,   │                  │                          │
              │ 10 categories    │                  │ SELECT/WITH only         │
              │ generated from   │                  │ one statement            │
              │ signal_layer     │                  │ table allowlist          │
              └──────────────────┘                  │ 200-row cap ×2           │
                                                    └──────────┬───────────────┘
                                                               │
                                        ┌──────────────────────┴──────────────────┐
                                        │ 10 use cases over 17 verified queries   │
                                        │ the customer's own SQL, loaded from the │
                                        │ gitignored semantic model, bundled into │
                                        │ the zip at build time — never committed │
                                        └─────────────────────────────────────────┘
```

`run_use_case` takes a **slug, not SQL**. The model picks the question; it cannot author or edit
the statement, so the joins and the test-record exclusions their engineer verified stay intact.
Substitution is pattern-located and refuses unless the anchor is unique — a half-rewritten query
runs and answers a different question, which looks like success.

The eight specialists receive none of this.

## Confidentiality boundary

```
   COMMITTED                              GITIGNORED  (delivered as a zip)
   ─────────                              ──────────
   prompts/loader.py                      prompts/verbatim/
   prompts/manifest.json                    9 agent prompts + 9 orchestration docs
     filename + sha256 only                 FRAUDBOT_SEMANTIC_VIEW.yaml   947 KB
                                            confidential_analyst_queries_082026.sql
   signal_layer/semantic/loader.py
   signal_layer/semantic/manifest.json    deploy/cdk/lambda-rds/use_cases.json
                                            generated at bundle time
   tools/usecases/catalog.py
     our slugs, our descriptions,         deploy/.vpc-config.json
     STRUCTURAL patterns only             deploy/.rds-connection.json
```

Code loads; bytes stay out. `tests/test_usecase_tools.py` greps every committed file in
`tools/usecases/` for identifiers taken from the real model, and asserts patterns contain no
literal alphabetic runs — a pasted lender name fails the build.

One known exception, flagged not fixed: `signal_layer/rules/r0105_lender_nicknames.py` is
committed and holds the customer's lender nickname table as literal values. See
`docs/semantic-model-reconciliation.md`.

## Deploy order, and why it is an order

```
  0  prerequisites          npm @aws/agentcore 1.0.0-preview.22, pip toolkit ABSENT
     │                      (the pip binary no-ops `configure`/`launch` and reports success)
     ▼
  1  deploy/one-shot.sh     VPC + 12 endpoints + Aurora        →  PpFraudData
     │                      3 modes: create / attach / wire
     ▼
  2  deploy/reference-tools.sh   Gateway Lambda ARN → agentcore.json
     │                          (the CLI reads that file BEFORE it synthesises)
     ▼
  3  deploy/vendor.sh        vendors prompts/ signal_layer/ agents/ fixtures/
     │                       skip it → "Runtime initialization exceeded 30s",
     │                       which is really ModuleNotFoundError
     ▼
     agentcore deploy        10 runtimes                        →  AgentCore-ppFraud-default
     │
     ▼
  4  deploy/verify.py        confirm live, not mock
     │                       MOCK_MODE defaults to "1": a runtime with no env block
     │                       returns a complete plausible stream, Bedrock calls = 0
     ▼
  5  deploy/connect-rds.sh   Gateway SQL Lambda + target        →  PpFraudRdsTools
     │  deploy/seed-rds.py   840 signal rows (synthetic fixtures)
     │  --probe              config → connect → signal_table → write, as 4 stages
     ▼
  6  deploy/deploy-sites.sh  2 CloudFront distributions         →  PpFraudSites
                             -c vpcId/subnetIds/securityGroupIds from step 1
```

## Stacks in the account

| Stack | Holds |
|---|---|
| `PpFraudData` | VPC, 12 endpoints, Aurora Serverless v2, client security group |
| `AgentCore-ppFraud-default` | 10 runtimes, memory, gateway, execution roles |
| `PpFraudReferenceTools` | the alert-dictionary Lambda |
| `PpFraudRdsTools` | the read-only SQL Lambda |
| `PpFraudSites` | 2 buckets, 2 distributions, the VPC-attached stream proxy |

## Measured, and not

**Measured, one run, APP-1004, us-east-1:** 37.5 s end to end; fan-out 31.4 s vs 157.6 s
sequential; synthesis 6.1 s on GPT-5.6 Luna (55 s on Sonnet 5); $0.245 per application from
real token usage; 21/21 keys valid; 8/8 specialists returned.

**Measured today, us-west-2:** the RDS round trip, in both transports — Data API from the
pipeline (840 rows read, output table created) and `get_schema` through the VPC-attached
Gateway Lambda. First live database contact in this project.

**Not measured:** p50/p95 across the fixtures or at concurrency. One run is an existence proof,
not a distribution, and no percentile may be quoted from it. `evals/bench.py --live` is what
produces one.
