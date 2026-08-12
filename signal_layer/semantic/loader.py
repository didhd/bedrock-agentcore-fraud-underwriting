"""Load the customer's Cortex semantic model, byte-checked, without committing a byte of it.

WHY A LOADER AND NOT GENERATED CODE

`FRAUDBOT_SEMANTIC_VIEW.yaml` is customer confidential material. It lives in
`prompts/verbatim/`, which `.gitignore` excludes, and it reaches the customer as a zip.
Extracting its SQL and column names into `.py` files would put exactly the same material
into git under a different extension.

So this module is the same shape as `prompts/loader.py`: git-tracked code that reads the
gitignored bytes at runtime and sha256-verifies them. `manifest.json` beside this file records
only a filename and a digest -- no content.

WHY IT MUST IMPORT WITHOUT THE FILE

CI and a fresh clone do not have the YAML. An import-time read would break collection for
every test in the repo. So `available()` answers the question, `load_model()` raises
`SemanticModelUnavailable`, and callers degrade rather than explode.

WHAT THIS ANSWERS, WHICH WAS OPEN

CLAUDE.md asks whether their Cortex Analyst is backed by a semantic VIEW or a stage-based
semantic model YAML. It is the YAML. Snowflake's managed Cortex Analyst MCP server supports
VIEWS only, so "keep their semantic layer by pointing at the managed MCP server" is not
available and the model has to be carried across.

It also supersedes a request: `module_custom_instructions.sql_generation` is the authoritative
numbered rule list that `signal_layer/rules/` previously RECONSTRUCTED from the nine agent
prompts. Where the two disagree, this file is right and ours is wrong.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
from dataclasses import dataclass, field
from typing import Any, Final

SEMANTIC_MODEL_FILE: Final[str] = "FRAUDBOT_SEMANTIC_VIEW.yaml"

_HERE: Final = pathlib.Path(__file__).resolve().parent
_REPO_ROOT: Final = _HERE.parent.parent
VERBATIM_DIR: Final = _REPO_ROOT / "prompts" / "verbatim"
MANIFEST_PATH: Final = _HERE / "manifest.json"


class SemanticIntegrityError(RuntimeError):
    """The semantic model on disk no longer matches its recorded sha256.

    Loud for the same reason `PromptIntegrityError` is: a silently edited semantic model would
    change which tables, columns and verified queries the agents believe exist, and nothing
    downstream would report that it had happened.
    """


class SemanticModelUnavailable(RuntimeError):
    """The semantic model file is absent.

    Expected on a fresh clone and in CI -- the file is gitignored. Distinct from
    `SemanticIntegrityError` so a caller can degrade on absence while still failing hard on
    corruption.
    """


@dataclass(frozen=True)
class Column:
    name: str
    expr: str
    data_type: str
    access_modifier: str | None = None
    description: str | None = None
    synonyms: tuple[str, ...] = ()


@dataclass(frozen=True)
class Table:
    name: str
    description: str
    database: str
    schema: str
    table: str
    synonyms: tuple[str, ...] = ()
    dimensions: tuple[Column, ...] = ()
    time_dimensions: tuple[Column, ...] = ()
    facts: tuple[Column, ...] = ()

    @property
    def fully_qualified(self) -> str:
        return f"{self.database}.{self.schema}.{self.table}"

    @property
    def column_count(self) -> int:
        return len(self.dimensions) + len(self.time_dimensions) + len(self.facts)


@dataclass(frozen=True)
class Relationship:
    name: str
    left_table: str
    right_table: str
    #: ``((left_column, right_column), ...)``. Multi-column on purpose: their own rules 4 and 5
    #: require joining on application_id AND a lender id, and a single-column view of this
    #: would quietly drop that.
    relationship_columns: tuple[tuple[str, str], ...] = ()
    join_type: str | None = None
    relationship_type: str | None = None


@dataclass(frozen=True)
class VerifiedQuery:
    name: str
    question: str
    sql: str
    verified_at: Any = None
    verified_by: str | None = None
    use_as_onboarding_question: bool = False


@dataclass(frozen=True)
class Rule:
    #: A string, not an int: the list contains "1.5".
    number: str
    text: str


@dataclass(frozen=True)
class SemanticModel:
    name: str
    description: str
    tables: dict[str, Table]
    relationships: tuple[Relationship, ...]
    verified_queries: tuple[VerifiedQuery, ...]
    sql_generation_rules: tuple[Rule, ...]
    question_categories: tuple[str, ...]
    raw_custom_instructions: dict[str, str] = field(default_factory=dict)

    @property
    def databases(self) -> tuple[str, ...]:
        return tuple(sorted({t.database for t in self.tables.values()}))

    @property
    def dimension_count(self) -> int:
        return sum(t.column_count for t in self.tables.values())

    def query_by_question(self, needle: str) -> VerifiedQuery | None:
        """Find a verified query by a substring of its question, case-insensitively.

        Exists because the `name` field is unusable as a key: the customer's export produced
        names like '"0;1"' and one that is 250-odd quote characters followed by a truncated
        sentence. The question is the stable, meaningful identifier.
        """
        lowered = needle.lower()
        for query in self.verified_queries:
            if lowered in query.question.lower():
                return query
        return None


def model_path() -> pathlib.Path:
    return VERBATIM_DIR / SEMANTIC_MODEL_FILE


def available() -> bool:
    return model_path().is_file()


def _manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.is_file():
        return {}
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _verify(raw: bytes) -> None:
    entry = _manifest().get("files", {}).get(SEMANTIC_MODEL_FILE)
    if not entry:
        raise SemanticIntegrityError(
            f"{SEMANTIC_MODEL_FILE} is not recorded in {MANIFEST_PATH}. Every verbatim "
            "customer file must be digest-recorded; add it rather than loading it unchecked."
        )
    digest = hashlib.sha256(raw).hexdigest()
    if digest != entry["sha256"]:
        raise SemanticIntegrityError(
            f"{SEMANTIC_MODEL_FILE} does not match its recorded sha256 "
            f"(on disk {digest}, recorded {entry['sha256']}). Restore the customer's original "
            "file; do not refresh the manifest to make this pass."
        )


#: A rule boundary: start of line, then "N." or "N.5", then whitespace. Anchored at line start
#: so a "1." occurring mid-sentence inside a rule's own prose cannot split it.
_RULE_BOUNDARY: Final = re.compile(r"(?m)^\s*(\d+(?:\.\d+)?)\s*[.)]\s+")


def _parse_rules(blob: str) -> tuple[Rule, ...]:
    """Split the one long sql_generation string into numbered rules.

    The string is a single YAML scalar containing embedded newlines, a nickname table and a
    long product glossary, so a naive split on "\\n" or on every "N." would shred it. Matching
    only line-anchored "N." boundaries and keeping everything up to the next boundary as that
    rule's body is what survives the real content.
    """
    matches = list(_RULE_BOUNDARY.finditer(blob))
    if not matches:
        return ()
    rules: list[Rule] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(blob)
        text = blob[start:end].strip()
        if text:
            rules.append(Rule(number=match.group(1), text=text))
    return tuple(rules)


def _columns(entries: Any) -> tuple[Column, ...]:
    out: list[Column] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        synonyms = entry.get("synonyms") or []
        out.append(
            Column(
                name=str(entry.get("name", "")),
                expr=str(entry.get("expr", entry.get("name", ""))),
                data_type=str(entry.get("data_type", "")),
                access_modifier=entry.get("access_modifier"),
                description=entry.get("description"),
                synonyms=tuple(str(s) for s in synonyms),
            )
        )
    return tuple(out)


_CACHE: SemanticModel | None = None


def load_model(*, refresh: bool = False) -> SemanticModel:
    """The parsed semantic model. Cached, because the parse is not free.

    Raises `SemanticModelUnavailable` when the gitignored file is absent and
    `SemanticIntegrityError` when it is present but altered.
    """
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE

    path = model_path()
    if not path.is_file():
        raise SemanticModelUnavailable(
            f"{path} is absent. It is customer confidential material and therefore "
            "gitignored, so it is not in a fresh clone. Copy it out of the delivered "
            "prompts/verbatim zip into prompts/verbatim/."
        )

    raw = path.read_bytes()
    _verify(raw)

    try:
        import yaml  # noqa: PLC0415
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency is declared
        raise SemanticModelUnavailable(
            "PyYAML is required to parse the semantic model. It is in requirements.txt; "
            "install it rather than hand-rolling a parser for a 21876-line file."
        ) from exc

    # C-accelerated loader when the build has it. SafeLoader, not FullLoader: this file is
    # customer input and nothing in a semantic model needs arbitrary object construction.
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    document = yaml.load(raw.decode("utf-8"), Loader=loader)  # noqa: S506 - SafeLoader above

    tables: dict[str, Table] = {}
    for entry in document.get("tables") or []:
        base = entry.get("base_table") or {}
        name = str(entry.get("name", ""))
        tables[name] = Table(
            name=name,
            description=str(entry.get("description", "")),
            database=str(base.get("database", "")),
            schema=str(base.get("schema", "")),
            table=str(base.get("table", "")),
            synonyms=tuple(str(s) for s in entry.get("synonyms") or []),
            dimensions=_columns(entry.get("dimensions")),
            time_dimensions=_columns(entry.get("time_dimensions")),
            facts=_columns(entry.get("facts")),
        )

    relationships: list[Relationship] = []
    for entry in document.get("relationships") or []:
        pairs = tuple(
            (str(c.get("left_column", "")), str(c.get("right_column", "")))
            for c in entry.get("relationship_columns") or []
            if isinstance(c, dict)
        )
        relationships.append(
            Relationship(
                name=str(entry.get("name", "")),
                left_table=str(entry.get("left_table", "")),
                right_table=str(entry.get("right_table", "")),
                relationship_columns=pairs,
                join_type=entry.get("join_type"),
                relationship_type=entry.get("relationship_type"),
            )
        )

    queries: list[VerifiedQuery] = []
    for entry in document.get("verified_queries") or []:
        queries.append(
            VerifiedQuery(
                name=str(entry.get("name", "")),
                question=str(entry.get("question", "")),
                sql=str(entry.get("sql", "")),
                verified_at=entry.get("verified_at"),
                verified_by=entry.get("verified_by"),
                use_as_onboarding_question=bool(
                    entry.get("use_as_onboarding_question", False)
                ),
            )
        )

    custom = document.get("module_custom_instructions") or {}
    raw_custom = {str(k): str(v) for k, v in custom.items() if isinstance(v, str)}

    categories = tuple(
        line.strip()
        for line in str(custom.get("question_categorization", "")).splitlines()
        if line.strip()
    )

    _CACHE = SemanticModel(
        name=str(document.get("name", "")),
        description=str(document.get("description", "")),
        tables=tables,
        relationships=tuple(relationships),
        verified_queries=tuple(queries),
        sql_generation_rules=_parse_rules(str(custom.get("sql_generation", ""))),
        question_categories=categories,
        raw_custom_instructions=raw_custom,
    )
    return _CACHE


def databases() -> tuple[str, ...]:
    return load_model().databases


def fully_qualified(table_name: str) -> str:
    table = load_model().tables.get(table_name)
    if table is None:
        raise KeyError(f"{table_name!r} is not a table in the semantic model")
    return table.fully_qualified
