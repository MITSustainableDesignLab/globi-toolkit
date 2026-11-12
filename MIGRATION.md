# Moving docs, dashboard and surrogate work into globi-toolkit

Status: docs, dashboard and sbem-ui copied in; surrogate waits for the engine merge. "Engine" means
[MITSustainableDesignLab/globi](https://github.com/MITSustainableDesignLab/globi).
Line counts and branch states are from 2026-11-10.

Goal: a user clones only globi-toolkit and gets the whole of GloBI. Everything else
arrives through `uv sync`, Docker or a download the toolkit does itself.

Order: 0–2 don't depend on anything else. 3 needs the surrogate branch merged into
the engine first. 4 comes later.

## 0. One clone

Decided:

- **sbem-ui moves into this repo.**
- **The worker image is built from the engine's git URL.**
- **The surrogate stage waits for the engine merge.**
- **CLI first; a run page in sbem-ui later.**

| Piece | How it arrives | Status |
|---|---|---|
| Engine, globi-data libraries | `uv sync` from git | done |
| Engine + globi-data CLIs | mounted as `globi-toolkit engine …` / `globi-toolkit data …` | done |
| globi-data **contents** | shallow clone in `~/.cache/globi-toolkit/globi-data` on first `globi-toolkit data …`; `data pull` updates it | done |
| Local run stack | engine's compose files, `.env*` templates, scripts and Makefile targets copied here | done; `make engine` not yet run end to end |
| Worker image | compose `build.context: https://github.com/MITSustainableDesignLab/globi.git#<commit>`, commit read from `uv.lock` (`make engine-ref`) | done; image built (6 GB) |
| sbem-ui | `apps/sbem-ui/`, uv workspace member | copied (no history); tests and frontend check pass |
| globi-use-cases | `uv sync` once it ships a real package | blocked in that repo |
| Dashboard, docs | sections 1 and 2 | copied here; engine copies not yet deleted |

**globi-data contents.** Chosen: option 1 of three (cached shallow clone; the others were
release zips per partner version, or teaching the globi-data CLI to read from GitHub).
`GLOBI_DATA_ROOT` or `--root` still win, for publishing from a real checkout. The cache
follows globi-data `main`, not the commit in `uv.lock`: data versions are append-only, but a
new registry format would need a newer `globi-data` package (bump the lock).

**Worker image pin.** The compose build ref and the `globi` commit in `uv.lock` must
match. Take both from one place: a Makefile variable read from `uv.lock`.

**sbem-ui move.** Copied from `~/school-repos/sbem-ui` working tree (2026-11-11), without
git history, uncommitted edits included.

- **Not copied:** local projects (`data/`, `server/daryaguettler/`) and sbem-ui's `uv.lock`
  (the toolkit lock covers it). Nothing was deleted in the sbem-ui repo.
- **Wiring:** `globi_root()` now returns the toolkit root, so the app's `make cli-native` /
  `make cli` calls run this repo's Makefile. The `../globi` path source is gone; globi comes
  from the toolkit's git pin. epinterface: one version, set by globi (the old branch pin was
  already dropped in sbem-ui).
- **Gap:** Single Building runs call `globi simulate-flat` and read
  `examples/tutorial/flat-building.yml`. Both exist only as uncommitted edits in the engine
  checkout. Until they land on engine main, that page fails at run time.
- **Ruff:** `apps/` is excluded from the toolkit's ruff config; sbem-ui keeps its own style.
- **Old repo:** `daryaguettler/sbem-ui` is now a stale copy; archive it once this repo has
  its GitHub home.
- **Later:** a page in sbem-ui to launch runs and surrogate training through the toolkit CLI.

## 1. Documentation: move

**Status:** copied from engine `HEAD` (`b4f2dfa`).
- **Paths:** `tests/data/e2e/` became `examples/e2e/` in the docs and the example files.
- **Direct docker commands:** raw `docker compose` lines became `make ps` / `make logs` / `make reset`.
- **Site config:** `repo_url` and `site_url` are left out until the repo has its GitHub home.
- **Not copied:** your uncommitted engine edits (`build-a-model.md`, `manifest-overheating.yml`) and the
  untracked tutorial files (`artifacts-overheating.yml`, `components-lib-overheating.db`,
  `flat-building.yml`, `schedule-variability.yml`, `sbem-project/components-lib.db`).
- **Clone URL:** `requirements.md` assumes `MITSustainableDesignLab/globi-toolkit`.
- **sbem-ui tutorials** (`edit-components.md`, `build-a-model.md`) now use `make ui-setup` / `make ui`
  from the toolkit folder.

- **What:** the mkdocs site (`mkdocs.yml`, `mkdocs_hooks/`, `docs/`: 15 pages + 24 dashboard
  screenshots) and the `docs`, `docs-test`, `docs-deploy` Makefile targets.
- **Moves to the toolkit:** home, use cases, workflow overview, all tutorials, the dashboard pages.
- **Stays in the engine:** a short README that links to the toolkit docs. The CLI/API
  reference can stay as mkdocstrings in the toolkit, since the toolkit installs `globi`.
- **Rewrites:** pages call engine `make` targets and `.env.*` files (`make viz-native`, Docker
  compose). Those must point to toolkit commands. `globi` CLI calls keep working
  (`uv run globi ...`).
- **URL:** the site is `mitsustainabledesignlab.github.io/globi`. Hosting it from the toolkit
  needs the toolkit in the MITSustainableDesignLab org, plus a redirect page on the old site.

## 2. Results dashboard: move

**Status:** copied to `src/globi_toolkit/visualise/`.
- **Changes:** imports renamed, nothing else (checked with a diff).
- **Run it:** `globi-toolkit visualise` / `make viz-native`; tests import every module and render the start page.
- **Docker:** the image builds (5 GB). Its start-up fix (`--no-sync`) is not yet re-tested.

- **What:** `src/globi/tools/visualization/` (11.7k lines). It imports only itself, so it can
  move into `globi_toolkit/visualise/` without changes to the engine's other code.
- **Goes with it:**
  - its `Dockerfile` and `config.toml`
  - `docker-compose.st.yml`
  - the `viz` and `viz-native` Makefile targets
  - the engine's `visualization` extra (streamlit, plotly, folium, …), which becomes toolkit
    dependencies
- **Branch conflict:** the surrogate branch carries an older, smaller copy of the dashboard
  (main → branch deletes about 8k lines there). Move main's version. When the branch merges,
  resolve those files by deleting them.
- **Not tracked by git, nothing to move:** `ui/`, `web/`, `gis-viewer/`,
  `src/globi/tools/viz-app/` (only `node_modules`/`dist`).

## 3. Surrogate training and inference: split, don't move wholesale

Facts:

- **Not on main yet:** the code lives only on branch
  `feature/create-refactored-surrogate-training` (67 commits ahead of main, 13 behind).
- **Size:** `globi.models.surrogate` (4.1k lines) plus `globi.pipelines.training` (473).
- **Runs on the engine's workers:** training is a Hatchet workflow (`iterative_training`)
  registered by the engine worker (`globi/worker/main.py`). It fans out EnergyPlus runs through
  scythe.
- **Other users:** `globi.validation` and the `globi` CLI import it. md-globi, norway-globi and
  globi-p3 import about 20 of its modules.

Recommendation:

- **The engine keeps** training and the model library: backends, transforms, `inference.py`.
  Moving them would put the toolkit inside the worker image. Splitting transforms from
  inference across repos also risks training and inference drifting apart.
- **The toolkit gets a `surrogate` stage** with what md-globi and norway-globi do on top of the
  engine:
  - training config (strata, budget)
  - launching training through the engine
  - inference on every building of a stock (n prior draws × scenarios → Hive partitions on S3)
  - recording the model in globi-data (`globi-data surrogates record` / `promote`)
- **Before that:** merge the surrogate branch into engine main and tag a release, then pin
  that tag in the toolkit.

The alternative, moving all surrogate code into the toolkit, makes the engine thinner but
turns the toolkit into a worker dependency. It would no longer be thin.

## 4. Validation and calibration: later

`globi.validation` (1.7k lines, surrogate branch) and globi-uk-calibration (Sam) exist. A
`validate` stage is reserved in the toolkit, with no work planned yet.

## Open

- Engine changes need Sam's agreement.
- Moving the toolkit (and globi-use-cases) into the MITSustainableDesignLab org.
- The engine checkout `~/school-repos/globi` has uncommitted changes (allocate.py, CLI, one
  tutorial, Makefile). Commit or stash them before any move.
