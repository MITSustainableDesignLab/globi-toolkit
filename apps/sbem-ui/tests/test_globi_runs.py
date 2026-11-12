"""Model Execution's runs: reading globi's result files, the sample's run folder, and the jobs
(globi itself is faked at run_step, so no EnergyPlus or Hatchet is needed)."""

import json
import time
from pathlib import Path

import pandas as pd
import pytest
import yaml
from services import globi_runs
from services.globi_runs import AREA, GlobiRunError, prepare_sample, summarize_stock
from test_globi_files import (
    client,
    generate,
    save_stock,
    saved_project,
    template_project,
)


def energy_frame(rows: list[tuple[str, str, float, dict[str, float]]]) -> pd.DataFrame:
    """globi's EnergyAndPeak layout: one row per building, monthly columns; rows are
    (building_id, Typology, conditioned area, kWh/m² per end use for the year)."""
    meters, months = ["Heating", "Lighting"], ["Jan", "Feb"]
    columns = pd.MultiIndex.from_tuples(
        [(m, a, meter, month) for m, a in (("Energy", "End Uses"), ("Peak", "Utilities")) for meter in meters for month in months],
        names=["Measurement", "Aggregation", "Meter", "Month"],
    )
    data = [[uses[meter] / len(months) for _, _, meter, _ in columns] for _, _, _, uses in rows]
    index = pd.MultiIndex.from_tuples(
        [(b, t, a) for b, t, a, _ in rows], names=["building_id", "feature.semantic.Typology", AREA]
    )
    return pd.DataFrame(data, index=index, columns=columns)


def wait(job_id: str) -> dict:
    for _ in range(200):
        job = client.get(f"/api/globi-files/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def stock_project():
    workspace = saved_project()
    save_stock(workspace)
    assert generate().status_code == 200
    return workspace


def test_stock_summary_is_floor_area_weighted(tmp_path):
    path = tmp_path / "EnergyAndPeak.pq"
    energy_frame([("a", "Office", 100.0, {"Heating": 10, "Lighting": 4}), ("b", "Hotel", 300.0, {"Heating": 30, "Lighting": 8})]).to_parquet(path)
    summary = summarize_stock(path)
    assert summary["mean_kwh_m2"] == pytest.approx({"Heating": 25.0, "Lighting": 7.0})
    assert summary["buildings"][1] == {
        "building_id": "b", "area_m2": 300.0, "values": {"Typology": "Hotel"}, "end_uses_kwh_m2": pytest.approx({"Heating": 30, "Lighting": 8}),
    }


def test_sample_runs_the_same_buildings_under_every_scenario():
    workspace = stock_project()
    run_dir, manifests, count = prepare_sample(workspace, 5, ["Baseline", "Retrofit"])
    assert count == 5
    sample = json.loads((run_dir / "buildings.geojson").read_text())["features"]
    assert len(sample) == 5
    docs = {s: yaml.safe_load(p.read_text()) for s, p in manifests.items()}
    assert {d["scenario"] for d in docs.values()} == {"Baseline", "Retrofit"}
    assert docs["Baseline"]["file_config"] == docs["Retrofit"]["file_config"]
    assert docs["Baseline"]["name"] == "Demo run-sample"
    artifacts = yaml.safe_load(Path(docs["Baseline"]["file_config"]).read_text())
    assert artifacts["gis_file"] == str((run_dir / "buildings.geojson").resolve())
    # the same seed picks the same buildings next time
    again, _, _ = prepare_sample(workspace, 5, ["Baseline"])
    assert json.loads((again / "buildings.geojson").read_text())["features"] == sample


def test_sample_needs_generated_batch_files_and_known_scenarios():
    workspace = saved_project()
    with pytest.raises(GlobiRunError, match="Sampling"):
        prepare_sample(workspace, 5, ["Baseline"])
    save_stock(workspace)
    generate()
    with pytest.raises(GlobiRunError, match="Choose scenarios"):
        prepare_sample(workspace, 5, ["Heat pumps"])


def test_sample_job_submits_each_scenario_then_reads_its_results(monkeypatch):
    stock_project()
    calls = []

    def fake_step(job, cmd, cwd, timeout):
        calls.append(cmd)
        if "e2e" in cmd:
            scenario = yaml.safe_load(Path(cmd[cmd.index("--manifest") + 1]).read_text())["scenario"]
            return f"INFO allocating\nRUN_NAME=Demo run-sample/{scenario}\n"
        run_name, out = cmd[cmd.index("--run-name") + 1], Path(cmd[cmd.index("--output-dir") + 1])
        heating = 20.0 if run_name.endswith("Baseline") else 5.0
        (out / run_name / "v1.0.0").mkdir(parents=True)
        energy_frame([("a", "Office", 100.0, {"Heating": heating, "Lighting": 4})]).to_parquet(out / run_name / "v1.0.0" / "EnergyAndPeak.pq")
        return ""

    monkeypatch.setattr(globi_runs, "run_step", fake_step)
    started = client.post("/api/globi-files/run-sample", json={"n": 3})
    assert started.status_code == 200, started.text
    job = wait(started.json()["id"])
    assert job["status"] == "done", job["error"]
    assert job["result"]["buildings"] == 3
    means = {s: r["mean_kwh_m2"]["Heating"] for s, r in job["result"]["scenarios"].items()}
    assert means == {"Baseline": 20.0, "Retrofit": 5.0}
    assert [c[2:4] for c in calls] == [["tests", "e2e"], ["get", "experiment"]] * 2
    assert calls[0][calls[0].index("--max-sims") + 1] == "3"


def test_a_failed_submission_says_to_start_the_engine(monkeypatch):
    stock_project()

    def fail(job, cmd, cwd, timeout):
        raise globi_runs.StepFailed("Connection refused")

    monkeypatch.setattr(globi_runs, "run_step", fail)
    job = wait(client.post("/api/globi-files/run-sample", json={"n": 2, "scenarios": ["Baseline"]}).json()["id"])
    assert job["status"] == "failed"
    assert "Connection refused" in job["error"] and "make engine" in job["error"]


def test_building_job_reads_the_simulated_building(monkeypatch):
    workspace = template_project()
    with pytest.raises(GlobiRunError, match="Generate"):
        globi_runs.start_building_run(workspace)
    workspace = stock_project()

    def fake_step(job, cmd, cwd, timeout):
        assert cmd[:3] == ["make", "cli-native", "simulate"]
        results = Path(cmd[cmd.index("--output-dir") + 1]) / "results"
        results.mkdir(parents=True, exist_ok=True)
        energy_frame([("placeholder", "Office", 600.0, {"Heating": 26.9, "Lighting": 18.7})]).to_parquet(results / "EnergyAndPeak.parquet")
        return ""

    monkeypatch.setattr(globi_runs, "run_step", fake_step)
    job = wait(client.post("/api/globi-files/run-building").json()["id"])
    assert job["status"] == "done", job["error"]
    assert job["result"]["end_uses_kwh_m2"] == pytest.approx({"Heating": 26.9, "Lighting": 18.7})
    assert job["result"]["context"]["Typology"] == "Office"
    assert Path(job["result"]["output_dir"]) == workspace.root / "outputs" / "building"


def test_a_reloaded_page_finds_the_last_run_and_a_lost_one_reads_as_interrupted(monkeypatch):
    workspace = stock_project()
    assert client.get("/api/globi-files/runs").json() == {"building": None, "sample": None}

    def fake_step(job, cmd, cwd, timeout):
        results = Path(cmd[cmd.index("--output-dir") + 1]) / "results"
        results.mkdir(parents=True, exist_ok=True)
        energy_frame([("placeholder", "Office", 600.0, {"Heating": 26.9, "Lighting": 18.7})]).to_parquet(results / "EnergyAndPeak.parquet")
        return ""

    monkeypatch.setattr(globi_runs, "run_step", fake_step)
    job = wait(client.post("/api/globi-files/run-building").json()["id"])
    latest = client.get("/api/globi-files/runs").json()
    assert latest["building"]["id"] == job["id"]
    assert latest["building"]["status"] == "done"
    assert latest["building"]["result"]["end_uses_kwh_m2"]["Heating"] == pytest.approx(26.9)

    # a run the backend was following when it restarted: saved as running by another process
    record = workspace.root / "runs" / "globi-sample.json"
    record.write_text(json.dumps({"id": "gone", "status": "running", "stage": "Baseline: simulating 10 buildings", "pid": -1}))
    sample = client.get("/api/globi-files/runs").json()["sample"]
    assert sample["status"] == "interrupted"
    assert "restarted" in sample["error"]


def test_a_failed_step_reports_the_command_error_not_makes_line(tmp_path):
    from services.jobs import Job, StepFailed, run_step

    script = "echo 'Traceback ...' >&2; echo \"AttributeError: no attribute 'x'\" >&2; echo 'make[1]: *** [cli-native] Error 1' >&2; exit 2"
    with pytest.raises(StepFailed, match="AttributeError: no attribute 'x'"):
        run_step(Job(id="t", command="t"), ["sh", "-c", script], tmp_path, 10)


def test_a_broken_run_is_not_blamed_on_the_engine(monkeypatch):
    stock_project()

    def fail(job, cmd, cwd, timeout):
        raise globi_runs.StepFailed("AttributeError: 'GloBIExperimentSpec' object has no attribute 'schedule_variability_config'")

    monkeypatch.setattr(globi_runs, "run_step", fail)
    job = wait(client.post("/api/globi-files/run-sample", json={"n": 2, "scenarios": ["Baseline"]}).json()["id"])
    assert "schedule_variability_config" in job["error"]
    assert "make engine" not in job["error"]
