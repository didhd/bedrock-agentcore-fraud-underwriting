"""
General Rule 11 — date-field routing, and an anti-pattern the customer discovered the
hard way.

Four separable requirements in one paragraph:

  (a) the literal word "submitted" in the question selects ``submitted_to_prod_at``; its
      absence selects ``application_date``;
  (b) a "submitted" question starts in ``TBL_AUTO_CONSORTIUM``, not all-records;
  (c) an undated "past applications generally" question also uses ``application_date``;
  (d) the generated SQL must never contain ``submitted_to_prod_at <= CURRENT_TIMESTAMP()``.

(d) is worth dwelling on. The customer's note is "This for some reason removes results."
They do not know why, and neither do we — but they have observed it, and a predicate that
looks like a harmless sanity bound silently deletes rows. Whatever the cause (a timezone
skew putting rows marginally in the future is the usual suspect), the instruction is
unambiguous and is enforced here as a hard rejection rather than a prompt line, because a
model asked not to emit a tautology will eventually emit it.

Requirement (a) is keyword-driven, so it is decided here and the chosen field is echoed
back for the agent to state. General Rule 13 overrides it for retro data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from signal_layer.rules._source import rule_text
from signal_layer.rules.r13_retro import RetroScope, resolve_retro_scope

RULE_ID: Final = "11"
RULE_TEXT: Final = rule_text(RULE_ID)

#: The field selected when the user says "submitted".
SUBMITTED_FIELD: Final = "submitted_to_prod_at"

#: The default date field for every other date question.
DEFAULT_DATE_FIELD: Final = "application_date"

#: The table a "submitted" question starts from ("you should start by looking in the
#: TBL_AUTO_CONSORTIUM table not the all records table"). Upper case as the doc writes it.
SUBMITTED_PRIMARY_TABLE: Final = "TBL_AUTO_CONSORTIUM"

#: The literal token that routes to submitted_to_prod_at.
SUBMITTED_KEYWORD: Final = "submitted"

#: The exact anti-pattern General Rule 11 forbids, as the customer writes it.
FORBIDDEN_PREDICATE: Final = "tac.submitted_to_prod_at <= CURRENT_TIMESTAMP()"

FORBIDDEN_PREDICATE_NOTE: Final = (
    "General Rule 11: \"in your query, don't do: tac.submitted_to_prod_at <= "
    "CURRENT_TIMESTAMP(). This for some reason removes results.\" The customer reports "
    "the symptom without a root cause, so no attempt is made to 'fix' the underlying "
    "issue - the predicate is simply rejected. Enforced mechanically because it looks "
    "like a harmless sanity bound and a model will re-add it."
)


@dataclass(frozen=True)
class DateFieldDecision:
    """Which date field a question resolves to, and why."""

    field: str
    reason: str
    primary_table: str = ""
    caveat: str = ""

    @property
    def is_submitted(self) -> bool:
        return self.field == SUBMITTED_FIELD


def resolve_date_field(question: str | None) -> DateFieldDecision:
    """
    Choose the date field for a question (General Rule 11), honouring General Rule 13.

    Verbatim rule 11: "Anytime someone asks about applications SUBMITTED on a date, please
    look specifically at the submitted_to_prod_at field rather than application date. Also
    you should start by looking in the TBL_AUTO_CONSORTIUM table not the all records table.
    However, if \"submitted\" is not specified you should use application_date.
    Additionally, if the user doesn't specify a time frame (or \"submitted\") but wants to
    look at past applications generally, you should look at previous applications according
    to application_date. Also, in your query, don't do: tac.submitted_to_prod_at <=
    CURRENT_TIMESTAMP(). This for some reason removes results."

    Deterministic on the literal token "submitted". The decision is returned rather than
    applied so the agent can state which date basis it used — "applications submitted
    yesterday" and "applications from yesterday" are different questions with different
    answers, and the answer must say which one it answered.

    General Rule 13 overrides: for a retro-scoped question, submitted_to_prod_at is NULL,
    so application_date is used and the substitution is returned as a caveat instead of
    producing an empty result set.
    """
    text = question or ""
    asks_submitted = SUBMITTED_KEYWORD in text.lower()
    if not asks_submitted:
        return DateFieldDecision(
            field=DEFAULT_DATE_FIELD,
            reason=(
                'the question does not say "submitted", so General Rule 11 selects '
                "application_date (this also covers 'past applications generally')"
            ),
        )
    retro = resolve_retro_scope(text)
    if retro.scope is RetroScope.RETRO_ONLY:
        return DateFieldDecision(
            field=DEFAULT_DATE_FIELD,
            reason=(
                'the question says "submitted", but it is retro-scoped and General Rule '
                "13 forbids submitted_to_prod_at for retro data (it is NULL there), so "
                "General Rule 13 overrides General Rule 11"
            ),
            primary_table=SUBMITTED_PRIMARY_TABLE,
            caveat=retro.caveat,
        )
    return DateFieldDecision(
        field=SUBMITTED_FIELD,
        reason=(
            'the question says "submitted", so General Rule 11 selects '
            "submitted_to_prod_at and starts from TBL_AUTO_CONSORTIUM"
        ),
        primary_table=SUBMITTED_PRIMARY_TABLE,
    )


def primary_table_for(question: str | None) -> str:
    """
    The table a date question starts from (General Rule 11 (b), General Rule 18).

    "submitted" -> TBL_AUTO_CONSORTIUM. Everything else also starts there per General Rule
    18 ("especially if the date is more recent (i.e. today) you should just start by looking
    first in the TBL_AUTO_CONSORTIUM table rather than all records"), with all-records as
    the documented fallback.
    """
    return SUBMITTED_PRIMARY_TABLE


class ForbiddenDatePredicate(ValueError):
    """Generated SQL contains the predicate General Rule 11 forbids."""


# CURRENT_DATE / SYSDATE / GETDATE are covered alongside the CURRENT_TIMESTAMP the customer
# names: same shape, same "bound submitted_to_prod_at by now" intent, same reported symptom of
# rows disappearing. The parentheses are optional because CURRENT_DATE and SYSDATE are valid
# without them in Snowflake.
_FORBIDDEN: Final = re.compile(
    r"\b\w*\.?submitted_to_prod_at\s*<=?\s*"
    r"(?:current_timestamp|current_date|localtimestamp|sysdate|getdate)"
    r"\s*(?:\(\s*\))?",
    re.IGNORECASE,
)


def validate_date_sql(sql: str) -> None:
    """
    Reject the ``submitted_to_prod_at <= CURRENT_TIMESTAMP()`` anti-pattern (Rule 11).

    Matches with or without a table alias, with any internal spacing, and also catches the
    strict ``<`` form — the customer names ``<=`` but the failure mode (rows vanishing) is
    identical for ``<``, and no legitimate query needs either. The same applies to the
    CURRENT_DATE / SYSDATE / GETDATE spellings of "now": the customer names CURRENT_TIMESTAMP
    because that is what they wrote, but the predicate's intent and its reported symptom are
    identical, so all of them are rejected. Raises rather than silently rewriting: a silent
    rewrite hides that the query builder tried to emit it.
    """
    m = _FORBIDDEN.search(sql)
    if m:
        raise ForbiddenDatePredicate(
            f"SQL contains {m.group(0)!r}, which General Rule 11 forbids: "
            f"{FORBIDDEN_PREDICATE_NOTE}\nRule: {RULE_TEXT}"
        )


def build_date_predicate(
    question: str | None,
    start: str,
    end: str | None = None,
    *,
    alias: str = "tac",
) -> str:
    """
    Build a date-range predicate on the correct field (General Rule 11).

    Uses the field resolve_date_field() selected, and emits no upper bound against
    CURRENT_TIMESTAMP() under any circumstances. ``end`` is an explicit user-supplied
    bound; omitting it yields an open-ended ``>=`` predicate.
    """
    field = resolve_date_field(question).field
    if end:
        predicate = f"{alias}.{field} >= '{start}' and {alias}.{field} <= '{end}'"
    else:
        predicate = f"{alias}.{field} >= '{start}'"
    validate_date_sql(predicate)  # belt and braces: never emit the forbidden form
    return predicate


__all__ = [
    "DEFAULT_DATE_FIELD",
    "DateFieldDecision",
    "FORBIDDEN_PREDICATE",
    "FORBIDDEN_PREDICATE_NOTE",
    "ForbiddenDatePredicate",
    "RULE_ID",
    "RULE_TEXT",
    "SUBMITTED_FIELD",
    "SUBMITTED_KEYWORD",
    "SUBMITTED_PRIMARY_TABLE",
    "build_date_predicate",
    "primary_table_for",
    "resolve_date_field",
    "validate_date_sql",
]
