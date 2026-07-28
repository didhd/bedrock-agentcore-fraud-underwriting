"""
Regenerate ``fixtures/applications/*.json``. Run as ``python3 -m fixtures.regenerate``.

Separate from ``_build`` so the scenario modules can be imported by tests without a side effect,
and separate from ``__init__`` so importing the fixture package never writes to the tree.
"""

from __future__ import annotations

import sys

from fixtures._build import write_all
from fixtures._scenarios_extra import EXTRA_SCENARIOS
from fixtures._scenarios_fp import FP_SCENARIOS
from fixtures._scenarios_legacy import LEGACY_SCENARIOS

#: Authoring order: the five ids the UI and README already reference, then the anti-false-positive
#: records, then the band-coverage records. File names carry the id, so order affects nothing but
#: the console output.
ALL_SCENARIOS = (*LEGACY_SCENARIOS, *FP_SCENARIOS, *EXTRA_SCENARIOS)


def main() -> int:
    ids = [s.app_id for s in ALL_SCENARIOS]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        print(f"duplicate application ids: {sorted(duplicates)}", file=sys.stderr)
        return 1
    for path in write_all(list(ALL_SCENARIOS)):
        print(f"wrote {path.relative_to(path.parent.parent.parent)}")
    print(f"{len(ALL_SCENARIOS)} applications")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
