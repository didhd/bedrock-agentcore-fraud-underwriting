"""
General Rule 7 — the default definition, and the wrong reading of it.

The rule excludes "exclusively chargeoffs". The obvious misreading is
``AND chargeoff_flag = false``, which also throws away every application that charged off
*and* had an EPD — and those are defaults. Under-counting defaults makes a fraudulent
portfolio look clean, which is the direction of error that loses money.

So a default is exactly ``epd_flag OR epd6_flag``, and the chargeoff flag never appears in
the numerator at all. ``is_pure_chargeoff()`` exists only to *report* the excluded
population, not to filter it.
"""

from __future__ import annotations

from typing import Any, Final, Iterable, Mapping

from signal_layer.rules._source import rule_text

RULE_ID: Final = "7"
RULE_TEXT: Final = rule_text(RULE_ID)

#: The two EPD flags the rule calls "either of the epd flags". The customer's data
#: dictionary spells them epd and epd6 (early payment default, and at 6 months); the
#: bustout orchestration names "epds, epd6, or fpds" as the default indicators.
EPD_FLAG_FIELDS: Final[tuple[str, ...]] = ("epd_flag", "epd6_flag")

#: Field-name aliases accepted for each logical flag, because the two source tables spell
#: them differently ("some fields are different between the two tables", General Rule 18).
FLAG_ALIASES: Final[Mapping[str, tuple[str, ...]]] = {
    "epd_flag": ("epd_flag", "epd", "epd_ind", "is_epd"),
    "epd6_flag": ("epd6_flag", "epd6", "epd6_ind", "is_epd6"),
    "chargeoff_flag": ("chargeoff_flag", "chargeoff", "charge_off_flag", "is_chargeoff"),
}

DEFAULT_DEFINITION: Final = (
    "is_default = (epd_flag OR epd6_flag). General Rule 7 excludes applications that "
    "are EXCLUSIVELY chargeoffs - chargeoff flag set with NEITHER epd flag set. A "
    "chargeoff that also has an epd flag DOES count as a default, so the rule must "
    "never be implemented as 'AND chargeoff_flag = false'."
)


class DefaultFlagError(ValueError):
    """A row's default flags could not be read as booleans."""


def _flag(row: Mapping[str, Any], logical: str) -> bool:
    """Read one logical flag from a row, tolerating the source tables' field aliases."""
    for alias in FLAG_ALIASES[logical]:
        if alias in row:
            value = row[alias]
            if value is None:
                return False
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return value != 0
            if isinstance(value, str):
                token = value.strip().upper()
                if token in {"1", "Y", "YES", "TRUE", "T"}:
                    return True
                if token in {"", "0", "N", "NO", "FALSE", "F", "NULL"}:
                    return False
                raise DefaultFlagError(
                    f"{alias}={value!r} is not a recognisable flag value"
                )
            raise DefaultFlagError(
                f"{alias}={value!r} ({type(value).__name__}) is not a flag value"
            )
    return False


def is_default(row: Mapping[str, Any]) -> bool:
    """
    Decide whether one application counts as a default (General Rule 7).

    Verbatim: "Please don't include chargeoffs (exclusively chargeoffs-meaning apps that
    have the chargeoff flag but not either of the epd flags) in the calculation for
    defaults."

    Returns ``epd_flag OR epd6_flag``. The chargeoff flag is deliberately not consulted:
    a chargeoff with an EPD is a default, and a chargeoff without one has no EPD flag set
    and so is already excluded. This is the single definition of default in the system;
    no caller assembles its own numerator.
    """
    return _flag(row, "epd_flag") or _flag(row, "epd6_flag")


def is_pure_chargeoff(row: Mapping[str, Any]) -> bool:
    """
    True for the population General Rule 7 excludes: chargeoff flag set, NEITHER epd flag.

    Used to report the size of the excluded set, never to filter — filtering on the
    chargeoff flag is the misreading this module exists to prevent.
    """
    return _flag(row, "chargeoff_flag") and not (
        _flag(row, "epd_flag") or _flag(row, "epd6_flag")
    )


def default_rate(rows: Iterable[Mapping[str, Any]]) -> float:
    """
    Default rate over a population, per General Rule 7.

    Numerator: rows where either EPD flag is set. Denominator: all rows EXCEPT the pure
    chargeoffs, which the rule removes "in the calculation for defaults" — they are
    neither a default nor a clean loan, so leaving them in the denominator would deflate
    the rate. Returns 0.0 for an empty population rather than raising, because a lender
    with no loans in a window is a normal state, not an error.
    """
    considered = [r for r in rows if not is_pure_chargeoff(r)]
    if not considered:
        return 0.0
    return sum(1 for r in considered if is_default(r)) / len(considered)


def default_counts(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """
    Break a population into the three groups General Rule 7 distinguishes.

    Returns {"defaults", "non_defaults", "excluded_pure_chargeoffs", "considered",
    "total"}. Emitting the excluded count alongside the rate is what lets an agent state
    the denominator it used, which is the only way a reviewer can check the rule was
    applied.
    """
    materialised = list(rows)
    excluded = [r for r in materialised if is_pure_chargeoff(r)]
    considered = [r for r in materialised if not is_pure_chargeoff(r)]
    defaults = [r for r in considered if is_default(r)]
    return {
        "defaults": len(defaults),
        "non_defaults": len(considered) - len(defaults),
        "excluded_pure_chargeoffs": len(excluded),
        "considered": len(considered),
        "total": len(materialised),
    }


def default_sql_predicate(alias: str = "v") -> str:
    """
    The SQL form of General Rule 7's default definition, for the semantic view.

    Returns the is_default expression. The exclusion of pure chargeoffs is implicit and
    intentional: such a row has neither EPD flag, so the expression is already false for
    it, and no ``chargeoff_flag = false`` predicate is emitted anywhere.
    """
    return f"({alias}.epd_flag or {alias}.epd6_flag)"


__all__ = [
    "DEFAULT_DEFINITION",
    "EPD_FLAG_FIELDS",
    "FLAG_ALIASES",
    "DefaultFlagError",
    "RULE_ID",
    "RULE_TEXT",
    "default_counts",
    "default_rate",
    "default_sql_predicate",
    "is_default",
    "is_pure_chargeoff",
]
