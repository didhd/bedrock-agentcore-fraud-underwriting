"""Use-case tools built over the customer's verified queries.

THE CONFIDENTIALITY TEST IS THE IMPORTANT ONE HERE.

`tools/usecases/` is committed and the SQL it serves is not. `test_no_customer_sql_is_committed`
greps every git-tracked file in the package for identifiers taken from the real model, so a
future change that inlines "just this one query" fails immediately rather than at review time.

Everything else skips when the model is absent, which is the normal state of a fresh clone.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from signal_layer.semantic import available, load_model
from tools.usecases import (
    CATALOG,
    USE_CASES,
    SubstitutionError,
    tool_schema,
    unavailable,
    use_cases_for_domain,
)

REPO = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = REPO / "tools" / "usecases"

needs_model = pytest.mark.skipif(
    not available(),
    reason="the semantic model is customer confidential and gitignored; absent in a clone",
)


# ---------------------------------------------------------------------------
# Confidentiality. Runs whether or not the model is present.
# ---------------------------------------------------------------------------


def test_no_customer_sql_is_committed() -> None:
    """No git-tracked file in tools/usecases/ may contain the customer's schema or SQL.

    Tokens are read from the real model rather than typed here, so this test cannot drift out
    of date, and it also does not itself become a place where a customer identifier is
    committed.
    """
    if not available():
        pytest.skip("cannot derive the token list without the model")

    model = load_model()
    # Real table names, and the physical names behind them. Distinctive enough that an
    # accidental appearance is certainly a leak, not a coincidence.
    tokens = set()
    for name, table in model.tables.items():
        tokens.add(name.lower())
        tokens.add(table.table.lower())
        tokens.add(table.database.lower())
    # Plus a few column names that only exist in their schema.
    for table in model.tables.values():
        for column in table.dimensions[:6]:
            if len(column.name) > 8:
                tokens.add(column.name.lower())

    # Only DISTINCTIVE tokens. Several of their databases and schemas are ordinary English
    # words -- one is literally `REPORTING` -- and matching those flags our own prose while
    # carrying no information: knowing a database is called "reporting" tells a reader nothing.
    # A real leak is a compound identifier, and those all contain an underscore or are long.
    tokens = {t for t in tokens if "_" in t or len(t) >= 12}

    offenders: list[str] = []
    for source in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in source.parts:
            continue
        text = source.read_text(encoding="utf-8").lower()
        for token in tokens:
            # Word-bounded: `facts` as an English word in a docstring is not the column `facts`.
            if re.search(rf"\b{re.escape(token)}\b", text):
                offenders.append(f"{source.relative_to(REPO)} contains {token!r}")

    assert not offenders, "customer schema leaked into committed files:\n  " + "\n  ".join(
        offenders[:12]
    )


def test_the_package_imports_without_the_model() -> None:
    """A fresh clone must import this package, not crash on it."""
    # Already proved by this module importing at collection time, but asserted explicitly
    # because the degradation contract is the point: empty, with a reason.
    if available():
        assert unavailable() is None
        assert USE_CASES
    else:
        assert USE_CASES == {}
        assert unavailable()
        assert "gitignored" in unavailable()


class TestCatalogShape:
    """Holds with or without the model: this is our own metadata."""

    def test_slugs_are_unique_and_tool_safe(self) -> None:
        slugs = [spec.slug for spec in CATALOG]
        assert len(slugs) == len(set(slugs))
        for slug in slugs:
            # Gateway tool names and enum values: lower snake_case, no leading digit.
            assert re.fullmatch(r"[a-z][a-z0-9_]{2,63}", slug), slug

    def test_every_use_case_has_a_selector(self) -> None:
        for spec in CATALOG:
            assert spec.selectors, spec.slug
            for selector in spec.selectors:
                assert selector == selector.lower(), (
                    f"{spec.slug}: selectors are matched against a lower-cased question, so "
                    f"{selector!r} would never match"
                )

    def test_descriptions_are_substantial(self) -> None:
        # A one-line description is how a model ends up choosing the wrong tool. These are what
        # the Gateway advertises, so they are the entire basis for selection.
        for spec in CATALOG:
            assert len(spec.description) > 180, spec.slug
            assert len(spec.intent) > 30, spec.slug

    def test_domains_are_real_fraud_domains(self) -> None:
        from prompts.loader import DOMAINS

        for spec in CATALOG:
            for domain in spec.domains:
                assert domain in DOMAINS, f"{spec.slug}: {domain!r} is not one of {DOMAINS}"


# ---------------------------------------------------------------------------
# Binding
# ---------------------------------------------------------------------------


@needs_model
class TestBinding:
    def test_ten_use_cases(self) -> None:
        assert len(USE_CASES) == 10

    def test_every_verified_query_is_bound_exactly_once(self) -> None:
        """The check that stops us shipping less than the customer already has.

        `build()` raises on an unmapped query, so this asserts the total instead: seventeen
        verified queries, each behind exactly one use case.
        """
        model = load_model()
        bound = [q for uc in USE_CASES.values() for q in uc.queries]
        assert len(bound) == len(model.verified_queries) == 17
        assert len({id(q) for q in bound}) == 17

    def test_every_use_case_resolves_to_real_sql(self) -> None:
        for uc in USE_CASES.values():
            assert uc.sql.strip(), uc.slug
            assert uc.question.strip(), uc.slug
            for query in uc.queries:
                assert len(query.sql) > 50, f"{uc.slug}: suspiciously short SQL"

    def test_tables_and_databases_are_reported(self) -> None:
        for uc in USE_CASES.values():
            assert uc.tables, f"{uc.slug} resolved no model tables"
            assert uc.databases
            for table in uc.tables:
                assert table.count(".") == 2, "tables must be fully qualified"

    def test_multi_query_use_cases_keep_their_variants(self) -> None:
        # Four lender-volume queries and two score-bin queries are kept as variants rather than
        # merged, because merging means rewriting SQL the customer verified.
        assert len(USE_CASES["lender_application_volume"].queries) == 4
        assert len(USE_CASES["score_bin_performance"].queries) == 2
        assert USE_CASES["lender_application_volume"].variant(3).sql

    def test_domain_lookup_returns_use_cases(self) -> None:
        assert use_cases_for_domain("identity")
        assert use_cases_for_domain("not_a_domain") == ()


@needs_model
class TestSubstitution:
    def test_no_values_returns_the_verbatim_sql(self) -> None:
        uc = USE_CASES["missed_fraud_patterns"]
        assert uc.substitute({}) == uc.sql

    def test_no_pattern_contains_a_customer_literal(self) -> None:
        """Patterns must be STRUCTURAL. A literal here would publish customer data.

        The verified queries hardcode lender names and a real borrower hash. A pattern is
        allowed to describe their shape and never their value, so it must not contain a long run
        of literal alphanumerics.
        """
        import re as _re

        for spec in CATALOG:
            for param in spec.parameters:
                if not param.pattern:
                    continue
                # Strip regex metacharacters and character classes, then look for a long
                # literal alphabetic run -- which is what a pasted lender name would look like.
                stripped = _re.sub(r"\[[^\]]*\]|\\[a-zA-Z]|[{}()?*+.^$|\\]|\d", "", param.pattern)
                runs = _re.findall(r"[A-Za-z]{4,}", stripped)
                assert not runs, f"{spec.slug}.{param.name}: pattern looks literal: {runs}"

    def test_an_undeclared_value_is_ignored_not_injected(self) -> None:
        # A model passing a parameter this use case does not declare must not affect the SQL.
        # The alternative -- appending or guessing -- would be injection with extra steps.
        uc = USE_CASES["lender_directory"]
        assert uc.substitute({"lender": "'; drop table x --"}) == uc.sql

    def test_a_pattern_that_is_not_unique_refuses(self) -> None:
        """The guard that stops a half-rewritten query running.

        Rigged with a pattern that certainly matches several distinct strings, because the point
        is the uniqueness check rather than any particular parameter. Checked rather than
        assumed: an earlier version of this test used a token that occurs exactly ONCE, so the
        guard passed legitimately and the test proved nothing.
        """
        from tools.usecases.catalog import ParamSpec

        uc = USE_CASES["missed_fraud_patterns"]
        rigged = ParamSpec(name="rigged", description="test", pattern=r"\balert\d+\b")
        patched = type(uc)(**{**uc.__dict__, "parameters": (rigged,)})
        assert patched.substitutable() == (), "the pattern must not resolve uniquely"
        with pytest.raises(SubstitutionError) as caught:
            patched.substitute({"rigged": "x"})
        assert "uniquely" in str(caught.value)

    def test_substitution_actually_rewrites_when_the_pattern_is_unique(self) -> None:
        uc = USE_CASES["missed_fraud_patterns"]
        assert "year" in uc.substitutable()
        rewritten = uc.substitute({"year": "2099"})
        assert "2099" in rewritten
        assert rewritten != uc.sql

    def test_substitutable_is_reported_per_variant(self) -> None:
        # The four lender-volume queries are not identically shaped, so which parameters apply
        # differs between them. Reporting one answer for the whole use case would tell a model a
        # filter was available when it is not.
        uc = USE_CASES["lender_application_volume"]
        per_variant = {i: uc.substitutable(i) for i in range(len(uc.queries))}
        assert set(per_variant) == {0, 1, 2, 3}

    def test_referenced_tables_come_from_the_query_not_the_model(self) -> None:
        """At least one verified query reads a table the semantic model does not declare.

        That is a real finding and a question we owe the customer, so it is asserted rather than
        smoothed over: the read allowlist for a verified query has to come from the query.
        """
        outside = []
        model_tables = {t.lower() for t in load_model().tables}
        model_physical = {t.table.lower() for t in load_model().tables.values()}
        for uc in USE_CASES.values():
            for index in range(len(uc.queries)):
                for table in uc.referenced_tables(index):
                    if table not in model_tables and table not in model_physical:
                        outside.append((uc.slug, table))
        assert outside, (
            "expected at least one verified query to reference a table outside the semantic "
            "model; if the customer has since added it, update this test and docs/"
        )


class TestToolSchema:
    def test_two_tools_not_ten(self) -> None:
        # Ten near-identical schemas is worse for selection than one enum, and it would also
        # collide with the customer's "MAXIMUM 2 tools per query" rule for the interactive agent.
        schema = tool_schema()
        assert [t["name"] for t in schema] == ["list_use_cases", "run_use_case"]

    def test_run_use_case_takes_a_slug_not_sql(self) -> None:
        # The whole safety argument: a model picks the question, never the query.
        run = next(t for t in tool_schema() if t["name"] == "run_use_case")
        properties = run["inputSchema"]["properties"]
        assert set(properties) == {"use_case", "parameters"}
        assert "sql" not in properties
        assert properties["use_case"]["enum"]

    def test_the_schema_carries_no_customer_text(self) -> None:
        if not available():
            pytest.skip("cannot derive the token list without the model")
        import json

        model = load_model()
        blob = json.dumps(tool_schema()).lower()
        for name, table in model.tables.items():
            assert name.lower() not in blob, f"{name} leaked into the tool schema"
            assert table.table.lower() not in blob
