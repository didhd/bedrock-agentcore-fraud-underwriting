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
_CODE_SPAN: Final = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)


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
        for cited in sorted(set(_CITED_PATH.findall(page.read_text(encoding="utf-8")))):
            if any(cited.startswith(prefix) for prefix in _CITED_PATH_EXEMPT):
                continue
            checked += 1
            if not (REPO_ROOT / cited).exists():
                missing.append(f"{page.relative_to(REPO_ROOT)} cites missing {cited}")

    assert checked, "no artifact citations found on the site -- the guard is inert"
    assert not missing, "documentation cites artifacts that do not exist:\n" + "\n".join(missing)


# ---------------------------------------------------------------------------
# Site shape
# ---------------------------------------------------------------------------


def test_the_site_has_at_least_twenty_pages() -> None:
    """The deliverable is a documentation site, not a README in a folder."""
    pages = _require_pages()
    assert len(pages) >= 20, f"only {len(pages)} pages: {[p.name for p in pages]}"


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
    percentile = re.compile(r"\bp(?:50|95|99)\b")
    count = re.compile(
        r"\bn\s*=\s*\d+"                                        # n=42
        r"|\|\s*n\s*(?:ok|failed)?\s*\|"                        # a markdown `n` column
        r"|\b\d[\d,]*\s*(?:runs|samples|calls|analyses|rows|reps|pairs|verdicts|fixtures|applications)\b",
        re.IGNORECASE,
    )

    offences: list[str] = []
    for page in pages:
        text = page.read_text(encoding="utf-8")
        if percentile.search(text) and not count.search(text):
            offences.append(f"{page.relative_to(REPO_ROOT)} quotes a percentile and states no n anywhere")

    assert not offences, "percentiles published without their n:\n" + "\n".join(offences)
