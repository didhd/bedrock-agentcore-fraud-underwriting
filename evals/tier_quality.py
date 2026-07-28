"""
Does the cheaper model actually *analyse* as well? The tier sweep cannot answer that.

WHY THIS FILE EXISTS. ``/tmp/bench/tiering.py`` measured latency, tokens and cost per
(agent, model). Those three numbers are enough to say Opus 5 on rings costs 3.6x Sonnet
and takes 2x as long; they are not enough to say the swap is safe. "Drop Opus, save 33%"
is only a recommendation if the Haiku analysis is as *correct* as the Opus one against
the customer's own standard. This module settles that, and it refuses to settle it by
counting words: more output is not more accuracy, and the customer's prompts say so
themselves ("the amount of analysis written by specialized agents does NOT necessarily
indicate fraud severity", MSTR-018).

THREE INDEPENDENT LAYERS, DELIBERATELY NOT ONE SCORE
----------------------------------------------------
1. **Deterministic contract check** (:func:`contract_findings`). Every rule is read out
   of the agent's own verbatim prompt at import time - the rule is only enforced for a
   domain whose prompt actually carries the sentence, and the sentence is quoted in the
   finding. This is where the sweep's most load-bearing correction lives: the header
   ban ("Do NOT include risk classification labels ... in section headers or titles")
   appears in **four** of eight prompts, not six. income and synthetic never received it.
   A checker that hard-coded "6 of 8" would report violations the customer's prompt does
   not contain.

2. **Deterministic band + grounding extraction** (:func:`extract_declared_band`,
   :func:`context_grounding`). The band is the classification the analysis itself
   declares, compared against ``fixtures.loader.expected_for(app)['domains'][domain]``,
   which carries the requirement id and the customer sentence that justifies it.
   Grounding is measured against the *paired* ``<signal>_context`` rows the payload
   actually contained: distinctive tokens are lifted out of ``example_rows`` and looked
   for in the prose, so "cites the context field" is evidence rather than the word
   "context" appearing somewhere.

3. **Blind LLM judge** (:func:`judge_analysis`). ``global.anthropic.claude-opus-5``,
   chosen because after this exercise it is not a candidate for any specialist under
   test, and it is the strongest model available. The analysis text is anonymised
   (:func:`anonymise`) and the order shuffled under a fixed seed before judging, so the
   judge never sees which tier wrote what, and two runs judge in the same order.

WHY THE BAND IS EXTRACTED TWICE. The deterministic extractor and the judge each report
the band they read, and :func:`aggregate` reports how often they disagree. A regex over
prose is fallible; so is a model. Two independent readings that agree are evidence, and
where they disagree the row is counted as ``band_unresolved`` rather than being scored
in either direction.

WHAT IS NEVER PRODUCED HERE. No cost or latency that was not measured on the call that
produced the text being judged - a model swap changes output length, so a cost held at
constant tokens is fiction. Percentiles come from ``evals.bench.summarize_values``, which
refuses a p50 below 5 samples and a p95 below 20, and the refusal is printed instead of a
number. With 3-5 applications per cell that means most cells report a median and an
explicit refusal for p95, which is the honest shape of this experiment.

RUNNING IT
----------
  # judge only what has already been paid for (18 analyses in /tmp/bench/tiering.json)
  PYTHONPATH=. python evals/tier_quality.py --reuse /tmp/bench/tiering.json \\
      --judge --live   # + AGENTCORE_BENCH_ALLOW_LIVE=1

  # extend the sweep to bustout and identity, then judge everything
  PYTHONPATH=. python evals/tier_quality.py --reuse /tmp/bench/tiering.json \\
      --sweep bustout,identity --apps APP-1004,APP-1012,APP-1009 --judge --live

  # dry run: prints the call plan and the priced-from-measured-tokens spend estimate
  PYTHONPATH=. python evals/tier_quality.py --sweep bustout --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agents.contracts import SPECIALIST_RISK_VALUES, contains_emoji  # noqa: E402
from agents.models import MAX_TOKENS_BY_TIER, tier_label  # noqa: E402
from agents.pricing import cost_usd_or_none  # noqa: E402
from evals.bench import LIVE_ENV_GATE, summarize_values  # noqa: E402
from fixtures.loader import build_payload, expected_for  # noqa: E402
from prompts.loader import AGENT_NAMES, DOMAINS, SPECIALIST_PROMPTS  # noqa: E402
from signal_layer.schema import render_for_agent  # noqa: E402

__all__ = [
    "BAND_ALIASES",
    "CANDIDATE_MODELS",
    "JUDGE_MODEL",
    "AnalysisRow",
    "ContractFinding",
    "SHUFFLE_SEED",
    "aggregate",
    "anonymise",
    "build_report",
    "contract_findings",
    "context_grounding",
    "evidence_density",
    "extract_declared_band",
    "judge_analysis",
    "load_reuse_file",
    "render_markdown",
    "rule_applies",
    "run_sweep",
    "sentence_count",
    "spend_estimate",
]

SCHEMA: Final[str] = "pointpredictive.agentcore.tier-quality/1"

#: Schemas for the two sidecars. They exist so nothing lands in ``evals/results/``
#: without declaring what it is: ``tests/test_bench.py`` collects every unschema'd
#: JSON in that directory and asserts the pipeline-bench contract against it, which
#: is the correct behaviour - a corrupt or anonymous report must fail loudly rather
#: than be skipped. Both sidecars are therefore envelopes, not bare arrays.
ANALYSES_SCHEMA: Final[str] = "pointpredictive.agentcore.tier-quality-analyses/1"
VERDICTS_SCHEMA: Final[str] = "pointpredictive.agentcore.tier-quality-verdicts/1"

#: The three tiers any specialist could be assigned to. Judged, never assumed.
CANDIDATE_MODELS: Final[tuple[str, ...]] = (
    "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    "global.anthropic.claude-sonnet-5",
    "global.anthropic.claude-opus-5",
)

#: The judge: the strongest model available, so it can hold the customer's standard.
JUDGE_MODEL: Final[str] = "global.anthropic.claude-opus-5"

#: The conflict of interest in the paragraph above, stated rather than hidden.
#:
#: Opus 5 is BOTH the judge and one of the three candidate tiers, so on a third of the
#: rows it grades its own output. There is no way out of this: a weaker judge cannot
#: hold the standard, and excluding Opus from the sweep would leave the incumbent on
#: rings and synthetic unmeasured, which is the whole question. So the bias is
#: measured instead (:func:`judge_self_preference`) and every conclusion that depends
#: on an Opus-vs-cheaper margin is read against it. Blinding limits the effect but
#: cannot remove it - stylistic self-recognition survives the removal of labels.
JUDGE_CONFLICT: Final[str] = (
    "The judge (Opus 5) is also one of the three candidate models, so on the Opus rows "
    "it is scoring its own output. Self-preference is therefore possible and is "
    "quantified in judge_self_preference rather than assumed absent. Any conclusion "
    "that rests on Opus outscoring a cheaper tier by less than the measured "
    "self-preference margin is NOT supported."
)

#: Fixed so the blind order is reproducible: a second run judges the same
#: analyses in the same sequence, which removes shuffle order as an explanation
#: for any score difference between runs.
SHUFFLE_SEED: Final[int] = 20260728


# ---------------------------------------------------------------------------
# Layer 1: the output contract, read out of the customer's own prompt text
# ---------------------------------------------------------------------------

#: rule id -> the verbatim substring that must be present in a domain's prompt for
#: the rule to apply to that domain. Nothing is enforced against an agent whose
#: prompt never states it: bustout and rings say only "Return only the final
#: analysis." and carry no title, no length cap, no emoji ban and no alert-number
#: ban, so a violation count that included them would be measuring this file's
#: opinion rather than the customer's contract.
RULE_MARKERS: Final[dict[str, tuple[str, ...]]] = {
    "no_alert_numbers": ("not alert numbers",),
    "no_emoji": ("No emojis.",),
    "no_risk_label_in_headers": ("risk classification labels",),
    "exact_title": ("- Title:",),
    "max_4000_chars": ("max 4000 characters",),
    "low_risk_3_to_5_sentences": ("3-5 sentences",),
    "no_coborrower_2_to_3_sentences": ("2-3 sentences",),
}


def rule_applies(domain: str, rule: str) -> bool:
    """Whether ``domain``'s verbatim prompt actually states ``rule``."""
    prompt = SPECIALIST_PROMPTS[domain]
    return any(marker in prompt for marker in RULE_MARKERS[rule])


def rule_source_line(domain: str, rule: str) -> str:
    """The prompt line that states ``rule`` for ``domain``, verbatim ('' if none)."""
    for marker in RULE_MARKERS[rule]:
        for line in SPECIALIST_PROMPTS[domain].splitlines():
            if marker in line:
                return line.strip()
    return ""


def declared_title(domain: str) -> str | None:
    """The exact ``- Title:`` value from a domain's prompt, or None if it defines none."""
    for line in SPECIALIST_PROMPTS[domain].splitlines():
        stripped = line.strip()
        if stripped.startswith("- Title:"):
            return stripped.split(":", 1)[1].strip()
    return None


#: A markdown/bold heading line. The header ban is about headers, so a band named
#: inside a sentence of body prose is not a violation of it - that is the analysis
#: stating its classification, which every prompt requires it to do.
_HEADING = re.compile(r"^\s*(?:#{1,6}\s+.*|\*\*[^*]+\*\*:?\s*)$")

#: "Alert 66", "Alert #4", "Alerts 13, 65 and 66", "alert #111".
_ALERT_NUMBER = re.compile(r"\balerts?\s*#?\s*\d+", re.IGNORECASE)

#: Sentence terminator followed by whitespace, or end of text. Markdown bullets are
#: counted as sentences when they end in a terminator; a bullet list of fragments is
#: counted by :func:`bullet_count` instead, because the customer's "3-5 sentences"
#: is a brevity rule and a 20-bullet list defeats it while containing few periods.
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")

_MODEL_TELLS: Final[tuple[str, ...]] = (
    "haiku",
    "sonnet",
    "opus",
    "claude",
    "anthropic",
    "bedrock",
    "gpt",
    "luna",
    "mantle",
)


@dataclass(slots=True)
class ContractFinding:
    """One deviation from the agent's own output contract.

    ``prompt_line`` is the verbatim sentence from the customer's prompt that the
    finding is measured against, so a reader can check the rule rather than trust
    the rule name.
    """

    rule: str
    detail: str
    prompt_line: str


def sentence_count(text: str) -> int:
    """Statements in ``text``, ignoring markdown scaffolding.

    Headings and horizontal rules are excluded. Every remaining line contributes
    its terminator count, or 1 if it has none - because a 20-item bullet list with
    no full stops is 20 statements, not one, and the customer's "3-5 sentences max"
    is a brevity rule that such a list defeats. Approximate by construction: it is
    used to answer 4 versus 25, not 4 versus 5.
    """
    total = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or _HEADING.match(line) or set(stripped) <= {"-", "*", "_", "="}:
            continue
        body = re.sub(r"^[-*+]\s+|^\d+[.)]\s+", "", stripped)
        if not body:
            continue
        total += max(1, len(_SENTENCE_END.findall(body)))
    return total


def bullet_count(text: str) -> int:
    """Markdown bullet/numbered list items in ``text``."""
    return sum(
        1
        for line in text.splitlines()
        if re.match(r"^\s*(?:[-*+]\s+\S|\d+[.)]\s+\S)", line)
    )


def _headings(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if _HEADING.match(line)]


def first_content_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _normalise_heading(line: str) -> str:
    return re.sub(r"[#*_:]+", "", line).strip().lower()


def contract_findings(domain: str, text: str, band: str | None) -> list[ContractFinding]:
    """Every deviation from ``domain``'s verbatim output contract in ``text``.

    ``band`` selects which length rule applies (the customer caps LOW RISK at 3-5
    sentences and POSSIBLE/HIGH at 4000 characters), and is allowed to be None -
    when the band could not be read, the length rules are skipped rather than
    guessed at.
    """
    findings: list[ContractFinding] = []

    if rule_applies(domain, "no_alert_numbers"):
        hits = _ALERT_NUMBER.findall(text)
        if hits:
            findings.append(
                ContractFinding(
                    "no_alert_numbers",
                    f"{len(hits)} numeric alert citation(s): {sorted(set(h.strip() for h in hits))[:6]}",
                    rule_source_line(domain, "no_alert_numbers"),
                )
            )

    if rule_applies(domain, "no_emoji") and contains_emoji(text):
        findings.append(
            ContractFinding(
                "no_emoji",
                "analysis contains an emoji or dingbat codepoint",
                rule_source_line(domain, "no_emoji"),
            )
        )

    if rule_applies(domain, "no_risk_label_in_headers"):
        offenders = [
            heading
            for heading in _headings(text)
            if any(value.lower() in heading.lower() for value in SPECIALIST_RISK_VALUES)
        ]
        if offenders:
            findings.append(
                ContractFinding(
                    "no_risk_label_in_headers",
                    f"{len(offenders)} header(s) name a risk band: {offenders[:3]}",
                    rule_source_line(domain, "no_risk_label_in_headers"),
                )
            )

    title = declared_title(domain)
    if title is not None:
        opening = _normalise_heading(first_content_line(text))
        if opening != title.lower():
            findings.append(
                ContractFinding(
                    "exact_title",
                    f"opens with {first_content_line(text)!r}, prompt requires {title!r}",
                    rule_source_line(domain, "exact_title"),
                )
            )
    else:
        # bustout and rings define NO title. An invented one is a deviation from
        # "Return only the final analysis." and the golden dataset asserts its
        # absence, so it is recorded - separately, because it is a different
        # failure from getting a defined title wrong.
        opening = _normalise_heading(first_content_line(text))
        if opening and ("analysis" in opening or "detection" in opening) and len(opening) < 80:
            findings.append(
                ContractFinding(
                    "invented_title",
                    f"prompt defines no title; analysis opens with a title line {first_content_line(text)!r}",
                    "Return only the final analysis." if domain == "bustout" else "Return only final analysis.",
                )
            )

    if band == "LOW RISK" and rule_applies(domain, "low_risk_3_to_5_sentences"):
        count = sentence_count(text)
        if count > 5:
            findings.append(
                ContractFinding(
                    "low_risk_3_to_5_sentences",
                    f"{count} sentences and {bullet_count(text)} bullet(s) on a LOW RISK call",
                    rule_source_line(domain, "low_risk_3_to_5_sentences"),
                )
            )
    if band in ("POSSIBLE RISK", "HIGH RISK") and rule_applies(domain, "max_4000_chars"):
        if len(text) > 4000:
            findings.append(
                ContractFinding(
                    "max_4000_chars",
                    f"{len(text)} characters",
                    rule_source_line(domain, "max_4000_chars"),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Layer 2: band extraction and context grounding, both deterministic
# ---------------------------------------------------------------------------

#: The customer's specialists classify in LOW / POSSIBLE / HIGH RISK. MEDIUM is
#: accepted as an alias only because it is the master layer's name for the same
#: middle band (see agents.contracts.PDF_TO_PROMPT_ENUM_MAP) and a specialist that
#: reaches for it has still made the middle call.
BAND_ALIASES: Final[dict[str, str]] = {
    "LOW RISK": "LOW RISK",
    "POSSIBLE RISK": "POSSIBLE RISK",
    "MEDIUM RISK": "POSSIBLE RISK",
    "MODERATE RISK": "POSSIBLE RISK",
    "HIGH RISK": "HIGH RISK",
}

#: A band named right after a classification word. This is the analysis declaring
#: its verdict, as opposed to quoting its own calibration text back ("LOW RISK
#: outcomes should remain COMMON"), which must NOT be read as a verdict.
_DECLARATION = re.compile(
    r"(?:risk\s+classification|classification|risk\s+level|risk\s+rating|rating"
    r"|verdict|assessment|determination|conclusion|overall|final|result)"
    r"[^A-Za-z0-9]{0,24}"
    r"(LOW|POSSIBLE|MEDIUM|MODERATE|HIGH)\s+RISK",
    re.IGNORECASE,
)

#: "... -> LOW RISK", "indicators support LOW RISK." Used only when no explicit
#: declaration is present.
_ARROW = re.compile(r"(?:->|→|=>)\s*\**\s*(LOW|POSSIBLE|MEDIUM|MODERATE|HIGH)\s+RISK", re.IGNORECASE)

_BAND_ANYWHERE = re.compile(r"\b(LOW|POSSIBLE|MEDIUM|MODERATE|HIGH)\s+RISK\b")


def extract_declared_band(text: str) -> tuple[str | None, str]:
    """The band the analysis declares, and how it was read.

    Returns ``(band, source)``; ``band`` is None when the text declares no band
    unambiguously. None is a real outcome, not a failure of this function: an
    analysis whose classification a reader cannot locate has not met "reasoning,
    classification, and conclusion must logically align", and scoring it as
    agreeing or disagreeing with the pinned expectation would be inventing data.
    """
    for pattern, source in ((_DECLARATION, "declaration"), (_ARROW, "arrow")):
        found = [BAND_ALIASES[f"{m.group(1).upper()} RISK"] for m in pattern.finditer(text)]
        if found:
            distinct = set(found)
            if len(distinct) == 1:
                return found[0], source
            # Multiple different bands declared: trust the first, but say so.
            return found[0], f"{source}-conflicting{sorted(distinct)}"
    # Last resort: a band inside a heading or bold line (uppercase only, so the
    # calibration text quoted in lower-case prose cannot win).
    for line in _headings(text):
        hits = _BAND_ANYWHERE.findall(line.upper())
        if hits:
            return BAND_ALIASES[f"{hits[0].upper()} RISK"], "heading"
    return None, "none"


#: How many distinct context-only tokens must appear before a signal counts as
#: grounded. One is too easy - a single shared number can be coincidence - and
#: three starts rejecting terse but genuinely grounded prose.
GROUNDING_TOKEN_THRESHOLD: Final[int] = 2


def _tokens(blob: str) -> set[str]:
    """Numbers and words of 5+ letters in ``blob``, comparably normalised."""
    numbers = {n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,]*\.?\d*", blob)}
    words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z-]{4,}", blob)}
    return {t for t in numbers | words if t}


def context_grounding(app_id: str, domain: str, text: str) -> dict[str, Any]:
    """How much of the paired ``<signal>_context`` evidence the analysis actually cites.

    The customer's hardest instruction to verify is "You MUST validate signals using
    relevant context (columns that end in _context)". Checking that the word
    "context" appears would verify nothing, and checking that the signal's *value*
    appears would not either - the value is already in the signals block, so quoting
    it proves only that the model can copy.

    So the tokens counted here are the ones that exist ONLY in the ``_context``
    row: tokens also present in the agent's own prompt, in the core application
    fields, in the signals block, or in the fired alerts are subtracted first. What
    remains ("47 days", "0.4 per unit", "below expected turnover", "unit 1804") can
    only have reached the analysis through the context row it was asked to validate
    against. A signal is grounded at :data:`GROUNDING_TOKEN_THRESHOLD` such tokens.

    This is a recall measure, not a contract: a LOW RISK analysis obeying "3-5
    sentences max" cannot ground ten signals and should not be penalised for it.
    It is reported per cell so two models on the SAME application can be compared.
    """
    payload = build_payload(app_id)
    view = payload["views"][domain]
    elsewhere = _tokens(SPECIALIST_PROMPTS[domain]) | _tokens(
        json.dumps(payload["application"])
        + json.dumps(view["signals"])
        + json.dumps(view["alerts_fired"])
    )
    present = _tokens(text)
    grounded: list[str] = []
    for signal, context in view["context"].items():
        name = context.get("signal") or signal.removesuffix("_context")
        distinctive = _tokens(str(context.get("example_rows", ""))) - elsewhere
        if len(distinctive & present) >= GROUNDING_TOKEN_THRESHOLD:
            grounded.append(name)
    total = len(view["context"])
    return {
        "signals_in_view": total,
        "signals_grounded_in_context": len(grounded),
        "grounded_fraction": round(len(grounded) / total, 3) if total else None,
        "grounded_signals": sorted(grounded),
        "token_threshold": GROUNDING_TOKEN_THRESHOLD,
    }


def evidence_density(text: str) -> dict[str, Any]:
    """Distinct evidence items per 1,000 characters - the padding test.

    Counts distinct numeric facts and distinct snake_case signal names, then
    divides by length. Two analyses reaching the same conclusion where one is twice
    as long and cites no more distinct facts differ in padding, not in substance.
    """
    numbers = {n.replace(",", "") for n in re.findall(r"\d[\d,.]*", text)}
    signals = set(re.findall(r"\b[a-z][a-z0-9]+(?:_[a-z0-9]+){2,}\b", text))
    chars = max(len(text), 1)
    return {
        "chars": len(text),
        "distinct_numbers": len(numbers),
        "distinct_signal_names": len(signals),
        "evidence_items": len(numbers) + len(signals),
        "evidence_per_1k_chars": round((len(numbers) + len(signals)) * 1000 / chars, 2),
    }


def anonymise(text: str) -> str:
    """Remove model/provider identifiers so the judge cannot see which tier wrote it.

    Stylistic tells survive, of course - blinding here means the label is hidden,
    not that two models are made indistinguishable.
    """
    out = text
    for tell in _MODEL_TELLS:
        out = re.sub(re.escape(tell), "[model]", out, flags=re.IGNORECASE)
    return out


# ---------------------------------------------------------------------------
# The row: one analysis, everything measured about producing it
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AnalysisRow:
    """One (app, domain, model) analysis plus its measured cost of production.

    ``wall_s``/``usage``/``cost_usd`` are None for a row whose text was reused from
    an earlier sweep that did not record them. They are never filled in by
    modelling: a swap changes output length, so a cost recomputed at another
    model's token counts is a fabrication.
    """

    app: str
    domain: str
    model: str
    text: str
    wall_s: float | None = None
    usage: dict[str, int] | None = None
    cost_usd: float | None = None
    source: str = "sweep"
    error: str | None = None

    @property
    def key(self) -> str:
        return f"{self.domain}|{self.model}|{self.app}"


    def to_reuse_json(self) -> dict[str, Any]:
        """This row in the on-disk shape ``load_reuse_file`` reads.

        Written to a sidecar next to the report so a second judging pass - a
        different judge, a changed rubric, a re-scored contract - costs judge calls
        only and never re-runs a specialist. The text is the evidence; discarding it
        would make every finding in the report unverifiable.
        """
        usage = self.usage or {}
        return {
            "app": self.app,
            "domain": self.domain,
            "model": self.model,
            "wall_s": self.wall_s,
            "in_tok": usage.get("inputTokens"),
            "out_tok": usage.get("outputTokens"),
            "cache_r": usage.get("cacheReadInputTokens", 0),
            "cache_w": usage.get("cacheWriteInputTokens", 0),
            "cost": self.cost_usd,
            "chars": len(self.text),
            "error": self.error,
            "text": self.text,
        }


def _expand_model_id(short: str) -> str:
    """Map a short label from an earlier sweep back to the full model id."""
    for full in CANDIDATE_MODELS:
        if full.endswith(short) or short in full:
            return full
    return short


def load_reuse_file(path: str | Path) -> list[AnalysisRow]:
    """Load analyses already paid for by an earlier sweep (``/tmp/bench/tiering.json``).

    Reused first and always: 18 real analyses already exist, and judging them costs
    nothing beyond the judge call. Accepts both a bare list (the shape the original
    ad-hoc sweep wrote) and the schema'd envelope this module now writes.
    """
    loaded = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = loaded["analyses"] if isinstance(loaded, dict) else loaded
    rows: list[AnalysisRow] = []
    for entry in raw:
        if not entry.get("text"):
            continue
        usage = None
        if entry.get("in_tok") is not None:
            usage = {
                "inputTokens": int(entry["in_tok"]),
                "outputTokens": int(entry.get("out_tok") or 0),
                "cacheReadInputTokens": int(entry.get("cache_r") or 0),
                "cacheWriteInputTokens": int(entry.get("cache_w") or 0),
            }
        rows.append(
            AnalysisRow(
                app=entry["app"],
                domain=entry["domain"],
                model=_expand_model_id(entry["model"]),
                text=entry["text"],
                wall_s=entry.get("wall_s"),
                usage=usage,
                cost_usd=entry.get("cost"),
                source=f"reused:{Path(path).name}",
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Layer 3: the live sweep
# ---------------------------------------------------------------------------


def spend_estimate(
    plan: Sequence[tuple[str, str, str]],
    judge_calls: int,
    *,
    measured_tokens: dict[str, tuple[int, int]] | None = None,
) -> dict[str, Any]:
    """Priced call plan, built ONLY from tokens that were actually measured.

    ``measured_tokens`` maps domain -> (mean input, mean output) from the 14x3
    pipeline benchmark. Those are this workload's real token counts for the model
    that ran there; using them for a *different* model is explicitly an estimate of
    spend, not a claim about that model's output length, and the returned dict says
    so. A domain with no measured tokens is priced ``None`` rather than guessed.
    """
    measured = measured_tokens or {}
    per_call: list[dict[str, Any]] = []
    total = 0.0
    unpriced = 0
    for domain, model, app in plan:
        tokens = measured.get(domain)
        if tokens is None:
            per_call.append({"domain": domain, "model": model, "app": app, "usd": None})
            unpriced += 1
            continue
        usage = {
            "inputTokens": tokens[0],
            "outputTokens": tokens[1],
            "cacheReadInputTokens": 0,
            "cacheWriteInputTokens": 0,
        }
        usd = cost_usd_or_none(model, usage)
        per_call.append({"domain": domain, "model": model, "app": app, "usd": usd})
        if usd is not None:
            total += usd
    judge_usage = {
        "inputTokens": JUDGE_INPUT_TOKENS_ESTIMATE,
        "outputTokens": JUDGE_OUTPUT_TOKENS_ESTIMATE,
        "cacheReadInputTokens": 0,
        "cacheWriteInputTokens": 0,
    }
    judge_each = cost_usd_or_none(JUDGE_MODEL, judge_usage) or 0.0
    return {
        "specialist_calls": len(plan),
        "specialist_usd_estimate": round(total, 4),
        "specialist_calls_unpriced": unpriced,
        "judge_calls": judge_calls,
        "judge_usd_estimate": round(judge_each * judge_calls, 4),
        "total_usd_estimate": round(total + judge_each * judge_calls, 4),
        "basis": (
            "specialist input/output tokens are the MEASURED per-call means from "
            "evals/results/pipeline_14x3_us-east-1.json for that domain, priced at the "
            "candidate model's published rate. A model swap changes output length, so "
            "this is a spend ceiling for planning, NOT a prediction of the swapped "
            "model's token count. Actual tokens are measured per call and reported."
        ),
        "per_call": per_call,
    }


#: Mean input/output tokens per call, MEASURED in the 14 apps x 3 reps live run
#: (evals/results/pipeline_14x3_us-east-1.json). Used only to price a *plan*.
MEASURED_MEAN_TOKENS: Final[dict[str, tuple[int, int]]] = {
    "rings": (1568, 1569),
    "bustout": (1786, 1316),
    "identity": (3110, 1105),
    "synthetic": (2730, 581),
    "income": (3184, 900),
    "employment": (2363, 527),
    "dealer": (3518, 379),
    "straw": (2400, 224),
}

#: Judge call size. MEASURED on a live smoke call (bustout/Haiku analysis on
#: APP-1005, judged by Opus 5 in us-east-1 on 2026-07-28): 6,655 input and 1,120
#: output tokens. A first pass estimated 3,200 input from the prompt's character
#: count and was low by 2x, because a structured-output invocation also carries the
#: verdict tool's JSON schema and the retry turn. Used only for the pre-run spend
#: estimate; every reported judge cost is that call's own measured usage.
JUDGE_INPUT_TOKENS_ESTIMATE: Final[int] = 6655
JUDGE_OUTPUT_TOKENS_ESTIMATE: Final[int] = 1120


def _live_gate_error(live: bool) -> str | None:
    if not live:
        return "live mode not requested (pass --live)"
    if os.environ.get(LIVE_ENV_GATE) != "1":
        return f"--live also requires {LIVE_ENV_GATE}=1 in the environment"
    return None


async def _one_specialist(domain: str, model_id: str, app: str) -> AnalysisRow:
    """One real specialist call: fresh agent, no tools, only its own domain view."""
    from strands import Agent
    from strands.agent import NullConversationManager

    from agents.models import build_bedrock_model, build_retry_strategy, cached_system_prompt

    body = render_for_agent(build_payload(app), domain)
    agent = Agent(
        model=build_bedrock_model(
            model_id, max_tokens=MAX_TOKENS_BY_TIER[tier_label(model_id)]
        ),
        system_prompt=cached_system_prompt(SPECIALIST_PROMPTS[domain]),
        tools=[],
        callback_handler=None,
        conversation_manager=NullConversationManager(),
        retry_strategy=build_retry_strategy(),
        name=AGENT_NAMES[domain],
        agent_id=AGENT_NAMES[domain],
    )
    started = time.perf_counter()
    try:
        result = await agent.invoke_async(
            f"Analyze this application for {domain} fraud risk.\n\n{body}"
        )
    except Exception as exc:  # one failed cell must not lose the sweep
        return AnalysisRow(
            app=app,
            domain=domain,
            model=model_id,
            text="",
            wall_s=round(time.perf_counter() - started, 3),
            error=f"{type(exc).__name__}: {exc}",
        )
    wall = time.perf_counter() - started
    usage = dict(result.metrics.accumulated_usage)
    normalised = {
        "inputTokens": int(usage.get("inputTokens", 0)),
        "outputTokens": int(usage.get("outputTokens", 0)),
        "cacheReadInputTokens": int(usage.get("cacheReadInputTokens", 0)),
        "cacheWriteInputTokens": int(usage.get("cacheWriteInputTokens", 0)),
    }
    return AnalysisRow(
        app=app,
        domain=domain,
        model=model_id,
        text=str(result),
        wall_s=round(wall, 3),
        usage=normalised,
        cost_usd=cost_usd_or_none(model_id, normalised),
    )


async def run_sweep(
    domains: Sequence[str],
    apps: Sequence[str],
    models: Sequence[str] = CANDIDATE_MODELS,
    *,
    progress: Any = None,
) -> list[AnalysisRow]:
    """Vary ONLY the model for one agent at a time, serially.

    Serial on purpose: a concurrent sweep would let queueing inside botocore land
    in the wall-clock number, and latency here has to remain attributable to the
    model. The fan-out benchmark is where concurrency is measured.
    """
    rows: list[AnalysisRow] = []
    for app in apps:
        for domain in domains:
            for model in models:
                row = await _one_specialist(domain, model, app)
                rows.append(row)
                if progress:
                    progress(
                        f"{row.app} {row.domain:<10} {row.model.split('claude-')[-1]:<24}"
                        + (
                            f"FAIL {row.error[:60]}"
                            if row.error
                            else f"{row.wall_s:>6.2f}s out={row.usage['outputTokens']:<5} "
                            f"chars={len(row.text):<5} ${row.cost_usd:.5f}"
                        )
                    )
    return rows


# ---------------------------------------------------------------------------
# Layer 3b: the blind judge
# ---------------------------------------------------------------------------

_JUDGE_INSTRUCTIONS: Final[str] = """\
You are auditing ONE fraud-specialist analysis against the customer's own standard. \
You do not know which model wrote it and you must not guess.

The standard is the verbatim production prompt below plus the pinned expectation for \
this application. Do not substitute your own view of what is risky, and do NOT reward \
an analysis for being long, thorough or alarming. Three rules from the customer override \
any impression of severity:

  1. LOW RISK must be the most common outcome. Escalating a routine application is a \
failure even when the prose reads well.
  2. Alert volume alone does NOT indicate risk. Multiple alerts arising from the same \
underlying behaviour are ONE signal, not independent corroboration.
  3. HIGH RISK requires multiple independent corroborated patterns, or one extremely \
severe well-supported indicator. It is uncommon.

Anti-false-positive rules that must NOT drive an escalation on their own: a dealer \
consortium score, default rate or EPD rate is portfolio/credit risk, not fraud; missing \
employer address/phone/city/state/ZIP (even all of them) is an underwriting data gap; \
large employers, military, government, staffing agencies and gig platforms naturally \
carry high SSN counts; address reuse in multi-unit or dense housing is benign unless it \
is repeated EXACT-UNIT reuse by unrelated SSNs corroborated by other inconsistencies.

=== THE AGENT'S VERBATIM PRODUCTION PROMPT ({domain}) ===
{prompt}

=== THE PINNED EXPECTATION FOR THIS APPLICATION ({app}) ===
expected band for the {domain} domain: {expected_band}
requirement id: {requirement_id}
the customer sentence that justifies it: {customer_rule}

=== THE ANALYSIS UNDER REVIEW (model identity removed) ===
{analysis}

Judge it. Be specific about evidence you can point at in the text. If the analysis \
reaches the expected band for the wrong reasons, say so and score the reasoning \
accordingly - the band alone is not the score."""


def _judge_verdict_model() -> Any:
    """The judge's structured verdict.

    Built lazily so importing this module never requires pydantic to be present for
    the deterministic layers or the tests.
    """
    from pydantic import BaseModel, Field

    class JudgeVerdict(BaseModel):
        """One blind judgement of one specialist analysis."""

        band_read_from_analysis: str = Field(
            description="The risk band this analysis declares, exactly one of "
            "'LOW RISK', 'POSSIBLE RISK', 'HIGH RISK', or 'UNCLEAR' if it declares none."
        )
        band_matches_expectation: bool = Field(
            description="Whether the band you read equals the pinned expected band."
        )
        calibration_score: int = Field(
            ge=1,
            le=5,
            description="1 miscalibrated (escalates a routine application or clears a "
            "corroborated one); 2 defensible band, reasoning contradicts the prompt's "
            "thresholds; 3 correct band, thin reasoning; 4 correct band with the specific "
            "mitigating or corroborating evidence named; 5 as 4 and it explicitly weighs "
            "the benign explanation the prompt names for this signal before concluding, "
            "and treats alerts from one behaviour as one signal.",
        )
        false_positive_verdict: str = Field(
            description="Exactly one of 'Pass', 'PassWithNote', 'FalsePositive', "
            "'FalseNegative' per the customer's anti-false-positive rules."
        )
        escalation_drift: str = Field(
            description="Exactly one of 'aligned', 'escalates', 'dismisses' - whether the "
            "analysis pushes above, sits at, or falls below the pinned expectation."
        )
        substance_score: int = Field(
            ge=1,
            le=5,
            description="How much of the length is load-bearing. 1 = mostly restatement, "
            "scaffolding or repetition of the same finding; 3 = mixed; 5 = every section "
            "adds a distinct fact or a distinct inference. Judge density, not length.",
        )
        padding_observed: bool = Field(
            description="True if material portions restate the same finding, echo the "
            "prompt's own calibration text, or add sections with no new evidence."
        )
        cites_context_fields: bool = Field(
            description="Whether the reasoning validates signals against the paired "
            "_context evidence rather than asserting the raw signal value."
        )
        contract_notes: str = Field(
            description="Output-contract deviations you can see (title, length, risk "
            "labels in headers, numeric alert citations, emoji). Empty string if none."
        )
        justification: str = Field(
            max_length=1200,
            description="Under 1200 characters. Quote or point at the specific text that "
            "drove the scores. Be terse: the scores above are the finding, this is the "
            "citation for them.",
        )

    return JudgeVerdict


#: Output cap for a judge verdict.
#:
#: MEASURED: at 2048 the judge truncated on 12 of 48 verdicts, all of them on
#: analyses long enough to warrant a long ``contract_notes`` plus a long
#: ``justification``. strands raises ``MaxTokensReachedException`` rather than
#: returning a partial object, so a truncation is a LOST judgement, and the losses
#: were not random - they concentrated on the verbose cells (rings/Haiku lost 3 of
#: 5), which would have silently reduced exactly the sample the padding question
#: depends on. A judge that cannot finish its sentence is not evidence about the
#: analysis; it is evidence about the cap.
JUDGE_MAX_TOKENS: Final[int] = 4096


async def judge_analysis(row: AnalysisRow, *, model_id: str = JUDGE_MODEL) -> dict[str, Any]:
    """One blind judgement of one analysis. Returns the verdict plus its own cost."""
    from strands import Agent
    from strands.agent import NullConversationManager

    from agents.models import build_bedrock_model, build_retry_strategy

    verdict_model = _judge_verdict_model()
    expectation = expected_for(row.app)["domains"][row.domain]
    prompt = _JUDGE_INSTRUCTIONS.format(
        domain=row.domain,
        prompt=SPECIALIST_PROMPTS[row.domain],
        app=row.app,
        expected_band=expectation["band"],
        requirement_id=expectation["requirement_id"],
        customer_rule=expectation["customer_rule"],
        analysis=anonymise(row.text),
    )
    agent = Agent(
        model=build_bedrock_model(model_id, max_tokens=JUDGE_MAX_TOKENS),
        system_prompt=(
            "You are an impartial auditor of fraud-analysis quality. You apply the "
            "customer's stated calibration, never your own risk appetite."
        ),
        tools=[],
        callback_handler=None,
        conversation_manager=NullConversationManager(),
        retry_strategy=build_retry_strategy(),
        name="TIER_QUALITY_JUDGE",
        agent_id="TIER_QUALITY_JUDGE",
    )
    started = time.perf_counter()
    try:
        result = await agent.invoke_async(prompt, structured_output_model=verdict_model)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "wall_s": round(time.perf_counter() - started, 3)}
    verdict = getattr(result, "structured_output", None)
    if verdict is None:
        return {
            "error": f"judge returned no structured output (stop_reason={getattr(result, 'stop_reason', None)!r})",
            "wall_s": round(time.perf_counter() - started, 3),
        }
    usage = dict(getattr(result.metrics, "accumulated_usage", {}) or {})
    normalised = {
        "inputTokens": int(usage.get("inputTokens", 0)),
        "outputTokens": int(usage.get("outputTokens", 0)),
        "cacheReadInputTokens": int(usage.get("cacheReadInputTokens", 0)),
        "cacheWriteInputTokens": int(usage.get("cacheWriteInputTokens", 0)),
    }
    return {
        "verdict": verdict.model_dump(),
        "judge_model": model_id,
        "judge_wall_s": round(time.perf_counter() - started, 3),
        "judge_usage": normalised,
        "judge_cost_usd": cost_usd_or_none(model_id, normalised),
    }


def blind_order(rows: Sequence[AnalysisRow], seed: int = SHUFFLE_SEED) -> list[AnalysisRow]:
    """Rows in a fixed shuffled order, so no tier is systematically judged first."""
    shuffled = list(rows)
    random.Random(seed).shuffle(shuffled)
    return shuffled


async def judge_all(
    rows: Sequence[AnalysisRow],
    *,
    model_id: str = JUDGE_MODEL,
    progress: Any = None,
    existing: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Judge every row blind, in the fixed shuffled order. Keyed by ``AnalysisRow.key``.

    ``existing`` carries verdicts from a previous pass; a row that already has a
    SUCCESSFUL verdict is not re-judged, and a row whose previous verdict recorded an
    error is. That makes a partial failure recoverable for the cost of the failures
    alone, and it keeps the successful verdicts bit-identical rather than replacing
    them with a second sample of a non-deterministic judge - which would otherwise
    confound "we fixed the cap" with "the judge scored differently this time".
    """
    verdicts: dict[str, dict[str, Any]] = dict(existing or {})
    for index, row in enumerate(blind_order(rows), start=1):
        cached = verdicts.get(row.key)
        if cached and "error" not in cached:
            if progress:
                progress(f"[{index}/{len(rows)}] {row.key:<70} cached")
            continue
        outcome = await judge_analysis(row, model_id=model_id)
        verdicts[row.key] = outcome
        if progress:
            summary = (
                f"FAIL {outcome['error'][:60]}"
                if "error" in outcome
                else f"band={outcome['verdict']['band_read_from_analysis']:<14} "
                f"cal={outcome['verdict']['calibration_score']} "
                f"sub={outcome['verdict']['substance_score']} "
                f"fp={outcome['verdict']['false_positive_verdict']}"
            )
            progress(f"[{index}/{len(rows)}] {row.key:<70} {summary}")
    return verdicts


# ---------------------------------------------------------------------------
# Aggregation and reporting
# ---------------------------------------------------------------------------


def _median_or_none(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return round(statistics.median(present), 4) if present else None


def evaluate_row(row: AnalysisRow, verdict: dict[str, Any] | None) -> dict[str, Any]:
    """Everything known about one analysis: deterministic checks + judge verdict."""
    expectation = expected_for(row.app)["domains"][row.domain]
    band, band_source = extract_declared_band(row.text) if row.text else (None, "no-text")
    findings = contract_findings(row.domain, row.text, band) if row.text else []
    judged = (verdict or {}).get("verdict")
    judge_band = (judged or {}).get("band_read_from_analysis")
    normalised_judge_band = BAND_ALIASES.get((judge_band or "").upper(), judge_band)
    return {
        "app": row.app,
        "domain": row.domain,
        "model": row.model,
        "source": row.source,
        "error": row.error,
        "expected_band": expectation["band"],
        "requirement_id": expectation["requirement_id"],
        "customer_rule": expectation["customer_rule"],
        "declared_band": band,
        "band_source": band_source,
        "band_agrees": None if band is None else band == expectation["band"],
        "judge_band": normalised_judge_band,
        "band_readers_agree": (
            None
            if band is None or normalised_judge_band is None
            else band == normalised_judge_band
        ),
        "contract_findings": [asdict(f) for f in findings],
        "contract_violation_count": len(findings),
        "grounding": context_grounding(row.app, row.domain, row.text) if row.text else None,
        "density": evidence_density(row.text) if row.text else None,
        "sentences": sentence_count(row.text) if row.text else None,
        "wall_s": row.wall_s,
        "usage": row.usage,
        "cost_usd": row.cost_usd,
        "judge": judged,
        "judge_cost_usd": (verdict or {}).get("judge_cost_usd"),
        "judge_error": (verdict or {}).get("error"),
    }


def aggregate(evaluated: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per (agent, model): band agreement, contract violations, judge score, cost, latency.

    Every rate carries its own denominator and every percentile goes through
    ``evals.bench.summarize_values``, which refuses a p50 under 5 samples and a p95
    under 20 and records the refusal. With 3-5 applications per cell the medians are
    reported and the p95s are refused, which is the truthful shape of this sample.
    """
    cells: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in evaluated:
        cells.setdefault((row["domain"], row["model"]), []).append(row)

    out: list[dict[str, Any]] = []
    for (domain, model), rows in sorted(cells.items()):
        usable = [r for r in rows if not r["error"]]
        with_band = [r for r in usable if r["declared_band"] is not None]
        agreeing = [r for r in with_band if r["band_agrees"]]
        judged = [r for r in usable if r.get("judge")]
        escalating = [
            r for r in judged if r["judge"].get("escalation_drift") == "escalates"
        ]
        dismissing = [r for r in judged if r["judge"].get("escalation_drift") == "dismisses"]
        false_positives = [
            r for r in judged if r["judge"].get("false_positive_verdict") == "FalsePositive"
        ]
        false_negatives = [
            r for r in judged if r["judge"].get("false_positive_verdict") == "FalseNegative"
        ]
        padding = [r for r in judged if r["judge"].get("padding_observed")]
        out.append(
            {
                "domain": domain,
                "model": model,
                "n_calls": len(rows),
                "n_usable": len(usable),
                "n_failed": len(rows) - len(usable),
                "band": {
                    "n_with_readable_band": len(with_band),
                    "n_unreadable_band": len(usable) - len(with_band),
                    "agreements": len(agreeing),
                    "agreement_rate": (
                        round(len(agreeing) / len(with_band), 3) if with_band else None
                    ),
                    "disagreements": [
                        {
                            "app": r["app"],
                            "declared": r["declared_band"],
                            "expected": r["expected_band"],
                            "requirement_id": r["requirement_id"],
                        }
                        for r in with_band
                        if not r["band_agrees"]
                    ],
                    "extractor_judge_disagreements": [
                        r["app"] for r in usable if r["band_readers_agree"] is False
                    ],
                },
                "contract": {
                    "total_violations": sum(r["contract_violation_count"] for r in usable),
                    "rows_clean": sum(1 for r in usable if r["contract_violation_count"] == 0),
                    "by_rule": _violations_by_rule(usable),
                },
                "judge": {
                    "n_judged": len(judged),
                    "calibration_mean": (
                        round(statistics.fmean(r["judge"]["calibration_score"] for r in judged), 2)
                        if judged
                        else None
                    ),
                    "calibration_min": (
                        min(r["judge"]["calibration_score"] for r in judged) if judged else None
                    ),
                    "substance_mean": (
                        round(statistics.fmean(r["judge"]["substance_score"] for r in judged), 2)
                        if judged
                        else None
                    ),
                    "escalates": len(escalating),
                    "dismisses": len(dismissing),
                    "false_positive": len(false_positives),
                    "false_negative": len(false_negatives),
                    "padding_observed": len(padding),
                    "cites_context_fields": sum(
                        1 for r in judged if r["judge"].get("cites_context_fields")
                    ),
                },
                "measured": {
                    "wall_s_median": _median_or_none(r["wall_s"] for r in usable),
                    "wall_s": summarize_values(
                        [r["wall_s"] for r in usable if r["wall_s"] is not None],
                        label=f"{domain}/{model} wall clock",
                        unit="s",
                    ),
                    "cost_usd_median": _median_or_none(r["cost_usd"] for r in usable),
                    "out_tok_median": _median_or_none(
                        (r["usage"] or {}).get("outputTokens") for r in usable
                    ),
                    "chars_median": _median_or_none(
                        (r["density"] or {}).get("chars") for r in usable
                    ),
                    "evidence_per_1k_chars_median": _median_or_none(
                        (r["density"] or {}).get("evidence_per_1k_chars") for r in usable
                    ),
                    "distinct_evidence_items_median": _median_or_none(
                        (r["density"] or {}).get("evidence_items") for r in usable
                    ),
                    "grounded_fraction_median": _median_or_none(
                        (r["grounding"] or {}).get("grounded_fraction") for r in usable
                    ),
                },
            }
        )
    return out


def _violations_by_rule(rows: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for finding in row["contract_findings"]:
            counts[finding["rule"]] = counts.get(finding["rule"], 0) + 1
    return dict(sorted(counts.items()))


def contract_scope() -> dict[str, Any]:
    """Which output rule applies to which agent, read from the prompts themselves.

    Published because it corrects a widely repeated summary of this contract: the
    header ban is in FOUR prompts, not six. Six prompts define a title and a
    4000-character cap; income and synthetic never received the header sentence, and
    bustout and rings received none of it.
    """
    scope: dict[str, Any] = {"rules": {}, "titles": {}}
    for rule in RULE_MARKERS:
        applies = [d for d in DOMAINS if rule_applies(d, rule)]
        scope["rules"][rule] = {
            "applies_to": applies,
            "count": len(applies),
            "example_prompt_line": rule_source_line(applies[0], rule) if applies else "",
        }
    for domain in DOMAINS:
        scope["titles"][domain] = declared_title(domain)
    return scope


#: The specialist assignment measured in the 14x3 pipeline benchmark, i.e. what a
#: swap would be swapping away from. Recorded so a verdict names the incumbent
#: rather than implying every agent starts from the same place.
CURRENT_ASSIGNMENT: Final[dict[str, str]] = {
    "identity": "sonnet-5",
    "dealer": "haiku-4-5",
    "straw": "haiku-4-5",
    "employment": "sonnet-5",
    "income": "sonnet-5",
    "synthetic": "opus-5",
    "bustout": "sonnet-5",
    "rings": "opus-5",
}

#: Applications per cell below which a band-agreement difference is not a finding.
#: One application's disagreement moves a 3-app rate by 33 points, so a difference
#: of one case between two models is inside the noise of this sample by
#: construction. Stated as a constant because the honest answer to several of the
#: questions in this exercise is "n is too small", and that answer has to be
#: mechanical rather than a matter of the author's mood.
MIN_APPS_FOR_A_BAND_CLAIM: Final[int] = 5


def per_agent_verdict(
    cells: Sequence[dict[str, Any]], *, self_preference_margin: float | None = None
) -> list[dict[str, Any]]:
    """Per agent: is the cheaper model defensible, and is n big enough to say?

    Deliberately mechanical. A cell only clears when it (a) matches the incumbent's
    band agreement on the same applications, (b) commits no more judge-confirmed
    false positives, and (c) is not scored below it on calibration by more than a
    point. Anything else reads "not shown", and a cell with fewer than
    :data:`MIN_APPS_FOR_A_BAND_CLAIM` applications says so out loud regardless of
    how the numbers fell.

    ``self_preference_margin`` is the judge's measured advantage for its own model
    family (:func:`judge_self_preference`). Where the incumbent is the judge's own
    family and its calibration lead over a cheaper tier is SMALLER than that margin,
    the lead is annotated as not distinguishable from self-preference - which is the
    situation on rings and synthetic, the two agents this exercise exists to settle.
    """
    by_domain: dict[str, list[dict[str, Any]]] = {}
    for cell in cells:
        by_domain.setdefault(cell["domain"], []).append(cell)

    verdicts: list[dict[str, Any]] = []
    for domain, group in sorted(by_domain.items()):
        current_label = CURRENT_ASSIGNMENT.get(domain, "")
        incumbent = next(
            (c for c in group if current_label and current_label in c["model"]), None
        )
        cheaper = [
            c
            for c in group
            if c is not incumbent and _tier_rank(c["model"]) < _tier_rank(incumbent["model"] if incumbent else "")
        ]
        n_apps = max((c["n_usable"] for c in group), default=0)
        evidence: list[str] = []
        if incumbent is None:
            verdicts.append(
                {
                    "domain": domain,
                    "current_model": current_label,
                    "verdict": "NOT ASSESSED - the incumbent model was not swept for this agent",
                    "evidence": [],
                    "sample_note": f"{n_apps} application(s) per cell",
                }
            )
            continue

        base = incumbent["band"]
        evidence.append(
            f"incumbent `{incumbent['model'].split('claude-')[-1]}`: band "
            f"{base['agreements']}/{base['n_with_readable_band']}, calibration mean "
            f"{incumbent['judge']['calibration_mean']}, judge-confirmed false positives "
            f"{incumbent['judge']['false_positive']}, ${incumbent['measured']['cost_usd_median']}"
            f"/call, {incumbent['measured']['wall_s_median']}s"
        )
        defensible: list[str] = []
        for cell in cheaper:
            band = cell["band"]
            label = cell["model"].split("claude-")[-1]
            band_ok = band["agreements"] >= base["agreements"]
            fp_ok = cell["judge"]["false_positive"] <= incumbent["judge"]["false_positive"]
            cal_gap = (incumbent["judge"]["calibration_mean"] or 0) - (
                cell["judge"]["calibration_mean"] or 0
            )
            caveat = ""
            if (
                self_preference_margin is not None
                and _tier_name(incumbent["model"]) == _tier_name(JUDGE_MODEL)
                and 0 < cal_gap < self_preference_margin
            ):
                caveat = (
                    f" - NOTE: that {cal_gap:+.2f} lead is smaller than the judge's "
                    f"measured {self_preference_margin:+.2f} self-preference for its own "
                    f"family, so it is not distinguishable from bias"
                )
            evidence.append(
                f"`{label}`: band {band['agreements']}/{band['n_with_readable_band']}, "
                f"calibration mean {cell['judge']['calibration_mean']} "
                f"(gap {cal_gap:+.2f} vs incumbent), false positives "
                f"{cell['judge']['false_positive']}, contract violations "
                f"{cell['contract']['total_violations']}, "
                f"${cell['measured']['cost_usd_median']}/call, "
                f"{cell['measured']['wall_s_median']}s{caveat}"
            )
            if band_ok and fp_ok and cal_gap <= 1.0:
                defensible.append(label)
        if not cheaper:
            verdict = "NO CHEAPER TIER - this agent is already on the cheapest model swept"
        elif n_apps < MIN_APPS_FOR_A_BAND_CLAIM:
            verdict = (
                f"n TOO SMALL TO TELL - {n_apps} applications per cell. One case moves the "
                f"band rate by {round(100 / n_apps)} points"
                + (f"; {', '.join(defensible)} did not lose on any axis here" if defensible else "")
            )
        elif defensible:
            verdict = f"DEFENSIBLE on this sample: {', '.join(defensible)}"
        else:
            verdict = "NOT SHOWN - every cheaper tier lost on band agreement, false positives or calibration"
        verdicts.append(
            {
                "domain": domain,
                "current_model": current_label,
                "verdict": verdict,
                "evidence": evidence,
                "sample_note": (
                    f"{n_apps} application(s) per cell; a band claim needs "
                    f"{MIN_APPS_FOR_A_BAND_CLAIM}"
                ),
            }
        )
    return verdicts


def _tier_rank(model_id: str) -> int:
    """Cheapest to most expensive, by published rate. Unknown ids sort highest."""
    for rank, needle in enumerate(("haiku", "sonnet", "opus")):
        if needle in model_id:
            return rank
    return 99


def verbosity_vs_substance(evaluated: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Is the longer model's extra length substance or padding?

    The comparison is PAIRED: only (app, domain) pairs where both models produced an
    analysis are compared, so a length difference cannot be an artifact of one model
    having drawn easier applications. Three questions are answered separately, because
    they have different answers:

      * does the extra length carry extra distinct evidence (``evidence_items``),
      * does the judge score the density higher (``substance_score``),
      * and does the extra length come with a drift toward escalation - which is the
        one that matters commercially, since the customer's cost of a false positive
        is a declined good loan.
    """
    pairs: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for row in evaluated:
        if row["error"] or not row["density"]:
            continue
        pairs.setdefault((row["app"], row["domain"]), {})[_tier_name(row["model"])] = row

    comparisons: list[dict[str, Any]] = []
    domains_present = sorted({domain for _, domain in pairs})
    for longer, shorter in (("opus-5", "sonnet-5"), ("opus-5", "haiku-4-5"), ("haiku-4-5", "sonnet-5")):
        for domain in [None, *domains_present]:
            comparison = _paired_comparison(pairs, longer, shorter, domain)
            if comparison:
                comparisons.append(comparison)
    return {
        "method": (
            "paired by (application, domain) so only analyses of the SAME payload are "
            "compared; distinct evidence items are distinct numbers plus distinct "
            "snake_case signal names. Reported pooled AND per agent, because pooling "
            "four agents can cancel out the one that motivated the question."
        ),
        "comparisons": comparisons,
    }


def _paired_comparison(
    pairs: dict[tuple[str, str], dict[str, dict[str, Any]]],
    longer: str,
    shorter: str,
    domain: str | None,
) -> dict[str, Any] | None:
    """One paired longer-vs-shorter comparison, pooled (``domain=None``) or per agent."""
    both = [
        p
        for (_, dom), p in pairs.items()
        if longer in p and shorter in p and (domain is None or dom == domain)
    ]
    if not both:
        return None
    deltas = [
        {
            "app": p[longer]["app"],
            "domain": p[longer]["domain"],
            "chars_ratio": round(
                p[longer]["density"]["chars"] / max(p[shorter]["density"]["chars"], 1), 2
            ),
            "evidence_ratio": round(
                p[longer]["density"]["evidence_items"]
                / max(p[shorter]["density"]["evidence_items"], 1),
                2,
            ),
            "substance_delta": (
                p[longer]["judge"]["substance_score"] - p[shorter]["judge"]["substance_score"]
                if p[longer]["judge"] and p[shorter]["judge"]
                else None
            ),
            "escalates_where_other_does_not": (
                (p[longer]["judge"] or {}).get("escalation_drift") == "escalates"
                and (p[shorter]["judge"] or {}).get("escalation_drift") != "escalates"
            ),
        }
        for p in both
    ]
    substance_deltas = [d["substance_delta"] for d in deltas if d["substance_delta"] is not None]
    return {
        "scope": domain or "ALL AGENTS POOLED",
        "longer": longer,
        "shorter": shorter,
        "n_paired_applications": len(both),
        "chars_ratio_median": round(statistics.median(d["chars_ratio"] for d in deltas), 2),
        "evidence_ratio_median": round(statistics.median(d["evidence_ratio"] for d in deltas), 2),
        "substance_delta_median": (
            round(statistics.median(substance_deltas), 2) if substance_deltas else None
        ),
        "pairs_where_longer_escalates_alone": sum(
            1 for d in deltas if d["escalates_where_other_does_not"]
        ),
        "reading": (
            "chars_ratio_median > evidence_ratio_median means the extra length is NOT "
            "carrying proportionally more distinct evidence, i.e. padding. The reverse "
            "means the longer analysis is denser, not merely longer."
        ),
        "pairs": deltas,
    }


def _tier_name(model_id: str) -> str:
    for needle in ("haiku-4-5", "sonnet-5", "opus-5"):
        if needle in model_id:
            return needle
    return model_id


def judge_self_preference(
    evaluated: Sequence[dict[str, Any]], *, judge_model: str = JUDGE_MODEL
) -> dict[str, Any]:
    """How much better the judge scores the model it shares an identity with.

    Two independent readings of the same conflict:

      * the judge's mean scores on its OWN rows minus its mean on everyone else's,
        which is the direct self-preference margin, and
      * the same margin computed against the DETERMINISTIC metrics, which the judge
        cannot influence at all: band agreement with the pinned expectation, and
        contract violations. If the judge's advantage for its own family is real
        quality, the deterministic metrics move with it. If the deterministic metrics
        are flat while the judge's scores are not, the margin is preference.

    The output is a caution, not a correction. Subtracting a bias estimate from the
    scores would produce a number that looks adjusted and is not measured.
    """
    own_tier = _tier_name(judge_model)
    own = [r for r in evaluated if r["judge"] and _tier_name(r["model"]) == own_tier]
    others = [r for r in evaluated if r["judge"] and _tier_name(r["model"]) != own_tier]
    if not own or not others:
        return {"judge_model": judge_model, "assessable": False, "conflict": JUDGE_CONFLICT}

    def mean(rows: Sequence[dict[str, Any]], path: str) -> float:
        if path in ("calibration_score", "substance_score"):
            return statistics.fmean(r["judge"][path] for r in rows)
        if path == "band_agrees":
            readable = [r for r in rows if r["declared_band"] is not None]
            return statistics.fmean(1.0 if r["band_agrees"] else 0.0 for r in readable)
        return statistics.fmean(float(r[path]) for r in rows)

    calibration_margin = mean(own, "calibration_score") - mean(others, "calibration_score")
    substance_margin = mean(own, "substance_score") - mean(others, "substance_score")
    band_margin = mean(own, "band_agrees") - mean(others, "band_agrees")
    violation_margin = mean(own, "contract_violation_count") - mean(
        others, "contract_violation_count"
    )
    return {
        "judge_model": judge_model,
        "assessable": True,
        "conflict": JUDGE_CONFLICT,
        "n_own_rows": len(own),
        "n_other_rows": len(others),
        "judge_scored": {
            "calibration_margin": round(calibration_margin, 2),
            "substance_margin": round(substance_margin, 2),
        },
        "deterministic_cross_check": {
            "band_agreement_margin": round(band_margin, 3),
            "contract_violations_margin": round(violation_margin, 2),
            "note": (
                "these two are computed without the judge. A judge-scored margin that "
                "is not accompanied by a band-agreement margin in the same direction "
                "is more likely preference than quality."
            ),
        },
        "reading": (
            f"On its own {len(own)} rows the judge scored substance "
            f"{substance_margin:+.2f} and calibration {calibration_margin:+.2f} versus "
            f"the other {len(others)}, while band agreement moved {band_margin:+.3f}. "
            "Treat any Opus-vs-cheaper margin below the substance margin as unsupported."
        ),
    }


def build_report(
    evaluated: Sequence[dict[str, Any]],
    *,
    region: str,
    judge_model: str,
    live_calls: dict[str, Any],
) -> dict[str, Any]:
    """The full result document."""
    judge_cost = [r["judge_cost_usd"] for r in evaluated if r.get("judge_cost_usd")]
    specialist_cost = [r["cost_usd"] for r in evaluated if r.get("cost_usd")]
    cells = aggregate(evaluated)
    bias = judge_self_preference(evaluated, judge_model=judge_model)
    margin = (bias.get("judge_scored") or {}).get("calibration_margin")
    return {
        "schema": SCHEMA,
        "run": {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "aws_region": region,
            "judge_model": judge_model,
            "candidate_models": list(CANDIDATE_MODELS),
            "shuffle_seed": SHUFFLE_SEED,
            "blinding": (
                "model/provider identifiers stripped from the analysis text before "
                "judging; judge order shuffled under the fixed seed"
            ),
        },
        "live_calls": live_calls,
        "spend_actual_usd": {
            "specialist_calls": round(sum(specialist_cost), 4) if specialist_cost else None,
            "judge_calls": round(sum(judge_cost), 4) if judge_cost else None,
            "total": (
                round(sum(specialist_cost) + sum(judge_cost), 4)
                if specialist_cost or judge_cost
                else None
            ),
            "note": (
                "measured from each call's own reported token usage. Rows reused from an "
                "earlier sweep carry that sweep's measured cost; rows with no recorded "
                "usage contribute nothing rather than an estimate."
            ),
        },
        "contract_scope": contract_scope(),
        "per_cell": cells,
        "per_agent_verdict": per_agent_verdict(cells, self_preference_margin=margin),
        "verbosity_vs_substance": verbosity_vs_substance(evaluated),
        "judge_self_preference": bias,
        "per_analysis": list(evaluated),
        "caveats": [
            "Percentiles come from evals.bench.summarize_values: p50 needs 5 samples, "
            "p95 needs 20. Cells below those counts report the refusal, not a number.",
            "The band is read twice - by regex and by the blind judge. Rows where the two "
            "readers disagree are listed per cell and are not counted as agreement.",
            "Output-contract rules are enforced per domain only where the domain's own "
            "verbatim prompt states them; bustout and rings state none of them.",
            "Cost and latency are only ever the measured values from the call that "
            "produced the judged text. No cost is modelled at another model's tokens.",
            JUDGE_CONFLICT,
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Human-readable summary. Every table cell traces to a measured field."""
    lines: list[str] = []
    run = report["run"]
    lines.append("# Tier quality: does the cheaper model analyse as well?")
    lines.append("")
    lines.append(
        f"Judge `{run['judge_model']}`, blind (model identifiers stripped, order shuffled "
        f"under seed {run['shuffle_seed']}). Region `{run['aws_region']}`. "
        f"Generated {run['timestamp_utc']}."
    )
    lines.append("")
    spend = report["spend_actual_usd"]
    lines.append(
        f"Actual measured spend: specialists ${spend['specialist_calls']}, "
        f"judge ${spend['judge_calls']}, total ${spend['total']}."
    )
    lines.append("")

    lines.append("## Per (agent, model)")
    lines.append("")
    lines.append(
        "| agent | model | n | band agreement | contract violations | judge calibration "
        "(mean/min) | judge substance | escalates | padding | wall p50 s | $/call p50 | "
        "out tok p50 | evidence/1k chars |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for cell in report["per_cell"]:
        band = cell["band"]
        judge = cell["judge"]
        measured = cell["measured"]
        agreement = (
            f"{band['agreements']}/{band['n_with_readable_band']}"
            if band["n_with_readable_band"]
            else "n/a"
        )
        lines.append(
            f"| {cell['domain']} | {cell['model'].split('claude-')[-1]} | {cell['n_usable']} "
            f"| {agreement} | {cell['contract']['total_violations']} "
            f"| {judge['calibration_mean']}/{judge['calibration_min']} "
            f"| {judge['substance_mean']} | {judge['escalates']} "
            f"| {judge['padding_observed']}/{judge['n_judged']} "
            f"| {measured['wall_s_median']} | {measured['cost_usd_median']} "
            f"| {measured['out_tok_median']} | {measured['evidence_per_1k_chars_median']} |"
        )
    lines.append("")

    lines.append("## Band disagreements with the pinned expectation")
    lines.append("")
    any_disagreement = False
    for cell in report["per_cell"]:
        for miss in cell["band"]["disagreements"]:
            any_disagreement = True
            lines.append(
                f"- `{cell['domain']}` / `{cell['model'].split('claude-')[-1]}` on "
                f"{miss['app']}: declared **{miss['declared']}**, pinned "
                f"**{miss['expected']}** ({miss['requirement_id']})"
            )
    if not any_disagreement:
        lines.append("- none: every readable band matched its pinned expectation.")
    lines.append("")

    bias = report["judge_self_preference"]
    lines.append("## The judge grades its own family (declared conflict)")
    lines.append("")
    lines.append(f"{bias['conflict']}")
    lines.append("")
    if bias.get("assessable"):
        lines.append(f"- {bias['reading']}")
        lines.append(
            f"- Deterministic cross-check (no judge involved): band agreement margin "
            f"{bias['deterministic_cross_check']['band_agreement_margin']:+}, contract "
            f"violations margin "
            f"{bias['deterministic_cross_check']['contract_violations_margin']:+}"
        )
    lines.append("")

    lines.append("## Is the extra length substance or padding? (paired by application)")
    lines.append("")
    lines.append(
        "| scope | longer | shorter | paired apps | chars ratio (median) | distinct-evidence "
        "ratio (median) | judge substance delta | pairs where only the longer one escalates |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for comparison in report["verbosity_vs_substance"]["comparisons"]:
        delta = comparison["substance_delta_median"]
        lines.append(
            f"| {comparison['scope']} | {comparison['longer']} | {comparison['shorter']} "
            f"| {comparison['n_paired_applications']} | {comparison['chars_ratio_median']}x "
            f"| {comparison['evidence_ratio_median']}x "
            f"| {'n/a' if delta is None else format(delta, '+')} "
            f"| {comparison['pairs_where_longer_escalates_alone']} |"
        )
    lines.append("")
    lines.append(
        "A chars ratio above the distinct-evidence ratio means the extra length is not "
        "carrying proportionally more evidence."
    )
    lines.append("")

    lines.append("## Is the cheaper model defensible, per agent?")
    lines.append("")
    for verdict in report["per_agent_verdict"]:
        lines.append(f"### {verdict['domain']} (currently `{verdict['current_model']}`)")
        lines.append("")
        lines.append(f"- **Verdict: {verdict['verdict']}**")
        for line in verdict["evidence"]:
            lines.append(f"- {line}")
        lines.append(f"- Sample: {verdict['sample_note']}")
        lines.append("")

    lines.append("## Which output rule applies to which agent (read from the prompts)")
    lines.append("")
    lines.append("| rule | agents | n |")
    lines.append("|---|---|---|")
    for rule, info in report["contract_scope"]["rules"].items():
        lines.append(f"| `{rule}` | {', '.join(info['applies_to']) or '(none)'} | {info['count']} |")
    lines.append("")
    lines.append("## Caveats")
    lines.append("")
    for caveat in report["caveats"]:
        lines.append(f"- {caveat}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reuse", action="append", default=[], help="JSON file of analyses already paid for")
    parser.add_argument("--sweep", default="", help="comma-separated domains to sweep live")
    parser.add_argument("--apps", default="APP-1004,APP-1012,APP-1009", help="comma-separated fixture ids")
    parser.add_argument("--models", default=",".join(CANDIDATE_MODELS), help="candidate model ids")
    parser.add_argument("--judge", action="store_true", help="run the blind judge")
    parser.add_argument("--judge-model", default=JUDGE_MODEL)
    parser.add_argument("--live", action="store_true", help="permit billed Bedrock calls")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and spend estimate only")
    parser.add_argument("--out", default="evals/results/tier_quality.json")
    parser.add_argument("--markdown", default="evals/results/tier_quality.md")
    parser.add_argument(
        "--analyses-out",
        default="evals/results/tier_quality_analyses.json",
        help="sidecar of every analysis text, in --reuse shape, so a re-judge is free",
    )
    parser.add_argument(
        "--verdicts",
        default="evals/results/tier_quality_verdicts.json",
        help="verdict cache: successful verdicts are kept, failed ones are retried",
    )
    parser.add_argument(
        "--fresh-verdicts",
        action="store_true",
        help="ignore the verdict cache and re-judge every analysis (billed again)",
    )
    return parser


def _plan(domains: Sequence[str], apps: Sequence[str], models: Sequence[str]) -> list[tuple[str, str, str]]:
    return [(d, m, a) for a in apps for d in domains for m in models]


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    domains = [d for d in args.sweep.split(",") if d]
    apps = [a for a in args.apps.split(",") if a]
    models = [m for m in args.models.split(",") if m]

    reused: list[AnalysisRow] = []
    for path in args.reuse:
        reused.extend(load_reuse_file(path))
    plan = _plan(domains, apps, models)

    verdict_path = REPO_ROOT / args.verdicts
    cached: dict[str, dict[str, Any]] = {}
    if args.judge and not args.fresh_verdicts and verdict_path.is_file():
        stored = json.loads(verdict_path.read_text(encoding="utf-8"))
        cached = {
            key: value
            for key, value in (stored.get("verdicts", {}) if isinstance(stored, dict) else {}).items()
            if "error" not in value
        }
    judge_calls = 0
    if args.judge:
        keys = {r.key for r in reused if r.text} | {f"{d}|{m}|{a}" for d, m, a in plan}
        judge_calls = len(keys - set(cached))
    estimate = spend_estimate(plan, judge_calls, measured_tokens=MEASURED_MEAN_TOKENS)

    print(f"reused analyses: {len(reused)}")
    print(f"planned specialist calls: {len(plan)}")
    print(f"cached verdicts reused: {len(cached)}")
    print(f"planned judge calls: {judge_calls} ({args.judge_model})")
    print(json.dumps({k: v for k, v in estimate.items() if k != "per_call"}, indent=1))
    if args.dry_run:
        return 0

    gate = _live_gate_error(args.live) if (plan or args.judge) else None
    if gate:
        print(f"refusing to spend: {gate}", file=sys.stderr)
        return 2

    rows = list(reused)
    swept: list[AnalysisRow] = []
    if plan:
        swept = asyncio.run(run_sweep(domains, apps, models, progress=print))
        rows.extend(swept)
        # Persist the paid-for text BEFORE judging: a judge failure must not throw
        # away specialist calls that have already been billed.
        analyses_path = REPO_ROOT / args.analyses_out
        analyses_path.parent.mkdir(parents=True, exist_ok=True)
        analyses_path.write_text(
            json.dumps(
                {
                    "schema": ANALYSES_SCHEMA,
                    "description": (
                        "verbatim specialist analyses from the tier sweep, kept so a "
                        "re-judge costs judge calls only"
                    ),
                    "analyses": [r.to_reuse_json() for r in rows],
                },
                indent=1,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"wrote {analyses_path} ({len(rows)} analyses)")

    verdicts: dict[str, dict[str, Any]] = {}
    if args.judge:
        verdicts = asyncio.run(
            judge_all(
                [r for r in rows if r.text],
                model_id=args.judge_model,
                progress=print,
                existing=cached,
            )
        )
        verdict_path.parent.mkdir(parents=True, exist_ok=True)
        verdict_path.write_text(
            json.dumps(
                {
                    "schema": VERDICTS_SCHEMA,
                    "description": (
                        "blind judge verdicts keyed by domain|model|application, kept so a "
                        "failed judge call can be retried without re-paying the successes"
                    ),
                    "judge_model": args.judge_model,
                    "verdicts": verdicts,
                },
                indent=1,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"wrote {verdict_path} ({len(verdicts)} verdicts)")

    evaluated = [evaluate_row(row, verdicts.get(row.key)) for row in rows]
    from agents.models import resolve_region

    report = build_report(
        evaluated,
        region=resolve_region(),
        judge_model=args.judge_model,
        live_calls={
            "specialist_calls_made": len(swept),
            "specialist_calls_failed": sum(1 for r in swept if r.error),
            "judge_calls_made": len(verdicts) - len(cached),
            "judge_verdicts_reused_from_cache": len(cached),
            "judge_calls_failed": sum(1 for v in verdicts.values() if "error" in v),
            "reused_analyses": len(reused),
            "reuse_sources": sorted({r.source for r in reused}),
        },
    )
    out_path = REPO_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    markdown_path = REPO_ROOT / args.markdown
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"\nwrote {out_path}\nwrote {markdown_path}")
    print(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
