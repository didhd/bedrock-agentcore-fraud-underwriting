"""
The customer's General Rules, read back out of their own documents at import time.

Every rule docstring, every catalog entry and every coverage-table row in this package
quotes its rule from here — not from a string a developer retyped. The text is sliced
out of ``prompts.loader.ORCHESTRATION_NOTES``, which is itself a byte-identical copy of
Point Predictive's nine orchestration documents guarded by a sha256 manifest. If the
customer edits a rule and we re-import their doc, the docstrings, the catalog and the
coverage table all move together and the tests fail loudly on any rule that changed
meaning. That is the point: a hand-copied rule table drifts, an extracted one cannot.

The eight domain documents do not agree with each other. The General Rules block is
repeated in all eight, but with per-document edits (rule 6 is dropped from some, rule 11
gains a sentence, rule 24's product table is truncated) and with RTF-conversion damage
(doubled internal spaces, hard wraps). ``IDENTITY`` is the reference text: the identity
document carries the longest form of every rule, all 29 numbered items, and the full
official product-description table. ``variants_of()`` exposes the per-document
differences so a reviewer can see them rather than discover them.
"""

from __future__ import annotations

import re
from types import MappingProxyType
from typing import Final, Mapping

from prompts.loader import ORCHESTRATION_NOTES

#: The document whose General Rules block is the reference text for this package.
#: Chosen because it is the only one carrying all 29 numbered rules in their longest
#: form (see the module docstring).
REFERENCE_DOMAIN: Final = "identity"

#: The 29 rule ids the customer actually numbers, in document order. Note 0.5 and 1.5:
#: rules were inserted after the list was written and never renumbered, so the ids are
#: strings, not ints, and sort order is document order — never numeric order.
RULE_IDS: Final[tuple[str, ...]] = (
    "0.5", "1", "1.5", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13",
    "14", "15", "16", "17", "18", "19", "20", "21", "22", "23", "24", "25", "26", "27",
)

# The "General Rules:" heading is present in seven of the eight documents. The
# employment document omits it and opens straight into "PLEASE FOLLOW ALL OF THESE
# RULES BELOW BEFORE GETTING RESULTS." followed by rule 0.5, so both are accepted.
_HEADING: Final = re.compile(
    r"^[ \t]*(?:General Rules:|PLEASE FOLLOW ALL OF THESE RULES BELOW BEFORE GETTING RESULTS\.)[ \t]*$",
    re.MULTILINE,
)

# A rule opens a line with its number and a dot. "1.5" has no trailing dot in the
# source ("1.5 Also, any time...") so the dot is optional; the alternation puts the
# two-part ids first so "1.5" is never mis-read as "1".
_RULE_START: Final = re.compile(r"^[ \t]*(0\.5|1\.5|\d{1,2})\.?[ \t]+(?=\S)")


def _extract(text: str) -> dict[str, str]:
    """Slice one document's General Rules block into {rule_id: verbatim block}."""
    heading = _HEADING.search(text)
    body = text[heading.end():] if heading else text
    lines = body.splitlines()
    starts: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = _RULE_START.match(line)
        if m:
            starts.append((i, m.group(1)))
    out: dict[str, str] = {}
    for n, (start, rule_id) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        # The RTF -> text conversion left every rule indented by four spaces in seven
        # documents and by zero in the eighth. Stripping per line makes the eight
        # documents comparable without touching intra-line text.
        block = "\n".join(ln.strip() for ln in lines[start:end]).strip()
        if rule_id not in out:  # first occurrence wins; see _DUPLICATE_NUMBERING
            out[rule_id] = block
    return out


#: {domain: {rule_id: verbatim text}} for all eight specialist documents.
RULES_BY_DOMAIN: Final[Mapping[str, Mapping[str, str]]] = MappingProxyType(
    {d: MappingProxyType(_extract(t)) for d, t in ORCHESTRATION_NOTES.items()}
)

#: {rule_id: verbatim text} from the reference (identity) document.
RULES: Final[Mapping[str, str]] = RULES_BY_DOMAIN[REFERENCE_DOMAIN]


class RuleTextMissing(LookupError):
    """A rule id was requested that the customer's reference document does not define."""


def rule_text(rule_id: str) -> str:
    """
    Return one General Rule verbatim, as the customer wrote it.

    The returned string starts with the customer's own rule number ("7. Please don't
    include chargeoffs...") because that number is part of how they cite it to each
    other, and dropping it would make a quote un-greppable against their document.
    """
    try:
        return RULES[rule_id]
    except KeyError as exc:
        raise RuleTextMissing(
            f"General Rule {rule_id!r} is not in the {REFERENCE_DOMAIN} document; "
            f"known ids: {', '.join(RULES)}"
        ) from exc


def variants_of(rule_id: str) -> dict[str, str]:
    """
    Return {domain: text} for every document whose wording of this rule differs from
    the reference document's.

    Empty means all eight documents agree character-for-character. Non-empty is not a
    defect — the customer edits rules per agent — but it is a fact a reviewer must see
    before deciding which wording the code implements.
    """
    reference = rule_text(rule_id)
    return {
        domain: rules[rule_id]
        for domain, rules in RULES_BY_DOMAIN.items()
        if domain != REFERENCE_DOMAIN
        and rule_id in rules
        and rules[rule_id] != reference
    }


def normalised(text: str) -> str:
    """
    Collapse runs of whitespace so two documents' wording of the same rule can be
    compared for *meaning*.

    The straw document's rule 7 reads "exclusively   chargeoffs-meaning apps ... not
    either of the  epd  flags" — three spaces and two double spaces the RTF conversion
    inserted. Comparing raw text calls that a different rule; comparing normalised text
    correctly calls it the same rule.
    """
    return re.sub(r"\s+", " ", text).strip()


__all__ = [
    "REFERENCE_DOMAIN",
    "RULES",
    "RULES_BY_DOMAIN",
    "RULE_IDS",
    "RuleTextMissing",
    "normalised",
    "rule_text",
    "variants_of",
]
