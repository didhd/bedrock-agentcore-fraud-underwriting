"""
The General Rules coverage catalog: rule id -> verbatim text -> enforcement layer -> callable
-> test id.

This module exists so a customer can be shown a coverage *table* instead of a coverage
*promise*. Rebecca's stated biggest migration risk is "replicating the SQL guardrails built
into snowflake cortex by the semantic layer", and the only credible answer to that is a list
of her own 29 numbered rules with, against each one, the function that enforces it and the
test that proves it.

Three properties make the table trustworthy rather than decorative:

  1. **The verbatim text is read from her documents**, via ``_source``, not retyped. A rule
     she edits changes here automatically.
  2. **Every ``implemented_by`` is a real dotted path that is resolved and called** by
     ``resolve()``; ``validate_catalog()`` fails if any entry points at a function that does
     not exist. A catalog that can name a non-existent function is worthless.
  3. **Every ``test_id`` names a real test** in ``tests/test_rules.py``, and a test asserts
     that correspondence. So "enforced" cannot be asserted without a test behind it.

``docs/general_rules_coverage.md`` is generated from this module (``python -m
signal_layer.rules.catalog``), so the document cannot drift from the code.

Enforcement layers, and what each one honestly means:

  ``signal-layer-code``   a deterministic function in this package runs it. The strongest.
  ``bands``               already implemented in ``signal_layer.bands``; imported, not
                          duplicated.
  ``alert-categories``    already implemented in ``signal_layer.alert_categories``.
  ``prompt-verbatim``     interpretive guidance that belongs in the model's context, not in
                          code. Enforced by the verbatim prompt text, and a test asserts the
                          text is actually present in the loaded prompts.
  ``prompt+code``         interpretive in the prompt, with a code-side validator as well.
  ``bedrock-guardrail``   needs a platform guardrail that this repo does NOT configure. Listed
                          as a gap, never as done.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Callable, Final, Mapping

from signal_layer.rules import _source

#: The enforcement layers, strongest first.
ENFORCEMENT_LAYERS: Final[tuple[str, ...]] = (
    "signal-layer-code",
    "bands",
    "alert-categories",
    "prompt+code",
    "prompt-verbatim",
    "bedrock-guardrail",
)

#: Layers that count as "enforced in code" for the honest coverage count.
CODE_LAYERS: Final[frozenset[str]] = frozenset(
    {"signal-layer-code", "bands", "alert-categories", "prompt+code"}
)


@dataclass(frozen=True)
class RuleEntry:
    """
    One row of the coverage table.

    ``verbatim`` is not stored: it is read from the customer's document on access, so it
    cannot drift from source.
    """

    rule_id: str
    title: str
    enforcement_layer: str
    implemented_by: tuple[str, ...]
    test_id: str
    note: str = ""
    prompt_evidence: str = ""

    @property
    def verbatim(self) -> str:
        """The rule as the customer wrote it, read from their document at access time."""
        return _source.rule_text(self.rule_id)

    @property
    def enforced_in_code(self) -> bool:
        """Whether a deterministic function in this repo runs this rule."""
        return self.enforcement_layer in CODE_LAYERS

    def resolve(self) -> list[Callable[..., Any]]:
        """
        Import and return every callable named in ``implemented_by``.

        Raises if any dotted path does not resolve. This is what makes the catalog
        self-verifying: an entry cannot claim an implementation that is not there.
        """
        out: list[Callable[..., Any]] = []
        for path in self.implemented_by:
            module_path, _, attr = path.rpartition(".")
            if not module_path:
                raise ValueError(f"{self.rule_id}: {path!r} is not a dotted path")
            module = importlib.import_module(module_path)
            try:
                out.append(getattr(module, attr))
            except AttributeError as exc:
                raise AttributeError(
                    f"General Rule {self.rule_id} claims {path!r} but {attr!r} is not in "
                    f"{module_path}"
                ) from exc
        return out


_R = "signal_layer.rules"

#: The catalog. One entry per rule id the customer numbers, in their document order.
CATALOG: Final[tuple[RuleEntry, ...]] = (
    RuleEntry(
        rule_id="0.5",
        title="Fraud score bands and the meaning of 999",
        enforcement_layer="bands",
        implemented_by=("signal_layer.bands.fraud_score_band",),
        test_id="test_rule_0_5_fraud_score_bands",
        note=(
            "Already owned by signal_layer.bands (FRAUD_SCORE_BANDS, fraud_score_band). "
            "Imported, not duplicated. Rule 6 is the same rule restated."
        ),
    ),
    RuleEntry(
        rule_id="1",
        title="INNER join to delink_lender_table on hash_lender_id, DISTINCT, excluding TEST lenders",
        enforcement_layer="signal-layer-code",
        implemented_by=(
            f"{_R}.r01_lender_join.inner_join_lenders",
            f"{_R}.r01_lender_join.resolve_lenders",
            f"{_R}.r01_lender_join.validate_lender_join_sql",
        ),
        test_id="test_rule_1_lender_join",
        note="All three parts (INNER, NOT LIKE '%TEST%', DISTINCT) tested individually.",
    ),
    RuleEntry(
        rule_id="1.5",
        title="Lender name normalisation and the 12-row / 14-nickname mapping table",
        enforcement_layer="signal-layer-code",
        implemented_by=(
            f"{_R}.r0105_lender_nicknames.resolve_lender",
            f"{_R}.r0105_lender_nicknames.norm_lender",
        ),
        test_id="test_rule_1_5_lender_nicknames",
        note=(
            "Data, not inference - the rule says 'don't hallucinate'. Unresolved input "
            "returns resolved=False rather than a guess. Resolves rules 16 and 19 together."
        ),
    ),
    RuleEntry(
        rule_id="2",
        title="UPPER(TRIM(x)) on borrower strings, and the '123_WALN_92101' address key",
        enforcement_layer="signal-layer-code",
        implemented_by=(
            f"{_R}.r02_normalize.norm_string",
            f"{_R}.r02_normalize.address_key",
        ),
        test_id="test_rule_2_normalisation_and_address_key",
        note=(
            "The customer's literal example is the specification. Same-street-different-"
            "number must not collide - the rule's stated purpose is removing false positives."
        ),
    ),
    RuleEntry(
        rule_id="3",
        title="The ten alert-ID categories",
        enforcement_layer="alert-categories",
        implemented_by=(
            "signal_layer.alert_categories.category_of",
            "signal_layer.alert_categories.alerts_for_domain",
        ),
        test_id="test_rule_3_alert_categories",
        note=(
            "Already owned by signal_layer.alert_categories (166 list entries, 148 distinct "
            "ids, 18 multi-category). Many-to-many by construction; a single-label mapping "
            "would silently lose a category for 18 ids."
        ),
    ),
    RuleEntry(
        rule_id="4",
        title="Join tbl_afa_alerts_clean on application_id AND hash_lender_id",
        enforcement_layer="signal-layer-code",
        implemented_by=(
            f"{_R}.r04_alert_join.join_alerts",
            f"{_R}.r04_alert_join.validate_join_sql",
        ),
        test_id="test_rule_4_alert_join_composite_key",
        note="Tested against the single-key join to show the alert-count inflation it prevents.",
    ),
    RuleEntry(
        rule_id="5",
        title="All application retrieval joins on application_id plus a lender identifier",
        enforcement_layer="signal-layer-code",
        implemented_by=(
            f"{_R}.r04_alert_join.require_composite_key",
            f"{_R}.r04_alert_join.submitted_to_lender_count",
        ),
        test_id="test_rule_5_application_grain",
        note=(
            "Also encodes the rule's semantics: multi-lender submission is NORMAL and must "
            "not be reported as velocity."
        ),
    ),
    RuleEntry(
        rule_id="6",
        title="Fraud score bands and the meaning of 999 (restatement of rule 0.5)",
        enforcement_layer="bands",
        implemented_by=("signal_layer.bands.fraud_score_band",),
        test_id="test_rule_6_restates_rule_0_5",
        note=(
            "Byte-identical to rule 0.5 in the identity document. One implementation, two "
            "rule ids - deduplicating in code is correct, deduplicating in the inventory is "
            "not, so both ids appear in this table."
        ),
    ),
    RuleEntry(
        rule_id="7",
        title="Exclude pure chargeoffs from default calculations",
        enforcement_layer="signal-layer-code",
        implemented_by=(
            f"{_R}.r07_chargeoff.is_default",
            f"{_R}.r07_chargeoff.is_pure_chargeoff",
            f"{_R}.r07_chargeoff.default_rate",
        ),
        test_id="test_rule_7_chargeoff_exclusion",
        note=(
            "Full chargeoff x (epd, epd6) truth table tested. The wrong reading - a blanket "
            "'AND chargeoff_flag = false' - would under-count defaults."
        ),
    ),
    RuleEntry(
        rule_id="8",
        title="Deprecated all-records tables must not be queried",
        enforcement_layer="signal-layer-code",
        implemented_by=(
            f"{_R}.r08_deprecated_tables.validate_no_deprecated_tables",
            f"{_R}.r08_deprecated_tables.is_deprecated_table",
        ),
        test_id="test_rule_8_deprecated_tables",
        note=(
            "Matched with underscore runs collapsed, because the customer's own two names "
            "use inconsistent underscore counts. Raises rather than silently rewriting."
        ),
    ),
    RuleEntry(
        rule_id="9",
        title="Co-borrower data from tbl_auto_consortium first, then all-records",
        enforcement_layer="signal-layer-code",
        implemented_by=(f"{_R}.r09_coborrower_source.resolve_coborrower",),
        test_id="test_rule_9_coborrower_source_precedence",
        note=(
            "Three-valued: PRESENT / ABSENT / UNKNOWN. 'No co-borrower' and 'not sourced' "
            "must not collapse - the identity agent escalates on the former."
        ),
    ),
    RuleEntry(
        rule_id="10",
        title="Only active dealers (consortium_number_of_apps_last_1_year > 0)",
        enforcement_layer="signal-layer-code",
        implemented_by=(
            f"{_R}.r10_active_dealer.is_active_dealer",
            f"{_R}.r10_active_dealer.active_dealers",
        ),
        test_id="test_rule_10_active_dealers",
        note=(
            "The NULL branch is explicit and tested: NULL is not > 0, so unknown activity is "
            "excluded. The customer does not state this; see ACTIVE_DEALER_NULL_NOTE."
        ),
    ),
    RuleEntry(
        rule_id="11",
        title="submitted_to_prod_at only for 'submitted'; never <= CURRENT_TIMESTAMP()",
        enforcement_layer="signal-layer-code",
        implemented_by=(
            f"{_R}.r11_submitted_at.resolve_date_field",
            f"{_R}.r11_submitted_at.validate_date_sql",
        ),
        test_id="test_rule_11_date_field_routing",
        note=(
            "All four parts: keyword routing, TBL_AUTO_CONSORTIUM first, undated -> "
            "application_date, and hard rejection of the CURRENT_TIMESTAMP() anti-pattern."
        ),
    ),
    RuleEntry(
        rule_id="12",
        title="No unsolicited recommendations; never auto-decline; never criminal penalty",
        enforcement_layer="prompt+code",
        implemented_by=(
            f"{_R}.r12_recommendations.check_recommendation_text",
            f"{_R}.r12_recommendations.requests_recommendations",
        ),
        test_id="test_rule_12_recommendation_constraints",
        prompt_evidence="Do not provide recommendations for loan response unless you are prompted for recommendations.",
        note=(
            "Primary enforcement is the verbatim prompt. This adds a post-generation "
            "validator. The Bedrock Guardrail backstop the rule really wants is NOT "
            "configured in this repo - recorded as a gap, see ENFORCEMENT_LAYERS."
        ),
    ),
    RuleEntry(
        rule_id="13",
        title="Retro: never filter by default, null-safe predicate, no submitted_to_prod_at",
        enforcement_layer="signal-layer-code",
        implemented_by=(
            f"{_R}.r13_retro.resolve_retro_scope",
            f"{_R}.r13_retro.is_non_retro",
            f"{_R}.r13_retro.validate_retro_sql",
        ),
        test_id="test_rule_13_retro_handling",
        note=(
            "The null-safe '(record_from not like %RETRO% or record_from is null)' idiom is "
            "enforced by a SQL validator; the rule-11 conflict surfaces as a caveat."
        ),
    ),
    RuleEntry(
        rule_id="14",
        title="ppi_dealer_id + dealer_name together identify a dealer",
        enforcement_layer="signal-layer-code",
        implemented_by=(
            f"{_R}.r10_active_dealer.dealer_key",
            f"{_R}.r10_active_dealer.distinct_dealers",
        ),
        test_id="test_rule_14_dealer_identity_key",
        note="'Please always use this one' is unconditional, so a half key raises.",
    ),
    RuleEntry(
        rule_id="15",
        title="NULL borr_ssn is not a distinct SSN",
        enforcement_layer="signal-layer-code",
        implemented_by=(
            f"{_R}.r15_null_ssn.count_distinct_ssns",
            f"{_R}.r15_null_ssn.is_present_ssn",
        ),
        test_id="test_rule_15_null_ssn_not_distinct",
        note=(
            "One uncounted NULL inflates every reuse count by one and fabricates an identity "
            "cluster from a missing value."
        ),
    ),
    RuleEntry(
        rule_id="16",
        title="PRIME and SUBPRIME are both Stellantis lenders",
        enforcement_layer="signal-layer-code",
        implemented_by=(f"{_R}.r0105_lender_nicknames.resolve_lender",),
        test_id="test_rule_16_prime_subprime_stellantis",
        note=(
            "Stellantis resolves to TWO lender names, which is why LenderResolution."
            "lender_names is a tuple and .lender_name raises on a multi-name result."
        ),
    ),
    RuleEntry(
        rule_id="17",
        title="Null-safe fake-record exclusion (fake emails, common VINs, common phones)",
        enforcement_layer="signal-layer-code",
        implemented_by=(
            f"{_R}.r17_fake_records.exclude_fake_records",
            f"{_R}.r17_fake_records.phone_excluded",
            f"{_R}.r17_fake_records.validate_exclusion_sql",
        ),
        test_id="test_rule_17_fake_record_exclusion",
        note=(
            "Highest-risk rule in the set: the naive '<> ALL' without the 'or ... is null' "
            "disjunct silently DELETES every legitimate application with no email on file. "
            "The phone carve-out is also implemented as written."
        ),
    ),
    RuleEntry(
        rule_id="18",
        title="Check TBL_AUTO_CONSORTIUM when all-records returns nothing",
        enforcement_layer="signal-layer-code",
        implemented_by=(
            f"{_R}.r09_coborrower_source.next_source",
            f"{_R}.r09_coborrower_source.is_exhausted",
        ),
        test_id="test_rule_18_source_fallback",
        note="An empty result is not an answer, it is a mandatory retry that costs a rule-27 call.",
    ),
    RuleEntry(
        rule_id="19",
        title="Crescent Bank is CRESCENTBANK, never FCRACRESCENTBANK",
        enforcement_layer="signal-layer-code",
        implemented_by=(f"{_R}.r0105_lender_nicknames.resolve_lender",),
        test_id="test_rule_19_crescent_bank",
        note=(
            "A naive '%CRESCENT%' wildcard - which rule 1.5 itself supplies as a nickname - "
            "matches both institutions and double-counts."
        ),
    ),
    RuleEntry(
        rule_id="20",
        title="IncomePass (income_misrep_score) is distinct from fraud score",
        enforcement_layer="prompt-verbatim",
        implemented_by=("signal_layer.bands.INCOMEPASS_SEMANTICS",),
        test_id="test_rule_20_incomepass_semantics_in_prompts",
        prompt_evidence="This is distinct from fraud score.",
        note=(
            "Score interpretation, so it belongs in the model's context. signal_layer.bands "
            "carries the verbatim semantics; a test asserts the text is in the loaded prompts."
        ),
    ),
    RuleEntry(
        rule_id="21",
        title="dealer_risk_score (lender-scoped) vs dealer_consortium_score (consortium-scoped)",
        enforcement_layer="bands",
        implemented_by=(
            "signal_layer.bands.DEALER_SCORE_BINDING",
            "signal_layer.bands.dealer_score_band",
        ),
        test_id="test_rule_21_dealer_score_binding",
        prompt_evidence="The dealer_risk_score is tied to the dealer_id",
        note=(
            "signal_layer.bands owns the id binding: dealer_risk_score on (dealer_id, "
            "lender_id), dealer_consortium_score on ppi_dealer_id."
        ),
    ),
    RuleEntry(
        rule_id="22",
        title="Do not average fraud scores - they are standard-scaled",
        enforcement_layer="prompt-verbatim",
        implemented_by=("signal_layer.bands.FRAUD_SCORE_AVERAGING_RULE",),
        test_id="test_rule_22_no_fraud_score_averaging",
        prompt_evidence="Averaging fraud scores is not always super meaningful because we standard-scaled them.",
        note=(
            "A ban on a statistic, not a computation to perform. Nothing in this repo computes "
            "AVG(fraud_score); a test asserts that and asserts the caveat text is in the "
            "prompts."
        ),
    ),
    RuleEntry(
        rule_id="23",
        title="NEVER include raw SSN values or any parts of them",
        enforcement_layer="signal-layer-code",
        implemented_by=(
            f"{_R}.r23_no_raw_ssn.assert_no_raw_ssn",
            f"{_R}.r23_no_raw_ssn.find_raw_ssn",
        ),
        test_id="test_rule_23_no_raw_ssn",
        prompt_evidence="NEVER INCLUDE RAW SSN VALUES OR ANY PARTS OF RAW SSN VALUES IN THE RESULTS.",
        note=(
            "The only hard compliance boundary in the set, and the only rule the customer "
            "capitalises. Enforced as a recursive gate. The Bedrock Guardrail PII backstop is "
            "NOT configured in this repo - see r23_no_raw_ssn.ENFORCEMENT_LAYERS."
        ),
    ),
    RuleEntry(
        rule_id="24",
        title="PRODUCT field concatenates PPI acronyms; match with wildcards on both sides",
        enforcement_layer="bands",
        implemented_by=(
            "signal_layer.bands.decode_product",
            "signal_layer.bands.PRODUCTS",
        ),
        test_id="test_rule_24_product_decode",
        note=(
            "signal_layer.bands owns the acronym table and the 'AFMIPIP' -> AFM + IP worked "
            "example. Note 'BC' maps to two different products in the customer's own table."
        ),
    ),
    RuleEntry(
        rule_id="25",
        title="Ignore invalid credit data: time in file <= 0 or 9999, oldest tradeline null/zero",
        enforcement_layer="signal-layer-code",
        implemented_by=(
            "signal_layer.bands.is_valid_time_in_file",
            f"{_R}.r25_invalid_credit.is_valid_oldest_tradeline",
            f"{_R}.r25_invalid_credit.scrub_credit_fields",
        ),
        test_id="test_rule_25_invalid_credit_data",
        note=(
            "bands owns the time-in-file half; this package adds the oldest-tradeline half "
            "bands does not cover, plus the row-level scrub that applies both."
        ),
    ),
    RuleEntry(
        rule_id="26",
        title="11-digit and 10-digit forms of a phone are the same phone",
        enforcement_layer="signal-layer-code",
        implemented_by=(
            f"{_R}.r26_phone_equiv.norm_phone",
            f"{_R}.r26_phone_equiv.same_phone",
        ),
        test_id="test_rule_26_phone_equivalence",
        note=(
            "Without this, reuse counts UNDERCOUNT and a real shared-phone ring is missed - "
            "a false negative. Also required upstream of rule 17's phone exclusion."
        ),
    ),
    RuleEntry(
        rule_id="27",
        title="Max 5 SQL calls per request; batches > 5 applications summarise incrementally",
        enforcement_layer="signal-layer-code",
        implemented_by=(
            f"{_R}.r27_query_budget.QueryBudget",
            f"{_R}.r27_query_budget.requires_incremental_summary",
        ),
        test_id="test_rule_27_query_budget",
        note=(
            "Mechanical, not prompt-trusted: the budget is spent BEFORE a call is issued, so "
            "the sixth call cannot be made. Shared with rules 13 and 18."
        ),
    ),
)

#: rule_id -> entry.
BY_ID: Final[Mapping[str, RuleEntry]] = {e.rule_id: e for e in CATALOG}


class CatalogError(RuntimeError):
    """The catalog is internally inconsistent with the customer's documents or the code."""


def validate_catalog() -> None:
    """
    Check the catalog against the customer's documents and against this package's code.

    Four invariants, all of which would otherwise let the coverage table lie:
      1. every rule id in the customer's reference document has an entry (and vice versa);
      2. entries are in the customer's document order;
      3. every enforcement_layer is a known layer;
      4. every implemented_by path resolves to a real callable or constant.
    """
    catalog_ids = tuple(e.rule_id for e in CATALOG)
    source_ids = _source.RULE_IDS
    missing = [r for r in source_ids if r not in BY_ID]
    extra = [r for r in catalog_ids if r not in source_ids]
    if missing:
        raise CatalogError(
            f"the customer's document defines rules with no catalog entry: {missing}"
        )
    if extra:
        raise CatalogError(f"catalog has entries the customer's document does not: {extra}")
    if catalog_ids != source_ids:
        raise CatalogError(
            f"catalog order {catalog_ids} does not match the customer's document order "
            f"{source_ids}"
        )
    for entry in CATALOG:
        if entry.enforcement_layer not in ENFORCEMENT_LAYERS:
            raise CatalogError(
                f"rule {entry.rule_id}: unknown enforcement layer "
                f"{entry.enforcement_layer!r}; expected one of {ENFORCEMENT_LAYERS}"
            )
        if not entry.implemented_by:
            raise CatalogError(f"rule {entry.rule_id} names no implementation")
        if not entry.test_id:
            raise CatalogError(f"rule {entry.rule_id} names no test")
        entry.resolve()


def coverage_summary() -> dict[str, Any]:
    """
    The honest coverage count, computed rather than asserted.

    Returns totals plus the per-layer breakdown and the ids in each layer, so a claim like
    "23 of 29 enforced in code" can be checked against the list that produced it.
    """
    by_layer: dict[str, list[str]] = {layer: [] for layer in ENFORCEMENT_LAYERS}
    for entry in CATALOG:
        by_layer[entry.enforcement_layer].append(entry.rule_id)
    code_ids = [e.rule_id for e in CATALOG if e.enforced_in_code]
    prompt_ids = [e.rule_id for e in CATALOG if not e.enforced_in_code]
    return {
        "total_rules": len(CATALOG),
        "enforced_in_code": len(code_ids),
        "enforced_in_code_ids": code_ids,
        "prompt_enforced": len(prompt_ids),
        "prompt_enforced_ids": prompt_ids,
        "by_layer": {k: v for k, v in by_layer.items() if v},
        "unimplemented": [],
    }


def _escape(text: str) -> str:
    """Escape a verbatim rule for a markdown table cell without altering its content."""
    out = text.replace("|", "\\|")
    out = out.replace("\r\n", "\n").replace("\r", "\n")
    return out.replace("\n", "<br>")


def render_markdown() -> str:
    """
    Render docs/general_rules_coverage.md from the catalog.

    Generated rather than written, so the document cannot drift from the code. Run via
    ``python -m signal_layer.rules.catalog``; a test asserts the checked-in file matches
    this output.
    """
    summary = coverage_summary()
    lines: list[str] = []
    lines.append("# General Rules coverage")
    lines.append("")
    lines.append(
        "Point Predictive's orchestration documents carry a numbered list of General Rules "
        "repeated across all eight specialist agents. Rebecca named replicating them as the "
        "biggest risk in this migration: *\"replicating the SQL guardrails built into "
        "snowflake cortex by the semantic layer.\"* This table is the answer to that, rule by "
        "rule."
    )
    lines.append("")
    lines.append(
        "**This file is generated.** Run `python -m signal_layer.rules.catalog > "
        "docs/general_rules_coverage.md`. The rule text in the *Verbatim rule* column is read "
        "out of `prompts/verbatim/identity_orchestration_062026.txt` at generation time, not "
        "retyped, so it cannot drift from the customer's document. `tests/test_rules.py` "
        "asserts that this file matches the catalog."
    )
    lines.append("")
    lines.append("## Coverage")
    lines.append("")
    lines.append(f"- **{summary['total_rules']}** numbered rules in the customer's document")
    lines.append(
        f"- **{summary['enforced_in_code']}** enforced by deterministic code in this repo"
    )
    lines.append(
        f"- **{summary['prompt_enforced']}** enforced by the verbatim prompt text "
        "(interpretive guidance that belongs in the model's context, not in a function)"
    )
    lines.append("")
    lines.append("By enforcement layer:")
    lines.append("")
    for layer, ids in summary["by_layer"].items():
        lines.append(f"- `{layer}` — {len(ids)}: {', '.join(ids)}")
    lines.append("")
    lines.append("Layer meanings:")
    lines.append("")
    lines.append(
        "| Layer | Meaning |\n|---|---|\n"
        "| `signal-layer-code` | A deterministic function in `signal_layer/rules/` runs it. |\n"
        "| `bands` | Already implemented in `signal_layer/bands.py`; imported, not duplicated. |\n"
        "| `alert-categories` | Already implemented in `signal_layer/alert_categories.py`. |\n"
        "| `prompt+code` | Interpretive in the prompt, with a code-side validator as well. |\n"
        "| `prompt-verbatim` | Interpretive guidance enforced by the verbatim prompt text; a "
        "test asserts the text is present in the loaded prompts. |\n"
        "| `bedrock-guardrail` | Requires a platform guardrail this repo does **not** "
        "configure. Not used as a sole layer for any rule. |"
    )
    lines.append("")
    lines.append("## Rules")
    lines.append("")
    lines.append(
        "| Rule | Title | Verbatim rule (customer's words) | Enforced in | Implemented by | Test |"
    )
    lines.append("|---|---|---|---|---|---|")
    for entry in CATALOG:
        impl = "<br>".join(f"`{p.split('.')[-1]}`" for p in entry.implemented_by)
        modules = sorted({".".join(p.split(".")[:-1]) for p in entry.implemented_by})
        impl_cell = impl + "<br><sub>" + "<br>".join(modules) + "</sub>"
        lines.append(
            f"| **{entry.rule_id}** | {entry.title} | {_escape(entry.verbatim)} | "
            f"`{entry.enforcement_layer}` | {impl_cell} | `{entry.test_id}` |"
        )
    lines.append("")
    lines.append("## Implementation notes")
    lines.append("")
    for entry in CATALOG:
        if entry.note:
            lines.append(f"- **Rule {entry.rule_id}** — {entry.note}")
    lines.append("")
    lines.append("## Known gaps")
    lines.append("")
    lines.append(
        "- **Rule 12 and Rule 23 both want a Bedrock Guardrail as their output-side "
        "backstop, and this repo does not configure one.** No `guardrailIdentifier` or "
        "`apply_guardrail` call exists anywhere in it. Rule 23 is enforced by a recursive "
        "payload gate and rule 12 by a post-generation validator, both of which are real and "
        "tested — but neither is the platform-level block the customer's compliance posture "
        "should ultimately have, because a validator in the same process can be bypassed by a "
        "code path that forgets to call it."
    )
    lines.append(
        "- **The rules are extracted from the identity document**, which carries the longest "
        "form of all 29. The other seven documents differ: rule 6 is absent from some, and "
        "rules 11, 13, 17, 18, 20 and 24 are worded differently or truncated per agent. "
        "`signal_layer.rules._source.variants_of()` reports the differences, and "
        "`test_rule_variants_across_documents` pins them so a re-import surfaces any change."
    )
    lines.append(
        "- **Enum conflict, unresolved deliberately.** The customer's process-guide PDF uses "
        "a mid-band of `POSSIBLE RISK` and a four-value recommendation enum (APPROVE / APPROVE "
        "WITH STIPULATIONS / MANUAL REVIEW / DECLINE), while the master prompt file uses "
        "`MEDIUM RISK` and three values. The prompt file is the executable artefact and wins; "
        "the conflict is surfaced for the customer to settle rather than silently resolved."
    )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    """
    Generate docs/general_rules_coverage.md on stdout.

    Entry point for ``python -m signal_layer.rules.catalog``. Validates first, so a stale or
    broken catalog cannot produce a customer-facing document.
    """
    validate_catalog()
    print(render_markdown())


if __name__ == "__main__":  # pragma: no cover - generation entry point
    main()


__all__ = [
    "BY_ID",
    "CATALOG",
    "CODE_LAYERS",
    "ENFORCEMENT_LAYERS",
    "CatalogError",
    "RuleEntry",
    "coverage_summary",
    "main",
    "render_markdown",
    "validate_catalog",
]
