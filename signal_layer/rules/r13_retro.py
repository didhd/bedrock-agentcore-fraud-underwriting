"""
General Rule 13 — retro data, and the three separable mechanics inside one paragraph.

  (a) **Default is no filter at all.** "By default you can look at all the data. You
      shouldn't filter out retro data by default." A retro filter applied by default hides
      part of the customer's book and every count silently shrinks.
  (b) **The filter must be null-tolerant.** ``record_from not like '%RETRO%'`` alone
      evaluates to NULL for a NULL record_from, so the row is dropped. The customer
      pre-empts this with the exact idiom to use, twice ("make sure these are not
      excluded"). Non-retro rows with an unpopulated record_from are the majority in some
      tables; dropping them is the single most expensive mistake in this rule.
  (c) **submitted_to_prod_at is unusable for retro.** "DO NOT USE THE SUBMITTED_TO_PROD_AT
      FIELD AS IT WILL BE NULL. PLEASE DO NOT." This directly contradicts General Rule 11,
      which routes the word "submitted" to that field. The retro branch wins, and the
      conflict is surfaced as a caveat rather than silently resolved — a retro question
      about submissions returns an empty set otherwise, which reads as "no activity".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final, Iterable, Mapping

from signal_layer.rules._source import rule_text

RULE_ID: Final = "13"
RULE_TEXT: Final = rule_text(RULE_ID)

#: The column the retro filter operates on.
RECORD_FROM_FIELD: Final = "record_from"

#: The marker identifying a retro record.
RETRO_MARKER: Final = "RETRO"

#: The field General Rule 13 forbids in a retro-scoped query, in all caps as the doc has it.
FORBIDDEN_RETRO_FIELD: Final = "submitted_to_prod_at"

#: The null-safe non-retro predicate, in the customer's own words:
#: "So the filter should be (record_from not like %RETRO% or record_from is null)".
NOT_RETRO_PREDICATE_SQL: Final = (
    "(record_from not like '%RETRO%' or record_from is null)"
)

#: The retro-only predicate. "Anytime someone asks about anything related to 'retro' or
#: 'during retro' be sure to look for the data for that lender, etc. that have record_from
#: as RETRO." No null disjunct here: a NULL record_from is not known to be retro.
RETRO_ONLY_PREDICATE_SQL: Final = "record_from like '%RETRO%'"

#: Tokens in a user question that select the non-retro scope.
NON_RETRO_KEYWORDS: Final[tuple[str, ...]] = ("non-retro", "non retro", "nonretro", "prod")

#: Tokens that select the retro scope. "during retro" is called out explicitly by the rule.
RETRO_KEYWORDS: Final[tuple[str, ...]] = ("retro", "during retro")


class RetroScope(str, Enum):
    """
    Which slice of the book a question is about (General Rule 13).

    ALL is the default and applies no record_from predicate whatsoever, because the rule
    says so twice.
    """

    ALL = "all"
    NON_RETRO = "non_retro"
    RETRO_ONLY = "retro_only"


@dataclass(frozen=True)
class RetroDecision:
    """The resolved retro scope, the SQL predicate it implies, and any rule conflict."""

    scope: RetroScope
    predicate: str
    matched_keyword: str = ""
    caveat: str = ""

    @property
    def filters(self) -> bool:
        """True when a record_from predicate will be emitted at all."""
        return self.scope is not RetroScope.ALL


def _find(question: str, keywords: Iterable[str]) -> str:
    lowered = question.lower()
    for kw in keywords:
        if kw in lowered:
            return kw
    return ""


def resolve_retro_scope(question: str | None) -> RetroDecision:
    """
    Choose the retro scope for a question (General Rule 13).

    Verbatim: "If a question specifically asks to look at \"non-retro\" or \"prod\" data,
    please use the not_retro_data filter (which filters on RECORD_FROM)...By default you
    can look at all the data. You shouldn't filter out retro data by default. ... Anytime
    someone asks about anything related to 'retro' or 'during retro' be sure to look for
    the data for that lender, etc. that have record_from as RETRO."

    Precedence: an explicit non-retro/prod request wins over a bare "retro" match, because
    the phrase "non-retro" contains "retro" and would otherwise select the opposite scope.
    No keyword at all -> ALL, with an empty predicate.

    When the retro scope is selected and the question also says "submitted", the returned
    caveat states the General Rule 11 conflict: submitted_to_prod_at is NULL for retro
    records, so no submitted-date answer exists for that population.
    """
    text = question or ""
    non_retro = _find(text, NON_RETRO_KEYWORDS)
    if non_retro:
        return RetroDecision(
            scope=RetroScope.NON_RETRO,
            predicate=NOT_RETRO_PREDICATE_SQL,
            matched_keyword=non_retro,
        )
    retro = _find(text, RETRO_KEYWORDS)
    if retro:
        caveat = ""
        if "submitted" in text.lower():
            caveat = (
                "This question is retro-scoped AND uses the word 'submitted'. General "
                "Rule 13 forbids submitted_to_prod_at for retro data ('DO NOT USE THE "
                "SUBMITTED_TO_PROD_AT FIELD AS IT WILL BE NULL. PLEASE DO NOT'), while "
                "General Rule 11 routes 'submitted' to that field. The retro rule wins: "
                "application_date is used instead, and the substitution must be stated in "
                "the answer rather than returning an empty result set."
            )
        return RetroDecision(
            scope=RetroScope.RETRO_ONLY,
            predicate=RETRO_ONLY_PREDICATE_SQL,
            matched_keyword=retro,
            caveat=caveat,
        )
    return RetroDecision(scope=RetroScope.ALL, predicate="")


def is_retro(record_from: Any) -> bool:
    """
    True when a record_from value marks a retro record.

    NULL is False: unknown provenance is not known to be retro. This asymmetry is the
    whole reason the non-retro predicate needs its ``or record_from is null`` disjunct
    while the retro-only predicate does not.
    """
    if record_from is None:
        return False
    return RETRO_MARKER in str(record_from).upper()


def is_non_retro(record_from: Any) -> bool:
    """
    Apply General Rule 13's null-safe non-retro filter to one value.

    Verbatim: "When you do filter on record_from, you should also also include those that
    have record_from as null, and make sure these are not excluded...So the filter should
    be (record_from not like %RETRO% or record_from is null) for example."

    True for a NULL record_from — that is the null-tolerance the rule demands, and getting
    it wrong silently deletes legitimate rows.
    """
    if record_from is None:
        return True
    return not is_retro(record_from)


def apply_retro_filter(
    rows: Iterable[Mapping[str, Any]],
    scope: RetroScope | RetroDecision = RetroScope.ALL,
    *,
    field: str = RECORD_FROM_FIELD,
) -> list[Mapping[str, Any]]:
    """
    Filter rows by retro scope (General Rule 13).

    ALL returns every row untouched, which is the documented default. NON_RETRO keeps
    non-retro rows AND rows whose record_from is null or absent. RETRO_ONLY keeps only rows
    positively marked retro.
    """
    decision = scope if isinstance(scope, RetroDecision) else None
    effective = decision.scope if decision else scope
    materialised = list(rows)
    if effective is RetroScope.ALL:
        return materialised
    if effective is RetroScope.NON_RETRO:
        return [r for r in materialised if is_non_retro(r.get(field))]
    return [r for r in materialised if is_retro(r.get(field))]


def record_from_join_note(table: str) -> str:
    """
    The join requirement for a table without a record_from column (General Rule 13).

    Verbatim: "Please note that if the table doesn't have a RECORD_FROM field you should
    try to join appropriately to one of the tables that does in the correct way." Returns
    the instruction text for the query builder to attach; the join itself must use the
    General Rule 4/5 composite key, and consumes one of General Rule 27's five SQL calls.
    """
    return (
        f"{table} has no {RECORD_FROM_FIELD} column. General Rule 13 requires joining to "
        f"a table that does, on the (application_id, hash_lender_id) grain of General "
        f"Rules 4 and 5. That join consumes one of the five SQL calls General Rule 27 "
        f"allows."
    )


_SUBMITTED_AT_REF: Final = re.compile(rf"\b{FORBIDDEN_RETRO_FIELD}\b", re.IGNORECASE)
_BARE_NOT_LIKE_RETRO: Final = re.compile(
    r"record_from\s+not\s+like\s+'%RETRO%'", re.IGNORECASE
)
_NULL_DISJUNCT: Final = re.compile(
    r"record_from\s+is\s+null", re.IGNORECASE
)


class RetroRuleViolation(ValueError):
    """Generated SQL breaks one of General Rule 13's three mechanics."""


def validate_retro_sql(sql: str, scope: RetroScope | RetroDecision = RetroScope.ALL) -> None:
    """
    Reject generated SQL that breaks General Rule 13.

    Two checks:
      1. Any ``record_from not like '%RETRO%'`` must be accompanied by ``record_from is
         null`` — the null-safety half. Without it the predicate drops every row with an
         unpopulated record_from.
      2. A retro-scoped query must not reference submitted_to_prod_at at all, because that
         column is NULL for retro records.
    """
    effective = scope.scope if isinstance(scope, RetroDecision) else scope
    problems: list[str] = []
    if _BARE_NOT_LIKE_RETRO.search(sql) and not _NULL_DISJUNCT.search(sql):
        problems.append(
            "record_from not like '%RETRO%' without an 'or record_from is null' "
            "disjunct; this silently drops every row with a null record_from"
        )
    if effective is RetroScope.RETRO_ONLY and _SUBMITTED_AT_REF.search(sql):
        problems.append(
            f"retro-scoped query references {FORBIDDEN_RETRO_FIELD}, which is NULL for "
            "retro records ('DO NOT USE ... PLEASE DO NOT')"
        )
    if problems:
        raise RetroRuleViolation(
            "General Rule 13 violated: " + "; ".join(problems) + f"\nRule: {RULE_TEXT}"
        )


__all__ = [
    "FORBIDDEN_RETRO_FIELD",
    "NON_RETRO_KEYWORDS",
    "NOT_RETRO_PREDICATE_SQL",
    "RECORD_FROM_FIELD",
    "RETRO_KEYWORDS",
    "RETRO_MARKER",
    "RETRO_ONLY_PREDICATE_SQL",
    "RULE_ID",
    "RULE_TEXT",
    "RetroDecision",
    "RetroRuleViolation",
    "RetroScope",
    "apply_retro_filter",
    "is_non_retro",
    "is_retro",
    "record_from_join_note",
    "resolve_retro_scope",
    "validate_retro_sql",
]
