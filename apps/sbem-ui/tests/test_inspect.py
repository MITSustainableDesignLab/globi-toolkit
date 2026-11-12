import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "server"))

from services.inspect import inspect_building


def test_record_names_match_globi_with_prefix_and_prefix_only_selectors():
    component_map = {
        "Envelope": {"selector": {"source_fields": ["Age_bracket"], "prefix": "auto"}},
        "Operations": {"DHW": {"selector": {"source_fields": [], "prefix": "auto"}}},
        "Lighting": {"selector": {"source_fields": ["Typology"], "suffix": "LPD"}},
    }
    rows = [{"id": "1", "Age_bracket": "Pre_1980", "Typology": "Office"}]
    trace = inspect_building(rows, "id", "1", [], {}, component_map, n=1)
    names = {part["path"]: part["records"][0]["name"] for part in trace["parts"]}
    assert names == {"Envelope": "auto_Pre_1980", "Operations.DHW": "auto_", "Lighting": "Office_LPD"}
