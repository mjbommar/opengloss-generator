"""Example 5: bring a migrated store up to the v3 contract.

Each pass does the free work first and is idempotent, so a sweep over an already-current
store costs nothing.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.function import FunctionModel

from opengloss_generator.config import AppConfig, ConcurrencyConfig, StoreConfig
from opengloss_generator.hygiene import is_headword_initial, is_near_copy
from opengloss_generator.prompts import build_classify_kind_prompt
from opengloss_generator.readability import flesch_kincaid_grade
from opengloss_generator.runner import RunSession
from opengloss_generator.schema import (
    Assessment,
    EntityType,
    Example,
    Lexeme,
    LexemeKind,
    Morphology,
    PartOfSpeech,
    POSEntry,
    ProperNounInfo,
    Provenance,
    QAFlag,
    ReadingLevel,
    Register,
    Relation,
    RelationTarget,
    RelationType,
    Rendition,
    Renditions,
    Sense,
    StageName,
    canonical_rendition,
)
from opengloss_generator.store import LexemeStore
from opengloss_generator.taxonomy import TAXONOMY_VERSION, DomainTag
from opengloss_generator.workflows import retrofit
from opengloss_generator.workflows.retrofit import (
    EVIDENCE_SNIPPET_CHARS,
    RetrofitPass,
    _residue_snippet,
    run_retrofit,
)
from tests.conftest import (
    NO_SPAN_HEADWORD,
    READABILITY_FIX_HEADWORD,
    READABILITY_FIX_TEMPLATE,
    READABILITY_INITIAL_HEADWORD,
    READABILITY_INITIAL_TEXT,
    READABILITY_LOSES_HEADWORD,
    SCRIPTED_RETROFIT_DOMAIN,
    make_entry,
)


def _entry_with_sense_examples(headword: str, renditions: list[Rendition[Example]]) -> Lexeme:
    """Build a one-sense noun entry holding exactly the given example renditions."""
    sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition("A finishing process for paper.")]),
        examples=Renditions[Example](root=list(renditions)),
    )
    return Lexeme.empty(
        headword,
        kind=LexemeKind.SIMPLEX,
        pos_entries=[POSEntry(pos=PartOfSpeech.NOUN, senses=[sense], morphology=Morphology())],
    )


def _entry_with_examples(headword: str, texts: list[str]) -> Lexeme:
    """Build an entry whose examples have no spans yet."""
    sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition("To descend a rock face using a rope.")]),
        examples=Renditions[Example](
            root=[canonical_rendition(Example(text=text)) for text in texts]
        ),
    )
    return Lexeme.empty(
        headword,
        kind=LexemeKind.SIMPLEX,
        pos_entries=[POSEntry(pos=PartOfSpeech.VERB, senses=[sense], morphology=Morphology())],
    )


async def test_classify_kind_prefers_rules_and_reports_the_ratio(session):
    session.store.write(make_entry("abseil"))  # rules say simplex
    session.store.write(make_entry("Einstein"))  # rules say proper noun
    session.store.write(make_entry("kick the bucket"))  # ambiguous: goes to the model

    outcome = await run_retrofit(session.store, session.stages, only=[RetrofitPass.CLASSIFY_KIND])
    result = outcome.passes["classify_kind"]

    assert result.entries_scanned == 3
    assert result.metrics["deterministic"] == 2
    assert result.metrics["residue"] == 1
    assert result.metrics["deterministic_ratio"] == pytest.approx(2 / 3)
    assert result.calls == 1  # one batched call for the whole residue

    assert session.store.read("abseil").kind is LexemeKind.SIMPLEX
    named = session.store.read("einstein")
    assert named.kind is LexemeKind.PROPER_NOUN
    # Neither the rules nor the batch contract carry an entity type, so a promotion to
    # proper noun gets "other" for a later pass to refine.
    assert named.proper_noun is not None
    assert named.proper_noun.entity_type is EntityType.OTHER
    assert session.store.read("kick_the_bucket").kind is LexemeKind.IDIOM


async def test_classify_kind_is_idempotent_and_free_on_a_second_sweep(session):
    session.store.write(make_entry("abseil"))
    session.store.write(make_entry("kick the bucket"))

    await run_retrofit(session.store, session.stages, only=[RetrofitPass.CLASSIFY_KIND])
    spent = session.meter.summary().total_usd

    again = await run_retrofit(session.store, session.stages, only=[RetrofitPass.CLASSIFY_KIND])
    result = again.passes["classify_kind"]

    assert result.entries_scanned == 0
    assert result.calls == 0
    assert result.cost_usd == 0.0
    assert session.meter.summary().total_usd == spent
    assert session.store.read("kick_the_bucket").kind is LexemeKind.IDIOM


async def test_tag_domain_only_fills_senses_that_have_none(session):
    untagged = make_entry("abseil")
    tagged = make_entry("rappel")
    tagged.pos_entries[0].senses[0].domain = DomainTag.SPORTS_RECREATION_GENERAL
    session.store.write(untagged)
    session.store.write(tagged)

    outcome = await run_retrofit(session.store, session.stages, only=[RetrofitPass.TAG_DOMAIN])
    result = outcome.passes["tag_domain"]

    assert result.entries_scanned == 2
    assert result.calls == 1  # only the untagged entry was sent
    assert result.items_changed == 1
    assert session.store.read("abseil").pos_entries[0].senses[0].domain == DomainTag(
        SCRIPTED_RETROFIT_DOMAIN
    )
    # The already-tagged entry keeps its tag and was never sent.
    assert (
        session.store.read("rappel").pos_entries[0].senses[0].domain
        is DomainTag.SPORTS_RECREATION_GENERAL
    )


async def test_tag_domain_is_free_on_a_second_sweep(session):
    session.store.write(make_entry("abseil"))
    await run_retrofit(session.store, session.stages, only=[RetrofitPass.TAG_DOMAIN])
    spent = session.meter.summary().total_usd

    again = await run_retrofit(session.store, session.stages, only=[RetrofitPass.TAG_DOMAIN])
    assert again.passes["tag_domain"].calls == 0
    assert session.meter.summary().total_usd == spent


async def test_spans_pass_places_what_it_can_for_free(session):
    session.store.write(
        _entry_with_examples("abseil", ["They abseiled down the cliff.", "Nothing matches here."])
    )

    outcome = await run_retrofit(session.store, session.stages, only=[RetrofitPass.SPANS])
    result = outcome.passes["spans"]

    assert result.metrics["deterministic"] == 1  # the inflected form is found by rule
    assert result.metrics["by_model"] == 1  # the other reaches the fallback
    assert result.calls == 1

    stored = session.store.read("abseil")
    examples = [r.content for r in stored.pos_entries[0].senses[0].examples]
    assert all(example.span is not None for example in examples)
    assert next(e for e in examples if "abseiled" in e.text).matched == "abseiled"


async def test_spans_pass_does_not_re_bill_the_model_fallback(session):
    session.store.write(_entry_with_examples("abseil", ["Nothing matches here."]))
    await run_retrofit(session.store, session.stages, only=[RetrofitPass.SPANS])
    spent = session.meter.summary().total_usd

    again = await run_retrofit(session.store, session.stages, only=[RetrofitPass.SPANS])
    assert again.passes["spans"].calls == 0
    assert session.meter.summary().total_usd == spent


async def test_all_passes_run_by_default_and_report_counts_and_cost(session):
    session.store.write(_entry_with_examples("ice axe", ["Nothing matches here."]))

    outcome = await run_retrofit(session.store, session.stages)

    assert set(outcome.passes) == set(RetrofitPass.ALL)
    assert outcome.counts()["classify_kind"] == 1  # simplex -> compound
    assert outcome.counts()["tag_domain"] == 1
    assert outcome.counts()["spans"] == 1
    assert outcome.cost_usd == pytest.approx(session.meter.summary().total_usd)
    assert outcome.calls == 3


async def test_an_unknown_pass_name_is_rejected(session):
    with pytest.raises(ValueError, match="unknown retrofit pass"):
        await run_retrofit(session.store, session.stages, only=["nonsense"])


# --------------------------------------------------------------------------------------
# D-26: the evidence rule inside the pass
# --------------------------------------------------------------------------------------


def _lowercased_proper_noun(headword: str) -> Lexeme:
    """Build a migrated-looking entry: a lowercase headword whose prose names a place."""
    return Lexeme.empty(
        headword,
        kind=LexemeKind.SIMPLEX,  # what migration guessed before D-26
        pos_entries=[
            POSEntry(
                pos=PartOfSpeech.NOUN,
                senses=[Sense.of(0, f"The capital city of England, {headword.title()}.")],
                morphology=Morphology(),
            )
        ],
        encyclopedia=Renditions[str](
            root=[
                canonical_rendition(
                    f"The capital, {headword.title()}, grew along the Thames. Rail links "
                    f"reach {headword.title()} in an hour."
                )
            ]
        ),
    )


async def test_classify_kind_re_examines_a_migrated_entry_and_corrects_it_for_free(session):
    # Migration writes no ``classify_kind`` marker, so the pass revisits the entry and
    # the evidence rule promotes it — no model call, no money.
    session.store.write(_lowercased_proper_noun("london"))

    outcome = await run_retrofit(session.store, session.stages, only=[RetrofitPass.CLASSIFY_KIND])
    result = outcome.passes["classify_kind"]

    assert result.entries_scanned == 1
    assert result.metrics["residue"] == 0
    assert result.calls == 0
    assert result.cost_usd == 0.0
    assert session.meter.summary().total_usd == 0.0

    corrected = session.store.read("london")
    assert corrected.kind is LexemeKind.PROPER_NOUN
    assert corrected.proper_noun is not None
    assert corrected.proper_noun.entity_type is EntityType.OTHER

    # And the marker it just wrote keeps the second sweep from re-deciding it (D-21).
    again = await run_retrofit(session.store, session.stages, only=[RetrofitPass.CLASSIFY_KIND])
    assert again.passes["classify_kind"].entries_scanned == 0


def test_the_residue_prompt_carries_one_short_gloss_per_term():
    long_gloss = "x" * (EVIDENCE_SNIPPET_CHARS + 50)
    prompt = build_classify_kind_prompt(
        [("einstein", "A unit of radiant energy."), ("give up", None), ("ice axe", long_gloss)]
    )

    assert "  1. einstein \u2014 A unit of radiant energy." in prompt
    assert "  2. give up\n" in prompt  # no snippet, no separator
    # The builder is handed an already-truncated snippet; nothing here re-truncates it,
    # so the caller's cap is what keeps the ~30-token-per-term budget.
    assert f"  3. ice axe \u2014 {long_gloss}" in prompt


def test_the_residue_snippet_is_the_first_gloss_and_is_capped():
    entry = make_entry("kick the bucket")
    entry.pos_entries[0].senses[0].gloss.root[0].content = "y" * (EVIDENCE_SNIPPET_CHARS + 40)

    snippet = _residue_snippet(entry)

    assert snippet == "y" * EVIDENCE_SNIPPET_CHARS
    assert _residue_snippet(Lexeme.empty("orphan")) is None


# --------------------------------------------------------------------------------------
# hygiene
# --------------------------------------------------------------------------------------


async def test_hygiene_strips_markdown_from_canonical_prose(session):
    entry = make_entry("abseil")
    entry.pos_entries[0].senses[0].gloss.root[
        0
    ].content = "**To descend** a rock face using a rope, per the *climbing* manual."
    entry.encyclopedia = Renditions[str](
        root=[canonical_rendition("# Abseiling\n\n- A `technique` used in **climbing**.")]
    )
    session.store.write(entry)

    outcome = await run_retrofit(session.store, session.stages, only=[RetrofitPass.HYGIENE])
    result = outcome.passes["hygiene"]

    assert result.metrics["markdown_stripped"] == 2
    assert result.calls == 0
    assert result.cost_usd == 0.0

    stored = session.store.read("abseil")
    assert (
        stored.pos_entries[0].senses[0].canonical_gloss()
        == "To descend a rock face using a rope, per the climbing manual."
    )
    assert stored.encyclopedia.canonical().content == "Abseiling\n\nA technique used in climbing."


async def test_hygiene_drops_artifact_relations(session):
    entry = make_entry("abseil")
    entry.pos_entries[0].senses[0].relations.append(
        Relation(type=RelationType.HYPERNYM, target=RelationTarget(term="transitive verb"))
    )
    session.store.write(entry)

    outcome = await run_retrofit(session.store, session.stages, only=[RetrofitPass.HYGIENE])
    result = outcome.passes["hygiene"]

    assert result.metrics["artifacts_dropped"] == 1
    assert result.calls == 0

    stored = session.store.read("abseil")
    targets = {r.target.term for r in stored.pos_entries[0].senses[0].relations}
    assert targets == {"rappel", "descend"}


async def test_hygiene_rewrites_headword_initial_glosses_and_keeps_the_old_one(session):
    entry = make_entry("abseil")
    old_gloss = "The word abseil refers to descending a rock face using a rope."
    entry.pos_entries[0].senses[0].gloss.root[0].content = old_gloss
    session.store.write(entry)

    outcome = await run_retrofit(session.store, session.stages, only=[RetrofitPass.HYGIENE])
    result = outcome.passes["hygiene"]

    assert result.metrics["glosses_rewritten"] == 1
    assert result.calls == 1
    assert result.cost_usd > 0.0

    stored = session.store.read("abseil")
    sense = stored.pos_entries[0].senses[0]
    assert sense.canonical_gloss() == "Scripted rewrite number 1 of the definition."

    hygiene_records = [p for p in stored.provenance.values() if p.stage is StageName.HYGIENE]
    assert any(p.note == old_gloss for p in hygiene_records)
    # The rewritten rendition points at the record that carries its own superseded text.
    canonical = sense.gloss.canonical()
    assert canonical is not None
    assert stored.provenance[canonical.provenance_id].note == old_gloss


async def test_hygiene_does_not_rewrite_a_proper_nouns_headword_initial_gloss(session):
    # Proper-noun definitions legitimately name their entity (CORE-DIARY iteration 2;
    # D-30) -- "The Congo River is a major central African river." is correct as-is, so
    # step (c) must not send it to the model or bill for it.
    sense = Sense.of(0, "The Congo River is a major central African river.")
    entry = Lexeme.empty(
        "Congo",
        kind=LexemeKind.PROPER_NOUN,
        proper_noun=ProperNounInfo(entity_type=EntityType.PLACE),
        pos_entries=[POSEntry(pos=PartOfSpeech.NOUN, senses=[sense], morphology=Morphology())],
    )
    session.store.write(entry)

    outcome = await run_retrofit(session.store, session.stages, only=[RetrofitPass.HYGIENE])
    result = outcome.passes["hygiene"]

    assert result.metrics["glosses_rewritten"] == 0
    assert result.calls == 0
    assert result.cost_usd == 0.0

    stored = session.store.read("congo")
    assert (
        stored.pos_entries[0].senses[0].canonical_gloss()
        == "The Congo River is a major central African river."
    )


async def test_hygiene_clears_a_general_domain_even_with_a_real_tag_domain_verdict(session):
    entry = make_entry("abseil")
    entry.pos_entries[0].senses[0].domain = DomainTag.SPORTS_RECREATION_GENERAL
    entry.add_provenance(Provenance(stage=StageName.TAG_DOMAIN, model="m", prompt_version="1"))
    session.store.write(entry)

    outcome = await run_retrofit(session.store, session.stages, only=[RetrofitPass.HYGIENE])
    result = outcome.passes["hygiene"]

    assert result.metrics["domains_cleared"] == 1
    assert result.calls == 0
    assert session.store.read("abseil").pos_entries[0].senses[0].domain is None


async def test_hygiene_clears_a_legacy_mapped_domain_that_was_never_model_verified(session):
    entry = make_entry("abseil")
    sense = entry.pos_entries[0].senses[0]
    sense.domain = DomainTag.SCIENCE_PHYSICS  # not .general
    sense.domain_hint = "physics"  # a v1.3 free-text domain, mapped by LEGACY_DOMAIN_MAP
    session.store.write(entry)

    outcome = await run_retrofit(session.store, session.stages, only=[RetrofitPass.HYGIENE])
    result = outcome.passes["hygiene"]

    assert result.metrics["domains_cleared"] == 1
    assert session.store.read("abseil").pos_entries[0].senses[0].domain is None


async def test_hygiene_leaves_a_properly_verified_specific_domain_alone(session):
    entry = make_entry("abseil")
    sense = entry.pos_entries[0].senses[0]
    sense.domain = DomainTag.SPORTS_RECREATION_OUTDOOR_RECREATION
    sense.domain_hint = "sports"
    entry.add_provenance(Provenance(stage=StageName.TAG_DOMAIN, model="m", prompt_version="1"))
    session.store.write(entry)

    outcome = await run_retrofit(session.store, session.stages, only=[RetrofitPass.HYGIENE])
    result = outcome.passes["hygiene"]

    assert result.metrics["domains_cleared"] == 0
    stored = session.store.read("abseil")
    assert stored.pos_entries[0].senses[0].domain is DomainTag.SPORTS_RECREATION_OUTDOOR_RECREATION


async def test_hygiene_pass_is_idempotent_and_free_on_a_second_sweep(session):
    entry = make_entry("abseil")
    entry.pos_entries[0].senses[0].gloss.root[
        0
    ].content = "**The word abseil** refers to descending a rock face using a rope."
    entry.pos_entries[0].senses[0].relations.append(
        Relation(type=RelationType.HYPERNYM, target=RelationTarget(term="transitive verb"))
    )
    entry.pos_entries[0].senses[0].domain = DomainTag.SPORTS_RECREATION_GENERAL
    session.store.write(entry)

    await run_retrofit(session.store, session.stages, only=[RetrofitPass.HYGIENE])
    spent = session.meter.summary().total_usd

    again = await run_retrofit(session.store, session.stages, only=[RetrofitPass.HYGIENE])
    result = again.passes["hygiene"]

    assert result.items_changed == 0
    assert result.calls == 0
    assert result.cost_usd == 0.0
    assert session.meter.summary().total_usd == spent


async def test_all_passes_run_by_default_includes_hygiene_in_order(session):
    session.store.write(make_entry("abseil"))

    outcome = await run_retrofit(session.store, session.stages)

    assert set(outcome.passes) == set(RetrofitPass.ALL)
    assert RetrofitPass.ALL == (
        RetrofitPass.CLASSIFY_KIND,
        RetrofitPass.HYGIENE,
        RetrofitPass.TAG_DOMAIN,
        RetrofitPass.SPANS,
        RetrofitPass.REPAIR,
        # D-47: the pass that rewrites prose runs before the pass that checks the form of
        # stored prose, never after it.
        RetrofitPass.READABILITY_HYGIENE,
        RetrofitPass.RENDITION_HYGIENE,
    )


# --------------------------------------------------------------------------------------
# D-37: repair — retire exact-duplicate senses, fill in missing canonical examples
# --------------------------------------------------------------------------------------


def _entry_with_senses(
    headword: str,
    glosses: list[str],
    *,
    pos: PartOfSpeech = PartOfSpeech.NOUN,
    with_examples: bool = True,
) -> Lexeme:
    """Build an entry with one part of speech carrying the given glosses.

    Each sense gets a placeholder canonical example already, unless ``with_examples`` is
    ``False`` -- a duplicate-retirement test wants senses that will not also trigger
    example generation.
    """

    def sense(index: int, gloss: str) -> Sense:
        examples = Renditions[Example](root=[])
        if with_examples:
            examples.add(canonical_rendition(Example(text=f"The {headword}, example {index}.")))
        return Sense(
            index=index, gloss=Renditions[str](root=[canonical_rendition(gloss)]), examples=examples
        )

    return Lexeme.empty(
        headword,
        kind=LexemeKind.SIMPLEX,
        pos_entries=[
            POSEntry(
                pos=pos,
                senses=[sense(i, gloss) for i, gloss in enumerate(glosses)],
                morphology=Morphology(),
            )
        ],
    )


async def test_repair_retires_a_later_duplicate_sense_in_the_same_pos(session):
    entry = _entry_with_senses("bank", ["A financial institution.", "a financial institution"])
    session.store.write(entry)

    outcome = await run_retrofit(session.store, session.stages, only=[RetrofitPass.REPAIR])
    result = outcome.passes["repair"]

    assert result.metrics["senses_retired"] == 1
    assert result.calls == 0  # both senses already have an example
    assert result.cost_usd == 0.0

    senses = session.store.read("bank").pos_entries[0].senses
    assert senses[0].retired is False
    assert senses[1].retired is True
    # Never deleted or renumbered (D-1).
    assert [s.index for s in senses] == [0, 1]
    assert senses[0].canonical_gloss() == "A financial institution."


async def test_repair_retires_a_duplicate_sense_across_parts_of_speech(session):
    noun_sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition("A financial institution.")]),
        examples=Renditions[Example](
            root=[canonical_rendition(Example(text="The bank, example 0."))]
        ),
    )
    verb_sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition("a financial institution")]),
        examples=Renditions[Example](
            root=[canonical_rendition(Example(text="The bank, example 1."))]
        ),
    )
    entry = Lexeme.empty(
        "bank",
        kind=LexemeKind.SIMPLEX,
        pos_entries=[
            POSEntry(pos=PartOfSpeech.NOUN, senses=[noun_sense], morphology=Morphology()),
            POSEntry(pos=PartOfSpeech.VERB, senses=[verb_sense], morphology=Morphology()),
        ],
    )
    session.store.write(entry)

    outcome = await run_retrofit(session.store, session.stages, only=[RetrofitPass.REPAIR])
    result = outcome.passes["repair"]

    assert result.metrics["senses_retired"] == 1
    stored = session.store.read("bank")
    assert stored.pos_entries[0].senses[0].retired is False  # noun: comes first
    assert stored.pos_entries[1].senses[0].retired is True  # verb: the later POS entry


async def test_repair_treats_a_trailing_period_as_the_same_gloss(session):
    entry = _entry_with_senses(
        "cat", ["A small domesticated animal", "A small domesticated animal."]
    )
    session.store.write(entry)

    await run_retrofit(session.store, session.stages, only=[RetrofitPass.REPAIR])

    senses = session.store.read("cat").pos_entries[0].senses
    assert senses[0].retired is False
    assert senses[1].retired is True


async def test_repair_leaves_distinct_senses_alone(session):
    entry = _entry_with_senses("bank", ["A financial institution.", "The edge of a river."])
    session.store.write(entry)

    outcome = await run_retrofit(session.store, session.stages, only=[RetrofitPass.REPAIR])
    result = outcome.passes["repair"]

    assert result.metrics["senses_retired"] == 0
    assert not any(s.retired for s in session.store.read("bank").pos_entries[0].senses)


async def test_repair_generates_examples_for_senses_that_have_none(session):
    entry = _entry_with_senses(
        "abseil", ["To descend a rock face using a rope."], with_examples=False
    )
    session.store.write(entry)

    outcome = await run_retrofit(session.store, session.stages, only=[RetrofitPass.REPAIR])
    result = outcome.passes["repair"]

    assert result.calls == 1
    assert result.cost_usd > 0.0
    assert result.metrics["examples_added"] == 1
    assert result.metrics["entries_needing_examples"] == 1

    examples = [r.content for r in session.store.read("abseil").pos_entries[0].senses[0].examples]
    assert len(examples) == 1
    assert examples[0].span is not None
    assert examples[0].matched is not None
    assert examples[0].matched.lower() == "abseil"


async def test_repair_keeps_an_unplaceable_sentence_with_no_span(session):
    # The scripted sentence for this headword never mentions it at all, which is how the
    # test observes find_span leave the span as None for the spans pass to retry later.
    entry = _entry_with_senses(NO_SPAN_HEADWORD, ["A made-up test sense."], with_examples=False)
    session.store.write(entry)

    await run_retrofit(session.store, session.stages, only=[RetrofitPass.REPAIR])

    stored = session.store.read(entry.lexeme_id)
    example = stored.pos_entries[0].senses[0].examples[0].content
    assert example.text == "Nothing here names the missing word at all."
    assert example.span is None


async def test_repair_does_not_call_the_model_for_senses_that_already_have_examples(session):
    entry = _entry_with_senses("abseil", ["To descend a rock face using a rope."])
    session.store.write(entry)

    outcome = await run_retrofit(session.store, session.stages, only=[RetrofitPass.REPAIR])
    result = outcome.passes["repair"]

    assert result.calls == 0
    assert result.cost_usd == 0.0
    assert result.metrics["examples_added"] == 0
    assert result.metrics["entries_needing_examples"] == 0


async def test_repair_is_idempotent_and_free_on_a_second_sweep(session):
    entry = _entry_with_senses(
        "abseil", ["To descend a rock face using a rope."], with_examples=False
    )
    session.store.write(entry)

    await run_retrofit(session.store, session.stages, only=[RetrofitPass.REPAIR])
    spent = session.meter.summary().total_usd
    before = session.store.read("abseil")

    again = await run_retrofit(session.store, session.stages, only=[RetrofitPass.REPAIR])
    result = again.passes["repair"]

    assert result.calls == 0
    assert result.cost_usd == 0.0
    assert result.metrics["senses_retired"] == 0
    assert result.metrics["examples_added"] == 0
    assert session.meter.summary().total_usd == spent

    after = session.store.read("abseil")
    after_examples = after.pos_entries[0].senses[0].examples.root
    before_examples = before.pos_entries[0].senses[0].examples.root
    assert after_examples == before_examples


async def test_all_passes_by_default_run_repair_last(session):
    entry = _entry_with_senses(
        "abseil",
        ["To descend a rock face using a rope.", "to descend a rock face using a rope"],
        with_examples=False,
    )
    session.store.write(entry)

    outcome = await run_retrofit(session.store, session.stages)

    assert list(outcome.passes) == list(RetrofitPass.ALL)
    result = outcome.passes["repair"]
    assert result.metrics["senses_retired"] == 1
    # The surviving sense had no example either, so repair's own call fills it in too.
    assert result.metrics["examples_added"] == 1


# --------------------------------------------------------------------------------------
# D-31: the passes run through the worker pool, and every entry is locked across its call
# --------------------------------------------------------------------------------------

#: Enough entries that a pool of 8 has something to overlap, and few enough that the
#: sequential half of the equality test stays quick.
_SWEEP_SIZE = 60

#: A ceiling the ``tag_domain`` sweep over :data:`_SWEEP_SIZE` entries reaches part way
#: through: roughly half the calls are affordable, the sixtieth is not. Held as a constant
#: because the tests assert "stopped part way", never a particular call count -- where
#: exactly the guard's in-flight reservations land the stop depends on the pool's
#: scheduling, which is the whole point of testing it.
_TIGHT_BUDGET_USD = 0.01


def _sweep_session(
    tmp_path: Path,
    model: FunctionModel,
    *,
    name: str,
    budget_usd: float = 5.0,
) -> RunSession:
    """Return a session over its own temporary store, so two runs cannot share state."""
    return RunSession(
        AppConfig(
            store=StoreConfig(root=tmp_path / name, fsync_on_write=False),
            concurrency=ConcurrencyConfig(workers=4, requests_per_minute=100_000),
            log_dir=tmp_path / "runs",
            budget_usd=budget_usd,
        ),
        model_override=model,
        run_id="test-run",
    )


def _seed_sweep(store: LexemeStore, count: int = _SWEEP_SIZE) -> None:
    """Write entries that every pass has something to do to.

    Each gloss carries markdown and begins with its own headword (hygiene steps (a) and
    (c)), each sense is untagged (``tag_domain``), and the one example never contains the
    headword, so it reaches the ``spans`` model fallback.
    """
    for index in range(count):
        headword = f"testword{index:03d}"
        sense = Sense(
            index=0,
            gloss=Renditions[str](
                root=[canonical_rendition(f"{headword} is a **test** thing, number {index}.")]
            ),
            examples=Renditions[Example](
                root=[canonical_rendition(Example(text=f"Nothing matches here, number {index}."))]
            ),
        )
        store.write(
            Lexeme.empty(
                headword,
                kind=LexemeKind.SIMPLEX,
                pos_entries=[
                    POSEntry(pos=PartOfSpeech.NOUN, senses=[sense], morphology=Morphology())
                ],
            )
        )


def _stored_content(store: LexemeStore) -> dict[str, object]:
    """Return exactly what the passes are responsible for, entry by entry.

    Timestamps and provenance ids are excluded deliberately: they differ between any two
    runs, and none of them is content a pass decided.
    """
    snapshot: dict[str, object] = {}
    for lexeme_id in sorted(store.iter_ids()):
        entry = store.read(lexeme_id)
        assert entry is not None
        snapshot[lexeme_id] = (
            entry.kind,
            [
                (
                    sense.canonical_gloss(),
                    sense.domain,
                    [rendition.content.span for rendition in sense.examples],
                )
                for _, sense, _ in entry.iter_senses()
            ],
        )
    return snapshot


class _Inflight:
    """Counts how many model calls are in flight at once."""

    def __init__(self) -> None:
        """Start with nothing in flight."""
        self.current = 0
        self.peak = 0


def _watched_model(inner: FunctionModel, inflight: _Inflight) -> FunctionModel:
    """Wrap a scripted model so overlapping calls become observable.

    The short sleep is what makes the measurement mean anything: without it a call can
    finish before the pool has dispatched the next one, and a peak of 1 would say nothing
    about whether the pass is capable of running calls concurrently.
    """
    respond = inner.function
    assert respond is not None

    async def watched(messages, info) -> ModelResponse:
        inflight.current += 1
        inflight.peak = max(inflight.peak, inflight.current)
        try:
            await asyncio.sleep(0.01)
            return respond(messages, info)
        finally:
            inflight.current -= 1

    return FunctionModel(watched)


async def test_a_pass_at_eight_workers_matches_the_same_pass_at_one(tmp_path, scripted_model):
    # Everything a pass decides is per-entry, so the pool must change none of it: the
    # counts, the money, and the content on disk are the same at 1 worker and at 8.
    outcomes = {}
    content = {}
    for workers in (1, 8):
        async with _sweep_session(tmp_path, scripted_model, name=f"w{workers}") as active:
            _seed_sweep(active.store)
            outcomes[workers] = await run_retrofit(active.store, active.stages, workers=workers)
            content[workers] = _stored_content(active.store)

    one, eight = outcomes[1], outcomes[8]
    assert eight.stopped_reason is None
    assert eight.counts() == one.counts()
    assert eight.calls == one.calls
    assert eight.cost_usd == pytest.approx(one.cost_usd)
    for name in RetrofitPass.ALL:
        assert eight.passes[name].entries_scanned == one.passes[name].entries_scanned
        assert eight.passes[name].entries_changed == one.passes[name].entries_changed
        assert eight.passes[name].metrics == one.passes[name].metrics
    assert content[8] == content[1]
    # And the sweep really did the work, or the equality above would be vacuous.
    assert one.passes["tag_domain"].calls == _SWEEP_SIZE
    assert one.passes["hygiene"].metrics["glosses_rewritten"] == _SWEEP_SIZE


async def test_a_pass_overlaps_its_model_calls(tmp_path, scripted_model):
    inflight = _Inflight()
    async with _sweep_session(
        tmp_path, _watched_model(scripted_model, inflight), name="peak"
    ) as active:
        _seed_sweep(active.store, 24)
        outcome = await run_retrofit(
            active.store, active.stages, only=[RetrofitPass.TAG_DOMAIN], workers=8
        )

    assert outcome.passes["tag_domain"].calls == 24
    assert inflight.peak > 1
    # The pool is bounded, so it never runs more at once than it was told to.
    assert inflight.peak <= 8


async def test_one_worker_never_overlaps(tmp_path, scripted_model):
    # The control for the probe above: the same measurement at workers=1 must read 1, or
    # a peak above 1 at workers=8 would not be evidence of anything.
    inflight = _Inflight()
    async with _sweep_session(
        tmp_path, _watched_model(scripted_model, inflight), name="serial"
    ) as active:
        _seed_sweep(active.store, 8)
        await run_retrofit(active.store, active.stages, only=[RetrofitPass.TAG_DOMAIN], workers=1)

    assert inflight.peak == 1


@pytest.mark.parametrize(
    "pass_name", [RetrofitPass.HYGIENE, RetrofitPass.TAG_DOMAIN, RetrofitPass.SPANS]
)
async def test_a_pass_never_reads_an_entry_outside_its_lock(
    tmp_path, scripted_model, monkeypatch, pass_name
):
    # The bug this replaces: read outside the lock, write inside it. Two workers -- or two
    # retrofit processes over one store -- would each write a value derived from a read
    # the other's write had already invalidated.
    async with _sweep_session(tmp_path, scripted_model, name=f"lock-{pass_name}") as active:
        store = active.store
        _seed_sweep(store, 8)
        real_read = store.read
        reads: list[str] = []
        unlocked: list[str] = []

        def read(headword_or_id: str) -> Lexeme | None:
            reads.append(headword_or_id)
            if not store.path_for(headword_or_id).with_suffix(".lock").exists():
                unlocked.append(headword_or_id)
            return real_read(headword_or_id)

        monkeypatch.setattr(store, "read", read)
        await run_retrofit(store, active.stages, only=[pass_name], workers=8)

    assert len(reads) == 8
    assert unlocked == []


async def test_a_budget_stop_mid_pass_is_reported_and_leaves_the_store_intact(
    tmp_path, scripted_model
):
    async with _sweep_session(
        tmp_path, scripted_model, name="budget", budget_usd=_TIGHT_BUDGET_USD
    ) as active:
        store = active.store
        _seed_sweep(store)
        outcome = await run_retrofit(
            store, active.stages, only=[RetrofitPass.TAG_DOMAIN, RetrofitPass.SPANS], workers=8
        )

    result = outcome.passes["tag_domain"]
    assert result.stopped_reason == "budget"
    assert outcome.stopped_reason == "budget"
    # The stop is reported, not raised, so the outcome survives it -- and the passes after
    # the one that stopped are skipped rather than run against a dead budget.
    assert RetrofitPass.SPANS not in outcome.passes
    assert 0 < result.calls < _SWEEP_SIZE

    # Nothing half-written: no lock left behind, no temp file, every entry still parses,
    # and each one is either fully tagged or untouched.
    assert list(store.root.rglob("*.lock")) == []
    assert [path for path in store.root.rglob(".*") if path.is_file()] == []
    assert store.count() == _SWEEP_SIZE
    tagged = 0
    for lexeme_id in sorted(store.iter_ids()):
        entry = store.read(lexeme_id)
        assert entry is not None
        domains = {sense.domain for _, sense, _ in entry.iter_senses()}
        assert domains in ({None}, {DomainTag(SCRIPTED_RETROFIT_DOMAIN)})
        if domains == {DomainTag(SCRIPTED_RETROFIT_DOMAIN)}:
            tagged += 1
    assert tagged == result.items_changed


async def test_a_pass_stopped_by_the_budget_resumes_where_it_left_off(tmp_path, scripted_model):
    # The idempotence markers are untouched by the pool, so a killed run is relaunchable:
    # the second sweep bills only for what the first one did not reach.
    async with _sweep_session(
        tmp_path, scripted_model, name="resume", budget_usd=_TIGHT_BUDGET_USD
    ) as active:
        _seed_sweep(active.store)
        first = await run_retrofit(
            active.store, active.stages, only=[RetrofitPass.TAG_DOMAIN], workers=8
        )
        done = first.passes["tag_domain"].items_changed
        before = _stored_content(active.store)
    assert first.stopped_reason == "budget"

    async with _sweep_session(tmp_path, scripted_model, name="resume") as resumed:
        second = await run_retrofit(
            resumed.store, resumed.stages, only=[RetrofitPass.TAG_DOMAIN], workers=8
        )
        after = _stored_content(resumed.store)

    assert second.stopped_reason is None
    assert second.passes["tag_domain"].calls == _SWEEP_SIZE - done
    # What the first sweep tagged was not re-tagged; only the rest was.
    assert all(after[key] == value for key, value in before.items() if value[1][0][1] is not None)


async def test_an_external_stop_event_ends_a_pass_and_skips_the_rest(tmp_path, scripted_model):
    stop = asyncio.Event()
    stop.set()
    async with _sweep_session(tmp_path, scripted_model, name="stopped") as active:
        _seed_sweep(active.store, 8)
        outcome = await run_retrofit(active.store, active.stages, workers=8, stop_event=stop)

    assert outcome.stopped_reason == "stopped"
    assert outcome.calls == 0
    assert list(outcome.passes) == [RetrofitPass.CLASSIFY_KIND]


async def test_the_pool_defaults_to_the_runners_configured_worker_count(session, monkeypatch):
    seen: list[int] = []
    real_pool = retrofit.run_pool

    async def spy(items, handler, *, workers, stop_event=None, fail_fast=False) -> tuple[int, int]:
        seen.append(workers)
        return await real_pool(
            items, handler, workers=workers, stop_event=stop_event, fail_fast=fail_fast
        )

    monkeypatch.setattr(retrofit, "run_pool", spy)
    session.store.write(make_entry("abseil"))

    await run_retrofit(session.store, session.stages, only=[RetrofitPass.TAG_DOMAIN])

    assert seen == [session.config.concurrency.workers]
    assert session.config.concurrency.workers == 4  # what the test config sets


async def test_pass_counters_are_only_mutated_under_the_tally_lock():
    # The discipline itself, asserted directly. Single-threaded asyncio would make a bare
    # ``+= 1`` atomic on its own, so holding the lock is what stops that guarantee from
    # being the only thing keeping the counts right.
    tally = retrofit._Tally("probe")
    async with tally._lock:
        blocked = asyncio.create_task(tally.entry(items_changed=1))
        await asyncio.sleep(0)  # let it run as far as it can get
        assert tally.result.entries_scanned == 0  # which is: no further than the lock
    await blocked
    assert tally.result.entries_scanned == 1

    await asyncio.gather(*(tally.entry(items_changed=2) for _ in range(200)))
    await asyncio.gather(*(tally.call(0.25) for _ in range(200)))

    assert tally.result.entries_scanned == 201
    assert tally.result.entries_changed == 201
    assert tally.result.items_changed == 401
    assert tally.result.calls == 200
    assert tally.result.cost_usd == pytest.approx(50.0)


async def test_a_long_pass_logs_its_progress(monkeypatch):
    # A ten-thousand-entry pass is otherwise silent for hours.
    events: list[tuple[str, dict[str, object]]] = []

    class _Sink:
        """A logger that records what a pass reports instead of writing it."""

        def info(self, event: str, **fields: object) -> None:
            """Record an info event."""
            events.append((event, fields))

    monkeypatch.setattr(retrofit, "_LOG", _Sink())
    tally = retrofit._Tally(RetrofitPass.HYGIENE)
    for index in range(retrofit.PROGRESS_EVERY * 2 + 3):
        await tally.entry(items_changed=index % 2)

    progress = [fields for event, fields in events if event == "retrofit_pass_progress"]
    assert len(progress) == 2  # at 500 and at 1000, not at 1003
    assert progress[0]["entries_done"] == retrofit.PROGRESS_EVERY
    assert progress[0]["pass_name"] == RetrofitPass.HYGIENE
    assert progress[-1]["entries_done"] == retrofit.PROGRESS_EVERY * 2
    assert progress[-1]["entries_changed"] == retrofit.PROGRESS_EVERY  # every other entry
    assert progress[-1]["cost_usd"] == 0.0


# --------------------------------------------------------------------------------------
# D-39: the shared headword-initial detector
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "headword"),
    [
        ("Ban is what happens when something is stopped.", "ban"),  # bare headword
        ("ban means to stop something happening.", "ban"),  # case-insensitive
        ("A ban is an order to stop.", "ban"),  # article + headword
        ("An apple is a round fruit.", "apple"),
        ("The people are human beings.", "people"),
        ("People are human beings.", "people"),  # plural -s of "people"? no: bare
        ("Bans are orders that stop something.", "ban"),  # plural -s
        ("The word ban refers to a formal prohibition.", "ban"),
        ("The term ban is used for a prohibition.", "ban"),
        ("To ban is to forbid something formally.", "ban"),
        ("To ban means to forbid something formally.", "ban"),
        ("  ban is leading whitespace and still offends.", "ban"),
        ("Ice axes are tools for climbing.", "ice axe"),  # multi-word headword, plural
    ],
)
def test_headword_initial_openings_are_detected(text: str, headword: str):
    assert is_headword_initial(text, headword)


@pytest.mark.parametrize(
    ("text", "headword"),
    [
        ("An order from someone in charge that stops something.", "ban"),
        ("Bananas are a yellow fruit.", "ban"),  # word boundary, not a prefix match
        ("Something that is forbidden by a ban.", "ban"),  # names it, but not first
        ("A group of human beings considered together.", "people"),
        ("Anything at all.", ""),  # a blank headword can never match
        ("", "ban"),  # nor can empty text
        ("The banning of a book is a ban.", "ban"),  # "banning" is not "ban" or "bans"
    ],
)
def test_acceptable_openings_are_not_detected(text: str, headword: str):
    assert not is_headword_initial(text, headword)


# --------------------------------------------------------------------------------------
# D-39: rendition_hygiene — rewrite stored gloss renditions that open with the headword
# --------------------------------------------------------------------------------------


def _entry_with_gloss_renditions(
    headword: str,
    renditions: list[tuple[ReadingLevel, Register, str]],
    *,
    canonical: str = "An order from someone in charge that stops something.",
    kind: LexemeKind = LexemeKind.SIMPLEX,
    proper_noun: ProperNounInfo | None = None,
) -> Lexeme:
    """Build an entry whose one sense carries the given non-canonical gloss renditions."""
    sense = Sense(
        index=0,
        gloss=Renditions[str](
            root=[
                canonical_rendition(canonical),
                *(
                    Rendition[str](reading_level=level, style=style, content=text)
                    for level, style, text in renditions
                ),
            ]
        ),
        examples=Renditions[Example](
            root=[canonical_rendition(Example(text=f"The {headword} was announced."))]
        ),
    )
    return Lexeme.empty(
        headword,
        kind=kind,
        proper_noun=proper_noun,
        pos_entries=[POSEntry(pos=PartOfSpeech.NOUN, senses=[sense], morphology=Morphology())],
    )


async def test_rendition_hygiene_rewrites_offenders_and_keeps_the_old_text(session):
    entry = _entry_with_gloss_renditions(
        "ban",
        [
            (ReadingLevel.GRADE_1, Register.PLAIN, "A ban is an order to stop."),
            (ReadingLevel.NEUTRAL, Register.FORMAL, "Ban is a formal prohibition."),
            (ReadingLevel.COLLEGE, Register.PLAIN, "An authoritative prohibition on conduct."),
        ],
    )
    session.store.write(entry)

    outcome = await run_retrofit(
        session.store, session.stages, only=[RetrofitPass.RENDITION_HYGIENE]
    )
    result = outcome.passes["rendition_hygiene"]

    # Two offenders, one entry, one call -- the clean college rendition is not sent.
    assert result.metrics["renditions_rewritten"] == 2
    assert result.metrics["still_initial"] == 0
    assert result.calls == 1
    assert result.cost_usd > 0.0

    gloss = session.store.read("ban").pos_entries[0].senses[0].gloss
    grade_1 = gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    formal = gloss.get(ReadingLevel.NEUTRAL, Register.FORMAL)
    # The scripted rewrite echoes the level/register label back, so this asserts each
    # offender was listed under -- and answered for -- its own audience.
    assert grade_1.content == "Scripted rewrite of the grade_1/plain rendition."
    assert formal.content == "Scripted rewrite of the neutral/formal rendition."
    # The clean one was never sent and is untouched.
    assert (
        gloss.get(ReadingLevel.COLLEGE, Register.PLAIN).content
        == "An authoritative prohibition on conduct."
    )
    # The canonical gloss is the `hygiene` pass's business, not this one's.
    assert gloss.canonical().content == "An order from someone in charge that stops something."


async def test_rendition_hygiene_keeps_the_superseded_text_in_a_zero_cost_note(session):
    old_text = "A ban is an order to stop."
    entry = _entry_with_gloss_renditions("ban", [(ReadingLevel.GRADE_1, Register.PLAIN, old_text)])
    session.store.write(entry)

    await run_retrofit(session.store, session.stages, only=[RetrofitPass.RENDITION_HYGIENE])

    stored = session.store.read("ban")
    rewritten = stored.pos_entries[0].senses[0].gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    note_record = stored.provenance[rewritten.provenance_id]
    assert note_record.note == old_text
    # Zero-cost, so a naive sum over the provenance table does not double-count the call.
    assert note_record.cost_usd == 0.0
    assert sum(record.cost_usd for record in stored.provenance.values()) == pytest.approx(
        session.meter.summary().total_usd
    )


async def test_rendition_hygiene_remeasures_readability_and_clears_the_flag(session):
    entry = _entry_with_gloss_renditions(
        "ban", [(ReadingLevel.GRADE_1, Register.PLAIN, "A ban is an order to stop.")]
    )
    stale = entry.pos_entries[0].senses[0].gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    stale.assessment = Assessment(readability_grade=99.0)
    stale.assessment.flag(QAFlag.OG_HEADWORD_INITIAL)
    session.store.write(entry)

    await run_retrofit(session.store, session.stages, only=[RetrofitPass.RENDITION_HYGIENE])

    rewritten = (
        session.store.read("ban")
        .pos_entries[0]
        .senses[0]
        .gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    )
    assert rewritten.assessment.readability_grade == pytest.approx(
        round(flesch_kincaid_grade(rewritten.content, ignore=("ban",)), 2)
    )
    # The rewrite fixed the opening, so the flag that described the old text is gone.
    assert rewritten.assessment.qa_flags == []


async def test_rendition_hygiene_exempts_proper_nouns(session):
    # A proper-noun definition legitimately names its entity (D-30), at every reading
    # level, so this pass must not send it or bill for it.
    entry = _entry_with_gloss_renditions(
        "Congo",
        [(ReadingLevel.GRADE_1, Register.PLAIN, "The Congo is a big river in Africa.")],
        canonical="The Congo River is a major central African river.",
        kind=LexemeKind.PROPER_NOUN,
        proper_noun=ProperNounInfo(entity_type=EntityType.PLACE),
    )
    session.store.write(entry)

    outcome = await run_retrofit(
        session.store, session.stages, only=[RetrofitPass.RENDITION_HYGIENE]
    )
    result = outcome.passes["rendition_hygiene"]

    assert result.metrics["renditions_rewritten"] == 0
    assert result.calls == 0
    assert result.cost_usd == 0.0
    assert (
        session.store.read("congo")
        .pos_entries[0]
        .senses[0]
        .gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
        .content
        == "The Congo is a big river in Africa."
    )


async def test_rendition_hygiene_is_idempotent_and_free_on_a_second_sweep(session):
    entry = _entry_with_gloss_renditions(
        "ban",
        [
            (ReadingLevel.GRADE_1, Register.PLAIN, "A ban is an order to stop."),
            (ReadingLevel.GRADE_5, Register.PLAIN, "Bans are orders that stop things."),
        ],
    )
    session.store.write(entry)

    await run_retrofit(session.store, session.stages, only=[RetrofitPass.RENDITION_HYGIENE])
    spent = session.meter.summary().total_usd
    before = session.store.read("ban").pos_entries[0].senses[0].gloss.root

    again = await run_retrofit(session.store, session.stages, only=[RetrofitPass.RENDITION_HYGIENE])
    result = again.passes["rendition_hygiene"]

    assert result.items_changed == 0
    assert result.calls == 0
    assert result.cost_usd == 0.0
    assert session.meter.summary().total_usd == spent
    assert session.store.read("ban").pos_entries[0].senses[0].gloss.root == before


#: The exact text ``tests/conftest.py``'s scripted rewrite produces for a grade_1/plain
#: offender. An entry headworded "scripted" therefore has an offending rendition the
#: scripted model "rewrites" into itself, which is how the two awkward paths below get
#: exercised without a second hand-rolled model.
_SCRIPTED_REWRITE = "Scripted rewrite of the grade_1/plain rendition."


async def test_rendition_hygiene_does_not_re_bill_an_entry_its_model_left_alone(session):
    # The marker is written whenever the call itself succeeded, whether or not it produced
    # a usable rewrite -- so the entry is persisted even when nothing changed, or the next
    # sweep pays for the same answer again. Here the "rewrite" comes back identical to
    # what was sent, so no rendition changes and only the marker is worth writing.
    entry = _entry_with_gloss_renditions(
        "scripted", [(ReadingLevel.GRADE_1, Register.PLAIN, _SCRIPTED_REWRITE)]
    )
    session.store.write(entry)

    first = await run_retrofit(session.store, session.stages, only=[RetrofitPass.RENDITION_HYGIENE])
    spent = session.meter.summary().total_usd
    second = await run_retrofit(
        session.store, session.stages, only=[RetrofitPass.RENDITION_HYGIENE]
    )

    assert first.passes["rendition_hygiene"].calls == 1
    assert first.passes["rendition_hygiene"].metrics["renditions_rewritten"] == 0
    assert first.passes["rendition_hygiene"].metrics["still_initial"] == 1
    assert second.passes["rendition_hygiene"].calls == 0
    assert second.passes["rendition_hygiene"].cost_usd == 0.0
    assert session.meter.summary().total_usd == spent


async def test_rendition_hygiene_flags_a_rewrite_that_still_opens_with_the_headword(session):
    # The scripted rewrite of a "scripted"-headworded entry begins "Scripted ...", so the
    # pass applies a rewrite that does not actually fix the defect; the flag records that
    # rather than the pass pretending it succeeded.
    entry = _entry_with_gloss_renditions(
        "scripted",
        [(ReadingLevel.GRADE_1, Register.PLAIN, "Scripted things are done from a script.")],
    )
    session.store.write(entry)

    outcome = await run_retrofit(
        session.store, session.stages, only=[RetrofitPass.RENDITION_HYGIENE]
    )
    result = outcome.passes["rendition_hygiene"]

    assert result.metrics["renditions_rewritten"] == 1
    assert result.metrics["still_initial"] == 1

    rewritten = (
        session.store.read("scripted")
        .pos_entries[0]
        .senses[0]
        .gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    )
    assert rewritten.content == _SCRIPTED_REWRITE
    assert rewritten.assessment.qa_flags == [QAFlag.OG_HEADWORD_INITIAL]


async def test_all_passes_by_default_run_rendition_hygiene_after_readability_hygiene(session):
    entry = _entry_with_gloss_renditions(
        "ban", [(ReadingLevel.GRADE_1, Register.PLAIN, "A ban is an order to stop.")]
    )
    session.store.write(entry)

    outcome = await run_retrofit(session.store, session.stages)

    order = list(outcome.passes)
    # D-47: rendition_hygiene checks the *form* of stored prose, so it runs after every
    # pass that rewrites prose -- readability_hygiene included, which used to run after it
    # and reintroduced the very defect this pass exists to remove.
    assert order[-1] == RetrofitPass.RENDITION_HYGIENE
    assert order[-2] == RetrofitPass.READABILITY_HYGIENE
    assert outcome.passes["rendition_hygiene"].metrics["renditions_rewritten"] == 1


# --------------------------------------------------------------------------------------
# D-47: the marker records *which* renditions were answered for, not just "was answered"
# --------------------------------------------------------------------------------------


def _add_gloss_rendition(store, lexeme_id: str, level: ReadingLevel, text: str) -> None:
    """Add one non-canonical gloss rendition to a stored entry, as a later pass would."""
    entry = store.read(lexeme_id)
    entry.pos_entries[0].senses[0].gloss.add(
        Rendition[str](reading_level=level, style=Register.PLAIN, content=text)
    )
    store.write(entry)


async def test_rendition_hygiene_re_attempts_when_a_new_offender_appears(session):
    # The defect D-47 found: readability_hygiene rewrote renditions into headword-initial
    # forms *behind* this pass's per-entry "already tried" boolean, and nothing revisited
    # them. The marker now carries a hash of the offending rendition ids, so an entry
    # whose offending set has changed earns one more attempt -- and one whose set is
    # unchanged still costs nothing.
    entry = _entry_with_gloss_renditions(
        "scripted", [(ReadingLevel.GRADE_1, Register.PLAIN, _SCRIPTED_REWRITE)]
    )
    session.store.write(entry)

    first = await run_retrofit(session.store, session.stages, only=[RetrofitPass.RENDITION_HYGIENE])
    unchanged = await run_retrofit(
        session.store, session.stages, only=[RetrofitPass.RENDITION_HYGIENE]
    )
    spent = session.meter.summary().total_usd

    _add_gloss_rendition(
        session.store,
        "scripted",
        ReadingLevel.GRADE_5,
        "Scripted things are done from a script.",
    )
    again = await run_retrofit(session.store, session.stages, only=[RetrofitPass.RENDITION_HYGIENE])

    assert first.passes["rendition_hygiene"].calls == 1
    # Same offending set as the first sweep: nothing new to say, nothing to bill.
    assert unchanged.passes["rendition_hygiene"].calls == 0
    assert unchanged.passes["rendition_hygiene"].cost_usd == 0.0
    # The set changed, so the entry is due one more attempt -- on the offenders it has now.
    assert again.passes["rendition_hygiene"].calls == 1
    assert again.passes["rendition_hygiene"].metrics["renditions_rewritten"] == 1
    assert session.meter.summary().total_usd > spent

    gloss = session.store.read("scripted").pos_entries[0].senses[0].gloss
    assert (
        gloss.get(ReadingLevel.GRADE_5, Register.PLAIN).content
        == "Scripted rewrite of the grade_5/plain rendition."
    )


async def test_rendition_hygiene_stops_after_two_attempts_on_one_entry(session):
    # The re-attempt rule is bounded by the attempt count in the same marker note: an
    # entry gets two attempts, ever, however often its offending set changes afterwards.
    entry = _entry_with_gloss_renditions(
        "scripted", [(ReadingLevel.GRADE_1, Register.PLAIN, _SCRIPTED_REWRITE)]
    )
    session.store.write(entry)

    first = await run_retrofit(session.store, session.stages, only=[RetrofitPass.RENDITION_HYGIENE])
    _add_gloss_rendition(
        session.store, "scripted", ReadingLevel.GRADE_5, "Scripted is what a script makes."
    )
    second = await run_retrofit(
        session.store, session.stages, only=[RetrofitPass.RENDITION_HYGIENE]
    )
    spent = session.meter.summary().total_usd

    _add_gloss_rendition(
        session.store, "scripted", ReadingLevel.COLLEGE, "Scripted conduct follows a script."
    )
    third = await run_retrofit(session.store, session.stages, only=[RetrofitPass.RENDITION_HYGIENE])

    assert first.passes["rendition_hygiene"].calls == 1
    assert second.passes["rendition_hygiene"].calls == 1
    # Two attempts spent: the third offending set is left flagged rather than re-billed.
    # All three renditions still open with the headword -- this entry is one the scripted
    # model cannot fix, which is exactly the case the bound exists for.
    assert third.passes["rendition_hygiene"].calls == 0
    assert third.passes["rendition_hygiene"].cost_usd == 0.0
    assert third.passes["rendition_hygiene"].metrics["still_initial"] == 3
    assert session.meter.summary().total_usd == spent
    assert (
        session.store.read("scripted")
        .pos_entries[0]
        .senses[0]
        .gloss.get(ReadingLevel.COLLEGE, Register.PLAIN)
        .content
        == "Scripted conduct follows a script."
    )


def test_the_marker_note_carries_the_offending_set_and_the_attempt_count():
    # The format is `<pass>:<digest>;attempts=<n>`, and every skip/attempt decision the
    # two passes make is this one function's.
    entry = make_entry("ban")
    prefix = retrofit._RENDITION_HYGIENE_PREFIX
    ids = ["ban:noun:0#grade_1/plain"]

    assert retrofit._hygiene_attempt_due(entry, prefix, []) is None  # nothing to fix
    first = retrofit._hygiene_attempt_due(entry, prefix, ids)
    assert first is not None
    digest, _, attempts = first.removeprefix(f"{prefix}:").partition(";attempts=")
    assert (len(digest), attempts) == (16, "1")

    entry.add_provenance(
        Provenance(stage=StageName.HYGIENE, model="m", prompt_version="1", note=first)
    )
    # Same offending set: already answered for, so no second call.
    assert retrofit._hygiene_attempt_due(entry, prefix, ids) is None
    # A changed set -- one more offender -- is a different question, and gets one answer.
    grown = [*ids, "ban:noun:0#grade_5/plain"]
    second = retrofit._hygiene_attempt_due(entry, prefix, grown)
    assert second is not None
    assert second.endswith(";attempts=2")

    entry.add_provenance(
        Provenance(stage=StageName.HYGIENE, model="m", prompt_version="1", note=second)
    )
    assert (
        retrofit._hygiene_attempt_due(entry, prefix, [*grown, "ban:noun:0#college/plain"]) is None
    )


def test_a_pre_d47_boolean_marker_earns_exactly_one_more_attempt():
    # Every entry the old per-entry boolean stamped carries `<pass>:rewritten`, which no
    # digest equals -- so the first sweep after this change revisits it once, and the
    # attempt count then stops it.
    entry = make_entry("ban")
    prefix = retrofit._RENDITION_HYGIENE_PREFIX
    entry.add_provenance(
        Provenance(
            stage=StageName.HYGIENE, model="m", prompt_version="1", note=f"{prefix}:rewritten"
        )
    )

    note = retrofit._hygiene_attempt_due(entry, prefix, ["ban:noun:0#grade_1/plain"])
    assert note is not None
    assert note.endswith(";attempts=2")

    entry.add_provenance(
        Provenance(stage=StageName.HYGIENE, model="m", prompt_version="1", note=note)
    )
    assert retrofit._hygiene_attempt_due(entry, prefix, ["ban:noun:0#grade_10/plain"]) is None


# --------------------------------------------------------------------------------------
# D-59: rendition_hygiene also flags stored register renditions that copy their canonical
# --------------------------------------------------------------------------------------


async def test_rendition_hygiene_flags_a_stored_near_copy_register_rendition(session):
    # Free (D-59): no model call, only a verdict recorded. The formal rendition here
    # echoes the canonical gloss verbatim.
    canonical = "An order from someone in charge that stops something."
    entry = _entry_with_gloss_renditions(
        "ban", [(ReadingLevel.NEUTRAL, Register.FORMAL, canonical)], canonical=canonical
    )
    session.store.write(entry)

    outcome = await run_retrofit(
        session.store, session.stages, only=[RetrofitPass.RENDITION_HYGIENE]
    )
    result = outcome.passes["rendition_hygiene"]

    assert result.metrics["near_copy_flagged"] == 1
    assert result.calls == 0
    assert result.cost_usd == 0.0

    formal = (
        session.store.read("ban")
        .pos_entries[0]
        .senses[0]
        .gloss.get(ReadingLevel.NEUTRAL, Register.FORMAL)
    )
    assert formal.assessment.qa_flags == [QAFlag.OG_NEAR_COPY]
    assert is_near_copy(formal.content, canonical)


async def test_rendition_hygiene_does_not_flag_a_genuinely_different_register_rendition(session):
    canonical = "An order from someone in charge that stops something."
    entry = _entry_with_gloss_renditions(
        "ban",
        [(ReadingLevel.NEUTRAL, Register.FORMAL, "A formally issued prohibition on conduct.")],
        canonical=canonical,
    )
    session.store.write(entry)

    outcome = await run_retrofit(
        session.store, session.stages, only=[RetrofitPass.RENDITION_HYGIENE]
    )
    result = outcome.passes["rendition_hygiene"]

    assert result.metrics["near_copy_flagged"] == 0
    formal = (
        session.store.read("ban")
        .pos_entries[0]
        .senses[0]
        .gloss.get(ReadingLevel.NEUTRAL, Register.FORMAL)
    )
    assert formal.assessment is None or QAFlag.OG_NEAR_COPY not in formal.assessment.qa_flags


async def test_rendition_hygiene_clears_a_stale_near_copy_flag_once_the_text_diverges(session):
    canonical = "An order from someone in charge that stops something."
    entry = _entry_with_gloss_renditions(
        "ban",
        [
            (
                ReadingLevel.NEUTRAL,
                Register.FORMAL,
                "A wholly reworded formal definition of conduct.",
            )
        ],
        canonical=canonical,
    )
    stale = entry.pos_entries[0].senses[0].gloss.get(ReadingLevel.NEUTRAL, Register.FORMAL)
    stale.assessment = Assessment()
    stale.assessment.flag(QAFlag.OG_NEAR_COPY)
    session.store.write(entry)

    outcome = await run_retrofit(
        session.store, session.stages, only=[RetrofitPass.RENDITION_HYGIENE]
    )
    result = outcome.passes["rendition_hygiene"]

    assert result.metrics["near_copy_flagged"] == 1  # the flag changed -- it was removed
    formal = (
        session.store.read("ban")
        .pos_entries[0]
        .senses[0]
        .gloss.get(ReadingLevel.NEUTRAL, Register.FORMAL)
    )
    assert formal.assessment.qa_flags == []


async def test_rendition_hygiene_never_checks_the_plain_register_for_near_copy(session):
    # plain is the canonical's own register, not a rewrite meant to diverge from it, so
    # even a plain rendition identical to the canonical is never flagged.
    canonical = "An order from someone in charge that stops something."
    entry = _entry_with_gloss_renditions(
        "ban", [(ReadingLevel.GRADE_5, Register.PLAIN, canonical)], canonical=canonical
    )
    session.store.write(entry)

    outcome = await run_retrofit(
        session.store, session.stages, only=[RetrofitPass.RENDITION_HYGIENE]
    )
    result = outcome.passes["rendition_hygiene"]

    assert result.metrics["near_copy_flagged"] == 0
    grade_5 = (
        session.store.read("ban")
        .pos_entries[0]
        .senses[0]
        .gloss.get(ReadingLevel.GRADE_5, Register.PLAIN)
    )
    assert grade_5.assessment is None or QAFlag.OG_NEAR_COPY not in grade_5.assessment.qa_flags


async def test_rendition_hygiene_does_not_exempt_proper_nouns_from_near_copy(session):
    # Unlike the headword-initial step (D-30), there is no proper-noun exemption here: a
    # proper noun's formal and slang registers still have to read differently (D-59).
    canonical = "The Congo River is a major central African river."
    entry = _entry_with_gloss_renditions(
        "Congo",
        [(ReadingLevel.NEUTRAL, Register.FORMAL, canonical)],
        canonical=canonical,
        kind=LexemeKind.PROPER_NOUN,
        proper_noun=ProperNounInfo(entity_type=EntityType.PLACE),
    )
    session.store.write(entry)

    outcome = await run_retrofit(
        session.store, session.stages, only=[RetrofitPass.RENDITION_HYGIENE]
    )
    result = outcome.passes["rendition_hygiene"]

    assert result.metrics["near_copy_flagged"] == 1
    formal = (
        session.store.read("congo")
        .pos_entries[0]
        .senses[0]
        .gloss.get(ReadingLevel.NEUTRAL, Register.FORMAL)
    )
    assert formal.assessment.qa_flags == [QAFlag.OG_NEAR_COPY]


# --------------------------------------------------------------------------------------
# readability_hygiene — rewrite stored renditions that still miss their readability band
# --------------------------------------------------------------------------------------

#: What every readability_hygiene marker note starts with; the rest is the flagged set's
#: digest and the attempt count (D-47).
_READABILITY_HYGIENE_PREFIX = "readability_hygiene:"


def _flagged_text_rendition(
    level: ReadingLevel, style: Register, text: str, *, grade: float = 99.0
) -> Rendition[str]:
    """Return a gloss/encyclopedia/explanation rendition carrying ``OG_READABILITY_MISS``.

    ``grade`` is stamped directly onto the assessment rather than measured, so a test
    controls exactly when the pass considers a candidate "better" without depending on
    the heuristic syllable counter agreeing with the fixture's prose.
    """
    rendition = Rendition[str](reading_level=level, style=style, content=text)
    rendition.assessment = Assessment(readability_grade=grade)
    rendition.assessment.flag(QAFlag.OG_READABILITY_MISS)
    return rendition


def _flagged_example_rendition(
    level: ReadingLevel, style: Register, text: str, *, grade: float = 99.0
) -> Rendition[Example]:
    """Return an example rendition carrying ``OG_READABILITY_MISS``, span left unset."""
    rendition = Rendition[Example](reading_level=level, style=style, content=Example(text=text))
    rendition.assessment = Assessment(readability_grade=grade)
    rendition.assessment.flag(QAFlag.OG_READABILITY_MISS)
    return rendition


def _entry_with_readability_misses(
    headword: str,
    *,
    gloss_text: str | None = None,
    example_text: str | None = None,
    grade: float = 99.0,
) -> Lexeme:
    """Build an entry whose one sense carries the given flagged grade_1/plain renditions.

    Either ``gloss_text`` or ``example_text`` (or both) may be given; whichever is
    omitted contributes only its canonical rendition, unflagged.
    """
    gloss_root = [canonical_rendition("A short canonical definition for the headword.")]
    if gloss_text is not None:
        gloss_root.append(
            _flagged_text_rendition(ReadingLevel.GRADE_1, Register.PLAIN, gloss_text, grade=grade)
        )
    example_root = [canonical_rendition(Example(text=f"The {headword} appeared in a sentence."))]
    if example_text is not None:
        example_root.append(
            _flagged_example_rendition(
                ReadingLevel.GRADE_1, Register.PLAIN, example_text, grade=grade
            )
        )
    sense = Sense(
        index=0,
        gloss=Renditions[str](root=gloss_root),
        examples=Renditions[Example](root=example_root),
    )
    return Lexeme.empty(
        headword,
        kind=LexemeKind.SIMPLEX,
        pos_entries=[POSEntry(pos=PartOfSpeech.NOUN, senses=[sense], morphology=Morphology())],
    )


def _entry_with_flagged_encyclopedia(headword: str, text: str, *, grade: float = 99.0) -> Lexeme:
    """Build an entry whose encyclopedia section carries one flagged grade_1 rendition."""
    entry = Lexeme.empty(headword, kind=LexemeKind.SIMPLEX)
    entry.encyclopedia = Renditions[str](
        root=[
            canonical_rendition("A canonical encyclopedia passage about the headword."),
            _flagged_text_rendition(ReadingLevel.GRADE_1, Register.PLAIN, text, grade=grade),
        ]
    )
    return entry


async def test_readability_hygiene_rewrites_gloss_and_example_and_clears_the_flag(session):
    entry = _entry_with_readability_misses(
        READABILITY_FIX_HEADWORD,
        gloss_text=(
            "An extraordinarily sophisticated theoretical characterisation requiring "
            "comprehensive interdisciplinary consideration throughout."
        ),
        example_text=(
            "An extraordinarily sophisticated theoretical characterisation necessitated "
            "comprehensive methodological consideration throughout every investigation."
        ),
    )
    session.store.write(entry)

    outcome = await run_retrofit(
        session.store, session.stages, only=[RetrofitPass.READABILITY_HYGIENE]
    )
    result = outcome.passes["readability_hygiene"]

    assert result.calls == 1
    assert result.cost_usd > 0.0
    assert result.metrics["renditions_rewritten"] == 2
    assert result.metrics["now_in_band"] == 2
    assert result.metrics["still_out_of_band"] == 0

    fixed_text = READABILITY_FIX_TEMPLATE.format(headword=READABILITY_FIX_HEADWORD)
    stored = session.store.read(READABILITY_FIX_HEADWORD)
    sense = stored.pos_entries[0].senses[0]

    gloss = sense.gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert gloss.content == fixed_text
    assert gloss.assessment.qa_flags == []

    example = sense.examples.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert example.content.text == fixed_text
    assert example.assessment.qa_flags == []
    # The rewrite is re-found by the same free finder the spans pass uses, so the span
    # actually selects the headword in the new text.
    assert example.content.span is not None
    start, end = example.content.span
    assert example.content.text[start:end].lower() == READABILITY_FIX_HEADWORD.lower()

    # Old text kept in a zero-cost note record, exactly like rendition_hygiene's.
    note_record = stored.provenance[gloss.provenance_id]
    assert "extraordinarily sophisticated" in (note_record.note or "")
    assert note_record.cost_usd == 0.0


async def test_readability_hygiene_keeps_the_old_text_and_flag_when_no_better(session):
    # The default (non-marker) headword's scripted rewrite echoes the offending text
    # straight back, so it is never simpler than what is already stored and the pass
    # must keep the original text and its flag.
    entry = _entry_with_readability_misses(
        "notimproved",
        gloss_text="An extraordinarily sophisticated theoretical characterisation of it.",
        example_text="An extraordinarily sophisticated theoretical characterisation "
        "of notimproved.",
    )
    session.store.write(entry)

    outcome = await run_retrofit(
        session.store, session.stages, only=[RetrofitPass.READABILITY_HYGIENE]
    )
    result = outcome.passes["readability_hygiene"]

    assert result.calls == 1
    assert result.metrics["renditions_rewritten"] == 0
    assert result.metrics["now_in_band"] == 0
    assert result.metrics["still_out_of_band"] == 2

    stored = session.store.read("notimproved")
    sense = stored.pos_entries[0].senses[0]
    gloss = sense.gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert gloss.content == "An extraordinarily sophisticated theoretical characterisation of it."
    assert gloss.assessment.qa_flags == [QAFlag.OG_READABILITY_MISS]
    assert gloss.assessment.readability_grade == 99.0


async def test_readability_hygiene_keeps_the_old_example_when_the_rewrite_loses_the_headword(
    session,
):
    entry = _entry_with_readability_misses(
        READABILITY_LOSES_HEADWORD,
        example_text=(
            "An extraordinarily sophisticated theoretical characterisation of it appeared, "
            "requiring consideration throughout."
        ),
    )
    session.store.write(entry)

    outcome = await run_retrofit(
        session.store, session.stages, only=[RetrofitPass.READABILITY_HYGIENE]
    )
    result = outcome.passes["readability_hygiene"]

    assert result.metrics["renditions_rewritten"] == 0
    assert result.metrics["still_out_of_band"] == 1

    example = (
        session.store.read(READABILITY_LOSES_HEADWORD)
        .pos_entries[0]
        .senses[0]
        .examples.get(ReadingLevel.GRADE_1, Register.PLAIN)
    )
    assert example.content.text == (
        "An extraordinarily sophisticated theoretical characterisation of it appeared, "
        "requiring consideration throughout."
    )
    assert example.assessment.qa_flags == [QAFlag.OG_READABILITY_MISS]


async def test_readability_hygiene_rewrites_the_encyclopedia_passage(session):
    entry = _entry_with_flagged_encyclopedia(
        READABILITY_FIX_HEADWORD,
        "An extraordinarily sophisticated theoretical characterisation of the headword, "
        "requiring extensive interdisciplinary consideration throughout every paragraph.",
    )
    session.store.write(entry)

    outcome = await run_retrofit(
        session.store, session.stages, only=[RetrofitPass.READABILITY_HYGIENE]
    )
    result = outcome.passes["readability_hygiene"]

    assert result.metrics["renditions_rewritten"] == 1
    assert result.metrics["now_in_band"] == 1

    rendition = session.store.read(READABILITY_FIX_HEADWORD).encyclopedia.get(
        ReadingLevel.GRADE_1, Register.PLAIN
    )
    assert rendition.content == READABILITY_FIX_TEMPLATE.format(headword=READABILITY_FIX_HEADWORD)
    assert rendition.assessment.qa_flags == []


async def test_readability_hygiene_skips_an_entry_with_no_flagged_renditions(session):
    entry = make_entry("cleanword")
    session.store.write(entry)

    outcome = await run_retrofit(
        session.store, session.stages, only=[RetrofitPass.READABILITY_HYGIENE]
    )
    result = outcome.passes["readability_hygiene"]

    assert result.calls == 0
    assert result.cost_usd == 0.0
    assert result.items_changed == 0
    stored = session.store.read("cleanword")
    assert not any(
        (record.note or "").startswith(_READABILITY_HYGIENE_PREFIX)
        for record in stored.provenance.values()
    )


async def test_readability_hygiene_is_idempotent_and_free_on_a_second_sweep(session):
    entry = _entry_with_readability_misses(
        READABILITY_FIX_HEADWORD,
        gloss_text="An extraordinarily sophisticated theoretical characterisation is difficult.",
    )
    session.store.write(entry)

    await run_retrofit(session.store, session.stages, only=[RetrofitPass.READABILITY_HYGIENE])
    spent = session.meter.summary().total_usd
    before = session.store.read(READABILITY_FIX_HEADWORD).pos_entries[0].senses[0].gloss.root

    again = await run_retrofit(
        session.store, session.stages, only=[RetrofitPass.READABILITY_HYGIENE]
    )
    result = again.passes["readability_hygiene"]

    assert result.calls == 0
    assert result.cost_usd == 0.0
    assert session.meter.summary().total_usd == spent
    after = session.store.read(READABILITY_FIX_HEADWORD).pos_entries[0].senses[0].gloss.root
    assert after == before


async def test_all_passes_by_default_run_readability_hygiene_second_to_last(session):
    entry = _entry_with_readability_misses(
        READABILITY_FIX_HEADWORD,
        gloss_text="An extraordinarily sophisticated theoretical characterisation is present.",
    )
    session.store.write(entry)

    outcome = await run_retrofit(session.store, session.stages)

    assert list(outcome.passes)[-1] == RetrofitPass.RENDITION_HYGIENE
    assert list(outcome.passes)[-2] == RetrofitPass.READABILITY_HYGIENE
    assert outcome.passes["readability_hygiene"].metrics["renditions_rewritten"] == 1
    # And rendition_hygiene, running after it, saw the rewritten text: nothing it produced
    # opens with the headword, so there is nothing left for that pass to bill for (D-47).
    assert outcome.passes["rendition_hygiene"].calls == 0


async def test_readability_hygiene_refuses_a_rewrite_that_opens_with_the_headword(session):
    # D-47: the simplest form of a hard definition is the one a dictionary must not use,
    # "A ban is an order to stop.", and this pass produced 1,934 of them on the core
    # before the rule was added. A gloss rewrite that opens with the headword is refused
    # -- old text kept, readability flag kept -- while an *example* rewrite, which may
    # legitimately open with its headword, is adopted from the same answer.
    entry = _entry_with_readability_misses(
        READABILITY_INITIAL_HEADWORD,
        gloss_text="An extraordinarily sophisticated theoretical characterisation of it.",
        example_text="An extraordinarily sophisticated theoretical characterisation appeared.",
    )
    session.store.write(entry)

    outcome = await run_retrofit(
        session.store, session.stages, only=[RetrofitPass.READABILITY_HYGIENE]
    )
    result = outcome.passes["readability_hygiene"]

    assert result.calls == 1
    assert result.metrics["renditions_rewritten"] == 1  # the example only
    assert result.metrics["still_out_of_band"] == 1

    sense = session.store.read(READABILITY_INITIAL_HEADWORD).pos_entries[0].senses[0]
    gloss = sense.gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert gloss.content == "An extraordinarily sophisticated theoretical characterisation of it."
    assert gloss.assessment.qa_flags == [QAFlag.OG_READABILITY_MISS]
    assert gloss.assessment.readability_grade == 99.0
    assert is_headword_initial(
        READABILITY_INITIAL_TEXT.format(headword=READABILITY_INITIAL_HEADWORD),
        READABILITY_INITIAL_HEADWORD,
    )

    example = sense.examples.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert example.content.text == READABILITY_INITIAL_TEXT.format(
        headword=READABILITY_INITIAL_HEADWORD
    )
    assert example.assessment.qa_flags == []


async def test_readability_hygiene_re_attempts_when_a_new_flag_appears(session):
    # The same marker rule as rendition_hygiene's (D-47): a flagged set that has not
    # changed is not re-billed, and one that has -- a rendition another pass rewrote and
    # re-flagged, say -- earns one more attempt.
    entry = _entry_with_readability_misses(
        "notimproved",
        gloss_text="An extraordinarily sophisticated theoretical characterisation of it.",
    )
    session.store.write(entry)

    first = await run_retrofit(
        session.store, session.stages, only=[RetrofitPass.READABILITY_HYGIENE]
    )
    unchanged = await run_retrofit(
        session.store, session.stages, only=[RetrofitPass.READABILITY_HYGIENE]
    )

    stored = session.store.read("notimproved")
    stored.pos_entries[0].senses[0].gloss.add(
        _flagged_text_rendition(
            ReadingLevel.GRADE_5,
            Register.PLAIN,
            "Another extraordinarily sophisticated theoretical characterisation of it.",
        )
    )
    session.store.write(stored)
    again = await run_retrofit(
        session.store, session.stages, only=[RetrofitPass.READABILITY_HYGIENE]
    )

    # The scripted rewrite for this headword echoes the text back, so the flag survives
    # every attempt: what changes between the sweeps is only the flagged set.
    assert first.passes["readability_hygiene"].calls == 1
    assert unchanged.passes["readability_hygiene"].calls == 0
    assert unchanged.passes["readability_hygiene"].cost_usd == 0.0
    assert again.passes["readability_hygiene"].calls == 1
    assert again.passes["readability_hygiene"].metrics["still_out_of_band"] == 2


def _general_sense_entry(note: str | None) -> Lexeme:
    """An entry whose only sense carries a ``.general`` tag from a tag_domain verdict."""
    entry = make_entry("abseil")
    sense = entry.pos_entries[0].senses[0]
    sense.domain = DomainTag("everyday_life.general")
    entry.add_provenance(
        Provenance(stage=StageName.TAG_DOMAIN, model="gpt-5.4-nano", prompt_version="1", note=note)
    )
    return entry


def test_general_verdict_under_current_taxonomy_is_not_cleared():
    entry = _general_sense_entry(f"taxonomy_version={TAXONOMY_VERSION}")
    assert retrofit._clear_weak_domains(entry) == 0
    assert entry.pos_entries[0].senses[0].domain is not None


def test_general_verdict_from_older_taxonomy_is_cleared_for_retag():
    entry = _general_sense_entry("taxonomy_version=0")
    assert retrofit._clear_weak_domains(entry) == 1
    assert entry.pos_entries[0].senses[0].domain is None


def test_general_verdict_without_version_note_is_treated_as_stale():
    entry = _general_sense_entry(None)
    assert retrofit._clear_weak_domains(entry) == 1


async def test_repair_revisits_an_entry_when_a_different_sense_later_loses_its_examples(
    config, scripted_model
):
    """A per-entry boolean marker left 54 core senses without examples (QA-DIARY close-out)."""
    async with RunSession(config, model_override=scripted_model, run_id="repair-revisit") as s:
        entry = make_entry("abseil")
        pos = entry.pos_entries[0]
        pos.senses.append(Sense.of(1, "A second, distinct sense of abseil for the test."))
        entry.pos_entries[0].senses[1].examples = (
            entry.pos_entries[0].senses[1].examples.__class__([])
        )
        s.store.write(entry)

        first = await run_retrofit(s.store, s.stages, only={RetrofitPass.REPAIR}, workers=2)
        assert first.passes["repair"].metrics["examples_added"] > 0

        # Now empty sense 0 — a different sense than the one repaired above.
        again = s.store.read("abseil")
        again.pos_entries[0].senses[0].examples = (
            again.pos_entries[0].senses[0].examples.__class__([])
        )
        s.store.write(again)

        second = await run_retrofit(s.store, s.stages, only={RetrofitPass.REPAIR}, workers=2)
        assert second.passes["repair"].metrics["examples_added"] > 0, (
            "stale marker suppressed a real repair"
        )

        third = await run_retrofit(s.store, s.stages, only={RetrofitPass.REPAIR}, workers=2)
        assert third.passes["repair"].calls == 0


def test_a_readability_rewrite_that_duplicates_a_sibling_example_is_refused():
    from opengloss_generator.schema import (
        Example,
        Provenance,
        ReadingLevel,
        Register,
        Rendition,
        StageName,
    )
    from opengloss_generator.workflows import retrofit as retrofit_module

    twin = Rendition[Example](
        reading_level=ReadingLevel.GRADE_1,
        style=Register.PLAIN,
        content=Example(text="Calendaring makes paper smooth.", span=(0, 11)),
    )
    offender_rendition = Rendition[Example](
        reading_level=ReadingLevel.GRADE_1,
        style=Register.PLAIN,
        content=Example(
            text="Calendaring is a finishing process that compresses paper between rollers "
            "to produce a smooth surface.",
            span=(0, 11),
        ),
    )
    entry = _entry_with_sense_examples("calendaring", [twin, offender_rendition])
    pos_entry = entry.pos_entries[0]
    sense = pos_entry.senses[0]
    offender = retrofit_module._ReadabilityOffender(
        rendition=offender_rendition,
        field_name="examples",
        pos_entry=pos_entry,
        rendition_id="calendaring:noun:0#grade_1/plain[1]",
        sense=sense,
    )
    provenance = Provenance(
        stage=StageName.RENDITIONS, model="test", prompt_version="1", cost_usd=0.0
    )
    adopted = retrofit_module._apply_readability_rewrite(
        entry, offender, "Calendaring makes paper smooth.", 1.0, provenance
    )
    assert adopted is False
    assert offender_rendition.content.text.startswith("Calendaring is a finishing")
    # The entry must still round-trip: no two renditions share a key.
    type(entry).model_validate(entry.model_dump(mode="json"))
