"""
General Rules 9 and 18 — source precedence, and why "no coborrower" is three answers.

Rule 9 orders the sources: tbl_auto_consortium first, all-records second. Implemented as
an agent decision this fails silently — the model queries all-records only, finds nothing,
and reports "no co-borrower". The identity orchestration makes that specific false negative
expensive: a missing co-borrower "does not automatically indicate risk" alone, but for a
case already high-risk on other features it "escalates the risk". So a wrong answer here
moves the risk classification.

Hence the three-valued result. ``PRESENT`` / ``ABSENT`` / ``UNKNOWN`` must not collapse:
ABSENT means both sources were consulted and neither has a co-borrower; UNKNOWN means the
question was never actually answered. Rule 18 is the same precedence pattern generalised —
an empty result from all-records is not a "no records" answer, it is a mandatory retry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final, Mapping, Sequence

from signal_layer.rules._source import rule_text

RULE_ID: Final = "9"
RULE_TEXT: Final = rule_text(RULE_ID)
RULE_18_ID: Final = "18"
RULE_18_TEXT: Final = rule_text(RULE_18_ID)

#: The co-borrower source order General Rule 9 mandates.
COBORROWER_SOURCE_ORDER: Final[tuple[str, ...]] = (
    "tbl_auto_consortium",
    "all_records_all_all_records_for_pg",
)

#: General Rule 18's source order for finding application data at all. Same precedence,
#: and the synthetic document states it in all caps for its own agent ("PLEASE USE
#: TBL_AUTO_CONSORTIUM AS YOUR PRIMARY DATA SOURCE").
APPLICATION_SOURCE_ORDER: Final[tuple[str, ...]] = COBORROWER_SOURCE_ORDER

#: Fields that evidence a co-borrower. Presence of any non-null one means present.
COBORROWER_FIELDS: Final[tuple[str, ...]] = (
    "coborr_ssn",
    "coborr_hash_ssn",
    "coborr_first_name",
    "coborr_last_name",
    "coborr_income_annual",
    "coborr_credit_score",
    "coborr_dob",
)

#: An explicit boolean, when a source provides one, outranks field sniffing.
COBORROWER_FLAG_FIELDS: Final[tuple[str, ...]] = (
    "has_coborrower",
    "coborrower_present",
    "coborr_flag",
)


class Coborrower(str, Enum):
    """
    Three-valued co-borrower state (General Rule 9).

    ABSENT and UNKNOWN must never collapse into one value: "we checked both sources and
    there is no co-borrower" and "we did not manage to find out" carry opposite weight in
    the identity agent's escalation logic.
    """

    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CoborrowerResult:
    """Resolved co-borrower state plus the provenance General Rule 9 requires."""

    state: Coborrower
    source: str = ""
    sources_checked: tuple[str, ...] = ()
    attributes: Mapping[str, Any] = field(default_factory=dict)

    @property
    def has_coborrower(self) -> bool:
        """
        True only for PRESENT.

        UNKNOWN is deliberately falsey here *and* distinguishable via .state, so a caller
        that only wants the boolean is safe, and a caller that must not treat unknown as
        absent has the information available.
        """
        return self.state is Coborrower.PRESENT


def _coborrower_in_row(row: Mapping[str, Any]) -> tuple[bool, dict[str, Any]]:
    """Read co-borrower evidence out of one row: (present, contributing attributes)."""
    attributes = {
        f: row[f] for f in COBORROWER_FIELDS if row.get(f) not in (None, "")
    }
    for flag in COBORROWER_FLAG_FIELDS:
        if flag in row and row[flag] is not None:
            value = row[flag]
            if isinstance(value, str):
                present = value.strip().upper() in {"1", "Y", "YES", "TRUE", "T"}
            else:
                present = bool(value)
            return present, attributes
    return bool(attributes), attributes


def resolve_coborrower(
    consortium_rows: Sequence[Mapping[str, Any]] | None,
    all_records_rows: Sequence[Mapping[str, Any]] | None = None,
) -> CoborrowerResult:
    """
    Resolve co-borrower presence with General Rule 9's source precedence.

    Verbatim: "For questions regarding coborrowers for applications please check for
    coborrower in the tbl_auto_consortium first and then all records. tbl auto consortium
    is a better source for coborrower data."

    An ordered coalesce, not a two-step agent decision. tbl_auto_consortium is consulted
    first; all-records is consulted only when the consortium rows are absent or carry no
    co-borrower evidence. ``None`` means "this source was not consulted" and ``[]`` means
    "consulted, no rows" — the difference is what separates ABSENT from UNKNOWN:

      * consortium row says present            -> PRESENT, source=tbl_auto_consortium
      * consortium silent, all-records present -> PRESENT, source=all records
      * both consulted, neither present        -> ABSENT
      * neither consulted (both None)          -> UNKNOWN
    """
    checked: list[str] = []
    for source, rows in zip(COBORROWER_SOURCE_ORDER, (consortium_rows, all_records_rows)):
        if rows is None:
            continue
        checked.append(source)
        for row in rows:
            present, attributes = _coborrower_in_row(row)
            if present:
                return CoborrowerResult(
                    state=Coborrower.PRESENT,
                    source=source,
                    sources_checked=tuple(checked),
                    attributes=attributes,
                )
    if not checked:
        return CoborrowerResult(state=Coborrower.UNKNOWN, sources_checked=())
    return CoborrowerResult(
        state=Coborrower.ABSENT,
        source=checked[-1],
        sources_checked=tuple(checked),
    )


def next_source(rows: Sequence[Any] | None, sources_tried: Sequence[str]) -> str | None:
    """
    Apply General Rule 18's fallback: which source to try next, or None when done.

    Verbatim (rule 18): "If the user tries to find specific application data meeting some
    criteria they have defined for you in her query, and you can't find any data for that
    in all records for pg, please try to look at the tbl_auto_consortium table... YOU NEED
    TO CHECK TBL_AUTO_CONSORTIUM especially if nothing shows in all_records_for_pg."

    An empty result set is NOT an answer — it is a mandatory retry against the next source
    in APPLICATION_SOURCE_ORDER. Returns None once rows were found or every source has been
    tried. Note each retry consumes one of General Rule 27's five SQL calls.
    """
    if rows:
        return None
    for source in APPLICATION_SOURCE_ORDER:
        if source not in sources_tried:
            return source
    return None


def is_exhausted(rows: Sequence[Any] | None, sources_tried: Sequence[str]) -> bool:
    """
    True when "no data" may finally be reported (General Rule 18).

    Requires that every source in APPLICATION_SOURCE_ORDER was tried and none returned
    rows. Guards the failure mode where the agent reports "no applications found" after
    querying one table.
    """
    return not rows and all(s in sources_tried for s in APPLICATION_SOURCE_ORDER)


__all__ = [
    "APPLICATION_SOURCE_ORDER",
    "COBORROWER_FIELDS",
    "COBORROWER_FLAG_FIELDS",
    "COBORROWER_SOURCE_ORDER",
    "Coborrower",
    "CoborrowerResult",
    "RULE_18_ID",
    "RULE_18_TEXT",
    "RULE_ID",
    "RULE_TEXT",
    "is_exhausted",
    "next_source",
    "resolve_coborrower",
]
