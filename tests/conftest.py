"""Shared fixtures.

The whole suite runs offline. ``scripted_model`` is a pydantic-ai ``FunctionModel`` that
inspects the output contract the agent asked for and returns a valid, deterministic
payload for it, with realistic token usage so cost accounting is exercised for real.

Dispatch is on the JSON-schema title (the contract class name), not the tool name:
pydantic-ai names every structured-output tool ``final_result``. The scripted payloads
parse the *prompt* the workflow built, so a change to a prompt builder that breaks the
contract between workflow and model shows up here rather than in production.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    ModelResponsePart,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from opengloss_generator.config import AppConfig, ConcurrencyConfig, StoreConfig
from opengloss_generator.runner import RunSession
from opengloss_generator.schema import (
    Example,
    Lexeme,
    LexemeKind,
    Morphology,
    PartOfSpeech,
    POSEntry,
    ReadingLevel,
    Register,
    Relation,
    RelationTarget,
    RelationType,
    Rendition,
    Renditions,
    Sense,
    canonical_rendition,
)

INPUT_TOKENS = 1_200
CACHED_TOKENS = 400
OUTPUT_TOKENS = 300

# The domain the scripted senses stage always returns, and the one the scripted
# tag_domain pass always returns. They differ so a test can tell which stage wrote a tag.
SCRIPTED_SENSE_DOMAIN = "education.general"
SCRIPTED_RETROFIT_DOMAIN = "nature.general"

# An example built from this template always contains the headword, so ``find_span``
# places it for free; ``UNPLACEABLE_EXAMPLE`` never does, so it falls through to the
# ``spans`` model stage. Generation exercises both paths on every entry.
PLACEABLE_EXAMPLE = "The {headword} appeared in example number {index}."
UNPLACEABLE_EXAMPLE = "Nothing here matches the entry, number {index}."

# Scripted rendition text. Short sentences and common words keep the measured
# Flesch-Kincaid grade of every default rendition under 3, which is inside the tightest
# band (grade_1), so no test pays for a readability retry it did not ask for.
SIMPLE_RENDITION = (
    "The {level} {register} text is here. It is a rewrite of the {field}. "
    "The words are short. Kids can read it. It is not hard."
)
SIMPLE_EXAMPLE = "The {headword} is here. A {level} {register} kid can read this. It is short."

# A headword whose grade_1 rendition is scripted to fail the readability check, and the
# unreadable text it produces. The retry prompt is recognised by the feedback marker.
COMPLEX_HEADWORD = "complexword"
COMPLEX_RENDITION = (
    "The extraordinarily sophisticated theoretical characterisation of {headword} "
    "necessitates comprehensive interdisciplinary methodological consideration "
    "throughout every subsequent investigative documentation exercise."
)
READABILITY_FEEDBACK_MARKER = "Measured Flesch-Kincaid"

# A headword whose gloss renditions are scripted to begin with the headword itself, and a
# second whose grade_1 rendition fails *both* generation-time checks at once (it opens
# with the headword and it is unreadable), so one test can watch the two share a single
# retry. The retry prompt is recognised by its own feedback marker.
INITIAL_HEADWORD = "initialword"
INITIAL_RENDITION = "{headword} is a thing that people use. It is easy to read."
BOTH_HEADWORD = "bothword"
BOTH_RENDITION = (
    "{headword} designates an extraordinarily sophisticated theoretical characterisation "
    "necessitating comprehensive interdisciplinary methodological consideration."
)
HEADWORD_FEEDBACK_MARKER = "began with the headword"

# A headword whose example renditions are scripted to never mention the headword at all
# (D-45), and the marker its retry-fixed text is recognised by. A prompt carrying the
# marker returns the simple example text instead, which does mention the headword, so a
# test can watch one miss and one fix, exactly as the two checks above do. The text is
# COMPLEX_RENDITION's own template with the headword slot replaced by unrelated words --
# same measured grade, so a target requested at grade_1 fails the readability check too,
# which is how one test watches this check share the single retry with that one, exactly
# as BOTH_HEADWORD does for the headword-initial check.
ABSENT_HEADWORD = "absentword"
ABSENT_RENDITION = (
    "The extraordinarily sophisticated theoretical characterisation of an entirely "
    "different subject necessitates comprehensive interdisciplinary methodological "
    "consideration throughout every subsequent investigative documentation exercise."
)
HEADWORD_ABSENT_FEEDBACK_MARKER = "did not use the word"

# A headword whose gloss renditions in any register but plain are scripted to echo the
# canonical gloss almost verbatim (D-59), and the marker its retry-fixed text is
# recognised by. A prompt carrying the marker returns SIMPLE_RENDITION instead, whose
# words share nothing with any source, so a test can watch one miss and one fix, exactly
# as the other generation-time checks do.
NEAR_COPY_HEADWORD = "nearcopyword"
NEAR_COPY_FEEDBACK_MARKER = "stayed too close to the source"

# A headword whose renditions come back wrapped in markdown and quoting the source text
# back, so one test can watch both directions of the markdown strip: what the model wrote
# and what it was shown.
MARKDOWN_HEADWORD = "markdownword"
MARKDOWN_RENDITION = "**The source was:** {source} It is `short` and _easy_."

# A headword whose scripted repair-pass sentence never mentions the headword at all, so a
# test can watch find_span leave the example's span as None for the spans pass to retry.
NO_SPAN_HEADWORD = "spanlessword"

# A headword whose scripted readability_hygiene rewrite comes back simple enough to land
# inside every reading level's band, mentioning the headword so an example rewrite still
# has somewhere for find_span to place it -- but never *opening* with it, since a gloss
# rewrite that does is refused (D-47). Any other headword gets its own offending text
# echoed straight back -- unchanged, so no simpler, so a test can watch the flag stay put.
READABILITY_FIX_HEADWORD = "readabilityfixword"
READABILITY_FIX_TEMPLATE = "It is small. It is easy. Kids like the {headword} a lot."

# A headword whose scripted readability_hygiene rewrite differs from the source but never
# mentions the headword either, so a test can watch an example's rewrite get discarded
# because find_span cannot place it, leaving the old example untouched.
READABILITY_LOSES_HEADWORD = "readabilitylosesword"
READABILITY_LOSES_TEXT = "Nothing here names the missing word at all, though it reads fine."

# A headword whose scripted readability_hygiene rewrite is simpler than the text it
# replaces but opens by naming the headword -- the regression D-47 measured, where making
# a hard definition easy produces "A ban is an order to stop." A test watches the pass
# refuse it and keep the old text and its readability flag.
READABILITY_INITIAL_HEADWORD = "readabilityinitialword"
READABILITY_INITIAL_TEXT = "A {headword} is a small thing. Kids like it a lot."

# The scripted QA judge's verdict. Every headword but :data:`QA_CLEAN_HEADWORD` gets a
# verdict with exactly one defect of each shape -- one sense dimension false, one
# rendition dimension false, an inaccurate encyclopedia -- so a test can assert the whole
# verdict-to-Assessment mapping from one call, and the entry score lands in the 60-79
# bucket. :data:`QA_CLEAN_HEADWORD` gets an all-true verdict scoring into the 90+ bucket,
# which is what lets a test watch the distribution and the defect rates move.
QA_CLEAN_HEADWORD = "cleanword"
QA_ENTRY_SCORE = 72
QA_CLEAN_ENTRY_SCORE = 95
QA_GLOSS_ISSUE = "the definition names the wrong instrument"
QA_INVALID_RELATION = "rope"
QA_RENDITION_ISSUE = "too many long words for this level"
QA_ENCYCLOPEDIA_ISSUE = "the founding date is wrong"
QA_NOTES = "Scripted verdict."

_LEVEL_RE = re.compile(r"reading_level=(\w+), register=(\w+)")
_NUMBERED_RE = re.compile(r"^ {2}(\d+)\. (.*)$", re.MULTILINE)


def _numbered(prompt: str) -> list[tuple[int, str]]:
    """Return the ``  N. text`` items of a prompt list, in order.

    Two-space indentation is what distinguishes a top-level list item from the nested
    candidate lines the resolve prompt indents further.
    """
    return [(int(number), text.strip()) for number, text in _NUMBERED_RE.findall(prompt)]


def _output_target(info: AgentInfo) -> tuple[str, Callable[[dict[str, Any]], ModelResponsePart]]:
    """Return the requested contract name and a part builder for the agent's output mode.

    Stages use ``NativeOutput`` (provider-side constrained decoding), so the agent asks for
    a JSON *text* response against ``output_object``; a plain ``output_type`` would instead
    expose an output tool. Supporting both keeps the scripted model honest for either mode.
    """
    if info.output_tools:
        tool = info.output_tools[0]
        contract = tool.parameters_json_schema.get("title", tool.name)
        return contract, lambda payload: ToolCallPart(tool.name, payload)
    obj = info.model_request_parameters.output_object
    assert obj is not None, "every stage in this project uses a structured output"
    contract = obj.json_schema.get("title") or obj.name or "unknown"
    return contract, lambda payload: TextPart(json.dumps(payload))


def _headword(prompt: str) -> str:
    """Extract the headword from a prompt body."""
    return _field(prompt, "Headword") or "unknown"


def _field(prompt: str, label: str) -> str | None:
    """Extract a ``Label: value`` line from a prompt body."""
    match = re.search(rf"^{re.escape(label)}: (.+)$", prompt, flags=re.MULTILINE)
    return match.group(1).strip() if match else None


def _overview_payload(prompt: str) -> dict[str, Any]:
    """Plan an entry: a capitalised headword is scripted as a proper noun."""
    headword = _headword(prompt)
    if headword[:1].isupper():
        return {
            "headword": headword,
            "kind": "proper_noun",
            "proper_noun": {"entity_type": "person", "wikidata_qid": "Q937"},
            "is_stopword": False,
            "domain": "physics",
            "pos_plans": [{"pos": "noun", "sense_count": 1, "note": "the person"}],
        }
    return {
        "headword": headword,
        "kind": "simplex",
        "proper_noun": None,
        "is_stopword": False,
        "domain": "general",
        "pos_plans": [
            {"pos": "noun", "sense_count": 2, "note": "concrete and abstract uses"},
            {"pos": "verb", "sense_count": 1, "note": "the action sense"},
        ],
    }


def _sense_set_payload(prompt: str) -> dict[str, Any]:
    """Write senses for one part of speech, with typed relations and one confusable."""
    headword = _headword(prompt)
    pos = _field(prompt, "Part of speech") or "noun"
    count = int(_field(prompt, "Number of senses to write") or 1)
    return {
        "pos": pos,
        "senses": [
            {
                "gloss": f"A test definition number {i} for the headword under test.",
                "examples": [
                    PLACEABLE_EXAMPLE.format(headword=headword, index=i),
                    UNPLACEABLE_EXAMPLE.format(index=i),
                ],
                "domain": SCRIPTED_SENSE_DOMAIN,
                "secondary_domains": [],
                "relations": [
                    {"type": "synonym", "term": f"synonym{i}"},
                    {"type": "synonym", "term": "shared_target"},
                    {"type": "antonym", "term": f"antonym{i}"},
                    {"type": "hypernym", "term": "broader_thing"},
                    {"type": "hyponym", "term": f"narrower_thing{i}"},
                ],
                "confusables": [
                    {
                        "term": f"confusable{i}",
                        "how_they_differ": "One names the act and the other names the tool.",
                    }
                ],
            }
            for i in range(count)
        ],
        "collocations": ["test collocation"],
        "derivations": ["derived_form"],
    }


def _rendition_set_payload(prompt: str) -> dict[str, Any]:
    """Rewrite one field for every requested target.

    The scripted text is deliberately simple: every default rendition measures well inside
    its band, so a test that is not about readability never triggers the regeneration pass
    and its call count stays predictable.

    Marker headwords drive the four generation-time checks. A prompt for
    :data:`COMPLEX_HEADWORD` returns an unreadable ``grade_1`` rendition; one for
    :data:`INITIAL_HEADWORD` returns renditions that open with the headword; one for
    :data:`BOTH_HEADWORD` returns a ``grade_1`` rendition that does both at once; one for
    :data:`ABSENT_HEADWORD` returns an ``examples`` rendition that never mentions the
    headword at all; one for :data:`NEAR_COPY_HEADWORD` returns a non-``plain``-register
    gloss rendition that echoes the canonical source almost verbatim (D-59). A prompt
    carrying any of the four feedback markers — which only a retry does — returns the
    simple text for every target it asks for, so each marker scripts one miss and one fix,
    and :data:`BOTH_HEADWORD` scripts a target that fails both checks and is fixed by the
    one retry they share. :data:`MARKDOWN_HEADWORD` returns markdown wrapped around the
    source text it was shown, which is how a test sees both ends of the markdown strip.
    """
    headword = _headword(prompt)
    field = _field(prompt, "Field") or "gloss"
    retrying = (
        READABILITY_FEEDBACK_MARKER in prompt
        or HEADWORD_FEEDBACK_MARKER in prompt
        or HEADWORD_ABSENT_FEEDBACK_MARKER in prompt
        or NEAR_COPY_FEEDBACK_MARKER in prompt
    )

    def content(level: str, register: str) -> str:
        if not retrying and headword == COMPLEX_HEADWORD and level == "grade_1":
            return COMPLEX_RENDITION.format(headword=headword)
        if not retrying and headword == BOTH_HEADWORD and level == "grade_1":
            return BOTH_RENDITION.format(headword=headword)
        if not retrying and headword == INITIAL_HEADWORD:
            return INITIAL_RENDITION.format(headword=headword)
        if not retrying and headword == ABSENT_HEADWORD and field == "examples":
            return ABSENT_RENDITION
        if (
            not retrying
            and headword == NEAR_COPY_HEADWORD
            and field == "gloss"
            and register != "plain"
        ):
            return _field(prompt, "Source") or ""
        if headword == MARKDOWN_HEADWORD:
            return MARKDOWN_RENDITION.format(source=_field(prompt, "Source") or "")
        template = SIMPLE_EXAMPLE if field == "examples" else SIMPLE_RENDITION
        return template.format(level=level, register=register, field=field, headword=headword)

    return {
        "renditions": [
            {"reading_level": level, "register": register, "content": content(level, register)}
            for level, register in _LEVEL_RE.findall(prompt)
        ]
    }


def _kind_batch_payload(prompt: str) -> dict[str, Any]:
    """Classify the ambiguous headwords: anything starting "kick" is scripted an idiom.

    Each listed item is ``term \u2014 gloss snippet``; the verdict echoes the term only,
    so the split here is what asserts the snippet is separable from the term.
    """
    terms = [item.split(" \u2014 ", 1)[0].strip() for _, item in _numbered(prompt)]
    return {
        "verdicts": [
            {"term": term, "kind": "idiom" if term.startswith("kick") else "compound"}
            for term in terms
        ]
    }


def _domain_tags_payload(prompt: str) -> dict[str, Any]:
    """Tag every sense the prompt listed with the scripted retrofit domain."""
    return {
        "tags": [
            {"sense_ref": number, "domain": SCRIPTED_RETROFIT_DOMAIN, "secondary_domains": []}
            for number, _ in _numbered(prompt)
        ]
    }


def _gloss_rewrite_payload(prompt: str) -> dict[str, Any]:
    """Rewrite every offending gloss the prompt listed, dropping the headword.

    Each listed item is ``[label] gloss``; the scripted rewrite is deterministic and
    provably headword-free, so a test can assert on its exact text.
    """
    return {
        "rewrites": [
            {"sense_ref": number, "gloss": f"Scripted rewrite number {number} of the definition."}
            for number, _ in _numbered(prompt)
        ]
    }


def _rendition_rewrite_payload(prompt: str) -> dict[str, Any]:
    """Rewrite every offending gloss rendition the prompt listed, dropping the headword.

    Each listed item is ``[level/register] text``; the scripted rewrite echoes the label
    back so a test can assert the pass held each rendition at the audience it was written
    for, and is provably not headword-initial whatever the headword is.
    """
    return {
        "rewrites": [
            {
                "rendition_ref": number,
                "text": f"Scripted rewrite of the {text.split(']')[0].lstrip('[')} rendition.",
            }
            for number, text in _numbered(prompt)
        ]
    }


def _readability_rewrite_payload(prompt: str) -> dict[str, Any]:
    """Rewrite every readability-band offender the prompt listed.

    Each listed item is ``[field level/register] (measured FK x.x, band limit y.y) text``.
    A prompt for :data:`READABILITY_FIX_HEADWORD` returns simple text mentioning the
    headword -- comfortably inside every band, and still findable by ``find_span`` if the
    offender is an example; one for :data:`READABILITY_INITIAL_HEADWORD` returns text that
    is just as simple but opens by naming the headword, which a gloss rewrite is refused
    for. Any other headword gets its own offending text echoed back unchanged, which the
    workflow will find no simpler than what is already stored.
    """
    headword = _headword(prompt)
    rewrites = []
    for number, item in _numbered(prompt):
        original = item.rsplit(") ", 1)[-1]
        if headword == READABILITY_FIX_HEADWORD:
            text = READABILITY_FIX_TEMPLATE.format(headword=headword)
        elif headword == READABILITY_INITIAL_HEADWORD:
            text = READABILITY_INITIAL_TEXT.format(headword=headword)
        elif headword == READABILITY_LOSES_HEADWORD:
            text = READABILITY_LOSES_TEXT
        else:
            text = original
        rewrites.append({"ref": number, "text": text})
    return {"rewrites": rewrites}


def _example_hygiene_rewrite_payload(prompt: str) -> dict[str, Any]:
    """Write a replacement sentence for every headword-absent example the pass listed.

    Each listed item is ``[level/register] (sense: gloss) text``. The scripted rewrite
    mentions the entry's own headword, so ``find_span`` places it -- except for
    :data:`NO_SPAN_HEADWORD`, reused from the repair pass's own scripted marker, whose
    rewrite still never mentions the headword, which is how a test watches the pass keep
    the old text and flag it instead.
    """
    headword = _headword(prompt)
    return {
        "rewrites": [
            {
                "ref": number,
                "text": (
                    "Nothing here names the missing word at all."
                    if headword == NO_SPAN_HEADWORD
                    else f"The {headword} showed up again in example {number}."
                ),
            }
            for number, _ in _numbered(prompt)
        ]
    }


def _repair_examples_payload(prompt: str) -> dict[str, Any]:
    """Write one scripted sentence per sense the repair pass asked an example for.

    Every sentence contains the headword, so ``find_span`` places it for free --
    except for :data:`NO_SPAN_HEADWORD`, whose scripted sentence never mentions the
    headword, which is how a test observes the pass keep a sentence with ``span=None``.
    """
    headword = _headword(prompt)
    return {
        "examples": [
            {
                "sense_ref": number,
                "sentences": (
                    ["Nothing here names the missing word at all."]
                    if headword == NO_SPAN_HEADWORD
                    else [f"The {headword} showed up again in sentence {number}."]
                ),
            }
            for number, _ in _numbered(prompt)
        ]
    }


def _qa_sections(prompt: str) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Split a QA prompt into its numbered sense list and its numbered rendition list.

    Both lists restart at 1, so they have to be separated before ``_numbered`` sees them
    or the refs would collide -- which is exactly the mistake the workflow must not make
    either.
    """
    senses, _, renditions = prompt.partition("\nRenditions (")
    return _numbered(senses), _numbered(renditions)


def _qa_verdict_payload(prompt: str) -> dict[str, Any]:
    """Judge the entry the prompt described, with one defect of each shape.

    Sense 1 fails ``gloss_accurate`` and ``relations_valid``; rendition 1 fails
    ``level_appropriate``; the encyclopedia is inaccurate. Everything else passes, so a
    test can assert each mapping independently. :data:`QA_CLEAN_HEADWORD` passes
    everything, which is the other end of every metric.
    """
    headword = _headword(prompt)
    senses, renditions = _qa_sections(prompt)
    clean = headword == QA_CLEAN_HEADWORD
    return {
        "entry_score": QA_CLEAN_ENTRY_SCORE if clean else QA_ENTRY_SCORE,
        "sense_verdicts": [
            {
                "sense_ref": number,
                "gloss_accurate": clean or number != 1,
                "gloss_issue": None if clean or number != 1 else QA_GLOSS_ISSUE,
                "distinct_from_other_senses": True,
                "examples_natural": True,
                "examples_fit_sense": True,
                "relations_valid": clean or number != 1,
                "invalid_relations": [] if clean or number != 1 else [QA_INVALID_RELATION],
                "domain_fits": True,
                "suggested_domain": None,
            }
            for number, _ in senses
        ],
        "rendition_verdicts": [
            {
                "rendition_ref": number,
                "faithful": True,
                "level_appropriate": clean or number != 1,
                "register_appropriate": True,
                "issue": None if clean or number != 1 else QA_RENDITION_ISSUE,
            }
            for number, _ in renditions
        ],
        "encyclopedia_accurate": clean,
        "encyclopedia_issue": None if clean else QA_ENCYCLOPEDIA_ISSUE,
        "flags": [] if clean else ["factual_error"],
        "notes": "" if clean else QA_NOTES,
    }


def _resolution_payload(prompt: str) -> dict[str, Any]:
    """Always choose the first candidate sense, with a high confidence."""
    return {
        "resolutions": [
            {"target_ref": number, "sense_choice": 0, "confidence": 0.9}
            for number, _ in _numbered(prompt)
        ]
    }


def _span_batch_payload(prompt: str) -> dict[str, Any]:
    """Place every example at its first three characters — valid, if not insightful."""
    return {
        "spans": [
            {"example_ref": number, "start": 0, "end": min(3, len(text))}
            for number, text in _numbered(prompt)
            if text
        ]
    }


def _frontier_payload(prompt: str) -> dict[str, Any]:
    """Accept every candidate except those whose term starts with ``reject_``."""
    verdicts = [
        {
            "term": term,
            "is_headword": not term.startswith("reject_"),
            "reason": "scripted verdict",
        }
        for _, term in _numbered(prompt)
    ]
    return {"verdicts": verdicts or [{"term": "none", "is_headword": False, "reason": "empty"}]}


_PAYLOADS = {
    "draftoverview": _overview_payload,
    "draftsenseset": _sense_set_payload,
    "draftrenditionset": _rendition_set_payload,
    "draftkindbatch": _kind_batch_payload,
    "draftdomaintags": _domain_tags_payload,
    "_draftglossrewritebatch": _gloss_rewrite_payload,
    "_draftrenditionrewritebatch": _rendition_rewrite_payload,
    "_draftreadabilityrewritebatch": _readability_rewrite_payload,
    "_draftexamplerewritebatch": _example_hygiene_rewrite_payload,
    "_draftrepairexamples": _repair_examples_payload,
    "draftqaverdict": _qa_verdict_payload,
    "draftresolution": _resolution_payload,
    "draftspanbatch": _span_batch_payload,
    "frontierjudgement": _frontier_payload,
    "draftetymology": lambda _: {
        "summary": "A test etymology summary long enough to satisfy the contract.",
        "segments": [{"language": "Latin", "form": "testum", "meaning": "a test"}],
        "cognates": ["testify"],
    },
    "draftencyclopedia": lambda _: {"text": "Encyclopedic prose about the headword. " * 8},
    "draftlexicalexplanation": lambda _: {
        "text": "You would reach for this word when you mean the tested sense of it."
    },
    "relatedterms": lambda _: {"terms": ["proposed_one", "proposed_two"]},
}


def _payload_for(contract: str, prompt: str) -> dict[str, Any]:
    """Return a valid payload for whichever output contract was requested."""
    builder = _PAYLOADS.get(contract.lower())
    if builder is None:
        raise AssertionError(f"scripted_model has no payload for contract {contract!r}")
    return builder(prompt)


def _last_user_text(messages: Sequence[ModelMessage]) -> str:
    """Return the text of the most recent user prompt."""
    for message in reversed(list(messages)):
        for part in getattr(message, "parts", []):
            if part.part_kind == "user-prompt":
                return str(part.content)
    return ""


@pytest.fixture
def scripted_model() -> FunctionModel:
    """Return an offline model that satisfies every stage contract."""

    def respond(messages: Sequence[ModelMessage], info: AgentInfo) -> ModelResponse:
        contract, reply = _output_target(info)
        payload = _payload_for(contract, _last_user_text(messages))
        return ModelResponse(
            parts=[reply(payload)],
            usage=RequestUsage(
                input_tokens=INPUT_TOKENS,
                cache_read_tokens=CACHED_TOKENS,
                output_tokens=OUTPUT_TOKENS,
            ),
            model_name="scripted",
        )

    return FunctionModel(respond)


@pytest.fixture
def failing_model() -> FunctionModel:
    """Return a model that always returns output failing schema validation."""

    def respond(messages: Sequence[ModelMessage], info: AgentInfo) -> ModelResponse:
        _, reply = _output_target(info)
        return ModelResponse(
            parts=[reply({"definitely": "wrong"})],
            usage=RequestUsage(input_tokens=10, output_tokens=10),
            model_name="failing",
        )

    return FunctionModel(respond)


@pytest.fixture
def config(tmp_path: Path) -> AppConfig:
    """Return a configuration pointing at a temporary store, with fsync off for speed."""
    return AppConfig(
        store=StoreConfig(root=tmp_path / "store", fsync_on_write=False),
        concurrency=ConcurrencyConfig(workers=4, requests_per_minute=10_000),
        log_dir=tmp_path / "runs",
        budget_usd=5.0,
    )


@pytest.fixture
async def session(config: AppConfig, scripted_model: FunctionModel):
    """Yield a live :class:`RunSession` wired to the scripted model."""
    async with RunSession(config, model_override=scripted_model, run_id="test-run") as active:
        yield active


def make_entry(headword: str = "abseil", *, variants: bool = False) -> Lexeme:
    """Build a small valid v3 entry for tests that do not need generation."""
    text = "They abseiled down the cliff."
    sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition("To descend a rock face using a rope.")]),
        examples=Renditions[Example](root=[canonical_rendition(Example(text=text, span=(5, 13)))]),
        relations=[
            Relation(type=RelationType.SYNONYM, target=RelationTarget(term="rappel")),
            Relation(type=RelationType.HYPERNYM, target=RelationTarget(term="descend")),
        ],
    )
    if variants:
        sense.gloss.add(
            Rendition[str](
                reading_level=ReadingLevel.GRADE_1,
                style=Register.PLAIN,
                content="To go down a big rock using a rope.",
            )
        )
    return Lexeme.empty(
        headword,
        kind=LexemeKind.SIMPLEX,
        pos_entries=[POSEntry(pos=PartOfSpeech.VERB, senses=[sense], morphology=Morphology())],
    )


def make_target(headword: str, *, senses: int = 2, pos: PartOfSpeech = PartOfSpeech.VERB) -> Lexeme:
    """Build an entry with a small sense inventory, for the resolver to choose from."""
    return Lexeme.empty(
        headword,
        kind=LexemeKind.SIMPLEX,
        pos_entries=[
            POSEntry(
                pos=pos,
                senses=[
                    Sense.of(index, f"Sense {index} of {headword}, written for the resolver.")
                    for index in range(senses)
                ],
            )
        ],
    )


# --------------------------------------------------------------------------------------
# workflows/content_hygiene.py contracts
# --------------------------------------------------------------------------------------
#
# Appended at the end of the file, and registered with ``_PAYLOADS.update`` rather than
# written into the dict literal above, deliberately: ``contracts.py``, ``prompts.py``,
# ``cli.py`` and this file's QA branch are being edited concurrently on this branch, and
# an append-only block cannot conflict with any of that. ``_payload_for`` looks its
# builder up at call time, so registering here works exactly as an inline entry would.

# Marker headwords for the ``degenerate_renditions`` step, mirroring the ones the
# renditions and readability payloads already use. One scripts a rewrite that opens by
# naming its own headword, which the step refuses (D-30/D-47's rule); the other scripts a
# rewrite that is still the canonical gloss verbatim, which the step refuses as not
# having fixed anything. Everything else gets a distinct, usable rendition.
DEGENERATE_INITIAL_HEADWORD = "degenerateinitialword"
DEGENERATE_ECHO_HEADWORD = "degenerateechoword"

_DEGENERATE_ROW_RE = re.compile(r"canonical: (.*?) \| current: ")


def _relation_choice_payload(prompt: str) -> dict[str, Any]:
    """Decide every synonym/hypernym contradiction the pass listed, deterministically.

    Each listed item carries ``target="<term>"``, and the verdict is a function of that
    term alone, so one call can script all three answers at once: a target beginning
    ``syn`` keeps the synonym, one beginning ``neither`` keeps neither, and anything else
    keeps the hypernym.
    """
    choices = []
    for number, item in _numbered(prompt):
        match = re.search(r'target="([^"]*)"', item)
        term = (match.group(1) if match else "").lower()
        if term.startswith("syn"):
            keep = "synonym"
        elif term.startswith("neither"):
            keep = "neither"
        else:
            keep = "hypernym"
        choices.append({"ref": number, "keep": keep})
    return {"choices": choices}


def _stilted_rewrite_payload(prompt: str) -> dict[str, Any]:
    """Replace every stilted canonical example the pass listed with an everyday one.

    The scripted sentence mentions the entry's own headword, so ``find_span`` places it --
    except for :data:`NO_SPAN_HEADWORD`, reused from the repair and example-hygiene
    payloads, whose replacement never mentions the headword, which is how a test watches
    the pass refuse a rewrite and keep the old text.
    """
    headword = _headword(prompt)
    return {
        "rewrites": [
            {
                "ref": number,
                "text": (
                    "Nothing here names the missing word at all."
                    if headword == NO_SPAN_HEADWORD
                    else f"We talked about the {headword} over dinner, number {number}."
                ),
            }
            for number, _ in _numbered(prompt)
        ]
    }


def _degenerate_rewrite_payload(prompt: str) -> dict[str, Any]:
    """Rewrite every degenerate gloss rendition the pass listed.

    Each listed item is ``[level/register] canonical: <gloss> | current: <text>``. A
    prompt for :data:`DEGENERATE_INITIAL_HEADWORD` returns text that opens by naming the
    headword; one for :data:`DEGENERATE_ECHO_HEADWORD` returns the canonical gloss
    verbatim. Both are refused by the pass, which is how a test watches each rejection.
    Anything else gets a distinct, usable rendition carrying its own target's label.
    """
    headword = _headword(prompt)
    rewrites = []
    for number, item in _numbered(prompt):
        label = item.split("]")[0].lstrip("[")
        canonical = _DEGENERATE_ROW_RE.search(item)
        if headword == DEGENERATE_INITIAL_HEADWORD:
            text = f"{headword} is a small thing that people like a lot."
        elif headword == DEGENERATE_ECHO_HEADWORD and canonical is not None:
            text = canonical.group(1)
        else:
            text = f"A short way of putting it for {label} readers, number {number}."
        rewrites.append({"ref": number, "text": text})
    return {"rewrites": rewrites}


_PAYLOADS.update(
    {
        "_draftrelationchoices": _relation_choice_payload,
        "_draftstiltedrewrites": _stilted_rewrite_payload,
        "_draftdegeneraterewrites": _degenerate_rewrite_payload,
    }
)


# --------------------------------------------------------------------------------------
# workflows/relation_hygiene.py contracts
# --------------------------------------------------------------------------------------
#
# Appended after the ``content_hygiene`` block for the same reason that one was appended
# rather than written into the dict literal above: ``contracts.py``, ``prompts.py``,
# ``readability.py`` and ``enrich.py`` are being edited concurrently on this branch, and
# an append-only block at the end of this file cannot conflict with any of that.
# ``_payload_for`` looks its builder up at call time, so registering here works exactly as
# an inline entry would.

# Marker targets for the ``validity`` step. The verdict is a function of the target term
# listed in each row, so one call scripts every answer the step can act on: a target named
# :data:`RELATION_INVALID_TARGET` comes back invalid with no better type (the demotion
# path), one named :data:`RELATION_RETYPE_TARGET` comes back invalid with a better type
# (the retype path), and everything else comes back valid and untouched. That last case is
# what makes the step's idempotence testable: a second sweep judges only the survivors,
# passes them all, and writes the same marker digest it wrote the first time.
RELATION_INVALID_TARGET = "invalidword"
RELATION_RETYPE_TARGET = "retypeword"
RELATION_RETYPE_TO = "hypernym"

_RELATION_TARGET_RE = re.compile(r'target="([^"]*)"')


def _relation_verdict_payload(prompt: str) -> dict[str, Any]:
    """Judge every relation the pass listed, deterministically, by its target term."""
    verdicts = []
    for number, item in _numbered(prompt):
        match = _RELATION_TARGET_RE.search(item)
        term = (match.group(1) if match else "").lower()
        if term == RELATION_INVALID_TARGET:
            verdict = {"ref": number, "valid": False, "better_type": None}
        elif term == RELATION_RETYPE_TARGET:
            verdict = {"ref": number, "valid": False, "better_type": RELATION_RETYPE_TO}
        else:
            verdict = {"ref": number, "valid": True, "better_type": None}
        verdicts.append(verdict)
    return {"verdicts": verdicts}


_PAYLOADS.update({"_draftrelationverdicts": _relation_verdict_payload})


# --------------------------------------------------------------------------------------
# vocabulary.py / workflows/vocabulary_hygiene.py contracts (D-51)
# --------------------------------------------------------------------------------------
#
# Appended, and registered with ``_PAYLOADS.update`` rather than written into the dict
# literal above, for the reason the previous block gives: several modules are being edited
# concurrently on this branch and an append-only block cannot conflict with any of them.
# The renditions entry is *replaced* here rather than edited in place, by a wrapper that
# delegates to the original builder for everything but its own marker headword.

# A headword whose grade_1/grade_5 renditions are scripted to use words no six-year-old
# knows while keeping sentences short enough to pass the Flesch-Kincaid band outright --
# which is the whole defect D-51 exists for, and what makes this marker different from
# :data:`COMPLEX_HEADWORD`, whose text fails both checks. A prompt carrying the vocabulary
# feedback marker -- which only a retry does -- falls through to the simple text, so the
# marker scripts one miss and one fix.
HARD_VOCAB_HEADWORD = "hardvocabword"
HARD_VOCAB_RENDITION = (
    "Monks made vows. Poverty was one vow. Chastity was next. Obedience too. "
    "Ancient oaths bound novices. The men lived in a big house. They did it for years."
)
VOCABULARY_FEEDBACK_MARKER = "too hard for"

# A headword whose scripted vocabulary_hygiene rewrite is made of familiar words and
# mentions the headword without opening on it, so a gloss rewrite is accepted and an
# example rewrite still has somewhere for ``find_span`` to place the word. Any other
# headword gets its own offending text echoed straight back -- no simpler, so a test can
# watch the rewrite refused and the flag stay put.
VOCAB_FIX_HEADWORD = "vocabfixword"
VOCAB_FIX_TEMPLATE = "It is a big day. People say yes to the {headword} and keep it."

# A headword whose scripted vocabulary_hygiene rewrite uses familiar words but opens by
# naming the headword -- D-47's regression, refused for a gloss.
VOCAB_INITIAL_HEADWORD = "vocabinitialword"
VOCAB_INITIAL_TEXT = "A {headword} is a big yes that you keep."

# A headword whose scripted vocabulary_hygiene rewrite is made of familiar words but never
# mentions the headword, so an example rewrite is refused (D-45).
VOCAB_LOSES_HEADWORD = "vocablosesword"
VOCAB_LOSES_TEXT = "It is a big day and the boy keeps his word all year."

_BASE_RENDITION_PAYLOAD = _rendition_set_payload


def _rendition_set_payload_with_vocabulary(prompt: str) -> dict[str, Any]:
    """Rewrite one field for every requested target, honouring D-51's marker headword.

    Everything but :data:`HARD_VOCAB_HEADWORD` is delegated to the original builder
    unchanged. That headword's first answer is short-sentenced but full of words a
    six-year-old does not know, so only the vocabulary check can be what makes it a miss;
    the retry -- recognised by the vocabulary feedback marker -- falls through to the
    simple text like every other fix.
    """
    if _headword(prompt) != HARD_VOCAB_HEADWORD or VOCABULARY_FEEDBACK_MARKER in prompt:
        return _BASE_RENDITION_PAYLOAD(prompt)
    return {
        "renditions": [
            {"reading_level": level, "register": register, "content": HARD_VOCAB_RENDITION}
            for level, register in _LEVEL_RE.findall(prompt)
        ]
    }


def _vocabulary_rewrite_payload(prompt: str) -> dict[str, Any]:
    """Rewrite every vocabulary offender the pass listed.

    Each listed item is ``[field level/register] (too hard: words) text``. The three
    marker headwords script the three ways a rewrite is refused and the one way it is
    accepted; anything else gets its own text echoed back, which is no simpler than what
    is stored and so is refused as well.
    """
    headword = _headword(prompt)
    rewrites = []
    for number, item in _numbered(prompt):
        original = item.rsplit(") ", 1)[-1]
        if headword == VOCAB_FIX_HEADWORD:
            text = VOCAB_FIX_TEMPLATE.format(headword=headword)
        elif headword == VOCAB_INITIAL_HEADWORD:
            text = VOCAB_INITIAL_TEXT.format(headword=headword)
        elif headword == VOCAB_LOSES_HEADWORD:
            text = VOCAB_LOSES_TEXT
        else:
            text = original
        rewrites.append({"ref": number, "text": text})
    return {"rewrites": rewrites}


_PAYLOADS.update(
    {
        "draftrenditionset": _rendition_set_payload_with_vocabulary,
        "_draftvocabularyrewritebatch": _vocabulary_rewrite_payload,
    }
)


# --------------------------------------------------------------------------------------
# workflows/sense_hygiene.py contracts (D-52)
# --------------------------------------------------------------------------------------
#
# Appended after the vocabulary block for the reason each earlier block gives: several
# modules are being edited concurrently on this branch, and an append-only block at the
# end of this file cannot conflict with any of that. ``_payload_for`` looks its builder up
# at call time, so registering here works exactly as an inline entry would.

# Markers for the two steps, all of them functions of the *listed row* rather than of the
# headword, so one entry can script several answers at once -- which is what the pass's
# own per-entry, many-refs-per-call shape needs. A sense whose gloss carries
# :data:`SENSE_DUPLICATE_MARKER` is grouped with every other sense that does (including
# one under a different part of speech, which is how a test watches the cross-POS refusal);
# an example whose text carries :data:`SENSE_FIT_NONE_MARKER` fits no sense; one carrying
# "belongs to sense N" is placed under sense N; anything else stays where it is filed.
SENSE_DUPLICATE_MARKER = "duplicate"
SENSE_FIT_NONE_MARKER = "fits no sense"

_SENSE_FIT_MOVE_RE = re.compile(r"belongs to sense (\d+)")
_FILED_UNDER_RE = re.compile(r"\(sense (\d+)\)")


def _duplicate_groups_payload(prompt: str) -> dict[str, Any]:
    """Group every listed sense whose gloss carries the duplicate marker.

    Each listed item is ``[pos] gloss | example: text``. One group is reported when two or
    more senses carry the marker and none at all otherwise, so a test controls the whole
    answer through the glosses it writes.
    """
    marked = [
        number for number, item in _numbered(prompt) if SENSE_DUPLICATE_MARKER in item.lower()
    ]
    return {"duplicate_groups": [marked] if len(marked) > 1 else []}


def _example_placements_payload(prompt: str) -> dict[str, Any]:
    """Place every listed example, deterministically, by what its own text says.

    The prompt carries two lists numbered from one, so the example list is split off first
    exactly as ``_qa_sections`` does it -- which is also what asserts the pass numbered the
    two independently, the mistake it must not make either.
    """
    _, _, examples = prompt.partition("\nExamples (")
    placements = []
    for number, item in _numbered(examples):
        filed = _FILED_UNDER_RE.search(item)
        moved = _SENSE_FIT_MOVE_RE.search(item)
        if SENSE_FIT_NONE_MARKER in item.lower():
            best = None
        elif moved is not None:
            best = int(moved.group(1))
        else:
            best = int(filed.group(1)) if filed else None
        placements.append({"example_ref": number, "best_sense_ref": best})
    return {"placements": placements}


_PAYLOADS.update(
    {
        "_draftduplicategroups": _duplicate_groups_payload,
        "_draftexampleplacements": _example_placements_payload,
    }
)


# --------------------------------------------------------------------------------------
# workflows/content_hygiene.py: fragment_examples (D-49 addendum)
# --------------------------------------------------------------------------------------
#
# Appended after every earlier block for the reason each one gives: ``content_hygiene.py``
# is the only module this addendum touches, but several other modules are still being
# edited concurrently on this branch, and an append-only block at the end of this file
# cannot conflict with any of that. ``_payload_for`` looks its builder up at call time, so
# registering here works exactly as an inline entry would.

# A headword whose scripted fragment_examples rewrite is itself still a fragment -- no
# terminal punctuation -- which is how a test watches the step refuse a rewrite that did
# not repair the defect it was asked to repair. :data:`NO_SPAN_HEADWORD`, reused from the
# stilted-examples payload above, covers the other rejection: a rewrite that never
# mentions the headword at all.
FRAGMENT_STILL_HEADWORD = "stillfragmentword"


def _fragment_rewrite_payload(prompt: str) -> dict[str, Any]:
    """Replace every fragment canonical example the pass listed with a complete sentence.

    The scripted sentence mentions the entry's own headword and is properly capitalised
    and terminally punctuated, so both this step's own fragment check and ``find_span``
    pass -- except for :data:`FRAGMENT_STILL_HEADWORD` (still a fragment) and
    :data:`NO_SPAN_HEADWORD` (never mentions the headword).
    """
    headword = _headword(prompt)
    return {
        "rewrites": [
            {
                "ref": number,
                "text": (
                    "Nothing here names the missing word at all."
                    if headword == NO_SPAN_HEADWORD
                    else f"we still have not fixed the {headword}"
                    if headword == FRAGMENT_STILL_HEADWORD
                    else f"We saw the {headword} again in example {number}."
                ),
            }
            for number, _ in _numbered(prompt)
        ]
    }


_PAYLOADS.update({"_draftfragmentrewrites": _fragment_rewrite_payload})


# --------------------------------------------------------------------------------------
# workflows/examples.py contracts (D-53)
# --------------------------------------------------------------------------------------
#
# Appended after every earlier block for the reason each of them gives: several modules are
# being edited concurrently on this branch, and an append-only block at the end of this file
# cannot conflict with any of that. ``_payload_for`` looks its builder up at call time, so
# registering here works exactly as an inline entry would.
#
# The generation payload is a function of the *listed senses and targets* rather than of the
# headword alone, because that is the shape the workflow's one-call-per-entry design needs:
# one prompt asks for ``per_sense`` sentences for every live sense at once, and a test has to
# be able to script an acceptable answer, a duplicate, an over-long sentence, one that never
# names the headword and one that is a definition in disguise, all inside a single answer.

#: The scripted acceptable sentence. Six words, all of them on the Dale-Chall familiar-word
#: list, one sentence, opening capital and terminal full stop, and it names the headword --
#: so it passes every deterministic acceptance rule at every reading level and register,
#: including the tightest (``grade_1``: at most ten words, Flesch-Kincaid under 4.5, at most
#: 15% unfamiliar words). ``tag`` is what makes each sentence's first three words unique,
#: which the repeated-opening rule requires.
EXAMPLE_SENTENCE = "Kid {tag} saw the {headword} today."

#: The sentence scripted for each of the five defect slots of :data:`EXAMPLES_MIXED_HEADWORD`.
#: Each is refused by exactly one rule, and by the first rule that applies to it, so a test
#: can assert the whole ``rejected_by_reason`` map from one call.
EXAMPLE_TOO_LONG = (
    "Every single one of the people who had gathered along the road that morning stood "
    "and watched the {headword} go slowly past them without saying anything at all."
)
EXAMPLE_NO_HEADWORD = "Nothing in this sentence names the entry at all."
EXAMPLE_GLOSS_SHAPED = "A {headword} is a small thing that kids like a lot."

#: A headword whose scripted answer carries one of each defect: slot 1 repeats slot 0
#: verbatim, slot 2 is over the word cap, slot 3 never names the headword, slot 4 is a
#: definition wearing a sentence's clothes, and every other slot is acceptable.
EXAMPLES_MIXED_HEADWORD = "mixedexampleword"

#: A headword every one of whose scripted sentences opens on the same three words while
#: differing after them, so a test can watch the repeated-opening rule keep the first and
#: refuse the rest.
EXAMPLES_ECHO_HEADWORD = "echoexampleword"
EXAMPLE_ECHO_SENTENCE = "Kid A saw the {headword} on day {tag}."

#: Written into a sentence to steer the scripted sense-fit checker; see
#: :func:`_sentence_fits_payload`.
EXAMPLES_FIT_RE = re.compile(r"fits sense (\d+)")

_EXAMPLE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _example_tag(index: int) -> str:
    """Return a short, unique tag for the ``index``-th sentence of one scripted answer."""
    letter = _EXAMPLE_ALPHABET[index % len(_EXAMPLE_ALPHABET)]
    return letter if index < len(_EXAMPLE_ALPHABET) else f"{letter}{index // 26}"


def _example_text(headword: str, index: int, slot: int) -> str:
    """Return the scripted sentence for one (sense, target) slot.

    Args:
        headword: The entry's surface form, which drives which defects are scripted.
        index: The sentence's position in the whole answer, which makes its opening
            unique.
        slot: Its position within its own sense's target list, which selects the defect
            for :data:`EXAMPLES_MIXED_HEADWORD`.

    Returns:
        One sentence, acceptable unless a marker headword scripts otherwise.
    """
    if headword == EXAMPLES_ECHO_HEADWORD:
        return EXAMPLE_ECHO_SENTENCE.format(headword=headword, tag=_example_tag(index))
    if headword == EXAMPLES_MIXED_HEADWORD:
        if slot == 1:  # an exact repeat of slot 0, which is already accepted
            return EXAMPLE_SENTENCE.format(headword=headword, tag=_example_tag(index - 1))
        if slot == 2:
            return EXAMPLE_TOO_LONG.format(headword=headword)
        if slot == 3:
            return EXAMPLE_NO_HEADWORD
        if slot == 4:
            return EXAMPLE_GLOSS_SHAPED.format(headword=headword)
    return EXAMPLE_SENTENCE.format(headword=headword, tag=_example_tag(index))


def _example_batch_payload(prompt: str) -> dict[str, Any]:
    """Write one sentence for every (listed sense, listed target) pair.

    The senses are the prompt's numbered list and the targets are its
    ``reading_level=…, register=…`` lines, so this payload asserts the prompt builder
    still emits both in the shape the workflow's own answer parser expects.
    """
    targets = _LEVEL_RE.findall(prompt)
    headword = _headword(prompt)
    examples = []
    for position, (sense_ref, _) in enumerate(_numbered(prompt)):
        for slot, (level, register) in enumerate(targets):
            examples.append(
                {
                    "sense_ref": sense_ref,
                    "reading_level": level,
                    "register": register,
                    "text": _example_text(headword, position * len(targets) + slot, slot),
                }
            )
    return {"examples": examples}


def _sentence_fits_payload(prompt: str) -> dict[str, Any]:
    """Answer which sense each listed sentence illustrates.

    Every sentence is judged to illustrate **sense 1** unless its own text names another
    one ("fits sense 2"). The scripted sentences name none, so an entry with two live
    senses has all of the second sense's sentences refiled onto the first and therefore
    dropped, which is exactly the path the sense-fit check exists for; a one-sense entry
    never reaches this call at all.

    The prompt carries two lists numbered from one, so the sentence list is split off
    first exactly as ``_qa_sections`` and ``_example_placements_payload`` do it -- which
    is also what asserts the workflow numbered the two lists independently.
    """
    _, _, sentences = prompt.partition("\nSentences (")
    fits = []
    for number, item in _numbered(sentences):
        named = EXAMPLES_FIT_RE.search(item)
        fits.append({"sentence_ref": number, "best_sense_ref": int(named.group(1)) if named else 1})
    return {"fits": fits}


_PAYLOADS.update(
    {
        "draftexamplebatch": _example_batch_payload,
        "_draftsentencefits": _sentence_fits_payload,
    }
)
