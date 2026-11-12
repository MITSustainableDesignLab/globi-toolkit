from click.testing import CliRunner

from globi_toolkit.cli import cli


def test_dependencies_import() -> None:
    import globi  # noqa: F401
    import globi_data  # noqa: F401


def test_info_lists_components() -> None:
    result = CliRunner().invoke(cli, ["info"])
    assert result.exit_code == 0, result.output
    for name in ("globi-toolkit", "globi", "globi-data"):
        assert name in result.output
    assert "not installed" not in result.output


def test_component_clis_are_mounted() -> None:
    runner = CliRunner()
    engine = runner.invoke(cli, ["engine", "--help"])
    data = runner.invoke(cli, ["data", "--help"])
    assert engine.exit_code == 0 and "submit" in engine.output, engine.output
    assert data.exit_code == 0 and "inputs" in data.output, data.output
