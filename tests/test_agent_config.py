"""Guards for the per-agent configuration reporter.

This module exists because "is thinking on for these agents?" could not be answered from
anywhere an operator or a customer would look. It was answerable only by building a model
object in Python and inspecting `config["additional_request_fields"]`.

So the reporter READS the builders rather than restating them, and these tests pin the two
properties that make it worth trusting:

  1. it reports what the code actually does, not what someone typed here;
  2. it never carries prompt TEXT, because it is rendered in a browser on a public URL.

The second one is the reason this file is strict about it. `prompts/verbatim/` is
git-ignored precisely because those files are customer-confidential, and a tooltip that
leaked one would be a worse problem than the one the tooltip solves.
"""

from __future__ import annotations

import json

import pytest

from agents.agent_config import ROLES, agent_config, all_agent_configs
from prompts.loader import DOMAINS, MASTER_SYNTHESIS_PROMPT, SPECIALIST_PROMPTS


def test_every_role_is_reportable():
    configs = all_agent_configs()
    assert set(configs) == set(ROLES)
    assert len(configs) == len(DOMAINS) + 1, "eight specialists plus the synthesizer"


@pytest.mark.parametrize("role", DOMAINS)
def test_specialist_reasoning_state_matches_the_built_model(role):
    """The reported state must come from the model object, not from a constant here.

    Cross-checked against `build_bedrock_model` directly: if `fanout.py` ever starts
    passing `effort`, this test keeps passing and the reporter starts saying "on" -- which
    is the whole point of reading rather than restating.
    """
    from agents.models import build_bedrock_model, max_tokens_for, model_for

    reported = agent_config(role)["reasoning"]
    model = build_bedrock_model(model_for(role), max_tokens=max_tokens_for(role))
    fields = (getattr(model, "config", {}) or {}).get("additional_request_fields")
    actual_effort = (
        (fields.get("output_config") or {}).get("effort") if isinstance(fields, dict) else None
    )

    assert reported["effort"] == actual_effort
    assert reported["enabled"] is (actual_effort is not None)
    assert reported["transport"] == "converse"


def test_the_synthesizer_reports_reasoning_on():
    """GPT-5.6 on mantle always sends reasoning.effort, so the report must say so."""
    from agents import mantle
    from agents.models import model_for

    model_id = model_for("synthesizer")
    if not mantle.is_mantle_model(model_id):
        pytest.skip(f"synthesizer is {model_id}, not a mantle model")

    reasoning = agent_config("synthesizer")["reasoning"]
    assert reasoning["enabled"] is True
    assert reasoning["effort"] == mantle.DEFAULT_EFFORT
    assert reasoning["transport"] == "openai-responses"


def test_the_report_explains_itself_not_just_its_verdict():
    """"off" invites "is that a bug?", so the reason travels with the value."""
    for role in ROLES:
        why = agent_config(role)["reasoning"]["why"]
        assert why and len(why) > 40, f"{role}: no explanation of the reasoning state"
        assert "effort" in why


@pytest.mark.parametrize("role", ROLES)
def test_max_output_tokens_matches_the_builder(role):
    from agents.models import max_tokens_for

    assert agent_config(role)["max_output_tokens"] == max_tokens_for(role)


# ---------------------------------------------------------------------------
# The confidentiality guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ROLES)
def test_no_prompt_text_is_ever_included(role):
    """Not one sentence of a customer prompt may appear in the report.

    Checked as SHINGLES, not as a whole-string containment: a report that leaked a single
    paragraph would pass a naive `prompt not in json` check while still publishing
    confidential text. Twelve words is the same window tests/test_docs_site.py uses.
    """
    serialized = json.dumps(agent_config(role))
    text = MASTER_SYNTHESIS_PROMPT if role == "synthesizer" else SPECIALIST_PROMPTS[role]
    words = text.split()

    leaked = []
    for start in range(0, max(0, len(words) - 12)):
        shingle = " ".join(words[start : start + 12])
        if shingle and shingle in serialized:
            leaked.append(shingle)
    assert not leaked, f"{role}: prompt text in the config report: {leaked[:2]}"


@pytest.mark.parametrize("role", ROLES)
def test_prompt_is_described_by_provenance_and_size(role):
    """Describing the prompt is the substitute for quoting it, so it must be complete."""
    prompt = agent_config(role)["prompt"]
    assert prompt["source"].startswith("prompts/verbatim/")
    assert prompt["chars"] > 0
    assert prompt["estimated_tokens"] > 0
    assert prompt["estimated"] is True, "the token count is a 4-chars-per-token estimate"
    assert prompt["sha256_verified"] is True

    text = MASTER_SYNTHESIS_PROMPT if role == "synthesizer" else SPECIALIST_PROMPTS[role]
    assert prompt["chars"] == len(text), "size must be measured, not approximated"


# ---------------------------------------------------------------------------
# The prompt-cache verdict
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", DOMAINS)
def test_prompt_cache_verdict_is_reported_not_swallowed(role):
    """A missing verdict and a broken reporter must not look identical.

    `cache_prefix_report(model_id, prompt)` takes the prompt as a REQUIRED argument --
    the verdict is a comparison between this prompt's prefix and the model's minimum, not
    a property of the model. Calling it with the id alone raised TypeError, which an
    `except` turned into `prompt_cache: None`, so the tooltip silently omitted the whole
    section. Any error now travels as data.
    """
    cache = agent_config(role).get("prompt_cache")
    assert cache is not None, f"{role}: no prompt-cache verdict at all"
    assert "error" not in cache, f"{role}: reporter failed: {cache.get('error')}"
    assert isinstance(cache["will_cache"], bool)
    assert cache["prompt_tokens"] > 0
    assert cache["min_cacheable_tokens"] > 0


def test_the_cache_verdict_agrees_with_the_repos_own_finding():
    """Caching engages on ZERO of the eight, and that is a measured claim.

    Every specialist prompt is smaller than its model's minimum cacheable prefix, so a
    cachePoint is accepted silently, reports cacheWriteInputTokens=0, and every call pays
    full input price. If a prompt grows past the minimum this test fails -- which is the
    correct outcome, because at that point the repo's documented finding is stale.
    """
    engaged = [
        role for role in DOMAINS if (agent_config(role).get("prompt_cache") or {}).get("will_cache")
    ]
    assert engaged == [], (
        f"prompt caching now engages for {engaged}; re-measure and update the claim in "
        "CLAUDE.md and the docs before quoting a caching benefit"
    )


# ---------------------------------------------------------------------------
# Delivery to both surfaces
# ---------------------------------------------------------------------------


def test_the_local_server_attaches_config_to_run_started():
    ui_server = pytest.importorskip("ui.server")
    config = ui_server._agent_config_of("identity")
    assert config is not None
    assert config["role"] == "identity"
    assert "reasoning" in config


def test_the_edge_bundle_forwards_config_and_never_the_prompt():
    """The CloudFront path must show the same facts, from bootstrap.json."""
    from pathlib import Path

    proxy = (
        Path(__file__).resolve().parent.parent
        / "deploy"
        / "cdk"
        / "lambda"
        / "stream-proxy.mjs"
    ).read_text(encoding="utf-8")
    assert "config: spec.config ?? null" in proxy, (
        "run_started must carry each agent's config at the edge, or the deployed tooltip "
        "differs from the local one"
    )
    assert "synthesizer_config" in proxy


def test_the_reducer_preserves_config_after_an_agent_completes():
    """The config arrives ONCE, on run_started. Nothing may reset it later.

    It was reset in `mergeCompleted`, so every tooltip fell back to "Per-agent
    configuration was not reported by this backend" the moment its agent finished --
    visible only while the card was still running, which is the least useful window and
    reads as a backend limitation rather than a bug.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent / "ui" / "src" / "hooks" / "useRun.ts"
    ).read_text(encoding="utf-8")

    start = source.index("function mergeCompleted")
    body = source[start : source.index("\n}", start)]
    assert "config: prior.config" in body, (
        "mergeCompleted must carry the config forward; no completion frame supplies it"
    )
    assert "config: null" not in body, "mergeCompleted must not reset the config"

    # blankCard is the ONE place a null config is correct: a card that exists before
    # run_started genuinely has no config yet.
    blank = source[source.index("function blankCard") :]
    blank = blank[: blank.index("\n}")]
    assert "config: null" in blank
