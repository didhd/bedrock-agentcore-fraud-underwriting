"""No customer file may be committable, from anywhere in the tree.

WHY THIS EXISTS AS A TEST AND NOT A .gitignore RULE

`.gitignore` had `prompts/verbatim/`, which is correct and was not enough. Two build steps copy
that tree elsewhere:

  * `deploy/vendor.sh` copies it beside each runtime's `main.py`
  * `agentcore deploy` stages another copy under `agentcore/<agent>/staging/`

The second one was not ignored. A `git add -A` after a deploy staged **160** copies of the
customer's nine agent prompts, their semantic model and their confidential analyst queries into
a commit. It was caught before any push, by reading the output of the command that made it --
which is exactly the wrong place to catch it, because it depended on someone looking.

A per-directory rule loses this race by construction: the next build step that copies the tree
needs a new rule, and nobody knows to add one until it has already happened. So the invariant is
asserted by CONTENT and by FILENAME, wherever the file is:

    no file whose basename matches a known customer artefact may be stageable

`.gitignore` is still the mechanism. This is the check that the mechanism is complete.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
VERBATIM = REPO / "prompts" / "verbatim"


def _tracked_or_untracked() -> list[str]:
    """Everything git would include in `git add -A`, plus everything already tracked."""
    staged = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        cwd=REPO,
    ).stdout.splitlines()
    tracked = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, cwd=REPO
    ).stdout.splitlines()
    return [line[3:].strip().strip('"') for line in staged] + tracked


def _customer_basenames() -> set[str]:
    """The names of the delivered files, taken from the delivered directory itself.

    Read from disk rather than listed here, so a future delivery is covered without anyone
    remembering to update this test — and so this test does not become the place a customer
    filename gets committed.
    """
    if not VERBATIM.is_dir():
        return set()
    return {p.name for p in VERBATIM.iterdir() if p.is_file()}


@pytest.mark.skipif(
    not VERBATIM.is_dir(),
    reason="the delivered files are absent, so there is nothing to check against",
)
def test_no_customer_file_is_committable_from_anywhere() -> None:
    names = _customer_basenames()
    assert names, "prompts/verbatim/ exists but is empty; nothing was checked"

    offenders = [
        path
        for path in _tracked_or_untracked()
        if path and pathlib.PurePosixPath(path).name in names
    ]

    assert not offenders, (
        f"{len(offenders)} copies of customer files are committable. Add the directory to "
        ".gitignore; do NOT delete the files, they are needed at build time.\n  "
        + "\n  ".join(sorted(offenders)[:12])
    )


@pytest.mark.skipif(
    not VERBATIM.is_dir(), reason="the delivered files are absent"
)
def test_the_known_copy_locations_are_all_ignored() -> None:
    """Every directory that a build step copies the tree into must be ignored.

    Checked explicitly as well as by name, because an empty copy directory would pass the test
    above while still being a rule that is missing — the copy appears only after the build step
    runs, which may be on someone else's machine.
    """
    patterns = [
        "prompts/verbatim",
        "app/underwriter/prompts/verbatim",
        "app/specialist/prompts/verbatim",
        "app/francis/prompts/verbatim",
        "agentcore/identity/staging/prompts/verbatim",
    ]
    sample = sorted(_customer_basenames())[0]
    unignored = []
    for pattern in patterns:
        candidate = f"{pattern}/{sample}"
        result = subprocess.run(
            ["git", "check-ignore", "-q", candidate], cwd=REPO
        )
        if result.returncode != 0:
            unignored.append(candidate)

    assert not unignored, (
        "these paths are NOT gitignored, and a build step puts customer files there:\n  "
        + "\n  ".join(unignored)
    )


def test_the_generated_lambda_payload_is_ignored() -> None:
    """`use_cases.json` carries the customer's verified SQL and is generated, not committed."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", "deploy/cdk/lambda-rds/use_cases.json"], cwd=REPO
    )
    assert result.returncode == 0, (
        "deploy/cdk/lambda-rds/use_cases.json is not gitignored. bundle.sh generates it from "
        "the semantic model and it contains the customer's verified SQL verbatim."
    )


def test_the_connection_state_files_are_ignored() -> None:
    """They describe the customer's network. Not credentials, but not ours to publish."""
    for path in ("deploy/.vpc-config.json", "deploy/.rds-connection.json"):
        result = subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO)
        assert result.returncode == 0, f"{path} is not gitignored"
