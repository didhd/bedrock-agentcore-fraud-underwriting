# Brief for Kiro (or any coding agent) deploying this

Give the agent everything between the two rules below. It is written to be pasted, not
summarised — the constraints in it are failures this repo already hit, and an agent that has not
read them will reproduce them.

Run it from a clean clone with AWS credentials for the target account:

```bash
git pull
kiro-cli          # export KIRO_API_KEY first; never paste a key into a file or a commit
```

> **On that API key.** Keep it in your shell or a secret manager. It must not land in
> `.env`, in a script, in a commit, or in a chat log. If one has been pasted somewhere shared,
> rotate it — a leaked key is leaked regardless of what was deleted afterwards.

---

## Your task

Deploy this repository into the AWS account whose credentials are configured, end to end, and
report what you verified rather than what you ran.

## Read these first, in this order

1. `CLAUDE.md` — the engineering contract. Several obvious improvements in this repo were
   measured and rejected; that file says which and why. Do not undo one.
2. `ARCHITECTURE.md` — what talks to what, and which boxes are deployed vs designed.
3. `README.md` § "Deploy: the ordered runbook" — the commands, in order.
4. `docs/rds-connection.md` — the two database transports and what is *not* verified.

## Non-negotiable rules

1. **Never fabricate a number.** No latencies, token counts, costs, percentages, or
   "typically ~2s". Numbers come from `evals/bench.py` or from something you measured in this
   session. If you did not measure it, say so. This repo previously shipped four fabricated
   figures and it was the single biggest liability.
2. **Never paraphrase customer text.** `prompts/verbatim/` is byte-identical and sha256-guarded.
   Load it or omit it.
3. **Never commit customer material.** `prompts/verbatim/`, `deploy/cdk/lambda-rds/use_cases.json`,
   `deploy/.vpc-config.json` and `deploy/.rds-connection.json` are gitignored for a reason. That
   includes their table names, column names, lender names, SQL and rule text — in code, tests,
   docs and docstrings alike. Verify with `git status --porcelain` and `git check-ignore -v`.
4. **The eight fraud specialists must never receive a SQL or schema tool.** Only the interactive
   agent gets data tools. `tests/test_rds_connection.py` asserts this in two directions; if you
   make it fail, you have broken the property the port rests on.
5. **No write tool may be reachable by a model.** Adjudications are written by the pipeline.
6. **Do not weaken a test to make it pass.** Report it instead.
7. **Do not `git commit --no-verify`.** If a hook blocks a commit, stop and report what it said.
8. **Run what you write.** `MOCK_MODE=1 python3 -m pytest tests/ -q` — baseline is
   **1473 passed, 1 skipped**. A drop in the passed count is a regression, not a detail.

## The seven traps, all of which fail silently

Each of these has actually happened here. Three of them return HTTP 200.

| # | Trap | How it looks | What it actually is |
|---|---|---|---|
| 1 | `agentcore configure` / `agentcore launch` | script "succeeds" | those verbs do not exist. The npm CLI exits 1; the **deprecated pip** `bedrock-agentcore-starter-toolkit` installs a different binary of the same name on which they parse and no-op. Check `pip show bedrock-agentcore-starter-toolkit` is "not found" |
| 2 | Skipping `./deploy/vendor.sh` | *"Runtime initialization time exceeded… 30s"* | `ModuleNotFoundError`. `agentcore package` roots the zip at `codeLocation`, so the shared packages are absent. Read CloudWatch, not the CLI error |
| 3 | `MOCK_MODE` unset | complete, plausible SSE stream | it defaults to `"1"`. Bedrock was called **zero times**. The tell is `"mock_mode": true` in the `start` frame and a fan-out under a second |
| 4 | `bedrock-mantle` IAM | first fix looks complete | it needs **two** actions with **different** resources. Granting one only reveals the other, and it fails at the *last* step after all eight specialists have been paid for |
| 5 | Renaming a resource in `agentcore.json` | a normal-looking deploy | the CFN logical id changes, so the resource is **destroyed and recreated** |
| 6 | `agentcore invoke` on a JSON SSE stream | `"success": true, "response": ""` | the runtime is streaming correctly. The CLI renders text deltas. Use boto3 `invoke_agent_runtime` and read the bytes |
| 7 | Editing a shell script while it runs | syntax error after the work succeeded | bash reads scripts incrementally. Finish, then edit |

## Deploy

Follow `README.md` § "Deploy: the ordered runbook" exactly. The order is load-bearing: step 2
must precede step 3 because the CLI reads `agentcore.json` *before* it synthesises, and
`vendor.sh` must run before **every** `agentcore deploy`.

```bash
./deploy/one-shot.sh --plan        # synth only; prints which of the three modes it selected
./deploy/one-shot.sh               # VPC + Aurora + 12 interface endpoints
./deploy/reference-tools.sh        # Gateway Lambda ARN into agentcore.json
./deploy/vendor.sh && agentcore validate --json && agentcore deploy --target default --yes
./deploy/verify.py                 # 17 checks
./deploy/connect-rds.sh --probe    # config → connect → signal_table → write, as four stages
./deploy/deploy-sites.sh -c vpcId=… -c subnetIds=… -c securityGroupIds=…
```

Three deployment modes, selected by what you pass to `one-shot.sh`, never guessed:

| You have | Flags | Stack creates |
|---|---|---|
| nothing | *(none)* | VPC + Aurora + endpoints |
| a VPC | `--vpc vpc-x --subnets a,b` | Aurora in your VPC |
| a VPC and a database | `--vpc vpc-x --subnets a,b --db-sg sg-y` | only the security-group rule |

Half-supplied flags are a hard error naming the missing value. Do not work around it by
supplying a placeholder.

**Region matters for one value.** The Aurora engine versions available differ by region and are
withdrawn over time. Check before you deploy, or CloudFormation builds the entire VPC and then
rolls it back on the cluster:

```bash
aws rds describe-db-engine-versions --engine aurora-postgresql --region <r> \
  --query 'DBEngineVersions[].EngineVersion'
./deploy/one-shot.sh --aurora-version <one that exists>
```

## Network: leave the runtimes PUBLIC

`agentcore.json` supports `networkMode: VPC`, and it works — the runtimes come up `READY`. They
then fail at request time with `APITimeoutError`, because a private-isolated subnet with no NAT
cannot reach the model the synthesizer uses, and the interface endpoint does not cover its
bearer-token path.

So: **runtimes PUBLIC, Lambdas in the VPC.** The Lambda is what needs to reach the private
database; invoking a runtime is a public SigV4 call either way. If you decide to move the
runtimes in, you own proving that every model they call is reachable — and you must prove it by
invoking, not by listing endpoints.

## The confidential files

Two of them, both required for the use-case tools and both gitignored:

```
prompts/verbatim/FRAUDBOT_SEMANTIC_VIEW.yaml            947 KB, sha256 8839d5e2…
prompts/verbatim/confidential_analyst_queries_082026.sql
```

Restore them from the delivered zip into `prompts/verbatim/` before deploying, then:

```bash
shasum -a 256 prompts/verbatim/FRAUDBOT_SEMANTIC_VIEW.yaml
MOCK_MODE=1 python3 -m pytest tests/test_semantic_model.py tests/test_usecase_tools.py -q
```

Absent, nothing crashes: the loaders raise `SemanticModelUnavailable`, `tools/usecases` is empty
and says why, and the affected tests **skip**. But the ten use-case tools will not exist, so it
is not optional for a working demo.

## What to report back

Not "I ran the commands". For each item: the command, its real output, and PASS or FAIL.

1. `MOCK_MODE=1 python3 -m pytest tests/ -q` — full counts, against the 1473/1 baseline.
2. `agentcore status` — all ten runtimes `READY`, and their `networkMode`.
3. A real invocation through boto3 `invoke_agent_runtime`, with the frames. Confirm no
   `"error"` frame and that `mock_mode` is false.
4. `./deploy/connect-rds.sh --probe` — the four stages, verbatim.
5. `git status --porcelain` plus `git check-ignore -v` on every confidential path. Zero
   customer material stageable.
6. The two CloudFront URLs, with the HTTP status of `/`, `/api/health`, and one live
   `/api/chat/francis?...` call including the final frame.
7. Anything you could not verify. Say it plainly — an unverified claim in this repo is worse
   than a gap, because the whole point of it is that every number on screen is real.

---

## Appendix: the questions still open with the customer

Do not resolve these by choosing. They need the customer's answer.

1. **Enum conflict.** `master_agent_prompt.txt` says mid-band `MEDIUM RISK` with three
   recommendation values; the process-guide PDF says `POSSIBLE RISK` with four and shows no
   `*_flag` columns. We implement the prompt file and expose `PDF_TO_PROMPT_ENUM_MAP`. This is a
   real DDL question for `UNDERWRITING_REPORT_OUTPUT`.
2. **Rule 12 vs the contract.** Their rule 12 forbids recommending an automatic decline, but
   their own `recommendation_decision` enum contains `DECLINE`. Both are their artefacts. See
   `docs/semantic-model-reconciliation.md`.
3. **The signal table.** Its real name and shape — one row per signal, or one wide row.
   `DEFAULT_ALLOWLIST` in `deploy/cdk/lambda-rds/main.py` is a guess extended by
   `RDS_TABLE_ALLOWLIST`.
4. **A SELECT-only database role** for the Gateway Lambda. Every check in that file is defence
   in depth; the grant is the actual boundary.
5. **`full_underwriting_signal_dictionary.xlsx`.** The semantic model settled all 25 rules; it
   did not settle the ~75-signal registry, which is still reconstructed from the nine prompts.
6. **Bedrock quota.** 300k applications/day × 9 calls × ~2,250 tokens ≈ 6.1B tokens/day. Sonnet
   5's daily ceiling is 8.64B and is **not** self-serve adjustable — raise it with the account
   team before any load test.
