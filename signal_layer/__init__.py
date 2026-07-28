"""
The signal layer: the governed boundary between precomputed signals and agent analysis.

Point Predictive's own architecture (Agent_Underwriting_Process_Guide, section 2.2)
draws a hard line here: "The signal generation layer transforms raw application data
into normalized underwriting signals used downstream by AI fraud agents. These signals
are customized to each of the subagent fraud areas to allow for ease of review and
optimize processing time by preventing the agents from making the same SQL queries for
each app."

Every module in this package exists to make that sentence executable:

  registry          - which signals exist, which domains own them, and the customer's
                      verbatim interpretation band for each one
  alert_categories  - the ten alert-ID category sets from General Rule 3
  bands             - the interpretive scales (fraud score, dealer scores, PRODUCT
                      acronyms, credit-report validity)
  schema            - the typed payload a specialist agent receives, and the projection
                      that guarantees it sees ONLY its own domain's signals

Nothing here imports boto3, strands, or bedrock_agentcore: the contract must be
importable and testable offline, because it is the thing every other module depends on.
"""

from __future__ import annotations

from signal_layer.alert_categories import (
    ALERT_CATEGORIES,
    CATEGORY_ALERTS,
    DISREGARDED_ALERTS,
    DOMAIN_ALERT_CATEGORIES,
    DOMAIN_EXTRA_ALERTS,
    MULTI_CATEGORY_ALERTS,
    alerts_for_domain,
    category_of,
)
from signal_layer.bands import (
    CREDIT_REPORT_VALIDITY_RULE,
    DEALER_SCORE_BINDING,
    DEALER_SCORE_BANDS,
    FRAUD_SCORE_999_MEANING,
    FRAUD_SCORE_BANDS,
    INCOMEPASS_SEMANTICS,
    PRODUCTS,
    Product,
    dealer_score_band,
    decode_product,
    fraud_score_band,
    is_incomepass_employment_accelerator,
    is_valid_time_in_file,
)
from signal_layer.registry import (
    DOMAINS,
    SIGNAL_SPEC,
    SignalSpec,
    signals_for,
)
from signal_layer.schema import (
    AlertFinding,
    DomainView,
    SignalPayload,
    render_for_agent,
)

__all__ = [
    "ALERT_CATEGORIES",
    "AlertFinding",
    "CATEGORY_ALERTS",
    "CREDIT_REPORT_VALIDITY_RULE",
    "DEALER_SCORE_BANDS",
    "DEALER_SCORE_BINDING",
    "DISREGARDED_ALERTS",
    "DOMAINS",
    "DOMAIN_ALERT_CATEGORIES",
    "DOMAIN_EXTRA_ALERTS",
    "DomainView",
    "FRAUD_SCORE_999_MEANING",
    "FRAUD_SCORE_BANDS",
    "INCOMEPASS_SEMANTICS",
    "MULTI_CATEGORY_ALERTS",
    "PRODUCTS",
    "Product",
    "SIGNAL_SPEC",
    "SignalPayload",
    "SignalSpec",
    "alerts_for_domain",
    "category_of",
    "dealer_score_band",
    "decode_product",
    "fraud_score_band",
    "is_incomepass_employment_accelerator",
    "is_valid_time_in_file",
    "render_for_agent",
    "signals_for",
]
