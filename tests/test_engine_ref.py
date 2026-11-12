import re

from globi_toolkit.engine_ref import LOCK, engine_commit


def test_engine_commit_is_the_locked_globi_commit() -> None:
    commit = engine_commit()
    assert re.fullmatch(r"[0-9a-f]{40}", commit)
    assert f"MITSustainableDesignLab/globi?branch=main#{commit}" in LOCK.read_text()
