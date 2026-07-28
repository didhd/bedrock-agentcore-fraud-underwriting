"""
Verbatim loader for the customer's nine production agent prompts.

WHY THIS EXISTS: the prompts in ``prompts/verbatim/`` are the literal strings the
customer runs in production (extracted from their CLEAN_MAIN_STREAM_FILE.sql and
their nine orchestration documents). Every threshold, calibration band, output
title and guardrail sentence in them is load-bearing and is diffed against the
original by the engineer who wrote them. Hand-transcribing or "improving" that
text is the single highest-visibility fidelity failure available to this port.

So no Python module in this repo is allowed to *contain* prompt text. Prompt text
is read from disk and every read is checked against a sha256 recorded in
``manifest.json``. If a byte moves, import fails and names the file. Paraphrase
stops being a judgement call and becomes a build error.

The only editing this module performs is removing the file header the extraction
tool prepended (the two-line ``©``/rule confidentiality banner and the
``# <NAME> Agent Prompt`` / ``# Extracted from ...`` lines) and de-indenting the
YAML block scalars in the orchestration documents. No rewording, no reordering,
no normalization of the customer's whitespace inside a block.
"""

from __future__ import annotations

import hashlib
import json
import os.path
import re
from pathlib import Path
from typing import Final

_HERE: Final = Path(__file__).resolve().parent
VERBATIM_DIR: Final = _HERE / "verbatim"
MANIFEST_PATH: Final = _HERE / "manifest.json"

# Domain order is the customer's master-prompt input order (MSTR-004):
# IDENTITY, DEALER, STRAW, EMPLOYMENT, INCOME, SYNTHETIC, BUSTOUT, RINGS.
# It is NOT alphabetical and NOT the PDF's order; downstream synthesis relies on
# it, so it is defined once here.
DOMAINS: Final[tuple[str, ...]] = (
    "identity",
    "dealer",
    "straw",
    "employment",
    "income",
    "synthetic",
    "bustout",
    "rings",
)

# RESULT_TABLE.AGENT_NAME values. The eight specialist ids are LITERAL customer
# strings lifted from each prompt file's own header line ("# ISAAC_IDENTITY Agent
# Prompt") and are re-verified against those headers at import time by
# _verify_agent_names(); downstream queries filter on them.
#
# The last two entries are NOT literal customer strings, and the distinction is
# recorded here so nobody later quotes them back to the customer as hers:
#   * "MASTER_FRAUD_AGENT" is this port's identifier, derived from the header
#     "# MASTER FRAUD AGENT Prompt (Senior Forensic Underwriting Analyst)" by
#     joining its words with underscores for consistency with the eight above.
#     _verify_agent_names() pins that derivation.
#   * "Francis" is the customer's exact casing from
#     master_agent_orchestration_062026 ("You are Francis, ..."). It is NOT
#     upper-cased to FRANCIS: the only uppercase FRANCIS the customer ever
#     writes is inside RETRO_FRANCIS, a *separate* retro system this agent is
#     required to deflect to, so an all-caps spelling here would collide with
#     the one name Francis must never answer as.
AGENT_NAMES: Final[dict[str, str]] = {
    "identity": "ISAAC_IDENTITY",
    "dealer": "DIANA_DEALER",
    "straw": "SAMMY_STRAW",
    "employment": "EMILY_EMPLOYMENT",
    "income": "IRIS_INCOME",
    "synthetic": "SYLVIA_SYNTHETIC",
    "bustout": "BRUCE_BUSTOUT",
    "rings": "RENEE_RINGS",
    "master": "MASTER_FRAUD_AGENT",
    # Interactive-path persona. "Francis" appears only in
    # master_agent_orchestration_062026, never in master_agent_prompt.txt, so it
    # is an alias of the same master agent rather than a tenth agent.
    "francis": "Francis",
}

# The master prompt header words that AGENT_NAMES["master"] is derived from.
_MASTER_HEADER_WORDS: Final = "MASTER FRAUD AGENT"

# Per-domain orchestration document. Filenames are the customer's own and are
# deliberately inconsistent ("..._orchestration_062026" vs
# "..._orchestration_instructions_062026" vs "bustout_fraudbot_...").
_ORCHESTRATION_FILES: Final[dict[str, str]] = {
    "identity": "identity_orchestration_062026",
    "dealer": "dealer_orchestration_instructions_062026",
    "straw": "straw_orchestration_instructions_062026",
    "employment": "employment_orchestration_062026",
    "income": "income_orchestration_instructions_062026",
    "synthetic": "synthetic_orchestration_instructions_062026",
    "bustout": "bustout_fraudbot_orchestration_062026",
    "rings": "rings_orchestration_instructions_062026",
}

_MASTER_ORCHESTRATION_FILE: Final = "master_agent_orchestration_062026"

# The eight placeholders the master prompt carries, in the order they appear in
# it. Exposed so synthesis can validate its substitution map without re-parsing
# the prompt; this module never calls str.format on the prompt itself.
MASTER_ANALYSIS_PLACEHOLDERS: Final[tuple[str, ...]] = tuple(
    f"{d}_analysis" for d in DOMAINS
)


class PromptIntegrityError(RuntimeError):
    """A verbatim prompt file no longer matches its recorded sha256."""


# Leading extraction header: the "© POINT PREDICTIVE" line, the "====" rule
# lines, the "# <NAME> Agent Prompt" / "# Extracted from ..." comment lines, and
# the blank lines between them. Matching is anchored to the top of the file and
# stops at the first real content line, so an interior "#" or "====" (none exist
# today, but the customer may add one) is never touched.
_HEADER_LINE: Final = re.compile(r"^(?:©.*|=+|#.*|\s*)$")

# YAML keys in the orchestration documents. Only `response:` and `orchestration:`
# carry block scalars; a bare `instructions:` line is a boundary. The customer's
# indentation of these keys is inconsistent across the nine files (0, 1 and 2
# spaces all occur), hence the leading \s*.
_YAML_KEY: Final = re.compile(
    r"^[ \t]*(instructions|response|orchestration)[ \t]*:[ \t]*(\|[ \t]*)?$"
)


_MANIFEST_FILES: Final[dict[str, dict[str, object]]] = json.loads(
    MANIFEST_PATH.read_text(encoding="utf-8")
)["files"]

MANIFEST: Final[dict[str, str]] = {
    name: str(entry["sha256"]) for name, entry in _MANIFEST_FILES.items()
}
#: verbatim filename -> repo-relative path of the customer document it came from.
#: tests/test_prompt_fidelity.py diffs the pair so a customer prompt update fails
#: CI instead of silently diverging.
SOURCES: Final[dict[str, str]] = {
    name: str(entry["source"]) for name, entry in _MANIFEST_FILES.items()
}


def read_verbatim(stem: str) -> str:
    """Return a verbatim file's exact text, after checking its sha256.

    ``stem`` is the filename without ``.txt``. Raises PromptIntegrityError naming
    the file if the bytes on disk drifted from the manifest.

    Every call re-reads and re-hashes: these files are small (the largest is 18 KB)
    and are read a couple of dozen times at import only. Caching would mean a file
    edited after import keeps serving stale bytes, which is exactly the silent
    drift the manifest exists to prevent. Callers that want the module-level
    constants get them for free; nothing in this repo reads in a hot loop.
    """
    name = f"{stem}.txt"
    path = VERBATIM_DIR / name
    if not path.is_file():
        raise PromptIntegrityError(
            f"{name}: missing from {VERBATIM_DIR}; re-copy it from the customer "
            f"source ({SOURCES.get(name, 'docs/')}) and refresh manifest.json"
        )
    raw = path.read_bytes()
    expected = MANIFEST.get(name)
    if expected is None:
        raise PromptIntegrityError(
            f"{name}: not recorded in manifest.json; every verbatim prompt must "
            "carry a sha256 so paraphrase cannot pass review"
        )
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected:
        raise PromptIntegrityError(
            f"{name}: sha256 drifted from customer source. "
            f"expected {expected}, found {actual}. "
            "Either restore the file byte-for-byte from "
            f"{SOURCES.get(name, 'docs/')} or, if the customer really changed "
            "the prompt, re-run prompts/manifest.json generation and re-read the "
            "output contracts in tests/test_prompt_fidelity.py."
        )
    return raw.decode("utf-8")


def verify_integrity() -> None:
    """Check every manifest entry. Raises PromptIntegrityError on first drift."""
    for name in MANIFEST:
        read_verbatim(name.removesuffix(".txt"))


def _strip_extraction_header(text: str) -> str:
    lines = text.split("\n")
    start = 0
    while start < len(lines) and _HEADER_LINE.match(lines[start]):
        start += 1
    return "\n".join(lines[start:]).strip()


def _dedent_block(lines: list[str]) -> str:
    """Remove the longest whitespace prefix common to all non-blank lines.

    Nothing else. Relative indentation, tabs, trailing whitespace and blank-line
    placement inside the customer's block scalar are preserved.
    """
    prefixes = [
        line[: len(line) - len(line.lstrip(" \t"))] for line in lines if line.strip()
    ]
    common = os.path.commonprefix(prefixes) if prefixes else ""
    if common:
        lines = [
            line[len(common) :] if line.startswith(common) else line for line in lines
        ]
    return "\n".join(lines).strip("\n")


def _yaml_blocks(text: str) -> dict[str, str]:
    """Split an orchestration document into its de-indented block scalars.

    The documents are RTF exports of Snowflake Cortex Agent YAML, so they are not
    valid YAML (inconsistent key indentation, block bodies less indented than
    their key). A real YAML parser rejects several of them; this reads the block
    boundaries the way the customer's own files are laid out.
    """
    lines = text.split("\n")
    keys = [
        (i, match.group(1), bool(match.group(2)))
        for i, line in enumerate(lines)
        if (match := _YAML_KEY.match(line))
    ]
    blocks: dict[str, str] = {}
    for index, (line_no, name, is_scalar) in enumerate(keys):
        if not is_scalar:
            continue
        end = keys[index + 1][0] if index + 1 < len(keys) else len(lines)
        blocks[name] = _dedent_block(lines[line_no + 1 : end])
    return blocks


def load_specialist_prompt(domain: str) -> str:
    """The customer's verbatim system prompt for one specialist."""
    return _strip_extraction_header(read_verbatim(f"{domain}_agent_prompt"))


def load_orchestration_blocks(domain: str) -> dict[str, str]:
    """The ``response:`` and ``orchestration:`` blocks of a domain's doc."""
    return _yaml_blocks(read_verbatim(_ORCHESTRATION_FILES[domain]))


#: Verbatim TASK / RULES / SIGNAL INTERPRETATION / CALIBRATION / OUTPUT text of
#: the eight specialists, banner stripped. Nothing normalized, nothing reordered.
SPECIALIST_PROMPTS: Final[dict[str, str]] = {
    domain: load_specialist_prompt(domain) for domain in DOMAINS
}

#: The batch synthesizer's verbatim prompt. Still contains its eight
#: ``{<domain>_analysis}`` placeholders under bare uppercase headings in the
#: customer's order (IDENTITY, DEALER, STRAW, EMPLOYMENT, INCOME, SYNTHETIC,
#: BUSTOUT, RINGS) and the 21-key REQUIRED JSON FORMAT block. Substitution is the
#: caller's job -- formatting here would destroy the contract this file proves.
MASTER_SYNTHESIS_PROMPT: Final[str] = _strip_extraction_header(
    read_verbatim("master_agent_prompt")
)

_MASTER_BLOCKS: Final[dict[str, str]] = _yaml_blocks(
    read_verbatim(_MASTER_ORCHESTRATION_FILE)
)

#: Interactive path system prompt: Francis's identity rule, the RETRO ->
#: RETRO_FRANCIS deflection, the MAXIMUM-2-tools rule, the eight-row keyword
#: routing table, the general-query fallback, "Execute immediately. No
#: announcements.", the telegram_messages note, the fake-employer note and the
#: no-recommendations-unless-asked rule -- all verbatim.
FRANCIS_ORCHESTRATION_PROMPT: Final[str] = _MASTER_BLOCKS["orchestration"]

#: Interactive path response contract: ## EXECUTIVE SUMMARY / ## ANALYSIS and the
#: conditional ###RECOMMENDATIONS section.
FRANCIS_RESPONSE_FORMAT: Final[str] = _MASTER_BLOCKS["response"]

_DOMAIN_BLOCKS: Final[dict[str, dict[str, str]]] = {
    domain: load_orchestration_blocks(domain) for domain in DOMAINS
}

#: Per-domain verbatim orchestration guidance (semantic-layer governance, the
#: numbered General Rules, the alert-ID categories) used by the interactive path.
ORCHESTRATION_NOTES: Final[dict[str, str]] = {
    domain: blocks["orchestration"] for domain, blocks in _DOMAIN_BLOCKS.items()
}

#: Per-domain verbatim response contract from the same documents ("Lead with a
#: one-line verdict: ..."), used when a specialist answers an interactive query
#: rather than a batch application.
RESPONSE_FORMATS: Final[dict[str, str]] = {
    domain: blocks["response"] for domain, blocks in _DOMAIN_BLOCKS.items()
}


def _verify_agent_names() -> None:
    """Cross-check AGENT_NAMES against the persona id in each file's header.

    The header line is stripped before the prompt is used, so this is the only
    place the two representations can be reconciled. Runs at import: a customer
    rename shows up as an error here rather than as a wrong AGENT_NAME silently
    persisted on every result row.

    The two non-literal ids are pinned to their derivation rather than trusted:
    "master" must equal the underscore-joined master header words, and "francis"
    must appear in the orchestration document with exactly that casing and must
    NOT be the all-caps RETRO_FRANCIS deflection target.
    """
    for domain in DOMAINS:
        raw = read_verbatim(f"{domain}_agent_prompt")
        headers = [line for line in raw.split("\n")[:8] if line.startswith("# ")]
        expected = f"# {AGENT_NAMES[domain]} Agent Prompt"
        if expected not in headers:
            raise PromptIntegrityError(
                f"{domain}_agent_prompt.txt: persona header {headers!r} does not "
                f"contain AGENT_NAMES[{domain!r}] ({expected!r})"
            )

    master_raw = read_verbatim("master_agent_prompt")
    if f"# {_MASTER_HEADER_WORDS} Prompt" not in master_raw:
        raise PromptIntegrityError(
            "master_agent_prompt.txt: header no longer contains "
            f"'# {_MASTER_HEADER_WORDS} Prompt'; AGENT_NAMES['master'] is derived "
            "from those words and must be re-derived"
        )
    if AGENT_NAMES["master"] != "_".join(_MASTER_HEADER_WORDS.split()):
        raise PromptIntegrityError(
            f"AGENT_NAMES['master'] ({AGENT_NAMES['master']!r}) is not the "
            f"underscore-joined form of {_MASTER_HEADER_WORDS!r}"
        )

    francis = AGENT_NAMES["francis"]
    if francis == "FRANCIS":
        raise PromptIntegrityError(
            "AGENT_NAMES['francis'] must not be 'FRANCIS': the customer's only "
            "uppercase spelling is RETRO_FRANCIS, the separate retro system this "
            "agent is instructed to deflect to. Use her casing, 'Francis'."
        )
    orchestration = read_verbatim(_MASTER_ORCHESTRATION_FILE)
    if not re.search(
        rf"(?<![A-Za-z0-9_]){re.escape(francis)}(?![A-Za-z0-9_])", orchestration
    ):
        raise PromptIntegrityError(
            f"master_agent_orchestration_062026.txt: AGENT_NAMES['francis'] "
            f"({francis!r}) does not appear as a standalone word in the "
            "customer's orchestration text with that exact casing"
        )


_verify_agent_names()
