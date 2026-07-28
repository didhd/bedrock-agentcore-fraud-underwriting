"""
General Rule 12 — the output-content constraint, and why it is not only a prompt rule.

Three distinct prohibitions, and they need three different mechanisms:

  1. **No unsolicited recommendations.** A *scope* constraint, not a content one. A topic
     guardrail cannot catch it — the recommendation is not forbidden, it is forbidden
     *unprompted*. Enforced structurally by gating on an explicit ``recommendations_requested``
     boolean.
  2. **Never recommend automatic decline.** "Instead the recommendation should be for the
     lender to conduct further analysis." This is ECOA/fair-lending adjacent: an LLM must not
     drive automated adverse action. That is why it belongs at the platform layer where it
     cannot be prompt-injected away.
  3. **Never recommend criminal penalty or arrest.**

This module is the post-generation validator — the layer that runs offline and can be tested.
It does not replace the verbatim prompt text (which is the primary enforcement) or a Bedrock
Guardrail on output (which is the backstop, and is not configured in this repo today).

Note the conflict with the master adjudication contract, which is surfaced rather than
resolved: the customer's own master prompt requires ``recommendation_decision`` with a DECLINE
value on every full review, while General Rule 12 tells specialists never to recommend
declining. Both are the customer's; see ``MASTER_PATH_CARVE_OUT``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from signal_layer.rules._source import rule_text

RULE_ID: Final = "12"
RULE_TEXT: Final = rule_text(RULE_ID)

#: The framing the rule mandates instead of a decline: "the recommendation should be for the
#: lender to conduct further analysis... The main recommendations should be for additional
#: review, etc."
SANCTIONED_FRAMING: Final = "conduct further analysis / additional review"

#: Phrasings that recommend adverse action. Matched as a recommendation, not as a mention:
#: an agent may factually report that a lender declined an application.
DECLINE_PHRASES: Final[tuple[str, ...]] = (
    "recommend declining",
    "recommend decline",
    "recommend that the lender decline",
    "recommend denying",
    "recommend denial",
    "recommend rejecting",
    "recommend rejection",
    "should be declined",
    "should be denied",
    "should be rejected",
    "automatically decline",
    "automatic decline",
    "auto-decline",
    "auto decline",
    "deny the application",
    "decline the application",
    "reject the application",
)

#: Criminal-justice outcomes the rule forbids recommending.
CRIMINAL_PHRASES: Final[tuple[str, ...]] = (
    "arrest",
    "prosecute",
    "prosecution",
    "criminal charges",
    "press charges",
    "criminal penalty",
    "criminal penalties",
    "refer for charges",
    "file charges",
    "indict",
    "jail",
    "imprison",
)

#: Tokens in a user question that constitute a request for recommendations, which is what
#: unlocks a recommendations section at all.
RECOMMENDATION_REQUEST_KEYWORDS: Final[tuple[str, ...]] = (
    "recommend",
    "recommendation",
    "recommendations",
    "what should",
    "should we",
    "should the lender",
    "advise",
    "next step",
    "next steps",
    "stipulation",
    "stipulations",
    "action",
)

MASTER_PATH_CARVE_OUT: Final = (
    "General Rule 12 appears in the eight SPECIALIST orchestration documents. The customer's "
    "own master (Francis) adjudication contract separately requires a recommendation_decision "
    "field whose allowed values include DECLINE. Both are the customer's own artefacts and "
    "they conflict when read literally. Resolution used here: rule 12 is enforced on "
    "specialist narrative output, and the master adjudication path is an explicit carve-out "
    "because its DECLINE value is a structured underwriting decision the customer's own "
    "contract mandates, not an unsolicited narrative recommendation. Flagged for the customer "
    "to confirm rather than resolved silently - see also the process-guide PDF's four-value "
    "recommendation enum, which conflicts with the prompt's three."
)

ENFORCEMENT_LAYERS: Final[tuple[str, ...]] = (
    "prompt-verbatim: General Rule 12 is present verbatim in all eight specialist "
    "orchestration prompts (primary enforcement)",
    "signal/output layer: validate_recommendation() as a post-generation check - this module",
    "bedrock-guardrail: denied topics for adverse-action recommendations and criminal-justice "
    "outcomes, configured to BLOCK on output. NOT configured in this repo today; this is the "
    "missing backstop and is recorded as a gap rather than claimed as done.",
)


class RecommendationRuleViolation(ValueError):
    """Generated narrative breaks General Rule 12."""


def requests_recommendations(question: str | None) -> bool:
    """
    Whether the user actually asked for recommendations (General Rule 12).

    Verbatim: "Do not provide recommendations for loan response unless you are prompted for
    recommendations."

    This is the boolean the recommendations section is gated on, so unsolicited advice is
    structurally suppressed rather than merely discouraged. Also drives Francis's
    ``###RECOMMENDATIONS`` section, which their response format includes "ONLY do so if the
    user requests recommendations".
    """
    if not question:
        return False
    lowered = question.lower()
    return any(kw in lowered for kw in RECOMMENDATION_REQUEST_KEYWORDS)


@dataclass(frozen=True)
class RecommendationCheck:
    """The outcome of validating one narrative against General Rule 12."""

    ok: bool
    violations: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.ok


def _matches(text: str, phrases: tuple[str, ...]) -> list[str]:
    lowered = text.lower()
    return [p for p in phrases if p in lowered]


def check_recommendation_text(
    text: str, *, recommendations_requested: bool = False
) -> RecommendationCheck:
    """
    Validate generated narrative against General Rule 12.

    Verbatim: "Do not provide recommendations for loan response unless you are prompted for
    recommendations. Anytime you find that an application is at all risky, you shouldn't ever
    recommend a lender to automatically decline the application. Instead the recommendation
    should be for the lender to conduct further analysis. You also should not recommend any
    sort of criminal penalty or arrest. The main recommendations should be for additional
    review, etc."

    Checks all three prohibitions. The decline and criminal checks apply regardless of whether
    recommendations were requested — asking for a recommendation does not authorise an
    auto-decline recommendation. The unsolicited-recommendation check applies only when they
    were not requested.
    """
    violations: list[str] = []
    for phrase in _matches(text, DECLINE_PHRASES):
        violations.append(
            f"recommends adverse action ({phrase!r}); General Rule 12 requires the "
            f"recommendation be for the lender to {SANCTIONED_FRAMING}"
        )
    for phrase in _matches(text, CRIMINAL_PHRASES):
        violations.append(
            f"recommends or invokes a criminal-justice outcome ({phrase!r}); General Rule 12 "
            "forbids recommending any sort of criminal penalty or arrest"
        )
    if not recommendations_requested:
        lowered = text.lower()
        if "recommend" in lowered or "###recommendations" in lowered.replace(" ", ""):
            violations.append(
                "contains a recommendation although the user did not ask for one; General "
                "Rule 12: 'Do not provide recommendations for loan response unless you are "
                "prompted for recommendations'"
            )
    return RecommendationCheck(ok=not violations, violations=tuple(violations))


def assert_recommendation_ok(
    text: str, *, recommendations_requested: bool = False, context: str = "narrative"
) -> None:
    """
    Raise unless generated narrative satisfies General Rule 12.

    For use as a post-generation gate on specialist output. The master adjudication path is a
    documented carve-out and must not be passed through this check — see MASTER_PATH_CARVE_OUT.
    """
    result = check_recommendation_text(
        text, recommendations_requested=recommendations_requested
    )
    if not result.ok:
        detail = "\n  ".join(result.violations)
        raise RecommendationRuleViolation(
            f"General Rule 12 violated in {context}:\n  {detail}\nRule: {RULE_TEXT}"
        )


_RECS_HEADER: Final = re.compile(r"#{2,3}\s*RECOMMENDATIONS", re.IGNORECASE)


def has_recommendations_section(text: str) -> bool:
    """
    Whether a narrative carries a RECOMMENDATIONS section header.

    Tolerates both of the customer's own spellings — their master response format writes
    ``## EXECUTIVE SUMMARY`` and ``## ANALYSIS`` with a space but ``###RECOMMENDATIONS``
    without one, an inconsistency in their document that is accommodated rather than
    normalised.
    """
    return bool(_RECS_HEADER.search(text))


__all__ = [
    "CRIMINAL_PHRASES",
    "DECLINE_PHRASES",
    "ENFORCEMENT_LAYERS",
    "MASTER_PATH_CARVE_OUT",
    "RECOMMENDATION_REQUEST_KEYWORDS",
    "RULE_ID",
    "RULE_TEXT",
    "RecommendationCheck",
    "RecommendationRuleViolation",
    "SANCTIONED_FRAMING",
    "assert_recommendation_ok",
    "check_recommendation_text",
    "has_recommendations_section",
    "requests_recommendations",
]
