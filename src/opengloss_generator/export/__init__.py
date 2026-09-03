"""Free, offline exports of a lexeme store into training-ready formats.

Every submodule here is one export format for ``../opengloss-embedding`` (pairs,
triples, qrels, pretraining documents): each reads the store and writes plain
JSONL/TSV, none calls a model, and none writes back to the store. See
``docs/RETRIEVAL-DATA-PLAN.md`` for the shared feature plan these belong to.
"""

from __future__ import annotations
