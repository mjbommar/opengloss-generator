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

import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from opengloss_generator.errors import DuplicateDocumentError, StoreError
from opengloss_generator.readability import word_count
from opengloss_generator.schema import (
    CANONICAL_KEY,
    Contrast,
    Etymology,
    Example,
    Lexeme,
    QAFlag,
    ReadingLevel,
    Register,
    RelationType,
    Renditions,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence
    from pathlib import Path

    from opengloss_generator.store import LexemeStore

__all__ = [
    "TEMPLATES",
    "ExportSummary",
    "PretrainRecord",
    "documents_for_entry",
    "export_pretrain",
    "iter_pretrain",
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


@dataclass(slots=True)
class _Doc:
    """One rendered document before it becomes a record.

    ``at_level`` counts the leveled sections (a gloss, an example group, an overview, an
    explanation, a register line, a contrast note) whose text was written at the
    requested level; ``fallback`` counts those that fell back to neutral text. At
    ``neutral`` nothing falls back. A non-neutral document with ``at_level == 0`` carries
    no text of its own and is never emitted (docs/LEVELED-PRETRAIN-PLAN.md § 4).
    """

    text: str
    at_level: int = 0
    fallback: int = 0


class _Tally:
    """Accumulates one document's leveled-section counts while it is rendered."""

    __slots__ = ("at_level", "fallback")

    def __init__(self) -> None:
        self.at_level = 0
        self.fallback = 0

    def note(self, used_fallback: bool) -> None:
        """Count one leveled section by whether it fell back."""
        if used_fallback:
            self.fallback += 1
        else:
            self.at_level += 1

    def doc(self, lines: list[str]) -> _Doc:
        """Return the finished document."""
        return _Doc("\n".join(lines), self.at_level, self.fallback)


def _join_sentences(bits: list[str]) -> str:
    """Join labelled register lines with ``; `` without doubling their end punctuation."""
    trimmed = [bit.rstrip().rstrip(".;") for bit in bits]
    return "; ".join(trimmed) + "."


def _sense_heading(pos_value: str, number: int, gloss: str) -> str:
    """Return the ``## <Pos> sense <n>: <gloss>`` heading shared by two templates."""
    return f"## {pos_value.capitalize()} sense {number}: {gloss}"


def _render_dictionary(entry: Lexeme, level: ReadingLevel) -> _Doc | None:
    """Render the dictionary-entry template: headword, POS blocks, numbered senses.

    Args:
        entry: The entry to render.
        level: The requested reading level.

    Returns:
        The document, or ``None`` if no part-of-speech entry has a live sense to show.
    """
    lines: list[str] = [f"# {entry.headword}"]
    tally = _Tally()
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
            tally.note(gloss_fallback)
            lines.append(f"{number}. {gloss_text}")
            example_texts, example_fallback = _pick_examples(sense.examples, level)
            if example_texts:
                tally.note(example_fallback)
            for example_text in example_texts:
                lines.append(f'   - "{example_text}"')
            added = True

    if not added:
        return None
    return tally.doc(lines)


def _contrasts_by_sense(entry: Lexeme) -> dict[str, list[Contrast]]:
    """Group the entry's contrasts under the sense id their edge starts from.

    ``identity.edge_id`` builds ``"<source_sense_id>-<relation>-><target>"``, so the
    source sense id is everything before the relation segment of the edge id.
    """
    grouped: dict[str, list[Contrast]] = {}
    for contrast in entry.contrasts:
        head = contrast.edge_id.rsplit("->", 1)[0]
        source_sense = head.rsplit("-", 1)[0]
        grouped.setdefault(source_sense, []).append(contrast)
    return grouped


def _render_thesaurus(entry: Lexeme, level: ReadingLevel) -> _Doc | None:
    """Render the thesaurus-entry template: relation lists plus "choosing between them".

    Per live sense with any listed relation or contrast: a heading carrying the sense's
    gloss at ``level``, the synonym/antonym/broader/see-also lists, and one "Choosing
    between them" note per stored contrast of that sense, at ``level``
    (docs/LEVELED-PRETRAIN-PLAN.md § 4.3). The heading gloss and each contrast note are
    leveled sections; the lists are surface forms and are not.

    Args:
        entry: The entry to render.
        level: The requested reading level.

    Returns:
        The document, or ``None`` if no live sense has a listed relation or a contrast.
    """
    lines: list[str] = [f"# {entry.headword}"]
    tally = _Tally()
    added = False
    contrasts = _contrasts_by_sense(entry)
    terms = {edge.edge_id: edge.target for edge in entry.edges()}

    for pos_entry, sense, sid in entry.iter_senses():
        if sense.retired:
            continue
        synonyms = sense.relations_of(RelationType.SYNONYM)
        antonyms = sense.relations_of(RelationType.ANTONYM)
        hypernyms = sense.relations_of(RelationType.HYPERNYM)
        see_also = sense.relations_of(RelationType.SEE_ALSO)
        notes: list[str] = []
        for contrast in contrasts.get(sid, []):
            pick = _pick_text(contrast.text, level)
            if pick is None:
                continue
            text, fallback = pick
            tally.note(fallback)
            target = terms.get(contrast.edge_id) or _parse_edge_target(contrast.edge_id)
            notes.append(f"{entry.headword} or {target or 'a related term'}? {text}")
        if not (synonyms or antonyms or hypernyms or see_also or notes):
            continue
        gloss_pick = _pick_text(sense.gloss, level)
        gloss = sense.canonical_gloss()
        if gloss_pick is not None:
            gloss, gloss_fallback = gloss_pick
            tally.note(gloss_fallback)
        lines.append(_sense_heading(pos_entry.pos.value, sense.index + 1, gloss))
        if synonyms:
            lines.append("Synonyms: " + ", ".join(r.target.term for r in synonyms) + ".")
        if antonyms:
            lines.append("Antonyms: " + ", ".join(r.target.term for r in antonyms) + ".")
        if hypernyms:
            lines.append("Broader terms: " + ", ".join(r.target.term for r in hypernyms) + ".")
        if see_also:
            lines.append("See also: " + ", ".join(r.target.term for r in see_also) + ".")
        if notes:
            lines.append("Choosing between them:")
            lines.extend(notes)
        added = True

    if not added:
        return None
    return tally.doc(lines)


def _render_encyclopedia(entry: Lexeme, level: ReadingLevel) -> _Doc | None:
    """Render the encyclopedia-article template: encyclopedia text, etymology, explanation.

    Args:
        entry: The entry to render.
        level: The requested reading level.

    Returns:
        The document, or ``None`` if the entry has none of the three sections.
    """
    lines: list[str] = [f"# {entry.headword}"]
    tally = _Tally()
    added = False

    overview = _pick_text(entry.encyclopedia, level)
    if overview is not None:
        text, fallback = overview
        tally.note(fallback)
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
        tally.note(fallback)
        lines.append("## Why This Word")
        lines.append(text)
        added = True

    if not added:
        return None
    return tally.doc(lines)


#: Generation-time flags that keep a leveled register rendition out of the usage note.
_EXCLUDING_FLAGS = frozenset(
    {
        QAFlag.OG_READABILITY_MISS,
        QAFlag.OG_HEADWORD_INITIAL,
        QAFlag.OG_NEAR_COPY,
        QAFlag.OG_HARD_VOCABULARY,
    }
)


def _flagged(renditions: Renditions[str], level: ReadingLevel, register: Register) -> bool:
    """Whether the rendition at ``(level, register)`` still carries an excluding flag."""
    rendition = renditions.get(level, register)
    if rendition is None or rendition.assessment is None:
        return False
    return bool(_EXCLUDING_FLAGS.intersection(rendition.assessment.qa_flags))


def _render_usage_note(entry: Lexeme, level: ReadingLevel) -> _Doc | None:
    """Render the usage-note template: one sense's register variants side by side.

    At ``neutral`` every register the sense has is listed. At any other level only the
    registers written *at that level* are listed: a neutral register line under a grade-5
    document would be copied text (docs/LEVELED-PRETRAIN-PLAN.md § 4.4). Contrast
    paragraphs belong to the thesaurus template, not here, so no paragraph appears in two
    templates of one entry.

    Args:
        entry: The entry to render.
        level: The requested reading level.

    Returns:
        The document, or ``None`` if no live sense has a register variant at ``level``.
    """
    lines: list[str] = [f"# {entry.headword}"]
    tally = _Tally()
    added = False

    for pos_entry, sense, _sid in entry.iter_senses():
        if sense.retired:
            continue
        variant_bits: list[str] = []
        for register in _USAGE_NOTE_REGISTERS:
            pick = _pick_register_text(sense.gloss, level, register)
            if pick is None:
                continue
            text, fallback = pick
            if fallback:
                continue
            if level is not ReadingLevel.NEUTRAL and _flagged(sense.gloss, level, register):
                # A leveled line still flagged after its retry stays in the store for
                # lookup but is kept out of the corpus (LEVELED-PRETRAIN-PLAN § 6, L1).
                continue
            tally.note(used_fallback=False)
            variant_bits.append(f"{_REGISTER_LABEL[register]}: {text}")
        if not variant_bits:
            continue
        gloss_pick = _pick_text(sense.gloss, level)
        gloss = gloss_pick[0] if gloss_pick is not None else sense.canonical_gloss()
        lines.append(_sense_heading(pos_entry.pos.value, sense.index + 1, gloss))
        lines.append(_join_sentences(variant_bits))
        added = True

    if not added:
        return None
    return tally.doc(lines)


_RENDER_FUNCS: dict[str, Callable[[Lexeme, ReadingLevel], _Doc | None]] = {
    "dictionary": _render_dictionary,
    "thesaurus": _render_thesaurus,
    "encyclopedia": _render_encyclopedia,
    "usage_note": _render_usage_note,
}

#: ``level_used`` for a non-neutral document some of whose leveled sections fell back to
#: neutral text (docs/LEVELED-PRETRAIN-PLAN.md § 4.2). A document all of whose leveled
#: sections are at its level reports the level itself; ``neutral`` documents report
#: ``neutral``.
MIXED_LEVEL = "mixed"


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
    #: Leveled sections written at ``level`` (docs/LEVELED-PRETRAIN-PLAN.md § 4.2).
    sections_at_level: int = 0

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
            "sections_at_level": self.sections_at_level,
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


def _normalized(text: str) -> str:
    """Return the duplicate-comparison key for a document: casefolded, whitespace-collapsed."""
    return " ".join(text.casefold().split())


def documents_for_entry(
    entry: Lexeme,
    *,
    templates: Sequence[str] = TEMPLATES,
    levels: Sequence[ReadingLevel] = (ReadingLevel.NEUTRAL,),
    per_entry: int | None = None,
    seed: int = 0,
    on_skip: Callable[[str, ReadingLevel], None] | None = None,
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
        on_skip: Called with ``(template, level)`` for every non-neutral document that
            was not emitted because it carried no text of its own at that level.

    Returns:
        One :class:`PretrainRecord` per ``(chosen template, level)`` pair that actually
        has content of its own, in template-then-level order. A retired lexeme (no live
        sense) returns nothing.
    """
    if not any(not sense.retired for _, sense, _ in entry.iter_senses()):
        # A lexeme with no live sense is retired; it renders nothing in any template.
        return []
    available = _available_templates(entry, templates)
    chosen = _select_templates(entry, available, per_entry, seed)

    records: list[PretrainRecord] = []
    for template in chosen:
        render = _RENDER_FUNCS[template]
        neutral = render(entry, ReadingLevel.NEUTRAL)
        neutral_key = _normalized(neutral.text) if neutral is not None else None
        for level in levels:
            doc = neutral if level is ReadingLevel.NEUTRAL else render(entry, level)
            if doc is None:
                if neutral is not None and on_skip is not None:
                    # The template renders at neutral but has nothing at this level.
                    on_skip(template, level)
                continue
            if level is ReadingLevel.NEUTRAL:
                level_used = level.value
            else:
                # No fallback copies (docs/LEVELED-PRETRAIN-PLAN.md § 4.1): a non-neutral
                # document must carry text written at its level and differ from neutral.
                if doc.at_level == 0 or _normalized(doc.text) == neutral_key:
                    if on_skip is not None:
                        on_skip(template, level)
                    continue
                level_used = level.value if doc.fallback == 0 else MIXED_LEVEL
            records.append(
                PretrainRecord(
                    id=f"{entry.lexeme_id}#pretrain-{template}-{level.value}",
                    headword=entry.headword,
                    template=template,
                    level=level.value,
                    level_used=level_used,
                    text=doc.text,
                    n_words=word_count(doc.text),
                    sections_at_level=doc.at_level,
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
    documents_by_fallback: dict[str, int] = field(default_factory=lambda: {"exact": 0, "mixed": 0})
    #: Non-neutral documents not emitted because they had no text of their own at the
    #: level, keyed ``"<template>/<level>"`` (docs/LEVELED-PRETRAIN-PLAN.md § 4.1).
    documents_skipped_no_level_content: dict[str, int] = field(default_factory=dict)
    #: Documents dropped by the duplicate gate when duplicates are allowed (§ 4.6).
    documents_duplicate: int = 0

    def skip(self, template: str, level: ReadingLevel) -> None:
        """Count one non-neutral document skipped for having no leveled text."""
        key = f"{template}/{level.value}"
        skipped = self.documents_skipped_no_level_content
        skipped[key] = skipped.get(key, 0) + 1

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
        bucket = "exact" if doc.level_used == doc.level else "mixed"
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
            "documents_skipped_no_level_content": dict(
                sorted(self.documents_skipped_no_level_content.items())
            ),
            "documents_duplicate": self.documents_duplicate,
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
    allow_duplicates: bool = False,
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
        allow_duplicates: Drop and count duplicates instead of failing (diagnosis only).

    Returns:
        Counts of what was written.
    """
    summary = ExportSummary()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for doc in iter_pretrain(
            store,
            templates=templates,
            levels=levels,
            per_entry=per_entry,
            seed=seed,
            lexeme_ids=lexeme_ids,
            summary=summary,
            allow_duplicates=allow_duplicates,
        ):
            handle.write(json.dumps(doc.as_dict(), ensure_ascii=False))
            handle.write("\n")
    return summary


def _entries_in_id_order(store: LexemeStore, lexeme_ids: Sequence[str] | None) -> Iterator[Lexeme]:
    """Yield the entries to render, in lexeme-id order, one at a time.

    The whole-store path walks ids rather than entry bodies, so only the entry being
    rendered is resident (D-77): a list of every parsed :class:`Lexeme` is tens of
    gigabytes on a full release store and was what made ``export-hf`` unrunnable there.
    A body that no longer parses is skipped, exactly as
    :meth:`~opengloss_generator.store.LexemeStore.iter_entries` skips it.

    Args:
        store: The store to read. Never written.
        lexeme_ids: The requested headwords/ids, or ``None`` for the whole store.

    Yields:
        One entry at a time; absent ids are silently skipped.
    """
    if lexeme_ids is not None:
        for lexeme_id in sorted(set(lexeme_ids)):
            entry = store.read(lexeme_id)
            if entry is not None:
                yield entry
        return
    for lexeme_id in sorted(store.iter_ids()):
        try:
            entry = store.read(lexeme_id)
        except StoreError:  # a corrupt entry must not halt iteration
            continue
        if entry is not None:
            yield entry


def iter_pretrain(
    store: LexemeStore,
    *,
    templates: Sequence[str] = TEMPLATES,
    levels: Sequence[ReadingLevel] = (ReadingLevel.NEUTRAL,),
    per_entry: int | None = None,
    seed: int = 0,
    lexeme_ids: Sequence[str] | None = None,
    summary: ExportSummary | None = None,
    allow_duplicates: bool = False,
) -> Iterator[PretrainRecord]:
    """Yield every pretraining document the store supports, in :func:`export_pretrain` order.

    The streaming half of :func:`export_pretrain`: one entry is read, rendered and
    released before the next is read, so a consumer that writes each record out as it
    arrives never holds the corpus (D-77).

    Args:
        store: The store to read. Never written.
        templates: Which templates to consider (see :data:`TEMPLATES`).
        levels: The reading levels to produce documents for.
        per_entry: Cap on distinct templates per entry; ``None`` for no cap.
        seed: The mixing seed for ``per_entry`` selection.
        lexeme_ids: Restrict to these headwords/ids, when given.
        summary: Filled in as records are yielded, when given. Its counts are complete
            only once the iterator is exhausted.
        allow_duplicates: When ``False`` (the default) the duplicate gate is fatal; when
            ``True`` a duplicate is dropped and counted instead, for diagnosis only.

    Yields:
        One :class:`PretrainRecord` per rendered document.

    Raises:
        DuplicateDocumentError: If two distinct documents share normalized text and
            ``allow_duplicates`` is ``False`` (docs/LEVELED-PRETRAIN-PLAN.md § 4.6).
    """
    seen: dict[bytes, str] = {}
    on_skip = summary.skip if summary is not None else None
    for entry in _entries_in_id_order(store, lexeme_ids):
        if summary is not None:
            summary.entries_scanned += 1
        for doc in documents_for_entry(
            entry,
            templates=templates,
            levels=levels,
            per_entry=per_entry,
            seed=seed,
            on_skip=on_skip,
        ):
            key = hashlib.blake2b(_normalized(doc.text).encode(), digest_size=12).digest()
            first = seen.setdefault(key, doc.id)
            if first != doc.id:
                if not allow_duplicates:
                    raise DuplicateDocumentError(
                        f"pretraining documents {first} and {doc.id} have the same text"
                    )
                if summary is not None:
                    summary.documents_duplicate += 1
                continue
            if summary is not None:
                summary.record(doc)
            yield doc
