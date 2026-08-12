"""The RDS connection: the read-only SQL tool, and the signal/adjudication path.

The tests that matter most here are not the happy paths. They are:

  * `test_no_specialist_receives_the_sql_tool` -- the property the whole port rests on. If a
    specialist ever gains a SQL tool, "agents interpret; they never query" is gone, a
    text-to-SQL round trip is back in the per-application minute, and each specialist can
    widen its own domain view. Asserted rather than trusted.
  * `TestValidatorRejects` -- every one of these was run against the validator, not imagined.
  * `test_no_write_tool_is_exposed` -- the write is `persist_adjudication`, called by the
    pipeline with columns from the 21-key contract. A model deciding to issue an UPDATE
    against underwriting data is a different thing and is not what the customer asked for.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load_lambda():
    """Import the Gateway Lambda by path.

    It is deployment code under `deploy/cdk/`, not an installed package, and it must stay
    that way -- it is zipped and shipped alone. Loading by path is how the alert-dictionary
    Lambda is tested too.
    """
    path = REPO / "deploy" / "cdk" / "lambda-rds" / "main.py"
    spec = importlib.util.spec_from_file_location("pp_rds_lambda", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rds = _load_lambda()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidatorRejects:
    """Statements that must never reach the database."""

    @pytest.mark.parametrize(
        ("sql", "because"),
        [
            ("drop table applications", "bare DDL"),
            ("update applications set fraud_score = 0", "bare UPDATE"),
            ("delete from applications", "bare DELETE"),
            ("insert into applications values (1)", "bare INSERT"),
            ("truncate applications", "bare TRUNCATE"),
            ("grant select on applications to public", "privilege change"),
            ("select * from applications; drop table applications", "stacked statement"),
            (
                "select * from applications /* hidden */; drop table applications",
                "block comment hiding the semicolon",
            ),
            (
                "select * from applications -- x\n; drop table applications",
                "line comment hiding the semicolon",
            ),
            ("select * into evil from applications", "SELECT INTO writes"),
            ("select * from pg_shadow", "table outside the allowlist"),
            ("select * from public.pg_user", "schema-qualified, outside the allowlist"),
            (
                "select * from applications join pg_authid on true",
                "allowlisted table joined to one that is not",
            ),
            ("select pg_sleep(60)", "resource exhaustion, and names no table"),
            ("select pg_read_file('/etc/passwd')", "file read"),
            ("select 1", "names no table"),
            ("", "empty"),
            ("   ", "whitespace"),
            ("-- just a comment", "only a comment"),
            ("/* only a comment */", "only a block comment"),
        ],
    )
    def test_rejected(self, sql: str, because: str) -> None:
        out, error = rds.validate_sql(sql)
        assert error, f"NOT REJECTED ({because}): {sql!r} -> {out!r}"
        assert out == ""


class TestValidatorAllows:
    """Statements an analyst's question legitimately produces."""

    @pytest.mark.parametrize(
        "sql",
        [
            "select * from applications",
            "SELECT * FROM Applications",
            "select count(*) from underwriting_report_output where overall_risk_decision = %s",
            "with recent as (select 1) select * from underwriting_report_output",
            "select a.* from applications a join alerts_fired f on true",
            "select * from application_signals where application_id = %s limit 10",
        ],
    )
    def test_allowed(self, sql: str) -> None:
        out, error = rds.validate_sql(sql)
        assert error is None, f"wrongly rejected {sql!r}: {error}"
        assert out

    def test_a_limit_is_added_when_absent(self) -> None:
        out, error = rds.validate_sql("select * from applications")
        assert error is None
        assert out.endswith(f"limit {rds.MAX_ROWS}")

    def test_an_existing_limit_is_respected(self) -> None:
        out, _ = rds.validate_sql("select * from applications limit 5")
        assert out.count("limit") == 1
        assert out.endswith("limit 5")

    def test_a_trailing_semicolon_is_not_a_second_statement(self) -> None:
        # A single terminated statement is what a human types. Rejecting it would be a
        # confusing failure for a query that is completely fine.
        out, error = rds.validate_sql("select * from applications;")
        assert error is None, error
        assert ";" not in out

    @pytest.mark.parametrize(
        "column",
        ["created_at", "updated_at", "deleted_at", "call_count", "date_into_default"],
    )
    def test_forbidden_keywords_do_not_match_inside_identifiers(self, column: str) -> None:
        # `\bcreate\b` must not fire on `created_at`. Without the word boundaries this
        # rejects most real schemas, which is the kind of thing that gets the allowlist
        # loosened in a hurry.
        _, error = rds.validate_sql(f"select {column} from applications")
        assert error is None, f"{column} tripped the keyword scan: {error}"


class TestParameters:
    """Values travel separately from the statement, and the count is checked."""

    def test_placeholder_count_mismatch_is_refused(self, monkeypatch) -> None:
        # Guards against a model that inlined one value and parameterized another; running
        # it would either error opaquely in the driver or bind the wrong value.
        monkeypatch.setattr(rds, "_run", lambda *a, **k: pytest.fail("must not execute"))
        out = rds.query_underwriting_data(
            {"sql": "select * from applications where a = %s and b = %s", "parameters": ["x"]}
        )
        assert out["ok"] is False
        assert "placeholder" in out["error"]

    def test_a_json_string_of_parameters_is_recovered(self, monkeypatch) -> None:
        seen: dict = {}

        def fake_run(sql, params):
            seen["params"] = params
            return []

        monkeypatch.setattr(rds, "_run", fake_run)
        out = rds.query_underwriting_data(
            {"sql": "select * from applications where a = %s", "parameters": '["APP-1004"]'}
        )
        assert out["ok"] is True
        assert seen["params"] == ["APP-1004"]

    def test_bool_is_coerced_before_int(self) -> None:
        # bool subclasses int in Python, so an int check placed first sends `longValue: 1`
        # for True. The Data API then rejects it against a boolean column.
        assert rds._data_api_value(True) == {"booleanValue": True}
        assert rds._data_api_value(1) == {"longValue": 1}
        assert rds._data_api_value(None) == {"isNull": True}
        assert rds._data_api_value("x") == {"stringValue": "x"}


class TestRowCap:
    """The cap is a blast-radius control, so it is applied after execution as well."""

    def test_rows_are_truncated_even_if_the_statement_slipped_past(self, monkeypatch) -> None:
        # A `limit` inside a CTE satisfies the validator's regex without bounding the outer
        # result, which is exactly how an unbounded borrower dump would reach a context
        # window and a trace.
        monkeypatch.setattr(
            rds, "_run", lambda *a, **k: [{"i": i} for i in range(rds.MAX_ROWS + 50)]
        )
        out = rds.query_underwriting_data(
            {"sql": "with x as (select 1 limit 1) select * from applications"}
        )
        assert out["ok"] is True
        assert out["truncated"] is True
        assert len(out["rows"]) == rds.MAX_ROWS


class TestTransport:
    """Which path is live must be reported, not inferred from a stack trace."""

    def test_data_api_when_a_cluster_arn_is_present(self, monkeypatch) -> None:
        monkeypatch.setenv("AURORA_CLUSTER_ARN", "arn:aws:rds:us-west-2:1:cluster:c")
        assert rds.describe_transport()["transport"] == "rds-data-api"
        assert rds.describe_transport()["vpc_attached"] is False

    def test_socket_when_it_is_not(self, monkeypatch) -> None:
        monkeypatch.delenv("AURORA_CLUSTER_ARN", raising=False)
        described = rds.describe_transport()
        assert described["transport"] == "pg8000-socket"
        # The whole point of this path: a plain RDS instance with no Data API, reached by a
        # VPC-attached Lambda, while the ten runtimes stay networkMode: PUBLIC.
        assert described["vpc_attached"] is True

    def test_a_failure_names_the_transport(self, monkeypatch) -> None:
        monkeypatch.setenv("AURORA_CLUSTER_ARN", "arn:aws:rds:us-west-2:1:cluster:c")
        failure = rds._failure(RuntimeError("Data API is not enabled"))
        assert failure["ok"] is False
        assert failure["transport"] == "rds-data-api"
        assert "hint" in failure


class TestAllowlist:
    def test_it_is_extended_not_replaced_by_the_environment(self, monkeypatch) -> None:
        monkeypatch.setenv("RDS_TABLE_ALLOWLIST", "their_real_table, another ")
        names = rds.allowlist()
        assert "their_real_table" in names
        assert "another" in names
        # The defaults must survive: replacing them would silently drop the signal table the
        # signal path itself reads.
        for default in rds.DEFAULT_ALLOWLIST:
            assert default in names

    def test_an_extended_table_becomes_queryable(self, monkeypatch) -> None:
        monkeypatch.setenv("RDS_TABLE_ALLOWLIST", "pp_underwriting_stream")
        _, error = rds.validate_sql("select * from pp_underwriting_stream")
        assert error is None, error


# ---------------------------------------------------------------------------
# The Gateway contract
# ---------------------------------------------------------------------------


class TestGatewayContract:
    def test_the_schema_file_declares_exactly_the_lambda_s_tools(self) -> None:
        schema = json.loads(
            (REPO / "agentcore" / "tool" / "underwriting_sql_schema.json").read_text()
        )
        declared = {t["name"] for t in schema}
        assert declared == set(rds._TOOLS), (
            "the Gateway advertises tools the Lambda does not implement, or the reverse. "
            "Either way the model calls a name that returns unknown-tool."
        )

    def test_no_write_tool_is_exposed(self) -> None:
        # The write half of what the customer asked for is `persist_adjudication`, driven by
        # the pipeline. Nothing a model can call may modify underwriting data.
        for name in rds._TOOLS:
            assert not any(
                verb in name for verb in ("write", "insert", "update", "delete", "persist")
            ), f"{name} looks like a write tool"

    def test_an_unknown_tool_name_does_not_raise(self) -> None:
        class Context:
            client_context = type(
                "C", (), {"custom": {"bedrockAgentCoreToolName": "UnderwritingSql___nope"}}
            )()

        out = rds.handler({}, Context())
        assert out["ok"] is False
        assert "unknown tool" in out["error"]

    def test_a_missing_client_context_does_not_raise(self) -> None:
        # `agentcore dev` and a direct `aws lambda invoke` both arrive without it.
        out = rds.handler({}, None)
        assert out["ok"] is False

    def test_the_tool_name_is_taken_from_after_the_separator(self) -> None:
        class Context:
            client_context = type(
                "C",
                (),
                {"custom": {"bedrockAgentCoreToolName": "UnderwritingSql___get_schema"}},
            )()

        assert rds._tool_name(Context()) == "get_schema"

    def test_the_target_is_registered_with_the_schema_file_it_ships(self) -> None:
        # Only once connect-rds.sh has run. Skipped rather than failed, because the target is
        # deployment state and the repo is checked out without it.
        config = json.loads((REPO / "agentcore" / "agentcore.json").read_text())
        targets = [
            t
            for gateway in config.get("agentCoreGateways", [])
            for t in gateway.get("targets", [])
            if t.get("name") == "UnderwritingSql"
        ]
        if not targets:
            pytest.skip("UnderwritingSql target not registered yet (run connect-rds.sh)")
        block = targets[0]["lambdaFunctionArn"]
        assert block["toolSchemaFile"] == "agentcore/tool/underwriting_sql_schema.json"
        assert block["lambdaArn"].startswith("arn:aws:lambda:")


# ---------------------------------------------------------------------------
# The property the port rests on
# ---------------------------------------------------------------------------


def _specialist_tool_names() -> list[str]:
    """Francis's tool names, read in a SUBPROCESS.

    Not an in-process import, for a reason that cost real time to find. Francis's bootstrap
    puts her own directory ahead of the repo root on `sys.path`, and `deploy/vendor.sh`
    copies `signal_layer/` beside her `main.py`. So importing her here makes the VENDORED
    `signal_layer` shadow the repo-root one for the rest of the interpreter -- and if that
    copy is stale, every later test importing `signal_layer.sources` fails with
    `ModuleNotFoundError` pointing at the wrong thing entirely.

    A subprocess gets the deployed layout exactly, and cannot leak it back.
    """
    import subprocess  # noqa: PLC0415

    script = (
        "import sys; sys.path.insert(0, %r); sys.path.insert(0, %r)\n"
        "from tools.specialists import SPECIALIST_TOOLS\n"
        "print('\\n'.join(getattr(t, 'tool_name', getattr(t, '__name__', str(t)))"
        " for t in SPECIALIST_TOOLS))\n"
    ) % (str(REPO), str(REPO / "app" / "francis"))

    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        env={**os.environ, "MOCK_MODE": "1"},
        timeout=120,
    )
    if completed.returncode != 0:
        pytest.skip(f"francis tools are not loadable here: {completed.stderr.strip()[-300:]}")
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def test_no_specialist_receives_the_sql_tool() -> None:
    """A specialist with a SQL tool would end the signal/analysis split.

    It could widen its own domain view past what was projected for it, and a text-to-SQL
    round trip would be back inside the per-application minute -- the two things
    `signal_layer/` exists to prevent. This is a one-line change away at any time, so it is
    asserted.
    """
    names = _specialist_tool_names()
    assert len(names) == 8, f"expected the eight specialists, got {sorted(names)}"
    for forbidden in ("get_schema", "query_underwriting_data"):
        assert forbidden not in names


def test_the_specialists_have_no_signal_widening_tool() -> None:
    """Same property, from the other direction: no tool name suggests raw data access."""
    for name in _specialist_tool_names():
        assert not any(w in name.lower() for w in ("sql", "query", "schema", "table")), name


# ---------------------------------------------------------------------------
# The signal path's DDL, generated from the 21-key contract
# ---------------------------------------------------------------------------


class TestGeneratedDDL:
    def test_the_flags_are_integers(self) -> None:
        # They are `Literal[0, 1]` in the contract, not `int`, so a substring test on the
        # annotation's repr produced `text` for all eight -- an integer contract written into
        # a text column that the customer's own reporting would then have to cast.
        from signal_layer.sources.aurora import output_ddl

        ddl = output_ddl("underwriting_report_output")
        flags = [line for line in ddl.splitlines() if "_flag" in line]
        assert len(flags) == 8, flags
        for line in flags:
            assert "integer" in line, line

    def test_the_enum_columns_stay_text(self) -> None:
        # Deliberate: the prompt file says the mid-band is MEDIUM RISK with three
        # recommendation values, the process-guide PDF says POSSIBLE RISK with four. A CHECK
        # constraint would encode one and reject the other while the question is still open.
        from signal_layer.sources.aurora import output_ddl

        ddl = output_ddl("underwriting_report_output")
        for column in ("overall_risk_decision", "recommendation_decision"):
            line = next(line for line in ddl.splitlines() if f'"{column}"' in line)
            assert "text" in line
        assert "check" not in ddl.lower()

    def test_every_contract_key_becomes_a_column(self) -> None:
        from agents.contracts import Adjudication
        from signal_layer.sources.aurora import output_ddl

        ddl = output_ddl("underwriting_report_output")
        for field in Adjudication.model_fields:
            assert f'"{field}"' in ddl, f"{field} is in the contract but not in the DDL"

    def test_application_id_is_a_column_but_not_a_contract_key(self) -> None:
        from agents.contracts import Adjudication
        from signal_layer.sources.aurora import output_ddl

        assert "application_id" not in Adjudication.model_fields
        assert '"application_id" text not null' in output_ddl("underwriting_report_output")


class TestSignalModeSeam:
    def test_aurora_raises_rather_than_falling_back_to_fixtures(self, monkeypatch) -> None:
        """The failure mode that must never be silent.

        A fall back to fixtures would produce an adjudication computed from demo data while
        everyone in the room believed it came from the staging cluster, and nothing in the
        output would say so.
        """
        monkeypatch.setenv("SIGNAL_MODE", "aurora")
        monkeypatch.delenv("AURORA_CLUSTER_ARN", raising=False)

        from app.runtime_support import build_signal_payload
        from signal_layer.sources.aurora import AuroraNotConfigured

        with pytest.raises(AuroraNotConfigured) as caught:
            build_signal_payload("APP-1004")
        assert "connect-rds.sh" in str(caught.value)

    def test_a_table_name_must_be_an_identifier(self) -> None:
        # A table name cannot be a bound parameter, so it is interpolated -- which makes the
        # allowlist the only available control.
        from signal_layer.sources.aurora import AuroraNotConfigured, _quoted_identifier

        assert _quoted_identifier("application_signals") == '"application_signals"'
        assert _quoted_identifier("pp.signals") == '"pp"."signals"'
        for hostile in ('a"; drop table x --', "a b", "a-b", "1abc", "a;b", ""):
            with pytest.raises(AuroraNotConfigured):
                _quoted_identifier(hostile)
