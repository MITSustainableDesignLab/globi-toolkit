"""Willingness to pay: the project's use-cases/wtp.yml, the data it can draw on, and a check of
the mapping against the saved buildings, before any GloBI results exist.

The model is the toolkit's (globi_toolkit.use_cases.wtp). sbem-ui runs inside the
globi-toolkit workspace, as it already does for every globi command.
"""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from globi_toolkit.use_cases.wtp import RESULT_COLUMNS, WTPConfig, check
from pydantic import ValidationError

from services.globi_run_files import saved_gis
from services.template_loader import TEMPLATES_DIR
from services.workspace import ProjectWorkspace

FOLDER = "use-cases"
EXAMPLE = TEMPLATES_DIR / "projects" / "globi-tutorial" / "use-cases"
#: a column offers its values (for typologies and category maps) up to this many
MAX_VALUES = 50


class WTPError(Exception):
    """A file the user gave can't be used."""


def wtp_status(workspace: ProjectWorkspace) -> dict[str, Any]:
    """The saved model, what it can map from, and what still stops it from running."""
    folder = workspace.root / FOLDER
    path = folder / "wtp.yml"
    raw = (yaml.safe_load(path.read_text()) or None) if path.exists() else None
    buildings = _saved_buildings(workspace)
    return {
        "config": raw,
        "buildingColumns": _columns(buildings),
        "resultColumns": [{"name": k, "description": v} for k, v in RESULT_COLUMNS.items()],
        "tables": [
            {"file": p.name, "columns": list(pd.read_csv(p, nrows=0).columns)}
            for p in sorted(folder.glob("*.csv"))
        ],
        "check": _check(folder, raw, buildings),
    }


def save_wtp(workspace: ProjectWorkspace, raw: dict[str, Any]) -> dict[str, Any]:
    """Save the form as wtp.yml, even if incomplete, and report what's still missing."""
    folder = workspace.root / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "wtp.yml").write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    return wtp_status(workspace)


def save_table(workspace: ProjectWorkspace, filename: str, data: bytes) -> dict[str, Any]:
    """Add (or replace) an area table: a CSV, one row per area."""
    name = Path(filename).name  # never a path outside the project
    if not name.lower().endswith(".csv"):
        raise WTPError("Area tables are CSV files (one row per area).")
    try:
        pd.read_csv(io.BytesIO(data), nrows=5)
    except (ValueError, UnicodeDecodeError) as exc:
        raise WTPError(f"{name} isn't a readable CSV: {exc}") from exc
    folder = workspace.root / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    return wtp_status(workspace)


def load_example(workspace: ProjectWorkspace) -> dict[str, Any]:
    """Copy the worked example (Massachusetts, Berzolla et al. 2025) into the project."""
    folder = workspace.root / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    for source in EXAMPLE.iterdir():
        if source.suffix in (".yml", ".csv"):
            shutil.copy2(source, folder / source.name)
    return wtp_status(workspace)


def _saved_buildings(workspace: ProjectWorkspace) -> pd.DataFrame | None:
    gis = saved_gis(workspace)
    if gis is None:
        return None
    features = json.loads(gis.read_text()).get("features") or []
    return pd.DataFrame([f.get("properties") or {} for f in features])


def _columns(buildings: pd.DataFrame | None) -> list[dict[str, Any]]:
    if buildings is None:
        return []
    out = []
    for name in buildings.columns:
        values = buildings[name].dropna().astype(str).unique()
        out.append({
            "name": str(name),
            "values": sorted(values)[:MAX_VALUES] if len(values) <= MAX_VALUES else [],
        })
    return out


def _check(folder: Path, raw: dict[str, Any] | None, buildings: pd.DataFrame | None) -> dict[str, Any]:
    """{ready, problems, residential, commercial}: ready once nothing stops a run."""
    counts = {"residential": 0, "commercial": 0}
    if raw is None:
        return {"ready": False, "problems": ["No model yet: load the worked example, or fill in the form."], **counts}
    try:
        config = WTPConfig.from_dict(raw, folder)
    except ValidationError as exc:
        problems = [_validation_message(err) for err in exc.errors()]
        return {"ready": False, "problems": problems, **counts}
    if buildings is None:
        problems = ["Save your buildings first (GIS Cleaning or Sampling), so the mapping can be checked against them."]
        return {"ready": False, "problems": problems, **counts}
    found = check(buildings, config)
    return {"ready": not found["problems"], **found}


def _validation_message(err: Any) -> str:
    where = ".".join(str(part) for part in err["loc"])
    message = str(err["msg"]).removeprefix("Value error, ")
    if err["type"] == "missing":
        return f"{where}: fill this in."
    return f"{where}: {message}" if where else message
