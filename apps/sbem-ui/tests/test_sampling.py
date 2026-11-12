"""Sampling: building selection, prior draws written into the GIS globi runs."""

import json

import pytest
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).parent.parent / "server"
sys.path.insert(0, str(SERVER_DIR))

from fastapi.testclient import TestClient
from main import app

from conftest import open_temp_workspace

client = TestClient(app)

TYPOLOGIES = ["Hospital", "Hotel", "Office", "Residential", "School"]
TYPOLOGY = {
    "id": "f-typ",
    "name": "Typology",
    "type": "deterministic",
    "data_type": "categorical",
    "options": TYPOLOGIES,
    "requires_prior": False,
    "transformation_rule": {
        "id": "t-typ",
        "type": "categorical_mapping",
        "source_column": "Typology",
        "target_field": "Typology",
        "categorical_groups": [{"group_label": t, "source_values": [t]} for t in TYPOLOGIES],
    },
}
HEATING = {
    "id": "f-heat",
    "name": "Heating",
    "type": "stochastic",
    "data_type": "categorical",
    "options": ["Gas", "HeatPump"],
    "requires_prior": True,
}
FIELDS = [TYPOLOGY, HEATING]


def setup_function():
    open_temp_workspace()
    assert client.post("/api/gis/sample").status_code == 200
    mappings = {"building_id": "building_id", "num_floors": "num_storeys", "height": "height"}
    cleaned = client.post("/api/gis/clean", json={"column_mappings": mappings, "validate_columns": ["building_id"]})
    assert cleaned.json()["validation"]["valid"]


def heating_config() -> dict:
    generated = client.post(
        "/api/prior-trees/generate",
        json={"fields": FIELDS, "target": "Heating", "target_values": ["Gas", "HeatPump"],
              "condition_on": ["Typology"], "method": "uniform"},
    ).json()
    config = {"registry": {"fields": []}, "priors": {}, "joints": [generated["joint"]], "scenarios": []}
    config["registry"]["fields"] = client.post(
        "/api/prior-trees/registry-suggestions", json={"fields": FIELDS, "config": config}
    ).json()["fields"]
    return config


def post(path: str, settings: dict, fields=FIELDS, config=None):
    body = {"fields": fields, "config": heating_config() if config is None else config, "settings": settings}
    return client.post(f"/api/sampling/{path}", json=body)


def test_preview_counts_buildings_times_draws():
    summary = post("preview", {"draws": 3, "buildings": {"mode": "random", "n": 10}}).json()
    assert summary["buildings"] == 10
    assert summary["simulations"] == 30
    assert summary["estimated"] == ["Heating"]


def test_write_draws_every_estimated_characteristic_with_unique_ids():
    result = post("write", {"draws": 2, "seed": 1, "buildings": {"mode": "random", "n": 5}})
    assert result.status_code == 200, result.text
    assert result.json()["rows"] == 10

    workspace = app.state.workspace
    features = json.loads((workspace.root / "gis" / "buildings-sampled.geojson").read_text())["features"]
    ids = [f["properties"]["building_id"] for f in features]
    assert len(set(ids)) == 10 and all("__" in i for i in ids)
    assert {f["properties"]["Heating"] for f in features} <= {"Gas", "HeatPump"}
    assert all(f["properties"]["Typology"] in TYPOLOGIES and f["geometry"] for f in features)
    assert (workspace.root / "sampling.yaml").exists()


def test_same_seed_same_sample():
    settings = {"draws": 2, "seed": 7, "buildings": {"mode": "random", "n": 8}}
    path = Path(app.state.workspace.root) / "gis" / "buildings-sampled.geojson"
    post("write", settings)
    first = path.read_text()
    post("write", settings)
    assert path.read_text() == first


def test_stratified_takes_n_per_combination():
    summary = post("preview", {"buildings": {"mode": "stratified", "per_stratum": 2, "strata": ["Typology"]}}).json()
    assert summary["strata"] == 5
    assert summary["buildings"] == 10


def test_without_estimates_one_draw_per_building():
    summary = post("preview", {"draws": 5}, fields=[TYPOLOGY], config={}).json()
    assert summary["draws"] == 1
    assert summary["simulations"] == 100


def test_refuses_an_estimated_characteristic_without_an_estimate():
    response = post("preview", {}, config={})
    assert response.status_code == 400
    assert "Heating" in response.json()["detail"]


def test_variant_indices_of_uncertain_components_are_drawn_evenly():
    from services.part_rules import VARIANT_NOTE

    workspace = app.state.workspace
    workspace.write_semantic_fields({"Name": "Test", "Fields": [
        {"Name": "Typology", "Options": TYPOLOGIES},
        {"Name": "FacadeAssembly_variant", "Options": ["1", "2", "3", "4"], "Description": VARIANT_NOTE},
    ]})
    empty = {"registry": {"fields": []}, "priors": {}, "joints": [], "scenarios": []}
    settings = {"draws": 20, "seed": 3, "buildings": {"mode": "all"}}
    summary = post("preview", settings, fields=[TYPOLOGY], config=empty).json()
    assert summary["estimated"] == ["FacadeAssembly_variant"] and summary["draws"] == 20
    assert post("write", settings, fields=[TYPOLOGY], config=empty).status_code == 200
    features = json.loads((workspace.root / "gis" / "buildings-sampled.geojson").read_text())["features"]
    drawn = [f["properties"]["FacadeAssembly_variant"] for f in features]
    assert len(drawn) == 2000 and {d: drawn.count(d) / 2000 for d in set(drawn)} == pytest.approx(dict.fromkeys("1234", 0.25), abs=0.03)


def test_correlated_variant_indices_are_drawn_jointly():
    import yaml
    from scipy.stats import spearmanr
    from services.part_rules import VARIANT_NOTE

    workspace = app.state.workspace
    workspace.write_semantic_fields({"Name": "Test", "Fields": [
        {"Name": "Typology", "Options": TYPOLOGIES},
        {"Name": "FacadeAssembly_variant", "Options": [str(k) for k in range(1, 6)], "Description": VARIANT_NOTE},
        {"Name": "Window_variant", "Options": ["1", "2", "3"], "Description": VARIANT_NOTE},
    ]})
    workspace.path("component_priors").write_text(yaml.safe_dump(
        {"links": [{"parts": ["Envelope.Assemblies.FacadeAssembly", "Envelope.Window"], "link": 0.9}]}
    ))
    empty = {"registry": {"fields": []}, "priors": {}, "joints": [], "scenarios": []}
    assert post("write", {"draws": 20, "seed": 5, "buildings": {"mode": "all"}}, fields=[TYPOLOGY], config=empty).status_code == 200
    rows = [f["properties"] for f in json.loads((workspace.root / "gis" / "buildings-sampled.geojson").read_text())["features"]]
    walls, windows = [int(r["FacadeAssembly_variant"]) for r in rows], [int(r["Window_variant"]) for r in rows]
    assert spearmanr(walls, windows).statistic > 0.75
    # each index is still even on its own
    assert {k: windows.count(k) / len(rows) for k in (1, 2, 3)} == pytest.approx(dict.fromkeys((1, 2, 3), 1 / 3), abs=0.03)
