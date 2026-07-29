"""
Gate on the published documentation site (``site/``).

WHY THIS EXISTS

The site is the artifact an AWS SA hands to the customer's engineer and to their
executive, so it carries two risks that no other part of the repo carries:

  1. **Confidentiality.** The customer's nine agent prompts and nine orchestration
     documents are hers. They live in git-ignored ``prompts/verbatim/`` precisely
     so they cannot be published. A documentation page that quotes a paragraph of
     one -- easy to do while explaining a rule -- would put confidential customer
     material in a git repository. So this module shingles every verbatim file
     into normalised word n-grams and asserts that none of them appears in any
     published page. Short phrases are allowed by design (the exact wording of a
     rule is sometimes the point being made); a contiguous run of
     ``_LEAK_NGRAM`` words is not a phrase, it is a copy.

  2. **Fabrication.** A draft summary of this engagement quoted "130 analyses over
     8 domains, judged by GPT-5.6 Sol, band 23/26 per model, 12.3 s end-to-end at
     $0.0508" -- and not one of those figures appears in any artifact on disk. The
     site's own stated rule is that every number traces to a committed artifact.
     Two guards enforce it mechanically: the known-bad figures may never appear,
     and every ``evals/results/*`` or ``docs/*`` path a page cites as its source
     must actually exist.

Both guards skip cleanly when the thing they guard is absent (no ``site/``
checkout, or ``prompts/verbatim/`` not restored), so this file is safe in CI
without the confidential documents present.

Run:  python -m pytest tests/test_docs_site.py -q
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Final

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT: Final = Path(__file__).resolve().parent.parent
SITE_CONTENT: Final = REPO_ROOT / "site" / "content" / "docs"
VERBATIM_DIR: Final = REPO_ROOT / "prompts" / "verbatim"

#: A contiguous run of this many words shared with a customer document is a copy,
#: not a citation. Twelve is low enough to catch a single copied sentence and high
#: enough that a page may quote a rule's operative phrase -- "no risk labels in
#: headers", "LOW RISK / POSSIBLE RISK / HIGH RISK" -- without tripping.
_LEAK_NGRAM: Final[int] = 12

#: Figures from the fabricated draft. None of them appears in any artifact on
#: disk, so none of them may appear on a page. Kept as the literal strings a
#: writer would type.
FABRICATED_FIGURES: Final[tuple[str, ...]] = (
    "130 analyses",
    "23/26",
    "$0.0508",
    "12.3 s end-to-end",
    "12.3s end-to-end",
)

#: Paths a page may cite. Matched loosely -- a page cites `evals/results/x.json`
#: inside prose or a table cell, so the surrounding markup varies.
_CITED_PATH: Final = re.compile(
    r"(?:evals/results/[A-Za-z0-9_./-]+\.(?:json|md)"
    r"|docs/[A-Za-z0-9_./-]+\.md"
    r"|(?:agents|signal_layer|prompts|evals|fixtures|app|tests)/[A-Za-z0-9_./-]+\.py)"
)

#: Cited paths that are legitimately absent from a clean checkout. The verbatim
#: prompts are git-ignored on purpose, and pages must be free to name them.
_CITED_PATH_EXEMPT: Final[tuple[str, ...]] = ("prompts/verbatim/",)

#: A page may cite a DELETED file when the deletion is the point being made -- the
#: paraphrasing module that was removed, for instance. That is legitimate history,
#: but a reader must not be left looking for a file that is not there, so the
#: citation only passes if the same paragraph says it is gone.
_DELETED_MARKERS: Final[tuple[str, ...]] = ("delet", "removed", "retired", "no longer", "prior revision")


def _pages() -> list[Path]:
    if not SITE_CONTENT.is_dir():
        return []
    return sorted(SITE_CONTENT.rglob("*.mdx"))


def _require_pages() -> list[Path]:
    pages = _pages()
    if not pages:
        pytest.skip("site/content/docs holds no .mdx pages in this checkout")
    return pages


_WORD: Final = re.compile(r"[a-z0-9]+")

#: Inline code spans and fenced blocks. Stripped from a PAGE before shingling.
#:
#: This is the one exemption in the leak guard and it is deliberate. What is
#: confidential is the customer's *writing* -- her instructions, her reasoning,
#: her paragraphs. What is not is the machine-readable contract her prompt
#: defines: the field names, the enum values, the JSON key order. A 1:1 port
#: cannot be documented at all without naming those, and this port's whole claim
#: is that it implements them exactly.
#:
#: In practice this exemption fired on exactly one real passage -- the page
#: comparing her prompt's `LOW RISK | MEDIUM RISK | HIGH RISK` against her PDF's
#: four-value list, which is the enum conflict being escalated to her. Every word
#: of that match was inside backticks. Prose lifted from a prompt would not be.
#:
#: Inline spans deliberately do NOT match across a blank line. Markdown allows an
#: inline span to wrap one newline, and both are stripped here, but a span that
#: appears to swallow two paragraphs is far more likely an unclosed backtick than
#: a real span -- and treating it as a span would hide everything inside it from
#: every guard in this file.
_CODE_SPAN: Final = re.compile(r"```.*?```|`[^`]*?`", re.DOTALL)


def _shingles(text: str, n: int = _LEAK_NGRAM) -> set[tuple[str, ...]]:
    """Normalised word n-grams.

    Case, punctuation and whitespace are discarded so that reflowing a paragraph
    into MDX, or changing its quoting, does not hide a copy.
    """
    words = _WORD.findall(text.lower())
    if len(words) < n:
        return set()
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def _prose_shingles(page_text: str) -> set[tuple[str, ...]]:
    """Shingles of a page's prose, with code spans removed.

    Code spans are replaced by a separator rather than deleted so that the words
    either side of one do not become adjacent and form a shingle that appears in
    neither the page nor the source.
    """
    return _shingles(_CODE_SPAN.sub("\n", page_text))


# ---------------------------------------------------------------------------
# Confidentiality
# ---------------------------------------------------------------------------


def _confidential_shingles() -> dict[tuple[str, ...], str]:
    confidential: dict[tuple[str, ...], str] = {}
    for source in sorted(VERBATIM_DIR.glob("*.txt")):
        for shingle in _shingles(source.read_text(encoding="utf-8")):
            confidential.setdefault(shingle, source.name)
    return confidential


def test_no_customer_prompt_text_is_published() -> None:
    """No page's PROSE contains a 12-word run from any confidential document.

    This is the test that makes "describe it, cite it, do not reproduce it" a
    build failure rather than an intention. Code spans are exempt -- see
    ``_CODE_SPAN`` for why the contract is not the confidential part.
    """
    if not VERBATIM_DIR.is_dir():
        pytest.skip("prompts/verbatim is not present in this checkout")
    pages = _require_pages()

    confidential = _confidential_shingles()
    assert confidential, "verbatim documents produced no shingles -- guard is inert"

    leaks: list[str] = []
    for page in pages:
        for shingle in _prose_shingles(page.read_text(encoding="utf-8")):
            if shingle in confidential:
                leaks.append(
                    f"{page.relative_to(REPO_ROOT)} reproduces "
                    f"{confidential[shingle]}: {' '.join(shingle)!r}"
                )
                break  # one report per page is enough to fail and to locate it

    assert not leaks, "confidential customer prompt text published on the docs site:\n" + "\n".join(leaks)


def test_the_code_span_exemption_does_not_swallow_prose() -> None:
    """The exemption must not become a way to publish a prompt inside a fence.

    A page could evade the leak guard by pasting a confidential paragraph into a
    code block. That is the failure mode the exemption creates, so it is asserted
    against directly: a fenced paste of real prompt PROSE must still be detected.
    The distinction is length -- an enum list is a handful of words, a paragraph is
    not -- so the guard checks fenced blocks with a larger shingle that a field
    list cannot reach but copied prose always will.
    """
    if not VERBATIM_DIR.is_dir():
        pytest.skip("prompts/verbatim is not present in this checkout")

    confidential: dict[tuple[str, ...], str] = {}
    for source in sorted(VERBATIM_DIR.glob("*.txt")):
        for shingle in _shingles(source.read_text(encoding="utf-8"), n=30):
            confidential.setdefault(shingle, source.name)

    leaks: list[str] = []
    for page in _require_pages():
        for shingle in _shingles(page.read_text(encoding="utf-8"), n=30):
            if shingle in confidential:
                leaks.append(f"{page.relative_to(REPO_ROOT)} bulk-copies {confidential[shingle]}")
                break

    assert not leaks, "confidential prose published inside code spans:\n" + "\n".join(leaks)


def test_the_leak_guard_would_catch_a_paste() -> None:
    """The guard is not vacuous: a pasted customer paragraph must trip it.

    Without this, a shingle size that silently produced no overlap would let the
    confidentiality test pass on any input.
    """
    if not VERBATIM_DIR.is_dir():
        pytest.skip("prompts/verbatim is not present in this checkout")

    source = VERBATIM_DIR / "identity_agent_prompt.txt"
    confidential = _shingles(source.read_text(encoding="utf-8"))
    pasted = " ".join(_WORD.findall(source.read_text(encoding="utf-8").lower())[:60])
    assert _shingles(pasted) & confidential, "a verbatim paste does not trip the leak guard"


# ---------------------------------------------------------------------------
# Anti-fabrication
# ---------------------------------------------------------------------------


def test_no_page_repeats_a_fabricated_figure() -> None:
    """The fabricated draft's figures may not appear as claims.

    A page IS allowed to name them while explaining the failure -- that is what
    the landing page does -- so an occurrence is only a failure when it is not
    marked as fabricated. The marker must be within the same paragraph.
    """
    pages = _require_pages()
    offences: list[str] = []
    disclaimers = ("fabricat", "not appear", "no artifact", "invented", "none of those")

    for page in pages:
        text = page.read_text(encoding="utf-8")
        for paragraph in re.split(r"\n\s*\n", text):
            lowered = paragraph.lower()
            for bad in FABRICATED_FIGURES:
                if bad.lower() in lowered and not any(d in lowered for d in disclaimers):
                    offences.append(f"{page.relative_to(REPO_ROOT)}: {bad!r} quoted without disclaimer")

    assert not offences, "fabricated figures quoted as fact:\n" + "\n".join(offences)


def test_quoted_refusal_strings_match_the_artifact() -> None:
    """A quoted ``refused: N sample(s)`` string must be one the harness really emitted.

    The pipeline artifact contains **two different** refusals: the latency blocks
    have n=42, and the cost block has n=41, because one degraded run stays in the
    latency distribution and drops out of the priced one. Both are correct in their
    own place and quoting the wrong one is a small, extremely plausible error --
    it happened on the cost table of the Snowflake page.

    It also matters more than its size: the refusal is the site's evidence that the
    harness declines to publish an unsupported percentile. Misquoting it undermines
    exactly the claim it is there to make.
    """
    pages = _require_pages()
    artifact = REPO_ROOT / "evals" / "results" / "pipeline_14x3_us-east-1.json"
    if not artifact.is_file():
        pytest.skip("pipeline artifact is not present in this checkout")

    import json

    data = json.loads(artifact.read_text(encoding="utf-8"))
    real: set[str] = set()
    for block in data.values():
        if isinstance(block, dict):
            for value in (block.get("suppressed") or {}).values():
                if isinstance(value, str):
                    real.add(value)
    assert real, "artifact records no refusal strings -- guard is inert"

    #: The n asserted inside a quoted refusal, however the page abbreviates the rest.
    quoted = re.compile(r"refused:\s*(\d+)\s*sample")
    allowed = {re.search(r"refused:\s*(\d+)", s).group(1) for s in real}

    offences: list[str] = []
    for page in pages:
        for n in set(quoted.findall(page.read_text(encoding="utf-8"))):
            if n not in allowed:
                offences.append(
                    f"{page.relative_to(REPO_ROOT)} quotes 'refused: {n} sample(s)' but the "
                    f"artifact only recorded n in {sorted(allowed)}"
                )

    assert not offences, "misquoted refusal strings:\n" + "\n".join(offences)


def test_per_cell_grounding_in_tables_matches_the_artifact_exactly() -> None:
    """A per-cell grounding median in a table must carry the artifact's own digits.

    ``_context`` grounding is the metric that decided the specialists stay on
    Claude, so its digits are load-bearing. A prose rewrite re-rounded three table
    cells from the artifact's ``0.3575`` to ``0.358``; a reader checking that
    against the JSON cannot tell whether the page rounded or the study moved.

    Scope is deliberately narrow -- **table cells only** -- and three *different*
    grounding quantities are legitimate, which is what makes this guard fiddly:

    1. per-cell medians, e.g. ``0.3575``, from ``per_set_verdict[].candidates[]``.
       Verdict tables show these and they must be verbatim.
    2. pooled per-analysis means, ``0.642`` (Claude, n=48) and ``0.357`` (GPT,
       n=72), over ``per_analysis[].grounding.grounded_fraction``.
    3. per-(agent, model) means over the same rows, e.g. ``0.214`` for
       ``bustout``/``sol``, which the per-agent matrix tabulates.

    Cases 2 and 3 are computed here rather than listed, because several of them
    look exactly like a truncation of a case-1 value and are not. ``bustout``/
    ``sol`` is the trap: its median is ``0.2145`` and its mean is ``0.2145`` too,
    which correctly renders as ``0.2145`` in the verdict table and ``0.214`` in the
    3-decimal mean matrix. Both are right, on the same page, for the same cell.

    Two earlier versions of this test were wrong in this exact way -- first
    flagging the pooled ``0.357``, then the per-agent ``0.214``. Both times the
    docs were correct and the test was not, which is worth recording: a guard on
    rounding has to model every quantity the site publishes, or it manufactures
    defects and trains a reader to ignore it.
    """
    pages = _require_pages()
    artifact = REPO_ROOT / "evals" / "results" / "set_comparison.json"
    if not artifact.is_file():
        pytest.skip("set_comparison artifact is not present in this checkout")

    import json

    data = json.loads(artifact.read_text(encoding="utf-8"))
    medians: set[str] = set()
    for row in data.get("per_set_verdict", []):
        for entry in [row.get("incumbent_scores") or {}, *(row.get("candidates") or [])]:
            value = entry.get("grounded_fraction_median")
            if isinstance(value, (int, float)):
                medians.add(f"{value}")
    assert medians, "artifact records no per-cell grounding medians -- guard is inert"

    #: Every mean the site could legitimately tabulate -- pooled by family, and per
    #: (agent, model). Computed rather than listed, because some of these collide
    #: with what looks like a truncated median and are not one.
    means: set[str] = set()
    by_set: dict[str, list[float]] = {}
    by_cell: dict[tuple[str, str], list[float]] = {}
    for row in data.get("per_analysis", []):
        fraction = (row.get("grounding") or {}).get("grounded_fraction")
        if not isinstance(fraction, (int, float)):
            continue
        by_set.setdefault(str(row.get("set")), []).append(float(fraction))
        by_cell.setdefault((str(row.get("domain")), str(row.get("model_label"))), []).append(float(fraction))
    for values in (*by_set.values(), *by_cell.values()):
        mean = sum(values) / len(values)
        means.update({f"{round(mean, places)}" for places in (2, 3, 4)})

    truncations: dict[str, str] = {}
    for exact in medians:
        for places in (1, 2, 3):
            rounded = f"{round(float(exact), places)}"
            if rounded != exact and rounded not in medians and rounded not in means:
                truncations.setdefault(rounded, exact)
    assert truncations, "no truncation is distinguishable from a real value -- guard is inert"

    offences: list[str] = []
    for page in pages:
        text = page.read_text(encoding="utf-8")
        for rounded, exact in truncations.items():
            if re.search(rf"\|\s*{re.escape(rounded)}\s*\|", text):
                offences.append(
                    f"{page.relative_to(REPO_ROOT)} tabulates grounding {rounded} "
                    f"where the artifact records {exact}"
                )

    assert not offences, "re-rounded grounding figures in tables:\n" + "\n".join(sorted(set(offences)))


def test_every_cited_artifact_path_exists() -> None:
    """A page that names its source must name a real file.

    The site's credibility rule is "every number traces to an artifact and the
    page names the file". A citation pointing at a path that does not exist is
    indistinguishable, to a reader, from one that does.
    """
    pages = _require_pages()
    missing: list[str] = []
    checked = 0

    for page in pages:
        text = page.read_text(encoding="utf-8")
        paragraphs = re.split(r"\n\s*\n", text)
        for cited in sorted(set(_CITED_PATH.findall(text))):
            if any(cited.startswith(prefix) for prefix in _CITED_PATH_EXEMPT):
                continue
            checked += 1
            if (REPO_ROOT / cited).exists():
                continue
            # Absent from the tree. Allowed only where the page says so, in the
            # same paragraph, so a reader is never sent after a deleted file.
            disclosed = any(
                cited in para and any(m in para.lower() for m in _DELETED_MARKERS)
                for para in paragraphs
            )
            if not disclosed:
                missing.append(
                    f"{page.relative_to(REPO_ROOT)} cites missing {cited} "
                    f"without saying it was removed"
                )

    assert checked, "no artifact citations found on the site -- the guard is inert"
    assert not missing, "documentation cites artifacts that do not exist:\n" + "\n".join(missing)


# ---------------------------------------------------------------------------
# Site shape
# ---------------------------------------------------------------------------


def test_the_site_has_at_least_twenty_pages() -> None:
    """The deliverable is a documentation site, not a README in a folder."""
    pages = _require_pages()
    assert len(pages) >= 20, f"only {len(pages)} pages: {[p.name for p in pages]}"


def test_the_navigation_and_the_pages_correspond_exactly() -> None:
    """Every page is in the sidebar and every sidebar entry is a real page.

    Both directions matter and they fail differently. A page missing from
    ``meta.json`` still builds and is still reachable by URL -- it is simply
    invisible, which is the worse failure because nothing reports it. A
    ``meta.json`` entry with no page is a **dead sidebar link**: Fumadocs drops it
    silently, but any page that hand-links the same path renders a link to a 404.
    That happened here: the planned page ``reference/artifact-index`` was in the
    tree and linked from the landing page, and no writer had been assigned it.
    """
    pages = _require_pages()
    meta_path = SITE_CONTENT / "meta.json"
    if not meta_path.is_file():
        pytest.skip("no root meta.json")

    import json

    listed = [
        entry
        for entry in json.loads(meta_path.read_text(encoding="utf-8")).get("pages", [])
        if not entry.startswith("---")  # separators, not pages
    ]
    actual = {str(p.relative_to(SITE_CONTENT)).removesuffix(".mdx") for p in pages}

    dead_links = [entry for entry in listed if entry not in actual]
    unlisted = sorted(actual - set(listed))

    assert not dead_links, f"meta.json points at pages that do not exist: {dead_links}"
    assert not unlisted, f"pages exist but appear nowhere in the sidebar: {unlisted}"


def test_every_internal_docs_link_resolves() -> None:
    """A cross-page link must point at a page that exists.

    Fumadocs does not validate these and ``next build`` does not either -- a bad
    ``/docs/...`` href compiles, renders, and 404s only when a reader clicks it.
    The realistic cause is a page landing at a slightly different slug than the
    one another page linked: here ``/docs/platform/strands-concurrency`` was
    linked while the file landed as ``platform/strands``.
    """
    pages = _require_pages()
    slugs = {"/docs"} | {
        "/docs/" + str(p.relative_to(SITE_CONTENT)).removesuffix(".mdx").removesuffix("/index")
        for p in pages
    }

    link = re.compile(r'href="(/docs[^"#]*)"|\]\((/docs[^)#\s]*)\)')
    broken: list[str] = []
    for page in pages:
        text = page.read_text(encoding="utf-8")
        for attr_href, md_href in link.findall(text):
            target = (attr_href or md_href).rstrip("/")
            if target not in slugs:
                broken.append(f"{page.relative_to(REPO_ROOT)} links to {target}, which is not a page")

    assert not broken, "broken internal links:\n" + "\n".join(sorted(set(broken)))


def test_every_page_declares_frontmatter_title_and_description() -> None:
    """Fumadocs derives navigation and search from frontmatter.

    A page missing ``title`` renders as its slug in the sidebar; one missing
    ``description`` gives search and the generated OG image nothing to show.
    """
    pages = _require_pages()
    bad: list[str] = []
    for page in pages:
        text = page.read_text(encoding="utf-8")
        head = text.split("---", 2)[1] if text.startswith("---") else ""
        for key in ("title:", "description:"):
            if key not in head:
                bad.append(f"{page.relative_to(REPO_ROOT)} has no frontmatter {key}")
    assert not bad, "\n".join(bad)


def test_every_page_has_parseable_yaml_frontmatter() -> None:
    """Frontmatter must be valid YAML, or the page does not build at all.

    A ``description`` that opens with a double quote is the specific trap: YAML
    reads it as a quoted scalar, hits the closing quote mid-sentence, and fails
    with ``Unexpected scalar at node end`` -- a message that names a column, not a
    cause. It happened on a page whose description quoted the objection it was
    answering. The fix is a block scalar (``>-``), and the reason this test exists
    is that ``next build`` is the only other thing that catches it, minutes later
    and with a worse error.
    """
    pages = _require_pages()
    yaml = pytest.importorskip("yaml", reason="PyYAML not installed")

    broken: list[str] = []
    for page in pages:
        text = page.read_text(encoding="utf-8")
        if not text.startswith("---"):
            broken.append(f"{page.relative_to(REPO_ROOT)} has no frontmatter block")
            continue
        head = text.split("---", 2)[1]
        try:
            parsed = yaml.safe_load(head)
        except yaml.YAMLError as exc:
            first = str(exc).splitlines()[0]
            broken.append(f"{page.relative_to(REPO_ROOT)} frontmatter is not valid YAML: {first}")
            continue
        if not isinstance(parsed, dict):
            broken.append(f"{page.relative_to(REPO_ROOT)} frontmatter is not a mapping")

    assert not broken, "\n".join(broken)


#: A brace-wrapped bare identifier or dotted path: `{region}`, `{app.id}`. In MDX
#: this is not text, it is a JavaScript expression, and an undefined name fails
#: the build. Braces holding JSX-ish content (`{['a','b']}`, `{true}`, `{/* */}`)
#: are legitimate and excluded by requiring an identifier and nothing else.
_MDX_EXPRESSION: Final = re.compile(r"\{[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*\}", re.IGNORECASE)


def test_no_prose_contains_an_unescaped_mdx_expression() -> None:
    """A placeholder like ``{region}`` in prose is a JS expression to MDX.

    Symptom: ``next build`` prerenders 20 pages, then dies with
    ``ReferenceError: region is not defined`` and a stack inside a minified chunk.
    Nothing names the page or the line. It happened here on a blockquote that
    reproduced a runtime error message containing ``{region}`` -- the same string
    appears three times on the same page inside backticks, where it is perfectly
    safe, which is what makes the failure confusing.

    Inside code spans, braces are text. Outside them, they are code.
    """
    pages = _require_pages()
    offences: list[str] = []

    for page in pages:
        prose = _CODE_SPAN.sub("\n", page.read_text(encoding="utf-8"))
        # JSX attributes are legitimately braced: items={['a']}, type={x}.
        prose = re.sub(r"<[^>]*>", "", prose, flags=re.DOTALL)
        for found in sorted(set(_MDX_EXPRESSION.findall(prose))):
            offences.append(
                f"{page.relative_to(REPO_ROOT)} has unescaped {found} in prose "
                f"-- wrap it in backticks or a fenced block"
            )

    assert not offences, "MDX will evaluate these as JavaScript:\n" + "\n".join(offences)


def test_no_page_claims_a_percentile_without_a_sample_count() -> None:
    """``p95`` with no ``n`` anywhere on the page is the laundering the harness refuses.

    ``evals/bench.py`` will not print a p95 below 20 samples or a p99 below 100 --
    it emits a refusal string instead. A page that quotes a percentile has to
    carry the count that makes it meaningful.

    The check is per PAGE, not per paragraph, and that is deliberate. These pages
    are mostly tables, and a table states its n once -- in an ``n`` column, in the
    caption, or in the sentence introducing it -- while every row then quotes a
    percentile. Demanding an n beside each occurrence flagged fourteen passages
    that were all correctly sourced, which would have trained a reader to ignore
    the guard. What it still catches is the real failure: a page that republishes
    a percentile with no sample count anywhere in it.
    """
    pages = _require_pages()
    #: A percentile is only "published" when a VALUE sits next to it. A page that
    #: says "nothing measured says what happens to p95 under concurrency" is
    #: naming an open question, not quoting a figure, and demanding an n from it
    #: would be demanding the sample size of a measurement that was never taken.
    published = re.compile(
        r"\bp(?:50|95|99)\b[^|\n]{0,24}?\d"      # p50 31.45 s
        r"|\d[^|\n]{0,24}?\bp(?:50|95|99)\b"     # 31.45 s at p50
        r"|\|\s*p(?:50|95|99)\s*\|",             # a markdown percentile column
        re.IGNORECASE,
    )
    count = re.compile(
        r"\bn\s*=\s*\d+"                                        # n=42
        r"|\|\s*n\s*(?:ok|failed)?\s*\|"                        # a markdown `n` column
        r"|\b\d[\d,]*\s*(?:runs|samples|calls|analyses|rows|reps|pairs|verdicts|fixtures|applications)\b",
        re.IGNORECASE,
    )

    offences: list[str] = []
    for page in pages:
        text = page.read_text(encoding="utf-8")
        if published.search(text) and not count.search(text):
            offences.append(f"{page.relative_to(REPO_ROOT)} publishes a percentile and states no n anywhere")

    assert not offences, "percentiles published without their n:\n" + "\n".join(offences)
