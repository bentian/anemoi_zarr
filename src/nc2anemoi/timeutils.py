"""Decoding of NetCDF time axes and alignment onto the output time axis."""
from __future__ import annotations

import os
import re

import numpy as np
import pandas as pd
import xarray as xr
from pandas.tseries.frequencies import to_offset
from xarray.coding.times import decode_cf_datetime

# (lowest value, highest value, strptime format) for integer encoded dates
_INT_FORMATS = (
    (1800010100, 2200123123, "%Y%m%d%H"),
    (18000101, 22001231, "%Y%m%d"),
    (180001, 220012, "%Y%m"),
)


def year_from_filename(filename: str) -> int | None:
    """Return the first plausible 4-digit year in the file name."""
    for year in re.findall(r"(?<!\d)(\d{4})(?!\d)", os.path.basename(filename)):
        if 1800 <= int(year) <= 2200:
            return int(year)
    return None


def _decode_cf(vals: np.ndarray, units: str, calendar: str) -> pd.DatetimeIndex:
    """Decode CF '<units> since <date>' values."""
    dec = decode_cf_datetime(vals, units, calendar)
    if np.asarray(dec).dtype == object:  # cftime objects (non-standard calendar)
        dec = xr.CFTimeIndex(dec).to_datetimeindex()
    return pd.DatetimeIndex(dec)


def _decode_integer_dates(ivals: np.ndarray) -> pd.DatetimeIndex | None:
    """Decode integer dates, including YYYYMMDD24 timestamps."""
    lo, hi = ivals.min(), ivals.max()

    for lower, upper, fmt in _INT_FORMATS:
        if not (lower <= lo and hi <= upper):
            continue

        if fmt == "%Y%m%d%H":
            dates = ivals // 100
            hours = ivals % 100

            if np.any(hours > 24):
                raise ValueError(f"Invalid hourly timestamps: {ivals[hours > 24]}")

            return pd.DatetimeIndex(
                pd.to_datetime(dates.astype(str), format="%Y%m%d")
                + pd.to_timedelta(hours, unit="h")
            )

        return pd.DatetimeIndex(pd.to_datetime(ivals.astype(str), format=fmt))

    return None


def _decode_index(ivals: np.ndarray, units: str, filename: str) -> pd.DatetimeIndex:
    """Decode a 0- or 1-based step index within the year given in the file name."""
    year = year_from_filename(filename)
    if year is None:
        raise ValueError(f"cannot decode time (units='{units}') in {filename}: "
                         "no year in file name")
    base = pd.Timestamp(year=year, month=1, day=1)
    offset = ivals - (1 if ivals.min() >= 1 else 0)
    if units.startswith("hour"):
        return pd.DatetimeIndex(base + pd.to_timedelta(offset, unit="h"))
    if units.startswith("month"):
        return pd.DatetimeIndex([base + pd.DateOffset(months=int(o)) for o in offset])
    # "daily", "day", "days" or empty -> day of year
    return pd.DatetimeIndex(base + pd.to_timedelta(offset, unit="D"))


def decode_time(tvar: xr.DataArray, filename: str) -> pd.DatetimeIndex:
    """Decode a time variable opened with ``decode_times=False``.

    Handles CF times (e.g. ERA5 "hours since 1900-01-01"), integer
    YYYYMMDD[HH] / YYYYMM values and the TCCIP style ``units = "daily"``
    day-of-year index, where the year comes from the file name.
    """
    vals = np.asarray(tvar.values)
    units = str(tvar.attrs.get("units", "")).strip()
    if "since" in units.lower():
        return _decode_cf(vals, units, tvar.attrs.get("calendar", "standard"))
    ivals = vals.astype("int64")
    decoded = _decode_integer_dates(ivals)
    if decoded is not None:
        return decoded
    return _decode_index(ivals, units.lower(), filename)


def resolve_time_method(method: str, src_step: pd.Timedelta, step: pd.Timedelta) -> str:
    """Pick a concrete method for ``auto`` from the source and target steps."""
    if method != "auto":
        return method
    if src_step > step * 1.01:
        return "ffill"   # coarser source (monthly -> daily): hold value over its period
    if src_step < step * 0.99:
        return "mean"    # finer source (hourly -> daily): average
    return "exact"


def align_time(da: xr.DataArray, dates: pd.DatetimeIndex, step: pd.Timedelta,
               method: str) -> tuple[xr.DataArray, str]:
    """Put ``da`` on the output time axis; returns the array and the method used."""
    times = da.indexes["time"]
    src_step = pd.Series(times).diff().median() if len(times) > 1 else step
    method = resolve_time_method(method, src_step, step)

    if method == "exact":
        out = da.reindex(time=dates)
    elif method == "nearest":
        out = da.reindex(time=dates, method="nearest", tolerance=step / 2)
    elif method == "ffill":
        out = da.reindex(time=dates, method="ffill", tolerance=src_step * 1.5)
    elif method == "interpolate":
        out = da.interp(time=dates)
    elif method == "mean":
        freq = to_offset(step).freqstr
        out = da.resample(time=freq, label="left", closed="left").mean().reindex(time=dates)
    else:
        raise ValueError(f"unknown time method '{method}'")
    return out.astype("float32"), method
