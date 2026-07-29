"""
General Rule 17 — fake-record exclusion, and the null-safety that makes it safe.

This is the most dangerous rule in the set, because the wrong implementation looks right and
deletes real applications.

**The null trap.** ``ar.borr_email <> ALL (SELECT borr_email FROM __fake_emails)`` evaluates
to NULL when ``ar.borr_email`` is NULL. NULL is not TRUE, so the row fails the WHERE clause
and vanishes. Every application with no email on file is deleted — not flagged, deleted, with
no error. The customer pre-empts this by writing out the exact idiom to use:

    AND (ar.borr_email <> ALL (SELECT borr_email FROM __fake_emails) or ar.borr_email is null)

**The phone carve-out.** The phone exclusion is deliberately weaker than the email and VIN
ones, and the rule spells out why: exclude only when a phone is common-consortium **and there
are NO OTHER VALID PHONE NUMBERS**. Keep the record if all phones are null, or if at least
one phone is not in the common table. Implementing phone like email would delete a real
borrower who happens to share a workplace switchboard.

**Don't join.** "Don't join to these tables." A join fans out rows (General Rule 1's problem
again); anti-membership on the primary source's cleaned field does not.

Phone membership is tested on General Rule 26 canonical form, or differently-formatted
matches slip through.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Final, Iterable, Mapping, Sequence

from signal_layer.rules._source import rule_text
from signal_layer.rules.r02_normalize import norm_string
from signal_layer.rules.r26_phone_equiv import is_comparable_phone, norm_phone

RULE_ID: Final = "17"
RULE_TEXT: Final = rule_text(RULE_ID)

#: The three exclusion sources the rule names, and the primary-source field each tests.
EXCLUSION_SOURCES: Final[Mapping[str, str]] = {
    "__fake_emails": "borr_email",
    "CONSORTIUM_COMMON_VIN": "v_vin",
    "common_consortium_phone": "borr phone numbers",
}

#: Concrete fake emails the identity/income/employment/straw/synthetic/bustout/rings
#: orchestration bodies name: "Often you'll see a fake emails like 123@gmail.com,
#: NA@gmail.com, NONE@yahoo.com, NOEMAIL@aol.com, etc." Held uppercase because General Rule
#: 2 standardises email with UPPER(TRIM(x)) before comparison. "etc." makes the list
#: non-exhaustive, which is what the TEST-marker heuristic below is for.
KNOWN_FAKE_EMAILS: Final[frozenset[str]] = frozenset(
    {"123@GMAIL.COM", "NA@GMAIL.COM", "NONE@YAHOO.COM", "NOEMAIL@AOL.COM"}
)

#: The marker the rule names for names, addresses and emails: "Emails that contain something
#: like '%TEST%' for example... Anywhere in the name of the application or address, etc. if
#: you see 'TEST'."
TEST_MARKER: Final = "TEST"

#: Fields the TEST marker is checked in.
TEST_MARKER_FIELDS: Final[tuple[str, ...]] = (
    "borr_email",
    "borr_first_name",
    "borr_last_name",
    "borr_street_addr",
    "borr_emp_name",
    "dealer_name",
)

#: "10 applications from the same lender on the same day are likely also not real
#: applications (just test applications)". The customer writes "10" and hedges with
#: "likely", so this is a default threshold, exposed as a parameter, not a constant truth.
SAME_DAY_BULK_THRESHOLD: Final = 10

NULL_SAFETY_NOTE: Final = (
    "General Rule 17's mandated idiom is: AND (ar.borr_email <> ALL (SELECT borr_email "
    "FROM __fake_emails) or ar.borr_email is null). The 'or ... is null' disjunct is not "
    "optional. A bare NOT IN / <> ALL evaluates to NULL for a null field, NULL is not TRUE, "
    "and the row is silently DROPPED - so the naive form deletes every legitimate "
    "application that has no email (or no VIN) on file, with no error and no trace. The "
    "customer states the requirement twice: 'be sure not to filter out records that have a "
    "null value in the field you're looking at.'"
)

PHONE_CARVE_OUT_NOTE: Final = (
    "The phone exclusion is deliberately weaker than the email and VIN ones. Verbatim: "
    "'filter out records that have one of the borr phone numbers in the common consortium "
    "phone number source and NO OTHER VALID PHONE NUMBERS (i.e. if either all phone numbers "
    "are null or at least one of the fields is a phone number not in the common consortium "
    "table the record should be included in results).' So: all phones null -> KEEP; any "
    "phone not in the common table -> KEEP; exclude only when at least one phone is present "
    "and every present phone is common-consortium."
)


class RecordExclusion(str):
    """A human-readable reason one record was excluded. A str subclass so it logs cleanly."""


@dataclass(frozen=True)
class FakeRecordSources:
    """
    The three exclusion sources of General Rule 17, as sets rather than joinable tables.

    Holding them as sets is the enforcement of "Don't join to these tables": there is no
    table to join to, only a membership test on the primary source's cleaned field.
    Normalisation is applied on construction — emails via General Rule 2's UPPER(TRIM),
    phones via General Rule 26's last-10-digits — so membership tests cannot miss a match
    on formatting alone.
    """

    fake_emails: frozenset[str] = field(default_factory=lambda: KNOWN_FAKE_EMAILS)
    common_vins: frozenset[str] = frozenset()
    common_phones: frozenset[str] = frozenset()

    @classmethod
    def build(
        cls,
        fake_emails: Iterable[str] | None = None,
        common_vins: Iterable[str] | None = None,
        common_phones: Iterable[Any] | None = None,
    ) -> "FakeRecordSources":
        """Normalise and freeze the three sources. Emails default to KNOWN_FAKE_EMAILS."""
        emails = (
            frozenset(norm_string(e) for e in fake_emails if e)
            if fake_emails is not None
            else KNOWN_FAKE_EMAILS
        )
        vins = frozenset(norm_string(v) for v in (common_vins or ()) if v)
        phones = frozenset(p for p in (norm_phone(x) for x in (common_phones or ())) if p)
        return cls(fake_emails=emails, common_vins=vins, common_phones=phones)


def is_fake_email(value: Any, sources: FakeRecordSources | None = None) -> bool:
    """
    Test one email against General Rule 17's fake-email source, null-safely.

    Returns False for a NULL/blank email: "be sure not to filter out records that have a
    null value in the field you're looking at". This is the ``or ar.borr_email is null``
    disjunct of the mandated idiom, expressed in Python.
    """
    src = sources or FakeRecordSources()
    normalised = norm_string(value)
    if not normalised:
        return False
    return normalised in src.fake_emails


def is_common_vin(value: Any, sources: FakeRecordSources | None = None) -> bool:
    """
    Test one VIN against CONSORTIUM_COMMON_VIN, null-safely (General Rule 17).

    Returns False for a NULL/blank VIN, same null-safety as is_fake_email().
    """
    src = sources or FakeRecordSources()
    normalised = norm_string(value)
    if not normalised:
        return False
    return normalised in src.common_vins


def has_test_marker(row: Mapping[str, Any], fields: Iterable[str] = TEST_MARKER_FIELDS) -> bool:
    """
    Look for the TEST marker (General Rule 17's heuristic half).

    Verbatim: "We shouldn't be looking at any applications that are obviously fake. Emails
    that contain something like '%TEST%' for example... Anywhere in the name of the
    application or address, etc. if you see 'TEST'."

    Substring match on the uppercased value, which is what LIKE '%TEST%' does. Note this
    over-matches by construction — a borrower surnamed "Testa" or a street named "Testament
    Rd" is caught. That is the customer's own predicate and it is applied as written; the
    ``reasons`` list on the ExclusionReport is what lets a reviewer see and override such a
    case, rather than the record disappearing silently.
    """
    for f in fields:
        value = row.get(f)
        if value is None:
            continue
        if TEST_MARKER in str(value).upper():
            return True
    return False


def phone_excluded(
    row: Mapping[str, Any],
    sources: FakeRecordSources | None = None,
    phone_fields: Sequence[str] = ("borr_home_phone", "borr_cell_phone", "borr_work_phone"),
) -> bool:
    """
    Apply General Rule 17's phone exclusion, with its explicit carve-out.

    Verbatim: "finally filter out records that have one of the borr phone numbers in the
    common consortium phone number source and NO OTHER VALID PHONE NUMBERS (i.e.if either
    all phone numbers are null or at least one of the fields is a phone number not in the
    common consortium table the record should be included in results)."

    Excludes only when at least one VALID phone is present AND every valid phone is in the
    common-consortium set:

      all phones null / placeholder junk  -> False (keep)
      any phone not in common set         -> False (keep)
      every valid phone in common set     -> True  (exclude)

    "Valid" is the rule's own word ("NO OTHER VALID PHONE NUMBERS"), and is read as General
    Rule 26's comparability test: a sub-10-digit placeholder such as "0" is not a valid phone
    number, so a row carrying only placeholders is treated as having no phone and is KEPT.
    Counting junk as valid would exclude legitimate applications whose phone fields were
    never populated properly.

    Comparison is on General Rule 26 canonical form, so "14724933572" in the row matches
    "4724933572" in the common set.
    """
    src = sources or FakeRecordSources()
    present = [
        p
        for p in (norm_phone(row.get(f)) for f in phone_fields)
        if is_comparable_phone(p)
    ]
    if not present:
        return False  # all phone numbers null (or junk) -> the record is included
    return all(p in src.common_phones for p in present)


@dataclass(frozen=True)
class ExclusionReport:
    """
    Why one record was or was not excluded (General Rule 17).

    Carrying the reasons rather than returning a bare boolean is what makes an exclusion
    reviewable: the TEST-marker and same-day-bulk heuristics are the customer's own
    approximations ("likely also not real"), and an analyst must be able to see which
    heuristic removed a record.
    """

    excluded: bool
    reasons: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.excluded


def classify_record(
    row: Mapping[str, Any],
    sources: FakeRecordSources | None = None,
    *,
    check_test_marker: bool = True,
) -> ExclusionReport:
    """
    Decide whether one record is an obvious fake, per General Rule 17.

    Verbatim (opening): "Can you please filter out records that have a borr_email in fake
    emails source, also filter out records that have v_vin in the CONSORTIUM_COMMON_VIN
    source, and finally filter out records that have one of the borr phone numbers in the
    common consortium phone number source and NO OTHER VALID PHONE NUMBERS ... When you do
    filter these, be sure not to filter out records that have a null value in the field
    you're looking at."

    All three source tests are null-safe: a record with no email, no VIN and no phone is
    never excluded by them. The same-day-bulk heuristic is not applied here because it is a
    property of a population, not of a row — see exclude_fake_records().
    """
    src = sources or FakeRecordSources()
    reasons: list[str] = []
    if is_fake_email(row.get("borr_email"), src):
        reasons.append(f"borr_email {row.get('borr_email')!r} is in the fake-emails source")
    if is_common_vin(row.get("v_vin"), src):
        reasons.append(f"v_vin {row.get('v_vin')!r} is in CONSORTIUM_COMMON_VIN")
    if phone_excluded(row, src):
        reasons.append(
            "every present borrower phone is in the common-consortium phone source and "
            "there is no other valid phone number"
        )
    if check_test_marker and has_test_marker(row):
        reasons.append("a TEST marker appears in the name, address or email")
    return ExclusionReport(excluded=bool(reasons), reasons=tuple(reasons))


def _day(value: Any) -> str:
    """Reduce a date/datetime/string to a YYYY-MM-DD day key."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def same_day_bulk_groups(
    rows: Iterable[Mapping[str, Any]],
    *,
    threshold: int = SAME_DAY_BULK_THRESHOLD,
    lender_field: str = "hash_lender_id",
    date_field: str = "application_date",
) -> dict[tuple[Any, str], int]:
    """
    Find (lender, day) groups that look like bulk test submissions (General Rule 17).

    Verbatim: "10 applications from the same lender on the same day are likely also not real
    applications (just test applications)".

    Returns {(lender, day): count} for groups at or above ``threshold``. Note the customer's
    hedge — "likely" — so this is reported, never silently applied: for a large lender, ten
    applications in a day is an ordinary Tuesday. exclude_fake_records() therefore requires
    the caller to opt in.
    """
    counts: dict[tuple[Any, str], int] = {}
    for row in rows:
        key = (row.get(lender_field), _day(row.get(date_field)))
        if key[0] is None or not key[1]:
            continue
        counts[key] = counts.get(key, 0) + 1
    return {k: v for k, v in counts.items() if v >= threshold}


def exclude_fake_records(
    rows: Iterable[Mapping[str, Any]],
    sources: FakeRecordSources | None = None,
    *,
    check_test_marker: bool = True,
    apply_same_day_bulk: bool = False,
    same_day_threshold: int = SAME_DAY_BULK_THRESHOLD,
) -> tuple[list[Mapping[str, Any]], list[tuple[Mapping[str, Any], ExclusionReport]]]:
    """
    Apply General Rule 17 to a population. Returns (kept, excluded_with_reasons).

    Returning both halves is the design point: a filter that only returns survivors makes a
    null-safety bug undetectable, whereas an excluded list with reasons can be asserted
    against in a test and shown to an analyst.

    ``apply_same_day_bulk`` defaults False — the customer's own wording is "likely also not
    real", and ten applications from one lender in one day is normal for a large lender.
    """
    src = sources or FakeRecordSources()
    materialised = list(rows)
    bulk = (
        same_day_bulk_groups(materialised, threshold=same_day_threshold)
        if apply_same_day_bulk
        else {}
    )
    kept: list[Mapping[str, Any]] = []
    excluded: list[tuple[Mapping[str, Any], ExclusionReport]] = []
    for row in materialised:
        report = classify_record(row, src, check_test_marker=check_test_marker)
        reasons = list(report.reasons)
        if bulk:
            key = (row.get("hash_lender_id"), _day(row.get("application_date")))
            if key in bulk:
                reasons.append(
                    f"{bulk[key]} applications from the same lender on the same day "
                    f"({key[1]}), at or above the {same_day_threshold}-application "
                    "bulk-test threshold"
                )
        if reasons:
            excluded.append((row, ExclusionReport(True, tuple(reasons))))
        else:
            kept.append(row)
    return kept, excluded


#: A bare SQL identifier: letters, digits, ``_``, ``$``, and a leading ``__`` as the
#: customer's own exclusion tables use (``__fake_emails``). Deliberately does NOT
#: allow quotes, whitespace, semicolons, parentheses or dots -- every one of those is
#: how a string-built predicate turns into a second statement.
_SQL_IDENTIFIER: Final = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


class UnsafeSQLIdentifier(ValueError):
    """An identifier passed to a SQL builder is not a bare identifier.

    Raised instead of quoting-and-continuing because these arguments are supposed to
    come from this repository's own constants, not from a request. A value that needs
    escaping is a caller bug, and failing loudly is the behaviour that surfaces it.
    """


def _checked_identifier(value: str, *, role: str) -> str:
    """Return ``value`` if it is a bare SQL identifier, else raise.

    Interpolating an identifier is unavoidable here: a table or column name cannot be
    a bind parameter in any SQL dialect, so parameterisation is not an option for this
    predicate. Validation against an allowlist pattern is the correct control, and it
    is applied at the one place identifiers enter the string.
    """
    if not isinstance(value, str) or not _SQL_IDENTIFIER.match(value):
        raise UnsafeSQLIdentifier(
            f"{role}={value!r} is not a bare SQL identifier "
            f"(expected /{_SQL_IDENTIFIER.pattern}/). Identifiers here come from this "
            "repository's own constants; a value needing escapes is a caller bug."
        )
    return value


def null_safe_exclusion_sql(
    column: str, source_table: str, source_column: str | None = None, alias: str = "ar"
) -> str:
    """
    Emit General Rule 17's mandated null-safe anti-membership predicate.

    Reproduces the customer's own example form exactly:

        (ar.borr_email <> ALL (SELECT borr_email FROM __fake_emails) or ar.borr_email is null)

    Not a join — "Don't join to these tables" — because a join fans out rows and inflates
    every count downstream.

    SECURITY: every identifier is validated against :data:`_SQL_IDENTIFIER` before it
    reaches the f-string. Table and column names cannot be bind parameters in any SQL
    dialect, so an allowlist check -- not parameterisation -- is the available control.
    Raises :class:`UnsafeSQLIdentifier` on anything that is not a bare identifier.
    """
    src_col = _checked_identifier(source_column or column, role="source_column")
    checked_column = _checked_identifier(column, role="column")
    checked_alias = _checked_identifier(alias, role="alias")
    checked_table = _checked_identifier(source_table, role="source_table")
    qualified = f"{checked_alias}.{checked_column}"
    return (
        f"({qualified} <> ALL (SELECT {src_col} FROM {checked_table}) "  # nosec B608
        f"or {qualified} is null)"
    )


class UnsafeExclusionSQL(ValueError):
    """Generated exclusion SQL omits the null disjunct General Rule 17 requires."""


_ANTI_MEMBERSHIP: Final = re.compile(
    r"(?P<col>[\w.]+)\s+(?:<>\s*ALL|NOT\s+IN)\s*\(", re.IGNORECASE
)


def validate_exclusion_sql(sql: str) -> None:
    """
    Reject anti-membership predicates missing their ``or <col> is null`` disjunct (Rule 17).

    Scans for ``<col> <> ALL (`` and ``<col> NOT IN (`` and requires an ``is null`` test on
    the same column somewhere in the statement. This is the check that prevents the silent
    deletion of every null-valued row; see NULL_SAFETY_NOTE.
    """
    problems: list[str] = []
    for m in _ANTI_MEMBERSHIP.finditer(sql):
        col = m.group("col")
        bare = col.split(".")[-1]
        null_test = re.compile(
            rf"\b(?:{re.escape(col)}|[\w]*\.?{re.escape(bare)})\s+is\s+null\b",
            re.IGNORECASE,
        )
        if not null_test.search(sql):
            problems.append(
                f"{col} is used in an anti-membership predicate with no "
                f"'or {col} is null' disjunct"
            )
    if problems:
        raise UnsafeExclusionSQL(
            "General Rule 17 violated: "
            + "; ".join(problems)
            + f"\n{NULL_SAFETY_NOTE}"
        )


__all__ = [
    "EXCLUSION_SOURCES",
    "ExclusionReport",
    "FakeRecordSources",
    "KNOWN_FAKE_EMAILS",
    "NULL_SAFETY_NOTE",
    "PHONE_CARVE_OUT_NOTE",
    "RULE_ID",
    "RULE_TEXT",
    "RecordExclusion",
    "SAME_DAY_BULK_THRESHOLD",
    "TEST_MARKER",
    "TEST_MARKER_FIELDS",
    "UnsafeExclusionSQL",
    "classify_record",
    "exclude_fake_records",
    "has_test_marker",
    "is_common_vin",
    "is_fake_email",
    "null_safe_exclusion_sql",
    "phone_excluded",
    "same_day_bulk_groups",
    "validate_exclusion_sql",
]
