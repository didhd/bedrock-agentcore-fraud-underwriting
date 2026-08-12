"""Cortex Analyst as a signal source, reached with a Snowflake PAT and nothing else.

WHAT THE CUSTOMER HAS TO SUPPLY

One secret. `deploy/connect-snowflake.sh` writes it and verifies it against their account
before anything else is deployed, so "does the key work" is answered in seconds rather than
discovered inside an agent invocation:

    {
      "account_url":   "https://<org>-<account>.snowflakecomputing.com",
      "pat":           "<programmatic access token>",
      "semantic_view": "FRAUD_CONSORTIUM_SEMANTIC_VIEW",
      "warehouse":     "<warehouse for executing the generated SQL>"
    }

Everything else -- the IAM role, the Secrets Manager grant, the mode switch -- is already
deployed. `SIGNAL_MODE=cortex` is the only other change, and it is one environment variable
on the runtime.

WHY A PAT AND NOT SIGV4, WHICH THE OLD DESIGN DOC CLAIMS

`docs/semantic-layer-mcp.md` shows `AgentCore Gateway -> Snowflake (auth: SigV4 / OAuth)`.
That cannot work: SigV4 is verified by AWS services -- AgentCore Gateway and Runtime, API
Gateway, Lambda function URLs -- and Snowflake is not one of them. There is nothing on the
Snowflake side to check an AWS signature. Snowflake's own AgentCore guide uses a
programmatic access token presented as a bearer/API-key header, which is what this does.

WHY TWO CALLS AND NOT ONE

The same doc treats `cortex_analyst_query(semantic_view=..., question=...)` as a tool that
returns rows. Two things are wrong with that. The tool name is user-defined on Snowflake's
managed MCP server (the fixed part is `type: "CORTEX_ANALYST_MESSAGE"`) and it takes a
single `message` argument with the semantic view bound server-side. And Cortex Analyst
`/api/v2/cortex/analyst/message` returns **generated SQL, not rows** -- so a second call to
the SQL API executes it. Both calls are here, in that order, because assuming one call
returns data is the specific mistake that made the previous integration fictional.

WHAT IS VERIFIED AND WHAT IS NOT

Verified: the credential path, the request shapes against Snowflake's published API, and
that the whole module is unreachable until `SIGNAL_MODE=cortex`.

NOT verified: a live round trip. There is no Snowflake account on this side to test
against, so the first real call happens on the customer's key. `probe()` exists for exactly
that moment -- it authenticates and lists the semantic view's dimensions without running an
agent, so a wrong warehouse or an expired token surfaces as one clear error instead of a
failed adjudication.
"""

from __future__ import annotations

import json
import os
from typing import Any, Final

#: Secrets Manager secret holding everything Snowflake-side. One secret, not four
#: environment variables: a PAT is a credential and belongs somewhere rotatable and
#: audited, and keeping the account URL beside it means there is exactly one thing to hand
#: over.
SECRET_ID_ENV: Final[str] = "SNOWFLAKE_SECRET_ID"
DEFAULT_SECRET_ID: Final[str] = "pp-fraud/snowflake"

#: Which of the customer's own guardrails the semantic view is expected to enforce. Listed
#: so a live response can be checked against what was promised rather than trusted --
#: these are the rules the migration must NOT lose, and they live in Snowflake precisely so
#: nothing here reimplements them.
EXPECTED_GUARDRAILS: Final[tuple[str, ...]] = (
    "hashed SSN references only, never raw SSN values",
    "join on application_id AND hash_lender_id",
    "exclude fake and test records",
    "normalise lender names before matching",
    "standardised address keys",
    "active dealers only",
    "invalid credit-report values treated as null",
)

_ANALYST_PATH: Final[str] = "/api/v2/cortex/analyst/message"
_SQL_PATH: Final[str] = "/api/v2/statements"


class SnowflakeNotConfigured(RuntimeError):
    """Raised when `SIGNAL_MODE=cortex` but the secret is absent or incomplete.

    Deliberately loud. A silent fall back to fixtures would mean an adjudication computed
    from demo data while everyone believed it came from the consortium -- the one failure
    this port must never produce.
    """


def secret_id() -> str:
    return os.environ.get(SECRET_ID_ENV) or DEFAULT_SECRET_ID


_CACHE: dict[str, Any] | None = None


def config() -> dict[str, Any]:
    """The Snowflake connection settings, from Secrets Manager.

    Cached for the container's lifetime. A PAT is long-lived enough that fetching it per
    application would add a Secrets Manager call to the per-application path for no
    benefit; rotation takes effect on the next cold start, which is the normal trade for
    this kind of credential.
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    import boto3  # noqa: PLC0415

    name = secret_id()
    try:
        client = boto3.client(
            "secretsmanager", region_name=os.environ.get("AWS_REGION", "us-west-2")
        )
        raw = client.get_secret_value(SecretId=name)["SecretString"]
    except Exception as exc:  # noqa: BLE001
        raise SnowflakeNotConfigured(
            f"SIGNAL_MODE=cortex but secret {name!r} could not be read: "
            f"{type(exc).__name__}: {exc}. Run ./deploy/connect-snowflake.sh to create it."
        ) from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SnowflakeNotConfigured(f"secret {name!r} is not JSON") from exc

    missing = [k for k in ("account_url", "pat", "semantic_view") if not parsed.get(k)]
    if missing:
        raise SnowflakeNotConfigured(
            f"secret {name!r} is missing: {', '.join(missing)}. "
            "Run ./deploy/connect-snowflake.sh to set it."
        )

    parsed["account_url"] = parsed["account_url"].rstrip("/")
    _CACHE = parsed
    return parsed


def _post(path: str, body: dict[str, Any], timeout: int = 60) -> tuple[int, dict[str, Any]]:
    """One authenticated POST to Snowflake.

    `urllib` rather than `requests`: this runs inside the AgentCore runtime whose closure is
    pinned, and adding an HTTP library for two calls is a dependency to keep current for no
    capability. The PAT goes in `Authorization` as a bearer token with
    `X-Snowflake-Authorization-Token-Type: PROGRAMMATIC_ACCESS_TOKEN`, which is what makes
    Snowflake treat it as a PAT rather than an OAuth token.
    """
    import urllib.error  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    settings = config()
    request = urllib.request.Request(
        f"{settings['account_url']}{path}",
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {settings['pat']}",
            "X-Snowflake-Authorization-Token-Type": "PROGRAMMATIC_ACCESS_TOKEN",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:600]
        try:
            return exc.code, json.loads(detail or "{}")
        except json.JSONDecodeError:
            return exc.code, {"error": detail}


def ask_analyst(question: str) -> dict[str, Any]:
    """Cortex Analyst call one: natural language in, GENERATED SQL out.

    Returns `{"sql": ..., "text": ..., "raw": ...}`. It does NOT return rows, and treating
    it as if it did is the mistake that made the previous integration fictional.
    """
    status, payload = _post(
        _ANALYST_PATH,
        {
            "messages": [{"role": "user", "content": [{"type": "text", "text": question}]}],
            "semantic_view": config()["semantic_view"],
        },
    )
    if status >= 400:
        raise SnowflakeNotConfigured(f"Cortex Analyst returned {status}: {payload}")

    sql, text = None, []
    for item in (payload.get("message") or {}).get("content", []) or []:
        if item.get("type") == "sql":
            sql = item.get("statement")
        elif item.get("type") == "text":
            text.append(item.get("text", ""))
    return {"sql": sql, "text": "\n".join(text), "raw": payload}


def run_sql(statement: str) -> list[dict[str, Any]]:
    """Cortex Analyst call two: execute the generated SQL and return rows.

    A separate API. The rows come back as positional arrays with the column names in the
    result-set metadata, so they are zipped into dicts here -- an agent reading
    `data[0][3]` would be one column reorder away from silently analysing the wrong field.
    """
    settings = config()
    body: dict[str, Any] = {"statement": statement, "timeout": 60}
    if settings.get("warehouse"):
        body["warehouse"] = settings["warehouse"]

    status, payload = _post(_SQL_PATH, body)
    if status >= 400:
        raise SnowflakeNotConfigured(f"Snowflake SQL API returned {status}: {payload}")

    columns = [
        c.get("name") for c in (payload.get("resultSetMetaData") or {}).get("rowType", []) or []
    ]
    return [dict(zip(columns, row)) for row in payload.get("data", []) or []]


def probe() -> dict[str, Any]:
    """Answer "does the key work" without running an agent.

    This is what `deploy/connect-snowflake.sh` calls after writing the secret. A wrong
    warehouse, an expired PAT or a semantic view the role cannot see then surfaces as one
    clear error, seconds after the key is pasted, instead of as a failed adjudication in
    front of the customer.
    """
    try:
        settings = config()
    except SnowflakeNotConfigured as exc:
        return {"ok": False, "stage": "secret", "error": str(exc)}

    try:
        rows = run_sql("select current_account() as account, current_role() as role, current_warehouse() as warehouse")
    except SnowflakeNotConfigured as exc:
        return {
            "ok": False,
            "stage": "sql_api",
            "error": str(exc),
            "hint": (
                "the PAT authenticated or it did not -- a 401 means the token, a 404 means "
                "the account URL, and a warehouse error means the `warehouse` field"
            ),
        }

    result: dict[str, Any] = {
        "ok": True,
        "stage": "sql_api",
        "account_url": settings["account_url"],
        "semantic_view": settings["semantic_view"],
        "identity": rows[0] if rows else None,
    }

    # The semantic view is the whole point -- it carries the guardrails -- so a probe that
    # only proved SQL works would miss the thing most likely to be misconfigured.
    try:
        analyst = ask_analyst(
            f"List the dimensions available in {settings['semantic_view']}."
        )
        result["analyst_reachable"] = True
        result["generated_sql_preview"] = (analyst.get("sql") or "")[:300] or None
    except SnowflakeNotConfigured as exc:
        result["analyst_reachable"] = False
        result["analyst_error"] = str(exc)
        result["ok"] = False
        result["stage"] = "cortex_analyst"

    result["guardrails_expected_in_the_view"] = list(EXPECTED_GUARDRAILS)
    return result


def build_cortex_payload(application_id: str) -> tuple[Any, dict[str, Any] | None, str]:
    """One application's governed signals, from the semantic view.

    Same return shape as the fixtures path -- ``(signal_payload, raw_record, source)`` --
    because that is the seam's whole contract: the eight specialists and the adjudication
    are downstream of it and must not know which source answered.

    THE TWO CALLS ARE VISIBLE HERE ON PURPOSE. Cortex Analyst turns the question into SQL;
    the SQL API runs it. A single-call version of this function would be the fictional
    integration again.

    UNTESTED AGAINST A LIVE ACCOUNT. There is no Snowflake here to call, so the first real
    execution is on the customer's key. ``probe()`` is what makes that safe: it authenticates
    and exercises the semantic view before any agent runs, so a bad warehouse or an expired
    PAT surfaces as one error rather than as a failed adjudication. The projection below is
    also the part most likely to need a shape adjustment on first contact -- the customer's
    view decides its own column names, and this asks for signals plus their paired
    ``_context`` because that pairing is what the specialists' anti-false-positive rules
    read.
    """
    settings = config()
    question = (
        f"Return every fraud signal and its paired _context field for application "
        f"{application_id}. Include the alert ids that fired. One row per signal."
    )

    analyst = ask_analyst(question)
    sql = analyst.get("sql")
    if not sql:
        raise SnowflakeNotConfigured(
            "Cortex Analyst returned no SQL for "
            f"{application_id}: {analyst.get('text')!r}. The semantic view may not expose "
            "this application, or the question needs adjusting for their column names."
        )

    rows = run_sql(sql)
    if not rows:
        raise SnowflakeNotConfigured(
            f"the semantic view returned no rows for {application_id}. Generated SQL: "
            f"{sql[:300]}"
        )

    # Flatten `[{signal: name, value: v, context: c}, ...]` into the mapping the projection
    # layer expects. The exact column names are the customer's, so this is deliberately
    # tolerant about which of several plausible names carries each part rather than assuming
    # one and failing opaquely.
    signals: dict[str, Any] = {}
    #: Kept separate from the signals: `domain_view` reads the two independently, and the
    #: anti-false-positive rules ARE the comparison between a signal and its context.
    context: dict[str, Any] = {}
    for row in rows:
        lowered = {str(k).lower(): v for k, v in row.items()}
        name = lowered.get("signal") or lowered.get("signal_name") or lowered.get("name")
        if not name:
            continue
        signals[str(name)] = lowered.get("value", lowered.get("signal_value"))
        paired = lowered.get("context") or lowered.get("signal_context")
        if paired is not None:
            context[f"{name}_context"] = paired

    record = {
        "application_id": application_id,
        "record_id": application_id,
        # The semantic view decides its own column names, so these are the fields
        # `payload_from_record` requires and the projection reads. Empty here rather than
        # invented: a missing `application` block fails loudly at assembly with the field
        # named, which is what the first live call needs to tell us.
        "application": {},
        "signals": signals,
        "context": context,
        "alerts_fired": [],
        "semantic_layer_rev": "",
        "computed_at": "",
        "source": "snowflake.cortex_analyst",
        "semantic_view": settings["semantic_view"],
        # Kept with the data so an operator can see which SQL produced an adjudication.
        # The guardrails are IN this SQL, which is the argument for leaving the semantic
        # layer in Snowflake rather than porting it.
        "generated_sql": sql,
        "guardrails_enforced_by_the_view": list(EXPECTED_GUARDRAILS),
    }

    # ASSEMBLED BY THE ONE ASSEMBLER, and NOT wrapped in a try/except.
    #
    # This used to be `SignalPayload(application_id=..., signals=...)` inside a bare
    # `except Exception: return None`. That shape does not exist -- the real one takes
    # record_id / hash_lender_id / application / views -- so the constructor raised TypeError
    # and the except turned it into None. The identical bug in the Aurora source produced
    # "no signals for APP-1004" from all eight specialists while the config panel reported the
    # cluster connected, and the read had worked perfectly. Fixed there and here.
    #
    # UNTESTED AGAINST A LIVE ACCOUNT, and this is the part most likely to need a shape
    # adjustment on first contact: `payload_from_record` requires every registry signal, the
    # paired `<signal>_context` for each, `alerts_fired`, and an `application` block. A
    # semantic view that returns only signals will raise here naming exactly what is absent,
    # which is the useful failure -- far better than a None that reads as an empty database.
    from fixtures.loader import payload_from_record  # noqa: PLC0415

    return payload_from_record(record), record, "snowflake.cortex"
