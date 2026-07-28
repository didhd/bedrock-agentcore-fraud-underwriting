"""
The interpretive scales Point Predictive's agents share, as functions rather than prose.

Every band, threshold and acronym here is transcribed from a customer document. The
scales appear in the orchestration General Rules (repeated identically in all eight
domain docs) and in the DIANA_DEALER agent prompt. They live in the signal layer
because they are governance, not reasoning: if the fraud-score bands or the
dealer-score bands differ by one point between two agents, two specialists disagree
about the same number and the master synthesizer cannot reconcile them.

Sources:
  General Rule 0.5 / 6  fraud score bands              -> FRAUD_SCORE_BANDS
  General Rule 20       IncomePass / income_misrep_score -> INCOMEPASS_SEMANTICS
  General Rule 21       dealer_risk_score vs dealer_consortium_score -> DEALER_SCORE_BINDING
  General Rule 24       PRODUCT acronym table          -> PRODUCTS
  General Rule 25       credit report validity         -> CREDIT_REPORT_VALIDITY_RULE
  dealer_agent_prompt   dealer_consortium_score bands  -> DEALER_SCORE_BANDS

Two boundary ambiguities in the customer's own text are resolved explicitly rather
than silently; see DEALER_BAND_BOUNDARY_NOTE and TIME_IN_FILE_SENTINEL_NOTE.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping

# ---------------------------------------------------------------------------
# Fraud score
# ---------------------------------------------------------------------------

#: General Rule 0.5 (and, identically, General Rule 6), verbatim.
FRAUD_SCORE_RULE: Final = (
    "Fraud scores between 0-299 are low. Fraud scores between 300-699 are medium. "
    "Fraud Scores between 700-998 are high. Any Fraud Scores of 999 are very high. "
    "A fraud score of 999 suggests that at least one of our fraud accelerators fired "
    "on the application, and the application is very likely to be fraudulent and default."
)

#: Label -> inclusive (low, high) bound, in ascending order. Bounds partition 0..999
#: with no gap and no overlap, exactly as the customer states them.
FRAUD_SCORE_BANDS: Final[Mapping[str, tuple[int, int]]] = MappingProxyType(
    {
        "low": (0, 299),
        "medium": (300, 699),
        "high": (700, 998),
        "very high": (999, 999),
    }
)

#: What 999 means, verbatim. Kept separate because it is a statement about the
#: application, not about the band.
FRAUD_SCORE_999_MEANING: Final = (
    "A fraud score of 999 suggests that at least one of our fraud accelerators fired "
    "on the application, and the application is very likely to be fraudulent and default."
)

#: General Rule 22, verbatim: why an average fraud score is not a useful statistic.
FRAUD_SCORE_AVERAGING_RULE: Final = (
    "Averaging fraud scores is not always super meaningful because we standard-scaled "
    "them. It's best to look at trends in fraud scores in a slightly different way for this."
)

#: Identity orchestration, verbatim: the population must not be pre-filtered by score.
FRAUD_SCORE_NO_PREFILTER_RULE: Final = (
    "please disregard the is_fraud field in your analysis, and please DO NOT limit your "
    "investigations to those applications above a fraud score of 700."
)


def fraud_score_band(n: int | float) -> str:
    """
    Classify a fraud score into the customer's four bands.

    Returns one of "low", "medium", "high", "very high" — the exact words of General
    Rule 0.5. Raises ValueError outside 0..999: the customer's scale has no band for
    such a value, and silently clamping would let a corrupt feed masquerade as "low".
    Retro records legitimately have no fraud score at all; callers must handle None
    before calling (the customer's rule: "For retro data, it is normal that there
    won't necessarily always be fraud scores").
    """
    if isinstance(n, bool) or not isinstance(n, (int, float)):
        raise TypeError(f"fraud score must be a number, got {type(n).__name__}")
    if n != n or n < 0 or n > 999:  # n != n catches NaN
        raise ValueError(f"fraud score {n!r} is outside the customer's 0-999 scale")
    for label, (low, high) in FRAUD_SCORE_BANDS.items():
        if low <= n <= high:
            return label
    raise ValueError(f"fraud score {n!r} is outside the customer's 0-999 scale")


# ---------------------------------------------------------------------------
# Dealer scores
# ---------------------------------------------------------------------------

#: DIANA_DEALER prompt, SIGNAL INTERPRETATION, verbatim.
DEALER_CONSORTIUM_SCORE_RULE: Final = (
    "dealer_consortium_score: 0-500 low, 500-700 moderate, 700-900 elevated, 900+ high. "
    "Reflects overall portfolio performance, NOT fraud participation."
)

#: The load-bearing half of the dealer prompt's CRITICAL DISTINCTION block, verbatim.
#: A dealer score is portfolio/credit risk. It is not evidence of fraud.
DEALER_SCORE_IS_PORTFOLIO_RISK: Final = (
    "High dealer default rates, EPD rates, and consortium scores are PORTFOLIO/CREDIT "
    "RISK metrics - they indicate poor loan performance, NOT dealer-orchestrated fraud. "
    "Do NOT escalate fraud risk classification based solely on dealer default rates or "
    "consortium scores. Report portfolio risk metrics factually but classify them as "
    "credit risk context, not fraud evidence."
)

#: General Rule 21, verbatim (the identity orchestration copy, which is the only one
#: that also states the id binding in its final sentence).
DEALER_SCORE_RULE_21: Final = (
    "There are two scores that measure dealer risk. The scores are dealer_risk_score "
    "and dealer_consortium_score. The dealer_risk_score is the numerical representation "
    "of the dealer risk within a particular lender's portfolio. Meanwhile, the "
    "dealer_consortium_score is the aggregated risk of that dealer amongst all lenders "
    "in the Point Predictive Consortium. The dealer_risk_score is tied to the dealer_id "
    "and lender_id while the dealer_consortium_score is tied to the ppi_dealer_id."
)

#: Which identifier each dealer score binds to (General Rule 21, final sentence).
#: Getting this wrong means aggregating a lender-scoped score across the consortium.
DEALER_SCORE_BINDING: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "dealer_risk_score": ("dealer_id", "lender_id"),
        "dealer_consortium_score": ("ppi_dealer_id",),
    }
)

#: Scope of each dealer score, for display next to a value.
DEALER_SCORE_SCOPE: Final[Mapping[str, str]] = MappingProxyType(
    {
        "dealer_risk_score": (
            "the numerical representation of the dealer risk within a particular "
            "lender's portfolio"
        ),
        "dealer_consortium_score": (
            "the aggregated risk of that dealer amongst all lenders in the Point "
            "Predictive Consortium"
        ),
    }
)

#: Label -> inclusive lower bound, ascending. The customer writes the bands with
#: shared endpoints ("0-500 low, 500-700 moderate"), so 500, 700 and 900 each belong
#: to two bands as written; see DEALER_BAND_BOUNDARY_NOTE.
DEALER_SCORE_BANDS: Final[Mapping[str, int]] = MappingProxyType(
    {"low": 0, "moderate": 500, "elevated": 700, "high": 900}
)

DEALER_BAND_BOUNDARY_NOTE: Final = (
    "The customer's bands share their endpoints: '0-500 low, 500-700 moderate, "
    "700-900 elevated, 900+ high'. Exactly 500, 700 and 900 are therefore ambiguous "
    "in the source text. dealer_score_band() resolves each boundary UPWARD (500 -> "
    "moderate, 700 -> elevated, 900 -> high), which matches the dealer orchestration "
    "doc's own phrasing '900 and above dealer_risk_score and or dealer_consortium_score' "
    "for the top band. Flag this to the customer if their SQL rounds the other way."
)

#: Dealer orchestration doc, verbatim: the doc applies the top band to both scores.
DEALER_HIGH_SCORE_RULE: Final = (
    "High Dealer Check scores (i.e. 900 and above dealer_risk_score and or "
    "dealer_consortium_score)"
)

#: General Rule 10, verbatim: only active dealers count.
ACTIVE_DEALER_RULE: Final = (
    "You should only focus on dealers that are active. You can tell if a dealer is "
    "active by looking at the consortium_number_of_apps_last_1_year > 0 in the dealer "
    "stats table."
)


def dealer_score_band(n: int | float) -> str:
    """
    Classify a dealer score (dealer_consortium_score or dealer_risk_score).

    Returns "low", "moderate", "elevated" or "high" — the dealer prompt's own words.
    Boundaries resolve upward; see DEALER_BAND_BOUNDARY_NOTE. The scale has no stated
    ceiling ("900+ high"), so large values are accepted; negatives are rejected.

    A "high" result is a PORTFOLIO risk statement, never fraud evidence
    (DEALER_SCORE_IS_PORTFOLIO_RISK).
    """
    if isinstance(n, bool) or not isinstance(n, (int, float)):
        raise TypeError(f"dealer score must be a number, got {type(n).__name__}")
    if n != n or n < 0:
        raise ValueError(f"dealer score {n!r} is below the customer's 0 floor")
    label = "low"
    for candidate, lower in DEALER_SCORE_BANDS.items():
        if n >= lower:
            label = candidate
    return label


# ---------------------------------------------------------------------------
# IncomePass / income_misrep_score
# ---------------------------------------------------------------------------

#: General Rule 20, verbatim (the identity orchestration copy, which carries the
#: full EPD/EPD6 corroboration clause).
INCOMEPASS_SEMANTICS: Final = (
    "The incomepass score is generated by Point Predictive which indicates the "
    "likelihood of material income misrepresentation by 15%. You will usually find it "
    "in the income_misrep_score field. This is distinct from fraud score. You should "
    "understand that income overstatement in isolation is not correlated to EPD or "
    "EPD6. But if other risk factors are introduced to overstatement, or if there are "
    "other forms of misrepresentation (identity, employment, collateral, etc.) occurs, "
    "the risk of default gets exponentially higher."
)

#: Income orchestration doc, verbatim: the OS15 overstatement indicator. The threshold
#: is 1.15x, matching the "by 15%" in General Rule 20.
OS15_DEFINITION: Final = (
    "CASE\n"
    "WHEN BORR_INCOME_ANNUAL IS NULL OR VERIFIED_INCOME IS NULL THEN NULL\n"
    "WHEN BORR_INCOME_ANNUAL > VERIFIED_INCOME * 1.15 THEN 1\n"
    "ELSE 0\n"
    "END as OS15"
)

#: Employment orchestration doc, verbatim: the one place a numeric IncomePass value is
#: given a hard interpretation.
INCOMEPASS_EMPLOYMENT_ACCELERATOR_RULE: Final = (
    "Any time you see an IncomePass score equal 999 OR any of the alerts 68-71, 117, "
    "140, 142 fire, then there is a very high risk of Employment fraud. This, however, "
    "is not the only feature or pattern you should consider in your analyses."
)

#: The alert IDs named in INCOMEPASS_EMPLOYMENT_ACCELERATOR_RULE ("alerts 68-71, 117,
#: 140, 142"). Transcribed, not inferred: 68, 69, 70, 71 is the expansion of "68-71".
INCOMEPASS_EMPLOYMENT_ACCELERATOR_ALERTS: Final[frozenset[int]] = frozenset(
    {68, 69, 70, 71, 117, 140, 142}
)


def is_incomepass_employment_accelerator(
    income_misrep_score: int | float | None,
    fired_alert_ids: frozenset[int] | set[int] | tuple[int, ...] = (),
) -> bool:
    """
    Evaluate the employment doc's "very high risk of Employment fraud" condition.

    True when income_misrep_score == 999 OR any of alerts 68-71, 117, 140, 142 fired.
    Per the same sentence this is explicitly NOT sufficient on its own ("this, however,
    is not the only feature or pattern you should consider"), so callers must treat a
    True as an input to the employment specialist, never as a verdict.
    """
    if income_misrep_score == 999:
        return True
    return bool(INCOMEPASS_EMPLOYMENT_ACCELERATOR_ALERTS & set(fired_alert_ids))


# ---------------------------------------------------------------------------
# PRODUCT field (General Rule 24)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProductVariant:
    """One official product name + description for a PRODUCT acronym."""

    name: str
    description: str


@dataclass(frozen=True, slots=True)
class Product:
    """
    A Point Predictive product as it appears in the PRODUCT field.

    `variants` is a tuple because the customer's own table maps 'BC' to TWO products
    (BorrowerCheck for Dealers and BorrowerCheck for Lenders); the PRODUCT field alone
    cannot disambiguate them, and collapsing them to one would misreport the other.
    """

    acronym: str
    aliases: tuple[str, ...]
    variants: tuple[ProductVariant, ...]


#: General Rule 24, verbatim.
PRODUCT_FIELD_RULE: Final = (
    "The PRODUCT field in the all records all records for pg table concatenates all of "
    "the Point Predictive (PPI) product abbreviations used for the application in the "
    'record. For example, if the PRODUCT field is something like "AFMIPIP", it means '
    "that the AFM and IP products have been used for the application."
)

#: General Rule 24, verbatim: how to filter for a product.
PRODUCT_FILTER_RULE: Final = (
    "When looking for records associated with a product, you should filter for those "
    "where the product field is like the acronym, and you should include a wildcard "
    "before and after the acronym. For example, for looking for Autopass records, the "
    "filter would be 'where PRODUCT like '%AFM%'."
)

#: The official Point Predictive product descriptions from General Rule 24, verbatim.
PRODUCTS: Final[Mapping[str, Product]] = MappingProxyType(
    {
        "AFM": Product(
            acronym="AFM",
            aliases=("AFM",),
            variants=(
                ProductVariant(
                    name="AutoPass",
                    description=(
                        "An AI-driven auto lending decisioning solution that identifies "
                        "low-risk and high-risk applications to automate approvals, "
                        "reduce fraud, and streamline processing."
                    ),
                ),
            ),
        ),
        "BC": Product(
            acronym="BC",
            aliases=("BC",),
            variants=(
                ProductVariant(
                    name="BorrowerCheck for Dealers",
                    description=(
                        "A dealer-focused fraud risk assessment tool that detects "
                        "income, identity, and employment misrepresentation to help "
                        "limit lender buy-backs and enhance loan quality."
                    ),
                ),
                ProductVariant(
                    name="BorrowerCheck for Lenders",
                    description=(
                        "A comprehensive application risk scoring service that flags "
                        "identity, income, and employment misrepresentation to improve "
                        "lender underwriting and reduce fraud losses."
                    ),
                ),
            ),
        ),
        "DC": Product(
            acronym="DC",
            aliases=("DC",),
            variants=(
                ProductVariant(
                    name="DealerCheck",
                    description=(
                        "A dealer performance and risk management platform that gives "
                        "lenders insights into dealer behavior, risk trends, and "
                        "comparative performance to support smarter dealer relationships."
                    ),
                ),
            ),
        ),
        "IEV": Product(
            acronym="IEV",
            aliases=("IEVALIDATE", "IEV"),
            variants=(
                ProductVariant(
                    name="IEValidate",
                    description=(
                        "A frictionless income and employment validation solution that "
                        "delivers reliable, data-backed verification to reduce reliance "
                        "on paystubs and costly employer checks."
                    ),
                ),
            ),
        ),
        "IP": Product(
            acronym="IP",
            aliases=("IP",),
            variants=(
                ProductVariant(
                    name="IncomePass",
                    description=(
                        "An automated income validation product that analyzes stated "
                        "incomes against large historical datasets to spot inflation "
                        "and improve loan pull-through."
                    ),
                ),
            ),
        ),
        "MP": Product(
            acronym="MP",
            aliases=("MP",),
            variants=(
                ProductVariant(
                    name="MortgagePass",
                    description=(
                        "A predictive analytics tool for mortgage lending that scores "
                        "applications for fraud and misrepresentation risk to streamline "
                        "low-risk loans and reduce false positives."
                    ),
                ),
            ),
        ),
    }
)

#: Longest alias first, so IEVALIDATE is matched before its own IEV prefix.
_ALIASES: Final[tuple[tuple[str, str], ...]] = tuple(
    sorted(
        ((alias, acronym) for acronym, p in PRODUCTS.items() for alias in p.aliases),
        key=lambda pair: (-len(pair[0]), pair[0]),
    )
)

PRODUCT_DECODE_NOTE: Final = (
    "decode_product() uses substring containment, which is exactly what General Rule "
    "24 prescribes for the PRODUCT field (\"filter for those where the product field "
    "is like the acronym ... include a wildcard before and after\"). Because the field "
    "is an unseparated concatenation, containment cannot distinguish an intended "
    "acronym from one straddling two concatenated codes; that imprecision is the "
    "customer's, and is preserved rather than second-guessed."
)


def decode_product(s: str | None) -> list[Product]:
    """
    Decode a PRODUCT field into the Point Predictive products it names.

    Follows General Rule 24: the field is a concatenation of acronyms, matched with a
    wildcard on both sides. "AFMIPIP" -> [AutoPass (AFM), IncomePass (IP)], the
    customer's own worked example. Returns products in PRODUCTS declaration order so
    the result is deterministic; duplicates in the field collapse to one entry.
    Unknown text yields an empty list rather than an error, because the field is free
    text in the source table.
    """
    if not s:
        return []
    upper = s.upper()
    hits: set[str] = {acronym for alias, acronym in _ALIASES if alias in upper}
    return [PRODUCTS[a] for a in PRODUCTS if a in hits]


# ---------------------------------------------------------------------------
# Credit report validity (General Rule 25)
# ---------------------------------------------------------------------------

#: General Rule 25, verbatim.
CREDIT_REPORT_VALIDITY_RULE: Final = (
    "Ignore credit report data when it is missing or invalid. Specifically, treat time "
    "in file as null when the value is less than or equal to zero or clearly an error, "
    "like 9999, and the date of the oldest trade line is null or zero."
)

#: The one error sentinel the customer names explicitly. Their wording is "clearly an
#: error, like 9999" - an example, not an exhaustive list.
TIME_IN_FILE_ERROR_SENTINELS: Final[frozenset[int]] = frozenset({9999})

TIME_IN_FILE_SENTINEL_NOTE: Final = (
    "General Rule 25 says time in file is invalid when it is '<= zero or clearly an "
    "error, like 9999'. Only <= 0 and 9999 are decidable from the text, so those are "
    "all is_valid_time_in_file() rejects. It deliberately does NOT invent an upper "
    "bound: any additional sentinel the customer uses must be added here from their "
    "data dictionary, not guessed."
)


def is_valid_time_in_file(v: int | float | None) -> bool:
    """
    Apply General Rule 25 to a time-in-file value (months).

    False when the value is missing, non-numeric, <= 0, or the named 9999 error
    sentinel - in all of which cases the caller must treat time in file as null and
    ignore the credit report data that depends on it. True otherwise.
    """
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    if v != v:  # NaN
        return False
    if v <= 0:
        return False
    return v not in TIME_IN_FILE_ERROR_SENTINELS
