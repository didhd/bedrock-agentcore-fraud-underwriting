"""
Synthetic auto-loan application dataset for the fraud-detection demo.

This module stands in for the auto-lending fraud platform's Snowflake consortium tables
(ALL_RECORDS_ALL__ALL_RECORDS_FOR_PG, TBL_AUTO_CONSORTIUM, TBL_AFA_ALERTS_CLEAN,
dealer stats, etc.) and the Cortex Analyst semantic view.

DEMO DATA ONLY. No real PII:
  - SSNs are fake hashes ("hash_ssn_xxx"), never raw values (Rule 23 in the
    original orchestration: never surface raw SSN).
  - Names, dealers, employers, emails are fictional.

Each application carries:
  - core application fields (loan, borrower profile, dealer, employer)
  - precomputed fraud SIGNALS (what the specialist agents validate)
  - matching *_context fields (the benign-explanation context the agents
    must weigh before escalating — mirrors the "_context" columns in prod)

The five applications are designed to exercise different orchestration paths:
  APP-1001  clean first-time buyer            -> expect LOW / APPROVE
  APP-1002  spousal co-borrower, thin file    -> expect LOW / APPROVE
  APP-1003  income overstatement, ambiguous   -> expect MEDIUM / REVIEW+STIPULATE
  APP-1004  synthetic identity + ring overlap -> expect HIGH / DECLINE
  APP-1005  bust-out velocity pattern         -> expect MEDIUM-HIGH / REVIEW
"""

from __future__ import annotations
from typing import Any


APPLICATIONS: dict[str, dict[str, Any]] = {

    # ------------------------------------------------------------------
    # APP-1001 — Clean first-time buyer. Everything explainable. LOW risk.
    # ------------------------------------------------------------------
    "APP-1001": {
        "application_id": "APP-1001",
        "hash_lender_id": "LNDR_A1",
        "lender_name": "SUMMIT_CU",
        "borrower_name": "Jordan Blake",
        "hash_ssn": "hash_ssn_7f3a91",
        "fraud_score": 142,               # 0-299 low
        "income_misrep_score": 8,         # income-misrep model: low
        "loan_amount": 24500,
        "stated_annual_income": 68000,
        "vehicle": "2021 Honda Civic",
        "ltv_pct": 88,
        "dealer_name": "Sunrise Auto Group",
        "consortium_dealer_id": "DLR-330",
        "employer_name": "Regional Medical Center",
        "has_coborrower": False,

        "signals": {
            # identity
            "ssn_distinct_identity_combinations": 1,
            "phone_reuse_distinct_ssn_count": 1,
            "email_reuse_distinct_ssn_count": 1,
            "address_reuse_distinct_ssn_count": 2,
            "ssn_days_in_system": 0,
            # income
            "ssn_distinct_income_amounts_all_time": 1,
            "ssn_income_increase_20_percent_or_more": 0,
            "ssn_income_overstatement_percentage": 3,
            "loan_to_annual_income_ratio": 0.36,
            # employment
            "employer_distinct_other_ssns_count": 480,
            "employer_new_ssns_90d": 12,
            "rapid_employer_change_flag": 0,
            "ssn_distinct_employers_all_time": 2,
            # synthetic
            "is_thin_file": 1,
            "very_new_file_high_score": 0,
            "rapid_app_velocity": 0,
            "high_authorized_user_ratio": 0,
            # bustout
            "ssn_apps_last_7d": 1,
            "ssn_apps_last_30d": 1,
            "ssn_loan_80_percent_or_more_annual_income_high_ratio": 0,
            # dealer
            "dealer_consortium_score": 310,
            "dealer_consortium_epd_rate_last_24mo": 0.03,
            "phone_reuse_count_this_dealer": 0,
            "borr_ssn_in_negative_file": 0,
            "borr_ssn_linked_to_synthetic_fraud": 0,
        },
        "context": {
            "is_multi_unit_address": True,
            "address_reuse_context": "Address is a 40-unit apartment complex; reuse across units is expected.",
            "ssn_days_in_system_context": "First-time buyer, no prior consortium history. Common and benign.",
            "employer_context": "Regional Medical Center is a large hospital employer (480 SSNs). High count is normal for employer scale.",
            "thin_file_context": "Borrower is 24 years old. Thin file consistent with age; no velocity or reuse signals.",
            "dealer_context": "Dealer has 1,900 apps/24mo, low EPD rate. Normal operational volume.",
        },
    },

    # ------------------------------------------------------------------
    # APP-1002 — Spousal co-borrower, thin file. Explainable. LOW risk.
    # ------------------------------------------------------------------
    "APP-1002": {
        "application_id": "APP-1002",
        "hash_lender_id": "LNDR_B2",
        "lender_name": "RIVERBEND_FCU",
        "borrower_name": "Priya Nair",
        "hash_ssn": "hash_ssn_2c88de",
        "fraud_score": 388,               # medium band but explainable
        "income_misrep_score": 11,
        "loan_amount": 31200,
        "stated_annual_income": 72000,
        "vehicle": "2022 Toyota RAV4",
        "ltv_pct": 91,
        "dealer_name": "Lakeside Motors",
        "consortium_dealer_id": "DLR-118",
        "employer_name": "Northwind Logistics",
        "has_coborrower": True,
        "coborrower_name": "Arjun Nair",
        "coborrower_relationship": "spouse",

        "signals": {
            "ssn_distinct_identity_combinations": 1,
            "phone_reuse_distinct_ssn_count": 2,
            "email_reuse_distinct_ssn_count": 1,
            "address_reuse_distinct_ssn_count": 2,
            "ssn_days_in_system": 210,
            "ssn_distinct_income_amounts_all_time": 2,
            "ssn_income_increase_20_percent_or_more": 0,
            "ssn_income_overstatement_percentage": 6,
            "loan_to_annual_income_ratio": 0.43,
            "employer_distinct_other_ssns_count": 34,
            "employer_new_ssns_90d": 3,
            "rapid_employer_change_flag": 0,
            "ssn_distinct_employers_all_time": 1,
            "is_thin_file": 1,
            "very_new_file_high_score": 0,
            "rapid_app_velocity": 0,
            "high_authorized_user_ratio": 1,
            # straw
            "coborrower_appears_as_borrower_count": 0,
            "borrower_appears_as_coborrower_count": 0,
            "coborr_distinct_primary_borrowers_count": 1,
            "credit_score_gap": 45,
            "coborr_income_70_percent_plus": 0,
            "coborr_3_plus_apps_same_dealer_flag": 0,
            "ssn_apps_last_7d": 1,
            "ssn_apps_last_30d": 1,
            "dealer_consortium_score": 520,
            "phone_reuse_count_this_dealer": 1,
            "borr_ssn_in_negative_file": 0,
            "borr_ssn_linked_to_synthetic_fraud": 0,
        },
        "context": {
            "coborrower_context": "Co-borrower shares surname and address (spouse). Shared phone (count 2) consistent with household.",
            "high_authorized_user_context": "Authorized-user tradelines typical for a newer credit-builder; no velocity or reuse.",
            "thin_file_context": "Recently established credit; 210 days in system with stable single employer.",
            "dealer_context": "Dealer consortium score 520 (moderate). Single shared phone reflects the spousal pair, not coordination.",
        },
    },

    # ------------------------------------------------------------------
    # APP-1003 — Income overstatement, ambiguous. MEDIUM / REVIEW+STIPULATE.
    # ------------------------------------------------------------------
    "APP-1003": {
        "application_id": "APP-1003",
        "hash_lender_id": "LNDR_C3",
        "lender_name": "HARBOR_BANK",
        "borrower_name": "Marcus Webb",
        "hash_ssn": "hash_ssn_9a41b0",
        "fraud_score": 655,               # medium-high
        "income_misrep_score": 62,        # income-misrep model: elevated
        "loan_amount": 41800,
        "stated_annual_income": 95000,
        "vehicle": "2023 Ford F-150",
        "ltv_pct": 96,
        "dealer_name": "Highway 9 Auto",
        "consortium_dealer_id": "DLR-207",
        "employer_name": "Webb Contracting LLC",
        "has_coborrower": False,

        "signals": {
            "ssn_distinct_identity_combinations": 1,
            "phone_reuse_distinct_ssn_count": 1,
            "email_reuse_distinct_ssn_count": 1,
            "address_reuse_distinct_ssn_count": 1,
            "ssn_days_in_system": 900,
            # income — the crux
            "ssn_distinct_income_amounts_all_time": 3,
            "ssn_income_increase_20_percent_or_more": 1,
            "ssn_multiple_apps_7d_different_incomes": 0,
            "ssn_stated_income_exceeds_verified_by_25_percent": 1,
            "ssn_income_overstatement_percentage": 31,
            "loan_to_annual_income_ratio": 0.44,
            "ssn_income_high_variance_inconsistent_reporting": 1,
            # employment
            "employer_distinct_other_ssns_count": 2,
            "employer_new_ssns_90d": 0,
            "rapid_employer_change_flag": 0,
            "ssn_distinct_employers_all_time": 3,
            # synthetic / bustout
            "is_thin_file": 0,
            "rapid_app_velocity": 0,
            "ssn_apps_last_7d": 1,
            "ssn_apps_last_30d": 2,
            "ssn_loan_80_percent_or_more_annual_income_high_ratio": 0,
            "dealer_consortium_score": 640,
            "phone_reuse_count_this_dealer": 0,
            "borr_ssn_in_negative_file": 0,
            "borr_ssn_linked_to_synthetic_fraud": 0,
        },
        "context": {
            "income_context": "Stated income exceeds verified by 31% (materially above the 25% threshold). Borrower is self-employed (Webb Contracting LLC), which explains variance but not the magnitude of overstatement.",
            "self_employed_context": "Self-employment causes legitimate income variance and seasonal swings, but the income-misrepresentation score of 62 is elevated.",
            "employer_context": "Employer is borrower's own single-member LLC (2 SSNs). Not a shell-employer pattern by itself.",
            "ltv_context": "LTV 96% is high; combined with income overstatement raises loss-given-default exposure.",
        },
    },

    # ------------------------------------------------------------------
    # APP-1004 — Synthetic identity + fraud-ring overlap. HIGH / DECLINE.
    # ------------------------------------------------------------------
    "APP-1004": {
        "application_id": "APP-1004",
        "hash_lender_id": "LNDR_D4",
        "lender_name": "MERIDIAN_FINANCE",
        "borrower_name": "Devon Cross",
        "hash_ssn": "hash_ssn_ff02aa",
        "fraud_score": 999,               # accelerator fired — very high
        "income_misrep_score": 71,
        "loan_amount": 58900,
        "stated_annual_income": 88000,
        "vehicle": "2024 BMW X5",
        "ltv_pct": 112,
        "dealer_name": "Prestige Import Center",
        "consortium_dealer_id": "DLR-451",
        "employer_name": "Apex Global Solutions",
        "has_coborrower": False,

        "signals": {
            # identity / synthetic — multiple INDEPENDENT inconsistencies
            "ssn_distinct_identity_combinations": 3,
            "has_significant_identity_inconsistency": 1,
            "ssn_address_location_variations": 4,
            "phone_reuse_distinct_ssn_count": 5,
            "email_reuse_distinct_ssn_count": 4,
            "address_reuse_distinct_ssn_count": 6,
            "address_ssn_count_same_unit": 5,
            "phone_ssn_count": 5,
            "ssn_days_in_system": 47,
            "is_thin_file": 1,
            "very_new_file_high_score": 1,
            "older_borrower_new_credit_high_score": 1,
            "high_authorized_user_ratio": 1,
            "rapid_app_velocity": 1,
            # rings
            "employer_other_ssns_count": 9,
            # employment — shell pattern
            "employer_distinct_other_ssns_count": 9,
            "employer_new_ssns_90d": 8,
            "employer_spike_flag": 1,
            "rapid_employer_change_flag": 1,
            "ssn_distinct_employers_all_time": 4,
            "ssn_distinct_employers_30d": 2,
            # income / bustout
            "ssn_stated_income_exceeds_verified_by_25_percent": 1,
            "ssn_income_overstatement_percentage": 44,
            "loan_to_annual_income_ratio": 0.67,
            "ssn_apps_last_7d": 4,
            "ssn_apps_last_30d": 7,
            # dealer
            "dealer_consortium_score": 930,
            "phone_reuse_count_this_dealer": 4,
            "borr_ssn_in_negative_file": 1,
            "borr_ssn_linked_to_synthetic_fraud": 1,
        },
        "context": {
            "identity_context": "SSN linked to 3 distinct name/DOB combinations with 4 address-state variations in 47 days. Not explainable by relocation.",
            "synthetic_context": "Thin file only 47 days old but high credit score on an older borrower, high authorized-user ratio, and manufactured-credit pattern. Classic synthetic construction.",
            "ring_context": "Same unit-level address shared by 5 unrelated SSNs; phone shared across 5 SSNs; email across 4; employer 'Apex Global Solutions' added 8 new SSNs in 90 days (spike). Exact-unit reuse, not building-level.",
            "employer_context": "Apex Global Solutions has 9 total SSNs, 8 new in 90 days, with a spike flag. Consistent with a fabricated/shell employer.",
            "dealer_context": "Dealer consortium score 930 (high) AND phone reuse of 4 at this dealer with synthetic-linked borrowers — coordination, not just volume.",
            "negative_file_context": "Borrower SSN is in the negative file and linked to prior synthetic fraud. Direct corroboration.",
            "ltv_context": "LTV 112% on a high-value vehicle with a weak/thin profile.",
        },
    },

    # ------------------------------------------------------------------
    # APP-1005 — Bust-out velocity pattern. MEDIUM-HIGH / REVIEW.
    # ------------------------------------------------------------------
    "APP-1005": {
        "application_id": "APP-1005",
        "hash_lender_id": "LNDR_E5",
        "lender_name": "PIONEER_AUTO_FINANCE",
        "borrower_name": "Alicia Moreno",
        "hash_ssn": "hash_ssn_5b7c30",
        "fraud_score": 712,               # high band
        "income_misrep_score": 28,
        "loan_amount": 46000,
        "stated_annual_income": 54000,
        "vehicle": "2023 Jeep Grand Cherokee",
        "ltv_pct": 103,
        "dealer_name": "Metro Motors East",
        "consortium_dealer_id": "DLR-289",
        "employer_name": "Contract Staffing Partners",
        "has_coborrower": False,

        "signals": {
            "ssn_distinct_identity_combinations": 1,
            "phone_reuse_distinct_ssn_count": 2,
            "email_reuse_distinct_ssn_count": 1,
            "address_reuse_distinct_ssn_count": 2,
            "ssn_days_in_system": 1400,
            # bustout — velocity + exposure
            "ssn_apps_last_7d": 5,
            "ssn_apps_last_30d": 9,
            "rapid_app_velocity": 1,
            "ssn_income_increased_20_percent_last_30d_rapid_growth": 1,
            "ssn_income_high_variance_inconsistent_reporting": 1,
            "ssn_loan_exceeds_annual_income_unsustainable_debt": 0,
            "ssn_loan_80_percent_or_more_annual_income_high_ratio": 1,
            "loan_to_annual_income_ratio": 0.85,
            # income
            "ssn_distinct_income_amounts_all_time": 3,
            "ssn_income_increase_20_percent_or_more": 1,
            "ssn_income_overstatement_percentage": 18,
            # employment
            "employer_distinct_other_ssns_count": 260,
            "employer_new_ssns_90d": 40,
            "rapid_employer_change_flag": 1,
            "ssn_distinct_employers_all_time": 5,
            "ssn_distinct_employers_30d": 1,
            # synthetic / identity
            "is_thin_file": 0,
            "very_new_file_high_score": 0,
            # dealer
            "dealer_consortium_score": 710,
            "phone_reuse_count_this_dealer": 1,
            "borr_ssn_in_negative_file": 0,
            "borr_ssn_linked_to_synthetic_fraud": 0,
        },
        "context": {
            "velocity_context": "5 applications in 7 days, 9 in 30 days. Exceeds normal rate-shopping. Coupled with rapid 20%+ income growth in 30 days with no employer change.",
            "income_context": "Income growth reported without an employment change; variance flagged. 18% overstatement is in the ambiguous band.",
            "employer_context": "Contract Staffing Partners is a staffing agency (260 SSNs, 40 new in 90d). High counts are normal for staffing agencies — this is NOT a shell-employer signal on its own.",
            "loan_ratio_context": "Loan is 85% of annual income and LTV 103% — elevated exposure that amplifies velocity concern.",
            "identity_context": "No identity manipulation and SSN well-established (1400 days). Bust-out concern rests on velocity + exposure, not identity fabrication.",
        },
    },
}


def list_application_ids() -> list[str]:
    """Return all demo application IDs."""
    return list(APPLICATIONS.keys())


def get_application(application_id: str) -> dict[str, Any] | None:
    """Fetch a single application record by ID (case-insensitive)."""
    if not application_id:
        return None
    return APPLICATIONS.get(application_id.strip().upper())
