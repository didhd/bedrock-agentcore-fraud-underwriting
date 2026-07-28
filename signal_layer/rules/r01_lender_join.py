"""
General Rule 1 — the delink_lender_table join, with all three parts enforced.

The customer writes one sentence; it contains three independent guardrails, and dropping
any one of them silently corrupts every count the agents report:

  INNER      an outer join keeps applications whose hash_lender_id resolves to nothing,
             and re-admits the TEST lenders the predicate just removed as NULL-lender
             rows. The customer says "inner join".
  NOT LIKE   test lenders are live rows in delink_lender_table; without the predicate
  '%TEST%'   their applications are analysed as real fraud.
  DISTINCT   delink_lender_table holds more than one row per hash_lender_id. Without
             DISTINCT the join fans out and every per-lender application count inflates
             by the duplicate multiplicity. This is the failure that is invisible in a
             spot check and wrong in every aggregate.

So this module does not just carry the SQL text: it applies the join to rows, which is
what lets a test prove the three parts individually.
"""

from __future__ import annotations

import re
from typing import Any, Final, Iterable, Iterator, Mapping, Sequence

from signal_layer.rules._source import rule_text

RULE_ID: Final = "1"
RULE_TEXT: Final = rule_text(RULE_ID)

#: The lender-resolution subquery, transcribed from the rule. This is the only form the
#: query builder may emit; the agent never authors it.
LENDER_SUBQUERY_SQL: Final = (
    "select distinct lender_name as lender_id, hash_lender_id\n"
    "from delink_lender_table\n"
    "where lender_name not like '%TEST%'"
)

#: The same subquery as a named CTE, for readability in generated SQL.
LENDER_CTE_SQL: Final = f"lenders as (\n{LENDER_SUBQUERY_SQL}\n)"

#: The single column the join predicate uses, per "You should use hash_lender_id from
#: each table to join them".
JOIN_COLUMN: Final = "hash_lender_id"

#: The two fact tables the rule names as the left side of this join.
APPLICATION_TABLES: Final[tuple[str, ...]] = (
    "all_records_all_all_records_for_pg",
    "tbl_auto_consortium",
)

#: The customer's exclusion predicate, as a Python substring test on the *uppercased*
#: lender name. SQL LIKE is case-sensitive in Snowflake for a case-sensitive collation,
#: but the customer's lender names are stored uppercase and their nickname table (Rule
#: 1.5) is uppercase, so matching uppercase is the faithful reading.
TEST_LENDER_MARKER: Final = "TEST"


class LenderJoinError(ValueError):
    """A lender join was requested or generated in a form General Rule 1 forbids."""


def is_test_lender(lender_name: str | None) -> bool:
    """
    True when a delink_lender_table row is a test lender and must be dropped.

    General Rule 1: ``where lender_name not like '%TEST%'``. NULL lender_name is not a
    test lender by this predicate — but note it is dropped anyway by the INNER join,
    because a row with no lender_name produces no usable lender_id.
    """
    if lender_name is None:
        return False
    return TEST_LENDER_MARKER in lender_name.upper()


def resolve_lenders(
    lender_rows: Iterable[Mapping[str, Any]],
) -> dict[str, str]:
    """
    Apply the rule's subquery to delink_lender_table rows.

    Implements ``select distinct lender_name as lender_id, hash_lender_id from
    delink_lender_table where lender_name not like '%TEST%'`` (General Rule 1) and
    returns {hash_lender_id: lender_id}.

    The dict return type *is* the DISTINCT: one hash_lender_id resolves to exactly one
    lender, so the downstream join cannot fan out. Duplicate rows that agree collapse
    silently, as SELECT DISTINCT would. Duplicate rows that *disagree* — the same
    hash_lender_id carrying two different lender_names — are not something DISTINCT can
    reconcile, and would make the join non-deterministic, so they raise rather than
    letting an arbitrary winner through.
    """
    resolved: dict[str, str] = {}
    for row in lender_rows:
        name = row.get("lender_name")
        hash_id = row.get(JOIN_COLUMN)
        if hash_id is None or name is None:
            continue
        if is_test_lender(name):
            continue
        previous = resolved.get(hash_id)
        if previous is not None and previous != name:
            raise LenderJoinError(
                f"{JOIN_COLUMN}={hash_id!r} maps to two lender names "
                f"({previous!r} and {name!r}); SELECT DISTINCT cannot make this join "
                "deterministic and the fan-out General Rule 1 guards against is real"
            )
        resolved[hash_id] = name
    return resolved


def inner_join_lenders(
    application_rows: Iterable[Mapping[str, Any]],
    lender_rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """
    Join application rows to resolved lenders exactly as General Rule 1 requires.

    Verbatim rule: "When you join all_records_all_all_records_for_pg or
    tbl_auto_consortium to delink_lender_table, please do an inner join on (select
    distinct lender_name as lender_id, hash_lender_id from delink_lender_table where
    lender_name not like '%TEST%')...You should use hash_lender_id from each table to
    join them...."

    INNER semantics: an application whose hash_lender_id is absent from the resolved
    set — because the lender was a TEST lender, or is simply unknown — is dropped, not
    emitted with a null lender. Row count out is <= row count in, never greater.
    """
    lenders = resolve_lenders(lender_rows)
    out: list[dict[str, Any]] = []
    for row in application_rows:
        lender_id = lenders.get(row.get(JOIN_COLUMN))
        if lender_id is None:
            continue
        joined = dict(row)
        joined["lender_id"] = lender_id
        out.append(joined)
    return out


def build_lender_join_sql(application_table: str, *, alias: str = "v") -> str:
    """
    Emit the canonical INNER JOIN for one of the two application tables (Rule 1).

    Raises LenderJoinError for any other table: the rule names exactly two left-hand
    tables, and General Rule 8 forbids the deprecated all-records variants outright.
    """
    if application_table.lower() not in APPLICATION_TABLES:
        raise LenderJoinError(
            f"General Rule 1 covers {APPLICATION_TABLES}, not {application_table!r}"
        )
    return (
        f"from {application_table} {alias}\n"
        f"inner join (\n{LENDER_SUBQUERY_SQL}\n) l\n"
        f"  on l.{JOIN_COLUMN} = {alias}.{JOIN_COLUMN}"
    )


_DELINK: Final = re.compile(r"\bdelink_lender_table\b", re.IGNORECASE)
_INNER_JOIN: Final = re.compile(r"\binner\s+join\b", re.IGNORECASE)
_OUTER_JOIN: Final = re.compile(r"\b(?:left|right|full)\s+(?:outer\s+)?join\b", re.IGNORECASE)
_DISTINCT: Final = re.compile(r"\bselect\s+distinct\b", re.IGNORECASE)
_TEST_PREDICATE: Final = re.compile(
    r"lender_name\s+not\s+like\s+'%TEST%'", re.IGNORECASE
)
_JOIN_ON_HASH: Final = re.compile(
    r"\bon\b[^;]*?\bhash_lender_id\b", re.IGNORECASE | re.DOTALL
)


def validate_lender_join_sql(sql: str) -> None:
    """
    Reject generated SQL that touches delink_lender_table without all three of General
    Rule 1's guardrails.

    A no-op when the SQL does not reference delink_lender_table at all. Raises
    LenderJoinError listing every missing part, so one failure message tells the whole
    story instead of one part per run.
    """
    if not _DELINK.search(sql):
        return
    problems: list[str] = []
    if not _INNER_JOIN.search(sql):
        problems.append("no INNER JOIN (rule 1 says 'please do an inner join')")
    if _OUTER_JOIN.search(sql):
        problems.append(
            "an outer join is present; it re-admits unmatched and TEST lenders as "
            "NULL-lender rows"
        )
    if not _DISTINCT.search(sql):
        problems.append(
            "no SELECT DISTINCT in the lender subquery; the join will fan out and "
            "inflate application counts"
        )
    if not _TEST_PREDICATE.search(sql):
        problems.append("no lender_name not like '%TEST%' predicate")
    if not _JOIN_ON_HASH.search(sql):
        problems.append(f"join predicate does not use {JOIN_COLUMN}")
    if problems:
        raise LenderJoinError(
            "General Rule 1 violated: " + "; ".join(problems) + f"\nRule: {RULE_TEXT}"
        )


def iter_test_lenders(
    lender_rows: Iterable[Mapping[str, Any]],
) -> Iterator[Mapping[str, Any]]:
    """Yield the delink_lender_table rows General Rule 1 excludes. For audit output."""
    for row in lender_rows:
        if is_test_lender(row.get("lender_name")):
            yield row


def assert_no_fanout(
    before: Sequence[Any], after: Sequence[Any], *, context: str = "lender join"
) -> None:
    """
    Assert the DISTINCT half of General Rule 1 held: a lender join must never produce
    more rows than it consumed.

    The check that catches the silent version of this bug, where every count is inflated
    by a duplicate multiplicity nobody notices because each individual row looks right.
    """
    if len(after) > len(before):
        raise LenderJoinError(
            f"{context} fanned out: {len(before)} rows in, {len(after)} rows out. "
            "General Rule 1 requires SELECT DISTINCT on the lender subquery."
        )


__all__ = [
    "APPLICATION_TABLES",
    "JOIN_COLUMN",
    "LENDER_CTE_SQL",
    "LENDER_SUBQUERY_SQL",
    "LenderJoinError",
    "RULE_ID",
    "RULE_TEXT",
    "TEST_LENDER_MARKER",
    "assert_no_fanout",
    "build_lender_join_sql",
    "inner_join_lenders",
    "is_test_lender",
    "iter_test_lenders",
    "resolve_lenders",
    "validate_lender_join_sql",
]
