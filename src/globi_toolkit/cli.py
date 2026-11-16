"""`globi-toolkit` command line."""

import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import click
from globi.tools.cli.main import cli as engine_cli

from globi_toolkit.data import data

COMPONENTS = ("globi-toolkit", "globi", "globi-data", "sbem-ui")


@click.group()
def cli() -> None:
    """GloBI toolkit."""


# The components' own CLIs, so cloning this repo is enough to reach every command.
cli.add_command(engine_cli, "engine")
cli.add_command(data)  # globi-data's CLI, on a cached clone of the data repo


@cli.command()
def info() -> None:
    """Print the installed version of each GloBI component."""
    for name in COMPONENTS:
        try:
            v = version(name)
        except PackageNotFoundError:
            v = "not installed"
        click.echo(f"{name:<14} {v}")


@cli.group()
def results() -> None:
    """Download runs and export their tables (annual, monthly or hourly)."""


@results.command()
@click.argument("run")
@click.option(
    "--dest",
    type=click.Path(path_type=Path),
    default=Path("outputs"),
    show_default=True,
)
def fetch(run: str, dest: Path) -> None:
    """Download every result table of RUN (e.g. "Demo/Baseline"), latest version."""
    from globi_toolkit.results import fetch_run

    click.echo(fetch_run(run, dest))


@results.command()
@click.argument(
    "run_dir", type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.option(
    "--granularity",
    type=click.Choice(["annual", "monthly", "hourly"]),
    default="monthly",
    show_default=True,
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["csv", "xlsx", "pq"]),
    default="csv",
    show_default=True,
)
@click.option(
    "--out",
    type=click.Path(path_type=Path),
    help="Folder to write to; default RUN_DIR/export-GRANULARITY.",
)
def export(run_dir: Path, granularity: str, fmt: str, out: Path | None) -> None:
    """Export the tables of a downloaded run folder at a chosen granularity."""
    from globi_toolkit.results import export_run

    out = out or run_dir / f"export-{granularity}"
    files = export_run(run_dir, out, granularity, fmt)  # type: ignore[arg-type]
    click.echo(f"Wrote {len(files)} files to {out}")


@cli.command()
def visualize() -> None:
    """Open the results dashboard (Streamlit)."""
    app = Path(__file__).parent / "visualize" / "main.py"
    # fixed argv (this interpreter + a path inside the package), no user input
    sys.exit(subprocess.call([sys.executable, "-m", "streamlit", "run", str(app)]))  # noqa: S603
