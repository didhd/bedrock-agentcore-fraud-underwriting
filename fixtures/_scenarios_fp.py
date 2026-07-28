"""
The five anti-false-positive applications: records that LOOK bad and must still resolve LOW.

Each one isolates a rule the customer states as an explicit exception, so a regression shows up
as a specific band flip rather than as a vague drop in quality:

  APP-1006  employer address, phone, city, state and ZIP all null  -> EMP-007 data gap
  APP-1007  dealer consortium score 940 with zero coordination     -> DLR-009 credit-risk context
  APP-1008  military-base employer, 3,100 SSNs, 210 new in 90 days -> EMP-017 not a fraud signal
  APP-1009  dense 240-unit apartment, 96 SSNs at the building      -> RNG-013 benign overlap
  APP-1010  eleven fired alerts, every one from a single behaviour  -> IDEN-014 / RNG-023 one signal

These are the fixtures the eval harness references by id, so their ids are part of the contract:
``evals/datasets/underwriting_golden.jsonl`` already names ``APP-1006`` for the null-employer rule.
"""

from __future__ import annotations

from typing import Final

from fixtures._baseline import application, context, signals
from fixtures._build import Alert, Scenario, expected_block

_ALL_LOW: Final = dict(
    identity="LOW RISK",
    dealer="LOW RISK",
    straw="LOW RISK",
    employment="LOW RISK",
    income="LOW RISK",
    synthetic="LOW RISK",
    bustout="LOW RISK",
    rings="LOW RISK",
)
_NO_FLAGS: Final = dict(
    identity=0, dealer=0, straw=0, employment=0, income=0, synthetic=0, bustout=0, rings=0
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

APP_1006: Final = Scenario(
    app_id="APP-1006",
    scenario="Employer with every address, phone, city, state and ZIP field null",
    why=(
        "EMP-007's unverifiable-vs-fraudulent distinction, isolated. Every employer contact field "
        "is null and the employer name is vague ('Sunset Services'), which is exactly the pair of "
        "conditions EMP-007 and EMP-008 call data quality rather than fraud. EMP-018 states the "
        "outcome directly: 'Missing fields alone = LOW.' Referenced by name from "
        "evals/datasets/underwriting_golden.jsonl."
    ),
    application=application(
        hash_lender_id="hash_lndr_f6a3",
        lender_name="GOLDENONE",
        borrower_name="Rosa Delgado",
        hash_ssn="hash_ssn_c41e08",
        borrower_age=36,
        credit_score=713,
        fraud_score=204,
        income_misrep_score=14,
        loan_amount=22400,
        stated_annual_income=58000,
        verified_annual_income=56900,
        vehicle="2020 Mazda CX-5",
        ltv_pct=84,
        dealer_name="Willow Creek Motors",
        ppi_dealer_id="PPI-DLR-0512",
        consortium_number_of_apps_last_1_year=380,
        employer_name="Sunset Services",
        employer_street_addr=None,
        employer_city=None,
        employer_state=None,
        employer_zip=None,
        employer_phone=None,
        occupation="ADMINISTRATIVE ASSISTANT",
        application_date="2026-06-19",
        submitted_to_prod_at="2026-06-19T10:33:00Z",
    ),
    signals=signals(
        ssn_days_in_system=1610,
        dealer_consortium_score=290,
        dealer_risk_score=260,
        dealer_app_volume_24mo=790,
        dealer_apps_last_7d=6,
        ssn_previous_apps_all_time=3,
        ssn_previous_apps_90d=0,
        employer_distinct_other_ssns_count=6,
        employer_other_ssns_count=6,
        employer_phone_distinct_ssn_count=0,
        employer_new_ssns_90d=1,
        ssn_distinct_employers_all_time=3,
        ssn_income_overstatement_percentage=2,
        loan_to_annual_income_ratio=0.39,
    ),
    context=context(
        ssn_days_in_system=(
            "First consortium sighting 1,610 days ago; 3 prior applications, all with the same "
            "name, DOB and address key, and all citing this same employer."
        ),
        dealer_consortium_score=(
            "Dealer scored 290 this month; 275-305 across the trailing 12 months, flat."
        ),
        dealer_risk_score="Lender-portfolio score 260 across 44 funded loans.",
        dealer_app_volume_24mo=(
            "790 consortium applications in 24 months; consortium_number_of_apps_last_1_year is "
            "380, so the dealer is active."
        ),
        dealer_apps_last_7d=(
            "6 applications at this dealer in the last 7 days: 6 distinct hash_ssn values, 6 "
            "distinct phones, 6 distinct addresses, 6 distinct employers."
        ),
        ssn_previous_apps_all_time=(
            "3 prior applications on this hash_ssn over 53 months, all citing Sunset Services and "
            "all with the employer contact fields equally blank. 2 funded, both current."
        ),
        ssn_previous_apps_90d="0 prior applications in the trailing 90 days for this hash_ssn.",
        employer_distinct_other_ssns_count=(
            "6 distinct hash_ssn values cite 'Sunset Services'. The employer record carries NO "
            "street address, city, state, ZIP or phone - every contact field is null in the "
            "source. The 6 SSNs are at 6 different addresses across 2 states with no shared "
            "phones or dealers, so the sparse record is a data gap rather than a cluster."
        ),
        employer_other_ssns_count=(
            "6 other hash_ssn values share this employer name, across 5 dealers and 2 states. No "
            "address, phone or dealer overlap among them."
        ),
        employer_phone_distinct_ssn_count=(
            "Employer phone is null on this application and on all 3 prior applications citing "
            "this employer, so 0 phone rows exist to count distinct SSNs over. This is missing "
            "data, not an observed count of zero sharing."
        ),
        employer_new_ssns_90d=(
            "1 new hash_ssn cited this employer in the last 90 days against 6 all-time - no "
            "growth from a quiet base."
        ),
        ssn_distinct_employers_all_time=(
            "3 employers all-time over 53 months; Sunset Services on the last 4 applications "
            "spanning 3 years."
        ),
        ssn_income_overstatement_percentage=(
            "Stated 58,000 vs verified 56,900 = 2% overstatement."
        ),
        loan_to_annual_income_ratio="Loan 22,400 against stated annual income 58,000 = 0.39.",
    ),
    alerts=(
        Alert(
            alert_id=78,
            finding_text=(
                "Employer record is incomplete: no street address, city, state, ZIP or telephone "
                "number is present for the stated employer."
            ),
            underlying_behavior="incomplete employer record",
        ),
        Alert(
            alert_id=156,
            finding_text=(
                "Stated employer name is generic and could not be matched to a verified business "
                "record."
            ),
            underlying_behavior="incomplete employer record",
        ),
    ),
    expected=expected_block(
        overall="LOW RISK",
        recommendation="APPROVE",
        bands=dict(_ALL_LOW),
        flags=dict(_NO_FLAGS),
        justifications=dict(
            identity=_IDEN_LOW,
            dealer=_DLR_LOW,
            straw=_STRW_NO_COBORROWER,
            employment=(
                "EMP-007",
                "Missing employer address, phone, city, state, or ZIP is an UNDERWRITING DATA "
                "GAP, not employment fraud evidence.",
            ),
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
        "Both fired alerts share underlying_behavior 'incomplete employer record'. Two alerts from "
        "one data gap is ONE signal, and EMP-010 forbids escalating on incomplete employer records "
        "at all.",
        "employer_phone_distinct_ssn_count is 0 because the phone itself is null - its _context "
        "row "
        "says so explicitly, so an agent cannot read the zero as 'no sharing observed'.",
        "The same borrower has cited this same blank-record employer on 4 applications across 3 "
        "years, which is the strongest available argument that the blankness is a source-data "
        "property.",
    ),
)

APP_1007: Final = Scenario(
    app_id="APP-1007",
    scenario="Dealer consortium score 940 and 15% EPD rate with no coordination evidence",
    why=(
        "The dealer prompt's CRITICAL DISTINCTION, isolated from every confound. The dealer's "
        "portfolio metrics are as bad as they get - 940 consortium score, 940 risk score, 15% EPD, "
        "18% default rate - and there is zero coordination evidence: no phone reuse at the dealer, "
        "no employer concentration, no identity patterns. DLR-009 forbids escalating on score "
        "alone and DLR-010 requires reporting the metrics as credit-risk context."
    ),
    application=application(
        hash_lender_id="hash_lndr_a7b1",
        lender_name="CRESCENTBANK",
        borrower_name="Terrence Odom",
        hash_ssn="hash_ssn_88b2f5",
        borrower_age=47,
        credit_score=677,
        fraud_score=232,
        income_misrep_score=12,
        loan_amount=27600,
        stated_annual_income=71000,
        verified_annual_income=69800,
        vehicle="2021 Chevrolet Equinox",
        ltv_pct=92,
        dealer_name="Ridgeline Auto Sales",
        ppi_dealer_id="PPI-DLR-0904",
        consortium_number_of_apps_last_1_year=1240,
        employer_name="Bluegrass Freight Co",
        employer_street_addr="2200 Rail Yard Rd",
        employer_city="Louisville",
        employer_state="KY",
        employer_zip="40203",
        employer_phone="502-555-0155",
        occupation="DIESEL MECHANIC",
        application_date="2026-06-21",
        submitted_to_prod_at="2026-06-21T16:12:00Z",
    ),
    signals=signals(
        ssn_days_in_system=2100,
        dealer_consortium_score=940,
        dealer_risk_score=940,
        dealer_lender_default_rate=0.18,
        dealer_consortium_epd_rate_last_24mo=0.15,
        phone_reuse_count_this_dealer=0,
        employer_concentration_pct=5.0,
        ssn_previous_apps_all_time=4,
        ssn_previous_apps_90d=1,
        dealer_app_volume_24mo=2600,
        dealer_apps_last_7d=22,
        employer_distinct_other_ssns_count=88,
        employer_other_ssns_count=88,
        employer_phone_distinct_ssn_count=88,
        employer_new_ssns_90d=5,
        ssn_distinct_employers_all_time=2,
        ssn_income_overstatement_percentage=2,
        loan_to_annual_income_ratio=0.39,
    ),
    context=context(
        ssn_days_in_system=(
            "First consortium sighting 2,100 days ago; 4 prior applications, all consistent name, "
            "DOB and address key."
        ),
        dealer_consortium_score=(
            "Dealer scored 940 this month; 905-945 across the trailing 12 months. This is a "
            "portfolio-performance measure aggregated across all consortium lenders and reflects "
            "loan outcomes, not participation in deception."
        ),
        dealer_risk_score=(
            "Lender-portfolio score 940 across 340 funded loans with this lender - the same "
            "portfolio story at the lender level."
        ),
        dealer_lender_default_rate=(
            "61 defaults across 340 funded loans for this dealer with this lender (18%). The "
            "dealer sells deep subprime; average borrower credit score across its book is 561."
        ),
        dealer_consortium_epd_rate_last_24mo=(
            "390 early payment defaults across 2,600 consortium applications in 24 months (15%), "
            "spread evenly across 9 lenders rather than concentrated in one."
        ),
        phone_reuse_count_this_dealer=(
            "No other borrower at this dealer shares this normalized phone number. Across the "
            "dealer's last 200 applications, 198 distinct phones appear on 200 applications."
        ),
        employer_concentration_pct=(
            "Top employer at this dealer accounts for 130 of 2,600 applications (5.0%). No "
            "employer exceeds 6%; this borrower's employer accounts for 27."
        ),
        ssn_previous_apps_all_time=(
            "4 prior applications on this hash_ssn over 69 months, at 3 dealers. No EPD, no 999."
        ),
        ssn_previous_apps_90d=(
            "1 prior application in the trailing 90 days, 62 days ago, same stated income."
        ),
        dealer_app_volume_24mo=(
            "2,600 consortium applications in 24 months; consortium_number_of_apps_last_1_year is "
            "1,240, so the dealer is active and high-volume."
        ),
        dealer_apps_last_7d=(
            "22 applications at this dealer in the last 7 days: 22 distinct hash_ssn values, 22 "
            "distinct phones, 21 distinct addresses, 20 distinct employers. No repeated "
            "identifier."
        ),
        employer_distinct_other_ssns_count=(
            "88 distinct hash_ssn values cite Bluegrass Freight Co over 6 years. Verifiable "
            "address, city, state, ZIP and listed main line; a regional trucking firm."
        ),
        employer_other_ssns_count=(
            "88 other hash_ssn values share this employer, across 31 dealers and 3 states. No "
            "clustering at this dealer."
        ),
        employer_phone_distinct_ssn_count=(
            "88 hash_ssn values share 502-555-0155, the firm's published dispatch line."
        ),
        employer_new_ssns_90d=(
            "5 new hash_ssn values cited this employer in the last 90 days against 88 all-time."
        ),
        ssn_distinct_employers_all_time=(
            "2 employers all-time: Bluegrass Freight Co for 7 years, one prior for 4."
        ),
        ssn_income_overstatement_percentage="Stated 71,000 vs verified 69,800 = 2% overstatement.",
        loan_to_annual_income_ratio="Loan 27,600 against stated annual income 71,000 = 0.39.",
    ),
    alerts=(
        Alert(
            alert_id=93,
            finding_text=(
                "Selling dealer's consortium performance score is in the highest band, driven by "
                "loan outcomes across its subprime book."
            ),
            underlying_behavior="dealer portfolio performance",
        ),
        Alert(
            alert_id=94,
            finding_text=(
                "Selling dealer's early payment default rate over the trailing 24 months is 15%, "
                "spread evenly across nine lenders."
            ),
            underlying_behavior="dealer portfolio performance",
        ),
        Alert(
            alert_id=95,
            finding_text=(
                "Selling dealer's default rate with this lender is 18% across 340 funded loans."
            ),
            underlying_behavior="dealer portfolio performance",
        ),
    ),
    expected=expected_block(
        overall="LOW RISK",
        recommendation="APPROVE",
        bands=dict(_ALL_LOW),
        flags=dict(_NO_FLAGS),
        justifications=dict(
            identity=_IDEN_LOW,
            dealer=(
                "DLR-009",
                "Do NOT escalate fraud risk classification based solely on dealer default rates "
                "or consortium scores.",
            ),
            straw=_STRW_NO_COBORROWER,
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
        "DLR-010 is the positive obligation here, not just the prohibition: the dealer analysis "
        "must 'report portfolio risk metrics factually but classify them as credit risk context, "
        "not fraud evidence'. A dealer analysis on this record that omits the 940 score is as "
        "wrong as one that escalates on it.",
        "All 3 fired alerts share underlying_behavior 'dealer portfolio performance' - one "
        "underlying fact reported three ways.",
        "APP-1004 also carries a 900+ dealer score, but there it sits alongside real coordination "
        "evidence. This record is the score with the coordination removed, which is what makes the "
        "rule testable.",
    ),
)

APP_1008: Final = Scenario(
    app_id="APP-1008",
    scenario="Military-base employer: 3,100 SSNs, 210 new in 90 days, 34% dealer concentration",
    why=(
        "EMP-017 names 'Large national employers, military, government, staffing agencies, and gig "
        "platforms' as explicitly NOT a fraud signal, and DLR-015 says employer concentration is "
        "'Normal for dealers near large employers/bases'. This record maximises both numbers at "
        "once so the two rules are exercised together rather than singly."
    ),
    application=application(
        hash_lender_id="hash_lndr_b9c6",
        lender_name="DIGITALFCU",
        borrower_name="Kendra Whitfield",
        hash_ssn="hash_ssn_1a77de",
        borrower_age=26,
        credit_score=698,
        fraud_score=188,
        income_misrep_score=10,
        loan_amount=28900,
        stated_annual_income=52000,
        verified_annual_income=51200,
        vehicle="2022 Nissan Frontier",
        ltv_pct=94,
        dealer_name="Gateway Base Motors",
        ppi_dealer_id="PPI-DLR-0677",
        consortium_number_of_apps_last_1_year=1020,
        employer_name="United States Army - Fort Cavendish",
        employer_street_addr="1 Cavendish Post Rd",
        employer_city="Cavendish",
        employer_state="TX",
        employer_zip="76544",
        employer_phone="254-555-0100",
        occupation="ACTIVE DUTY SERVICE MEMBER",
        application_date="2026-06-23",
        submitted_to_prod_at="2026-06-23T12:45:00Z",
    ),
    signals=signals(
        address_reuse_distinct_ssn_count=4,
        is_multi_unit_address=True,
        ssn_days_in_system=520,
        inquiries_in_2weeks=2,
        dealer_consortium_score=560,
        dealer_risk_score=520,
        dealer_consortium_epd_rate_last_24mo=0.06,
        employer_concentration_pct=34.0,
        ssn_previous_apps_all_time=1,
        ssn_previous_apps_90d=0,
        dealer_app_volume_24mo=2100,
        dealer_apps_last_7d=19,
        employer_distinct_other_ssns_count=3100,
        employer_other_ssns_count=3100,
        employer_phone_distinct_ssn_count=3100,
        employer_new_ssns_90d=210,
        employer_spike_flag=1,
        ssn_distinct_employers_all_time=1,
        ssn_income_overstatement_percentage=2,
        loan_to_annual_income_ratio=0.56,
        address_ssn_count_same_building=9,
        address_ssn_count_same_unit=2,
        phone_reuse_distinct_ssn_count=2,
        phone_ssn_count=2,
        is_thin_file=1,
        high_authorized_user_ratio=1,
    ),
    context=context(
        address_reuse_distinct_ssn_count=(
            "Address key resolves to 4 hash_ssn values. The address is on-post military housing; "
            "the 4 identities are successive occupants across 5 years of PCS rotations, not "
            "concurrent residents."
        ),
        is_multi_unit_address=(
            "Address parses a unit token (BLDG 2210 UNIT C) in on-post family housing."
        ),
        ssn_days_in_system=(
            "First consortium sighting 520 days ago; 1 prior application, same employer, same "
            "on-post address."
        ),
        inquiries_in_2weeks="2 inquiries in the trailing 14 days. Credit score 698.",
        dealer_consortium_score=(
            "Dealer scored 560 this month; 540-580 across the trailing 12 months. Moderate band."
        ),
        dealer_risk_score="Lender-portfolio score 520 across 174 funded loans.",
        dealer_consortium_epd_rate_last_24mo=(
            "126 early payment defaults across 2,100 consortium applications in 24 months."
        ),
        employer_concentration_pct=(
            "Fort Cavendish accounts for 714 of 2,100 applications at this dealer (34%). The "
            "dealership is 1.2 miles from the main post gate and 68% of its inventory is sold to "
            "service members. This is the geography DLR-015 describes, not concentration at a "
            "small dealer."
        ),
        ssn_previous_apps_all_time=(
            "1 prior application on this hash_ssn, 14 months ago, funded and current."
        ),
        ssn_previous_apps_90d="0 prior applications in the trailing 90 days for this hash_ssn.",
        dealer_app_volume_24mo=(
            "2,100 consortium applications in 24 months; consortium_number_of_apps_last_1_year is "
            "1,020, so the dealer is active and high-volume."
        ),
        dealer_apps_last_7d=(
            "19 applications at this dealer in the last 7 days: 19 distinct hash_ssn values, 19 "
            "distinct phones, 17 distinct addresses (2 pairs in on-post housing), and 13 citing "
            "Fort Cavendish."
        ),
        employer_distinct_other_ssns_count=(
            "3,100 distinct hash_ssn values cite Fort Cavendish across the consortium over 7 "
            "years. The employer is a US Army installation with a verifiable address and a listed "
            "public line; the post's assigned strength is roughly 24,000."
        ),
        employer_other_ssns_count=(
            "3,100 other hash_ssn values share this employer, across 210 dealers and 31 states - "
            "the geographic spread of a military installation's personnel, with no clustering."
        ),
        employer_phone_distinct_ssn_count=(
            "3,100 hash_ssn values share 254-555-0100, the post's published information line. Not "
            "a personal cell number."
        ),
        employer_new_ssns_90d=(
            "210 new hash_ssn values cited this employer in the last 90 days against 3,100 "
            "all-time. The post's trailing 4 quarters added 190, 225, 178 and 210 - continuous "
            "PCS rotation, not growth from a quiet base."
        ),
        employer_spike_flag=(
            "Monthly applications citing this employer: 58, 61, 55, 64, 71, 88 over the last 6 "
            "months. The recent rise tracks the summer PCS season, which repeats every year."
        ),
        ssn_distinct_employers_all_time=(
            "1 employer all-time: the Army, on both applications for this hash_ssn."
        ),
        ssn_income_overstatement_percentage=(
            "Stated 52,000 vs verified 51,200 = 2% overstatement. Verified from an LES."
        ),
        loan_to_annual_income_ratio=(
            "Loan 28,900 against stated annual income 52,000 = 0.56, inside the elevated-but-"
            "common band for vehicle loans."
        ),
        address_ssn_count_same_building=(
            "9 hash_ssn values at this building across 8 units and 5 years of on-post occupancy."
        ),
        address_ssn_count_same_unit=(
            "2 hash_ssn values at this unit: this borrower and the prior occupant, who PCS'd out "
            "19 months ago. Not concurrent."
        ),
        phone_reuse_distinct_ssn_count=(
            "Normalized phone matches 2 hash_ssn values: this borrower and her spouse, same "
            "on-post address and surname."
        ),
        phone_ssn_count=(
            "2 hash_ssn values on this normalized phone: the borrower and her spouse."
        ),
        is_thin_file=(
            "4 open tradelines, time in file 41 months. Borrower is 26 and enlisted at 22; the "
            "file length matches. Credit report present and valid."
        ),
        high_authorized_user_ratio=(
            "2 of 4 open tradelines are authorized-user lines (50%), both on a parent's older "
            "accounts, opened 4 years ago. No velocity: 1 application in 30 days."
        ),
    ),
    alerts=(
        Alert(
            alert_id=76,
            finding_text=(
                "Employer cited on this application appears on a very high number of consortium "
                "applications; the employer is a US Army installation."
            ),
            underlying_behavior="military-installation employer scale",
        ),
        Alert(
            alert_id=132,
            finding_text=(
                "Number of new applicant SSNs citing this employer rose in the last ninety days, "
                "matching the installation's annual summer rotation season."
            ),
            underlying_behavior="military-installation employer scale",
        ),
        Alert(
            alert_id=136,
            finding_text=(
                "A high share of the selling dealer's applications cite a single employer; the "
                "dealership is 1.2 miles from the installation's main gate."
            ),
            underlying_behavior="dealer proximity to a large employer",
        ),
        Alert(
            alert_id=13,
            finding_text=(
                "Borrower address is shared with other consortium identities; the address is "
                "on-post family housing occupied successively rather than concurrently."
            ),
            underlying_behavior="on-post housing turnover",
        ),
        Alert(
            alert_id=19,
            finding_text=(
                "Half of the borrower's open tradelines are authorized-user lines, both on a "
                "parent's accounts opened four years ago."
            ),
            underlying_behavior="authorized-user credit building",
        ),
    ),
    expected=expected_block(
        overall="LOW RISK",
        recommendation="APPROVE",
        bands=dict(_ALL_LOW),
        flags=dict(_NO_FLAGS),
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
                "EMP-017",
                "Large national employers, military, government, staffing agencies, and gig "
                "platforms naturally have high SSN counts — this is NOT fraud signal.",
            ),
            income=_INC_LOW,
            synthetic=_SYN_LOW,
            bustout=_BST_LOW,
            rings=(
                "RNG-010",
                "Evaluate whether shared identifiers, overlap patterns, and network relationships "
                "materially exceed normal underwriting variation after considering family "
                "households, apartment complexes, shelters, military housing, large employers, "
                "population density, and benign operational overlap.",
            ),
        ),
        overall_justification=_MSTR_LOW,
        stipulations_expected=False,
        top_risk_factors_expected=False,
    ),
    notes=(
        "employer_spike_flag fires and must still clear: EMP-015 says a spike 'may reflect hiring "
        "surge, not fraud', and the context gives the specific cause (annual PCS season, with the "
        "prior three quarters' counts to prove the seasonality).",
        "The 34% employer concentration is above DLR-015's 20% threshold, but the threshold is "
        "qualified 'at small dealers with few apps' - this dealer has 2,100 applications in 24 "
        "months and sits 1.2 miles from the gate.",
        "'military housing' is one of the seven benign explanations RNG-010 names by name, which "
        "is "
        "why the rings justification quotes that sentence rather than the generic LOW definition.",
    ),
)

APP_1009: Final = Scenario(
    app_id="APP-1009",
    scenario="Dense 240-unit apartment: 96 SSNs at the building, 1 at the unit",
    why=(
        "RNG-013's benign-overlap rule at maximum magnitude. Building-level address reuse is 96 "
        "and exact-unit reuse is 1 - the exact contrast RNG-014 and SYN-010 draw between "
        "'repeated exact-unit reuse' and overlap that is 'normal in apartments'. The record exists "
        "to prove a big number alone cannot move the band."
    ),
    application=application(
        hash_lender_id="hash_lndr_c2f9",
        lender_name="GENERALELECTRIC",
        borrower_name="Nadia Kowalski",
        hash_ssn="hash_ssn_6e30ab",
        borrower_age=31,
        credit_score=690,
        fraud_score=356,
        income_misrep_score=13,
        loan_amount=23800,
        stated_annual_income=64000,
        verified_annual_income=62900,
        vehicle="2021 Hyundai Elantra",
        ltv_pct=90,
        dealer_name="Harborview Auto",
        ppi_dealer_id="PPI-DLR-0745",
        consortium_number_of_apps_last_1_year=610,
        employer_name="Lakefront Hospitality Group",
        employer_street_addr="310 Harbor St",
        employer_city="Chicago",
        employer_state="IL",
        employer_zip="60614",
        employer_phone="312-555-0166",
        occupation="RESTAURANT MANAGER",
        application_date="2026-06-17",
        submitted_to_prod_at="2026-06-17T18:04:00Z",
    ),
    signals=signals(
        address_reuse_distinct_ssn_count=96,
        is_multi_unit_address=True,
        ssn_days_in_system=990,
        inquiries_in_2weeks=2,
        dealer_consortium_score=480,
        dealer_risk_score=440,
        dealer_app_volume_24mo=1300,
        dealer_apps_last_7d=12,
        ssn_previous_apps_all_time=2,
        ssn_previous_apps_90d=0,
        employer_distinct_other_ssns_count=142,
        employer_other_ssns_count=142,
        employer_phone_distinct_ssn_count=142,
        employer_new_ssns_90d=14,
        ssn_distinct_employers_all_time=3,
        ssn_income_overstatement_percentage=2,
        loan_to_annual_income_ratio=0.37,
        address_ssn_count_same_building=96,
        address_ssn_count_same_unit=1,
        ssn_address_location_variations=2,
    ),
    context=context(
        address_reuse_distinct_ssn_count=(
            "Address key resolves to 96 hash_ssn values. The property is a 240-unit high-rise in "
            "a census tract with 21,000 people per square mile; 96 identities across 240 units and "
            "7 years of applications is below the building's expected turnover. Only 1 of the 96 "
            "is at this borrower's unit."
        ),
        is_multi_unit_address=(
            "Address parses a unit token (UNIT 1804) in a 240-unit residential high-rise."
        ),
        ssn_days_in_system=(
            "First consortium sighting 990 days ago; 2 prior applications, consistent name and "
            "DOB, 1 at a prior address in the same city."
        ),
        inquiries_in_2weeks="2 inquiries in the trailing 14 days. Credit score 690.",
        dealer_consortium_score=(
            "Dealer scored 480 this month; 460-500 across the trailing 12 months, flat."
        ),
        dealer_risk_score="Lender-portfolio score 440 across 88 funded loans.",
        dealer_app_volume_24mo=(
            "1,300 consortium applications in 24 months; consortium_number_of_apps_last_1_year is "
            "610, so the dealer is active."
        ),
        dealer_apps_last_7d=(
            "12 applications at this dealer in the last 7 days: 12 distinct hash_ssn values, 12 "
            "distinct phones, 11 distinct addresses (2 in the same high-rise, different units)."
        ),
        ssn_previous_apps_all_time=(
            "2 prior applications on this hash_ssn over 33 months, both funded and current."
        ),
        ssn_previous_apps_90d="0 prior applications in the trailing 90 days for this hash_ssn.",
        employer_distinct_other_ssns_count=(
            "142 distinct hash_ssn values cite Lakefront Hospitality Group over 5 years. "
            "Verifiable address, city, state, ZIP and listed line; the group operates 11 "
            "restaurants."
        ),
        employer_other_ssns_count=(
            "142 other hash_ssn values share this employer, across 48 dealers in 2 states. 3 of "
            "the 142 also live in this high-rise, which is a 240-unit building in the same "
            "neighbourhood as two of the group's restaurants."
        ),
        employer_phone_distinct_ssn_count=(
            "142 hash_ssn values share 312-555-0166, the group's corporate line."
        ),
        employer_new_ssns_90d=(
            "14 new hash_ssn values cited this employer in the last 90 days against 142 all-time - "
            "normal hospitality turnover."
        ),
        ssn_distinct_employers_all_time=(
            "3 employers all-time over 33 months, all hospitality; the current one for 2 years."
        ),
        ssn_income_overstatement_percentage="Stated 64,000 vs verified 62,900 = 2% overstatement.",
        loan_to_annual_income_ratio="Loan 23,800 against stated annual income 64,000 = 0.37.",
        address_ssn_count_same_building=(
            "96 hash_ssn values at this building across 240 units over 7 years - an average of 0.4 "
            "identities per unit, which is below normal turnover for the tract."
        ),
        address_ssn_count_same_unit=(
            "1 hash_ssn at unit 1804 - this borrower only. No exact-unit overlap at all."
        ),
        ssn_address_location_variations=(
            "2 address locations for this hash_ssn, both in Chicago, 22 months apart."
        ),
    ),
    alerts=(
        Alert(
            alert_id=13,
            finding_text=(
                "Borrower address is shared with a large number of other consortium identities; "
                "the property is a 240-unit residential high-rise."
            ),
            underlying_behavior="dense multi-unit building address",
        ),
        Alert(
            alert_id=65,
            finding_text=(
                "Borrower address appears in a cluster of consortium applications; the cluster is "
                "distributed across the building's 240 separate units."
            ),
            underlying_behavior="dense multi-unit building address",
        ),
        Alert(
            alert_id=66,
            finding_text=(
                "Multiple applicant identities are associated with the borrower's building; only "
                "one identity is associated with the borrower's own unit."
            ),
            underlying_behavior="dense multi-unit building address",
        ),
        Alert(
            alert_id=111,
            finding_text=(
                "Borrower shares an employer with three other applicants who live in the same "
                "building; the employer operates two restaurants in that neighbourhood."
            ),
            underlying_behavior="neighbourhood employer overlap",
        ),
    ),
    expected=expected_block(
        overall="LOW RISK",
        recommendation="APPROVE",
        bands=dict(_ALL_LOW),
        flags=dict(_NO_FLAGS),
        justifications=dict(
            identity=(
                "IDEN-012",
                "address_reuse_distinct_ssn_count: varies by property type. Multi-unit or dense "
                "areas can have high counts normally. Check is_multi_unit_address.",
            ),
            dealer=_DLR_LOW,
            straw=_STRW_NO_COBORROWER,
            employment=_EMP_LOW,
            income=_INC_LOW,
            synthetic=(
                "SYN-010",
                "address_ssn_count_same_building vs same_unit: Building-level overlap is normal in "
                "apartments. Same-unit overlap with unrelated SSNs is more concerning.",
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
        "address_reuse_distinct_ssn_count = 96 is the largest reuse number in the fixture set and "
        "must produce the lowest possible concern. APP-1004 escalates on 6 - because 5 of its 6 "
        "are in ONE unit. The pair is the contrast SYN-010 and RNG-014 describe.",
        "3 of the 4 fired alerts share underlying_behavior 'dense multi-unit building address'.",
        "fraud_score 356 is in the medium band and must not by itself move the outcome.",
    ),
)

APP_1010: Final = Scenario(
    app_id="APP-1010",
    scenario="Eleven fired alerts, every one traceable to a single shared household phone",
    why=(
        "The most repeated guardrail in the corpus, isolated: 'Alert volume alone does NOT "
        "indicate "
        "risk. Multiple alerts from the same underlying behavior = one signal' (IDEN-018, INC-019, "
        "SYN-020) and its bustout/rings twin (BST-024, RNG-023). Eleven alerts fire across five "
        "General Rule 3 categories; all eleven carry the same underlying_behavior, so the correct "
        "reading is one signal, and one household phone is not fraud."
    ),
    application=application(
        hash_lender_id="hash_lndr_d8e4",
        lender_name="ARRA",
        borrower_name="Hector Villanueva",
        hash_ssn="hash_ssn_39f6c2",
        borrower_age=52,
        credit_score=671,
        fraud_score=598,
        income_misrep_score=17,
        loan_amount=26300,
        stated_annual_income=61000,
        verified_annual_income=59500,
        vehicle="2020 Toyota Tacoma",
        ltv_pct=87,
        dealer_name="Sierra Valley Motors",
        ppi_dealer_id="PPI-DLR-0821",
        consortium_number_of_apps_last_1_year=440,
        employer_name="Valley Irrigation Supply",
        employer_street_addr="740 Canal Rd",
        employer_city="Fresno",
        employer_state="CA",
        employer_zip="93706",
        employer_phone="559-555-0121",
        occupation="PARTS MANAGER",
        application_date="2026-06-16",
        submitted_to_prod_at="2026-06-16T09:12:00Z",
        coborrower_name="Marisol Villanueva",
        coborrower_relationship_type="spouse",
    ),
    signals=signals(
        phone_reuse_distinct_ssn_count=4,
        email_reuse_distinct_ssn_count=2,
        address_reuse_distinct_ssn_count=4,
        ssn_days_in_system=2600,
        inquiries_in_2weeks=2,
        dealer_consortium_score=430,
        dealer_risk_score=400,
        phone_reuse_count_this_dealer=3,
        dealer_app_volume_24mo=920,
        dealer_apps_last_7d=8,
        ssn_previous_apps_all_time=5,
        ssn_previous_apps_90d=1,
        has_coborrower=True,
        coborr_distinct_primary_borrowers_count=2,
        credit_score_gap=38,
        coborrower_appears_as_borrower_count=1,
        employer_distinct_other_ssns_count=57,
        employer_other_ssns_count=57,
        employer_phone_distinct_ssn_count=57,
        employer_new_ssns_90d=4,
        ssn_distinct_employers_all_time=2,
        ssn_income_overstatement_percentage=3,
        loan_to_annual_income_ratio=0.43,
        phone_ssn_count=4,
        address_ssn_count_same_building=4,
        address_ssn_count_same_unit=4,
    ),
    context=context(
        phone_reuse_distinct_ssn_count=(
            "Normalized phone matches 4 hash_ssn values, ALL FOUR sharing the surname Villanueva "
            "and the same address key: the borrower, his spouse, and their two adult children. "
            "The household has had this number for 9 years."
        ),
        email_reuse_distinct_ssn_count=(
            "Normalized email matches 2 hash_ssn values: the borrower and his spouse, who share "
            "the household mailbox."
        ),
        address_reuse_distinct_ssn_count=(
            "Address key resolves to 4 hash_ssn values - the same 4 household members, all "
            "surname Villanueva, single-family home owned since 2011."
        ),
        ssn_days_in_system=(
            "First consortium sighting 2,600 days ago; 5 prior applications over 7 years, "
            "consistent name, DOB and address key throughout."
        ),
        inquiries_in_2weeks="2 inquiries in the trailing 14 days. Credit score 671.",
        dealer_consortium_score=(
            "Dealer scored 430 this month; 410-450 across the trailing 12 months, flat."
        ),
        dealer_risk_score="Lender-portfolio score 400 across 52 funded loans.",
        phone_reuse_count_this_dealer=(
            "3 other borrowers at this dealer share this normalized phone: the borrower's spouse "
            "and two adult children, on family applications in 2023, 2024 and 2025. All four share "
            "the surname and the address."
        ),
        dealer_app_volume_24mo=(
            "920 consortium applications in 24 months; consortium_number_of_apps_last_1_year is "
            "440, so the dealer is active. It is the family's local dealership, 2 miles away."
        ),
        dealer_apps_last_7d=(
            "8 applications at this dealer in the last 7 days: 8 distinct hash_ssn values, 8 "
            "distinct phones, 8 distinct addresses."
        ),
        ssn_previous_apps_all_time=(
            "5 prior applications on this hash_ssn over 7 years, 4 at this same dealer. No EPD, no "
            "999, all funded loans current or paid."
        ),
        ssn_previous_apps_90d=(
            "1 prior application in the trailing 90 days, 84 days ago - the spouse's vehicle, on "
            "which this borrower was the co-borrower."
        ),
        has_coborrower=(
            "Co-borrower present. Source precedence resolved the row from the primary co-borrower "
            "table: shares the borrower's surname, address key and phone; relationship type on "
            "file is spouse. Married 26 years."
        ),
        coborr_distinct_primary_borrowers_count=(
            "The co-borrower is linked to 2 primary borrowers: this borrower, and their adult "
            "daughter on a 2024 application. Both share her surname and address."
        ),
        credit_score_gap=(
            "Borrower 671, co-borrower 709. A 38-point gap, well under the 100-point piggybacking "
            "threshold."
        ),
        coborrower_appears_as_borrower_count=(
            "The co-borrower was the primary borrower once, 84 days ago, on her own vehicle - with "
            "this borrower as her co-borrower. A spousal role swap on two household vehicles."
        ),
        borrower_appears_as_coborrower_count=(
            "This hash_ssn appeared as co-borrower once, 84 days ago, on his spouse's application."
        ),
        employer_distinct_other_ssns_count=(
            "57 distinct hash_ssn values cite Valley Irrigation Supply over 6 years. Verifiable "
            "address, city, state, ZIP and listed line."
        ),
        employer_other_ssns_count=(
            "57 other hash_ssn values share this employer, across 22 dealers and 1 state. One of "
            "the 57 shares the borrower's address - his adult son, also employed there."
        ),
        employer_phone_distinct_ssn_count=(
            "57 hash_ssn values share 559-555-0121, the firm's published line."
        ),
        employer_new_ssns_90d=(
            "4 new hash_ssn values cited this employer in the last 90 days against 57 all-time."
        ),
        ssn_distinct_employers_all_time=(
            "2 employers all-time: Valley Irrigation Supply for 11 years, one prior for 6."
        ),
        ssn_income_overstatement_percentage="Stated 61,000 vs verified 59,500 = 3% overstatement.",
        loan_to_annual_income_ratio="Loan 26,300 against stated annual income 61,000 = 0.43.",
        phone_ssn_count=(
            "4 hash_ssn values on this normalized phone - the four Villanueva household members, "
            "same surname, same address."
        ),
        address_ssn_count_same_building=(
            "4 hash_ssn values at this address: the four household members. Single-family home."
        ),
        address_ssn_count_same_unit=(
            "4 hash_ssn values at this unit - the same four household members, all surname "
            "Villanueva."
        ),
    ),
    alerts=(
        Alert(
            alert_id=58,
            finding_text=(
                "Borrower phone number is shared with other consortium identities; all of them "
                "share the borrower's surname and address."
            ),
            underlying_behavior="shared household phone",
        ),
        Alert(
            alert_id=59,
            finding_text=(
                "Phone number appears on more than one identity record in the trailing twelve "
                "months - members of the same household."
            ),
            underlying_behavior="shared household phone",
        ),
        Alert(
            alert_id=60,
            finding_text=(
                "Phone number matches other applicants at the same dealer; those applicants are "
                "the borrower's spouse and two adult children."
            ),
            underlying_behavior="shared household phone",
        ),
        Alert(
            alert_id=61,
            finding_text=(
                "Phone number is associated with more than two distinct applicant identities - "
                "four household members over nine years."
            ),
            underlying_behavior="shared household phone",
        ),
        Alert(
            alert_id=62,
            finding_text=(
                "Phone number recurs across multiple applications at a single dealership; the "
                "dealership is the household's local dealer two miles away."
            ),
            underlying_behavior="shared household phone",
        ),
        Alert(
            alert_id=63,
            finding_text=(
                "Phone number links applicants who also share an address; the shared address is "
                "the family home."
            ),
            underlying_behavior="shared household phone",
        ),
        Alert(
            alert_id=64,
            finding_text=(
                "Phone number connects a group of applicant identities; every member of the group "
                "shares the surname Villanueva."
            ),
            underlying_behavior="shared household phone",
        ),
        Alert(
            alert_id=65,
            finding_text=(
                "Applicant belongs to a network of identities linked by a shared telephone number "
                "and a shared address - a single household."
            ),
            underlying_behavior="shared household phone",
        ),
        Alert(
            alert_id=72,
            finding_text=(
                "Co-borrower on this application has previously been a primary borrower; she is "
                "the borrower's spouse of twenty-six years."
            ),
            underlying_behavior="shared household phone",
        ),
        Alert(
            alert_id=110,
            finding_text=(
                "Borrower and co-borrower share a telephone number and an address, and have "
                "exchanged primary and co-borrower roles across two household vehicles."
            ),
            underlying_behavior="shared household phone",
        ),
        Alert(
            alert_id=147,
            finding_text=(
                "Selling dealer has funded several applications sharing one telephone number; the "
                "applications are the four members of one family over three years."
            ),
            underlying_behavior="shared household phone",
        ),
    ),
    expected=expected_block(
        overall="LOW RISK",
        recommendation="APPROVE",
        bands=dict(_ALL_LOW),
        flags=dict(_NO_FLAGS),
        justifications=dict(
            identity=(
                "IDEN-018",
                "Alert volume alone does NOT indicate risk. Multiple alerts from the same "
                "underlying behavior = one signal.",
            ),
            dealer=(
                "DLR-014",
                "phone_reuse_count_this_dealer: Borrowers sharing phones at same dealer. 0-1 is "
                "normal. 2+ with high fraud scores is concerning for coordination.",
            ),
            straw=(
                "STRW-015",
                "Family relationships (spouse, parent, child): Generally benign unless supported "
                "by role-switching + dealer clustering + income disproportion combined.",
            ),
            employment=_EMP_LOW,
            income=_INC_LOW,
            synthetic=(
                "SYN-011",
                "phone_ssn_count: 1-2 normal (household/family). 3+ with unrelated identities "
                "warrants investigation.",
            ),
            bustout=(
                "BST-024",
                "Alert volume alone does NOT necessarily indicate material fraud risk, as multiple "
                "alerts may originate from the same underlying behavior or weak heuristic "
                "indicators.",
            ),
            rings=(
                "RNG-023",
                "Alert volume alone does NOT necessarily indicate material fraud risk, as multiple "
                "alerts may originate from the same underlying behavior or weak heuristic "
                "indicators.",
            ),
        ),
        overall_justification=_MSTR_LOW,
        stipulations_expected=False,
        top_risk_factors_expected=False,
    ),
    notes=(
        "This is also the 'all signals fire from ONE underlying behavior' record: "
        "phone_reuse_distinct_ssn_count, phone_ssn_count, phone_reuse_count_this_dealer, "
        "address_reuse_distinct_ssn_count, address_ssn_count_same_unit, "
        "address_ssn_count_same_building, email_reuse_distinct_ssn_count, "
        "coborrower_appears_as_borrower_count, borrower_appears_as_coborrower_count and "
        "coborr_distinct_primary_borrowers_count are all elevated by the single fact that four "
        "relatives share one house and one phone number. Ten signals, one concern.",
        "phone_ssn_count = 4 is above SYN-011's '1-2 normal' band, and "
        "phone_reuse_count_this_dealer "
        "= 3 is above DLR-014's '0-1 is normal'. Both bands are qualified - SYN-011 by 'with "
        "unrelated identities', DLR-014 by 'with high fraud scores' - and neither qualifier holds: "
        "all four identities share a surname and an address.",
        "fraud_score 598 sits in the customer's medium band. A counting-based decision rule would "
        "escalate this record on 11 alerts and a medium score; the customer's rules require LOW.",
    ),
)

FP_SCENARIOS: Final[tuple[Scenario, ...]] = (
    APP_1006,
    APP_1007,
    APP_1008,
    APP_1009,
    APP_1010,
)

__all__ = ["FP_SCENARIOS"]
