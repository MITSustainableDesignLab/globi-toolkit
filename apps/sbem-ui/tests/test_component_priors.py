import sys
from pathlib import Path

import numpy as np
import pytest
from scipy import stats

sys.path.insert(0, str(Path(__file__).parent.parent / "server"))

from services.component_priors import (
    PriorError,
    curve,
    layers_for_u,
    quantiles,
    u_value,
    variant_record,
    variants,
)

# The tutorial library's walls (conductivities in W/mK).
CONDUCTIVITY = {"Wood siding": 0.11, "Oriented strand board": 0.13, "Wood stud": 0.19, "Plaster": 0.48,
                "Spray foam": 0.025, "Gypsum Board": 0.09, "Mineral Wool": 0.041}
PRE_1980 = [{"ConstructionMaterial": "Wood siding", "Thickness": 0.025}, {"ConstructionMaterial": "Oriented strand board", "Thickness": 0.015},
            {"ConstructionMaterial": "Wood stud", "Thickness": 0.1}, {"ConstructionMaterial": "Plaster", "Thickness": 0.015}]
POST_2000 = [{"ConstructionMaterial": "Wood stud", "Thickness": 0.15}, {"ConstructionMaterial": "Spray foam", "Thickness": 0.089},
             {"ConstructionMaterial": "Gypsum Board", "Thickness": 0.013}]


def test_variants_are_the_curves_stratified_quantiles():
    assert quantiles({"shape": "uniform", "min": 0, "max": 1}, 4) == pytest.approx([0.125, 0.375, 0.625, 0.875])
    assert quantiles({"shape": "normal", "mean": 5, "std": 1}, 3)[1] == pytest.approx(5)
    tri = quantiles({"shape": "triangular", "min": 0.6, "mode": 0.9, "max": 1.1}, 5)
    assert tri == sorted(tri) and 0.6 < tri[0] and tri[-1] < 1.1


@pytest.mark.parametrize(
    "spec",
    [
        {"shape": "lognormal", "mean": 2.0, "std": 0.5},
        {"shape": "beta", "alpha": 2, "beta": 5, "min": 0.1, "max": 0.5},
        {"shape": "histogram", "edges": [0, 1, 2, 3], "weights": [1, 2, 1]},
        {"shape": "points", "x": [0, 1, 2], "density": [0, 1, 0]},
        {"shape": "empirical", "values": [3.1, 2.4, 2.9, 3.3, 2.2]},
        {"shape": "scipy", "name": "gamma", "params": {"a": 2, "scale": 0.5}},
    ],
)
def test_every_shape_gives_n_increasing_values(spec):
    values = quantiles(spec, 7)
    assert len(values) == 7 and values == sorted(values)


def test_shapes_match_their_definitions():
    # the lognormal is set by the value's own mean and std
    assert np.mean(quantiles({"shape": "lognormal", "mean": 2.0, "std": 0.5}, 4000)) == pytest.approx(2.0, rel=1e-3)
    # a drawn triangle is the triangular curve
    drawn = quantiles({"shape": "points", "x": [0, 1, 2], "density": [0, 1, 0]}, 9)
    assert drawn == pytest.approx(quantiles({"shape": "triangular", "min": 0, "mode": 1, "max": 2}, 9), abs=1e-3)
    assert quantiles({"shape": "beta", "alpha": 2, "beta": 5, "min": 0, "max": 1}, 1)[0] == pytest.approx(stats.beta(2, 5).median())


def test_lower_and_upper_cut_the_curve():
    values = quantiles({"shape": "normal", "mean": 0.3, "std": 0.3, "lower": 0.05}, 20)
    assert min(values) > 0.05
    assert quantiles({"shape": "empirical", "values": [1, 2, 3, 100], "upper": 10}, 1) == [2.0]


@pytest.mark.parametrize(
    "spec, n, message",
    [
        ({"shape": "wobbly"}, 3, "Unknown shape"),
        ({"shape": "triangular", "min": 1, "max": 2}, 3, "needs mode"),
        ({"shape": "uniform", "min": 2, "max": 1}, 3, "above min"),
        ({"shape": "uniform", "min": 0, "max": 1}, 0, "at least 1"),
        ({"shape": "uniform", "min": 0, "max": 1, "lower": 2}, 3, "no weight"),
        ({"shape": "scipy", "name": "nope", "params": {}}, 3, "no continuous"),
    ],
)
def test_bad_curves_say_what_to_fix(spec, n, message):
    with pytest.raises(PriorError, match=message):
        quantiles(spec, n)


def _spearman(rows, a, b):
    return stats.spearmanr([r[a] for r in rows], [r[b] for r in rows]).statistic


def test_several_numbers_link_independently_together_or_by_correlation():
    fields = {"UValue": {"shape": "uniform", "min": 1, "max": 3}, "SHGF": {"shape": "uniform", "min": 0.3, "max": 0.7}}
    together = variants("Window", {"n": 5, "fields": fields, "link": "together"})
    assert [r["UValue"] for r in together] == sorted(r["UValue"] for r in together)
    assert _spearman(together, "UValue", "SHGF") == pytest.approx(1)
    assert _spearman(variants("Window", {"n": 200, "fields": fields, "link": 0.8}), "UValue", "SHGF") > 0.7
    independent = variants("Window", {"n": 200, "fields": fields})
    assert abs(_spearman(independent, "UValue", "SHGF")) < 0.2
    assert independent == variants("Window", {"n": 200, "fields": fields})  # reproducible
    # every variant keeps the curve's own values
    assert sorted(r["SHGF"] for r in independent) == pytest.approx(quantiles(fields["SHGF"], 200))


def test_a_wall_without_insulation_gets_mineral_wool_before_the_inner_finish():
    assert u_value(PRE_1980, CONDUCTIVITY) == pytest.approx(1.111, abs=1e-3)
    layers = layers_for_u(PRE_1980, CONDUCTIVITY, 0.6)
    assert [l["ConstructionMaterial"] for l in layers][-2:] == ["Mineral Wool", "Plaster"]
    assert u_value(layers, CONDUCTIVITY) == pytest.approx(0.6, abs=1e-3)
    with pytest.raises(PriorError, match="without its insulation this construction is U 1.11"):
        layers_for_u(PRE_1980, CONDUCTIVITY, 1.5)


def test_a_wall_with_insulation_resizes_it_both_ways():
    thinner = layers_for_u(POST_2000, CONDUCTIVITY, 0.4)
    thicker = layers_for_u(POST_2000, CONDUCTIVITY, 0.15)
    for layers, target in ((thinner, 0.4), (thicker, 0.15)):
        assert [l["ConstructionMaterial"] for l in layers] == ["Wood stud", "Spray foam", "Gypsum Board"]
        assert u_value(layers, CONDUCTIVITY) == pytest.approx(target, abs=1e-3)
    assert POST_2000[1]["Thickness"] == 0.089  # the original is untouched


def test_variant_records_check_the_types_limits():
    lighting = {"Name": "TestRegion_Office", "PowerDensity": 4.9, "DimmingType": "Off", "IsOn": True, "Schedule": "S"}
    assert variant_record("Lighting", lighting, {"PowerDensity": 6.2})["PowerDensity"] == 6.2
    with pytest.raises(PriorError, match="outside its limit"):
        variant_record("Lighting", lighting, {"PowerDensity": -1})
    with pytest.raises(PriorError, match="no number called 'Schedule'"):
        variant_record("Lighting", lighting, {"Schedule": 1})
    wall = {"Name": "W", "Type": "Facade", "Layers": PRE_1980}
    assert u_value(variant_record("ConstructionAssembly", wall, {"U": 0.5}, CONDUCTIVITY)["Layers"], CONDUCTIVITY) == pytest.approx(0.5, abs=1e-3)


@pytest.mark.parametrize(
    "spec",
    [
        {"shape": "normal", "mean": 0.3, "std": 0.1, "lower": 0.2},
        {"shape": "uniform", "min": 1, "max": 2},
        {"shape": "points", "x": [0, 1, 2], "density": [0, 1, 0]},
        {"shape": "empirical", "values": [1, 2, 2, 3, 3, 3, 4]},
    ],
)
def test_the_curve_to_draw_covers_where_the_weight_is(spec):
    drawn = curve(spec)
    assert len(drawn["x"]) == len(drawn["density"]) and max(drawn["density"]) > 0
    assert drawn["x"] == sorted(drawn["x"])
    if spec.get("lower") is not None:
        assert min(drawn["x"]) >= spec["lower"]
