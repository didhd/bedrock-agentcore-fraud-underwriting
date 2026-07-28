"""
Point Predictive's nine production prompts, loaded byte-for-byte from disk.

Import from here, never from a module that stores prompt text in a Python
literal. See prompts/loader.py for why the sha256 manifest exists.
"""

from __future__ import annotations

from prompts.loader import (
    AGENT_NAMES,
    DOMAINS,
    FRANCIS_ORCHESTRATION_PROMPT,
    FRANCIS_RESPONSE_FORMAT,
    MANIFEST,
    MASTER_ANALYSIS_PLACEHOLDERS,
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

__all__ = [
    "AGENT_NAMES",
    "DOMAINS",
    "FRANCIS_ORCHESTRATION_PROMPT",
    "FRANCIS_RESPONSE_FORMAT",
    "MANIFEST",
    "MASTER_ANALYSIS_PLACEHOLDERS",
    "MASTER_SYNTHESIS_PROMPT",
    "ORCHESTRATION_NOTES",
    "RESPONSE_FORMATS",
    "SOURCES",
    "SPECIALIST_PROMPTS",
    "VERBATIM_DIR",
    "PromptIntegrityError",
    "read_verbatim",
    "verify_integrity",
]
