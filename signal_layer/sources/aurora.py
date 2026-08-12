"""Aurora PostgreSQL as the signal source, and as the adjudication sink. Read AND write.

WHY THE DATA API AND NOT A POSTGRES DRIVER

Every runtime in this project is `networkMode: PUBLIC`, so it has no route into a private
subnet and cannot open a socket to an RDS endpoint. There are exactly two ways out:

  1. **RDS Data API** -- a public AWS API endpoint, IAM-authenticated, credentials in
     Secrets Manager, no VPC anything. Nothing about the runtime changes.
  2. `networkMode: VPC` on all ten runtimes, plus subnets, security groups, and a
     Postgres driver in every dependency closure.

This module takes (1). It is the only one of the two that can be stood up in an afternoon,
and it is the reason the agents can reach a staging cluster today without a networking
change to anything already deployed.

The trade is real and stated rather than hidden: the Data API requires **Aurora** with the
Data API enabled -- Serverless v2, or provisioned Aurora PostgreSQL. A plain
`db.t3.micro` RDS PostgreSQL instance cannot serve it, and for that cluster the answer is
(2). `probe()` reports which case the customer is actually in instead of guessing.

WRITE, NOT JUST READ

The customer's stated goal is read AND write: signals in, adjudication back out. So
`persist_adjudication` exists, and it writes the 21 keys in the customer's own order.

WE DO NOT KNOW THEIR OUTPUT DDL, AND THIS DOES NOT PRETEND TO

`UNDERWRITING_REPORT_OUTPUT` is an open question in CLAUDE.md, and it is not cosmetic:
`master_agent_prompt.txt` specifies a mid-band of `MEDIUM RISK` with three recommendation
values, while the process-guide PDF specifies `POSSIBLE RISK` with four and shows no
`*_flag` columns at all. Baking either into a CHECK constraint would reject the other.

So the generated DDL uses plain `text` for both enum columns and prints itself, so the
customer can diff it against their real table rather than discover a rejected insert. If
the table already exists this module does not touch its schema -- it inserts and lets the
database reject anything that genuinely does not fit.
"""

from __future__ import annotations

import json
import os
from typing import Any, Final, Literal, get_args, get_origin

#: Secrets Manager secret holding the cluster's credentials. The Data API needs the SECRET
#: ARN, not a username and password -- it fetches the credentials itself, which is why no
#: password ever reaches the agent side.
SECRET_ID_ENV: Final[str] = "AURORA_SECRET_ARN"
CLUSTER_ARN_ENV: Final[str] = "AURORA_CLUSTER_ARN"
DATABASE_ENV: Final[str] = "AURORA_DATABASE"

#: Where the adjudication is written. Overridable because it is the customer's table, not
#: ours, and the name is one of the things we have asked them for.
OUTPUT_TABLE_ENV: Final[str] = "AURORA_OUTPUT_TABLE"
DEFAULT_OUTPUT_TABLE: Final[str] = "underwriting_report_output"

#: Where signals are read from. One row per (application, signal) is the shape their own
#: architecture document describes -- precomputed per-domain signals -- so that is the
#: default, and it is overridable rather than assumed.
SIGNAL_TABLE_ENV: Final[str] = "AURORA_SIGNAL_TABLE"
DEFAULT_SIGNAL_TABLE: Final[str] = "application_signals"


class AuroraNotConfigured(RuntimeError):
    """Raised when `SIGNAL_MODE=aurora` but the connection is absent or unusable.

    Loud for the same reason the Snowflake source is loud: falling back to fixtures would
    produce an adjudication computed from 14 demo applications while everyone believed it
    came from the staging cluster, and nothing in the output would say so.
    """


def _setting(env: str, default: str | None = None) -> str:
    value = os.environ.get(env) or default
    if not value:
        raise AuroraNotConfigured(
            f"SIGNAL_MODE=aurora but {env} is not set. Run ./deploy/connect-rds.sh."
        )
    return value


_CLIENT: Any = None


def _client() -> Any:
    global _CLIENT
    if _CLIENT is None:
        import boto3  # noqa: PLC0415
        from botocore.config import Config  # noqa: PLC0415

        _CLIENT = boto3.client(
            "rds-data",
            region_name=os.environ.get("AWS_REGION", "us-west-2"),
            # Eight specialists could each read concurrently in the distributed topology,
            # and botocore's default pool of 10 is the same ceiling that bit the Bedrock
            # client. Set explicitly rather than discovered under load.
            config=Config(max_pool_connections=16, read_timeout=60, connect_timeout=10),
        )
    return _CLIENT


def _execute(sql: str, parameters: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """One Data API statement.

    Parameters are PASSED, never interpolated. `application_id` arrives from a request, and
    an f-string here would be SQL injection reachable from the demo's own URL -- the same
    class of defect `signal_layer/rules/r17_fake_records.py` already carries an allowlist
    for.
    """
    try:
        return _client().execute_statement(
            resourceArn=_setting(CLUSTER_ARN_ENV),
            secretArn=_setting(SECRET_ID_ENV),
            database=_setting(DATABASE_ENV),
            sql=sql,
            parameters=parameters or [],
            includeResultMetadata=True,
        )
    except AuroraNotConfigured:
        raise
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        hint = ""
        if "BadRequestException" in type(exc).__name__ and "Data API" in message:
            hint = (
                " -- this cluster does not have the Data API enabled. Enable it on an "
                "Aurora cluster, or switch the runtimes to networkMode: VPC and use a "
                "Postgres driver instead."
            )
        raise AuroraNotConfigured(f"{type(exc).__name__}: {message}{hint}") from exc


def _rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Data API records as dicts.

    The Data API returns each value wrapped in a type tag (`{"stringValue": ...}`) and the
    column names separately, so a caller reading `records[0][3]` would be one column
    reorder away from silently analysing the wrong field.
    """
    columns = [c["name"] for c in response.get("columnMetadata", []) or []]
    out: list[dict[str, Any]] = []
    for record in response.get("records", []) or []:
        values = []
        for cell in record:
            if cell.get("isNull"):
                values.append(None)
            else:
                # Exactly one type key is populated per cell.
                values.append(next(iter(cell.values())))
        out.append(dict(zip(columns, values)))
    return out


def _quoted_identifier(name: str) -> str:
    """A table name safe to interpolate, or an exception.

    A table name cannot be a bound parameter, so it has to be interpolated -- which makes
    an allowlist the only correct control. Same approach as
    `signal_layer/rules/r17_fake_records.py`, and for the same reason.
    """
    import re  # noqa: PLC0415

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}(\.[A-Za-z_][A-Za-z0-9_]{0,62})?", name):
        raise AuroraNotConfigured(
            f"{name!r} is not a valid table name. Expected letters, digits and underscores, "
            "optionally schema-qualified."
        )
    return ".".join(f'"{part}"' for part in name.split("."))


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def build_aurora_payload(application_id: str) -> tuple[Any, dict[str, Any] | None, str]:
    """One application's precomputed signals. Same shape as every other source.

    `(signal_payload, raw_record, source)` -- because the eight specialists and the
    adjudication contract are downstream of this and must not know which source answered.
    """
    table = _quoted_identifier(_setting(SIGNAL_TABLE_ENV, DEFAULT_SIGNAL_TABLE))
    response = _execute(
        f"select * from {table} where application_id = :application_id",  # noqa: S608
        [{"name": "application_id", "value": {"stringValue": application_id}}],
    )
    rows = _rows(response)
    if not rows:
        raise AuroraNotConfigured(
            f"no rows in {table} for {application_id}. The signals may not be precomputed "
            "for this application yet, or AURORA_SIGNAL_TABLE names the wrong table."
        )

    # Two plausible shapes, and which one the customer used is not something to assume:
    # one row per (application, signal) -- what their architecture document describes -- or
    # one wide row per application. Both are handled, and the chosen reading is reported.
    signals: dict[str, Any] = {}
    shape = "wide"
    if len(rows) > 1 or any(
        k in rows[0] for k in ("signal", "signal_name", "signal_value")
    ):
        shape = "long"
        for row in rows:
            lowered = {str(k).lower(): v for k, v in row.items()}
            name = lowered.get("signal") or lowered.get("signal_name") or lowered.get("name")
            if not name:
                continue
            signals[str(name)] = lowered.get("signal_value", lowered.get("value"))
            context = lowered.get("signal_context") or lowered.get("context")
            if context is not None:
                signals[f"{name}_context"] = context
    else:
        signals = {
            str(k): v for k, v in rows[0].items() if str(k).lower() != "application_id"
        }

    record = {
        "application_id": application_id,
        "signals": signals,
        "source": "aurora.rds_data_api",
        "table": _setting(SIGNAL_TABLE_ENV, DEFAULT_SIGNAL_TABLE),
        "row_shape": shape,
        "rows_read": len(rows),
    }

    try:
        from signal_layer.schema import SignalPayload  # noqa: PLC0415

        return (
            SignalPayload(application_id=application_id, signals=signals),
            record,
            "aurora.rds_data_api",
        )
    except Exception:
        return None, record, "aurora.rds_data_api"


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def output_ddl(table: str | None = None) -> str:
    """The DDL this module would create, generated from the 21-key contract.

    `text` for both enum columns on purpose. The customer's prompt file says the mid-band is
    `MEDIUM RISK` with three recommendation values; their process-guide PDF says
    `POSSIBLE RISK` with four. A CHECK constraint would encode one and reject the other, and
    which is authoritative is still an open question with them -- so the column accepts both
    and the disagreement stays visible instead of becoming a failed insert.
    """
    from agents.contracts import Adjudication  # noqa: PLC0415

    name = _quoted_identifier(table or _setting(OUTPUT_TABLE_ENV, DEFAULT_OUTPUT_TABLE))
    columns = [
        # application_id is NOT one of the 21 keys -- it travels in the transport envelope.
        # It is a column here because a row needs to say which application it adjudicates.
        '  "application_id" text not null',
    ]
    for field, info in Adjudication.model_fields.items():
        # The flags are `Literal[0, 1]`, not `int`, so a substring test on the annotation
        # string produced `text` for all eight of them -- an integer contract silently
        # written into a text column, which the customer's own reporting would then have to
        # cast. Read the Literal's members instead of pattern-matching its repr.
        sql_type = "text"
        annotation = info.annotation
        if get_origin(annotation) is Literal:
            if all(isinstance(v, int) and not isinstance(v, bool) for v in get_args(annotation)):
                sql_type = "integer"
        elif annotation in (int,):
            sql_type = "integer"
        columns.append(f'  "{field}" {sql_type}')
    columns += [
        '  "adjudicated_at" timestamptz not null default now()',
        '  "source" text',
        '  "cost_usd" numeric(12, 6)',
        '  "end_to_end_ms" numeric(12, 1)',
    ]
    return (
        f"create table if not exists {name} (\n"
        + ",\n".join(columns)
        + f",\n  primary key (\"application_id\", \"adjudicated_at\")\n);"
    )


def ensure_output_table(table: str | None = None) -> str:
    """Create the output table if it is absent. Returns the DDL used.

    `create table if not exists`, so an existing table of the customer's own design is left
    exactly as it is -- this must not alter a schema someone else owns.
    """
    ddl = output_ddl(table)
    _execute(ddl)
    return ddl


def persist_adjudication(
    application_id: str,
    adjudication: dict[str, Any],
    *,
    source: str | None = None,
    cost_usd: float | None = None,
    end_to_end_ms: float | None = None,
    table: str | None = None,
) -> dict[str, Any]:
    """Write one adjudication back. The write half of what the customer asked for.

    Columns are taken from the CONTRACT, not from the dict's keys, so an adjudication that
    somehow carried an extra field cannot silently add a column, and the customer's key
    order is preserved in the insert.
    """
    from agents.contracts import Adjudication  # noqa: PLC0415

    name = _quoted_identifier(table or _setting(OUTPUT_TABLE_ENV, DEFAULT_OUTPUT_TABLE))

    columns = ["application_id"]
    parameters: list[dict[str, Any]] = [
        {"name": "application_id", "value": {"stringValue": application_id}}
    ]

    for field in Adjudication.model_fields:
        value = adjudication.get(field)
        columns.append(field)
        if value is None:
            parameters.append({"name": field, "value": {"isNull": True}})
        elif isinstance(value, bool):
            # Before int: bool is a subclass of int in Python, and the flags are 0/1 ints in
            # the contract, so checking int first would write true/false into an integer
            # column on some drivers.
            parameters.append({"name": field, "value": {"longValue": int(value)}})
        elif isinstance(value, int):
            parameters.append({"name": field, "value": {"longValue": value}})
        else:
            parameters.append({"name": field, "value": {"stringValue": str(value)}})

    for extra, value in (
        ("source", source),
        ("cost_usd", cost_usd),
        ("end_to_end_ms", end_to_end_ms),
    ):
        columns.append(extra)
        if value is None:
            parameters.append({"name": extra, "value": {"isNull": True}})
        elif isinstance(value, str):
            parameters.append({"name": extra, "value": {"stringValue": value}})
        else:
            # Numeric via stringValue + a cast: the Data API's doubleValue loses precision
            # on a money column, and a cost of $0.154764 rounding to $0.1548 in the
            # customer's own reporting would be our fault.
            parameters.append({"name": extra, "value": {"stringValue": str(value)}})

    placeholders = ", ".join(
        f":{c}::numeric" if c in ("cost_usd", "end_to_end_ms") else f":{c}" for c in columns
    )
    quoted = ", ".join(f'"{c}"' for c in columns)
    _execute(f"insert into {name} ({quoted}) values ({placeholders})", parameters)  # noqa: S608

    return {
        "written": True,
        "table": table or _setting(OUTPUT_TABLE_ENV, DEFAULT_OUTPUT_TABLE),
        "application_id": application_id,
        "columns": len(columns),
        "contract_keys": len(Adjudication.model_fields),
    }


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------


def probe() -> dict[str, Any]:
    """Answer "can the agents reach this cluster, and can they write to it".

    Called by `deploy/connect-rds.sh` right after the connection details are stored, so a
    cluster without the Data API, a wrong database name or a missing signal table surfaces
    in seconds -- not inside an agent invocation, where it would look like a broken agent.
    """
    result: dict[str, Any] = {"ok": False, "stage": "config", "transport": "rds-data-api"}

    try:
        result["cluster_arn"] = _setting(CLUSTER_ARN_ENV)
        result["database"] = _setting(DATABASE_ENV)
        result["secret_arn"] = _setting(SECRET_ID_ENV)[:60] + "..."
    except AuroraNotConfigured as exc:
        result["error"] = str(exc)
        return result

    result["stage"] = "connect"
    try:
        rows = _rows(_execute("select current_database() as db, version() as version"))
    except AuroraNotConfigured as exc:
        result["error"] = str(exc)
        result["hint"] = (
            "A 'Data API not enabled' error means this is not an Aurora cluster with the "
            "Data API on -- that cluster needs networkMode: VPC instead. A credentials "
            "error means the secret; a database error means AURORA_DATABASE."
        )
        return result

    result["identity"] = rows[0] if rows else None
    result["stage"] = "signal_table"

    signal_table = _setting(SIGNAL_TABLE_ENV, DEFAULT_SIGNAL_TABLE)
    try:
        count = _rows(
            _execute(
                f"select count(*) as n from {_quoted_identifier(signal_table)}"  # noqa: S608
            )
        )
        result["signal_table"] = signal_table
        result["signal_rows"] = count[0].get("n") if count else None
        result["read_ok"] = True
    except AuroraNotConfigured as exc:
        result["signal_table"] = signal_table
        result["read_ok"] = False
        result["read_error"] = str(exc)
        result["hint"] = (
            f"the cluster is reachable but {signal_table!r} is not. Set "
            "AURORA_SIGNAL_TABLE to the table holding the precomputed signals."
        )
        # Reachability is the harder half; a missing table is a name, so this is still a
        # useful partial result rather than a failure.
        result["ok"] = False
        result["stage"] = "signal_table"
        return result

    result["stage"] = "write"
    try:
        ddl = ensure_output_table()
        result["output_table"] = _setting(OUTPUT_TABLE_ENV, DEFAULT_OUTPUT_TABLE)
        result["output_ddl"] = ddl
        result["write_ok"] = True
    except AuroraNotConfigured as exc:
        result["write_ok"] = False
        result["write_error"] = str(exc)
        result["hint"] = (
            "reads work but the adjudication table could not be created. The role in the "
            "secret may lack CREATE, in which case create the table from `output_ddl` by "
            "hand and re-probe."
        )
        return result

    result["ok"] = True
    result["stage"] = "done"
    return result
