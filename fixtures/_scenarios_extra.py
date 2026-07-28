"""
Four applications that fill the remaining bands the first ten leave untested.

The legacy five and the five anti-false-positive records between them produce eight LOW records,
two MEDIUM and one HIGH — good calibration (XAG-003: LOW should be the most common outcome), but
they leave three specialist branches with no exercise at all:

  APP-1011  no co-borrower, nothing else notable  -> straw's bare 2-3 sentence minimal-risk path
                                                     (STRW-005) with no competing signal anywhere
  APP-1012  co-borrower on 5 applications with 4  -> straw POSSIBLE / HIGH (STRW-017, STRW-018):
            unrelated primaries at one dealer        the pattern family relationships do NOT explain
  APP-1013  employer unverifiable AND clustered   -> employment POSSIBLE (EMP-019), the other side
            AND velocity-linked                      of APP-1006's data-gap coin
  APP-1014  three genuinely independent weak      -> the MEDIUM band reached by corroboration
            concerns across three domains            across domains rather than by one loud domain
"""

from __future__ import annotations

from typing import Final

from fixtures._baseline import application, context, signals
from fixtures._build import Alert, Scenario, expected_block

_IDEN_LOW: Final = (
    "IDEN-015",
    "LOW RISK: Indicators are isolated, explainable (shared household, formatting, population "
    "density, data quality), or insufficiently corroborated. LOW should be the most common "
    "outcome.",
)
_INC_LOW: Final = (
    "INC-016",
    "LOW RISK: Indicators are isolated, explainable (career progression, commission, overtime, "
    "self-employment, seasonal work, reporting variance, data quality), or insufficiently "
    "corroborated. LOW should be the most common outcome.",
)
_SYN_LOW: Final = (
    "SYN-017",
    "LOW RISK: Indicators are isolated, explainable (thin history from youth/immigration, "
    "authorized user credit building, shared household, population density), or insufficiently "
    "corroborated. LOW should be the most common outcome.",
)
_BST_LOW: Final = (
    "BST-021",
    "LOW RISK: suspicious indicators or alert activity may exist but are weak, isolated, "
    "explainable, operationally normal, contradicted by context, caused by data quality issues, "
    "or insufficiently corroborated to support material fraud concern.",
)
_RNG_LOW: Final = (
    "RNG-020",
    "LOW RISK: suspicious indicators or alert activity may exist but are weak, isolated, "
    "explainable, operationally normal, contradicted by context, caused by data quality issues, "
    "or insufficiently corroborated to support material fraud concern.",
)
_DLR_LOW: Final = (
    "DLR-020",
    "LOW RISK: Dealer signals reflect normal operational volume, portfolio risk metrics without "
    "fraud coordination evidence, or isolated borrower-level concerns. LOW should be the most "
    "common outcome.",
)
_EMP_LOW: Final = (
    "EMP-018",
    "LOW RISK: Employer is verifiable or explainable, employment patterns are consistent, signals "
    "are isolated or explained by employer scale/industry. Missing fields alone = LOW. LOW should "
    "be the most common outcome.",
)
_STRW_NO_COBORROWER: Final = (
    "STRW-008",
    "No co-borrower present: Primary straw mechanism absent. Only evaluate if other patterns "
    "(role-switching history, dealer clustering) exist.",
)
_MSTR_LOW: Final = (
    "MSTR-026",
    "LOW RISK: suspicious indicators may exist but are isolated, explainable, operationally "
    "normal, weakly supported, or insufficiently corroborated.",
)
_MSTR_MEDIUM: Final = (
    "MSTR-027",
    "MEDIUM RISK: unresolved fraud concerns supported by moderate corroborating evidence or "
    "meaningful inconsistencies requiring additional verification.",
)

APP_1011: Final = Scenario(
    app_id="APP-1011",
    scenario="No co-borrower at all; every other signal at baseline",
    why=(
        "The straw prompt's minimal-risk branch with nothing to distract it. STRW-005 caps the "
        "analysis at 2-3 sentences when no co-borrower exists, and STRW-008 says the primary "
        "mechanism is absent. APP-1003 also has no co-borrower but carries an income concern that "
        "a straw analysis could be tempted to borrow; here there is nothing else to say, so a "
        "specialist that pads its answer has nowhere to pad from."
    ),
    application=application(
        hash_lender_id="hash_lndr_e1a8",
        lender_name="SUMMIT_CU",
        borrower_name="Owen Bishop",
        hash_ssn="hash_ssn_7b2d44",
        borrower_age=49,
        credit_score=742,
        fraud_score=96,
        income_misrep_score=5,
        loan_amount=19900,
        stated_annual_income=83000,
        verified_annual_income=82100,
        vehicle="2019 Ford Escape",
        ltv_pct=71,
        dealer_name="Cedar Hollow Auto",
        ppi_dealer_id="PPI-DLR-3308",
        consortium_number_of_apps_last_1_year=690,
        employer_name="Fairview Regional Clinics",
        occupation="FACILITIES SUPERVISOR",
        application_date="2026-06-15",
        submitted_to_prod_at="2026-06-15T08:22:00Z",
    ),
    signals=signals(
        ssn_days_in_system=3200,
        ssn_income_overstatement_percentage=1,
        loan_to_annual_income_ratio=0.24,
        ssn_distinct_income_amounts_all_time=3,
        ssn_previous_apps_all_time=4,
        ssn_distinct_employers_all_time=3,
    ),
    context=context(
        ssn_days_in_system=(
            "First consortium sighting 3,200 days ago; 4 prior applications over 9 years, "
            "consistent name, DOB and address key throughout."
        ),
        ssn_income_overstatement_percentage=(
            "Stated 83,000 vs verified 82,100 = 1% overstatement."
        ),
        loan_to_annual_income_ratio="Loan 19,900 against stated annual income 83,000 = 0.24.",
        ssn_distinct_income_amounts_all_time=(
            "3 stated amounts across 9 years: 68,000, 76,000 and 83,000 - steady progression with "
            "one employer change 6 years ago."
        ),
        ssn_previous_apps_all_time=(
            "4 prior applications over 9 years, all funded and paid or current. No EPD, no 999."
        ),
        ssn_distinct_employers_all_time=(
            "3 employers all-time over 9 years; the current one for 6 years."
        ),
        has_coborrower=(
            "No co-borrower on this application. All co-borrower source tables were checked in "
            "precedence order and none returned a row for this (application_id, hash_lender_id), "
            "so the absence is confirmed rather than assumed from a single missing table."
        ),
        coborrower_appears_as_borrower_count=(
            "No co-borrower on this application, so 0 co-borrower role records exist to compare "
            "across its 4 prior applications. This is the absence of a party, not a known party "
            "with 0 role switches."
        ),
        borrower_appears_as_coborrower_count=(
            "0 of the 4 prior applications for this hash_ssn carry it as a co-borrower, and 0 "
            "other consortium applications do either."
        ),
        coborr_distinct_primary_borrowers_count=(
            "No co-borrower on this application, so 0 primary borrowers are linked through one."
        ),
        credit_score_gap=(
            "No co-borrower, so 0 second credit scores exist to compare against the borrower's 742."
        ),
        coborr_income_70_percent_plus=(
            "No co-borrower income on file, so the co-borrower share of the 83,000 combined stated "
            "income is 0%."
        ),
        coborr_3_plus_apps_same_dealer_flag=(
            "No co-borrower, so 0 co-borrower applications exist at this or any other dealer."
        ),
        high_value_vehicle_weak_profile_straw_risk=(
            "19,900 loan on a 7-year-old compact SUV at 71% LTV against a 742 score and 9 years of "
            "consortium history. Collateral is modest relative to the profile."
        ),
    ),
    alerts=(),
    expected=expected_block(
        overall="LOW RISK",
        recommendation="APPROVE",
        bands=dict(
            identity="LOW RISK",
            dealer="LOW RISK",
            straw="LOW RISK",
            employment="LOW RISK",
            income="LOW RISK",
            synthetic="LOW RISK",
            bustout="LOW RISK",
            rings="LOW RISK",
        ),
        flags=dict(
            identity=0, dealer=0, straw=0, employment=0, income=0, synthetic=0, bustout=0, rings=0
        ),
        justifications=dict(
            identity=_IDEN_LOW,
            dealer=_DLR_LOW,
            straw=(
                "STRW-005",
                "If no co-borrower exists, straw risk is inherently minimal — keep analysis to 2-3 "
                "sentences.",
            ),
            employment=_EMP_LOW,
            income=_INC_LOW,
            synthetic=_SYN_LOW,
            bustout=_BST_LOW,
            rings=_RNG_LOW,
        ),
        overall_justification=_MSTR_LOW,
        stipulations_expected=False,
        top_risk_factors_expected=False,
    ),
    notes=(
        "alerts_fired is deliberately EMPTY. Every other fixture has at least one alert, so this "
        "is "
        "the only record that proves the payload builder and the specialists handle a zero-length "
        "alerts_fired array rather than assuming at least one element.",
        "The straw _context rows here distinguish 'no co-borrower exists' from 'a co-borrower "
        "exists with a count of zero'. Both render as 0 in the signal, and only the context tells "
        "the agent which it is - which is precisely what SIGBOUND-04 requires context to do.",
    ),
)

APP_1012: Final = Scenario(
    app_id="APP-1012",
    scenario="Co-borrower on 5 applications with 4 unrelated primaries at one dealer",
    why=(
        "The straw pattern family relationships cannot explain (STRW-017, STRW-018): a co-borrower "
        "reused across unrelated primary borrowers, clustered at one dealer, with role switching "
        "and a 178-point credit gap. Every clause STRW-015 requires before overriding the benign "
        "family reading is present, and the relationship type is non-family."
    ),
    application=application(
        hash_lender_id="hash_lndr_f3d7",
        lender_name="PRIME",
        borrower_name="Trevor Lang",
        hash_ssn="hash_ssn_2f9e61",
        borrower_age=23,
        credit_score=548,
        fraud_score=734,
        income_misrep_score=44,
        loan_amount=52700,
        stated_annual_income=34000,
        verified_annual_income=31900,
        vehicle="2024 Cadillac Escalade",
        ltv_pct=108,
        dealer_name="Crown Prestige Motors",
        ppi_dealer_id="PPI-DLR-0388",
        consortium_number_of_apps_last_1_year=560,
        employer_name="Rapid Delivery Couriers",
        employer_street_addr="19 Foundry St",
        employer_city="Newark",
        employer_state="NJ",
        employer_zip="07102",
        employer_phone="973-555-0147",
        occupation="DELIVERY DRIVER",
        application_date="2026-06-24",
        submitted_to_prod_at="2026-06-24T19:31:00Z",
        coborrower_name="Bernard Achebe",
        coborrower_relationship_type="other/non-family",
    ),
    signals=signals(
        phone_reuse_distinct_ssn_count=3,
        email_reuse_distinct_ssn_count=1,
        address_reuse_distinct_ssn_count=2,
        ssn_days_in_system=140,
        inquiries_in_2weeks=5,
        dealer_consortium_score=680,
        dealer_risk_score=650,
        dealer_lender_default_rate=0.09,
        dealer_consortium_epd_rate_last_24mo=0.08,
        phone_reuse_count_this_dealer=3,
        employer_concentration_pct=12.0,
        ssn_previous_apps_all_time=1,
        ssn_previous_apps_90d=1,
        dealer_app_volume_24mo=1180,
        dealer_apps_last_7d=10,
        has_coborrower=True,
        coborrower_appears_as_borrower_count=2,
        borrower_appears_as_coborrower_count=1,
        coborr_distinct_primary_borrowers_count=4,
        credit_score_gap=178,
        coborr_income_70_percent_plus=1,
        coborr_3_plus_apps_same_dealer_flag=1,
        high_value_vehicle_weak_profile_straw_risk=1,
        employer_distinct_other_ssns_count=210,
        employer_other_ssns_count=210,
        employer_phone_distinct_ssn_count=210,
        employer_new_ssns_90d=28,
        rapid_employer_change_flag=1,
        ssn_distinct_employers_all_time=2,
        ssn_distinct_employers_60d=2,
        ssn_income_overstatement_percentage=7,
        loan_to_annual_income_ratio=1.55,
        is_thin_file=1,
        ssn_apps_last_7d=2,
        ssn_apps_last_30d=2,
        ssn_loan_exceeds_annual_income_unsustainable_debt=1,
        ssn_loan_80_percent_or_more_annual_income_high_ratio=1,
    ),
    context=context(
        phone_reuse_distinct_ssn_count=(
            "Normalized phone matches 3 hash_ssn values with 3 different surnames: this borrower, "
            "the co-borrower, and one of the co-borrower's other primary borrowers. No shared "
            "address among them."
        ),
        address_reuse_distinct_ssn_count=(
            "Address key resolves to 2 hash_ssn values with different surnames; the second is one "
            "of the co-borrower's other primaries, who listed this address 5 weeks ago."
        ),
        ssn_days_in_system=(
            "First consortium sighting 140 days ago; 1 prior application 38 days ago, at this same "
            "dealer, also with this same co-borrower."
        ),
        inquiries_in_2weeks="5 inquiries in the trailing 14 days. Credit score 548.",
        dealer_consortium_score=(
            "Dealer scored 680 this month; 640-690 across the trailing 12 months. Moderate band."
        ),
        dealer_risk_score="Lender-portfolio score 650 across 96 funded loans.",
        dealer_lender_default_rate=(
            "9 defaults across 96 funded loans for this dealer with this lender."
        ),
        dealer_consortium_epd_rate_last_24mo=(
            "94 early payment defaults across 1,180 consortium applications in 24 months."
        ),
        phone_reuse_count_this_dealer=(
            "3 other borrowers at this dealer share this normalized phone. They carry 3 different "
            "surnames and 3 different addresses, and 2 of the 3 also name this same co-borrower."
        ),
        employer_concentration_pct=(
            "Rapid Delivery Couriers accounts for 142 of 1,180 applications at this dealer (12%). "
            "The employer is a gig courier platform, whose drivers are geographically dispersed - "
            "the dealer is not adjacent to a single worksite."
        ),
        ssn_previous_apps_all_time=(
            "1 prior application on this hash_ssn, 38 days ago, at this dealer with this same "
            "co-borrower, for a 48,000 vehicle. Withdrawn before funding."
        ),
        ssn_previous_apps_90d="1 prior application in the trailing 90 days, at this same dealer.",
        dealer_app_volume_24mo=(
            "1,180 consortium applications in 24 months; consortium_number_of_apps_last_1_year is "
            "560, so the dealer is active."
        ),
        dealer_apps_last_7d=(
            "10 applications at this dealer in the last 7 days: 10 distinct hash_ssn values but "
            "only 7 distinct phones, and 3 name the same co-borrower."
        ),
        has_coborrower=(
            "Co-borrower present. Source precedence resolved the row from the primary co-borrower "
            "table. The co-borrower shares NO surname and NO address key with the borrower; the "
            "relationship type on file is other/non-family."
        ),
        coborrower_appears_as_borrower_count=(
            "The co-borrower has been the PRIMARY borrower on 2 other consortium applications, "
            "both at this same dealer, 4 and 7 months ago, each with a different co-borrower."
        ),
        borrower_appears_as_coborrower_count=(
            "This hash_ssn appeared as a co-borrower once, 61 days ago, on an application whose "
            "primary borrower is one of this co-borrower's other 4 primaries."
        ),
        coborr_distinct_primary_borrowers_count=(
            "The co-borrower is linked to 4 distinct primary borrowers across 5 applications. The "
            "4 primaries carry 4 different surnames and 4 different addresses, and all 5 "
            "applications were filed at THIS dealer within 8 months."
        ),
        credit_score_gap=(
            "Borrower 548, co-borrower 726. A 178-point gap, well above the 100-point piggybacking "
            "threshold. The same 726 co-borrower score carried the other 4 applications."
        ),
        coborr_income_70_percent_plus=(
            "Co-borrower contributes 76% of combined stated income (108,000 of 142,000). The same "
            "co-borrower income figure appears on all 5 applications, and the parties are "
            "unrelated."
        ),
        coborr_3_plus_apps_same_dealer_flag=(
            "The co-borrower appears on 5 applications at this dealer in 8 months, with 4 "
            "different primary borrowers."
        ),
        high_value_vehicle_weak_profile_straw_risk=(
            "52,700 loan on a 2024 Cadillac Escalade at 108% LTV against a 548 score, a 34,000 "
            "stated income and a 140-day consortium history. The co-borrower's credit and income "
            "are what qualify the application."
        ),
        employer_distinct_other_ssns_count=(
            "210 distinct hash_ssn values cite Rapid Delivery Couriers over 3 years. It is a gig "
            "courier platform with a verifiable address and listed line - high counts are expected "
            "for gig platforms."
        ),
        employer_other_ssns_count=(
            "210 other hash_ssn values share this employer, across 64 dealers and 6 states. 4 of "
            "the 210 applied at this dealer with this same co-borrower."
        ),
        employer_phone_distinct_ssn_count=(
            "210 hash_ssn values share 973-555-0147, the platform's published support line."
        ),
        employer_new_ssns_90d=(
            "28 new hash_ssn values cited this employer in the last 90 days against 210 all-time - "
            "normal gig-platform churn; prior quarters added 24, 31 and 26."
        ),
        rapid_employer_change_flag=(
            "2 employers cited across 60 days, both gig delivery platforms. Gig platform switching "
            "is one of the benign explanations the employment prompt names."
        ),
        ssn_distinct_employers_all_time=(
            "2 employers all-time over 140 days, both gig courier platforms."
        ),
        ssn_distinct_employers_60d="2 employers cited in the trailing 60 days, both gig platforms.",
        ssn_income_overstatement_percentage=(
            "Stated 34,000 vs verified 31,900 = 7% overstatement, inside normal reporting variance."
        ),
        loan_to_annual_income_ratio=(
            "Loan 52,700 against the borrower's stated annual income 34,000 = 1.55, above the 1.0 "
            "scrutiny line. Against combined stated income of 142,000 it is 0.37."
        ),
        is_thin_file=(
            "2 open tradelines, time in file 9 months. Borrower is 23; a short file is age-"
            "appropriate. Credit report present and valid."
        ),
        ssn_apps_last_7d=(
            "2 applications in the trailing 7 days, both at this dealer, both naming this same "
            "co-borrower."
        ),
        ssn_apps_last_30d="2 applications in the trailing 30 days, both at this dealer.",
        ssn_loan_exceeds_annual_income_unsustainable_debt=(
            "Loan 52,700 exceeds the borrower's stated annual income of 34,000 by 55%. Existing "
            "monthly obligations 210."
        ),
        ssn_loan_80_percent_or_more_annual_income_high_ratio=(
            "Loan 52,700 is 155% of the borrower's stated annual income."
        ),
    ),
    alerts=(
        Alert(
            alert_id=21,
            finding_text=(
                "Co-borrower on this application appears as a co-borrower on several other "
                "applications with different, unrelated primary borrowers."
            ),
            underlying_behavior="co-borrower reuse across unrelated primaries",
        ),
        Alert(
            alert_id=22,
            finding_text=(
                "Co-borrower has previously been the primary borrower on other applications at "
                "this same dealership."
            ),
            underlying_behavior="co-borrower role switching",
        ),
        Alert(
            alert_id=24,
            finding_text=(
                "Credit score gap between borrower and co-borrower exceeds 170 points, and the "
                "co-borrower supplies more than three quarters of stated income."
            ),
            underlying_behavior="co-borrower carries the credit and income",
        ),
        Alert(
            alert_id=26,
            finding_text=(
                "The same co-borrower appears on five applications at one dealership within eight "
                "months."
            ),
            underlying_behavior="co-borrower dealer clustering",
        ),
        Alert(
            alert_id=161,
            finding_text=(
                "Vehicle value and loan amount are disproportionate to the primary borrower's "
                "documented income and credit profile."
            ),
            underlying_behavior="collateral disproportionate to primary profile",
        ),
        Alert(
            alert_id=58,
            finding_text=(
                "Borrower phone number is shared with unrelated applicant identities carrying "
                "different surnames and addresses."
            ),
            underlying_behavior="phone shared across unrelated identities",
        ),
    ),
    expected=expected_block(
        overall="HIGH RISK",
        recommendation="DECLINE",
        bands=dict(
            identity="POSSIBLE RISK",
            dealer="POSSIBLE RISK",
            straw="HIGH RISK",
            employment="LOW RISK",
            income="LOW RISK",
            synthetic="LOW RISK",
            bustout="POSSIBLE RISK",
            rings="POSSIBLE RISK",
        ),
        flags=dict(
            identity=0, dealer=0, straw=1, employment=0, income=0, synthetic=0, bustout=0, rings=0
        ),
        justifications=dict(
            identity=(
                "IDEN-016",
                "POSSIBLE RISK: Materially suspicious patterns from genuinely independent sources "
                "requiring verification.",
            ),
            dealer=(
                "DLR-021",
                "POSSIBLE RISK: Meaningful evidence of dealer-level coordination (phone reuse + "
                "employer clustering + identity patterns) that cannot be explained by volume or "
                "geography.",
            ),
            straw=(
                "STRW-018",
                "HIGH RISK: Coordinated straw activity with role-switching, non-family co-borrower "
                "reuse, dealer clustering, and meaningful profile mismatches. Uncommon.",
            ),
            employment=(
                "EMP-017",
                "Large national employers, military, government, staffing agencies, and gig "
                "platforms naturally have high SSN counts — this is NOT fraud signal.",
            ),
            income=_INC_LOW,
            synthetic=(
                "SYN-013",
                "is_thin_file + very_new_file_high_score: Legitimate for young adults and new "
                "immigrants. Concerning only with multiple corroborating patterns.",
            ),
            bustout=(
                "BST-020",
                "POSSIBLE RISK: materially suspicious patterns supported by moderate corroborating "
                "evidence requiring verification before LOW RISK confidence can reasonably be "
                "established.",
            ),
            rings=(
                "RNG-019",
                "POSSIBLE RISK: materially suspicious patterns supported by moderate corroborating "
                "evidence requiring verification before LOW RISK confidence can reasonably be "
                "established.",
            ),
        ),
        overall_justification=(
            "MSTR-028",
            "HIGH RISK: clear, material, corroborated fraud evidence that is difficult to "
            "reasonably explain through benign or operational factors.",
        ),
        stipulations_expected=False,
        top_risk_factors_expected=True,
    ),
    notes=(
        "straw_flag is the only flag set to 1. The identity, dealer, bustout and rings bands are "
        "POSSIBLE because they see genuine spillover from the straw pattern, but MSTR-022 warns "
        "that overlapping references are not automatic independent corroboration - the phone "
        "sharing, the dealer clustering and the loan-to-income ratio are all the SAME straw "
        "arrangement viewed from four angles, so only straw earns a flag.",
        "employment must stay LOW: 210 SSNs and rapid employer change both have gig-platform "
        "explanations, and EMP-017 names gig platforms explicitly. This is the record that checks "
        "the employment domain does not get dragged up by a HIGH overall outcome.",
        "STRW-015's override requires role-switching AND dealer clustering AND income "
        "disproportion combined. All three are present, and the relationship is non-family, so "
        "STRW-018's HIGH is reachable - which the customer notes is 'Uncommon', and this is the "
        "only straw-HIGH record in the set.",
    ),
)

APP_1013: Final = Scenario(
    app_id="APP-1013",
    scenario="Employer unverifiable AND SSN-clustered AND velocity-linked: employment POSSIBLE",
    why=(
        "The other side of APP-1006's coin. EMP-019 defines POSSIBLE as an employer that 'appears "
        "potentially fabricated (no verifiable presence + coordinated SSN clustering + abnormal "
        "velocity)' - three conjuncts. APP-1006 has the first alone and must be LOW; this record "
        "has all three and must be POSSIBLE. Without both records the rule is only half-tested."
    ),
    application=application(
        hash_lender_id="hash_lndr_a4f2",
        lender_name="SUBPRIME",
        borrower_name="Lena Ostrowski",
        hash_ssn="hash_ssn_5c8b17",
        borrower_age=34,
        credit_score=634,
        fraud_score=688,
        income_misrep_score=39,
        loan_amount=33500,
        stated_annual_income=67000,
        verified_annual_income=48200,
        vehicle="2022 Ram 1500",
        ltv_pct=99,
        dealer_name="Junction Point Auto",
        ppi_dealer_id="PPI-DLR-0559",
        consortium_number_of_apps_last_1_year=470,
        employer_name="Meridian Consulting Partners",
        employer_street_addr="4400 Enterprise Ct Ste 900",
        employer_city="Atlanta",
        employer_state="GA",
        employer_zip="30303",
        employer_phone="404-555-0182",
        occupation="PROJECT COORDINATOR",
        application_date="2026-06-25",
        submitted_to_prod_at="2026-06-25T13:58:00Z",
    ),
    signals=signals(
        phone_reuse_distinct_ssn_count=2,
        ssn_days_in_system=760,
        inquiries_in_2weeks=4,
        dealer_consortium_score=610,
        dealer_risk_score=580,
        phone_reuse_count_this_dealer=1,
        employer_concentration_pct=17.0,
        ssn_previous_apps_all_time=3,
        ssn_previous_apps_90d=2,
        dealer_app_volume_24mo=980,
        dealer_apps_last_7d=9,
        employer_distinct_other_ssns_count=14,
        employer_other_ssns_count=14,
        employer_phone_distinct_ssn_count=14,
        employer_new_ssns_90d=11,
        employer_spike_flag=1,
        rapid_employer_change_flag=1,
        ssn_distinct_employers_all_time=4,
        ssn_distinct_employers_30d=1,
        ssn_distinct_employers_60d=2,
        ssn_distinct_income_amounts_all_time=3,
        ssn_income_increase_20_percent_or_more=1,
        ssn_stated_income_exceeds_verified_by_25_percent=1,
        ssn_income_overstatement_percentage=39,
        loan_to_annual_income_ratio=0.50,
        ssn_income_high_variance_inconsistent_reporting=1,
        ssn_apps_last_7d=2,
        ssn_apps_last_30d=3,
        rapid_app_velocity=1,
    ),
    context=context(
        phone_reuse_distinct_ssn_count=(
            "Normalized phone matches 2 hash_ssn values: this borrower and one other, who cites "
            "the same employer but lives 90 miles away and shares no surname."
        ),
        ssn_days_in_system=(
            "First consortium sighting 760 days ago; 3 prior applications, consistent name, DOB "
            "and address key."
        ),
        inquiries_in_2weeks="4 inquiries in the trailing 14 days. Credit score 634.",
        dealer_consortium_score=(
            "Dealer scored 610 this month; 590-625 across the trailing 12 months. Moderate band."
        ),
        dealer_risk_score="Lender-portfolio score 580 across 74 funded loans.",
        phone_reuse_count_this_dealer=(
            "1 other borrower at this dealer shares this normalized phone - the same unrelated "
            "co-worker identity described above."
        ),
        employer_concentration_pct=(
            "Meridian Consulting Partners accounts for 167 of 980 applications at this dealer "
            "(17%). The employer has only 14 SSNs consortium-wide, so the concentration is not a "
            "large-employer effect - 12 of its 14 SSNs applied at THIS dealer."
        ),
        ssn_previous_apps_all_time=(
            "3 prior applications on this hash_ssn over 25 months. No EPD, no 999. The 2 most "
            "recent are inside the last 30 days."
        ),
        ssn_previous_apps_90d=(
            "2 prior applications in the trailing 90 days, both inside the last 26 days, at 2 "
            "different dealers, stating 58,000 and 67,000."
        ),
        dealer_app_volume_24mo=(
            "980 consortium applications in 24 months; consortium_number_of_apps_last_1_year is "
            "470, so the dealer is active."
        ),
        employer_distinct_other_ssns_count=(
            "14 distinct hash_ssn values cite Meridian Consulting Partners. 12 of the 14 applied "
            "at THIS dealer, and 11 first appeared in the last 90 days. The suite address is a "
            "virtual-office registration shared by 60 entities, there is no state business "
            "registration, and the listed number is a mobile line."
        ),
        employer_other_ssns_count=(
            "14 other hash_ssn values share this employer; 12 of them at this one dealer, and 3 "
            "share a phone number with each other."
        ),
        employer_phone_distinct_ssn_count=(
            "14 hash_ssn values share 404-555-0182. The number is a mobile line and appears as the "
            "personal contact number on 2 of those applications."
        ),
        employer_new_ssns_90d=(
            "11 new hash_ssn values cited this employer in the last 90 days against 14 all-time. "
            "The employer was cited twice in its entire prior history."
        ),
        employer_spike_flag=(
            "Monthly applications citing this employer: 0, 0, 1, 3, 4, 4 over the last 6 months, "
            "from a base of zero. No hiring announcement, no verifiable presence."
        ),
        rapid_employer_change_flag=(
            "2 employers cited across 60 days. Neither is a staffing agency, gig platform, "
            "seasonal employer or contract placement."
        ),
        ssn_distinct_employers_all_time=(
            "4 employers all-time over 25 months; the 2 earliest were verifiable firms, the 2 "
            "recent ones are not."
        ),
        ssn_distinct_employers_30d="1 employer cited in the trailing 30 days.",
        ssn_distinct_employers_60d="2 employers cited in the trailing 60 days.",
        ssn_distinct_income_amounts_all_time=(
            "3 stated amounts: 44,000 (25 months ago), 58,000 (26 days ago), 67,000 (this "
            "application)."
        ),
        ssn_income_increase_20_percent_or_more=(
            "Stated income rose 58,000 to 67,000 (+16%) in 26 days, and 44,000 to 67,000 (+52%) "
            "over 25 months, spanning the two unverifiable employers."
        ),
        ssn_stated_income_exceeds_verified_by_25_percent=(
            "Stated 67,000 against verified 48,200 - a 39% gap. Verification could not reach the "
            "stated employer; the figure is from a prior employer's records."
        ),
        ssn_income_overstatement_percentage=(
            "Stated 67,000 vs verified 48,200 = 39% overstatement, above the 25% materially-"
            "suspicious line."
        ),
        loan_to_annual_income_ratio="Loan 33,500 against stated annual income 67,000 = 0.50.",
        ssn_income_high_variance_inconsistent_reporting=(
            "Three stated amounts, coefficient of variation 0.19. Filing type is W-2, not "
            "self-employed, commission, seasonal or gig - so the variance has no occupational "
            "explanation on file."
        ),
        ssn_apps_last_7d="2 applications in the trailing 7 days, at 2 different dealers.",
        ssn_apps_last_30d=(
            "3 applications in the trailing 30 days, at 3 dealers, with 2 different stated incomes."
        ),
        rapid_app_velocity=(
            "3 applications in 30 days at 3 dealers, all citing the same unverifiable employer."
        ),
    ),
    alerts=(
        Alert(
            alert_id=140,
            finding_text=(
                "Stated employer has no verifiable business presence: the suite address is a "
                "virtual-office registration and no state business registration exists."
            ),
            underlying_behavior="unverifiable employer",
        ),
        Alert(
            alert_id=142,
            finding_text=(
                "Employer added eleven new applicant SSNs in the last ninety days against fourteen "
                "all-time, from a base of two."
            ),
            underlying_behavior="employer SSN clustering",
        ),
        Alert(
            alert_id=144,
            finding_text=(
                "Twelve of the fourteen applicants citing this employer applied at the same "
                "dealership."
            ),
            underlying_behavior="employer SSN clustering",
        ),
        Alert(
            alert_id=27,
            finding_text=(
                "Stated income exceeds the verified figure by 39%; verification could not reach "
                "the stated employer."
            ),
            underlying_behavior="income overstatement against an unreachable employer",
        ),
        Alert(
            alert_id=76,
            finding_text=(
                "Employer telephone number is a mobile line that also appears as an applicant's "
                "personal contact number."
            ),
            underlying_behavior="unverifiable employer",
        ),
    ),
    expected=expected_block(
        overall="MEDIUM RISK",
        recommendation="REVIEW AND APPLY STIPULATIONS",
        bands=dict(
            identity="LOW RISK",
            dealer="POSSIBLE RISK",
            straw="LOW RISK",
            employment="POSSIBLE RISK",
            income="POSSIBLE RISK",
            synthetic="LOW RISK",
            bustout="LOW RISK",
            rings="POSSIBLE RISK",
        ),
        flags=dict(
            identity=0, dealer=0, straw=0, employment=1, income=0, synthetic=0, bustout=0, rings=0
        ),
        justifications=dict(
            identity=_IDEN_LOW,
            dealer=(
                "DLR-015",
                "employer_concentration_pct: High concentration of same employer at a dealer. "
                "Normal for dealers near large employers/bases. >20% at small dealers with few "
                "apps is more concerning.",
            ),
            straw=_STRW_NO_COBORROWER,
            employment=(
                "EMP-019",
                "POSSIBLE RISK: Employer appears potentially fabricated (no verifiable presence + "
                "coordinated SSN clustering + abnormal velocity) OR employment history shows "
                "patterns inconsistent with stated occupation requiring verification.",
            ),
            income=(
                "INC-012",
                "ssn_income_overstatement_percentage: <15% is normal reporting variance. 15-25% "
                "requires context. >25% is materially suspicious.",
            ),
            synthetic=_SYN_LOW,
            bustout=(
                "BST-010",
                "Evaluate whether borrowing behavior materially exceeds normal underwriting "
                "variation after considering legitimate credit shopping, refinancing, major "
                "purchases, relocation, business use, seasonal demand, and benign application "
                "activity.",
            ),
            rings=(
                "RNG-012",
                "Shared phone numbers, addresses, employers, or emails are more significant when "
                "combined with meaningful identity inconsistencies, coordinated dealership "
                "activity, abnormal application velocity, conflicting borrower information, or "
                "repeated exact-unit overlap.",
            ),
        ),
        overall_justification=_MSTR_MEDIUM,
        stipulations_expected=True,
        top_risk_factors_expected=True,
    ),
    notes=(
        "Compare against APP-1006 deliberately: there the employer record is emptier than this one "
        "and the answer is LOW, because EMP-019's other two conjuncts (SSN clustering, abnormal "
        "velocity) are absent. Sparse data is not the signal; sparse data plus clustering plus "
        "velocity is.",
        "employment_flag is 1 while income_flag is 0 even though both bands are POSSIBLE. The "
        "employer's fabrication indicators are independent of each other; the 39% overstatement is "
        "a consequence of the same unreachable employer, so MSTR-022 forbids counting it as "
        "separate corroboration.",
        "The outcome is MEDIUM rather than HIGH because MSTR-029 reserves HIGH for multiple "
        "strongly corroborated patterns, and there is no identity manipulation, no negative-file "
        "hit and no confirmed prior fraud here.",
    ),
)

APP_1014: Final = Scenario(
    app_id="APP-1014",
    scenario="Three weak but genuinely independent concerns across three domains",
    why=(
        "MSTR-021 and MSTR-022 in tension: the master must weigh 'the totality of corroborated "
        "evidence' while refusing to treat overlapping references as automatic corroboration. Here "
        "three concerns arise from three genuinely separate underlying facts - an unexplained "
        "third-party email, a 22% income overstatement, and 3 states in 90 days - none sufficient "
        "alone. Every specialist reports POSSIBLE at most, and the master must reach MEDIUM "
        "without any single domain being loud."
    ),
    application=application(
        hash_lender_id="hash_lndr_b6c8",
        lender_name="HARBOR_BANK",
        borrower_name="Callum Reyes",
        hash_ssn="hash_ssn_9e4a26",
        borrower_age=39,
        credit_score=668,
        fraud_score=612,
        income_misrep_score=33,
        loan_amount=35900,
        stated_annual_income=79000,
        verified_annual_income=64800,
        vehicle="2022 Honda Pilot",
        ltv_pct=95,
        dealer_name="Northgate Family Auto",
        ppi_dealer_id="PPI-DLR-0631",
        consortium_number_of_apps_last_1_year=350,
        employer_name="Ridgeway Building Systems",
        employer_street_addr="1200 Trade Center Dr",
        employer_city="Denver",
        employer_state="CO",
        employer_zip="80216",
        employer_phone="303-555-0173",
        occupation="SITE SUPERINTENDENT",
        application_date="2026-06-23",
        submitted_to_prod_at="2026-06-23T15:47:00Z",
    ),
    signals=signals(
        email_reuse_distinct_ssn_count=3,
        ssn_days_in_system=1050,
        inquiries_in_2weeks=3,
        dealer_consortium_score=400,
        dealer_risk_score=370,
        ssn_previous_apps_all_time=3,
        ssn_previous_apps_90d=2,
        dealer_app_volume_24mo=720,
        dealer_apps_last_7d=5,
        employer_distinct_other_ssns_count=96,
        employer_other_ssns_count=96,
        employer_phone_distinct_ssn_count=96,
        employer_new_ssns_90d=7,
        ssn_distinct_employers_all_time=3,
        ssn_distinct_income_amounts_all_time=3,
        ssn_stated_income_exceeds_verified_by_25_percent=0,
        ssn_income_overstatement_percentage=22,
        loan_to_annual_income_ratio=0.45,
        ssn_address_location_variations=3,
        ssn_apps_last_30d=2,
    ),
    context=context(
        email_reuse_distinct_ssn_count=(
            "Normalized email matches 3 hash_ssn values with 3 different surnames and 3 different "
            "addresses. Two of the three first appeared in the last 60 days. No family "
            "relationship, no shared household, and no data-entry explanation is on file - this "
            "one has no benign account."
        ),
        ssn_days_in_system=(
            "First consortium sighting 1,050 days ago; 3 prior applications, consistent name and "
            "DOB, but 3 different address states."
        ),
        inquiries_in_2weeks="3 inquiries in the trailing 14 days. Credit score 668.",
        dealer_consortium_score=(
            "Dealer scored 400 this month; 385-420 across the trailing 12 months. Low band."
        ),
        dealer_risk_score="Lender-portfolio score 370 across 41 funded loans.",
        ssn_previous_apps_all_time=(
            "3 prior applications on this hash_ssn over 34 months, at 3 dealers in 3 states. No "
            "EPD, no 999."
        ),
        ssn_previous_apps_90d=(
            "2 prior applications in the trailing 90 days, 41 and 78 days ago, in 2 different "
            "states."
        ),
        dealer_app_volume_24mo=(
            "720 consortium applications in 24 months; consortium_number_of_apps_last_1_year is "
            "350, so the dealer is active."
        ),
        dealer_apps_last_7d=(
            "5 applications at this dealer in the last 7 days: 5 distinct hash_ssn values, 5 "
            "distinct phones, 5 distinct addresses, 5 distinct employers."
        ),
        employer_distinct_other_ssns_count=(
            "96 distinct hash_ssn values cite Ridgeway Building Systems over 5 years. Verifiable "
            "address, city, state, ZIP and listed line; a regional commercial builder with project "
            "sites in several states."
        ),
        employer_other_ssns_count=(
            "96 other hash_ssn values share this employer, across 38 dealers and 5 states - the "
            "spread of a multi-state construction firm, with no clustering."
        ),
        employer_phone_distinct_ssn_count=(
            "96 hash_ssn values share 303-555-0173, the firm's published main line."
        ),
        employer_new_ssns_90d=(
            "7 new hash_ssn values cited this employer in the last 90 days against 96 all-time."
        ),
        ssn_distinct_employers_all_time=(
            "3 employers all-time over 34 months; Ridgeway for the last 2 years, across the 3 "
            "state moves - the employer did NOT change when the address did."
        ),
        ssn_distinct_income_amounts_all_time=(
            "3 stated amounts: 71,000 (34 months ago), 74,000 (78 days ago), 79,000 (this "
            "application)."
        ),
        ssn_stated_income_exceeds_verified_by_25_percent=(
            "Stated 79,000 against verified 64,800 - a 22% gap, below the 25% flag threshold, so "
            "this flag does not fire."
        ),
        ssn_income_overstatement_percentage=(
            "Stated 79,000 vs verified 64,800 = 22% overstatement - inside the 15-25% band the "
            "customer says 'requires context'. Verified figure excludes per-diem and completion "
            "bonuses that the borrower's stated figure includes; the employer confirmed the base "
            "salary but not the bonus history."
        ),
        loan_to_annual_income_ratio="Loan 35,900 against stated annual income 79,000 = 0.45.",
        ssn_address_location_variations=(
            "3 address locations in 3 states within 90 days: CO, UT and NM. The employer is a "
            "multi-state builder and all 3 addresses are within 12 miles of one of its active "
            "project sites - but no lease or utility record confirms residence at any of them."
        ),
        ssn_apps_last_30d=(
            "2 applications in the trailing 30 days, in 2 states, for 2 different vehicle classes."
        ),
    ),
    alerts=(
        Alert(
            alert_id=50,
            finding_text=(
                "Borrower email address is shared with two other applicant identities carrying "
                "different surnames and addresses, both first seen in the last sixty days."
            ),
            underlying_behavior="email shared across unrelated identities",
        ),
        Alert(
            alert_id=28,
            finding_text=(
                "Stated income exceeds the verified base salary by 22%; the difference is "
                "attributed to per-diem and bonus income the employer would not confirm."
            ),
            underlying_behavior="income overstatement pending bonus verification",
        ),
        Alert(
            alert_id=44,
            finding_text=(
                "Borrower address has changed between applications, moving across three states "
                "within ninety days."
            ),
            underlying_behavior="multi-state address movement",
        ),
        Alert(
            alert_id=45,
            finding_text=(
                "Current address state differs from the state on the two most recent prior "
                "applications; each move tracks an employer project site."
            ),
            underlying_behavior="multi-state address movement",
        ),
    ),
    expected=expected_block(
        overall="MEDIUM RISK",
        recommendation="REVIEW AND APPLY STIPULATIONS",
        bands=dict(
            identity="POSSIBLE RISK",
            dealer="LOW RISK",
            straw="LOW RISK",
            employment="LOW RISK",
            income="POSSIBLE RISK",
            synthetic="POSSIBLE RISK",
            bustout="LOW RISK",
            rings="LOW RISK",
        ),
        flags=dict(
            identity=0, dealer=0, straw=0, employment=0, income=0, synthetic=0, bustout=0, rings=0
        ),
        justifications=dict(
            identity=(
                "IDEN-011",
                "email_reuse_distinct_ssn_count: 1 is normal. 2+ requires context validation "
                "(family vs. fraud).",
            ),
            dealer=_DLR_LOW,
            straw=_STRW_NO_COBORROWER,
            employment=(
                "EMP-016",
                "ssn_distinct_employers_all_time vs ssn_distinct_employers_30d/60d: High all-time "
                "with low recent is normal career history. High recent count is velocity concern.",
            ),
            income=(
                "INC-012",
                "ssn_income_overstatement_percentage: <15% is normal reporting variance. 15-25% "
                "requires context. >25% is materially suspicious.",
            ),
            synthetic=(
                "SYN-008",
                "ssn_address_location_variations: 1-2 normal (relocation). 3+ states in short "
                "timeframes is concerning.",
            ),
            bustout=_BST_LOW,
            rings=_RNG_LOW,
        ),
        overall_justification=(
            "MSTR-021",
            "Base decisions on the totality of corroborated evidence across agents.",
        ),
        stipulations_expected=True,
        top_risk_factors_expected=True,
    ),
    notes=(
        "All eight flags are 0 while the overall outcome is MEDIUM. That combination is legal and "
        "important: MSTR-039 sets flags to 1 only on 'clear, material, corroborated evidence', and "
        "no single domain here reaches that bar - but MSTR-027's MEDIUM ('unresolved fraud "
        "concerns "
        "supported by moderate corroborating evidence... requiring additional verification') is "
        "exactly this record. A flag-counting implementation would return LOW.",
        "The three concerns rest on three separate underlying facts, so unlike APP-1010 they are "
        "not one signal counted three times. The email overlap is the one indicator in this "
        "fixture "
        "set whose _context row explicitly states that no benign explanation is available.",
        "The address movement has a partial benign account (project sites) and a gap (no residence "
        "record), which is what keeps synthetic at POSSIBLE rather than resolving it either way - "
        "the verification MSTR-033 wants stipulations for.",
    ),
)

EXTRA_SCENARIOS: Final[tuple[Scenario, ...]] = (
    APP_1011,
    APP_1012,
    APP_1013,
    APP_1014,
)

__all__ = ["EXTRA_SCENARIOS"]
