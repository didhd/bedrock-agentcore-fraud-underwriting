"""
Guards on the module that decides whether the OTHER model family can do the fraud analysis.

WHY THESE TESTS EXIST. ``evals/set_comparison.py`` is the artifact that can turn "Luna is
8x faster and 12x cheaper on rings" into "so move the specialists to GPT". Latency and cost
already favour GPT decisively, which means every remaining incentive in this study points one
way, and the failure modes below are the ones that would let it say yes persuasively and
wrongly:

  1. **A single judge deciding a cross-family comparison.** The prior tier study MEASURED its
     Opus 5 judge preferring its own family by +0.31 calibration / +0.78 substance while the
     deterministic band margin moved -0.026. One judge from either family would settle this
     comparison by its own identity. So two judges from opposite families are required, and
     :func:`tests.test_set_comparison.test_disagreeing_judges_yield_no_finding` pins that
     disagreement produces NOT ESTABLISHED rather than an average.

  2. **A judge verdict repaired into agreement.** The GPT judge answers with raw JSON rather
     than a tool schema. A parser that clamped an out-of-range score, or defaulted a missing
     field, could manufacture the agreement between judges that this design treats as its
     strongest signal. Parsing must raise.

  3. **A band-extraction artifact scored as a quality difference.** 6 of the 50 committed
     analyses were unreadable to the shared extractor and ALL SIX were GPT, because GPT opens
     with the bare band on its own line. Left alone, that reads as "GPT fails to classify" and
     is purely an artifact. The fallback must recover those AND must never overrule the
     primary extractor.

  4. **Length mistaken for substance, in either direction.** The pro-GPT error is "shorter is
     efficient"; the pro-Claude error is "longer is thorough". The paired ratios must
     distinguish padding from lost evidence, and must say EVIDENCE LOST when the short
     analysis really does cite fewer of the payload's signals.

  5. **A false positive tolerated because the model was cheap.** The customer's stated goal is
     reducing false positives. Any excess over the incumbent on the anti-false-positive
     fixtures must be disqualifying on its own, no matter how good the latency looks.

  6. **A verdict declared on a sample too small to support it**, which is how the n=3 probe
     would have been over-read.

Offline only: no Bedrock call, no AWS credentials, no network.

Run:  MOCK_MODE=1 PYTHONPATH=. python -m pytest tests/test_set_comparison.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Final

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evals import set_comparison as sc  # noqa: E402
from evals import tier_quality as tq  # noqa: E402
from fixtures.loader import expected_for  # noqa: E402
from prompts.loader import DOMAINS  # noqa: E402

RESULT_PATH = REPO_ROOT / "evals" / "results" / "set_comparison.json"
ANALYSES_PATH = REPO_ROOT / "evals" / "results" / "set_comparison_analyses.json"


# ---------------------------------------------------------------------------
# 1. The two-judge design, which is the whole defence against a biased verdict
# ---------------------------------------------------------------------------


def test_the_two_judges_come_from_opposite_families() -> None:
    """One judge per family, or the comparison is decided by the judge's identity."""
    families = set(sc.JUDGES.values())
    assert families == {"claude", "gpt"}, (
        f"judges {sc.JUDGES} do not span both families; a single-family judge panel cannot "
        f"settle a cross-family comparison"
    )


def test_the_gpt_judge_is_not_one_of_the_candidates() -> None:
    """The GPT judge must not be grading its own output.

    The Claude judge unavoidably is (Opus 5 is the incumbent under test, and excluding it
    would leave the incumbent unmeasured), which is why that conflict is declared and
    quantified. There is no such excuse on the GPT side: 5.5 is not a candidate, so this
    asymmetry is a deliberate reduction of the total bias and must not silently regress.
    """
    candidates = set(sc.GPT_SET.values())
    gpt_judges = [j for j, family in sc.JUDGES.items() if family == "gpt"]
    for judge in gpt_judges:
        assert judge not in candidates, (
            f"{judge} is both a GPT judge and a GPT candidate; the GPT side of the panel "
            f"does not need this conflict"
        )


def test_the_claude_judge_conflict_is_declared_not_hidden() -> None:
    """The Claude judge IS a candidate, and the note must say so with the measured margin."""
    claude_judges = [j for j, family in sc.JUDGES.items() if family == "claude"]
    assert any(j in set(sc.CLAUDE_SET.values()) for j in claude_judges), (
        "the Claude judge is expected to also be a candidate here; if that changed, this "
        "test and JUDGE_CONFLICT_NOTE both need updating"
    )
    note = sc.JUDGE_CONFLICT_NOTE
    assert "+0.31" in note and "+0.78" in note, (
        "the conflict note must quote the MEASURED self-preference margins, not merely "
        "assert that bias is possible"
    )
    assert "-0.026" in note, (
        "the note must also quote the deterministic band margin, which is what shows the "
        "judged margin was mostly preference"
    )


def _evaluated(
    *,
    claude_cal: int,
    gpt_cal: int,
    claude_sub: int = 3,
    gpt_sub: int = 3,
    judges: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Two synthetic rows per set per judge, at the requested judge scores."""
    judge_ids = list(judges or sc.JUDGES)
    rows: list[dict[str, Any]] = []
    for label, which, cal, sub in (
        ("opus-5", "claude", claude_cal, claude_sub),
        ("luna", "gpt", gpt_cal, gpt_sub),
    ):
        rows.append(
            {
                "app": "APP-1009",
                "domain": "rings",
                "model": sc.MODELS[label],
                "model_label": label,
                "set": which,
                "error": None,
                "declared_band": "LOW RISK",
                "expected_band": "LOW RISK",
                "band_agrees": True,
                "escalates": False,
                "dismisses": False,
                "is_anti_fp_fixture": True,
                "band_justified": True,
                "contract_violations": [],
                "contract_violation_count": 0,
                "grounding": {"grounded_fraction": 0.5, "signals_grounded_in_context": 2},
                "signals": {"signals_cited": 4},
                "density": {"chars": 1000, "evidence_items": 10},
                "wall_s": 5.0,
                "usage": {"outputTokens": 400},
                "cost_usd": 0.01,
                "judges": {
                    judge: {
                        "verdict": {
                            "calibration_score": cal,
                            "substance_score": sub,
                            "false_positive_verdict": "Pass",
                            "escalation_drift": "aligned",
                            "padding_observed": False,
                            "cites_context_fields": True,
                            "band_read_from_analysis": "LOW RISK",
                        },
                        "error": None,
                        "cost_usd": 0.01,
                    }
                    for judge in judge_ids
                },
            }
        )
    return rows


def test_agreeing_judges_produce_a_finding() -> None:
    """Both judges favouring GPT by more than the bias floor is the strong signal."""
    rows = _evaluated(claude_cal=2, gpt_cal=5)
    pooled = [
        a
        for a in sc.two_judge_agreement(rows)["axes"]
        if a["axis"] == "calibration_score" and a["scope"] == "ALL AGENTS POOLED"
    ][0]
    assert pooled["verdict"] == "GPT better on both judges", pooled


def test_disagreeing_judges_yield_no_finding() -> None:
    """The case this design exists for: opposite signs must NOT average into a verdict.

    Here the Claude judge scores its own family far higher and the GPT judge does the
    mirror. Averaging would report "no difference", implying a measurement was made. The
    correct output is that nothing was established.
    """
    claude_judge = next(j for j, f in sc.JUDGES.items() if f == "claude")
    gpt_judge = next(j for j, f in sc.JUDGES.items() if f == "gpt")
    rows = _evaluated(claude_cal=3, gpt_cal=3)
    # Claude judge: Claude 5, GPT 1. GPT judge: Claude 1, GPT 5.
    for row in rows:
        own = 5 if row["set"] == "claude" else 1
        row["judges"][claude_judge]["verdict"]["calibration_score"] = own
        row["judges"][gpt_judge]["verdict"]["calibration_score"] = 6 - own
    pooled = [
        a
        for a in sc.two_judge_agreement(rows)["axes"]
        if a["axis"] == "calibration_score" and a["scope"] == "ALL AGENTS POOLED"
    ][0]
    assert pooled["verdict"].startswith("NOT ESTABLISHED"), (
        f"two judges pointing opposite ways must establish nothing, got {pooled['verdict']!r}"
    )


def test_a_margin_inside_the_measured_bias_floor_is_no_difference() -> None:
    """A gap smaller than the judge's own measured self-preference is not a finding.

    This is the rule the prior study asked for in so many words: any conclusion resting on
    a margin below the measured self-preference is unsupported.
    """
    rows = _evaluated(claude_cal=3, gpt_cal=3)
    pooled = [
        a
        for a in sc.two_judge_agreement(rows)["axes"]
        if a["axis"] == "calibration_score" and a["scope"] == "ALL AGENTS POOLED"
    ][0]
    assert pooled["verdict"].startswith("NO DIFFERENCE"), pooled
    assert sc.JUDGE_MARGIN_FLOOR == pytest.approx(0.31), (
        "the floor is the prior study's MEASURED calibration self-preference; changing it "
        "changes which findings survive and must be a deliberate, documented act"
    )


def test_a_missing_judge_verdict_establishes_nothing() -> None:
    """One judge failing must not let the surviving judge decide the axis alone."""
    gpt_judge = next(j for j, f in sc.JUDGES.items() if f == "gpt")
    rows = _evaluated(claude_cal=1, gpt_cal=5)
    for row in rows:
        row["judges"][gpt_judge] = {"verdict": None, "error": "throttled", "cost_usd": None}
    pooled = [
        a
        for a in sc.two_judge_agreement(rows)["axes"]
        if a["axis"] == "calibration_score" and a["scope"] == "ALL AGENTS POOLED"
    ][0]
    assert pooled["verdict"].startswith("NOT ESTABLISHED"), pooled


def test_self_preference_is_reported_per_judge_and_never_subtracted() -> None:
    """Each judge's own-family margin is published; no score is 'bias-corrected'."""
    claude_judge = next(j for j, f in sc.JUDGES.items() if f == "claude")
    rows = _evaluated(claude_cal=5, gpt_cal=1)
    bias = sc.judge_self_preference(rows)
    entry = bias["per_judge"][claude_judge]
    assert entry["assessable"] is True
    # GPT minus Claude is -4; for a Claude-family judge that is +4 for its own family.
    assert entry["gpt_minus_claude"]["calibration"] == pytest.approx(-4.0)
    assert entry["own_family_margin"]["calibration"] == pytest.approx(4.0)
    assert "deterministic_cross_check" in bias, (
        "the judged margin must be published next to the metrics no judge can influence"
    )


# ---------------------------------------------------------------------------
# 2. The GPT judge's JSON must be validated, never repaired
# ---------------------------------------------------------------------------


def _valid_verdict() -> dict[str, Any]:
    return {
        "band_read_from_analysis": "LOW RISK",
        "band_matches_expectation": True,
        "calibration_score": 4,
        "false_positive_verdict": "Pass",
        "escalation_drift": "aligned",
        "substance_score": 3,
        "padding_observed": False,
        "cites_context_fields": True,
        "contract_notes": "",
        "justification": "cites the multi-unit context row before clearing",
    }


def test_a_well_formed_gpt_verdict_parses() -> None:
    parsed = sc._parse_gpt_verdict(json.dumps(_valid_verdict()))
    assert parsed["calibration_score"] == 4
    assert parsed["band_read_from_analysis"] == "LOW RISK"


def test_a_gpt_verdict_wrapped_in_prose_or_a_fence_still_parses() -> None:
    """Tolerating a code fence is fine; tolerating a WRONG VALUE is not (see below)."""
    body = json.dumps(_valid_verdict())
    for wrapped in (f"```json\n{body}\n```", f"Here is my verdict:\n{body}\nDone."):
        assert sc._parse_gpt_verdict(wrapped)["substance_score"] == 3


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param({"calibration_score": 9}, id="score-above-range"),
        pytest.param({"calibration_score": 0}, id="score-below-range"),
        pytest.param({"substance_score": "high"}, id="score-not-an-int"),
        pytest.param({"calibration_score": True}, id="bool-is-not-a-score"),
        pytest.param({"escalation_drift": "somewhat higher"}, id="drift-off-enum"),
        pytest.param({"false_positive_verdict": "Fine"}, id="fp-off-enum"),
        pytest.param({"band_read_from_analysis": "MEDIUM-ISH"}, id="band-off-enum"),
        pytest.param({"padding_observed": "no"}, id="flag-not-a-bool"),
    ],
)
def test_an_invalid_gpt_verdict_raises_rather_than_being_coerced(mutation: dict[str, Any]) -> None:
    """A clamped or coerced score could manufacture agreement between the two judges.

    Agreement is this study's strongest claim, so a malformed verdict has to become a
    recorded judge ERROR - visible in the run's failure count - not a value nudged into
    range until it happens to match.
    """
    payload = {**_valid_verdict(), **mutation}
    with pytest.raises((ValueError, TypeError)):
        sc._parse_gpt_verdict(json.dumps(payload))


@pytest.mark.parametrize("field", sorted(_valid_verdict()))
def test_a_gpt_verdict_missing_any_field_raises(field: str) -> None:
    payload = {k: v for k, v in _valid_verdict().items() if k != field}
    with pytest.raises(ValueError, match="missing"):
        sc._parse_gpt_verdict(json.dumps(payload))


@pytest.mark.parametrize("reply", ["", "   ", "I decline to answer.", "[1, 2, 3]"])
def test_a_non_object_gpt_reply_raises(reply: str) -> None:
    with pytest.raises((ValueError, TypeError, json.JSONDecodeError)):
        sc._parse_gpt_verdict(reply)


def test_both_judges_are_scored_on_the_same_rubric() -> None:
    """Two judges on differently-worded rubrics are not comparable.

    The GPT judge gets its schema as text because mantle has no tool-schema path here, so
    the anchors have to be copied - and a copy can drift. Every field name in the Claude
    judge's pydantic model must appear in the GPT judge's schema text.
    """
    schema_text = sc._GPT_VERDICT_SCHEMA
    claude_fields = tq._judge_verdict_model().model_fields
    for name in claude_fields:
        assert name in schema_text, (
            f"{name} is scored by the Claude judge but is absent from the GPT judge's "
            f"schema; the two judges would not be answering the same question"
        )
    # And the shared instruction body really is shared, not a paraphrase of it. The GPT
    # judge's prompt is the Claude judge's prompt plus the schema text, so every fixed
    # section header of the shared template must survive into it.
    prompt = sc.judge_prompt(
        sc.AnalysisRow(app="APP-1009", domain="rings", model="x", text="LOW RISK.")
    )
    for marker in (
        "THE AGENT'S VERBATIM PRODUCTION PROMPT",
        "THE PINNED EXPECTATION FOR THIS APPLICATION",
        "THE ANALYSIS UNDER REVIEW",
        "Alert volume alone does NOT indicate risk",
    ):
        assert marker in prompt, f"the shared judge template lost {marker!r}"


def test_the_judge_prompt_carries_the_verbatim_prompt_and_the_pinned_band() -> None:
    """Blind means the label is hidden, not that the standard is."""
    row = sc.AnalysisRow(
        app="APP-1004",
        domain="rings",
        model=sc.MODELS["luna"],
        text="HIGH RISK\n\nWritten by GPT-5.6 Luna on Bedrock.",
    )
    prompt = sc.judge_prompt(row)
    expectation = expected_for("APP-1004")["domains"]["rings"]
    assert expectation["band"] in prompt
    assert expectation["requirement_id"] in prompt
    assert "luna" not in prompt.lower(), "the model identity must be stripped before judging"
    assert "gpt" not in prompt.lower().split("=== the analysis under review")[-1], (
        "the anonymiser must remove family tells from the analysis body"
    )


# ---------------------------------------------------------------------------
# 3. Band extraction: the artifact that was penalising GPT
# ---------------------------------------------------------------------------


#: Opening lines that are UNDECORATED - no heading marker, no bold - which is the shape
#: the shared extractor genuinely cannot read and the shape GPT actually produced in all
#: six recovered rows. ``**LOW RISK**`` and ``# LOW RISK`` are deliberately NOT here: they
#: are markdown headings and the primary extractor's own heading branch already reads them.
BARE_LEAD_LINES: Final = [
    ("HIGH RISK\n\nThe ring signals are corroborated.", "HIGH RISK"),
    ("LOW RISK\n\nExplained by the 240-unit building.", "LOW RISK"),
    ("POSSIBLE RISK\n\nOne unexplained overlap.", "POSSIBLE RISK"),
    ("LOW RISK.\n\nNothing corroborated.", "LOW RISK"),
    ("MODERATE RISK\n\nAliased to POSSIBLE.", "POSSIBLE RISK"),
]


@pytest.mark.parametrize("text,expected", BARE_LEAD_LINES)
def test_a_bare_band_on_the_opening_line_is_read(text: str, expected: str) -> None:
    """GPT's habit. The shared extractor returns None for all of these."""
    assert tq.extract_declared_band(text)[0] is None, (
        "if the shared extractor learned this shape, the local fallback is now redundant "
        "and should be deleted rather than left to duplicate it"
    )
    band, source = sc.declared_band(text)
    assert (band, source) == (expected, "lead-line")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("**LOW RISK**\n\nExplained by the 240-unit building.", "LOW RISK"),
        ("# POSSIBLE RISK\n\nOne unexplained overlap.", "POSSIBLE RISK"),
    ],
)
def test_a_decorated_band_heading_is_read_by_the_shared_extractor(
    text: str, expected: str
) -> None:
    """Recorded so the division of labour is explicit rather than accidental.

    A bold or ``#`` band line is a heading, and ``extract_declared_band`` already has a
    heading branch for it. Only the undecorated form above needed the local fallback, and
    keeping that boundary pinned stops the fallback quietly growing to duplicate the
    extractor.
    """
    assert tq.extract_declared_band(text)[0] == expected
    assert sc.declared_band(text) == (expected, "heading")


@pytest.mark.parametrize(
    "text,expected",
    [
        pytest.param(
            "# Synthetic Identity Fraud Risk Analysis\n\n**LOW RISK.** The only supported "
            "indicator is a thin credit file, age-appropriate for a 23-year-old.",
            "LOW RISK",
            id="bolded-after-heading-title",
        ),
        pytest.param(
            "# Identity Fraud Risk Analysis\n\nLOW RISK. The address-reuse signal is "
            "explained by a 240-unit high-rise.",
            "LOW RISK",
            id="unbolded-after-heading-title",
        ),
        pytest.param(
            "# Identity Fraud Risk Analysis\n\nLow risk — 96 identities across 240 units.",
            "LOW RISK",
            id="lower-case-with-dash",
        ),
        pytest.param(
            "**Identity Fraud Risk Analysis**\n\nHIGH RISK: three corroborated vectors.",
            "HIGH RISK",
            id="after-a-bold-title-line",
        ),
    ],
)
def test_a_band_opening_the_first_body_line_is_read(text: str, expected: str) -> None:
    """The other real shape, produced by BOTH families on the identity agent.

    A line that opens with the band and closes it with a terminator is a declaration, and
    the primary extractor reads none of these.
    """
    assert tq.extract_declared_band(text)[0] is None
    assert sc.declared_band(text) == (expected, "sentence-lead")


@pytest.mark.parametrize(
    "text",
    [
        "Low risk indicators are present but insufficient to escalate.",
        "# T\n\nThis application clears as low risk for identity fraud.",
        "# T\n\nConclusion: Possible identity fraud requiring verification.",
        "# T\n\nLOW RISK outcomes remain the most common band for this portfolio.",
    ],
)
def test_a_band_word_inside_prose_is_not_a_declaration(text: str) -> None:
    """The terminator is what separates a declaration from a mention.

    These four are REAL committed analyses' opening lines (or the prompt's own calibration
    text). Reading any of them as a verdict would fabricate a band the analysis never
    declared - and three of the four are Claude, so the residue of unreadable rows is not a
    thumb on the scale against GPT.
    """
    assert sc.declared_band(text)[0] is None


def test_a_band_followed_by_a_qualifying_sentence_is_not_a_bare_declaration() -> None:
    """The loophole a first-character-only tail check would leave open.

    "LOW RISK, however, HIGH RISK applies here" starts with a comma after the band, so a
    tail check that only inspected the first character would record LOW - reading the
    opposite of what the sentence says. The tail must be punctuation ALL the way to the end
    of the line.
    """
    text = "LOW RISK, however, HIGH RISK applies here on further review.\n\nBody follows."
    assert sc.declared_band(text)[0] is None


def test_prose_before_the_band_is_not_recovered() -> None:
    """At most ONE markdown title may precede the declaration."""
    text = "# Title\n\nSome introductory prose first.\n\nHIGH RISK\n\nbody"
    band, source = sc.declared_band(text)
    assert source != "lead-line", (
        f"a band appearing after prose is a section heading, not the opening declaration "
        f"(got source={source!r}, band={band!r})"
    )


def test_the_primary_extractor_always_wins() -> None:
    """The fallback must never overrule an explicit declaration."""
    text = "LOW RISK\n\nRisk Classification: HIGH RISK. Corroborated on three vectors."
    assert tq.extract_declared_band(text)[0] == "HIGH RISK"
    assert sc.declared_band(text)[0] == "HIGH RISK"


@pytest.mark.parametrize(
    "text",
    [
        "The signals support a high-risk finding.",
        "Alert volume alone does NOT indicate risk. LOW RISK should be common.",
        "Analysis follows.\n\nHIGH RISK\n\n(a heading much later in the document)",
        "LOW RISK outcomes remain the most common band for this portfolio.",
    ],
)
def test_prose_and_late_headings_are_not_read_as_a_declaration(text: str) -> None:
    """None is a real outcome: an unlocatable classification must not be invented.

    In particular the fallback only looks at the FIRST non-blank line, so a band appearing
    later - as a section heading, or inside the prompt's own calibration text quoted back -
    cannot become the verdict.
    """
    assert sc.declared_band(text)[0] is None


def test_the_fallback_never_contradicts_the_extractor_on_the_committed_corpus() -> None:
    """Validated on real analyses, not only on hand-written strings.

    This is the test that would catch a fallback which "fixes" GPT's rows by quietly
    reinterpreting Claude's.
    """
    if not ANALYSES_PATH.is_file():
        pytest.skip("no committed analyses to validate against")
        return
    rows = json.loads(ANALYSES_PATH.read_text(encoding="utf-8"))["analyses"]
    assert rows, "the committed analyses file is empty"
    for row in rows:
        text = row.get("text") or ""
        if not text:
            continue
        primary = tq.extract_declared_band(text)[0]
        if primary is not None:
            assert sc.declared_band(text)[0] == primary, (
                f"{row['app']}/{row['domain']}/{row['model']}: the fallback changed a band "
                f"the primary extractor had already read"
            )


def test_band_recovery_is_not_one_sided_by_construction() -> None:
    """The fallback keys on line SHAPE, never on which family produced the text.

    It happens to recover GPT rows because GPT writes that way, and the same shape from a
    Claude analysis must be recovered identically. A rule that keyed on the model would be
    a thumb on the scale.
    """
    text = "HIGH RISK\n\nCorroborated across three independent vectors."
    for label in sc.MODELS:
        row = sc.AnalysisRow(app="APP-1004", domain="rings", model=sc.MODELS[label], text=text)
        assert sc.evaluate_row(row, {})["declared_band"] == "HIGH RISK"


# ---------------------------------------------------------------------------
# 4. The length question, which must be answerable in BOTH directions
# ---------------------------------------------------------------------------


def test_signals_cited_counts_prose_not_just_identifiers() -> None:
    """A model writing prose must not score zero for avoiding snake_case.

    ``evidence_density`` counts snake_case tokens and so rewards identifier style. The
    length comparison cannot rest on that, or "GPT dropped evidence" would just mean "GPT
    writes English".
    """
    prose = (
        "Six distinct SSNs reuse the address, and the phone is reused across five SSNs, "
        "while the email is tied to four and the employer covers nine others."
    )
    scored = sc.signals_cited("APP-1004", "rings", prose)
    assert scored["signals_cited"] >= 3, scored
    identifiers = " ".join(sc.shared_signal_names("rings", "APP-1004"))
    assert sc.signals_cited("APP-1004", "rings", identifiers)["signals_cited"] == scored[
        "signals_in_view"
    ]


def test_signals_cited_denominator_is_the_payload_not_the_registry() -> None:
    """Only the signals this agent's view actually carries can be cited."""
    for domain in ("rings", "synthetic"):
        names = sc.shared_signal_names(domain, "APP-1004")
        assert names
        assert sc.signals_cited("APP-1004", domain, "")["signals_in_view"] == len(names)


def _pair_rows(
    *, claude_chars: int, gpt_chars: int, claude_signals: int, gpt_signals: int
) -> list[dict[str, Any]]:
    rows = _evaluated(claude_cal=3, gpt_cal=3)
    for row in rows:
        is_claude = row["set"] == "claude"
        row["density"] = {
            "chars": claude_chars if is_claude else gpt_chars,
            "evidence_items": 20 if is_claude else 10,
        }
        row["signals"] = {"signals_cited": claude_signals if is_claude else gpt_signals}
        row["grounding"] = {
            "grounded_fraction": 0.5,
            "signals_grounded_in_context": 2 if is_claude else 2,
        }
    return rows


def test_more_words_for_the_same_signals_is_reported_as_padding() -> None:
    gap = sc.length_gap(_pair_rows(claude_chars=4000, gpt_chars=1000, claude_signals=4, gpt_signals=4))
    pooled = [c for c in gap["comparisons"] if c["scope"] == "ALL AGENTS POOLED"][0]
    assert pooled["chars_ratio_median"] == pytest.approx(4.0)
    assert pooled["signals_cited_ratio_median"] == pytest.approx(1.0)
    assert pooled["finding"].startswith("PADDING"), pooled["finding"]


def test_fewer_signals_in_the_shorter_analysis_is_reported_as_evidence_lost() -> None:
    """The finding that must be reachable, or the study cannot conclude against GPT.

    If brevity really does drop evidence, the module has to say so - that is the outcome
    that outweighs the latency and cost win.
    """
    gap = sc.length_gap(_pair_rows(claude_chars=1500, gpt_chars=1000, claude_signals=4, gpt_signals=1))
    pooled = [c for c in gap["comparisons"] if c["scope"] == "ALL AGENTS POOLED"][0]
    assert pooled["finding"].startswith("EVIDENCE LOST"), pooled["finding"]


def test_the_finding_does_not_assume_claude_is_the_longer_one() -> None:
    """"the shorter GPT analysis" must not be printed about the LONGER GPT analysis.

    Claude being longer is the expected case but not a guaranteed one - Opus is already
    terse on synthetic - and an earlier revision hard-coded the direction into the wording,
    so a GPT analysis 3x the length was described as "the shorter GPT analysis names 1.0x
    the signals for 3.03x the length": both labels inverted.
    """
    gap = sc.length_gap(_pair_rows(claude_chars=1000, gpt_chars=3000, claude_signals=4, gpt_signals=4))
    pooled = [c for c in gap["comparisons"] if c["scope"] == "ALL AGENTS POOLED"][0]
    assert pooled["claude_is_longer"] is False
    assert "shorter GPT" not in pooled["finding"], pooled["finding"]
    assert "GPT IS THE LONGER ONE" in pooled["finding"], pooled["finding"]


def test_the_comparison_names_each_model_by_family_not_by_length() -> None:
    gap = sc.length_gap(_pair_rows(claude_chars=4000, gpt_chars=1000, claude_signals=4, gpt_signals=4))
    pooled = [c for c in gap["comparisons"] if c["scope"] == "ALL AGENTS POOLED"][0]
    assert pooled["claude_model"] == "opus-5" and pooled["gpt_model"] == "luna"
    assert pooled["claude_is_longer"] is True


def test_the_length_comparison_is_paired_on_the_same_application() -> None:
    """An unpaired length comparison could be an artifact of easier applications."""
    rows = _evaluated(claude_cal=3, gpt_cal=3)
    rows[1]["app"] = "APP-1013"  # GPT answered a different application
    gap = sc.length_gap(rows)
    assert gap["comparisons"] == [], (
        "with no shared (application, domain) there is nothing to compare and the module "
        "must report nothing rather than compare across applications"
    )


def test_an_unreadable_band_is_excluded_from_the_pairing() -> None:
    rows = _evaluated(claude_cal=3, gpt_cal=3)
    rows[0]["declared_band"] = None
    assert sc.length_gap(rows)["comparisons"] == []


# ---------------------------------------------------------------------------
# 5. False positives outrank everything, including cost
# ---------------------------------------------------------------------------


def _cell(
    *,
    label: str,
    which: str,
    domain: str = "rings",
    n: int = 6,
    band_agreements: int = 6,
    anti_fp: int = 0,
    anti_fp_n: int = 2,
    false_negatives: int = 0,
    grounded: float | None = 0.5,
    violations: int = 0,
) -> dict[str, Any]:
    return {
        "domain": domain,
        "model_label": label,
        "model": sc.MODELS.get(label, label),
        "set": which,
        "n_calls": n,
        "n_usable": n,
        "n_failed": 0,
        "n_unreadable_band": 0,
        "unreadable_band_apps": [],
        "band": {
            "n": n,
            "agreements": band_agreements,
            "agreement_rate": band_agreements / n,
            "escalations": anti_fp,
            "dismissals": false_negatives,
            "disagreements": [],
        },
        "anti_false_positive": {
            "n_fixtures": anti_fp_n,
            "false_positives": anti_fp,
            "apps_escalated": ["APP-1010"] * anti_fp,
        },
        "false_negatives": {"n_at_risk": 3, "count": false_negatives, "apps": ["APP-1004"] * false_negatives},
        "contract": {"total_violations": violations, "rows_clean": n, "by_rule": {}},
        "measured": {
            "wall_s_median": 4.0,
            "wall_s": {},
            "cost_usd_median": 0.005,
            "out_tok_median": 425,
            "chars_median": 1500,
            "evidence_items_median": 10,
            "evidence_per_1k_chars_median": 6.0,
            "signals_cited_median": 4,
            "grounded_fraction_median": grounded,
            "grounded_signals_median": 2,
            "band_justified_rate": 1.0,
        },
        "judges": {j: {"n_judged": n, "calibration_mean": 4.0, "substance_mean": 4.0} for j in sc.JUDGES},
    }


def test_an_extra_false_positive_disqualifies_however_cheap_the_model_is() -> None:
    """The customer's stated goal. Latency and cost must not buy their way past it."""
    cells = [
        _cell(label="opus-5", which="claude", anti_fp=0),
        _cell(label="luna", which="gpt", anti_fp=1),
    ]
    candidate = sc.per_set_verdict(cells)[0]["candidates"][0]
    assert candidate["verdict"].startswith("NOT AS GOOD"), candidate["verdict"]
    assert "false positive" in candidate["verdict"]
    assert candidate["disqualifying"] is True


def test_an_extra_false_negative_disqualifies() -> None:
    """The expensive error in the other direction: a funded fraud."""
    cells = [
        _cell(label="opus-5", which="claude", false_negatives=0),
        _cell(label="luna", which="gpt", false_negatives=1),
    ]
    candidate = sc.per_set_verdict(cells)[0]["candidates"][0]
    assert candidate["verdict"].startswith("NOT AS GOOD")
    assert "dismissal" in candidate["verdict"]


def test_worse_band_agreement_disqualifies() -> None:
    cells = [
        _cell(label="opus-5", which="claude", band_agreements=6),
        _cell(label="luna", which="gpt", band_agreements=4),
    ]
    candidate = sc.per_set_verdict(cells)[0]["candidates"][0]
    assert candidate["verdict"].startswith("NOT AS GOOD")
    assert "band agreement" in candidate["verdict"]


def test_collapsed_grounding_disqualifies() -> None:
    """Guessing the right band without validating against _context is not the job.

    Every specialist prompt requires validation against the paired ``_context`` columns, so
    a model that reaches the band while ignoring them has not done what was asked.
    """
    cells = [
        _cell(label="opus-5", which="claude", grounded=1.0),
        _cell(label="luna", which="gpt", grounded=0.1),
    ]
    candidate = sc.per_set_verdict(cells)[0]["candidates"][0]
    assert candidate["verdict"].startswith("NOT AS GOOD")
    assert "grounded" in candidate["verdict"]


def test_a_clean_cheaper_model_can_pass() -> None:
    """The rule must be capable of saying yes, or it is not a test of anything."""
    cells = [
        _cell(label="opus-5", which="claude", band_agreements=5, grounded=0.6),
        _cell(label="luna", which="gpt", band_agreements=6, grounded=0.5),
    ]
    candidate = sc.per_set_verdict(cells)[0]["candidates"][0]
    assert candidate["verdict"] == "AS GOOD ON THIS SAMPLE", candidate["verdict"]
    assert candidate["disqualifying"] is False


def test_a_small_sample_refuses_a_verdict_however_good_the_numbers_look() -> None:
    """The n=3 probe's mistake, made impossible."""
    cells = [
        _cell(label="opus-5", which="claude", n=3, band_agreements=3),
        _cell(label="luna", which="gpt", n=3, band_agreements=3),
    ]
    candidate = sc.per_set_verdict(cells)[0]["candidates"][0]
    assert candidate["verdict"].startswith("n TOO SMALL"), candidate["verdict"]
    assert str(sc.MIN_APPS_FOR_A_BAND_CLAIM) in candidate["verdict"]


def test_the_claude_set_is_never_scored_as_a_candidate_against_itself() -> None:
    cells = [_cell(label="opus-5", which="claude"), _cell(label="sonnet-5", which="claude")]
    verdict = sc.per_set_verdict(cells)[0]
    assert verdict["candidates"] == [], "only GPT models are candidates in this study"


# ---------------------------------------------------------------------------
# 6. Deterministic scoring: escalation direction, anti-FP fixtures, contract
# ---------------------------------------------------------------------------


def test_escalation_and_dismissal_are_measured_by_direction_not_just_mismatch() -> None:
    """The two errors have different prices, so they cannot be one "disagreement" count."""
    row = sc.AnalysisRow(
        app="APP-1009",  # pinned LOW RISK on rings
        domain="rings",
        model=sc.MODELS["luna"],
        text="HIGH RISK\n\nNinety-six SSNs at the address.",
    )
    evaluated = sc.evaluate_row(row, {})
    assert evaluated["expected_band"] == "LOW RISK"
    assert evaluated["escalates"] is True
    assert evaluated["dismisses"] is False
    assert evaluated["band_agrees"] is False
    assert evaluated["is_anti_fp_fixture"] is True, (
        "APP-1009 is an anti-false-positive fixture; an escalation here is the exact error "
        "the customer is paying to remove"
    )


def test_the_anti_fp_fixture_list_matches_the_fixtures_built_for_that_purpose() -> None:
    """Pinned against the fixture module, so the list cannot drift into convenience."""
    from fixtures import _scenarios_fp

    assert set(sc.ANTI_FP_APPS) == {s.app_id for s in _scenarios_fp.FP_SCENARIOS}


def test_the_sweep_includes_anti_fp_fixtures_and_a_genuinely_risky_one() -> None:
    """A sample of only benign applications rewards a model that always says LOW."""
    assert set(sc.SWEEP_APPS) & set(sc.ANTI_FP_APPS), "no anti-false-positive fixture in the sweep"
    risky = [
        app
        for app in sc.SWEEP_APPS
        if any(
            expected_for(app)["domains"][d]["band"] == "HIGH RISK" for d in sc.SWEEP_DOMAINS
        )
    ]
    assert risky, (
        "no application in the sweep pins HIGH RISK on a swept agent, so a model that "
        "declares LOW everywhere could score perfectly"
    )


def test_the_known_shared_failures_match_the_fixtures_pinned_bands() -> None:
    """The two cases every Claude tier failed, pinned against the fixtures themselves."""
    assert sc.KNOWN_SHARED_FAILURES, "the shared-failure cases must be recorded"
    for app, domain, expected_band, claude_band in sc.KNOWN_SHARED_FAILURES:
        assert expected_for(app)["domains"][domain]["band"] == expected_band, (
            f"{app}/{domain}: recorded expectation {expected_band} does not match the fixture"
        )
        assert claude_band != expected_band, (
            "a 'shared failure' whose recorded Claude band equals the pinned band is not a "
            "failure at all"
        )
        assert app in sc.SWEEP_APPS and domain in sc.SWEEP_DOMAINS, (
            f"{app}/{domain} is a known failure but is not in the sweep, so whether GPT "
            f"reproduces it could not be measured"
        )


def test_known_failure_reproduction_reports_per_model_and_per_set() -> None:
    rows = []
    for label, which, band in (
        ("opus-5", "claude", "POSSIBLE RISK"),
        ("luna", "gpt", "LOW RISK"),
    ):
        rows.append(
            {
                "app": "APP-1014",
                "domain": "rings",
                "model": sc.MODELS[label],
                "model_label": label,
                "set": which,
                "error": None,
                "declared_band": band,
                "expected_band": "LOW RISK",
                "band_agrees": band == "LOW RISK",
                "requirement_id": "RNG-020",
                "customer_rule": "…",
            }
        )
    case = [c for c in sc.known_failure_reproduction(rows) if c["app"] == "APP-1014"][0]
    assert case["claude_reproduces"] == 1 and case["claude_n"] == 1
    assert case["gpt_reproduces"] == 0 and case["gpt_n"] == 1
    assert case["gpt_correct"] == 1


def test_contract_violations_come_from_the_shared_checker() -> None:
    """Reuse ``agents.output_contract``; a second opinion would be a different contract."""
    invented_title = "# Fraud Ring Analysis\n\nHIGH RISK\n\nCorroborated."
    evaluated = sc.evaluate_row(
        sc.AnalysisRow(app="APP-1004", domain="rings", model=sc.MODELS["luna"], text=invented_title),
        {},
    )
    rules = {v["rule"] for v in evaluated["contract_violations"]}
    assert "title_invented" in rules, (
        "rings defines no title, and the shared checker flags an invented one"
    )
    from agents import output_contract as oc

    assert rules == {v.rule for v in oc.check("rings", invented_title).violations}


def test_latency_and_cost_are_measured_over_every_row_that_answered() -> None:
    """An unreadable band must NOT remove a real wall-clock measurement from the median.

    This was a live distortion: scoring latency over the band-readable subset only would
    make a model whose prose is hard to parse look FASTER, because its dropped rows are
    dropped from the timing too. The band subset is for band claims; timing, cost, length
    and contract are measured over everything that answered.
    """
    rows = []
    for app, text, wall in (
        ("APP-1009", "LOW RISK\n\nExplained by the building.", 2.0),
        # Declares no locatable band, but took 40 seconds and cost real money.
        ("APP-1010", "The signals support a low-risk reading overall.", 40.0),
    ):
        rows.append(
            sc.evaluate_row(
                sc.AnalysisRow(
                    app=app,
                    domain="rings",
                    model=sc.MODELS["luna"],
                    text=text,
                    wall_s=wall,
                    usage={"inputTokens": 10, "outputTokens": 10},
                    cost_usd=0.01,
                ),
                {},
            )
        )
    cell = sc.aggregate(rows)[0]
    assert cell["n_usable"] == 2, "both calls answered"
    assert cell["band"]["n"] == 1, "only one declared a locatable band"
    assert cell["n_unreadable_band"] == 1
    assert cell["unreadable_band_apps"] == ["APP-1010"]
    assert cell["measured"]["wall_s_median"] == pytest.approx(21.0), (
        "the 40s call must stay in the latency median; excluding it would flatter the model"
    )


def test_an_unreadable_band_is_disqualifying_and_caps_the_sample() -> None:
    """Declaring no band is a defect, and it cannot be averaged away.

    The customer requires that reasoning, classification and conclusion align. A model that
    answers six times but declares a band five times has n=5 for a band claim AND carries a
    recorded defect, so it must not read as "AS GOOD".
    """
    cells = [
        _cell(label="opus-5", which="claude"),
        {**_cell(label="luna", which="gpt"), "n_unreadable_band": 1, "unreadable_band_apps": ["APP-1010"]},
    ]
    candidate = sc.per_set_verdict(cells)[0]["candidates"][0]
    assert candidate["verdict"].startswith("NOT AS GOOD"), candidate["verdict"]
    assert "no locatable" in candidate["verdict"]
    assert candidate["n_unreadable_band"] == 1


def test_the_sample_test_counts_band_readable_rows_not_answered_rows() -> None:
    cells = [
        _cell(label="opus-5", which="claude"),
        {**_cell(label="luna", which="gpt"), "band": {**_cell(label="luna", which="gpt")["band"], "n": 4, "agreements": 4}},
    ]
    candidate = sc.per_set_verdict(cells)[0]["candidates"][0]
    assert candidate["verdict"].startswith("n TOO SMALL"), candidate["verdict"]
    assert "readable band" in candidate["verdict"]


def test_a_failed_specialist_call_scores_nothing_rather_than_zero() -> None:
    """An error row must not be counted as a wrong answer or a clean contract."""
    row = sc.AnalysisRow(
        app="APP-1004", domain="rings", model=sc.MODELS["luna"], text="", error="ThrottlingException"
    )
    evaluated = sc.evaluate_row(row, {})
    assert evaluated["declared_band"] is None
    assert evaluated["band_agrees"] is None
    assert evaluated["grounding"] is None and evaluated["density"] is None
    cells = sc.aggregate([evaluated])
    assert cells[0]["n_usable"] == 0 and cells[0]["n_failed"] == 1


# ---------------------------------------------------------------------------
# 7. Cost and spend: measured or None, never modelled
# ---------------------------------------------------------------------------


def test_gpt_cost_prices_the_cache_write_term() -> None:
    """Omitting cache-WRITE under-reported a measured call by 62%.

    GPT-5.6 implicit caching is ON, so a first call against a large prompt reports most of
    its input as cacheWriteInputTokens at 1.25x the input rate.
    """
    from agents import mantle

    usage = {
        "inputTokens": 100,
        "outputTokens": 100,
        "cacheReadInputTokens": 0,
        "cacheWriteInputTokens": 1000,
    }
    with_write = mantle.cost_usd("openai.gpt-5.6-luna", usage)
    without = mantle.cost_usd("openai.gpt-5.6-luna", {**usage, "cacheWriteInputTokens": 0})
    assert with_write > without
    rate_in = mantle.MANTLE_RATES_PER_MTOK["openai.gpt-5.6-luna"][0]
    assert with_write - without == pytest.approx(1000 * rate_in * 1.25 / 1_000_000)


def test_the_spend_estimate_never_invents_a_price_for_an_unmeasured_domain() -> None:
    plan = [("dealer", sc.MODELS["luna"], "APP-1004")]
    estimate = sc.spend_estimate(plan, {})
    assert estimate["specialist_calls_unpriced"] == 1
    assert estimate["per_call"][0]["usd"] is None
    assert "NOT a prediction" in estimate["basis"]


def test_the_spend_estimate_prices_both_families_and_both_judges() -> None:
    plan = [
        ("rings", sc.MODELS["luna"], "APP-1004"),
        ("rings", sc.MODELS["opus-5"], "APP-1004"),
    ]
    estimate = sc.spend_estimate(plan, {j: 2 for j in sc.JUDGES})
    assert estimate["specialist_calls_unpriced"] == 0
    assert estimate["specialist_usd_estimate"] > 0
    assert set(estimate["judges"]) == set(sc.JUDGES)
    assert estimate["judge_usd_estimate"] > 0
    assert estimate["total_usd_estimate"] == pytest.approx(
        estimate["specialist_usd_estimate"] + estimate["judge_usd_estimate"], abs=1e-4
    )


def test_a_row_with_no_measured_usage_contributes_no_cost() -> None:
    row = sc.AnalysisRow(app="APP-1009", domain="rings", model=sc.MODELS["luna"], text="LOW RISK.")
    cell = sc.aggregate([sc.evaluate_row(row, {})])[0]
    assert cell["measured"]["cost_usd_median"] is None
    assert cell["measured"]["out_tok_median"] is None


def test_percentiles_are_refused_below_the_supporting_sample() -> None:
    """Reuses ``evals.bench.summarize_values`` policy rather than re-implementing it laxly."""
    rows = []
    for app in ("APP-1009", "APP-1010"):
        row = sc.AnalysisRow(
            app=app,
            domain="rings",
            model=sc.MODELS["luna"],
            text="LOW RISK.",
            wall_s=3.0,
            usage={"inputTokens": 1, "outputTokens": 1},
            cost_usd=0.001,
        )
        rows.append(sc.evaluate_row(row, {}))
    summary = sc.aggregate(rows)[0]["measured"]["wall_s"]
    assert summary.get("p50") is None, (
        f"a p50 from 2 samples must be refused, got {summary}"
    )


# ---------------------------------------------------------------------------
# 8. Live spending is double-gated, exactly like the other benchmarks
# ---------------------------------------------------------------------------


def test_live_calls_need_the_flag_and_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sc.LIVE_ENV_GATE, raising=False)
    assert sc._live_gate_error(False) is not None
    assert sc._live_gate_error(True) is not None
    monkeypatch.setenv(sc.LIVE_ENV_GATE, "1")
    assert sc._live_gate_error(True) is None
    assert sc._live_gate_error(False) is not None


def test_dry_run_spends_nothing_and_prints_the_plan(capsys: pytest.CaptureFixture[str]) -> None:
    code = sc.main(["--sweep", "rings", "--apps", "APP-1004", "--judge", "--dry-run"])
    assert code == 0
    out = capsys.readouterr().out
    assert "planned specialist calls" in out
    assert "total_usd_estimate" in out


def test_a_live_run_without_the_env_var_refuses(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(sc.LIVE_ENV_GATE, raising=False)
    code = sc.main(["--sweep", "rings", "--apps", "APP-1004", "--live"])
    assert code == 2
    assert "refusing to spend" in capsys.readouterr().err


def test_the_plan_skips_cells_already_paid_for() -> None:
    have = {f"rings|{sc.MODELS['luna']}|APP-1004"}
    plan = sc._plan(["rings"], ["APP-1004"], ["luna", "opus-5"], have)
    assert (("rings", sc.MODELS["luna"], "APP-1004")) not in plan
    assert (("rings", sc.MODELS["opus-5"], "APP-1004")) in plan


# ---------------------------------------------------------------------------
# 9. The committed report must stay honest and re-readable
# ---------------------------------------------------------------------------


def test_a_report_is_committed() -> None:
    assert RESULT_PATH.is_file(), (
        f"no committed report at {RESULT_PATH}; run "
        "`PYTHONPATH=. python3 -m evals.set_comparison --rescore`"
    )


@pytest.fixture(scope="module")
def report() -> dict[str, Any]:
    if not RESULT_PATH.is_file():
        pytest.skip("no committed report")
    return json.loads(RESULT_PATH.read_text(encoding="utf-8"))


def test_the_report_declares_its_schema_and_provenance(report: dict[str, Any]) -> None:
    assert report["schema"] == sc.SCHEMA
    run = report["run"]
    assert run["timestamp_utc"] and run["aws_region"]
    assert set(run["judges"]) == set(sc.JUDGES)
    assert run["blinding"]
    assert run["specialist_max_output_tokens"] == sc.SPECIALIST_MAX_TOKENS, (
        "both families must be capped identically, or the cap is inside the length finding"
    )


def test_the_report_declares_the_judge_conflict(report: dict[str, Any]) -> None:
    """A study whose judge grades its own family must say so where a reader will look."""
    assert any("+0.31" in c for c in report["caveats"]), (
        "the measured self-preference margin must appear in the caveats"
    )
    assert sc.JUDGE_CONFLICT_NOTE in report["caveats"]


def test_the_report_names_what_the_sample_cannot_settle(report: dict[str, Any]) -> None:
    joined = " ".join(report["caveats"]).lower()
    for expected in ("anti-false-positive", "p95", "converse"):
        assert expected in joined, f"the caveats do not mention {expected}"


def test_every_reported_cost_is_measured_or_absent(report: dict[str, Any]) -> None:
    for row in report["per_analysis"]:
        if row.get("error"):
            continue
        if row["cost_usd"] is None:
            continue
        assert row["usage"], (
            f"{row['app']}/{row['domain']}/{row['model_label']} reports a cost with no "
            f"measured usage, which would make it modelled rather than measured"
        )


def test_every_rate_in_the_report_carries_its_denominator(report: dict[str, Any]) -> None:
    for cell in report["per_cell"]:
        assert isinstance(cell["band"]["n"], int), "every rate must publish its denominator"
        assert cell["band"]["agreements"] <= cell["band"]["n"]
        assert cell["band"]["escalations"] + cell["band"]["dismissals"] == cell["band"]["n"] - cell[
            "band"
        ]["agreements"], (
            f"{cell['domain']}/{cell['model_label']}: every band disagreement must be "
            f"classified as an escalation or a dismissal, or the two error types do not add up"
        )
        anti = cell["anti_false_positive"]
        assert anti["false_positives"] <= anti["n_fixtures"]
        if cell["band"]["n"] == 0:
            assert cell["band"]["agreement_rate"] is None, (
                "a rate with a zero denominator must be None, not 0.0"
            )


def test_the_report_verdicts_respect_the_sample_size(report: dict[str, Any]) -> None:
    """No cell may claim AS GOOD on fewer applications than a band claim needs."""
    for verdict in report["per_set_verdict"]:
        for candidate in verdict["candidates"]:
            if candidate["verdict"] == "AS GOOD ON THIS SAMPLE":
                assert candidate["n"] >= sc.MIN_APPS_FOR_A_BAND_CLAIM, (
                    f"{verdict['domain']}/{candidate['model_label']} claims AS GOOD on "
                    f"n={candidate['n']}"
                )


def test_the_markdown_renders_from_the_committed_report(report: dict[str, Any]) -> None:
    rendered = sc.render_markdown(report)
    assert rendered.startswith("# Is the GPT set as good at the fraud analysis?")
    for judge in sc.JUDGES:
        assert sc._judge_short(judge) in rendered
    assert "5.5 (gpt)" in rendered or "gpt-5.5 (gpt)" in rendered, (
        "the GPT judge column must be legible; splitting the id on '.' renders it as '5'"
    )
    assert "None" not in rendered.replace("NoneType", ""), (
        "a missing measurement must render as an em dash, not the string 'None'"
    )


def test_the_markdown_is_committed_next_to_the_json() -> None:
    markdown = REPO_ROOT / "evals" / "results" / "set_comparison.md"
    assert markdown.is_file(), "the human-readable summary must be committed too"
    text = markdown.read_text(encoding="utf-8")
    assert "length gap" in text.lower()
    assert "judge" in text.lower()


def test_the_swept_domains_are_real_agents() -> None:
    for domain in sc.SWEEP_DOMAINS:
        assert domain in DOMAINS, f"{domain} is not one of the customer's eight agents"


def test_set_membership_is_derived_not_guessed() -> None:
    for label, model_id in sc.CLAUDE_SET.items():
        assert sc.set_of(label) == "claude" and sc.set_of(model_id) == "claude"
    for label, model_id in sc.GPT_SET.items():
        assert sc.set_of(label) == "gpt" and sc.set_of(model_id) == "gpt"
    assert sc.label_of(sc.MODELS["luna"]) == "luna"
