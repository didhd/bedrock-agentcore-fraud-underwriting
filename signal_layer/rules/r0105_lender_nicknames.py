"""
General Rules 1.5, 16 and 19 — deterministic lender-name resolution.

Rule 1.5 ends with four words that decide the whole design: **"don't hallucinate."** The
customer is telling us that lender resolution must be a lookup, not an inference. A model
asked to recall "which lender is APCU?" will answer confidently and sometimes wrongly,
and a wrong lender silently re-points the entire analysis at another institution's
portfolio. So the nickname table lives here as data, unresolved input returns an explicit
unresolved result, and no code path guesses.

Three rules resolve together because they contradict each other if resolved separately:

  1.5  normalise (trim, strip *all* internal whitespace, uppercase) then look up the
       nickname table.
  19   '%CRESCENT%' — a nickname rule 1.5 itself lists — also matches FCRACRESCENTBANK.
       Rule 19 says Crescent Bank is CRESCENTBANK only. A wildcard resolver double-counts
       two institutions as one; this resolver pins the canonical name instead.
  16   PRIME and SUBPRIME are both Stellantis, so Stellantis resolves to *two* lender
       names and neither of PRIME/SUBPRIME means a credit tier here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping

from signal_layer.rules._source import rule_text

RULE_ID: Final = "1.5"
RULE_TEXT: Final = rule_text(RULE_ID)
RULE_16_ID: Final = "16"
RULE_16_TEXT: Final = rule_text(RULE_16_ID)
RULE_19_ID: Final = "19"
RULE_19_TEXT: Final = rule_text(RULE_19_ID)

#: The customer's MORE NICKNAMES table, all 12 rows, canonical NAME -> nicknames as the
#: document lists them. Two rows are one-to-many ("ARRA or CRESCENT", "DIGITAL, DCU"),
#: which is why the reverse index below has 14 keys for these 12 names.
NICKNAME_TABLE: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "ALABAMACU": ("ALABAMA",),
        "AMARILLONATIONALBANK": ("AMARILLO",),
        "AMERICAFIRSTCU": ("AMERICAFIRST",),
        # Self-mapping in the source (NAME and NICKNAME are both ARKANSASFCU). Kept as
        # the customer wrote it rather than "corrected" away.
        "ARKANSASFCU": ("ARKANSASFCU",),
        "ATLANTAPOSTALCU": ("APCU",),
        "CRESCENTBANK": ("ARRA", "CRESCENT"),
        "DIGITALFCU": ("DIGITAL", "DCU"),
        "FIRSTCOMMUNITYCU": ("FCCU",),
        "FOURSIGHTCAPITAL": ("FOURSIGHT",),
        "NEIGHBORSFCU": ("NEIGHBORS",),
        "CONSUMERPORTFOLIOSERVICES": ("CPS",),
        "POINTPREDICTIVE": ("PPI",),
    }
)

#: nickname -> canonical NAME. 14 keys for 12 canonical names; see NICKNAME_TABLE.
NICKNAME_INDEX: Final[Mapping[str, str]] = MappingProxyType(
    {nick: name for name, nicks in NICKNAME_TABLE.items() for nick in nicks}
)

#: The three worked examples stated inline in rule 1.5, as {input: expected pattern}.
#: Golden One and General Electric Credit Union are *not* in the nickname table — they
#: are pure normalisation examples, and note the second one truncates ("General Electric
#: Credit Union" -> GENERALELECTRIC, dropping CREDITUNION). That truncation is the
#: customer's, is not derivable from the normalisation rule, and so is encoded as data.
INLINE_EXAMPLES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "Golden One": "%GOLDENONE%",
        "General Electric Credit Union": "%GENERALELECTRIC%",
        "DCU": "%DIGITALFCU%",
    }
)

#: Canonical names the inline examples establish that are absent from the 12-row table.
INLINE_CANONICAL: Final[Mapping[str, str]] = MappingProxyType(
    {
        "GOLDENONE": "GOLDENONE",
        "GENERALELECTRIC": "GENERALELECTRIC",
        "GENERALELECTRICCREDITUNION": "GENERALELECTRIC",
    }
)

#: General Rule 16: both of these lender_name values are Stellantis.
STELLANTIS_LENDER_NAMES: Final[tuple[str, ...]] = ("PRIME", "SUBPRIME")

#: General Rule 19: the lender name that must NOT be returned for "Crescent Bank",
#: even though it matches the '%CRESCENT%' nickname rule 1.5 supplies.
CRESCENT_EXCLUDED_NAME: Final = "FCRACRESCENTBANK"
CRESCENT_CANONICAL_NAME: Final = "CRESCENTBANK"

_WHITESPACE: Final = re.compile(r"\s+")


def norm_lender(user_text: str | None) -> str:
    """
    Normalise a user-supplied lender name per General Rule 1.5.

    Verbatim: "any time a user asks about a lender with spaces in the name, you should
    remove those spaces and put the name into all upper case in order to find the
    correct lender".

    Pipeline: trim, strip ALL internal whitespace (not just the ones between words —
    tabs, newlines and doubled spaces from a pasted query too), uppercase.
    "  golden   one " -> "GOLDENONE".
    """
    if not user_text:
        return ""
    return _WHITESPACE.sub("", user_text).upper()


@dataclass(frozen=True)
class LenderResolution:
    """
    The outcome of resolving one user-supplied lender name.

    ``resolved`` is False for the "don't hallucinate" case: the caller must surface the
    unresolved input to the user rather than substitute a guess. ``lender_names`` is a
    tuple because General Rule 16 makes Stellantis resolve to two lender_name values;
    every caller therefore handles the multi-name case by construction.
    """

    query: str
    normalised: str
    resolved: bool
    lender_names: tuple[str, ...] = ()
    like_patterns: tuple[str, ...] = ()
    matched_via: str = ""
    note: str = ""

    @property
    def lender_name(self) -> str:
        """
        The single canonical lender name.

        Raises when the resolution is unresolved or multi-valued, so a caller cannot
        quietly collapse Stellantis's two names down to one.
        """
        if not self.resolved:
            raise LenderUnresolved(self.note or f"unresolved lender: {self.query!r}")
        if len(self.lender_names) != 1:
            raise LenderUnresolved(
                f"{self.query!r} resolves to {len(self.lender_names)} lender names "
                f"{self.lender_names}; use .lender_names"
            )
        return self.lender_names[0]


class LenderUnresolved(LookupError):
    """A lender name could not be resolved, and General Rule 1.5 forbids guessing."""


def _like(name: str) -> str:
    """Wrap a canonical name in the wildcards rule 1.5's examples show ('%GOLDENONE%')."""
    return f"%{name}%"


def resolve_lender(user_text: str | None) -> LenderResolution:
    """
    Resolve a user-supplied lender name to canonical lender_name value(s).

    General Rule 1.5, verbatim: "Also, any time a user asks about a lender with spaces
    in the name, you should remove those spaces and put the name into all upper case in
    order to find the correct lender...For example, Golden One should be '%GOLDENONE%',
    General Electric Credit Union should be '%GENERALELECTRIC%'..do your best to find
    the correct lender, but don't hallucinate. Here are some other nicknames: DCU is
    '%DIGITALFCU%', and 'ARRA' is for Crescent bank." (plus the 12-row MORE NICKNAMES
    table, reproduced in NICKNAME_TABLE.)

    Also applies General Rule 19 (Crescent Bank -> CRESCENTBANK, never
    FCRACRESCENTBANK) and General Rule 16 (Stellantis -> both PRIME and SUBPRIME).

    Resolution order, most specific first:
      1. exact canonical NAME from the table
      2. exact nickname (14 keys)
      3. the inline examples' canonical names
      4. Stellantis, and PRIME/SUBPRIME themselves (rule 16)
      5. "CRESCENTBANK"-family input (rule 19)
      6. unresolved — returns resolved=False, never a guess
    """
    query = user_text or ""
    key = norm_lender(user_text)
    if not key:
        return LenderResolution(
            query=query,
            normalised=key,
            resolved=False,
            note="empty lender name",
        )

    # (5) Rule 19 first among the Crescent forms: FCRACRESCENTBANK typed explicitly is
    # a deliberate request for that lender, but any *Crescent Bank* phrasing must pin
    # to CRESCENTBANK. Checked before the nickname table because "CRESCENTBANK" is a
    # canonical NAME and "CRESCENT" is a nickname, and both must land on the same
    # answer with the rule-19 note attached.
    if key == CRESCENT_EXCLUDED_NAME:
        return LenderResolution(
            query=query,
            normalised=key,
            resolved=True,
            lender_names=(CRESCENT_EXCLUDED_NAME,),
            like_patterns=(_like(CRESCENT_EXCLUDED_NAME),),
            matched_via="explicit-name",
            note=(
                "FCRACRESCENTBANK requested by exact name. General Rule 19: for "
                "questions about 'Crescent Bank', CRESCENTBANK is the correct lender."
            ),
        )
    if "CRESCENT" in key:
        return LenderResolution(
            query=query,
            normalised=key,
            resolved=True,
            lender_names=(CRESCENT_CANONICAL_NAME,),
            like_patterns=(_like(CRESCENT_CANONICAL_NAME),),
            matched_via="rule-19",
            note=RULE_19_TEXT,
        )

    # (4) Rule 16.
    if key == "STELLANTIS":
        return LenderResolution(
            query=query,
            normalised=key,
            resolved=True,
            lender_names=STELLANTIS_LENDER_NAMES,
            like_patterns=tuple(_like(n) for n in STELLANTIS_LENDER_NAMES),
            matched_via="rule-16",
            note=RULE_16_TEXT,
        )
    if key in STELLANTIS_LENDER_NAMES:
        return LenderResolution(
            query=query,
            normalised=key,
            resolved=True,
            lender_names=(key,),
            like_patterns=(_like(key),),
            matched_via="rule-16",
            note=(
                f"{key} is a Stellantis lender_name, not a credit tier. "
                f"General Rule 16: {RULE_16_TEXT}"
            ),
        )

    # (1) exact canonical name.
    if key in NICKNAME_TABLE:
        return LenderResolution(
            query=query,
            normalised=key,
            resolved=True,
            lender_names=(key,),
            like_patterns=(_like(key),),
            matched_via="canonical-name",
        )

    # (2) exact nickname.
    if key in NICKNAME_INDEX:
        canonical = NICKNAME_INDEX[key]
        return LenderResolution(
            query=query,
            normalised=key,
            resolved=True,
            lender_names=(canonical,),
            like_patterns=(_like(canonical),),
            matched_via="nickname",
        )

    # (3) the inline examples.
    if key in INLINE_CANONICAL:
        canonical = INLINE_CANONICAL[key]
        return LenderResolution(
            query=query,
            normalised=key,
            resolved=True,
            lender_names=(canonical,),
            like_patterns=(_like(canonical),),
            matched_via="inline-example",
        )

    # (6) No confident match. "do your best to find the correct lender, but don't
    # hallucinate" — so we stop here and hand the ambiguity back. Near-miss candidates
    # are offered for a clarifying question, never auto-selected.
    candidates = tuple(
        sorted(
            name
            for name in NICKNAME_TABLE
            if key in name or name.startswith(key[:4] or key)
        )
    )
    return LenderResolution(
        query=query,
        normalised=key,
        resolved=False,
        matched_via="",
        note=(
            f"No confident lender match for {query!r} (normalised {key!r}). "
            "General Rule 1.5: \"do your best to find the correct lender, but don't "
            "hallucinate.\" "
            + (
                f"Possible candidates to confirm with the user: {', '.join(candidates)}."
                if candidates
                else "No candidates; ask the user to confirm the lender."
            )
        ),
    )


def lender_like_pattern(user_text: str | None) -> str:
    """
    Resolve to a single '%NAME%' LIKE pattern, the form rule 1.5's examples emit.

    Raises LenderUnresolved rather than returning a guessed pattern or the raw user
    text — a bad pattern silently re-points the query at the wrong institution.
    """
    return _like(resolve_lender(user_text).lender_name)


__all__ = [
    "CRESCENT_CANONICAL_NAME",
    "CRESCENT_EXCLUDED_NAME",
    "INLINE_CANONICAL",
    "INLINE_EXAMPLES",
    "NICKNAME_INDEX",
    "NICKNAME_TABLE",
    "RULE_16_ID",
    "RULE_16_TEXT",
    "RULE_19_ID",
    "RULE_19_TEXT",
    "RULE_ID",
    "RULE_TEXT",
    "STELLANTIS_LENDER_NAMES",
    "LenderResolution",
    "LenderUnresolved",
    "lender_like_pattern",
    "norm_lender",
    "resolve_lender",
]
