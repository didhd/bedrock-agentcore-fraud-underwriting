"""
The master adjudication contract, as the customer's prompt defines it.

WHY THIS IS A PYDANTIC MODEL AND NOT A DICT: ``master_agent_prompt.txt`` ends in a
``REQUIRED JSON FORMAT`` block that is a wire contract, not a suggestion. It fixes
the key set (21), the key order, two string enums, eight integer flags, a
1200-character ceiling per summary, and four cross-field rules. Downstream, those
keys become columns in ``SNOWFLAKE_INTELLIGENCE.PUBLIC.UNDERWRITING_REPORT_OUTPUT``.
The previous revision of this port emitted 22 keys (it prepended
``application_id``) and validated none of the rules, because synthesis was
``str(result)`` wrapped in ``except: pass``. Every rule below is therefore stated
once, here, as an executable assertion:

  * MSTR-013  exactly 21 keys in exactly this order, ``master_summary`` first
  * MSTR-014  ``overall_risk_decision`` in {LOW RISK, MEDIUM RISK, HIGH RISK}
  * MSTR-015  ``recommendation_decision`` in {APPROVE, REVIEW AND APPLY
              STIPULATIONS, DECLINE}
  * MSTR-016  the eight ``*_flag`` values are integers 0 or 1
  * MSTR-010  no nulls anywhere - empty strings instead
  * MSTR-011  every summary is under 1200 characters
  * MSTR-020  no emojis
  * MSTR-036  ``recommended_stipulations`` is populated only for the
              REVIEW AND APPLY STIPULATIONS outcome
  * MSTR-033  REVIEW AND APPLY STIPULATIONS applies only to MEDIUM RISK
  * MSTR-037  MEDIUM/HIGH RISK carries a numbered list of 3-4 factors
  * MSTR-038  LOW RISK carries an empty ``top_risk_factors``

``application_id`` is deliberately absent. It is not in the customer's contract;
it belongs to the transport envelope that carries the adjudication (see
``agents.synthesize.SynthesisResult`` and the AgentCore entrypoint), so
``extra="forbid"`` rejects it rather than letting it drift back in as key #1.

No field is ``Optional``. A non-required field drops out of the JSON schema's
``required`` array, and the schema generated from this model is what constrains
the model at inference time via ``structured_output_model`` - so one ``Optional``
would silently make one of the 21 keys omissible.

THREE RISK VOCABULARIES, ONE OPEN QUESTION
------------------------------------------
The customer's artifacts do not agree on the middle risk band or on the
recommendation set, and this port does not get to pick quietly:

  1. ``master_agent_prompt.txt``  - MEDIUM RISK; 3 recommendations. **Implemented
     here**, because it is the executable artifact: it is the string the
     production agent is running today.
  2. ``Agent_Underwriting_Process_Guide (1).pdf`` sections 5.1/5.2 - POSSIBLE
     RISK; 4 recommendations including MANUAL REVIEW. Narrative documentation.
  3. The eight specialist prompts - LOW RISK / POSSIBLE RISK / HIGH RISK, i.e.
     they agree with the PDF, not with the master. So a band rename happens
     between the analysis layer and the adjudication layer, by design or by
     drift.

:data:`PDF_TO_PROMPT_ENUM_MAP` records the mapping that can be made
mechanically. ``MANUAL REVIEW`` has no counterpart in the executable prompt and
is therefore *not* mapped - :func:`map_pdf_enum` raises for it. That is a real
DDL question for ``UNDERWRITING_REPORT_OUTPUT`` and needs a customer ruling, not
a default.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from prompts.loader import DOMAINS

__all__ = [
    "ADJUDICATION_FIELD_ORDER",
    "ADJUDICATION_KEYS",
    "Adjudication",
    "AdjudicationContractError",
    "EnumConflictError",
    "ENUM_CONFLICT",
    "MASTER_RISK_VALUES",
    "MAX_SUMMARY_CHARS",
    "PDF_RECOMMENDATION_VALUES",
    "PDF_RISK_VALUES",
    "PDF_TO_PROMPT_ENUM_MAP",
    "RECOMMENDATION_VALUES",
    "SPECIALIST_RISK_VALUES",
    "STIPULATIONS_RECOMMENDATION",
    "STIPULATION_VOCABULARY",
    "SUMMARY_FIELDS",
    "TOP_RISK_FACTOR_MAX_ITEMS",
    "TOP_RISK_FACTOR_MIN_ITEMS",
    "UNMAPPED_PDF_VALUES",
    "contains_emoji",
    "map_pdf_enum",
    "parse_top_risk_factors",
]


class AdjudicationContractError(ValueError):
    """A rule from the customer's master prompt was violated."""


class EnumConflictError(ValueError):
    """A PDF enum value has no counterpart in the executable prompt."""


# ---------------------------------------------------------------------------
# Enums, all three vocabularies
# ---------------------------------------------------------------------------

#: MSTR-014. The executable master prompt's risk bands.
MASTER_RISK_VALUES: Final[tuple[str, str, str]] = ("LOW RISK", "MEDIUM RISK", "HIGH RISK")

#: MSTR-015. The executable master prompt's recommendations.
RECOMMENDATION_VALUES: Final[tuple[str, str, str]] = (
    "APPROVE",
    "REVIEW AND APPLY STIPULATIONS",
    "DECLINE",
)

#: The one recommendation that gates ``recommended_stipulations`` (MSTR-036).
STIPULATIONS_RECOMMENDATION: Final[str] = "REVIEW AND APPLY STIPULATIONS"

#: The eight specialists' own calibration bands - a THIRD vocabulary. They match
#: the PDF's mid-band name ("POSSIBLE RISK"), not the master's ("MEDIUM RISK").
SPECIALIST_RISK_VALUES: Final[tuple[str, str, str]] = ("LOW RISK", "POSSIBLE RISK", "HIGH RISK")

#: Agent_Underwriting_Process_Guide section 5.1.
PDF_RISK_VALUES: Final[tuple[str, str, str]] = ("LOW RISK", "POSSIBLE RISK", "HIGH RISK")

#: Agent_Underwriting_Process_Guide section 5.2 - four values, not three.
PDF_RECOMMENDATION_VALUES: Final[tuple[str, str, str, str]] = (
    "APPROVE",
    "APPROVE WITH STIPULATIONS",
    "MANUAL REVIEW",
    "DECLINE",
)

#: PDF value -> executable-prompt value, for the mappings that are mechanical.
#: ``MANUAL REVIEW`` is absent on purpose; see :data:`UNMAPPED_PDF_VALUES`.
PDF_TO_PROMPT_ENUM_MAP: Final[dict[str, dict[str, str]]] = {
    "overall_risk_decision": {
        "LOW RISK": "LOW RISK",
        "POSSIBLE RISK": "MEDIUM RISK",
        "HIGH RISK": "HIGH RISK",
    },
    "recommendation_decision": {
        "APPROVE": "APPROVE",
        "APPROVE WITH STIPULATIONS": STIPULATIONS_RECOMMENDATION,
        "DECLINE": "DECLINE",
    },
}

#: PDF values with no counterpart in the executable prompt. Mapping one of these
#: is a business decision, so it raises instead.
UNMAPPED_PDF_VALUES: Final[dict[str, tuple[str, ...]]] = {
    "overall_risk_decision": (),
    "recommendation_decision": ("MANUAL REVIEW",),
}

#: The open question, in the shape the UI and the customer deck both render.
ENUM_CONFLICT: Final[dict[str, Any]] = {
    "question": (
        "Which artifact defines the authoritative risk band and recommendation "
        "enums for UNDERWRITING_REPORT_OUTPUT?"
    ),
    "implemented": {
        "artifact": "docs/master_agent_prompt.txt",
        "why": "executable - this is the string the production agent runs today",
        "overall_risk_decision": list(MASTER_RISK_VALUES),
        "recommendation_decision": list(RECOMMENDATION_VALUES),
    },
    "not_implemented": {
        "artifact": "docs/Agent_Underwriting_Process_Guide (1).pdf sections 5.1, 5.2",
        "why": "narrative documentation; conflicts with the prompt",
        "overall_risk_decision": list(PDF_RISK_VALUES),
        "recommendation_decision": list(PDF_RECOMMENDATION_VALUES),
    },
    "third_vocabulary": {
        "artifact": "the eight docs/<domain>_agent_prompt.txt CALIBRATION blocks",
        "why": (
            "the specialists classify in LOW/POSSIBLE/HIGH RISK, so the mid-band is "
            "renamed between the analysis layer and the adjudication layer"
        ),
        "values": list(SPECIALIST_RISK_VALUES),
    },
    "unresolvable_without_a_ruling": (
        "MANUAL REVIEW exists only in the PDF. It is not a rename of any prompt "
        "value, so it cannot be mapped mechanically."
    ),
    "blast_radius": (
        "the enum strings are persisted values in UNDERWRITING_REPORT_OUTPUT and "
        "are read by downstream workflow routing"
    ),
}

#: PDF section 5.3's stipulation examples. Recorded because they are the
#: controlled vocabulary downstream routing keys off; NOT enforced here, because
#: the executable prompt places no constraint on the string.
STIPULATION_VOCABULARY: Final[tuple[str, ...]] = (
    "Proof of income",
    "Employment verification",
    "Proof of residence",
    "Driver's license validation",
    "Identity document review",
    "Dealer escalation review",
    "Fraud operations escalation",
    "Bank statement verification",
)


def map_pdf_enum(field: str, pdf_value: str) -> str:
    """Translate a PDF enum value into the executable prompt's vocabulary.

    Raises :class:`EnumConflictError` for a value the prompt has no counterpart
    for, rather than picking one.
    """
    if field not in PDF_TO_PROMPT_ENUM_MAP:
        raise KeyError(f"no PDF/prompt enum mapping for field {field!r}")
    mapping = PDF_TO_PROMPT_ENUM_MAP[field]
    if pdf_value in mapping:
        return mapping[pdf_value]
    if pdf_value in UNMAPPED_PDF_VALUES.get(field, ()):
        raise EnumConflictError(
            f"{pdf_value!r} appears only in Agent_Underwriting_Process_Guide "
            f"section 5.2 and has no counterpart in master_agent_prompt.txt's "
            f"{field}. Mapping it is a customer decision: "
            f"{ENUM_CONFLICT['unresolvable_without_a_ruling']}"
        )
    raise KeyError(f"{pdf_value!r} is not a documented PDF value for {field!r}")


# ---------------------------------------------------------------------------
# Field-level limits
# ---------------------------------------------------------------------------

#: MSTR-011: "Keep summaries concise and under 1200 characters." Strictly under,
#: so the declarative ceiling is 1199.
MAX_SUMMARY_CHARS: Final[int] = 1200

#: MSTR-037: "the top 3-4 specific factors driving the risk".
TOP_RISK_FACTOR_MIN_ITEMS: Final[int] = 3
TOP_RISK_FACTOR_MAX_ITEMS: Final[int] = 4

Summary = Annotated[str, StringConstraints(max_length=MAX_SUMMARY_CHARS - 1)]

_NUMBERED_ITEM = re.compile(r"^\d+\.\s+\S")

# Emoji and dingbat blocks. Deliberately narrow: it must catch a pictograph but
# never an en dash, ellipsis or arrow, all of which appear in ordinary
# underwriting prose.
_EMOJI = re.compile(
    "["
    "\U0001f000-\U0001faff"  # pictographs, emoticons, transport, symbols, extended-A
    "\U00002600-\U000027bf"  # misc symbols + dingbats
    "\U00002b00-\U00002bff"  # misc symbols and arrows (heavy round-tipped etc.)
    "\U0001f1e6-\U0001f1ff"  # regional indicators (flags)
    "️"  # variation selector-16
    "]"
)


def contains_emoji(text: str) -> bool:
    """Whether ``text`` carries an emoji or dingbat codepoint (MSTR-020)."""
    return bool(_EMOJI.search(text))


def parse_top_risk_factors(text: str) -> list[str]:
    """Split ``top_risk_factors`` into its numbered items.

    The customer's illustration is semicolon-separated on one line
    ("1. ...; 2. ...; 3. ..."). Newline-separated numbered lists are accepted as
    the same structure; nothing else is.
    """
    return [part.strip() for part in re.split(r"[;\n]+", text) if part.strip()]


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------

#: The 21 keys, derived from the prompt loader's domain order so this file and
#: the master prompt's input order cannot drift apart. Guarded below against the
#: model's own declared field order.
ADJUDICATION_FIELD_ORDER: Final[tuple[str, ...]] = (
    "master_summary",
    "overall_risk_decision",
    "recommendation_decision",
    "recommended_stipulations",
    "top_risk_factors",
    *(name for domain in DOMAINS for name in (f"{domain}_summary", f"{domain}_flag")),
)

#: Alias for callers that think of them as JSON keys rather than model fields.
ADJUDICATION_KEYS: Final[tuple[str, ...]] = ADJUDICATION_FIELD_ORDER

#: The nine free-text fields the 1200-character rule applies to.
SUMMARY_FIELDS: Final[tuple[str, ...]] = (
    "master_summary",
    *(f"{domain}_summary" for domain in DOMAINS),
)

_FLAG_FIELDS: Final[tuple[str, ...]] = tuple(f"{domain}_flag" for domain in DOMAINS)


class Adjudication(BaseModel):
    """The master agent's strict-JSON output: 21 required keys, exact order.

    Field declaration order IS the wire order - ``model_dump()`` and
    ``model_dump_json()`` both emit in declaration order, and MSTR-013 fixes it.
    Do not reorder, do not add a field, do not make one Optional.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    master_summary: Summary
    overall_risk_decision: Literal["LOW RISK", "MEDIUM RISK", "HIGH RISK"]
    recommendation_decision: Literal["APPROVE", "REVIEW AND APPLY STIPULATIONS", "DECLINE"]
    recommended_stipulations: str
    top_risk_factors: str

    identity_summary: Summary
    identity_flag: Literal[0, 1]

    dealer_summary: Summary
    dealer_flag: Literal[0, 1]

    straw_summary: Summary
    straw_flag: Literal[0, 1]

    employment_summary: Summary
    employment_flag: Literal[0, 1]

    income_summary: Summary
    income_flag: Literal[0, 1]

    synthetic_summary: Summary
    synthetic_flag: Literal[0, 1]

    bustout_summary: Summary
    bustout_flag: Literal[0, 1]

    rings_summary: Summary
    rings_flag: Literal[0, 1]

    @model_validator(mode="before")
    @classmethod
    def _reject_nulls_and_booleans(cls, data: Any) -> Any:
        """MSTR-010 (no nulls) and MSTR-016 (flags are integers, not booleans).

        Runs before coercion because ``Literal[0, 1]`` would otherwise accept
        JSON ``true``/``false`` as 1/0, and a ``null`` would surface only as a
        generic type error that reads like a schema bug rather than a contract
        breach.
        """
        if not isinstance(data, dict):
            return data
        for key, value in data.items():
            if value is None:
                raise AdjudicationContractError(
                    f"{key} is null. MSTR-010: 'Use empty strings instead of null "
                    f"values.' Every one of the {len(ADJUDICATION_FIELD_ORDER)} keys "
                    "must always exist and must never be null."
                )
            if isinstance(value, bool) and key in _FLAG_FIELDS:
                raise AdjudicationContractError(
                    f"{key}={value!r} is a boolean. MSTR-016: the fraud-type flags "
                    "are integers 0 or 1."
                )
        return data

    @model_validator(mode="after")
    def _enforce_customer_rules(self) -> Adjudication:
        risk = self.overall_risk_decision
        recommendation = self.recommendation_decision

        # MSTR-020: no emojis, in any field.
        for field in ADJUDICATION_FIELD_ORDER:
            value = getattr(self, field)
            if isinstance(value, str) and contains_emoji(value):
                raise AdjudicationContractError(
                    f"{field} contains an emoji. MSTR-020: 'Do not use emojis.'"
                )

        # MSTR-036 / MASTER-08: stipulations belong to exactly one outcome.
        stipulations = self.recommended_stipulations.strip()
        if recommendation == STIPULATIONS_RECOMMENDATION:
            if not stipulations:
                raise AdjudicationContractError(
                    "recommendation_decision is 'REVIEW AND APPLY STIPULATIONS' but "
                    "recommended_stipulations is empty. MSTR-036: 'Populate "
                    "recommended_stipulations only for REVIEW AND APPLY STIPULATIONS "
                    "outcomes.'"
                )
        elif stipulations:
            raise AdjudicationContractError(
                f"recommendation_decision is {recommendation!r} but "
                "recommended_stipulations is populated. MSTR-036 restricts it to "
                "REVIEW AND APPLY STIPULATIONS outcomes; every other outcome must "
                "use an empty string."
            )

        # MSTR-033: the stipulations outcome exists only for the middle band.
        if recommendation == STIPULATIONS_RECOMMENDATION and risk != "MEDIUM RISK":
            raise AdjudicationContractError(
                f"recommendation_decision is 'REVIEW AND APPLY STIPULATIONS' with "
                f"overall_risk_decision {risk!r}. MSTR-033: 'REVIEW AND APPLY "
                "STIPULATIONS only for MEDIUM RISK applications requiring "
                "verification.'"
            )

        # MSTR-037 / MSTR-038: top_risk_factors is band-conditional.
        factors = self.top_risk_factors.strip()
        if risk == "LOW RISK":
            if factors:
                raise AdjudicationContractError(
                    "overall_risk_decision is 'LOW RISK' but top_risk_factors is "
                    "populated. MSTR-038: 'If overall_risk_decision is LOW RISK, set "
                    "top_risk_factors to an empty string.'"
                )
        else:
            items = parse_top_risk_factors(factors)
            if not (TOP_RISK_FACTOR_MIN_ITEMS <= len(items) <= TOP_RISK_FACTOR_MAX_ITEMS):
                raise AdjudicationContractError(
                    f"overall_risk_decision is {risk!r} so top_risk_factors must be a "
                    f"numbered list of {TOP_RISK_FACTOR_MIN_ITEMS}-"
                    f"{TOP_RISK_FACTOR_MAX_ITEMS} factors (MSTR-037); found "
                    f"{len(items)} item(s) in {factors!r}."
                )
            bad = [item for item in items if not _NUMBERED_ITEM.match(item)]
            if bad:
                raise AdjudicationContractError(
                    "top_risk_factors items must be numbered like '1. <factor>' "
                    f"(MSTR-037); these are not: {bad!r}."
                )

        return self


# Guard: the declared field order IS the customer's key order. If DOMAINS ever
# changes, or a field is reordered, this fails at import rather than emitting a
# silently wrong report row.
_declared = tuple(Adjudication.model_fields)
if _declared != ADJUDICATION_FIELD_ORDER:
    raise AdjudicationContractError(
        "Adjudication field order drifted from MSTR-013.\n"
        f"  declared: {_declared}\n"
        f"  required: {ADJUDICATION_FIELD_ORDER}"
    )
if len(ADJUDICATION_FIELD_ORDER) != 21:
    raise AdjudicationContractError(
        f"MSTR-013 requires exactly 21 keys; got {len(ADJUDICATION_FIELD_ORDER)}."
    )
if "application_id" in Adjudication.model_fields:
    raise AdjudicationContractError(
        "application_id is not part of the customer's 21-key contract; it belongs "
        "to the transport envelope."
    )
