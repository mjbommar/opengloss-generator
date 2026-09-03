"""Deterministic identifier derivation.

Every identifier in the store is a pure function of an entry's structure, so a consumer
holding only an export can recompute it. See ``docs/DESIGN.md`` § 2.1 for why.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

__all__ = [
    "edge_id",
    "encyclopedia_owner_id",
    "explanation_owner_id",
    "pos_entry_id",
    "qa_id",
    "query_id",
    "rendition_id",
    "sense_id",
    "shard_for",
    "slugify",
    "variant_id",
]

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_SHARD_WIDTH = 2
_SHARD_DEPTH = 2


def slugify(headword: str) -> str:
    """Return the canonical lexeme identifier for a headword.

    Unicode is folded to ASCII, case is lowered, and any run of non-alphanumeric
    characters becomes a single underscore. Multi-word headwords keep a single
    underscore between tokens, matching the OpenGloss v1.x convention where
    ``"3d model"`` is stored as ``3d_model``.

    Args:
        headword: The surface form of the lexeme.

    Returns:
        A slug safe for use as both an identifier and a filename.

    Raises:
        ValueError: If the headword contains no slug-able characters.
    """
    folded = unicodedata.normalize("NFKD", headword)
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")
    slug = _NON_SLUG.sub("_", ascii_only.lower()).strip("_")
    if not slug:
        raise ValueError(f"headword {headword!r} yields an empty slug")
    return slug


def pos_entry_id(lexeme_id: str, pos: str) -> str:
    """Return the identifier for a part-of-speech entry within a lexeme."""
    return f"{lexeme_id}:{pos}"


def sense_id(lexeme_id: str, pos: str, index: int) -> str:
    """Return the identifier for a sense.

    Args:
        lexeme_id: The owning lexeme's slug.
        pos: The part-of-speech tag.
        index: Zero-based position of the sense within its part-of-speech entry.

    Returns:
        A positional sense identifier, e.g. ``abseil:verb:0``.
    """
    return f"{lexeme_id}:{pos}:{index}"


def rendition_id(owner_id: str, reading_level: str, register: str) -> str:
    """Return the identifier for one rendition of a text-bearing field.

    Args:
        owner_id: The owning object's id — a sense id for glosses and examples,
            :func:`encyclopedia_owner_id` or :func:`explanation_owner_id` for the
            entry-level prose sections.
        reading_level: The reading-level tag, e.g. ``grade_5``.
        register: The register tag, e.g. ``plain``.

    Returns:
        A rendition identifier, e.g. ``abseil:verb:0#grade_5/plain``.
    """
    return f"{owner_id}#{reading_level}/{register}"


def query_id(owning_sense_id: str, index: int) -> str:
    """Return the identifier for one synthetic retrieval query on a sense.

    Positional within the sense's ``queries`` list and **zero-based**, matching
    :func:`sense_id`'s treatment of a sense's position within its part-of-speech entry
    (the provenance table's ``p1``, ``p2``, … are one-based, but those are dictionary
    keys handed out on insertion, not list positions). Reordering the list therefore
    renames its members: append, never insert.

    Args:
        owning_sense_id: The sense the query belongs to.
        index: Zero-based position within :attr:`~opengloss_generator.schema.Sense.queries`.

    Returns:
        A query identifier, e.g. ``abseil:verb:0#q3``.
    """
    return f"{owning_sense_id}#q{index}"


def qa_id(owning_sense_id: str, index: int) -> str:
    """Return the identifier for one question/answer pair on a sense.

    Zero-based and positional, exactly as :func:`query_id`.

    Args:
        owning_sense_id: The sense the pair belongs to.
        index: Zero-based position within :attr:`~opengloss_generator.schema.Sense.qa`.

    Returns:
        A QA-pair identifier, e.g. ``abseil:verb:0#qa3``.
    """
    return f"{owning_sense_id}#qa{index}"


def variant_id(owning_sense_id: str, reading_level: str, register: str) -> str:
    """Return the identifier for a definition variant of a sense.

    Deprecated: schema v3 calls these renditions. Use :func:`rendition_id`, which this
    function forwards to unchanged; the identifier format is identical.
    """
    return rendition_id(owning_sense_id, reading_level, register)


def encyclopedia_owner_id(lexeme_id: str) -> str:
    """Return the rendition owner id for an entry's encyclopedia section."""
    return f"{lexeme_id}:encyclopedia"


def explanation_owner_id(lexeme_id: str) -> str:
    """Return the rendition owner id for an entry's lexical explanation."""
    return f"{lexeme_id}:explanation"


def edge_id(source_sense_id: str, relation: str, target: str) -> str:
    """Return the identifier for a derived semantic edge."""
    return f"{source_sense_id}-{relation}->{target}"


def shard_for(lexeme_id: str) -> tuple[str, ...]:
    """Return the storage shard path components for a lexeme id.

    Shards come from a hash rather than the leading characters of the slug: English
    vocabulary is heavily skewed by first letter, and first-letter sharding would put
    a sixth of the store under ``s/``.

    Args:
        lexeme_id: The lexeme slug.

    Returns:
        A tuple of directory names, outermost first.
    """
    digest = hashlib.blake2b(lexeme_id.encode("utf-8"), digest_size=8).hexdigest()
    return tuple(digest[i * _SHARD_WIDTH : (i + 1) * _SHARD_WIDTH] for i in range(_SHARD_DEPTH))
