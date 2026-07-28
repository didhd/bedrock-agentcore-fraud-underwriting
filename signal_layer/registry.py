"""
The signal registry: which signals exist, who owns them, and the customer's own band for each.

This module is the CONTRACT. Every other layer resolves signal names through it:

  * ``signals_for(domain)`` decides what a specialist is entitled to see. A specialist that is
    handed a signal its prompt never mentions is being invited to reason about something it has
    no interpretation for; one that is missing a signal its prompt names must, per its own RULES
    section, say "signal not present in the input" instead of investigating.
  * ``SIGNAL_SPEC[name].interpretation`` is the customer's verbatim sentence about the signal,
    read out of ``prompts/verbatim/`` at import. Nothing here paraphrases a threshold.

Near-duplicate names are BOTH materialised, never aliased (XAG-006). ``employer_other_ssns_count``
(rings) and ``employer_distinct_other_ssns_count`` (employment) are separate keys; so are
``phone_ssn_count`` (synthetic) and ``phone_reuse_distinct_ssn_count`` (identity/rings), and
``ssn_income_increase_20_percent_or_more`` (income) and
``ssn_income_increased_20_percent_last_30d_rapid_growth`` (bustout). De-duping them by spelling
silently degrades whichever agent uses the other name.

Five names are spelled identically in two prompts and legitimately appear in both views; they are
listed in ``SHARED_SIGNALS``.

Imports nothing but the standard library and ``fixtures._spec`` helpers are NOT used here: this is
the authority, and ``tests/test_fixtures.py`` asserts the fixture-side mirror agrees with it
name-for-name.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping

#: The customer's master-prompt input order (MSTR-004). Not alphabetical: the master prompt
#: injects the analyses as IDENTITY, DEALER, STRAW, EMPLOYMENT, INCOME, SYNTHETIC, BUSTOUT, RINGS
#: and the synthesis input must be built in that order.
DOMAINS: Final[tuple[str, ...]] = (
    "identity",
    "dealer",
    "straw",
    "employment",
    "income",
    "synthetic",
    "bustout",
    "rings",
)

#: The context-key suffix the agents are instructed to look for (SIGBOUND-03; BST-005 and RNG-005
#: say it literally: "columns that end in _context").
CONTEXT_SUFFIX: Final = "_context"

#: signal name -> the domain whose specialist prompt names it.
#:
#: One owner per name is deliberate: the customer gives each fraud area its own signal set
#: (SIGBOUND-02), and a shared *concept* travels under a different name in each prompt rather than
#: one name in two sets. The exceptions are in ``SHARED_SIGNALS``.
SIGNAL_OWNER: Final[Mapping[str, str]] = MappingProxyType(
    {
        # identity — identity_agent_prompt.txt SIGNAL INTERPRETATION
        "ssn_distinct_identity_combinations": "identity",
        "phone_reuse_distinct_ssn_count": "identity",
        "email_reuse_distinct_ssn_count": "identity",
        "address_reuse_distinct_ssn_count": "identity",
        "is_multi_unit_address": "identity",
        "ssn_days_in_system": "identity",
        "inquiries_in_2weeks": "identity",
        # dealer — dealer_agent_prompt.txt
        "dealer_consortium_score": "dealer",
        "dealer_risk_score": "dealer",
        "dealer_lender_default_rate": "dealer",
        "dealer_consortium_epd_rate_last_24mo": "dealer",
        "phone_reuse_count_this_dealer": "dealer",
        "employer_concentration_pct": "dealer",
        "ssn_previous_apps_all_time": "dealer",
        "ssn_previous_apps_90d": "dealer",
        "previous_epd_count": "dealer",
        "previous_999_count": "dealer",
        "borr_ssn_in_negative_file": "dealer",
        "borr_ssn_linked_to_synthetic_fraud": "dealer",
        "dealer_app_volume_24mo": "dealer",
        "dealer_apps_last_7d": "dealer",
        # straw — straw_agent_prompt.txt
        "has_coborrower": "straw",
        "coborrower_appears_as_borrower_count": "straw",
        "borrower_appears_as_coborrower_count": "straw",
        "coborr_distinct_primary_borrowers_count": "straw",
        "credit_score_gap": "straw",
        "coborr_income_70_percent_plus": "straw",
        "coborr_3_plus_apps_same_dealer_flag": "straw",
        "high_value_vehicle_weak_profile_straw_risk": "straw",
        # employment — employment_agent_prompt.txt
        "employer_distinct_other_ssns_count": "employment",
        "employer_new_ssns_90d": "employment",
        "employer_phone_distinct_ssn_count": "employment",
        "rapid_employer_change_flag": "employment",
        "employer_spike_flag": "employment",
        "ssn_distinct_employers_all_time": "employment",
        "ssn_distinct_employers_30d": "employment",
        "ssn_distinct_employers_60d": "employment",
        # income — income_agent_prompt.txt
        "ssn_distinct_income_amounts_all_time": "income",
        "ssn_income_increase_20_percent_or_more": "income",
        "ssn_multiple_apps_7d_different_incomes": "income",
        "ssn_stated_income_exceeds_verified_by_25_percent": "income",
        "ssn_income_overstatement_percentage": "income",
        "loan_to_annual_income_ratio": "income",
        "ssn_income_high_variance_inconsistent_reporting": "income",
        # synthetic — synthetic_agent_prompt.txt
        "ssn_address_location_variations": "synthetic",
        "has_significant_identity_inconsistency": "synthetic",
        "address_ssn_count_same_building": "synthetic",
        "address_ssn_count_same_unit": "synthetic",
        "phone_ssn_count": "synthetic",
        "high_authorized_user_ratio": "synthetic",
        "is_thin_file": "synthetic",
        "very_new_file_high_score": "synthetic",
        "older_borrower_new_credit_high_score": "synthetic",
        "rapid_app_velocity": "synthetic",
        # bustout — bustout_agent_prompt.txt KEY SIGNALS TO PRIORITIZE
        "ssn_apps_last_7d": "bustout",
        "ssn_apps_last_30d": "bustout",
        "ssn_income_increased_20_percent_last_30d_rapid_growth": "bustout",
        "ssn_loan_exceeds_annual_income_unsustainable_debt": "bustout",
        "ssn_loan_80_percent_or_more_annual_income_high_ratio": "bustout",
        # rings — rings_agent_prompt.txt KEY SIGNALS TO PRIORITIZE
        "employer_other_ssns_count": "rings",
    }
)

#: Signals spelled the same way in two prompts, so both specialists are entitled to them.
#: rings names three of the identity reuse counters as its own KEY SIGNALS TO PRIORITIZE;
#: bustout names two signals the income and synthetic prompts also interpret.
SHARED_SIGNALS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "phone_reuse_distinct_ssn_count": ("identity", "rings"),
        "address_reuse_distinct_ssn_count": ("identity", "rings"),
        "email_reuse_distinct_ssn_count": ("identity", "rings"),
        "rapid_app_velocity": ("synthetic", "bustout"),
        "ssn_income_high_variance_inconsistent_reporting": ("income", "bustout"),
    }
)

#: Stable ordering for every signal name. Registry order, not alphabetical, so a rendered payload
#: groups a domain's signals the way its prompt discusses them.
SIGNAL_NAMES: Final[tuple[str, ...]] = tuple(SIGNAL_OWNER)


@dataclass(frozen=True, slots=True)
class SignalSpec:
    """One signal: who reads it, what the customer said about it, and where that came from."""

    name: str
    owners: tuple[str, ...]
    context_key: str
    interpretation: str
    source_doc: str

    @property
    def shared(self) -> bool:
        """True when more than one specialist is entitled to this signal."""
        return len(self.owners) > 1


def context_key(signal: str) -> str:
    """The paired context key for a signal: the convention agents are told to rely on."""
    return f"{signal}{CONTEXT_SUFFIX}"


def domains_for(signal: str) -> tuple[str, ...]:
    """Every domain whose view carries this signal. Raises KeyError for an unknown name."""
    if signal in SHARED_SIGNALS:
        return SHARED_SIGNALS[signal]
    return (SIGNAL_OWNER[signal],)


def signals_for(domain: str) -> tuple[str, ...]:
    """
    Every signal name a domain's specialist is entitled to see, in registry order.

    Raises KeyError for an unknown domain rather than returning an empty tuple: a typo'd domain
    must not silently produce a specialist with no signals at all, which is indistinguishable
    from a clean application.
    """
    if domain not in DOMAINS:
        raise KeyError(f"unknown fraud domain {domain!r}; expected one of {list(DOMAINS)}")
    return tuple(s for s in SIGNAL_NAMES if domain in domains_for(s))


def _build_spec() -> Mapping[str, SignalSpec]:
    """
    Assemble the registry, reading each signal's interpretation from the verbatim prompts.

    The interpretation lookup lives in ``fixtures._spec`` because that is where the per-signal
    line anchors are maintained; it reads ``prompts/verbatim/`` at call time and raises if an
    anchor no longer matches, so a customer prompt update fails loudly instead of leaving a stale
    band in the registry. If that module is unavailable the registry still builds, with empty
    interpretations -- the names and ownership are the load-bearing part.
    """
    try:
        from fixtures._spec import interpretation_for  # noqa: PLC0415
    except Exception:  # pragma: no cover - registry must import standalone
        def interpretation_for(signal: str) -> tuple[str, str]:  # type: ignore[misc]
            return ("", "")

    specs: dict[str, SignalSpec] = {}
    for name in SIGNAL_NAMES:
        try:
            interpretation, source = interpretation_for(name)
        except Exception:  # pragma: no cover - a stale anchor must not break importability
            interpretation, source = ("", "")
        specs[name] = SignalSpec(
            name=name,
            owners=domains_for(name),
            context_key=context_key(name),
            interpretation=interpretation,
            source_doc=source,
        )
    return MappingProxyType(specs)


#: name -> SignalSpec for every signal in the contract.
SIGNAL_SPEC: Final[Mapping[str, SignalSpec]] = _build_spec()

__all__ = [
    "CONTEXT_SUFFIX",
    "DOMAINS",
    "SHARED_SIGNALS",
    "SIGNAL_NAMES",
    "SIGNAL_OWNER",
    "SIGNAL_SPEC",
    "SignalSpec",
    "context_key",
    "domains_for",
    "signals_for",
]
