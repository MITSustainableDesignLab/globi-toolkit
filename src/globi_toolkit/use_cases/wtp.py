"""Willingness to pay: the probability that each building accepts a retrofit at its cost.

Everything regional lives in the project's `use-cases/wtp.yml`, in four parts:

- areas: tables of WTP data per area. The area is whatever unit the user split their
  buildings into (tract, district, municipality, parcel ...); each table is joined to the
  buildings on a column the user names. A table holds one value per area (e.g. climate
  concern) and/or distributions (counts per category, e.g. households by income), which
  are sampled `monte_carlo_samples` times per building.
- inputs: what feeds the model and how it is encoded. The source is a building attribute
  (a GIS column), a GloBI result (`RESULT_COLUMNS`), an area value or distribution, or a
  fixed value; then optionally map (category -> number), scale, then bins (-> ordinal).
- residential: the typologies it covers, and the model (logit: intercept + coefficient x input).
- commercial: the typologies it covers; accepted when the NPV of the savings is at least
  `threshold_usd`.

Missing inputs are errors, never defaults: a column, an area code, or a category the
model has no value for stops the run and is named.

The worked example (globi-tutorial) is the Berzolla et al. (2025) model from
MA-stock-analysis, with the encodings in its `main_pipeline_doc.md`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

import numpy as np
import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, model_validator

#: per-building values the toolkit computes from GloBI results and the project's costs
RESULT_COLUMNS = {
    "net_cost_usd": "Upfront cost of the retrofit, after incentives (USD)",
    "energy_cost_usd": "Annual energy bill before the retrofit (USD)",
    "energy_savings_usd": "Annual energy-bill savings from the retrofit (USD)",
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Input(_Strict):
    """One model input: where it comes from, then how it is encoded."""

    source: Literal["building", "results", "area", "value"]
    #: building or results column, or an area table's column (one value per area)
    column: str | None = None
    table: str | None = None
    #: an area table's distribution, drawn per household
    sample: str | None = None
    value: float | None = None
    map: dict[str, float] | None = None
    scale: float = 1.0
    #: ordinal bin edges (np.digitize: below the first edge -> 0)
    bins: list[float] | None = None

    @model_validator(mode="after")
    def _source_has_what_it_needs(self) -> Input:
        given = {
            k
            for k in ("column", "table", "sample", "value")
            if getattr(self, k) is not None
        }
        needs = {
            "building": [{"column"}],
            "results": [{"column"}],
            "area": [{"table", "column"}, {"table", "sample"}],
            "value": [{"value"}],
        }[self.source]
        if given not in needs:
            options = " or ".join(" + ".join(sorted(n)) for n in needs)
            msg = f"a {self.source} input needs {options}"
            raise ValueError(msg)
        if self.source == "results" and self.column not in RESULT_COLUMNS:
            msg = f"results columns are {', '.join(RESULT_COLUMNS)}"
            raise ValueError(msg)
        return self


class Join(_Strict):
    """Which building column holds the area code, and which table column matches it."""

    building: str
    table: str


class AreaTable(_Strict):
    """A CSV of WTP data, one row per area."""

    file: Path
    join: Join
    #: name -> {value: [count columns that add up to it]}
    distributions: dict[str, dict[float, list[str]]] = {}


class LogitModel(_Strict):
    """P(accept) = 1 / (1 + exp(-(intercept + sum of coefficient x input)))."""

    type: Literal["logit"] = "logit"
    intercept: float
    coefficients: dict[str, float]


class Residential(_Strict):
    """Which buildings are residential, and how they decide."""

    typologies: list[str]
    model: LogitModel
    monte_carlo_samples: int = 100


class Commercial(_Strict):
    """Accept (1) when NPV of savings >= threshold, else 0."""

    typologies: list[str]
    years: int
    discount_rate: float
    price_growth: float = 0.0
    threshold_usd: float = 0.0


class WTPConfig(_Strict):
    """The contents of use-cases/wtp.yml."""

    typology_column: str = "Typology"
    seed: int = 42
    areas: dict[str, AreaTable] = {}
    inputs: dict[str, Input]
    residential: Residential
    commercial: Commercial

    @model_validator(mode="after")
    def _names_match(self) -> WTPConfig:
        coefficients = self.residential.model.coefficients
        problems = [
            f"coefficient {n} has no input"
            for n in coefficients
            if n not in self.inputs
        ]
        problems += [
            f"input {n} has no coefficient"
            for n in self.inputs
            if n not in coefficients
        ]
        for name, inp in self.inputs.items():
            if inp.source != "area":
                continue
            table = self.areas.get(str(inp.table))
            if table is None:
                problems.append(f"input {name}: there is no area table {inp.table}")
            elif inp.sample is not None and inp.sample not in table.distributions:
                problems.append(
                    f"input {name}: table {inp.table} has no distribution {inp.sample}"
                )
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @classmethod
    def from_dict(cls, raw: dict[str, Any], base: Path) -> WTPConfig:
        """Validate a wtp.yml document; area files are taken relative to `base`."""
        config = cls.model_validate(raw)
        for table in config.areas.values():
            if not table.file.is_absolute():
                table.file = base / table.file
        return config

    @classmethod
    def load(cls, path: Path) -> WTPConfig:
        """Read a wtp.yml file."""
        return cls.from_dict(yaml.safe_load(path.read_text()) or {}, path.parent)


class WTPInputError(ValueError):
    """The buildings or the area tables are missing something the model needs."""


def acceptance(buildings: pd.DataFrame, config: WTPConfig) -> pd.DataFrame:
    """Probability that each row accepts its retrofit; same index as `buildings`.

    Columns: sector, acceptance_probability, and p_min / p_max / p_sd across the
    sampled households (no spread for commercial rows or unsampled models).
    """
    residential, commercial = _sectors(buildings, config)
    table = pd.DataFrame(index=buildings.index)
    table["sector"] = np.where(residential, "residential", "commercial")
    for name in ("acceptance_probability", "p_min", "p_max", "p_sd"):
        table[name] = np.nan
    if residential.any():
        stats = _residential(buildings.loc[residential], config)
        table.loc[residential, list(stats)] = stats.to_numpy()
    if commercial.any():
        p = _commercial(buildings.loc[commercial], config.commercial)
        for name in ("acceptance_probability", "p_min", "p_max"):
            table.loc[commercial, name] = p
        table.loc[commercial, "p_sd"] = 0.0
    return table


def check(buildings: pd.DataFrame, config: WTPConfig) -> dict[str, Any]:
    """Everything that would stop a run on these buildings, before GloBI results exist.

    -> {"problems": [...], "residential": n, "commercial": n}
    """
    try:
        residential, commercial = _sectors(buildings, config)
    except WTPInputError as exc:
        return {"problems": [str(exc)], "residential": 0, "commercial": 0}
    problems: list[str] = []
    rows = buildings.loc[residential]
    if len(rows):
        skip = {n for n, i in config.inputs.items() if i.source == "results"}
        rng = np.random.default_rng(0)
        _inputs(rows, config, 1, rng, skip=skip, problems=problems)
    return {
        "problems": problems,
        "residential": int(residential.sum()),
        "commercial": int(commercial.sum()),
    }


def _sectors(
    buildings: pd.DataFrame, config: WTPConfig
) -> tuple[np.ndarray, np.ndarray]:
    typology = _column(buildings, config.typology_column).astype(str)
    residential = typology.isin(config.residential.typologies).to_numpy()
    commercial = typology.isin(config.commercial.typologies).to_numpy()
    unknown = sorted(set(typology[~(residential | commercial)]))
    if unknown:
        msg = (
            f"{config.typology_column} values in neither residential nor commercial: "
            f"{', '.join(unknown)}."
        )
        raise WTPInputError(msg)
    return residential, commercial


def _residential(rows: pd.DataFrame, config: WTPConfig) -> pd.DataFrame:
    model = config.residential.model
    n = config.residential.monte_carlo_samples
    values = _inputs(rows, config, n, np.random.default_rng(config.seed))
    z = np.full((len(rows), 1), model.intercept)
    for name, coef in model.coefficients.items():
        z = z + coef * values[name]
    p = 1 / (1 + np.exp(-z))
    return pd.DataFrame({
        "acceptance_probability": p.mean(axis=1),
        "p_min": p.min(axis=1),
        "p_max": p.max(axis=1),
        "p_sd": p.std(axis=1),
    })


def _commercial(rows: pd.DataFrame, rule: Commercial) -> np.ndarray:
    years = np.arange(1, rule.years + 1)
    factor = (((1 + rule.price_growth) / (1 + rule.discount_rate)) ** years).sum()
    savings = _numeric(_column(rows, "energy_savings_usd"), "energy_savings_usd")
    cost = _numeric(_column(rows, "net_cost_usd"), "net_cost_usd")
    return (savings * factor - cost >= rule.threshold_usd).astype(float)


class _Attempt:
    """Runs a step; given a list, records its WTPInputError there instead of raising."""

    def __init__(self, problems: list[str] | None) -> None:
        self.problems = problems

    def __call__(self, fn: Callable[..., Any], *args: Any) -> Any:
        try:
            return fn(*args)
        except WTPInputError as exc:
            if self.problems is None:
                raise
            self.problems.append(str(exc))
            return None


def _inputs(
    rows: pd.DataFrame,
    config: WTPConfig,
    n: int,
    rng: np.random.Generator,
    skip: set[str] | None = None,
    problems: list[str] | None = None,
) -> dict[str, np.ndarray]:
    """Input name -> encoded values, (rows, 1), or (rows, n) when sampled.

    With `problems`, every problem is collected there instead of stopping at the first.
    """
    attempt = _Attempt(problems)
    tables: dict[str, tuple[pd.DataFrame, np.ndarray] | None] = {}
    # inputs that sample the same distribution share its draws (e.g. bedrooms, residents)
    draws: dict[tuple[str, str], np.ndarray | None] = {}
    out: dict[str, np.ndarray] = {}
    for name, inp in config.inputs.items():
        if name in (skip or ()):
            continue
        if inp.source == "area":
            values = _area_input(
                name, inp, rows, config, n, rng, tables, draws, attempt
            )
        elif inp.source == "value":
            values = attempt(_encode_series, name, inp, pd.Series([inp.value]))
        else:
            raw = attempt(_column, rows, inp.column)
            values = None if raw is None else attempt(_encode_series, name, inp, raw)
        if values is not None:
            out[name] = values if values.ndim == 2 else values[:, None]
    return out


def _area_input(
    name: str,
    inp: Input,
    rows: pd.DataFrame,
    config: WTPConfig,
    n: int,
    rng: np.random.Generator,
    tables: dict[str, tuple[pd.DataFrame, np.ndarray] | None],
    draws: dict[tuple[str, str], np.ndarray | None],
    attempt: _Attempt,
) -> np.ndarray | None:
    """An area value per row, or n draws per row from an area distribution."""
    table = str(inp.table)
    if table not in tables:
        tables[table] = attempt(_join, rows, table, config.areas[table])
    joined = tables[table]
    if joined is None:
        return None
    if inp.sample is None:
        raw = attempt(_area_column, *joined, inp.column, table)
        return None if raw is None else attempt(_encode_series, name, inp, raw)
    key = (table, inp.sample)
    if key not in draws:
        spec = config.areas[table].distributions[inp.sample]
        draws[key] = attempt(_draw, *joined, spec, n, rng, ".".join(key))
    found = draws[key]
    return None if found is None else _encode(inp, found)


def _encode_series(name: str, inp: Input, raw: pd.Series) -> np.ndarray:
    if inp.map is None:
        return _encode(inp, _numeric(raw, f"input {name}"))
    labels = raw.astype(str)
    values = labels.map(inp.map)
    unmatched = sorted(set(labels[values.isna()]))
    if unmatched:
        msg = f"input {name}: no number given for {', '.join(unmatched[:10])}."
        raise WTPInputError(msg)
    return _encode(inp, values.to_numpy(dtype="float64"))


def _encode(inp: Input, values: np.ndarray) -> np.ndarray:
    values = values.astype(float) * inp.scale
    if inp.bins is not None:
        values = np.digitize(values, inp.bins).astype(float)
    return values


def _join(
    rows: pd.DataFrame, name: str, table: AreaTable
) -> tuple[pd.DataFrame, np.ndarray]:
    """The table, and each row's position in it (matched on the join columns)."""
    if not table.file.exists():
        msg = f"area table {name}: {table.file.name} is missing."
        raise WTPInputError(msg)
    area = pd.read_csv(table.file, dtype={table.join.table: str})
    if table.join.table not in area.columns:
        msg = f"area table {name}: {table.file.name} has no column {table.join.table}."
        raise WTPInputError(msg)
    codes = pd.Index(area[table.join.table].astype(str))
    if codes.has_duplicates:
        msg = f"area table {name}: {table.join.table} repeats an area code."
        raise WTPInputError(msg)
    keys = _column(rows, table.join.building).astype(str)
    keys = keys.str.replace(r"\.0$", "", regex=True)  # codes read as floats
    positions = codes.get_indexer(keys)
    unmatched = sorted(set(keys[positions < 0]))
    if unmatched:
        msg = (
            f"area table {name}: {(positions < 0).sum()} buildings have a "
            f"{table.join.building} the table doesn't have ({', '.join(unmatched[:5])})."
        )
        raise WTPInputError(msg)
    return area, positions


def _area_column(
    area: pd.DataFrame, positions: np.ndarray, column: str | None, name: str
) -> pd.Series:
    if column not in area.columns:
        msg = f"area table {name} has no column {column}."
        raise WTPInputError(msg)
    return area[column].iloc[positions].reset_index(drop=True)


def _draw(
    area: pd.DataFrame,
    positions: np.ndarray,
    spec: dict[float, list[str]],
    n: int,
    rng: np.random.Generator,
    name: str,
) -> np.ndarray:
    """N draws per row from its area's counts: (rows, n) of the distribution's values."""
    missing = sorted({c for cols in spec.values() for c in cols} - set(area.columns))
    if missing:
        msg = f"{name}: the table has no column {', '.join(missing)}."
        raise WTPInputError(msg)
    values = np.array(list(spec), dtype=float)
    counts = np.column_stack([area[cols].sum(axis=1) for cols in spec.values()])
    counts = counts.astype(float)
    if (counts.sum(axis=1) <= 0)[positions].any():
        msg = f"{name}: some buildings are in areas whose counts are all zero."
        raise WTPInputError(msg)
    out = np.empty((len(positions), n))
    for where in np.unique(positions):
        here = positions == where
        p = counts[where] / counts[where].sum()
        out[here] = rng.choice(values, size=(int(here.sum()), n), p=p)
    return out


def _column(rows: pd.DataFrame, name: str | None) -> pd.Series:
    if name in rows.columns:
        return cast(pd.Series, rows[name]).reset_index(drop=True)
    if name in (rows.index.names or []):
        return pd.Series(rows.index.get_level_values(name))
    msg = f"The buildings have no {name} column."
    raise WTPInputError(msg)


def _numeric(values: pd.Series, name: str) -> np.ndarray:
    numbers = cast(pd.Series, pd.to_numeric(values, errors="coerce"))
    if numbers.isna().any():
        msg = f"{name}: {numbers.isna().sum()} rows have no number."
        raise WTPInputError(msg)
    return numbers.to_numpy(dtype="float64")
