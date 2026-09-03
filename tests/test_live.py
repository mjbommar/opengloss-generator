"""Live provider smoke test. Deselected by default; run with ``pytest -m live``.

Spends real money, capped at ten cents. Requires ``OPENAI_API_KEY``.
"""

from __future__ import annotations

import os

import pytest

from opengloss_generator.config import AppConfig, ConcurrencyConfig, StoreConfig
from opengloss_generator.runner import RunSession
from opengloss_generator.schema import ReadingLevel
from opengloss_generator.workflows.enrich import EnrichmentSpec, enrich_entry
from opengloss_generator.workflows.generate import EntrySpec, generate_entry

pytestmark = pytest.mark.live


@pytest.mark.skipif("OPENAI_API_KEY" not in os.environ, reason="no OPENAI_API_KEY")
async def test_generate_and_grade_one_real_entry(tmp_path):
    config = AppConfig(
        store=StoreConfig(root=tmp_path / "store"),
        concurrency=ConcurrencyConfig(workers=4),
        log_dir=tmp_path / "runs",
        budget_usd=0.10,
    )
    async with RunSession(config, run_id="live-smoke") as session:
        generated = await generate_entry(EntrySpec(headword="abseil"), session.stages)
        assert generated.entry.sense_count() >= 1
        assert next(iter(generated.entry.provenance.values())).service_tier == "flex"

        enriched = await enrich_entry(
            generated.entry,
            EnrichmentSpec.for_glosses(reading_levels=[ReadingLevel.GRADE_1, ReadingLevel.COLLEGE]),
            session.stages,
        )
        assert enriched.renditions_added >= 2
        session.store.write(enriched.entry)

    summary = session.summary().as_dict()
    assert 0 < summary["cost_usd"] <= 0.10
