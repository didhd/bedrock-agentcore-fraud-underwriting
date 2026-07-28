"""
The ten alert-ID category sets from Point Predictive's General Rule 3.

Transcribed character-by-character from the customer's own orchestration documents.
Every one of the eight fraud-domain orchestration docs carries an identical copy of
General Rule 3; the canonical source used here is:

    docs/.converted/identity_orchestration_062026.txt lines 72-93

and the identical lists were cross-checked against the other seven converted docs
(bustout, dealer, employment, income, rings, straw, synthetic) — all eight agree
exactly, including ID order. The master orchestration doc contains no General Rules.

WHY this module exists: all eight specialists are forbidden from citing alert numbers
("Reference specific findings from alerts, not alert numbers"), so the only way an
agent can be given the *right* alerts is for the signal layer to filter them by
category before the payload is built. A single wrong digit here silently removes a
detection or attributes it to the wrong specialist.

IDs THAT APPEAR IN MORE THAN ONE CATEGORY (do not assume uniqueness):
    40  Income + Default
    42  Income + Default
    68  Employment + Employment Deviation
    69  Employment + Employment Deviation
    70  Employment + Employment Deviation
    71  Employment + Employment Deviation
    73  Employment + Employment Deviation
    74  Employment + Employment Deviation
    75  Employment + Employment Deviation
    77  Employment + Employment Deviation
    79  Employment + Employment Deviation
    89  Income + Default
    97  Vehicle Risk + Collateral
    113 Income + Default
    114 Employment + Employment Deviation
    116 Employment + Default
    117 Employment + Default
    164 Income + Bust Out Risk

MULTI_CATEGORY_ALERTS is derived from the transcribed sets rather than re-typed, so
this docstring and the data cannot drift apart (asserted in tests).
"""

from __future__ import annotations

from collections import defaultdict
from types import MappingProxyType
from typing import Final, Mapping

# ---------------------------------------------------------------------------
# General Rule 3, verbatim. IDs are kept in the customer's stated order (the
# Employment list is deliberately NOT sorted: the source lists 76..156 first and
# then the ten Employment Deviation IDs).
# ---------------------------------------------------------------------------

GENERAL_RULE_3_PREAMBLE: Final = "3. Please consider alerts grouped into these categories:"

_IDENTITY: Final = (
    3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 44, 45, 46, 47,
    50, 51, 52, 53, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 104, 105, 106, 107, 111,
    112, 123, 128, 135, 137, 139, 141, 145, 146, 150, 151, 152, 153, 154, 155, 158,
    159, 165, 166,
)
_INCOME: Final = (27, 28, 29, 30, 40, 42, 43, 89, 113, 133, 134, 164)
_EMPLOYMENT: Final = (
    76, 78, 116, 117, 132, 140, 142, 143, 144, 148, 156, 68, 69, 70, 71, 73, 74, 75,
    77, 79, 114,
)
_EMPLOYMENT_DEVIATION: Final = (68, 69, 70, 71, 73, 74, 75, 77, 79, 114)
_STRAW_BORROWER: Final = (21, 22, 23, 24, 25, 26, 72, 110, 161, 168)
_VEHICLE_RISK: Final = (97, 162, 167)
_DEFAULT: Final = (
    40, 41, 42, 48, 49, 54, 55, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92,
    113, 115, 116, 117, 119, 120, 129, 130, 131, 138, 160, 163, 169,
)
_DEALER_RISK: Final = (56, 57, 93, 94, 95, 96, 103, 136, 147)
_COLLATERAL: Final = (97, 98, 99, 100, 101, 102)
_BUST_OUT_RISK: Final = (157, 164)

#: Category label -> alert IDs, in the customer's source order.
#: Labels are the customer's own headings with the trailing "Alerts"/"Alert" removed.
ALERT_CATEGORIES: Final[Mapping[str, tuple[int, ...]]] = MappingProxyType(
    {
        "Identity": _IDENTITY,
        "Income": _INCOME,
        "Employment": _EMPLOYMENT,
        "Employment Deviation": _EMPLOYMENT_DEVIATION,
        "Straw Borrower": _STRAW_BORROWER,
        "Vehicle Risk": _VEHICLE_RISK,
        "Default": _DEFAULT,
        "Dealer Risk": _DEALER_RISK,
        "Collateral": _COLLATERAL,
        "Bust Out Risk": _BUST_OUT_RISK,
    }
)

#: The customer's exact source headings, for provenance / display.
SOURCE_HEADINGS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "Identity": "Identity-related Alerts",
        "Income": "Income Alerts",
        "Employment": "Employment Alerts",
        "Employment Deviation": "Employment Deviation Alerts",
        "Straw Borrower": "Straw Borrower Alerts",
        "Vehicle Risk": "Vehicle Risk Alerts",
        "Default": "Default Alerts",
        "Dealer Risk": "Dealer Risk Alerts",
        "Collateral": "Collateral Alert",
        "Bust Out Risk": "Bust Out Risk Alert",
    }
)

#: Category label -> frozenset of IDs (order-free membership tests).
CATEGORY_ALERTS: Final[Mapping[str, frozenset[int]]] = MappingProxyType(
    {label: frozenset(ids) for label, ids in ALERT_CATEGORIES.items()}
)

#: General Rule 3, immediately after the Income list, verbatim.
DISREGARD_RULE: Final = (
    "Note that Alerts 31-39 are not greatly indicative of fraud risk. "
    "You may disregard them for the most part."
)

#: The IDs covered by DISREGARD_RULE. Note that none of them appear in any of the ten
#: categories, so this is an instruction about *uncategorised* alerts arriving in
#: alerts_fired: they must not be dropped from the record, but must not drive risk.
DISREGARDED_ALERTS: Final[frozenset[int]] = frozenset(range(31, 40))

# ---------------------------------------------------------------------------
# Reverse index. IDs are NOT unique across categories, so this returns a list.
# ---------------------------------------------------------------------------

_REVERSE: dict[int, list[str]] = defaultdict(list)
for _label, _ids in ALERT_CATEGORIES.items():
    for _alert_id in _ids:
        if _label not in _REVERSE[_alert_id]:
            _REVERSE[_alert_id].append(_label)

#: Every alert ID named anywhere in General Rule 3.
ALL_CATEGORISED_ALERTS: Final[frozenset[int]] = frozenset(_REVERSE)

#: IDs that belong to more than one category. Derived, never re-typed.
MULTI_CATEGORY_ALERTS: Final[frozenset[int]] = frozenset(
    alert_id for alert_id, labels in _REVERSE.items() if len(labels) > 1
)


def category_of(alert_id: int) -> list[str]:
    """
    Return every General Rule 3 category an alert ID belongs to.

    An ID can be in several categories (alert 97 is both Vehicle Risk and Collateral;
    alert 164 is both Income and Bust Out Risk), so callers must never treat this as
    a single value. Returns [] for IDs the customer never categorised — including
    31-39, which DISREGARD_RULE covers.
    """
    return list(_REVERSE.get(alert_id, ()))


# ---------------------------------------------------------------------------
# Fraud domain -> alert categories.
#
# Only five of the eight fraud domains have a same-named category in General Rule 3.
# For the other three the mapping is derived, and PROVENANCE records exactly which
# mappings are quoted from a customer document and which are inferred, so the
# customer can correct the inferred ones without archaeology.
#
# "Vehicle Risk" and "Collateral" are deliberately assigned to NO fraud domain: no
# customer document routes them to any of the eight specialists. They stay reachable
# through CATEGORY_ALERTS and are carried on the unfiltered record, but they are not
# injected into a specialist's view on a guess. See UNASSIGNED_CATEGORIES.
# ---------------------------------------------------------------------------

DOMAIN_ALERT_CATEGORIES: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "identity": ("Identity",),
        "dealer": ("Dealer Risk",),
        "straw": ("Straw Borrower",),
        "employment": ("Employment", "Employment Deviation"),
        "income": ("Income",),
        "synthetic": ("Identity",),
        "bustout": ("Bust Out Risk", "Default"),
        "rings": ("Identity",),
    }
)

#: Categories that no customer document assigns to a fraud domain.
UNASSIGNED_CATEGORIES: Final[tuple[str, ...]] = ("Vehicle Risk", "Collateral")

PROVENANCE: Final[Mapping[str, str]] = MappingProxyType(
    {
        "identity": (
            'DOC-QUOTED (identity_orchestration_062026): "You should also focus on the '
            'identity alerts that we have as defined below in general rules."'
        ),
        "dealer": (
            'DOC-QUOTED (dealer_orchestration_instructions_062026): "You may also consider '
            'the dealer alerts as defined below in general rules."'
        ),
        "straw": (
            'DOC-QUOTED (straw_orchestration_instructions_062026): "You may as well '
            'consider the alert fires for straw-borrower alerts as described below."'
        ),
        "employment": (
            'DOC-QUOTED (employment_orchestration_062026): "You may also consider the '
            "employment related alerts as defined below in general rules. Consider in "
            'particular those alerts that refer to employment deviations."'
        ),
        "income": (
            'DOC-QUOTED (income_orchestration_instructions_062026): "You may also consider '
            'the income-related fraud alerts as defined below under general rules."'
        ),
        "synthetic": (
            "INFERRED. General Rule 3 defines no Synthetic category. The synthetic "
            "orchestration doc names its own supplemental list (see DOMAIN_EXTRA_ALERTS), "
            "and 16 of those 18 IDs are members of the Identity-related set (the other "
            "two, 138 and 163, are Default), so Identity is the closest customer-defined "
            "container. Confirm with the customer."
        ),
        "rings": (
            "INFERRED. General Rule 3 defines no Rings category. The rings orchestration "
            'doc says "You may also consider the fraud-ring alerts 64, 65, 66, and 67" '
            "and all four are members of the Identity-related set. Confirm with the "
            "customer."
        ),
        "bustout": (
            'INFERRED for "Default". "Bust Out Risk" is DOC-QUOTED via General Rule 3. '
            "Default is included because the customer defines bust-out as "
            '"Coordinated rapid acquisition of multiple credit products ... followed by '
            'non-repayment" and instructs the bustout agent to "see epds, epd6, or fpds '
            'to see defaults". Confirm with the customer.'
        ),
    }
)

# ---------------------------------------------------------------------------
# Per-domain supplemental alert IDs named directly in that domain's orchestration
# doc, outside General Rule 3. Verbatim quotes carried alongside so the numbers are
# auditable.
# ---------------------------------------------------------------------------

DOMAIN_EXTRA_ALERTS: Final[Mapping[str, frozenset[int]]] = MappingProxyType(
    {
        "identity": frozenset({44, 46, 12, 145}),
        "dealer": frozenset(),
        "straw": frozenset(),
        "employment": frozenset(
            {68, 69, 70, 71, 117, 140, 142, 139, 141, 143, 144, 148}
        ),
        "income": frozenset(),
        "synthetic": frozenset(
            {9, 10, 19, 50, 51, 104, 105, 106, 107, 138, 139, 141, 146, 152, 158, 159,
             163, 165}
        ),
        "bustout": frozenset(),
        "rings": frozenset({64, 65, 66, 67}),
    }
)

DOMAIN_EXTRA_ALERT_QUOTES: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "identity": (
            "Some very useful alerts to look at are alert 44 (address change between "
            "applications) and 46 (phone number change between applications), help to "
            "look at suspicious identity behaviors related to change in personal details.",
            "Alerts 12 or 145 look at dealer distance from the borrower and also are "
            "fairly indicative on identity fraud as well.",
        ),
        "dealer": (),
        "straw": (),
        "employment": (
            "Any time you see an IncomePass score equal 999 OR any of the alerts 68-71, "
            "117, 140, 142 fire, then there is a very high risk of Employment fraud.",
            "You can leverage alerts 143 and 144 to help pick up on some of this "
            "behavior, but do not strictly rely on these alerts.",
            "You can utilize alert 148 to pick up on some of this activity, but you "
            "should also consider recent employment deviations when assessing employer "
            "risk for a borrower.",
            "Alerts 117 as well as alerts 139-142 are additional helpful alerts looking "
            "at some employment deviations, specifically within the context of previous "
            "fake employers.",
        ),
        "income": (),
        "synthetic": (
            "The Alerts you may use to supplement your analysis for synthetic identities "
            "are as follows: 9, 10, 19, 50, 51, 104, 105, 106, 107, 138, 139, 141, 146, "
            "152, 158, 159, 163, and 165.",
        ),
        "bustout": (),
        "rings": (
            "You may also consider the fraud-ring alerts 64, 65, 66, and 67, as "
            "additional supporters of other fraud ring patterns you may find.",
        ),
    }
)


def alerts_for_domain(domain: str) -> set[int]:
    """
    Every alert ID a given fraud domain's specialist is entitled to see.

    Union of the domain's General Rule 3 categories (DOMAIN_ALERT_CATEGORIES) and the
    supplemental IDs its own orchestration doc names (DOMAIN_EXTRA_ALERTS). Raises
    KeyError for an unknown domain rather than returning an empty set, because a typo'd
    domain must not silently produce a specialist with zero alerts.
    """
    if domain not in DOMAIN_ALERT_CATEGORIES:
        raise KeyError(
            f"unknown fraud domain {domain!r}; expected one of "
            f"{sorted(DOMAIN_ALERT_CATEGORIES)}"
        )
    ids: set[int] = set()
    for label in DOMAIN_ALERT_CATEGORIES[domain]:
        ids |= CATEGORY_ALERTS[label]
    ids |= DOMAIN_EXTRA_ALERTS[domain]
    return ids
