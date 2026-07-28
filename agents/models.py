"""
Per-agent model selection for the fraud pipeline.

Snowflake Intelligence pins every Cortex Agent to one LLM class. Amazon Bedrock
lets each of the nine agents run on a right-sized model, so this module owns two
decisions and nothing else: which model id each agent gets, and how a
`BedrockModel` is constructed so an eight-way fan-out does not throttle itself.

Three things here are deliberate and load-bearing:

1. **The `global.*` inference profile is the default.** `us.*` and `global.*`
   address the same weights but `us.*` bills at exactly ``REGIONAL_MULTIPLIER``
   (1.10x) on every rate category. The prefix is env-overridable because a
   customer with a data-residency requirement needs `us.*`; the multiplier lives
   next to it so `agents.pricing` can charge the right rate either way instead of
   silently under-reporting by 9.09%.

2. **Tier assignment is justified from the customer's own prompt text, or it is
   labelled unjustified.** Every entry in ``TIER_RATIONALE`` quotes the sentence
   in ``docs/<domain>_agent_prompt.txt`` that motivates it. Four agents have no
   such sentence; they sit at MID and say so.

   Tiering is *not* claimed to save money here. It is claimed to right-size each
   agent, which is a latency and quality argument, not a cost one. The rate card
   makes the cost argument impossible for this shape: Haiku->Sonnet is a
   2.00/10.00 per-MTok step down and Sonnet->Opus is the same 2.00/10.00 step up,
   so two LIGHT agents fund exactly two HEAVY agents at equal token counts, and
   the structural saving is $0.00. Worse, straw's own prompt caps it at "2-3
   sentences max" while synthetic and rings write "detailed analysis" - so the
   LIGHT agents carry fewer tokens than the HEAVY ones and the shape is mildly
   *negative*. ``agents.pricing.compare_tiered_vs_uniform`` decomposes any
   reported saving into ``structural_saving_usd`` (the shape, ~zero) and
   ``model_saving_usd`` (the MID model generation, currently everything), from
   real usage. Nothing in this module asserts a dollar figure.

3. **There is no latency table.** A previous revision carried
   ``NOMINAL_LATENCY_S = {haiku: 0.9, sonnet: 2.5, opus: 6.0}`` and derived a
   customer-facing "26s sequential -> 8.5s parallel" headline from it. Those
   figures were invented and measurement contradicts their ordering (Sonnet 4.6
   is slower than Opus 4.8 on a real fraud prompt). Latency numbers now come from
   one place - the benchmark harness, which records
   ``result.metrics.accumulated_metrics["latencyMs"]`` and wall clock from live
   invocations. Nothing in this module may be used to predict latency.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Final

# ---------------------------------------------------------------------------
# Inference profile prefix
# ---------------------------------------------------------------------------

#: `us.*` cross-region inference costs exactly this multiple of `global.*` on
#: every rate category (input, output, cache read, cache write). Verified against
#: the published Bedrock rate card; `agents.pricing` applies it.
REGIONAL_MULTIPLIER: Final[float] = 1.10

GLOBAL_PREFIX: Final[str] = "global."
US_PREFIX: Final[str] = "us."
SUPPORTED_PREFIXES: Final[tuple[str, ...]] = (GLOBAL_PREFIX, US_PREFIX)


def _resolve_prefix() -> str:
    """Read BEDROCK_MODEL_PREFIX, defaulting to the cheaper `global.` profile.

    Raises loudly on an unknown value: a silently-accepted typo would route every
    agent to a model id that does not exist and cost math that cannot be trusted.
    """
    raw = os.environ.get("BEDROCK_MODEL_PREFIX", GLOBAL_PREFIX)
    prefix = raw if raw.endswith(".") else f"{raw}."
    if prefix not in SUPPORTED_PREFIXES:
        raise ValueError(
            f"BEDROCK_MODEL_PREFIX={raw!r} is not supported. "
            f"Use one of {', '.join(repr(p) for p in SUPPORTED_PREFIXES)}."
        )
    return prefix


MODEL_PREFIX: Final[str] = _resolve_prefix()

# ---------------------------------------------------------------------------
# Model ids
#
# Base ids carry no inference-profile prefix. Every one is live-invokable in
# us-east-1 and us-west-2. Current generation is 5.x; the 4.x ids are kept
# because Sonnet 4.6 is the source system's model and therefore the honest
# cost baseline, and because Opus 4.8 remains a valid HEAVY alternative.
# ---------------------------------------------------------------------------

BASE_HAIKU_4_5: Final[str] = "anthropic.claude-haiku-4-5-20251001-v1:0"
BASE_SONNET_5: Final[str] = "anthropic.claude-sonnet-5"
BASE_SONNET_4_6: Final[str] = "anthropic.claude-sonnet-4-6"
BASE_OPUS_5: Final[str] = "anthropic.claude-opus-5"
BASE_OPUS_4_8: Final[str] = "anthropic.claude-opus-4-8"
BASE_OPUS_4_7: Final[str] = "anthropic.claude-opus-4-7"

BASE_MODEL_IDS: Final[tuple[str, ...]] = (
    BASE_HAIKU_4_5,
    BASE_SONNET_5,
    BASE_SONNET_4_6,
    BASE_OPUS_5,
    BASE_OPUS_4_8,
    BASE_OPUS_4_7,
)


def with_prefix(base_model_id: str, prefix: str | None = None) -> str:
    """Attach an inference-profile prefix to a base model id."""
    return f"{prefix or MODEL_PREFIX}{base_model_id}"


def prefix_of(model_id: str) -> str:
    """Return the inference-profile prefix carried by ``model_id`` ('' if none).

    Cost math keys off this rather than off ``MODEL_PREFIX`` so a model id built
    with an explicit prefix, or read back from a live response, is still priced
    correctly.
    """
    for candidate in SUPPORTED_PREFIXES:
        if model_id.startswith(candidate):
            return candidate
    return ""


def base_model_id(model_id: str) -> str:
    """Strip any inference-profile prefix from ``model_id``."""
    return model_id[len(prefix_of(model_id)) :]


# Tier defaults. LIGHT/MID/HEAVY are env-overridable with a full model id
# (prefix included) for operators who need to pin something else.
LIGHT_MODEL: Final[str] = os.environ.get("BEDROCK_MODEL_LIGHT", with_prefix(BASE_HAIKU_4_5))
MID_MODEL: Final[str] = os.environ.get("BEDROCK_MODEL_MID", with_prefix(BASE_SONNET_5))
HEAVY_MODEL: Final[str] = os.environ.get("BEDROCK_MODEL_HEAVY", with_prefix(BASE_OPUS_5))

#: The source system runs Claude Sonnet 4.6 across all nine agents. Any
#: "tiering saves money" claim must be measured against this, not against our
#: own MID tier.
BASELINE_MODEL: Final[str] = os.environ.get("BEDROCK_MODEL_BASELINE", with_prefix(BASE_SONNET_4_6))

TIER_OF: Final[dict[str, str]] = {
    LIGHT_MODEL: "light",
    MID_MODEL: "mid",
    HEAVY_MODEL: "heavy",
}

# ---------------------------------------------------------------------------
# Per-agent assignment
# ---------------------------------------------------------------------------

DOMAINS: Final[tuple[str, ...]] = (
    "identity",
    "dealer",
    "straw",
    "employment",
    "income",
    "synthetic",
    "bustout",
    "rings",
)

SPECIALIST_MODELS: Final[dict[str, str]] = {
    "identity": MID_MODEL,
    "dealer": LIGHT_MODEL,
    "straw": LIGHT_MODEL,
    "employment": MID_MODEL,
    "income": MID_MODEL,
    "synthetic": HEAVY_MODEL,
    "bustout": MID_MODEL,
    "rings": HEAVY_MODEL,
}

#: GPT-5.6 Luna on the bedrock-mantle endpoint. See agents/mantle.py.
#:
#: This is the one model choice in the repo made on a MEASUREMENT rather than on a
#: reading of the customer's prompt. The synthesizer is the pipeline's bottleneck: it
#: consumes all eight specialist narratives (~28K input tokens) and emits the 21-key
#: object. Measured on APP-1004 with the same eight analyses:
#:
#:     Claude Sonnet 5   55.2s   $0.11731   21 keys, HIGH RISK / DECLINE
#:     GPT-5.6 Luna       6.1s   $0.01488   21 keys, HIGH RISK / DECLINE
#:
#: ~9x faster for an identical verdict, and the difference between a 101s end-to-end
#: and a 37.5s one -- i.e. the difference between missing and meeting the customer's
#: sub-minute target. The eight specialists deliberately stay on Claude: they ARE the
#: fraud analysis, and no accuracy comparison justifies moving them.
#:
#: What is NOT claimed: that Luna reasons as well as Sonnet 5 on this task. One
#: application agreed. Before this ships to the customer, run the calibration judge in
#: evals/ across the fixture set on both.
#:
#: Set BEDROCK_MODEL_SYNTHESIZER to a Claude id to go back to the Converse path; the
#: routing in agents/synthesize.py follows the id, not a flag.
DEFAULT_SYNTHESIZER_MODEL: Final[str] = "openai.gpt-5.6-luna"
SYNTHESIZER_MODEL: Final[str] = os.environ.get(
    "BEDROCK_MODEL_SYNTHESIZER", DEFAULT_SYNTHESIZER_MODEL
)

ROLES: Final[tuple[str, ...]] = DOMAINS + ("synthesizer",)

#: Why each agent sits where it sits, quoting the customer prompt that decides
#: it. Where no sentence in their prompt discriminates between tiers, the entry
#: says so explicitly instead of inventing a reason - an unjustified tier is a
#: thing to measure, not a thing to assert.
TIER_RATIONALE: Final[dict[str, str]] = {
    "straw": (
        "LIGHT - justified. straw_agent_prompt.txt: 'If no co-borrower exists, straw risk is "
        "inherently minimal - keep analysis to 2-3 sentences.' and OUTPUT: 'No co-borrower cases: "
        "2-3 sentences max stating straw risk is minimal.' A large share of applications carry no "
        "co-borrower at all, and for those the prompt itself defines the task as a presence check "
        "plus two sentences."
    ),
    "dealer": (
        "LIGHT - justified. dealer_agent_prompt.txt SIGNAL INTERPRETATION is a banded lookup with "
        "explicit numeric cut-points ('dealer_consortium_score: 0-500 low, 500-700 moderate, "
        "700-900 elevated, 900+ high'), and its CRITICAL DISTINCTION is a single suppression rule: "
        "'Do NOT escalate fraud risk classification based solely on dealer default rates or "
        "consortium scores.' Threshold reading plus one anti-escalation rule, not multi-signal "
        "inference."
    ),
    "synthetic": (
        "HEAVY - justified. synthetic_agent_prompt.txt requires joint reasoning across signals "
        "('has_significant_identity_inconsistency: Only concerning when corroborated by other "
        "synthetic indicators (thin file + reuse + velocity)') and simultaneous de-duplication of "
        "correlated signals ('Multiple signals from the same underlying pattern (e.g. thin file "
        "triggering both is_thin_file and very_new_file) = ONE concern')."
    ),
    "rings": (
        "HEAVY - justified. rings_agent_prompt.txt is network reasoning under an explicit no-query "
        "constraint: 'Do NOT recompute network relationships through excessive queries', while "
        "still requiring 'Repeated exact-unit reuse, coordinated multi-identifier overlap, or "
        "highly connected borrower clusters supported by corroborating inconsistencies' to be "
        "distinguished from benign overlap across four separate reuse counters."
    ),
    "identity": (
        "MID - not justified by the prompt. identity_agent_prompt.txt is per-signal threshold "
        "interpretation plus one de-duplication rule; that evidence does not discriminate between "
        "LIGHT and MID. MID is the source system's model class, so it is the conservative default. "
        "Candidate for a measured LIGHT trial."
    ),
    "income": (
        "MID - not justified by the prompt. income_agent_prompt.txt is likewise banded thresholds "
        "('ssn_income_overstatement_percentage: <15% is normal... >25% is materially suspicious') "
        "plus a de-duplication rule. No prompt evidence for a tier change; MID is the conservative "
        "default."
    ),
    "employment": (
        "MID - not justified by the prompt. employment_agent_prompt.txt adds an unverifiable-vs-"
        "fraudulent suppression rule on top of banded thresholds, which is closer to dealer's "
        "shape than to synthetic's, but nothing in the text settles LIGHT vs MID. Held at MID "
        "pending measurement."
    ),
    "bustout": (
        "MID - arguably under-tiered. bustout_agent_prompt.txt is the longest specialist prompt "
        "(3825 chars), names seven KEY SIGNALS TO PRIORITIZE, and asks for corroboration across "
        "velocity, income trend and loan exposure simultaneously - the same shape that puts "
        "synthetic and rings on HEAVY. It stays at MID because no accuracy measurement exists to "
        "justify the cost increase. This is the first tier decision to revisit."
    ),
    "synthesizer": (
        "MID - not justified by measurement. master_agent_prompt.txt DECISIONING requires weighing "
        "corroboration across eight narratives and forbids volume-based escalation ('Do not assign "
        "HIGH RISK based on isolated signals, alert volume, overlapping findings'), which argues "
        "for HEAVY. Held at MID to match the source system until a tier comparison is run; "
        "BEDROCK_MODEL_SYNTHESIZER overrides it."
    ),
}


def model_for(role: str) -> str:
    """Return the configured model id for a domain or for 'synthesizer'."""
    if role == "synthesizer":
        return SYNTHESIZER_MODEL
    return SPECIALIST_MODELS.get(role, MID_MODEL)


def tier_label(model_id: str) -> str:
    """Tier name for a model id ('light' / 'mid' / 'heavy' / 'custom')."""
    return TIER_OF.get(model_id, "custom")


def rationale_for(role: str) -> str:
    """Return the documented tier rationale for a role."""
    return TIER_RATIONALE.get(role, "no rationale recorded")


def model_assignment_table() -> str:
    """Render the per-agent model configuration for display.

    Deliberately carries no latency or cost column: neither can be known from
    configuration alone.
    """
    rows = [
        "Per-agent model configuration:",
        f"  {'agent':<13}{'tier':<7}{'model id':<52}justified by prompt text",
    ]
    for role in ROLES:
        mid = model_for(role)
        justified = "yes" if rationale_for(role).startswith(("LIGHT - justified", "HEAVY - justified")) else "no"
        rows.append(f"  {role:<13}{tier_label(mid):<7}{mid:<52}{justified}")
    rows.append(f"  baseline (source system, all agents): {BASELINE_MODEL}")
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Prompt caching: minimum cacheable prefix
# ---------------------------------------------------------------------------

#: Minimum cacheable prefix in tokens, measured per model by bisection. Below
#: this a `cachePoint` is accepted with no error, no warning, and
#: cacheWriteInputTokens == 0 - the caller pays full input price forever. The
#: table is NOT monotonic across generations, which is exactly the trap: Haiku
#: 4.5 needs 8x the prefix Opus 5 does.
MIN_CACHEABLE_PROMPT_TOKENS: Final[dict[str, int]] = {
    BASE_OPUS_5: 512,
    BASE_SONNET_5: 1024,
    BASE_SONNET_4_6: 1024,
    BASE_OPUS_4_8: 1024,
    BASE_HAIKU_4_5: 4096,
}

#: Coarse chars-per-token divisor used only by `estimate_prompt_tokens`.
#: This is an estimate for a pre-flight warning, never an input to cost.
_CHARS_PER_TOKEN_ESTIMATE: Final[int] = 4


def supports_prompt_caching(model_id: str) -> bool:
    """Whether Bedrock prompt caching engages for this model id at all.

    Mirrors the SDK's own gate (`'claude' in model_id or 'anthropic' in
    model_id`), so a non-Claude id silently drops cache points.
    """
    lowered = model_id.lower()
    return "claude" in lowered or "anthropic" in lowered


def min_cacheable_tokens(model_id: str) -> int | None:
    """Minimum cacheable prefix for ``model_id``, or None if unmeasured."""
    return MIN_CACHEABLE_PROMPT_TOKENS.get(base_model_id(model_id))


def estimate_prompt_tokens(text: str) -> int:
    """Rough token count for a prompt string (~4 chars/token).

    An estimate, and labelled as one everywhere it surfaces. Use a real
    ``inputTokens`` from a response, or Bedrock's own count, for anything a
    customer sees.
    """
    return len(text) // _CHARS_PER_TOKEN_ESTIMATE


def will_cache(model_id: str, prompt_tokens: int) -> bool:
    """Whether a prefix of ``prompt_tokens`` actually caches on ``model_id``.

    Takes a token count, not a string, so the answer is not contaminated by the
    char/4 estimate. Returns False for an unmeasured model - refusing to promise
    a cache we have not verified.
    """
    if not supports_prompt_caching(model_id):
        return False
    minimum = min_cacheable_tokens(model_id)
    if minimum is None:
        return False
    return prompt_tokens >= minimum


def cache_prefix_report(model_id: str, prompt: str, prompt_tokens: int | None = None) -> dict[str, Any]:
    """Pre-flight report on whether ``prompt`` will cache on ``model_id``.

    Pass ``prompt_tokens`` when a real count is available; otherwise the report
    is driven by ``estimate_prompt_tokens`` and marks itself ``estimated``.
    """
    estimated = prompt_tokens is None
    tokens = estimate_prompt_tokens(prompt) if estimated else int(prompt_tokens)
    minimum = min_cacheable_tokens(model_id)
    caches = will_cache(model_id, tokens)
    if not supports_prompt_caching(model_id):
        reason = "model id is not a Claude/Anthropic id; cache points are dropped"
    elif minimum is None:
        reason = "minimum cacheable prefix not measured for this model id"
    elif caches:
        reason = f"prefix {tokens} >= minimum {minimum}"
    else:
        reason = (
            f"prefix {tokens} < minimum {minimum}: cachePoint is accepted silently, "
            "cacheWriteInputTokens will be 0 and every call pays full input price"
        )
    return {
        "model_id": model_id,
        "prompt_tokens": tokens,
        "prompt_tokens_estimated": estimated,
        "min_cacheable_tokens": minimum,
        "will_cache": caches,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# BedrockModel construction
# ---------------------------------------------------------------------------

#: An eight-way fan-out needs more than botocore's 10-connection default, which
#: is what `BedrockModel` uses when no client config is supplied.
MAX_POOL_CONNECTIONS: Final[int] = 32

#: Supplying `boto_client_config` makes the SDK skip its own
#: `read_timeout=120`, silently falling back to botocore's 60s. Set explicitly.
READ_TIMEOUT_S: Final[int] = 120
CONNECT_TIMEOUT_S: Final[int] = 10

#: The SDK default is (max_attempts=6, initial_delay=4, max_delay=240), i.e.
#: backoff [4, 8, 16, 32, 64, 128]s. One throttle costs 4s against a sub-minute
#: SLA. These values keep total added latency under ~15s.
RETRY_MAX_ATTEMPTS: Final[int] = 4
RETRY_INITIAL_DELAY_S: Final[int] = 1
RETRY_MAX_DELAY_S: Final[int] = 8

#: Per-tier output cap. Derived from the customer's own output contracts, not from a
#: latency guess: six specialists cap at 'max 4000 characters' (~1000 tokens) and the
#: master caps each of its summaries at 'under 1200 characters' across a 21-key object
#: (~7000 characters of text in the worst case).
#:
#: These are deliberately generous rather than tight. A truncated specialist analysis is
#: not a cheaper analysis — it is a lost fraud dimension, and a truncated synthesis is
#: invalid JSON that fails the 21-key contract outright. An early live bench run lost a
#: specialist to exactly this. Output tokens are only billed as generated, so headroom
#: costs nothing when the model obeys its own brevity rules (the customer's prompts push
#: hard for 3-5 sentences on LOW, which is the common case).
#:
#: bustout and rings carry NO character cap in their prompts at all ("Return only the
#: final analysis"), which is the other reason not to run these close to the contract.
#: MEASURED: raising these to 4096/8192 let the specialists write 3,000-6,600 character
#: narratives, which BREACHES the customer's own 'max 4000 characters' rule and pushed a
#: live end-to-end run to 105s against her sub-minute target. The cap is a contract, not
#: a budget: 4000 characters is ~1100 tokens, so 2048 leaves generous headroom for the
#: six capped specialists while making the cap the binding constraint rather than the
#: model's verbosity. bustout and rings state no character cap, so they get more room.
#: MEASURED, second pass: 2048 on the mid tier made IRIS_INCOME fail outright with
#: MaxTokensReachedException on APP-1004 (the run still adjudicated 7-of-8, which is the
#: graceful-degradation path working, but a lost fraud dimension is not a saving). The cap
#: must therefore sit ABOVE what the model actually writes, and the customer's 4000-char
#: contract must be enforced where it belongs -- in her prompt text and in the contract
#: tests -- not by truncating the model mid-sentence.
MAX_TOKENS_BY_TIER: Final[dict[str, int]] = {
    "light": 3072,
    "mid": 4096,
    "heavy": 4096,
    "custom": 4096,
}

#: The synthesizer emits a 21-key JSON object whose nine summaries are each capped by the
#: customer at "under 1200 characters", plus recommended_stipulations and
#: top_risk_factors. Nine summaries x 1200 chars is ~2,850 tokens; 4096 covers the worst
#: legitimate case with headroom for the JSON scaffolding.
#:
#: MEASURED: at 16384 a live synthesis took 84s of a 113s end-to-end run and emitted
#: 9,244 output tokens - far past the contract it was asked to honour. Truncation here
#: destroys the object rather than shortening it, so this is not tight, but leaving it
#: wide open simply pays for prose the contract forbids.
SYNTHESIZER_MAX_TOKENS: Final[int] = 4096

_shared_client_lock = threading.Lock()
_shared_clients: dict[str, Any] = {}


def resolve_region() -> str:
    """Region for the bedrock-runtime client (AWS_REGION, else us-east-1)."""
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"


def shared_bedrock_client(region: str | None = None) -> Any:
    """Return the process-wide bedrock-runtime client for ``region``.

    Two ``BedrockModel`` instances built from the same ``boto3.Session`` still
    get *separate* botocore clients, and therefore separate connection pools -
    so an 8-way fan-out over 8 models would hold 8 pools of 10. botocore clients
    are thread-safe, so one client with a raised pool is both correct and the
    only way the pool ceiling is actually shared.
    """
    import boto3
    from botocore.config import Config as BotocoreConfig

    resolved = region or resolve_region()
    with _shared_client_lock:
        client = _shared_clients.get(resolved)
        if client is None:
            client = boto3.Session().client(
                service_name="bedrock-runtime",
                region_name=resolved,
                config=BotocoreConfig(
                    max_pool_connections=MAX_POOL_CONNECTIONS,
                    read_timeout=READ_TIMEOUT_S,
                    connect_timeout=CONNECT_TIMEOUT_S,
                    retries={"mode": "adaptive"},
                    user_agent_extra="pp-fraud-underwriter",
                ),
            )
            _shared_clients[resolved] = client
        return client


def reset_shared_clients() -> None:
    """Drop cached bedrock-runtime clients (tests, region switches)."""
    with _shared_client_lock:
        _shared_clients.clear()


def _validate_model_config(model_config: dict[str, Any]) -> None:
    """Fail loudly on a key `BedrockConfig` does not declare.

    ``BedrockModel.update_config`` only emits a ``UserWarning`` for unknown keys,
    so a typo like ``max_token`` is accepted, ignored, and never surfaces. This
    turns that into an exception at construction time.
    """
    from typing_extensions import get_type_hints

    from strands.models import BedrockModel

    valid = set(get_type_hints(BedrockModel.BedrockConfig).keys())
    unknown = sorted(set(model_config) - valid)
    if unknown:
        raise ValueError(
            f"BedrockConfig does not declare {unknown}; valid keys are {sorted(valid)}. "
            "strands only warns about this, so it is raised here instead."
        )


def build_bedrock_model(
    model_id: str,
    *,
    max_tokens: int | None = None,
    temperature: float | None = None,
    region: str | None = None,
    effort: str | None = None,
    streaming: bool = True,
    client: Any | None = None,
) -> Any:
    """Build a `BedrockModel` sharing one bedrock-runtime client.

    ``effort`` (Opus 5 / Sonnet 5) is passed through
    ``additional_request_fields={'output_config': {'effort': ...}}`` and is
    opt-in - the default sends nothing, so no behaviour is assumed.

    Note the argument order constraint in the SDK: passing both ``region_name``
    and ``boto_session`` raises, so this builds the model with neither and
    replaces ``model.client`` with the shared one afterwards.
    """
    from strands.models import BedrockModel

    resolved_max_tokens = max_tokens if max_tokens is not None else MAX_TOKENS_BY_TIER[tier_label(model_id)]

    model_config: dict[str, Any] = {
        "model_id": model_id,
        "max_tokens": resolved_max_tokens,
        "streaming": streaming,
    }
    if temperature is not None:
        model_config["temperature"] = temperature
    if effort is not None:
        model_config["additional_request_fields"] = {"output_config": {"effort": effort}}

    _validate_model_config(model_config)

    model = BedrockModel(region_name=region or resolve_region(), **model_config)
    # Replace the per-instance client so all N models share one pool.
    model.client = client if client is not None else shared_bedrock_client(region)
    return model


def build_retry_strategy() -> Any:
    """Throttle-retry strategy tuned for a sub-minute SLA."""
    from strands.agent import ModelRetryStrategy

    return ModelRetryStrategy(
        max_attempts=RETRY_MAX_ATTEMPTS,
        initial_delay=RETRY_INITIAL_DELAY_S,
        max_delay=RETRY_MAX_DELAY_S,
    )


def cached_system_prompt(prompt: str) -> list[dict[str, Any]]:
    """Wrap a system prompt in the SystemContentBlock list that enables caching.

    Reaches Bedrock verbatim as ``[{'text': ...}, {'cachePoint': {...}}]``.
    Check ``will_cache`` first: below the model's minimum prefix this is a no-op
    that costs full price.

    MEASURED REALITY FOR THIS WORKLOAD -- read before quoting a caching benefit.

    The cache point is placed here, after the verbatim customer prompt, and that is
    the only correct place for it, but it rarely pays:

      * The customer's specialist prompts are 547-956 estimated tokens. The minimum
        cacheable prefix is 512 (Opus 5), 1024 (Sonnet 5 / 4.6, Opus 4.8) and 4096
        (Haiku 4.5). So only the two Opus-tier agents clear the bar on the prompt
        alone; the other six accept the cache point silently, report
        ``cacheWriteInputTokens=0``, and pay full input price.
      * Extending the cached prefix with the signal layer's per-domain reference
        block (signal names, the customer's own bands, entitled alert ids) lifts five
        of eight past 1024 but still leaves all eight far below Haiku's 4096.
      * More fundamentally, the part of the request that is actually large -- the
        projected application payload, 3.4-8.3 KB per domain -- is DIFFERENT for every
        application. A cache entry is keyed on an exact prefix, so it can only be
        reused when the same application is analysed again. In a forward-flow
        underwriting stream that essentially never happens.

    Padding the prefix to cross a threshold would raise input token count in order to
    make a cache-hit metric look good, which costs more than it saves. So caching is
    left enabled (it is free when it misses) and is NOT counted as a cost lever. The
    levers that survive measurement are the ``global.*`` prefix (-9.09%), batch
    inference (-50%, on the models that support it), and removing per-application
    Cortex Analyst calls. ``cache_prefix_report`` reports the truth per agent, and
    ``evals/bench.py`` records real ``cacheReadInputTokens`` rather than assuming.
    """
    return [{"text": prompt}, {"cachePoint": {"type": "default"}}]
