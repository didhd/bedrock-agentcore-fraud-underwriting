#!/usr/bin/env python3
"""Create the customer's 24 tables, empty, in our Aurora cluster, from their semantic model.

WHY

`run_use_case` executes the customer's own verified SQL, and that SQL names their tables. Our
cluster had three tables, so nine of the ten use cases failed with
`relation "delink_lender_table" does not exist` -- an honest failure that tells you nothing
about whether the query itself is sound.

The semantic model carries the full schema: 24 tables, every column, every Snowflake type. So
the tables can be created rather than guessed, and then a use case that fails is failing on
something real.

WHAT THIS DOES NOT DO

It creates them **empty**. No synthetic rows, because a fabricated row in an underwriting table
is a fabricated finding one join away, and this repo's whole posture is that a number either
came from somewhere real or does not appear. An empty schema-identical cluster answers exactly
one question -- "does the query run" -- and answers it honestly with zero rows.

THE FINDING THIS SURFACES

24 of the 104 table references in the verified queries are three-part Snowflake names like
`dev_reporting_db.internal.delink_lender_table`. **PostgreSQL cannot resolve those**: it accepts
a three-part name only when the first part is the current database, so a cross-database
reference is a syntax that does not exist here. No amount of schema creation fixes it — the
queries would have to be rewritten, which is not something to do to a query the customer's
engineer verified.

So this script creates the 80 bare references and reports the 24 qualified ones as what they
are: a portability gap between their Snowflake queries and any PostgreSQL target, which is a
decision for them.

    ./deploy/provision-semantic-schema.py           # create the tables
    ./deploy/provision-semantic-schema.py --plan    # print the DDL, touch nothing
    ./deploy/provision-semantic-schema.py --drop    # remove them again
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

STATE = REPO / "deploy" / ".rds-connection.json"

#: Snowflake type -> PostgreSQL type. Deliberately lossy in one direction only: every mapping
#: here is at least as wide as the source, so a value that fit in Snowflake fits here. The
#: reverse would silently truncate.
TYPE_MAP = {
    "VARCHAR": "text",          # VARCHAR(16777216) is Snowflake's unbounded; text is ours
    "CHAR": "text",
    "STRING": "text",
    "FLOAT": "double precision",
    "DOUBLE": "double precision",
    "NUMBER": "numeric",        # precision/scale preserved below where it is meaningful
    "DECIMAL": "numeric",
    "INT": "bigint",
    "INTEGER": "bigint",
    "BIGINT": "bigint",
    "BOOLEAN": "boolean",
    "DATE": "date",
    "TIMESTAMP_NTZ": "timestamp",
    "TIMESTAMP_LTZ": "timestamptz",
    "TIMESTAMP_TZ": "timestamptz",
    "TIMESTAMP": "timestamp",
    "VARIANT": "jsonb",         # semi-structured; jsonb is the honest equivalent
    "OBJECT": "jsonb",
    "ARRAY": "jsonb",
}


def pg_type(snowflake_type: str) -> str:
    """One Snowflake column type as PostgreSQL."""
    base = snowflake_type.split("(")[0].strip().upper()
    mapped = TYPE_MAP.get(base)
    if mapped is None:
        # Reported, not guessed. An unmapped type creating a `text` column would make the
        # cluster look schema-identical while silently changing what the query can compare.
        return f"text /* UNMAPPED Snowflake type {base} */"
    if mapped == "numeric":
        match = re.search(r"\((\d+)\s*,\s*(\d+)\)", snowflake_type)
        if match:
            precision, scale = int(match.group(1)), int(match.group(2))
            # NUMBER(38,0) is Snowflake's default integer. numeric(38,0) is legal but
            # pointlessly wide for a join key, so it becomes bigint when it fits.
            if scale == 0 and precision <= 18:
                return "bigint"
            return f"numeric({precision},{scale})"
    return mapped


def settings() -> dict[str, str]:
    if not STATE.is_file():
        sys.exit(f"{STATE} is absent. Run ./deploy/connect-rds.sh first.")
    stored = json.loads(STATE.read_text())
    for key in ("cluster_arn", "secret_arn", "database"):
        if not stored.get(key):
            sys.exit(f"{STATE} is missing {key}; the Data API needs it.")
    return stored


def ddl_for(table) -> str:
    """`create table if not exists` for one semantic-model table, bare name in `public`."""
    columns = []
    seen = set()
    for column in list(table.dimensions) + list(table.time_dimensions) + list(table.facts):
        name = column.name.lower()
        if name in seen:
            continue
        seen.add(name)
        columns.append(f'  "{name}" {pg_type(column.data_type)}')
    body = ",\n".join(columns)
    return f'create table if not exists "{table.table.lower()}" (\n{body}\n);'


def main() -> int:
    from signal_layer.semantic import available, load_model

    if not available():
        sys.exit(
            "the semantic model is absent. It is customer confidential and gitignored; restore "
            "prompts/verbatim/FRAUDBOT_SEMANTIC_VIEW.yaml from the delivered zip."
        )
    model = load_model()

    plan_only = "--plan" in sys.argv
    drop = "--drop" in sys.argv

    if not plan_only:
        stored = settings()
        os.environ.setdefault("AWS_REGION", "us-west-2")
        os.environ["AURORA_CLUSTER_ARN"] = stored["cluster_arn"]
        os.environ["AURORA_SECRET_ARN"] = stored["secret_arn"]
        os.environ["AURORA_DATABASE"] = stored["database"]
        from signal_layer.sources.aurora import _execute
    else:
        _execute = None  # type: ignore[assignment]

    statements = []
    for name, table in sorted(model.tables.items()):
        statements.append((table.table.lower(), ddl_for(table)))

    unmapped = sum(1 for _, sql in statements if "UNMAPPED" in sql)
    columns = sum(t.column_count for t in model.tables.values())
    print(f"==> {len(statements)} tables, {columns} columns, {unmapped} with an unmapped type")

    if plan_only:
        for physical, sql in statements:
            print(f"\n-- {physical}\n{sql}")
        return 0

    # The queries reference tables by BOTH names: the semantic-model name and the physical
    # base_table name, and for several tables those differ (one has a single underscore where
    # the other has two, and one is a short label over a long dbt-generated name). So the table
    # is created under the physical name and, where the semantic name differs, a VIEW carries
    # the alias. A view rather than a second table: two tables would be two places for a row to
    # be, which is worse than a missing table.
    aliases = [
        (t.table.lower(), name.lower())
        for name, t in sorted(model.tables.items())
        if t.table.lower() != name.lower()
    ]

    for physical, sql in statements:
        if drop:
            _execute(f'drop table if exists "{physical}" cascade')  # noqa: S608
            print(f"    dropped {physical}")
        else:
            _execute(sql)
            print(f"    {physical}")

    for physical, semantic in aliases:
        if drop:
            _execute(f'drop view if exists "{semantic}"')  # noqa: S608
        else:
            _execute(
                f'create or replace view "{semantic}" as select * from "{physical}"'  # noqa: S608
            )
            print(f"    {semantic}  (view -> {physical})")

    if drop:
        print(f"==> dropped {len(statements)} tables")
        return 0

    # ------------------------------------------------------------------
    # The portability gap, reported rather than worked around.
    # ------------------------------------------------------------------
    qualified: set[str] = set()
    bare: set[str] = set()
    for query in model.verified_queries:
        for ref in re.findall(r"\b(?:from|join)\s+([A-Za-z_\"][\w\".]*)", query.sql, re.I):
            cleaned = ref.strip('"').lower()
            (qualified if "." in cleaned else bare).add(cleaned)

    print(f"\n==> {len(statements)} tables created (empty)")
    print(f"    {len(bare)} distinct bare table references in the verified queries will resolve")
    if qualified:
        print(f"\n    {len(qualified)} THREE-PART references will NOT resolve, and cannot:")
        for name in sorted(qualified):
            print(f"      {name}")
        print(
            "\n    PostgreSQL accepts a three-part name only when the first part is the current\n"
            "    database, so a cross-database reference is a syntax that does not exist here.\n"
            "    Creating more schemas does not fix it. Either the queries are rewritten -- which\n"
            "    is not something to do to SQL the customer's engineer verified -- or those use\n"
            "    cases run against Snowflake. This is a decision for the customer, and it is\n"
            "    recorded in docs/semantic-model-reconciliation.md."
        )

    print("\nTry them:  python3 -c \"...\" or ask Francis to run a use case.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
