"""Output time axis and grid."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import parse_period
from .grid import build_target_grid


@dataclass(frozen=True)
class Target:
    """Time axis and lat/lon grid of the output dataset."""

    dates: pd.DatetimeIndex
    step: pd.Timedelta
    lat: np.ndarray
    lon: np.ndarray

    @property
    def ntime(self) -> int:
        """Number of output dates."""
        return len(self.dates)

    @property
    def ncell(self) -> int:
        """Number of grid points (flattened)."""
        return self.lat.size * self.lon.size

    @property
    def field_shape(self) -> list[int]:
        """[nlat, nlon]."""
        return [int(self.lat.size), int(self.lon.size)]

    @property
    def resolution(self) -> float | None:
        """Latitude spacing in degrees."""
        if self.lat.size < 2:
            return None
        return float(np.round(np.median(np.abs(np.diff(self.lat))), 6))


def make_target(cfg: dict) -> Target:
    """Build the output time axis and grid from the config."""
    start, end = parse_period(cfg["period"])
    step = pd.to_timedelta(cfg["frequency"])
    lat, lon = build_target_grid(cfg)
    return Target(pd.date_range(start, end, freq=step), step, lat, lon)
