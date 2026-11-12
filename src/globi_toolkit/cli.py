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


@cli.command()
def visualise() -> None:
    """Open the results dashboard (Streamlit)."""
    app = Path(__file__).parent / "visualise" / "main.py"
    # fixed argv (this interpreter + a path inside the package), no user input
    sys.exit(subprocess.call([sys.executable, "-m", "streamlit", "run", str(app)]))  # noqa: S603
