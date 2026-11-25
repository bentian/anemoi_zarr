"""Command-line interface."""
from __future__ import annotations

import argparse
import importlib
import logging

from .builder import build
from .config import load_config

LOG = logging.getLogger(__name__)

DESCRIPTION = """\
Build an anemoi-datasets compatible Zarr store from folders of NetCDF files.
All variables are aligned on one lat/lon grid and one time axis.
"""


def check_with_anemoi(path: str) -> None:
    """Open the result with anemoi-datasets, if it is installed."""
    try:
        datasets = importlib.import_module("anemoi.datasets")
    except ImportError:
        LOG.info("anemoi-datasets not installed; skipping open_dataset check")
        return
    ds = datasets.open_dataset(path)
    LOG.info("anemoi open_dataset OK: %s %s %s", ds.shape, ds.variables, ds.frequency)


def main(argv=None) -> int:
    """Parse arguments and build the dataset."""
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("config", help="YAML config file")
    parser.add_argument("--overwrite", action="store_true", help="replace existing output")
    parser.add_argument("--block", type=int, default=366, help="time steps per write block")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    out = build(load_config(args.config), overwrite=args.overwrite, block=args.block)
    check_with_anemoi(out)
    return 0
