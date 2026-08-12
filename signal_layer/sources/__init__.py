"""Signal sources behind ``app.runtime_support.build_signal_payload``.

One module per source, selected by ``SIGNAL_MODE``:

    fixtures  the 14 committed applications (default; no network, no credentials)
    cortex    Snowflake Cortex Analyst, reached with a PAT -- ``snowflake.py``
    aurora    the precomputed governed signals in Aurora PostgreSQL

The point of the package is that the AGENTS do not change when the source does. Every
specialist reads through one function, so migration is a config change rather than an
agent rewrite -- which is the property the customer's own architecture already relies on.
"""
