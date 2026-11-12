import importlib
import pkgutil
from pathlib import Path

from streamlit.testing.v1 import AppTest

import globi_toolkit.visualise as visualise


def test_dashboard_modules_import() -> None:
    # main.py is the Streamlit script and runs the app on import, so it is excluded
    for mod in pkgutil.walk_packages(visualise.__path__, "globi_toolkit.visualise."):
        if mod.name != "globi_toolkit.visualise.main":
            importlib.import_module(mod.name)


def test_dashboard_start_page_renders() -> None:
    app = AppTest.from_file(str(Path(visualise.__file__).parent / "main.py"))
    app.run(timeout=60)
    assert not app.exception
    assert "GloBI results" in [h.value for h in app.header]
