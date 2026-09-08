"""Lexeme hygiene: inflected forms that got their own entry, and sentence fragments (D-79).

Companion to ``test_sense_hygiene.py``. That pass audits an entry's inventory from inside the
entry; this one asks whether the *headword* deserved an entry at all, and its first step is the
only question in the project that reads a second entry to answer it. So most of what is
asserted here is a *refusal*: the four free guards that stop a fold, the kind and WordNet keeps
that stop a retirement, and the five plurals — "glasses", "arms", "customs", "goods",
"manners" — that carry a meaning their singular does not and must come through untouched.

WordNet is an optional signal, so every test that is not about WordNet drives it explicitly:
:func:`_without_wordnet` makes every lookup answer "not consulted" (the shape a machine with no
``nltk`` sees), and :func:`_with_wordnet_keeping` makes the free keep fire without needing the
corpus on disk. The two tests that do want the real corpus skip themselves when it is absent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from opengloss_generator import cli, wordnet
from opengloss_generator.config import StoreConfig
from opengloss_generator.runner import RunSession
from opengloss_generator.schema import (
    Example,
    Lexeme,
    LexemeKind,
    Morphology,
    PartOfSpeech,
    POSEntry,
    Relation,
    RelationTarget,
    RelationType,
    Renditions,
    Sense,
    canonical_rendition,
)
from opengloss_generator.store import LexemeStore
from opengloss_generator.workflows import lexeme_hygiene as module
from opengloss_generator.workflows.lexeme_hygiene import (
    FOLD_RELATION_NOTE,
    FRAGMENT_RELATION_NOTE,
    RETIRED_FOLD_NOTE,
    RETIRED_FRAGMENT_NOTE,
    InflectionIndex,
    LexemeHygieneStep,
    plan_lexeme_hygiene,
    run_lexeme_hygiene,
)
from tests.conftest import ALIAS_RELATED_MARKER, ALIAS_SAME_MARKER, FRAGMENT_MARKER

# Two definitions that share a content word, so the scripted judge folds; and two that share
# none, so it keeps. See ``conftest._inflection_fold_payload`` for why the scripted answer is
# computed from the prompt rather than planted in it.
BASE_GLOSS = "A structured collection of data held in a computer and organised for retrieval."
INFLECTED_GLOSS = "Structured collections of data organised for retrieval; more than one."
UNRELATED_GLOSS = "Merchandise offered for sale; wares moved by a merchant."


def _sense(index: int, gloss: str, *, relations: list[Relation] | None = None) -> Sense:
    """Build one sense carrying a canonical gloss and whatever relations a test needs."""
    return Sense(
        index=index,
        gloss=Renditions[str](root=[canonical_rendition(gloss)]),
        examples=Renditions[Example](root=[]),
        relations=relations or [],
    )


def _relation(relation_type: RelationType, term: str) -> Relation:
    """Build one typed relation."""
    return Relation(type=relation_type, target=RelationTarget(term=term))


def _entry(
    headword: str,
    senses: list[Sense],
    *,
    pos: PartOfSpeech = PartOfSpeech.NOUN,
    morphology: Morphology | None = None,
    kind: LexemeKind = LexemeKind.SIMPLEX,
    extra: list[POSEntry] | None = None,
) -> Lexeme:
    """Build an entry holding one part-of-speech block, plus any extra blocks named."""
    blocks = [POSEntry(pos=pos, senses=senses, morphology=morphology or Morphology())]
    blocks.extend(extra or [])
    return Lexeme.empty(headword, kind=kind, pos_entries=blocks)


def _lemma(headword: str = "database", *, plural: str = "databases") -> Lexeme:
    """Build the base word's entry, whose morphology records the plural."""
    return _entry(headword, [_sense(0, BASE_GLOSS)], morphology=Morphology(plural=plural))


def _form(headword: str = "databases", *, gloss: str = INFLECTED_GLOSS) -> Lexeme:
    """Build the inflected form's own entry, with one relation to watch demoted."""
    return _entry(
        headword,
        [_sense(0, gloss, relations=[_relation(RelationType.HYPERNYM, "collection")])],
    )


def _notes(entry: Lexeme) -> list[str]:
    """Return every non-empty provenance note on an entry."""
    return [record.note for record in entry.provenance.values() if record.note]


def _without_wordnet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every WordNet lookup answer "not consulted", as a machine with no nltk does."""
    monkeypatch.setattr(module.wordnet, "distinct_from_lemma", lambda *_: None)
    monkeypatch.setattr(module.wordnet, "as_lemma", lambda *_: None)
    monkeypatch.setattr(module.wordnet, "evidence_line", lambda *_: "WordNet: not consulted.")
    monkeypatch.setattr(
        module.wordnet,
        "availability",
        lambda: wordnet.Availability(usable=False, reason="nltk not installed"),
    )


def _with_wordnet_keeping(monkeypatch: pytest.MonkeyPatch, *forms: str) -> None:
    """Make WordNet keep exactly ``forms``, without needing the corpus on disk."""
    kept = {form.lower() for form in forms}
    monkeypatch.setattr(module.wordnet, "distinct_from_lemma", lambda f, _: f.lower() in kept)
    monkeypatch.setattr(module.wordnet, "as_lemma", lambda f: f.lower() in kept)
    monkeypatch.setattr(module.wordnet, "evidence_line", lambda *_: "WordNet: scripted.")
    monkeypatch.setattr(module.wordnet, "availability", lambda: wordnet.Availability(usable=True))


async def _fold(session, **kwargs: object) -> module.StepResult:
    """Run the fold step alone and return its result."""
    outcome = await run_lexeme_hygiene(
        session.store,
        session.stages,
        workers=4,
        only={LexemeHygieneStep.INFLECTION_FOLD},
        **kwargs,
    )
    return outcome.steps[LexemeHygieneStep.INFLECTION_FOLD]


async def _fragments(session, **kwargs: object) -> module.StepResult:
    """Run the fragments step alone and return its result."""
    outcome = await run_lexeme_hygiene(
        session.store, session.stages, workers=4, only={LexemeHygieneStep.FRAGMENTS}, **kwargs
    )
    return outcome.steps[LexemeHygieneStep.FRAGMENTS]


# --------------------------------------------------------------------------------------
# Step 1 — inflection_fold: the mechanics
# --------------------------------------------------------------------------------------


async def test_a_pure_inflection_is_tombstoned_onto_its_lemma(session, monkeypatch):
    _without_wordnet(monkeypatch)
    session.store.write(_lemma())
    session.store.write(_form())

    result = await _fold(session)

    assert result.candidates == 1
    assert result.calls == 1
    assert result.retired == 1
    assert result.senses_retired == 1
    assert result.retired_by_reason == {"plural": 1}
    assert result.entries_changed == 1

    stored = session.store.read("databases")
    assert [sense.retired for sense in stored.pos_entries[0].senses] == [True]
    # Nothing is deleted: the tombstoned sense keeps the definition it had.
    assert stored.pos_entries[0].senses[0].canonical_gloss() == INFLECTED_GLOSS


async def test_a_fold_notes_the_lemma_it_resolves_to(session, monkeypatch):
    _without_wordnet(monkeypatch)
    session.store.write(_lemma())
    session.store.write(_form())

    await _fold(session)

    expected = RETIRED_FOLD_NOTE.format(retired="databases:noun:0", lemma_id="database")
    assert expected in _notes(session.store.read("databases"))


async def test_a_folded_sense_has_its_relations_demoted_not_dropped(session, monkeypatch):
    _without_wordnet(monkeypatch)
    session.store.write(_lemma())
    session.store.write(_form())

    result = await _fold(session)

    assert result.relations_demoted == 1
    relation = session.store.read("databases").pos_entries[0].senses[0].relations[0]
    assert relation.type is RelationType.SEE_ALSO
    assert relation.target.term == "collection"
    assert relation.note is not None
    assert relation.note.startswith(FOLD_RELATION_NOTE)


async def test_the_lemma_carries_the_form_and_is_left_alone(session, monkeypatch):
    _without_wordnet(monkeypatch)
    session.store.write(_lemma())
    session.store.write(_form())

    await _fold(session)

    lemma = session.store.read("database")
    # The assertion the fold rests on: the tombstoned string still resolves, because D-75's
    # `inflections` dataset is built from exactly this field.
    assert lemma.pos_entries[0].morphology.plural == "databases"
    assert [sense.retired for sense in lemma.pos_entries[0].senses] == [False]
    assert _notes(lemma) == []


async def test_a_lemma_that_does_not_carry_the_form_is_refused(session):
    """The by-construction assertion, forced by an index that disagrees with the store."""
    session.store.write(_entry("database", [_sense(0, BASE_GLOSS)]))
    session.store.write(_form())
    index = InflectionIndex(
        forms={
            "databases": (
                module._LemmaRecord(
                    lexeme_id="database",
                    headword="database",
                    pos=PartOfSpeech.NOUN,
                    relation="plural",
                ),
            )
        },
        lemma_ids=frozenset(),
    )

    plan = module._fold_plan(session.store.read("databases"), index, session.store)

    assert plan.skip == "skipped_form_missing"
    assert plan.lemma is None


# --------------------------------------------------------------------------------------
# Step 1 — the free guards
# --------------------------------------------------------------------------------------


async def test_an_entry_that_is_itself_a_lemma_is_never_folded(session, monkeypatch):
    """A form that is also a lemma: "copies" holds "copied", so folding it breaks a chain."""
    _without_wordnet(monkeypatch)
    session.store.write(
        _entry("copy", [_sense(0, BASE_GLOSS)], morphology=Morphology(plural="copies"))
    )
    session.store.write(
        _entry(
            "copies",
            [_sense(0, INFLECTED_GLOSS)],
            pos=PartOfSpeech.VERB,
            morphology=Morphology(past_tense="copied"),
        )
    )
    session.store.write(_entry("copied", [_sense(0, BASE_GLOSS)], pos=PartOfSpeech.VERB))

    result = await _fold(session, lexeme_ids=["copies"])

    assert result.skipped_is_lemma == 1
    assert result.calls == 0
    assert result.retired == 0
    assert [s.retired for s in session.store.read("copies").pos_entries[0].senses] == [False]


async def test_a_noun_plural_is_never_folded_onto_a_verb_only_lemma(session, monkeypatch):
    """The POS-mismatch guard: the lemma records the form under a POS the form does not have."""
    _without_wordnet(monkeypatch)
    session.store.write(
        _entry(
            "run",
            [_sense(0, BASE_GLOSS)],
            pos=PartOfSpeech.VERB,
            morphology=Morphology(third_person_singular="runs"),
        )
    )
    session.store.write(_entry("runs", [_sense(0, INFLECTED_GLOSS)], pos=PartOfSpeech.NOUN))

    result = await _fold(session)

    assert result.candidates == 1
    assert result.skipped_pos_mismatch == 1
    assert result.calls == 0
    assert result.retired == 0


async def test_a_form_whose_lemma_is_retired_is_never_folded(session, monkeypatch):
    _without_wordnet(monkeypatch)
    retired = _lemma()
    retired.pos_entries[0].senses[0].retired = True
    session.store.write(retired)
    session.store.write(_form())

    result = await _fold(session)

    # A lemma with no live sense is not in the index at all, so the form is not a candidate.
    assert result.candidates == 0
    assert result.calls == 0
    assert result.retired == 0


async def test_a_derivation_is_never_a_candidate(session, monkeypatch):
    """A derivation is a different word: "validly" is not an inflection of "valid"."""
    _without_wordnet(monkeypatch)
    session.store.write(
        _entry(
            "valid",
            [_sense(0, BASE_GLOSS)],
            pos=PartOfSpeech.ADJECTIVE,
            morphology=Morphology(derivations=["validly"]),
        )
    )
    session.store.write(_entry("validly", [_sense(0, INFLECTED_GLOSS)], pos=PartOfSpeech.ADVERB))

    result = await _fold(session)

    assert result.candidates == 0
    assert result.calls == 0


# --------------------------------------------------------------------------------------
# Step 1 — the keeps
# --------------------------------------------------------------------------------------


async def test_wordnet_keeps_a_form_that_is_a_lemma_of_its_own_for_free(session, monkeypatch):
    _with_wordnet_keeping(monkeypatch, "databases")
    session.store.write(_lemma())
    session.store.write(_form())

    result = await _fold(session)

    assert result.kept_wordnet == 1
    assert result.calls == 0
    assert result.cost_usd == 0.0
    assert result.retired == 0
    assert [s.retired for s in session.store.read("databases").pos_entries[0].senses] == [False]


async def test_a_verdict_keeps_a_form_whose_definitions_diverge(session, monkeypatch):
    _without_wordnet(monkeypatch)
    session.store.write(_lemma("good", plural="goods"))
    session.store.write(_form("goods", gloss=UNRELATED_GLOSS))

    result = await _fold(session)

    assert result.calls == 1
    assert result.kept_verdict == 1
    assert result.retired == 0
    assert result.wordnet_unavailable == 1


#: The five plurals whose entries carry a meaning their singular does not. Every one of them is
#: a fold this pass must never make; the pilot (D-79) kept all five, two on WordNet evidence
#: and three on the verdict.
MUST_KEEP: tuple[tuple[str, str, str], ...] = (
    ("glasses", "glass", "A device with two lenses worn in front of the eyes to correct vision."),
    ("arms", "arm", "Weapons and equipment used in warfare; a heraldic coat of arms."),
    ("customs", "custom", "The government service that inspects imports and collects duty."),
    ("goods", "good", "Merchandise offered for sale; wares moved by a merchant."),
    ("manners", "manner", "Polite social behaviour; the etiquette expected in company."),
)

#: What each of :data:`MUST_KEEP`'s singulars means. Deliberately sharing no content word with
#: the plural above it, which is what makes each of these a keep rather than a fold.
MUST_KEEP_BASES: dict[str, str] = {
    "glass": "A hard transparent substance produced by melting sand.",
    "arm": "An upper limb of the human body, from shoulder to hand.",
    "custom": "A habitual practice followed by a community over time.",
    "good": "That which is morally right, or a benefit to someone.",
    "manner": "A way in which a thing is done or happens.",
}


@pytest.mark.parametrize(("form", "base", "gloss"), MUST_KEEP)
async def test_the_five_plurals_that_carry_their_own_meaning_are_kept(
    session, monkeypatch, form, base, gloss
):
    """The failure to avoid, measured with WordNet switched off so the verdict must carry it."""
    _without_wordnet(monkeypatch)
    session.store.write(
        _entry(base, [_sense(0, MUST_KEEP_BASES[base])], morphology=Morphology(plural=form))
    )
    session.store.write(_entry(form, [_sense(0, gloss)]))

    result = await _fold(session)

    assert result.candidates == 1
    assert result.retired == 0
    assert result.kept_verdict == 1
    stored = session.store.read(form)
    assert [sense.retired for sense in stored.pos_entries[0].senses] == [False]


@pytest.mark.parametrize(("form", "base"), [("glasses", "glass"), ("arms", "arm")])
def test_the_real_wordnet_holds_the_plural_as_a_lemma_of_its_own(form, base):
    """The free half of the keep, against the corpus itself rather than a monkeypatch."""
    if not wordnet.availability().usable:
        pytest.skip("nltk or its WordNet corpus is not installed")
    assert wordnet.distinct_from_lemma(form, base) is True
    assert wordnet.distinct_from_lemma("databases", "database") is False


# --------------------------------------------------------------------------------------
# Step 1 — idempotence and the WordNet fallback
# --------------------------------------------------------------------------------------


async def test_a_second_sweep_over_an_unchanged_entry_is_free(session, monkeypatch):
    _without_wordnet(monkeypatch)
    session.store.write(_lemma("good", plural="goods"))
    session.store.write(_form("goods", gloss=UNRELATED_GLOSS))

    first = await _fold(session)
    second = await _fold(session)

    assert first.calls == 1
    assert second.calls == 0
    assert second.attempts_exhausted == 1
    assert second.cost_usd == 0.0


async def test_a_folded_entry_is_never_billed_again(session, monkeypatch):
    _without_wordnet(monkeypatch)
    session.store.write(_lemma())
    session.store.write(_form())

    first = await _fold(session)
    second = await _fold(session)

    assert first.retired == 1
    # It has no live sense left, so it is not a candidate at any price.
    assert second.candidates == 0
    assert second.calls == 0


async def test_the_pass_runs_without_nltk_and_says_so(session, monkeypatch):
    _without_wordnet(monkeypatch)
    session.store.write(_lemma())
    session.store.write(_form())

    outcome = await run_lexeme_hygiene(
        session.store, session.stages, workers=4, only={LexemeHygieneStep.INFLECTION_FOLD}
    )

    result = outcome.steps[LexemeHygieneStep.INFLECTION_FOLD]
    assert outcome.wordnet.usable is False
    assert outcome.wordnet.reason == "nltk not installed"
    assert result.wordnet_unavailable == 1
    # The skipped check does not become a keep or a fold of its own: the verdict decides.
    assert result.calls == 1
    assert result.retired == 1


# --------------------------------------------------------------------------------------
# Step 2 — fragments
# --------------------------------------------------------------------------------------


def _fragment_entry(
    headword: str,
    *,
    kind: LexemeKind = LexemeKind.COMPOUND,
    fragment: bool = True,
) -> Lexeme:
    """Build a multiword entry the scripted judge calls a fragment, or does not."""
    gloss = (
        f"An unspecified quantity of something; {FRAGMENT_MARKER}."
        if fragment
        else "Directly above and in contact with something."
    )
    return _entry(
        headword,
        [_sense(0, gloss, relations=[_relation(RelationType.HYPERNYM, "quantity")])],
        kind=kind,
    )


@pytest.mark.parametrize(
    ("headword", "reason"),
    [
        ("the outcome", "leading_article"),
        ("some sugar", "leading_determiner"),
        ("is not", "leading_auxiliary"),
        ("with chemicals", "leading_preposition"),
        ("progress toward", "trailing_preposition"),
        ("complete being", "trailing_auxiliary"),
        ("machine based", None),
        ("solar panel", None),
        ("sugar", None),
    ],
)
def test_the_function_word_gate_classifies_a_headword(headword, reason):
    assert module._fragment_reason(headword) == reason


async def test_a_fragment_is_tombstoned_with_its_reason(session, monkeypatch):
    _without_wordnet(monkeypatch)
    session.store.write(_fragment_entry("some sugar"))

    result = await _fragments(session)

    assert result.candidates == 1
    assert result.retired == 1
    assert result.retired_by_reason == {"leading_determiner": 1}
    assert result.senses_retired == 1
    assert result.relations_demoted == 1

    stored = session.store.read("some_sugar")
    assert [sense.retired for sense in stored.pos_entries[0].senses] == [True]
    expected = RETIRED_FRAGMENT_NOTE.format(
        retired="some_sugar:noun:0", reason="leading_determiner"
    )
    assert expected in _notes(stored)
    relation = stored.pos_entries[0].senses[0].relations[0]
    assert relation.type is RelationType.SEE_ALSO
    assert relation.note.startswith(FRAGMENT_RELATION_NOTE)


@pytest.mark.parametrize("kind", [LexemeKind.PHRASAL_VERB, LexemeKind.IDIOM])
async def test_a_phrasal_verb_or_idiom_is_skipped_whole(session, monkeypatch, kind):
    _without_wordnet(monkeypatch)
    session.store.write(_fragment_entry("break down", kind=kind))

    result = await _fragments(session)

    assert result.candidates == 1
    assert result.skipped_kind == 1
    assert result.calls == 0
    assert result.retired == 0


async def test_a_phrase_wordnet_holds_as_a_lemma_is_kept_for_free(session, monkeypatch):
    _with_wordnet_keeping(monkeypatch, "on top of")
    session.store.write(_fragment_entry("on top of"))

    result = await _fragments(session)

    assert result.kept_wordnet == 1
    assert result.calls == 0
    assert result.retired == 0
    assert [s.retired for s in session.store.read("on_top_of").pos_entries[0].senses] == [False]


async def test_a_lexical_unit_verdict_keeps_the_entry(session, monkeypatch):
    _without_wordnet(monkeypatch)
    session.store.write(_fragment_entry("on top of", fragment=False))

    result = await _fragments(session)

    assert result.calls == 1
    assert result.kept_verdict == 1
    assert result.retired == 0


async def test_a_second_fragments_sweep_is_free(session, monkeypatch):
    _without_wordnet(monkeypatch)
    session.store.write(_fragment_entry("on top of", fragment=False))

    first = await _fragments(session)
    second = await _fragments(session)

    assert first.calls == 1
    assert second.calls == 0
    assert second.attempts_exhausted == 1


def test_the_real_wordnet_holds_some_multiword_phrases_and_not_others():
    if not wordnet.availability().usable:
        pytest.skip("nltk or its WordNet corpus is not installed")
    assert wordnet.as_lemma("of course") is True
    assert wordnet.as_lemma("out of stock") is True
    assert wordnet.as_lemma("some sugar") is False
    # WordNet's multiword coverage is partial, which is why the residue buys a verdict.
    assert wordnet.as_lemma("on top of") is False


# --------------------------------------------------------------------------------------
# Selection, planning and the CLI
# --------------------------------------------------------------------------------------


async def test_an_unknown_step_is_refused(session):
    with pytest.raises(ValueError, match="unknown lexeme hygiene step"):
        await run_lexeme_hygiene(session.store, session.stages, workers=1, only={"nonsense"})


async def test_the_index_is_built_over_the_whole_store_not_the_id_list(session, monkeypatch):
    """A ``--from-list`` naming only the form must still find the lemma."""
    _without_wordnet(monkeypatch)
    session.store.write(_lemma())
    session.store.write(_form())

    result = await _fold(session, lexeme_ids=["databases"])

    assert result.entries_scanned == 1
    assert result.retired == 1


def test_the_plan_runs_every_free_filter_and_spends_nothing(tmp_path, monkeypatch):
    _without_wordnet(monkeypatch)
    store = LexemeStore(StoreConfig(root=tmp_path / "store", fsync_on_write=False))
    store.write(_lemma())
    store.write(_form())
    store.write(_fragment_entry("some sugar"))
    store.write(_fragment_entry("break down", kind=LexemeKind.PHRASAL_VERB))

    plan = plan_lexeme_hygiene(store, sorted(store.iter_ids()))

    assert plan["steps"]["inflection_fold"]["candidates"] == 1
    assert plan["steps"]["inflection_fold"]["calls_due"] == 1
    assert plan["steps"]["fragments"]["candidates"] == 2
    assert plan["steps"]["fragments"]["free_skips"] == 1
    assert plan["estimated_calls"] == 2


@pytest.fixture
def _cli_offline(monkeypatch, scripted_model) -> None:
    """Force every CLI-created session onto the scripted model."""
    original = RunSession.__init__

    def patched(
        self, config, *, model_override=None, run_id=None, install_signal_handler=False
    ) -> None:
        original(
            self,
            config,
            model_override=scripted_model,
            run_id=run_id,
            install_signal_handler=False,
        )

    monkeypatch.setattr(RunSession, "__init__", patched)


def _cli(*args: str) -> dict:
    """Invoke the CLI and return its JSON summary; structured logs go to stderr."""
    result = CliRunner().invoke(cli.app, list(args))
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _seeded_store(root: Path) -> LexemeStore:
    """Write one foldable pair and one fragment into a fresh store."""
    store = LexemeStore(StoreConfig(root=root, fsync_on_write=False))
    store.write(_lemma())
    store.write(_form())
    store.write(_fragment_entry("some sugar"))
    return store


@pytest.mark.usefixtures("_cli_offline")
def test_the_cli_dry_run_writes_nothing_and_prices_the_plan(tmp_path, monkeypatch):
    _without_wordnet(monkeypatch)
    store = _seeded_store(tmp_path / "store")

    summary = _cli("lexeme-hygiene", "--store", str(store.root), "--dry-run")

    assert summary["stop_reason"] == "dry_run"
    assert summary["cost_usd"] == 0.0
    assert summary["estimated_calls"] == 2
    assert summary["estimated_cost_usd"] > 0
    assert [s.retired for s in store.read("databases").pos_entries[0].senses] == [False]


@pytest.mark.usefixtures("_cli_offline")
def test_the_cli_runs_one_named_step(tmp_path, monkeypatch):
    _without_wordnet(monkeypatch)
    store = _seeded_store(tmp_path / "store")

    summary = _cli("lexeme-hygiene", "--only", "fragments", "--store", str(store.root))

    assert set(summary["steps"]) == {"fragments"}
    assert summary["steps"]["fragments"]["retired"] == 1
    assert summary["cost_usd"] > 0
    assert [s.retired for s in store.read("databases").pos_entries[0].senses] == [False]


@pytest.mark.usefixtures("_cli_offline")
def test_the_cli_restricts_the_sweep_to_a_word_list(tmp_path, monkeypatch):
    _without_wordnet(monkeypatch)
    store = _seeded_store(tmp_path / "store")
    word_list = tmp_path / "words.tsv"
    word_list.write_text("word\tgroup\nsome sugar\tfragment\n", encoding="utf-8")

    summary = _cli("lexeme-hygiene", "--from-list", str(word_list), "--store", str(store.root))

    assert summary["steps"]["inflection_fold"]["entries_scanned"] == 1
    assert summary["steps"]["fragments"]["retired"] == 1
    assert [s.retired for s in store.read("databases").pos_entries[0].senses] == [False]


@pytest.mark.usefixtures("_cli_offline")
def test_the_cli_refuses_an_unknown_step(tmp_path):
    result = CliRunner().invoke(
        cli.app, ["lexeme-hygiene", "--only", "nonsense", "--store", str(tmp_path / "store")]
    )

    assert result.exit_code != 0
    assert "nonsense" in result.output


# --------------------------------------------------------------------------------------
# Step 3 — aliases (D-81)
# --------------------------------------------------------------------------------------
#
# The only step here that *adds* an edge. What is asserted is mostly the refusals again:
# the free skip when an edge already exists, the free `alias_of` when the short entry's own
# gloss already names the long headword, and the `see_also` a common noun gets — because
# the failure this step must never commit is calling "city" another name for New York City.

LINCOLN_GLOSS = "The sixteenth president of the United States; Abraham Lincoln."
LINCOLN_LONG_GLOSS = "The sixteenth president, who issued the Emancipation Proclamation."
# A short entry the scripted judge calls one referent with the long name, without naming
# it verbatim — so the free keep does not fire and the verdict is actually bought.
FDR_SHORT_GLOSS = f"A twentieth-century American president, {ALIAS_SAME_MARKER} man."
FDR_LONG_GLOSS = "The thirty-second president of the United States, in office 1933-1945."
CITY_GLOSS = f"A large or important town: {ALIAS_RELATED_MARKER} a settlement is."
NYC_GLOSS = "The largest city in the United States, on the Atlantic coast."
NUMERAL_GLOSS = "The Roman numeral for two."
WAR_GLOSS = "The global conflict of 1939 to 1945 between Allied and Axis powers."


def _candidate_file(tmp_path: Path, pairs: list[tuple[str, str]]) -> Path:
    """Write a candidate TSV whose ``notes`` column proposes each ``(name, target)`` pair."""
    lines = ["name\tword\tentity_type\tsource\tqid\tnotes"]
    lines += [
        f"{name}\t{name}\tperson\tname_seed\t\talias_of candidate: store has '{target}'; wn"
        for name, target in pairs
    ]
    path = tmp_path / "tier6_candidates.tsv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


async def _aliases(session, alias_list: Path, **kwargs: object) -> module.StepResult:
    """Run the aliases step alone and return its result."""
    outcome = await run_lexeme_hygiene(
        session.store,
        session.stages,
        workers=4,
        only={LexemeHygieneStep.ALIASES},
        alias_list=alias_list,
        **kwargs,
    )
    return outcome.steps[LexemeHygieneStep.ALIASES]


def _edges(entry: Lexeme) -> list[tuple[str, str]]:
    """Return ``(relation type, target term)`` for every relation on every live sense."""
    return [
        (relation.type.value, relation.target.term)
        for _, sense, _ in entry.iter_senses()
        if not sense.retired
        for relation in sense.relations
    ]


async def test_a_short_entry_naming_the_long_headword_is_an_alias_for_free(session, tmp_path):
    session.store.write(_entry("Abraham Lincoln", [_sense(0, LINCOLN_LONG_GLOSS)]))
    session.store.write(_entry("Lincoln", [_sense(0, LINCOLN_GLOSS)]))
    path = _candidate_file(tmp_path, [("Abraham Lincoln", "lincoln")])

    result = await _aliases(session, path)

    assert result.candidates == 1
    assert result.calls == 0
    assert result.cost_usd == 0.0
    assert result.alias_free == 1
    assert result.alias_written == 1
    assert _edges(session.store.read("abraham_lincoln")) == [("alias_of", "Lincoln")]


async def test_the_alias_edge_has_no_far_side(session, tmp_path):
    session.store.write(_entry("Abraham Lincoln", [_sense(0, LINCOLN_LONG_GLOSS)]))
    session.store.write(_entry("Lincoln", [_sense(0, LINCOLN_GLOSS)]))
    path = _candidate_file(tmp_path, [("Abraham Lincoln", "lincoln")])

    await _aliases(session, path)

    # An alias points one way (D-81). The short entry is read and never written.
    assert _edges(session.store.read("lincoln")) == []


async def test_a_bought_verdict_can_write_an_alias_too(session, tmp_path):
    session.store.write(_entry("Franklin D. Roosevelt", [_sense(0, FDR_LONG_GLOSS)]))
    session.store.write(_entry("FDR", [_sense(0, FDR_SHORT_GLOSS)]))
    path = _candidate_file(tmp_path, [("Franklin D. Roosevelt", "fdr")])

    result = await _aliases(session, path)

    assert result.calls == 1
    assert result.alias_free == 0
    assert result.alias_written == 1
    assert _edges(session.store.read("franklin_d_roosevelt")) == [("alias_of", "FDR")]


def test_the_alias_prompt_carries_both_headwords_and_both_definitions():
    # The prompt is what the verdict is bought on, and it is the one thing a marker-keyed
    # scripted judge cannot assert, so it is checked exactly here instead.
    prompt = module._build_alias_prompt(
        _entry("New York City", [_sense(0, NYC_GLOSS)]),
        _entry("city", [_sense(0, CITY_GLOSS)]),
    )
    assert "Long: New York City" in prompt
    assert NYC_GLOSS in prompt
    assert "Short: city" in prompt
    assert CITY_GLOSS in prompt


async def test_a_common_noun_gets_an_authored_see_also_not_an_alias(session, tmp_path):
    session.store.write(_entry("New York City", [_sense(0, NYC_GLOSS)]))
    session.store.write(_entry("city", [_sense(0, CITY_GLOSS)]))
    path = _candidate_file(tmp_path, [("New York City", "city")])

    result = await _aliases(session, path)

    assert result.calls == 1
    assert result.see_also_written == 1
    assert result.alias_written == 0
    stored = session.store.read("new_york_city")
    assert _edges(stored) == [("see_also", "city")]
    # An *authored* see_also, carrying no demotion note, so relation-reconcile's tombstone
    # step leaves it where it is (D-65 removes only edges that were demoted to see_also).
    relation = stored.pos_entries[0].senses[0].relations[0]
    assert relation.note == module.SEE_ALSO_NOTE
    assert "demoted" not in relation.note


async def test_an_unrelated_short_entry_writes_nothing(session, tmp_path):
    session.store.write(_entry("World War II", [_sense(0, WAR_GLOSS)]))
    session.store.write(_entry("ii", [_sense(0, NUMERAL_GLOSS)]))
    path = _candidate_file(tmp_path, [("World War II", "ii")])

    result = await _aliases(session, path)

    assert result.calls == 1
    assert result.alias_none == 1
    assert _edges(session.store.read("world_war_ii")) == []


async def test_an_entry_already_linked_to_its_target_is_skipped_for_free(session, tmp_path):
    session.store.write(
        _entry(
            "Abraham Lincoln",
            [
                _sense(
                    0, LINCOLN_LONG_GLOSS, relations=[_relation(RelationType.SEE_ALSO, "Lincoln")]
                )
            ],
        )
    )
    session.store.write(_entry("Lincoln", [_sense(0, LINCOLN_GLOSS)]))
    path = _candidate_file(tmp_path, [("Abraham Lincoln", "lincoln")])

    result = await _aliases(session, path)

    assert result.calls == 0
    assert result.skipped_already_linked == 1
    assert result.alias_written == 0


async def test_a_missing_target_is_counted_and_costs_nothing(session, tmp_path):
    session.store.write(_entry("Abraham Lincoln", [_sense(0, LINCOLN_LONG_GLOSS)]))
    path = _candidate_file(tmp_path, [("Abraham Lincoln", "lincoln")])

    result = await _aliases(session, path)

    assert result.candidates == 1
    assert result.skipped_target_missing == 1
    assert result.calls == 0


async def test_an_entry_the_list_does_not_pair_is_not_a_candidate(session, tmp_path):
    session.store.write(_entry("Denver", [_sense(0, "The capital city of Colorado.")]))
    path = _candidate_file(tmp_path, [("Abraham Lincoln", "lincoln")])

    result = await _aliases(session, path)

    assert result.candidates == 0
    assert result.calls == 0


async def test_the_step_is_idempotent(session, tmp_path):
    session.store.write(_entry("New York City", [_sense(0, NYC_GLOSS)]))
    session.store.write(_entry("city", [_sense(0, CITY_GLOSS)]))
    path = _candidate_file(tmp_path, [("New York City", "city")])

    first = await _aliases(session, path)
    second = await _aliases(session, path)

    assert first.calls == 1
    # The free already-linked check catches it before the marker is even read, which is
    # what makes a second sweep cost nothing whatever the digest says.
    assert second.calls == 0
    assert second.skipped_already_linked == 1
    assert len(_edges(session.store.read("new_york_city"))) == 1


async def test_a_missing_candidate_file_makes_the_step_a_no_op(session, tmp_path):
    session.store.write(_entry("Abraham Lincoln", [_sense(0, LINCOLN_LONG_GLOSS)]))

    result = await _aliases(session, tmp_path / "absent.tsv")

    assert result.candidates == 0
    assert result.calls == 0


async def test_the_dry_run_plan_prices_the_calls_the_alias_step_would_buy(session, tmp_path):
    session.store.write(_entry("New York City", [_sense(0, NYC_GLOSS)]))
    session.store.write(_entry("city", [_sense(0, CITY_GLOSS)]))
    session.store.write(_entry("Abraham Lincoln", [_sense(0, LINCOLN_LONG_GLOSS)]))
    session.store.write(_entry("Lincoln", [_sense(0, LINCOLN_GLOSS)]))
    path = _candidate_file(tmp_path, [("New York City", "city"), ("Abraham Lincoln", "lincoln")])

    plan = plan_lexeme_hygiene(
        session.store,
        sorted(session.store.iter_ids()),
        only={LexemeHygieneStep.ALIASES},
        alias_list=path,
    )

    step = plan["steps"][LexemeHygieneStep.ALIASES]
    assert step["candidates"] == 2
    # One is settled for free by the short entry's own gloss; only the other is bought.
    assert step["kept_wordnet"] == 1
    assert step["calls_due"] == 1
    assert plan["estimated_calls"] == 1
