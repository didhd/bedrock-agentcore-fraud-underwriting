"""Use-case tools, built by binding our catalog to the customer's verified queries.

WHY THIS IS A BUILDER AND NOT A SET OF FILES

The SQL is customer confidential. `tools/usecases/catalog.py` holds our slugs, descriptions and
selectors and is committed; the SQL is loaded at import from the gitignored semantic model and
never written to disk in this form. So the repo carries the mapping and the customer carries
the material, which is the same arrangement `prompts/loader.py` already uses for the nine agent
prompts.

WHAT "VERIFIED" BUYS AND WHAT IT DOES NOT

Every query here was written and verified by the customer's own engineer, so the joins, the
guardrails and the exclusions are theirs. That is exactly why a model is given a SLUG and not
SQL: it picks the question, not the query.

It does NOT buy skipping validation. Parameter substitution is new attack surface that their
verification never covered, so a substituted query still goes through the Gateway Lambda's
read-only validator before it executes.

DEGRADES, DOES NOT EXPLODE

With the semantic model absent -- a fresh clone, CI -- `USE_CASES` is empty and `unavailable()`
says why. Import never fails. Nothing else in the repo should break because a gitignored file is
gitignored.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from signal_layer.semantic import (
    SemanticModelUnavailable,
    VerifiedQuery,
    available,
    load_model,
)
from tools.usecases.catalog import CATALOG, ParamSpec, UseCaseSpec, slugs

__all__ = [
    "CATALOG",
    "USE_CASES",
    "ParamSpec",
    "UseCase",
    "UseCaseSpec",
    "SubstitutionError",
    "build",
    "slugs",
    "tool_schema",
    "unavailable",
    "use_cases_for_domain",
]


class SubstitutionError(RuntimeError):
    """A declared literal was not found exactly once in the verified SQL.

    Raised rather than substituted-anyway. A literal that now occurs twice means the query
    changed shape in a later delivery, and rewriting one of two occurrences would produce a
    query that runs and answers a different question -- the worst possible outcome here.
    """


@dataclass(frozen=True)
class UseCase:
    slug: str
    title: str
    intent: str
    description: str
    parameters: tuple[ParamSpec, ...]
    domains: tuple[str, ...]
    notes: str
    #: The customer's verified queries backing this use case, in catalog selector order.
    queries: tuple[VerifiedQuery, ...]
    #: Fully-qualified table names the backing SQL touches, resolved through the model.
    tables: tuple[str, ...]
    databases: tuple[str, ...]

    @property
    def question(self) -> str:
        """The customer's own question for the primary backing query. Verbatim."""
        return self.queries[0].question

    @property
    def sql(self) -> str:
        """The customer's verified SQL for the primary backing query. Verbatim."""
        return self.queries[0].sql

    def variant(self, index: int = 0) -> VerifiedQuery:
        """One of several verified queries behind this use case.

        Lender volume has four and score-bin performance has two. They are kept as variants
        rather than merged, because merging would mean rewriting SQL the customer verified.
        """
        return self.queries[index]

    def substitute(self, values: dict[str, Any], *, index: int = 0) -> str:
        """The verified SQL with declared literals replaced. Verbatim when `values` is empty.

        Exact-count checked: each literal must appear precisely once. See `SubstitutionError`.
        """
        sql = self.queries[index].sql
        for spec in self.parameters:
            if spec.pattern is None or spec.name not in values:
                continue
            literal = spec.locate(sql)
            if literal is None:
                raise SubstitutionError(
                    f"{self.slug}: parameter {spec.name!r} could not be located uniquely in "
                    f"variant {index}. Its pattern matched zero or several distinct literals, "
                    "so this refuses rather than rewriting one of them -- a half-substituted "
                    "query runs and answers a different question."
                )
            sql = sql.replace(literal, str(values[spec.name]))
        return sql

    def referenced_tables(self, index: int = 0) -> tuple[str, ...]:
        """Every bare table name this variant's SQL reads, CTEs excluded.

        Used as the read allowlist when the Gateway Lambda executes a verified query, and NOT
        derived from the semantic model -- because at least one verified query references a
        table the model does not declare. The model is the documented surface; the query is the
        truth about what it touches.

        The table allowlist is a control on MODEL-AUTHORED SQL. On this path a model supplies a
        slug and cannot author or edit the statement, so the controls that apply are: read-only
        validation, the row cap, and the database role. Deriving the allowlist from the query
        itself is therefore honest rather than circular -- and it makes visible which tables the
        customer's own queries need, which is a question we owe them.
        """
        import re  # noqa: PLC0415

        sql = self.queries[index].sql
        lowered = sql.lower()
        ctes = {
            m.group(1)
            for m in re.finditer(
                r"(?:\bwith\s+(?:recursive\s+)?|,)\s*([a-z_]\w*)\s+as\s*\(", lowered
            )
        }
        found = {
            m.group(1).split(".")[-1].strip('"').lower()
            for m in re.finditer(r"\b(?:from|join)\s+([A-Za-z_\"][\w\".]*)", sql, re.I)
        }
        return tuple(sorted(found - ctes))

    def substitutable(self, index: int = 0) -> tuple[str, ...]:
        """Parameter names that can actually be applied to this variant.

        Reported rather than assumed, because a pattern that locates a literal in one variant
        may not in another -- the four lender-volume queries are not identically shaped.
        """
        sql = self.queries[index].sql
        return tuple(p.name for p in self.parameters if p.locate(sql) is not None)


def _tables_in(sql: str, known: dict[str, str]) -> tuple[str, ...]:
    """Fully-qualified names for the model tables a statement references.

    Regex over `from`/`join`, resolved against the model's declared tables. Approximate by
    construction -- a CTE alias looks like a table -- so it is used for REPORTING which
    databases a use case needs, never as a security control. The Gateway Lambda's allowlist is
    the control.
    """
    import re  # noqa: PLC0415

    found = {
        match.group(1).split(".")[-1].strip('"').lower()
        for match in re.finditer(r"\b(?:from|join)\s+([A-Za-z_\"][\w\".]*)", sql, re.I)
    }
    return tuple(sorted({known[name] for name in found if name in known}))


_MISSING: str | None = None


def build() -> dict[str, UseCase]:
    """Bind the catalog to the loaded model. Returns {} when the model is absent."""
    global _MISSING

    if not available():
        _MISSING = (
            "the semantic model is absent, so no use-case tool could be built. It is customer "
            "confidential and gitignored; copy FRAUDBOT_SEMANTIC_VIEW.yaml into "
            "prompts/verbatim/ from the delivered zip."
        )
        return {}

    try:
        model = load_model()
    except SemanticModelUnavailable as exc:  # pragma: no cover - covered by available()
        _MISSING = str(exc)
        return {}

    # Keyed by BOTH names. The semantic name and the physical table name differ for at least
    # one table -- one has a single underscore where the other has two -- so a lookup on the
    # semantic name alone silently failed to resolve it, and the use case reported fewer
    # databases than it actually needs.
    by_lower_name: dict[str, str] = {}
    for name, table in model.tables.items():
        by_lower_name[name.lower()] = table.fully_qualified
        by_lower_name.setdefault(table.table.lower(), table.fully_qualified)
    remaining = list(model.verified_queries)
    built: dict[str, UseCase] = {}

    for spec in CATALOG:
        matched: list[VerifiedQuery] = []
        for selector in spec.selectors:
            hits = [q for q in model.verified_queries if selector in q.question.lower()]
            if len(hits) != 1:
                raise RuntimeError(
                    f"{spec.slug}: selector {selector!r} matched {len(hits)} verified queries, "
                    "expected exactly 1. The delivered model changed; fix the selector in "
                    "tools/usecases/catalog.py rather than loosening this check."
                )
            matched.append(hits[0])
            if hits[0] in remaining:
                remaining.remove(hits[0])

        tables: set[str] = set()
        for query in matched:
            tables.update(_tables_in(query.sql, by_lower_name))

        built[spec.slug] = UseCase(
            slug=spec.slug,
            title=spec.title,
            intent=spec.intent,
            description=spec.description,
            parameters=spec.parameters,
            domains=spec.domains,
            notes=spec.notes,
            queries=tuple(matched),
            tables=tuple(sorted(tables)),
            databases=tuple(sorted({t.split(".")[0] for t in tables})),
        )

    if remaining:
        # Not a warning. An unmapped verified query is a capability the customer's engineer
        # built and we are not exposing, and it will otherwise be discovered by them noticing
        # the agent cannot answer something their own bot could.
        raise RuntimeError(
            f"{len(remaining)} verified quer{'y' if len(remaining) == 1 else 'ies'} map to no "
            "use case: "
            + "; ".join(q.question[:70] for q in remaining)
            + ". Add a UseCaseSpec to tools/usecases/catalog.py."
        )

    _MISSING = None
    return built


USE_CASES: Final[dict[str, UseCase]] = build()


def unavailable() -> str | None:
    """Why `USE_CASES` is empty, or None when it is populated."""
    return _MISSING


def use_cases_for_domain(domain: str) -> tuple[UseCase, ...]:
    """Use cases that inform one fraud domain.

    Reporting only. It does NOT hand these to a specialist: the eight specialists have no data
    tool, and `tests/test_rds_connection.py` asserts that in two directions. This exists so the
    UI and the docs can show which portfolio questions relate to which discipline.
    """
    return tuple(uc for uc in USE_CASES.values() if domain in uc.domains)


def tool_schema() -> list[dict[str, Any]]:
    """Gateway tool schema for the use-case tools. Contains no customer text.

    Two tools, not ten. A model choosing between ten near-identical schemas picks worse than one
    choosing a slug from an enum, and ten tools would also collide with the customer's own
    "MAXIMUM 2 tools per query" rule for the interactive agent.
    """
    enum = list(USE_CASES) or list(slugs())
    listing = "; ".join(f"{spec.slug}: {spec.intent}" for spec in CATALOG)

    return [
        {
            "name": "list_use_cases",
            "description": (
                "List the verified analyst queries available to run, with what each answers and "
                "which parameters it takes. Call this first when you are unsure which use case "
                "fits the question. Available: " + listing
            ),
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "run_use_case",
            "description": (
                "Run one of the customer's own verified analyst queries by name and return the "
                "rows. These queries were written and verified by the lender's fraud engineering "
                "team, so prefer them over writing SQL yourself: they already contain the "
                "correct joins and the exclusions for test and known-fake records. Use "
                "query_underwriting_data only when no use case fits. Results are capped."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "use_case": {
                        "type": "string",
                        "enum": enum,
                        "description": "Which verified query to run. Call list_use_cases for what each does.",
                    },
                    "parameters": {
                        "type": "object",
                        "description": (
                            "Optional named values for this use case, e.g. a lender name or a "
                            "date range. Omit to run the query exactly as the customer verified "
                            "it. list_use_cases reports which names each use case accepts."
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["use_case"],
            },
        },
    ]
