"""Reading variables from folders of NetCDF files."""
from __future__ import annotations

import glob
import logging
import os

import numpy as np
import pandas as pd
import xarray as xr

from .timeutils import decode_time
from .config import variable_specs

LOG = logging.getLogger(__name__)

LAT_NAMES = ("lat", "latitude", "y", "nav_lat")
LON_NAMES = ("lon", "longitude", "x", "nav_lon")
TIME_NAMES = ("time", "valid_time", "t")
KEPT_ATTRS = ("units", "long_name", "standard_name")


def find_name(ds: xr.Dataset, candidates, what: str, filename: str) -> str:
    """Return the first of ``candidates`` present in ``ds``."""
    for name in candidates:
        if name in ds.dims or name in ds.variables:
            return name
    raise KeyError(f"no {what} coordinate ({'/'.join(candidates)}) in {filename}")

def list_files(src: dict) -> list[str]:
    """List NetCDF files matching one or more glob patterns."""
    patterns = src.get("pattern", "*.nc")
    if isinstance(patterns, str):
        patterns = [patterns]

    files = sorted({
        f
        for pattern in patterns
        for f in glob.glob(
            os.path.join(src["path"], pattern),
            recursive=True,
        )
    })
    if not files:
        raise FileNotFoundError(f"No files found: {src['path']}, patterns={patterns}")

    return files


def standardise(da: xr.DataArray, names: dict, varname: str, filename: str) -> xr.DataArray:
    """Rename dims to (time, lat, lon), drop singleton dims, sort lat/lon ascending."""
    da = da.rename({k: v for k, v in names.items() if k != v and k in da.dims})
    for dim in [d for d in da.dims if d not in ("time", "lat", "lon")]:
        if da.sizes[dim] != 1:
            raise ValueError(
                f"'{varname}' in {filename} has extra dimension '{dim}' "
                f"(size {da.sizes[dim]}); select a single level first"
            )
        da = da.isel({dim: 0}, drop=True)
    if "time" not in da.dims:
        raise ValueError(f"'{varname}' in {filename} has no time dimension")
    da = da.reset_coords(drop=True)
    da = da.assign_coords(lat=da["lat"].values.astype("float64"),
                          lon=da["lon"].values.astype("float64"))
    return da.transpose("time", "lat", "lon").sortby("lat").sortby("lon")


def read_grid(src: dict) -> tuple[np.ndarray, np.ndarray]:
    """Ascending lat/lon of the first file holding one of the source variables."""
    for path in list_files(src):
        with xr.open_dataset(path, decode_times=False) as ds:
            if any(v in ds.data_vars for v, _, _ in variable_specs(src)):
                lat = ds[find_name(ds, LAT_NAMES, "latitude", path)].values
                lon = ds[find_name(ds, LON_NAMES, "longitude", path)].values
                return np.sort(lat.astype("float64")), np.sort(lon.astype("float64"))
    raise ValueError(f"none of {src['variables']} found under {src['path']}")


def select_level(ds: xr.Dataset, da: xr.DataArray, coord: str,
                 level: int, filename: str) -> xr.DataArray:
    """Select an exact pressure in hPa, including auxiliary coordinates."""
    if coord not in ds.variables:
        raise ValueError(f"{filename}: missing pressure coordinate {coord!r}")
    pressure = ds[coord]
    if pressure.ndim != 1 or pressure.dims[0] not in da.dims:
        raise ValueError(f"{filename}: {coord!r} must be a 1D coordinate of {da.name}")
    units = str(pressure.attrs.get("units", "")).strip().lower()
    values = np.asarray(pressure.values, dtype=float)
    if units in ("pa", "pascal", "pascals"):
        values = values / 100
    elif units not in ("hpa", "hectopascal", "hectopascals", "mb", "mbar", "millibar", "millibars"):
        raise ValueError(f"{filename}: unsupported pressure units {units!r}")
    idx = np.flatnonzero(np.isclose(values, level, rtol=0, atol=1e-4))
    if idx.size != 1:
        raise ValueError(f"{filename}: {da.name} requires exactly one {level} hPa level; found {idx.size}")
    return da.isel({pressure.dims[0]: int(idx[0])}, drop=True)


def _read_file(path: str, src: dict, t0: pd.Timestamp,
               t1: pd.Timestamp) -> list[tuple[str, xr.DataArray, dict]]:
    """Read the requested variables of one file within [t0, t1]."""
    # decode_times=False still applies _FillValue / missing_value masking
    with xr.open_dataset(path, decode_times=False) as ds:
        present = [(v, level, key) for v, level, key in variable_specs(src)
                   if v in ds.data_vars]
        if not present:
            return []
        tname = find_name(ds, TIME_NAMES, "time", path)
        times = decode_time(ds[tname], path)
        idx = np.where((times >= t0) & (times <= t1))[0]
        if idx.size == 0:
            return []
        names = {tname: "time",
                 find_name(ds, LAT_NAMES, "latitude", path): "lat",
                 find_name(ds, LON_NAMES, "longitude", path): "lon"}
        LOG.info("  %s: %s, %d steps", os.path.basename(path), present, idx.size)
        out = []
        for var, level, key in present:
            da = ds[var]
            if level is not None:
                da = select_level(ds, da, src.get("level_coord", "level"), level, path)
            da = da.isel({tname: idx}).assign_coords({tname: times[idx]})
            da = standardise(da, names, var, path).astype("float32").load()
            attrs = {k: str(a) for k, a in ds[var].attrs.items() if k in KEPT_ATTRS}
            attrs["source_name"] = var
            if level is not None:
                attrs["pressure_level"] = level
                attrs["pressure_level_units"] = "hPa"
            out.append((key, da, attrs))
    return out


def _combine(var: str, pieces: list[xr.DataArray], src: dict) -> xr.DataArray:
    """Concatenate the per-file pieces of one variable along time."""
    ref = pieces[0]
    for piece in pieces[1:]:
        same = (piece.shape[1:] == ref.shape[1:]
                and np.allclose(piece.lat, ref.lat, atol=1e-5)
                and np.allclose(piece.lon, ref.lon, atol=1e-5))
        if not same:
            raise ValueError(f"variable '{var}': files under {src['path']} use different grids")
    da = xr.concat(pieces, dim="time", join="override") if len(pieces) > 1 else ref
    return da.sortby("time").drop_duplicates("time")


def read_source(src: dict, t0: pd.Timestamp, t1: pd.Timestamp):
    """Return ({variable: DataArray(time, lat, lon)}, {variable: attrs}) for [t0, t1]."""
    pieces = {key: [] for _, _, key in variable_specs(src)}
    attrs = {}
    for path in list_files(src):
        for var, da, var_attrs in _read_file(path, src, t0, t1):
            pieces[var].append(da)
            attrs.setdefault(var, var_attrs)

    arrays = {}
    for var, lst in pieces.items():
        if not lst:
            raise ValueError(f"variable '{var}' not found in {src['path']}/{src['pattern']} "
                             f"between {t0} and {t1}")
        arrays[var] = _combine(var, lst, src)
    return arrays, attrs
