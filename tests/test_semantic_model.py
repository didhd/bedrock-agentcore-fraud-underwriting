"""The semantic-model loader.

EVERY ASSERTION HERE IS A COUNT OR A STRUCTURE, NEVER A VALUE.

The model is customer confidential and gitignored; this file is committed. So a test may
assert that there are 24 tables, and must not assert what any of them is called. A reviewer
should be able to read this file and learn nothing about the customer's schema.

Skipped wholesale when the model is absent, which is the normal state of a fresh clone.
"""

from __future__ import annotations

import hashlib

import pytest

from signal_layer.semantic import (
    SemanticIntegrityError,
    SemanticModelUnavailable,
    available,
    load_model,
    model_path,
)

pytestmark = pytest.mark.skipif(
    not available(),
    reason="the semantic model is customer confidential and gitignored; absent in a clone",
)


@pytest.fixture(scope="module")
def model():
    return load_model()


class TestCounts:
    """Measured on the delivered file (sha256 8839d5e2…). A change means a new delivery."""

    def test_tables(self, model) -> None:
        assert len(model.tables) == 24

    def test_databases(self, model) -> None:
        # Twelve, not one. This is the fact that most affects the RDS plan: the agents cannot
        # reach all of this from a single Postgres connection, so which tables actually exist
        # in the customer's staging RDS decides how many use cases work on day one.
        assert len(model.databases) == 12

    def test_relationships(self, model) -> None:
        assert len(model.relationships) == 21

    def test_verified_queries(self, model) -> None:
        assert len(model.verified_queries) == 17

    def test_columns(self, model) -> None:
        assert model.dimension_count == 2409

    def test_sql_generation_rules(self, model) -> None:
        # 25 top-level rules, numbered 1..25 with no gaps. The customer's "1.5" is a
        # sub-point INSIDE rule 1 (same subject: looking up a lender by name), which is how
        # they wrote it, so it is not promoted to a 26th rule.
        rules = model.sql_generation_rules
        assert len(rules) == 25
        assert [r.number for r in rules] == [str(n) for n in range(1, 26)]

    def test_rule_one_kept_its_sub_point(self, model) -> None:
        # Guards the rule splitter from the opposite failure: a boundary regex loose enough to
        # split on any "N." would cut rule 1 in half and silently drop the lender-nickname
        # table that the rest of rule 1 consists of.
        rule_one = next(r for r in model.sql_generation_rules if r.number == "1")
        assert "1.5" in rule_one.text
        assert len(rule_one.text) > 1000

    def test_question_categories(self, model) -> None:
        # 26 lines: 1, 1.5, then 2..25. One more than the rule count, and that is expected.
        assert len(model.question_categories) == 26

    def test_custom_instruction_keys(self, model) -> None:
        assert set(model.raw_custom_instructions) == {
            "sql_generation",
            "question_categorization",
        }


class TestStructure:
    def test_every_table_is_fully_qualified(self, model) -> None:
        for table in model.tables.values():
            assert table.database and table.schema and table.table
            assert table.fully_qualified.count(".") == 2

    def test_every_table_has_columns(self, model) -> None:
        for name, table in model.tables.items():
            assert table.column_count > 0, f"{name} parsed with no columns"

    def test_every_verified_query_has_sql_and_a_question(self, model) -> None:
        for query in model.verified_queries:
            assert query.sql.strip(), f"{query.question[:40]!r} has no SQL"
            assert query.question.strip()

    def test_relationships_carry_their_join_columns(self, model) -> None:
        # Multi-column joins are load-bearing: the customer's own rules require joining on an
        # application id AND a lender id, so a relationship parsed down to one column would
        # produce a fan-out across lenders.
        multi = [r for r in model.relationships if len(r.relationship_columns) > 1]
        assert multi, "no multi-column relationship parsed; the join columns were dropped"
        for relationship in model.relationships:
            assert relationship.left_table and relationship.right_table
            for left, right in relationship.relationship_columns:
                assert left and right

    def test_relationship_endpoints_are_declared_tables(self, model) -> None:
        for relationship in model.relationships:
            for side in (relationship.left_table, relationship.right_table):
                assert side in model.tables, "relationship references an undeclared table"

    def test_lookup_by_question_works(self, model) -> None:
        # The `name` field is unusable as a key -- the export produced one name that is a long
        # run of quote characters and another that is '"0;1"' -- so question lookup is the
        # supported path and is asserted rather than assumed.
        first = model.verified_queries[0]
        assert model.query_by_question(first.question[:25]) is not None
        assert model.query_by_question("no query asks this particular thing") is None


class TestIntegrity:
    def test_drift_is_detected(self, tmp_path, monkeypatch) -> None:
        """A mutated model must fail loudly rather than load."""
        import signal_layer.semantic.loader as loader

        original = model_path().read_bytes()
        mutated = tmp_path / loader.SEMANTIC_MODEL_FILE
        # Append rather than edit: preserves valid YAML, so the ONLY thing that can reject it
        # is the digest check. An edit that broke the YAML would pass this test for the wrong
        # reason.
        mutated.write_bytes(original + b"\n# appended\n")

        monkeypatch.setattr(loader, "VERBATIM_DIR", tmp_path)
        with pytest.raises(SemanticIntegrityError) as caught:
            loader.load_model(refresh=True)
        assert loader.SEMANTIC_MODEL_FILE in str(caught.value)
        # No cache restore here on purpose: both failure paths raise BEFORE assigning _CACHE,
        # so the cache still holds the real model. Calling load_model() again inside the test
        # would run while monkeypatch is still redirecting VERBATIM_DIR and would raise.

    def test_absence_is_a_different_error_from_corruption(self, tmp_path, monkeypatch) -> None:
        import signal_layer.semantic.loader as loader

        monkeypatch.setattr(loader, "VERBATIM_DIR", tmp_path)
        assert loader.available() is False
        with pytest.raises(SemanticModelUnavailable):
            loader.load_model(refresh=True)

    def test_the_manifest_records_the_file_on_disk(self) -> None:
        import json

        import signal_layer.semantic.loader as loader

        recorded = json.loads(loader.MANIFEST_PATH.read_text())["files"][
            loader.SEMANTIC_MODEL_FILE
        ]["sha256"]
        assert hashlib.sha256(model_path().read_bytes()).hexdigest() == recorded


def test_the_model_itself_is_not_committed() -> None:
    """The confidentiality boundary, asserted rather than trusted.

    One `git add -A` by anyone is all it takes, and the material is a customer's production
    fraud schema. Cheap to check, catastrophic to miss.
    """
    import subprocess

    result = subprocess.run(
        ["git", "check-ignore", str(model_path())],
        capture_output=True,
        text=True,
        cwd=str(model_path().parents[2]),
    )
    assert result.returncode == 0, (
        f"{model_path()} is NOT gitignored. It is customer confidential material and must "
        "never be committable."
    )
