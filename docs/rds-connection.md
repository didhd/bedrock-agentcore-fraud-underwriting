# Connecting the agents to the customer's RDS

The customer's stated first priority, from the 2026-08-11 call: get a POC connected to a
real RDS in their test environment "to do read and writes". Their words on the priority
order — Bill "said that was the biggest thing that they wanted to do is get the real
connection to the RDS staging environment."

This document is what was built, what it costs, and the one place where the obvious
reference architecture is the wrong answer here.

## Two database needs, not one

Conflating these breaks the property the whole port rests on, so they are built as two
separate things with two separate security postures.

| | Signal path | Analyst tool |
|---|---|---|
| Question | "Adjudicate APP-1004" | "What was our decline rate last week?" |
| Who reads | the pipeline, before any model runs | Francis, as a tool call |
| Who writes the SQL | nobody — signals are precomputed | the model |
| Consumers | all 8 specialists + synthesizer | Francis only |
| Latency budget | inside the <1 min per application | a human is waiting |
| Transport | `signal_layer/sources/aurora.py`, RDS Data API | Lambda behind AgentCore Gateway |
| Runtime network mode | `PUBLIC` — verified working | `PUBLIC`; only the Lambda is in the VPC |
| Writes | yes — `persist_adjudication` | **no** |
| Enable with | `./deploy/connect-rds.sh --enable` | deployed by `./deploy/connect-rds.sh` |

The eight specialists have no SQL tool and never will. `tests/test_rds_connection.py`
asserts it in two directions, because it is a one-line change away at any time:

```
test_no_specialist_receives_the_sql_tool
test_the_specialists_have_no_signal_widening_tool
```

A specialist that could query would be able to widen its own domain view past what was
projected for it, and it would put a text-to-SQL round trip back inside the per-application
minute. Those are the two things `signal_layer/` exists to prevent.

## Where the AWS Aurora DSQL + AgentCore reference architecture applies, and where it does not

The AWS Database Blog's [grid investigation agent][blog] (2026-05-18) builds a database
agent on AgentCore: a Lambda behind a Gateway exposing `get_schema` and
`query_grid_database`, an agent that generates parameterized SQL, read-only validation, a
table allowlist, a 500-row cap, and a least-privilege database role as the real boundary.

[blog]: https://aws.amazon.com/blogs/database/building-an-ai-powered-grid-investigation-agent-with-aurora-dsql-and-amazon-bedrock-agentcore/

**Adopted, for the analyst tool.** `deploy/cdk/lambda-rds/main.py` is that pattern:
`get_schema` reading `information_schema` at call time, `query_underwriting_data` taking
`%s` placeholders with values in a separate array, SELECT/WITH only, one statement, an
allowlist, and a row cap. The agent generating the SQL is correct here — the question is
invented at the keyboard and there is no precomputed answer to project.

**Rejected, for the signal path.** The blog's agent generates SQL for its primary workload.
Doing that per application would cost 8 model-generated queries plus 8 executions inside a
sub-minute budget, and it is the specific thing this migration is removing: eliminating the
per-application Cortex Analyst calls is one of the few cost levers in this project that is
verified. The signal path stays precomputed.

**The one thing the blog gives us that we did not have.** Its Lambda holds the database
connection, not the agent. That matters because all ten AgentCore runtimes are
`networkMode: PUBLIC` — deliberately, and verified working — so they cannot open a socket
into a private subnet:

> **In-VPC runtimes were tried and reverted.** The CLI supports `networkMode: VPC` and all
> ten came up `READY` that way, then returned `APITimeoutError` at request time: a
> private-isolated subnet cannot reach the GPT-5.6 synthesizer through the `bedrock-mantle`
> interface endpoint. Nothing on this page requires in-VPC runtimes, and the signal path is
> verified with them `PUBLIC`.

- `signal_layer/sources/aurora.py` runs **inside** the runtime, so it needs the RDS Data
  API — which requires Aurora with the Data API enabled. A plain `db.t3.micro` RDS
  PostgreSQL instance cannot serve it.
- The Gateway Lambda can be **VPC-attached**, so it reaches any RDS PostgreSQL instance
  with no Data API at all, while the ten runtimes stay `PUBLIC`.

So `--vpc` exists, and it is the answer when `--probe` reports the Data API is off:

```bash
./deploy/connect-rds.sh --vpc subnet-a,subnet-b sg-xxxx
```

That path bundles `pg8000` rather than `psycopg2` — pure Python, so it installs from a
platform-independent wheel and `connect-rds.sh` does not have to cross-compile a manylinux
binary from a Mac. `describe_transport()` reports which of the two is live, and every error
carries it, because a Data API error and a subnet timeout look identical from the agent side
and have opposite fixes.

**Not adopted: Aurora DSQL.** The blog's database is DSQL, which is publicly addressable and
IAM-authenticated, so it needs no VPC. The customer's data is already in Aurora PostgreSQL
(~10 GB, 3.4M reads/day). Migrating it is not on the table for a POC, and the two transports
above make it unnecessary.

**Not adopted: A2A between agents.** The blog uses it to let a thin app agent delegate to a
database agent. This project's 8-way fan-out is `asyncio.gather` over
`InvokeAgentRuntime` — measured, traced as one joined trace across nine runtimes. A2A would
add a protocol layer without changing the topology.

## Read is half of it. Write is the half they asked about.

`persist_adjudication` writes one row per adjudication. Two decisions worth knowing:

**Columns come from the contract, not from the dict.** `agents/contracts.Adjudication` has
exactly 21 fields in the customer's order; the insert iterates *those*, so an adjudication
carrying an extra key cannot silently add a column. `application_id` is a column but is
**not** one of the 21 keys — it travels in the transport envelope.

**`output_ddl()` prints the DDL rather than assuming theirs.** `UNDERWRITING_REPORT_OUTPUT`
is an open question with the customer, and not a cosmetic one: `master_agent_prompt.txt`
specifies a mid-band of `MEDIUM RISK` with three recommendation values, while the
process-guide PDF specifies `POSSIBLE RISK` with four and shows no `*_flag` columns at all.
A `CHECK` constraint would encode one and reject the other. So both enum columns are plain
`text` and there is no `CHECK` in the generated DDL — a test asserts that. The eight
`*_flag` columns are `integer`, because the contract says `Literal[0, 1]`.

If the table already exists, `ensure_output_table()` leaves it alone
(`create table if not exists`); it must not alter a schema someone else owns.

## Failing loud

`SIGNAL_MODE=aurora` with a missing or unusable connection **raises**. It does not fall back
to fixtures. A fall back would produce an adjudication computed from 14 demo applications
while everyone in the room believed it came from the staging cluster, and nothing in the
output would say so. Same posture as the Snowflake source, for the same reason.

## What is verified, and what is not

**Verified by execution:**
- The SQL validator against 20 hostile statements — stacked statements, comment-hidden
  semicolons, `SELECT INTO`, off-allowlist tables, `pg_sleep`, `pg_read_file`. All rejected;
  6 legitimate analyst queries pass. Word boundaries checked against `created_at`,
  `updated_at`, `deleted_at`, `call_count` so the keyword scan does not reject real schemas.
- The row cap applied *after* execution as well as in the statement, because a `limit`
  inside a CTE satisfies the validator without bounding the outer result.
- The generated DDL against the 21-key contract: 8 integer flags, 9 text summaries, no
  `CHECK`, every contract key present.
- `bool` coerced before `int` in the Data API parameter mapping (bool subclasses int in
  Python, so the order is load-bearing).
- Table names allowlisted by regex, tested against `a"; drop table x --` and five other
  forms. A table name cannot be a bound parameter, so interpolation is unavoidable and an
  allowlist is the only available control.
- `deploy/vendor.sh` now resolves every dotted import of a shared package anywhere in the
  vendored tree, not just `from app.X` in `main.py`. `signal_layer.sources.aurora` is
  imported lazily inside the `SIGNAL_MODE` branch, so a missing submodule previously passed
  CI, passed `agentcore validate`, deployed clean, and raised only on the first aurora-mode
  invocation. Mutation-tested twice: the first version of the check was a no-op and that is
  how it was found.
- 1396 tests pass offline.

**Verified live on 2026-08-12, in our own account:**
- Both transports. The Data API from the pipeline (840 signal rows read, output table
  created) and `get_schema` through the VPC-attached Gateway Lambda. The runtimes were
  `networkMode: PUBLIC` throughout — the Data API is a public IAM-authenticated endpoint,
  so in-VPC runtimes are **not** required for this path and are not used.
- A full adjudication with `SIGNAL_MODE=aurora`, after two defects that a green probe did
  not catch: the runtime execution role lacked `rds-data:ExecuteStatement` (the probe runs
  on the *operator's* credentials, a different principal), and
  `build_aurora_payload` constructed a `SignalPayload` shape that does not exist, with a
  bare `except Exception` turning the `TypeError` into `None` — so all eight specialists
  reported "no signals" while the panel said the cluster was connected.

**Still not verified — there is no CUSTOMER database on this side to call:**
- A round trip against *their* cluster, in either transport. `--probe` exists for exactly that moment: it
  reports config → connect → signal table → write as four distinct stages, so a cluster
  without the Data API, a wrong database name, a missing signal table and a role without
  `CREATE` are four different messages rather than one failure.
- The signal table's actual shape. `build_aurora_payload` handles both one-row-per-signal
  and one-wide-row-per-application and reports which it read, because their architecture
  document describes the former and we have not seen the table.
- The real table names. `DEFAULT_ALLOWLIST` is a guess; `RDS_TABLE_ALLOWLIST` extends it
  without replacing the defaults, and `get_schema` reports which allowlisted tables are
  absent from their database.

## Running it

```bash
# Aurora with the Data API (nothing enters a VPC)
./deploy/connect-rds.sh

# any RDS PostgreSQL (the Lambda joins the VPC; the ten runtimes stay PUBLIC)
./deploy/connect-rds.sh --vpc subnet-a,subnet-b sg-xxxx

./deploy/connect-rds.sh --probe        # test, change nothing
./deploy/vendor.sh && agentcore deploy --target default --yes

./deploy/connect-rds.sh --enable       # specialists read signals from the cluster
./deploy/connect-rds.sh --disable      # back to committed fixtures
```

`--enable` refuses on a `--vpc` deployment and says why: the signal path reads through the
Data API from inside the runtime, so it needs a cluster ARN. The analyst tool still works.

## Ask them for

1. **The signal table name and its shape** — one row per signal, or one wide row.
2. **`CLEAN_MAIN_STREAM_FILE.sql`** and the `UNDERWRITING_REPORT_OUTPUT` DDL. This settles
   the enum conflict above, and `full_underwriting_signal_dictionary.xlsx` would settle the
   signal set at the same time.
3. **A SELECT-only database role for the Lambda.** Every check in
   `deploy/cdk/lambda-rds/main.py` is defence in depth; the database grant is the actual
   boundary, and the blog is right about that. Give the Lambda a role that cannot write and
   the validator becomes a second line rather than the only one.
4. Whether the staging cluster is Aurora with the Data API enabled, or a plain instance.
   That single answer picks the transport.
