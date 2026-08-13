"""What computing the signals from a raw application table actually requires.

WHY THIS EXISTS

The customer said it in one sentence on the 2026-08-13 call: *"these values don't exist anywhere
and needs to be calculated using data scoring."* Their staging RDS holds one main application
table; the ~60 registry signals are not in it. `SIGNAL_MODE=aurora` reads PRECOMPUTED signals, so
against that database it has nothing to read.

This module answers the only question that matters before writing any SQL: **which signals can be
computed from an application history table at all, and which cannot?** It is a classification, not
a computation, and it is code rather than a one-off script so `coverage()` can be asserted by a
test and quoted in a customer conversation without anyone re-deriving it.

THE ANSWER, MEASURED AGAINST THE REGISTRY

Most of them can. A signal like `phone_reuse_distinct_ssn_count` is
`count(distinct hash_ssn) over rows sharing this phone` — a COUNT DISTINCT is a COUNT DISTINCT,
and computing it is not reinterpreting anybody's fraud logic. What CANNOT be computed is the
handful that are Point Predictive's own products: their model scores, their negative file, and
credit-bureau data. Those have to be supplied, and if they are absent the run must fail rather
than read a missing signal as a benign zero.

WHAT MAKES THIS SAFE TO BUILD, AND WHAT WOULD MAKE IT UNSAFE

Safe: the `_context` that the specialists' anti-false-positive rules read is GENERATED, not
authored. `fixtures/_build.py:context_row` builds `{signal, value, customer_interpretation}` where
the interpretation is read from `prompts/verbatim/` at generation time. So context costs nothing
extra once a value exists, and it still quotes the customer's own sentence about the threshold.

Unsafe, and the reason every derivation below carries its window in a comment: **the window
semantics are ours, not theirs.** `ssn_apps_last_7d` could be seven days back from this
application's date or from today, inclusive or exclusive of the application itself. Their
Snowflake layer has one answer and we cannot see it. A wrong window produces a plausible number
and silently changes a fraud band, which is worse than an error. So each window is stated, and
`NEEDS_CONFIRMATION` names the ones where more than one reading is defensible.

WHAT THIS MODULE DELIBERATELY DOES NOT DO

It does not name a single column of the customer's schema. The derivations are written against
LOGICAL roles (`borrower_id`, `application_date`, …) and a gitignored map supplies their real
names. That keeps their schema out of git for the same reason `prompts/verbatim/` is gitignored,
and it turns "send us your schema" into a short, answerable list.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final, Mapping

from signal_layer.registry import SIGNAL_NAMES


class Origin(str, Enum):
    """Where a signal's value can come from."""

    #: An aggregation over the application history, keyed by a hashed identifier.
    #: `count(distinct …)`, `count(*) where date >= …`. Mechanical.
    AGGREGATION = "aggregation"
    #: Arithmetic on fields of the application's own row. A ratio, a gap, a percentage.
    ROW = "row"
    #: A boolean over one of the above, at a threshold the customer states in their own
    #: prompt text (`SIGNAL_SPEC[name].interpretation`).
    FLAG = "flag"
    #: Point Predictive's OWN model output. Cannot be computed from an application table by
    #: anyone but them. Must be supplied.
    MODEL = "model"
    #: A reference-file lookup they maintain -- the negative file, prior-fraud links.
    #: Must be supplied.
    REFERENCE = "reference"
    #: Credit-bureau data. Not in an application table and not ours to reconstruct.
    BUREAU = "bureau"


@dataclass(frozen=True)
class Requirement:
    """One logical column the derivations need, described by ROLE and never by their name."""

    role: str
    description: str
    #: Why it is needed, in terms of what breaks without it.
    needed_for: str
    optional: bool = False


#: The short, answerable list to put in front of the customer. Sixteen roles, and the first six
#: carry most of the signals -- a reuse count needs an identifier to group by and nothing else.
COLUMN_CONTRACT: Final[tuple[Requirement, ...]] = (
    Requirement(
        "application_id",
        "the application's unique id",
        "every signal; it is what a payload is keyed by",
    ),
    Requirement(
        "borrower_id",
        "the HASHED borrower identifier (never a raw SSN -- their rule 23)",
        "every ssn_* signal: 20 of the 27 aggregations group by it",
    ),
    Requirement(
        "application_date",
        "when the application was made",
        "every velocity window: apps_last_7d, apps_last_30d, *_90d, days_in_system",
    ),
    Requirement(
        "phone",
        "the borrower's phone, normalised or raw",
        "phone_reuse_distinct_ssn_count, phone_ssn_count, phone_reuse_count_this_dealer",
    ),
    Requirement(
        "email",
        "the borrower's email",
        "email_reuse_distinct_ssn_count",
    ),
    Requirement(
        "street_address",
        "the borrower's street address",
        "address_reuse_*, address_ssn_count_same_unit / _same_building",
    ),
    Requirement(
        "zip_code",
        "the borrower's postal code",
        "their rule 2 defines address matching as street number + zip, not a string compare",
    ),
    Requirement(
        "employer_name",
        "the employer as stated, or their cleaned column if they have one",
        "employer_distinct_other_ssns_count, employer_new_ssns_90d, employer_spike_flag",
    ),
    Requirement(
        "dealer_id",
        "the dealer identifier. Their rule 14 says dealer identity is ppi_dealer_id + name",
        "dealer-scoped reuse counts",
    ),
    Requirement(
        "stated_income",
        "the income on the application",
        "loan_to_annual_income_ratio and every income comparison",
    ),
    Requirement(
        "verified_income",
        "the verified or estimated income, if they hold one",
        "ssn_stated_income_exceeds_verified_by_25_percent, overstatement_percentage",
        optional=True,
    ),
    Requirement(
        "loan_amount",
        "the requested loan amount",
        "loan_to_annual_income_ratio, the 80%-of-income flag",
    ),
    Requirement(
        "first_name",
        "borrower first name",
        "ssn_distinct_identity_combinations -- distinct name/DOB per identifier",
    ),
    Requirement(
        "last_name",
        "borrower last name",
        "the same identity-combination count",
    ),
    Requirement(
        "date_of_birth",
        "borrower date of birth",
        "the same identity-combination count",
    ),
    Requirement(
        "credit_score",
        "the borrower's credit score, if held",
        "credit_score_gap, and the thin-file / new-file flags",
        optional=True,
    ),
    Requirement(
        "coborrower_id",
        "the hashed co-borrower identifier, if held",
        "every straw-borrower signal. Absent, those cannot be computed",
        optional=True,
    ),
)


#: Signals that CANNOT be computed from an application table, and what they are instead.
#: Named individually rather than pattern-matched, because "we could not compute it" has to be a
#: statement about a specific signal, not a guess from its spelling.
NOT_DERIVABLE: Final[Mapping[str, tuple[Origin, str]]] = MappingProxyType(
    {
        "dealer_consortium_score": (
            Origin.MODEL,
            "Point Predictive's aggregated dealer risk across all consortium lenders, keyed by "
            "ppi_dealer_id (their rule 21). A single lender's table cannot produce it.",
        ),
        "dealer_risk_score": (
            Origin.MODEL,
            "Their dealer risk within ONE lender's portfolio, keyed by dealer_id + lender_id "
            "(rule 21). A model output, not an aggregation.",
        ),
        "borr_ssn_in_negative_file": (
            Origin.REFERENCE,
            "A lookup against the negative file they maintain. Not an application-table fact.",
        ),
        "borr_ssn_linked_to_synthetic_fraud": (
            Origin.REFERENCE,
            "A lookup against the prior-confirmed-fraud links they maintain across the "
            "consortium. An application table records what was applied for, not what was later "
            "adjudicated as fraud, so this cannot be recovered from one.",
        ),
        "inquiries_in_2weeks": (
            Origin.BUREAU,
            "Credit-bureau inquiry count. Not in an application table, and not ours to "
            "reconstruct from one.",
        ),
        "dealer_consortium_epd_rate_last_24mo": (
            Origin.MODEL,
            "Requires consortium-wide loan PERFORMANCE, not applications. Derivable only if "
            "their table carries funded/EPD outcomes for every lender, which one lender's "
            "staging table does not.",
        ),
        "dealer_lender_default_rate": (
            Origin.MODEL,
            "Requires this lender's loan performance history. Derivable IF they hold "
            "funded/EPD flags; treated as supplied until they confirm they do.",
        ),
    }
)


#: Signals where more than one window or definition is defensible, so ours must be confirmed
#: before any number computed from it is shown to an underwriter. A plausible wrong window is
#: worse than an error: it moves a fraud band silently.
NEEDS_CONFIRMATION: Final[Mapping[str, str]] = MappingProxyType(
    {
        "ssn_apps_last_7d": (
            "seven days back from THIS application's date, or from today? inclusive of this "
            "application itself? Their own prompt text carries no threshold sentence for this "
            "signal, so there is nothing to read it off."
        ),
        "ssn_apps_last_30d": (
            "the same window question as the 7-day count: thirty days back from when, and is "
            "this application counted? Their prompt text carries no threshold sentence for it "
            "either."
        ),
        "employer_new_ssns_90d": (
            "'new' relative to the employer's first appearance, or simply first-seen within the "
            "window? The two differ for a long-standing employer."
        ),
        "ssn_days_in_system": (
            "days since this identifier's FIRST application in their consortium. A single "
            "lender's table only sees that lender's history, so this is a floor, not the value."
        ),
        "address_ssn_count_same_unit": (
            "is there a unit/apt column at all? Their rule 2 defines address matching as street "
            "number + zip, which is BUILDING level -- so without one, same_unit and "
            "same_building collapse to the same number, and the rings agent's whole "
            "exact-unit-versus-building distinction disappears."
        ),
        "employer_spike_flag": (
            "'sudden increase' is their phrase; the threshold and baseline period are not in the "
            "prompt text."
        ),
    }
)


def origin_of(signal: str) -> Origin:
    """Where this signal's value must come from.

    The classification is by NAME and it is deliberate that `NOT_DERIVABLE` is consulted first:
    a name-pattern rule would classify `dealer_consortium_epd_rate_last_24mo` as an aggregation
    because it ends in a window, and it is not one.
    """
    if signal in NOT_DERIVABLE:
        return NOT_DERIVABLE[signal][0]

    import re  # noqa: PLC0415

    if re.search(
        r"_count$|_count_|apps_last_\d+d|_reuse_|distinct_|_variations|days_in_system"
        r"|_ssns_\d+d|_90d$|_30d$|_7d$|volume_\d+mo",
        signal,
    ):
        return Origin.AGGREGATION
    if re.search(r"_ratio$|_gap$|_percentage$|_pct$", signal):
        return Origin.ROW
    return Origin.FLAG


def coverage() -> dict[str, object]:
    """What a derivation layer could and could not produce. Asserted by a test.

    Reported rather than summarised in prose, so a customer conversation can quote a number that
    a test keeps honest.
    """
    by_origin: dict[str, list[str]] = {}
    for signal in SIGNAL_NAMES:
        by_origin.setdefault(origin_of(signal).value, []).append(signal)

    derivable = sorted(
        s
        for s in SIGNAL_NAMES
        if origin_of(s) in (Origin.AGGREGATION, Origin.ROW, Origin.FLAG)
    )
    supplied = sorted(s for s in SIGNAL_NAMES if s in NOT_DERIVABLE)

    return {
        "registry_signals": len(SIGNAL_NAMES),
        "derivable": len(derivable),
        "must_be_supplied": len(supplied),
        "needs_confirmation": len(NEEDS_CONFIRMATION),
        "by_origin": {k: len(v) for k, v in sorted(by_origin.items())},
        "derivable_signals": derivable,
        "supplied_signals": supplied,
        "required_columns": [r.role for r in COLUMN_CONTRACT if not r.optional],
        "optional_columns": [r.role for r in COLUMN_CONTRACT if r.optional],
    }


__all__ = [
    "COLUMN_CONTRACT",
    "NEEDS_CONFIRMATION",
    "NOT_DERIVABLE",
    "Origin",
    "Requirement",
    "coverage",
    "origin_of",
]
