"""
The fixture set: 14 synthetic applications that actually exercise Point Predictive's contract.

The five records this replaces filled 20-30 of the 60 signals the nine prompts name, carried no
``alerts_fired`` at all, and followed the ``<signal>_context`` convention for 2 of ~20 context keys
— so an agent doing exactly what its prompt told it ("You MUST validate signals using relevant
context (columns that end in _context)") found nothing for most of them. Every fixture here carries
all 60 signals, all 60 paired ``_context`` rows with example data, and a descriptive
``alerts_fired`` array whose IDs resolve to real General Rule 3 categories.

Coverage is by decision branch, not by count. Eight records resolve LOW (XAG-003: "LOW should be
the most common outcome"), three MEDIUM, two HIGH:

  APP-1001  clean first-time buyer, thin file explained by age            LOW
  APP-1002  spousal co-borrower, shared phone, authorized-user tradelines LOW
  APP-1003  self-employed 31% overstatement, ambiguous middle band        MEDIUM
  APP-1004  synthetic identity + exact-unit ring + shell employer         HIGH
  APP-1005  bust-out velocity, 5 apps/7d, loan at 85% of income           MEDIUM
  APP-1006  employer address/phone/city/state/ZIP all null                LOW   (EMP-007)
  APP-1007  dealer consortium score 940, zero coordination evidence       LOW   (DLR-009)
  APP-1008  military-base employer, 3,100 SSNs, 34% dealer concentration  LOW   (EMP-017)
  APP-1009  dense 240-unit apartment, 96 SSNs at the building, 1 at unit   LOW   (RNG-013)
  APP-1010  eleven alerts, all from one shared household phone            LOW   (IDEN-018)
  APP-1011  no co-borrower and no alerts at all                           LOW   (STRW-005)
  APP-1012  co-borrower reused across 4 unrelated primaries at one dealer HIGH  (STRW-018)
  APP-1013  employer unverifiable AND clustered AND velocity-linked       MEDIUM (EMP-019)
  APP-1014  three independent weak concerns across three domains          MEDIUM (MSTR-021)

Six of them exist specifically to fail loudly if the implementation drifts toward false positives,
which is the failure mode the customer's prompts guard against most often.

Public surface: ``list_application_ids``, ``get_application``, ``build_payload``, ``domain_view``,
``expected_for``, ``iter_applications``. ``fixtures/_build.py``, ``_baseline.py``, ``_spec.py`` and
``_scenarios_*.py`` are the authoring tools behind ``python3 -m fixtures.regenerate``; nothing at
runtime imports them, and the tests read the committed JSON so a generator bug cannot hide behind
agreeing expectations.
"""

from __future__ import annotations

from fixtures.loader import (
    APPLICATIONS_DIR,
    FixtureError,
    build_payload,
    domain_view,
    expected_for,
    get_application,
    iter_applications,
    list_application_ids,
)

__all__ = [
    "APPLICATIONS_DIR",
    "FixtureError",
    "build_payload",
    "domain_view",
    "expected_for",
    "get_application",
    "iter_applications",
    "list_application_ids",
]
