"""
General Rule 8 — the deprecated all-records tables.

The customer names two tables that must no longer be queried and one that replaces them. The
right enforcement is *by exposure*: the semantic view should not surface the deprecated tables
at all, so an agent cannot reference what it cannot see. This module is the second line — a
denylist assertion in the query path for when a hand-written query or a misconfigured semantic
view reaches through.

It raises rather than silently rewriting the table name. A silent rewrite would produce a
correct-looking answer while hiding the fact that the semantic layer is misconfigured, and the
misconfiguration is the thing that needs fixing.

Note the customer's own inconsistent underscoring: ``ALL_RECORDS_ALL_ALL_RECORDS_CLEAN_PERF``
has a single underscore where ``ALL_RECORDS_ALL__ALL_RECORDS_CLEAN`` has two. Matching literal
strings would miss a one-underscore typo variant, so matching is done on a form with runs of
underscores collapsed.
"""

from __future__ import annotations

import re
from typing import Final

from signal_layer.rules._source import rule_text

RULE_ID: Final = "8"
RULE_TEXT: Final = rule_text(RULE_ID)

#: The two deprecated tables, spelled exactly as the customer spells them (note the
#: inconsistent underscore counts, which are theirs).
DEPRECATED_TABLES: Final[tuple[str, ...]] = (
    "ALL_RECORDS_ALL_ALL_RECORDS_CLEAN_PERF",
    "ALL_RECORDS_ALL__ALL_RECORDS_CLEAN",
)

#: The replacement table.
APPROVED_ALL_RECORDS_TABLE: Final = "ALL_RECORDS_ALL__ALL_RECORDS_FOR_PG"

UNDERSCORE_NOTE: Final = (
    "The customer writes ALL_RECORDS_ALL_ALL_RECORDS_CLEAN_PERF with a single underscore "
    "after the first ALL and ALL_RECORDS_ALL__ALL_RECORDS_CLEAN with two. Matching literal "
    "strings would let a one-underscore typo variant through, so both the query text and "
    "the denylist are compared with runs of underscores collapsed to one."
)


class DeprecatedTableError(ValueError):
    """Generated SQL references a table General Rule 8 retired."""


def _collapse(name: str) -> str:
    """Normalise a table name for comparison: uppercase, underscore runs collapsed."""
    return re.sub(r"_+", "_", name.upper())


#: The deprecated names in collapsed form. Note both tables collapse to distinct values,
#: and neither collides with the approved table.
_DENY: Final[frozenset[str]] = frozenset(_collapse(t) for t in DEPRECATED_TABLES)

_IDENTIFIER: Final = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


def is_deprecated_table(name: str) -> bool:
    """
    Whether a table name is one General Rule 8 retired.

    Verbatim: "Please no longer use the ALL_RECORDS_ALL_ALL_RECORDS_CLEAN_PERF OR
    ALL_RECORDS_ALL__ALL_RECORDS_CLEAN table for queries. Use
    ALL_RECORDS_ALL__ALL_RECORDS_FOR_PG instead."

    Case-insensitive and tolerant of underscore-count typos (see UNDERSCORE_NOTE). The
    approved ..._FOR_PG table is never matched: it collapses to a different name.
    """
    return _collapse(name) in _DENY


def validate_no_deprecated_tables(sql: str) -> None:
    """
    Reject generated SQL that references a deprecated all-records table (General Rule 8).

    Raises DeprecatedTableError naming the offending table and the replacement. Deliberately
    does not rewrite: a silent rewrite hides a semantic-layer misconfiguration behind a
    plausible answer.
    """
    hits = sorted(
        {tok for tok in _IDENTIFIER.findall(sql) if is_deprecated_table(tok)}
    )
    if hits:
        raise DeprecatedTableError(
            f"SQL references deprecated table(s) {', '.join(hits)}. General Rule 8 "
            f"requires {APPROVED_ALL_RECORDS_TABLE} instead.\nRule: {RULE_TEXT}"
        )


__all__ = [
    "APPROVED_ALL_RECORDS_TABLE",
    "DEPRECATED_TABLES",
    "DeprecatedTableError",
    "RULE_ID",
    "RULE_TEXT",
    "UNDERSCORE_NOTE",
    "is_deprecated_table",
    "validate_no_deprecated_tables",
]
