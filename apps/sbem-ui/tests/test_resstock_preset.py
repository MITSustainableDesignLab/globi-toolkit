"""ResStock preset: the converter's records, and a project from the resstock template compiling with them."""

import importlib.util
import tempfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from main import app
from services import component_library as lib
from services import part_rules
from services.template_loader import TEMPLATES_DIR
from services.workspace import ProjectWorkspace

client = TestClient(app)
SCRIPT = Path(__file__).parent.parent / "scripts" / "convert_resstock.py"
spec = importlib.util.spec_from_file_location("convert_resstock", SCRIPT)
convert = importlib.util.module_from_spec(spec)
spec.loader.exec_module(convert)

LOOKUP = (
    "Parameter Name\tOption Name\tMeasure Dir\tMeasure Arg 1\n"
    'Insulation Wall\t"Wood Stud, R-13"\tResStockArguments\twall_assembly_r=10\n'
    "Insulation Wall\tVoid\n"
    'HVAC Heating Efficiency\t"Fuel Furnace, 80% AFUE"\tResStockArguments\theating_system_type=Furnace\theating_system_heating_efficiency=0.8\n'
    'HVAC Heating Efficiency\t"ASHP, SEER 14, 8.2 HSPF"\tResStockArguments\theating_system_type=none\theat_pump_type=air-to-air\theat_pump_heating_efficiency_type=HSPF\theat_pump_heating_efficiency=8.2\n'
    "Lighting\t100% LED\tResStockArguments\tlighting_present=true\tlighting_interior_fraction_cfl=0\tlighting_interior_fraction_lfl=0\tlighting_interior_fraction_led=1\n"
    "Lighting\tNone\tResStockArguments\tlighting_present=false\tlighting_interior_fraction_cfl=0\tlighting_interior_fraction_lfl=0\tlighting_interior_fraction_led=0\n"
    "Plug Loads\t110%\tResStockArguments\tmisc_plug_loads_other_usage_multiplier=1.1\n"
    "Occupants\t4\tResStockArguments\tgeometry_unit_num_occupants=4\n"
    "Heating Setpoint\t68F\tResStockArguments\thvac_control_heating_weekday_setpoint_temp=68\n"
)


def test_converter_records_carry_resstock_values(tmp_path):
    (tmp_path / "options_lookup.tsv").write_text(LOOKUP)
    lookup = convert.read_lookup(tmp_path / "options_lookup.tsv")
    workspace = ProjectWorkspace.create(tmp_path / "ws", "R", template_dir=TEMPLATES_DIR / "projects" / "globi-starter")
    db = workspace.prisma_client()
    db.connect()
    try:
        characteristics = {c["name"]: c for c in convert.write_records(db, lookup)}
        wall = characteristics["Insulation Wall"]
        assert wall["skipped"] == {"Void": "not a modelled option"}
        deep = lib.get_record(db, "ConstructionAssembly", wall["options"]["Wood Stud, R-13"], deep=True)
        u = lib._component_type_map()["ConstructionAssembly"].model_validate(deep).u_value
        assert u == pytest.approx(1 / (10 * 0.1761), abs=0.01)

        heating = characteristics["HVAC Heating Efficiency"]["options"]
        furnace = lib.get_record(db, "ThermalSystem", heating["Fuel Furnace, 80% AFUE"])
        assert (furnace["Fuel"], furnace["SystemCOP"]) == ("NaturalGas", 0.8)
        heat_pump = lib.get_record(db, "ThermalSystem", heating["ASHP, SEER 14, 8.2 HSPF"])
        assert (heat_pump["Fuel"], heat_pump["SystemCOP"]) == ("Electricity", pytest.approx(8.2 * 0.293, abs=0.001))

        name = characteristics["Heating Setpoint"]["options"]["68F"]
        thermostat = lib.get_record(db, "Thermostat", name, deep=True)
        assert thermostat["HeatingSetpoint"] == 20.0
        assert thermostat["HeatingSchedule"]["January"]["Monday"]["Hour_12"] == 20.0
        assert characteristics["Lighting"]["skipped"] == {"None": "no lighting"}
        led = lib.get_record(db, "Lighting", characteristics["Lighting"]["options"]["100% LED"])
        assert led["PowerDensity"] == pytest.approx(6.5 * 15 / 90, abs=0.01)  # starter 6.5 W/m2 taken as all-incandescent
        equipment = lib.get_record(db, "Equipment", characteristics["Plug Loads"]["options"]["110%"])
        assert equipment["PowerDensity"] == pytest.approx(3.8 * 1.1, abs=0.01)
        occupancy = lib.get_record(db, "Occupancy", characteristics["Occupants"]["options"]["4"])
        assert occupancy["PeopleDensity"] == pytest.approx(4 / convert.REFERENCE_FLOOR_AREA_M2, abs=0.001)
        assert characteristics["Infiltration"]["options"] == {}  # not in this lookup
    finally:
        db.disconnect()


def test_a_resstock_project_compiles_a_characteristic_from_the_preset():
    root = Path(tempfile.mkdtemp()) / "workspace"
    response = client.post("/api/workspace/create", json={"path": str(root), "name": "RS", "template": "resstock.yml"})
    assert response.status_code == 200, response.text
    preset = client.get("/api/workspace/stock-preset").json()["preset"]
    wall = next(c for c in preset["characteristics"] if c["name"] == "Insulation Wall")
    values = ["Wood Stud, Uninsulated", "Wood Stud, R-13", "CMU, 6-in Hollow, R-7"]

    # what AddCharacteristic does: the field, then the rule of each part it decides
    workspace = app.state.workspace
    workspace.write_semantic_fields({"Name": "RS", "Fields": [{"Name": wall["name"], "Options": values}]})
    rules = part_rules.read_rules(workspace)
    for part in wall["parts"]:
        rules["parts"][part] = {
            "depends_on": [wall["name"]],
            "default": wall["options"][values[0]],
            "cells": {v: wall["options"][v] for v in values[1:]},
        }
    workspace.path("part_rules").write_text(yaml.safe_dump(rules, sort_keys=False))
    result = part_rules.compile_project(workspace)
    assert result["ok"], result["errors"]
    assert result["groups"]["Envelope"]["combinations"] == len(values)

    check = client.post("/api/component-map/check").json()
    assert check["ok"], check
