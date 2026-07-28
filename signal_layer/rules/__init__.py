"""
Point Predictive's General Rules, as executable code.

The customer's eight specialist orchestration documents each end with the same numbered list
of General Rules — 29 of them, ids 0.5 through 27. In Snowflake Intelligence those rules are
carried by the Cortex semantic layer, and their engineer named replicating them as the single
biggest risk in this migration: *"replicating the SQL guardrails built into snowflake cortex
by the semantic layer."*

A rule reproduced as a display string is not replicated. This package implements each
data-plane rule as a function with the rule's verbatim text in its docstring, and records
every rule — including the interpretive ones that correctly belong in the prompt — in
``catalog.py``, which generates ``docs/general_rules_coverage.md``. So the claim "we replicate
your guardrails" is a table with a test id in every row, not an assertion.

Layout, one module per rule group:

  ``_source``                the rules themselves, sliced out of the customer's documents
  ``r01_lender_join``        1     INNER + NOT LIKE '%TEST%' + DISTINCT
  ``r0105_lender_nicknames`` 1.5, 16, 19  lender resolution (resolved together; they conflict)
  ``r02_normalize``          2     UPPER(TRIM(x)) and the '123_WALN_92101' address key
  ``r04_alert_join``         4, 5  the (application_id, hash_lender_id) grain
  ``r07_chargeoff``          7     the default definition
  ``r08_deprecated_tables``  8     retired all-records tables
  ``r09_coborrower_source``  9, 18 source precedence and the mandatory retry
  ``r10_active_dealer``      10, 14  active-dealer filter and dealer identity
  ``r11_submitted_at``       11    date-field routing and the CURRENT_TIMESTAMP() anti-pattern
  ``r12_recommendations``    12    no auto-decline, no criminal penalty, no unsolicited advice
  ``r13_retro``              13    retro scope and the null-safe record_from predicate
  ``r15_null_ssn``           15    NULL is not a distinct SSN
  ``r17_fake_records``       17    null-safe fake-record exclusion
  ``r23_no_raw_ssn``         23    the raw-SSN gate
  ``r25_invalid_credit``     25    credit sentinel scrubbing
  ``r26_phone_equiv``        26    phone canonicalisation
  ``r27_query_budget``       27    five SQL calls per request
  ``catalog``                the coverage table, and the generator for the customer-facing doc

Rules 0.5, 3, 6, 20, 21, 22 and 24 are owned by sibling modules (``signal_layer.bands`` and
``signal_layer.alert_categories``) and are imported from there, never reimplemented: two copies
of a band table drift, and a drifted band means two specialists disagree about the same number.

Nothing here imports boto3, strands or bedrock_agentcore. The guardrails must be testable
offline, because they are what every other module depends on.
"""

from __future__ import annotations

from signal_layer.rules._source import (
    REFERENCE_DOMAIN,
    RULE_IDS,
    RULES,
    RULES_BY_DOMAIN,
    RuleTextMissing,
    rule_text,
    variants_of,
)
from signal_layer.rules.catalog import (
    BY_ID,
    CATALOG,
    RuleEntry,
    coverage_summary,
    render_markdown,
    validate_catalog,
)
from signal_layer.rules.r01_lender_join import (
    LENDER_SUBQUERY_SQL,
    LenderJoinError,
    inner_join_lenders,
    is_test_lender,
    resolve_lenders,
    validate_lender_join_sql,
)
from signal_layer.rules.r02_normalize import (
    AddressKeyError,
    address_key,
    building_number,
    norm_email,
    norm_string,
    norm_zip,
    same_address,
    street_name_prefix,
    try_address_key,
)
from signal_layer.rules.r04_alert_join import (
    APPLICATION_GRAIN,
    JoinKeyError,
    deduplicate_applications,
    join_alerts,
    join_keys,
    require_composite_key,
    submitted_to_lender_count,
    validate_join_sql,
)
from signal_layer.rules.r07_chargeoff import (
    default_counts,
    default_rate,
    is_default,
    is_pure_chargeoff,
)
from signal_layer.rules.r08_deprecated_tables import (
    APPROVED_ALL_RECORDS_TABLE,
    DEPRECATED_TABLES,
    DeprecatedTableError,
    is_deprecated_table,
    validate_no_deprecated_tables,
)
from signal_layer.rules.r09_coborrower_source import (
    Coborrower,
    CoborrowerResult,
    is_exhausted,
    next_source,
    resolve_coborrower,
)
from signal_layer.rules.r0105_lender_nicknames import (
    NICKNAME_INDEX,
    NICKNAME_TABLE,
    STELLANTIS_LENDER_NAMES,
    LenderResolution,
    LenderUnresolved,
    lender_like_pattern,
    norm_lender,
    resolve_lender,
)
from signal_layer.rules.r10_active_dealer import (
    DEALER_IDENTITY_KEY,
    DealerIdentityError,
    active_dealers,
    count_active_distinct_dealers,
    dealer_key,
    distinct_dealers,
    is_active_dealer,
)
from signal_layer.rules.r11_submitted_at import (
    DEFAULT_DATE_FIELD,
    SUBMITTED_FIELD,
    DateFieldDecision,
    ForbiddenDatePredicate,
    resolve_date_field,
    validate_date_sql,
)
from signal_layer.rules.r12_recommendations import (
    RecommendationCheck,
    RecommendationRuleViolation,
    assert_recommendation_ok,
    check_recommendation_text,
    requests_recommendations,
)
from signal_layer.rules.r13_retro import (
    NOT_RETRO_PREDICATE_SQL,
    RetroDecision,
    RetroRuleViolation,
    RetroScope,
    apply_retro_filter,
    is_non_retro,
    is_retro,
    resolve_retro_scope,
    validate_retro_sql,
)
from signal_layer.rules.r15_null_ssn import (
    count_distinct_ssns,
    distinct_ssns,
    is_present_ssn,
)
from signal_layer.rules.r17_fake_records import (
    KNOWN_FAKE_EMAILS,
    ExclusionReport,
    FakeRecordSources,
    UnsafeExclusionSQL,
    classify_record,
    exclude_fake_records,
    has_test_marker,
    null_safe_exclusion_sql,
    phone_excluded,
    validate_exclusion_sql,
)
from signal_layer.rules.r23_no_raw_ssn import (
    RawSSNError,
    SSNFinding,
    assert_no_raw_ssn,
    find_raw_ssn,
    is_clean,
    redact_ssns,
)
from signal_layer.rules.r25_invalid_credit import (
    is_valid_oldest_tradeline,
    is_valid_time_in_file,
    scrub_credit_fields,
)
from signal_layer.rules.r26_phone_equiv import (
    count_distinct_phones,
    distinct_phones,
    is_comparable_phone,
    norm_phone,
    phone_changed,
    same_phone,
)
from signal_layer.rules.r27_query_budget import (
    MAX_SQL_CALLS_PER_REQUEST,
    QueryBudget,
    QueryBudgetExceeded,
    incremental_batches,
    requires_incremental_summary,
)

__all__ = [
    "APPLICATION_GRAIN",
    "APPROVED_ALL_RECORDS_TABLE",
    "AddressKeyError",
    "BY_ID",
    "CATALOG",
    "Coborrower",
    "CoborrowerResult",
    "DEALER_IDENTITY_KEY",
    "DEFAULT_DATE_FIELD",
    "DEPRECATED_TABLES",
    "DateFieldDecision",
    "DealerIdentityError",
    "DeprecatedTableError",
    "ExclusionReport",
    "FakeRecordSources",
    "ForbiddenDatePredicate",
    "JoinKeyError",
    "KNOWN_FAKE_EMAILS",
    "LENDER_SUBQUERY_SQL",
    "LenderJoinError",
    "LenderResolution",
    "LenderUnresolved",
    "MAX_SQL_CALLS_PER_REQUEST",
    "NICKNAME_INDEX",
    "NICKNAME_TABLE",
    "NOT_RETRO_PREDICATE_SQL",
    "QueryBudget",
    "QueryBudgetExceeded",
    "REFERENCE_DOMAIN",
    "RULES",
    "RULES_BY_DOMAIN",
    "RULE_IDS",
    "RawSSNError",
    "RecommendationCheck",
    "RecommendationRuleViolation",
    "RetroDecision",
    "RetroRuleViolation",
    "RetroScope",
    "RuleEntry",
    "RuleTextMissing",
    "SSNFinding",
    "STELLANTIS_LENDER_NAMES",
    "SUBMITTED_FIELD",
    "UnsafeExclusionSQL",
    "active_dealers",
    "address_key",
    "apply_retro_filter",
    "assert_no_raw_ssn",
    "assert_recommendation_ok",
    "building_number",
    "check_recommendation_text",
    "classify_record",
    "count_active_distinct_dealers",
    "count_distinct_phones",
    "count_distinct_ssns",
    "coverage_summary",
    "dealer_key",
    "deduplicate_applications",
    "default_counts",
    "default_rate",
    "distinct_dealers",
    "distinct_phones",
    "distinct_ssns",
    "exclude_fake_records",
    "find_raw_ssn",
    "has_test_marker",
    "incremental_batches",
    "inner_join_lenders",
    "is_active_dealer",
    "is_clean",
    "is_comparable_phone",
    "is_deprecated_table",
    "is_default",
    "is_exhausted",
    "is_non_retro",
    "is_present_ssn",
    "is_pure_chargeoff",
    "is_retro",
    "is_test_lender",
    "is_valid_oldest_tradeline",
    "is_valid_time_in_file",
    "join_alerts",
    "join_keys",
    "lender_like_pattern",
    "next_source",
    "norm_email",
    "norm_lender",
    "norm_phone",
    "norm_string",
    "norm_zip",
    "null_safe_exclusion_sql",
    "phone_changed",
    "phone_excluded",
    "redact_ssns",
    "render_markdown",
    "require_composite_key",
    "requests_recommendations",
    "requires_incremental_summary",
    "resolve_coborrower",
    "resolve_date_field",
    "resolve_lender",
    "resolve_lenders",
    "resolve_retro_scope",
    "rule_text",
    "same_address",
    "same_phone",
    "scrub_credit_fields",
    "street_name_prefix",
    "submitted_to_lender_count",
    "try_address_key",
    "validate_catalog",
    "validate_date_sql",
    "validate_exclusion_sql",
    "validate_join_sql",
    "validate_lender_join_sql",
    "validate_no_deprecated_tables",
    "validate_retro_sql",
    "variants_of",
]
