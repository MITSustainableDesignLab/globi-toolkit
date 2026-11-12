"""Willingness to pay in sbem-ui: the model file, area tables, and the check against the buildings."""

import shutil

import yaml
from test_globi_files import client, save_stock, template_project


def wtp():
    return client.get("/api/wtp").json()


def test_the_worked_example_says_what_these_buildings_still_need():
    workspace = template_project()
    save_stock(workspace)

    status = wtp()
    assert status["config"]["residential"]["model"]["coefficients"]["upfront_cost"] == -0.058
    assert [t["file"] for t in status["tables"]] == ["census-ma.csv"]
    typology = next(c for c in status["buildingColumns"] if c["name"] == "Typology")
    assert "Residential" in typology["values"]
    problems = status["check"]["problems"]
    assert not status["check"]["ready"]
    assert "input year_built: no number given for 1980_to_2000, Post_2000, Pre_1980." in problems
    assert "The buildings have no tract_id column." in problems


def test_a_region_maps_its_own_areas_and_becomes_ready():
    workspace = template_project()
    save_stock(workspace)
    csv = b"region,concern,low,high\nTestRegion,2.5,30,70\n"
    upload = client.post("/api/wtp/tables", files={"file": ("regions.csv", csv, "text/csv")})
    assert {"file": "regions.csv", "columns": ["region", "concern", "low", "high"]} in upload.json()["tables"]

    config = wtp()["config"]
    config["areas"] = {
        "regions": {
            "file": "regions.csv",
            "join": {"building": "Region", "table": "region"},
            "distributions": {"income_k": {20: ["low"], 120: ["high"]}},
        }
    }
    inputs = config["inputs"]
    inputs["year_built"]["map"] = {"Post_2000": 1, "1980_to_2000": 2, "Pre_1980": 3.5}
    inputs["concern"] = {"source": "area", "table": "regions", "column": "concern"}
    inputs["income"] = {"source": "area", "table": "regions", "sample": "income_k", "bins": [25, 50, 75, 100, 150]}
    for name in ("education", "bedrooms", "residents"):  # this region's survey has no such data
        del inputs[name]
        del config["residential"]["model"]["coefficients"][name]

    check = client.put("/api/wtp", json=config).json()["check"]
    assert check == {"ready": True, "problems": [], "residential": check["residential"], "commercial": check["commercial"]}
    assert check["residential"] + check["commercial"] == 100
    saved = yaml.safe_load((workspace.root / "use-cases" / "wtp.yml").read_text())
    assert saved["areas"]["regions"]["join"] == {"building": "Region", "table": "region"}


def test_an_incomplete_form_is_saved_and_its_blanks_are_listed():
    template_project()
    blank = {"inputs": {}, "residential": {"typologies": [], "model": {"coefficients": {}}}, "commercial": {"typologies": []}}
    check = client.put("/api/wtp", json=blank).json()["check"]
    assert "residential.model.intercept: fill this in." in check["problems"]
    assert "commercial.years: fill this in." in check["problems"]


def test_tables_must_be_csv_and_the_example_can_be_reloaded():
    workspace = template_project()
    assert client.post("/api/wtp/tables", files={"file": ("x.xlsx", b"PK", "application/octet-stream")}).status_code == 400
    shutil.rmtree(workspace.root / "use-cases")
    assert wtp()["config"] is None
    assert client.post("/api/wtp/example").json()["config"]["residential"]["typologies"] == ["Residential"]
