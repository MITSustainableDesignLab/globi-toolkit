"""Part rules: baseline, reuse, new dependencies, retrofits as Scenario, and importing a map."""

import itertools
import shutil
import tempfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from main import app
from services import component_library as lib
from services import part_rules
from services.globi_component_map import _selector_model
from services.template_loader import TEMPLATES_DIR
from services.workspace import ProjectWorkspace
from test_globi_files import E2E_MAP, FIELDS

client = TestClient(app)
TUTORIAL = TEMPLATES_DIR / "projects" / "globi-tutorial"
GOLDEN = Path("/Users/daryaguettler/school-repos/globi/inputs/sbem-demo-golden")
LIGHTING = "Operations.SpaceUse.Lighting"
HEATING = "Operations.HVAC.ConditioningSystems.Heating"
META = {"Name", "Category", "Comment", "DataSource", "Version"}


def tutorial_project(fields: dict[str, list[str]] | None = None) -> ProjectWorkspace:
    root = Path(tempfile.mkdtemp()) / "workspace"
    workspace = ProjectWorkspace.create(root, "Rules", init_db=True, template_dir=TUTORIAL)
    workspace.write_semantic_fields(
        {"Name": "Rules", "Fields": [{"Name": n, "Options": o} for n, o in (fields or {}).items()]}
    )
    app.state.workspace = workspace
    return workspace


def strip(value):
    if isinstance(value, dict):
        return {k: strip(v) for k, v in value.items() if k not in META}
    if isinstance(value, list):
        return [strip(v) for v in value]
    return value


def resolve(workspace, contexts, component_map=None):
    component_map = component_map or workspace.read_generated_component_map()
    model = _selector_model().model_validate(component_map)
    db = workspace.prisma_client()
    db.connect()
    try:
        return [strip(model.get_component(context=c, db=db).model_dump()) for c in contexts]
    finally:
        db.disconnect()


def save_rules(workspace, rules) -> dict:
    workspace.path("part_rules").write_text(yaml.safe_dump(rules, sort_keys=False))
    return part_rules.compile_project(workspace)


def baseline(workspace) -> dict:
    db = workspace.prisma_client()
    db.connect()
    try:
        return part_rules.baseline(db)
    finally:
        db.disconnect()


def names(workspace, component_type) -> list[str]:
    db = workspace.prisma_client()
    db.connect()
    try:
        return sorted(r["Name"] for r in lib.list_records(db, component_type) if not r["Name"].startswith("auto_"))
    finally:
        db.disconnect()


def test_baseline_runs_before_any_decision():
    workspace = tutorial_project()
    rules = baseline(workspace)
    assert set(rules["parts"]) == set(part_rules.parts()) and len(rules["parts"]) == 23
    assert save_rules(workspace, rules)["ok"]
    component_map = workspace.read_generated_component_map()
    assert component_map["Envelope"]["selector"] == {"prefix": "auto"}
    assert len(resolve(workspace, [{}])) == 1
    assert client.post("/api/component-map/check").json()["ok"] is True


def test_rows_that_share_an_option_reuse_one_record():
    workspace = tutorial_project({"Typology": ["Office", "Hospital", "School"]})
    rules = baseline(workspace)
    office, school = rules["parts"][LIGHTING]["default"], next(n for n in names(workspace, "Lighting") if n != rules["parts"][LIGHTING]["default"])
    rules["parts"][LIGHTING] = {"depends_on": ["Typology"], "default": office, "cells": {"School": school}}
    lighting_records = names(workspace, "Lighting")
    result = save_rules(workspace, rules)
    assert result["ok"], result["errors"]
    assert result["groups"]["Operations.SpaceUse"] == {"fields": ["Typology"], "combinations": 3}
    assert result["groups"]["Envelope"]["fields"] == []  # nothing else varies

    zones = resolve(workspace, [{"Typology": t} for t in ("Office", "Hospital", "School")])
    lighting = [z["Operations"]["SpaceUse"]["Lighting"] for z in zones]
    assert lighting[0] == lighting[1] != lighting[2]
    assert names(workspace, "Lighting") == lighting_records  # reuse made no copies


def test_a_new_dependency_keeps_every_option():
    workspace = tutorial_project({"Typology": ["Office", "School"], "Age": ["Old", "New"]})
    rules = baseline(workspace)
    save_rules(workspace, rules)
    before = resolve(workspace, [{}])[0]
    rules["parts"][HEATING]["depends_on"] = ["Age"]  # no cells yet: every row keeps the default
    assert save_rules(workspace, rules)["ok"]
    after = resolve(workspace, [{"Age": "Old"}, {"Age": "New"}])
    assert after == [before, before]


def test_retrofit_packages_compile_to_scenario_rows():
    workspace = tutorial_project({"LightingTech": ["NoLED", "LED"]})
    rules = baseline(workspace)
    old = rules["parts"][LIGHTING]["default"]
    new = next(n for n in names(workspace, "Lighting") if n != old)
    rules["parts"][LIGHTING] = {"depends_on": ["LightingTech"], "default": old, "cells": {"LED": new}}
    workspace.path("retrofits").write_text(yaml.safe_dump({
        "upgrade_order": {"LightingTech": ["NoLED", "LED"]},
        "packages": [{"name": "LED retrofit", "measures": [{"field": "LightingTech", "to": "LED"}]}],
    }))
    assert save_rules(workspace, rules)["ok"]

    scenario = next(f for f in workspace.read_semantic_fields()["Fields"] if f["Name"] == "Scenario")
    assert scenario["Options"] == ["Baseline", "LED retrofit"]
    zones = resolve(workspace, [
        {"LightingTech": "NoLED", "Scenario": "Baseline"},
        {"LightingTech": "NoLED", "Scenario": "LED retrofit"},
        {"LightingTech": "LED", "Scenario": "LED retrofit"},
    ])
    lighting = [z["Operations"]["SpaceUse"]["Lighting"] for z in zones]
    assert lighting[0] != lighting[1] == lighting[2]

    # saving the characteristics again keeps the generated Scenario field
    client.post("/api/artifacts/semantic-fields", json={"payload": {"name": "Rules", "fields": [
        {"name": "LightingTech", "options": ["NoLED", "LED"], "type": "stochastic"}
    ]}})
    assert [f["Name"] for f in workspace.read_semantic_fields()["Fields"]] == ["LightingTech", "Scenario"]


def test_one_package_can_shift_numbers_on_two_parts_whose_records_share_a_name():
    workspace = tutorial_project({})
    rules = baseline(workspace)
    infiltration = "Envelope.Infiltration"
    # both defaults are named alike (as in the starter library), so the variant names collide by name alone
    assert rules["parts"][infiltration]["default"] == rules["parts"][LIGHTING]["default"]
    workspace.path("retrofits").write_text(yaml.safe_dump({"upgrade_order": {}, "packages": [{"name": "Package 2", "measures": [
        {"part": infiltration, "number": "AirChangesPerHour", "op": "add", "value": -0.1},
        {"part": LIGHTING, "number": "PowerDensity", "op": "scale", "value": 0.5},
    ]}]}))
    assert save_rules(workspace, rules)["ok"]

    base, retrofit = resolve(workspace, [{"Scenario": "Baseline"}, {"Scenario": "Package 2"}])
    assert retrofit["Operations"]["SpaceUse"]["Lighting"]["PowerDensity"] == pytest.approx(base["Operations"]["SpaceUse"]["Lighting"]["PowerDensity"] * 0.5)
    assert retrofit["Envelope"]["Infiltration"]["AirChangesPerHour"] == pytest.approx(base["Envelope"]["Infiltration"]["AirChangesPerHour"] - 0.1)


def test_never_downgrade():
    package = {"measures": [{"field": "Windows", "to": "Double"}]}
    order = {"Windows": ["Single", "Double", "Triple"]}
    assert part_rules.apply_package({"Windows": "Single"}, package, order) == {"Windows": "Double"}
    assert part_rules.apply_package({"Windows": "Triple"}, package, order) == {"Windows": "Triple"}
    when = {"measures": [{"field": "Windows", "to": "Double", "when": {"field": "Age", "in": ["Old"]}}]}
    assert part_rules.apply_package({"Windows": "Single", "Age": "New"}, when, order)["Windows"] == "Single"


def test_importing_the_tutorial_map_resolves_to_identical_zones():
    workspace = tutorial_project(FIELDS)
    original = E2E_MAP
    db = workspace.prisma_client()
    db.connect()
    try:
        rules, notes = part_rules.infer(original, workspace.read_semantic_fields(), db)
    finally:
        db.disconnect()
    assert set(rules["parts"]) == set(part_rules.parts())
    # Region has one value, so nothing depends on it
    assert all("Region" not in r["depends_on"] for r in rules["parts"].values())
    assert save_rules(workspace, rules)["ok"]

    contexts = [dict(zip(FIELDS, combo)) for combo in itertools.product(*FIELDS.values())]
    assert resolve(workspace, contexts) == resolve(workspace, contexts, original)


@pytest.mark.skipif(not GOLDEN.exists(), reason="optional: needs globi/inputs/sbem-demo-golden locally")
def test_importing_the_golden_demo_resolves_to_identical_zones():
    root = Path(tempfile.mkdtemp()) / "golden"
    shutil.copytree(GOLDEN, root)
    workspace = ProjectWorkspace.open(root)
    original = workspace.read_generated_component_map()
    fields = {f["Name"]: f["Options"] for f in workspace.read_semantic_fields()["Fields"]}
    db = workspace.prisma_client()
    db.connect()
    try:
        rules, _ = part_rules.infer(original, workspace.read_semantic_fields(), db)
    finally:
        db.disconnect()
    assert save_rules(workspace, rules)["ok"]
    contexts = [dict(zip(fields, combo)) for combo in itertools.product(*fields.values())]
    assert resolve(workspace, contexts) == resolve(workspace, contexts, original)


def test_values_that_join_to_the_same_name_are_refused():
    workspace = tutorial_project({"A": ["x_y", "x"], "B": ["z", "y_z"]})
    rules = baseline(workspace)
    rules["parts"][LIGHTING]["depends_on"] = ["A", "B"]
    result = save_rules(workspace, rules)
    assert not result["ok"] and "same record name" in result["errors"][0]


def test_a_part_without_an_option_is_named():
    workspace = tutorial_project()
    rules = baseline(workspace)
    del rules["parts"][LIGHTING]
    result = save_rules(workspace, rules)
    assert not result["ok"] and "Lighting: choose an option" in result["errors"][0]


def test_rules_own_the_map_once_they_exist():
    workspace = tutorial_project()
    save_rules(workspace, baseline(workspace))
    response = client.post("/api/artifacts/component-map", json={"payload": E2E_MAP})
    assert response.status_code == 404 and "part-rules" in response.json()["detail"]


def test_a_new_project_starts_from_a_model_that_runs():
    root = Path(tempfile.mkdtemp()) / "new"
    response = client.post("/api/workspace/create", json={"path": str(root), "name": "New", "template": "globi-tutorial.yml"})
    assert response.status_code == 200
    assert (root / "part-rules.yaml").exists() and (root / "component-map.yaml").exists()
    assert client.post("/api/component-map/check").json()["ok"] is True


def test_a_new_project_from_the_starter_runs_too():
    root = Path(tempfile.mkdtemp()) / "starter"
    client.post("/api/workspace/create", json={"path": str(root), "name": "S", "template": "globi-starter.yml"})
    rules = yaml.safe_load((root / "part-rules.yaml").read_text())
    assert len(rules["parts"]) == 23
    assert client.post("/api/component-map/check").json()["ok"] is True
