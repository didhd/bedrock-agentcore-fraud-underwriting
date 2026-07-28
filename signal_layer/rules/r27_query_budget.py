"""
General Rule 27 — five SQL calls per request, and incremental output past five applications.

Two budgets in one sentence, and they are unrelated to each other:

  (a) **A hard resource ceiling: 5 SQL calls per request.** This is the customer's latency
      guardrail, and it is why it must be mechanical rather than a prompt line. A model that
      is *asked* to stay under five calls will occasionally make eight, and eight Cortex
      Analyst round-trips is the difference between a sub-minute answer and the >48h
      turnaround that is driving this migration. A counter that refuses the sixth call
      cannot be talked out of it.

      The ceiling also constrains every other rule here: General Rule 18's mandatory
      TBL_AUTO_CONSORTIUM fallback and General Rule 13's join-to-a-record_from-table each
      spend from the same five.

  (b) **An output discipline past 5 applications:** summarise incrementally rather than
      accumulating everything before responding. On AgentCore this maps to the streaming
      entrypoint — the first finding should be on the wire before the last application is
      analysed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Iterable, Iterator, Sequence, TypeVar

from signal_layer.rules._source import rule_text

RULE_ID: Final = "27"
RULE_TEXT: Final = rule_text(RULE_ID)

#: "Limit to 5 SQL calls per request."
MAX_SQL_CALLS_PER_REQUEST: Final = 5

#: "For batch analyses of more than 5 applications, summarize findings incrementally".
#: Strictly MORE than 5, so 5 applications may be buffered and 6 may not.
INCREMENTAL_SUMMARY_THRESHOLD: Final = 5

BUDGET_INTERACTION_NOTE: Final = (
    "The five-call ceiling is shared, not per-rule. General Rule 18's mandatory "
    "TBL_AUTO_CONSORTIUM retry after an empty all-records result, and General Rule 13's "
    "join to a record_from-bearing table when the primary table lacks the column, both "
    "spend from the same budget of five. A request that needs a retry plus a record_from "
    "join has three calls left, not five."
)


class QueryBudgetExceeded(RuntimeError):
    """
    The sixth SQL call of a request was refused (General Rule 27).

    A RuntimeError rather than a ValueError: nothing about the call itself is invalid, the
    request has simply spent its budget. Callers should surface a partial answer with a
    caveat, not retry.
    """


@dataclass
class QueryBudget:
    """
    A per-request SQL-call counter that refuses the sixth call (General Rule 27).

    One instance per request, never shared across requests or reused. ``spend()`` is called
    immediately BEFORE issuing a query, so a call that raises has not been issued; counting
    after the fact would let the sixth query run and then complain about it.

    ``labels`` records what each call was for, which turns "budget exceeded" from an opaque
    failure into a diagnosable one — usually it shows a retry loop, or two rules each
    spending a call the author did not account for.
    """

    limit: int = MAX_SQL_CALLS_PER_REQUEST
    used: int = 0
    labels: list[str] = field(default_factory=list)

    @property
    def remaining(self) -> int:
        """Calls still available. Never negative."""
        return max(0, self.limit - self.used)

    @property
    def exhausted(self) -> bool:
        """True when no further SQL call is permitted."""
        return self.remaining == 0

    def spend(self, label: str = "sql") -> int:
        """
        Consume one SQL call from the request's budget (General Rule 27).

        Verbatim: "Limit to 5 SQL calls per request."

        Call this immediately before issuing the query. Raises QueryBudgetExceeded on the
        call that would exceed the limit, listing what the budget was spent on.
        """
        if self.used >= self.limit:
            raise QueryBudgetExceeded(
                f"General Rule 27 allows {self.limit} SQL calls per request; a "
                f"{self.used + 1}th was requested for {label!r}. "
                f"Spent on: {', '.join(self.labels) or '(nothing recorded)'}. "
                f"{BUDGET_INTERACTION_NOTE}"
            )
        self.used += 1
        self.labels.append(label)
        return self.remaining

    def can_spend(self, n: int = 1) -> bool:
        """Whether ``n`` more calls fit, for planning a multi-step retrieval up front."""
        return self.remaining >= n

    def reset(self) -> None:
        """Start a new request. Explicit, because silent reuse is how a budget leaks."""
        self.used = 0
        self.labels.clear()


def requires_incremental_summary(application_count: int) -> bool:
    """
    Whether a batch must stream its findings (General Rule 27).

    Verbatim: "For batch analyses of more than 5 applications, summarize findings
    incrementally rather than accumulating all data before responding."

    Strictly greater than five: exactly 5 applications may be buffered, 6 may not. On
    AgentCore this selects the async-generator entrypoint, whose yields become SSE frames.
    """
    return application_count > INCREMENTAL_SUMMARY_THRESHOLD


T = TypeVar("T")


def incremental_batches(
    applications: Sequence[T], batch_size: int = INCREMENTAL_SUMMARY_THRESHOLD
) -> Iterator[list[T]]:
    """
    Chunk a batch so findings can be emitted as they are produced (General Rule 27).

    For a batch at or below the threshold, yields one chunk — buffering is permitted there,
    and chunking it would add streaming overhead the rule does not ask for.
    """
    total = len(applications)
    if not requires_incremental_summary(total):
        if total:
            yield list(applications)
        return
    for start in range(0, total, batch_size):
        yield list(applications[start : start + batch_size])


def budgeted(budget: QueryBudget, label: str = "sql"):
    """
    Decorator applying the General Rule 27 budget to a query function.

    Wraps a retrieval callable so the counter is spent before the call is issued. The
    mechanical enforcement point the rule needs: a decorated function cannot be called a
    sixth time regardless of what the model asks for.
    """

    def decorate(fn):
        def wrapper(*args: Any, **kwargs: Any):
            budget.spend(label)
            return fn(*args, **kwargs)

        wrapper.__name__ = getattr(fn, "__name__", "budgeted")
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return decorate


def plan_calls(steps: Iterable[str], budget: QueryBudget | None = None) -> list[str]:
    """
    Check a planned sequence of SQL calls fits the budget before any is issued (Rule 27).

    Returns the steps unchanged when they fit; raises QueryBudgetExceeded naming the first
    step that would not. Planning up front is what lets a caller drop an optional step (a
    General Rule 18 fallback, say) rather than discovering mid-request that it has no calls
    left for the mandatory one.
    """
    plan = list(steps)
    b = budget or QueryBudget()
    if len(plan) > b.remaining:
        raise QueryBudgetExceeded(
            f"planned {len(plan)} SQL calls ({', '.join(plan)}) but only {b.remaining} "
            f"of General Rule 27's {b.limit} remain. {BUDGET_INTERACTION_NOTE}"
        )
    return plan


__all__ = [
    "BUDGET_INTERACTION_NOTE",
    "INCREMENTAL_SUMMARY_THRESHOLD",
    "MAX_SQL_CALLS_PER_REQUEST",
    "QueryBudget",
    "QueryBudgetExceeded",
    "RULE_ID",
    "RULE_TEXT",
    "budgeted",
    "incremental_batches",
    "plan_calls",
    "requires_incremental_summary",
]
