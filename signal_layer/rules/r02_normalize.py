"""
General Rule 2 — string standardisation and the address key.

The customer states the purpose of this rule explicitly: **"Do something like that to
match addresses and remove false positives."** Address matching is what drives the
identity agent's shared-address findings, so a key that over-matches manufactures fraud
rings out of neighbours, and a key that under-matches misses real ones. Both failures are
invisible in the narrative the agent produces.

The rule is deliberately loose in the source ("STANDARDIZE / TAKE FIRST FEW CHARACTERS",
"something like '123_WALN_92101'"). Loose is unimplementable: two callers reading "first
few characters" build different keys and stop matching each other. So this module pins the
one worked example the customer gives — ``'123 Walnut St., 92101'`` -> ``'123_WALN_92101'``
— and derives every parameter from it: building number, first FOUR characters of the
street name uppercased, five-digit zip, underscore separated, street-type suffix dropped.
``ADDRESS_KEY_INTERPRETATION`` records which parts are the customer's and which are ours.
"""

from __future__ import annotations

import re
from types import MappingProxyType
from typing import Final, Mapping

from signal_layer.rules._source import rule_text

RULE_ID: Final = "2"
RULE_TEXT: Final = rule_text(RULE_ID)

#: The borrower string fields General Rule 2 names for UPPER(TRIM(x)) standardisation.
NORMALISED_FIELDS: Final[tuple[str, ...]] = (
    "borr_first_name",
    "borr_last_name",
    "occupation",
    "borr_email",
)

#: The customer's one worked example, and therefore this module's specification.
ADDRESS_KEY_EXAMPLE: Final[Mapping[str, str]] = MappingProxyType(
    {"123 Walnut St., 92101": "123_WALN_92101"}
)

#: How many characters of the street name the key keeps. The customer writes "TAKE FIRST
#: FEW CHARACTERS"; their example shows Walnut -> WALN, so "few" is four.
STREET_NAME_PREFIX_LEN: Final = 4

ADDRESS_KEY_INTERPRETATION: Final = (
    "General Rule 2 specifies the address key by example, not by algorithm: "
    "\"123 Walnut St., 92101, should become something like '123_WALN_92101'\". "
    "From the customer: extract the building/street number, standardise and take the "
    "first few characters of the street name, combine with the zip code. Derived here "
    "from their example and pinned so every caller builds an identical key: 'few' = 4 "
    "characters; the street name is uppercased; the street-type suffix (St./Ave./Blvd.) "
    "is dropped; the separator is '_'; the zip is the first 5 digits, zero-padded and "
    "kept as text so 02134 does not become 2134. Any change to these parameters "
    "silently changes which applications match."
)

#: Street-type suffixes dropped before the street name is read. The customer's example
#: drops "St." (WALN comes from Walnut, not from St), but never enumerates the set, so
#: this list is ours; it deliberately covers only unambiguous US suffixes and their
#: common abbreviations.
STREET_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        "ST", "STREET", "AVE", "AV", "AVENUE", "RD", "ROAD", "DR", "DRIVE", "BLVD",
        "BOULEVARD", "LN", "LANE", "CT", "COURT", "PL", "PLACE", "TER", "TERRACE",
        "WAY", "CIR", "CIRCLE", "PKWY", "PARKWAY", "HWY", "HIGHWAY", "TRL", "TRAIL",
        "SQ", "SQUARE", "LOOP", "ALY", "ALLEY", "PLZ", "PLAZA", "RUN", "PASS", "PATH",
        "CRES", "CRESCENT", "XING", "CROSSING",
    }
)

#: Directional prefixes/suffixes dropped so "123 N Walnut St" and "123 Walnut St" agree.
DIRECTIONALS: Final[frozenset[str]] = frozenset(
    {"N", "S", "E", "W", "NE", "NW", "SE", "SW",
     "NORTH", "SOUTH", "EAST", "WEST", "NORTHEAST", "NORTHWEST", "SOUTHEAST", "SOUTHWEST"}
)

#: Unit designators: everything from here on is a unit, not part of the street name.
#: Dropping them is what makes the multi-unit-building caveat in the rings and identity
#: docs tractable — 12 apartments in one building share an address key, and the
#: is_multi_unit_address signal is what distinguishes that from a real shared address.
UNIT_MARKERS: Final[frozenset[str]] = frozenset(
    {"APT", "APARTMENT", "UNIT", "STE", "SUITE", "FL", "FLOOR", "RM", "ROOM",
     "BLDG", "BUILDING", "TRLR", "SPC", "SPACE", "LOT", "PMB", "BOX"}
)

_PUNCT: Final = re.compile(r"[^0-9A-Za-z\s#/-]")
_WHITESPACE: Final = re.compile(r"\s+")
_LEADING_NUMBER: Final = re.compile(r"^(\d+)(?:[-/]\d+)?[A-Za-z]?\b")
_DIGITS: Final = re.compile(r"\d")


class AddressKeyError(ValueError):
    """An address could not be reduced to the key General Rule 2 requires."""


def norm_string(x: str | None) -> str:
    """
    Standardise a borrower string field: ``UPPER(TRIM(x))`` (General Rule 2).

    Verbatim: "When you compare borrower string fields of first name, last name,
    occupation, email, you should put them in upper case and trim so as to standardize
    the comparison."

    TRIM in Snowflake strips leading and trailing whitespace only, and that is what this
    reproduces — internal spacing is preserved, because "MARY ANN" and "MARYANN" are
    different names and collapsing them would create the false positives rule 2 exists
    to remove. (Contrast norm_lender() for General Rule 1.5, where the customer
    explicitly *does* require internal spaces removed.) None becomes "" so a missing
    field never equals another missing field by accident at the SQL layer, where NULL
    != NULL already holds.
    """
    if x is None:
        return ""
    return x.strip().upper()


def norm_email(x: str | None) -> str:
    """
    Standardise an email per General Rule 2 (it names email among the string fields).

    UPPER(TRIM(x)) exactly as the rule says — no local-part or domain rewriting. Gmail
    dot-and-plus normalisation is a real deduplication technique and is deliberately NOT
    applied: the customer did not ask for it, and it would silently merge distinct
    addresses in a fraud investigation.
    """
    return norm_string(x)


def norm_zip(zip_code: str | int | None) -> str:
    """
    Reduce a zip to the 5-digit form the address key uses (General Rule 2: "as well as
    the zip code").

    Handles ZIP+4 ("92101-4521" -> "92101") and integer-typed zips, where the leading
    zero has already been destroyed by the type: 2134 -> "02134". Returns "" when no
    digits are present. Kept as text throughout, because a zip is an identifier, not a
    quantity.
    """
    if zip_code is None:
        return ""
    if isinstance(zip_code, bool):
        return ""
    if isinstance(zip_code, int):
        text = f"{zip_code:05d}"
    else:
        text = str(zip_code)
    digits = "".join(_DIGITS.findall(text))
    if not digits:
        return ""
    if len(digits) < 5:
        return digits.zfill(5)
    return digits[:5]


def building_number(street: str | None) -> str:
    """
    Extract the building/street number General Rule 2 requires ("YOU SHOULD EXTRACT THE
    BUILDING/STREET NUMBER").

    Takes the leading numeric token: "123 Walnut St." -> "123". Hyphenated and fractional
    forms keep only the first part ("123-B" -> "123", "123 1/2" -> "123") so that a unit
    suffix does not split one building into several keys. Returns "" when the address does
    not start with a number, which is what makes address_key() refuse to build a key
    rather than build a weak one.
    """
    if not street:
        return ""
    cleaned = _PUNCT.sub(" ", street).strip()
    m = _LEADING_NUMBER.match(cleaned)
    return m.group(1) if m else ""


def street_name_prefix(street: str | None, length: int = STREET_NAME_PREFIX_LEN) -> str:
    """
    Standardise the street name and take its first characters ("STANDARDIZE / TAKE FIRST
    FEW CHARACTERS OF THE STREET NAME", General Rule 2).

    "123 Walnut St., 92101" -> "WALN", the customer's own example. Drops the leading
    building number, any directional ("123 N Walnut St" -> "WALN"), the street-type
    suffix, and everything from a unit marker onward. Returns fewer than ``length``
    characters when the street name is shorter ("123 Oak St" -> "OAK"), never padded:
    padding would make OAK collide with OAKA.
    """
    if not street:
        return ""
    cleaned = _PUNCT.sub(" ", street).upper()
    tokens = [t for t in _WHITESPACE.split(cleaned) if t]
    if tokens and _LEADING_NUMBER.match(tokens[0]):
        tokens = tokens[1:]
    # Cut at the first unit marker: "MAIN ST APT 4B" -> "MAIN ST".
    for i, tok in enumerate(tokens):
        if tok in UNIT_MARKERS or tok.startswith("#"):
            tokens = tokens[:i]
            break
    words = [t for t in tokens if t not in DIRECTIONALS and t not in STREET_SUFFIXES]
    if not words:
        # Every token was a suffix or directional (e.g. "123 Broadway" is fine, but
        # "123 West" is not) - fall back to the non-directional tokens so a street
        # legitimately named after a direction still yields a key.
        words = [t for t in tokens if t not in STREET_SUFFIXES] or tokens
    if not words:
        return ""
    return "".join(words)[:length]


def address_key(street: str | None, zip_code: str | int | None) -> str:
    """
    Build the General Rule 2 address key: ``<building>_<STREET[:4]>_<zip5>``.

    Verbatim: "When you are comparing street address, instead of doing a straight
    comparison of borr_street_addr, YOU SHOULD EXTRACT THE BUILDING/STREET NUMBER as
    well as the zip code...STANDARDIZE / TAKE FIRST FEW CHARACTERS OF THE STREET NAME,
    and CREATE SOME SORT OF KEY COMBINING ALL OF THOSE...I.E. 123 Walnut St., 92101,
    should become something like '123_WALN_92101'. Do something like that to match
    addresses and remove false positives."

    The customer's example is the test: address_key("123 Walnut St.", "92101") ==
    "123_WALN_92101".

    Raises AddressKeyError when the building number, street name or zip is missing.
    That is deliberate and is the "remove false positives" half of the rule: a partial
    key such as "_WALN_92101" would match every Walnut address in the zip, turning one
    bad record into a fabricated address cluster. Callers must treat an unkeyable
    address as *unknown*, never as a match candidate.
    """
    number = building_number(street)
    name = street_name_prefix(street)
    zip5 = norm_zip(zip_code)
    missing = [
        label
        for label, value in (("building number", number), ("street name", name), ("zip", zip5))
        if not value
    ]
    if missing:
        raise AddressKeyError(
            f"cannot build a General Rule 2 address key from street={street!r} "
            f"zip={zip_code!r}: missing {', '.join(missing)}. An address key with a "
            "missing part over-matches and manufactures false address clusters; treat "
            "this address as unknown instead."
        )
    return f"{number}_{name}_{zip5}"


def try_address_key(street: str | None, zip_code: str | int | None) -> str | None:
    """address_key() but returning None instead of raising, for bulk row processing."""
    try:
        return address_key(street, zip_code)
    except AddressKeyError:
        return None


def same_address(
    street_a: str | None,
    zip_a: str | int | None,
    street_b: str | None,
    zip_b: str | int | None,
) -> bool:
    """
    Compare two addresses by their General Rule 2 keys.

    False whenever either address cannot be keyed: an unknown address is not a match.
    Same street, different building number is False — that is precisely the false
    positive the rule was written to remove.
    """
    key_a = try_address_key(street_a, zip_a)
    key_b = try_address_key(street_b, zip_b)
    if key_a is None or key_b is None:
        return False
    return key_a == key_b


__all__ = [
    "ADDRESS_KEY_EXAMPLE",
    "ADDRESS_KEY_INTERPRETATION",
    "AddressKeyError",
    "DIRECTIONALS",
    "NORMALISED_FIELDS",
    "RULE_ID",
    "RULE_TEXT",
    "STREET_NAME_PREFIX_LEN",
    "STREET_SUFFIXES",
    "UNIT_MARKERS",
    "address_key",
    "building_number",
    "norm_email",
    "norm_string",
    "norm_zip",
    "same_address",
    "street_name_prefix",
    "try_address_key",
]
