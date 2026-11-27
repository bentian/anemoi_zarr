"""Target grid definition and spatial regridding."""
from __future__ import annotations

import numpy as np
import xarray as xr

from .reader import read_grid


def _axis(spec) -> np.ndarray:
    """[start, stop, step] (inclusive) or an explicit list of coordinates."""
    if len(spec) == 3:
        start, stop, step = map(float, spec)
        count = int(round((stop - start) / step)) + 1
        return np.round(start + np.arange(count) * step, 8)
    return np.asarray(spec, dtype="float64")


def _to_lon_convention(lon: np.ndarray, bounds: tuple[float, float]) -> np.ndarray:
    """Convert native longitudes to the [0, 360) / [-180, 180) convention of ``bounds``."""
    low, high = bounds
    if high <= 180 < lon.max():
        return np.sort(((lon + 180) % 360) - 180)
    if low < 0 <= lon.min():
        return np.sort(lon % 360)
    return lon


def _crop(native: np.ndarray, bounds, name: str) -> np.ndarray:
    """Native coordinates within [start, stop] (inclusive), keeping the native step."""
    low, high = sorted(map(float, bounds))
    if name == "lon":
        native = _to_lon_convention(native, (low, high))
    tol = 1e-6
    sub = native[(native >= low - tol) & (native <= high + tol)]
    if sub.size == 0:
        raise ValueError(f"grid.{name} {list(bounds)} does not overlap the reference grid "
                         f"({native.min()}..{native.max()})")
    return sub


def build_target_grid(cfg: dict) -> tuple[np.ndarray, np.ndarray]:
    """Target lat/lon.

    ``grid.lat`` / ``grid.lon`` may each be:
      * ``[start, stop, step]`` – regular axis with the given step
      * ``[start, stop]``       – crop the reference source grid, keeping its native step
      * a longer list           – explicit coordinates
    Without ``lat`` / ``lon`` the full grid of ``grid.reference`` is used.
    """
    grid_cfg = cfg["grid"]
    ref = int(grid_cfg.get("reference", 0))
    specs = {name: grid_cfg.get(name) for name in ("lat", "lon")}
    if any(spec is None for spec in specs.values()):
        if any(spec is not None for spec in specs.values()):
            raise ValueError("grid: set both 'lat' and 'lon', or neither")
        return read_grid(cfg["sources"][ref])

    native = None
    axes = []
    for name, spec in specs.items():
        if len(spec) == 2:
            if native is None:
                native = dict(zip(("lat", "lon"), read_grid(cfg["sources"][ref])))
            axes.append(_crop(native[name], spec, name))
        else:
            axes.append(np.sort(_axis(spec)))
    return axes[0], axes[1]


def _match_lon_convention(da: xr.DataArray, tlon: np.ndarray) -> xr.DataArray:
    """Convert source longitudes between [0, 360) and [-180, 180) to match the target."""
    lon = da["lon"].values
    if tlon.max() <= 180 < lon.max():
        return da.assign_coords(lon=((lon + 180) % 360) - 180).sortby("lon")
    if lon.min() < 0 <= tlon.min():
        return da.assign_coords(lon=lon % 360).sortby("lon")
    return da


def same_grid(da: xr.DataArray, tlat: np.ndarray, tlon: np.ndarray) -> bool:
    """True if ``da`` is already on the target grid."""
    return (da.sizes["lat"] == tlat.size and da.sizes["lon"] == tlon.size
            and np.allclose(da["lat"], tlat, atol=1e-5)
            and np.allclose(da["lon"], tlon, atol=1e-5))


def regrid(da: xr.DataArray, tlat: np.ndarray, tlon: np.ndarray, method: str) -> xr.DataArray:
    """Interpolate ``da`` onto the target grid; points outside its domain become NaN."""
    da = _match_lon_convention(da, tlon)
    if same_grid(da, tlat, tlon):
        return da.assign_coords(lat=tlat, lon=tlon)
    return da.interp(lat=tlat, lon=tlon, method=method).astype("float32")
