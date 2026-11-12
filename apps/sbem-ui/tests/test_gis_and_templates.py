import sys
from pathlib import Path

SERVER_DIR = Path(__file__).parent.parent / "server"
sys.path.insert(0, str(SERVER_DIR))

from services.gis_parser import parse_csv_content, validate_dataset
from services.template_loader import resolve_template_path


def test_parse_csv_content_infers_columns():
    content = b"building_id,num_floors,building_type\nb1,2,SFH\nb2,3,MFH\n"
    parsed = parse_csv_content(content)

    assert parsed["row_count"] == 2
    assert len(parsed["columns"]) == 3
    assert parsed["columns"][0]["name"] == "building_id"


def test_validate_dataset_flags_missing_required_columns():
    dataset = {
        "row_count": 1,
        "columns": [{"name": "building_id", "null_count": 0}],
    }
    result = validate_dataset(
        dataset,
        column_mappings={"building_id": "building_id", "num_floors": "missing_col"},
        validate_columns=["building_id"],
    )

    assert result["valid"] is False
    assert "missing_col" in result["missing_columns"]


def test_validate_dataset_ignores_unselected_null_columns():
    dataset = {
        "row_count": 2,
        "columns": [
            {"name": "building_id", "null_count": 0},
            {"name": "CT2020", "null_count": 2},
        ],
    }
    result = validate_dataset(
        dataset,
        column_mappings={"building_id": "building_id", "num_floors": "building_id"},
        validate_columns=["building_id"],
    )

    assert result["valid"] is True


def test_suggest_column_mappings_matches_assessor_area():
    from services.globi_columns import suggest_column_mappings

    suggestions = suggest_column_mappings(["OID", "sqft_assessor", "Story", "CT2020"])

    assert suggestions.get("building_id") == "OID"
    assert suggestions.get("footprint_area") == "sqft_assessor"
    assert suggestions.get("num_floors") == "Story"
    assert "CT2020" not in suggestions.values()


def test_drop_incomplete_rows_removes_rows_with_missing_values():
    from services.gis_parser import drop_incomplete_rows

    dataset = {
        "row_count": 3,
        "rows": [
            {"building_id": "b1", "sqft_assessor": "1000"},
            {"building_id": "", "sqft_assessor": "900"},
            {"building_id": "b3", "sqft_assessor": ""},
        ],
        "columns": [],
        "column_mappings": {"building_id": "building_id", "footprint_area": "sqft_assessor"},
        "validated_columns": ["building_id", "sqft_assessor"],
        "source_format": "csv",
    }
    cleaned = drop_incomplete_rows(dataset, ["building_id", "sqft_assessor"])

    assert cleaned["row_count"] == 1
    assert cleaned["rows"][0]["building_id"] == "b1"
    assert cleaned["dropped_row_count"] == 2


def test_build_cleaned_geojson_keeps_geometry_and_filtered_properties():
    from services.gis_parser import build_cleaned_geojson, drop_incomplete_rows

    dataset = {
        "row_count": 1,
        "rows": [{"building_id": "b1", "CT2020": "x", "sqft_assessor": "1000"}],
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [1, 2]},
                "properties": {"building_id": "b1", "CT2020": "x", "sqft_assessor": "1000"},
            }
        ],
        "column_mappings": {"building_id": "building_id", "footprint_area": "sqft_assessor"},
        "validated_columns": ["building_id", "sqft_assessor"],
        "source_format": "geojson",
    }
    cleaned = drop_incomplete_rows(dataset, ["building_id", "sqft_assessor"])
    geojson = build_cleaned_geojson(cleaned)

    assert len(geojson["features"]) == 1
    assert geojson["features"][0]["geometry"]["coordinates"] == [1, 2]
    assert set(geojson["features"][0]["properties"].keys()) == {"building_id", "sqft_assessor"}


def test_resolve_template_path_blocks_traversal():
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        resolve_template_path("semantic_fields", "../component_maps/residential.yml")


def test_basement_and_attic_rules_come_from_globi():
    from typing import get_args

    from globi.type_utils.literals import BasementAtticOccupationConditioningStatus
    from services.globi_columns import GLOBI_COLUMN_ROLES

    roles = {role["key"]: role for role in GLOBI_COLUMN_ROLES}
    expected = list(get_args(BasementAtticOccupationConditioningStatus))
    assert roles["basement"]["allowed_values"] == expected
    assert roles["attic"]["allowed_values"] == expected
    assert roles["wwr"]["rule"] == "Number from 0 to 1."


def test_validate_flags_values_globi_drops_but_not_blanks():
    content = b"id,floors,basement\nb1,2,Y\nb2,2,N\nb3,2,none\nb4,2,\nb5,0,none\n"
    dataset = parse_csv_content(content)
    result = validate_dataset(
        dataset,
        column_mappings={"building_id": "id", "num_floors": "floors", "basement": "basement"},
        validate_columns=["id"],
    )

    assert result["valid"] is False
    assert result["invalid_row_count"] == 3  # Y, N, and 0 floors; the blank basement is filled by GloBI
    assert any("Basement status: 2 rows" in issue and "N, Y" in issue for issue in result["issues"])


def test_value_mappings_rewrite_rows_and_drop_removes_the_rest():
    from services.gis_parser import (
        apply_value_mappings,
        drop_incomplete_rows,
        rule_sources,
    )

    mappings = {"building_id": "id", "num_floors": "floors", "basement": "basement"}
    dataset = parse_csv_content(b"id,floors,basement\nb1,2,Y\nb2,2,N\nb3,2,maybe\n")
    apply_value_mappings(dataset, mappings, {"basement": {"Y": "occupied_conditioned", "N": "none"}})

    assert [row["basement"] for row in dataset["rows"]] == ["occupied_conditioned", "none", "maybe"]
    cleaned = drop_incomplete_rows(dataset, ["id"], rule_sources(mappings))
    assert [row["id"] for row in cleaned["rows"]] == ["b1", "b2"]
    assert validate_dataset(cleaned, column_mappings=mappings, validate_columns=["id"])["valid"] is True
