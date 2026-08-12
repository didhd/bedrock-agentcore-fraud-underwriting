"""The customer's Cortex semantic model, loaded verbatim and sha256-guarded.

Re-exported here so callers write `from signal_layer.semantic import load_model` and never
touch the file path themselves. Nothing in this package embeds customer content: the bytes
live in `prompts/verbatim/`, which is gitignored.
"""

from signal_layer.semantic.loader import (
    SEMANTIC_MODEL_FILE,
    Column,
    Relationship,
    Rule,
    SemanticIntegrityError,
    SemanticModel,
    SemanticModelUnavailable,
    Table,
    VerifiedQuery,
    available,
    databases,
    fully_qualified,
    load_model,
    model_path,
)

__all__ = [
    "SEMANTIC_MODEL_FILE",
    "Column",
    "Relationship",
    "Rule",
    "SemanticIntegrityError",
    "SemanticModel",
    "SemanticModelUnavailable",
    "Table",
    "VerifiedQuery",
    "available",
    "databases",
    "fully_qualified",
    "load_model",
    "model_path",
]
