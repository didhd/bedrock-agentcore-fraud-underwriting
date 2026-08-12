"""Guards for the Snowflake signal source and the SIGNAL_MODE seam.

The point of this file is one property: **`SIGNAL_MODE=cortex` must never silently produce
fixture data.** An adjudication computed from 14 demo applications while everyone believes
it came from the consortium is the single worst thing this port could do, and nothing in
the output would reveal it. So the cortex path raises where every other path degrades.

The rest pins the two facts that made the previous integration fictional, recorded in
CLAUDE.md: Cortex Analyst returns generated SQL rather than rows, and SigV4 cannot
authenticate to Snowflake.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_the_default_mode_is_fixtures_and_needs_no_credentials(monkeypatch):
    monkeypatch.delenv("SIGNAL_MODE", raising=False)
    from app.runtime_support import build_signal_payload

    _payload, record, source = build_signal_payload("APP-1004")
    assert source == "fixtures.loader"
    assert record and record.get("signals")


def test_cortex_mode_raises_rather_than_falling_back(monkeypatch):
    """The whole reason this module is strict.

    Every other failure in this codebase degrades: a specialist that dies leaves 7-of-8, a
    missing gateway loses two tools, absent memory loses continuity. This one cannot, because
    the degraded result is indistinguishable from the real one.
    """
    monkeypatch.setenv("SIGNAL_MODE", "cortex")
    monkeypatch.setenv("SNOWFLAKE_SECRET_ID", "pp-fraud/does-not-exist-in-any-account")

    from signal_layer.sources import snowflake

    snowflake._CACHE = None
    from app.runtime_support import build_signal_payload

    with pytest.raises(snowflake.SnowflakeNotConfigured) as caught:
        build_signal_payload("APP-1004")
    # The message has to name the fix, not just the failure.
    assert "connect-snowflake.sh" in str(caught.value)


@pytest.mark.parametrize("mode", ["fixtures", "", "FIXTURES", "nonsense"])
def test_only_the_exact_string_cortex_selects_snowflake(monkeypatch, mode):
    """A typo must not route production traffic at an unconfigured Snowflake."""
    monkeypatch.setenv("SIGNAL_MODE", mode)
    from app.runtime_support import build_signal_payload

    _payload, _record, source = build_signal_payload("APP-1004")
    assert source == "fixtures.loader"


def test_the_secret_requires_exactly_the_documented_fields(monkeypatch):
    """`account_url`, `pat`, `semantic_view` are required; `warehouse` is not.

    Four values are asked for, three are structurally necessary -- the warehouse only
    matters for executing the generated SQL and Snowflake may supply a default.
    """
    from signal_layer.sources import snowflake

    for incomplete in (
        {"pat": "x", "semantic_view": "V"},
        {"account_url": "https://a.snowflakecomputing.com", "semantic_view": "V"},
        {"account_url": "https://a.snowflakecomputing.com", "pat": "x"},
    ):
        snowflake._CACHE = None
        monkeypatch.setattr(
            snowflake, "config", snowflake.config
        )  # keep the real function; patch boto3 below

        class FakeClient:
            def get_secret_value(self, SecretId):  # noqa: N803
                return {"SecretString": json.dumps(incomplete)}

        import boto3

        monkeypatch.setattr(boto3, "client", lambda *a, **k: FakeClient())
        with pytest.raises(snowflake.SnowflakeNotConfigured) as caught:
            snowflake.config()
        assert "missing" in str(caught.value)

    snowflake._CACHE = None


def test_the_pat_is_sent_as_a_programmatic_access_token():
    """The header type is what makes Snowflake treat it as a PAT rather than OAuth."""
    source = (ROOT / "signal_layer" / "sources" / "snowflake.py").read_text(encoding="utf-8")
    assert "X-Snowflake-Authorization-Token-Type" in source
    assert "PROGRAMMATIC_ACCESS_TOKEN" in source
    # And no attempt to SIGN with SigV4, which Snowflake has nothing to verify it against.
    # Checked as the absence of signing CODE, not of the word: the docstring explains why
    # SigV4 is impossible here, so the word legitimately appears.
    for signing in ("SigV4Auth", "botocore.auth", "aws4_request", "hmac.new"):
        assert signing not in source, f"{signing!r} suggests an attempt to SigV4-sign Snowflake"


def test_two_calls_analyst_then_sql():
    """Cortex Analyst returns SQL; a second call runs it.

    Asserted structurally because collapsing this into one call is exactly the mistake
    CLAUDE.md records as having made the previous integration fictional.
    """
    from signal_layer.sources import snowflake

    assert hasattr(snowflake, "ask_analyst")
    assert hasattr(snowflake, "run_sql")
    assert snowflake._ANALYST_PATH.endswith("/cortex/analyst/message")
    assert snowflake._SQL_PATH.endswith("/statements")

    source = (ROOT / "signal_layer" / "sources" / "snowflake.py").read_text(encoding="utf-8")
    builder = source[source.index("def build_cortex_payload") :]
    assert "ask_analyst(" in builder and "run_sql(" in builder
    assert builder.index("ask_analyst(") < builder.index("run_sql("), "SQL is generated first"


def test_the_guardrails_stay_in_snowflake_and_are_only_recorded_here():
    """Nothing in this module may reimplement a semantic-layer guardrail.

    They are the institutional knowledge the migration must not lose, which is the argument
    for leaving the semantic view in Snowflake. Listing them is fine; enforcing them here
    would create a second, drifting source of truth.
    """
    from signal_layer.sources.snowflake import EXPECTED_GUARDRAILS

    assert len(EXPECTED_GUARDRAILS) >= 7
    joined = " ".join(EXPECTED_GUARDRAILS).lower()
    for expected in ("hashed ssn", "hash_lender_id", "fake", "lender names", "address", "dealers"):
        assert expected in joined

    source = (ROOT / "signal_layer" / "sources" / "snowflake.py").read_text(encoding="utf-8")
    # A guardrail implemented here would look like SQL. There is none.
    for sql_ish in ("WHERE ", " JOIN ", "GROUP BY"):
        assert sql_ish not in source, f"{sql_ish!r} suggests a guardrail was reimplemented"


def test_the_connect_script_probes_before_enabling():
    """`--enable` must not flip the mode on an unverified key.

    Enabling first and discovering the key is wrong later means the discovery happens inside
    an agent invocation, where a bad warehouse looks like a broken agent.
    """
    script = (ROOT / "deploy" / "connect-snowflake.sh").read_text(encoding="utf-8")
    enable = script[script.index("--enable)") :]
    enable = enable[: enable.index("exit 0")]
    assert "probe" in enable and enable.index("probe") < enable.index("set_mode")
