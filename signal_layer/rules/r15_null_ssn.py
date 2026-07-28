"""
General Rule 15 — a NULL SSN is not an SSN.

``COUNT(DISTINCT borr_ssn)`` in SQL already ignores NULLs; the rule exists because the
equivalent Python/pandas operation does not, and because the customer has evidently been
burned by a count that did. Every identity-reuse signal is a distinct-SSN count —
``phone_reuse_distinct_ssn_count``, ``address_reuse_distinct_ssn_count``,
``email_reuse_distinct_ssn_count``, ``employer_distinct_other_ssns_count`` — so one
uncounted NULL inflates reuse by one, and a phone shared by one real borrower plus one
NULL-SSN record reads as a phone shared by two identities. That is a fabricated identity
cluster from a missing value.

The empty string is treated as NULL too. It is not what the rule says, but a blank SSN is
not an SSN either, and CSV/RTF round-tripping turns NULL into '' routinely.
"""

from __future__ import annotations

from typing import Any, Final, Iterable, Mapping

from signal_layer.rules._source import rule_text

RULE_ID: Final = "15"
RULE_TEXT: Final = rule_text(RULE_ID)

#: The SSN field the rule names, plus the hashed form the rest of the system uses (General
#: Rule 23 permits only hashed values downstream, so distinct-SSN counts in the signal
#: layer are computed over hash_ssn — the same NULL problem applies identically).
SSN_FIELDS: Final[tuple[str, ...]] = ("borr_ssn", "hash_ssn", "borr_hash_ssn")

#: Values treated as "no SSN". The customer names NULL; '' and whitespace-only strings are
#: included because they are NULL after a CSV or RTF round-trip and are equally not an SSN.
NULL_EQUIVALENTS: Final[frozenset[str]] = frozenset({"", "NULL", "NONE", "NAN", "N/A", "NA"})


def is_present_ssn(value: Any) -> bool:
    """
    True when an SSN value is actually there (General Rule 15).

    Verbatim: "Please do not consider null borr_ssn as a distinct ssn. To look at distinct
    ssn's you should make sure only to consider when the ssn is actually there."

    "actually there" is the rule's own phrase and this function is its definition: not
    None, not blank, not a stringified null.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().upper() not in NULL_EQUIVALENTS
    return True


def distinct_ssns(values: Iterable[Any]) -> set[Any]:
    """
    The set of distinct SSNs present in a sequence (General Rule 15).

    NULLs and blanks are dropped before the set is formed, so ``len()`` of the result is
    the count the rule asks for. Contrast ``len(set(values))``, which counts None as a
    distinct SSN — the bug this replaces.
    """
    return {v for v in values if is_present_ssn(v)}


def count_distinct_ssns(values: Iterable[Any]) -> int:
    """Count distinct present SSNs (General Rule 15). The measure every reuse signal uses."""
    return len(distinct_ssns(values))


def count_distinct_ssns_in_rows(
    rows: Iterable[Mapping[str, Any]], field: str = "borr_ssn"
) -> int:
    """
    Count distinct present SSNs across rows, reading one named field (General Rule 15).

    Accepts any of SSN_FIELDS; rows missing the field contribute nothing rather than
    contributing a None.
    """
    return count_distinct_ssns(row.get(field) for row in rows)


def distinct_ssn_sql(column: str = "borr_ssn", alias: str = "v") -> str:
    """
    The SQL form of General Rule 15, with the NULL exclusion spelled out.

    ``count(distinct case when v.borr_ssn is not null then v.borr_ssn end)``. SQL's
    COUNT(DISTINCT) already skips NULLs, so the CASE is redundant *as SQL* — it is emitted
    anyway so that the generated query visibly states the rule it is honouring, which is
    what makes the guardrail auditable by the customer reading our SQL.
    """
    qualified = f"{alias}.{column}"
    return f"count(distinct case when {qualified} is not null then {qualified} end)"


__all__ = [
    "NULL_EQUIVALENTS",
    "RULE_ID",
    "RULE_TEXT",
    "SSN_FIELDS",
    "count_distinct_ssns",
    "count_distinct_ssns_in_rows",
    "distinct_ssn_sql",
    "distinct_ssns",
    "is_present_ssn",
]
