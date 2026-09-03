"""Controlled domain taxonomy for sense-level tagging.

See ``docs/SCHEMA-V3.md`` § 2 for the contract this module implements: a fixed
15-root, ~150-leaf :class:`DomainTag` enum that replaces the free-text ``domain``
field from OpenGloss v1.x. That field collapsed under its own freedom — 84% of
v1.3 entries ended up tagged "general academic" because nothing constrained the
model's word choice. A controlled vocabulary, and leaves specific enough that a
K-12-through-college dictionary does not need to lean on ``.general``, is the fix.

Two things are load-bearing here:

* Every member of :data:`ROOTS` is one of the 15 fixed roots. Adding a root is a
  breaking schema change, not a taxonomy tweak — do not do it without updating
  ``ROOTS`` and the tests that pin it.
* :data:`TAXONOMY_PROMPT_BLOCK` is byte-stable across imports and calls. Per
  ``docs/DESIGN.md`` § 4.3, static content belongs in *instructions* so prompt
  caching gets a stable prefix; a block that changes on every call (dict
  iteration order, timestamps, non-deterministic sorting) would defeat that.
  It is built once, at import time, from a plain dict literal in sorted order.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

__all__ = [
    "GLOSSES",
    "IPTC_MAP",
    "LCC_MAP",
    "LEAF_COUNT",
    "LEGACY_DOMAIN_MAP",
    "ROOTS",
    "TAXONOMY_PROMPT_BLOCK",
    "TAXONOMY_VERSION",
    "DomainTag",
    "deficit_table",
    "is_general",
    "leaves_of",
    "legacy_domain",
    "root_of",
]

ROOTS: tuple[str, ...] = (
    "arts",
    "business",
    "education",
    "everyday_life",
    "health",
    "history",
    "humanities",
    "language",
    "law_government",
    "mathematics",
    "nature",
    "people_society",
    "science",
    "sports_recreation",
    "technology",
)


class DomainTag(StrEnum):
    """A controlled ``root.leaf`` domain tag for a :class:`~opengloss_generator.schema.Sense`.

    Every root in :data:`ROOTS` carries 8-14 leaves (most roots hold 8-12; two —
    ``everyday_life`` and ``people_society``, the roots with the largest ``.general``
    residue on the 10K core, D-44 — carry 14), one of which is always ``<root>.general``
    for "in this domain but no finer distinction applies".
    """

    ARTS_GENERAL = "arts.general"
    ARTS_VISUAL_ART = "arts.visual_art"
    ARTS_MUSIC = "arts.music"
    ARTS_DANCE = "arts.dance"
    ARTS_THEATER = "arts.theater"
    ARTS_FILM = "arts.film"
    ARTS_PHOTOGRAPHY = "arts.photography"
    ARTS_ARCHITECTURE = "arts.architecture"
    ARTS_DESIGN = "arts.design"
    ARTS_CRAFTS = "arts.crafts"

    BUSINESS_GENERAL = "business.general"
    BUSINESS_FINANCE = "business.finance"
    BUSINESS_MARKETING = "business.marketing"
    BUSINESS_MANAGEMENT = "business.management"
    BUSINESS_ACCOUNTING = "business.accounting"
    BUSINESS_ENTREPRENEURSHIP = "business.entrepreneurship"
    BUSINESS_TRADE_COMMERCE = "business.trade_commerce"
    BUSINESS_HUMAN_RESOURCES = "business.human_resources"
    BUSINESS_REAL_ESTATE = "business.real_estate"
    BUSINESS_MANUFACTURING = "business.manufacturing"
    BUSINESS_VALUE_QUALITY = "business.value_quality"
    BUSINESS_OCCUPATIONS_CAREERS = "business.occupations_careers"

    EDUCATION_GENERAL = "education.general"
    EDUCATION_PEDAGOGY = "education.pedagogy"
    EDUCATION_CURRICULUM = "education.curriculum"
    EDUCATION_ASSESSMENT = "education.assessment"
    EDUCATION_LITERACY = "education.literacy"
    EDUCATION_EARLY_CHILDHOOD = "education.early_childhood"
    EDUCATION_HIGHER_EDUCATION = "education.higher_education"
    EDUCATION_SPECIAL_EDUCATION = "education.special_education"
    EDUCATION_SCHOOL_LIFE = "education.school_life"

    EVERYDAY_LIFE_GENERAL = "everyday_life.general"
    EVERYDAY_LIFE_FOOD = "everyday_life.food"
    EVERYDAY_LIFE_CLOTHING = "everyday_life.clothing"
    EVERYDAY_LIFE_HOME = "everyday_life.home"
    EVERYDAY_LIFE_FAMILY = "everyday_life.family"
    EVERYDAY_LIFE_SHOPPING = "everyday_life.shopping"
    EVERYDAY_LIFE_TRAVEL = "everyday_life.travel"
    EVERYDAY_LIFE_HOBBIES = "everyday_life.hobbies"
    EVERYDAY_LIFE_PERSONAL_CARE = "everyday_life.personal_care"
    EVERYDAY_LIFE_HOUSEHOLD_TOOLS = "everyday_life.household_tools"
    EVERYDAY_LIFE_CELEBRATIONS_HOLIDAYS = "everyday_life.celebrations_holidays"
    EVERYDAY_LIFE_TRANSPORTATION = "everyday_life.transportation"
    EVERYDAY_LIFE_ACTIONS_ROUTINES = "everyday_life.actions_routines"
    EVERYDAY_LIFE_QUANTITY_TIME = "everyday_life.quantity_time"

    HEALTH_GENERAL = "health.general"
    HEALTH_ANATOMY = "health.anatomy"
    HEALTH_NUTRITION = "health.nutrition"
    HEALTH_DISEASE_ILLNESS = "health.disease_illness"
    HEALTH_MENTAL_HEALTH = "health.mental_health"
    HEALTH_MEDICINE_TREATMENT = "health.medicine_treatment"
    HEALTH_FITNESS_EXERCISE = "health.fitness_exercise"
    HEALTH_PUBLIC_HEALTH = "health.public_health"
    HEALTH_FIRST_AID = "health.first_aid"
    HEALTH_REPRODUCTION_GROWTH = "health.reproduction_growth"

    HISTORY_GENERAL = "history.general"
    HISTORY_ANCIENT_HISTORY = "history.ancient_history"
    HISTORY_MEDIEVAL_HISTORY = "history.medieval_history"
    HISTORY_MODERN_HISTORY = "history.modern_history"
    HISTORY_WORLD_WARS = "history.world_wars"
    HISTORY_REVOLUTIONS = "history.revolutions"
    HISTORY_EXPLORATION_COLONIZATION = "history.exploration_colonization"
    HISTORY_HISTORICAL_FIGURES = "history.historical_figures"
    HISTORY_ARCHAEOLOGY = "history.archaeology"

    HUMANITIES_GENERAL = "humanities.general"
    HUMANITIES_PHILOSOPHY = "humanities.philosophy"
    HUMANITIES_RELIGION = "humanities.religion"
    HUMANITIES_MYTHOLOGY = "humanities.mythology"
    HUMANITIES_ETHICS = "humanities.ethics"
    HUMANITIES_LITERATURE = "humanities.literature"
    HUMANITIES_FOLKLORE = "humanities.folklore"
    HUMANITIES_CLASSICAL_STUDIES = "humanities.classical_studies"
    HUMANITIES_CULTURAL_STUDIES = "humanities.cultural_studies"

    LANGUAGE_GENERAL = "language.general"
    LANGUAGE_GRAMMAR = "language.grammar"
    LANGUAGE_VOCABULARY = "language.vocabulary"
    LANGUAGE_RHETORIC = "language.rhetoric"
    LANGUAGE_WRITING = "language.writing"
    LANGUAGE_READING_COMPREHENSION = "language.reading_comprehension"
    LANGUAGE_PHONETICS_PRONUNCIATION = "language.phonetics_pronunciation"
    LANGUAGE_TRANSLATION = "language.translation"
    LANGUAGE_LINGUISTICS = "language.linguistics"
    LANGUAGE_ETYMOLOGY = "language.etymology"
    LANGUAGE_COMMUNICATION = "language.communication"

    LAW_GOVERNMENT_GENERAL = "law_government.general"
    LAW_GOVERNMENT_CRIMINAL_LAW = "law_government.criminal_law"
    LAW_GOVERNMENT_CIVIL_LAW = "law_government.civil_law"
    LAW_GOVERNMENT_CONSTITUTIONAL_LAW = "law_government.constitutional_law"
    LAW_GOVERNMENT_GOVERNMENT_STRUCTURE = "law_government.government_structure"
    LAW_GOVERNMENT_ELECTIONS_POLITICS = "law_government.elections_politics"
    LAW_GOVERNMENT_INTERNATIONAL_LAW = "law_government.international_law"
    LAW_GOVERNMENT_CIVICS = "law_government.civics"
    LAW_GOVERNMENT_COURTS_JUSTICE = "law_government.courts_justice"
    LAW_GOVERNMENT_PUBLIC_POLICY = "law_government.public_policy"

    MATHEMATICS_GENERAL = "mathematics.general"
    MATHEMATICS_ARITHMETIC = "mathematics.arithmetic"
    MATHEMATICS_ALGEBRA = "mathematics.algebra"
    MATHEMATICS_GEOMETRY = "mathematics.geometry"
    MATHEMATICS_TRIGONOMETRY = "mathematics.trigonometry"
    MATHEMATICS_CALCULUS = "mathematics.calculus"
    MATHEMATICS_STATISTICS_PROBABILITY = "mathematics.statistics_probability"
    MATHEMATICS_NUMBER_THEORY = "mathematics.number_theory"
    MATHEMATICS_LOGIC = "mathematics.logic"
    MATHEMATICS_APPLIED_MATH = "mathematics.applied_math"

    NATURE_GENERAL = "nature.general"
    NATURE_ANIMALS = "nature.animals"
    NATURE_PLANTS = "nature.plants"
    NATURE_WEATHER = "nature.weather"
    NATURE_LANDFORMS = "nature.landforms"
    NATURE_ECOSYSTEMS = "nature.ecosystems"
    NATURE_WATER_BODIES = "nature.water_bodies"
    NATURE_CLIMATE = "nature.climate"
    NATURE_CONSERVATION = "nature.conservation"
    NATURE_NATURAL_DISASTERS = "nature.natural_disasters"
    NATURE_MINERALS_ROCKS = "nature.minerals_rocks"

    PEOPLE_SOCIETY_GENERAL = "people_society.general"
    PEOPLE_SOCIETY_FAMILY_RELATIONSHIPS = "people_society.family_relationships"
    PEOPLE_SOCIETY_SOCIAL_ISSUES = "people_society.social_issues"
    PEOPLE_SOCIETY_CULTURE_CUSTOMS = "people_society.culture_customs"
    PEOPLE_SOCIETY_DEMOGRAPHICS = "people_society.demographics"
    PEOPLE_SOCIETY_COMMUNITY_LIFE = "people_society.community_life"
    PEOPLE_SOCIETY_GENDER_IDENTITY = "people_society.gender_identity"
    PEOPLE_SOCIETY_ETHNICITY_RACE = "people_society.ethnicity_race"
    PEOPLE_SOCIETY_SOCIAL_CLASS = "people_society.social_class"
    PEOPLE_SOCIETY_MIGRATION = "people_society.migration"
    PEOPLE_SOCIETY_PERSONAL_NAMES = "people_society.personal_names"
    PEOPLE_SOCIETY_CHARACTER_TRAITS = "people_society.character_traits"
    PEOPLE_SOCIETY_SOCIAL_ROLES = "people_society.social_roles"
    PEOPLE_SOCIETY_EMOTION_ATTITUDE = "people_society.emotion_attitude"

    SCIENCE_GENERAL = "science.general"
    SCIENCE_PHYSICS = "science.physics"
    SCIENCE_CHEMISTRY = "science.chemistry"
    SCIENCE_BIOLOGY = "science.biology"
    SCIENCE_EARTH_SCIENCE = "science.earth_science"
    SCIENCE_ASTRONOMY = "science.astronomy"
    SCIENCE_GENETICS = "science.genetics"
    SCIENCE_SCIENTIFIC_METHOD = "science.scientific_method"
    SCIENCE_MATERIALS_SCIENCE = "science.materials_science"
    SCIENCE_ENVIRONMENTAL_SCIENCE = "science.environmental_science"

    SPORTS_RECREATION_GENERAL = "sports_recreation.general"
    SPORTS_RECREATION_TEAM_SPORTS = "sports_recreation.team_sports"
    SPORTS_RECREATION_INDIVIDUAL_SPORTS = "sports_recreation.individual_sports"
    SPORTS_RECREATION_WATER_SPORTS = "sports_recreation.water_sports"
    SPORTS_RECREATION_WINTER_SPORTS = "sports_recreation.winter_sports"
    SPORTS_RECREATION_COMBAT_SPORTS = "sports_recreation.combat_sports"
    SPORTS_RECREATION_TRACK_FIELD = "sports_recreation.track_field"
    SPORTS_RECREATION_GAMES_PUZZLES = "sports_recreation.games_puzzles"
    SPORTS_RECREATION_OUTDOOR_RECREATION = "sports_recreation.outdoor_recreation"
    SPORTS_RECREATION_FITNESS_TRAINING = "sports_recreation.fitness_training"

    TECHNOLOGY_GENERAL = "technology.general"
    TECHNOLOGY_COMPUTING_SOFTWARE = "technology.computing_software"
    TECHNOLOGY_HARDWARE_DEVICES = "technology.hardware_devices"
    TECHNOLOGY_INTERNET_COMMUNICATION = "technology.internet_communication"
    TECHNOLOGY_ENGINEERING = "technology.engineering"
    TECHNOLOGY_ROBOTICS_AI = "technology.robotics_ai"
    TECHNOLOGY_TRANSPORTATION_TECHNOLOGY = "technology.transportation_technology"
    TECHNOLOGY_ENERGY_TECHNOLOGY = "technology.energy_technology"
    TECHNOLOGY_BIOTECHNOLOGY = "technology.biotechnology"
    TECHNOLOGY_INFORMATION_SECURITY = "technology.information_security"
    TECHNOLOGY_DEVICE_OPERATION = "technology.device_operation"


#: Bumped whenever a leaf is added to :class:`DomainTag` (never on rename or removal —
#: stored ``root.leaf`` values are permanent, D-1's discipline applied to the taxonomy
#: itself). ``workflows/retrofit.py``'s ``hygiene`` step (d) records this value in the
#: ``tag_domain`` provenance ``note`` of every sense it tags, so a later hygiene sweep can
#: tell a stale ``.general`` verdict (tagged under an older, thinner taxonomy) from a
#: current one (tagged with these leaves already on the menu) and only clear the former.
#: See D-44.
TAXONOMY_VERSION: str = "2"


LEAF_COUNT: int = len(DomainTag)


def root_of(tag: DomainTag) -> str:
    """Return the root component of a domain tag, e.g. ``"science"`` for ``science.physics``."""
    return tag.value.split(".", 1)[0]


def leaves_of(root: str) -> tuple[DomainTag, ...]:
    """Return every :class:`DomainTag` under ``root``, in enum definition order.

    Args:
        root: One of :data:`ROOTS`. An unknown root yields an empty tuple.

    Returns:
        The tags whose root component equals ``root``.
    """
    return tuple(tag for tag in DomainTag if root_of(tag) == root)


def is_general(tag: DomainTag) -> bool:
    """Return whether ``tag`` is its root's catch-all ``.general`` leaf."""
    return tag.value.endswith(".general")


# Five-word (approximate) glosses for every leaf, keyed by tag. This is the single
# source of truth for TAXONOMY_PROMPT_BLOCK: change a gloss here and the block
# picks it up, in the same deterministic sorted layout, on next import.
GLOSSES: dict[DomainTag, str] = {
    DomainTag.ARTS_GENERAL: "unclassified terms about art forms",
    DomainTag.ARTS_VISUAL_ART: "painting drawing sculpture and imagery",
    DomainTag.ARTS_MUSIC: "musical instruments genres and performance",
    DomainTag.ARTS_DANCE: "dance styles movement and choreography",
    DomainTag.ARTS_THEATER: "stage plays acting and drama",
    DomainTag.ARTS_FILM: "movies filmmaking and cinema terms",
    DomainTag.ARTS_PHOTOGRAPHY: "cameras photographs and image capture",
    DomainTag.ARTS_ARCHITECTURE: "buildings structures and architectural design",
    DomainTag.ARTS_DESIGN: "graphic product and visual design",
    DomainTag.ARTS_CRAFTS: "handmade crafts sewing and woodworking",
    DomainTag.BUSINESS_GENERAL: "unclassified general business and commerce",
    DomainTag.BUSINESS_FINANCE: "money banking investing and finance",
    DomainTag.BUSINESS_MARKETING: "advertising branding and promoting products",
    DomainTag.BUSINESS_MANAGEMENT: "leading organizing and managing workers",
    DomainTag.BUSINESS_ACCOUNTING: "bookkeeping audits and financial records",
    DomainTag.BUSINESS_ENTREPRENEURSHIP: "startups founders and new ventures",
    DomainTag.BUSINESS_TRADE_COMMERCE: "buying selling and commercial exchange",
    DomainTag.BUSINESS_HUMAN_RESOURCES: "hiring staffing and workplace personnel",
    DomainTag.BUSINESS_REAL_ESTATE: "property buying renting and land",
    DomainTag.BUSINESS_MANUFACTURING: "factories production and industrial processes",
    DomainTag.BUSINESS_VALUE_QUALITY: "pricing value quality and worth",
    DomainTag.BUSINESS_OCCUPATIONS_CAREERS: "jobs careers professions and occupations",
    DomainTag.EDUCATION_GENERAL: "unclassified general academic and school",
    DomainTag.EDUCATION_PEDAGOGY: "teaching methods and instructional practice",
    DomainTag.EDUCATION_CURRICULUM: "courses syllabi and academic subjects",
    DomainTag.EDUCATION_ASSESSMENT: "tests grading and student evaluation",
    DomainTag.EDUCATION_LITERACY: "reading writing and basic literacy",
    DomainTag.EDUCATION_EARLY_CHILDHOOD: "preschool kindergarten and young learners",
    DomainTag.EDUCATION_HIGHER_EDUCATION: "college university and degree programs",
    DomainTag.EDUCATION_SPECIAL_EDUCATION: "learning differences and needed accommodations",
    DomainTag.EDUCATION_SCHOOL_LIFE: "classrooms recess and student routines",
    DomainTag.EVERYDAY_LIFE_GENERAL: "unclassified everyday household and routine",
    DomainTag.EVERYDAY_LIFE_FOOD: "meals cooking ingredients and eating",
    DomainTag.EVERYDAY_LIFE_CLOTHING: "garments fashion and dressing terms",
    DomainTag.EVERYDAY_LIFE_HOME: "houses rooms furniture and dwelling",
    DomainTag.EVERYDAY_LIFE_FAMILY: "parents siblings relatives and households",
    DomainTag.EVERYDAY_LIFE_SHOPPING: "stores purchases prices and errands",
    DomainTag.EVERYDAY_LIFE_TRAVEL: "trips vacations and getting around",
    DomainTag.EVERYDAY_LIFE_HOBBIES: "leisure pastimes and personal interests",
    DomainTag.EVERYDAY_LIFE_PERSONAL_CARE: "hygiene grooming and self care",
    DomainTag.EVERYDAY_LIFE_HOUSEHOLD_TOOLS: "tools appliances and everyday gadgets",
    DomainTag.EVERYDAY_LIFE_CELEBRATIONS_HOLIDAYS: "holidays parties and festive occasions",
    DomainTag.EVERYDAY_LIFE_TRANSPORTATION: "cars trains commuting and vehicles",
    DomainTag.EVERYDAY_LIFE_ACTIONS_ROUTINES: "everyday actions habits and routines",
    DomainTag.EVERYDAY_LIFE_QUANTITY_TIME: "amounts sizes dates and duration",
    DomainTag.HEALTH_GENERAL: "unclassified general health and wellness",
    DomainTag.HEALTH_ANATOMY: "body parts organs and structure",
    DomainTag.HEALTH_NUTRITION: "diet vitamins and healthy eating",
    DomainTag.HEALTH_DISEASE_ILLNESS: "sicknesses symptoms and medical conditions",
    DomainTag.HEALTH_MENTAL_HEALTH: "emotions stress and psychological wellbeing",
    DomainTag.HEALTH_MEDICINE_TREATMENT: "doctors medicines and medical treatment",
    DomainTag.HEALTH_FITNESS_EXERCISE: "workouts exercise and physical training",
    DomainTag.HEALTH_PUBLIC_HEALTH: "disease prevention and community health",
    DomainTag.HEALTH_FIRST_AID: "emergency care and injury response",
    DomainTag.HEALTH_REPRODUCTION_GROWTH: "pregnancy birth and human development",
    DomainTag.HISTORY_GENERAL: "unclassified general historical events terms",
    DomainTag.HISTORY_ANCIENT_HISTORY: "ancient civilizations empires and antiquity",
    DomainTag.HISTORY_MEDIEVAL_HISTORY: "medieval kingdoms knights and feudalism",
    DomainTag.HISTORY_MODERN_HISTORY: "modern era nations and events",
    DomainTag.HISTORY_WORLD_WARS: "world war battles and conflicts",
    DomainTag.HISTORY_REVOLUTIONS: "uprisings revolts and political revolutions",
    DomainTag.HISTORY_EXPLORATION_COLONIZATION: "explorers voyages and colonial settlement",
    DomainTag.HISTORY_HISTORICAL_FIGURES: "notable leaders rulers and figures",
    DomainTag.HISTORY_ARCHAEOLOGY: "artifacts excavation and ancient remains",
    DomainTag.HUMANITIES_GENERAL: "unclassified general humanities and culture",
    DomainTag.HUMANITIES_PHILOSOPHY: "logic ethics and philosophical thought",
    DomainTag.HUMANITIES_RELIGION: "faiths worship and religious practice",
    DomainTag.HUMANITIES_MYTHOLOGY: "myths gods and legendary stories",
    DomainTag.HUMANITIES_ETHICS: "morality right and wrong conduct",
    DomainTag.HUMANITIES_LITERATURE: "novels poetry and literary works",
    DomainTag.HUMANITIES_FOLKLORE: "folktales legends and oral tradition",
    DomainTag.HUMANITIES_CLASSICAL_STUDIES: "greek roman classics and antiquity",
    DomainTag.HUMANITIES_CULTURAL_STUDIES: "traditions customs and cultural identity",
    DomainTag.LANGUAGE_GENERAL: "unclassified general language and linguistics",
    DomainTag.LANGUAGE_GRAMMAR: "syntax parts of speech rules",
    DomainTag.LANGUAGE_VOCABULARY: "word meanings usage and definitions",
    DomainTag.LANGUAGE_RHETORIC: "persuasion argument and rhetorical devices",
    DomainTag.LANGUAGE_WRITING: "composition essays and written expression",
    DomainTag.LANGUAGE_READING_COMPREHENSION: "understanding texts and reading skills",
    DomainTag.LANGUAGE_PHONETICS_PRONUNCIATION: "sounds accents and spoken pronunciation",
    DomainTag.LANGUAGE_TRANSLATION: "interpreting and translating between languages",
    DomainTag.LANGUAGE_LINGUISTICS: "language structure history and study",
    DomainTag.LANGUAGE_ETYMOLOGY: "word origins and historical derivation",
    DomainTag.LANGUAGE_COMMUNICATION: "conversation discussion and spoken interaction",
    DomainTag.LAW_GOVERNMENT_GENERAL: "unclassified general law and government",
    DomainTag.LAW_GOVERNMENT_CRIMINAL_LAW: "crimes trials and criminal justice",
    DomainTag.LAW_GOVERNMENT_CIVIL_LAW: "contracts torts and civil disputes",
    DomainTag.LAW_GOVERNMENT_CONSTITUTIONAL_LAW: "constitutions rights and governmental powers",
    DomainTag.LAW_GOVERNMENT_GOVERNMENT_STRUCTURE: "branches agencies and government institutions",
    DomainTag.LAW_GOVERNMENT_ELECTIONS_POLITICS: "voting campaigns and political parties",
    DomainTag.LAW_GOVERNMENT_INTERNATIONAL_LAW: "treaties diplomacy and global law",
    DomainTag.LAW_GOVERNMENT_CIVICS: "citizenship duties and civic participation",
    DomainTag.LAW_GOVERNMENT_COURTS_JUSTICE: "judges juries and legal proceedings",
    DomainTag.LAW_GOVERNMENT_PUBLIC_POLICY: "regulations legislation and public programs",
    DomainTag.MATHEMATICS_GENERAL: "unclassified general mathematics and numbers",
    DomainTag.MATHEMATICS_ARITHMETIC: "addition subtraction multiplication and division",
    DomainTag.MATHEMATICS_ALGEBRA: "equations variables and algebraic expressions",
    DomainTag.MATHEMATICS_GEOMETRY: "shapes angles and spatial relationships",
    DomainTag.MATHEMATICS_TRIGONOMETRY: "triangles angles and trigonometric functions",
    DomainTag.MATHEMATICS_CALCULUS: "derivatives integrals limits and change",
    DomainTag.MATHEMATICS_STATISTICS_PROBABILITY: "data averages chance and probability",
    DomainTag.MATHEMATICS_NUMBER_THEORY: "primes factors and integer properties",
    DomainTag.MATHEMATICS_LOGIC: "proofs reasoning and logical statements",
    DomainTag.MATHEMATICS_APPLIED_MATH: "modeling optimization and practical mathematics",
    DomainTag.NATURE_GENERAL: "unclassified general nature and outdoors",
    DomainTag.NATURE_ANIMALS: "wildlife creatures and animal behavior",
    DomainTag.NATURE_PLANTS: "trees flowers and plant life",
    DomainTag.NATURE_WEATHER: "rain wind storms and forecasts",
    DomainTag.NATURE_LANDFORMS: "mountains valleys and land features",
    DomainTag.NATURE_ECOSYSTEMS: "habitats food webs and biomes",
    DomainTag.NATURE_WATER_BODIES: "rivers oceans lakes and streams",
    DomainTag.NATURE_CLIMATE: "climate patterns seasons and temperature",
    DomainTag.NATURE_CONSERVATION: "wildlife protection and environmental preservation",
    DomainTag.NATURE_NATURAL_DISASTERS: "earthquakes floods and natural hazards",
    DomainTag.NATURE_MINERALS_ROCKS: "rocks minerals gems and crystals",
    DomainTag.PEOPLE_SOCIETY_GENERAL: "unclassified general social and society",
    DomainTag.PEOPLE_SOCIETY_FAMILY_RELATIONSHIPS: "parents friends and personal relationships",
    DomainTag.PEOPLE_SOCIETY_SOCIAL_ISSUES: "poverty inequality and social problems",
    DomainTag.PEOPLE_SOCIETY_CULTURE_CUSTOMS: "traditions rituals and cultural practices",
    DomainTag.PEOPLE_SOCIETY_DEMOGRAPHICS: "population age and social statistics",
    DomainTag.PEOPLE_SOCIETY_COMMUNITY_LIFE: "neighborhoods clubs and civic community",
    DomainTag.PEOPLE_SOCIETY_GENDER_IDENTITY: "gender roles identity and expression",
    DomainTag.PEOPLE_SOCIETY_ETHNICITY_RACE: "ethnic groups race and heritage",
    DomainTag.PEOPLE_SOCIETY_SOCIAL_CLASS: "wealth status and social stratification",
    DomainTag.PEOPLE_SOCIETY_MIGRATION: "immigration emigration and human movement",
    DomainTag.PEOPLE_SOCIETY_PERSONAL_NAMES: "given names surnames and nicknames",
    DomainTag.PEOPLE_SOCIETY_CHARACTER_TRAITS: "personality traits character and temperament",
    DomainTag.PEOPLE_SOCIETY_SOCIAL_ROLES: "titles ranks and social positions",
    DomainTag.PEOPLE_SOCIETY_EMOTION_ATTITUDE: "emotions attitudes and personal feelings",
    DomainTag.SCIENCE_GENERAL: "unclassified general science and inquiry",
    DomainTag.SCIENCE_PHYSICS: "motion energy forces and matter",
    DomainTag.SCIENCE_CHEMISTRY: "elements compounds reactions and molecules",
    DomainTag.SCIENCE_BIOLOGY: "cells organisms genetics and life",
    DomainTag.SCIENCE_EARTH_SCIENCE: "rocks plate tectonics and geology",
    DomainTag.SCIENCE_ASTRONOMY: "stars planets galaxies and space",
    DomainTag.SCIENCE_GENETICS: "genes heredity and dna inheritance",
    DomainTag.SCIENCE_SCIENTIFIC_METHOD: "hypotheses experiments and scientific reasoning",
    DomainTag.SCIENCE_MATERIALS_SCIENCE: "metals polymers and material properties",
    DomainTag.SCIENCE_ENVIRONMENTAL_SCIENCE: "pollution sustainability and ecosystem science",
    DomainTag.SPORTS_RECREATION_GENERAL: "unclassified general sports and recreation",
    DomainTag.SPORTS_RECREATION_TEAM_SPORTS: "soccer basketball football and teams",
    DomainTag.SPORTS_RECREATION_INDIVIDUAL_SPORTS: "tennis golf and solo competition",
    DomainTag.SPORTS_RECREATION_WATER_SPORTS: "swimming surfing and water activities",
    DomainTag.SPORTS_RECREATION_WINTER_SPORTS: "skiing hockey and winter activities",
    DomainTag.SPORTS_RECREATION_COMBAT_SPORTS: "boxing wrestling and martial arts",
    DomainTag.SPORTS_RECREATION_TRACK_FIELD: "running jumping and track events",
    DomainTag.SPORTS_RECREATION_GAMES_PUZZLES: "board games card games puzzles",
    DomainTag.SPORTS_RECREATION_OUTDOOR_RECREATION: "camping hiking and outdoor adventure",
    DomainTag.SPORTS_RECREATION_FITNESS_TRAINING: "training drills and athletic conditioning",
    DomainTag.TECHNOLOGY_GENERAL: "unclassified general technology and devices",
    DomainTag.TECHNOLOGY_COMPUTING_SOFTWARE: "computers programming and software applications",
    DomainTag.TECHNOLOGY_HARDWARE_DEVICES: "gadgets circuits and electronic devices",
    DomainTag.TECHNOLOGY_INTERNET_COMMUNICATION: "internet networks and online communication",
    DomainTag.TECHNOLOGY_ENGINEERING: "machines mechanisms and engineering design",
    DomainTag.TECHNOLOGY_ROBOTICS_AI: "robots automation and artificial intelligence",
    DomainTag.TECHNOLOGY_TRANSPORTATION_TECHNOLOGY: "vehicles engines and transportation systems",
    DomainTag.TECHNOLOGY_ENERGY_TECHNOLOGY: "power generation and energy systems",
    DomainTag.TECHNOLOGY_BIOTECHNOLOGY: "genetic engineering and biotech applications",
    DomainTag.TECHNOLOGY_INFORMATION_SECURITY: "cybersecurity encryption and data protection",
    DomainTag.TECHNOLOGY_DEVICE_OPERATION: "device settings modes and operation",
}


def _build_prompt_block() -> str:
    """Build the deterministic, root-grouped listing behind :data:`TAXONOMY_PROMPT_BLOCK`.

    Sorting is purely by tag value (str comparison, since ``DomainTag`` is a
    ``StrEnum``) within each root, and roots are walked in :data:`ROOTS` order
    (itself alphabetical). Both are fixed at module load, so the result is the
    same string every call and every import — required for it to sit in a
    prompt-cached instructions block per ``docs/DESIGN.md`` § 4.3.
    """
    lines: list[str] = []
    for root in ROOTS:
        lines.append(f"# {root}")
        for tag in sorted(leaves_of(root)):
            lines.append(f"{tag.value} — {GLOSSES[tag]}")
    return "\n".join(lines)


TAXONOMY_PROMPT_BLOCK: str = _build_prompt_block()


LEGACY_DOMAIN_MAP: dict[str, DomainTag] = {
    "general academic": DomainTag.EDUCATION_GENERAL,
    "history": DomainTag.HISTORY_GENERAL,
    "geography": DomainTag.NATURE_LANDFORMS,
    "art": DomainTag.ARTS_VISUAL_ART,
    "civics": DomainTag.LAW_GOVERNMENT_CIVICS,
    "biology": DomainTag.SCIENCE_BIOLOGY,
    "physics": DomainTag.SCIENCE_PHYSICS,
    "chemistry": DomainTag.SCIENCE_CHEMISTRY,
    "mathematics": DomainTag.MATHEMATICS_GENERAL,
    "literature": DomainTag.HUMANITIES_LITERATURE,
    "music": DomainTag.ARTS_MUSIC,
    "technology": DomainTag.TECHNOLOGY_GENERAL,
    "medicine": DomainTag.HEALTH_MEDICINE_TREATMENT,
    "law": DomainTag.LAW_GOVERNMENT_GENERAL,
    "economics": DomainTag.BUSINESS_GENERAL,
    "sports": DomainTag.SPORTS_RECREATION_GENERAL,
    "religion": DomainTag.HUMANITIES_RELIGION,
    "philosophy": DomainTag.HUMANITIES_PHILOSOPHY,
    "psychology": DomainTag.PEOPLE_SOCIETY_GENERAL,
    "computing": DomainTag.TECHNOLOGY_COMPUTING_SOFTWARE,
}


#: Root -> Library of Congress Classification main-class letter(s), for export alongside
#: the stored :class:`DomainTag` leaf (docs/STANDARDS-PLAN.md § 4, C1; letters verified
#: against STANDARDS.md § 5a/5c). LCC is a *shelving* taxonomy, so several roots are
#: legitimately one-to-many (flagged below) and none is replaced or narrowed by this —
#: it is an export-time crosswalk only (STANDARDS.md § 5d).
LCC_MAP: dict[str, tuple[str, ...]] = {
    "arts": ("M", "N"),  # one-to-many: Music and Fine Arts are separate LCC classes
    "business": ("H",),  # economics/finance is the HB-HJ subclass, not a top-level letter
    "education": ("L",),
    "everyday_life": ("G", "S", "T"),  # one-to-many: no LCC top-level home; fragments
    "health": ("R",),
    "history": ("C", "D", "E", "F"),  # one-to-many: spans 3-4 LCC classes
    "humanities": ("B", "P"),  # one-to-many: Philosophy/Religion and Language/Literature
    "language": ("P",),
    "law_government": ("J", "K"),  # one-to-many: Political Science and Law
    "mathematics": ("Q",),  # QA subclass; no dedicated top-level letter
    "nature": ("Q", "G"),  # one-to-many: physical sciences (QC-QR) plus Geography
    "people_society": ("H",),  # least-bad fit: an academic-discipline letter, not a life-topic one
    "science": ("Q",),
    "sports_recreation": ("G",),  # GV subclass; no dedicated top-level letter
    "technology": ("T",),
}

#: Root -> IPTC Media Topic top-level ``medtop:`` qcode(s) (docs/STANDARDS-PLAN.md § 4, C1;
#: codes verified against STANDARDS.md § 5b/5c). IPTC is a *news-event* taxonomy, so
#: ``history`` has no direct counterpart at all — its tuple is deliberately empty, a
#: documented gap rather than a forced fit (STANDARDS.md § 5c/5d).
IPTC_MAP: dict[str, tuple[str, ...]] = {
    "arts": ("medtop:01000000",),  # arts, culture, entertainment and media
    "business": ("medtop:04000000",),  # economy, business and finance
    "education": ("medtop:05000000",),
    "everyday_life": ("medtop:10000000", "medtop:08000000"),  # lifestyle/leisure; partial interest
    "health": ("medtop:07000000",),
    "history": (),  # no IPTC top-level topic corresponds to history-as-a-subject
    "humanities": ("medtop:12000000", "medtop:01000000"),  # religion; literature nests under arts
    "language": ("medtop:01000000",),  # nested narrower term only, several levels deep
    "law_government": ("medtop:02000000", "medtop:11000000"),  # crime/law/justice; politics
    "mathematics": ("medtop:13000000",),  # science and technology
    "nature": ("medtop:06000000", "medtop:17000000"),  # environment; partial weather
    "people_society": ("medtop:14000000",),
    "science": ("medtop:13000000",),
    "sports_recreation": ("medtop:15000000",),
    "technology": ("medtop:13000000",),  # merged with science on the IPTC side
}


def legacy_domain(text: str) -> DomainTag | None:
    """Look up a v1.3 free-text ``domain`` string in :data:`LEGACY_DOMAIN_MAP`.

    The lookup is case-insensitive and collapses internal whitespace, so
    ``" General  Academic "`` and ``"general academic"`` both resolve.

    Args:
        text: The legacy free-text domain string.

    Returns:
        The mapped :class:`DomainTag`, or ``None`` if ``text`` is not one of the
        known legacy strings (it should fall back to ``domain_hint`` per
        ``docs/SCHEMA-V3.md`` § 4).
    """
    normalized = " ".join(text.split()).lower()
    return LEGACY_DOMAIN_MAP.get(normalized)


def deficit_table(
    counts: Mapping[DomainTag, int],
    target_share: Mapping[str, float] | None = None,
) -> dict[str, float]:
    """Return each root's target share minus its actual share of ``counts``.

    This is the input to the `walk` workflow's ``domain-deficit`` strategy
    (``docs/SCHEMA-V3.md`` § 6): roots with the largest positive value are the
    furthest below where they should be, and are the best seeds to sample next.

    Args:
        counts: Per-tag counts, e.g. a ``collections.Counter[DomainTag]`` built
            from an existing store. Tags are rolled up to their root via
            :func:`root_of`; leaves are not distinguished.
        target_share: Desired share of the total per root, keyed by root name.
            Defaults to a uniform ``1 / len(ROOTS)`` for every root. Missing
            roots default to a target share of ``0.0``.

    Returns:
        A mapping from root name to ``target_share - actual_share``. Values sum
        to (approximately) zero when ``target_share`` itself sums to one and
        ``counts`` is non-empty.
    """
    if target_share is None:
        uniform = 1.0 / len(ROOTS)
        target_share = dict.fromkeys(ROOTS, uniform)
    root_counts: dict[str, int] = dict.fromkeys(ROOTS, 0)
    for tag, count in counts.items():
        root_counts[root_of(tag)] += count
    total = sum(root_counts.values())
    return {
        root: target_share.get(root, 0.0) - (root_counts[root] / total if total else 0.0)
        for root in ROOTS
    }
