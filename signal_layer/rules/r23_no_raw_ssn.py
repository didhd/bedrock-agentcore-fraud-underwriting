"""
General Rule 23 — the only hard compliance boundary in the set.

The customer writes this rule, and only this rule, entirely in capitals:

    NEVER INCLUDE RAW SSN VALUES OR ANY PARTS OF RAW SSN VALUES IN THE RESULTS. YOU SHOULD
    ONLY FOCUS ON THE HASHED SSN VALUES.

"ANY PARTS" is the operative phrase. It bans last-4, first-3, masked forms like
``***-**-1234``, and an SSN fragment embedded in narrative prose or in example SQL — not
merely a projected ``borr_ssn`` column. Every other General Rule is an analytical preference
whose violation produces a wrong number; this one's violation is a PII incident.

So it is enforced as a gate, not a guideline: ``assert_no_raw_ssn()`` walks any structure
recursively and raises. Wire it as the **last** thing that touches a payload before it leaves
the signal layer, after every enrichment step, because a later step is exactly what would
reintroduce a value an earlier check cleared.

Defence in depth matters here — the customer's architecture wants a Bedrock Guardrail PII
filter on model output as well, and the semantic layer must never project ``borr_ssn`` in the
first place. This module is the middle layer of the three, and the only one that runs
offline: see ``ENFORCEMENT_LAYERS``.

**Precision.** A nine-digit-number scanner run over a fraud payload full of amounts, VINs and
timestamps would fire constantly, and a check that cries wolf gets disabled. So detection is
scoped: hyphenated/spaced SSN shapes anywhere; bare nine-digit runs only in a field whose NAME
says SSN, or in text adjacent to an SSN word. Loan amounts, zips, phones, dates and VINs do
not fire. ``FALSE_POSITIVE_POLICY`` records the trade-off.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final, Iterator, Mapping, Sequence

from signal_layer.rules._source import rule_text

RULE_ID: Final = "23"
RULE_TEXT: Final = rule_text(RULE_ID)

#: The field name prefix that marks an SSN value as already hashed and therefore permitted.
HASHED_SSN_PREFIXES: Final[tuple[str, ...]] = ("hash_ssn", "hash_", "sha256:", "hashed_")

#: Field names that legitimately carry SSN-derived data, in hashed form only.
ALLOWED_SSN_FIELDS: Final[tuple[str, ...]] = (
    "hash_ssn",
    "borr_hash_ssn",
    "coborr_hash_ssn",
    "hashed_ssn",
    "ssn_hash",
)

ENFORCEMENT_LAYERS: Final[tuple[str, ...]] = (
    "semantic layer: never project borr_ssn; expose hash_ssn only (the customer's own "
    "bustout output contract already writes 'borr_ssn (hash)')",
    "signal layer: assert_no_raw_ssn() as the last gate before any payload leaves for an "
    "agent - this module",
    "model output: a Bedrock Guardrail PII filter configured to BLOCK, as the backstop for "
    "anything the model composes itself",
)

FALSE_POSITIVE_POLICY: Final = (
    "A bare nine-digit run is indistinguishable from a loan amount, an account number or a "
    "VIN fragment. Detecting every one of them would make this gate fire on ordinary "
    "payloads, and a gate that fires on ordinary payloads gets turned off - which is a "
    "worse compliance outcome than a narrower gate that stays on. So: SSN-shaped strings "
    "(NNN-NN-NNNN, NNN NN NNNN, and masked forms) are detected ANYWHERE; bare nine-digit "
    "runs are detected only under an SSN-named key or beside an SSN word in text. Zips, "
    "phones, dates, VINs and amounts do not fire. The Bedrock Guardrail PII filter is the "
    "backstop for the residue."
)

# NNN-NN-NNNN and NNN NN NNNN. Requires the group structure, which no amount or date has.
_SSN_DELIMITED: Final = re.compile(r"(?<![\d-])\d{3}[-\s]\d{2}[-\s]\d{4}(?![\d-])")

# Masked and partial forms: ***-**-1234, XXX-XX-1234, ###-##-1234.
_SSN_MASKED: Final = re.compile(
    r"(?<![\w-])[*xX#]{3}[-\s]?[*xX#]{2}[-\s]?\d{4}(?![\w-])"
)

# "last 4 of SSN: 1234", "SSN ending 1234", "ssn last four 1234".
_SSN_PARTIAL_PROSE: Final = re.compile(
    r"\bssn\b[^\n]{0,40}?(?:last\s*(?:4|four)|ending|ends?\s+(?:in|with)|xxx)\b[^\n]{0,12}?(?<!\d)\d{4}(?!\d)",
    re.IGNORECASE,
)
_SSN_PARTIAL_PROSE_REVERSED: Final = re.compile(
    r"\b(?:last\s*(?:4|four)|final\s*(?:4|four))\b[^\n]{0,20}?\bssn\b[^\n]{0,12}?(?<!\d)\d{4}(?!\d)",
    re.IGNORECASE,
)

# A bare nine-digit run. Only ever applied under an SSN-named key or next to an SSN word.
_NINE_DIGITS: Final = re.compile(r"(?<!\d)\d{9}(?!\d)")

# An SSN word in surrounding text, which promotes a bare nine-digit run to a finding.
_SSN_WORD: Final = re.compile(r"\bssn\b|\bsocial\s+security\s+num", re.IGNORECASE)

# A key that names an SSN field. hash_ssn and friends are excluded by _is_allowed_key.
_SSN_KEY: Final = re.compile(r"ssn|social_?security", re.IGNORECASE)


class RawSSNError(ValueError):
    """
    A raw SSN value, or part of one, was found in a payload.

    Deliberately not a subclass of a warning type and deliberately not catchable by a
    generic data-quality handler: General Rule 23 is a compliance boundary, so the correct
    response is to fail the request, not to degrade it.
    """


@dataclass(frozen=True)
class SSNFinding:
    """One raw-SSN detection: where it was, what shape it matched, and a redacted excerpt."""

    path: str
    pattern: str
    excerpt: str

    def __str__(self) -> str:
        return f"{self.path}: {self.pattern} ({self.excerpt})"


def _redact(text: str) -> str:
    """Redact a finding excerpt, so the exception message is not itself a rule violation."""
    return re.sub(r"\d", "#", text)[:48]


def _is_allowed_key(key: str) -> bool:
    """True when a field name marks an already-hashed SSN, which the rule explicitly permits."""
    lowered = key.lower()
    if lowered in ALLOWED_SSN_FIELDS:
        return True
    return any(p in lowered for p in HASHED_SSN_PREFIXES)


def _is_hashed_value(value: str) -> bool:
    """
    True when a value looks like a hash rather than an SSN.

    The repo's own fixtures use "hash_ssn_7f3a91"; a real deployment would use a hex digest.
    Either way the marker is a non-numeric prefix or a length no SSN has.
    """
    lowered = value.lower()
    if any(lowered.startswith(p) for p in HASHED_SSN_PREFIXES):
        return True
    return bool(re.fullmatch(r"[0-9a-f]{32,}", lowered))


def _scan_text(text: str, path: str, *, key_is_ssn: bool) -> Iterator[SSNFinding]:
    """Scan one string for raw-SSN shapes. ``key_is_ssn`` enables bare nine-digit detection."""
    for pattern, label in (
        (_SSN_DELIMITED, "SSN-shaped NNN-NN-NNNN"),
        (_SSN_MASKED, "masked SSN (NNN-NN-NNNN with a visible last 4)"),
        (_SSN_PARTIAL_PROSE, "SSN last-4 disclosed in prose"),
        (_SSN_PARTIAL_PROSE_REVERSED, "SSN last-4 disclosed in prose"),
    ):
        for m in pattern.finditer(text):
            yield SSNFinding(path=path, pattern=label, excerpt=_redact(m.group(0)))
    if key_is_ssn or _SSN_WORD.search(text):
        if _is_hashed_value(text.strip()):
            return
        for m in _NINE_DIGITS.finditer(text):
            yield SSNFinding(
                path=path,
                pattern=(
                    "bare 9-digit value under an SSN-named field"
                    if key_is_ssn
                    else "bare 9-digit value adjacent to an SSN reference"
                ),
                excerpt=_redact(m.group(0)),
            )


def iter_raw_ssn_findings(obj: Any, path: str = "$") -> Iterator[SSNFinding]:
    """
    Walk any payload and yield every raw-SSN finding (General Rule 23).

    Recurses through dicts, lists, tuples and sets; inspects dict keys as well as values,
    because a dict keyed by SSN leaks just as effectively as one valued by it. Integers and
    floats are stringified before scanning — the customer's rule says "ANY PARTS OF RAW SSN
    VALUES", and 123456789 stored as an int is a raw SSN, which is the case a
    string-only scanner misses.
    """
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            key_text = str(key)
            child = f"{path}.{key_text}"
            scalar = isinstance(value, (str, int, float)) and not isinstance(value, bool)
            allowed = _is_allowed_key(key_text)
            names_ssn = bool(_SSN_KEY.search(key_text))
            if names_ssn and not allowed and scalar:
                yield from _scan_text(str(value), child, key_is_ssn=True)
            elif names_ssn and allowed and scalar:
                # A field NAMED as hashed is not trusted to CONTAIN a hash. If the
                # hashing step is skipped or misconfigured, a raw SSN lands in hash_ssn
                # and a name-based allowlist would wave it straight through - the exact
                # failure General Rule 23 is written to prevent. So the value must
                # actually look hashed.
                text = str(value).strip()
                if not _is_hashed_value(text):
                    yield from _scan_text(text, child, key_is_ssn=True)
            else:
                yield from iter_raw_ssn_findings(value, child)
            # A raw SSN used as a dict key.
            yield from _scan_text(key_text, f"{path}.<key>", key_is_ssn=False)
        return
    if isinstance(obj, (list, tuple, set, frozenset)):
        for i, item in enumerate(obj):
            yield from iter_raw_ssn_findings(item, f"{path}[{i}]")
        return
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (str, int, float)):
        yield from _scan_text(str(obj), path, key_is_ssn=False)


def find_raw_ssn(obj: Any) -> list[SSNFinding]:
    """Collect every raw-SSN finding in a payload (General Rule 23). Empty means clean."""
    seen: set[tuple[str, str, str]] = set()
    out: list[SSNFinding] = []
    for f in iter_raw_ssn_findings(obj):
        key = (f.path, f.pattern, f.excerpt)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def assert_no_raw_ssn(obj: Any, *, context: str = "payload") -> None:
    """
    The last gate before a payload leaves the signal layer (General Rule 23).

    Verbatim: "NEVER INCLUDE RAW SSN VALUES OR ANY PARTS OF RAW SSN VALUES IN THE RESULTS.
    YOU SHOULD ONLY FOCUS ON THE HASHED SSN VALUES."

    Raises RawSSNError listing every finding by path, with digits redacted so the exception
    message and any log line derived from it are not themselves violations. Call this AFTER
    every enrichment step, immediately before handing a payload to an agent: a later step is
    exactly what reintroduces a value an earlier check cleared.
    """
    findings = find_raw_ssn(obj)
    if findings:
        detail = "\n  ".join(str(f) for f in findings)
        raise RawSSNError(
            f"General Rule 23 violated in {context}: {len(findings)} raw-SSN finding(s).\n"
            f"  {detail}\nRule: {RULE_TEXT}"
        )


def is_clean(obj: Any) -> bool:
    """Non-raising form of assert_no_raw_ssn(), for reporting rather than gating."""
    return not find_raw_ssn(obj)


def redact_ssns(text: str) -> str:
    """
    Redact SSN-shaped substrings from free text (General Rule 23).

    A remediation aid for logs and error messages, NOT a substitute for the gate: General
    Rule 23 says never include, not include-then-redact. A payload that needed redacting
    should fail assert_no_raw_ssn() and be fixed upstream.
    """
    out = _SSN_DELIMITED.sub("[SSN REDACTED]", text)
    return _SSN_MASKED.sub("[SSN REDACTED]", out)


def allowed_ssn_projection(alias: str = "v") -> Sequence[str]:
    """The only SSN-derived columns the semantic layer may project (General Rule 23)."""
    return tuple(f"{alias}.{f}" for f in ("hash_ssn",))


__all__ = [
    "ALLOWED_SSN_FIELDS",
    "ENFORCEMENT_LAYERS",
    "FALSE_POSITIVE_POLICY",
    "HASHED_SSN_PREFIXES",
    "RULE_ID",
    "RULE_TEXT",
    "RawSSNError",
    "SSNFinding",
    "allowed_ssn_projection",
    "assert_no_raw_ssn",
    "find_raw_ssn",
    "is_clean",
    "iter_raw_ssn_findings",
    "redact_ssns",
]
