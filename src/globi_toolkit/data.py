"""`globi-toolkit data`: the globi-data CLI, run against a cached clone of globi-data.

The globi-data package reads its registry (published.yml, inputs/, outputs/) from a
checkout of the globi-data repo. So that cloning the toolkit is enough, the toolkit keeps a
shallow clone in the user cache. GLOBI_DATA_ROOT or --root point at your own checkout
instead; use one when you publish or record versions, since those write to the repo.
"""

import os
import subprocess
from pathlib import Path

import click
from globi_data.cli import cli as data_cli

URL = "https://github.com/MITSustainableDesignLab/globi-data.git"
CACHE = (
    Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser()
    / "globi-toolkit"
    / "globi-data"
)


def _git(*args: str) -> None:
    # fixed git argv; the only inputs are the repo URL and cache path above
    subprocess.run(["git", *args], check=True)  # noqa: S603, S607


def data_checkout() -> Path:
    """Return the globi-data checkout to use: GLOBI_DATA_ROOT, else the cached clone."""
    if env := os.environ.get("GLOBI_DATA_ROOT"):
        return Path(env).expanduser().resolve()
    if not (CACHE / ".git").exists():
        click.echo(f"First use: cloning globi-data into {CACHE}", err=True)
        _git("clone", "--depth", "1", "--quiet", URL, str(CACHE))
    return CACHE


@click.group("data", commands=dict(data_cli.commands), help=data_cli.help)
@click.option(
    "--root",
    "root_opt",
    default=None,
    help="Path to a globi-data checkout (default: GLOBI_DATA_ROOT, else a cached clone).",
)
@click.pass_context
def data(ctx: click.Context, root_opt: str | None) -> None:
    """Versioned inputs and outputs for GloBI simulation runs."""
    # globi-data's commands read the repo root from ctx.obj["root"], as its own group sets it
    ctx.ensure_object(dict)
    ctx.obj["root"] = (
        Path(root_opt).expanduser().resolve() if root_opt else data_checkout()
    )


@data.command("pull")
def pull() -> None:
    """Update the cached globi-data clone to the latest main."""
    if os.environ.get("GLOBI_DATA_ROOT"):
        msg = "GLOBI_DATA_ROOT is set; update that checkout yourself."
        raise click.ClickException(msg)
    if not (CACHE / ".git").exists():
        data_checkout()
        return
    # --ff-only: never overwrite anything written into the cache by hand
    _git("-C", str(CACHE), "pull", "--ff-only", "--quiet")
    click.echo(f"globi-data at {CACHE} is up to date.")
