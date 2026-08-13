"""
Fidelity gate for the customer's nine production prompts.

WHY: the customer's own engineer wrote all nine prompts and will diff this port
against her originals. These tests are the mechanism that makes paraphrase a
build failure rather than a review opinion. They assert three separate things:

  1. the packaged copies are byte-identical to the customer documents in docs/,
     so a customer prompt update fails CI instead of silently diverging;
  2. the sha256 manifest matches, so an edit to a packaged copy fails too;
  3. the load-bearing contract strings (persona sentences, OUTPUT titles, the
     master's 8 placeholders and 21 JSON keys, Francis's identity and RETRO
     deflection) survive the banner-stripping loader intact -- and the invented
     "ROLE:/RISK:/FINDINGS:" format the earlier port used is absent everywhere.

Run:  python -m pytest tests/test_prompt_fidelity.py -q
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prompts import loader  # noqa: E402
from prompts.loader import (  # noqa: E402
    AGENT_NAMES,
    DOMAINS,
    FRANCIS_ORCHESTRATION_PROMPT,
    FRANCIS_RESPONSE_FORMAT,
    MANIFEST,
    MASTER_SYNTHESIS_PROMPT,
    ORCHESTRATION_NOTES,
    RESPONSE_FORMATS,
    SOURCES,
    SPECIALIST_PROMPTS,
    VERBATIM_DIR,
    PromptIntegrityError,
    read_verbatim,
    verify_integrity,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Exact persona sentence per specialist (IDEN-001, DLR-001, STRW-001, EMP-001,
# INC-001, SYN-001, BST-001, RNG-001). Note straw omits "Fraud" and bustout
# hyphenates "Bust-Out" -- both are the customer's wording, not typos.
PERSONA_SENTENCES = {
    "identity": "You are an Identity Fraud Detection Agent.",
    "dealer": "You are a Dealer Fraud Detection Agent.",
    "straw": "You are a Straw Borrower Detection Agent.",
    "employment": "You are an Employment Fraud Detection Agent.",
    "income": "You are an Income Fraud Detection Agent.",
    "synthetic": "You are a Synthetic Identity Fraud Detection Agent.",
    "bustout": "You are a Bust-Out Fraud Detection Agent.",
    "rings": "You are a Fraud Ring Detection Agent.",
}

# OUTPUT titles (IDEN-019, INC-020, EMP-021, DLR-023, STRW-019, SYN-021).
# bustout and rings are deliberately absent: BST-025 and RNG-024 record that
# those two prompts define NO title, and inventing one breaks the 1:1 port.
OUTPUT_TITLES = {
    "identity": "Identity Fraud Risk Analysis",
    "income": "Income Fraud Risk Analysis",
    "employment": "Employment Fraud Risk Analysis",
    "dealer": "Dealer Fraud Risk Analysis",
    "straw": "Straw Borrower Fraud Risk Analysis",
    "synthetic": "Synthetic Identity Fraud Risk Analysis",
}

UNTITLED_DOMAINS = ("bustout", "rings")

# The two agents' only output contract, and the wording differs by one word
# ("the"). Pinning both spellings keeps a well-meaning normalization out.
RETURN_ONLY_RULES = {
    "bustout": "- Return only the final analysis.",
    "rings": "- Return only final analysis.",
}

# MSTR-013: exactly these keys, in exactly this order.
MASTER_JSON_KEYS = (
    "master_summary",
    "overall_risk_decision",
    "recommendation_decision",
    "recommended_stipulations",
    "top_risk_factors",
    "identity_summary",
    "identity_flag",
    "dealer_summary",
    "dealer_flag",
    "straw_summary",
    "straw_flag",
    "employment_summary",
    "employment_flag",
    "income_summary",
    "income_flag",
    "synthetic_summary",
    "synthetic_flag",
    "bustout_summary",
    "bustout_flag",
    "rings_summary",
    "rings_flag",
)

# The invented format the earlier port forced every agent into:
#   RISK: <LOW RISK | POSSIBLE RISK | HIGH RISK>
#   FINDINGS: <2-5 sentences ...>
#   ROLE: Identity Fraud Detection Agent. ...
# Anchored to line start because the customer's own text legitimately contains
# "LOW RISK:", "HIGH RISK:" and "OVERALL RISK:" as calibration-band labels.
INVENTED_MARKER = re.compile(r"^[ \t]*(ROLE|RISK|FINDINGS)[ \t]*:", re.MULTILINE)


def _verbatim_files() -> list[str]:
    return sorted(p.name for p in VERBATIM_DIR.glob("*.txt"))


def _all_loaded_prompts() -> dict[str, str]:
    loaded = {f"specialist:{d}": SPECIALIST_PROMPTS[d] for d in DOMAINS}
    loaded["master"] = MASTER_SYNTHESIS_PROMPT
    loaded["francis:orchestration"] = FRANCIS_ORCHESTRATION_PROMPT
    loaded["francis:response"] = FRANCIS_RESPONSE_FORMAT
    loaded.update({f"notes:{d}": ORCHESTRATION_NOTES[d] for d in DOMAINS})
    loaded.update({f"response:{d}": RESPONSE_FORMATS[d] for d in DOMAINS})
    return loaded


# --------------------------------------------------------------------------
# 1. packaged copies vs the customer documents
# --------------------------------------------------------------------------


def test_all_eighteen_customer_documents_are_packaged():
    """9 agent prompts + the master orchestration doc + 8 per-agent docs."""
    assert len(_verbatim_files()) == 18
    for domain in DOMAINS:
        assert f"{domain}_agent_prompt.txt" in _verbatim_files()
    assert "master_agent_prompt.txt" in _verbatim_files()
    assert "master_agent_orchestration_062026.txt" in _verbatim_files()


@pytest.mark.parametrize("name", _verbatim_files())
def test_verbatim_copy_is_byte_identical_to_docs_source(name):
    source = os.path.join(REPO_ROOT, SOURCES[name])
    if not os.path.isfile(source):
        # SKIP, not fail. `docs/` holds the customer's ORIGINALS and is gitignored, so it is
        # absent from every clone and from the delivered material -- which made this test fail
        # 18 times on a customer's first deploy and read as "prompt fidelity is broken".
        #
        # Fidelity is still enforced there: `test_manifest_hash_matches` checks all 18 sha256
        # digests and does not depend on the originals. THIS test is the second source -- it
        # catches a manifest regenerated to cover a drifted file, which a hash alone cannot.
        # So it must run wherever the originals exist and stay quiet where they do not.
        #
        # Deliberately NOT fixed by copying verbatim/ -> docs/. That would make the diff
        # tautological and delete the only check that regenerating the manifest cannot defeat.
        pytest.skip(
            f"{SOURCES[name]} is absent. It is the customer's original, gitignored and not "
            "shipped; the sha256 gate in test_manifest_hash_matches still applies."
        )
    with open(source, "rb") as handle:
        expected = handle.read()
    with open(VERBATIM_DIR / name, "rb") as handle:
        actual = handle.read()
    assert actual == expected, (
        f"{name} diverged from {SOURCES[name]}. Re-copy it byte-for-byte "
        "(shutil.copyfile) and regenerate prompts/manifest.json."
    )


@pytest.mark.parametrize("name", _verbatim_files())
def test_manifest_hash_matches(name):
    with open(VERBATIM_DIR / name, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    assert digest == MANIFEST[name], f"{name} sha256 drifted"


def test_manifest_covers_exactly_the_packaged_files():
    manifest = json.loads((loader.MANIFEST_PATH).read_text(encoding="utf-8"))
    assert sorted(manifest["files"]) == _verbatim_files()
    for name, entry in manifest["files"].items():
        assert entry["bytes"] == (VERBATIM_DIR / name).stat().st_size


def test_verify_integrity_passes():
    verify_integrity()


def test_loader_raises_naming_the_file_on_drift(tmp_path, monkeypatch):
    """The integrity check is the anti-paraphrase mechanism, so prove it fires."""
    name = "identity_agent_prompt.txt"
    tampered = (VERBATIM_DIR / name).read_text(encoding="utf-8").replace(
        "You are an Identity Fraud Detection Agent.",
        "ROLE: Identity Fraud Detection Agent",
    )
    (tmp_path / name).write_text(tampered, encoding="utf-8")
    monkeypatch.setattr(loader, "VERBATIM_DIR", tmp_path)
    with pytest.raises(PromptIntegrityError) as excinfo:
        read_verbatim("identity_agent_prompt")
    assert name in str(excinfo.value)
    assert MANIFEST[name] in str(excinfo.value)


def test_loader_raises_on_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "VERBATIM_DIR", tmp_path)
    with pytest.raises(PromptIntegrityError) as excinfo:
        read_verbatim("rings_agent_prompt")
    assert "rings_agent_prompt.txt" in str(excinfo.value)


# --------------------------------------------------------------------------
# 2. the loader strips the banner and nothing else
# --------------------------------------------------------------------------


@pytest.mark.parametrize("domain", DOMAINS)
def test_specialist_prompt_starts_with_its_persona_sentence(domain):
    prompt = SPECIALIST_PROMPTS[domain]
    assert prompt.splitlines()[0] == PERSONA_SENTENCES[domain]
    assert PERSONA_SENTENCES[domain] in prompt


@pytest.mark.parametrize("domain", DOMAINS)
def test_banner_and_extraction_header_are_stripped(domain):
    prompt = SPECIALIST_PROMPTS[domain]
    assert "POINT PREDICTIVE" not in prompt
    assert "Confidential Material" not in prompt
    assert "Extracted from CLEAN_MAIN_STREAM_FILE.sql" not in prompt
    assert AGENT_NAMES[domain] not in prompt  # header persona id is not spoken
    assert "====" not in prompt


@pytest.mark.parametrize("domain", DOMAINS)
def test_stripping_removes_only_the_header(domain):
    """Every non-header line of the source survives, in order, unchanged."""
    raw = read_verbatim(f"{domain}_agent_prompt")
    loaded = SPECIALIST_PROMPTS[domain]
    assert raw.rstrip("\n").endswith(loaded)
    header, _, remainder = raw.partition(loaded)
    assert remainder.strip() == ""
    header_lines = [line for line in header.splitlines() if line.strip()]
    assert len(header_lines) == 5  # (c) line, ====, # NAME, # Extracted, ====
    assert header_lines[0].startswith("©")


def test_master_prompt_banner_stripped_and_persona_intact():
    assert MASTER_SYNTHESIS_PROMPT.splitlines()[0] == (
        "You are a senior forensic underwriting analyst."
    )
    assert MASTER_SYNTHESIS_PROMPT.splitlines()[2] == (
        "Return your output as STRICT JSON only. No extra text."
    )
    assert "POINT PREDICTIVE" not in MASTER_SYNTHESIS_PROMPT
    assert "Extracted from" not in MASTER_SYNTHESIS_PROMPT


# --------------------------------------------------------------------------
# 3. per-agent output contracts
# --------------------------------------------------------------------------


@pytest.mark.parametrize("domain,title", sorted(OUTPUT_TITLES.items()))
def test_titled_agents_keep_their_exact_output_title(domain, title):
    assert f"- Title: {title}" in SPECIALIST_PROMPTS[domain]


@pytest.mark.parametrize("domain", UNTITLED_DOMAINS)
def test_untitled_agents_have_no_title_and_none_was_invented(domain):
    """BST-025 / RNG-024: these two prompts define no Title. Keep it that way."""
    prompt = SPECIALIST_PROMPTS[domain]
    assert not re.search(r"^[ \t]*-?[ \t]*Title[ \t]*:", prompt, re.MULTILINE)
    assert "Title" not in prompt
    assert "Risk Analysis" not in prompt
    for title in OUTPUT_TITLES.values():
        assert title not in prompt


@pytest.mark.parametrize("domain,rule", sorted(RETURN_ONLY_RULES.items()))
def test_untitled_agents_keep_their_return_only_rule(domain, rule):
    assert rule in SPECIALIST_PROMPTS[domain]


def test_return_only_wording_differs_between_bustout_and_rings():
    """bustout says "the final analysis", rings says "final analysis"."""
    assert "Return only the final analysis" in SPECIALIST_PROMPTS["bustout"]
    assert "Return only the final analysis" not in SPECIALIST_PROMPTS["rings"]


@pytest.mark.parametrize("domain", DOMAINS)
def test_calibration_bands_survive(domain):
    prompt = SPECIALIST_PROMPTS[domain]
    for band in ("LOW RISK", "POSSIBLE RISK", "HIGH RISK"):
        assert band in prompt


@pytest.mark.parametrize(
    "domain",
    ["identity", "income", "employment", "dealer", "straw", "synthetic"],
)
def test_titled_agents_keep_the_4000_character_cap(domain):
    assert "max 4000 characters" in SPECIALIST_PROMPTS[domain]


@pytest.mark.parametrize("domain", UNTITLED_DOMAINS)
def test_untitled_agents_define_no_length_cap(domain):
    assert "4000" not in SPECIALIST_PROMPTS[domain]
    assert "sentences" not in SPECIALIST_PROMPTS[domain]


@pytest.mark.parametrize("domain", DOMAINS)
def test_context_validation_rule_survives(domain):
    assert "_context" in SPECIALIST_PROMPTS[domain]


# --------------------------------------------------------------------------
# 4. master synthesis contract
# --------------------------------------------------------------------------


def test_master_placeholders_present_in_spec_order():
    found = re.findall(r"\{(\w+_analysis)\}", MASTER_SYNTHESIS_PROMPT)
    assert found == [f"{domain}_analysis" for domain in DOMAINS]
    assert found == [
        "identity_analysis",
        "dealer_analysis",
        "straw_analysis",
        "employment_analysis",
        "income_analysis",
        "synthetic_analysis",
        "bustout_analysis",
        "rings_analysis",
    ]


def test_master_placeholders_sit_under_bare_uppercase_headings():
    """MSTR-004: "IDENTITY:" not "IDENTITY ANALYSIS:"."""
    for domain in DOMAINS:
        assert (
            f"{domain.upper()}:\n{{{domain}_analysis}}" in MASTER_SYNTHESIS_PROMPT
        )
        assert f"{domain.upper()} ANALYSIS:" not in MASTER_SYNTHESIS_PROMPT


def test_master_section_rules_are_forty_hyphens():
    """MSTR-003 / MSTR-043."""
    rule = "-" * 40
    assert f"{rule}\nSPECIALIZED FRAUD ANALYSES\n{rule}" in MASTER_SYNTHESIS_PROMPT
    assert f"{rule}\nOUTPUT FORMAT - STRICT JSON\n{rule}" in MASTER_SYNTHESIS_PROMPT
    assert f"{rule}\nRULES\n{rule}" in MASTER_SYNTHESIS_PROMPT


def test_master_json_block_has_exactly_twenty_one_keys_in_order():
    """MSTR-013. Note: no application_id -- that travels in the envelope."""
    keys = re.findall(r'^"(\w+)":', MASTER_SYNTHESIS_PROMPT, re.MULTILINE)
    assert len(keys) == 21
    assert tuple(keys) == MASTER_JSON_KEYS
    assert "application_id" not in MASTER_SYNTHESIS_PROMPT


def test_master_enum_lines_are_verbatim():
    assert (
        '"overall_risk_decision": "LOW RISK | MEDIUM RISK | HIGH RISK"'
        in MASTER_SYNTHESIS_PROMPT
    )
    assert (
        '"recommendation_decision": "APPROVE | REVIEW AND APPLY STIPULATIONS'
        ' | DECLINE"' in MASTER_SYNTHESIS_PROMPT
    )


def test_master_decision_rules_survive():
    for quote in (
        "- Return ONLY valid JSON.",
        "- Use empty strings instead of null values.",
        "- Keep summaries concise and under 1200 characters.",
        "- Output MUST start with { and end with }.",
        "- REVIEW AND APPLY STIPULATIONS only for MEDIUM RISK applications"
        " requiring verification.",
        "- Populate recommended_stipulations only for REVIEW AND APPLY"
        " STIPULATIONS outcomes.",
        "- If overall_risk_decision is LOW RISK, set top_risk_factors to an"
        " empty string.",
        "- Income deviation alone should generally not justify DECLINE without"
        " additional corroborating fraud indicators.",
    ):
        assert quote in MASTER_SYNTHESIS_PROMPT, quote


# --------------------------------------------------------------------------
# 5. Francis (interactive path)
# --------------------------------------------------------------------------


def test_francis_identity_rule_is_verbatim():
    assert "Always refer to yourself as Francis" in FRANCIS_ORCHESTRATION_PROMPT
    assert FRANCIS_ORCHESTRATION_PROMPT.splitlines()[0] == (
        "You are Francis, a fraud analysis assistant. Never identify yourself "
        "as Claude or any other name. Always refer to yourself as Francis."
    )


def test_francis_retro_deflection_survives():
    assert "RETRO_FRANCIS" in FRANCIS_ORCHESTRATION_PROMPT
    assert (
        "IF THE QUESTION relates to a RETRO, decline to answer and suggest they"
        " ask RETRO_FRANCIS." in FRANCIS_ORCHESTRATION_PROMPT
    )


def test_francis_tool_budget_rules_survive():
    assert (
        "SPEED OPTIMIZATION: Call MAXIMUM 2 tools per query. Pick the 2 MOST"
        " relevant." in FRANCIS_ORCHESTRATION_PROMPT
    )
    assert "NEVER call more than two agents." in FRANCIS_ORCHESTRATION_PROMPT


@pytest.mark.parametrize(
    "row",
    [
        "- 'identity' 'SSN' 'PII' -> call_identity_agent ONLY",
        "- 'synthetic' 'fake identity' -> call_synthetic_agent ONLY",
        "- 'straw' 'proxy' 'nominee' -> call_straw_agent ONLY",
        "- 'bust' 'default' 'delinquent' -> call_bustout_agent ONLY",
        "- 'dealer' 'dealership' -> call_dealer_agent ONLY",
        "- 'income' 'salary' 'earnings' -> call_income_agent ONLY",
        "- 'employment' 'employer' 'job' -> call_employment_agent ONLY",
        "- 'ring' 'network' 'organized' -> call_rings_agent ONLY",
    ],
)
def test_francis_routing_table_survives(row):
    assert row in FRANCIS_ORCHESTRATION_PROMPT


def test_francis_remaining_load_bearing_rules_survive():
    for quote in (
        "Execute immediately. No announcements.",
        "Call ONLY call_rings_agent (covers coordinated patterns)",
        "OR call_identity_agent (most common fraud type).",
        "Use Cortex Analyst and the semantic view only if one of the agents"
        " cannot provide an answer to the request.",
        "If you're asked about telegram messages, be sure to reference the"
        " telegram_messages logical source in the semantic view.",
        "Note for fake employers: anything like disability, retired, social"
        " security, pension, etc. are not fake employers.",
        "Do not provide recommendations for loan response unless you are"
        " prompted for recommendations.",
    ):
        assert quote in FRANCIS_ORCHESTRATION_PROMPT, quote


def test_francis_response_format_survives():
    assert FRANCIS_RESPONSE_FORMAT.splitlines() == [
        "Synthesize results into:",
        "## EXECUTIVE SUMMARY Risk level (HIGH/MEDIUM/LOW) + key findings in"
        " 2-3 sentences.",
        "## ANALYSIS Connect findings if multiple domains queried.",
        "Then include ###RECOMMENDATIONS section but ONLY do so if the user"
        " requests recommendations.",
        "Keep response concise.",
    ]


# --------------------------------------------------------------------------
# 6. no regression to the invented format
# --------------------------------------------------------------------------


def test_invented_marker_regex_actually_matches_the_old_format():
    """Canary: without this, the absence assertions below could be vacuous."""
    old_format = (
        "ROLE: Identity Fraud Detection Agent.\n"
        "  RISK: <LOW RISK | POSSIBLE RISK | HIGH RISK>\n"
        "  FINDINGS: <2-5 sentences>\n"
    )
    assert sorted(INVENTED_MARKER.findall(old_format)) == [
        "FINDINGS",
        "RISK",
        "ROLE",
    ]


@pytest.mark.parametrize("label", sorted(_all_loaded_prompts()))
def test_no_loaded_prompt_uses_the_invented_format(label):
    prompt = _all_loaded_prompts()[label]
    found = INVENTED_MARKER.findall(prompt)
    assert not found, (
        f"{label} contains invented format marker(s) {found}; the customer's "
        "prompts use prose sections, never ROLE:/RISK:/FINDINGS: lines"
    )
    assert "ROLE:" not in prompt
    assert "FINDINGS:" not in prompt


@pytest.mark.parametrize("label", sorted(_all_loaded_prompts()))
def test_risk_colon_appears_only_as_a_calibration_band_label(label):
    """"RISK:" is legal in the customer text -- but only as "<BAND> RISK:"."""
    for match in re.finditer(r"RISK[ \t]*:", _all_loaded_prompts()[label]):
        prefix = _all_loaded_prompts()[label][: match.start()]
        line_head = prefix.rsplit("\n", 1)[-1]
        assert line_head.rstrip().endswith(
            ("LOW", "POSSIBLE", "HIGH", "MEDIUM", "OVERALL")
        ), f"{label}: bare 'RISK:' marker at offset {match.start()}"


# --------------------------------------------------------------------------
# 7. persona identifiers and per-domain orchestration notes
# --------------------------------------------------------------------------


def test_agent_names_match_the_prompt_file_headers():
    for domain in DOMAINS:
        header = f"# {AGENT_NAMES[domain]} Agent Prompt"
        assert header in read_verbatim(f"{domain}_agent_prompt")
    assert AGENT_NAMES["master"] == "MASTER_FRAUD_AGENT"
    assert "# MASTER FRAUD AGENT Prompt (Senior Forensic Underwriting Analyst)" in (
        read_verbatim("master_agent_prompt")
    )
    assert AGENT_NAMES["francis"] == "Francis"
    assert set(AGENT_NAMES) == set(DOMAINS) | {"master", "francis"}


def test_agent_names_are_the_customer_persona_ids():
    assert AGENT_NAMES == {
        "identity": "ISAAC_IDENTITY",
        "dealer": "DIANA_DEALER",
        "straw": "SAMMY_STRAW",
        "employment": "EMILY_EMPLOYMENT",
        "income": "IRIS_INCOME",
        "synthetic": "SYLVIA_SYNTHETIC",
        "bustout": "BRUCE_BUSTOUT",
        "rings": "RENEE_RINGS",
        "master": "MASTER_FRAUD_AGENT",
        "francis": "Francis",
    }


@pytest.mark.parametrize("domain", DOMAINS)
def test_specialist_agent_names_are_literal_customer_strings(domain):
    """The eight ids are quoted from the customer's files, not coined here."""
    assert AGENT_NAMES[domain] in read_verbatim(f"{domain}_agent_prompt")


def test_master_agent_name_is_derived_from_the_customer_header():
    """MASTER_FRAUD_AGENT is this port's id; pin what it is derived from.

    The customer never writes "MASTER_FRAUD_AGENT" anywhere, so it must not be
    presented as her string. It is the header's words joined by underscores.
    """
    header_words = "MASTER FRAUD AGENT"
    assert f"# {header_words} Prompt" in read_verbatim("master_agent_prompt")
    assert AGENT_NAMES["master"] == "_".join(header_words.split())
    assert "MASTER_FRAUD_AGENT" not in read_verbatim("master_agent_prompt")


def test_francis_keeps_the_customers_exact_casing():
    """Regression pin: "Francis", never "FRANCIS".

    The customer writes "You are Francis" in mixed case. The ONLY uppercase
    FRANCIS in any of her documents is inside RETRO_FRANCIS -- the separate retro
    system Francis is instructed to deflect to. Upper-casing this id would name
    the one system this agent must never answer as.
    """
    doc = read_verbatim("master_agent_orchestration_062026")
    assert AGENT_NAMES["francis"] == "Francis"
    assert re.search(r"(?<![A-Za-z0-9_])Francis(?![A-Za-z0-9_])", doc)
    # every uppercase FRANCIS in the customer's text belongs to RETRO_FRANCIS
    assert not re.search(r"(?<![A-Za-z0-9_])FRANCIS(?![A-Za-z0-9_])", doc)
    assert [m.group(0) for m in re.finditer(r"\w*FRANCIS\w*", doc)] == [
        "RETRO_FRANCIS"
    ]


def test_loader_rejects_an_uppercased_francis(monkeypatch):
    """Prove the import-time guard fires rather than merely being asserted."""
    monkeypatch.setitem(loader.AGENT_NAMES, "francis", "FRANCIS")
    with pytest.raises(PromptIntegrityError) as excinfo:
        loader._verify_agent_names()
    assert "RETRO_FRANCIS" in str(excinfo.value)


def test_loader_rejects_a_persona_id_absent_from_the_customer_text(monkeypatch):
    monkeypatch.setitem(loader.AGENT_NAMES, "identity", "IVAN_IDENTITY")
    with pytest.raises(PromptIntegrityError) as excinfo:
        loader._verify_agent_names()
    assert "identity_agent_prompt.txt" in str(excinfo.value)


def _contains_loosely(haystack: str, needle: str) -> bool:
    """Substring match tolerant of the customer's own irregular whitespace.

    Used ONLY for asserting a block is present. straw_orchestration_instructions
    contains "PLEASE FOLLOW ALL OF THESE RULES BELOW  BEFORE GETTING  RESULTS."
    with doubled spaces and "Identity-related   Alerts:" with three. That is how
    the customer typed it, so the loader must not normalize it -- the test flexes
    instead of the prompt.
    """
    pattern = r"\s+".join(re.escape(part) for part in needle.split())
    return re.search(pattern, haystack) is not None


@pytest.mark.parametrize("domain", DOMAINS)
def test_orchestration_notes_carry_the_general_rules_block(domain):
    notes = ORCHESTRATION_NOTES[domain]
    for marker in (
        "PLEASE FOLLOW ALL OF THESE RULES BELOW BEFORE GETTING RESULTS.",
        "Fraud scores between 0-299 are low.",
        "Identity-related Alerts:",
        "Limit to 5 SQL calls per request.",
    ):
        assert _contains_loosely(notes, marker), f"{domain}: missing {marker!r}"
    assert "delink_lender_table" in notes


def test_general_rules_heading_is_absent_from_the_employment_doc():
    """Customer-source quirk, pinned so nobody "fixes" it into the copy.

    Seven of the eight docs label the block "General Rules:"; employment jumps
    straight from the fake-email note to "PLEASE FOLLOW ALL OF THESE RULES...".
    """
    assert "General Rules" not in ORCHESTRATION_NOTES["employment"]
    for domain in set(DOMAINS) - {"employment"}:
        assert _contains_loosely(ORCHESTRATION_NOTES[domain], "General Rules:")


def test_straw_doc_keeps_the_customers_doubled_spaces():
    """Whitespace inside a block is content, not formatting. Do not collapse it."""
    assert (
        "PLEASE FOLLOW ALL OF THESE RULES BELOW  BEFORE GETTING  RESULTS."
        in ORCHESTRATION_NOTES["straw"]
    )
    assert "Identity-related   Alerts:" in ORCHESTRATION_NOTES["straw"]


@pytest.mark.parametrize("domain", DOMAINS)
def test_orchestration_notes_are_banner_stripped_but_not_reworded(domain):
    notes = ORCHESTRATION_NOTES[domain]
    assert "POINT PREDICTIVE" not in notes
    assert not notes.startswith("orchestration:")
    assert "orchestration: |" not in notes
    assert "response: |" not in notes
    # de-indent only: the customer's own line content is untouched
    raw = read_verbatim(loader._ORCHESTRATION_FILES[domain])
    for line in notes.splitlines():
        if line.strip():
            assert line.strip() in raw


@pytest.mark.parametrize("domain", DOMAINS)
def test_per_domain_response_format_leads_with_a_one_line_verdict(domain):
    assert RESPONSE_FORMATS[domain].startswith("Lead with a one-line verdict:")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
