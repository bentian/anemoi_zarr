"""Writing the anemoi Zarr layout: arrays, statistics and attributes."""
from __future__ import annotations

import datetime as dt
import logging

import numpy as np
import zarr

from .config import anemoi_frequency
from .target import Target

LOG = logging.getLogger(__name__)

ZARR3 = int(zarr.__version__.split(".", maxsplit=1)[0]) >= 3
STAT_NAMES = ("mean", "stdev", "minimum", "maximum", "sums", "squares", "count", "has_nans")


def open_output(path: str):
    """Open a new Zarr (format 2, as read by anemoi-datasets) group for writing."""
    if ZARR3:
        return zarr.open_group(path, mode="w")
    return zarr.open_group(path, mode="w")


def new_array(root, name: str, data=None, **kwargs):
    """Create an array (zarr 2 and 3 compatible), optionally filled with ``data``."""
    if data is not None:
        kwargs.setdefault("chunks", data.shape)
        kwargs.update(shape=data.shape, dtype=data.dtype)
    create = root.create_array if hasattr(root, "create_array") else root.create_dataset
    arr = create(name, **kwargs)
    if data is not None:
        arr[...] = data
    return arr


def create_data_array(root, target: Target, nvar: int):
    """Create the (time, variable, ensemble, cell) ``data`` array."""
    return new_array(root, "data", shape=(target.ntime, nvar, 1, target.ncell),
                     chunks=(1, nvar, 1, target.ncell), dtype="float32",
                     fill_value=np.nan)


def write_coordinates(root, target: Target) -> None:
    """Write ``dates``, ``latitudes`` and ``longitudes`` (cell = ilat * nlon + ilon)."""
    new_array(root, "dates", data=target.dates.values.astype("datetime64[s]"))
    lat2d, lon2d = np.meshgrid(target.lat, target.lon, indexing="ij")
    new_array(root, "latitudes", data=lat2d.ravel().astype("float64"))
    new_array(root, "longitudes", data=lon2d.ravel().astype("float64"))


def write_variables(data, first: int, arrays: list[np.ndarray], block: int) -> None:
    """Write contiguous variables (each shaped (time, cell)) starting at index ``first``."""
    ntime = data.shape[0]
    for t0 in range(0, ntime, block):
        t1 = min(t0 + block, ntime)
        stacked = np.stack([a[t0:t1] for a in arrays], axis=1)
        data[t0:t1, first:first + len(arrays), 0, :] = stacked


class RunningStats:
    """NaN-aware per-variable statistics accumulated block by block."""

    def __init__(self, nvar: int):
        self.count = np.zeros(nvar, "int64")
        self.sums = np.zeros(nvar, "float64")
        self.squares = np.zeros(nvar, "float64")
        self.minimum = np.full(nvar, np.inf)
        self.maximum = np.full(nvar, -np.inf)
        self.has_nans = np.zeros(nvar, bool)

    def update(self, values: np.ndarray) -> None:
        """Add a (variable, n) block."""
        valid = ~np.isnan(values)
        self.has_nans |= ~valid.all(axis=1)
        self.count += valid.sum(axis=1)
        self.sums += np.nansum(values, axis=1)
        self.squares += np.nansum(values * values, axis=1)
        self.minimum = np.fmin(self.minimum, np.where(valid, values, np.inf).min(axis=1))
        self.maximum = np.fmax(self.maximum, np.where(valid, values, -np.inf).max(axis=1))

    def result(self) -> dict[str, np.ndarray]:
        """Final statistics keyed by anemoi array name."""
        with np.errstate(all="ignore"):
            mean = self.sums / self.count
            stdev = np.sqrt(np.maximum(self.squares / self.count - mean ** 2, 0.0))
        empty = self.count == 0
        return {
            "mean": mean, "stdev": stdev,
            "minimum": np.where(empty, np.nan, self.minimum),
            "maximum": np.where(empty, np.nan, self.maximum),
            "sums": self.sums, "squares": self.squares,
            "count": self.count, "has_nans": self.has_nans,
        }


def compute_statistics(data, block: int) -> dict[str, np.ndarray]:
    """Statistics of the whole ``data`` array, read in time blocks."""
    ntime, nvar = data.shape[:2]
    stats = RunningStats(nvar)
    for t0 in range(0, ntime, block):
        values = data[t0:min(t0 + block, ntime), :, 0, :].astype("float64")
        stats.update(np.moveaxis(values, 1, 0).reshape(nvar, -1))
    return stats.result()


def write_statistics(root, stats: dict[str, np.ndarray]) -> None:
    """Write the statistics arrays."""
    for name in STAT_NAMES:
        new_array(root, name, data=stats[name])


def write_attributes(root, cfg: dict, target: Target, metadata: dict, stats: dict) -> None:
    """Write the anemoi attributes; ``metadata`` is ordered like the variables."""
    names = list(metadata)
    start, end = target.dates[0].isoformat(), target.dates[-1].isoformat()
    root.attrs.update({
        "version": "0.30",
        "description": cfg.get("description", ""),
        "licence": cfg.get("licence", "unknown"),
        "attribution": cfg.get("attribution", "unknown"),
        "start_date": start,
        "end_date": end,
        "frequency": anemoi_frequency(target.step),
        "resolution": target.resolution,
        "field_shape": target.field_shape,
        "flatten_grid": True,
        "ensemble_dimension": 1,
        "variables": names,
        "name_to_index": {n: k for k, n in enumerate(names)},
        "variables_metadata": metadata,
        "allow_nans": bool(stats["has_nans"].any()),
        "missing_dates": [],
        "statistics_start_date": start,
        "statistics_end_date": end,
        "recipe": cfg,
        "created_by": "nc2anemoi",
        "latest_write_timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
    })


def log_summary(names: list[str], stats: dict) -> None:
    """Log per-variable statistics."""
    for k, name in enumerate(names):
        if stats["count"][k] == 0:
            LOG.warning("variable '%s' has no valid values in the period", name)
        LOG.info("  %-12s mean=%10.4g std=%10.4g min=%10.4g max=%10.4g nans=%s",
                 name, stats["mean"][k], stats["stdev"][k], stats["minimum"][k],
                 stats["maximum"][k], stats["has_nans"][k])
