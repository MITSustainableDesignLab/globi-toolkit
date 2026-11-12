"""Download ResStock's odds (housing_characteristics tables) for the preset's characteristics.

    uv run python scripts/fetch_resstock_odds.py

Saves NREL/resstock's project_national/housing_characteristics/<Characteristic>.tsv at the
template's tag into templates/resstock-odds/<tag>/, unchanged, for "From ResStock's odds" on
Building Characteristics (services/resstock_odds.py). Each row is one combination of the
characteristic's dependencies, with the share of homes taking each option and
`sampling_probability`, how common that combination is among ResStock's homes.

It also writes state-regions.yml: for each state, the values of ResStock's geographic
dependencies (Location Region, Census Division, climate zone, ...) its homes have, derived
from ResStock's spatial tables, so the odds can be narrowed to one state.
"""

from __future__ import annotations

import sys
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PRESET = ROOT / "templates" / "projects" / "resstock" / "resstock-preset.yml"
URL = "https://raw.githubusercontent.com/NREL/resstock/{tag}/project_national/housing_characteristics/{name}.tsv"


#: State FIPS code -> postal code; ResStock's "County and PUMA" values start with G<state FIPS>.
STATE_FIPS = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE", "11": "DC",
    "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
    "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT",
    "31": "NE", "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
    "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
    "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI", "56": "WY",
}
#: Spatial tables keyed by State directly: each state row gives its value(s).
BY_STATE = ["Location Region", "Census Division", "Census Division RECS", "Custom State"]
CLIMATE_ZONE = "ASHRAE IECC Climate Zone 2004"


def _table(tag: str, name: str) -> tuple[list[str], list[list[str]]]:
    url = URL.format(tag=tag, name=urllib.parse.quote(name))
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            text = response.read().decode()
    except OSError as exc:
        sys.exit(f"Could not download {url}: {exc}")
    lines = [line.split("\t") for line in text.splitlines() if line.strip() and not line.startswith("#")]
    return lines[0], lines[1:]


def state_regions(tag: str) -> dict[str, dict[str, list[str]]]:
    """Per state: the values of each geographic dependency that some of its homes have."""
    out: dict[str, dict[str, list[str]]] = {state: {"State": [state]} for state in STATE_FIPS.values()}
    for name in BY_STATE:
        header, rows = _table(tag, name)
        for row in rows:
            if row[0] in out:
                out[row[0]][name] = [
                    h.removeprefix("Option=") for h, v in zip(header, row) if h.startswith("Option=") and float(v) > 0
                ]
    # a state spans several climate zones: a zone counts when any county+PUMA of the state is in it
    header, rows = _table(tag, "County and PUMA")
    for row in rows:
        for h, v in zip(header, row):
            if h.startswith("Option=G") and float(v) > 0:
                state = STATE_FIPS.get(h.removeprefix("Option=G")[:2])
                if state:
                    zones = out[state].setdefault(CLIMATE_ZONE, [])
                    if row[0] not in zones:
                        zones.append(row[0])
    for regions in out.values():
        regions.get(CLIMATE_ZONE, []).sort()
    return out


def main() -> None:
    preset = yaml.safe_load(PRESET.read_text())
    tag = preset["source"]["tag"]
    out = ROOT / "templates" / "resstock-odds" / tag
    out.mkdir(parents=True, exist_ok=True)
    for characteristic in preset["characteristics"]:
        name = characteristic["name"]
        url = URL.format(tag=tag, name=urllib.parse.quote(name))
        try:
            with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - fixed https URL
                (out / f"{name}.tsv").write_bytes(response.read())
        except OSError as exc:
            sys.exit(f"Could not download {url}: {exc}")
        print(f"{name}: saved")
    (out / "state-regions.yml").write_text(yaml.safe_dump(state_regions(tag), sort_keys=True))
    print("state-regions.yml: saved")
    # the tables are ResStock's, redistributed unchanged under its license (the text after the template NOTICE's intro)
    license_text = (PRESET.parent / "NOTICE").read_text().split("\n\n", 1)[1]
    (out / "NOTICE").write_text(
        f"The .tsv files here are unchanged copies of ResStock {tag} project_national/housing_characteristics/,\n"
        f"from https://github.com/NREL/resstock, under its license:\n\n{license_text}"
    )


if __name__ == "__main__":
    main()
