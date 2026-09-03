"""Prompt text.

Two rules govern everything in this module, both of them about cost:

1. **Instructions are static and byte-stable.** Provider prompt caching is a prefix
   match, so a timestamp, a headword, or a re-ordered list inside the instructions
   destroys the cache for every call in the stage. Instructions are module constants for
   exactly this reason — nothing here is an f-string over run-time data.
   :data:`~opengloss_generator.taxonomy.TAXONOMY_PROMPT_BLOCK` is the largest single
   instance of the rule: roughly 1.5K tokens of leaf list that belongs in the
   ``tag_domain`` *instructions* and must never be pasted into a per-call prompt.
2. **Volatile input goes in the user prompt, last.** The builders below produce the
   per-call half.

``PROMPT_VERSION`` is recorded in provenance, so a change here is visible in the data it
produced. Bump it whenever instruction text changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from opengloss_generator.contracts import QA_MAX_SENSES
from opengloss_generator.taxonomy import TAXONOMY_PROMPT_BLOCK

if TYPE_CHECKING:
    from collections.abc import Sequence

    from opengloss_generator.schema import Lexeme, ReadingLevel, Register

__all__ = [
    "CLASSIFY_KIND_INSTRUCTIONS",
    "ENCYCLOPEDIA_INSTRUCTIONS",
    "ETYMOLOGY_INSTRUCTIONS",
    "EXAMPLES_INSTRUCTIONS",
    "FRONTIER_INSTRUCTIONS",
    "LEXICAL_EXPLANATION_INSTRUCTIONS",
    "OVERVIEW_INSTRUCTIONS",
    "PROMPT_VERSION",
    "QA_INSTRUCTIONS",
    "QA_TEXT_WORD_LIMIT",
    "RELATED_TERMS_INSTRUCTIONS",
    "RENDITIONS_INSTRUCTIONS",
    "RESOLVE_INSTRUCTIONS",
    "SENSES_INSTRUCTIONS",
    "SPANS_INSTRUCTIONS",
    "TAG_DOMAIN_INSTRUCTIONS",
    "ExampleSenseView",
    "QARenditionView",
    "QASenseView",
    "RenditionMiss",
    "ResolveCandidate",
    "ResolveTarget",
    "SenseView",
    "build_classify_kind_prompt",
    "build_encyclopedia_prompt",
    "build_etymology_prompt",
    "build_examples_prompt",
    "build_frontier_prompt",
    "build_headword_absent_feedback",
    "build_headword_initial_feedback",
    "build_lexical_explanation_prompt",
    "build_near_copy_feedback",
    "build_overview_prompt",
    "build_qa_prompt",
    "build_readability_feedback",
    "build_related_terms_prompt",
    "build_renditions_prompt",
    "build_resolve_prompt",
    "build_senses_prompt",
    "build_spans_prompt",
    "build_tag_domain_prompt",
    "build_vocabulary_feedback",
]

PROMPT_VERSION = "8"

# ``(sense_ref, part of speech, canonical gloss, one existing example)`` for one live
# sense shown to the example-writing stage. ``sense_ref`` is the number the sense is
# listed under and the number the answer refers back to, never a stored sense id.
type ExampleSenseView = tuple[int, str, str, str]
# ``(label, canonical gloss)`` for one candidate sense of a relation target.
type ResolveCandidate = tuple[str, str]
# ``(relation type, target term, source gloss, candidate senses)``.
type ResolveTarget = tuple[str, str, str, "Sequence[ResolveCandidate]"]
# ``(label, canonical gloss)`` for one sense being tagged.
type SenseView = tuple[str, str]
# ``(reading level, measured Flesch-Kincaid grade, the level's upper limit)``.
type RenditionMiss = tuple["ReadingLevel", float, float]
# ``(label, canonical gloss, examples, relations as "type->term", domain)`` for one
# sense shown to the judge.
type QASenseView = tuple[str, str, "Sequence[str]", "Sequence[str]", str]
# ``(label, text)`` for one sampled rendition shown to the judge. Its ``rendition_ref``
# is its position in the list, never a stored id: the judge cannot invent a ref for a
# rendition it was not shown.
type QARenditionView = tuple[str, str]

#: How many words of any one long text the judge is shown. Caps the volatile half of
#: the QA prompt: an encyclopedia section runs 200-400 words per rendition and there
#: are three of them in the sample, which alone would triple the prompt.
QA_TEXT_WORD_LIMIT = 120

_SHARED_STANCE = """\
You are a computational lexicographer building an open English lexical knowledge graph.

Follow WordNet's pragmatic stance: prefer what is computationally useful and clear to a \
learner over what is traditionally correct in print lexicography. Proper nouns, \
inflected forms, and multi-word units are all in scope when they are pedagogically \
useful.

Write plainly. Do not hedge, do not editorialise, and never mention that you are a \
language model or describe your own process."""

OVERVIEW_INSTRUCTIONS = f"""{_SHARED_STANCE}

Your task is to plan an entry. For the given headword, decide:

- what kind of lexical item it is: simplex (one ordinary word), compound, phrasal_verb, \
idiom, proper_noun, abbreviation, affix (a prefix or suffix), or function_word;
- if and only if it is a proper noun, what kind of entity it names, and its Wikidata \
item id if you are confident of it (leave the id null rather than guessing);
- which parts of speech it genuinely supports in ordinary modern English usage;
- how many distinct senses each part of speech needs (1 for a monosemous word, more only \
when the senses are genuinely distinct rather than shades of one meaning);
- whether it is a function word (stopword);
- its primary subject domain in ordinary words, if it clearly has one.

Do not list a part of speech that only appears in rare or archaic usage. Do not pad the \
sense count; a typical common noun has 1-3 senses, not 6."""

SENSES_INSTRUCTIONS = f"""{_SHARED_STANCE}

Your task is to write the senses for one part of speech of one headword.

For each sense:
- the gloss is a single sentence that could stand alone in a dictionary; it does not \
begin with the headword and it does not begin with an article plus the headword;
- examples are natural sentences a person would actually write or say, not corpus-style \
or academic-register constructions, and each one must actually contain the headword or \
an inflected form of it;
- choose one domain tag for the sense, and at most two secondary tags only when the \
sense genuinely straddles domains;
- relations are a single list of typed links. Use the type that is actually true: \
synonym, antonym, hypernym (a broader term), hyponym (a narrower term), meronym (a \
part), holonym (a whole), derivation, collocation, see_also, causes, entails, used_with, \
instance_of. Do not pad: eight good relations beat twenty weak ones, and a wrong type is \
worse than a missing relation;
- every relation target is a single lexical item in its base form — a term that could \
itself be a dictionary headword — not a phrase and not a definition;
- do not use confusable_with in the relations list. Confusable terms go in the separate \
confusables field, where each entry says in one clause how the two differ. Give at most \
three, and only where a learner would genuinely mix them up.

Senses must be mutually distinct. If you cannot make two senses genuinely different, \
return one sense."""

RENDITIONS_INSTRUCTIONS = f"""{_SHARED_STANCE}

Your task is to rewrite one piece of text for several audiences at once.

You will be given the field being rewritten, the source text, any rewrites that already \
exist, and a list of (reading level, register) targets. Produce exactly one rewrite per \
target, and nothing for a target you were not given.

A rewrite is written FOR ITS AUDIENCE. It is not a paraphrase of the source. Read the \
source, work out what it means, then set its sentences aside and write what a reader at \
that level would understand on first reading. Reordering the source's clauses, swapping \
a few words for synonyms, or dropping a subordinate clause is not a rewrite: a rewrite \
that could be mistaken for the source with two words changed has failed.

Never begin a definition rendition with the headword, with "the word X", or with "X is"; \
start with the meaning itself, as a dictionary does.

Formatting, for every field and every target: plain prose, no markdown. No bold, no \
italics, no backticks, no bullets, no headings, no numbered lists, and no asterisks or \
underscores used for emphasis. Write sentences and nothing else.

READING LEVELS. These are hard constraints, not suggestions.

neutral - no particular audience; ordinary reference prose.

grade_1 - a six-year-old who has been reading for about a year.
  * Every sentence is at most 10 words. Six to eight is better.
  * Only very common words: words a six-year-old already says out loud.
  * No number larger than ten, and write numbers as words - "three", not "3".
  * No symbols, no formulas, no units, no abbreviations. Never "m/s^2", "kg", "%", "$", \
"e.g.", "a = dv/dt". Say "how fast it speeds up", not "the rate of change of velocity".
  * Concrete nouns: things a child can see, hold, hear, or do. An abstract idea has to \
arrive attached to something physical.
  * No parenthetical asides, no dashes carrying a second thought, no semicolons, and no \
subordinate clauses stacked on one another.
  * Three short sentences beat one long one. Repeating a plain noun beats a pronoun the \
child has to resolve.

grade_5 - a ten-year-old.
  * Every sentence is at most 16 words.
  * Everyday vocabulary. Exactly one technical word is allowed, and only when the very \
next clause explains it in ordinary words: "friction, which is the rubbing that slows \
things down".
  * No formulas and no symbolic notation. A plain number is fine.
  * One clause of elaboration per sentence; two makes it a grade_10 sentence.

grade_10 - a fifteen-year-old. Ordinary adult prose, the register of a good newspaper. \
Abstraction is fine and subordinate clauses are fine; unglossed jargon is not, so a \
technical term gets a short explanation the first time it appears.

college - an adult general reader willing to concentrate. Be precise. The field's own \
vocabulary is allowed and is usually better than a circumlocution. Do not pad: precision \
is the point, not length.

REGISTERS. Each register below is shown against the same plain baseline, so you can see \
how far it has to move.

plain - neutral reference prose.
  plain: A deadline is the time by which a task must be finished.

informal - how you would explain it to a friend; contractions welcome, second person is \
fine.
  informal: A deadline is just the moment the thing has to be done by, and after that \
you're late.

technical - precise, uses the field's terminology, no simplification.
  technical: A deadline is the specified instant after which a deliverable is treated as \
overdue under the governing schedule.

formal - formal written register as in a reference work or official document: neutral, \
direct, no slang and no flourish.
  formal: A deadline is the date and time by which a deliverable must be submitted.

slang - casual, in-group speech; loose and unguarded, using words a formal source never \
would.
  slang: A deadline's whenever you've gotta have the thing done, no excuses.

in_house - an organisation's own internal terminology: the shorthand insiders use, not \
what an outsider would recognise.
  in_house: A deadline is the date on the ticket after which it auto-escalates to the \
duty manager.

marketing - benefit-led and vivid, but never overstated and never inaccurate.
  marketing: A deadline is the finish line that turns a good intention into finished work.

A register rewrite of the gloss must land at a lexical diversity of 0.30-0.60 against the \
canonical gloss you were given — measured as 1 minus the overlap of the two sentences' \
content words. Below 0.30 you have copied the source with a synonym or two swapped in; \
above 0.60 you have likely drifted from its meaning. Never reuse the canonical gloss's \
own sentence verbatim, or with only a word or two changed: read it, understand it, then \
write the definition again from scratch, in the vocabulary and rhythm the register calls \
for. "A deadline is a time by which a task must be finished." next to "A deadline is the \
time by which a task must be finished" is not a rewrite, whatever register it is labelled \
with.

WHAT THE FIELD MEANS FOR YOUR OUTPUT.

gloss - one dictionary sentence defining the headword, at that level and register.

examples - one fresh, natural sentence using the headword, of the kind a reader at that \
level would actually meet. A child's example belongs in a kitchen, a playground or a \
bedroom, not in a laboratory. Do not open with "Researchers", "Scientists", "Studies", \
"Experts" or any other academic framing unless the sense itself is academic. The sentence \
must contain the headword or an inflected form of it. Do not reuse the source example's \
situation; invent one that fits the audience.

encyclopedia - the whole passage, rewritten at that level. Keep the same facts in the \
same order. Keep roughly the same length at grade_10 and college; at grade_1 and grade_5 \
a shorter passage of short sentences is correct, because the constraints above bind.

explanation - a two or three sentence usage note, at that level.

WORKED EXAMPLE. The source is the gloss of one sense of "friction": "The force that \
resists motion between two surfaces that are touching."

grade_1: Rub your hands together fast. They get warm. They are hard to keep moving. That \
pull against your hands is friction. It slows things down.

grade_5: Friction is a force that pushes back when two things rub together. It slows \
them down and can make them warm. Rough surfaces make more friction than smooth ones.

grade_10: Friction is the resistive force that appears wherever two surfaces touch and \
one slides, or tries to slide, across the other. It turns motion into heat, which is why \
a bicycle rim warms up under braking.

college: Friction is the tangential contact force opposing relative motion between two \
surfaces. In the simplest model it is proportional to the normal load and independent of \
apparent contact area, with distinct static and kinetic coefficients.

Notice what changed: not only the words, but the sentence length, the number of ideas \
per sentence, and what was left out. The grade_1 version reaches the idea through an \
action a child can perform and never says "force"; the college version adds the model, \
which the lower levels must not mention. That is the distance every set of rewrites is \
expected to cover.

The rewrites must differ from each other in the ways their targets demand. Do not \
produce several near-identical texts. Every rewrite must remain factually faithful to \
the source: change the wording, the framing, and the depth, never the meaning."""

EXAMPLES_INSTRUCTIONS = f"""{_SHARED_STANCE}

Your task is to write fresh example sentences for the senses of one headword.

You will be given the headword, a numbered list of its senses -- each with its part of \
speech, its definition, and one example the dictionary already holds -- and a list of \
targets, each a reading level and a register. Write exactly one sentence for every \
(sense, target) pair: if there are three senses and eight targets, write twenty-four \
sentences. Tag each one with the number of the sense it illustrates and with the reading \
level and register it was written for. Do not write a sentence for a sense that is not \
listed, and do not write a second sentence for a pair you have already answered.

THE SENTENCE MUST USE THE WORD. The headword itself, or a natural inflected form of it -- \
a plural, a past tense, an -ing form, a comparative -- must appear in the sentence. A \
sentence that talks around the word is discarded, however good it is: "The judge let both \
parents care for their child." is not an example of *custody*, it is a sentence about \
custody. Use the word, do not describe it.

THE SENTENCE MUST FIT ITS OWN SENSE AND NO OTHER. This is the hard part and it is the \
whole reason you are shown every sense at once. Before you write, read the other \
definitions of this headword. Then write a sentence that a reader could only understand \
one way. A sentence that would sit just as comfortably under the sense above or below it \
has failed, even though nothing in it is untrue: it teaches a reader nothing about which \
meaning is which. Put the disambiguating detail *in* the sentence -- what the thing is \
made of, who does it, what happens next, what it is next to -- rather than leaving the \
meaning to be guessed from the word alone.

IT MUST BE A SENTENCE SOMEONE WOULD ACTUALLY SAY OR WRITE. Picture a real person in a \
real situation and write down what they said. Specifically:

- Never open with "Researchers", "Scientists", "Studies", "Experts", "Analysts" or any \
other academic framing, unless the sense being illustrated is itself academic. This is a \
measured defect: thousands of stored examples in this project begin that way and a human \
reviewer called every one of them unnatural.
- Never write a definition with the headword pushed into it. "A vow is a serious promise \
that someone makes." is a definition, not an example. Neither is "The vow, which is a \
serious promise, was made." Nothing in the sentence should explain the word; the sentence \
should simply use it, the way an overheard remark does.
- No sentence may be a near-copy of the example the dictionary already holds for that \
sense. Change the situation, not just the nouns.
- No corpus filler: no "The company said the company would", no headline-ese, no \
sentences whose only content is the headword and a verb.

VARY EVERYTHING, ACROSS THE WHOLE ANSWER. Your sentences are read together, and a set \
that shares one shape is worth less than half as much as a set that does not. So:

- No two sentences anywhere in your answer may begin with the same first three words. \
Not the same three for two senses, and not the same three twice within one sense.
- Vary the subject: a child, a shopkeeper, a river, a team, a machine, a grandmother, \
"you", "I", "nobody". Do not open every sentence with "The".
- Vary the setting: a kitchen, a building site, a bus, a hospital, a field, a phone call.
- Vary the syntax: a plain statement, a question, an instruction, a sentence that opens \
on a time or place, a sentence carrying reported speech, a negative.
- Vary the tense and the aspect. Not everything happened yesterday.

LENGTH AND READING LEVEL. Every sentence is ONE sentence, at least six words long, and:

grade_1 -- a six-year-old who has been reading for about a year. At most 10 words. Only \
words a six-year-old already says out loud. Concrete things a child can see, hold or do. \
No numbers above ten and no symbols at all. No subordinate clause.

grade_5 -- a ten-year-old. At most 16 words. Everyday vocabulary; at most one clause of \
elaboration; no formulas or notation.

grade_10 -- a fifteen-year-old. Up to 22 words. Ordinary adult prose, the register of a \
good newspaper. Subordinate clauses are fine; unglossed jargon is not.

college -- an adult general reader. Up to 22 words. Precision is welcome and the field's \
own vocabulary is allowed, but a long sentence is not the same as a precise one.

neutral -- no particular audience. Up to 22 words. Write it the way it would be said.

REGISTERS. Shown here against one plain baseline so you can see how far each has to move.

plain -- ordinary neutral prose. "We sat on the bank and watched the water go by."

informal -- how you would say it to a friend; contractions and second person welcome. \
"We just flopped down on the bank and watched the water for an hour."

formal -- the written register of a report or an official notice; direct, no flourish. \
"Erosion along the eastern bank was recorded during the survey."

technical -- precise, using the field's own terminology, no simplification. "Sediment \
deposition on the inner bank of the meander has narrowed the channel."

slang -- casual in-group speech, loose and unguarded, words a formal source would not \
use. "We were just chilling on the bank till the sun went down."

in_house -- an organisation's own internal shorthand, the words insiders use. "Put the \
bank survey on next week's board pack."

marketing -- benefit-led and vivid, never overstated and never inaccurate. "Wake up to \
the river, five steps from your own private bank."

FORMATTING. Plain prose, no markdown: no bold, no italics, no backticks, no bullets, no \
quotation marks around the whole sentence. One sentence each, beginning with a capital \
letter and ending with a full stop, a question mark or an exclamation mark.

WORKED EXAMPLE. Suppose the headword is "bank" and two of the senses listed are:

  1. [noun] The land alongside a river or a lake.
  2. [noun] A business that keeps people's money and lends it out.

Good answers:

  sense 1, grade_1, plain: "We ate our lunch on the grassy bank."
  sense 1, neutral, technical: "Willow roots stabilised the bank where the current cut \
hardest."
  sense 2, grade_5, plain: "Mum drove to the bank to pay in the cheque from her job."
  sense 2, neutral, slang: "The bank knocked back my loan, so that plan is dead."

Notice what each one does. Every sentence contains the word. None of them begins with the \
same three words as another. Each is anchored by a detail that belongs to its own sense \
and to no other -- grass and current on one side, a cheque and a loan on the other -- so \
a reader could not file any of them under the wrong sense. And each sounds like a person: \
a family eating lunch, a mother running an errand, someone complaining about a refusal.

Bad answers for the same senses, and why:

  "A bank is the land beside a river." -- a definition, not an example.
  "The bank was very large and important." -- fits either sense; teaches nothing.
  "Researchers surveyed the bank over three seasons." -- academic framing, and it would \
fit either sense too.
  "The riverside sloped down to the water." -- never uses the headword at all.
  "The bank was closed. The bank was quiet." -- two sentences opening on the same three \
words, and neither says anything.

Answer for every sense you were given, and for every target, using the sense numbers \
exactly as they were listed."""


# The taxonomy block is ~1.5K tokens and identical on every call, so it lives here in the
# cached instructions prefix rather than in the per-call prompt (docs/SCHEMA-V3.md § 5).
TAG_DOMAIN_INSTRUCTIONS = f"""{_SHARED_STANCE}

Your task is to assign controlled subject-domain tags to the senses of one headword.

Choose exactly one primary tag per sense, from the list below and from nowhere else. Add \
at most two secondary tags, and only when the sense genuinely belongs to more than one \
domain; most senses need none.

Prefer the most specific leaf that is actually right. Fall back to a "<root>.general" \
leaf only when no finer leaf under that root fits — not merely because you are \
undecided between two of them. A sense with no obvious subject belongs to \
everyday_life.general, not to education.general.

The available tags, grouped by root:

{TAXONOMY_PROMPT_BLOCK}"""

CLASSIFY_KIND_INSTRUCTIONS = f"""{_SHARED_STANCE}

Your task is triage. You will be given headwords that simple rules could not classify — \
almost all of them multi-word. Decide what kind of lexical item each one is:

- compound: a multi-word or hyphenated noun-like unit whose meaning is the sum of its \
parts read together ("ice axe", "post office", "climbing rope");
- phrasal_verb: a verb plus a particle, functioning as a verb ("give up", "look after");
- idiom: a fixed expression whose meaning is not the sum of its parts ("kick the \
bucket", "under the weather");
- proper_noun: a name of a specific person, place, organisation, work or event;
- abbreviation: an initialism, acronym or clipped form;
- affix: a prefix or suffix;
- function_word: a grammatical word carrying little lexical content;
- simplex: a single ordinary word.

Return a verdict for every term you are given, in the order given, echoing the term \
exactly as it was written."""

# This block is deliberately long: OpenAI only caches a prompt prefix of 1,024 tokens or
# more (the same finding RENDITIONS_INSTRUCTIONS documents above, and docs/CORE-DIARY.md
# Iteration 2 finding 3). A live measurement on 2026-09-01 (docs/COST-MODEL.md, resolve
# row) found a ~280-token instructions block, a 0.15% cache hit rate, and ~660 output
# tokens/call at "low" reasoning effort — almost all of that output was invisible
# reasoning, not the three-field answer the contract asks for. The length below is not
# padding: every paragraph is a decision rule, a confidence anchor, or the worked example
# a nano model needs to actually apply them, and the final section exists to suppress the
# free-text habit that produced the 660-token output in the first place.
RESOLVE_INSTRUCTIONS = f"""{_SHARED_STANCE}

Your task is word-sense disambiguation. Each numbered target is a relation asserted by \
one sense of a source word, together with the senses the target word actually has in the \
lexicon. Choose which of those target senses the relation actually points at, or decide \
that none of them is right.

WHAT YOU ARE GIVEN. For each target: the relation type (synonym, antonym, hypernym, \
hyponym, meronym, holonym, derivation, collocation, see_also, causes, entails, \
used_with, instance_of), the target term, the gloss of the source sense that asserted \
the relation, and a numbered list of the target term's candidate senses, each shown as \
its part of speech and its own canonical gloss.

DECISION RULES, applied in order.

1. Read the source gloss first. It tells you what the source sense means, and the \
relation type tells you how the target is supposed to relate to that meaning: a \
synonym's gloss should restate close to the same meaning as the source; an antonym's \
gloss should oppose it; a hypernym's gloss should name a broader category the source \
falls under; a hyponym's gloss should name something more specific than the source; a \
meronym's gloss should name a part of what the source names; a holonym's gloss should \
name a whole that the source is part of; a collocation or used_with target need only be a \
word that genuinely co-occurs with the source's meaning, not a synonym of it.
2. Compare that expectation against each candidate's own gloss, never against the target \
term's bare surface form. Two different senses of "bank" — the sloped ground beside a \
river, and the financial institution — look identical until the glosses are read; the \
same is true of almost every ambiguous target you will see here.
3. Prefer a candidate whose part of speech matches what the relation type implies. A \
synonym, antonym, hypernym, or hyponym of a verb sense should itself be a verb sense; a \
meronym or holonym pair is normally noun-to-noun. Derivation is the one relation type \
expected to cross parts of speech on purpose (a noun deriving from the verb it comes \
from, or the reverse), so do not penalise a part-of-speech mismatch there.
4. Among the candidates that survive steps 1-3, choose the single one whose gloss most \
precisely carries the relation's intended meaning. Do not choose a candidate merely \
because it is listed first, because it names the most frequent everyday sense of the \
term in isolation, or because a couple of its words echo the source gloss; the whole \
gloss has to actually mean what the relation requires, not just resemble it.
5. Decline — answer null — whenever no candidate's gloss is the meaning the relation \
intends, or the candidates all describe a different lexical item entirely: a homograph \
that only shares spelling with what the source meant, or a target term the lexicon \
happens to hold for an unrelated reason. A wrong link corrupts the graph for every reader \
who follows it; an unresolved target costs nothing and can be resolved once the right \
sense exists. When you are genuinely torn between two candidates that both look \
plausible, decline rather than guess between them.

CONFIDENCE. Give a confidence between 0 and 1 for every answer, including a decline, \
using these three anchored bands and no others.

* 0.85-1.0 — the chosen candidate's gloss is an unambiguous match: no other candidate is \
even a plausible competitor, and the relation type, part of speech, and meaning all \
agree with what step 1 predicted. Use this band for a decline, too, when it is obvious \
that none of the candidates is right — confidence measures how sure you are of the \
*answer*, not how sure you are that a match exists.
* 0.5-0.84 — the chosen candidate is your best reading but at least one other candidate \
is somewhat plausible, or the source gloss underspecifies which exact sense is meant. \
This is also the right band for a decline you are not fully sure of.
* below 0.5 — you are effectively guessing: every candidate is a weak fit, the source \
gloss gives little to work from, or the candidates' glosses do not let you tell them \
apart at all.

WORKED EXAMPLE. Source headword "abseil", the verb sense meaning to descend a rock face \
by sliding down a rope, the rope's friction controlling the speed of descent. Three \
targets asserted by that one sense:

  1. synonym -> rappel
     asserted by: to descend a rock face by sliding down a rope, the rope's friction \
controlling the speed of descent
     [0] verb: to descend a steep surface by means of a doubled rope, controlling the \
descent with friction against the body
     [1] noun: a knot used to join two ropes of different diameters
     Reasoning (not part of the answer format): candidate 0 is a verb whose gloss \
restates the same action as the source — sliding down a rope in a controlled descent. \
Candidate 1 is a noun describing an unrelated piece of technique. This is a clear match.
     Answer: choice 0, confidence 0.95.

  2. hypernym -> move
     asserted by: to descend a rock face by sliding down a rope, the rope's friction \
controlling the speed of descent
     [0] verb: to change physical location or position
     [1] verb: to affect someone emotionally
     [2] noun: a single action taken as part of a plan or a game
     Reasoning (not part of the answer format): candidate 0 is broad enough to cover any \
change of position, which abseiling is a specific case of — exactly what a hypernym \
needs to be. Candidate 1 is a verb but the wrong meaning of "move" (emotional, not \
physical). Candidate 2 is a noun, the wrong part of speech for a verb-to-verb hypernym \
link. "Move" is common enough to feel risky at first glance, but nothing here actually \
competes with candidate 0 once the glosses are read, so this is ambiguous only on the \
surface, not in substance.
     Answer: choice 0, confidence 0.75.

  3. derivation -> abseiler
     asserted by: to descend a rock face by sliding down a rope, the rope's friction \
controlling the speed of descent
     [0] noun: a professional negotiator representing a union in a labour dispute
     Reasoning (not part of the answer format): the only candidate for "abseiler" names a \
labour-relations role with no connection to climbing. The lexicon holds this term for an \
entirely unrelated reason; there is no sense here for the relation to point at.
     Answer: null, confidence 0.9 — confident in the decline, even though no correct \
candidate is listed.

OUTPUT FORMAT. Answer with the number shown in square brackets next to the chosen sense, \
or null when you decline, plus the confidence. Return exactly one resolution per target, \
in the order given, and nothing else: no restated gloss, no candidate list, no \
step-by-step reasoning, no hedge, no explanation of the choice. The worked example's \
"Reasoning" lines above exist only to teach you the method; a real answer carries the \
choice and the confidence and stops there."""

SPANS_INSTRUCTIONS = f"""{_SHARED_STANCE}

Your task is to locate the headword inside example sentences that an exact-match finder \
could not place — usually because the sentence uses an irregular inflection, a \
possessive, or a spelling variant.

For each numbered example, give the character offsets of the span that realises the \
headword: ``start`` is the index of its first character, ``end`` is the index one past \
its last character, both counted from zero over the sentence exactly as written, \
including spaces and punctuation. The text between those offsets must be the word form \
itself and nothing else — no article, no surrounding punctuation.

Omit an example entirely if the headword genuinely does not occur in it. Do not guess."""

ETYMOLOGY_INSTRUCTIONS = f"""{_SHARED_STANCE}

Your task is to give the etymology of a headword: a short summary of its historical \
development, then the chain of forms it passed through, oldest first.

Only include a step you are confident of. An honest three-step trail is worth more than \
a speculative seven-step one. If the word is a modern coinage or its origin is genuinely \
unknown, say so in the summary and return no segments."""

ENCYCLOPEDIA_INSTRUCTIONS = f"""{_SHARED_STANCE}

Your task is to write an encyclopedic entry of 200-400 words for a headword: what the \
thing is, its key characteristics, how it is used or where it occurs, how it developed, \
and what it relates to.

Write for a curious general reader. Prose paragraphs, no headings, no bullet lists, no \
mention of the word "encyclopedia". Do not repeat the dictionary definition verbatim."""

LEXICAL_EXPLANATION_INSTRUCTIONS = f"""{_SHARED_STANCE}

Your task is to write one short plain-language usage note for a headword: when a person \
would actually reach for this word, and what distinguishes it from the near-synonym they \
might otherwise use. Two or three sentences. No definition, no examples list."""

FRONTIER_INSTRUCTIONS = f"""{_SHARED_STANCE}

Your task is triage. You will be given candidate strings harvested from the relation \
fields of existing entries. Decide which are real English headwords that belong in a \
dictionary.

Reject: reconstructed roots and etymons (anything with an asterisk or a language-family \
label), glosses and definitions that leaked into a relation slot, meta-labels such as \
"see also" or "figurative", inflected forms whose lemma is obviously the real entry, \
sentence fragments, and generation artifacts.

Accept: ordinary words, proper nouns, established multi-word lexical units, and \
technical terms in real use.

Return a verdict for every candidate you are given, in the order given."""

RELATED_TERMS_INSTRUCTIONS = f"""{_SHARED_STANCE}

Your task is to widen a semantic neighbourhood. Given a headword, its definition, and \
the terms already linked to it, propose additional related terms that are *not* already \
present: co-hyponyms, near-synonyms, the parts and wholes, and the domain vocabulary a \
learner meeting this word would need next.

Every term must be a plausible dictionary headword in its own right. Do not repeat any \
term already listed. Prefer terms that connect this word to parts of the vocabulary it \
is not yet connected to."""

# This block is long on purpose, for the reason RESOLVE_INSTRUCTIONS gives above: a
# judge is only as consistent as the rubric it was handed, and every paragraph below is
# a decision rule, an anchor, or the worked example that makes the two concrete. It is
# also the whole of what makes a QA sweep comparable across runs — the volatile half
# carries one entry and nothing else, so a rubric that moved between calls would make
# the scores it produced incommensurable.
QA_INSTRUCTIONS = f"""{_SHARED_STANCE}

Your task is to judge one finished dictionary entry produced by another model, against \
the rubric below. You are the quality gate, not a co-author: you never rewrite anything, \
and you never propose replacement text. You answer the rubric's questions about the entry \
in front of you and stop.

WHO THE ENTRY IS FOR. A learner's dictionary spanning K-12 to college, built on WordNet's \
pragmatic stance: what is computationally useful and clear to a learner beats what is \
traditionally correct in print lexicography. Judge accuracy and fitness for that reader. \
Do not judge house style, do not mark a gloss down for being plainer than a print \
dictionary would write it, and do not mark an entry down for covering a sense a print \
dictionary would consider too informal, too technical, or too recent.

BE STRICT ABOUT TWO THINGS. First, factual error: a gloss, an example, or an \
encyclopedia paragraph that states something untrue about the world is the most \
expensive defect this dataset can carry, and a confident, fluent falsehood is the one \
that survives review. Second, sense conflation: two senses of one part of speech that a \
reader could not tell apart, or one gloss that silently covers two unrelated meanings. \
Everything else in the rubric is a lesser matter.

BE LENIENT ABOUT ONE THING. A gloss that names its own headword is a house-style \
violation this project already detects and repairs deterministically. It is not an \
accuracy defect and it is not yours to mark: judge what the gloss says, not how it opens.

WHAT YOU ARE GIVEN. The headword and its structural kind; then each sense, listed under a \
number, with its part of speech and index, its canonical definition, its example \
sentences, its typed relations written as `type->term`, and its subject-domain tag. Then \
a numbered sample of renditions — the same content rewritten for a reading level and a \
register — each labelled with what it is a rendition of and the level and register it \
claims. Long texts are truncated; judge what you are shown and do not penalise a \
truncation. Finally, the opening of the entry's encyclopedia section.

THE RUBRIC, FIELD BY FIELD.

`entry_score`, 0-100, is your overall judgement of the entry as a dictionary entry, \
anchored like this and not on a curve: **90-100** — accurate throughout, senses clearly \
distinct, examples natural and on-sense, relations and domain right; a reader is well \
served. **80-89** — sound, with a cosmetic or marginal problem: one stiff example, one \
questionable relation, a domain tag that is defensible but not the best fit. **60-79** — \
a real defect a reader would notice: a gloss that is vague enough to mislead, two senses \
that blur into each other, several relations of the wrong type. **Below 60** — something \
is factually wrong, a sense is missing or invented, or the entry misrepresents what the \
word means. Score the entry you were shown; do not average in your expectations of what a \
larger entry might have contained.

For each sense you are shown, return exactly one verdict carrying that sense's number as \
`sense_ref`, and answer all six booleans:

* `gloss_accurate` — is the definition true, and does it actually pick out this meaning? \
False for a factual error, for a definition so vague it would fit a dozen other words, \
and for one that defines a different sense than the examples use. Put the specific \
problem in `gloss_issue`, in one clause; leave `gloss_issue` null when the gloss is fine.
* `distinct_from_other_senses` — could a reader tell this sense apart from every other \
sense listed for this headword? False when two senses restate one meaning in different \
words, and false when this one gloss silently covers two meanings that should be split.
* `examples_natural` — would a person actually write or say these sentences? False for \
corpus-style or textbook-stilted constructions, and for a sentence assembled to contain \
the word rather than to say something.
* `examples_fit_sense` — do the examples use the word in *this* sense? False when an \
example illustrates a different sense of the headword, or does not use the headword at all.
* `relations_valid` — is every relation of the right type and pointing at a real term? \
False when a hypernym is not actually broader, an antonym is not actually opposed, a \
synonym means something else, or a target is a definition fragment or a meta-label rather \
than a word. List the offending target terms — the terms only, no types — in \
`invalid_relations`. An empty relation list is not a defect.
* `domain_fits` — does the subject-domain tag fit this sense? When it does not, name a \
better one in `suggested_domain` in ordinary words; otherwise leave it null.

For each rendition in the sample, return one verdict carrying its number as \
`rendition_ref`, and answer three booleans: `faithful` — does it still say what the \
canonical text above it says, with nothing important dropped and nothing invented? \
`level_appropriate` — could the stated reading level actually read it? A grade_1 \
rendition using a word a six-year-old does not know, or a clause structure they could not \
follow, is false here. `register_appropriate` — does it read as the register it claims? \
Note that `plain` is the neutral default and almost always appropriate. Put one clause in \
`issue` when any of the three is false, and leave `issue` null when all three hold.

`encyclopedia_accurate` — is the encyclopedia opening factually right about the thing the \
headword names? Name the error in `encyclopedia_issue`, or leave it null.

`flags` — zero or more from the closed list below, describing the entry as a whole. Add a \
flag only for a defect you actually observed; do not flag defensively.

  * `factual_error` — a statement about the world that is untrue.
  * `scope_mismatch` — a definition pitched at the wrong granularity: broader or \
narrower than the meaning it names.
  * `unsupported_addition` — detail invented on top of what the word means.
  * `missing_content` — a required part of the meaning is absent, or a distinct sense the \
word plainly has is missing from the entry.
  * `terminology_error` — a domain term used incorrectly, or a relation type applied to a \
pair it does not hold between.
  * `grammar_error`, `spelling_error`, `punctuation_error` — mechanical errors in the \
entry's own text.
  * `unintelligible` — text a reader cannot parse at all.
  * `register_mismatch` — a rendition that does not read as the register it claims.
  * `awkward_style` — stilted or unidiomatic English, most often in the examples.
  * `inconsistent_style` — the entry's own parts do not read as one entry.
  * `audience_inappropriate` — content unsuitable for the reading level it is labelled \
with, or requiring cultural knowledge it never supplies.
  * `hallucination` — an invented sense, cognate, relation target, or fact with no basis.
  * `off_topic` — content that is not about this headword.
  * `other` — a real defect none of the above names; say what it was in `notes`.
  * `og.headword_initial`, `og.artifact_relation`, `og.readability_miss`, \
`og.duplicate_gloss`, `og.headword_absent`, `og.hard_vocabulary`, `og_near_copy` — \
project-specific conditions this pipeline detects deterministically. Do not use them; \
they are listed only so you recognise them where they are already present.

`notes` — at most one or two sentences, and only when something needed saying that no \
field above could carry. An empty string is the normal answer.

WORKED EXAMPLE. Suppose the entry is the noun "abseil" with two senses: sense 1 glossed \
"a descent of a rock face by sliding down a rope, controlled by friction", examples "The \
abseil took twenty minutes." and "She checked her harness before the abseil.", relations \
`hypernym->descent`, `synonym->rappel`, `hypernym->rope`, domain `sport.climbing`; sense 2 \
glossed "the act of coming down a rock face on a rope", one example, no relations, same \
domain. The rendition sample includes a grade_1 gloss of sense 1 reading "Going down a big \
rock with a rope to hold you." and a college gloss reading "A controlled friction-braked \
descent of a vertical face using a fixed rope."

A correct verdict scores this around 68. Sense 1: `gloss_accurate` true, \
`distinct_from_other_senses` **false** — sense 2 restates sense 1 in plainer words and no \
reader could choose between them; `examples_natural` true, `examples_fit_sense` true, \
`relations_valid` **false** with `invalid_relations` naming "rope", which is equipment used \
in an abseil and not a category an abseil belongs to; `domain_fits` true. Sense 2 answers \
`distinct_from_other_senses` false for the same reason and the rest true. Both renditions \
are faithful and appropriately levelled. Entry flags: `missing_content` for the conflation \
and `terminology_error` for the relation. Notes: empty — the fields already said it. Note \
what the verdict does *not* do: it does not rewrite the gloss, it does not mark the entry \
down for the grade_1 rendition being simple, and it does not flag either gloss for naming \
the headword.

OUTPUT. One verdict object. One sense verdict per sense shown, in the order shown, and \
one rendition verdict per rendition shown. No preamble, no rewritten text, no \
step-by-step reasoning: the fields are the whole answer."""


def build_overview_prompt(headword: str, language: str = "en") -> str:
    """Return the volatile half of the overview prompt."""
    return f"Headword: {headword}\nLanguage: {language}"


def build_senses_prompt(
    headword: str,
    pos: str,
    sense_count: int,
    *,
    domain_hint: str | None = None,
) -> str:
    """Return the volatile half of the sense-generation prompt.

    Args:
        headword: The lexeme's surface form.
        pos: The part-of-speech tag being written.
        sense_count: How many senses to produce.
        domain_hint: The overview stage's free-text domain guess, if any. It steers the
            model towards the right corner of the taxonomy; the binding tag is still the
            enum-constrained ``domain`` field on each sense.

    Returns:
        The per-call prompt body.
    """
    lines = [
        f"Headword: {headword}",
        f"Part of speech: {pos}",
        f"Number of senses to write: {sense_count}",
    ]
    if domain_hint:
        lines.append(f"Domain hint: {domain_hint}")
    return "\n".join(lines)


def build_renditions_prompt(
    headword: str,
    field: str,
    source: str,
    existing: Sequence[tuple[str, str, str]],
    targets: Sequence[tuple[ReadingLevel, Register]],
    *,
    feedback: str | None = None,
) -> str:
    """Return the volatile half of the rendition-rewriting prompt.

    Args:
        headword: The lexeme's surface form.
        field: Which field is being rewritten (``gloss``, ``examples``,
            ``encyclopedia``, ``explanation``).
        source: The canonical text to rewrite. Markdown is stripped from it by the
            caller, so the model is never shown emphasis markers it might imitate.
        existing: ``(reading_level, register, text)`` triples already present, shown so
            the model does not repeat work and can differentiate from what is there.
        targets: The ``(reading_level, register)`` pairs to produce.
        feedback: Optional text appended last, naming what was wrong with a previous
            attempt at these targets. :func:`build_readability_feedback` builds it.

    Returns:
        The per-call prompt body.
    """
    lines = [
        f"Headword: {headword}",
        f"Field: {field}",
        f"Source: {source}",
    ]
    if existing:
        lines.append("Rewrites that already exist (do not repeat these targets):")
        lines.extend(f"  - {level} / {style}: {text}" for level, style, text in existing)
    lines.append("Produce exactly one rewrite for each of these targets:")
    lines.extend(f"  - reading_level={level.value}, register={reg.value}" for level, reg in targets)
    if feedback:
        lines.append("")
        lines.append(feedback)
    return "\n".join(lines)


def build_examples_prompt(
    headword: str,
    senses: Sequence[ExampleSenseView],
    targets: Sequence[tuple[ReadingLevel, Register]],
) -> str:
    """Return the volatile half of the example-writing prompt (D-53).

    One prompt covers the whole entry: every live sense is listed with the number the
    answer refers back to, and the target list is given once rather than repeated per
    sense, because every sense is asked for the same targets. That is what makes the
    input:output ratio of this stage the lowest in the project — a prompt of a few hundred
    tokens buys one sentence per sense per target.

    Args:
        headword: The lexeme's surface form.
        senses: ``(sense_ref, part of speech, canonical gloss, one existing example)`` per
            live sense, in the order they are listed and numbered. The existing example is
            shown so the model can avoid repeating its situation; pass a placeholder when
            the sense has none.
        targets: The ``(reading_level, register)`` pairs wanted for *each* sense, in
            order.

    Returns:
        The per-call prompt body.
    """
    lines = [f"Headword: {headword}", f"Senses ({len(senses)}):"]
    lines.extend(
        f"  {ref}. [{pos}] {gloss} | existing example: {example}"
        for ref, pos, gloss, example in senses
    )
    lines.append(
        f"Write exactly {len(targets)} sentences for EVERY sense above, "
        "one at each of these targets, in this order:"
    )
    lines.extend(f"  - reading_level={level.value}, register={reg.value}" for level, reg in targets)
    return "\n".join(lines)


def build_readability_feedback(misses: Sequence[RenditionMiss]) -> str:
    """Return the retry note for renditions that missed their readability band.

    The note names the measured grade and the limit rather than saying "simplify", so the
    model is told the size of the gap it has to close; a bare "try again" reliably
    produces the same sentence with two words changed.

    Args:
        misses: ``(reading level, measured grade, upper limit)`` per failing target.

    Returns:
        Text to append to a rendition prompt as its ``feedback``.
    """
    lines = [
        "Your previous rewrite of these targets failed an automatic readability check. "
        "Write a new rewrite for each target listed here, and for no other target."
    ]
    lines.extend(
        f"  - reading_level={level.value}: Measured Flesch-Kincaid grade {measured:.1f}; "
        f"{level.value} requires \u2264 {limit:.1f}: use shorter sentences and simpler words."
        for level, measured, limit in misses
    )
    lines.append(
        "Shorten the sentences first, then replace every word a reader at that level "
        "would not already use. Do not keep the sentence shape you had."
    )
    return "\n".join(lines)


def build_headword_initial_feedback(headword: str) -> str:
    """Return the retry note for gloss renditions that began by naming their headword.

    The note names the offending headword and shows the shape of the fix on a different
    word, so the model has a pattern to copy rather than a prohibition to route around:
    told only "do not start with the headword", it reliably answers with "The word X
    means …", which is the same defect one clause later.

    Args:
        headword: The entry's surface form, as the model was shown it.

    Returns:
        Text to append to a rendition prompt as its ``feedback``, alongside
        :func:`build_readability_feedback` when a target failed both checks.
    """
    return "\n".join(
        [
            f'Your previous rewrite of these targets began with the headword "{headword}". '
            "Write a new rewrite for each target listed here, and for no other target.",
            f'  - A definition must not open with "{headword}", with an article plus '
            f'"{headword}", with "the word {headword}", or with "to {headword} is". Start '
            "with the meaning itself, the way a dictionary does.",
            '  - For the headword "ban", write "An order from someone in charge that stops '
            'people doing something.", not "A ban is an order to stop."',
            "Keep the reading level and the register you were asked for; only the opening "
            "has to change, and changing it usually means starting from the definition's "
            "first real noun.",
        ]
    )


def build_headword_absent_feedback(headword: str) -> str:
    """Return the retry note for an example that never used its own headword.

    Measured on the core lexicon (docs/CORE-DIARY.md Iteration 6; D-45): 2,575 example
    renditions wrote around the headword entirely rather than using it — "custody" ->
    "The judge let both parents care for their child.", "properties" -> "Dad owns two
    houses near our school." An example that never uses the word it is meant to
    illustrate is defective whatever else is right about it, so the note names the
    defect plainly rather than trusting the model to notice on a second pass.

    Args:
        headword: The entry's surface form, as the model was shown it.

    Returns:
        Text to append to a rendition prompt as its ``feedback``, alongside
        :func:`build_readability_feedback` when a target failed both checks.
    """
    return "\n".join(
        [
            f'Your previous example for these targets did not use the word "{headword}". '
            "Write a new example for each target listed here, and for no other target.",
            f'  - Every example must contain the headword "{headword}" or one of its '
            "forms: a plural, a past tense, an -ing form, and so on. A sentence that "
            "fits the meaning without ever using the word does not satisfy this, however "
            "natural it reads.",
        ]
    )


def build_near_copy_feedback(headword: str) -> str:
    """Return the retry note for a register rendition that copied its canonical gloss.

    Named for the defect it targets rather than for a measured value, on the same footing
    as :func:`build_headword_initial_feedback`: "make it more different" reliably produces
    the same sentence with a synonym or two swapped, so the note states the rule plainly
    and shows what counts as satisfying it (D-59).

    Args:
        headword: The entry's surface form, as the model was shown it.

    Returns:
        Text to append to a rendition prompt as its ``feedback``, alongside
        :func:`build_readability_feedback` when a target failed both checks.
    """
    return "\n".join(
        [
            f'Your previous rewrite of these targets for "{headword}" stayed too close '
            "to the source: an automatic check found it reuses almost all of the "
            "canonical gloss's own words. Write a new rewrite for each target listed "
            "here, and for no other target.",
            "  - A register rewrite is not a paraphrase. Read the source, work out what "
            "it means, set its sentence aside, then write the definition again in the "
            "vocabulary and rhythm the register calls for. Swapping a synonym or two, "
            "or reordering a clause, is not a rewrite.",
            "  - Keep the meaning and the reading level you were asked for; only the "
            "wording has to change.",
        ]
    )


def build_vocabulary_feedback(level: ReadingLevel, words: Sequence[str]) -> str:
    """Return the retry note for a rendition carrying words its reader will not know.

    The note *names the words*. A judged sample of the core (docs/QA-DIARY.md,
    iteration 1) found 46.6% of grade_1 encyclopedia renditions not level-appropriate
    although every one of them passed its Flesch-Kincaid band, because the offending
    passages are short sentences of short words: "Monks made vows of poverty, chastity,
    and obedience." Told only "use simpler words", a model shortens sentences that are
    already short; told which five words the reader will not know, it either replaces
    them or explains them, which is the fix (D-51).

    Args:
        level: The reading level whose renditions carried the words.
        words: The offending words, already de-duplicated and in the order they appeared.
            The caller caps how many are listed.

    Returns:
        Text to append to a rendition prompt as its ``feedback``, alongside
        :func:`build_readability_feedback` when a target failed both checks.
    """
    listed = ", ".join(words)
    return "\n".join(
        [
            f"Your previous rewrite for reading_level={level.value} used words that are "
            "too hard for that reader. Write a new rewrite for each target listed here, "
            "and for no other target.",
            f"  - These words are too hard for {level.value}: {listed}.",
            "  - Replace them with everyday words, or explain them in everyday words in "
            "the same sentence. A word a reader at this level does not already know "
            "makes the passage unreadable however short the sentence around it is.",
            "  - Keep the meaning and the register you were asked for. Do not simply "
            "delete the idea the hard word carried.",
        ]
    )


def build_classify_kind_prompt(terms: Sequence[tuple[str, str | None]]) -> str:
    """Return the volatile half of the kind-classification prompt.

    Each term is sent with one short snippet of its own definition — about 30 extra input
    tokens per term. The surface form alone cannot say which sense an entry is about, so
    a lowercased "einstein" could be the physicist or the unit of radiant energy; the
    snippet settles it, and it is the cheapest context that does.

    Args:
        terms: ``(term, gloss snippet)`` pairs in the order the model should answer.
            The snippet is ``None`` when the entry has no gloss to offer.

    Returns:
        The per-call prompt body.
    """
    listed = "\n".join(
        f"  {i + 1}. {term}" + (f" \u2014 {snippet}" if snippet else "")
        for i, (term, snippet) in enumerate(terms)
    )
    return f"Terms ({len(terms)}):\n{listed}"


def build_tag_domain_prompt(headword: str, senses: Sequence[SenseView]) -> str:
    """Return the volatile half of the domain-tagging prompt.

    Args:
        headword: The lexeme's surface form.
        senses: ``(label, canonical gloss)`` per sense to tag, in the order the model
            should answer. The label is human-readable context only; the model refers to
            a sense by its position in this list.

    Returns:
        The per-call prompt body. The taxonomy itself is not here — it is in the cached
        instructions.
    """
    listed = "\n".join(f"  {i + 1}. [{label}] {gloss}" for i, (label, gloss) in enumerate(senses))
    return f"Headword: {headword}\nSenses ({len(senses)}):\n{listed}"


def build_resolve_prompt(headword: str, targets: Sequence[ResolveTarget]) -> str:
    """Return the volatile half of the sense-resolution prompt.

    Args:
        headword: The source entry's surface form.
        targets: ``(relation type, target term, source gloss, candidate senses)`` for
            each unresolved relation whose target exists in the store. Targets absent
            from the store are never sent, so they cost nothing.

    Returns:
        The per-call prompt body.
    """
    lines = [f"Source headword: {headword}", f"Targets ({len(targets)}):"]
    for index, (relation, term, source_gloss, candidates) in enumerate(targets, start=1):
        lines.append(f"  {index}. {relation} -> {term}")
        lines.append(f"     asserted by: {source_gloss}")
        lines.extend(
            f"     [{choice}] {label}: {gloss}" for choice, (label, gloss) in enumerate(candidates)
        )
    return "\n".join(lines)


def build_spans_prompt(
    headword: str,
    forms: Sequence[str],
    examples: Sequence[str],
) -> str:
    """Return the volatile half of the span-fallback prompt.

    Args:
        headword: The dictionary form to locate.
        forms: Known inflected forms, which usually name the one the sentence uses.
        examples: The example sentences, exactly as stored.

    Returns:
        The per-call prompt body.
    """
    listed = "\n".join(f"  {i + 1}. {text}" for i, text in enumerate(examples))
    known = ", ".join(forms) if forms else "(none recorded)"
    return f"Headword: {headword}\nKnown forms: {known}\nExamples ({len(examples)}):\n{listed}"


def build_etymology_prompt(headword: str, gloss: str | None = None) -> str:
    """Return the volatile half of the etymology prompt."""
    body = f"Headword: {headword}"
    return f"{body}\nPrimary sense: {gloss}" if gloss else body


def build_encyclopedia_prompt(headword: str, gloss: str | None = None) -> str:
    """Return the volatile half of the encyclopedia prompt."""
    body = f"Headword: {headword}"
    return f"{body}\nPrimary sense: {gloss}" if gloss else body


def build_lexical_explanation_prompt(headword: str, gloss: str | None = None) -> str:
    """Return the volatile half of the lexical-explanation prompt."""
    body = f"Headword: {headword}"
    return f"{body}\nPrimary sense: {gloss}" if gloss else body


def build_frontier_prompt(candidates: Sequence[str]) -> str:
    """Return the volatile half of the frontier triage prompt."""
    listed = "\n".join(f"  {i + 1}. {term}" for i, term in enumerate(candidates))
    return f"Candidates ({len(candidates)}):\n{listed}"


def build_related_terms_prompt(entry: Lexeme, limit: int) -> str:
    """Return the volatile half of the related-terms prompt.

    Args:
        entry: The entry whose neighbourhood is being widened.
        limit: Maximum number of new terms to request.

    Returns:
        The per-call prompt body.
    """
    primary = next(
        (sense.canonical_gloss() for _, sense, _ in entry.iter_senses() if not sense.retired),
        "(no definition available)",
    )
    existing = sorted(entry.relation_targets())
    return (
        f"Headword: {entry.headword}\n"
        f"Primary sense: {primary}\n"
        f"Already linked ({len(existing)}): {', '.join(existing) if existing else '(none)'}\n"
        f"Propose at most {limit} additional terms."
    )


def _truncate_words(text: str, limit: int = QA_TEXT_WORD_LIMIT) -> str:
    """Return at most ``limit`` whitespace-separated words of ``text``, marked if cut.

    Args:
        text: The text to shorten.
        limit: Maximum number of words to keep.

    Returns:
        ``text`` unchanged when it is short enough, otherwise its first ``limit`` words
        followed by an ellipsis, so the judge can see that it was shown an opening
        rather than a whole section and does not mark the truncation as missing content.
    """
    words = text.split()
    if len(words) <= limit:
        return text
    return " ".join(words[:limit]) + " ..."


def build_qa_prompt(
    headword: str,
    kind: str,
    senses: Sequence[QASenseView],
    renditions: Sequence[QARenditionView],
    encyclopedia: str | None = None,
) -> str:
    """Return the volatile half of the QA judge prompt.

    Everything that bounds the prompt's size is applied here rather than by the caller,
    so the cap is a property of the prompt itself: senses are cut to
    :data:`~opengloss_generator.contracts.QA_MAX_SENSES` (the contract's own ceiling, so
    the judge is never shown a sense it has no room to answer for) and every long text
    to :data:`QA_TEXT_WORD_LIMIT` words. A whole entry rendered without those two caps
    runs past 10K tokens on a polysemous headword with four encyclopedia renditions;
    with them it lands near 3.5K.

    Args:
        headword: The entry's surface form.
        kind: The entry's structural kind, as a plain string.
        senses: ``(label, gloss, examples, relations, domain)`` per sense, in the order
            the judge should answer. The judge refers to a sense by its position here.
        renditions: ``(label, text)`` for each sampled rendition, in the order the judge
            should answer. The label says what the rendition is of and what level and
            register it claims.
        encyclopedia: The canonical encyclopedia opening, or ``None`` when the entry has
            no encyclopedia section. Without it the judge has no source to check the
            sampled encyclopedia renditions against.

    Returns:
        The per-call prompt body. The rubric itself is not here — it is in the static
        instructions, which is what lets a sweep's verdicts be compared with each other.
    """
    shown = list(senses)[:QA_MAX_SENSES]
    lines = [f"Headword: {headword}", f"Kind: {kind}", f"Senses ({len(shown)}):"]
    for index, (label, gloss, examples, relations, domain) in enumerate(shown, start=1):
        lines.append(f"  {index}. [{label}] {_truncate_words(gloss)}")
        for example in examples:
            lines.append(f"     example: {_truncate_words(example)}")
        lines.append(f"     relations: {', '.join(relations) if relations else '(none)'}")
        lines.append(f"     domain: {domain}")
    lines.append(f"Renditions ({len(renditions)}):")
    lines.extend(
        f"  {index}. [{label}] {_truncate_words(text)}"
        for index, (label, text) in enumerate(renditions, start=1)
    )
    if encyclopedia:
        lines.append(f"Encyclopedia (canonical opening): {_truncate_words(encyclopedia)}")
    return "\n".join(lines)
