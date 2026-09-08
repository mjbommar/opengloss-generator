"""Build the tier-6 named-entity candidate list. Zero model calls, zero cost.

Ranks proper nouns and named noun phrases by importance to an English speaker in the
United States, from four free sources:

* **English Wikipedia "vital articles"** levels 1-5 (`Wikipedia:Vital articles/data/*.json`,
  CC BY-SA 4.0) — a hand-curated importance ladder over ~50K article titles. This is the
  backbone: the level *is* the importance prior.
* **Wikidata** (CC0), via the SPARQL endpoint and only for candidate items: sitelink
  count (global notability), `P31` instance-of (entity typing), `P17`/`P27` (the US and
  anglophone boost), `P569`/`P570`/`P571` (living-person and recency filters), plus ~25
  curated US-specific list queries (presidents, states, Supreme Court, S&P 500, national
  parks, pro sports teams, federal agencies...).
* **Wikimedia pageviews** `top-per-country/US` (CC0) — how many of 14 sampled days an
  article was in the top 1,000 read *in the United States*. A tie-breaker only; it never
  introduces a candidate on its own, because it is dominated by the news cycle.
* **WordNet 3.0 instance synsets** (WordNet License) — the 14,391 lemmas Princeton's
  lexicographers already judged dictionary-worthy names, with an instance hypernym that
  types them and can seed the entry's `hypernym` relation.

Nothing here downloads a multi-GB dump: `enwiki-latest-pagelinks.sql.gz` is 7.1 GB, so
incoming-link count is *not* among the signals (see docs/NAMED-ENTITY-PLAN.md § 1).

Usage:
    uv run --with pyarrow python scripts/build_tier6_candidates.py \
        --cache data/core/tier6_cache --out data/core/tier6_candidates.tsv \
        --docs-out docs/tier6_top200.md --size 15000

    # re-score from the cache without touching the network
    uv run --with pyarrow python scripts/build_tier6_candidates.py --offline ...
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

USER_AGENT = "OpenGloss-tier6-builder/0.1 (https://github.com/mjbommar; michael@bommaritollc.com)"
WIKI_API = "https://en.wikipedia.org/w/api.php"
WDQS = "https://query.wikidata.org/sparql"
PAGEVIEWS = "https://wikimedia.org/api/rest_v1/metrics/pageviews/top-per-country/US/all-access"

VITAL_SHARDS = [*(chr(c) for c in range(ord("A"), ord("Z") + 1)), "others"]

#: Days sampled from the US top-1,000 pageview endpoint. Spread over a year so one news
#: cycle cannot dominate; ``all-days`` is not supported by this endpoint.
PAGEVIEW_DAYS = [
    f"{y}/{m:02d}/{d:02d}"
    for y, m in [(2025, 9), (2025, 11), (2026, 1), (2026, 3), (2026, 5), (2026, 7), (2026, 8)]
    for d in (5, 18)
]

# --------------------------------------------------------------------------------------
# Scoring weights. Every number below is stated in docs/NAMED-ENTITY-PLAN.md § 2.
# --------------------------------------------------------------------------------------

#: Vital-article level -> points. The backbone of the score.
VITAL_POINTS = {1: 100.0, 2: 90.0, 3: 80.0, 4: 60.0, 5: 40.0}
#: Multiplier on log10(1 + sitelinks): 300 sitelinks ~ 30 pts, 30 ~ 18, 5 ~ 9.
SITELINK_WEIGHT = 12.0
#: A WordNet lexicographer already judged this name dictionary-worthy.
WORDNET_POINTS = 15.0
#: Country / citizenship / country-of-origin is the United States.
US_POINTS = 12.0
#: ... or another majority-anglophone country whose names a US reader still meets.
ANGLO_POINTS = 6.0
#: On a curated US list (president, state, SCOTUS justice, S&P 500, pro team, park...).
US_LIST_POINTS = 15.0
#: A *broad* membership list — every Nobel laureate, every US city over 100K, every
#: sovereign country. Real evidence, but not the tight canonical enumeration that
#: "the 47 presidents" or "the 50 states" is, so it is worth about half as much.
WORLD_LIST_POINTS = 8.0
#: The broad lists, by name.
BROAD_LISTS = frozenset(
    {
        "nobel_laureate",
        "nobel_peace",
        "us_cabinet_sec",
        "us_university",
        "us_city_100k",
        "us_battle",
        "us_conflict",
        "world_country",
    }
)
#: Max points from the US pageview signal, reached at 14/14 sampled days.
PAGEVIEW_MAX = 6.0
#: A living person's gloss dates; a lexicon prefers settled subjects.
LIVING_PENALTY = -10.0
#: An organisation or work founded very recently has no settled meaning yet.
RECENT_PENALTY = -10.0
#: An over-long title is an article subject, not a headword.
LONG_TITLE_PENALTY = -8.0

#: Anglophone countries other than the US (UK, Canada, Australia, Ireland, New Zealand).
ANGLO_QIDS = {"Q145", "Q16", "Q408", "Q27", "Q664", "Q21", "Q22", "Q26"}
US_QID = "Q30"

#: Born on or after this year with no recorded death -> treated as a living person.
LIVING_BIRTH_YEAR = 1935
#: Founded/created on or after this year -> treated as too recent to have settled.
RECENT_INCEPTION_YEAR = 2015

#: Per-type *ceiling*, as a share of the cut. Without a cap on ``person`` the list is
#: 55% biographies, because vital level 5 alone holds 14,209 of them. The shares sum to
#: 1.13, deliberately: they are ceilings, not targets, so a type that has no more good
#: candidates gives its slack to the next-best rows overall instead of being padded out.
TYPE_QUOTA = {
    "person": 0.42,
    "place": 0.26,
    "organization": 0.15,
    "work": 0.15,
    "event": 0.10,
    "other": 0.05,
}

#: Hand-checked type for the 200 most frequent ``P31`` values across the candidate pool
#: (they cover ~85% of all instance-of statements in it). This map *wins* over the
#: subclass closure below, because Wikidata's ``P279`` graph is noisy at scale: "war" and
#: "economic crisis" both reach `geographic location`, which would file World War II as a
#: place. ``CONCEPT`` marks a class whose members are categories rather than names
#: ("academic discipline", "type of chemical entity", "family name") — those are rejected,
#: not typed. Fictional and mythological characters are ``person``, following OntoNotes,
#: whose PERSON type is explicitly "people, including fictional" (STANDARDS.md § 4a).
# fmt: off
CLASS_MAP: dict[str, str] = {
    # people
    "Q5": "person", "Q20643955": "person", "Q15632617": "person", "Q3658341": "person",
    "Q15773347": "person", "Q15773317": "person", "Q1114461": "person",
    "Q22988604": "person", "Q15711870": "person",
    # places
    "Q1549591": "place", "Q515": "place", "Q1093829": "place", "Q4022": "place",
    "Q23442": "place", "Q6256": "place", "Q82794": "place", "Q62049": "place",
    "Q8502": "place", "Q46831": "place", "Q748149": "place", "Q2264924": "place",
    "Q33837": "place", "Q1620908": "place", "Q56061": "place", "Q51929311": "place",
    "Q486972": "place", "Q1402592": "place", "Q23397": "place", "Q3502482": "place",
    "Q484170": "place", "Q15661340": "place", "Q7930989": "place", "Q39594": "place",
    "Q34763": "place", "Q902814": "place", "Q1187811": "place", "Q108178728": "place",
    "Q42744322": "place", "Q747074": "place", "Q200250": "place", "Q112099": "place",
    "Q35657": "place", "Q56557504": "place", "Q37901": "place", "Q180673": "place",
    "Q50337": "place", "Q13218391": "place", "Q494721": "place", "Q3184121": "place",
    "Q3199141": "place", "Q20202352": "place", "Q13218357": "place", "Q165": "place",
    "Q123480": "place", "Q3624078": "place", "Q3024240": "place", "Q15239622": "place",
    "Q46169": "place", "Q893745": "place", "Q34918903": "place", "Q893775": "place",
    "Q30304302": "place", "Q1768043": "place", "Q839954": "place", "Q3957": "place",
    "Q174844": "place", "Q537127": "place", "Q9430": "place",
    # organisations
    "Q4830453": "organization", "Q891723": "organization", "Q6881511": "organization",
    "Q20857065": "organization", "Q23002039": "organization", "Q215380": "organization",
    "Q43229": "organization", "Q3918": "organization", "Q23002054": "organization",
    "Q875538": "organization", "Q62078547": "organization", "Q1752939": "organization",
    "Q45400320": "organization", "Q163740": "organization", "Q902104": "organization",
    "Q615150": "organization", "Q15936437": "organization", "Q7278": "organization",
    "Q484652": "organization", "Q245065": "organization", "Q33506": "organization",
    "Q207694": "organization", "Q1110794": "organization", "Q17127659": "organization",
    "Q4498974": "organization", "Q13393265": "organization", "Q5503": "organization",
    "Q16887380": "organization", "Q910252": "organization", "Q12973014": "organization",
    # works
    "Q7725634": "work", "Q105543609": "work", "Q11424": "work", "Q5398426": "work",
    "Q47461344": "work", "Q3305213": "work", "Q179461": "work", "Q482994": "work",
    "Q58483083": "work", "Q29154430": "work", "Q7889": "work", "Q116476516": "work",
    "Q7058673": "work", "Q196600": "work", "Q35127": "work", "Q571": "work",
    "Q838948": "work", "Q2188189": "work",
    # events
    "Q178561": "event", "Q198": "event", "Q8465": "event", "Q1261499": "event",
    "Q188055": "event", "Q104212151": "event", "Q831663": "event", "Q124734": "event",
    "Q10931": "event", "Q645883": "event", "Q18608583": "event", "Q11514315": "event",
    "Q17544377": "event", "Q1079023": "event", "Q34439356": "event", "Q752783": "event",
    "Q273120": "event", "Q864113": "event", "Q1469686": "event", "Q4176199": "event",
    "Q176494": "event", "Q1006644": "event",
    # named things that are none of the above
    "Q8928": "other", "Q41710": "other", "Q164950": "other", "Q25295": "other",
    "Q34770": "other", "Q33742": "other", "Q1288568": "other", "Q9174": "other",
    "Q465299": "other", "Q968159": "other", "Q49773": "other", "Q4204501": "other",
    "Q214070": "other", "Q65943": "other", "Q32880": "other", "Q11344": "other",
    # classes whose members are categories, not names
    "Q11862829": "CONCEPT", "Q113145171": "CONCEPT", "Q112193867": "CONCEPT",
    "Q101352": "CONCEPT", "Q112826905": "CONCEPT", "Q188451": "CONCEPT",
    "Q1047113": "CONCEPT", "Q55983715": "CONCEPT", "Q31629": "CONCEPT",
    "Q112965645": "CONCEPT", "Q4671286": "CONCEPT", "Q151885": "CONCEPT",
    "Q47154513": "CONCEPT", "Q2267705": "CONCEPT", "Q124078422": "CONCEPT",
    "Q28640": "CONCEPT", "Q24034552": "CONCEPT", "Q2996394": "CONCEPT",
    "Q12737077": "CONCEPT", "Q268592": "CONCEPT", "Q1914636": "CONCEPT",
    "Q2312410": "CONCEPT", "Q23847174": "CONCEPT", "Q12909644": "CONCEPT",
    "Q47728": "CONCEPT", "Q2465832": "CONCEPT", "Q63981612": "CONCEPT",
    "Q107357104": "CONCEPT", "Q82047057": "CONCEPT", "Q2135465": "CONCEPT",
    "Q223393": "CONCEPT", "Q1936384": "CONCEPT", "Q4164871": "CONCEPT",
    "Q110295396": "CONCEPT", "Q930752": "CONCEPT", "Q627436": "CONCEPT",
    "Q109551565": "CONCEPT", "Q4162444": "CONCEPT", "Q29028649": "CONCEPT",
    "Q483394": "CONCEPT", "Q22675015": "CONCEPT", "Q25403900": "CONCEPT",
    "Q19861951": "CONCEPT", "Q12308941": "CONCEPT", "Q130583773": "CONCEPT",
    "Q2695280": "CONCEPT", "Q12015335": "CONCEPT", "Q28598684": "CONCEPT",
    "Q5058355": "CONCEPT", "Q7257": "CONCEPT", "Q8187769": "CONCEPT",
    "Q103812529": "CONCEPT", "Q1792379": "CONCEPT", "Q355567": "CONCEPT",
    "Q17444909": "CONCEPT", "Q12089225": "CONCEPT", "Q17524420": "CONCEPT",
    "Q23038290": "species", "Q16521": "species",
    # page kinds
    "Q4167410": "DISAMBIG", "Q22808320": "DISAMBIG", "Q13406463": "LIST",
    "Q4167836": "CATEGORY",
}
# fmt: on

#: Wikidata top classes that decide an entity type, in priority order. A `taxon` is a
#: species name, which tier 5 already excluded and this tier does not want either.
CLASS_TOPS = {
    "Q5": "person",
    "Q43229": "organization",
    "Q2221906": "place",
    "Q386724": "work",
    "Q17537576": "work",
    "Q571": "work",
    "Q7725634": "work",
    "Q2188189": "work",
    "Q838948": "work",
    "Q11424": "work",
    "Q1656682": "event",
    "Q16521": "species",
    "Q4167410": "DISAMBIG",
    "Q13406463": "LIST",
    "Q4167836": "CATEGORY",
    "Q811979": "other",
    "Q8205328": "other",
    "Q15642541": "organization",
    "Q3505845": "place",
    "Q15617994": "other",
    "Q28640": "other",
}
#: Priority when an item matches several tops. ``place`` must beat ``organization``:
#: Wikidata files "city" and "country" under *both* (a polity is an organisation), and a
#: city is a place first. Universities, agencies and companies match only ``organization``,
#: so nothing is lost by the order.
TYPE_PRIORITY = ["person", "place", "organization", "work", "event", "species", "other"]

#: Vital topics that name a *concept* rather than an entity even when the title is
#: capitalised; used only as a fallback when Wikidata gives no usable class.
CONCEPT_TOPICS = {"Mathematics", "Physical sciences", "Technology", "Everyday life"}

#: Title shapes a lexicon should not carry as a headword.
BAD_TITLE = re.compile(
    r"^(List of |Lists of |Index of |Outline of |Timeline of |Glossary of |History of |"
    r"Comparison of |Bibliography of |Category:|Portal:|Wikipedia:|Template:|Draft:|"
    r"Special:|File:|Help:|Talk:)",
    re.IGNORECASE,
)
DISAMBIG_TITLE = re.compile(r"\((disambiguation|surname|given name|name)\)$", re.IGNORECASE)
PARENTHETICAL = re.compile(r"\s*\([^)]*\)\s*$")
NON_SLUG = re.compile(r"[^a-z0-9]+")

MAX_TITLE_WORDS = 6
MAX_TITLE_CHARS = 48

#: Store kinds that mean "this string is an ordinary English word", used to tell a name
#: whose first token happens to be lowercase-able ("Climate change") from one whose is
#: not ("Apollo program").
COMMON_WORD_KINDS = frozenset({"simplex", "compound", "function_word", "phrasal_verb", "idiom"})

#: Words a genuine multi-word name may leave lowercase ("War of the Roses", "Bank of
#: America", "Ludwig van Beethoven"), so they do not count as evidence *against* a name.
# fmt: off
NAME_INTERNAL_LOWERCASE = {
    "a", "al", "an", "and", "at", "bin", "by", "da", "das", "de", "del", "der", "des",
    "di", "dos", "du", "e", "el", "for", "from", "ibn", "in", "la", "las", "le", "les",
    "los", "of", "on", "or", "the", "to", "van", "von", "y", "zu",
}
# fmt: on


def looks_like_a_name(title: str) -> bool:
    """Return whether the *surface form* is a proper noun, not a capitalised concept.

    English marks names orthographically, and a Wikipedia title capitalises only its
    first word unless the rest of the phrase is itself a name. So "New York City",
    "World War II" and "Bank of America" read as names, while "Injection moulding",
    "Neutron radiation" and "Jensen's inequality" — all vital articles, all typed by
    Wikidata as some subclass of *work* or *process* — do not. Single-token titles carry
    no such evidence and are decided by the caller from the entity type instead.
    """
    tokens = PARENTHETICAL.sub("", title).strip().split()
    if len(tokens) < 2:
        return False
    return any(
        token[:1].isupper() or token[:1].isdigit()
        for token in tokens[1:]
        if token.strip(".,'’-").lower() not in NAME_INTERNAL_LOWERCASE
    )


def slugify(headword: str) -> str:
    """Return the OpenGloss lexeme id for a headword (mirrors ``identity.slugify``)."""
    folded = unicodedata.normalize("NFKD", headword)
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")
    return NON_SLUG.sub("_", ascii_only.lower()).strip("_")


# --------------------------------------------------------------------------------------
# Network helpers. Every fetch caches its raw response, so a re-run is free and offline.
# --------------------------------------------------------------------------------------


def _get(url: str, *, timeout: int = 90, accept: str | None = None) -> bytes:
    """GET a URL with the project user agent, retrying transient failures."""
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    last: Exception | None = None
    for attempt in range(5):
        try:
            request = urllib.request.Request(url, headers=headers)  # noqa: S310
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                return response.read()
        except Exception as error:
            last = error
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"GET failed after 5 attempts: {url}: {last}")


def _cached(cache: Path, name: str, build: Any, *, offline: bool) -> Any:
    """Return ``cache/name``'s JSON, building and writing it when it is absent."""
    path = cache / name
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    if offline:
        raise SystemExit(f"--offline but {path} is missing; run once with the network")
    value = build()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return value


def sparql(query: str, *, timeout: int = 300) -> list[dict[str, Any]]:
    """Run a SPARQL query against WDQS and return its bindings."""
    url = f"{WDQS}?{urllib.parse.urlencode({'query': query})}"
    raw = _get(url, timeout=timeout, accept="application/sparql-results+json")
    return json.loads(raw)["results"]["bindings"]


def _qid(uri: str) -> str:
    """Return the bare Q-id from a Wikidata entity URI."""
    return uri.rsplit("/", 1)[-1]


def _cell(binding: dict[str, Any], key: str) -> str:
    """Return one variable's value from a SPARQL binding, or ``""`` if it is unbound."""
    return binding.get(key, {}).get("value") or ""


# --------------------------------------------------------------------------------------
# Source 1 — Wikipedia vital articles (CC BY-SA 4.0)
# --------------------------------------------------------------------------------------


def fetch_vital(cache: Path, *, offline: bool) -> dict[str, dict[str, Any]]:
    """Return ``title -> {level, topic, section}`` for every vital article, levels 1-5."""

    def build() -> dict[str, dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for shard in VITAL_SHARDS:
            url = f"https://en.wikipedia.org/wiki/Wikipedia:Vital_articles/data/{shard}.json?action=raw"
            merged.update(json.loads(_get(url).decode("utf-8")))
            time.sleep(0.2)
        return merged

    return _cached(cache, "vital_all.json", build, offline=offline)


# --------------------------------------------------------------------------------------
# Source 2 — Wikipedia pageprops: title -> Wikidata QID, redirects resolved
# --------------------------------------------------------------------------------------


def fetch_pageprops(
    cache: Path, name: str, titles: list[str], *, offline: bool
) -> dict[str, dict[str, Any]]:
    """Return ``title -> {final_title, qid, disambiguation, missing}`` for ``titles``."""

    def build() -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for start in range(0, len(titles), 50):
            chunk = titles[start : start + 50]
            query = {
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "prop": "pageprops",
                "ppprop": "wikibase_item|disambiguation",
                "redirects": "1",
                "titles": "|".join(chunk),
            }
            payload = json.loads(_get(f"{WIKI_API}?{urllib.parse.urlencode(query)}").decode())
            result = payload.get("query", {})
            normalized = {n["to"]: n["from"] for n in result.get("normalized", [])}
            redirects = {r["to"]: r["from"] for r in result.get("redirects", [])}
            for page in result.get("pages", []):
                title = page["title"]
                props = page.get("pageprops", {})
                record = {
                    "final_title": title,
                    "qid": props.get("wikibase_item"),
                    "disambiguation": "disambiguation" in props,
                    "missing": bool(page.get("missing", False)),
                }
                source = redirects.get(title, title)
                out[normalized.get(source, source)] = record
            for title in chunk:
                out.setdefault(
                    title,
                    {"final_title": title, "qid": None, "disambiguation": False, "missing": True},
                )
            time.sleep(0.05)
        return out

    return _cached(cache, name, build, offline=offline)


# --------------------------------------------------------------------------------------
# Source 3 — Wikidata facts for candidate items only (CC0)
# --------------------------------------------------------------------------------------

FACTS_QUERY = """SELECT ?item ?sl
  (GROUP_CONCAT(DISTINCT ?p31x; separator=",") AS ?p31)
  (GROUP_CONCAT(DISTINCT ?ctyx; separator=",") AS ?cty)
  (GROUP_CONCAT(DISTINCT ?citx; separator=",") AS ?cit)
  (SAMPLE(?dobx) AS ?dob) (SAMPLE(?dodx) AS ?dod) (SAMPLE(?incx) AS ?inc)
WHERE {
  VALUES ?item { %s }
  ?item wikibase:sitelinks ?sl .
  OPTIONAL { ?item wdt:P31 ?p31x }
  OPTIONAL { ?item wdt:P17 ?ctyx }
  OPTIONAL { ?item wdt:P27 ?citx }
  OPTIONAL { ?item wdt:P569 ?dobx }
  OPTIONAL { ?item wdt:P570 ?dodx }
  OPTIONAL { ?item wdt:P571 ?incx }
} GROUP BY ?item ?sl"""


def fetch_facts(cache: Path, qids: list[str], *, offline: bool) -> dict[str, dict[str, Any]]:
    """Return per-QID sitelink count, instance-of classes, country, and dates."""

    def build() -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for start in range(0, len(qids), 400):
            chunk = qids[start : start + 400]
            values = " ".join("wd:" + q for q in chunk)
            for binding in sparql(FACTS_QUERY % values):
                out[_qid(binding["item"]["value"])] = {
                    "sitelinks": int(binding["sl"]["value"]),
                    "p31": [_qid(x) for x in _cell(binding, "p31").split(",") if x],
                    "country": [_qid(x) for x in _cell(binding, "cty").split(",") if x],
                    "citizenship": [_qid(x) for x in _cell(binding, "cit").split(",") if x],
                    "dob": _cell(binding, "dob")[:10],
                    "dod": _cell(binding, "dod")[:10],
                    "inception": _cell(binding, "inc")[:10],
                }
            time.sleep(0.3)
        return out

    return _cached(cache, "wd_facts.json", build, offline=offline)


CLASS_QUERY = """SELECT ?c ?top WHERE {
  VALUES ?c { %s }
  VALUES ?top { %s }
  ?c wdt:P279* ?top .
}"""


def fetch_class_types(cache: Path, classes: list[str], *, offline: bool) -> dict[str, list[str]]:
    """Return ``class QID -> [entity types it is a subclass of]``.

    Resolving the ~8K distinct ``P31`` values once is far cheaper than asking for a
    transitive-closure test per candidate.
    """

    def build() -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        tops = " ".join("wd:" + t for t in CLASS_TOPS)
        for start in range(0, len(classes), 300):
            chunk = classes[start : start + 300]
            hits: dict[str, set[str]] = {}
            values = " ".join("wd:" + c for c in chunk)
            for binding in sparql(CLASS_QUERY % (values, tops)):
                hits.setdefault(_qid(binding["c"]["value"]), set()).add(
                    CLASS_TOPS[_qid(binding["top"]["value"])]
                )
            for klass in chunk:
                out[klass] = sorted(hits.get(klass, set()))
            time.sleep(0.3)
        return out

    return _cached(cache, "class_types.json", build, offline=offline)


# --------------------------------------------------------------------------------------
# Source 4 — curated US-specific lists, straight out of Wikidata
# --------------------------------------------------------------------------------------

US_LIST_QUERIES: dict[str, tuple[str, str]] = {
    "us_president": ("person", "?item wdt:P39 wd:Q11696 ."),
    "us_vice_president": ("person", "?item wdt:P39 wd:Q11699 ."),
    "scotus_justice": ("person", "?item wdt:P39 wd:Q11144 ."),
    "us_cabinet_sec": ("person", "?item wdt:P39 ?pos . ?pos wdt:P361 wd:Q639738 ."),
    "nobel_laureate": (
        "person",
        "VALUES ?p { wd:Q38104 wd:Q44585 wd:Q80061 wd:Q47170 } ?item wdt:P166 ?p .",
    ),
    "nobel_peace": ("person", "?item wdt:P166 wd:Q35637 ."),
    "us_state": ("place", "?item wdt:P31 wd:Q35657 ."),
    "us_state_capital": ("place", "?item wdt:P1376 ?st . ?st wdt:P31 wd:Q35657 ."),
    "us_city_100k": (
        "place",
        "?item wdt:P17 wd:Q30 ; wdt:P31/wdt:P279* wd:Q486972 ; wdt:P1082 ?pop ."
        " FILTER(?pop >= 100000)",
    ),
    "us_national_park": ("place", "?item wdt:P31 wd:Q34918903 ."),
    "us_natl_monument": ("place", "?item wdt:P31 wd:Q893775 ."),
    "world_country": ("place", "?item wdt:P31 wd:Q6256 ."),
    "us_fed_department": ("organization", "?item wdt:P31 wd:Q910252 ."),
    "us_fed_agency": ("organization", "VALUES ?c { wd:Q20857065 wd:Q1752939 } ?item wdt:P31 ?c ."),
    "sp500_company": ("organization", "?item wdt:P361 wd:Q242345 ."),
    "dow30_company": ("organization", "?item wdt:P361 wd:Q180816 ."),
    "ivy_league": ("organization", "?item wdt:P361 wd:Q49088 ."),
    "us_university": (
        "organization",
        "?item wdt:P31/wdt:P279* wd:Q3918 ; wdt:P17 wd:Q30 ; wikibase:sitelinks ?n ."
        " FILTER(?n >= 15)",
    ),
    "nfl_team": ("organization", "?item wdt:P118 wd:Q1215884 ; wdt:P31/wdt:P279* wd:Q12973014 ."),
    "nba_team": ("organization", "?item wdt:P118 wd:Q155223 ; wdt:P31/wdt:P279* wd:Q12973014 ."),
    "mlb_team": ("organization", "?item wdt:P118 wd:Q1163715 ; wdt:P31/wdt:P279* wd:Q12973014 ."),
    "nhl_team": ("organization", "?item wdt:P118 wd:Q1215892 ; wdt:P31/wdt:P279* wd:Q12973014 ."),
    "mls_team": ("organization", "?item wdt:P118 wd:Q18543 ; wdt:P31/wdt:P279* wd:Q12973014 ."),
    "us_battle": ("event", "?item wdt:P31/wdt:P279* wd:Q178561 ; wdt:P710 wd:Q30 ."),
    "us_conflict": ("event", "?item wdt:P31/wdt:P279* wd:Q198 ; wdt:P710 wd:Q30 ."),
    "us_amendment": ("other", "?item wdt:P31 wd:Q1006644 ."),
}

US_LIST_TEMPLATE = """SELECT DISTINCT ?item ?enTitle ?sl WHERE {
  %s
  ?a schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> ; schema:name ?enTitle .
  ?item wikibase:sitelinks ?sl .
}"""


def fetch_us_lists(cache: Path, *, offline: bool) -> dict[str, dict[str, Any]]:
    """Return ``enwiki title -> {qid, sitelinks, lists, entity_type}`` for the US lists."""

    def build() -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for name, (entity_type, body) in US_LIST_QUERIES.items():
            for binding in sparql(US_LIST_TEMPLATE % body):
                title = binding["enTitle"]["value"]
                record = out.setdefault(
                    title,
                    {
                        "qid": _qid(binding["item"]["value"]),
                        "sitelinks": int(binding["sl"]["value"]),
                        "lists": [],
                        "entity_type": entity_type,
                    },
                )
                if name not in record["lists"]:
                    record["lists"].append(name)
            time.sleep(0.5)
        return out

    return _cached(cache, "us_lists.json", build, offline=offline)


# --------------------------------------------------------------------------------------
# Source 5 — US pageviews (CC0). Tie-breaker only.
# --------------------------------------------------------------------------------------


def fetch_pageviews(cache: Path, *, offline: bool) -> dict[str, Any]:
    """Return ``{days, counts}``: how many sampled days each title made the US top 1,000."""

    def build() -> dict[str, Any]:
        counts: Counter[str] = Counter()
        days_ok = 0
        for day in PAGEVIEW_DAYS:
            try:
                payload = json.loads(_get(f"{PAGEVIEWS}/{day}").decode("utf-8"))
            except RuntimeError:
                continue
            for article in payload["items"][0]["articles"]:
                counts[article["article"].replace("_", " ")] += 1
            days_ok += 1
            time.sleep(2)
        return {"days": days_ok, "counts": dict(counts)}

    return _cached(cache, "us_pageviews.json", build, offline=offline)


# --------------------------------------------------------------------------------------
# Source 6 — WordNet 3.0 instance synsets (offline, WordNet License)
# --------------------------------------------------------------------------------------

WN_ROOTS: list[tuple[str, list[str]]] = [
    ("person", ["person.n.01", "causal_agent.n.01"]),
    (
        "place",
        [
            "location.n.01",
            "geological_formation.n.01",
            "body_of_water.n.01",
            "land.n.04",
            "structure.n.01",
        ],
    ),
    ("organization", ["social_group.n.01", "organization.n.01", "institution.n.01"]),
    (
        "work",
        ["creation.n.02", "communication.n.02", "product.n.02", "publication.n.01", "music.n.01"],
    ),
    ("event", ["event.n.01", "act.n.02", "time_period.n.01", "happening.n.01"]),
    ("species", ["organism.n.01", "animal.n.01", "plant.n.02"]),
]


def load_wordnet_instances(cache: Path, *, offline: bool) -> dict[str, dict[str, Any]]:
    """Return ``lemma -> {entity_type, synsets, hypernyms}`` for WordNet instance synsets."""

    def build() -> dict[str, dict[str, Any]]:
        from nltk.corpus import wordnet as wn  # noqa: PLC0415

        roots = {name: [wn.synset(s) for s in names] for name, names in WN_ROOTS}
        seen: dict[str, list[str]] = {}

        def classify(synset: Any) -> list[str]:
            if synset.name() in seen:
                return seen[synset.name()]
            ancestors = {s for path in synset.hypernym_paths() for s in path}
            hits = [n for n, targets in WN_ROOTS if any(t in ancestors for t in roots[n])]
            seen[synset.name()] = hits
            return hits

        out: dict[str, dict[str, Any]] = {}
        for synset in wn.all_synsets("n"):
            instance_of = synset.instance_hypernyms()
            if not instance_of:
                continue
            hits = [h for parent in instance_of for h in classify(parent)]
            entity_type = next((t for t in TYPE_PRIORITY if t in hits), "other")
            hypernyms = [h.name().split(".")[0].replace("_", " ") for h in instance_of]
            for lemma in synset.lemmas():
                name = lemma.name().replace("_", " ")
                record = out.setdefault(
                    name, {"entity_type": entity_type, "synsets": [], "hypernyms": []}
                )
                record["synsets"].append(synset.name())
                record["hypernyms"] = sorted({*record["hypernyms"], *hypernyms})
        return out

    return _cached(cache, "wn_instances.json", build, offline=offline)


# --------------------------------------------------------------------------------------
# Local: what the store and the v1.3 lexicon already hold
# --------------------------------------------------------------------------------------


def load_store(parquet_glob: Path) -> dict[str, dict[str, Any]]:
    """Return ``lexeme_id -> {headword, kind, retired}`` from the exported lexicon parquet."""
    import pyarrow.parquet as pq  # noqa: PLC0415

    files = sorted(parquet_glob.parent.glob(parquet_glob.name))
    if not files:
        raise SystemExit(f"no lexicon parquet under {parquet_glob}")
    out: dict[str, dict[str, Any]] = {}
    columns = ["lexeme_id", "headword", "kind", "retired"]
    for file in files:
        for batch in pq.ParquetFile(file).iter_batches(batch_size=20_000, columns=columns):
            rows = batch.to_pydict()
            for i in range(len(rows["lexeme_id"])):
                out[rows["lexeme_id"][i]] = {
                    "headword": rows["headword"][i],
                    "kind": rows["kind"][i],
                    "retired": bool(rows["retired"][i]),
                }
    return out


def load_v13(light: Path) -> set[str]:
    """Return the slug set of every v1.3 headword in ``light.parquet``."""
    import pyarrow.parquet as pq  # noqa: PLC0415

    if not light.is_file():
        return set()
    table = pq.read_table(light, columns=["word"])
    return {slugify(w) for w in table.column("word").to_pylist() if w}


# --------------------------------------------------------------------------------------
# Typing, filtering, scoring
# --------------------------------------------------------------------------------------


def entity_type_of(
    facts: dict[str, Any] | None,
    class_types: dict[str, list[str]],
    wn_type: str | None,
    vital_topic: str | None,
) -> str:
    """Return one of person/place/organization/work/event/other, or a reject marker.

    :data:`CLASS_MAP` decides where it has an opinion; the ``P279`` closure is the
    fallback for the long tail of classes; WordNet's instance hypernym is next; the vital
    topic is the last resort. ``DISAMBIG``/``LIST``/``CATEGORY``/``CONCEPT``/``species``
    come back as-is so the caller can drop them.
    """
    classes = (facts or {}).get("p31", [])
    curated = [CLASS_MAP[k] for k in classes if k in CLASS_MAP]
    for marker in ("DISAMBIG", "LIST", "CATEGORY"):
        if marker in curated:
            return marker
    for candidate in TYPE_PRIORITY:
        if candidate in curated:
            return candidate
    if curated:  # every curated opinion said "this names a category, not a thing"
        return "CONCEPT"

    hits: list[str] = []
    for klass in classes:
        hits.extend(class_types.get(klass, []))
    for marker in ("DISAMBIG", "LIST", "CATEGORY"):
        if marker in hits:
            return marker
    for candidate in TYPE_PRIORITY:
        if candidate in hits:
            return candidate
    if wn_type:
        return wn_type
    if vital_topic == "People":
        return "person"
    if vital_topic == "Geography":
        return "place"
    return "UNTYPED"


def _year(date: str) -> int | None:
    """Return the year of an ISO-ish date string, or ``None``."""
    match = re.match(r"^(-?\d{4})", date or "")
    return int(match.group(1)) if match else None


def rejection(
    name: str,
    entity_type: str,
    facts: dict[str, Any] | None,
    vital_level: int,
    store_kind: str | None,
    *,
    in_wordnet: bool,
    head_is_common_word: bool,
) -> str:
    """Return why this candidate must not enter the lexicon, or ``""`` to keep it.

    ``store_kind`` is the ``kind`` of the live store entry with this slug, if any. A
    single-word title the store already holds as a ``simplex`` is a common noun that the
    vital list happens to capitalise ("Earth", "Life", "Land", "Time"): the lexicon has
    it, and it is not a name, so it is a type error rather than a duplicate.
    """
    if store_kind in {"simplex", "compound", "function_word"} and " " not in name:
        return "common_noun_already_in_store"
    if BAD_TITLE.match(name):
        return "list_or_meta_title"
    if DISAMBIG_TITLE.search(name):
        return "disambiguation_title"
    if entity_type in {"DISAMBIG", "LIST", "CATEGORY", "CONCEPT"}:
        return "wikidata_" + entity_type.lower()
    if entity_type == "species":
        return "taxon_excluded_at_tier5"
    if entity_type == "UNTYPED":
        return "untyped"
    if len(name) > MAX_TITLE_CHARS or len(name.split()) > MAX_TITLE_WORDS:
        return "title_too_long"
    if not any(c.isalpha() for c in name):
        return "no_letters"
    if re.match(r"^\d{4}(-|–|\s)", name) and vital_level >= 5:
        return "dated_article_title"
    if not looks_like_a_name(name):
        if in_wordnet:
            return ""
        # "Apollo program", "Han dynasty", "Amazon rainforest" are names whose second
        # word is an ordinary noun; "Climate change", "Mental health", "Square root" are
        # not. What separates them is the *first* word: the store's own frequency-ranked
        # vocabulary holds "climate" and "square" as common words and does not hold
        # "Apollo" or "Han" as one.
        if " " in name and not head_is_common_word and entity_type != "other":
            return ""
        if " " not in name and entity_type in {"person", "place", "organization"}:
            return ""
        return "capitalised_concept_not_a_name"
    return ""


def score(
    *,
    vital_level: int | None,
    sitelinks: int,
    in_wordnet: bool,
    facts: dict[str, Any] | None,
    us_lists: list[str],
    pageview_days: int,
    pageview_total: int,
    name: str,
    entity_type: str,
) -> tuple[float, list[str]]:
    """Return the importance score and the reasons that produced it."""
    points = 0.0
    why: list[str] = []

    if vital_level:
        value = VITAL_POINTS[vital_level]
        points += value
        why.append(f"vital{vital_level}={value:.0f}")

    if sitelinks:
        value = SITELINK_WEIGHT * math.log10(1 + sitelinks)
        points += value
        why.append(f"sitelinks{sitelinks}={value:.1f}")

    if in_wordnet:
        points += WORDNET_POINTS
        why.append(f"wordnet={WORDNET_POINTS:.0f}")

    countries = set((facts or {}).get("country", [])) | set((facts or {}).get("citizenship", []))
    if US_QID in countries:
        points += US_POINTS
        why.append(f"us={US_POINTS:.0f}")
    elif countries & ANGLO_QIDS:
        points += ANGLO_POINTS
        why.append(f"anglo={ANGLO_POINTS:.0f}")

    if us_lists:
        narrow = [name for name in us_lists if name not in BROAD_LISTS]
        value = US_LIST_POINTS if narrow else WORLD_LIST_POINTS
        points += value
        why.append(f"uslist:{','.join(us_lists)}={value:.0f}")

    if pageview_days and pageview_total:
        value = PAGEVIEW_MAX * pageview_days / pageview_total
        points += value
        why.append(f"usviews{pageview_days}/{pageview_total}={value:.1f}")

    if entity_type == "person" and facts and not facts.get("dod"):
        born = _year(facts.get("dob", ""))
        if born is not None and born >= LIVING_BIRTH_YEAR:
            points += LIVING_PENALTY
            why.append(f"living={LIVING_PENALTY:.0f}")

    inception = _year((facts or {}).get("inception", ""))
    if inception is not None and inception >= RECENT_INCEPTION_YEAR:
        points += RECENT_PENALTY
        why.append(f"recent={RECENT_PENALTY:.0f}")

    if len(name) > 34 or len(name.split()) > 4:
        points += LONG_TITLE_PENALTY
        why.append(f"long={LONG_TITLE_PENALTY:.0f}")

    return points, why


# --------------------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------------------


def build_candidates(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Gather every source, type, filter and score the union, and return the ranked rows."""
    cache = Path(args.cache)
    offline = bool(args.offline)

    vital = fetch_vital(cache, offline=offline)
    wn_instances = load_wordnet_instances(cache, offline=offline)
    us_lists = fetch_us_lists(cache, offline=offline)
    pageviews = fetch_pageviews(cache, offline=offline)
    pv_counts: dict[str, int] = pageviews["counts"]
    pv_total: int = pageviews["days"]

    vital_props = fetch_pageprops(cache, "vital_pageprops.json", sorted(vital), offline=offline)
    wn_props = fetch_pageprops(cache, "wn_pageprops.json", sorted(wn_instances), offline=offline)

    qids = sorted(
        {r["qid"] for r in vital_props.values() if r.get("qid")}
        | {r["qid"] for r in wn_props.values() if r.get("qid")}
        | {r["qid"] for r in us_lists.values() if r.get("qid")}
    )
    facts = fetch_facts(cache, qids, offline=offline)
    classes = sorted({c for f in facts.values() if f for c in f["p31"]})
    class_types = fetch_class_types(cache, classes, offline=offline)

    store = load_store(Path(args.store_parquet))
    v13 = load_v13(Path(args.light))

    # One record per candidate *name*, keyed by the surface form we would use as headword.
    records: dict[str, dict[str, Any]] = {}

    def add(name: str, source: str, **extra: Any) -> dict[str, Any]:
        record = records.setdefault(
            name, {"name": name, "sources": [], "vital_level": None, "us_lists": []}
        )
        if source not in record["sources"]:
            record["sources"].append(source)
        record.update({k: v for k, v in extra.items() if v is not None})
        return record

    for title, meta in vital.items():
        props = vital_props.get(title, {})
        record = add(
            title,
            "wikipedia_vital",
            qid=props.get("qid"),
            vital_topic=meta["topic"],
            vital_section=meta.get("section"),
        )
        level = int(meta["level"])
        if record["vital_level"] is None or level < record["vital_level"]:
            record["vital_level"] = level

    for lemma, meta in wn_instances.items():
        props = wn_props.get(lemma, {})
        record = add(
            lemma,
            "wordnet",
            qid=props.get("qid"),
            wn_type=meta["entity_type"],
            wn_hypernyms=meta["hypernyms"],
        )
        record["in_wordnet"] = True

    for title, meta in us_lists.items():
        record = add(title, "wikidata_us", qid=meta["qid"], us_type=meta["entity_type"])
        for name in meta["lists"]:
            if name not in record["us_lists"]:
                record["us_lists"].append(name)

    rows: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    for name, record in records.items():
        fact = facts.get(record.get("qid") or "") or None
        entity_type = entity_type_of(
            fact, class_types, record.get("wn_type"), record.get("vital_topic")
        )
        if entity_type == "UNTYPED" and record.get("us_type"):
            entity_type = record["us_type"]
        level = record["vital_level"] or 0
        slug = slugify(name)
        entry = store.get(slug)
        live_kind = entry["kind"] if entry and not entry["retired"] else None
        head = store.get(slugify(name.split()[0])) if name.split() else None
        why_not = rejection(
            name,
            entity_type,
            fact,
            level or 9,
            live_kind,
            in_wordnet=bool(record.get("in_wordnet")),
            head_is_common_word=bool(
                head and not head["retired"] and head["kind"] in COMMON_WORD_KINDS
            ),
        )
        if why_not:
            rejected[why_not] += 1
            continue

        sitelinks = (fact or {}).get("sitelinks", 0)
        points, why = score(
            vital_level=record["vital_level"],
            sitelinks=sitelinks,
            in_wordnet=bool(record.get("in_wordnet")),
            facts=fact,
            us_lists=record["us_lists"],
            pageview_days=pv_counts.get(name, 0),
            pageview_total=pv_total,
            name=name,
            entity_type=entity_type,
        )

        bare = PARENTHETICAL.sub("", name).strip()
        surname = name.rsplit(" ", 1)[-1] if " " in name else ""
        surname_entry = store.get(slugify(surname)) if surname else None

        notes: list[str] = []
        if entry and not entry["retired"]:
            notes.append(f"in_store as {entry['kind']}")
        elif entry:
            notes.append("in_store but retired")
        if bare != name:
            notes.append(f"strip_parenthetical={bare!r}")
        if surname_entry and not surname_entry["retired"] and not entry:
            notes.append(f"alias_of candidate: store has '{surname_entry['headword']}'")
        if record.get("wn_hypernyms"):
            notes.append("wn_hypernym=" + "|".join(record["wn_hypernyms"][:3]))
        if record["us_lists"]:
            notes.append("us_list=" + ",".join(record["us_lists"]))

        rows.append(
            {
                "name": name,
                "word": name,
                "entity_type": entity_type,
                "source": "wordnet" if record.get("in_wordnet") else "name_seed",
                "sources": ",".join(record["sources"]),
                "importance_score": round(points, 2),
                "vital_level": record["vital_level"] or "",
                "sitelinks": sitelinks,
                "in_wordnet": int(bool(record.get("in_wordnet"))),
                "in_v13": int(slug in v13),
                "in_store": int(bool(entry and not entry["retired"])),
                "store_slug": slug,
                "qid": record.get("qid") or "",
                "score_terms": ";".join(why),
                "notes": "; ".join(notes),
            }
        )

    rows.sort(key=lambda r: (-r["importance_score"], r["name"]))
    stats = {
        "records": len(records),
        "rejected": dict(rejected.most_common()),
        "kept": len(rows),
        "pageview_days": pv_total,
        "vital_titles": len(vital),
        "wordnet_instances": len(wn_instances),
        "us_list_titles": len(us_lists),
        "store_lexemes": len(store),
    }
    return rows, stats


def apply_quotas(rows: list[dict[str, Any]], size: int) -> list[dict[str, Any]]:
    """Take the top ``size`` rows in score order, subject to :data:`TYPE_QUOTA` as a cap.

    The quota is a *ceiling*, not a target: the walk stops as soon as ``size`` rows are
    chosen, so a small type is not padded out to its share with low-scoring residue. A
    type that runs out early simply gives its slack to the next-best rows overall.
    """
    caps = {t: int(size * share) for t, share in TYPE_QUOTA.items()}
    taken: Counter[str] = Counter()
    chosen: list[dict[str, Any]] = []
    overflow: list[dict[str, Any]] = []
    for row in rows:
        if len(chosen) >= size:
            break
        kind = row["entity_type"]
        if taken[kind] < caps.get(kind, 0):
            taken[kind] += 1
            chosen.append(row)
        else:
            overflow.append(row)
    if len(chosen) < size:
        chosen.extend(overflow[: size - len(chosen)])
    chosen.sort(key=lambda r: (-r["importance_score"], r["name"]))
    return chosen[:size]


COLUMNS = [
    "name",
    "word",
    "entity_type",
    "source",
    "sources",
    "importance_score",
    "vital_level",
    "sitelinks",
    "in_wordnet",
    "in_v13",
    "in_store",
    "store_slug",
    "qid",
    "score_terms",
    "notes",
]


def write_tsv(rows: list[dict[str, Any]], path: Path) -> None:
    """Write the candidate list as a TSV with a header row."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write("\t".join(COLUMNS) + "\n")
        for row in rows:
            handle.write("\t".join(str(row.get(c, "")).replace("\t", " ") for c in COLUMNS) + "\n")


def write_top200(rows: list[dict[str, Any]], path: Path) -> None:
    """Write the docs preview: the 200 highest-scoring candidates."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Tier 6 — top 200 named-entity candidates",
        "",
        "Generated by `scripts/build_tier6_candidates.py`; the full list lives in the",
        "gitignored `data/core/tier6_candidates.tsv`. Scoring is described in",
        "[`NAMED-ENTITY-PLAN.md`](NAMED-ENTITY-PLAN.md) § 2.",
        "",
        "Sources for the ranking: the English Wikipedia **vital articles** lists",
        "(CC BY-SA 4.0), **Wikidata** sitelink counts, `P31` typing and 26 US-specific list",
        "queries (CC0), the Wikimedia **US pageviews** API (CC0), and **WordNet 3.0**",
        "instance synsets (WordNet License, `LICENSES/WordNet.txt`). Names and rankings",
        "only — no Wikipedia article text is used or reproduced.",
        "",
        "| # | name | type | score | vital | sitelinks | in WordNet | in store |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, row in enumerate(rows[:200], start=1):
        lines.append(
            f"| {i} | {row['name']} | {row['entity_type']} | {row['importance_score']:.1f} | "
            f"{row['vital_level'] or '—'} | {row['sitelinks']} | "
            f"{'yes' if row['in_wordnet'] else '—'} | {'yes' if row['in_store'] else '—'} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Build the tier-6 candidate list."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", default="data/core/tier6_cache")
    parser.add_argument("--out", default="data/core/tier6_candidates.tsv")
    parser.add_argument("--docs-out", default="docs/tier6_top200.md")
    parser.add_argument("--store-parquet", default="data/hf/opengloss-v2.2-lexicon/data/*.parquet")
    parser.add_argument("--light", default="data/core/light.parquet")
    parser.add_argument("--size", type=int, default=15000)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--stats-out", default="")
    args = parser.parse_args(argv)

    rows, stats = build_candidates(args)
    chosen = apply_quotas(rows, args.size)
    write_tsv(chosen, Path(args.out))
    write_top200(chosen, Path(args.docs_out))

    stats["selected"] = len(chosen)
    stats["by_type"] = dict(Counter(r["entity_type"] for r in chosen).most_common())
    stats["already_in_store"] = sum(r["in_store"] for r in chosen)
    stats["in_wordnet"] = sum(r["in_wordnet"] for r in chosen)
    stats["in_v13"] = sum(r["in_v13"] for r in chosen)
    if args.stats_out:
        Path(args.stats_out).write_text(json.dumps(stats, indent=2), encoding="utf-8")
    json.dump(stats, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
