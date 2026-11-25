#!/usr/bin/env python3
"""Entry point: ``python build_anemoi_zarr.py config.yaml [--overwrite] [--block 366]``.

The implementation lives in the ``nc2anemoi`` package:
    config.py     config loading / validation, period and frequency parsing
    timeutils.py  NetCDF time decoding and time-axis alignment
    reader.py     reading variables from folders of NetCDF files
    grid.py       target grid and regridding
    target.py     output time axis + grid
    writer.py     anemoi Zarr arrays, statistics and attributes
    builder.py    orchestration
    cli.py        command-line interface
"""
import sys

from nc2anemoi.cli import main

if __name__ == "__main__":
    sys.exit(main())
