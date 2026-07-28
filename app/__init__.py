"""
The two AgentCore Runtime applications.

``app/underwriter/`` is the batch path (one application in, one 21-key
adjudication out) and ``app/francis/`` is the interactive path (an analyst
question in, markdown out). Each is a *separate deployed runtime*: see the two
entries in ``agentcore/agentcore.json``, whose ``codeLocation`` fields point at
these two directories and whose ``entrypoint`` is ``main.py`` in each.

WHY THIS PACKAGE EXISTS AT ALL, given that neither runtime imports it:

The AgentCore CLI (npm ``@aws/agentcore`` 1.0.0-preview.22) treats the agent
directory as the **import root**, not the repo root. Verified two ways:

  * ``agentcore dev`` spawns ``.venv/bin/uvicorn main:app --reload`` with
    ``cwd`` set to ``codeLocation`` (CodeZipDevServer.getSpawnConfig), so
    ``main`` resolves as a top-level module and ``sys.path[0]`` is
    ``app/<runtime>/``.
  * ``agentcore package`` copies ``srcDir`` -- the directory containing the
    nearest ``pyproject.toml`` walking up from cwd -- into a staging dir
    alongside the pip-installed dependencies and zips *that* directory's
    contents at the archive root. There is no repo-root prefix inside the zip.

Consequently ``main.py`` in each runtime resolves its repo-root imports
(``prompts``, ``signal_layer``, ``agents``, ``fixtures``) through an explicit
``sys.path`` insert of the repo root, and each runtime's ``pyproject.toml``
declares its own dependency closure. This file therefore holds no importable
code, only the invariant above -- ``app/`` is a directory the CLI reads, not a
package either runtime imports.

NAME COLLISION, deliberate and safe: the repo root also contains a module-level
``app.py`` (the local CLI runner). A regular package wins over a same-named
module on ``sys.path`` (verified: with both ``app/__init__.py`` and ``app.py``
present, ``import app`` resolves to the package), so creating this package makes
``import app`` stop resolving to ``app.py``. Nothing imports ``app.py`` as a
module -- every reference in README.md and deploy/deploy.md invokes it as a
script (``python app.py --review APP-1004``), which reads the file by path and
never consults ``sys.path``. Grep before adding an ``import app`` anywhere.
"""

from __future__ import annotations

__all__: list[str] = []
