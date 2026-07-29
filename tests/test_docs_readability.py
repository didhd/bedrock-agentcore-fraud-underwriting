"""
Readability gate on the documentation site.

WHY THIS EXISTS SEPARATELY FROM ``test_docs_site.py``

That module asks "is this true and is it safe to publish". This one asks "can a
human actually read it". Both matter and they fail for different reasons, so they
are separate files.

Every threshold here comes from a real review of the first complete draft, which
was content-complete and read badly. What the reviewer said, and what a count of
the tree confirmed:

  * **17 of 42 page titles began with "The"** -- "The tail", "The critical path",
    "The tier re-derivation". A sidebar of thirty such entries is unreadable
    because the first word carries no information in any of them.
  * **235 callouts, 156 of them ``warn`` or ``error``** -- 4.2 alarms per page.
    When every third paragraph is a warning, the reader stops seeing warnings, so
    the genuinely dangerous ones (a CLI command that deploys nothing) lose the
    emphasis they need.
  * **All 37 pages opened with an identical ``title="Audience"`` info callout**,
    pushing the actual content below the fold on every single page.
  * **One diagram in the entire site**, an ASCII box drawing. Architecture,
    request lifecycle and decision flow were all prose.
  * **No comparison table anywhere** -- not Claude vs GPT, not before vs after.
    A reader had to diff two paragraphs to see a trade-off.

These are style rules, so they are deliberately loose: they catch the *pattern*
returning at scale, not a single awkward sentence. Each one names the count that
justified it so a future reader can argue with the threshold rather than guess at
it.

Run:  python -m pytest tests/test_docs_readability.py -q
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

#: Callouts per page. Three is enough for a genuine aside, a genuine caveat and a
#: genuine danger; beyond that they stop being asides and become the page.
MAX_CALLOUTS_PER_PAGE: Final[int] = 4

#: Of those, how many may raise an alarm. One per page keeps "warn" meaningful.
#: The draft averaged 4.2.
MAX_ALARM_CALLOUTS_PER_PAGE: Final[int] = 2

#: Pages whose subject is a structure, a flow or a choice must show it. Listed by
#: path so the requirement is explicit rather than inferred from the prose.
PAGES_REQUIRING_A_DIAGRAM: Final[tuple[str, ...]] = (
    "architecture",
    "what-was-built",
)

_CALLOUT: Final = re.compile(r"<Callout\b", re.IGNORECASE)
_ALARM_CALLOUT: Final = re.compile(r'<Callout[^>]*type=(["\'])(warn|error)\1', re.IGNORECASE | re.DOTALL)
_AUDIENCE_CALLOUT: Final = re.compile(r'<Callout[^>]*title=(["\'])Audience\1', re.IGNORECASE | re.DOTALL)
_MERMAID: Final = re.compile(r"<Mermaid\b")
_TABLE_ROW: Final = re.compile(r"^\|.+\|\s*$", re.MULTILINE)


def _pages() -> list[Path]:
    if not SITE_CONTENT.is_dir():
        return []
    return sorted(SITE_CONTENT.rglob("*.mdx"))


def _require_pages() -> list[Path]:
    pages = _pages()
    if not pages:
        pytest.skip("site/content/docs holds no .mdx pages in this checkout")
    return pages


def _title_of(page: Path) -> str:
    text = page.read_text(encoding="utf-8")
    match = re.search(r"^title:\s*(.+)$", text, re.MULTILINE)
    return (match.group(1).strip().strip("\"'") if match else "")


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------


def test_no_page_title_starts_with_the() -> None:
    """A sidebar of "The ..." entries is a sidebar with no scannable first word.

    17 of 42 titles opened with "The" in the first draft. A title's job in a
    navigation tree is to name its subject at the front, where the eye lands.
    """
    offenders = [
        f"{page.relative_to(REPO_ROOT)}: {_title_of(page)!r}"
        for page in _require_pages()
        if _title_of(page).lower().startswith("the ")
    ]
    assert not offenders, "page titles beginning with 'The':\n" + "\n".join(offenders)


def test_titles_are_not_sentences() -> None:
    """Titles are labels. A title ending in a full stop is a sentence in a menu."""
    offenders = [
        f"{page.relative_to(REPO_ROOT)}: {_title_of(page)!r}"
        for page in _require_pages()
        if _title_of(page).endswith(".")
    ]
    assert not offenders, "titles punctuated as sentences:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# Callout discipline
# ---------------------------------------------------------------------------


def test_no_page_opens_with_an_audience_callout() -> None:
    """Every page opening with the same boilerplate box wastes every first screen.

    All 37 pages did this. The audience belongs in the description or in one line
    under the heading -- not in a bordered box above the content.
    """
    offenders = [
        str(page.relative_to(REPO_ROOT))
        for page in _require_pages()
        if _AUDIENCE_CALLOUT.search(page.read_text(encoding="utf-8"))
    ]
    assert not offenders, "'Audience' callouts still present on:\n" + "\n".join(offenders)


def test_callouts_stay_within_budget() -> None:
    """Past a handful, callouts stop being emphasis and become the layout.

    The draft ran 235 callouts over 37 pages. A callout should mark the one thing
    on a page a reader must not miss.
    """
    offenders: list[str] = []
    for page in _require_pages():
        count = len(_CALLOUT.findall(page.read_text(encoding="utf-8")))
        if count > MAX_CALLOUTS_PER_PAGE:
            offenders.append(f"{page.relative_to(REPO_ROOT)}: {count} callouts (budget {MAX_CALLOUTS_PER_PAGE})")
    assert not offenders, "pages over the callout budget:\n" + "\n".join(offenders)


def test_alarm_callouts_stay_rare() -> None:
    """If everything is a warning, nothing is.

    156 of 235 callouts were ``warn`` or ``error``. This repo has real hazards --
    a CLI verb that deploys nothing, a cost term whose omission under-reports a
    call by 62% -- and they need to stand out from ordinary caveats.
    """
    offenders: list[str] = []
    for page in _require_pages():
        count = len(_ALARM_CALLOUT.findall(page.read_text(encoding="utf-8")))
        if count > MAX_ALARM_CALLOUTS_PER_PAGE:
            offenders.append(
                f"{page.relative_to(REPO_ROOT)}: {count} warn/error callouts "
                f"(budget {MAX_ALARM_CALLOUTS_PER_PAGE})"
            )
    assert not offenders, "pages crying wolf:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# Showing rather than telling
# ---------------------------------------------------------------------------


def test_the_site_has_an_architecture_page_with_a_diagram() -> None:
    """A reader must be able to see the system before reading about it.

    The first draft had no architecture overview at all, and one ASCII drawing in
    the whole site.
    """
    pages = _require_pages()
    by_stem = {str(p.relative_to(SITE_CONTENT)).removesuffix(".mdx"): p for p in pages}

    architecture = by_stem.get("architecture")
    assert architecture is not None, (
        "no site/content/docs/architecture.mdx -- the site has no overview page"
    )
    text = architecture.read_text(encoding="utf-8")
    assert _MERMAID.search(text), "architecture.mdx contains no <Mermaid> diagram"


def test_structural_pages_carry_a_diagram() -> None:
    """Pages whose subject is a structure must draw it, not describe it."""
    pages = {str(p.relative_to(SITE_CONTENT)).removesuffix(".mdx"): p for p in _require_pages()}
    missing: list[str] = []
    for stem in PAGES_REQUIRING_A_DIAGRAM:
        page = pages.get(stem)
        if page is None:
            continue  # covered by the architecture test; not this test's business
        if not _MERMAID.search(page.read_text(encoding="utf-8")):
            missing.append(f"{page.relative_to(REPO_ROOT)} has no <Mermaid> diagram")
    assert not missing, "\n".join(missing)


def test_the_site_uses_diagrams_and_tables_at_scale() -> None:
    """Site-wide floors, not per-page rules.

    A trade-off shown as two paragraphs is a trade-off the reader has to diff by
    hand. The first draft contained zero comparison tables and one diagram; these
    floors are set well below what a well-illustrated site of this size carries, so
    they fail only if the practice is abandoned rather than merely uneven.
    """
    pages = _require_pages()
    diagrams = sum(len(_MERMAID.findall(p.read_text(encoding="utf-8"))) for p in pages)
    #: A table with 3+ columns is comparing things; a 2-column table is a glossary.
    wide_tables = 0
    for page in pages:
        for row in _TABLE_ROW.findall(page.read_text(encoding="utf-8")):
            if row.count("|") >= 4 and set(row) & set("-:") and "---" in row:
                wide_tables += 1

    assert diagrams >= 8, f"only {diagrams} <Mermaid> diagrams across {len(pages)} pages"
    assert wide_tables >= 20, f"only {wide_tables} multi-column comparison tables across {len(pages)} pages"


# ---------------------------------------------------------------------------
# Sequencing
# ---------------------------------------------------------------------------


def test_pages_do_not_open_with_a_component_block() -> None:
    """A page's first content should be a sentence that answers its title.

    Opening on a bordered component -- a Callout, a Cards grid, a table -- makes
    every page start with furniture instead of an answer. This checks the first
    non-blank line after the frontmatter.
    """
    offenders: list[str] = []
    for page in _require_pages():
        text = page.read_text(encoding="utf-8")
        body = text.split("---", 2)[2] if text.startswith("---") else text
        first = next((line.strip() for line in body.splitlines() if line.strip()), "")
        if first.startswith(("<Callout", "<Cards", "<Tabs", "|")):
            offenders.append(f"{page.relative_to(REPO_ROOT)} opens with {first[:60]!r}")
    assert not offenders, "pages that open with furniture instead of an answer:\n" + "\n".join(offenders)
