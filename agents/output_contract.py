"""
Detect where a specialist's prose breaks the customer's own output contract.

WHY THIS EXISTS

The tier-quality sweep (``evals/results/tier_quality.json``, 48 blind-judged analyses)
found two fidelity defects that no model swap fixes, because they are not model defects:

1. **All 24 bustout and rings analyses invented a title.** Those two prompts define no
   title at all -- they say "Return only the final analysis" / "Return only final
   analysis" -- yet every analysis from every tier opened with one, in eleven different
   spellings (``# FRAUD RING DETECTION ANALYSIS``, ``# Bust-Out Fraud Risk Analysis``,
   ``# RINGS FRAUD RISK ANALYSIS``, ...). 24 of 24, across Haiku 4.5, Sonnet 5 and Opus 5.

2. Some of those invented titles also carry a **risk label** (``## Fraud Ring Risk
   Analysis: HIGH RISK``) or the **borrower's name and hashed SSN**
   (``... — Application (Devon Cross / hash_ssn_ff02aa)``). Four of the eight prompts
   explicitly forbid risk labels in headers.

This module reports those violations; it does not silently rewrite the prose. Rewriting
would hide a real divergence between what the customer's prompt asks for and what the
model does, and that divergence is a decision for the prompt's author, not for us. What
we owe her is to surface it precisely.

WHAT IS AND IS NOT IN EACH PROMPT

The rules are NOT uniform across the eight agents, and the tier sweep corrected an
earlier assumption here. Enforcing the six-agent contract on all eight would report
violations the customer never asked for:

======================  =====  =========  ==========  ==============  ==============
agent                   title  char cap   no emoji    no alert nums   no risk labels
                                                                      in headers
======================  =====  =========  ==========  ==============  ==============
identity                yes    4000       yes         yes             yes
dealer                  yes    4000       yes         yes             yes
straw                   yes    4000       yes         yes             yes
employment              yes    4000       yes         yes             yes
income                  yes    4000       yes         yes             no
synthetic               yes    4000       yes         yes             no
bustout                 NONE   none       no          no              no
rings                   NONE   none       no          no              no
======================  =====  =========  ==========  ==============  ==============

Every cell is derived from the verbatim prompt text at import (see
:func:`_derive_rules`) rather than hard-coded, so a customer prompt update changes the
contract automatically instead of leaving this table stale.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Final

from prompts.loader import DOMAINS, SPECIALIST_PROMPTS

#: The exact title each prompt defines, or None where it defines none.
#: Mirrors ``agents.fanout.ANALYSIS_TITLES``; derived here from the prompt text so the
#: two cannot drift apart silently (a test asserts they agree).
TITLES: Final[dict[str, str | None]] = {
    "identity": "Identity Fraud Risk Analysis",
    "dealer": "Dealer Fraud Risk Analysis",
    "straw": "Straw Borrower Fraud Risk Analysis",
    "employment": "Employment Fraud Risk Analysis",
    "income": "Income Fraud Risk Analysis",
    "synthetic": "Synthetic Identity Fraud Risk Analysis",
    "bustout": None,
    "rings": None,
}

#: The customer's three risk bands, as her prompts spell them.
RISK_BANDS: Final[tuple[str, ...]] = ("HIGH RISK", "POSSIBLE RISK", "LOW RISK")

#: Character ceiling stated by the six capped prompts.
CHAR_CAP: Final[int] = 4000

_HEADING = re.compile(r"^\s{0,3}(#{1,6}\s+.*|.+\n\s{0,3}[=-]{3,}\s*)$", re.MULTILINE)
_ALERT_NUMBER = re.compile(r"\balert[s]?\s*#?\s*\d+", re.IGNORECASE)
_HASHED_SSN = re.compile(r"hash_ssn[_a-z0-9]*", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Violation:
    """One breach of the contract the agent's own prompt states."""

    rule: str
    detail: str
    #: The prompt sentence that makes this a violation, quoted so a reviewer can check
    #: the finding against the customer's text rather than trusting this module.
    prompt_basis: str


@dataclass(frozen=True, slots=True)
class ContractReport:
    """Everything this module can say about one analysis."""

    domain: str
    violations: tuple[Violation, ...] = ()
    char_count: int = 0
    first_line: str = ""
    #: Rules that do not apply to this agent, so a reader can tell "not checked" from
    #: "checked and clean" -- bustout and rings are unconstrained on almost everything.
    not_applicable: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.violations


def _derive_rules(domain: str) -> dict[str, bool]:
    """Read which rules a domain's verbatim prompt actually states.

    Derived rather than declared: the tier sweep found the 'no risk labels in section
    headers' sentence in four prompts, not the six an earlier revision assumed, and
    income/synthetic never received it. Reading the text is the only way this stays true
    when the customer edits a prompt.
    """
    text = SPECIALIST_PROMPTS[domain]
    lower = text.lower()
    return {
        "title": TITLES[domain] is not None,
        "char_cap": "max 4000 characters" in lower,
        "no_emoji": "no emojis" in lower,
        "no_alert_numbers": "not alert numbers" in lower,
        "no_risk_labels_in_headers": "risk classification labels" in lower,
    }


RULES: Final[dict[str, dict[str, bool]]] = {d: _derive_rules(d) for d in DOMAINS}


def _has_emoji(text: str) -> bool:
    """True if any character is an emoji/pictograph.

    Uses Unicode categories plus the pictograph blocks rather than a fixed list, so an
    emoji nobody enumerated still trips it. Deliberately does not flag ``—`` or ``…``,
    which the customer's own prompts use.
    """
    for ch in text:
        if unicodedata.category(ch) == "So":
            return True
        if 0x1F000 <= ord(ch) <= 0x1FAFF:
            return True
    return False


def _headings(text: str) -> list[str]:
    return [m.group(0).strip() for m in _HEADING.finditer(text)]


def check(domain: str, analysis: str) -> ContractReport:
    """Report every contract breach in one specialist analysis.

    Reports; does not repair. A caller that wants clean prose for a UI can use
    :func:`strip_invented_title`, but the violation must still be recorded -- the
    customer needs to know her prompt is not producing what it asks for.
    """
    if domain not in RULES:
        raise KeyError(f"unknown fraud domain {domain!r}; expected one of {list(DOMAINS)}")

    rules = RULES[domain]
    text = analysis or ""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    first = lines[0].strip() if lines else ""
    violations: list[Violation] = []
    not_applicable = tuple(sorted(r for r, applies in rules.items() if not applies))

    expected_title = TITLES[domain]
    if expected_title is not None:
        if expected_title.lower() not in text.lower():
            violations.append(
                Violation(
                    "title_missing",
                    f"prompt defines the title {expected_title!r}; the analysis opens with {first[:80]!r}",
                    f"OUTPUT: - Title: {expected_title}",
                )
            )
    else:
        # bustout and rings define NO title. 24 of 24 measured analyses invented one.
        if _looks_like_title(first, domain):
            violations.append(
                Violation(
                    "title_invented",
                    f"prompt defines no title; the analysis opens with {first[:90]!r}",
                    "Return only the final analysis.",
                )
            )

    if rules["char_cap"] and len(text) > CHAR_CAP:
        violations.append(
            Violation(
                "char_cap_exceeded",
                f"{len(text)} characters against a stated ceiling of {CHAR_CAP}",
                "For POSSIBLE/HIGH: Detailed analysis proportional to severity, max 4000 characters.",
            )
        )

    if rules["no_emoji"] and _has_emoji(text):
        violations.append(Violation("emoji_present", "analysis contains an emoji", "No emojis."))

    if rules["no_alert_numbers"]:
        hits = _ALERT_NUMBER.findall(text)
        if hits:
            violations.append(
                Violation(
                    "alert_number_cited",
                    f"cites {len(hits)} numeric alert reference(s): {hits[:4]}",
                    "Reference specific findings from alerts, not alert numbers.",
                )
            )

    if rules["no_risk_labels_in_headers"]:
        labelled = [h for h in _headings(text) if any(b in h.upper() for b in RISK_BANDS)]
        if labelled:
            violations.append(
                Violation(
                    "risk_label_in_header",
                    f"heading carries a risk label: {labelled[0][:90]!r}",
                    'Do NOT include risk classification labels (e.g. "HIGH RISK", '
                    '"POSSIBLE RISK") in section headers or titles.',
                )
            )

    # Not a rule in any prompt, but reported because it is the kind of thing the
    # customer's engineer will notice: a heading that embeds identifying fields.
    for heading in _headings(text)[:1]:
        if _HASHED_SSN.search(heading):
            violations.append(
                Violation(
                    "identifier_in_heading",
                    f"heading embeds a hashed SSN: {heading[:90]!r}. Not a Rule 23 breach "
                    "(the value is hashed, never raw) but it puts an identifier in a title.",
                    "(observation, not a stated prompt rule)",
                )
            )

    return ContractReport(
        domain=domain,
        violations=tuple(violations),
        char_count=len(text),
        first_line=first,
        not_applicable=not_applicable,
    )


def _looks_like_title(line: str, domain: str) -> bool:
    """Whether a first line is a title rather than the first sentence of the analysis.

    Deliberately conservative: a markdown heading, or a short line naming the domain
    with no terminal punctuation. A real opening sentence ends in a full stop and is
    longer, so it is not flagged.
    """
    if not line:
        return False
    if line.lstrip().startswith("#"):
        return True
    if line.endswith((".", "!", "?")) or len(line) > 120:
        return False
    keyword = "ring" if domain == "rings" else "bust"
    return keyword in line.lower() and "analysis" in line.lower()


def strip_invented_title(domain: str, analysis: str) -> str:
    """Remove a title from an agent whose prompt defines none.

    For a display surface that wants to honour the contract the prompt states. The
    violation must still be recorded via :func:`check`; this is presentation, not a fix.
    Returns the text unchanged for the six agents that DO define a title.
    """
    if TITLES.get(domain) is not None:
        return analysis
    lines = (analysis or "").splitlines()
    idx = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if idx is None or not _looks_like_title(lines[idx].strip(), domain):
        return analysis
    return "\n".join(lines[idx + 1 :]).lstrip("\n")


def check_all(analyses: dict[str, str]) -> dict[str, ContractReport]:
    """Report on a whole fan-out result."""
    return {d: check(d, text) for d, text in analyses.items() if d in RULES}


def summarize(reports: dict[str, ContractReport]) -> dict[str, object]:
    """Aggregate for a run report or a UI banner."""
    by_rule: dict[str, int] = {}
    for report in reports.values():
        for violation in report.violations:
            by_rule[violation.rule] = by_rule.get(violation.rule, 0) + 1
    return {
        "domains_checked": sorted(reports),
        "domains_clean": sorted(d for d, r in reports.items() if r.ok),
        "domains_violating": sorted(d for d, r in reports.items() if not r.ok),
        "violations_by_rule": dict(sorted(by_rule.items())),
        "total_violations": sum(len(r.violations) for r in reports.values()),
    }


__all__ = [
    "CHAR_CAP",
    "ContractReport",
    "RISK_BANDS",
    "RULES",
    "TITLES",
    "Violation",
    "check",
    "check_all",
    "strip_invented_title",
    "summarize",
]
