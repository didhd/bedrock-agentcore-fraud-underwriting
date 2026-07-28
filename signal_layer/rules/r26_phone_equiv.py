"""
General Rule 26 — one phone number stored two ways is one phone number.

The customer's worked example is the whole rule: ``14724933572`` and ``4724933572`` are the
same number. Without canonicalisation that phone splits into two keys, and every reuse
count *undercounts* — a phone shared by four borrowers, two of whom are stored with the
leading 1, reads as two phones shared by two borrowers each. Neither crosses a threshold.
A real shared-phone ring goes unreported, which is a false negative, and false negatives in
fraud detection are the expensive direction.

Two second-order consequences the rule's placement in the document does not spell out:

  * Rule 17's common-consortium-phone exclusion must run on canonicalised values, or the
    anti-membership test misses differently-formatted matches.
  * Identity alert 46 is "phone number change between applications". A pure format change
    must not register as a change.

The customer's gloss "The first number in that case is area code" is imprecise — the
stripped leading 1 is the country/long-distance code — but the mechanic they prescribe
("Look at the last 10 digits if the phone number has 11 digits") is unambiguous, and that
is what is implemented. Their imprecision is noted, not corrected.
"""

from __future__ import annotations

import re
from typing import Any, Final, Iterable, Mapping

from signal_layer.rules._source import rule_text

RULE_ID: Final = "26"
RULE_TEXT: Final = rule_text(RULE_ID)

#: The canonical length. "Look at the last 10 digits if the phone number has 11 digits."
CANONICAL_LENGTH: Final = 10

#: Minimum digits for a value to be comparable as a phone number. The rule speaks only of
#: 10- and 11-digit numbers, so anything shorter cannot be a North American number at all.
MIN_COMPARABLE_DIGITS: Final = 10

SHORT_VALUE_NOTE: Final = (
    "General Rule 26 addresses only 10- and 11-digit numbers. Placeholder junk does appear in "
    "these fields ('0', '000-0000', an extension digit-string), and treating it as a phone "
    "would be actively harmful: same_phone('0', '0') returning True links two unrelated "
    "borrowers into a ring on a placeholder. So a value with fewer than 10 digits is "
    "normalised transparently by norm_phone() but is NOT comparable - is_comparable_phone() "
    "is False, it never matches, and it never counts toward a distinct-phone total. This also "
    "reads correctly against General Rule 17's wording, which says 'NO OTHER VALID PHONE "
    "NUMBERS': junk is not a valid phone number."
)

#: The customer's worked example, and therefore this module's specification.
WORKED_EXAMPLE: Final[tuple[str, str]] = ("14724933572", "4724933572")

#: The borrower phone fields the customer's schema carries (named in the rings document's
#: Shared Attribute Fields, plus the employer phone the employment document uses).
PHONE_FIELDS: Final[tuple[str, ...]] = (
    "borr_home_phone",
    "borr_cell_phone",
    "borr_work_phone",
    "borr_emp_phone",
)

GLOSS_NOTE: Final = (
    "General Rule 26 explains the mechanic as \"The first number in that case is area "
    "code\". That gloss is imprecise - the stripped leading 1 is the country / "
    "long-distance code, and the area code is the following three digits - but the "
    "instruction it accompanies (\"Look at the last 10 digits if the phone number has 11 "
    "digits\") is unambiguous and is what norm_phone() implements. The customer's wording "
    "is recorded rather than corrected."
)

_NON_DIGIT: Final = re.compile(r"\D")


def norm_phone(value: Any) -> str:
    """
    Canonicalise a phone number to its last 10 digits (General Rule 26).

    Verbatim: "Phone numbers like 14724933572 and 4724933572 should be considered the same
    phone number NOT DIFFERENT. The first number in that case is area code. Look at the
    last 10 digits if the phone number has 11 digits."

    Strips all formatting first, so "(472) 493-3572", "472-493-3572", "+1 472 493 3572" and
    14724933572 all yield "4724933572". Integer input is accepted because a phone stored as
    a number has already lost any leading zero — and a 10-digit NANP number cannot start
    with 0 or 1, so nothing is lost.

    Returns "" for a missing or unusable value. Numbers with fewer than 10 digits are
    returned as-is rather than padded: a 7-digit local number is genuinely ambiguous, and
    zero-padding it would make it collide with a real number. Such short values are NOT
    comparable — see is_comparable_phone() and SHORT_VALUE_NOTE. Numbers longer than 11 digits
    (international) also take the last 10 — the rule only specifies the 11-digit case, so
    this is an extension, and it is noted here rather than hidden.
    """
    if value is None or isinstance(value, bool):
        return ""
    digits = _NON_DIGIT.sub("", str(value))
    if not digits:
        return ""
    if len(digits) > CANONICAL_LENGTH:
        return digits[-CANONICAL_LENGTH:]
    return digits


def is_comparable_phone(value: Any) -> bool:
    """
    Whether a value is a phone number two records may be matched on (General Rule 26).

    Requires at least 10 digits, the shortest form the rule contemplates. Placeholder junk
    ('0', '000-0000', a bare extension) normalises to a non-empty string but must never
    match — see SHORT_VALUE_NOTE for the ring-fabrication failure that prevents.
    """
    return len(norm_phone(value)) >= MIN_COMPARABLE_DIGITS


def same_phone(a: Any, b: Any) -> bool:
    """
    Compare two phone numbers per General Rule 26.

    same_phone("14724933572", "4724933572") is True — the customer's example. Two missing
    values are NOT equal: absence is not a match, and returning True there would link every
    borrower with no phone into one enormous ring. The same applies to placeholder junk: two
    records both holding "0" are not two records sharing a phone.
    """
    left = norm_phone(a)
    right = norm_phone(b)
    if not is_comparable_phone(left) or not is_comparable_phone(right):
        return False
    return left == right


def distinct_phones(values: Iterable[Any]) -> set[str]:
    """
    The set of distinct canonical phone numbers (General Rule 26).

    Formatting variants of one number collapse to one entry; blanks and sub-10-digit
    placeholders are dropped. This is the measure every phone-reuse signal must use.
    """
    return {p for p in (norm_phone(v) for v in values) if is_comparable_phone(p)}


def count_distinct_phones(values: Iterable[Any]) -> int:
    """Count distinct canonical phone numbers (General Rule 26)."""
    return len(distinct_phones(values))


def row_phones(row: Mapping[str, Any], fields: Iterable[str] = PHONE_FIELDS) -> set[str]:
    """Every comparable phone number on one row, across the borrower phone fields (Rule 26)."""
    return {
        p for p in (norm_phone(row.get(f)) for f in fields) if is_comparable_phone(p)
    }


def phone_changed(before: Any, after: Any) -> bool:
    """
    Whether a borrower's phone genuinely changed between two applications.

    Supports identity alert 46 ("phone number change between applications"). A reformatting
    of the same number is NOT a change — that is the direct consequence of General Rule 26,
    and treating it as one would fire alert 46 on a data-entry convention. Returns False
    when either side is missing or is not a comparable phone: an appearing or disappearing
    phone is not a *change*, and neither is a placeholder being replaced by a real number.
    """
    left = norm_phone(before)
    right = norm_phone(after)
    if not is_comparable_phone(left) or not is_comparable_phone(right):
        return False
    return left != right


def norm_phone_sql(column: str, alias: str = "v") -> str:
    """
    The SQL form of General Rule 26: ``right(regexp_replace(v.col, '[^0-9]', ''), 10)``.

    The regexp_replace is required, not cosmetic: RIGHT(phone, 10) on a formatted string
    such as "(472) 493-3572" returns ") 493-3572" and silently produces a garbage key.
    """
    qualified = f"{alias}.{column}"
    return f"right(regexp_replace({qualified}, '[^0-9]', ''), {CANONICAL_LENGTH})"


__all__ = [
    "CANONICAL_LENGTH",
    "GLOSS_NOTE",
    "MIN_COMPARABLE_DIGITS",
    "PHONE_FIELDS",
    "RULE_ID",
    "RULE_TEXT",
    "SHORT_VALUE_NOTE",
    "WORKED_EXAMPLE",
    "count_distinct_phones",
    "distinct_phones",
    "is_comparable_phone",
    "norm_phone",
    "norm_phone_sql",
    "phone_changed",
    "row_phones",
    "same_phone",
]
