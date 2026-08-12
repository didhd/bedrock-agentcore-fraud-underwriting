"""
DEPRECATED SHIM. The runtime entrypoints now live under ``app/``.

    app/underwriter/main.py   batch underwriting: 8-way fan-out + master synthesis
    app/francis/main.py       interactive: Francis routes to at most 2 specialists

Those two paths are what ``agentcore/agentcore.json`` declares (``codeLocation``
``app/underwriter/`` and ``app/francis/``, ``entrypoint`` ``main.py`` in each), and
they are what the CLI runs. This file exists only so an existing reference to
``agentcore_runtime.py`` keeps working; it declares no runtime of its own.

WHY THE OLD ENTRYPOINT WAS REPLACED RATHER THAN EDITED

It was ``def invoke(payload: dict) -> dict``: synchronous, non-streaming, and
without a ``context`` parameter. Each of those is a distinct defect.

  * No ``context``. ``BedrockAgentCoreApp._takes_context()`` injects the
    ``RequestContext`` only when the handler's **second positional parameter is
    literally named** ``context``. A one-parameter handler never receives it, so
    the runtime could not see its own ``session_id``.
  * Synchronous. ``_invoke_handler`` dispatches a sync handler through
    ``run_in_threadpool``, so an eight-way fan-out held a Starlette worker for the
    whole run against the **non-adjustable 15-minute** synchronous invocation
    timeout. The streaming ceiling is 60 minutes.
  * Non-streaming. Nothing could be shown until all eight specialists and the
    synthesis had finished, so the live UI was wired to a local FastAPI generator
    rather than to the runtime it is supposed to demonstrate.

WHY THE COMMANDS IN THE OLD DOCSTRING WERE WRONG

It documented ``agentcore configure --entrypoint ...`` and ``agentcore launch``.
Neither subcommand exists in the real CLI (npm ``@aws/agentcore``), which prints
``error: unknown command 'configure'`` and **exits 1** -- the safe failure.

An earlier version of this docstring said the npm CLI prints help and exits 0.
That was the DEPRECATED ``bedrock-agentcore-starter-toolkit``'s behaviour
attributed to the wrong program. Its console script is also named ``agentcore``
and shadows the real one, and on that binary the subcommands parse and then do
approximately nothing -- so whichever resolves first on ``PATH`` decides whether
a script fails loudly or deploys nothing while reporting success. The real
commands are:

    agentcore create / add / validate / package / deploy / dev / invoke
    agentcore logs / traces / status / run

Local development, and the invocation shapes each runtime accepts:

    # batch underwriting
    agentcore dev                       # run from app/underwriter/
    agentcore invoke --dev '{"application_id": "APP-1004"}'
    agentcore invoke --dev "Run a full underwriting review of APP-1004"

    # interactive
    agentcore dev                       # run from app/francis/
    agentcore invoke --dev "Is there income fraud on APP-1003?"

Both the deployed runtime and ``agentcore dev`` always answer
``text/event-stream``, even for a one-line prompt, so a client must read frames
rather than expect a JSON body -- and must check EVERY frame for an ``error`` key,
because an exception raised mid-stream is delivered as a terminal data frame
*after* HTTP 200 has already been sent.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

#: Repo-relative paths of the real entrypoints, for anything that reports them.
UNDERWRITER_ENTRYPOINT = "app/underwriter/main.py"
FRANCIS_ENTRYPOINT = "app/francis/main.py"

warnings.warn(
    "agentcore_runtime.py is a deprecated shim. The batch runtime is "
    f"{UNDERWRITER_ENTRYPOINT} and the interactive runtime is {FRANCIS_ENTRYPOINT}; "
    "agentcore/agentcore.json points at those two directories.",
    DeprecationWarning,
    stacklevel=2,
)


def _load_underwriter():
    """Import ``app/underwriter/main.py`` as a module.

    Loaded by file path rather than as ``app.underwriter.main`` because the CLI
    runs that file as a *top-level* module named ``main`` (``uvicorn main:app``
    with cwd set to the agent directory), and the file's own bootstrap arranges
    sys.path for that layout. Going through importlib keeps this shim honest about
    which file is authoritative instead of duplicating any of its logic.
    """
    import importlib.util

    path = _REPO_ROOT / UNDERWRITER_ENTRYPOINT
    spec = importlib.util.spec_from_file_location("pp_underwriter_main", path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"cannot load the underwriting runtime from {path}")
    module = importlib.util.module_from_spec(spec)
    # Registered before exec so the module is importable by name during its own
    # execution, matching normal import semantics.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


#: The batch runtime's ``BedrockAgentCoreApp``. Delegating rather than declaring a
#: second app matters: ``BedrockAgentCoreApp`` supports exactly ONE entrypoint
#: (``self.handlers["main"]`` is overwritten, not appended), so a shim that
#: registered its own handler would silently replace the real one in any process
#: that imported both.
_underwriter = _load_underwriter()
app = _underwriter.app

#: The real async-generator entrypoint, re-exported unchanged. Its signature is
#: ``(payload, context)`` and it is an async generator; both properties are part of
#: the contract and are asserted by tests/test_runtime_contract.py.
invoke = _underwriter.invoke

__all__ = ["FRANCIS_ENTRYPOINT", "UNDERWRITER_ENTRYPOINT", "app", "invoke"]


if __name__ == "__main__":
    print(
        "agentcore_runtime.py is a deprecated shim.\n"
        f"  batch underwriting : {UNDERWRITER_ENTRYPOINT}\n"
        f"  interactive        : {FRANCIS_ENTRYPOINT}\n"
        "Run a runtime locally with `agentcore dev` from its directory, or:\n"
        f"  python {UNDERWRITER_ENTRYPOINT}\n"
        "Note: `agentcore configure` and `agentcore launch` do not exist in the real CLI.",
        file=sys.stderr,
    )
    raise SystemExit(2)
