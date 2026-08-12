# Deploying to Amazon Bedrock AgentCore

Runbook for the two runtimes this repo declares in `agentcore/agentcore.json`:

| Runtime | Path | Purpose |
| --- | --- | --- |
| `underwriter` | `app/underwriter/` | Batch path: 8-way specialist fan-out + master synthesis, one adjudication per application. |
| `francis` | `app/francis/` | Interactive path: routes an analyst question to at most 2 specialists, answers in markdown. |

Verified against **@aws/agentcore 1.0.0-preview.22**, `bedrock-agentcore` 1.14.0,
Node v22.22.0, npm 10.9.4, Python 3.12.9.

---

## 0. Read this before you copy any command from an older write-up

**`agentcore configure` and `agentcore launch` do not exist.** They were the
starter-toolkit's verbs. The npm CLI rejects both:

```console
$ agentcore configure --entrypoint agentcore_runtime.py
error: unknown command 'configure'
(Did you mean config?)
$ echo $?
1
```

The trap is not the exit code — it is the **name collision**. The deprecated pip
package `bedrock-agentcore-starter-toolkit` installs a console script *also*
called `agentcore`. Whichever resolves first on `PATH` decides which tool your CI
actually invokes, and on the toolkit's binary those subcommands parse and then do
approximately nothing. A pipeline can therefore report success while deploying
zero infrastructure. `deploy/ci.sh` fails the build if either name reappears
anywhere in the repo, and refuses to run at all if the pip toolkit is installed.

The real verbs are `create`, `add`, `remove`, `validate`, `package`, `deploy`,
`dev`, `invoke`, `logs`, `traces`, `status`, `fetch`, `exec`, `import`, `run`.

---

## 1. Install the toolchain

```bash
# The CLI. Requires Node >= 20.
npm install -g @aws/agentcore
agentcore --version          # expect 1.0.0-preview.22

# Remove the colliding binary if it is present. Not "if you want to" — if it is
# installed, every agentcore call below is ambiguous.
pip uninstall -y bedrock-agentcore-starter-toolkit

# Python deps for local runs and tests.
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Regulated-customer note — telemetry is **on by default**. The CLI reports
anonymous usage analytics unless you opt out, per machine:

```bash
agentcore config telemetry.enabled false
agentcore telemetry status                 # confirm the preference and its source
```

---

## 2. `agentcore/` already exists — do not re-scaffold

The `agentcore/` directory in this repo is hand-authored and validated. If you
ever need to reproduce it from scratch, this is the exact sequence that produces
the same shape:

```bash
agentcore create --project-name ppFraud --name underwriter --defaults \
  --language Python --framework Strands --model-provider Bedrock \
  --memory none --protocol HTTP --build CodeZip --json

agentcore add agent --name francis --type create \
  --language Python --framework Strands --model-provider Bedrock \
  --memory shortTerm --protocol HTTP --json
```

Naming constraints, all enforced by the schema (`agentcore validate` rejects
violations before any AWS call):

- **Project name** — `^[A-Za-z][A-Za-z0-9]{0,22}$`. Alphanumeric only, max 23
  characters. No hyphens, no underscores. `ppFraud` is 7.
- **Runtime / memory name** — `^[a-zA-Z][a-zA-Z0-9_]{0,47}$`. Underscores are
  allowed, **hyphens are not**. `under-writer` fails; `under_writer` passes.
- The deployed runtime name is `<project>_<runtime>`, and that combined string
  must be ≤ 48 characters. `ppFraud_underwriter` is 19.
- **Deployment target name** — `^[a-zA-Z][a-zA-Z0-9-]*$`, max 64. Exactly the
  inverse of a runtime name: hyphens are allowed, **underscores are not**.
  `pp-prod` passes, `pp_prod` fails.

The root spec is Zod **`.strict()`**: one unknown key at the top level fails
validation outright, with the offending key named.

```console
$ agentcore validate --json
{"success":false,"error":"…/agentcore/agentcore.json:\n  - root: unknown keys (remove): \"canaryKey\""}
```

Note that *nested* objects are not all strict — an unknown key inside a
`runtimes[]` entry is silently accepted. Do not rely on validate to catch a typo
inside a runtime block; check it against
`agentcore/.llm-context/agentcore.ts`, and for anything that file does not
mention, against the CLI's own Zod source at
`$(npm root -g)/@aws/agentcore/dist/schema/schemas/agentcore-project.js`.
**`.llm-context/agentcore.ts` is incomplete** — its `AgentCoreProjectSpec`
declares 15 of the root's 20 keys, omitting `knowledgeBases`, `harnesses`,
`payments` and `httpGateways` (deprecated; `.max(0)`). The generated `AGENTS.md`
is worse: it advertises `onlineInsightsConfigs` and `runtimeEndpoints` as root
arrays, which the strict root does **not** accept — declare either one and
validate fails. The compiled schema is the only authority.

### The contract `agentcore/agentcore.json` imposes on `app/`

Everything below is what the declared config *requires* of the runtime code, so
the two stay in sync:

| Declared | What `app/` must provide |
| --- | --- |
| `codeLocation: app/underwriter/`, `entrypoint: main.py` | `app/underwriter/main.py` **and** `app/underwriter/pyproject.toml`. `agentcore package` fails with `Required project file not found: …/pyproject.toml` if the latter is missing — the config alone is not enough. |
| `codeLocation: app/francis/`, `entrypoint: main.py` | Same, under `app/francis/`. |
| `runtimeVersion: PYTHON_3_12` | Each `pyproject.toml` should say `requires-python = ">=3.12"`. The packaged closure is built for that interpreter on `aarch64`. |
| `instrumentation.enableOtel: true` | `aws-opentelemetry-distro>=0.18.0` in **each** `pyproject.toml`, not just `requirements.txt`. |
| `protocol: HTTP` | An `@app.entrypoint` whose second parameter is literally named `context`. |
| `memories[].name: francisMemory` | Deploy injects the memory id as **`MEMORY_FRANCISMEMORY_ID`** (the name upper-cased). `app/francis/memory/session.py` must read exactly that, and no-op when it is unset so `agentcore dev` still works. |

`francisMemory` is declared with `strategies: []` — **short-term only**: raw
events with a 7-day expiry, no semantic/summarization extraction. That is
deliberate for a fraud-analyst session: conversation continuity within a case
review, no long-term profile of the analyst. Constraints, all verified against
validate: `eventExpiryDuration` must be **3–365** days (2 and 366 both fail), and
`indexedKeys` requires at least one strategy — adding one to a strategy-less
memory fails with `indexedKeys requires at least one memory strategy (long-term
memory)`. If long-term recall is wanted later, add strategies *and* remember that
`indexedKeys` is append-only on the service side: shrinking the array fails at
deploy time.

### Install the CDK app's dependencies — MANDATORY

```bash
(cd agentcore/cdk && npm install)
```

`agentcore deploy` runs `npm run build` (i.e. bare `tsc`) inside
`agentcore/cdk/`. The vended project pins `typescript: ~5.9.3` as a devDependency.
If you skip `npm install`, `tsc` resolves to whatever is global — TypeScript 6.x
here — and the build dies on the vended tsconfig:

```console
$ tsc --noEmit -p agentcore/cdk/tsconfig.json
tsconfig.json(5,25): error TS5107: Option 'moduleResolution=node10' is deprecated
and will stop functioning in TypeScript 7.0. Specify compilerOption
'"ignoreDeprecations": "6.0"' to silence this error.
```

Do not "fix" this by editing the vended `tsconfig.json`. Install the pinned
compiler.

---

## 3. Author the deployment target

`agentcore create` writes `agentcore/aws-targets.json` as `[]`, and this repo
ships it that way. Fill it in:

```json
[
  {
    "name": "default",
    "description": "Point Predictive fraud underwriting - primary",
    "account": "123456789012",
    "region": "us-east-1"
  }
]
```

- `account` must be exactly **12 digits** — validate rejects anything else with
  `AWS account ID must be exactly 12 digits`.
- `region` is a closed enum of 20 AgentCore regions; a wrong value fails
  validation with the full list. Memory is region-restricted *beyond* that enum,
  so a target being valid here does not mean `francisMemory` can be created
  there — check Memory availability separately for any non-US target.
- **`agentcore deploy` has no `--region`, no `--profile`, no `--agent` flag.**
  Region and account come from this file only; credentials come from the ambient
  chain (`AWS_PROFILE`, env vars, instance role). If you need a different profile,
  export `AWS_PROFILE` before the call.
- Export **`AWS_REGION` explicitly**. Do not rely on `AWS_DEFAULT_REGION`: the
  Python SDK's fallback is `os.getenv("AWS_REGION") or boto3.Session().region_name
  or "us-west-2"` (`bedrock_agentcore/identity/auth.py:339`), which ignores
  `AWS_DEFAULT_REGION` and silently lands in **us-west-2**. `MemoryClient` and
  `GatewayClient` do the same. A cross-region surprise here looks like a
  permissions error, not a config error.

```bash
export AWS_REGION=us-east-1
export AWS_PROFILE=your-bedrock-profile
```

Bedrock model access must be enabled in that region. The configuration this repo
targets uses the **`global.*`** inference-profile prefix (`us.*` costs exactly
1.10× `global.*` on every rate category), so make sure
`deploy/iam_policy.json`'s `runtimeExecutionRole` is what you attach — a policy
allowing only `us.anthropic.*` forbids the cheaper prefix.

---

## 4. One-time account setup: CloudWatch Transaction Search

Without this, you get AgentCore's service metrics and **zero spans**. There is no
per-agent latency or token attribution to look at.

`agentcore deploy` attempts it for you whenever the project has a Python runtime
(both of ours are), and reports:

> Transaction search enabled. It takes ~10 minutes for transaction search to be
> fully active and for traces from invocations to be indexed.

It performs four idempotent, account-level calls, which is why the deploying
principal needs the `EnableTransactionSearchOnce` statement:

1. `application-signals:StartDiscovery`
2. A CloudWatch Logs resource policy named **`TransactionSearchXRayAccess`**
   granting `logs:PutLogEvents` to principal `xray.amazonaws.com` on log groups
   `aws/spans` and `/aws/application-signals/data`, conditioned on
   `aws:SourceAccount` and an `aws:SourceArn` ArnLike of
   `arn:aws:xray:<region>:<account>:*`
3. `xray:UpdateTraceSegmentDestination` → `CloudWatchLogs`
4. `xray:UpdateIndexingRule` — rule `Default`, probabilistic, 100% sampling

If the customer will not grant those actions to the deploy role, have an admin
run the equivalent once and then tell the CLI to stop trying:

```bash
agentcore config disableTransactionSearch true
# or keep it enabled but sample less:
agentcore config transactionSearchIndexPercentage 10
```

The other half of observability is in-process:

- Both runtimes declare `"instrumentation": {"enableOtel": true}`, which makes
  AgentCore wrap the entrypoint with **`opentelemetry-instrument`**.
- That wrapper needs **`aws-opentelemetry-distro>=0.18.0`** installed. It is in
  `requirements.txt` and must also be in each runtime's
  `app/<runtime>/pyproject.toml`, because `agentcore package` builds each
  runtime's dependency closure from its own pyproject, not from
  `requirements.txt`. Missing it means the wrapper cannot start.
- **Memory and Gateway get no log or trace destinations automatically. Only
  Runtime does.** Wiring theirs is a manual three-step:
  `logs:PutDeliverySource` on the resource → `logs:PutDeliveryDestination` on a
  log group → `logs:CreateDelivery` joining them. Those actions are in the
  `ObservabilityDeliveryWiring` statement. Skip it and `francisMemory` is
  silently trace-free.
- The AgentCore SDK emits **no** token, cost, or latency span attributes, and
  Strands records its token histograms without attributes — so they cannot be
  sliced per agent. Per-agent spend telemetry is this repo's own code
  (`observability/metrics.py`, namespace `AgentCoreFraudDemo`), which is why the
  execution role carries a namespace-scoped `cloudwatch:PutMetricData`.

---

## 5. Local development

```bash
agentcore dev --runtime underwriter --logs
agentcore dev --runtime francis --logs
```

- `--runtime` is **required in non-interactive mode** once the project has more
  than one runtime: `agentcore dev --logs` alone stops with
  `Error: Multiple runtimes found. Use --runtime to specify which one. Available:
  underwriter, francis`. Bare `agentcore dev` opens the interactive picker
  instead, so the omission only bites in scripts.
- The default port is 8080, offset by the runtime's index in a multi-runtime
  project — so `francis` (index 1) lands on 8081 unless you pass `-p`.
- The local server exposes `POST /invocations` and `GET /ping`, and — like the
  deployed runtime — replies `text/event-stream` **always**, even for a one-line
  prompt. Every client must parse `data: {...}` frames.
- Because an async-generator entrypoint's exceptions are swallowed into a
  terminal `data: {"error": ...}` frame *after* HTTP 200, clients must inspect
  every frame for an `error` key. A 200 is not success.
- Memory is **not available** under `agentcore dev`. The `francis` session
  manager is written to no-op when `MEMORY_FRANCISMEMORY_ID` is unset, so local
  runs are stateless by design.
- Local-dev identity writes `.agentcore.json` into the **current working
  directory** (a workload identity name plus a generated user id). It is
  gitignored; do not commit it.

---

## 6. Pre-deploy gate

```bash
bash deploy/ci.sh
```

Runs, in order: the dead-command guard → `agentcore validate --json` → `pytest`
→ `agentcore package` → `agentcore deploy --dry-run`. Creates no AWS resources.
Set `CI_SKIP_DRY_RUN=1` to stop after packaging; the dry run is also skipped
automatically while `aws-targets.json` is still `[]`.

Or run the pieces by hand:

```bash
agentcore validate --json
agentcore package                       # writes agentcore/<runtime>.zip
agentcore package --runtime underwriter # just one

agentcore deploy --dry-run --target default --json
agentcore deploy --diff  --target default    # CDK diff instead
```

**It is `--dry-run`, not `--plan`.** The AWS docs' `--plan` flag does not exist
in this CLI:

```console
$ agentcore deploy --plan
error: unknown option '--plan'
```

(Internally the CLI does map `--dry-run` onto a `plan` option, which is likely
how the docs bug started.) `--dry-run` returns after the deployability check and
before `deployStack`, so it synthesizes CloudFormation and touches nothing.

Note `agentcore package` leaves `agentcore/<runtime>/staging/` — the runtime's
full dependency closure — next to the zip. Several third-party wheels in there
ship test modules with colliding basenames, which aborts a bare `pytest` during
collection; `ci.sh` passes `--ignore=agentcore` for that reason. Both the zips
and the staging trees are gitignored build artifacts.

---

## 7. Deploy

```bash
agentcore deploy --target default --yes
# add --verbose for resource-level CloudFormation events
# add --json    for machine-readable output
```

What it actually does, in order: resolve the target → validate the project spec →
`npm run build` in `agentcore/cdk/` → create credential providers out of band
(see below) → synthesize CloudFormation → bootstrap the environment if needed
(**only with `--yes`**; otherwise it stops and tells you to) → check stack
deployability → deploy stack `AgentCore-ppFraud-default` → persist deployed state
→ enable Transaction Search.

Things that will bite you:

- **`agentcore/.cli/deployed-state.json` must be committed.** The credential
  providers are created *before* CloudFormation runs, out of band, and their ARNs
  are recorded only in that file; the CDK app reads them back on the next synth.
  `agentcore/.gitignore` ignores `.cli/*` and then negates this one path — do not
  "tidy up" that negation. Losing the file orphans the providers and breaks the
  next deploy.
- **A resource's `name` is its CloudFormation logical id.** Renaming a runtime or
  memory in `agentcore.json` **destroys and recreates** it — new ARN, new
  endpoint, memory contents gone. Changing any other field updates in place.
- **Container builds are ARM64-only** (Graviton). Both runtimes use
  `"build": "CodeZip"` specifically to sidestep needing an ARM64 Docker builder;
  the packaged closure is already `aarch64` (you can see
  `cpython-312-aarch64-linux-gnu.so` inside the zip). If you switch to
  `Container`, you need an ARM64 build path and CodeBuild in a VPC needs an
  explicit `vpcId`.
- `runtimeVersion` is pinned to `PYTHON_3_12` in `agentcore.json` to match the
  local interpreter. The CLI's own default is `PYTHON_3_14` — if you regenerate
  the config, re-pin it or the deployed interpreter silently diverges from the
  one the tests ran on.
- Keep `debug=False` on `BedrockAgentCoreApp`. `debug=True` exposes a
  health-state backdoor through `_agent_core_app_action`.

### Deploy exit codes

| Code | Meaning |
| --- | --- |
| 0 | Deployed. |
| 2 | Deployed, **with post-deploy warnings** — success, not failure. `ci.sh` treats 2 as a pass. |
| 1 | Failed. A log path is printed (`agentcore/.cli/logs/deploy/…`). |

---

## 8. Invoke

```bash
agentcore invoke --runtime underwriter --target default --stream \
  "Run a full underwriting review of APP-1004"

agentcore invoke --runtime francis --target default --stream \
  "Is there income fraud on APP-1003?"

# Long or structured payloads that would blow the shell arg limit:
agentcore invoke --runtime underwriter --prompt-file ./payload.json
```

Session continuity:

```bash
SESSION_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"   # 36 chars
agentcore invoke --runtime francis --session-id "$SESSION_ID" --user-id analyst-42 "…"
```

`runtimeSessionId` has a client-side **33-character minimum**. `uuid4().hex` is 32
and raises `ParamValidationError` — use `str(uuid.uuid4())` (36 with hyphens).
`--user-id` becomes the memory `actorId`, so it must be stable per analyst for
long-term memory to accumulate against the right namespace.

Timeouts: a synchronous request is capped at **15 minutes, not adjustable**. A
streaming response gets 60 minutes. The 8-hour number you may have seen is the
session ceiling, not a request timeout.

---

## 9. Operate

```bash
agentcore status --json                          # all resources and their state
agentcore status --runtime underwriter
agentcore status --type memory --state deployed

agentcore logs --runtime underwriter --since 30m --level error
agentcore logs --runtime francis  --query "ConcurrencyException" --json

agentcore traces list --runtime underwriter --limit 20 --since 1h
agentcore traces get <traceId>                   # downloads to a JSON file

agentcore fetch access --help                    # endpoint URL / auth guidance
agentcore exec --runtime underwriter --it        # shell into a running container
```

Traces need Transaction Search active (§4) **and** roughly 10 minutes after
enablement before anything is indexed. An empty `traces list` right after the
first deploy is expected, not a bug.

---

## 10. Teardown

There is no `agentcore destroy`. The destroy path is: empty the config, then
deploy the emptiness.

```bash
agentcore remove all --dry-run          # show what would be reset
agentcore remove all --yes              # reset agentcore/*.json to empty
agentcore deploy --target default --yes # deletes the stack. --yes is REQUIRED here
```

Deploy detects "no resources defined" as a teardown and **refuses to proceed
without `--yes`**. To drop a single resource instead, `agentcore remove agent
--name francis --yes` then deploy.

If you want to detach one target without touching resources, remove it from
`aws-targets.json` — but note the deployed stack then becomes unmanaged. Prefer
the remove+deploy path.

---

## 11. What this runbook deliberately does not use

- **`agentcore import`** — it publishes CDK assets to S3 *before* it can fail, so
  it is not a local operation. Do not run it to "see what it would do."
- **`agentcore deploy --plan`** — does not exist (§6).
- **A container build** — `CodeZip` avoids the ARM64-only constraint (§7).

---

## 12. Applying the IAM policies

`deploy/iam_policy.json` holds **two** documents under
`runtimeExecutionRole` and `deployingPrincipal`, with `_purpose` annotations on
every statement. IAM does not accept those annotation keys, so strip them:

```bash
ACCOUNT_ID=123456789012

jq --arg a "$ACCOUNT_ID" '
  .deployingPrincipal
  | {Version, Statement: [.Statement[] | del(._purpose)]}
  | walk(if type == "string" then gsub("<ACCOUNT_ID>"; $a) else . end)
' deploy/iam_policy.json > /tmp/deployer.json

aws iam put-role-policy --role-name <your-deploy-role> \
  --policy-name AgentCorePpFraudDeploy \
  --policy-document file:///tmp/deployer.json
```

Same shape for `.runtimeExecutionRole`, attached to each runtime's execution role
(or to the role you name in `agentcore.json`'s `executionRoleArn`). The runtime
role's trust policy is `bedrock-agentcore.amazonaws.com` with
`aws:SourceAccount` / `aws:SourceArn` conditions; CDK writes it for you when it
synthesizes the role.

---

## 13. Production hardening still open

- **Data layer** — replace the fixture backend with the Lambda Gateway target
  fronting Snowflake/Aurora. Nothing above the signal layer changes.
- **Guardrails** — attach a Bedrock Guardrail so SSN redaction is enforced
  server-side and no prompt can bypass it.
- **Identity** — put the endpoints behind `authorizerType: CUSTOM_JWT` so each
  underwriter's calls are attributable. Requires an
  `authorizerConfiguration.customJwtAuthorizer` block; validate rejects one
  without the other, in both directions.
- **Evaluation** — `agentcore add evaluator` / `add dataset` / `run eval` /
  `add online-eval` for the calibration distribution check ("LOW should be the
  most common outcome").
- **Model pinning** — keep the model id pinned to an exact version so
  adjudications are reproducible.
