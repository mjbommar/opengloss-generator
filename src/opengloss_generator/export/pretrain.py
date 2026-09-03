"""F9 — serialise entries into natural pretraining documents (D-61).

``../opengloss-embedding`` wants tokens per dollar as well as fine-tuning pairs, and an
OpenGloss entry already holds the four kinds of reference prose a dictionary-adjacent
pretraining corpus wants: a dictionary entry, a thesaurus entry, an encyclopedia
article, and a usage note. All four already exist as *fields* on the entry; this module
only serialises them into flowing prose/light-markdown documents, so it makes no model
calls and never writes to the store (docs/RETRIEVAL-DATA-PLAN.md, F9).

Every renderer degrades gracefully rather than emitting a hole: a section with nothing
to say is left out of the document entirely (never an empty heading), a whole template
that would have no content for one entry is skipped for that entry, and any renderer
that needs a reading level neither has itself falls back to the canonical
``(neutral, plain)`` text and reports that in the returned record's ``level_used`` (the
plan's "fall back to neutral and say so"). A retired sense contributes nothing to any
template — the dictionary and thesaurus templates check :attr:`Sense.retired` directly,
and the other two only ever read entry-level fields or a sense's canonical gloss through
those same checks.

:func:`documents_for_entry` picks, once per entry from a seeded RNG keyed on the entry's
own id, which of the requested templates that entry gets when ``per_entry`` asks for
fewer than are available — so a corpus built with a small ``per_entry`` still mixes
templates across entries rather than always dropping the same ones. Document ids are
derived (``<lexeme_id>#pretrain-<template>-<level>``), like every other id in this
project (D-1): a consumer holding only the JSONL can recompute what produced a row.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from opengloss_generator.readability import word_count
from opengloss_generator.schema import (
    CANONICAL_KEY,
    Etymology,
    Example,
    Lexeme,
    ReadingLevel,
    Register,
    RelationType,
    Renditions,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from pathlib import Path

    from opengloss_generator.store import LexemeStore

__all__ = [
    "TEMPLATES",
    "ExportSummary",
    "PretrainRecord",
    "documents_for_entry",
    "export_pretrain",
]

#: The four templates F9 defines, in a fixed order used everywhere selection or
#: iteration needs one: the corpus mix, a document's id, and every summary breakdown are
#: all stable across runs because of this one ordering.
TEMPLATES: tuple[str, ...] = ("dictionary", "thesaurus", "encyclopedia", "usage_note")

#: How many examples the dictionary template shows per sense (plan: "1-2 examples").
_MAX_DICTIONARY_EXAMPLES = 2

#: Registers a usage note compares side by side, in display order. ``plain`` is the
#: baseline every other register is implicitly contrasted with, so it is not repeated as
#: its own line.
_USAGE_NOTE_REGISTERS: tuple[Register, ...] = (
    Register.INFORMAL,
    Register.FORMAL,
    Register.TECHNICAL,
    Register.SLANG,
    Register.IN_HOUSE,
    Register.MARKETING,
)

_REGISTER_LABEL: dict[Register, str] = {
    Register.INFORMAL: "Informally",
    Register.FORMAL: "In formal writing",
    Register.TECHNICAL: "In technical writing",
    Register.SLANG: "In slang",
    Register.IN_HOUSE: "In-house",
    Register.MARKETING: "In marketing copy",
}


def _pick_text(renditions: Renditions[str], level: ReadingLevel) -> tuple[str, bool] | None:
    """Return ``(text, used_fallback)`` for a plain-register field at ``level``.

    Tries ``(level, plain)`` first; falls back to the canonical ``(neutral, plain)``
    rendition when that is absent. Returns ``None`` when neither has any text at all.

    Args:
        renditions: The rendition set to read (a gloss, the encyclopedia, ...).
        level: The requested reading level.

    Returns:
        The text and whether it came from the neutral fallback rather than ``level``,
        or ``None`` if the set has nothing usable.
    """
    exact = renditions.get(level, Register.PLAIN)
    if exact is not None and exact.content.strip():
        return exact.content, False
    canonical = renditions.canonical()
    if canonical is not None and canonical.content.strip():
        return canonical.content, (level, Register.PLAIN) != CANONICAL_KEY
    return None


def _pick_register_text(
    renditions: Renditions[str], level: ReadingLevel, register: Register
) -> tuple[str, bool] | None:
    """Return ``(text, used_fallback)`` for one register at ``level``, register held fixed.

    Unlike :func:`_pick_text`, this never falls back to a *different* register — a usage
    note comparing registers must not silently repeat the plain gloss under a register
    label it was never written for. It falls back only along the reading-level axis, to
    ``(neutral, register)``.

    Args:
        renditions: The rendition set to read (a sense's gloss).
        level: The requested reading level.
        register: The register that must be matched.

    Returns:
        The text and whether it came from the neutral-level fallback, or ``None`` if
        this register has no rendition at either level.
    """
    exact = renditions.get(level, register)
    if exact is not None and exact.content.strip():
        return exact.content, False
    neutral = renditions.get(ReadingLevel.NEUTRAL, register)
    if neutral is not None and neutral.content.strip():
        return neutral.content, level is not ReadingLevel.NEUTRAL
    return None


def _pick_examples(
    examples: Renditions[Example], level: ReadingLevel, *, limit: int = _MAX_DICTIONARY_EXAMPLES
) -> tuple[list[str], bool]:
    """Return up to ``limit`` example sentences at ``level``, falling back to canonical.

    Args:
        examples: A sense's example rendition set.
        level: The requested reading level.
        limit: The maximum number of example texts to return.

    Returns:
        The example texts (possibly empty) and whether the neutral fallback was used.
    """
    at_level = [
        rendition.content.text
        for rendition in examples
        if rendition.reading_level is level
        and rendition.style is Register.PLAIN
        and rendition.content.text.strip()
    ]
    if at_level:
        return at_level[:limit], False
    at_neutral = [
        rendition.content.text
        for rendition in examples
        if rendition.reading_level is ReadingLevel.NEUTRAL
        and rendition.style is Register.PLAIN
        and rendition.content.text.strip()
    ]
    return at_neutral[:limit], bool(at_neutral) and level is not ReadingLevel.NEUTRAL


def _parse_edge_target(edge_id: str) -> str | None:
    """Return the display name of the far end of a derived edge id, or ``None``.

    ``identity.edge_id`` builds ``"<source_sense_id>-<relation>-><target>"``; the source
    sense id and the relation type never themselves contain ``"->"``, so splitting on
    the last occurrence of it isolates the target's lexeme slug cleanly.

    Args:
        edge_id: A :class:`~opengloss_generator.schema.Contrast`'s edge id.

    Returns:
        The target lexeme slug with underscores turned back into spaces, or ``None``
        if ``edge_id`` is not shaped like a derived edge id.
    """
    if "->" not in edge_id:
        return None
    _, target = edge_id.rsplit("->", 1)
    target = target.strip()
    if not target:
        return None
    return target.replace("_", " ")


def _etymology_prose(etymology: Etymology) -> str:
    """Render an :class:`Etymology` as a short prose paragraph.

    Args:
        etymology: The entry's etymology block.

    Returns:
        The paragraph, or ``""`` if the etymology carries no usable text at all.
    """
    parts: list[str] = []
    if etymology.summary.strip():
        parts.append(etymology.summary.strip())
    for segment in etymology.segments:
        piece = f'In {segment.language}, it appeared as "{segment.form}"'
        if segment.meaning:
            piece += f' (meaning "{segment.meaning}")'
        if segment.era:
            piece += f", during the {segment.era}"
        parts.append(piece + ".")
    if etymology.cognates:
        parts.append("Cognates include " + ", ".join(etymology.cognates) + ".")
    return " ".join(parts)


def _render_dictionary(entry: Lexeme, level: ReadingLevel) -> tuple[str, bool] | None:
    """Render the dictionary-entry template: headword, POS blocks, numbered senses.

    Args:
        entry: The entry to render.
        level: The requested reading level.

    Returns:
        ``(text, used_fallback)``, or ``None`` if no part-of-speech entry has a live
        sense to show.
    """
    lines: list[str] = [f"# {entry.headword}"]
    used_fallback = False
    added = False

    for pos_entry in entry.pos_entries:
        live_senses = [s for s in pos_entry.senses if not s.retired]
        if not live_senses:
            continue
        lines.append(f"## {pos_entry.pos.value.capitalize()}")

        morphology = pos_entry.morphology
        forms: list[str] = []
        for label, value in (
            ("plural", morphology.plural),
            ("past tense", morphology.past_tense),
            ("past participle", morphology.past_participle),
            ("present participle", morphology.present_participle),
            ("third person singular", morphology.third_person_singular),
            ("comparative", morphology.comparative),
            ("superlative", morphology.superlative),
        ):
            if value:
                forms.append(f"{label}: {value}")
        if morphology.derivations:
            forms.append("derived forms: " + ", ".join(morphology.derivations))
        if forms:
            lines.append("Forms: " + "; ".join(forms) + ".")

        for number, sense in enumerate(live_senses, start=1):
            gloss_pick = _pick_text(sense.gloss, level)
            if gloss_pick is None:
                continue
            gloss_text, gloss_fallback = gloss_pick
            used_fallback = used_fallback or gloss_fallback
            lines.append(f"{number}. {gloss_text}")
            example_texts, example_fallback = _pick_examples(sense.examples, level)
            used_fallback = used_fallback or example_fallback
            for example_text in example_texts:
                lines.append(f'   - "{example_text}"')
            added = True

    if not added:
        return None
    return "\n".join(lines), used_fallback


def _render_thesaurus(entry: Lexeme, level: ReadingLevel) -> tuple[str, bool] | None:
    """Render the thesaurus-entry template: per-sense synonyms/antonyms/hypernyms/see-also.

    Relation targets are surface forms, not leveled text, so this template never falls
    back — ``level`` is accepted only so every template shares one call signature.

    Args:
        entry: The entry to render.
        level: Unused; present for signature parity with the other renderers.

    Returns:
        ``(text, False)``, or ``None`` if no live sense has any of the four relation
        types this template lists.
    """
    lines: list[str] = [f"# {entry.headword}"]
    added = False

    for pos_entry in entry.pos_entries:
        for sense in pos_entry.senses:
            if sense.retired:
                continue
            synonyms = sense.relations_of(RelationType.SYNONYM)
            antonyms = sense.relations_of(RelationType.ANTONYM)
            hypernyms = sense.relations_of(RelationType.HYPERNYM)
            see_also = sense.relations_of(RelationType.SEE_ALSO)
            if not (synonyms or antonyms or hypernyms or see_also):
                continue
            lines.append(
                f"## {pos_entry.pos.value.capitalize()} sense {sense.index + 1}: "
                f"{sense.canonical_gloss()}"
            )
            if synonyms:
                lines.append("Synonyms: " + ", ".join(r.target.term for r in synonyms) + ".")
            if antonyms:
                lines.append("Antonyms: " + ", ".join(r.target.term for r in antonyms) + ".")
            if hypernyms:
                lines.append("Broader terms: " + ", ".join(r.target.term for r in hypernyms) + ".")
            if see_also:
                lines.append("See also: " + ", ".join(r.target.term for r in see_also) + ".")
            added = True

    if not added:
        return None
    return "\n".join(lines), False


def _render_encyclopedia(entry: Lexeme, level: ReadingLevel) -> tuple[str, bool] | None:
    """Render the encyclopedia-article template: encyclopedia text, etymology, explanation.

    Args:
        entry: The entry to render.
        level: The requested reading level.

    Returns:
        ``(text, used_fallback)``, or ``None`` if the entry has none of the three
        sections.
    """
    lines: list[str] = [f"# {entry.headword}"]
    used_fallback = False
    added = False

    overview = _pick_text(entry.encyclopedia, level)
    if overview is not None:
        text, fallback = overview
        used_fallback = used_fallback or fallback
        lines.append("## Overview")
        lines.append(text)
        added = True

    if entry.etymology is not None:
        prose = _etymology_prose(entry.etymology)
        if prose:
            lines.append("## Etymology")
            lines.append(prose)
            added = True

    explanation = _pick_text(entry.lexical_explanation, level)
    if explanation is not None:
        text, fallback = explanation
        used_fallback = used_fallback or fallback
        lines.append("## Why This Word")
        lines.append(text)
        added = True

    if not added:
        return None
    return "\n".join(lines), used_fallback


def _render_usage_note(entry: Lexeme, level: ReadingLevel) -> tuple[str, bool] | None:
    """Render the usage-note template: register variants side by side, plus contrasts.

    Args:
        entry: The entry to render.
        level: The requested reading level.

    Returns:
        ``(text, used_fallback)``, or ``None`` if no live sense has a register variant
        and the entry has no contrasts either.
    """
    lines: list[str] = [f"# {entry.headword}"]
    used_fallback = False
    added = False

    for pos_entry in entry.pos_entries:
        for sense in pos_entry.senses:
            if sense.retired:
                continue
            variant_bits: list[str] = []
            for register in _USAGE_NOTE_REGISTERS:
                pick = _pick_register_text(sense.gloss, level, register)
                if pick is None:
                    continue
                text, fallback = pick
                used_fallback = used_fallback or fallback
                variant_bits.append(f"{_REGISTER_LABEL[register]}: {text}")
            if variant_bits:
                lines.append(
                    f"## {pos_entry.pos.value.capitalize()} sense {sense.index + 1}: "
                    f"{sense.canonical_gloss()}"
                )
                lines.append("; ".join(variant_bits) + ".")
                added = True

    contrast_lines: list[str] = []
    for contrast in entry.contrasts:
        pick = _pick_text(contrast.text, level)
        if pick is None:
            continue
        text, fallback = pick
        used_fallback = used_fallback or fallback
        target = _parse_edge_target(contrast.edge_id) or "a related term"
        contrast_lines.append(f"Compared to {target}: {text}")
    if contrast_lines:
        lines.append("## Related Terms")
        lines.extend(contrast_lines)
        added = True

    if not added:
        return None
    return "\n".join(lines), used_fallback


_RENDER_FUNCS: dict[str, Callable[[Lexeme, ReadingLevel], tuple[str, bool] | None]] = {
    "dictionary": _render_dictionary,
    "thesaurus": _render_thesaurus,
    "encyclopedia": _render_encyclopedia,
    "usage_note": _render_usage_note,
}


@dataclass(frozen=True, slots=True)
class PretrainRecord:
    """One serialised pretraining document (F9)."""

    id: str
    headword: str
    template: str
    level: str
    level_used: str
    text: str
    n_words: int

    def as_dict(self) -> dict[str, object]:
        """Return the JSONL row, field order matching ``docs/RETRIEVAL-DATA-PLAN.md``."""
        return {
            "id": self.id,
            "headword": self.headword,
            "template": self.template,
            "level": self.level,
            "level_used": self.level_used,
            "text": self.text,
            "n_words": self.n_words,
        }


def _available_templates(entry: Lexeme, templates: Iterable[str]) -> list[str]:
    """Return the requested templates that actually render something for ``entry``.

    Availability is checked once at :attr:`~opengloss_generator.schema.ReadingLevel.NEUTRAL`
    — every renderer's canonical-fallback path guarantees that whatever content exists
    at all is visible at the neutral level, so this is a level-independent yes/no test,
    which is what lets ``per_entry`` select a template set once per entry rather than
    once per ``(entry, level)`` pair.

    Args:
        entry: The entry to probe.
        templates: The template names to consider, checked in :data:`TEMPLATES` order.

    Returns:
        The subset that renders, in :data:`TEMPLATES` order.
    """
    wanted = set(templates)
    return [
        name
        for name in TEMPLATES
        if name in wanted and _RENDER_FUNCS[name](entry, ReadingLevel.NEUTRAL) is not None
    ]


def _select_templates(
    entry: Lexeme, available: list[str], per_entry: int | None, seed: int
) -> list[str]:
    """Return which of ``available`` templates ``entry`` gets, seeded so the corpus mixes.

    Args:
        entry: The entry being rendered, whose id seeds the per-entry draw.
        available: The templates that render something for this entry, in
            :data:`TEMPLATES` order.
        per_entry: The cap on templates per entry, or ``None`` for no cap.
        seed: The run's mixing seed; combined with the entry id so two entries with the
            same seed draw independently, and the same ``(seed, entry)`` always draws
            the same subset.

    Returns:
        The chosen templates, in :data:`TEMPLATES` order.
    """
    if per_entry is None or per_entry >= len(available):
        return available
    if per_entry <= 0:
        return []
    rng = random.Random(f"{seed}:{entry.lexeme_id}")  # noqa: S311 - sampling, not crypto
    chosen = set(rng.sample(available, per_entry))
    return [name for name in available if name in chosen]


def documents_for_entry(
    entry: Lexeme,
    *,
    templates: Sequence[str] = TEMPLATES,
    levels: Sequence[ReadingLevel] = (ReadingLevel.NEUTRAL,),
    per_entry: int | None = None,
    seed: int = 0,
) -> list[PretrainRecord]:
    """Render every requested pretraining document for one entry, deterministically.

    Args:
        entry: The entry to render.
        templates: Which templates to consider, in any order (see :data:`TEMPLATES` for
            the valid names).
        levels: The reading levels to produce a document for.
        per_entry: Cap on distinct templates rendered for this entry; ``None`` renders
            every available one. When the cap is below what is available, the chosen
            subset is drawn deterministically from ``seed`` and the entry's id.
        seed: The mixing seed (see :func:`_select_templates`).

    Returns:
        One :class:`PretrainRecord` per ``(chosen template, level)`` pair that actually
        has content, in template-then-level order.
    """
    available = _available_templates(entry, templates)
    chosen = _select_templates(entry, available, per_entry, seed)

    records: list[PretrainRecord] = []
    for template in chosen:
        render = _RENDER_FUNCS[template]
        for level in levels:
            result = render(entry, level)
            if result is None:
                continue
            text, used_fallback = result
            level_used = ReadingLevel.NEUTRAL.value if used_fallback else level.value
            doc_id = f"{entry.lexeme_id}#pretrain-{template}-{level.value}"
            records.append(
                PretrainRecord(
                    id=doc_id,
                    headword=entry.headword,
                    template=template,
                    level=level.value,
                    level_used=level_used,
                    text=text,
                    n_words=word_count(text),
                )
            )
    return records


@dataclass(slots=True)
class ExportSummary:
    """Counts from one :func:`export_pretrain` run."""

    entries_scanned: int = 0
    documents_written: int = 0
    words_total: int = 0
    documents_by_template: dict[str, int] = field(default_factory=dict)
    words_by_template: dict[str, int] = field(default_factory=dict)
    documents_by_level: dict[str, int] = field(default_factory=dict)
    words_by_level: dict[str, int] = field(default_factory=dict)
    documents_by_fallback: dict[str, int] = field(
        default_factory=lambda: {"exact": 0, "fallback": 0}
    )

    def record(self, doc: PretrainRecord) -> None:
        """Fold one written record's counts into the summary."""
        self.documents_written += 1
        self.words_total += doc.n_words
        by_template = self.documents_by_template
        by_template[doc.template] = by_template.get(doc.template, 0) + 1
        words_by_template = self.words_by_template
        words_by_template[doc.template] = words_by_template.get(doc.template, 0) + doc.n_words
        by_level = self.documents_by_level
        by_level[doc.level] = by_level.get(doc.level, 0) + 1
        words_by_level = self.words_by_level
        words_by_level[doc.level] = words_by_level.get(doc.level, 0) + doc.n_words
        bucket = "exact" if doc.level_used == doc.level else "fallback"
        self.documents_by_fallback[bucket] += 1

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-able view of the summary."""
        return {
            "entries_scanned": self.entries_scanned,
            "documents_written": self.documents_written,
            "words_total": self.words_total,
            "documents_by_template": dict(sorted(self.documents_by_template.items())),
            "words_by_template": dict(sorted(self.words_by_template.items())),
            "documents_by_level": dict(sorted(self.documents_by_level.items())),
            "words_by_level": dict(sorted(self.words_by_level.items())),
            "documents_by_fallback": dict(self.documents_by_fallback),
        }


def export_pretrain(
    store: LexemeStore,
    out_path: Path,
    *,
    templates: Sequence[str] = TEMPLATES,
    levels: Sequence[ReadingLevel] = (ReadingLevel.NEUTRAL,),
    per_entry: int | None = None,
    seed: int = 0,
    lexeme_ids: Sequence[str] | None = None,
) -> ExportSummary:
    """Write one pretraining-document JSONL file for every entry in ``store``.

    Entries are visited in lexeme-id order regardless of on-disk shard layout, so the
    output is byte-identical across runs and machines for the same inputs (D-61).

    Args:
        store: The store to read. Never written.
        out_path: Where to write the JSONL. Parent directories are created as needed.
        templates: Which templates to consider (see :data:`TEMPLATES`).
        levels: The reading levels to produce documents for.
        per_entry: Cap on distinct templates per entry; ``None`` for no cap.
        seed: The mixing seed for ``per_entry`` selection.
        lexeme_ids: Restrict to these headwords/ids, when given; otherwise every entry
            in the store.

    Returns:
        Counts of what was written.
    """
    if lexeme_ids is not None:
        entries = [
            entry
            for lexeme_id in sorted(set(lexeme_ids))
            if (entry := store.read(lexeme_id)) is not None
        ]
    else:
        entries = sorted(store.iter_entries(), key=lambda e: e.lexeme_id)

    summary = ExportSummary()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            summary.entries_scanned += 1
            for doc in documents_for_entry(
                entry, templates=templates, levels=levels, per_entry=per_entry, seed=seed
            ):
                handle.write(json.dumps(doc.as_dict(), ensure_ascii=False))
                handle.write("\n")
                summary.record(doc)
    return summary
