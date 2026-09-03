"""Attribution, lexical diversity, and anchoring metrics for the writer-diversity pilot (D-63).

Reads the five pilot store copies directly (never ``data/core-store``) and attributes
every rendition/example to the model that actually wrote it via its own
``provenance_id`` -> ``entry.provenance[pid].model`` link, rather than trusting the
arm's name — the canonical ``(neutral, plain)`` items in every non-``luna`` arm's store
are still luna's, untouched by the pilot, and must be labelled as such.

Three reports, each keyed by writer model id:

* **Attribution**: TF-IDF (word 1-2 grams) + logistic regression, balanced per writer
  by undersampling to the smallest class, 5-fold stratified cross-validation, plus the
  top positive coefficients per writer as a style-tell list.
* **Lexical diversity**: type-token ratio and distinct-4-gram rate per writer, and
  pooled-mix-vs-luna-alone for the same two statistics; sentence-opener distribution
  (first 3 words, normalised) entropy per writer.
* **Anchoring**: headword-initial rate, near-copy-vs-canonical-gloss rate (gloss
  renditions only), and the share of example renditions whose text contains some
  inflected form of the headword, per writer.

Needs scikit-learn only for the attribution model; run it as an ephemeral dependency
rather than adding it to the package:

    uv run --with scikit-learn python scripts/writer_diversity_report.py \
        --out /tmp/writer_report.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path

from opengloss_generator.config import StoreConfig
from opengloss_generator.hygiene import content_words, is_headword_initial, is_near_copy
from opengloss_generator.schema import CANONICAL_KEY, Example
from opengloss_generator.store import LexemeStore

_ARMS = ("luna", "qwen", "haiku", "gemini", "deepseek")
_STORE_ROOT = Path("data")
_WORD_RE = re.compile(r"[A-Za-z']+")
_OPENING_WORDS = 3
_MIN_WRITERS = 2
_MIN_ITEMS_PER_WRITER = 10


def _tokens(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text)]


def _ngrams(tokens: list[str], n: int) -> set[tuple[str, ...]]:
    if len(tokens) < n:
        return set()
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def _opening(text: str) -> str:
    return " ".join(_tokens(text)[:_OPENING_WORDS])


def _iter_attributed_items(store: LexemeStore) -> list[dict[str, object]]:
    """Return one record per rendition/example item this store can attribute a model to."""
    items: list[dict[str, object]] = []
    for entry in store.iter_entries():
        for pos_entry in entry.pos_entries:
            for sense in pos_entry.senses:
                gloss_canon = sense.canonical_gloss()
                for rendition in sense.gloss:
                    pid = rendition.provenance_id
                    if pid is None or pid not in entry.provenance:
                        continue
                    model = entry.provenance[pid].model
                    text = str(rendition.content)
                    items.append(
                        {
                            "model": model,
                            "text": text,
                            "field": "gloss",
                            "headword": entry.headword,
                            "is_canonical": rendition.key == CANONICAL_KEY,
                            "canonical_gloss": gloss_canon,
                            "qa_flags": [
                                f.value
                                for f in (
                                    rendition.assessment.qa_flags if rendition.assessment else []
                                )
                            ],
                        }
                    )
                for rendition in sense.examples:
                    pid = rendition.provenance_id
                    if pid is None or pid not in entry.provenance:
                        continue
                    model = entry.provenance[pid].model
                    content = rendition.content
                    text = content.text if isinstance(content, Example) else str(content)
                    items.append(
                        {
                            "model": model,
                            "text": text,
                            "field": "example",
                            "headword": entry.headword,
                            "is_canonical": rendition.key == CANONICAL_KEY,
                            "canonical_gloss": gloss_canon,
                            "qa_flags": [
                                f.value
                                for f in (
                                    rendition.assessment.qa_flags if rendition.assessment else []
                                )
                            ],
                        }
                    )
    return items


def _collect_all_items() -> list[dict[str, object]]:
    """Scan every arm's store and dedupe items by (model, text) across the copies.

    The canonical items are byte-identical across every arm's copy (only the
    non-canonical items differ), so scanning all five stores and keeping only the
    first occurrence of each ``(model, text)`` pair avoids counting the same luna
    sentence five times.
    """
    seen: set[tuple[str, str]] = set()
    items: list[dict[str, object]] = []
    for arm in _ARMS:
        root = _STORE_ROOT / f"sample-writers-{arm}"
        if not root.is_dir():
            continue
        store = LexemeStore(StoreConfig(root=root))
        for item in _iter_attributed_items(store):
            key = (str(item["model"]), str(item["text"]))
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
    return items


def _lexical_diversity_report(items: list[dict[str, object]]) -> dict[str, object]:
    by_model: dict[str, list[str]] = {}
    for item in items:
        by_model.setdefault(str(item["model"]), []).append(str(item["text"]))

    report: dict[str, object] = {}
    all_texts: list[str] = []
    luna_texts: list[str] = []
    for model, texts in by_model.items():
        tokens_all: list[str] = []
        distinct_4grams: set[tuple[str, ...]] = set()
        total_4grams = 0
        openers: Counter[str] = Counter()
        for text in texts:
            toks = _tokens(text)
            tokens_all.extend(toks)
            grams = _ngrams(toks, 4)
            distinct_4grams |= grams
            total_4grams += len(grams)
            openers[_opening(text)] += 1
        ttr = len(set(tokens_all)) / len(tokens_all) if tokens_all else 0.0
        distinct_4gram_rate = len(distinct_4grams) / total_4grams if total_4grams else 0.0
        n = sum(openers.values())
        opener_entropy = (
            -sum((c / n) * math.log2(c / n) for c in openers.values() if c) if n else 0.0
        )
        report[model] = {
            "n_items": len(texts),
            "type_token_ratio": round(ttr, 4),
            "distinct_4gram_rate": round(distinct_4gram_rate, 4),
            "sentence_opener_entropy_bits": round(opener_entropy, 4),
            "distinct_openers": len(openers),
            "top_openers": openers.most_common(5),
        }
        all_texts.extend(texts)
        if model == "gpt-5.6-luna":
            luna_texts.extend(texts)

    def _pool_stats(texts: list[str]) -> dict[str, float]:
        toks: list[str] = []
        grams: set[tuple[str, ...]] = set()
        total = 0
        for text in texts:
            t = _tokens(text)
            toks.extend(t)
            g = _ngrams(t, 4)
            grams |= g
            total += len(g)
        return {
            "type_token_ratio": round(len(set(toks)) / len(toks), 4) if toks else 0.0,
            "distinct_4gram_rate": round(len(grams) / total, 4) if total else 0.0,
            "n_items": len(texts),
        }

    report["_pooled_all_writers"] = _pool_stats(all_texts)
    report["_pooled_luna_only"] = _pool_stats(luna_texts)
    return report


def _gate_breakdown_report(items: list[dict[str, object]]) -> dict[str, object]:
    """Return, per writer, the share of non-canonical items surviving with each QA flag.

    These flags are set by the generation-time checks themselves
    (:mod:`opengloss_generator.workflows.enrich`) on whatever a rendition still carries
    after its one retry — so a flag here means the writer missed the FK band,
    headword-initial, headword-absent, hard-vocabulary, or near-copy check *twice*, not
    once. ``og.filler`` is set separately by ``qc filler`` and is not generation-time;
    it is reported by ``scripts/run_writer_pilot.py``'s own ``qc filler`` invocation.
    """
    by_model: dict[str, list[dict[str, object]]] = {}
    for item in items:
        if item["is_canonical"]:
            continue
        by_model.setdefault(str(item["model"]), []).append(item)

    report: dict[str, object] = {}
    for model, model_items in by_model.items():
        n = len(model_items)
        flag_counts: Counter[str] = Counter()
        any_flag = 0
        for item in model_items:
            flags = item["qa_flags"]
            if flags:
                any_flag += 1
            for flag in flags:  # type: ignore[union-attr]
                flag_counts[str(flag)] += 1
        report[model] = {
            "n_items": n,
            "any_flag_rate": round(any_flag / n, 4) if n else None,
            "flag_counts": dict(flag_counts),
            "flag_rates": {k: round(v / n, 4) for k, v in flag_counts.items()} if n else {},
        }
    return report


def _anchoring_report(items: list[dict[str, object]]) -> dict[str, object]:
    by_model: dict[str, list[dict[str, object]]] = {}
    for item in items:
        by_model.setdefault(str(item["model"]), []).append(item)

    report: dict[str, object] = {}
    for model, model_items in by_model.items():
        gloss_items = [i for i in model_items if i["field"] == "gloss" and not i["is_canonical"]]
        example_items = [i for i in model_items if i["field"] == "example"]
        headword_initial = sum(
            1 for i in gloss_items if is_headword_initial(str(i["text"]), str(i["headword"]))
        )
        near_copy = sum(
            1 for i in gloss_items if is_near_copy(str(i["text"]), str(i["canonical_gloss"]))
        )
        headword_present = sum(
            1
            for i in example_items
            if str(i["headword"]).lower() in str(i["text"]).lower()
            or bool(content_words(str(i["headword"])) & content_words(str(i["text"])))
        )
        report[model] = {
            "gloss_renditions": len(gloss_items),
            "headword_initial_rate": round(headword_initial / len(gloss_items), 4)
            if gloss_items
            else None,
            "near_copy_rate": round(near_copy / len(gloss_items), 4) if gloss_items else None,
            "example_renditions": len(example_items),
            "headword_present_rate": round(headword_present / len(example_items), 4)
            if example_items
            else None,
        }
    return report


def _attribution_report(items: list[dict[str, object]]) -> dict[str, object]:
    """TF-IDF + logistic regression attribution, balanced and 5-fold cross-validated."""
    # Imported lazily: scikit-learn is deliberately not a package dependency (this
    # script is one-off pilot analysis, run via `uv run --with scikit-learn`).
    from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: PLC0415
    from sklearn.linear_model import LogisticRegression  # noqa: PLC0415
    from sklearn.metrics import confusion_matrix  # noqa: PLC0415
    from sklearn.model_selection import StratifiedKFold, cross_val_predict  # noqa: PLC0415
    from sklearn.utils import resample  # noqa: PLC0415

    by_model: dict[str, list[str]] = {}
    for item in items:
        # Only non-canonical items carry the writer's own style; canonical items are
        # luna's regardless of which arm's store they were read from, and including
        # them would just add easy, uninformative luna-vs-everyone signal.
        if item["is_canonical"]:
            continue
        by_model.setdefault(str(item["model"]), []).append(str(item["text"]))

    models = sorted(by_model)
    if len(models) < _MIN_WRITERS:
        return {"error": "fewer than two writers with attributable, non-canonical text"}
    min_n = min(len(texts) for texts in by_model.values())
    if min_n < _MIN_ITEMS_PER_WRITER:
        return {
            "error": (
                f"smallest class has only {min_n} items; "
                f"need >={_MIN_ITEMS_PER_WRITER} per writer for a fold"
            ),
            "counts": {m: len(t) for m, t in by_model.items()},
        }

    rng_seed = 42
    texts: list[str] = []
    labels: list[str] = []
    for model in models:
        balanced = resample(by_model[model], n_samples=min_n, replace=False, random_state=rng_seed)
        texts.extend(balanced)
        labels.extend([model] * min_n)

    vectorizer = TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_features=20_000)
    features = vectorizer.fit_transform(texts)
    clf = LogisticRegression(max_iter=1000, class_weight="balanced", random_state=rng_seed)
    n_splits = min(5, min_n)
    cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=rng_seed)
    predicted = cross_val_predict(clf, features, labels, cv=cv)
    accuracy = sum(p == y for p, y in zip(predicted, labels, strict=True)) / len(labels)
    chance = 1.0 / len(models)

    clf.fit(features, labels)
    feature_names = vectorizer.get_feature_names_out()
    top_features: dict[str, list[str]] = {}
    coefs = clf.coef_ if len(models) > _MIN_WRITERS else [clf.coef_[0], -clf.coef_[0]]
    classes = list(clf.classes_) if len(models) > _MIN_WRITERS else models
    for cls, row in zip(classes, coefs, strict=True):
        top_idx = row.argsort()[::-1][:10]
        top_features[str(cls)] = [feature_names[i] for i in top_idx]

    cm = confusion_matrix(labels, predicted, labels=models)
    return {
        "writers": models,
        "n_per_writer_balanced": min_n,
        "cv_folds": n_splits,
        "accuracy": round(float(accuracy), 4),
        "chance_accuracy": round(chance, 4),
        "confusion_matrix": {"labels": models, "matrix": cm.tolist()},
        "top_features_per_writer": top_features,
    }


def main() -> None:
    """Collect attributed items across every arm and write the combined JSON report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="Write the JSON report here.")
    args = parser.parse_args()

    items = _collect_all_items()
    report = {
        "n_attributed_items": len(items),
        "counts_by_model": dict(Counter(str(i["model"]) for i in items)),
        "lexical_diversity": _lexical_diversity_report(items),
        "anchoring": _anchoring_report(items),
        "gate_breakdown": _gate_breakdown_report(items),
        "attribution": _attribution_report(items),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    overview = {k: v for k, v in report.items() if k != "attribution"}
    print(json.dumps(overview, indent=2, default=str))  # noqa: T201
    print(json.dumps(report["attribution"], indent=2, default=str))  # noqa: T201


if __name__ == "__main__":
    main()
