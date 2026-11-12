import subprocess

import click
from click.testing import CliRunner

from globi_toolkit import data as data_mod
from globi_toolkit.cli import cli


def _git(*args):
    subprocess.run(  # noqa: S603
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],  # noqa: S607
        check=True,
        capture_output=True,
    )


@data_mod.data.command("root")
@click.pass_context
def _print_root(ctx):
    """Test-only probe: the root the group hands to globi-data's commands."""
    click.echo(ctx.obj["root"])


def test_clones_once_then_pulls(tmp_path, monkeypatch):
    src, cache = tmp_path / "src", tmp_path / "cache"
    _git("init", "-q", "-b", "main", str(src))
    (src / "published.yml").write_text("v: 1\n")
    _git("-C", str(src), "add", ".")
    _git("-C", str(src), "commit", "-qm", "one")
    monkeypatch.delenv("GLOBI_DATA_ROOT", raising=False)
    monkeypatch.setattr(data_mod, "URL", src.as_uri())
    monkeypatch.setattr(data_mod, "CACHE", cache)
    runner = CliRunner()

    # --root: no clone
    out = runner.invoke(cli, ["data", "--root", str(src), "root"])
    assert out.exit_code == 0, out.output
    assert not cache.exists()

    # default: shallow clone into the cache, used as the root
    out = runner.invoke(cli, ["data", "root"])
    assert out.exit_code == 0, out.output
    assert out.output.strip().endswith(str(cache))
    assert (cache / "published.yml").read_text() == "v: 1\n"

    # pull fast-forwards to the source's new commit
    (src / "published.yml").write_text("v: 2\n")
    _git("-C", str(src), "commit", "-qam", "two")
    out = runner.invoke(cli, ["data", "pull"])
    assert out.exit_code == 0, out.output
    assert (cache / "published.yml").read_text() == "v: 2\n"


def test_env_root_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("GLOBI_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(data_mod, "CACHE", tmp_path / "cache")
    assert data_mod.data_checkout() == tmp_path.resolve()
    assert CliRunner().invoke(cli, ["data", "pull"]).exit_code != 0
