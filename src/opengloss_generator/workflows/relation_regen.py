"""Workflow 12 — relation regeneration for senses left with zero relations (D-74).

``workflows/relation_hygiene.py`` (D-50) demotes an untrue edge to ``see_also``;
``workflows/relation_reconcile.py`` (D-65) tombstones it — takes it out of
``Sense.relations`` and writes what it took out to provenance. Both are the right
behaviour, and both are deliberately silent about what is left behind: a sense every one
of whose edges turned out to be wrong ends the sweep with an empty relation list, and
neither pass puts anything back. The 2026-09-05 store-wide audit counted 3,709 of 137,314
live senses in exactly that state (``docs/CORE-DIARY.md``, "Goal 2 complete", open item
1) — every edge they ever had judged untrue and removed. Tier 4's thinner multiword
entries add more (D-70's follow-up already measured 521 on tier 3 alone). This is the
pass that fills them back in.

One luna call per empty sense
------------------------------

Not per entry. A sense with zero relations is rare enough (2.7% of the store) that
pooling calls the way ``examples.py`` pools per-entry buys nothing an entry-level call
would not: most entries needing this pass have exactly one such sense, and an entry with
several gets several independent calls rather than one call straining to keep several
senses' worth of candidates apart. Each call is given the headword, the sense's part of
speech and canonical gloss, one of its own examples, the entry's *other* live senses'
glosses (so the model does not hand back a term that fits a sibling sense instead of this
one — the same discrimination context ``examples.py``'s sense-fit call and
``queries.py``'s prompt both give), the sense's domain, and the list of targets already
judged wrong for this exact sense, and asks for up to :data:`MAX_RELATIONS` typed
relations, each with a one-line justification.

Reuses ``StageName.SENSES`` rather than adding a stage of its own, the way
``relation_hygiene``'s ``validity`` step reuses ``HYGIENE``: this is a generation task in
the same register as the sense stage's own relation list (``workflows/generate.py``), not
a structural verdict, so luna at the sense policy's cost is the right instrument, and a
new :class:`~opengloss_generator.schema.StageName` member would need a
:mod:`~opengloss_generator.config` policy, pricing-table coverage and cost-accounting
plumbing this narrow a pass does not justify. Its contract and instructions are therefore
module-private, following the same convention and for the same reason
``relation_hygiene`` and ``examples.py``'s sense-fit call give: ``contracts.py`` and
``prompts.py`` are edited by several other passes concurrently, and a call that reuses an
existing stage has nothing to add to either file.

Do not re-propose a tombstoned target
--------------------------------------

Every target already judged wrong *for this sense* is collected before the call is made
and named in the prompt as terms not to propose. Two sources, both read-only and both
already on disk:

* a live relation still on the sense whose note is a hygiene demotion note
  (:func:`~opengloss_generator.workflows.relation_reconcile.is_demotion_note`) — not
  expected for a sense that has zero relations by definition, but checked anyway so the
  helper is correct for any sense, not only an empty one;
* :data:`~opengloss_generator.workflows.relation_reconcile.TOMBSTONE_RECORD_PREFIX`
  provenance records naming this sense id, parsed the same way
  ``export/hf_rows.py``'s ``tombstoned`` config parses them: one header line naming the
  sense, then one line per removed edge carrying the type, the term, and the note it left
  with. The term is what is collected; the note is what already explains why it was
  wrong, so there is nothing to relearn from it.

Deliberately *not* collected: ``reconcile:cap`` removals. A relation ``cap`` trims was
never judged untrue — it was crowded out by more of the same type on a sense that had
plenty — and re-proposing it is not the mistake this pass exists to prevent.

Free post-checks, same caps reconcile uses
-------------------------------------------

Every proposed relation is checked before it is written, all of it for $0: a target that
slugs to the entry's own lexeme id is dropped (a sense cannot be related to itself);
a target in the rejected set collected above is dropped and counted, which is the pilot's
headline number — how often the model reached for exactly the term a previous pass
already rejected; an exact ``(type, target)`` duplicate within one call's own answer is
dropped; and each relation type is capped at
:class:`~opengloss_generator.workflows.relation_reconcile.RelationCaps`' own allowance
(synonym 8, antonym 4, hypernym 3, hyponym 8, meronym/holonym 4 by that class's
``default``) — the same ceiling ``relation-reconcile --only cap`` enforces on every other
sense in the store, applied here before an over-long answer is ever written rather than
trimmed by a later sweep.

What this pass does not do
----------------------------

It writes **unresolved** relations — ``RelationTarget.sense_id`` stays ``None`` — with
note ``regen: <justification>``, and stops there. It does not resolve them
(:func:`opengloss_generator.workflows.resolve.resolve_store` does that on its next run,
reading the same store) and it does not judge them
(:func:`~opengloss_generator.workflows.relation_hygiene.run_relation_hygiene`'s
``validity`` step does that on *its* next run). Reimplementing either here would be
asking a generation call to also be the judge of its own output, which is exactly the
shape ``docs/CORE-DIARY.md`` and ``docs/QA-DIARY.md`` warn against elsewhere in this
project. A freshly filled sense is therefore not "done" the moment this pass touches it;
it is done once resolve and relation-hygiene have both run over it again.

Idempotence (D-47) and the two ways a sense stops being visited
-------------------------------------------------------------------

A sense a successful call gave at least one accepted relation to is no longer selected by
a later sweep at all: the selection rule is ``not sense.relations``, read fresh every
sweep, and a non-empty list is a non-empty list whatever put the relations there. No
marker is needed to keep that sense from being billed for twice.

A sense a call *failed to fill* — every proposal was the headword, already rejected, a
duplicate, or over its type's cap — is a different case: it is still empty, so the
selection rule would hand it right back to the next sweep, and a term this pass has
already tried and failed to replace is unlikely to look different on identical input. The
call's own provenance record therefore carries a D-47 sentinel,
``relation_regen:<sense_id>:<digest of the sense's own canonical gloss>;attempts=<n>``,
riding the same record its cost is billed on (``examples.py``'s convention, not
``relation_hygiene``'s separate zero-cost marker record, because there is exactly one
call per marker here). A later sweep skips the sense while its gloss digest matches the
marker's, tries again — up to :data:`MAX_ATTEMPTS` total — the moment a content pass
changes the gloss text (a fresh digest is a fresh chance, on the theory that a gloss a
rewrite pass touched may make a different candidate obvious), and gives up silently past
the bound rather than paying a third opinion for input nothing has changed. A call that
fails outright (``GenerationError``) writes no marker at all, the convention every model
call in this project follows, so it is retried whole rather than counted against the
bound.

Concurrency and locking (D-31) mirror every other sweep here: the unit of work is one
entry, and the handler holding that entry's lock reads it, makes one call per empty sense
it still has, and writes it back inside the same lock hold. An entry with two empty
senses therefore makes its two calls sequentially rather than concurrently, which is the
right trade for a pass this rare — the two calls could not usefully discriminate against
each other's *proposals* anyway, only against each other's glosses, which are already in
each other's prompt as "other senses".
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, ConfigDict, Field

from opengloss_generator.errors import BudgetExceededError, GenerationError
from opengloss_generator.identity import slugify
from opengloss_generator.log import get_logger
from opengloss_generator.prompts import PROMPT_VERSION
from opengloss_generator.runner import run_pool
from opengloss_generator.schema import Relation, RelationTarget, RelationType, StageName
from opengloss_generator.workflows.content_hygiene import PROGRESS_EVERY
from opengloss_generator.workflows.relation_reconcile import (
    TOMBSTONE_LINE_PREFIX,
    TOMBSTONE_RECORD_PREFIX,
    RelationCaps,
    is_demotion_note,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from opengloss_generator.schema import Lexeme, POSEntry, Sense
    from opengloss_generator.stages import StageRunner
    from opengloss_generator.store import LexemeStore

__all__ = [
    "MARKER_PREFIX",
    "MAX_ATTEMPTS",
    "MAX_RELATIONS",
    "RelationRegenOutcome",
    "RelationRegenPlan",
    "plan_relation_regen",
    "run_relation_regen",
]

_LOG = get_logger(__name__)

#: The D-47 sentinel's prefix, completed with the sense id and then, on the same record,
#: the gloss digest and attempt count (see the module docstring).
MARKER_PREFIX = "relation_regen"

#: How many attempts this pass makes on one sense, across sweeps, before leaving it empty
#: rather than billing a third opinion about input nothing has changed (D-47's bound).
MAX_ATTEMPTS = 2

#: The most relations one call is asked for. Six is generous for a sense that currently
#: has none at all, and keeps the answer well under a nano-scale output budget even at
#: the one-line-justification-per-relation this pass asks for.
MAX_RELATIONS = 6

#: Separates the gloss digest from the attempt count inside a marker note, mirroring
#: ``relation_hygiene``'s own separator.
_ATTEMPTS_SEPARATOR = ";attempts="

#: Placeholder shown in the prompt when a sense holds no example yet.
_NO_EXAMPLE = "(none stored)"


# --------------------------------------------------------------------------------------
# The contract and instructions (module-private; see the module docstring)
# --------------------------------------------------------------------------------------


class _RegenRelationType(StrEnum):
    """The relation types this pass may propose.

    A strict subset of :class:`~opengloss_generator.schema.RelationType`. A separate enum,
    not a reused import, is what makes "strict enum types" true for this contract rather
    than merely documented: structured output constrains the model to exactly these six
    values, the same way every other stage's enum field does (``docs/SCHEMA-V3.md`` § 5),
    so a type this pass was not asked to consider — ``derivation``, ``collocation``,
    ``see_also`` — cannot appear in a response at all. Every member's string value matches
    its :class:`RelationType` counterpart exactly, so conversion is a bare value lookup.
    """

    SYNONYM = "synonym"
    ANTONYM = "antonym"
    HYPERNYM = "hypernym"
    HYPONYM = "hyponym"
    MERONYM = "meronym"
    HOLONYM = "holonym"


class _DraftRegenRelation(BaseModel):
    """One proposed relation for a sense that currently has none."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    type: _RegenRelationType
    term: Annotated[str, Field(min_length=1, max_length=60)]
    justification: Annotated[
        str, Field(min_length=1, max_length=200, description="One clause, why this is true.")
    ]


class _DraftRegenRelations(BaseModel):
    """Up to :data:`MAX_RELATIONS` proposals for one empty sense."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    relations: Annotated[list[_DraftRegenRelation], Field(max_length=MAX_RELATIONS)]


#: Instructions for the regeneration call. Byte-stable so the provider's prompt cache
#: matches across the sweep. The six type definitions are worded to match
#: ``relation_hygiene.RELATION_VALIDITY_INSTRUCTIONS``' own, deliberately: the validity
#: pass judges these same six shapes on every other sense in the store, and a proposal
#: this call writes is judged by that same yardstick on relation-hygiene's next sweep.
RELATION_REGEN_INSTRUCTIONS = """\
You are filling in relations for one dictionary sense that currently has none at all —
every relation it used to have was judged untrue by an earlier pass and removed. You are
given the headword, its part of speech, the sense's own definition, one example sentence,
the headword's other senses (so you do not propose a term that actually belongs to one of
them), and the sense's subject domain where one is recorded. Propose up to six new
relations that are true of THIS sense specifically.

Use only these six relation types, and nothing else:

- "synonym": the target means roughly the same thing as this sense, closely enough that
either could stand in for the other in an ordinary sentence without changing what is
meant.
- "antonym": the target is the opposite of this sense along one clear axis. A different
member of the same set is not an antonym, and neither is a term that simply lacks the
quality.
- "hypernym": the target names a broader category this sense belongs to — "this sense IS
A KIND OF the target" must read as true.
- "hyponym": the reverse — "the target IS A KIND OF this sense" must read as true. A
description of this sense with a modifier stuck on the front is not a hyponym; it is the
same thing again, and there is no second word there.
- "meronym": the target is a part, member or substance of this sense.
- "holonym": the reverse — the target is the whole this sense is a part of.

Every target must be a real, independently look-up-able word or short fixed phrase — not
an inflected form of the headword (a plural, a past tense), not the headword with a
modifier attached, and not a label about words ("slang term", "plural form"). If you
cannot find a genuine target for a type, leave that type out rather than stretching for
one; an empty answer for a type is better than a wrong one, and returning fewer than six
relations is expected and fine.

You may be shown a list of terms already judged wrong for this exact sense by an earlier
pass. Do not propose any of them again, under any type: they have already been tried and
rejected, and returning the same term does not make it true.

For every relation you propose, give a one-clause justification: the plainest possible
statement of why the claim holds, in the register of a dictionary editor's note to
another editor, not a sentence written for a reader. Answer with only the relations you
are confident in; there is no reward for filling all six slots."""


def _build_prompt(headword: str, slot: _RegenSlot) -> str:
    """Return the volatile half of the regeneration prompt for one empty sense.

    Args:
        headword: The entry's surface form.
        slot: The sense being filled and its discrimination context.

    Returns:
        The per-call prompt body.
    """
    lines = [
        f"Headword: {headword}",
        f"Part of speech: {slot.pos_entry.pos.value}",
        f"Sense: {slot.gloss}",
        f"Example: {slot.example}",
    ]
    if slot.domain:
        lines.append(f"Domain: {slot.domain}")
    if slot.other_senses:
        lines.append(f"Other senses of {headword!r} — do not use a term that belongs here:")
        lines.extend(f"  [{pos}] {gloss}" for pos, gloss in slot.other_senses)
    if slot.rejected_display:
        lines.append("Already judged wrong for this sense; do not propose these again:")
        lines.extend(f"  - {term}" for term in slot.rejected_display)
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# Rejected-target collection
# --------------------------------------------------------------------------------------


def _safe_slug(term: str) -> str | None:
    """Return ``term``'s slug, or ``None`` when it has no slug-able content."""
    try:
        return slugify(term)
    except ValueError:
        return None


def _parse_removal_term(prefix: str, line: str) -> str | None:
    """Return the target term named by one ``relation_reconcile`` removal line.

    Args:
        prefix: :data:`~opengloss_generator.workflows.relation_reconcile.TOMBSTONE_LINE_PREFIX`.
        line: One line of a removal record's note, as written by
            ``relation_reconcile._removal_line``: ``<prefix><type> -> <term> [<note>]``.

    Returns:
        The term, or ``None`` if the line does not match the expected shape.
    """
    if not line.startswith(prefix):
        return None
    remainder = line[len(prefix) :]
    _type, sep, rest = remainder.partition(" -> ")
    if not sep:
        return None
    term, _, _ = rest.rpartition(" [")
    term = (term or rest).strip()
    return term or None


@dataclass(frozen=True, slots=True)
class _Rejected:
    """Targets already judged wrong for one sense.

    Attributes:
        terms: Original-cased terms, deduplicated by slug, in first-seen order — what the
            prompt shows.
        slugs: The same set, slugified — what the post-check compares proposals against.
    """

    terms: list[str]
    slugs: frozenset[str]


def _rejected_targets(entry: Lexeme, sense: Sense, sense_id: str) -> _Rejected:
    """Return every target a hygiene pass has already rejected for one sense.

    Two sources, both read-only: a live relation on the sense itself carrying a hygiene
    demotion note (not expected on a sense with zero relations, but checked for
    correctness independent of that precondition), and
    ``relation_reconcile``'s ``tombstone`` records naming this sense id — the edges a
    hygiene demotion earned and ``tombstone`` then took out of the list. ``cap``'s
    removals are deliberately not read here: a relation ``cap`` trimmed was never judged
    untrue, only crowded out, and re-proposing it is not the mistake this pass exists to
    prevent.

    Args:
        entry: The entry the sense belongs to.
        sense: The sense being filled.
        sense_id: Its derived id, which keys the tombstone records.

    Returns:
        The rejected set, for the prompt and the post-check alike.
    """
    terms: list[str] = []
    slugs: set[str] = set()

    def add(term: str) -> None:
        slug = _safe_slug(term)
        if slug is None or slug in slugs:
            return
        slugs.add(slug)
        terms.append(term)

    for relation in sense.relations:
        if relation.type is RelationType.SEE_ALSO and is_demotion_note(relation.note):
            add(relation.target.term)

    header = f"{TOMBSTONE_RECORD_PREFIX}{sense_id}"
    for record in entry.provenance.values():
        note = record.note or ""
        if not note.startswith(header):
            continue
        lines = note.split("\n")
        if lines[0] != header:
            continue
        for line in lines[1:]:
            term = _parse_removal_term(TOMBSTONE_LINE_PREFIX, line)
            if term is not None:
                add(term)

    return _Rejected(terms=terms, slugs=frozenset(slugs))


# --------------------------------------------------------------------------------------
# Planning: which senses, and the D-47 marker
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class _RegenSlot:
    """One live sense with zero relations, and everything filling it needs.

    Attributes:
        sense_id: The sense's derived id.
        pos_entry: The owning part-of-speech entry, for its part-of-speech label.
        sense: The sense itself, mutated when a relation is accepted for it.
        gloss: Its canonical definition.
        example: One example it holds, or :data:`_NO_EXAMPLE` when it holds none.
        other_senses: ``(part of speech, gloss)`` of every other live sense of the same
            entry, for the discrimination context the prompt gives.
        domain: The sense's own domain tag, falling back to its ``domain_hint``, or
            ``None`` when neither is set.
        rejected_display: Terms already judged wrong for this sense, for the prompt.
        rejected_slugs: The same set, slugified, for the post-check.
        gloss_digest: The D-47 marker's key.
    """

    sense_id: str
    pos_entry: POSEntry
    sense: Sense
    gloss: str
    example: str
    other_senses: list[tuple[str, str]]
    domain: str | None
    rejected_display: list[str]
    rejected_slugs: frozenset[str]
    gloss_digest: str


def _gloss_digest(gloss: str) -> str:
    """Return a stable short hash of a sense's canonical gloss text.

    Args:
        gloss: The canonical gloss.

    Returns:
        Sixteen hex characters of SHA-256, so the marker changes exactly when the gloss a
        later pass rewrote does, and never otherwise.
    """
    return hashlib.sha256(gloss.encode("utf-8")).hexdigest()[:16]


def _slots(entry: Lexeme) -> list[_RegenSlot]:
    """Return one slot per live sense of an entry that currently holds no relations.

    Args:
        entry: The entry to inspect. Never mutated.

    Returns:
        The slots, in document order.
    """
    live = [
        (pos_entry, sense, sid)
        for pos_entry, sense, sid in entry.iter_senses()
        if not sense.retired
    ]
    slots: list[_RegenSlot] = []
    for pos_entry, sense, sense_id in live:
        if sense.relations:
            continue
        others = [
            (other_pos.pos.value, other_sense.canonical_gloss())
            for other_pos, other_sense, other_id in live
            if other_id != sense_id
        ]
        canonical = sense.examples.canonical()
        if canonical is not None:
            example = canonical.content.text
        elif len(sense.examples):
            example = sense.examples[0].content.text
        else:
            example = _NO_EXAMPLE
        gloss = sense.canonical_gloss()
        rejected = _rejected_targets(entry, sense, sense_id)
        domain = sense.domain.value if sense.domain is not None else sense.domain_hint
        slots.append(
            _RegenSlot(
                sense_id=sense_id,
                pos_entry=pos_entry,
                sense=sense,
                gloss=gloss,
                example=" ".join(example.split()),
                other_senses=others,
                domain=domain,
                rejected_display=rejected.terms,
                rejected_slugs=rejected.slugs,
                gloss_digest=_gloss_digest(gloss),
            )
        )
    return slots


@dataclass(frozen=True, slots=True)
class _Marker:
    """The most recent D-47 sentinel this pass left for one sense.

    Attributes:
        digest: The gloss digest the marker was written for.
        attempts: How many attempts this pass has made on this sense, this one included.
    """

    digest: str
    attempts: int


def _marker_prefix(sense_id: str) -> str:
    """Return the note prefix that keys markers to one sense."""
    return f"{MARKER_PREFIX}:{sense_id}"


def _latest_marker(entry: Lexeme, sense_id: str) -> _Marker | None:
    """Return the last marker this pass wrote for one sense, parsed.

    Args:
        entry: The entry to inspect.
        sense_id: The sense whose marker is wanted.

    Returns:
        The most recent marker, or ``None`` if this pass has never visited the sense.
        Provenance ids are assigned in insertion order and never reused, so the last
        matching record in document order is the most recently written one.
    """
    prefix = f"{_marker_prefix(sense_id)}:"
    latest: _Marker | None = None
    for record in entry.provenance_in_order():
        note = record.note or ""
        if not note.startswith(prefix):
            continue
        digest, _, attempts = note[len(prefix) :].partition(_ATTEMPTS_SEPARATOR)
        latest = _Marker(digest, int(attempts) if attempts.isdigit() else 1)
    return latest


def _attempt_number(entry: Lexeme, sense_id: str, digest: str) -> int | None:
    """Return which attempt is due on a sense, or ``None`` if none is.

    A sense is due an attempt when this pass has never visited it, or its gloss has
    changed since this pass last answered — and it has not already had
    :data:`MAX_ATTEMPTS` of them (D-47).

    Args:
        entry: The entry the sense belongs to.
        sense_id: The sense being considered.
        digest: The sense's current gloss digest.

    Returns:
        The 1-based attempt number, or ``None`` when the sense must be skipped — which is
        also the "do not bill this" signal for the caller.
    """
    marker = _latest_marker(entry, sense_id)
    if marker is None:
        return 1
    if marker.digest == digest or marker.attempts >= MAX_ATTEMPTS:
        return None
    return marker.attempts + 1


def _marker_note(sense_id: str, digest: str, attempt: int) -> str:
    """Return the sentinel to stamp for an attempt, in D-47's form."""
    return f"{_marker_prefix(sense_id)}:{digest}{_ATTEMPTS_SEPARATOR}{attempt}"


@dataclass(slots=True)
class RelationRegenPlan:
    """What one entry would cost this workflow, computed without a model call.

    Attributes:
        senses_due: How many of the entry's zero-relation senses would actually be
            visited this sweep — excludes any already at :data:`MAX_ATTEMPTS` for an
            unchanged gloss.
    """

    senses_due: int = 0

    @property
    def due(self) -> bool:
        """Return whether this entry would cost anything at all."""
        return self.senses_due > 0


def plan_relation_regen(entry: Lexeme) -> RelationRegenPlan:
    """Return how many of an entry's empty senses would actually be filled, for free.

    Args:
        entry: The entry to inspect. Never mutated.

    Returns:
        A :class:`RelationRegenPlan`. ``due`` is ``False`` when a sweep would spend
        nothing on this entry — either it has no zero-relation senses, or every one of
        them has already exhausted its attempts on an unchanged gloss.
    """
    due = sum(
        1
        for slot in _slots(entry)
        if _attempt_number(entry, slot.sense_id, slot.gloss_digest) is not None
    )
    return RelationRegenPlan(senses_due=due)


# --------------------------------------------------------------------------------------
# Applying one call's answer
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class _ApplyCounts:
    """What happened to one call's proposed relations.

    Attributes:
        accepted_by_type: How many relations of each type were written, keyed by
            :class:`~opengloss_generator.schema.RelationType` value.
        dropped_self: Proposals whose target was the entry's own headword.
        dropped_rejected: Proposals matching a term already judged wrong for this sense —
            the pilot's headline number.
        dropped_duplicate: Proposals repeating an earlier one in the same answer.
        dropped_capped: Proposals dropped only because their type had already reached its
            reconcile cap within this one answer.
        dropped_unusable: Proposals whose term had no slug-able content at all.
    """

    accepted_by_type: dict[str, int] = field(default_factory=dict)
    dropped_self: int = 0
    dropped_rejected: int = 0
    dropped_duplicate: int = 0
    dropped_capped: int = 0
    dropped_unusable: int = 0

    @property
    def accepted(self) -> int:
        """Return the total number of relations accepted."""
        return sum(self.accepted_by_type.values())


def _apply(
    entry: Lexeme,
    slot: _RegenSlot,
    drafted: Sequence[_DraftRegenRelation],
    provenance_id: str,
) -> tuple[list[Relation], _ApplyCounts]:
    """Turn one call's proposals into stored relations, applying every free post-check.

    Args:
        entry: The entry the sense belongs to, read only for its own lexeme id.
        slot: The sense being filled.
        drafted: The model's proposals, in the order it returned them.
        provenance_id: The entry's record for this call.

    Returns:
        ``(accepted relations, counts)``. The relations are not yet attached to the
        sense — the caller does that once it has decided whether to write the entry.
    """
    counts = _ApplyCounts()
    accepted: list[Relation] = []
    seen: set[tuple[RelationType, str]] = set()
    caps = RelationCaps()

    for proposal in drafted:
        relation_type = RelationType(proposal.type.value)
        try:
            target = RelationTarget(term=proposal.term)
        except ValueError:
            counts.dropped_unusable += 1
            continue
        slug = target.lexeme_id
        if slug == entry.lexeme_id:
            counts.dropped_self += 1
            continue
        if slug in slot.rejected_slugs:
            counts.dropped_rejected += 1
            continue
        key = (relation_type, slug)
        if key in seen:
            counts.dropped_duplicate += 1
            continue
        already = counts.accepted_by_type.get(relation_type.value, 0)
        if already >= caps.for_type(relation_type):
            counts.dropped_capped += 1
            continue
        seen.add(key)
        counts.accepted_by_type[relation_type.value] = already + 1
        accepted.append(
            Relation(
                type=relation_type,
                target=target,
                note=f"regen: {proposal.justification.strip()}",
                provenance_id=provenance_id,
            )
        )
    return accepted, counts


# --------------------------------------------------------------------------------------
# The outcome and tally
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class RelationRegenOutcome:
    """What one :func:`run_relation_regen` sweep did, and what it cost.

    Attributes:
        entries_scanned: Entries visited.
        entries_changed: Entries that gained at least one relation.
        senses_scanned: Zero-relation senses actually visited (an attempt was due).
        senses_filled: Of those, how many gained at least one relation.
        calls: Model calls made.
        relations_proposed: Relations the model returned, before any post-check.
        relations_accepted: Relations written, after every post-check.
        accepted_by_type: :attr:`relations_accepted`, split by relation type.
        dropped_self: Proposals dropped for naming the entry's own headword.
        dropped_rejected: Proposals dropped for matching a target a hygiene pass already
            rejected for this sense — how often the model reached for a tombstoned term.
        dropped_duplicate: Proposals dropped as an in-call repeat.
        dropped_capped: Proposals dropped only for exceeding their type's reconcile cap.
        dropped_unusable: Proposals dropped for an unusable type or term.
        cost_usd: Total spend.
        stopped_reason: ``"budget"`` or ``"stopped"``, or ``None`` if the sweep ran to
            the end.
    """

    entries_scanned: int = 0
    entries_changed: int = 0
    senses_scanned: int = 0
    senses_filled: int = 0
    calls: int = 0
    relations_proposed: int = 0
    relations_accepted: int = 0
    accepted_by_type: dict[str, int] = field(default_factory=dict)
    dropped_self: int = 0
    dropped_rejected: int = 0
    dropped_duplicate: int = 0
    dropped_capped: int = 0
    dropped_unusable: int = 0
    cost_usd: float = 0.0
    stopped_reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable view for the CLI run summary."""
        return {
            "entries_scanned": self.entries_scanned,
            "entries_changed": self.entries_changed,
            "senses_scanned": self.senses_scanned,
            "senses_filled": self.senses_filled,
            "senses_still_empty": self.senses_scanned - self.senses_filled,
            "calls": self.calls,
            "relations_proposed": self.relations_proposed,
            "relations_accepted": self.relations_accepted,
            "accepted_by_type": dict(sorted(self.accepted_by_type.items())),
            "dropped_self": self.dropped_self,
            "dropped_rejected": self.dropped_rejected,
            "dropped_duplicate": self.dropped_duplicate,
            "dropped_capped": self.dropped_capped,
            "dropped_unusable": self.dropped_unusable,
            "cost_usd": round(self.cost_usd, 6),
            "stopped_reason": self.stopped_reason,
        }


class _Tally:
    """One sweep's counters, mutated only while holding an ``asyncio.Lock``.

    Mirrors ``examples.py``'s own ``_Tally`` and every sweep that followed
    ``retrofit.py``'s: many handlers touch these counters around many awaits, and
    single-threaded asyncio only makes one await-free statement atomic, not a whole
    read-modify-write spanning one.
    """

    def __init__(self) -> None:
        """Start an empty outcome."""
        self._lock = asyncio.Lock()
        self._result = RelationRegenOutcome()

    @property
    def result(self) -> RelationRegenOutcome:
        """Return the accumulated outcome; read it once the pool has drained."""
        return self._result

    async def entry(self, *, changed: bool) -> None:
        """Fold one visited entry into the outcome."""
        async with self._lock:
            self._result.entries_scanned += 1
            if changed:
                self._result.entries_changed += 1
            if self._result.entries_scanned % PROGRESS_EVERY == 0:
                _LOG.info(
                    "relation_regen_progress",
                    entries_scanned=self._result.entries_scanned,
                    senses_filled=self._result.senses_filled,
                    cost_usd=round(self._result.cost_usd, 6),
                )

    async def call(self, cost_usd: float) -> None:
        """Record one completed model call and what it cost."""
        async with self._lock:
            self._result.calls += 1
            self._result.cost_usd += cost_usd

    async def sense(self, *, proposed: int, counts: _ApplyCounts) -> None:
        """Record one sense's call outcome."""
        async with self._lock:
            result = self._result
            result.senses_scanned += 1
            result.relations_proposed += proposed
            result.relations_accepted += counts.accepted
            for relation_type, n in counts.accepted_by_type.items():
                result.accepted_by_type[relation_type] = (
                    result.accepted_by_type.get(relation_type, 0) + n
                )
            result.dropped_self += counts.dropped_self
            result.dropped_rejected += counts.dropped_rejected
            result.dropped_duplicate += counts.dropped_duplicate
            result.dropped_capped += counts.dropped_capped
            result.dropped_unusable += counts.dropped_unusable
            if counts.accepted:
                result.senses_filled += 1

    async def note_stop(self, reason: str) -> None:
        """Record why the sweep stopped early, keeping the first reason given."""
        async with self._lock:
            if self._result.stopped_reason is None:
                self._result.stopped_reason = reason


# --------------------------------------------------------------------------------------
# Filling one entry, and the sweep
# --------------------------------------------------------------------------------------


async def _fill_sense(
    entry: Lexeme,
    slot: _RegenSlot,
    runner: StageRunner,
    tally: _Tally,
) -> bool:
    """Fill one empty sense, in place, if it is due an attempt.

    Args:
        entry: The entry the sense belongs to, mutated in place.
        slot: The sense to fill.
        runner: The stage runner.
        tally: The sweep tally.

    Returns:
        Whether the entry needs to be written back because of this sense.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates,
            before anything from this sense is written.
    """
    attempt = _attempt_number(entry, slot.sense_id, slot.gloss_digest)
    if attempt is None:
        return False

    try:
        result = await runner.run(
            stage=StageName.SENSES,
            output_type=_DraftRegenRelations,
            instructions=RELATION_REGEN_INSTRUCTIONS,
            prompt=_build_prompt(entry.headword, slot),
            prompt_version=PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        # A failed call writes no marker, so the sense is retried whole on the next
        # sweep — the convention every model call in this project follows.
        _LOG.warning("relation_regen_call_failed", headword=entry.headword, error=str(exc))
        return False

    await tally.call(result.cost_usd)
    marker = _marker_note(slot.sense_id, slot.gloss_digest, attempt)
    provenance_id = entry.add_provenance(result.provenance.model_copy(update={"note": marker}))
    accepted, counts = _apply(entry, slot, result.output.relations, provenance_id)
    slot.sense.relations.extend(accepted)
    await tally.sense(proposed=len(result.output.relations), counts=counts)
    _LOG.info(
        "relation_regen_sense",
        headword=entry.headword,
        sense_id=slot.sense_id,
        proposed=len(result.output.relations),
        accepted=len(accepted),
        rejected_hits=counts.dropped_rejected,
        cost_usd=round(result.cost_usd, 6),
    )
    # The marker rides this call's own provenance record, so it is written even when
    # nothing was accepted: the marker is the only thing the call bought, and losing it
    # re-bills the same answer on the next sweep.
    return True


async def _fill_entry(entry: Lexeme, runner: StageRunner, tally: _Tally) -> bool:
    """Fill every empty sense of one entry that is due an attempt.

    Args:
        entry: The entry to fill, mutated in place.
        runner: The stage runner.
        tally: The sweep tally.

    Returns:
        Whether the entry needs to be written back.

    Raises:
        BudgetExceededError: Propagates from :func:`_fill_sense`.
    """
    needs_write = False
    for slot in _slots(entry):
        if await _fill_sense(entry, slot, runner, tally):
            needs_write = True
    return needs_write


async def run_relation_regen(
    store: LexemeStore,
    runner: StageRunner,
    *,
    lexeme_ids: Iterable[str] | None = None,
    workers: int,
    stop_event: asyncio.Event | None = None,
) -> RelationRegenOutcome:
    """Regenerate relations for every live sense in the store that currently has none.

    Args:
        store: The store to fill. Each entry is read, filled — including one model call
            per empty sense it still has, made sequentially within the entry's own lock —
            and written back inside one hold of that lock (D-31).
        runner: The stage runner; ``StageName.SENSES``' policy prices every call.
        lexeme_ids: Ids to visit; defaults to every id in the store, sorted.
        workers: Pool size.
        stop_event: Shared stop event; set by a budget stop, and honoured if the caller
            sets it from outside.

    Returns:
        A :class:`RelationRegenOutcome`. A sweep that stopped early still returns its
        outcome, with ``stopped_reason`` set, so a partial sweep reports what it managed
        to do. Filled senses are not "done": resolve and relation-hygiene still need to
        run over the store again to resolve and judge what this pass wrote (see the
        module docstring).
    """
    ids = list(lexeme_ids) if lexeme_ids is not None else sorted(store.iter_ids())
    tally = _Tally()

    async def fill(lexeme_id: str) -> None:
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            changed = await _fill_entry(entry, runner, tally)
            if changed:
                store.write(entry)
        await tally.entry(changed=changed)

    async def guarded(lexeme_id: str) -> None:
        try:
            await fill(lexeme_id)
        except BudgetExceededError:
            await tally.note_stop("budget")
            raise

    await run_pool(ids, guarded, workers=workers, stop_event=stop_event)
    if stop_event is not None and stop_event.is_set():
        await tally.note_stop("stopped")

    result = tally.result
    _LOG.info("relation_regen_complete", **result.as_dict())
    return result
