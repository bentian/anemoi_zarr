"""Build anemoi-datasets compatible Zarr stores from folders of NetCDF files."""
from .builder import build
from .config import load_config

__all__ = ["build", "load_config"]
