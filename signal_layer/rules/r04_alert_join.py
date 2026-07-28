"""
General Rules 4 and 5 — the composite application grain.

Both rules say the same thing about a different table, and rule 5 supplies the reason:
**"This is because the same application can be sent to different lenders (this is
normal)."** In indirect auto lending a dealer shops one application to several lenders, so
``application_id`` is not unique. Joining on it alone cross-multiplies one application's
alerts across every lender that received it. The alert count inflates, the identity agent
sees a cluster that does not exist, and the master synthesiser adjudicates on it.

Rule 5 also carries a second, easily-missed instruction: because multi-lender submission is
*normal*, the agent must not report it as velocity or as duplicate-application fraud. That
is why ``submitted_to_lender_count()`` exists — the de-duplication belongs here, not in the
agent's reasoning.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Final, Iterable, Mapping, Sequence

from signal_layer.rules._source import rule_text

RULE_ID: Final = "4"
RULE_TEXT: Final = rule_text(RULE_ID)
RULE_05_ID: Final = "5"
RULE_05_TEXT: Final = rule_text(RULE_05_ID)

#: The composite primary key of the application grain, everywhere downstream.
APPLICATION_GRAIN: Final[tuple[str, ...]] = ("application_id", "hash_lender_id")

#: Rule 5 permits either lender identifier ("join on application_id but also on
#: lender_id or hash_lender_id"); hash_lender_id is preferred because General Rule 1
#: makes it the join column against delink_lender_table.
LENDER_KEY_ALIASES: Final[tuple[str, ...]] = ("hash_lender_id", "lender_id")

#: The alert table General Rule 4 names.
ALERT_TABLE: Final = "tbl_afa_alerts_clean"

#: The two application tables both rules name.
APPLICATION_TABLES: Final[tuple[str, ...]] = (
    "all_records_all_all_records_for_pg",
    "tbl_auto_consortium",
)


class JoinKeyError(ValueError):
    """A join or lookup was attempted at a grain General Rules 4 and 5 forbid."""


def join_keys(row: Mapping[str, Any]) -> tuple[Any, Any]:
    """
    Extract the composite join key from a row (General Rules 4 and 5).

    Verbatim rule 4: "Whenever you connect the tbl_afa_alerts_clean table to the all
    records for pg or tbl_auto_consortium table please join by application_id AND
    hash_lender_id."

    Verbatim rule 5: "Whenever you have to retrieve application-related data, please
    make sure to join on application_id but also on lender_id or hash_lender_id. This is
    because the same application can be sent to different lenders (this is normal)."

    Accepts either lender alias. Raises JoinKeyError when the lender half is missing —
    the whole point of both rules is that a row without a lender identifier cannot be
    placed at the application grain, so returning a one-part key would defeat them.
    """
    if "application_id" not in row or row["application_id"] is None:
        raise JoinKeyError(
            "row has no application_id; General Rules 4 and 5 require "
            f"{APPLICATION_GRAIN} as the join key"
        )
    for alias in LENDER_KEY_ALIASES:
        if row.get(alias) is not None:
            return (row["application_id"], row[alias])
    raise JoinKeyError(
        f"row {row.get('application_id')!r} has no lender identifier "
        f"(one of {LENDER_KEY_ALIASES} is required). General Rule 5: {RULE_05_TEXT}"
    )


def require_composite_key(
    application_id: str | None, lender_id: str | None = None, **aliases: Any
) -> tuple[str, str]:
    """
    Validate a retrieval request carries both halves of the grain (General Rule 5).

    Make the composite grain unavoidable at the interface: a retrieval entry point should
    call this first, so a single-key lookup fails at the boundary rather than returning
    silently-fanned-out rows. ``aliases`` accepts hash_lender_id= for callers that use
    that name.
    """
    lender = lender_id
    if lender is None:
        for alias in LENDER_KEY_ALIASES:
            if aliases.get(alias) is not None:
                lender = aliases[alias]
                break
    if not application_id:
        raise JoinKeyError("application_id is required (General Rules 4 and 5)")
    if not lender:
        raise JoinKeyError(
            f"a lender identifier ({' or '.join(LENDER_KEY_ALIASES)}) is required "
            f"alongside application_id={application_id!r}. General Rule 5: {RULE_05_TEXT}"
        )
    return (application_id, lender)


def join_alerts(
    application_rows: Iterable[Mapping[str, Any]],
    alert_rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """
    Join tbl_afa_alerts_clean rows to application rows on the composite key (Rule 4).

    Returns one dict per application row with an "alerts" list holding only the alerts
    belonging to that (application_id, hash_lender_id) pair. Contrast the single-key
    join, which would attach lender B's alerts to lender A's copy of the same
    application: see the docstring of assert_composite_join() for the count comparison
    that proves it.
    """
    index: dict[tuple[Any, Any], list[Mapping[str, Any]]] = defaultdict(list)
    for alert in alert_rows:
        index[join_keys(alert)].append(alert)
    out: list[dict[str, Any]] = []
    for row in application_rows:
        key = join_keys(row)
        joined = dict(row)
        joined["alerts"] = list(index.get(key, ()))
        out.append(joined)
    return out


def join_alerts_by_application_only(
    application_rows: Iterable[Mapping[str, Any]],
    alert_rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """
    The WRONG join, kept so a test can measure how wrong (General Rule 4).

    Joins on application_id alone. Never call this in production code — it exists purely
    so tests/test_rules.py can assert the correct join returns fewer alerts, which is the
    only way to demonstrate that the composite key is load-bearing rather than decorative.
    """
    index: dict[Any, list[Mapping[str, Any]]] = defaultdict(list)
    for alert in alert_rows:
        index[alert.get("application_id")].append(alert)
    out: list[dict[str, Any]] = []
    for row in application_rows:
        joined = dict(row)
        joined["alerts"] = list(index.get(row.get("application_id"), ()))
        out.append(joined)
    return out


def submitted_to_lender_count(
    application_rows: Iterable[Mapping[str, Any]], application_id: str
) -> int:
    """
    Count the distinct lenders one application was sent to (General Rule 5).

    Rule 5 states multi-lender submission "is normal", so this count must be reported as
    reach, never as application velocity. The bustout orchestration says the same thing
    from the other direction: multiple same-day applications to multiple lenders for the
    SAME vehicle is "very normal" in indirect auto lending; only different vehicles across
    different lenders is "much riskier".
    """
    lenders: set[Any] = set()
    for row in application_rows:
        if row.get("application_id") != application_id:
            continue
        for alias in LENDER_KEY_ALIASES:
            if row.get(alias) is not None:
                lenders.add(row[alias])
                break
    return len(lenders)


def deduplicate_applications(
    application_rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """
    Collapse an application's per-lender copies to one row carrying the lender fan-out
    as data (General Rule 5).

    Emits one row per application_id with ``lender_count`` and sorted ``lenders``, so an
    agent counting applications counts applications rather than submissions. Preserves
    first-seen order for deterministic output.
    """
    grouped: dict[Any, dict[str, Any]] = {}
    for row in application_rows:
        app_id = row.get("application_id")
        if app_id is None:
            raise JoinKeyError("row has no application_id (General Rules 4 and 5)")
        lender = next(
            (row[a] for a in LENDER_KEY_ALIASES if row.get(a) is not None), None
        )
        entry = grouped.setdefault(app_id, {**dict(row), "lenders": set()})
        if lender is not None:
            entry["lenders"].add(lender)
    out: list[dict[str, Any]] = []
    for entry in grouped.values():
        lenders = sorted(entry.pop("lenders"), key=str)
        entry["lenders"] = lenders
        entry["lender_count"] = len(lenders)
        out.append(entry)
    return out


_ALERT_TABLE_REF: Final = re.compile(rf"\b{ALERT_TABLE}\b", re.IGNORECASE)
_APP_ID_EQ: Final = re.compile(
    r"\b\w*\.?application_id\s*=\s*\w*\.?application_id\b", re.IGNORECASE
)
_LENDER_EQ: Final = re.compile(
    r"\b\w*\.?(?:hash_lender_id|lender_id)\s*=\s*\w*\.?(?:hash_lender_id|lender_id)\b",
    re.IGNORECASE,
)
# USING (application_id, hash_lender_id) is an equally valid spelling of the composite key
# and must not be rejected just because the query builder happens to emit the ON form.
_USING: Final = re.compile(r"\busing\s*\(([^)]*)\)", re.IGNORECASE)


def validate_join_sql(sql: str) -> None:
    """
    Reject generated SQL that joins the alert table on a single key (General Rule 4).

    A no-op when the SQL does not reference tbl_afa_alerts_clean. Raises JoinKeyError
    naming the missing predicate. This is the machine-checkable half of rules 4 and 5 and
    belongs in the query-validation path, not in a prompt line.

    Accepts both spellings of the composite key: explicit ``ON a.x = v.x AND a.y = v.y``, and
    ``USING (application_id, hash_lender_id)``. Rejecting the USING form would be a false
    positive, and a validator with false positives gets switched off.
    """
    if not _ALERT_TABLE_REF.search(sql):
        return
    has_app = bool(_APP_ID_EQ.search(sql))
    has_lender = bool(_LENDER_EQ.search(sql))
    for match in _USING.finditer(sql):
        columns = {c.strip().lower() for c in match.group(1).split(",")}
        has_app = has_app or "application_id" in columns
        has_lender = has_lender or bool(columns & {"hash_lender_id", "lender_id"})
    if has_app and has_lender:
        return
    missing = []
    if not has_app:
        missing.append("application_id equality")
    if not has_lender:
        missing.append("hash_lender_id (or lender_id) equality")
    raise JoinKeyError(
        f"SQL joins {ALERT_TABLE} without {' and '.join(missing)}. "
        "Joining on application_id alone cross-multiplies one application's alerts "
        f"across every lender that received it.\nGeneral Rule 4: {RULE_TEXT}"
    )


def assert_composite_join(
    single_key_result: Sequence[Mapping[str, Any]],
    composite_result: Sequence[Mapping[str, Any]],
) -> None:
    """
    Assert the composite join did not admit more alerts than the single-key join.

    The invariant that makes General Rule 4 testable: correcting the join can only ever
    *remove* mis-attributed alerts. If the composite result carries more, the join keys
    are wrong somewhere.
    """
    single = sum(len(r.get("alerts", ())) for r in single_key_result)
    composite = sum(len(r.get("alerts", ())) for r in composite_result)
    if composite > single:
        raise JoinKeyError(
            f"composite join produced MORE alerts ({composite}) than the single-key "
            f"join ({single}); the composite key must only ever remove "
            "mis-attributed alerts"
        )


__all__ = [
    "ALERT_TABLE",
    "APPLICATION_GRAIN",
    "APPLICATION_TABLES",
    "JoinKeyError",
    "LENDER_KEY_ALIASES",
    "RULE_05_ID",
    "RULE_05_TEXT",
    "RULE_ID",
    "RULE_TEXT",
    "assert_composite_join",
    "deduplicate_applications",
    "join_alerts",
    "join_alerts_by_application_only",
    "join_keys",
    "require_composite_key",
    "submitted_to_lender_count",
    "validate_join_sql",
]
