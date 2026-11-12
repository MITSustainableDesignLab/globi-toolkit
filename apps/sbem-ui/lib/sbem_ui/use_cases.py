"""Use cases a run can be set up for, and the outputs each one has to turn on in globi.

sbem-ui writes these into every manifest it generates; the results dashboard and the
toolkit's downloader read them to tell a user what a run can and can't answer.
Energy use and peaks (EnergyAndPeak) are always produced, so they need no entry.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


def _overheating_defaults() -> dict[str, Any]:
    from epinterface.analysis.overheating import OverheatingAnalysisConfig

    return OverheatingAnalysisConfig().model_dump(mode="json")


def _hourly_defaults() -> dict[str, Any]:
    from epinterface.sbem.builder import AVAILABLE_HOURLY_VARIABLES

    # fileref-only: one file per building, so the final gather never holds the whole stock
    return {"data": list(AVAILABLE_HOURLY_VARIABLES), "output_mode": "fileref-only"}


@dataclass(frozen=True)
class UseCase:
    """What a use case is for, and the manifest settings it needs."""

    label: str
    description: str
    #: manifest key -> (project file holding its settings, the file's starting contents)
    outputs: dict[str, tuple[str, Callable[[], dict[str, Any]]]]


USE_CASES: dict[str, UseCase] = {
    "overheating": UseCase(
        label="Overheating",
        description="Hours and degree-hours above temperature thresholds, by building and room.",
        outputs={"overheating_config": ("use-cases/overheating.yml", _overheating_defaults)},
    ),
    "hourly-conditions": UseCase(
        label="Hourly room conditions",
        description="Hour-by-hour indoor temperature and humidity for every room "
        "(about 1 MB per building per variable).",
        outputs={"hourly_data_config": ("use-cases/hourly-data.yml", _hourly_defaults)},
    ),
}
