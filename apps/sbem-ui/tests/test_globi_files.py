"""Template projects, the sample GIS, and generating globi's run files from a project."""

import json
import shutil
import sys
import tempfile
from pathlib import Path

SERVER_DIR = Path(__file__).parent.parent / "server"
sys.path.insert(0, str(SERVER_DIR))

import yaml
from fastapi.testclient import TestClient
from main import app

from services.template_loader import TEMPLATES_DIR
from services.workspace import ProjectWorkspace

client = TestClient(app)

TEMPLATE_DIR = TEMPLATES_DIR / "projects" / "globi-tutorial"
WEATHER = "weather/USA_MA_Boston-Logan.Intl.AP.725090_TMYx.2009-2023.zip"
E2E_MAP = {
    "Envelope": {"selector": {"source_fields": ["Region", "Typology", "Scenario", "Age_bracket"]}},
    "Operations": {
        "SpaceUse": {"selector": {"source_fields": ["Region", "Typology", "Income", "Scenario"]}},
        "HVAC": {"selector": {"source_fields": ["Region", "Typology", "Scenario", "Age_bracket"]}},
        "DHW": {"selector": {"source_fields": ["Region", "Typology"]}},
    },
}
FIELDS = {
    "Region": ["TestRegion"],
    "Typology": ["Office", "School", "Residential", "Hospital", "Hotel"],
    "Age_bracket": ["Pre_1980", "1980_to_2000", "Post_2000"],
    "Income": ["Low", "High"],
    "Scenario": ["Baseline", "Retrofit"],
}
CONTEXT = {"Region": "TestRegion", "Typology": "Office", "Age_bracket": "Post_2000", "Income": "Low", "Scenario": "Baseline"}


def template_project() -> ProjectWorkspace:
    root = Path(tempfile.mkdtemp()) / "workspace"
    # init_db=True: a template's db must make the (raising) empty-db init a no-op
    workspace = ProjectWorkspace.create(root, "Demo", init_db=True, template_dir=TEMPLATE_DIR)
    app.state.workspace = workspace
    return workspace


def saved_project(fields=FIELDS) -> ProjectWorkspace:
    workspace = template_project()
    workspace.write_semantic_fields(
        {"Name": "Demo", "Fields": [{"Name": name, "Options": options} for name, options in fields.items()]}
    )
    assert client.post("/api/artifacts/component-map", json={"payload": E2E_MAP}).status_code == 200
    return workspace


def generate(**overrides):
    body = {"name": "Demo run", "epwzip_file": WEATHER, "context": CONTEXT, **overrides}
    return client.post("/api/globi-files/generate", json=body)


def test_template_is_listed_and_copied_into_a_new_project():
    templates = client.get("/api/workspace/templates").json()["templates"]
    assert [t["filename"] for t in templates] == ["globi-starter.yml", "globi-tutorial.yml", "resstock.yml"]

    workspace = template_project()
    assert workspace.path("components_db").stat().st_size == (TEMPLATE_DIR / "components-lib.db").stat().st_size
    assert (workspace.root / WEATHER).exists()


def test_sample_gis_loads_like_an_upload():
    template_project()
    data = client.post("/api/gis/sample").json()
    assert data["rowCount"] == 100
    assert data["columnMappings"]["num_floors"] == "num_storeys"


def test_saved_semantic_fields_carry_every_column_role_from_gis_cleaning():
    workspace = template_project()
    client.post("/api/gis/sample")
    mappings = {"building_id": "building_id", "num_floors": "num_storeys", "height": "height", "wwr": "WWR"}
    client.post("/api/gis/clean", json={"column_mappings": mappings, "validate_columns": []})

    client.post("/api/artifacts/semantic-fields", json={"payload": {"name": "Demo", "fields": []}})
    saved = yaml.safe_load(workspace.path("semantic_fields").read_text())
    assert saved["Height_col"] == "height"
    assert saved["WWR_col"] == "WWR"
    assert saved["Num_Floors_col"] == "num_storeys"


def test_generates_building_file_only_until_a_gis_is_saved():
    workspace = saved_project()
    result = generate()
    assert result.status_code == 200, result.text
    assert result.json()["written"] == ["building.yml"]
    assert "No buildings saved" in result.json()["warnings"][0]

    building = yaml.safe_load(workspace.path("building").read_text())
    assert building["semantic_field_context"] == CONTEXT
    assert building["component_map_file"] == str(workspace.path("component_map").resolve())


def save_stock(workspace: ProjectWorkspace) -> None:
    """The sample buildings as Save to project writes them: Age_bracket made from YEAR_BUILT's ranges."""
    stock = json.loads((TEMPLATES_DIR / "gis" / "buildings-100.geojson").read_text())
    for feature in stock["features"]:
        year = feature["properties"]["YEAR_BUILT"]
        feature["properties"]["Age_bracket"] = "Pre_1980" if year < 1980 else "1980_to_2000" if year <= 2000 else "Post_2000"
    (workspace.path("gis_dir") / "buildings.geojson").write_text(json.dumps(stock))


def test_generates_batch_files_from_a_saved_gis():
    workspace = saved_project()
    save_stock(workspace)

    result = generate().json()
    assert result["written"] == ["building.yml", "artifacts.yml", "gis-preprocessor.yml", "manifest.yml", "manifest-retrofit.yml"]
    assert result["warnings"] == []
    manifest = yaml.safe_load(workspace.path("manifest").read_text())
    assert manifest["file_config"] == str(workspace.path("artifacts").resolve())
    # globi sets Scenario from the manifest: one run per Scenario value
    assert manifest["scenario"] == "Baseline"
    assert yaml.safe_load((workspace.root / "manifest-retrofit.yml").read_text())["scenario"] == "Retrofit"
    assert result["commands"]["batch"].count("--max-sims 5") == 2


def test_use_cases_turn_their_outputs_on_in_every_manifest():
    workspace = saved_project()
    save_stock(workspace)
    settings = workspace.root / "use-cases" / "overheating.yml"
    settings.parent.mkdir(exist_ok=True)
    settings.write_text(yaml.safe_dump({"heat_thresholds": [{"threshold": 28.0}]}))  # a user's own thresholds

    assert generate(use_cases=["overheating", "hourly-conditions"]).status_code == 200
    for name in ("manifest.yml", "manifest-retrofit.yml"):
        manifest = yaml.safe_load((workspace.root / name).read_text())
        assert manifest["overheating_config"] == str(settings.resolve())
        assert manifest["hourly_data_config"].endswith("use-cases/hourly-data.yml")
    # an edited file is kept; a missing one starts from globi's defaults
    assert yaml.safe_load(settings.read_text())["heat_thresholds"] == [{"threshold": 28.0}]
    assert yaml.safe_load((workspace.root / "use-cases" / "hourly-data.yml").read_text())["output_mode"] == "fileref-only"
    chosen = {c["id"]: c["chosen"] for c in client.get("/api/globi-files/").json()["useCases"]}
    assert chosen == {"overheating": True, "hourly-conditions": True}

    assert generate(use_cases=[]).status_code == 200
    assert yaml.safe_load(workspace.path("manifest").read_text())["hourly_data_config"] is None
    assert "overheating_config" not in yaml.safe_load(workspace.path("manifest").read_text())


def test_each_climate_runs_every_scenario_with_its_own_weather():
    workspace = saved_project()
    save_stock(workspace)
    future = workspace.root / "weather" / "boston-2050-ssp245.zip"
    shutil.copy(workspace.root / WEATHER, future)
    climates = [
        {"label": "2025", "weather": WEATHER, "year": 2025},
        {"label": "2050 SSP2-4.5", "weather": "weather/boston-2050-ssp245.zip", "year": 2050, "ssp": "SSP2-4.5"},
    ]

    result = generate(climates=climates).json()
    assert result["written"] == [
        "building.yml", "artifacts.yml", "gis-preprocessor.yml", "manifest.yml", "manifest-retrofit-2025.yml",
        "artifacts-2050-ssp2-4-5.yml", "manifest-baseline-2050-ssp2-4-5.yml", "manifest-retrofit-2050-ssp2-4-5.yml",
    ]
    future_run = yaml.safe_load((workspace.root / "manifest-retrofit-2050-ssp2-4-5.yml").read_text())
    assert future_run["name"] == "Demo run/2050-ssp2-4-5"  # runs as Demo run/2050-ssp2-4-5/Retrofit
    future_artifacts = yaml.safe_load(Path(future_run["file_config"]).read_text())
    assert future_artifacts["epwzip_file"] == str(future.resolve())
    assert yaml.safe_load(workspace.path("manifest").read_text())["name"] == "Demo run/2025"
    saved = client.get("/api/globi-files/").json()["climates"]
    assert [(c["year"], c["ssp"]) for c in saved] == [(2025, None), (2050, "SSP2-4.5")]

    # back to one weather file: the climate files go, so they can't be run by mistake
    assert generate().json()["written"][-1] == "manifest-retrofit.yml"
    assert not (workspace.root / "artifacts-2050-ssp2-4-5.yml").exists()
    assert sorted(p.name for p in workspace.root.glob("manifest*.yml")) == ["manifest-retrofit.yml", "manifest.yml"]


def test_climates_need_their_own_names():
    workspace = saved_project()
    save_stock(workspace)
    same = [{"label": "2050", "weather": WEATHER}, {"label": "2050", "weather": WEATHER}]
    assert "its own name" in generate(climates=same).json()["detail"]
    unnamed = [{"label": "", "weather": WEATHER}, {"label": "2050", "weather": WEATHER}]
    assert generate(climates=unnamed).status_code == 400


def test_rejects_an_unknown_use_case_and_an_invalid_settings_file():
    workspace = saved_project()
    save_stock(workspace)
    assert "Unknown use case" in generate(use_cases=["wtp"]).json()["detail"]
    settings = workspace.root / "use-cases" / "overheating.yml"
    settings.parent.mkdir(exist_ok=True)
    settings.write_text(yaml.safe_dump({"heat_thresholds": "hot"}))
    assert generate(use_cases=["overheating"]).status_code == 400


def test_batch_warns_about_fields_the_gis_has_no_column_for():
    workspace = saved_project({**FIELDS, "Heating": ["Gas", "HeatPump"]})
    save_stock(workspace)
    result = generate(context={**CONTEXT, "Heating": "Gas"}).json()
    assert "Heating" in result["warnings"][0]
    assert "Scenario" not in result["warnings"][0]  # globi adds it from each manifest


def test_rejects_a_building_value_outside_the_field_options():
    saved_project()
    result = generate(context={**CONTEXT, "Typology": "Warehouse"})
    assert result.status_code == 400
    assert "Typology" in result.json()["detail"]


def test_refuses_run_files_the_library_cannot_build():
    saved_project({**FIELDS, "Typology": [*FIELDS["Typology"], "Warehouse"]})
    result = generate(context={**CONTEXT, "Typology": "Warehouse"})
    assert result.status_code == 400
    assert "doesn't cover this building" in result.json()["detail"]
