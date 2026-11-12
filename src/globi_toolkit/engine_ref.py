"""The engine commit pinned in uv.lock.

The Docker worker image is built from this commit, so workers run the same engine code
that `uv sync` installs. `python -m globi_toolkit.engine_ref` prints it (used by the Makefile).
"""

import tomllib
from pathlib import Path

LOCK = Path(__file__).resolve().parents[2] / "uv.lock"


def engine_commit(lock: Path = LOCK) -> str:
    """Return the git commit of the `globi` package locked in `lock`."""
    packages = tomllib.loads(lock.read_text())["package"]
    git = next(p["source"]["git"] for p in packages if p["name"] == "globi")
    return git.rsplit("#", 1)[1]


if __name__ == "__main__":
    print(engine_commit())
