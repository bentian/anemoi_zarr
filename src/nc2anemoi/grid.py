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


def build_target_grid(cfg: dict) -> tuple[np.ndarray, np.ndarray]:
    """Target lat/lon from explicit ``grid.lat/lon`` or a reference source."""
    grid_cfg = cfg["grid"]
    if "lat" in grid_cfg and "lon" in grid_cfg:
        return np.sort(_axis(grid_cfg["lat"])), np.sort(_axis(grid_cfg["lon"]))
    ref = int(grid_cfg.get("reference", 0))
    return read_grid(cfg["sources"][ref])


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
