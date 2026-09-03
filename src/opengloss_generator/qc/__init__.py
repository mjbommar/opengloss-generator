"""Free, model-free quality-control passes over a whole store.

Distinct from ``workflows/*_hygiene.py`` (which *repair* defects, several with a model
call per entry): a ``qc`` pass only measures and, optionally, flags. See
:mod:`opengloss_generator.qc.filler` for the first one.
"""

from __future__ import annotations

from opengloss_generator.qc.filler import (
    CalibrationPoint,
    FillerConfig,
    FillerFlagOutcome,
    FillerReport,
    analyze_filler,
    apply_filler_flags,
    calibrate_thresholds,
    phrases_in,
)

__all__ = [
    "CalibrationPoint",
    "FillerConfig",
    "FillerFlagOutcome",
    "FillerReport",
    "analyze_filler",
    "apply_filler_flags",
    "calibrate_thresholds",
    "phrases_in",
]
