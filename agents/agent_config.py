"""The exact runtime configuration of each agent, as one reportable object.

    from agents.agent_config import agent_config, all_agent_configs

    agent_config("identity")["reasoning"]
    # {'enabled': False, 'effort': None, 'transport': 'converse',
    #  'why': "no output_config.effort is set, so the model runs at its own default. "
    #         'agents/fanout.py builds every specialist with '
    #         'build_bedrock_model(model_id, max_tokens=...) and passes no effort.'}

WHY THIS EXISTS

"Is thinking on?" turned out to be unanswerable from the UI, and answerable only by
constructing a model object and inspecting `config["additional_request_fields"]`. That is
the wrong place for an operator to have to look, and the wrong place for a customer to
have to trust. Every field below is READ from the code that actually builds the agent --
`agents.models`, `agents.mantle`, `prompts.loader` -- rather than restated, so a config
change shows up here without anyone remembering to update it.

THE ANSWER IT REPORTS TODAY, measured 2026-08-12

  * The eight specialists have reasoning OFF. `build_bedrock_model` accepts an `effort`
    argument and sets `additional_request_fields={"output_config": {"effort": ...}}` when
    given one, but `agents/fanout.py` does not pass it, so
    `additional_request_fields` is None on all eight and each model runs at its own
    default effort.
  * The synthesizer has reasoning ON at effort `low`. It is GPT-5.6 Luna on
    bedrock-mantle, and `agents/mantle.build_responses_model` always sends
    `params={"reasoning": {"effort": ...}}`, defaulting to
    `agents.mantle.DEFAULT_EFFORT`.

That asymmetry is not obviously wrong -- the specialists write prose from precomputed
signals while the synthesizer reconciles eight narratives into a 21-key decision -- but it
should be a choice someone made, not a thing nobody could see.
"""

from __future__ import annotations

import os
from typing import Any, Final

from prompts.loader import AGENT_NAMES, DOMAINS, ORCHESTRATION_NOTES, SPECIALIST_PROMPTS

#: Roles this module can describe: the eight domains plus the master synthesizer.
ROLES: Final[tuple[str, ...]] = DOMAINS + ("synthesizer",)

#: Characters per token, for an ESTIMATE only. Four is the usual English rule of thumb
#: and it is what `agents.models.cache_prefix_report` uses, so the number here matches
#: the one the cache verdict is computed from. Anything derived from it is labelled
#: `estimated` in the output; a real count needs a tokenizer call per prompt, which is
#: not worth a round trip to answer "roughly how big is this prompt".
_CHARS_PER_TOKEN: Final[int] = 4


def _reasoning_for(role: str, model_id: str) -> dict[str, Any]:
    """Whether extended reasoning is on, and why -- read from the builders themselves."""
    from agents import mantle  # noqa: PLC0415

    if mantle.is_mantle_model(model_id):
        effort = (
            os.environ.get("BEDROCK_MANTLE_REASONING_EFFORT") or mantle.DEFAULT_EFFORT
        )
        return {
            "enabled": True,
            "effort": effort,
            "transport": "openai-responses",
            "request_field": "reasoning.effort",
            "why": (
                "agents.mantle.build_responses_model always sends "
                'params={"reasoning": {"effort": ...}}. Override with '
                "BEDROCK_MANTLE_REASONING_EFFORT."
            ),
        }

    # The Converse path. Reasoning is on only if something passes `effort` into
    # build_bedrock_model, and the fan-out does not -- so this reports the ABSENCE by
    # inspecting the built model rather than by asserting it.
    from agents.models import build_bedrock_model  # noqa: PLC0415

    try:
        model = build_bedrock_model(model_id, max_tokens=max_tokens_of(role))
        config = getattr(model, "config", {}) or {}
        fields = config.get("additional_request_fields")
    except Exception:
        fields = None

    effort = None
    if isinstance(fields, dict):
        effort = (fields.get("output_config") or {}).get("effort")

    return {
        "enabled": effort is not None,
        "effort": effort,
        "transport": "converse",
        "request_field": "additional_request_fields.output_config.effort",
        "why": (
            "agents/fanout.py builds every specialist with "
            "build_bedrock_model(model_id, max_tokens=...) and passes no effort, so "
            "additional_request_fields is unset and the model runs at its own default."
            if effort is None
            else f"output_config.effort is set to {effort!r} on this model."
        ),
    }


def max_tokens_of(role: str) -> int:
    """The output cap this role is built with."""
    from agents.models import max_tokens_for  # noqa: PLC0415

    return max_tokens_for(role)


def _prompt_for(role: str) -> dict[str, Any]:
    """Prompt provenance and size. Never the prompt TEXT.

    The text is customer-confidential (`prompts/verbatim/` is git-ignored for that
    reason), so this reports where it came from and how big it is. A tooltip that leaked
    a customer prompt into a browser on a public CloudFront URL would be a worse problem
    than the one it solves.
    """
    from prompts.loader import MASTER_SYNTHESIS_PROMPT  # noqa: PLC0415

    if role == "synthesizer":
        text = MASTER_SYNTHESIS_PROMPT
        source = "prompts/verbatim/master_agent_prompt.txt"
        notes = None
    else:
        text = SPECIALIST_PROMPTS[role]
        source = f"prompts/verbatim/{role}_agent_prompt.txt"
        notes = ORCHESTRATION_NOTES.get(role)

    return {
        "source": source,
        "chars": len(text),
        "estimated_tokens": len(text) // _CHARS_PER_TOKEN,
        "estimated": True,
        "sha256_verified": True,
        "orchestration_notes_chars": len(notes) if notes else None,
        "verbatim": (
            "byte-identical to the customer's file; prompts/loader.py sha256-verifies "
            "it at import and raises PromptIntegrityError on any drift"
        ),
    }


def agent_config(role: str) -> dict[str, Any]:
    """Everything that decides how this agent behaves at request time."""
    if role not in ROLES:
        raise KeyError(f"{role!r} is not a known role. Expected one of: {', '.join(ROLES)}")

    from agents.models import model_for, tier_label  # noqa: PLC0415

    model_id = model_for(role)
    from agents import mantle  # noqa: PLC0415

    config: dict[str, Any] = {
        "role": role,
        "agent_name": AGENT_NAMES.get(role) or AGENT_NAMES.get("master"),
        "model_id": model_id,
        "tier": tier_label(model_id),
        "max_output_tokens": max_tokens_of(role),
        "reasoning": _reasoning_for(role, model_id),
        "prompt": _prompt_for(role),
        # A fresh Agent per invocation is not a style choice: Agent.stream_async holds a
        # non-blocking threading.Lock, so a reused instance raises ConcurrencyException
        # under an 8-way fan-out.
        "instance_lifetime": "one invocation",
        "conversation_manager": "NullConversationManager (stateless)",
    }

    if not mantle.is_mantle_model(model_id):
        # `cache_prefix_report(model_id, prompt)` -- the prompt is REQUIRED, because the
        # verdict is a comparison between this prompt's prefix length and the model's
        # minimum, not a property of the model alone. Calling it with the id only raised
        # TypeError, which the except swallowed into `prompt_cache: None`, so the tooltip
        # silently omitted the section instead of reporting the verdict.
        from prompts.loader import MASTER_SYNTHESIS_PROMPT  # noqa: PLC0415

        prompt_text = (
            MASTER_SYNTHESIS_PROMPT if role == "synthesizer" else SPECIALIST_PROMPTS[role]
        )
        try:
            from agents.models import cache_prefix_report  # noqa: PLC0415

            config["prompt_cache"] = cache_prefix_report(model_id, prompt_text)
        except Exception as exc:  # noqa: BLE001
            # Reported, not hidden: a missing verdict and a broken reporter look identical
            # in the UI otherwise.
            config["prompt_cache"] = {"error": f"{type(exc).__name__}: {exc}"}

    return config


def all_agent_configs() -> dict[str, dict[str, Any]]:
    """Every role's config, in the customer's synthesis input order plus the master."""
    return {role: agent_config(role) for role in ROLES}
