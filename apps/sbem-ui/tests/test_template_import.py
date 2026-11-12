import sqlite3
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).parent.parent / "server"
sys.path.insert(0, str(SERVER_DIR))

import pytest
from fastapi.testclient import TestClient
from main import app
from services.workspace import WorkspaceError

from conftest import open_temp_workspace

client = TestClient(app)
RESSTOCK = Path(__file__).parent.parent / "templates" / "projects" / "resstock"


def _rows(db: Path, sql: str) -> list[tuple]:
    con = sqlite3.connect(db)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_empty_project_can_add_resstock_later():
    workspace = open_temp_workspace(init_db=True)
    assert client.get("/api/workspace/stock-preset").json()["preset"] is None

    response = client.post("/api/workspace/import-template", json={"template": "resstock.yml"})
    assert response.status_code == 200
    assert response.json()["added"]["Lighting"] > 0

    db = workspace.components_db_path
    names = {name for (name,) in _rows(db, "SELECT Name FROM Lighting")}
    assert any(name.startswith("resstock_") for name in names)
    # every record comes with what it points at
    assert _rows(db, "SELECT count(*) FROM Lighting l LEFT JOIN Year y ON y.id = l.ScheduleId WHERE y.id IS NULL") == [(0,)]
    assert _rows(
        db,
        "SELECT count(*) FROM ConstructionAssemblyLayer l "
        "LEFT JOIN ConstructionMaterial m ON m.id = l.ConstructionMaterialId WHERE m.id IS NULL",
    ) == [(0,)]
    assert client.get("/api/workspace/stock-preset").json()["preset"] is not None

    # importing again changes nothing
    assert client.post("/api/workspace/import-template", json={"template": "resstock.yml"}).json()["added"] == {}


def test_import_refuses_when_a_user_record_has_a_template_name():
    workspace = open_temp_workspace(init_db=True)
    (name,) = _rows(RESSTOCK / "components-lib.db", "SELECT Name FROM Day LIMIT 1")[0]
    con = sqlite3.connect(workspace.components_db_path)
    hours = ", ".join(["0"] * 24)
    with con:
        con.execute(f"INSERT INTO Day VALUES ('user-own-id', ?, 'Fraction', {hours})", (name,))
    con.close()

    with pytest.raises(WorkspaceError, match=name):
        workspace.import_template(RESSTOCK)
