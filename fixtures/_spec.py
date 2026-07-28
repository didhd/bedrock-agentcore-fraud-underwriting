"""
The signal contract as the fixtures see it: names, ownership, and verbatim interpretations.

Three jobs, all of them about not inventing anything:

1. ``SIGNAL_OWNER`` / ``signals_for`` mirror ``signal_layer.registry``. The registry is the
   contract and lands in its own wave; this mirror lets the fixture set be authored and tested
   against the same names before then. ``tests/test_fixtures.py`` asserts the two agree
   name-for-name as soon as the registry is importable, so the mirror cannot silently drift.

2. ``bind_contract`` resolves the real signal-layer symbols. It tolerates ``signal_layer``'s
   package ``__init__`` eagerly importing ``registry``/``schema`` before those modules exist by
   loading the already-landed submodules directly. That seam is temporary by construction: a
   test asserts the direct-load path is unused once the package imports cleanly.

3. ``interpretation_for`` returns the customer's own sentence about a signal, read out of
   ``prompts/verbatim/`` at call time. Fixture context text quotes the customer rather than
   paraphrasing her, and a signal the customer never gave a threshold for gets an empty string,
   never an invented band.

Near-duplicate names are BOTH materialised, never aliased (XAG-006): renaming or de-duping
degrades whichever agent spells it the other way to "signal not present in the input", which its
own RULES section forbids it from working around.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Final, Mapping

REPO_ROOT: Final = Path(__file__).resolve().parent.parent
VERBATIM_DIR: Final = REPO_ROOT / "prompts" / "verbatim"

#: The customer's master-prompt input order (MSTR-004). Not alphabetical.
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

#: signal name -> the domain whose specialist prompt names it.
#:
#: One owner per name is deliberate: the customer gives each domain its own signal table
#: (SIGBOUND-02), and shared *concepts* travel under separate names in each prompt (XAG-006)
#: rather than one name in two tables. The five names literally spelled the same way in two
#: prompts are in ``SHARED_SIGNALS`` and appear in both views.
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

#: Signals whose EXACT name appears in more than one specialist prompt, and which therefore
#: appear in more than one domain view (XAG-006).
SHARED_SIGNALS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "phone_reuse_distinct_ssn_count": ("identity", "rings"),
        "address_reuse_distinct_ssn_count": ("identity", "rings"),
        "email_reuse_distinct_ssn_count": ("identity", "rings"),
        "rapid_app_velocity": ("synthetic", "bustout"),
        "ssn_income_high_variance_inconsistent_reporting": ("income", "bustout"),
    }
)

#: Every signal name a fixture must carry.
SIGNAL_NAMES: Final[tuple[str, ...]] = tuple(SIGNAL_OWNER)

#: The five names that are NOT literal substrings of any customer prompt, each mapped to the
#: customer text it is derived from.
#:
#: Recorded rather than quietly accepted, because "every signal name is the customer's" is a claim
#: Rebecca will check. Four of the five are halves of a compound the customer writes as one phrase
#: ("ssn_distinct_employers_all_time vs ssn_distinct_employers_30d/60d",
#: "address_ssn_count_same_building vs same_unit"); ``dealer_apps_last_7d`` is spelled out in the
#: process-guide PDF as the canonical signal/context pair rather than in the .txt prompts; and
#: ``has_coborrower`` / ``dealer_app_volume_24mo`` name a condition the customer states in prose
#: ("No co-borrower present:", "High-volume dealers (1000+ apps/24mo)") and which the straw and
#: dealer prompts both reason from. ``tests/test_fixtures.py`` asserts each anchor below is a real
#: substring of the customer corpus, so a derivation cannot be justified by an invented quote.
DERIVED_SIGNAL_NAMES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "ssn_distinct_employers_60d": (
            "ssn_distinct_employers_all_time vs ssn_distinct_employers_30d/60d"
        ),
        "address_ssn_count_same_unit": "address_ssn_count_same_building vs same_unit",
        "has_coborrower": "No co-borrower present: Primary straw mechanism absent.",
        "dealer_app_volume_24mo": "High-volume dealers (1000+ apps/24mo)",
        # PDF section 2.2, quoted in the spec bundle as the verbatim signal/context example. The
        # .txt prompts never name it, so it is derived from the PDF rather than from a prompt.
        "dealer_apps_last_7d": "",
    }
)

#: The context-key suffix the agents are told to look for (SIGBOUND-03, BST-005, RNG-005:
#: "columns that end in _context").
CONTEXT_SUFFIX: Final = "_context"


def context_key(signal: str) -> str:
    """The paired context key for a signal. The convention agents are instructed to rely on."""
    return f"{signal}{CONTEXT_SUFFIX}"


def domains_for(signal: str) -> tuple[str, ...]:
    """Every domain whose view carries this signal. Raises KeyError for an unknown name."""
    if signal in SHARED_SIGNALS:
        return SHARED_SIGNALS[signal]
    return (SIGNAL_OWNER[signal],)


def signals_for(domain: str) -> tuple[str, ...]:
    """
    Every signal name a domain's specialist is entitled to see.

    Mirrors ``signal_layer.registry.signals_for``. Raises KeyError for an unknown domain rather
    than returning an empty tuple: a typo'd domain must not silently yield a specialist with no
    signals at all, which is indistinguishable from a clean application.
    """
    if domain not in DOMAINS:
        raise KeyError(f"unknown fraud domain {domain!r}; expected one of {list(DOMAINS)}")
    return tuple(s for s in SIGNAL_NAMES if domain in domains_for(s))


# ---------------------------------------------------------------------------
# Binding to the real signal layer
# ---------------------------------------------------------------------------


def _install_namespace_shims() -> None:
    """
    Register import-free stand-ins for ``signal_layer`` and ``signal_layer.rules``.

    A signal-layer submodule loaded from its file still resolves its OWN absolute imports through
    the normal machinery — ``signal_layer.rules.r23_no_raw_ssn`` imports
    ``signal_layer.rules._source``, which re-enters the package ``__init__`` and fails again. So the
    parent packages are installed as path-carrying namespace shims: real submodule imports beneath
    them resolve from ``__path__``, and the ``__init__`` bodies (the only thing that needs
    ``registry``) never run. Existing entries are left alone, so this cannot shadow a package that
    already imported cleanly.
    """
    for name, directory in (
        ("signal_layer", REPO_ROOT / "signal_layer"),
        ("signal_layer.rules", REPO_ROOT / "signal_layer" / "rules"),
    ):
        if name in sys.modules:
            continue
        spec = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
        spec.submodule_search_locations = [str(directory)]
        shim = importlib.util.module_from_spec(spec)
        shim.__path__ = [str(directory)]  # type: ignore[attr-defined]
        sys.modules[name] = shim


def _load_submodule_directly(name: str) -> ModuleType:
    """
    Import ``signal_layer.<name>`` from its file, bypassing the package ``__init__``.

    ``name`` may be dotted (``rules.r23_no_raw_ssn``). The loaded module is registered in
    ``sys.modules`` under its real dotted name, so a second import of the same module — including
    one issued by another signal-layer module — reuses this instance rather than re-executing it.
    """
    dotted = f"signal_layer.{name}"
    if dotted in sys.modules:
        return sys.modules[dotted]
    relative = Path(*name.split(".")).with_suffix(".py")
    path = REPO_ROOT / "signal_layer" / relative
    if not path.is_file():
        raise ModuleNotFoundError(f"no module named {dotted!r} (looked for {path})")
    _install_namespace_shims()
    return importlib.import_module(dotted)


def rule_module(name: str) -> ModuleType:
    """
    One ``signal_layer.rules`` module, e.g. ``rule_module("r23_no_raw_ssn")``.

    Prefers the ordinary import so the real package is exercised whenever it can be. Falls back to
    the direct file load only while ``signal_layer/__init__`` still re-exports ``registry`` and
    ``schema``, which land in a later wave: the rule modules themselves import nothing from either,
    so the fallback runs the same code the package import would.
    """
    dotted = f"signal_layer.rules.{name}"
    try:
        return importlib.import_module(dotted)
    except ImportError:
        return _load_submodule_directly(f"rules.{name}")


def bind_contract() -> tuple[ModuleType, bool]:
    """
    Return ``signal_layer.alert_categories`` and whether the package imported cleanly.

    The signal layer's ``__init__`` re-exports ``registry`` and ``schema``, which land in a
    later wave than ``alert_categories``. Until they do, ``import signal_layer.alert_categories``
    raises even though the module itself is complete and dependency-free. Rather than duplicate
    the ten alert-ID sets — the one thing in this repo where a single wrong digit silently
    removes a detection — this loads the landed module directly and reports that it had to.
    """
    try:
        return importlib.import_module("signal_layer.alert_categories"), True
    except ImportError:
        return _load_submodule_directly("alert_categories"), False


def try_registry() -> ModuleType | None:
    """``signal_layer.registry`` if it has landed, else None. For drift-checking the mirror."""
    for loader in (lambda: importlib.import_module("signal_layer.registry"),
                   lambda: _load_submodule_directly("registry")):
        try:
            return loader()
        except (ImportError, ModuleNotFoundError):
            continue
    return None


def try_schema() -> ModuleType | None:
    """``signal_layer.schema`` if it has landed, else None."""
    for loader in (lambda: importlib.import_module("signal_layer.schema"),
                   lambda: _load_submodule_directly("schema")):
        try:
            return loader()
        except (ImportError, ModuleNotFoundError):
            continue
    return None


# ---------------------------------------------------------------------------
# The customer's own sentence about each signal
# ---------------------------------------------------------------------------

#: Signals whose interpretation line does not begin with the signal's own name, mapped to the
#: exact anchor text to search for. Every anchor is a literal substring of the named file; the
#: fixture tests re-derive the lines and fail on drift, so a typo here cannot survive.
_LINE_ANCHOR: Final[Mapping[str, tuple[str, str]]] = MappingProxyType(
    {
        "ssn_distinct_identity_combinations": (
            "identity_agent_prompt",
            "- ssn_distinct_identity_combinations = 1",
        ),
        "ssn_days_in_system": ("identity_agent_prompt", "- ssn_days_in_system = 0"),
        # IDEN-012's own line is what tells the agent to check this flag.
        "is_multi_unit_address": (
            "identity_agent_prompt",
            "- address_reuse_distinct_ssn_count:",
        ),
        "inquiries_in_2weeks": (
            "identity_orchestration_062026",
            "It's also useful to look at cases where the borrower's credit score",
        ),
        "dealer_risk_score": (
            "dealer_orchestration_instructions_062026",
            "21. There are two scores that measure dealer risk.",
        ),
        "dealer_app_volume_24mo": (
            "dealer_agent_prompt",
            "- High-volume dealers (1000+ apps/24mo)",
        ),
        "has_coborrower": ("straw_agent_prompt", "- No co-borrower present:"),
        "coborrower_appears_as_borrower_count": (
            "straw_agent_prompt",
            "- coborrower_appears_as_borrower_count or borrower_appears_as_coborrower_count",
        ),
        "borrower_appears_as_coborrower_count": (
            "straw_agent_prompt",
            "- coborrower_appears_as_borrower_count or borrower_appears_as_coborrower_count",
        ),
        "credit_score_gap": ("straw_agent_prompt", "- credit_score_gap > 100:"),
        "ssn_distinct_employers_all_time": (
            "employment_agent_prompt",
            "- ssn_distinct_employers_all_time vs",
        ),
        "ssn_distinct_employers_30d": (
            "employment_agent_prompt",
            "- ssn_distinct_employers_all_time vs",
        ),
        "ssn_distinct_employers_60d": (
            "employment_agent_prompt",
            "- ssn_distinct_employers_all_time vs",
        ),
        "address_ssn_count_same_building": (
            "synthetic_agent_prompt",
            "- address_ssn_count_same_building vs same_unit:",
        ),
        "address_ssn_count_same_unit": (
            "synthetic_agent_prompt",
            "- address_ssn_count_same_building vs same_unit:",
        ),
        "is_thin_file": ("synthetic_agent_prompt", "- is_thin_file + very_new_file_high_score:"),
        "very_new_file_high_score": (
            "synthetic_agent_prompt",
            "- is_thin_file + very_new_file_high_score:",
        ),
        "borr_ssn_in_negative_file": (
            "dealer_agent_prompt",
            "- borr_ssn_in_negative_file / borr_ssn_linked_to_synthetic_fraud:",
        ),
        "borr_ssn_linked_to_synthetic_fraud": (
            "dealer_agent_prompt",
            "- borr_ssn_in_negative_file / borr_ssn_linked_to_synthetic_fraud:",
        ),
        "previous_epd_count": (
            "dealer_agent_prompt",
            "- previous_epd_count / previous_999_count:",
        ),
        "previous_999_count": (
            "dealer_agent_prompt",
            "- previous_epd_count / previous_999_count:",
        ),
        "ssn_previous_apps_all_time": (
            "dealer_agent_prompt",
            "- ssn_previous_apps_all_time / ssn_previous_apps_90d:",
        ),
        "ssn_previous_apps_90d": (
            "dealer_agent_prompt",
            "- ssn_previous_apps_all_time / ssn_previous_apps_90d:",
        ),
    }
)

#: Signals the customer names but never gives an interpretation, threshold or band for. Their
#: context carries an empty ``customer_interpretation`` — an invented band would be worse than
#: an honest blank, and the bustout prompt is explicit that these have "no numeric threshold".
NO_INTERPRETATION: Final[frozenset[str]] = frozenset(
    {
        "ssn_apps_last_7d",
        "ssn_apps_last_30d",
        "ssn_income_increased_20_percent_last_30d_rapid_growth",
        "ssn_loan_exceeds_annual_income_unsustainable_debt",
        "ssn_loan_80_percent_or_more_annual_income_high_ratio",
        "employer_other_ssns_count",
        "dealer_apps_last_7d",
    }
)


def _read_verbatim(stem: str) -> str:
    path = VERBATIM_DIR / f"{stem}.txt"
    if not path.is_file():
        raise FileNotFoundError(f"no verbatim customer source at {path}")
    return path.read_text(encoding="utf-8")


def interpretation_for(signal: str) -> tuple[str, str]:
    """
    The customer's own sentence about a signal, plus the repo-relative file it came from.

    Returns ``("", "")`` for the signals in ``NO_INTERPRETATION``. Raises LookupError when a
    signal is expected to have a line and the anchor no longer matches — that means the customer
    updated her prompt and the fixture context text is now stale, which must fail loudly.
    """
    if signal in NO_INTERPRETATION:
        return ("", "")
    stem, anchor = _LINE_ANCHOR.get(signal, (None, f"- {signal}"))  # type: ignore[arg-type]
    stems = (stem,) if stem else (f"{SIGNAL_OWNER[signal]}_agent_prompt",)
    for candidate in stems:
        for line in _read_verbatim(candidate).splitlines():
            stripped = line.strip()
            if stripped.startswith(anchor) or (anchor in stripped and not anchor.startswith("- ")):
                text = stripped[2:].strip() if stripped.startswith("- ") else stripped
                return (text, f"prompts/verbatim/{candidate}.txt")
    raise LookupError(
        f"no interpretation line for {signal!r}: anchor {anchor!r} not found in "
        f"{[f'prompts/verbatim/{s}.txt' for s in stems]}. Either the customer's prompt changed "
        f"or the signal belongs in NO_INTERPRETATION."
    )


def all_interpretations() -> dict[str, tuple[str, str]]:
    """``interpretation_for`` over every signal, for building and for drift-checking fixtures."""
    return {s: interpretation_for(s) for s in SIGNAL_NAMES}


__all__ = [
    "CONTEXT_SUFFIX",
    "DERIVED_SIGNAL_NAMES",
    "DOMAINS",
    "NO_INTERPRETATION",
    "REPO_ROOT",
    "SHARED_SIGNALS",
    "SIGNAL_NAMES",
    "SIGNAL_OWNER",
    "VERBATIM_DIR",
    "all_interpretations",
    "bind_contract",
    "context_key",
    "domains_for",
    "interpretation_for",
    "rule_module",
    "signals_for",
    "try_registry",
    "try_schema",
]
