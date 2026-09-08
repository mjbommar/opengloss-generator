"""Scratch script (D-81/D-84 alias-prompt pilot): add ~30 true alias positives.

The D-81 pilot's 100-pair sample (``data/sample-ner``, rebuilt fresh by
``build_sample_ner.py`` from the untouched production store) is entirely compound-head
pairs, where the correct answer is always ``see_also`` or ``none`` -- it has no positive
to detect a regression against. Tier 6's own long-name entries (``abraham_lincoln``,
``albert_einstein``, ...) do not exist in the production store yet (stage 1 has not run),
so no true ``alias_of`` pair of two *live* entries can be copied wholesale.

This authors ~30 minimal long-form entries and pairs each with a short single-word entry
the production store already holds live (read only, never written): person surnames whose
own canonical gloss already names the specific bearer (``darwin`` -> Charles Darwin,
``einstein`` -> Albert Einstein, ...), plus a handful of institutional abbreviations whose
own gloss already expands the acronym (``nato``, ``fbi``, ...). The long entries are new
inventions built for this pilot only, written into ``data/sample-ner`` alongside the
copied short entries; nothing in the production store is touched.

Usage:
    uv run python scripts/_add_true_alias_pairs.py
"""

from __future__ import annotations

from pathlib import Path

from opengloss_generator.config import StoreConfig
from opengloss_generator.identity import shard_for
from opengloss_generator.schema import (
    EntityType,
    Example,
    Lexeme,
    LexemeKind,
    PartOfSpeech,
    POSEntry,
    ProperNounInfo,
    Renditions,
    Sense,
    canonical_rendition,
)
from opengloss_generator.store import LexemeStore

_MAIN_CHECKOUT = Path("/home/mjbommar/projects/personal/opengloss-generator")
_SOURCE_STORE = _MAIN_CHECKOUT / "data/core-store"
_DEST_STORE = Path("data/sample-ner")
_CANDIDATE_LIST = Path("data/sample-ner-candidates.tsv")

#: (long headword, short-entry slug already live in the production store, long gloss).
#: Person pairs first -- the short entry's own gloss names the bearer but not the full
#: name string, so the free `_target_names_headword` skip does NOT fire and a verdict is
#: actually bought. This is the population the D-81 finding (the head-noun false
#: positive) was never measured against, and is exactly the population tier 6 is made of.
_PERSON_PAIRS: list[tuple[str, str, str]] = [
    (
        "Abraham Lincoln",
        "lincoln",
        "The 16th President of the United States (1809-1865), "
        "who led the Union through the Civil War and issued the Emancipation Proclamation.",
    ),
    (
        "Charles Darwin",
        "darwin",
        "The English naturalist (1809-1882) who proposed the "
        "theory of evolution by natural selection in On the Origin of Species.",
    ),
    (
        "Albert Einstein",
        "einstein",
        "The German-born theoretical physicist (1879-1955) "
        "who developed the theory of relativity and won the 1921 Nobel Prize in Physics.",
    ),
    (
        "William Shakespeare",
        "shakespeare",
        "The English playwright and poet "
        "(1564-1616), widely regarded as the greatest writer in the English language.",
    ),
    (
        "Wolfgang Amadeus Mozart",
        "mozart",
        "The Austrian composer (1756-1791), a "
        "prolific and influential figure of the Classical era.",
    ),
    (
        "Ludwig van Beethoven",
        "beethoven",
        "The German composer and pianist "
        "(1770-1827) whose works bridge the Classical and Romantic eras.",
    ),
    (
        "Johann Sebastian Bach",
        "bach",
        "The German composer and organist (1685-1750) of the late Baroque period.",
    ),
    (
        "Pablo Picasso",
        "picasso",
        "The Spanish painter and sculptor (1881-1973), co-founder of the Cubist movement.",
    ),
    ("Sigmund Freud", "freud", "The Austrian neurologist (1856-1939) who founded psychoanalysis."),
    (
        "Karl Marx",
        "marx",
        "The German philosopher and economist (1818-1883) whose writings underlie Marxist theory.",
    ),
    (
        "Mahatma Gandhi",
        "gandhi",
        "The Indian lawyer and independence leader "
        "(1869-1948) who led nonviolent resistance to British rule.",
    ),
    (
        "Adolf Hitler",
        "hitler",
        "The Austrian-born German dictator (1889-1945) who led "
        "the Nazi Party and ruled Germany from 1933 to 1945.",
    ),
    (
        "Joseph Stalin",
        "stalin",
        "The Soviet leader (1878-1953) who ruled the Soviet "
        "Union from the mid-1920s until his death.",
    ),
    (
        "Leo Tolstoy",
        "tolstoy",
        "The Russian novelist (1828-1910), author of War and Peace and Anna Karenina.",
    ),
    (
        "Charles Dickens",
        "dickens",
        "The English novelist (1812-1870), author of Oliver Twist and Great Expectations.",
    ),
    (
        "Vladimir Lenin",
        "lenin",
        "The Russian revolutionary (1870-1924) who led the "
        "Bolsheviks and founded the Soviet state.",
    ),
    (
        "Martin Luther",
        "luther",
        "The German theologian (1483-1546) whose criticisms of "
        "the Catholic Church sparked the Protestant Reformation.",
    ),
    (
        "Galileo Galilei",
        "galileo",
        "The Italian astronomer and physicist (1564-1642), "
        "a central figure of the scientific revolution.",
    ),
    (
        "Aristotle of Stagira",
        "aristotle",
        "The ancient Greek philosopher (384-322 BCE) "
        "whose work shaped Western logic, ethics, and natural science.",
    ),
    (
        "Socrates of Athens",
        "socrates",
        "The classical Greek philosopher active in "
        "Athens whose method of questioning underlies Western philosophy.",
    ),
    (
        "Emperor Nero",
        "nero",
        "The Roman emperor who reigned from 54 to 68 CE, known for his autocratic rule.",
    ),
    (
        "Cleopatra VII",
        "cleopatra",
        "The last active ruler of the Ptolemaic Kingdom of Egypt, reigning from 51 to 30 BCE.",
    ),
    (
        "Nelson Mandela",
        "mandela",
        "The South African anti-apartheid leader "
        "(1918-2013) who served as president of South Africa from 1994 to 1999.",
    ),
]

#: Institutional abbreviations. The short entry's own gloss already expands the name
#: verbatim, so most of these settle for free -- included anyway because a free `alias_of`
#: is still a positive the after-run must keep getting right, and D-81's own worked
#: examples ("UN", "FDR") are exactly this shape.
_ABBREVIATION_PAIRS: list[tuple[str, str, str]] = [
    (
        "North Atlantic Treaty Organization",
        "nato",
        "A military alliance of North American and European states, founded in 1949.",
    ),
    (
        "Federal Bureau of Investigation",
        "fbi",
        "The principal federal law enforcement and domestic intelligence agency of the "
        "United States.",
    ),
    (
        "Central Intelligence Agency",
        "cia",
        "The United States federal agency responsible for foreign intelligence "
        "collection and covert operations.",
    ),
    (
        "National Aeronautics and Space Administration",
        "nasa",
        "The independent agency of the United States federal government responsible for "
        "the civilian space program.",
    ),
    (
        "United Nations Educational, Scientific and Cultural Organization",
        "unesco",
        "A specialized agency of the United Nations that promotes international "
        "cooperation in education, science, and culture.",
    ),
    (
        "International Federation of Association Football",
        "fifa",
        "The international governing body of association football, futsal, and beach soccer.",
    ),
    (
        "National Basketball Association",
        "nba",
        "The major professional basketball league in North America.",
    ),
    (
        "National Football League",
        "nfl",
        "The major professional American football league in the United States.",
    ),
]


def _short_exists(slug: str) -> bool:
    return (_SOURCE_STORE.joinpath(*shard_for(slug), f"{slug}.json")).exists()


def _copy_short(store: LexemeStore, slug: str) -> None:
    """Copy one short entry read-only from the production store into the pilot store."""
    src = _SOURCE_STORE.joinpath(*shard_for(slug), f"{slug}.json")
    dest_dir = _DEST_STORE.joinpath(*shard_for(slug))
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{slug}.json"
    if not dest.exists():
        dest.write_bytes(src.read_bytes())


def _write_long(store: LexemeStore, headword: str, gloss: str, entity_type: EntityType) -> None:
    """Author one minimal long-form entry, invented for this pilot only."""
    sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition(gloss)]),
        examples=Renditions[Example](root=[]),
        relations=[],
    )
    entry = Lexeme.empty(
        headword,
        kind=LexemeKind.PROPER_NOUN,
        pos_entries=[POSEntry(pos=PartOfSpeech.NOUN, senses=[sense])],
        proper_noun=ProperNounInfo(entity_type=entity_type),
    )
    store.write(entry)


def main() -> None:
    store = LexemeStore(StoreConfig(root=_DEST_STORE))
    rows: list[str] = []
    written = 0
    tagged_pairs = [(h, s, g, EntityType.PERSON) for h, s, g in _PERSON_PAIRS] + [
        (h, s, g, EntityType.ORGANIZATION) for h, s, g in _ABBREVIATION_PAIRS
    ]
    for long_headword, slug, gloss, entity_type in tagged_pairs:
        if not _short_exists(slug):
            print(  # noqa: T201 - reports to stdout
                f"SKIP {long_headword!r}: short entry {slug!r} missing from production store"
            )
            continue
        _copy_short(store, slug)
        _write_long(store, long_headword, gloss, entity_type)
        rows.append(
            "\t".join(
                (
                    long_headword,
                    long_headword,
                    entity_type.value,
                    "name_seed",
                    "store",
                    "0",
                    "",
                    "0",
                    "0",
                    "0",
                    "1",
                    slug,
                    "",
                    "",
                    f"alias_of candidate: store has '{slug}'",
                )
            )
        )
        written += 1

    with _CANDIDATE_LIST.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + "\n")

    print(  # noqa: T201 - reports to stdout
        f"added {written} true-alias candidate pairs to {_CANDIDATE_LIST}"
    )


if __name__ == "__main__":
    main()
