"""Download a run's results and export them at the granularity a use case needs.

`fetch_run` downloads every final table of a run (`globi get` takes one table per call).
`export_run` writes a downloaded run's tables as Parquet, CSV or Excel:

- annual: EnergyAndPeak's months combined (energy added up, peak = the year's highest month);
- monthly: the tables as GloBI writes them;
- hourly: the monthly tables, plus the per-building hourly room-condition files listed in
  result_file_refs.pq (runs set up with sbem-ui's "Hourly room conditions").
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

import pandas as pd

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

Granularity = Literal["annual", "monthly", "hourly"]
Format = Literal["pq", "csv", "xlsx"]

NO_HOURLY = (
    "This run has no hourly files. In sbem-ui, tick Hourly room conditions under "
    "Model Execution → What will you analyze?, generate the files and run again."
)


def fetch_run(run: str, dest: Path) -> Path:
    """Download every final table of a run's latest version into dest/<run id>/.

    `run` is a run name ("Demo/Baseline") or a run id with its version
    ("Demo/Baseline/v1.0.0"). Reads the bucket from the scythe storage settings.
    """
    from globi_toolkit.visualize.data_sources import S3DataSource
    from globi_toolkit.visualize.models import S3DataSourceConfig

    source = S3DataSource(S3DataSourceConfig(cache_dir=dest))
    run = run.strip("/")
    # sorted by version, so the last match is the latest
    ids = [
        r for r in source.list_available_runs() if r == run or r.startswith(f"{run}/")
    ]
    if not ids:
        msg = f"No run named {run} in the bucket."
        raise FileNotFoundError(msg)
    run_id = ids[-1]
    run_dir = dest / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    for key, size in source._final_files(run_id).values():
        target = run_dir / Path(key).name
        if not (target.is_file() and target.stat().st_size == size):
            source.client.download_file(
                Bucket=source._settings.BUCKET, Key=key, Filename=target.as_posix()
            )
    return run_dir


def to_annual(df: pd.DataFrame) -> pd.DataFrame:
    """EnergyAndPeak by month -> by year: energy added up, peak the highest month."""
    levels = [name for name in df.columns.names if name != "Month"]
    energy = df[["Energy"]].T.groupby(level=levels).sum().T
    peak = df[["Peak"]].T.groupby(level=levels).max().T
    return pd.concat([energy, peak], axis=1)


def download_hourly(
    refs_path: Path, dest: Path, s3: S3Client | None = None
) -> list[Path]:
    """Download the per-building hourly files a run lists, one Parquet file per building.

    Kept as separate files, so a big stock never has to fit in memory at once;
    `pd.read_parquet(dest)` reads them all as one table.
    """
    if not refs_path.exists():
        raise FileNotFoundError(NO_HOURLY)
    refs = pd.read_parquet(refs_path)
    if "hourly_data" not in refs.columns:
        raise FileNotFoundError(NO_HOURLY)
    if s3 is None:
        import boto3

        s3 = boto3.client("s3")
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for uri in refs["hourly_data"].dropna():
        bucket, key = str(uri).removeprefix("s3://").split("/", 1)
        target = dest / Path(key).name  # the simulation task's id: unique per row
        if not target.exists():  # download_file renames when done, so a file is whole
            s3.download_file(Bucket=bucket, Key=key, Filename=target.as_posix())
        written.append(target)
    return written


def export_run(
    run_dir: Path,
    out_dir: Path,
    granularity: Granularity = "monthly",
    fmt: Format = "csv",
    s3: S3Client | None = None,
) -> list[Path]:
    """Write every table of a downloaded run at the chosen granularity. Returns the files."""
    tables = sorted(p for p in run_dir.glob("*.pq") if p.stem != "result_file_refs")
    if not tables:
        msg = f"No result tables (.pq) in {run_dir}."
        raise FileNotFoundError(msg)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for path in tables:
        # 8760 columns per row: only worth exporting when hourly was asked for
        if path.stem.startswith("HourlyData.") and granularity != "hourly":
            continue
        df = pd.read_parquet(path)
        if granularity == "annual" and path.stem == "EnergyAndPeak":
            df = to_annual(df)
        written.append(_write(df, out_dir / f"{path.stem}.{fmt}", fmt))
    if granularity == "hourly":
        # shortcut: hourly files stay Parquet (too big for CSV/Excel); convert on request
        written += download_hourly(
            run_dir / "result_file_refs.pq", out_dir / "hourly", s3
        )
    return written


def _write(df: pd.DataFrame, path: Path, fmt: Format) -> Path:
    if fmt == "pq":
        df.to_parquet(path)
        return path
    # spreadsheets: one header row ("Energy.End Uses.Heating.1") and every index level
    # (building, zone, ...) as a column, so nothing is lost
    if isinstance(df.columns, pd.MultiIndex):
        df = df.set_axis([".".join(map(str, c)) for c in df.columns], axis=1)
    df = df.reset_index()
    if fmt == "csv":
        df.to_csv(path, index=False)
    else:
        df.to_excel(path, index=False)
    return path
