#!/usr/bin/env python3
"""Create the signal table in the customer's Aurora cluster and load the committed fixtures.

WHAT THIS IS FOR

`SIGNAL_MODE=aurora` reads precomputed signals from a table. Until that table exists with rows
in it, the mode is configured but unusable, and `--probe` correctly reports
"cluster reachable but application_signals is not". This creates the table and loads the
fourteen committed fixtures into it, so the aurora path can be exercised end to end before the
customer's own data arrives.

WHAT IT IS NOT

It is NOT customer data. The fixtures are synthetic -- every one carries a
`synthetic_data_notice` -- so nothing here is a real borrower. When the customer's own signal
table lands, point `AURORA_SIGNAL_TABLE` at it and this table becomes irrelevant rather than
wrong.

WHY THE LONG SHAPE

One row per (application_id, signal) rather than one wide row per application. Their own
architecture document describes the long shape, and `build_aurora_payload` reads either -- but
seeding the shape they described means the code path the demo exercises is the one their data
will use, not the one that happened to be easier to write.

Idempotent: `create table if not exists`, and each application's rows are deleted before being
re-inserted, so re-running does not double-count.

    ./deploy/seed-rds.py                 # create + load
    ./deploy/seed-rds.py --verify        # count only, change nothing
"""

from __future__ import annotations

import glob
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

STATE = REPO / "deploy" / ".rds-connection.json"


def settings() -> dict[str, str]:
    if not STATE.is_file():
        sys.exit(f"{STATE} is absent. Run ./deploy/connect-rds.sh first.")
    stored = json.loads(STATE.read_text())
    missing = [k for k in ("cluster_arn", "secret_arn", "database") if not stored.get(k)]
    if missing:
        sys.exit(
            f"{STATE} is missing {', '.join(missing)}. The Data API needs all three; a --vpc "
            "deployment has no cluster ARN and cannot be seeded this way."
        )
    return stored


def main() -> int:
    stored = settings()
    table = stored.get("signal_table") or "application_signals"

    os.environ.setdefault("AWS_REGION", "us-west-2")
    os.environ["AURORA_CLUSTER_ARN"] = stored["cluster_arn"]
    os.environ["AURORA_SECRET_ARN"] = stored["secret_arn"]
    os.environ["AURORA_DATABASE"] = stored["database"]
    os.environ["AURORA_SIGNAL_TABLE"] = table

    from signal_layer.sources.aurora import _execute, _quoted_identifier, _rows

    quoted = _quoted_identifier(table)

    if "--verify" in sys.argv:
        rows = _rows(_execute(f"select count(*) as n from {quoted}"))  # noqa: S608
        apps = _rows(
            _execute(f"select count(distinct application_id) as n from {quoted}")  # noqa: S608
        )
        print(f"{table}: {rows[0]['n']} rows across {apps[0]['n']} applications")
        return 0

    print(f"==> creating {table} if absent")
    # `value` is text and not jsonb: a signal is a scalar in every fixture, and a text column is
    # what any Postgres the customer runs will have. The paired `_context` string lives in its
    # own column rather than being folded into the value, because the specialists' own
    # anti-false-positive rules read the context separately from the signal.
    _execute(
        f"""
        create table if not exists {quoted} (
          application_id text not null,
          signal         text not null,
          signal_value   text,
          signal_context text,
          loaded_at      timestamptz not null default now(),
          primary key (application_id, signal)
        )
        """  # noqa: S608
    )

    record_table = "application_records"
    print(f"==> creating {record_table} if absent")
    # One JSON document per application, not a column per field. It IS one document in their
    # architecture, and a column-per-field schema would need revising every time the customer
    # adds one. jsonb rather than text so their own reporting can query into it.
    _execute(
        f"""
        create table if not exists {_quoted_identifier(record_table)} (
          application_id text primary key,
          record         jsonb not null,
          loaded_at      timestamptz not null default now()
        )
        """  # noqa: S608
    )

    files = sorted(glob.glob(str(REPO / "fixtures" / "applications" / "*.json")))
    if not files:
        sys.exit("no fixtures found under fixtures/applications/")

    total = 0
    for path in files:
        record = json.loads(pathlib.Path(path).read_text())
        application_id = record["application_id"]
        signals = record.get("signals") or {}
        context = record.get("context") or {}

        # Delete-then-insert per application, so a re-run replaces rather than accumulates.
        _execute(
            f"delete from {quoted} where application_id = :aid",  # noqa: S608
            [{"name": "aid", "value": {"stringValue": application_id}}],
        )

        for name, value in signals.items():
            _execute(
                f"""insert into {quoted}
                    (application_id, signal, signal_value, signal_context)
                    values (:aid, :sig, :val, :ctx)""",  # noqa: S608
                [
                    {"name": "aid", "value": {"stringValue": application_id}},
                    {"name": "sig", "value": {"stringValue": str(name)}},
                    {
                        "name": "val",
                        "value": (
                            {"isNull": True} if value is None else {"stringValue": str(value)}
                        ),
                    },
                    {
                        "name": "ctx",
                        "value": (
                            {"stringValue": str(context[name])}
                            if name in context and context[name] is not None
                            else {"isNull": True}
                        ),
                    },
                ],
            )
            total += 1
        # The non-signal half. Everything `domain_view` and the guardrail checks read, and
        # nothing else -- notably not `expected`, which is our golden-test data rather than
        # anything the agents may see.
        meta = {
            "application_id": application_id,
            "record_id": record.get("record_id"),
            "application": record.get("application") or {},
            "context": context,
            "alerts_fired": record.get("alerts_fired") or [],
            "semantic_layer_rev": record.get("semantic_layer_rev") or "",
            "computed_at": record.get("computed_at") or "",
        }
        _execute(
            f"""insert into {_quoted_identifier(record_table)} (application_id, record)
                values (:aid, :rec::jsonb)
                on conflict (application_id) do update set record = excluded.record,
                                                           loaded_at = now()""",  # noqa: S608
            [
                {"name": "aid", "value": {"stringValue": application_id}},
                {"name": "rec", "value": {"stringValue": json.dumps(meta)}},
            ],
        )
        print(f"    {application_id}: {len(signals)} signals + record")

    print(f"==> {total} rows across {len(files)} applications")
    print(f"\nProbe it:  ./deploy/connect-rds.sh --probe")
    print(f"Enable it: ./deploy/connect-rds.sh --enable && ./deploy/vendor.sh && "
          f"agentcore deploy --target default --yes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
