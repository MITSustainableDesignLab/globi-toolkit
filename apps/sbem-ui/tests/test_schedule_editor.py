import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from main import app

from conftest import open_temp_workspace
from services import component_library as lib
from services.workspace import ProjectWorkspace

DAYS, MONTHS = lib.DAYS, lib.MONTHS
WEEKDAY = [0.1] * 7 + [1.0] * 11 + [0.1] * 6
WEEKEND = [0.3] * 24


@pytest.fixture
def client():
    root = Path(tempfile.mkdtemp()) / "workspace"
    db_client = ProjectWorkspace.create(root, "Test Project", init_db=True).prisma_client()
    db_client.connect()
    yield db_client
    db_client.disconnect()


def _draft(name="Office", **extra):
    return {
        "Name": name,
        "Type": "Occupancy",
        "Limit": "Fraction",
        "profiles": {"Weekday": WEEKDAY, "Weekend": WEEKEND},
        "weeks": {"All year": {d: ("Weekend" if d in ("Saturday", "Sunday") else "Weekday") for d in DAYS}},
        "months": dict.fromkeys(MONTHS, "All year"),
        **extra,
    }


def _legacy_year(client, name, day_name="Shared"):
    """A Year in the old style: its Day/Week shared under plain names."""
    if lib.get_record(client, "Day", day_name) is None:
        lib.create_record(client, "Day", {"Name": day_name, "Type": "Fraction", **{f"Hour_{h:02d}": 0.5 for h in range(24)}})
        lib.create_record(client, "Week", {"Name": day_name, **dict.fromkeys(DAYS, day_name)})
    lib.create_record(client, "Year", {"Name": name, "Type": "Lighting", **dict.fromkeys(MONTHS, day_name)})


def test_create_then_get_round_trips(client):
    saved = lib.save_schedule(client, _draft())
    assert saved == _draft()
    deep = lib.get_record(client, "Year", "Office", deep=True)
    assert deep["July"]["Saturday"]["Name"] == "Office__Weekend"
    assert deep["July"]["Monday"]["Hour_08"] == 1.0


def test_editing_a_shared_schedule_leaves_the_other_untouched(client):
    _legacy_year(client, "A")
    _legacy_year(client, "B")
    draft = lib.get_schedule(client, "A")
    assert draft["profiles"] == {"Shared": [0.5] * 24}
    draft["profiles"]["Shared"] = [0.9] * 24
    lib.save_schedule(client, draft, old_name="A")

    assert lib.get_record(client, "Year", "A", deep=True)["May"]["Monday"]["Hour_03"] == 0.9
    assert lib.get_record(client, "Year", "B", deep=True)["May"]["Monday"]["Hour_03"] == 0.5
    assert lib.get_record(client, "Day", "Shared")["Hour_03"] == 0.5


def test_rename_keeps_links_and_drops_old_records(client):
    lib.save_schedule(client, _draft())
    lib.create_record(client, "Lighting", {"Name": "L", "PowerDensity": 5, "DimmingType": "Off", "IsOn": True, "Schedule": "Office"})
    lib.save_schedule(client, _draft("Office v2"), old_name="Office")

    assert lib.get_record(client, "Lighting", "L")["Schedule"] == "Office v2"
    assert lib.get_record(client, "Year", "Office") is None
    assert lib.get_record(client, "Day", "Office__Weekday") is None
    assert lib.get_record(client, "Day", "Office v2__Weekday") is not None


def test_removing_a_profile_deletes_its_day_but_not_a_longer_named_schedules(client):
    lib.save_schedule(client, _draft())
    lib.save_schedule(client, _draft("Office__x"))  # its Days start with "Office__" too
    week = dict.fromkeys(DAYS, "Weekday")
    lib.save_schedule(client, _draft(profiles={"Weekday": WEEKDAY}, weeks={"All year": week}), old_name="Office")
    assert lib.get_record(client, "Day", "Office__Weekend") is None
    assert lib.get_record(client, "Day", "Office__x__Weekend") is not None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"profiles": {"Weekday": [1.5] * 24, "Weekend": WEEKEND}}, "upper limit"),
        ({"profiles": {"Weekday": [0.1] * 23, "Weekend": WEEKEND}}, "24 hourly values"),
        ({"months": dict.fromkeys(MONTHS[:11], "All year")}, "December"),
        ({"profiles": {"a__b": WEEKDAY}}, "can't be empty"),
    ],
)
def test_invalid_drafts_are_refused_before_writing(client, change, message):
    with pytest.raises(ValueError, match=message):
        lib.save_schedule(client, _draft(**change))
    assert lib.get_record(client, "Year", "Office") is None


def test_creating_over_an_existing_name_is_refused(client):
    lib.save_schedule(client, _draft())
    with pytest.raises(ValueError, match="already exists"):
        lib.save_schedule(client, _draft())


def test_api_round_trip_and_error():
    open_temp_workspace(init_db=True)
    api = TestClient(app)
    assert api.put("/api/component-library/schedule", json={"draft": _draft()}).status_code == 200
    assert api.get("/api/component-library/schedule/Office").json()["profiles"]["Weekend"] == WEEKEND
    bad = api.put("/api/component-library/schedule", json={"draft": _draft("X", Limit="Fraction", profiles={"Weekday": [2] * 24, "Weekend": WEEKEND})})
    assert bad.status_code == 400 and "upper limit" in bad.json()["detail"]
    assert api.get("/api/component-library/schedule/nope").status_code == 404


E2E_DB = Path(__file__).resolve().parents[3] / "examples/e2e/components-lib.db"  # globi-toolkit root
SPACE_USE = "TestRegion_Hospital_High_Baseline"


@pytest.mark.skipif(not E2E_DB.exists(), reason="needs globi-toolkit's examples/e2e")
def test_profile_preview_matches_globi_perturbation():
    """Guards the shortcut in perturb_profile: same draws as globi's perturb_space_use."""
    from epinterface.sbem.prisma.client import deep_fetcher
    from services.schedule_variability import ScheduleVariabilityConfig, perturb_space_use

    workspace = open_temp_workspace()
    shutil.copy(E2E_DB, workspace.components_db_path)
    db = workspace.prisma_client()
    db.connect()
    _, space_use = deep_fetcher.SpaceUse.get_deep_object(SPACE_USE, db=db)
    db.disconnect()

    config = {"seed": 3, "default": {"time_shift_std_h": 1.5, "hourly_noise_std": 0.1, "setpoint_offset_std_c": 1.0}}
    params = ScheduleVariabilityConfig.model_validate(config).default
    for schedule in (lambda su: su.Occupancy.Schedule, lambda su: su.Thermostat.HeatingSchedule):
        day = schedule(space_use).January.Monday
        response = TestClient(app).post(
            "/api/schedule-variability/preview-profile",
            json={"config": config, "limit": day.Type, "values": day.Values, "day_name": day.Name, "n": 3},
        )
        assert response.status_code == 200
        for i, sample in enumerate(response.json()["samples"]):
            perturbed, _ = perturb_space_use(space_use, params, 3, f"preview-{i}")
            assert sample == pytest.approx(schedule(perturbed).January.Monday.Values)
