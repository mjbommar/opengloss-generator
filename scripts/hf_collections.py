"""Maintain the Hugging Face collections: the legacy "OpenGloss 1.x" collection and the
"OpenGloss 2.x" collection that lists the current release. Needs a token with the
*collections* permission. Re-run with RELEASE bumped after each release; items are
added idempotently (`exists_ok=True`) — remove superseded items by hand or extend this.
"""

OLD = "mjbommar/opengloss-69304505fa0ddaaad8a3ca28"
RELEASE = "v2.3"
REPOS = ["senses", "lexicon", "inflections", "definitions", "examples", "encyclopedia", "etymology",
         "relations", "contrasts", "queries", "qa-pairs", "retrieval-pairs", "retrieval-triples",
         "qrels", "pretrain", "provenance"]
api = HfApi()
api.update_collection_metadata(OLD, title="OpenGloss 1.x",
    description="OpenGloss v1.1–v1.3: 206K LLM-generated English lexemes, one definition per sense, string relations. Superseded by OpenGloss 2.x.")
print("renamed:", OLD)
new = api.create_collection(title="OpenGloss 2.x",
    description=f"OpenGloss {RELEASE}: 160K lexemes, 300K senses, graded definitions, judged relations, named entities, provenance, retrieval data. Start with senses.",
    namespace="mjbommar", exists_ok=True)
print("created:", new.slug)
for r in REPOS:
    api.add_collection_item(new.slug, item_id=f"mjbommar/opengloss-{RELEASE}-{r}", item_type="dataset", exists_ok=True)
    print("  added", r)
api.add_collection_item(new.slug, item_id="2511.18622", item_type="paper", exists_ok=True); print("  added paper")
print("done:", f"https://huggingface.co/collections/{new.slug}")
