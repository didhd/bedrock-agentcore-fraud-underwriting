"""
General Rule 25 — sentinel scrubbing before any credit-age reasoning.

``signal_layer.bands.is_valid_time_in_file()`` already implements the time-in-file half of this
rule and is imported, not reimplemented. This module adds the half bands.py does not cover —
the oldest-tradeline date ("and the date of the oldest trade line is null or zero") — and the
row-level scrub that applies both together.

Why the sentinels matter more here than elsewhere: the synthetic agent's entire thesis is
thin-file behaviour, and it reads ``time_in_file_months``. A raw 9999 presents as a maximally
established credit file, which masks exactly the synthetic thin file the agent is hunting. A
raw 0 or negative presents as a brand-new file, which manufactures a synthetic flag on a
borrower who simply has no value populated. The rule fires in both directions, so the scrub
must run before any interpretation, not as a post-hoc sanity check.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Final, Mapping

from signal_layer.bands import (
    CREDIT_REPORT_VALIDITY_RULE,
    TIME_IN_FILE_ERROR_SENTINELS,
    is_valid_time_in_file,
)
from signal_layer.rules._source import rule_text

RULE_ID: Final = "25"
RULE_TEXT: Final = rule_text(RULE_ID)

#: signal_layer.bands owns the time-in-file half of General Rule 25 and is imported here
#: rather than duplicated: two copies of a validity rule drift, and a drifted validity rule
#: means two agents disagree about whether the same credit file exists.
TIME_IN_FILE_VALIDATOR: Final = is_valid_time_in_file

#: Re-exported so a caller of this module has the verbatim rule without a second import.
BANDS_RULE_TEXT: Final = CREDIT_REPORT_VALIDITY_RULE

#: The fields this rule governs.
TIME_IN_FILE_FIELDS: Final[tuple[str, ...]] = ("time_in_file_months", "time_in_file")
OLDEST_TRADELINE_FIELDS: Final[tuple[str, ...]] = (
    "date_oldest_trade_line",
    "date_oldest_tradeline",
    "oldest_trade_line_date",
)

#: Values that mean "no oldest-tradeline date". The customer names null and zero; "0",
#: "0000-00-00" and the epoch-zero forms are the same value after a type round-trip.
TRADELINE_NULL_EQUIVALENTS: Final[frozenset[str]] = frozenset(
    {"", "0", "0.0", "00000000", "0000-00-00", "NULL", "NONE", "NAN", "N/A", "NA"}
)

SYNTHETIC_IMPACT_NOTE: Final = (
    "General Rule 25 must be applied BEFORE any thin-file or credit-age reasoning. A raw "
    "9999 in time_in_file_months reads as an implausibly long, maximally established credit "
    "file and masks a synthetic thin file; a raw 0 or negative reads as a brand-new file and "
    "manufactures a false thin-file/synthetic flag. The rule fires in both directions, so "
    "scrubbing after interpretation is too late."
)


def is_valid_oldest_tradeline(value: Any) -> bool:
    """
    Apply the oldest-tradeline half of General Rule 25.

    Verbatim: "Ignore credit report data when it is missing or invalid. Specifically, treat
    time in file as null when the value is less than or equal to zero or clearly an error,
    like 9999, and the date of the oldest trade line is null or zero."

    False for None, zero in any numeric or string spelling, and blank. True for a real date
    or a plausible date-like string. A negative numeric value is also False: a date cannot be
    negative, so it is the same class of corruption as zero.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return False
    if isinstance(value, (datetime, date)):
        return True
    if isinstance(value, (int, float)):
        if value != value:  # NaN
            return False
        return value > 0
    text = str(value).strip()
    if not text:
        return False
    if text.upper() in TRADELINE_NULL_EQUIVALENTS:
        return False
    # "0000-00-00" style zero-dates, and any all-zero digit string.
    if not any(ch.isdigit() and ch != "0" for ch in text):
        return False
    return True


def clean_time_in_file(value: Any) -> int | float | None:
    """
    Coerce an invalid time-in-file to None, per General Rule 25.

    Delegates the validity decision to signal_layer.bands.is_valid_time_in_file() — the
    single definition of the rule — and returns the value unchanged when valid, None when
    not. Returning None rather than 0 is the point: 0 is a *value* that thin-file logic
    would happily interpret, whereas None forces the caller to handle absence.
    """
    return value if is_valid_time_in_file(value) else None


def clean_oldest_tradeline(value: Any) -> Any:
    """Coerce an invalid oldest-tradeline date to None, per General Rule 25."""
    return value if is_valid_oldest_tradeline(value) else None


def scrub_credit_fields(row: Mapping[str, Any]) -> dict[str, Any]:
    """
    Return a copy of a row with General Rule 25's invalid credit values coerced to None.

    Applies to every alias in TIME_IN_FILE_FIELDS and OLDEST_TRADELINE_FIELDS that the row
    actually carries. Adds ``credit_report_usable``: False when either governed field was
    scrubbed, so a downstream agent can say "credit data unavailable" instead of reasoning
    off a sentinel. Fields the row does not have are not invented.
    """
    out = dict(row)
    scrubbed: list[str] = []
    for f in TIME_IN_FILE_FIELDS:
        if f in out and not is_valid_time_in_file(out[f]):
            if out[f] is not None:
                scrubbed.append(f)
            out[f] = None
    for f in OLDEST_TRADELINE_FIELDS:
        if f in out and not is_valid_oldest_tradeline(out[f]):
            if out[f] is not None:
                scrubbed.append(f)
            out[f] = None
    out["credit_report_usable"] = not scrubbed
    if scrubbed:
        out["credit_report_scrubbed_fields"] = tuple(scrubbed)
    return out


def is_thin_file_assessable(row: Mapping[str, Any]) -> bool:
    """
    Whether thin-file reasoning is permitted for this row (General Rule 25).

    False when time-in-file is invalid or missing. Guards the synthetic agent's core signal:
    an unusable credit file must produce "cannot assess", never "thin file". See
    SYNTHETIC_IMPACT_NOTE.
    """
    for f in TIME_IN_FILE_FIELDS:
        if f in row:
            return is_valid_time_in_file(row[f])
    return False


__all__ = [
    "BANDS_RULE_TEXT",
    "OLDEST_TRADELINE_FIELDS",
    "RULE_ID",
    "RULE_TEXT",
    "SYNTHETIC_IMPACT_NOTE",
    "TIME_IN_FILE_ERROR_SENTINELS",
    "TIME_IN_FILE_FIELDS",
    "TIME_IN_FILE_VALIDATOR",
    "TRADELINE_NULL_EQUIVALENTS",
    "clean_oldest_tradeline",
    "clean_time_in_file",
    "is_thin_file_assessable",
    "is_valid_oldest_tradeline",
    "is_valid_time_in_file",
    "scrub_credit_fields",
]
