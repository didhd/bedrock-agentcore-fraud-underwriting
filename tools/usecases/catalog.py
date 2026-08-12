"""The use-case clustering, and the tool text, in our words.

WHY USE CASES AND NOT DATABASES OR TABLES

The customer was explicit about this: split by use case, not by database. It is also the only
split that survives contact with their data. Their 24 tables sit in 12 Snowflake databases and
almost every interesting query joins four or five of them -- a per-database tool would force a
model to plan a join across tools, which is the thing it is worst at. A per-use-case tool
answers one analyst question and already contains the join their own engineer verified.

WHAT IS IN THIS FILE AND WHAT IS DELIBERATELY NOT

In here: our slugs, our titles, our tool descriptions, our parameter descriptions, and a
SELECTOR -- a distinctive fragment of the customer's question used to find the right verified
query at load time.

Not in here: any of the customer's SQL, column names, table names, lender names or rule text.
Those live only in `prompts/verbatim/FRAUDBOT_SEMANTIC_VIEW.yaml`, which is gitignored, and are
loaded by `tools/usecases/__init__.py`. This file is committed, so anything written into it is
published; that is the whole reason for the split.

The selectors are the one borderline case. They are short fragments of the customer's own
question text -- the least sensitive part of the model, with no schema, no data and no
thresholds -- and they are the only stable way to bind to a query, because the `name` field in
their export is unusable: one is `'"0;1"'` and another is 250-odd quote characters followed by
a truncated sentence.

Borderline still has a line, and it is this: **a selector must not contain a lender name.**
Several of their questions name a real lender, and a lender name is customer data exactly as
much as a column name is. Where that happened, the selector matches the shape of the question
instead, and `tests/test_usecase_tools.py` checks each replacement is still unique across all
seventeen queries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final


@dataclass(frozen=True)
class ParamSpec:
    r"""One analyst-supplied value, and how to FIND the literal it replaces.

    `pattern` is a regex describing the literal STRUCTURALLY -- `\'[0-9a-f]{64}\'` for a hashed
    identifier, `\b20\d{2}\b` for a year. Never the literal itself.

    That is a confidentiality requirement, not a style choice. This file is committed and the
    customer's queries are not, so writing the actual embedded value here would publish it: the
    hardcoded lender names in their verified queries are customer data exactly as much as their
    column names are. A structural pattern locates the same token and reveals nothing.

    Substitution requires the pattern to match EXACTLY ONE distinct string in that variant's
    SQL. Zero or several means the query is not shaped the way this spec assumes -- a later
    delivery changed it -- and rewriting one of several occurrences would produce a statement
    that runs and answers a different question. So it refuses, and `list_use_cases` reports the
    parameter as not substitutable rather than accepting a value it will silently drop.

    `pattern=None` means the query is run exactly as verified.
    """

    name: str
    description: str
    pattern: str | None = None
    required: bool = True
    example: str | None = None

    def locate(self, sql: str) -> str | None:
        """The single literal this parameter would replace, or None if it is not unique."""
        if not self.pattern:
            return None
        found = {m.group(0) for m in re.finditer(self.pattern, sql)}
        if len(found) != 1:
            return None
        only = next(iter(found))
        return only if sql.count(only) == 1 else None


@dataclass(frozen=True)
class UseCaseSpec:
    slug: str
    title: str
    #: What an analyst is trying to find out. Ours, not theirs.
    intent: str
    #: The model-facing tool description. Written to make tool SELECTION unambiguous, which
    #: means it says what the tool does NOT answer as well as what it does.
    description: str
    #: Distinctive fragments of the customer's question, lower-cased. Each must match exactly
    #: one verified query, and every verified query must be matched by exactly one use case --
    #: both asserted at import and in tests.
    selectors: tuple[str, ...]
    parameters: tuple[ParamSpec, ...] = ()
    #: Which of the eight fraud domains this informs. Empty means portfolio-level: it says
    #: nothing about one application and belongs to no specialist.
    domains: tuple[str, ...] = ()
    notes: str = ""
    aliases: dict[str, str] = field(default_factory=dict)


#: Ten use cases over seventeen verified queries. The many-to-one cases are the interesting
#: ones: four separate lender-volume queries differ only by which lender and which window, and
#: the two score-bin queries are the same report measured two ways.
CATALOG: Final[tuple[UseCaseSpec, ...]] = (
    UseCaseSpec(
        slug="borrower_application_history",
        title="Borrower application history",
        intent="Everything one borrower has applied for, across every lender in the consortium.",
        description=(
            "Return one borrower's loan applications across all lenders, most recent first, "
            "with the fraud score for each. Use this when an analyst asks what else a borrower "
            "has applied for, whether they have shopped multiple lenders, or how their score "
            "has moved over time. Takes a HASHED borrower identifier, never a raw one. This "
            "returns history only -- it does not analyse the current application's fraud risk, "
            "which is what the fraud specialists are for."
        ),
        selectors=(
            "most recent loan applications and their fraud scores for this specific borrower",
            "complete application history for this specific borrower",
        ),
        parameters=(
            ParamSpec(
                name="borrower_hash",
                description=(
                    "The borrower's hashed identifier. Hashed only: the customer's own rule 23 "
                    "forbids raw values anywhere in a result."
                ),
                # A 64-hex-character digest. Structural, so the actual hash in their verified
                # query -- which is a real borrower -- is not written into this repo.
                pattern=r"'[0-9a-f]{64}'",
                example="a 64-character hex digest",
            ),
        ),
        # History across lenders is what a bust-out looks like before it completes, and repeat
        # applications under one identity is an identity signal.
        domains=("identity", "bustout"),
        notes=(
            "Two verified queries answer this at different depths: one returns recent "
            "applications with scores, the other the full cross-lender history."
        ),
    ),
    UseCaseSpec(
        slug="lender_application_volume",
        title="Lender application volume",
        intent="How much business a lender, or every lender, has sent over a period.",
        description=(
            "Count applications per lender over a period. Use this for questions about volume, "
            "market share or a named lender's recent activity. Handles both the all-lenders "
            "roll-up and a single named lender. Lender names are matched loosely by the "
            "verified query, which already strips spaces and applies the customer's own "
            "nicknames, so pass the name as the analyst said it."
        ),
        selectors=(
            "how many auto loan applications did each lender receive over the past year",
            "how many auto loan applications has each of our key partner lenders processed",
            # Deliberately name-free. The customer's question text names a real lender, and this
            # file is committed -- so the selector matches on the shape of the question
            # instead. Verified equally unique across the seventeen queries.
            "how many loans has",
            "how many applications has",
        ),
        parameters=(
            # NOT substitutable, deliberately. A lender name appears in these queries as a bare
            # quoted token, and no structural pattern distinguishes it from the other quoted
            # tokens beside it (status codes, boolean strings) -- so locating it would be a
            # guess, and guessing wrong rewrites the query into a different question. The
            # all-lender variants answer the general case; a named lender needs the customer to
            # confirm the anchor. `list_use_cases` reports this as not substitutable.
            ParamSpec(
                name="lender",
                description=(
                    "Not yet substitutable: two of the four variants are already scoped to one "
                    "lender and two roll up all lenders. Pick the variant rather than passing a "
                    "name, or use query_underwriting_data."
                ),
                required=False,
            ),
        ),
        domains=(),
        notes="Four verified queries: two all-lender roll-ups and two named-lender windows.",
    ),
    UseCaseSpec(
        slug="lender_directory",
        title="Lender directory",
        intent="Which lenders exist, and their organisational details.",
        description=(
            "List the lending institutions in the system with their details. Use this to answer "
            "'which lenders do we have' or to resolve a name an analyst is unsure about before "
            "calling another tool. Test lenders are excluded by the verified query."
        ),
        selectors=("all the financial lending institutions and their organizational details",),
        domains=(),
    ),
    UseCaseSpec(
        slug="portfolio_default_rate",
        title="Default and early-payment-default rates",
        intent="How the funded book is actually performing over a window.",
        description=(
            "Early payment default rates for funded loans over a date range, and the underlying "
            "per-application funding and default indicators. Use this for portfolio performance "
            "questions. It reports outcomes, not fraud risk, and per the customer's own rule it "
            "does not count charge-offs that carry no early-payment-default flag."
        ),
        selectors=(
            "early payment default rate for funded loans in 2024",
            "early payment default (epd) and early payment default 6-month (epd6) rates",
            "loan performance details including funding status and early payment default",
        ),
        parameters=(
            # The verified queries in this group express their window as a year rather than a
            # pair of dates, so a year is what can actually be substituted. Offering
            # start_date/end_date here would accept two values and apply neither.
            ParamSpec(
                name="year",
                description="The year to report on, as four digits, e.g. 2025.",
                pattern=r"\b20\d{2}\b",
                required=False,
            ),
        ),
        domains=(),
    ),
    UseCaseSpec(
        slug="score_bin_performance",
        title="Score-bin performance and alert firing rates",
        intent=(
            "Whether the score and the alerts are separating good from bad, bin by bin -- the "
            "question a lender asks when deciding where to set a cutoff."
        ),
        description=(
            "Break a lender's book into fraud-score bins and report, per bin, either the "
            "6-month early-payment-default rate or the alert firing rate. Use this for cutoff, "
            "calibration and model-performance questions. These are the two largest verified "
            "queries in the model and they are the closest thing to a lender-facing "
            "performance report."
        ),
        selectors=(
            "epd6 analysis by score bin",
            "firing rate analysis by score bin",
        ),
        parameters=(
            ParamSpec(
                name="lender",
                description="The lender whose book to analyse, as the analyst said it.",
                required=False,
            ),
            ParamSpec(
                name="start_date",
                description="Start of the window, YYYY-MM-DD.",
                required=False,
            ),
            ParamSpec(
                name="end_date",
                description="End of the window, YYYY-MM-DD.",
                required=False,
            ),
        ),
        domains=(),
        notes="Two verified queries, same shape: one measures EPD6, one measures firing rate.",
    ),
    UseCaseSpec(
        slug="identity_theft_reapplication",
        title="Identity theft signalled by re-application drift",
        intent=(
            "A borrower whose stated income and address jumped between applications at "
            "different lenders -- someone else using the identity, or the identity being "
            "prepared for use."
        ),
        description=(
            "Find applicants whose stated income rose sharply and whose address changed "
            "relative to their own earlier applications at other lenders. Use this for "
            "identity-theft and identity-manipulation questions at portfolio level. The "
            "cross-lender comparison is the point: neither application looks wrong alone."
        ),
        selectors=("signs of possible identity theft based on a significant increase",),
        parameters=(
            ParamSpec(
                name="lender",
                description="The lender to examine, as the analyst said it.",
                required=False,
            ),
        ),
        domains=("identity", "synthetic"),
    ),
    UseCaseSpec(
        slug="coborrower_removal",
        title="Co-borrower dropped between applications",
        intent=(
            "A co-borrower present on a recent application and absent from this one, where the "
            "dropped co-borrower had materially worse credit -- the shape of a straw-borrower "
            "arrangement or of shopping a file after a decline."
        ),
        description=(
            "Find applications where the borrower recently applied WITH a co-borrower who is "
            "absent from this application, and that co-borrower's credit score was far lower. "
            "Use this for straw-borrower and application-manipulation questions. The credit-gap "
            "threshold and the lookback window are in the verified query."
        ),
        selectors=("co-borrower who is not on this application",),
        domains=("straw", "identity"),
    ),
    UseCaseSpec(
        slug="phone_fraud_tag_reuse",
        title="Phone numbers carrying prior fraud tags",
        intent=(
            "A phone number on a live application that has already been tagged for fraud "
            "elsewhere -- the cheapest true link between an application and known bad activity."
        ),
        description=(
            "Return phone numbers on recent applications that carry multiple previous fraud "
            "tags. Use this for questions about repeat offenders, linked applications, or "
            "whether a contact detail has history. The verified query already excludes the "
            "consortium's common-phone list, so a shared carrier number does not present as a "
            "ring."
        ),
        selectors=("phone numbers on recent applications that have multiple previous fraud tags",),
        domains=("rings", "identity"),
    ),
    UseCaseSpec(
        slug="missed_fraud_patterns",
        title="Defaults our alerts and score missed",
        intent=(
            "Applications that defaulted while the score was low and no alert fired, grouped by "
            "the pattern they share. This is the false-negative review, and it is the query "
            "most likely to justify the whole engagement."
        ),
        description=(
            "Find applications that defaulted in a year although no alert fired and the fraud "
            "score was below the high band, and label each with the suspicious pattern it "
            "shares with others -- shared identifiers, reused vehicles, dealer concentration, "
            "rapid re-application, fragmented identities. Use this for coverage-gap, "
            "false-negative and 'what are we missing' questions. It is the largest verified "
            "query in the model and it excludes known-fake records before looking for patterns."
        ),
        selectors=("defaulted in 2024 but were not caught by any of our alerts",),
        parameters=(
            ParamSpec(
                name="year",
                description="The default year to review, as four digits, e.g. 2025.",
                pattern=r"\b20\d{2}\b",
                required=False,
            ),
        ),
        domains=("rings", "dealer", "identity", "bustout"),
    ),
    UseCaseSpec(
        slug="submitted_today",
        title="Applications submitted today",
        intent="What has arrived for a lender today, from the real-time source.",
        description=(
            "List the applications submitted for a lender today. Use this for 'what came in "
            "today' questions. It reads the real-time submission source and keys on the "
            "submission timestamp rather than the application date, which per the customer's "
            "own rule are different questions with different answers."
        ),
        # Name-free for the same reason as above.
        selectors=("apps have been submitted for",),
        parameters=(
            ParamSpec(
                name="lender",
                description="The lender to list, as the analyst said it.",
                required=False,
            ),
        ),
        domains=(),
    ),
)


def slugs() -> tuple[str, ...]:
    return tuple(spec.slug for spec in CATALOG)
