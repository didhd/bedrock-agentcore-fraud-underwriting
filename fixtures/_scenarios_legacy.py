"""
The five legacy APP-100x applications, ported forward without changing their stories.

The UI and README name these ids and their scenarios, and ``evals/datasets/underwriting_golden``
already asserts specific numbers from them (address reuse 2 at a 40-unit complex, 480 SSNs at the
hospital, 31% overstatement, 930 dealer score, 5-apps-in-7-days, 260 staffing-agency SSNs). Those
values are preserved verbatim here; what changes is that all 60 signals and all 60 ``_context``
rows are now present instead of the 20-30 the legacy module carried, so nothing is read through
``.get(name, 0)``.

Each ``expected`` block cites the requirement id and the customer's own sentence behind the band,
so the golden expectation is reviewable against her prompt rather than against this repo's taste.
"""

from __future__ import annotations

from typing import Final

from fixtures._baseline import application, context, signals
from fixtures._build import Alert, Scenario, expected_block

# --- shared customer sentences, quoted once ---------------------------------

_RNG_LOW: Final = (
    "RNG-020",
    "LOW RISK: suspicious indicators or alert activity may exist but are weak, isolated, "
    "explainable, operationally normal, contradicted by context, caused by data quality issues, "
    "or insufficiently corroborated to support material fraud concern.",
)
_BST_LOW: Final = (
    "BST-021",
    "LOW RISK: suspicious indicators or alert activity may exist but are weak, isolated, "
    "explainable, operationally normal, contradicted by context, caused by data quality issues, "
    "or insufficiently corroborated to support material fraud concern.",
)
_STRW_NO_COBORROWER: Final = (
    "STRW-008",
    "No co-borrower present: Primary straw mechanism absent. Only evaluate if other patterns "
    "(role-switching history, dealer clustering) exist.",
)
_DLR_LOW: Final = (
    "DLR-020",
    "LOW RISK: Dealer signals reflect normal operational volume, portfolio risk metrics without "
    "fraud coordination evidence, or isolated borrower-level concerns. LOW should be the most "
    "common outcome.",
)
_EMP_LARGE: Final = (
    "EMP-017",
    "Large national employers, military, government, staffing agencies, and gig platforms "
    "naturally have high SSN counts — this is NOT fraud signal.",
)
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
_MSTR_HIGH: Final = (
    "MSTR-029",
    "HIGH RISK should generally require either multiple strongly corroborated fraud patterns or "
    "one extremely severe and well-supported fraud indicator. This should be reserved for the "
    "most extreme and clear cases of fraud.",
)

APP_1001: Final = Scenario(
    app_id="APP-1001",
    scenario="Clean first-time buyer; thin file explained by age",
    why=(
        "The LOW-across-the-board control. Every suspicious-looking value has a benign cause "
        "stated in its own _context row: the address is a 40-unit complex, the SSN is new to the "
        "system because the borrower is a first-time buyer, and the 480-SSN employer is a "
        "hospital. Exercises XAG-003 (LOW is the most common outcome) end to end."
    ),
    application=application(
        hash_lender_id="hash_lndr_a1c4",
        lender_name="SUMMIT_CU",
        borrower_name="Jordan Blake",
        hash_ssn="hash_ssn_7f3a91",
        borrower_age=24,
        credit_score=688,
        fraud_score=142,
        income_misrep_score=8,
        loan_amount=24500,
        stated_annual_income=68000,
        verified_annual_income=66000,
        vehicle="2021 Honda Civic",
        ltv_pct=88,
        dealer_name="Sunrise Auto Group",
        ppi_dealer_id="PPI-DLR-0330",
        consortium_number_of_apps_last_1_year=940,
        employer_name="Regional Medical Center",
        employer_street_addr="1400 Medical Center Dr",
        employer_city="Spokane",
        employer_state="WA",
        employer_zip="99204",
        employer_phone="509-555-0188",
        occupation="RESPIRATORY THERAPIST",
        application_date="2026-06-18",
        submitted_to_prod_at="2026-06-18T13:41:00Z",
    ),
    signals=signals(
        address_reuse_distinct_ssn_count=2,
        is_multi_unit_address=True,
        ssn_days_in_system=0,
        inquiries_in_2weeks=2,
        dealer_consortium_score=310,
        dealer_risk_score=295,
        dealer_consortium_epd_rate_last_24mo=0.03,
        dealer_app_volume_24mo=1900,
        dealer_apps_last_7d=14,
        employer_concentration_pct=6.5,
        ssn_previous_apps_all_time=0,
        ssn_previous_apps_90d=0,
        employer_distinct_other_ssns_count=480,
        employer_other_ssns_count=480,
        employer_phone_distinct_ssn_count=480,
        employer_new_ssns_90d=12,
        ssn_distinct_employers_all_time=2,
        ssn_distinct_income_amounts_all_time=1,
        ssn_income_overstatement_percentage=3,
        loan_to_annual_income_ratio=0.36,
        is_thin_file=1,
        address_ssn_count_same_building=38,
        address_ssn_count_same_unit=1,
    ),
    context=context(
        address_reuse_distinct_ssn_count=(
            "Address key resolves to 2 hash_ssn values. The property is a 40-unit apartment "
            "complex; the other hash_ssn applied from a different unit number 8 months ago."
        ),
        is_multi_unit_address=(
            "Street address parses a unit token (APT 12) and the property record is a 40-unit "
            "apartment complex."
        ),
        ssn_days_in_system=(
            "First consortium sighting is this application - 0 days. No prior applications, no "
            "prior alerts, no prior dealer contact for this hash_ssn."
        ),
        inquiries_in_2weeks="2 inquiries in the trailing 14 days. Credit score 688.",
        dealer_consortium_score=(
            "Dealer scored 310 this month; 295-325 across the trailing 12 months, flat."
        ),
        dealer_risk_score="Lender-portfolio score 295 for this dealer_id / lender_id pair.",
        dealer_app_volume_24mo=(
            "1,900 consortium applications in the trailing 24 months; "
            "consortium_number_of_apps_last_1_year is 940, so the dealer is active. A "
            "high-volume dealer carries higher absolute counts of everything."
        ),
        dealer_apps_last_7d=(
            "14 applications at this dealer in the last 7 days: 14 distinct hash_ssn values, 14 "
            "distinct phones, 13 distinct addresses, 12 distinct employers."
        ),
        employer_concentration_pct=(
            "Regional Medical Center accounts for 124 of 1,900 applications at this dealer "
            "(6.5%). The dealer is 3 miles from the hospital campus."
        ),
        ssn_previous_apps_all_time=(
            "0 prior applications on this hash_ssn - first-time buyer, and 0 prior alerts."
        ),
        ssn_previous_apps_90d="0 prior applications in the trailing 90 days for this hash_ssn.",
        employer_distinct_other_ssns_count=(
            "480 distinct hash_ssn values cite Regional Medical Center across the consortium over "
            "6 years. The employer has a verifiable street address, city, state, ZIP and a listed "
            "main line, and is a 1,100-bed hospital."
        ),
        employer_other_ssns_count=(
            "480 other hash_ssn values share this employer, spread across 41 dealers and 5 "
            "states. No address, phone or dealer clustering among them."
        ),
        employer_phone_distinct_ssn_count=(
            "480 hash_ssn values share 509-555-0188, the hospital's published main line. Not a "
            "personal cell number."
        ),
        employer_new_ssns_90d=(
            "12 new hash_ssn values cited this employer in the last 90 days against 480 all-time "
            "- a 2.5% addition rate, in line with the hospital's normal hiring."
        ),
        ssn_distinct_employers_all_time=(
            "2 employers all-time: Regional Medical Center for 14 months, and a part-time campus "
            "job before that."
        ),
        ssn_distinct_income_amounts_all_time=(
            "1 stated amount on this hash_ssn: 68,000 on this application. No prior application "
            "to compare against."
        ),
        ssn_income_overstatement_percentage=(
            "Stated 68,000 vs verified 66,000 = 3% overstatement, inside normal reporting "
            "variance."
        ),
        loan_to_annual_income_ratio="Loan 24,500 against stated annual income 68,000 = 0.36.",
        is_thin_file=(
            "3 open tradelines, time in file 19 months. Borrower is 24; the file length matches "
            "the borrower's age. Credit report present and valid."
        ),
        address_ssn_count_same_building=(
            "38 hash_ssn values at this building across 40 units and 6 years of applications - "
            "building-level overlap in an apartment complex."
        ),
        address_ssn_count_same_unit="1 hash_ssn at unit APT 12 - this borrower only.",
    ),
    alerts=(
        Alert(
            alert_id=13,
            finding_text=(
                "Borrower address is a multi-tenant property shared with other consortium "
                "applicants; the property record is a 40-unit apartment complex."
            ),
            underlying_behavior="apartment-complex address",
        ),
        Alert(
            alert_id=3,
            finding_text=(
                "Borrower SSN has no prior consortium history, consistent with a first-time "
                "applicant."
            ),
            underlying_behavior="first-time buyer, no prior history",
        ),
    ),
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
            identity=(
                "IDEN-012",
                "address_reuse_distinct_ssn_count: varies by property type. Multi-unit or dense "
                "areas can have high counts normally. Check is_multi_unit_address.",
            ),
            dealer=_DLR_LOW,
            straw=_STRW_NO_COBORROWER,
            employment=_EMP_LARGE,
            income=_INC_LOW,
            synthetic=(
                "SYN-013",
                "is_thin_file + very_new_file_high_score: Legitimate for young adults and new "
                "immigrants. Concerning only with multiple corroborating patterns.",
            ),
            bustout=_BST_LOW,
            rings=(
                "RNG-013",
                "Broad overlap patterns alone are not sufficient evidence of coordinated fraud "
                "ring activity, especially in dense urban populations, multi-unit housing "
                "environments, or high-volume operational networks.",
            ),
        ),
        overall_justification=_MSTR_LOW,
        stipulations_expected=False,
        top_risk_factors_expected=False,
    ),
    notes=(
        "ssn_days_in_system = 0 is the canonical IDEN-013 false positive: 'common for first-time "
        "buyers. Only concerning with other velocity signals' - and there are none here.",
    ),
)

APP_1002: Final = Scenario(
    app_id="APP-1002",
    scenario="Spousal co-borrower, shared phone and address, authorized-user tradelines",
    why=(
        "Exercises the straw prompt's family-relationship rule (STRW-015) and the identity "
        "prompt's shared-household reading of phone reuse (IDEN-010). Also the alert-volume "
        "control: 5 alerts fire, four of them from the single shared-household phone, so they are "
        "ONE signal (IDEN-014) and volume alone must not move the band (IDEN-018)."
    ),
    application=application(
        hash_lender_id="hash_lndr_b2e7",
        lender_name="RIVERBEND_FCU",
        borrower_name="Priya Nair",
        hash_ssn="hash_ssn_2c88de",
        borrower_age=29,
        credit_score=661,
        fraud_score=388,
        income_misrep_score=11,
        loan_amount=31200,
        stated_annual_income=72000,
        verified_annual_income=68000,
        vehicle="2022 Toyota RAV4",
        ltv_pct=91,
        dealer_name="Lakeside Motors",
        ppi_dealer_id="PPI-DLR-0118",
        consortium_number_of_apps_last_1_year=520,
        employer_name="Northwind Logistics",
        employer_street_addr="88 Depot Rd",
        employer_city="Tacoma",
        employer_state="WA",
        employer_zip="98402",
        employer_phone="253-555-0119",
        occupation="DISPATCH COORDINATOR",
        application_date="2026-06-20",
        submitted_to_prod_at="2026-06-20T17:26:00Z",
        coborrower_name="Arjun Nair",
        coborrower_relationship_type="spouse",
    ),
    signals=signals(
        phone_reuse_distinct_ssn_count=2,
        address_reuse_distinct_ssn_count=2,
        ssn_days_in_system=210,
        inquiries_in_2weeks=2,
        dealer_consortium_score=520,
        dealer_risk_score=470,
        dealer_consortium_epd_rate_last_24mo=0.05,
        dealer_app_volume_24mo=1100,
        dealer_apps_last_7d=9,
        phone_reuse_count_this_dealer=1,
        ssn_previous_apps_all_time=1,
        ssn_previous_apps_90d=0,
        has_coborrower=True,
        coborr_distinct_primary_borrowers_count=1,
        credit_score_gap=45,
        employer_distinct_other_ssns_count=34,
        employer_other_ssns_count=34,
        employer_phone_distinct_ssn_count=34,
        employer_new_ssns_90d=3,
        ssn_distinct_employers_all_time=1,
        ssn_income_overstatement_percentage=6,
        loan_to_annual_income_ratio=0.43,
        is_thin_file=1,
        high_authorized_user_ratio=1,
        phone_ssn_count=2,
        address_ssn_count_same_building=2,
        address_ssn_count_same_unit=2,
    ),
    context=context(
        phone_reuse_distinct_ssn_count=(
            "Normalized phone matches 2 hash_ssn values: this borrower and the co-borrower on this "
            "same application, who shares her surname and address."
        ),
        address_reuse_distinct_ssn_count=(
            "Address key resolves to 2 hash_ssn values: the borrower and the co-borrower, same "
            "unit, same surname."
        ),
        ssn_days_in_system=(
            "First consortium sighting 210 days ago; 1 prior application, same employer, same "
            "address."
        ),
        inquiries_in_2weeks="2 inquiries in the trailing 14 days. Credit score 661.",
        dealer_consortium_score=(
            "Dealer scored 520 this month; 495-540 across the trailing 12 months, flat. Moderate "
            "band."
        ),
        dealer_risk_score="Lender-portfolio score 470 across 61 funded loans.",
        dealer_consortium_epd_rate_last_24mo=(
            "55 early payment defaults across 1,100 consortium applications in 24 months."
        ),
        dealer_app_volume_24mo=(
            "1,100 consortium applications in 24 months; consortium_number_of_apps_last_1_year is "
            "520, so the dealer is active."
        ),
        dealer_apps_last_7d=(
            "9 applications at this dealer in the last 7 days: 9 distinct hash_ssn values, 8 "
            "distinct phones - the repeated phone is this borrower/co-borrower pair."
        ),
        phone_reuse_count_this_dealer=(
            "1 other borrower at this dealer shares this normalized phone: the co-borrower on "
            "this same application. No third party."
        ),
        ssn_previous_apps_all_time=(
            "1 prior application on this hash_ssn, 7 months ago, funded and current."
        ),
        ssn_previous_apps_90d="0 prior applications in the trailing 90 days for this hash_ssn.",
        has_coborrower=(
            "Co-borrower present. Source precedence resolved the row from the primary co-borrower "
            "table: shares the borrower's surname, address key and phone; relationship type on "
            "file is spouse."
        ),
        coborrower_appears_as_borrower_count=(
            "0 applications carry this co-borrower as the primary borrower; he appears on 2 "
            "applications, both as co-borrower with this same primary."
        ),
        borrower_appears_as_coborrower_count=(
            "0 applications in the consortium carry this hash_ssn as a co-borrower, across its 1 "
            "prior application."
        ),
        coborr_distinct_primary_borrowers_count=(
            "The co-borrower is linked to 1 primary borrower - this one, across both of their "
            "applications."
        ),
        credit_score_gap=(
            "Borrower 661, co-borrower 706. A 45-point gap, under the 100-point piggybacking "
            "threshold."
        ),
        coborr_income_70_percent_plus=(
            "Co-borrower contributes 44% of combined stated income (56,000 of 128,000)."
        ),
        coborr_3_plus_apps_same_dealer_flag=(
            "The co-borrower appears on 2 applications at this dealer, both with this same primary "
            "borrower."
        ),
        employer_distinct_other_ssns_count=(
            "34 distinct hash_ssn values cite Northwind Logistics over 4 years. Verifiable "
            "address, city, state, ZIP and listed main line."
        ),
        employer_other_ssns_count=(
            "34 other hash_ssn values share this employer, across 12 dealers and 3 states. No "
            "clustering."
        ),
        employer_phone_distinct_ssn_count=(
            "34 hash_ssn values share 253-555-0119, a listed business line."
        ),
        ssn_distinct_employers_all_time=(
            "1 employer all-time: Northwind Logistics, 3 years, on both applications."
        ),
        ssn_income_overstatement_percentage=(
            "Stated 72,000 vs verified 68,000 = 6% overstatement, inside normal reporting "
            "variance."
        ),
        loan_to_annual_income_ratio="Loan 31,200 against stated annual income 72,000 = 0.43.",
        is_thin_file=(
            "4 open tradelines, time in file 31 months. Credit established 2.5 years ago; report "
            "present and valid."
        ),
        high_authorized_user_ratio=(
            "2 of 4 open tradelines are authorized-user lines (50%), both on the spouse's older "
            "accounts. No velocity: 1 application in 30 days."
        ),
        phone_ssn_count=(
            "2 hash_ssn values on this normalized phone: the borrower and her spouse, same "
            "address."
        ),
        address_ssn_count_same_building="2 hash_ssn values at this building: the married couple.",
        address_ssn_count_same_unit=(
            "2 hash_ssn values at this unit: the borrower and the co-borrower, who share a "
            "surname."
        ),
    ),
    alerts=(
        Alert(
            alert_id=58,
            finding_text=(
                "Borrower phone number is shared with another consortium identity. The other "
                "identity is the co-borrower on this application, who shares the borrower's "
                "surname and address."
            ),
            underlying_behavior="shared household phone",
        ),
        Alert(
            alert_id=59,
            finding_text=(
                "Phone number appears on more than one identity record in the trailing 12 months "
                "- the borrower and her spouse."
            ),
            underlying_behavior="shared household phone",
        ),
        Alert(
            alert_id=60,
            finding_text=(
                "Phone number matches a second applicant at the same dealer; the second applicant "
                "is the co-borrower on this application."
            ),
            underlying_behavior="shared household phone",
        ),
        Alert(
            alert_id=46,
            finding_text=(
                "Borrower phone number differs from the number on the prior application 7 months "
                "ago; the new number is the household line now shared with the spouse."
            ),
            underlying_behavior="shared household phone",
        ),
        Alert(
            alert_id=19,
            finding_text=(
                "High share of authorized-user tradelines relative to open tradelines: 2 of 4, "
                "both on the spouse's older accounts."
            ),
            underlying_behavior="authorized-user credit building",
        ),
    ),
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
            identity=(
                "IDEN-014",
                "Multiple alerts firing from the same underlying data point (e.g. shared phone) "
                "is ONE signal, not multiple independent concerns.",
            ),
            dealer=_DLR_LOW,
            straw=(
                "STRW-015",
                "Family relationships (spouse, parent, child): Generally benign unless supported "
                "by role-switching + dealer clustering + income disproportion combined.",
            ),
            employment=(
                "EMP-018",
                "LOW RISK: Employer is verifiable or explainable, employment patterns are "
                "consistent, signals are isolated or explained by employer scale/industry. "
                "Missing fields alone = LOW. LOW should be the most common outcome.",
            ),
            income=_INC_LOW,
            synthetic=(
                "SYN-012",
                "high_authorized_user_ratio: Common for young adults, immigrants, credit "
                "builders. Only concerning with thin file + new credit + velocity combined.",
            ),
            bustout=_BST_LOW,
            rings=_RNG_LOW,
        ),
        overall_justification=_MSTR_LOW,
        stipulations_expected=False,
        top_risk_factors_expected=False,
    ),
    notes=(
        "This is the alert-volume-heavy-but-explainable case for the identity domain: 4 of the 5 "
        "fired alerts carry underlying_behavior 'shared household phone', which is exactly the "
        "example IDEN-014 gives.",
        "fraud_score 388 is in the customer's medium band (300-699, General Rule 0.5) and must "
        "still resolve LOW: the score is a model output, not corroborated evidence.",
    ),
)

APP_1003: Final = Scenario(
    app_id="APP-1003",
    scenario="Self-employed income variance with 31% overstatement; ambiguous MEDIUM",
    why=(
        "The ambiguous middle band. ssn_income_overstatement_percentage = 31 is above the "
        "customer's 25% 'materially suspicious' line (INC-012) while self-employment genuinely "
        "explains variance (INC-014) - so the income domain is POSSIBLE, the master is MEDIUM, "
        "and MSTR-035 forbids DECLINE on income deviation alone. Also the no-co-borrower straw "
        "path (STRW-005: 2-3 sentences)."
    ),
    application=application(
        hash_lender_id="hash_lndr_c3f1",
        lender_name="HARBOR_BANK",
        borrower_name="Marcus Webb",
        hash_ssn="hash_ssn_9a41b0",
        borrower_age=44,
        credit_score=692,
        fraud_score=655,
        income_misrep_score=62,
        loan_amount=41800,
        stated_annual_income=95000,
        verified_annual_income=72500,
        vehicle="2023 Ford F-150",
        ltv_pct=96,
        dealer_name="Highway 9 Auto",
        ppi_dealer_id="PPI-DLR-0207",
        consortium_number_of_apps_last_1_year=410,
        employer_name="Webb Contracting LLC",
        employer_street_addr="17 Kestrel Ln",
        employer_city="Reno",
        employer_state="NV",
        employer_zip="89502",
        employer_phone="775-555-0164",
        occupation="SELF-EMPLOYED CONTRACTOR",
        application_date="2026-06-22",
        submitted_to_prod_at="2026-06-22T11:08:00Z",
    ),
    signals=signals(
        ssn_days_in_system=900,
        inquiries_in_2weeks=3,
        dealer_consortium_score=640,
        dealer_risk_score=600,
        dealer_lender_default_rate=0.07,
        dealer_consortium_epd_rate_last_24mo=0.06,
        dealer_app_volume_24mo=860,
        dealer_apps_last_7d=7,
        ssn_previous_apps_all_time=3,
        ssn_previous_apps_90d=1,
        ssn_distinct_income_amounts_all_time=3,
        ssn_income_increase_20_percent_or_more=1,
        ssn_stated_income_exceeds_verified_by_25_percent=1,
        ssn_income_overstatement_percentage=31,
        loan_to_annual_income_ratio=0.44,
        ssn_income_high_variance_inconsistent_reporting=1,
        employer_distinct_other_ssns_count=2,
        employer_other_ssns_count=2,
        employer_phone_distinct_ssn_count=2,
        employer_new_ssns_90d=0,
        ssn_distinct_employers_all_time=3,
        ssn_apps_last_30d=2,
    ),
    context=context(
        ssn_days_in_system=(
            "First consortium sighting 900 days ago; 3 prior applications, all with the same name, "
            "DOB and address key."
        ),
        inquiries_in_2weeks="3 inquiries in the trailing 14 days. Credit score 692.",
        dealer_consortium_score=(
            "Dealer scored 640 this month; 615-665 across the trailing 12 months. Moderate band, "
            "no upward trend."
        ),
        dealer_risk_score="Lender-portfolio score 600 across 48 funded loans.",
        dealer_lender_default_rate=(
            "7 defaults (charge-off or 90+) across 96 funded loans for this dealer with this "
            "lender."
        ),
        dealer_consortium_epd_rate_last_24mo=(
            "52 early payment defaults across 860 consortium applications in 24 months."
        ),
        dealer_app_volume_24mo=(
            "860 consortium applications in 24 months; consortium_number_of_apps_last_1_year is "
            "410, so the dealer is active."
        ),
        dealer_apps_last_7d=(
            "7 applications at this dealer in the last 7 days: 7 distinct hash_ssn values, 7 "
            "distinct phones, 7 distinct addresses, 6 distinct employers."
        ),
        ssn_previous_apps_all_time=(
            "3 prior applications on this hash_ssn over 30 months: 2 funded and current, 1 "
            "withdrawn. No EPD, no 999."
        ),
        ssn_previous_apps_90d=(
            "1 prior application in the trailing 90 days, at a different dealer 71 days ago, "
            "stated income 88,000."
        ),
        ssn_distinct_income_amounts_all_time=(
            "3 stated amounts: 74,000 (30 months ago), 88,000 (71 days ago), 95,000 (this "
            "application). Filing type on all three is self-employed."
        ),
        ssn_income_increase_20_percent_or_more=(
            "Stated income rose from 74,000 to 95,000 across 30 months (+28%). The step from "
            "88,000 to 95,000 is +8% over 71 days. No employer change in the interval - the "
            "employer is the borrower's own LLC throughout."
        ),
        ssn_stated_income_exceeds_verified_by_25_percent=(
            "Stated 95,000 against verified 72,500 - a 31% gap. Verification source is a 2-year "
            "tax transcript averaging Schedule C net profit."
        ),
        ssn_income_overstatement_percentage=(
            "Stated 95,000 vs verified 72,500 = 31% overstatement. Prior applications overstated "
            "9% and 17%, so the gap is widening rather than a one-off."
        ),
        loan_to_annual_income_ratio="Loan 41,800 against stated annual income 95,000 = 0.44.",
        ssn_income_high_variance_inconsistent_reporting=(
            "Three stated amounts over 30 months, coefficient of variation 0.11. Schedule C "
            "filer: quarterly net profit over the last 8 quarters ranged 12,400 to 24,900, which "
            "is genuine self-employment seasonality."
        ),
        employer_distinct_other_ssns_count=(
            "2 distinct hash_ssn values cite Webb Contracting LLC: the borrower and one other, "
            "who shares the borrower's surname. The LLC has a registered address, a listed phone "
            "and 4 years of state registration."
        ),
        employer_other_ssns_count=(
            "2 other hash_ssn values share this employer, both at the same address as the "
            "borrower, and one shares his surname."
        ),
        employer_phone_distinct_ssn_count=(
            "2 hash_ssn values share 775-555-0164. The number is the LLC's registered line and "
            "also appears as the borrower's personal contact number."
        ),
        employer_new_ssns_90d=(
            "0 new hash_ssn values cited this employer in the last 90 days, against 2 all-time."
        ),
        ssn_distinct_employers_all_time=(
            "3 employers all-time: Webb Contracting LLC for 4 years, and 2 construction employers "
            "before that."
        ),
        ssn_apps_last_30d=(
            "2 applications in the trailing 30 days: this one, and one 22 days ago at a different "
            "dealer for the same vehicle class."
        ),
    ),
    alerts=(
        Alert(
            alert_id=27,
            finding_text=(
                "Stated income exceeds the verified figure by more than a quarter: 95,000 stated "
                "against 72,500 verified from tax transcripts."
            ),
            underlying_behavior="income overstatement",
        ),
        Alert(
            alert_id=29,
            finding_text=(
                "Stated income has risen across successive applications without a change of "
                "employer: 74,000 to 88,000 to 95,000."
            ),
            underlying_behavior="income overstatement",
        ),
        Alert(
            alert_id=133,
            finding_text=(
                "Income reported for this SSN varies materially between applications, consistent "
                "with self-employed net-profit reporting."
            ),
            underlying_behavior="self-employment income variance",
        ),
    ),
    expected=expected_block(
        overall="MEDIUM RISK",
        recommendation="REVIEW AND APPLY STIPULATIONS",
        bands=dict(
            identity="LOW RISK",
            dealer="LOW RISK",
            straw="LOW RISK",
            employment="LOW RISK",
            income="POSSIBLE RISK",
            synthetic="LOW RISK",
            bustout="LOW RISK",
            rings="LOW RISK",
        ),
        flags=dict(
            identity=0, dealer=0, straw=0, employment=0, income=1, synthetic=0, bustout=0, rings=0
        ),
        justifications=dict(
            identity=_IDEN_LOW,
            dealer=(
                "DLR-013",
                "dealer_consortium_epd_rate_last_24mo: Early payment default rate. Credit "
                "indicator, not fraud indicator unless unusually concentrated.",
            ),
            straw=(
                "STRW-005",
                "If no co-borrower exists, straw risk is inherently minimal — keep analysis to "
                "2-3 sentences.",
            ),
            employment=(
                "EMP-008",
                'Vague employer names (e.g. "Sunset", "ABC Services") are data quality issues '
                "unless combined with other fraud patterns.",
            ),
            income=(
                "INC-012",
                "ssn_income_overstatement_percentage: <15% is normal reporting variance. 15-25% "
                "requires context. >25% is materially suspicious.",
            ),
            synthetic=_SYN_LOW,
            bustout=_BST_LOW,
            rings=_RNG_LOW,
        ),
        overall_justification=_MSTR_MEDIUM,
        stipulations_expected=True,
        top_risk_factors_expected=True,
    ),
    notes=(
        "MSTR-035 is the load-bearing constraint here: 'Income deviation alone should generally "
        "not justify DECLINE without additional corroborating fraud indicators.' Nothing else on "
        "this record corroborates, so the ceiling is REVIEW AND APPLY STIPULATIONS.",
        "The employment domain must stay LOW even though the employer has only 2 SSNs: it is the "
        "borrower's own registered LLC, which the employer context states.",
        "Straw is the 2-3 sentence path (STRW-005), which the golden dataset asserts separately.",
    ),
)

APP_1004: Final = Scenario(
    app_id="APP-1004",
    scenario="Synthetic identity, exact-unit ring overlap, shell employer, negative-file hit",
    why=(
        "The only HIGH record among the legacy five, and the one that must be HIGH for the right "
        "reason: multiple independent corroborated patterns (MSTR-029), not alert volume. Carries "
        "the dealer-900 trap - the consortium score is 930, and DLR-009 forbids escalating on "
        "that alone, so the dealer band rests on the phone-reuse coordination instead."
    ),
    application=application(
        hash_lender_id="hash_lndr_d4b8",
        lender_name="MERIDIAN_FINANCE",
        borrower_name="Devon Cross",
        hash_ssn="hash_ssn_ff02aa",
        borrower_age=57,
        credit_score=771,
        fraud_score=999,
        income_misrep_score=71,
        loan_amount=58900,
        stated_annual_income=88000,
        verified_annual_income=61100,
        vehicle="2024 BMW X5",
        ltv_pct=112,
        dealer_name="Prestige Import Center",
        ppi_dealer_id="PPI-DLR-0451",
        consortium_number_of_apps_last_1_year=1180,
        employer_name="Apex Global Solutions",
        employer_street_addr="900 Commerce Way Ste 210",
        employer_city="Phoenix",
        employer_state="AZ",
        employer_zip="85004",
        employer_phone="602-555-0177",
        occupation="OPERATIONS MANAGER",
        application_date="2026-06-25",
        submitted_to_prod_at="2026-06-25T09:54:00Z",
    ),
    signals=signals(
        ssn_distinct_identity_combinations=3,
        phone_reuse_distinct_ssn_count=5,
        email_reuse_distinct_ssn_count=4,
        address_reuse_distinct_ssn_count=6,
        is_multi_unit_address=True,
        ssn_days_in_system=47,
        inquiries_in_2weeks=6,
        dealer_consortium_score=930,
        dealer_risk_score=905,
        dealer_lender_default_rate=0.14,
        dealer_consortium_epd_rate_last_24mo=0.12,
        phone_reuse_count_this_dealer=4,
        employer_concentration_pct=23.0,
        ssn_previous_apps_all_time=4,
        ssn_previous_apps_90d=4,
        previous_epd_count=1,
        previous_999_count=2,
        borr_ssn_in_negative_file=1,
        borr_ssn_linked_to_synthetic_fraud=1,
        dealer_app_volume_24mo=2300,
        dealer_apps_last_7d=31,
        high_value_vehicle_weak_profile_straw_risk=1,
        employer_distinct_other_ssns_count=9,
        employer_other_ssns_count=9,
        employer_phone_distinct_ssn_count=9,
        employer_new_ssns_90d=8,
        rapid_employer_change_flag=1,
        employer_spike_flag=1,
        ssn_distinct_employers_all_time=4,
        ssn_distinct_employers_30d=2,
        ssn_distinct_employers_60d=3,
        ssn_distinct_income_amounts_all_time=4,
        ssn_income_increase_20_percent_or_more=1,
        ssn_multiple_apps_7d_different_incomes=1,
        ssn_stated_income_exceeds_verified_by_25_percent=1,
        ssn_income_overstatement_percentage=44,
        loan_to_annual_income_ratio=0.67,
        ssn_address_location_variations=4,
        has_significant_identity_inconsistency=1,
        address_ssn_count_same_building=7,
        address_ssn_count_same_unit=5,
        phone_ssn_count=5,
        high_authorized_user_ratio=1,
        is_thin_file=1,
        very_new_file_high_score=1,
        older_borrower_new_credit_high_score=1,
        rapid_app_velocity=1,
        ssn_apps_last_7d=4,
        ssn_apps_last_30d=7,
    ),
    context=context(
        ssn_distinct_identity_combinations=(
            "3 combinations on this hash_ssn: DEVON CROSS / 1969-02-xx, D CROSS / 1978-11-xx, and "
            "DEVIN CROSSE / 1969-02-xx. The DOBs differ by 9 years, which normalization cannot "
            "explain."
        ),
        phone_reuse_distinct_ssn_count=(
            "Normalized phone matches 5 hash_ssn values with 5 different surnames, 4 of them "
            "first seen in the last 90 days. None share an address with the others except the "
            "unit below."
        ),
        email_reuse_distinct_ssn_count=(
            "Normalized email matches 4 hash_ssn values with 4 different surnames, all 4 "
            "registered within a 6-week window."
        ),
        address_reuse_distinct_ssn_count=(
            "Address key resolves to 6 hash_ssn values. 5 of the 6 are at the SAME unit number, "
            "not spread across the building, and none share a surname."
        ),
        is_multi_unit_address=(
            "Street address parses a unit token (STE 4B) in a 12-unit building - but the overlap "
            "below is within one unit, not across the building."
        ),
        ssn_days_in_system=(
            "First consortium sighting 47 days ago, yet 4 applications have been filed since, at "
            "4 different dealers."
        ),
        inquiries_in_2weeks=(
            "6 inquiries in the trailing 14 days with a 771 credit score. Score at or above 650 "
            "with more than 4 recent inquiries is the pattern the identity instructions call out."
        ),
        dealer_consortium_score=(
            "Dealer scored 930 this month; 880-935 across the trailing 12 months, trending up. "
            "This is a portfolio-performance measure across all consortium lenders."
        ),
        dealer_risk_score=(
            "Lender-portfolio score 905 across 210 funded loans with this lender."
        ),
        dealer_lender_default_rate=(
            "29 defaults across 210 funded loans for this dealer with this lender."
        ),
        dealer_consortium_epd_rate_last_24mo=(
            "276 early payment defaults across 2,300 consortium applications in 24 months."
        ),
        phone_reuse_count_this_dealer=(
            "4 other borrowers at THIS dealer share this normalized phone number. None share a "
            "surname or address with each other; 3 of the 4 carried fraud scores above 900."
        ),
        employer_concentration_pct=(
            "Apex Global Solutions accounts for 529 of 2,300 applications at this dealer (23%). "
            "The employer has no verifiable presence and only 9 SSNs consortium-wide, so the "
            "concentration is not a large-employer or military-base effect."
        ),
        ssn_previous_apps_all_time=(
            "4 prior applications on this hash_ssn, all inside the last 47 days, at 4 different "
            "dealers."
        ),
        ssn_previous_apps_90d=(
            "4 prior applications in the trailing 90 days, at 4 different dealers, with 4 "
            "different stated incomes."
        ),
        previous_epd_count=(
            "1 prior application for this hash_ssn went to early payment default within 4 months "
            "of funding."
        ),
        previous_999_count=(
            "2 prior applications for this hash_ssn carried a fraud score of 999, meaning a fraud "
            "accelerator fired on each."
        ),
        borr_ssn_in_negative_file=(
            "hash_ssn is present in the negative file, added 3 months ago by a consortium lender "
            "after a confirmed loss."
        ),
        borr_ssn_linked_to_synthetic_fraud=(
            "1 confirmed synthetic-fraud record is linked to this hash_ssn: a loan funded 14 "
            "months "
            "ago and closed as a confirmed fabricated identity."
        ),
        dealer_app_volume_24mo=(
            "2,300 consortium applications in 24 months; consortium_number_of_apps_last_1_year is "
            "1,180, so the dealer is active and high-volume."
        ),
        dealer_apps_last_7d=(
            "31 applications at this dealer in the last 7 days: 31 distinct hash_ssn values but "
            "only 24 distinct phones, and 7 of them cite Apex Global Solutions."
        ),
        high_value_vehicle_weak_profile_straw_risk=(
            "58,900 loan on a 2024 BMW X5 at 112% LTV against a 47-day-old credit file. Vehicle "
            "value is disproportionate to the documented profile."
        ),
        employer_distinct_other_ssns_count=(
            "9 distinct hash_ssn values cite Apex Global Solutions - 8 of them first seen in the "
            "last 90 days. No state business registration, and the suite address is a mail drop "
            "shared by 40 registrations."
        ),
        employer_other_ssns_count=(
            "9 other hash_ssn values share this employer; 7 of the 9 applied at THIS dealer and 3 "
            "share the borrower's phone number."
        ),
        employer_phone_distinct_ssn_count=(
            "9 hash_ssn values share 602-555-0177. The number is a mobile line and also appears "
            "as the personal contact number on 3 of those applications."
        ),
        employer_new_ssns_90d=(
            "8 new hash_ssn values cited this employer in the last 90 days against 9 all-time. "
            "The employer was effectively unknown to the consortium 90 days ago."
        ),
        rapid_employer_change_flag=(
            "3 employers cited across 47 days: Apex Global Solutions, and 2 others on applications "
            "18 and 31 days ago. No staffing agency or gig platform among them."
        ),
        employer_spike_flag=(
            "Monthly applications citing this employer: 0, 0, 1, 2, 3, 3 over the last 6 months, "
            "from a base of zero."
        ),
        ssn_distinct_employers_all_time=(
            "4 employers all-time for this hash_ssn, all first cited within the last 47 days."
        ),
        ssn_distinct_employers_30d="2 employers cited in the trailing 30 days.",
        ssn_distinct_employers_60d=(
            "3 employers cited in the trailing 60 days - the same window in which the hash_ssn "
            "first appeared."
        ),
        ssn_distinct_income_amounts_all_time=(
            "4 stated amounts within 47 days: 61,000, 74,000, 82,000 and 88,000."
        ),
        ssn_income_increase_20_percent_or_more=(
            "Stated income rose 61,000 to 88,000 in 47 days (+44%) across 3 employer changes."
        ),
        ssn_multiple_apps_7d_different_incomes=(
            "4 applications in the trailing 7 days stating 74,000, 82,000, 88,000 and 88,000 - a "
            "19% spread with no correction note or co-borrower application to explain it."
        ),
        ssn_stated_income_exceeds_verified_by_25_percent=(
            "Stated 88,000 against verified 61,100 - a 44% gap. No tax transcript or paystub on "
            "file."
        ),
        ssn_income_overstatement_percentage=(
            "Stated 88,000 vs verified 61,100 = 44% overstatement, well above the 25% materially-"
            "suspicious line."
        ),
        loan_to_annual_income_ratio="Loan 58,900 against stated annual income 88,000 = 0.67.",
        ssn_address_location_variations=(
            "4 address locations in 4 different states within 47 days: AZ, NV, TX and GA. No "
            "relocation record and the employer address did not change with them."
        ),
        has_significant_identity_inconsistency=(
            "Normalized DOB differs by 9 years between two of the three identity combinations on "
            "this hash_ssn, and the middle initial differs on all three."
        ),
        address_ssn_count_same_building=(
            "7 hash_ssn values in this 12-unit building - but 5 of the 7 are in the same unit."
        ),
        address_ssn_count_same_unit=(
            "5 hash_ssn values at unit STE 4B with 5 different surnames, 4 first seen in the last "
            "90 days. Exact-unit reuse, not building-level spread."
        ),
        phone_ssn_count=(
            "5 hash_ssn values on this normalized phone with 5 different surnames and 4 different "
            "addresses - not a household."
        ),
        high_authorized_user_ratio=(
            "5 of 6 open tradelines are authorized-user lines (83%), all added within the last 40 "
            "days, all on accounts belonging to unrelated identities."
        ),
        is_thin_file=(
            "6 open tradelines, time in file 2 months. Credit report present and valid, so the "
            "2-month file length is real, not a scrubbed sentinel."
        ),
        very_new_file_high_score=(
            "Time in file 2 months with a 771 score. The score is carried almost entirely by "
            "authorized-user tradelines opened in the same window."
        ),
        older_borrower_new_credit_high_score=(
            "Borrower is 57 with a 2-month credit file and a 771 score. A 57-year-old with no "
            "credit history before this quarter is rare legitimately."
        ),
        rapid_app_velocity=(
            "4 applications in 7 days, 7 in 30, at 5 different dealers in 4 states - not "
            "single-market rate shopping."
        ),
        ssn_apps_last_7d=(
            "4 applications in the trailing 7 days at 4 different dealers in 3 states, for 3 "
            "different vehicle classes."
        ),
        ssn_apps_last_30d=(
            "7 applications in the trailing 30 days at 5 different dealers, total requested "
            "exposure 310,000."
        ),
    ),
    alerts=(
        Alert(
            alert_id=4,
            finding_text=(
                "Borrower SSN is associated with more than one name and date of birth; two of the "
                "dates of birth differ by nine years."
            ),
            underlying_behavior="identity combination mismatch",
        ),
        Alert(
            alert_id=104,
            finding_text=(
                "Credit file is two months old while the credit score is in the high 700s, with "
                "nearly all tradelines added as authorized-user lines in the same window."
            ),
            underlying_behavior="manufactured credit file",
        ),
        Alert(
            alert_id=66,
            finding_text=(
                "Borrower address, at unit level, is shared with four other applicant identities "
                "carrying different surnames, all first seen in the last 90 days."
            ),
            underlying_behavior="exact-unit address cluster",
        ),
        Alert(
            alert_id=64,
            finding_text=(
                "Borrower phone number links to four other applicant identities at this same "
                "dealer, none of whom share an address or surname with each other."
            ),
            underlying_behavior="dealer-level phone cluster",
        ),
        Alert(
            alert_id=140,
            finding_text=(
                "Employer cited on this application has no verifiable business presence and added "
                "eight new applicant SSNs in the last ninety days from a base of one."
            ),
            underlying_behavior="shell employer",
        ),
        Alert(
            alert_id=27,
            finding_text=(
                "Stated income exceeds the verified figure by 44%, with no paystub or tax "
                "transcript on file."
            ),
            underlying_behavior="income overstatement",
        ),
        Alert(
            alert_id=157,
            finding_text=(
                "Seven applications in thirty days across five dealers, with total requested "
                "exposure of 310,000."
            ),
            underlying_behavior="application velocity",
        ),
        Alert(
            alert_id=163,
            finding_text=(
                "Borrower SSN appears in the negative file and is linked to a prior loss closed "
                "as a confirmed fabricated identity."
            ),
            underlying_behavior="negative-file and confirmed synthetic linkage",
        ),
    ),
    expected=expected_block(
        overall="HIGH RISK",
        recommendation="DECLINE",
        bands=dict(
            identity="HIGH RISK",
            dealer="POSSIBLE RISK",
            straw="LOW RISK",
            employment="HIGH RISK",
            income="POSSIBLE RISK",
            synthetic="HIGH RISK",
            bustout="POSSIBLE RISK",
            rings="HIGH RISK",
        ),
        flags=dict(
            identity=1, dealer=0, straw=0, employment=1, income=0, synthetic=1, bustout=0, rings=1
        ),
        justifications=dict(
            identity=(
                "IDEN-017",
                "HIGH RISK: Strong, corroborated identity manipulation with multiple independent "
                "inconsistencies. Uncommon.",
            ),
            dealer=(
                "DLR-021",
                "POSSIBLE RISK: Meaningful evidence of dealer-level coordination (phone reuse + "
                "employer clustering + identity patterns) that cannot be explained by volume or "
                "geography.",
            ),
            straw=_STRW_NO_COBORROWER,
            employment=(
                "EMP-020",
                "HIGH RISK: Strong evidence of fabricated/shell employer with coordinated "
                "application activity, OR confirmed employment history manipulation with "
                "corroborating fraud patterns. Uncommon.",
            ),
            income=(
                "INC-017",
                "POSSIBLE RISK: Materially suspicious patterns from genuinely independent sources "
                "(e.g. large overstatement + conflicting employment + rapid inflation across "
                "lenders).",
            ),
            synthetic=(
                "SYN-019",
                "HIGH RISK: Strong, corroborated synthetic construction with multiple independent "
                "inconsistencies (fabricated identity + manufactured credit + coordinated reuse). "
                "Uncommon.",
            ),
            bustout=(
                "BST-020",
                "POSSIBLE RISK: materially suspicious patterns supported by moderate corroborating "
                "evidence requiring verification before LOW RISK confidence can reasonably be "
                "established.",
            ),
            rings=(
                "RNG-014",
                "Repeated exact-unit reuse, coordinated multi-identifier overlap, or highly "
                "connected borrower clusters supported by corroborating inconsistencies are more "
                "significant than isolated overlap patterns alone.",
            ),
        ),
        overall_justification=_MSTR_HIGH,
        stipulations_expected=False,
        top_risk_factors_expected=True,
    ),
    notes=(
        "dealer_flag stays 0 while the dealer band is POSSIBLE. DLR-009 forbids escalating on the "
        "930 consortium score, and DLR-018 says the negative-file and synthetic-linkage flags are "
        "individual borrower flags that mean dealer fraud 'only if multiple such borrowers cluster "
        "at one dealer' - the phone cluster is suggestive but not the clear, material, "
        "corroborated "
        "evidence MSTR-039 requires for a flag of 1.",
        "straw stays LOW: there is no co-borrower at all, so the primary mechanism is absent "
        "(STRW-008) even though high_value_vehicle_weak_profile_straw_risk fires - STRW-014 says "
        "that signal is 'only concerning with OTHER corroborating straw patterns'.",
        "The 8 fired alerts span 8 distinct underlying_behavior values, which is what makes this "
        "record legitimately HIGH rather than an alert-volume artefact (MSTR-031).",
    ),
)

APP_1005: Final = Scenario(
    app_id="APP-1005",
    scenario="Bust-out velocity (5 apps/7d, 9/30d) with elevated loan-to-income",
    why=(
        "Velocity plus exposure with no identity manipulation. Also the staffing-agency control: "
        "the employer carries 260 SSNs and 40 new in 90 days, and EMP-017 names staffing agencies "
        "explicitly as NOT a fraud signal, so the employment domain must clear independently even "
        "though the application is MEDIUM overall."
    ),
    application=application(
        hash_lender_id="hash_lndr_e5d2",
        lender_name="PIONEER_AUTO_FINANCE",
        borrower_name="Alicia Moreno",
        hash_ssn="hash_ssn_5b7c30",
        borrower_age=41,
        credit_score=649,
        fraud_score=712,
        income_misrep_score=28,
        loan_amount=46000,
        stated_annual_income=54000,
        verified_annual_income=45800,
        vehicle="2023 Jeep Grand Cherokee",
        ltv_pct=103,
        dealer_name="Metro Motors East",
        ppi_dealer_id="PPI-DLR-0289",
        consortium_number_of_apps_last_1_year=760,
        employer_name="Contract Staffing Partners",
        employer_street_addr="55 Industrial Blvd",
        employer_city="Columbus",
        employer_state="OH",
        employer_zip="43215",
        employer_phone="614-555-0130",
        occupation="WAREHOUSE ASSOCIATE",
        application_date="2026-06-26",
        submitted_to_prod_at="2026-06-26T14:17:00Z",
    ),
    signals=signals(
        phone_reuse_distinct_ssn_count=2,
        address_reuse_distinct_ssn_count=2,
        ssn_days_in_system=1400,
        inquiries_in_2weeks=7,
        dealer_consortium_score=710,
        dealer_risk_score=680,
        dealer_lender_default_rate=0.09,
        dealer_consortium_epd_rate_last_24mo=0.08,
        phone_reuse_count_this_dealer=1,
        dealer_app_volume_24mo=1600,
        dealer_apps_last_7d=13,
        ssn_previous_apps_all_time=6,
        ssn_previous_apps_90d=9,
        employer_distinct_other_ssns_count=260,
        employer_other_ssns_count=260,
        employer_phone_distinct_ssn_count=260,
        employer_new_ssns_90d=40,
        rapid_employer_change_flag=1,
        employer_spike_flag=1,
        ssn_distinct_employers_all_time=5,
        ssn_distinct_employers_30d=1,
        ssn_distinct_employers_60d=2,
        ssn_distinct_income_amounts_all_time=3,
        ssn_income_increase_20_percent_or_more=1,
        ssn_income_overstatement_percentage=18,
        loan_to_annual_income_ratio=0.85,
        ssn_income_high_variance_inconsistent_reporting=1,
        phone_ssn_count=2,
        address_ssn_count_same_building=2,
        address_ssn_count_same_unit=2,
        rapid_app_velocity=1,
        ssn_apps_last_7d=5,
        ssn_apps_last_30d=9,
        ssn_income_increased_20_percent_last_30d_rapid_growth=1,
        ssn_loan_80_percent_or_more_annual_income_high_ratio=1,
    ),
    context=context(
        phone_reuse_distinct_ssn_count=(
            "Normalized phone matches 2 hash_ssn values: the borrower and one other sharing her "
            "surname and address."
        ),
        address_reuse_distinct_ssn_count=(
            "Address key resolves to 2 hash_ssn values, same surname, same unit, both present for "
            "3 years."
        ),
        ssn_days_in_system=(
            "First consortium sighting 1,400 days ago; 6 applications since, all with the same "
            "name, DOB and address key."
        ),
        inquiries_in_2weeks=(
            "7 inquiries in the trailing 14 days. Credit score 649, just below the 650 threshold "
            "the identity instructions pair with high inquiry counts."
        ),
        dealer_consortium_score=(
            "Dealer scored 710 this month; 690-725 across the trailing 12 months. Elevated band, "
            "flat trend."
        ),
        dealer_risk_score="Lender-portfolio score 680 across 132 funded loans.",
        dealer_lender_default_rate=(
            "12 defaults across 132 funded loans for this dealer with this lender."
        ),
        dealer_consortium_epd_rate_last_24mo=(
            "128 early payment defaults across 1,600 consortium applications in 24 months."
        ),
        phone_reuse_count_this_dealer=(
            "1 other borrower at this dealer shares this phone: the household member above."
        ),
        dealer_app_volume_24mo=(
            "1,600 consortium applications in 24 months; consortium_number_of_apps_last_1_year is "
            "760, so the dealer is active."
        ),
        dealer_apps_last_7d=(
            "13 applications at this dealer in the last 7 days: 13 distinct hash_ssn values, 12 "
            "distinct phones, 13 distinct addresses."
        ),
        ssn_previous_apps_all_time=(
            "6 prior applications on this hash_ssn over 46 months. No EPD, no 999, 2 funded and "
            "current."
        ),
        ssn_previous_apps_90d=(
            "9 prior applications in the trailing 90 days - 8 of them inside the last 30 days, at "
            "6 dealers within a 40-mile radius."
        ),
        employer_distinct_other_ssns_count=(
            "260 distinct hash_ssn values cite Contract Staffing Partners over 5 years. The "
            "employer is a licensed staffing agency with a verifiable address, city, state, ZIP "
            "and listed line."
        ),
        employer_other_ssns_count=(
            "260 other hash_ssn values share this employer, across 78 dealers and 4 states. No "
            "address or phone clustering among them."
        ),
        employer_phone_distinct_ssn_count=(
            "260 hash_ssn values share 614-555-0130, the agency's published main line."
        ),
        employer_new_ssns_90d=(
            "40 new hash_ssn values cited this employer in the last 90 days against 260 all-time. "
            "A staffing agency's roster turns over continuously; its trailing 4 quarters added "
            "36, 44, 31 and 40."
        ),
        rapid_employer_change_flag=(
            "2 employers cited across 60 days, both staffing placements: the agency itself and a "
            "prior agency assignment that ended 51 days ago."
        ),
        employer_spike_flag=(
            "Monthly applications citing this employer: 12, 15, 11, 14, 13, 16 over the last 6 "
            "months. The 90-day count is flat, not a spike from a quiet base."
        ),
        ssn_distinct_employers_all_time=(
            "5 employers all-time over 46 months, 3 of them staffing agencies - normal career "
            "history for contract work."
        ),
        ssn_distinct_employers_30d="1 employer cited in the trailing 30 days.",
        ssn_distinct_employers_60d=(
            "2 employers cited in the trailing 60 days, both staffing assignments."
        ),
        ssn_distinct_income_amounts_all_time=(
            "3 stated amounts: 44,000 (23 months ago), 45,000 (11 months ago), 54,000 (this "
            "application)."
        ),
        ssn_income_increase_20_percent_or_more=(
            "Stated income rose 45,000 to 54,000 (+20%) inside the last 30 days, with no employer "
            "change recorded in that window."
        ),
        ssn_income_overstatement_percentage=(
            "Stated 54,000 vs verified 45,800 = 18% overstatement - inside the 15-25% band the "
            "customer says 'requires context'."
        ),
        loan_to_annual_income_ratio=(
            "Loan 46,000 against stated annual income 54,000 = 0.85, above the 0.8 scrutiny line."
        ),
        ssn_income_high_variance_inconsistent_reporting=(
            "Three stated amounts over 23 months, coefficient of variation 0.10. Staffing work "
            "means hours vary by assignment; weekly gross over the last 26 weeks ranged 610 to "
            "1,240."
        ),
        phone_ssn_count=(
            "2 hash_ssn values on this normalized phone: the borrower and a household member with "
            "the same surname."
        ),
        address_ssn_count_same_building="2 hash_ssn values at this building, same surname.",
        address_ssn_count_same_unit="2 hash_ssn values at this unit, same surname, 3 years each.",
        rapid_app_velocity=(
            "5 applications in 7 days and 9 in 30, at 6 dealers inside a 40-mile radius, all for "
            "the same vehicle class."
        ),
        ssn_apps_last_7d=(
            "5 applications in the trailing 7 days at 5 different dealers, all for mid-size SUVs, "
            "requested amounts 43,000-47,000."
        ),
        ssn_apps_last_30d=(
            "9 applications in the trailing 30 days at 6 dealers. Total requested exposure "
            "398,000 against stated annual income of 54,000."
        ),
        ssn_income_increased_20_percent_last_30d_rapid_growth=(
            "Stated income rose from 45,000 to 54,000 (+20%) within the trailing 30 days. The "
            "employer record did not change in that window and no new assignment is on file."
        ),
        ssn_loan_80_percent_or_more_annual_income_high_ratio=(
            "Loan 46,000 is 85% of stated annual income 54,000, and 100% of verified income "
            "45,800."
        ),
        ssn_loan_exceeds_annual_income_unsustainable_debt=(
            "Loan 46,000 against stated annual income 54,000 - below income on the stated figure, "
            "though only just, and above the verified figure."
        ),
    ),
    alerts=(
        Alert(
            alert_id=157,
            finding_text=(
                "Nine applications in thirty days across six dealers, all for the same vehicle "
                "class, with total requested exposure of 398,000."
            ),
            underlying_behavior="application velocity",
        ),
        Alert(
            alert_id=164,
            finding_text=(
                "Requested loan amount is 85% of stated annual income and exceeds the verified "
                "income figure."
            ),
            underlying_behavior="loan exposure relative to income",
        ),
        Alert(
            alert_id=29,
            finding_text=(
                "Stated income increased 20% within thirty days with no corresponding change to "
                "the employer record."
            ),
            underlying_behavior="rapid stated-income growth",
        ),
        Alert(
            alert_id=133,
            finding_text=(
                "Income reported for this SSN varies between applications, consistent with "
                "assignment-based staffing hours."
            ),
            underlying_behavior="staffing-hours income variance",
        ),
        Alert(
            alert_id=76,
            finding_text=(
                "Employer cited on this application appears on a high number of consortium "
                "applications; the employer is a licensed staffing agency."
            ),
            underlying_behavior="staffing-agency employer scale",
        ),
    ),
    expected=expected_block(
        overall="MEDIUM RISK",
        recommendation="REVIEW AND APPLY STIPULATIONS",
        bands=dict(
            identity="LOW RISK",
            dealer="LOW RISK",
            straw="LOW RISK",
            employment="LOW RISK",
            income="POSSIBLE RISK",
            synthetic="LOW RISK",
            bustout="POSSIBLE RISK",
            rings="LOW RISK",
        ),
        flags=dict(
            identity=0, dealer=0, straw=0, employment=0, income=0, synthetic=0, bustout=1, rings=0
        ),
        justifications=dict(
            identity=_IDEN_LOW,
            dealer=(
                "DLR-009",
                "Do NOT escalate fraud risk classification based solely on dealer default rates "
                "or consortium scores.",
            ),
            straw=_STRW_NO_COBORROWER,
            employment=_EMP_LARGE,
            income=(
                "INC-012",
                "ssn_income_overstatement_percentage: <15% is normal reporting variance. 15-25% "
                "requires context. >25% is materially suspicious.",
            ),
            synthetic=_SYN_LOW,
            bustout=(
                "BST-012",
                "Rapid application velocity, elevated utilization, and increasing loan exposure "
                "are more significant when combined with meaningful income inconsistencies, "
                "coordinated dealership activity, abnormal tradeline behavior, or conflicting "
                "borrower information.",
            ),
            rings=_RNG_LOW,
        ),
        overall_justification=_MSTR_MEDIUM,
        stipulations_expected=True,
        top_risk_factors_expected=True,
    ),
    notes=(
        "employment must be LOW despite 260 SSNs, 40 new in 90 days, rapid_employer_change_flag "
        "and employer_spike_flag all firing: every one of those has a staffing-agency explanation "
        "in its own _context row, and EMP-017 names staffing agencies as NOT a fraud signal.",
        "income is POSSIBLE but income_flag is 0: 18% overstatement is in the 'requires context' "
        "band, and the context (assignment-based hours) supplies it, so MSTR-039's 'clear, "
        "material, corroborated' bar for a flag is not met.",
        "fraud_score 712 is in the customer's high band (700-998) and the record is still MEDIUM, "
        "not HIGH - MSTR-029 reserves HIGH for multiple strongly corroborated patterns.",
    ),
)

LEGACY_SCENARIOS: Final[tuple[Scenario, ...]] = (
    APP_1001,
    APP_1002,
    APP_1003,
    APP_1004,
    APP_1005,
)

__all__ = ["LEGACY_SCENARIOS"]
