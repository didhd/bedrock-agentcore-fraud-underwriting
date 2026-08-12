"""Read-only SQL against the customer's RDS, exposed to Francis through AgentCore Gateway.

WHAT THIS IS FOR, AND WHAT IT IS DELIBERATELY NOT FOR

Two completely different database needs live in this project and conflating them would
break the property the whole port rests on.

  1. **Per-application signals.** Eight specialists, one application, under a minute.
     Handled by `signal_layer/sources/aurora.py`: precomputed signals, read by the pipeline
     BEFORE any model runs, projected per domain. No model writes SQL, and no text-to-SQL
     round trip sits in the hot path. That is the rule in CLAUDE.md -- "agents interpret;
     they never query" -- and it is also the reason Cortex Analyst's ~27s is out of the
     per-application budget. **This module has nothing to do with that path.**

  2. **An analyst's ad-hoc question.** "How many applications did we decline last week?"
     "Show me every application that tripped alert 164 this month." Unbounded, exploratory,
     one human waiting. There is no precomputed answer to project, and there never will be
     -- the question is invented at the keyboard.

This module is (2), and only (2). It is attached to Francis, the interactive agent, and to
nothing else. The eight specialists have no SQL tool and gain none from this file.

WHY A LAMBDA BEHIND THE GATEWAY, NOT A DIRECT CALL FROM THE RUNTIME

Three reasons, and the first is the one that actually unblocks the customer:

  * **The network path is one thing to get right, not ten.** A runtime reaches a private
    subnet only if it is `networkMode: VPC` with a correct `networkConfig`; a VPC-attached
    Lambda reaches it regardless. So this target works whether or not the runtimes are in the
    VPC, and it works against a plain `db.t3.micro` with no Data API at all --
    `signal_layer/sources/aurora.py` needs the Data API precisely because it runs INSIDE the
    runtime, and this does not.
  * **The database role is the security boundary, not our code.** The Lambda connects as a
    SELECT-only role. Every check in this file is defence in depth on top of that, not
    instead of it. If the validation below were bypassed entirely, the database still
    refuses the write.
  * **No agent holds database credentials.** Francis gets a tool. The secret, the grant and
    the subnet stay on this side of the Gateway.

TWO TRANSPORTS, CHOSEN BY WHAT THE CUSTOMER ACTUALLY HAS

`AURORA_CLUSTER_ARN` set -> RDS Data API. No VPC configuration on this function, no driver
to bundle, credentials fetched by the service. Requires Aurora with the Data API enabled.

Otherwise -> `pg8000` over a socket, which needs this function in the VPC. Works with any
RDS PostgreSQL. `pg8000` and not `psycopg2` because it is pure Python: it installs from a
platform-independent wheel, so `deploy/reference-tools.sh` does not have to cross-compile a
manylinux binary from a Mac.

`describe_transport()` reports which one is live, so nobody has to infer it from a stack
trace.

WRITES ARE NOT REACHABLE FROM HERE, ON PURPOSE

The customer asked for read AND write, and they get both -- but the write is
`persist_adjudication`, called by the pipeline after an adjudication is produced, with
columns taken from the 21-key contract. A model deciding to issue an UPDATE against
underwriting data is a different thing entirely, and it is not what was asked for. So this
target exposes no write tool and the validator rejects every non-SELECT statement.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Final

#: Data API coordinates. Their presence is what selects the transport.
CLUSTER_ARN_ENV: Final[str] = "AURORA_CLUSTER_ARN"
SECRET_ARN_ENV: Final[str] = "AURORA_SECRET_ARN"
DATABASE_ENV: Final[str] = "AURORA_DATABASE"

#: Direct-socket coordinates, used when the Data API is not configured.
HOST_ENV: Final[str] = "PGHOST"
PORT_ENV: Final[str] = "PGPORT"

#: Ceiling on rows returned to the model. Not a performance guard -- a blast-radius guard.
#: An unbounded `select *` over an application table would put borrower records into a model
#: context window and into the trace, and no analyst question needs 40,000 rows to answer.
MAX_ROWS: Final[int] = 200

#: Tables the tool may name. An allowlist and not a denylist: the customer's cluster holds
#: their whole underwriting estate, and "everything except the things we thought of" is not
#: a control. Extended by `RDS_TABLE_ALLOWLIST` at deploy time, because the real table names
#: are theirs and we have asked for them rather than guessed.
DEFAULT_ALLOWLIST: Final[tuple[str, ...]] = (
    "application_signals",
    "underwriting_report_output",
    "applications",
    "alerts_fired",
)


def allowlist() -> tuple[str, ...]:
    extra = os.environ.get("RDS_TABLE_ALLOWLIST", "")
    names = [n.strip().lower() for n in extra.split(",") if n.strip()]
    return tuple(dict.fromkeys([*DEFAULT_ALLOWLIST, *names]))


# ---------------------------------------------------------------------------
# Validation. Everything here is defence in depth on top of a SELECT-only DB role.
# ---------------------------------------------------------------------------

#: Statement forms that may run. `with` is included because a CTE is how any real analyst
#: question over six joined tables gets written, and a CTE that only reads is still a read.
_READ_PREFIXES: Final[tuple[str, ...]] = ("select", "with")

#: Rejected outright wherever they appear as a word. Belt and braces alongside the prefix
#: check: `select * from t; drop table t` is caught by the semicolon rule, and
#: `select ... into newtable` is caught here, because it writes while starting with `select`.
_FORBIDDEN: Final[tuple[str, ...]] = (
    "insert", "update", "delete", "drop", "alter", "truncate", "create", "grant",
    "revoke", "copy", "vacuum", "call", "do", "merge", "into", "pg_read_file",
    "pg_ls_dir", "dblink", "pg_sleep", "set_config", "lo_import", "lo_export",
)


def _strip_sql_comments(sql: str) -> str:
    """Remove comments before validating.

    Without this, `select 1 /* */ ; drop table x --` hides a second statement from the
    semicolon check, and `--` hides a forbidden keyword from the word scan. Validating the
    text a human sees rather than the text the server executes is the standard way this kind
    of check gets bypassed.
    """
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def validate_sql(sql: str, *, allowed: set[str] | None = None) -> tuple[str, str | None]:
    """`(sql_to_run, error)`. Exactly one is meaningful.

    Returns the error rather than raising it: the Gateway hands a return value to the model,
    and a model that is TOLD "only SELECT is allowed, and only these tables" writes a
    correct query on its next attempt. An exception would reach it as an opaque failure.
    """
    if not sql or not sql.strip():
        return "", "empty sql"

    cleaned = _strip_sql_comments(sql).strip().rstrip(";").strip()
    if not cleaned:
        return "", "the statement was only comments"

    lowered = cleaned.lower()

    if ";" in cleaned:
        return "", (
            "multiple statements are not allowed. Send one SELECT; if you need several, "
            "call the tool again."
        )

    if not lowered.startswith(_READ_PREFIXES):
        return "", (
            f"only SELECT and WITH are allowed, and this starts with "
            f"{lowered.split(' ', 1)[0]!r}. This tool reads; it cannot modify underwriting "
            "data."
        )

    for word in _FORBIDDEN:
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            return "", (
                f"the keyword {word!r} is not allowed in a read query. If this was "
                "`select ... into`, use a plain select instead."
            )

    # Which tables does it name? Everything following `from` or `join`, schema qualifier
    # dropped, compared against the allowlist. A quoted or aliased name is fine; a name that
    # is not on the list is not.
    referenced = {
        m.group(1).split(".")[-1].strip('"').lower()
        for m in re.finditer(r'\b(?:from|join)\s+([A-Za-z_"][\w".]*)', lowered)
    }
    # `allowed` is passed by run_use_case: a verified query's own declared tables, rather
    # than the ad-hoc allowlist. Widening DEFAULT_ALLOWLIST to fit verified queries would
    # widen what the free-form SQL tool can read too, which is the opposite of the intent.
    permitted = set(allowed) if allowed else set(allowlist())

    # CTE names are DEFINED BY THIS STATEMENT, so they are not tables and must not be checked
    # against a table allowlist. Without this, any query with a `with x as (...)` clause is
    # rejected for referencing "unknown tables" that it created itself -- which rejected the
    # customer's largest verified query (eleven CTEs) and would reject most real analyst
    # questions, since a CTE is how anything joining five tables gets written.
    ctes = {
        match.group(1).lower()
        for match in re.finditer(r"(?:\bwith\s+(?:recursive\s+)?|,)\s*([A-Za-z_]\w*)\s+as\s*\(",
                                 lowered)
    }
    permitted |= ctes

    unknown = sorted(referenced - permitted)
    if unknown:
        return "", (
            f"table(s) {', '.join(unknown)} are not available to this tool. Readable "
            f"tables: {', '.join(sorted(permitted - ctes))}. Call get_schema for their columns."
        )
    if not referenced:
        return "", (
            "no table was referenced. Call get_schema first to see what is readable."
        )

    # A LIMIT the caller did not ask for, rather than trusting one it might omit. `MAX_ROWS`
    # is also applied again when the rows come back, because `limit` inside a CTE would
    # satisfy this regex without bounding the outer result.
    if not re.search(r"\blimit\s+\d+", lowered):
        cleaned = f"{cleaned} limit {MAX_ROWS}"

    return cleaned, None


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def describe_transport() -> dict[str, Any]:
    """Which path is live, without connecting. Cheap enough to include in every result."""
    if os.environ.get(CLUSTER_ARN_ENV):
        return {
            "transport": "rds-data-api",
            "database": os.environ.get(DATABASE_ENV),
            "requires": "Aurora with the Data API enabled",
            "vpc_attached": False,
        }
    return {
        "transport": "pg8000-socket",
        "host": os.environ.get(HOST_ENV),
        "database": os.environ.get(DATABASE_ENV),
        "requires": "this Lambda attached to a subnet that can reach the instance",
        "vpc_attached": True,
    }


def _run_data_api(sql: str, params: list[Any]) -> list[dict[str, Any]]:
    """Execute over the Data API.

    Positional `%s`-style placeholders are not what the Data API speaks -- it takes named
    ones -- so they are rewritten to `:p0, :p1, ...` here. The values still travel
    separately; nothing is interpolated into the statement.
    """
    import boto3  # noqa: PLC0415

    named = sql
    bound = []
    for index, value in enumerate(params):
        named = named.replace("%s", f":p{index}", 1)
        bound.append({"name": f"p{index}", "value": _data_api_value(value)})

    response = boto3.client("rds-data").execute_statement(
        resourceArn=os.environ[CLUSTER_ARN_ENV],
        secretArn=os.environ[SECRET_ARN_ENV],
        database=os.environ[DATABASE_ENV],
        sql=named,
        parameters=bound,
        includeResultMetadata=True,
    )

    columns = [c["name"] for c in response.get("columnMetadata", []) or []]
    rows = []
    for record in response.get("records", []) or []:
        values = [None if c.get("isNull") else next(iter(c.values())) for c in record]
        rows.append(dict(zip(columns, values)))
    return rows


def _data_api_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {"isNull": True}
    if isinstance(value, bool):
        # Before int: bool subclasses int in Python, so the order matters.
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"longValue": value}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": str(value)}


def _run_socket(sql: str, params: list[Any]) -> list[dict[str, Any]]:
    """Execute over a direct connection. The path that works with plain RDS PostgreSQL.

    Credentials come from the same Secrets Manager secret the Data API would have used, so
    the customer stores one thing regardless of which transport they end up on.
    """
    import boto3  # noqa: PLC0415
    import pg8000.dbapi  # noqa: PLC0415

    secret = json.loads(
        boto3.client("secretsmanager").get_secret_value(
            SecretId=os.environ[SECRET_ARN_ENV]
        )["SecretString"]
    )

    connection = pg8000.dbapi.connect(
        host=os.environ.get(HOST_ENV) or secret.get("host"),
        port=int(os.environ.get(PORT_ENV) or secret.get("port") or 5432),
        database=os.environ.get(DATABASE_ENV) or secret.get("dbname") or "postgres",
        user=secret["username"],
        password=secret["password"],
        ssl_context=True,
        timeout=25,
    )
    try:
        cursor = connection.cursor()
        # pg8000 speaks `%s`, which is what the tool schema asks the model for, so no
        # rewriting is needed on this path.
        cursor.execute(sql, tuple(params) if params else None)
        columns = [d[0] for d in (cursor.description or [])]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        # Read-only, but an unclosed transaction still holds a connection slot, and this
        # function can run concurrently with eight specialists.
        connection.rollback()
        connection.close()


def _run(sql: str, params: list[Any]) -> list[dict[str, Any]]:
    if os.environ.get(CLUSTER_ARN_ENV):
        return _run_data_api(sql, params)
    return _run_socket(sql, params)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def get_schema(event: dict[str, Any]) -> dict[str, Any]:
    """The live schema of the readable tables.

    Read from `information_schema` at call time rather than hard-coded, because the customer
    owns these tables and their columns are one of the open questions in CLAUDE.md. A column
    added on their side becomes visible here without a redeploy, and -- more importantly --
    a column we assumed and they do not have shows up as absent instead of as a failed query
    the model cannot explain.
    """
    names = allowlist()
    placeholders = ", ".join(["%s"] * len(names))
    try:
        rows = _run(
            "select table_name, column_name, data_type, is_nullable "
            "from information_schema.columns "
            f"where lower(table_name) in ({placeholders}) "
            "order by table_name, ordinal_position",
            list(names),
        )
    except Exception as exc:  # noqa: BLE001
        return _failure(exc)

    tables: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        tables.setdefault(str(row["table_name"]), []).append(
            {
                "column": row["column_name"],
                "type": row["data_type"],
                "nullable": row["is_nullable"] == "YES",
            }
        )

    absent = [n for n in names if n not in {t.lower() for t in tables}]
    return {
        "ok": True,
        "tables": tables,
        "readable_tables": list(names),
        "not_present_in_this_database": absent,
        "row_cap": MAX_ROWS,
        **describe_transport(),
    }


def query_underwriting_data(event: dict[str, Any]) -> dict[str, Any]:
    """Run one validated, parameterized, read-only SELECT."""
    sql = str(event.get("sql") or "")
    params = event.get("parameters") or []
    if isinstance(params, str):
        # A model that has been asked for an array sometimes sends a JSON string of one.
        # Recovering is better than telling it the schema was wrong when the values are
        # right there.
        try:
            params = json.loads(params)
        except json.JSONDecodeError:
            params = [params]
    if not isinstance(params, list):
        params = [params]

    checked, error = validate_sql(sql)
    if error:
        return {
            "ok": False,
            "error": error,
            "readable_tables": list(allowlist()),
            "rule": "read-only: SELECT or WITH, one statement, allowlisted tables",
        }

    placeholder_count = checked.count("%s")
    if placeholder_count != len(params):
        return {
            "ok": False,
            "error": (
                f"the statement has {placeholder_count} %s placeholder(s) but "
                f"{len(params)} parameter(s) were supplied. Values must be passed as "
                "parameters, never written into the SQL."
            ),
        }

    try:
        rows = _run(checked, list(params))
    except Exception as exc:  # noqa: BLE001
        return _failure(exc)

    # Applied again after the fact: a `limit` inside a CTE satisfies the validator's regex
    # without bounding the outer result set.
    truncated = len(rows) > MAX_ROWS
    return {
        "ok": True,
        "sql_executed": checked,
        "row_count": min(len(rows), MAX_ROWS),
        "truncated": truncated,
        "rows": rows[:MAX_ROWS],
        **({"note": f"result truncated to {MAX_ROWS} rows"} if truncated else {}),
    }


def _failure(exc: Exception) -> dict[str, Any]:
    """A database error the model can act on, with the diagnosis it cannot see.

    The transport is included because the two failure modes look identical from the agent
    side and have opposite fixes: a Data API error means the cluster does not have it
    enabled, and a socket timeout means this function is not in the right subnet.
    """
    name = type(exc).__name__
    message = str(exc)[:400]
    hint = None
    if "Data API" in message or "BadRequestException" in name:
        hint = (
            "this cluster may not have the RDS Data API enabled. Either enable it, or "
            "redeploy this function into the VPC and unset AURORA_CLUSTER_ARN to use the "
            "direct-socket path."
        )
    elif name in ("timeout", "TimeoutError") or "timed out" in message.lower():
        hint = (
            "no route to the database. This function needs a subnet that can reach the "
            "instance and a security group the instance accepts."
        )
    elif "ModuleNotFoundError" in name:
        hint = (
            "pg8000 is not in the deployment package. ./deploy/connect-rds.sh --vpc bundles "
            "it; the Data API path does not need it."
        )
    elif "permission denied" in message.lower():
        hint = (
            "the database role cannot read that table. That is the intended boundary -- "
            "grant SELECT if the table should be readable."
        )
    return {
        "ok": False,
        "error": f"{name}: {message}",
        **({"hint": hint} if hint else {}),
        **describe_transport(),
    }


# ---------------------------------------------------------------------------
# The customer's own verified queries
# ---------------------------------------------------------------------------

#: Generated at bundle time by deploy/cdk/lambda-rds/bundle.sh from the gitignored semantic
#: model, and gitignored itself. Absent in a fresh clone, which is why every read of it is
#: guarded rather than assumed.
USE_CASE_FILE: Final[str] = "use_cases.json"

_USE_CASES: dict[str, Any] | None = None


def use_cases() -> dict[str, Any]:
    """The bundled use-case payload, or {} when it was not bundled.

    Cached for the container's lifetime. Returning {} rather than raising means the ad-hoc SQL
    tools keep working if only this payload is missing -- a partial capability loss is better
    than a dead target.
    """
    global _USE_CASES
    if _USE_CASES is None:
        try:
            with open(os.path.join(os.path.dirname(__file__), USE_CASE_FILE)) as handle:
                _USE_CASES = json.load(handle).get("use_cases") or {}
        except (OSError, json.JSONDecodeError):
            _USE_CASES = {}
    return _USE_CASES


def list_use_cases(event: dict[str, Any]) -> dict[str, Any]:
    """What verified queries exist, what each answers, and what it accepts.

    Returns metadata only -- never the SQL. A model does not need to read a 16,000-character
    query to decide whether it answers the question, and putting it in a tool result would
    spend the context window and put the customer's SQL into the trace.
    """
    catalog = use_cases()
    if not catalog:
        return {
            "ok": False,
            "error": (
                "no verified queries are bundled with this function. The customer's semantic "
                "model is confidential and is not in the repository; re-bundle with "
                "deploy/cdk/lambda-rds/bundle.sh after restoring prompts/verbatim/."
            ),
            "use_cases": [],
        }

    return {
        "ok": True,
        "count": len(catalog),
        "use_cases": [
            {
                "use_case": slug,
                "title": entry.get("title"),
                "answers": entry.get("intent"),
                "description": entry.get("description"),
                "informs_fraud_domains": entry.get("domains") or [],
                "parameters": [
                    {
                        "name": p["name"],
                        "description": p["description"],
                        "required": p.get("required", True),
                        # Resolved per variant at bundle time. A parameter reported as accepted
                        # but silently dropped is worse than one reported as unavailable, because
                        # the model then believes its filter was applied.
                        "substitutable": any(
                            p["name"] in (v.get("substitutable") or [])
                            for v in entry.get("variants") or []
                        ),
                    }
                    for p in entry.get("parameters") or []
                ],
                "variants": len(entry.get("variants") or []),
                "row_cap": MAX_ROWS,
            }
            for slug, entry in sorted(catalog.items())
        ],
    }


def _locate(pattern: str, sql: str) -> str | None:
    """The single literal a pattern identifies, or None when it is not unique.

    The pattern is STRUCTURAL -- a hex-digest shape, a year shape -- never the literal itself,
    because the catalog that carries it is committed and the literal is customer data.
    """
    try:
        found = {match.group(0) for match in re.finditer(pattern, sql)}
    except re.error:
        return None
    if len(found) != 1:
        return None
    only = next(iter(found))
    return only if sql.count(only) == 1 else None


def _substitute(sql: str, parameters: dict[str, Any], specs: list[dict[str, Any]]) -> tuple[str, str | None]:
    """Replace located literals. `(sql, error)`.

    Refuses unless the pattern identifies EXACTLY ONE literal. Zero or several means the query
    is not shaped the way the spec assumes -- a later delivery changed it -- and rewriting one
    of several occurrences yields a statement that runs and answers a different question, which
    is the worst outcome available here because it looks like a success.
    """
    for spec in specs:
        pattern = spec.get("pattern")
        name = spec["name"]
        if not pattern or name not in parameters:
            continue
        literal = _locate(pattern, sql)
        if literal is None:
            return sql, (
                f"parameter {name!r} could not be applied: its anchor is not uniquely locatable "
                "in this verified query, and this tool will not rewrite a query it cannot locate "
                "exactly. Run the use case without that parameter, or use "
                "query_underwriting_data."
            )
        sql = sql.replace(literal, str(parameters[name]))
    return sql, None


def run_use_case(event: dict[str, Any]) -> dict[str, Any]:
    """Run one of the customer's verified queries by name.

    TAKES A SLUG, NOT SQL. That is the whole safety argument for this tool: the model chooses
    the QUESTION and never the query, so the joins, the guardrails and the test-record
    exclusions its own engineers verified cannot be edited by a model mid-conversation.

    Still validated. Verified provenance is not a reason to skip `validate_sql`: parameter
    substitution is new attack surface that their verification never covered. The table
    allowlist for this path comes from the tables the semantic model itself declares for the
    use case, rather than from DEFAULT_ALLOWLIST -- a verified query legitimately touches
    tables the ad-hoc tool has no business reading, and loosening the ad-hoc allowlist to make
    room would widen the wrong thing.
    """
    slug = str(event.get("use_case") or "").strip()
    catalog = use_cases()

    if not catalog:
        return list_use_cases({})
    entry = catalog.get(slug)
    if entry is None:
        return {
            "ok": False,
            "error": f"unknown use case {slug!r}.",
            "available": sorted(catalog),
        }

    variants = entry.get("variants") or []
    if not variants:
        return {"ok": False, "error": f"{slug} has no verified query bundled"}

    try:
        index = int(event.get("variant") or 0)
    except (TypeError, ValueError):
        index = 0
    if not 0 <= index < len(variants):
        index = 0

    parameters = event.get("parameters") or {}
    if isinstance(parameters, str):
        try:
            parameters = json.loads(parameters)
        except json.JSONDecodeError:
            parameters = {}
    if not isinstance(parameters, dict):
        parameters = {}

    sql, error = _substitute(
        variants[index]["sql"], parameters, entry.get("parameters") or []
    )
    if error:
        return {"ok": False, "error": error, "use_case": slug}

    # The allowlist for THIS statement: the tables the verified query itself reads, resolved at
    # bundle time. NOT the semantic model's table list -- at least one verified query references
    # a table the model does not declare, and the model is documentation while the query is what
    # actually runs. This is not circular: the table allowlist exists to constrain
    # MODEL-AUTHORED SQL, and on this path the model supplies a slug and cannot edit the
    # statement. Read-only validation, the row cap and the database role are the controls here.
    permitted = {t.lower() for t in variants[index].get("reads") or []}
    checked, invalid = validate_sql(sql, allowed=permitted or None)
    if invalid:
        # A verified query failing our own validator is worth surfacing loudly: either the
        # substitution broke it or the validator is wrong about a construct their engineer uses.
        return {
            "ok": False,
            "error": f"the verified query for {slug} did not pass read-only validation: {invalid}",
            "use_case": slug,
            "hint": (
                "this is a defect on our side, not a bad request -- the query is the "
                "customer's own. Report it rather than working around it."
            ),
        }

    try:
        rows = _run(checked, [])
    except Exception as exc:  # noqa: BLE001
        failure = _failure(exc)
        failure["use_case"] = slug
        failure["databases_required"] = entry.get("databases") or []
        return failure

    truncated = len(rows) > MAX_ROWS
    return {
        "ok": True,
        "use_case": slug,
        "title": entry.get("title"),
        # The customer's own question for this query, so an analyst can see exactly what was
        # asked on their behalf.
        "question": variants[index].get("question"),
        "variant": index,
        "variants_available": len(variants),
        # What was ACTUALLY applied, not what was passed. A model that filtered on a year and
        # got an unfiltered result must be able to see that from the response.
        "parameters_applied": {
            k: v
            for k, v in parameters.items()
            if k in (variants[index].get("substitutable") or [])
        },
        "row_count": min(len(rows), MAX_ROWS),
        "truncated": truncated,
        "rows": rows[:MAX_ROWS],
        **({"note": f"result truncated to {MAX_ROWS} rows"} if truncated else {}),
    }


_TOOLS = {
    "get_schema": get_schema,
    "query_underwriting_data": query_underwriting_data,
    "list_use_cases": list_use_cases,
    "run_use_case": run_use_case,
}


def _tool_name(context: Any) -> str:
    """Which tool the Gateway is invoking.

    The Gateway passes `<TargetName>___<tool>` in the client context, not in the event, so
    the payload stays exactly the tool's own arguments.
    """
    try:
        raw = context.client_context.custom["bedrockAgentCoreToolName"]
    except (AttributeError, KeyError, TypeError):
        return ""
    return raw.split("___")[-1] if raw else ""


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Gateway entry point.

    Returns the tool result DIRECTLY, not wrapped in `{"statusCode", "body"}` -- the Gateway
    hands the return value to the model as the tool result, so an HTTP-shaped envelope would
    make it read the scaffolding as the answer.
    """
    name = _tool_name(context)
    tool = _TOOLS.get(name)
    if tool is None:
        return {
            "ok": False,
            "error": f"unknown tool {name!r}. This target serves: {', '.join(sorted(_TOOLS))}.",
        }
    return tool(event or {})
