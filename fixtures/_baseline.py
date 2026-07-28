"""
The benign baseline every scenario starts from, and the example rows that explain each value.

WHY a baseline: each application must carry all 60 signals and all 60 ``<signal>_context`` keys.
Authoring 14 x 120 entries by hand reproduces the defect this fixture set removes — the legacy
module filled 20-30 signals per record and let ``.get(name, 0)`` invent the rest. Here the
baseline is an explicit, reviewable statement of "what an unremarkable application looks like",
each scenario declares only its deviations, and ``_build.build()`` still refuses to emit a record
unless all 60 values and all 60 context rows are present. Nothing is defaulted at read time.

The values are the low end of the customer's own stated bands (``ssn_distinct_identity_combinations
= 1 is NORMAL``, ``email_reuse_distinct_ssn_count: 1 is normal``, ``phone_reuse_distinct_ssn_count:
1-2 is normal``) so a scenario that overrides nothing is genuinely a LOW-across-the-board record
rather than an accidentally-interesting one.

``BASELINE_CONTEXT`` holds only the ``example_rows`` half of each context block; the customer's
interpretation sentence is read from ``prompts/verbatim/`` at generation time by
``_spec.interpretation_for`` and is never typed here.
"""

from __future__ import annotations

from typing import Any, Final

#: Unremarkable values for all 60 signals, in registry order.
BASELINE_SIGNALS: Final[dict[str, Any]] = {
    # identity
    "ssn_distinct_identity_combinations": 1,
    "phone_reuse_distinct_ssn_count": 1,
    "email_reuse_distinct_ssn_count": 1,
    "address_reuse_distinct_ssn_count": 1,
    "is_multi_unit_address": False,
    "ssn_days_in_system": 1180,
    "inquiries_in_2weeks": 1,
    # dealer
    "dealer_consortium_score": 320,
    "dealer_risk_score": 280,
    "dealer_lender_default_rate": 0.04,
    "dealer_consortium_epd_rate_last_24mo": 0.03,
    "phone_reuse_count_this_dealer": 0,
    "employer_concentration_pct": 4.0,
    "ssn_previous_apps_all_time": 2,
    "ssn_previous_apps_90d": 1,
    "previous_epd_count": 0,
    "previous_999_count": 0,
    "borr_ssn_in_negative_file": 0,
    "borr_ssn_linked_to_synthetic_fraud": 0,
    "dealer_app_volume_24mo": 1450,
    "dealer_apps_last_7d": 11,
    # straw
    "has_coborrower": False,
    "coborrower_appears_as_borrower_count": 0,
    "borrower_appears_as_coborrower_count": 0,
    "coborr_distinct_primary_borrowers_count": 0,
    "credit_score_gap": 0,
    "coborr_income_70_percent_plus": 0,
    "coborr_3_plus_apps_same_dealer_flag": 0,
    "high_value_vehicle_weak_profile_straw_risk": 0,
    # employment
    "employer_distinct_other_ssns_count": 41,
    "employer_new_ssns_90d": 3,
    "employer_phone_distinct_ssn_count": 41,
    "rapid_employer_change_flag": 0,
    "employer_spike_flag": 0,
    "ssn_distinct_employers_all_time": 2,
    "ssn_distinct_employers_30d": 1,
    "ssn_distinct_employers_60d": 1,
    # income
    "ssn_distinct_income_amounts_all_time": 2,
    "ssn_income_increase_20_percent_or_more": 0,
    "ssn_multiple_apps_7d_different_incomes": 0,
    "ssn_stated_income_exceeds_verified_by_25_percent": 0,
    "ssn_income_overstatement_percentage": 4,
    "loan_to_annual_income_ratio": 0.38,
    "ssn_income_high_variance_inconsistent_reporting": 0,
    # synthetic
    "ssn_address_location_variations": 1,
    "has_significant_identity_inconsistency": 0,
    "address_ssn_count_same_building": 1,
    "address_ssn_count_same_unit": 1,
    "phone_ssn_count": 1,
    "high_authorized_user_ratio": 0,
    "is_thin_file": 0,
    "very_new_file_high_score": 0,
    "older_borrower_new_credit_high_score": 0,
    "rapid_app_velocity": 0,
    # bustout
    "ssn_apps_last_7d": 1,
    "ssn_apps_last_30d": 1,
    "ssn_income_increased_20_percent_last_30d_rapid_growth": 0,
    "ssn_loan_exceeds_annual_income_unsustainable_debt": 0,
    "ssn_loan_80_percent_or_more_annual_income_high_ratio": 0,
    # rings
    "employer_other_ssns_count": 41,
}

#: ``example_rows`` for each baseline value: what the signal layer would have shown the agent.
#: Phrased as the historical rows the customer's own ``_context`` columns carry ("shows example
#: applications at the dealer in the last 7 days", SIGBOUND-03), not as a verdict.
BASELINE_CONTEXT: Final[dict[str, str]] = {
    "ssn_distinct_identity_combinations": (
        "1 combination on this hash_ssn: PAT MORALES / 1987-04-xx, seen on 2 prior applications "
        "with the same normalized first name, last name and DOB."
    ),
    "phone_reuse_distinct_ssn_count": (
        "Normalized phone matches 1 hash_ssn - this borrower only. No other consortium record "
        "shares the number."
    ),
    "email_reuse_distinct_ssn_count": (
        "Normalized email matches 1 hash_ssn - this borrower only. Not present in the fake-email "
        "source."
    ),
    "address_reuse_distinct_ssn_count": (
        "Address key resolves to 1 hash_ssn. Prior rows: 2 applications from this borrower at "
        "this address, 14 and 26 months ago."
    ),
    "is_multi_unit_address": (
        "Property type on file is single-family detached; no unit or apartment token parsed from "
        "the street address."
    ),
    "ssn_days_in_system": (
        "First consortium sighting 1,180 days ago; 2 applications since, both with this employer "
        "and this address."
    ),
    "inquiries_in_2weeks": (
        "1 inquiry in the trailing 14 days, on this application. Credit score 704."
    ),
    "dealer_consortium_score": (
        "Dealer scored 320 in the current month and 305-340 across the trailing 12 months - flat, "
        "no upward trend."
    ),
    "dealer_risk_score": (
        "Lender-portfolio score for this dealer_id / lender_id pair is 280 over 96 funded loans."
    ),
    "dealer_lender_default_rate": (
        "4 defaults (charge-off or 90+) across 96 funded loans for this dealer with this lender."
    ),
    "dealer_consortium_epd_rate_last_24mo": (
        "43 early payment defaults across 1,450 consortium applications in the trailing 24 months."
    ),
    "phone_reuse_count_this_dealer": (
        "0 other borrowers at this dealer share this normalized phone number; across the "
        "dealer's last 120 applications, 120 distinct phones appear."
    ),
    "employer_concentration_pct": (
        "Top employer at this dealer accounts for 58 of 1,450 applications (4.0%); this "
        "borrower's employer accounts for 11."
    ),
    "ssn_previous_apps_all_time": (
        "2 prior applications on this hash_ssn: one 26 months ago (funded, current), one 14 "
        "months ago (declined for LTV)."
    ),
    "ssn_previous_apps_90d": "1 prior application in the trailing 90 days, at this same dealer.",
    "previous_epd_count": (
        "0 early payment defaults across the 2 prior applications for this hash_ssn."
    ),
    "previous_999_count": (
        "No prior application for this hash_ssn carried a fraud score of 999; prior scores were "
        "168 and 205."
    ),
    "borr_ssn_in_negative_file": (
        "hash_ssn is not present in the negative file; 0 matching entries across all "
        "contributing lenders."
    ),
    "borr_ssn_linked_to_synthetic_fraud": (
        "0 confirmed synthetic-fraud records are linked to this hash_ssn across 2 prior "
        "funded loans."
    ),
    "dealer_app_volume_24mo": (
        "1,450 consortium applications in the trailing 24 months; consortium_number_of_apps_last_"
        "1_year is 690, so the dealer is active."
    ),
    "dealer_apps_last_7d": (
        "11 applications at this dealer in the last 7 days: 11 distinct hash_ssn values, 11 "
        "distinct addresses, 11 distinct phones, 9 distinct employers."
    ),
    "has_coborrower": (
        "No co-borrower on this application. coborrower source tables return no row for this "
        "(application_id, hash_lender_id)."
    ),
    "coborrower_appears_as_borrower_count": (
        "No co-borrower on this application, so there are 0 co-borrower role records to "
        "compare - an absence of a party, not a party with 0 switches."
    ),
    "borrower_appears_as_coborrower_count": (
        "0 consortium applications carry this hash_ssn as a co-borrower."
    ),
    "coborr_distinct_primary_borrowers_count": (
        "No co-borrower on this application, so 0 primary borrowers are linked through one."
    ),
    "credit_score_gap": "No co-borrower, so no second credit score to compare against 704.",
    "coborr_income_70_percent_plus": (
        "No co-borrower income on file, so the co-borrower share of combined income is 0%."
    ),
    "coborr_3_plus_apps_same_dealer_flag": (
        "No co-borrower, so 0 co-borrower applications exist at this or any dealer."
    ),
    "high_value_vehicle_weak_profile_straw_risk": (
        "Vehicle is mid-market and the loan is 0.38x annual income; profile and collateral are "
        "proportionate."
    ),
    "employer_distinct_other_ssns_count": (
        "41 distinct hash_ssn values cite this employer across the consortium, spread over 5 "
        "years; the employer has a verifiable address, phone, city, state and ZIP."
    ),
    "employer_new_ssns_90d": (
        "3 new hash_ssn values cited this employer in the last 90 days, against 41 all-time - in "
        "line with its historical rate of 2-4 per quarter."
    ),
    "employer_phone_distinct_ssn_count": (
        "41 hash_ssn values share the employer's main line, which is a listed business number, "
        "not a personal cell."
    ),
    "rapid_employer_change_flag": (
        "Employer on file has been the same across the last 2 applications, 26 and 14 months ago."
    ),
    "employer_spike_flag": (
        "Monthly applications citing this employer: 2, 1, 3, 2, 2, 1 over the last 6 months. No "
        "spike."
    ),
    "ssn_distinct_employers_all_time": (
        "2 employers all-time for this hash_ssn: the current one for 4 years, one prior for 3."
    ),
    "ssn_distinct_employers_30d": "1 employer cited in the last 30 days - the current one.",
    "ssn_distinct_employers_60d": "1 employer cited in the last 60 days - the current one.",
    "ssn_distinct_income_amounts_all_time": (
        "2 stated amounts on this hash_ssn: 61,000 (26 months ago) and 66,000 (this application)."
    ),
    "ssn_income_increase_20_percent_or_more": (
        "Largest change between consecutive stated incomes is +8.2% over 26 months."
    ),
    "ssn_multiple_apps_7d_different_incomes": (
        "Only 1 application in the trailing 7 days, so no within-week income comparison exists."
    ),
    "ssn_stated_income_exceeds_verified_by_25_percent": (
        "Stated 66,000 against verified 63,400 - a 4.1% gap, inside the reporting-variance band."
    ),
    "ssn_income_overstatement_percentage": (
        "Stated 66,000 vs verified 63,400 = 4% overstatement. Prior application overstated 3%."
    ),
    "loan_to_annual_income_ratio": "Loan 25,100 against stated annual income 66,000 = 0.38.",
    "ssn_income_high_variance_inconsistent_reporting": (
        "Two stated amounts 26 months apart, coefficient of variation 0.05. W-2 employee, single "
        "employer."
    ),
    "ssn_address_location_variations": (
        "1 address location for this hash_ssn across all prior applications, same city and state."
    ),
    "has_significant_identity_inconsistency": (
        "Normalized first name, last name, DOB and address key match on all 3 applications for "
        "this hash_ssn."
    ),
    "address_ssn_count_same_building": (
        "1 hash_ssn at this building - this borrower. Single-family property."
    ),
    "address_ssn_count_same_unit": "1 hash_ssn at this unit - this borrower.",
    "phone_ssn_count": "1 hash_ssn on this normalized phone number - this borrower.",
    "high_authorized_user_ratio": (
        "1 authorized-user tradeline out of 9 open tradelines (11%). Oldest tradeline opened 11 "
        "years ago."
    ),
    "is_thin_file": (
        "9 open tradelines, time in file 134 months. Credit report present and valid, so thin-file "
        "reasoning is assessable."
    ),
    "very_new_file_high_score": "Time in file 134 months with a 704 score - established, not new.",
    "older_borrower_new_credit_high_score": (
        "Borrower is 38 with an 11-year credit history; the file is not new for the borrower's age."
    ),
    "rapid_app_velocity": (
        "1 application in 7 days, 1 in 30, 2 in 90. No clustering across dealers."
    ),
    "ssn_apps_last_7d": "1 application in the trailing 7 days: this one, at this dealer.",
    "ssn_apps_last_30d": "1 application in the trailing 30 days: this one.",
    "ssn_income_increased_20_percent_last_30d_rapid_growth": (
        "No stated-income change inside the trailing 30 days; the prior amount is 26 months old."
    ),
    "ssn_loan_exceeds_annual_income_unsustainable_debt": (
        "Loan 25,100 against annual income 66,000. Existing monthly obligations 640."
    ),
    "ssn_loan_80_percent_or_more_annual_income_high_ratio": (
        "Loan is 38% of stated annual income, well under the 80% threshold encoded in the signal "
        "name."
    ),
    "employer_other_ssns_count": (
        "41 other hash_ssn values share this employer, across 6 states and 5 years of "
        "applications - no dealer or address clustering among them."
    ),
}

#: An unremarkable core application record. Scenarios override the fields their story needs.
BASELINE_APPLICATION: Final[dict[str, Any]] = {
    "hash_lender_id": "hash_lndr_5f2a",
    "lender_name": "SUMMIT_CU",
    "borrower_name": "Pat Morales",
    "hash_ssn": "hash_ssn_4d19c7",
    "borrower_age": 38,
    "credit_score": 704,
    "fraud_score": 168,
    "income_misrep_score": 9,
    "loan_amount": 25100,
    "stated_annual_income": 66000,
    "verified_annual_income": 63400,
    "vehicle": "2021 Subaru Outback",
    "ltv_pct": 89,
    "dealer_name": "Cedar Hollow Auto",
    "ppi_dealer_id": "PPI-DLR-3308",
    "consortium_number_of_apps_last_1_year": 690,
    "employer_name": "Fairview Regional Clinics",
    "employer_street_addr": "4120 Fairview Pkwy",
    "employer_city": "Boise",
    "employer_state": "ID",
    "employer_zip": "83702",
    "employer_phone": "208-555-0142",
    "occupation": "RADIOLOGY TECHNICIAN",
    "application_date": "2026-06-24",
    "submitted_to_prod_at": "2026-06-24T15:02:00Z",
    "product": "AUTO",
}


def signals(**overrides: Any) -> dict[str, Any]:
    """Baseline signal values with the named overrides applied. Unknown names raise."""
    unknown = [k for k in overrides if k not in BASELINE_SIGNALS]
    if unknown:
        raise KeyError(f"{unknown} are not registry signals; check the spelling in the prompt")
    return {**BASELINE_SIGNALS, **overrides}


def context(**overrides: str) -> dict[str, str]:
    """Baseline example rows with the named overrides applied. Unknown names raise."""
    unknown = [k for k in overrides if k not in BASELINE_CONTEXT]
    if unknown:
        raise KeyError(f"{unknown} are not registry signals; check the spelling in the prompt")
    return {**BASELINE_CONTEXT, **overrides}


def application(**overrides: Any) -> dict[str, Any]:
    """Baseline core application fields with the named overrides applied."""
    return {**BASELINE_APPLICATION, **overrides}


__all__ = [
    "BASELINE_APPLICATION",
    "BASELINE_CONTEXT",
    "BASELINE_SIGNALS",
    "application",
    "context",
    "signals",
]
