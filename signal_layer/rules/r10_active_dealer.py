"""
General Rules 10 and 14 — the active-dealer filter and the dealer identity key.

The two rules must be applied together: rule 10's activity predicate has to be evaluated at
the grain rule 14 defines. Filter by ``ppi_dealer_id`` alone and two dealerships sharing an
id (a rooftop group) merge; group by ``dealer_name`` alone and every "AUTO SALES" collides.

Rule 10 also has a NULL branch the customer does not mention. ``consortium_number_of_apps_
last_1_year`` can be NULL, and ``NULL > 0`` is NULL in SQL, so a NULL dealer is dropped. That
is the correct behaviour — an unknown activity level is not evidence of activity — but it is
made explicit and tested here rather than left as an accident of three-valued logic.
"""

from __future__ import annotations

from typing import Any, Final, Iterable, Mapping

from signal_layer.rules._source import rule_text

RULE_ID: Final = "10"
RULE_TEXT: Final = rule_text(RULE_ID)
RULE_14_ID: Final = "14"
RULE_14_TEXT: Final = rule_text(RULE_14_ID)

#: The activity field General Rule 10 names, in the dealer stats table.
ACTIVITY_FIELD: Final = "consortium_number_of_apps_last_1_year"

#: General Rule 14's composite dealer identity, in the customer's own order.
DEALER_IDENTITY_KEY: Final[tuple[str, ...]] = ("ppi_dealer_id", "dealer_name")

ACTIVE_DEALER_NULL_NOTE: Final = (
    "General Rule 10 says a dealer is active when consortium_number_of_apps_last_1_year "
    "> 0. It does not say what to do when the field is NULL. In SQL, NULL > 0 evaluates "
    "to NULL and the row is dropped, so a dealer with unknown activity is treated as "
    "INACTIVE and excluded. is_active_dealer() reproduces that deliberately: an unknown "
    "activity level is not evidence of activity. The choice is documented and tested "
    "rather than left as an accident of three-valued logic."
)

#: The dealer document's own emphatic output requirement, verbatim. It is not a numbered
#: General Rule but it constrains every dealer result set, so it lives beside rule 14.
DEALER_NAME_OUTPUT_RULE: Final = (
    "When you give me dealers in results, I prefer mostly to see the dealer_name. You "
    "can include the dealer_id as well, but primarily include the dealer_name. ALWAYS "
    "INCLUDE THE DEALER NAME!!!"
)


class DealerIdentityError(ValueError):
    """A dealer row lacks the composite identity General Rule 14 requires."""


def is_active_dealer(dealer: Mapping[str, Any]) -> bool:
    """
    Apply General Rule 10's activity predicate to one dealer-stats row.

    Verbatim: "You should only focus on dealers that are active. You can tell if a dealer
    is active by looking at the consortium_number_of_apps_last_1_year > 0 in the dealer
    stats table."

    Strictly greater than zero. A missing or NULL activity count returns False — see
    ACTIVE_DEALER_NULL_NOTE for why that is intentional rather than incidental. A
    negative count (which should not occur) is also False.
    """
    value = dealer.get(ACTIVITY_FIELD)
    if value is None or isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    if value != value:  # NaN
        return False
    return value > 0


def dealer_key(dealer: Mapping[str, Any]) -> tuple[Any, Any]:
    """
    Build the distinct-dealer key from General Rule 14.

    Verbatim: "The ppi_dealer_id and dealer_name together is the best way to get distinct
    dealers/identify dealers. Please always use this one."

    "Please always use this one" is unconditional, so this raises rather than falling back
    to whichever half is present: a half key silently merges or splits dealers, and both
    directions corrupt every dealer-level count.
    """
    missing = [f for f in DEALER_IDENTITY_KEY if dealer.get(f) in (None, "")]
    if missing:
        raise DealerIdentityError(
            f"dealer row is missing {', '.join(missing)}; General Rule 14 requires "
            f"{DEALER_IDENTITY_KEY} together. Rule: {RULE_14_TEXT}"
        )
    return tuple(dealer[f] for f in DEALER_IDENTITY_KEY)


def active_dealers(
    dealers: Iterable[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Filter a dealer population to the active ones (General Rule 10)."""
    return [d for d in dealers if is_active_dealer(d)]


def distinct_dealers(dealers: Iterable[Mapping[str, Any]]) -> set[tuple[Any, Any]]:
    """
    Count distinct dealers at General Rule 14's grain.

    Returns the set of (ppi_dealer_id, dealer_name) pairs. Rows missing either half raise
    via dealer_key(); callers wanting a lenient count must filter first, deliberately.
    """
    return {dealer_key(d) for d in dealers}


def count_active_distinct_dealers(dealers: Iterable[Mapping[str, Any]]) -> int:
    """
    Apply both rules in the required order: filter to active (rule 10), then count
    distinct at the composite grain (rule 14).

    The order matters for the count only when a dealer appears with both active and
    inactive stat rows; filtering first is what the rules say ("only focus on dealers that
    are active").
    """
    return len(distinct_dealers(active_dealers(dealers)))


def active_dealer_sql_predicate(alias: str = "d") -> str:
    """
    The default predicate for the dealer-stats path of the semantic view (Rule 10).

    Emits ``d.consortium_number_of_apps_last_1_year > 0``. No ``IS NOT NULL`` is added:
    the comparison already excludes NULL, and spelling it out would imply the customer
    asked for something they did not.
    """
    return f"{alias}.{ACTIVITY_FIELD} > 0"


def dealer_group_by_sql(alias: str = "d") -> str:
    """The GROUP BY clause General Rule 14 mandates for any dealer-level aggregate."""
    return ", ".join(f"{alias}.{f}" for f in DEALER_IDENTITY_KEY)


__all__ = [
    "ACTIVE_DEALER_NULL_NOTE",
    "ACTIVITY_FIELD",
    "DEALER_IDENTITY_KEY",
    "DEALER_NAME_OUTPUT_RULE",
    "DealerIdentityError",
    "RULE_14_ID",
    "RULE_14_TEXT",
    "RULE_ID",
    "RULE_TEXT",
    "active_dealer_sql_predicate",
    "active_dealers",
    "count_active_distinct_dealers",
    "dealer_group_by_sql",
    "dealer_key",
    "distinct_dealers",
    "is_active_dealer",
]
