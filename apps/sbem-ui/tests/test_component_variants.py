"""Component priors compiled into variants: records, the variant-index field, resolution."""

import pytest
import yaml
from services import component_library as lib
from services import part_rules
from services.component_priors import quantiles
from test_part_rules import (
    LIGHTING,
    baseline,
    client,
    names,
    resolve,
    save_rules,
    tutorial_project,
)

WALLS = "Envelope.Assemblies.FacadeAssembly"
OLD_WALL = "TestRegion_Residential_Baseline_Pre_1980_Facade"
NEW_WALL = "TestRegion_Residential_Baseline_Post_2000_Facade"
CURVE = {"shape": "triangular", "min": 0.6, "mode": 0.9, "max": 1.1}


def write_priors(workspace, components):
    workspace.path("component_priors").write_text(yaml.safe_dump({"components": components}))


def facade_u(zone) -> float:
    layers = zone["Envelope"]["Assemblies"]["FacadeAssembly"]["Layers"]
    return 1 / sum(l["Thickness"] / l["ConstructionMaterial"]["Conductivity"] for l in layers)


def generated(workspace, component_type) -> list[str]:
    db = workspace.prisma_client()
    db.connect()
    try:
        return sorted(r["Name"] for r in lib.list_records(db, component_type) if r["Name"].startswith("auto_"))
    finally:
        db.disconnect()


def walls_by_age(workspace):
    rules = baseline(workspace)
    rules["parts"][WALLS] = {"depends_on": ["Age"], "default": OLD_WALL, "cells": {"New": NEW_WALL}}
    return rules


def test_an_uncertain_wall_becomes_n_variants_that_every_building_resolves_to():
    workspace = tutorial_project({"Age": ["Old", "New"]})
    rules = walls_by_age(workspace)
    write_priors(workspace, {"ConstructionAssembly": {OLD_WALL: {"n": 4, "fields": {"U": CURVE}}}})
    result = save_rules(workspace, rules)
    assert result["ok"], result["errors"]
    assert result["variants"] == {"FacadeAssembly_variant": 4}

    field = next(f for f in workspace.read_semantic_fields()["Fields"] if f["Name"] == "FacadeAssembly_variant")
    assert field["Options"] == ["1", "2", "3", "4"] and field["Description"] == part_rules.VARIANT_NOTE
    assert workspace.read_generated_component_map()["Envelope"]["selector"]["source_fields"] == ["Age", "FacadeAssembly_variant"]

    old = resolve(workspace, [{"Age": "Old", "FacadeAssembly_variant": str(k)} for k in range(1, 5)])
    assert [facade_u(z) for z in old] == pytest.approx(quantiles(CURVE, 4), abs=1e-3)
    new = resolve(workspace, [{"Age": "New", "FacadeAssembly_variant": k} for k in ("1", "4")])
    assert new[0] == new[1] and facade_u(new[0]) == pytest.approx(0.2127, abs=1e-3)  # certain: untouched

    variants = [f"auto_{OLD_WALL}__{k}" for k in range(1, 5)]
    assert generated(workspace, "ConstructionAssembly") == variants
    assert save_rules(workspace, rules)["ok"]  # recompiling replaces, never duplicates
    assert generated(workspace, "ConstructionAssembly") == variants

    write_priors(workspace, {})
    assert save_rules(workspace, rules)["ok"]
    assert generated(workspace, "ConstructionAssembly") == []
    assert "FacadeAssembly_variant" not in [f["Name"] for f in workspace.read_semantic_fields()["Fields"]]
    assert workspace.read_generated_component_map()["Envelope"]["selector"]["source_fields"] == ["Age"]


def test_any_number_varies_and_different_ns_keep_equal_weights():
    workspace = tutorial_project({"Typology": ["Office", "School"]})
    rules = baseline(workspace)
    office = rules["parts"][LIGHTING]["default"]
    school = next(n for n in names(workspace, "Lighting") if n != office)
    rules["parts"][LIGHTING] = {"depends_on": ["Typology"], "default": office, "cells": {"School": school}}
    office_curve = {"shape": "uniform", "min": 4, "max": 6}
    school_curve = {"shape": "normal", "mean": 2.2, "std": 0.3, "lower": 0}
    write_priors(workspace, {"Lighting": {
        office: {"n": 2, "fields": {"PowerDensity": office_curve}},
        school: {"n": 3, "fields": {"PowerDensity": school_curve}},
    }})
    result = save_rules(workspace, rules)
    assert result["ok"], result["errors"]
    assert result["variants"] == {"Lighting_variant": 6}  # lcm(2, 3)

    def lpd(typology):
        zones = resolve(workspace, [{"Typology": typology, "Lighting_variant": str(k)} for k in range(1, 7)])
        return [z["Operations"]["SpaceUse"]["Lighting"]["PowerDensity"] for z in zones]

    # index k picks variant ceil(k·n/6): each variant gets an equal share of the six
    a, b = quantiles(office_curve, 2)
    assert lpd("Office") == pytest.approx([a, a, a, b, b, b])
    x, y, z = quantiles(school_curve, 3)
    assert lpd("School") == pytest.approx([x, x, y, y, z, z])


def test_a_curve_a_wall_cant_reach_is_refused_with_the_reason():
    workspace = tutorial_project({"Age": ["Old", "New"]})
    write_priors(workspace, {"ConstructionAssembly": {OLD_WALL: {"n": 3, "fields": {"U": {"shape": "uniform", "min": 0.9, "max": 1.6}}}}})
    result = save_rules(workspace, walls_by_age(workspace))
    assert not result["ok"]
    assert "can't be reached: without its insulation this construction is U 1.11" in result["errors"][0]


def test_a_characteristic_named_like_a_variant_field_is_refused():
    workspace = tutorial_project({"Age": ["Old", "New"], "FacadeAssembly_variant": ["a"]})
    write_priors(workspace, {"ConstructionAssembly": {OLD_WALL: {"n": 2, "fields": {"U": CURVE}}}})
    result = save_rules(workspace, walls_by_age(workspace))
    assert not result["ok"] and "FacadeAssembly_variant" in result["errors"][0]


def test_the_preview_draws_each_curve_and_says_why_a_variant_cant_be_built():
    tutorial_project()
    body = {"component_type": "ConstructionAssembly", "name": OLD_WALL, "prior": {"n": 3, "fields": {"U": CURVE}}}
    ok = client.post("/api/component-priors/preview", json=body).json()
    assert ok["errors"] == [] and ok["fields"]["U"]["values"] == pytest.approx(quantiles(CURVE, 3))
    assert len(ok["variants"]) == 3 and len(ok["fields"]["U"]["x"]) == len(ok["fields"]["U"]["density"])

    body["prior"]["fields"]["U"] = {"shape": "uniform", "min": 0.9, "max": 1.6}
    too_high = client.post("/api/component-priors/preview", json=body).json()
    assert [e.split(":")[0] for e in too_high["errors"]] == ["Variant 2", "Variant 3"]

    body["prior"]["fields"]["U"] = {"shape": "triangular", "min": 1}
    assert client.post("/api/component-priors/preview", json=body).json()["errors"] == ["U: A triangular curve needs mode, max."]


WINDOW = "Envelope.Window"
WINDOW_CURVE = {"shape": "uniform", "min": 1.0, "max": 3.0}


def linked_walls_and_windows(workspace, link):
    rules = walls_by_age(workspace)
    window = rules["parts"][WINDOW]["default"]
    workspace.path("component_priors").write_text(yaml.safe_dump({
        "components": {
            "ConstructionAssembly": {OLD_WALL: {"n": 4, "fields": {"U": CURVE}}},
            "GlazingConstructionSimple": {window: {"n": 2, "fields": {"UValue": WINDOW_CURVE}}},
        },
        "links": [{"parts": [WALLS, WINDOW], "link": link}],
    }))
    return rules


def test_parts_that_move_together_share_one_index_and_stay_in_step():
    workspace = tutorial_project({"Age": ["Old", "New"]})
    result = save_rules(workspace, linked_walls_and_windows(workspace, "together"))
    assert result["ok"], result["errors"]
    assert result["variants"] == {"FacadeAssembly_Window_variant": 4}  # lcm(4, 2), one field
    assert result["groups"]["Envelope"] == {"fields": ["Age", "FacadeAssembly_Window_variant"], "combinations": 8}

    zones = resolve(workspace, [{"Age": "Old", "FacadeAssembly_Window_variant": str(k)} for k in range(1, 5)])
    assert [facade_u(z) for z in zones] == pytest.approx(quantiles(CURVE, 4), abs=1e-3)
    low, high = quantiles(WINDOW_CURVE, 2)
    assert [z["Envelope"]["Window"]["UValue"] for z in zones] == pytest.approx([low, low, high, high])


def test_correlated_parts_keep_their_own_index():
    workspace = tutorial_project({"Age": ["Old", "New"]})
    result = save_rules(workspace, linked_walls_and_windows(workspace, 0.8))
    assert result["ok"], result["errors"]
    assert result["variants"] == {"FacadeAssembly_variant": 4, "Window_variant": 2}
    priors = yaml.safe_load(workspace.path("component_priors").read_text())
    assert part_rules.correlated(priors, result["variants"]) == [(["FacadeAssembly_variant", "Window_variant"], 0.8)]


@pytest.mark.parametrize(
    "links, message",
    [
        ([{"parts": [WALLS, WINDOW], "link": "together"}, {"parts": [WINDOW, LIGHTING], "link": 0.5}], "is in two links"),
        ([{"parts": [WALLS, WINDOW, LIGHTING], "link": -0.9}], "has to be above -0.50"),
        ([{"parts": [WALLS, WINDOW], "link": 1.5}], "between -1 and 1"),
    ],
)
def test_links_that_cant_hold_are_refused(links, message):
    workspace = tutorial_project({"Age": ["Old", "New"]})
    rules = linked_walls_and_windows(workspace, "together")
    priors = yaml.safe_load(workspace.path("component_priors").read_text())
    workspace.path("component_priors").write_text(yaml.safe_dump({**priors, "links": links}))
    result = save_rules(workspace, rules)
    assert not result["ok"] and message in result["errors"][0]


# -- retrofits on parts -------------------------------------------------------------------


def write_packages(workspace, *packages):
    workspace.path("retrofits").write_text(yaml.safe_dump({"packages": list(packages)}))


def walls(workspace, contexts):
    return [facade_u(z) for z in resolve(workspace, contexts)]


def test_a_shift_moves_every_variant_and_keeps_each_buildings_draw():
    workspace = tutorial_project({"Age": ["Old", "New"]})
    write_priors(workspace, {"ConstructionAssembly": {OLD_WALL: {"n": 4, "fields": {"U": CURVE}}}})
    write_packages(workspace, {"name": "Insulate walls", "measures": [{"part": WALLS, "number": "U", "op": "scale", "value": 0.5}]})
    result = save_rules(workspace, walls_by_age(workspace))
    assert result["ok"], result["errors"]
    assert result["groups"]["Envelope"]["fields"] == ["Age", "FacadeAssembly_variant", "Scenario"]

    before = walls(workspace, [{"Age": "Old", "FacadeAssembly_variant": str(k), "Scenario": "Baseline"} for k in range(1, 5)])
    after = walls(workspace, [{"Age": "Old", "FacadeAssembly_variant": str(k), "Scenario": "Insulate walls"} for k in range(1, 5)])
    assert before == pytest.approx(quantiles(CURVE, 4), abs=1e-3)
    assert after == pytest.approx([u / 2 for u in before], abs=1e-3)  # variant k stays variant k
    new = walls(workspace, [{"Age": "New", "FacadeAssembly_variant": "1", "Scenario": s} for s in ("Baseline", "Insulate walls")])
    assert new == pytest.approx([0.2127, 0.2127 / 2], abs=1e-3)  # a certain wall shifts too
    assert f"auto_{OLD_WALL}__1__Insulate-walls" in generated(workspace, "ConstructionAssembly")


def test_at_most_never_makes_a_part_worse_and_when_limits_who_gets_it():
    workspace = tutorial_project({"Age": ["Old", "New"]})
    write_priors(workspace, {"ConstructionAssembly": {OLD_WALL: {"n": 4, "fields": {"U": CURVE}}}})
    cap = {"part": WALLS, "number": "U", "op": "at_most", "value": 0.8, "when": [{"field": "Age", "in": ["Old"]}]}
    write_packages(workspace, {"name": "To code", "measures": [cap]})
    result = save_rules(workspace, walls_by_age(workspace))
    assert result["ok"], result["errors"]
    after = walls(workspace, [{"Age": "Old", "FacadeAssembly_variant": str(k), "Scenario": "To code"} for k in range(1, 5)])
    assert after == pytest.approx([min(u, 0.8) for u in quantiles(CURVE, 4)], abs=1e-3)
    new = walls(workspace, [{"Age": "New", "FacadeAssembly_variant": "1", "Scenario": "To code"}])
    assert new == pytest.approx([0.2127], abs=1e-3)  # not Old: untouched


def test_a_replace_swaps_in_a_new_curve_at_the_same_rank():
    workspace = tutorial_project({"Age": ["Old", "New"]})
    retrofit = {"shape": "uniform", "min": 0.15, "max": 0.3}
    write_priors(workspace, {"ConstructionAssembly": {
        OLD_WALL: {"n": 4, "fields": {"U": CURVE}},
        NEW_WALL: {"n": 2, "fields": {"U": retrofit}},
    }})
    write_packages(workspace, {"name": "New walls", "measures": [{"part": WALLS, "to": NEW_WALL, "when": [{"field": "Age", "in": ["Old"]}]}]})
    rules = walls_by_age(workspace)
    rules["parts"][WALLS] = {"depends_on": ["Age"], "default": OLD_WALL, "cells": {"New": OLD_WALL}}  # NEW_WALL only by retrofit
    result = save_rules(workspace, rules)
    assert result["ok"], result["errors"]
    assert result["variants"] == {"FacadeAssembly_variant": 4}  # lcm(4, 2): the replacement counts
    after = walls(workspace, [{"Age": "Old", "FacadeAssembly_variant": str(k), "Scenario": "New walls"} for k in range(1, 5)])
    low, high = quantiles(retrofit, 2)
    assert after == pytest.approx([low, low, high, high], abs=1e-3)


@pytest.mark.parametrize(
    "measure, message",
    [
        ({"part": "Envelope.Nope", "to": "x"}, "isn't a model part"),
        ({"part": WALLS, "to": "No such wall"}, "isn't a ConstructionAssembly record"),
        ({"part": WALLS, "number": "U", "op": "double", "value": 2}, "how to change it"),
        ({"part": LIGHTING, "number": "Colour", "op": "add", "value": 1}, "has no number called 'Colour'"),
        ({"part": WALLS, "number": "U", "op": "scale", "value": 3}, "can't be reached"),
    ],
)
def test_part_measures_that_cant_be_built_are_refused(measure, message):
    workspace = tutorial_project({"Age": ["Old", "New"]})
    write_packages(workspace, {"name": "P", "measures": [measure]})
    result = save_rules(workspace, walls_by_age(workspace))
    assert not result["ok"] and message in result["errors"][0], result["errors"]
