"""ResStock's own odds: reading its tables, weighting rows by how common they are, and starting
an estimate from them (prior_trees method "resstock")."""

import numpy as np
import pytest
from fastapi.testclient import TestClient
from main import app

from services import resstock_odds
from services.prior_trees import PriorTreeError, generate_prior

client = TestClient(app)
WALL = "Insulation Wall"
WOOD = ["Wood Stud, Uninsulated", "Wood Stud, R-11", "Wood Stud, R-19"]


def test_tables_are_read_with_their_dependencies_and_weights():
    table = resstock_odds.load_table(WALL)
    assert table.dependencies == ["Location Region", "Vintage", "Geometry Wall Type"]
    assert "Wood Stud, R-11" in table.options
    assert table.weights.sum() == pytest.approx(1, abs=1e-3)
    assert np.allclose(table.shares.sum(axis=1), 1, atol=0.02)
    vintage = next(d for d in resstock_odds.dependencies(WALL) if d["name"] == "Vintage")
    assert {v["value"] for v in vintage["values"]} >= {"<1940", "1950s", "2010s"}
    assert sum(v["share"] for v in vintage["values"]) == pytest.approx(1, abs=1e-3)


def test_any_dependency_averages_rows_weighted_by_how_common_they_are():
    table = resstock_odds.load_table(WALL)
    old = [i for i, key in enumerate(table.keys) if key[1] == "<1940"]
    w = table.weights[old]
    expected = (table.shares[old] * w[:, None]).sum(axis=0) / w.sum()
    picked = np.array([expected[table.options.index(o)] for o in WOOD])

    odds, covered, matched = resstock_odds.odds(WALL, WOOD, {"Vintage": {"mode": "fixed", "value": "<1940"}})
    assert matched == len(old)
    assert covered == pytest.approx(picked.sum())
    assert [odds[o] for o in WOOD] == pytest.approx(list(picked / picked.sum()))
    # older homes are far more often uninsulated than new ones
    new, _, _ = resstock_odds.odds(WALL, WOOD, {"Vintage": {"mode": "fixed", "value": "2010s"}})
    assert odds["Wood Stud, Uninsulated"] > new["Wood Stud, Uninsulated"]


def test_a_mapped_characteristic_gives_one_row_of_odds_per_value():
    spec = {"Vintage": {"mode": "field", "field": "Age_bracket", "mapping": {"Old": ["<1940", "1940s"], "New": ["2000s", "2010s"]}}}
    rows = [{"Age_bracket": "Old"}] * 3 + [{"Age_bracket": "New"}] * 2
    result = generate_prior(target=WALL, target_values=WOOD, condition_on=[], rows=rows, method="resstock",
                            resstock={"characteristic": WALL, "dependencies": spec})
    joint = result["joint"]
    assert joint["condition_on"] == ["Age_bracket"]
    assert set(joint["table"]) == {"Old", "New"}
    assert joint["table"]["Old"]["Wood Stud, Uninsulated"] > joint["table"]["New"]["Wood Stud, Uninsulated"]
    assert sum(joint["table"]["Old"].values()) == pytest.approx(1, abs=1e-3)
    # the fallback is every vintage in ResStock's mix
    overall, _, _ = resstock_odds.odds(WALL, WOOD, {})
    assert joint["fallback"] == pytest.approx({o: round(overall[o], 4) for o in WOOD})
    assert {k["key"]: (k["count"], k["source"]) for k in result["keys"]} == {"Old": (3, "resstock"), "New": (2, "resstock")}


def test_options_resstock_never_uses_get_zero_and_a_warning():
    options = [*WOOD, "Wood Stud, R-13"]  # in ResStock's catalogue, not in its national sample
    result = generate_prior(target=WALL, target_values=options, condition_on=[], rows=[], method="resstock",
                            resstock={"characteristic": WALL, "dependencies": {}})
    sampler = result["prior"]["sampler"]
    assert dict(zip(sampler["values"], sampler["weights"], strict=True))["Wood Stud, R-13"] == 0
    assert any("never uses Wood Stud, R-13" in w for w in result["warnings"])


def test_fixed_values_no_home_has_are_refused():
    spec = {"Vintage": {"mode": "fixed", "value": "1700s"}}
    with pytest.raises(PriorTreeError, match="No ResStock homes match"):
        generate_prior(target=WALL, target_values=WOOD, condition_on=[], rows=[], method="resstock",
                       resstock={"characteristic": WALL, "dependencies": spec})
    with pytest.raises(PriorTreeError, match="Not a dependency"):
        generate_prior(target=WALL, target_values=WOOD, condition_on=[], rows=[], method="resstock",
                       resstock={"characteristic": WALL, "dependencies": {"Colour": {"mode": "any"}}})


def test_dependencies_endpoint():
    data = client.get("/api/prior-trees/resstock-dependencies", params={"characteristic": WALL}).json()
    assert data["available"] is True
    assert [d["name"] for d in data["dependencies"]] == ["Location Region", "Vintage", "Geometry Wall Type"]
    assert client.get("/api/prior-trees/resstock-dependencies", params={"characteristic": "Typology"}).json() == {
        "available": False, "dependencies": []}


def test_state_options_narrow_to_the_states_regions():
    tx = resstock_odds.options_in_state("Water Heater Efficiency", "TX")
    all_options = resstock_odds.load_table("Water Heater Efficiency").options
    assert tx and len(tx) < len(all_options)  # some options no Texas home has are left out
    assert abs(sum(tx.values()) - 1) < 0.01
    assert list(tx) == sorted(tx, key=lambda option: -tx[option])  # commonest first
    # no geographic dependency: nothing to narrow
    assert resstock_odds.options_in_state("Insulation Roof", "TX") is None
    regions = resstock_odds.state_regions()["TX"]
    assert regions["Location Region"] == ["CR09"]
    assert "2A" in regions["ASHRAE IECC Climate Zone 2004"]


def test_region_values_list_their_states_and_a_state_narrows_to_its_regions():
    region = next(d for d in resstock_odds.dependencies(WALL) if d["name"] == "Location Region")
    assert region["geographic"] is True
    cr09 = next(v for v in region["values"] if v["value"] == "CR09")
    assert "TX" in cr09["states"]
    vintage = next(d for d in resstock_odds.dependencies(WALL) if d["name"] == "Vintage")
    assert vintage["geographic"] is False and "states" not in vintage["values"][0]

    # TX spans several climate zones: "state" mode keeps all of them, same as ticking them as fixed values
    zone = "ASHRAE IECC Climate Zone 2004"
    options = resstock_odds.load_table("Infiltration").options
    by_state = resstock_odds.odds("Infiltration", options, {zone: {"mode": "state", "state": "TX"}})
    by_map = resstock_odds.odds(
        "Infiltration",
        options,
        {zone: {"mode": "field", "field": "Where", "mapping": {"here": resstock_odds.state_regions()["TX"][zone]}}},
        context={"Where": "here"},
    )
    assert by_state[2] == by_map[2] > 0
    assert by_state[0] == pytest.approx(by_map[0])

    with pytest.raises(resstock_odds.ResStockOddsError):
        resstock_odds.odds(WALL, WOOD, {"Location Region": {"mode": "state", "state": "XX"}})
