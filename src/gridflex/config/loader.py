"""Load, validate and fingerprint GridFlex configuration files."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from gridflex.config.models import GridFlexConfig


class ConfigError(ValueError):
    """A configuration file is missing, unreadable or invalid."""


def load_config(path: str | Path) -> GridFlexConfig:
    """Read a YAML file and validate it into a :class:`GridFlexConfig`."""
    path = Path(path)
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"config file is not valid YAML: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"config file must contain a mapping at top level: {path}")
    try:
        return GridFlexConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration in {path}:\n{exc}") from exc


def config_fingerprint(cfg: GridFlexConfig) -> str:
    """SHA-256 of the canonical JSON form; identical configs hash identically."""
    canonical = json.dumps(
        cfg.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
