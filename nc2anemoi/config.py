"""Configuration loading, validation and period / frequency parsing."""
from __future__ import annotations

import re
from typing import NamedTuple

import pandas as pd
import yaml

TIME_METHODS = ("auto", "exact", "ffill", "nearest", "interpolate", "mean")
REQUIRED_KEYS = ("output", "period", "sources")


class VarEntry(NamedTuple):
    """One output variable and where it comes from."""

    index: int          # position in the output ``variables`` list
    name: str           # output name (after rename)
    source_name: str    # internal source key (e.g. v_850)
    source: int         # index of the source in the config


def load_config(path: str) -> dict:
    """Load the YAML config, fill in defaults and validate it."""
    with open(path, "r", encoding="utf-8") as stream:
        cfg = yaml.safe_load(stream)

    for key in REQUIRED_KEYS:
        if key not in cfg:
            raise ValueError(f"config is missing '{key}'")
    if len(cfg["period"]) != 2:
        raise ValueError("'period' must be [start, end], e.g. ['200001', '201512']")

    cfg.setdefault("frequency", "1D")
    cfg.setdefault("grid", {"reference": 0})
    cfg["grid"].setdefault("method", "linear")

    for i, src in enumerate(cfg["sources"]):
        _validate_source(i, src, cfg["grid"]["method"])

    names = [entry.name for entry in output_variables(cfg)]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise ValueError(f"duplicate output variable names {dup}; use 'rename' in the config")
    return cfg


def _validate_source(i: int, src: dict, default_regrid: str) -> None:
    """Check one ``sources`` entry and fill in its defaults."""
    if "path" not in src or "variables" not in src:
        raise ValueError(f"source #{i} needs 'path' and 'variables'")

    # Variables and pressure levels
    if isinstance(src["variables"], str):
        src["variables"] = [src["variables"]]
    variable_specs(src)  # validate variable/level mapping
    src.setdefault("level_coord", "level")

    # File patterns
    src.setdefault("pattern", "*.nc")
    if isinstance(src["pattern"], str):
        src["pattern"] = [src["pattern"]]
    if not (
        isinstance(src["pattern"], list)
        and src["pattern"]
        and all(isinstance(p, str) for p in src["pattern"])
    ):
        raise ValueError("'pattern' must be a string or a non-empty list of strings")

    # Others
    src.setdefault("rename", {})
    src.setdefault("time_method", "auto")
    src.setdefault("regrid_method", default_regrid)
    if src["time_method"] not in TIME_METHODS:
        raise ValueError(f"source #{i}: time_method must be one of {TIME_METHODS}")


def variable_specs(src: dict):
    """Return (NetCDF name, pressure in hPa or None, internal key)."""
    variables = src["variables"]
    if isinstance(variables, str):
        variables = [variables]
    if isinstance(variables, list):
        items = [(v, None) for v in variables]
    elif isinstance(variables, dict):
        items = variables.items()
    else:
        raise ValueError("variables must be a list or mapping")
    specs = []
    for var, levels in items:
        if not isinstance(var, str) or not var:
            raise ValueError("variable names must be nonempty strings")
        if levels is None:
            specs.append((var, None, var))
            continue
        if not isinstance(levels, list) or not levels:
            raise ValueError(f"{var}: expected a nonempty list of pressure levels or null")
        for level in levels:
            if type(level) is not int or level <= 0:
                raise ValueError(f"{var}: pressure levels must be positive integer hPa")
            specs.append((var, level, f"{var}_{level}"))
    keys = [key for _, _, key in specs]
    if not keys or len(keys) != len(set(keys)):
        raise ValueError("empty or duplicate expanded source variables")
    return specs


def output_variables(cfg: dict) -> list[VarEntry]:
    """Flatten all sources into the ordered list of output variables."""
    entries = []
    for i, src in enumerate(cfg["sources"]):
        rename = src.get("rename", {})
        for var, level, key in variable_specs(src):
            base = rename.get(var, var)
            name = base if level is None else f"{base}_{level}"
            entries.append(VarEntry(len(entries), rename.get(key, name), key, i))
    return entries


def _parse_date(text, is_end: bool) -> pd.Timestamp:
    """Parse YYYYMM / YYYYMMDD / ISO; an end date covers its whole month or day."""
    text = str(text).strip()
    one_second = pd.Timedelta(seconds=1)
    if re.fullmatch(r"\d{6}", text):
        stamp = pd.Timestamp(year=int(text[:4]), month=int(text[4:]), day=1)
        if is_end:
            stamp = stamp + pd.offsets.MonthEnd(0) + pd.Timedelta(days=1) - one_second
        return stamp
    if re.fullmatch(r"\d{8}", text):
        stamp = pd.Timestamp(text)
        return stamp + pd.Timedelta(days=1) - one_second if is_end else stamp
    return pd.Timestamp(text)


def parse_period(period) -> tuple[pd.Timestamp, pd.Timestamp]:
    """'200001' -> 2000-01-01 00:00; end '201512' -> 2015-12-31 23:59:59."""
    start, end = _parse_date(period[0], False), _parse_date(period[1], True)
    if end < start:
        raise ValueError(f"period end {end} is before start {start}")
    return start, end


def anemoi_frequency(step: pd.Timedelta) -> str:
    """Convert a Timedelta to the anemoi frequency string ('1d', '6h', ...)."""
    hours = step / pd.Timedelta(hours=1)
    if hours != int(hours):
        raise ValueError(f"frequency {step} must be a whole number of hours")
    hours = int(hours)
    return f"{hours // 24}d" if hours % 24 == 0 else f"{hours}h"
