"""Config loading for the legacy modules.

The seven files in ``config/`` are validated against the typed schema in
``maq_core.config`` and environment overrides (``MAQ_<SECTION>__<KEY>=...``) are
applied. Only keys present in the file or set by an override are returned, so
callers keep their own ``cfg.get(key, default)`` defaults.

Escape hatch while the schema settles: ``MAQ_CONFIG_STRICT=0`` turns validation
errors into a warning and returns the file as parsed.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from maq_core.config import ConfigError, load_section
from maq_core.config.loader import read_yaml

logger = logging.getLogger(__name__)


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    try:
        return load_section(path)
    except ConfigError as error:
        if os.environ.get("MAQ_CONFIG_STRICT", "1") != "0":
            raise
        logger.warning("Using unvalidated config because MAQ_CONFIG_STRICT=0. %s", error)
        return read_yaml(path)
