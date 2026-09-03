"""Free, offline exporters that turn stored entries into retrieval-training data.

Every module here reads a :class:`~opengloss_generator.store.LexemeStore` and writes a
plain JSONL/TSV file; none call a model (``docs/RETRIEVAL-DATA-PLAN.md``'s
non-negotiable 6/8). Kept deliberately minimal: sibling modules (``triples.py``,
``qrels.py``, ``pretrain.py``, ...) are added by later features, each on its own
branch, so this file stays a docstring rather than a shared surface they must agree on.
"""

from __future__ import annotations

__all__: list[str] = []
