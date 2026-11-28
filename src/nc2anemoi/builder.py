"""Orchestration: read every source, align it and write the Zarr store."""
from __future__ import annotations

import logging
import os
import shutil

import numpy as np
import pandas as pd
import zarr

from .config import VarEntry, output_variables
from .grid import regrid
from .reader import read_source
from .target import Target, make_target
from .timeutils import align_time
from .writer import (compute_statistics, create_data_array, log_summary, open_output,
                     write_attributes, write_coordinates, write_statistics, write_variables)

LOG = logging.getLogger(__name__)

# Read slightly outside the period so ffill / interpolation have neighbours.
MIN_READ_BUFFER = pd.Timedelta(days=1)


def _prepare_output(path: str, overwrite: bool) -> str:
    """Remove an existing output if allowed."""
    if os.path.exists(path):
        if not overwrite:
            raise FileExistsError(f"{path} exists (use --overwrite)")
        shutil.rmtree(path)
    return path


def _process_source(src: dict, entries: list[VarEntry], target: Target,
                    data, block: int) -> dict:
    """Read, regrid and time-align one source and write it; returns variable metadata."""
    LOG.info("source %d: %s (%s)", entries[0].source, src["path"], src["pattern"])
    buffer = max(target.step, MIN_READ_BUFFER)
    lookback = buffer
    if src.get("lookback") is not None:
        lookback = max(buffer, pd.to_timedelta(src["lookback"]))
    arrays, attrs = read_source(src, target.dates[0] - lookback, target.dates[-1] + buffer)

    metadata, aligned = {}, []
    for entry in entries:
        da = regrid(arrays.pop(entry.source_name), target.lat, target.lon,
                    src["regrid_method"])
        da, used = align_time(da, target.dates, target.step, src["time_method"])
        values = da.values.reshape(target.ntime, target.ncell)

        all_nan = np.isnan(values).all(axis=1)
        if all_nan.any():
            LOG.warning(
                "%s: all-NaN timesteps: %d/%d, missing timesteps: %s",
                entry.name,
                all_nan.sum(),
                target.ntime,
                target.dates[all_nan].strftime("%Y-%m-%d %H:00").tolist(),
            )

        LOG.info("  %-12s -> %-12s time=%s, all-NaN timesteps: %d/%d", entry.source_name,
                 entry.name, used, int(np.isnan(values).all(axis=1).sum()), target.ntime)
        metadata[entry.name] = {
            "source_name": entry.source_name, "source_path": src["path"],
            "pattern": src["pattern"], "time_method": used,
            "regrid_method": src["regrid_method"], **attrs.get(entry.source_name, {}),
        }
        aligned.append(values)
    write_variables(data, entries[0].index, aligned, block)
    return metadata


def build(cfg: dict, overwrite: bool = False, block: int = 366) -> str:
    """Build the Zarr store described by ``cfg``; returns its path."""
    out_path = _prepare_output(cfg["output"], overwrite)
    target = make_target(cfg)
    entries = output_variables(cfg)

    print()
    LOG.info("period [%s -> %s], %d timesteps, step [%s]",
             target.dates[0], target.dates[-1], target.ntime, target.step)
    LOG.info("grid [%d x %d]  lat [%.4f..%.4f]  lon [%.4f..%.4f]", *target.field_shape,
             target.lat[0], target.lat[-1], target.lon[0], target.lon[-1])
    LOG.info("variables: %s", [e.name for e in entries])

    root = open_output(out_path)
    data = create_data_array(root, target, len(entries))
    write_coordinates(root, target)

    metadata = {}
    for i, src in enumerate(cfg["sources"]):
        mine = [e for e in entries if e.source == i]
        metadata.update(_process_source(src, mine, target, data, block))

    LOG.info("computing statistics")
    stats = compute_statistics(data, block)
    write_statistics(root, stats)
    write_attributes(root, cfg, target, metadata, stats)
    zarr.consolidate_metadata(out_path)

    LOG.info("written %s  data shape %s", out_path, data.shape)
    log_summary(list(metadata), stats)
    return out_path
