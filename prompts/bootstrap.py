"""
Restore ``prompts/verbatim/`` from the customer's documents, and rewrite the manifest.

The verbatim copies are customer-confidential and therefore git-ignored (see
``prompts/README.md``), so a fresh clone has the loader and the tests but not the text.
This module rebuilds the package copy from ``docs/`` and regenerates
``prompts/manifest.json`` in one step.

It copies bytes with ``shutil.copyfile`` and never transcribes: a paraphrase introduced
here would defeat the whole package. ``tests/test_prompt_fidelity.py`` diffs the result
against ``docs/`` independently of the manifest, so regenerating hashes over an edited
file still fails CI.

Usage::

    python3 -c "from prompts.bootstrap import sync; sync()"
    python3 -m prompts.bootstrap --check     # report status without writing
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERBATIM_DIR = Path(__file__).resolve().parent / "verbatim"
MANIFEST_PATH = Path(__file__).resolve().parent / "manifest.json"

#: Written into a regenerated manifest when the existing one carries no comment. The
#: manifest schema is {"_comment": str, "files": {name: {source, bytes, sha256}}} and
#: ``loader.py`` reads the "files" mapping; writing a flat {name: sha} breaks it.
MANIFEST_COMMENT = (
    "sha256 of every byte-for-byte copy of a Point Predictive production prompt. "
    "prompts/loader.py refuses to load a file whose hash drifted; "
    "tests/test_prompt_fidelity.py additionally diffs each copy against the 'source' "
    "path so a customer prompt update fails CI instead of diverging silently. "
    "Regenerate only when the customer's own document changes."
)

#: packaged filename -> repo-relative source. Mirrors ``loader.SOURCES``; kept here too so
#: bootstrap works when ``loader`` cannot import (which is the situation it exists for).
SOURCES: dict[str, str] = {
    **{
        f"{d}_agent_prompt.txt": f"docs/{d}_agent_prompt.txt"
        for d in (
            "identity",
            "dealer",
            "straw",
            "employment",
            "income",
            "synthetic",
            "bustout",
            "rings",
            "master",
        )
    },
    "master_agent_orchestration_062026.txt": "docs/.converted/master_agent_orchestration_062026.txt",
    "identity_orchestration_062026.txt": "docs/.converted/identity_orchestration_062026.txt",
    "dealer_orchestration_instructions_062026.txt": "docs/.converted/dealer_orchestration_instructions_062026.txt",
    "straw_orchestration_instructions_062026.txt": "docs/.converted/straw_orchestration_instructions_062026.txt",
    "employment_orchestration_062026.txt": "docs/.converted/employment_orchestration_062026.txt",
    "income_orchestration_instructions_062026.txt": "docs/.converted/income_orchestration_instructions_062026.txt",
    "synthetic_orchestration_instructions_062026.txt": "docs/.converted/synthetic_orchestration_instructions_062026.txt",
    "bustout_fraudbot_orchestration_062026.txt": "docs/.converted/bustout_fraudbot_orchestration_062026.txt",
    "rings_orchestration_instructions_062026.txt": "docs/.converted/rings_orchestration_instructions_062026.txt",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def status() -> dict[str, list[str]]:
    """Which packaged files are present, missing, or differ from their source."""
    present, missing_source, missing_copy, differs = [], [], [], []
    for name, rel in sorted(SOURCES.items()):
        source = REPO_ROOT / rel
        target = VERBATIM_DIR / name
        if not source.is_file():
            missing_source.append(rel)
            continue
        if not target.is_file():
            missing_copy.append(name)
            continue
        (present if _sha256(source) == _sha256(target) else differs).append(name)
    return {
        "present": present,
        "differs": differs,
        "missing_copy": missing_copy,
        "missing_source": missing_source,
    }


def sync(*, write_manifest: bool = True) -> dict[str, list[str]]:
    """Copy every available source into ``verbatim/`` and rewrite the manifest.

    Returns the same report as :func:`status`, evaluated after copying. A missing source
    is reported rather than raised: a partial restore plus an explicit list of what is
    still needed is more useful than a traceback.
    """
    VERBATIM_DIR.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name, rel in sorted(SOURCES.items()):
        source = REPO_ROOT / rel
        if not source.is_file():
            continue
        shutil.copyfile(source, VERBATIM_DIR / name)
        copied.append(name)

    if write_manifest and copied:
        existing = {}
        if MANIFEST_PATH.is_file():
            try:
                existing = json.loads(MANIFEST_PATH.read_text())
            except json.JSONDecodeError:
                existing = {}
        manifest = {
            "_comment": existing.get("_comment", MANIFEST_COMMENT),
            "files": {
                name: {
                    "source": SOURCES[name],
                    "bytes": (VERBATIM_DIR / name).stat().st_size,
                    "sha256": _sha256(VERBATIM_DIR / name),
                }
                for name in copied
            },
        }
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    report = status()
    print(f"restored {len(copied)}/{len(SOURCES)} files into {VERBATIM_DIR}")
    if report["missing_source"]:
        print(
            "MISSING from docs/ (obtain from the customer, then re-run):\n  "
            + "\n  ".join(report["missing_source"])
        )
        if any(s.startswith("docs/.converted/") for s in report["missing_source"]):
            print(
                "\nThe .converted/*.txt files are produced from the customer's .rtf docs:\n"
                '  mkdir -p docs/.converted\n'
                '  for f in docs/*_orchestration*.rtf; do\n'
                '    textutil -convert txt "$f" -output "docs/.converted/$(basename "${f%.rtf}").txt"\n'
                "  done"
            )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report status without copying anything; exit 1 if anything is missing",
    )
    args = parser.parse_args(argv)

    report = status() if args.check else sync()
    for key in ("present", "differs", "missing_copy", "missing_source"):
        if report[key]:
            print(f"{key}: {len(report[key])}")
    incomplete = report["differs"] or report["missing_copy"] or report["missing_source"]
    return 1 if (args.check and incomplete) else 0


if __name__ == "__main__":
    sys.exit(main())
