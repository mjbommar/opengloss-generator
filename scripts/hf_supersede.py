"""Add a "Superseded by" banner to every card of a published release (idempotent).

Downloads each ``mjbommar/opengloss-{OLD}-*`` README, inserts the banner right after the
YAML front matter (the position the v2.2 -> v2.3 banners used), and uploads it back as a
single-file commit. A card that already carries a banner for ``NEW`` is left alone.

Usage:
    uv run python scripts/hf_supersede.py
"""

from huggingface_hub import HfApi, hf_hub_download

OLD = "v2.3"
NEW = "v2.4"
DATE = "2026-09-25"
REPOS = [
    "senses", "lexicon", "inflections", "definitions", "examples", "encyclopedia",
    "etymology", "relations", "contrasts", "queries", "qa-pairs", "retrieval-pairs",
    "retrieval-triples", "qrels", "pretrain", "provenance",
]  # fmt: skip
SUMMARY = (
    "every sense now has search queries, QA pairs and verified examples (v2.3 had them "
    "only for core and tier 2); level x register definitions and leveled contrasts and "
    "explanations are added; and the pretraining corpus no longer contains duplicate "
    "documents"
)


def main() -> None:
    """Patch every card."""
    api = HfApi()
    for repo in REPOS:
        old_id = f"mjbommar/opengloss-{OLD}-{repo}"
        new_id = f"mjbommar/opengloss-{NEW}-{repo}"
        path = hf_hub_download(old_id, "README.md", repo_type="dataset")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        if f"Superseded by [OpenGloss {NEW}]" in text:
            print("already", old_id)
            continue
        banner = (
            f"> **Superseded by [OpenGloss {NEW}](https://huggingface.co/datasets/{new_id})** "
            f"({DATE}): {SUMMARY}. {OLD} stays published for reproducibility.\n\n"
        )
        head, sep, body = text.partition("\n---\n")
        if not text.startswith("---") or not sep:
            raise SystemExit(f"{old_id}: unexpected card layout")
        patched = head + sep + "\n" + banner + body.lstrip("\n")
        api.upload_file(
            path_or_fileobj=patched.encode("utf-8"),
            path_in_repo="README.md",
            repo_id=old_id,
            repo_type="dataset",
            commit_message=f"Point to OpenGloss {NEW}",
        )
        print("patched", old_id)


if __name__ == "__main__":
    main()
