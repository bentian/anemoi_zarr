"""Allow ``python -m nc2anemoi config.yaml``."""
import sys

from .cli import main

sys.exit(main())
