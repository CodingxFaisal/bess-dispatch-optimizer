"""Run metadata: what code, configuration and environment produced a result."""

from __future__ import annotations

import datetime as dt
import importlib.metadata as md
import platform
import subprocess
from pathlib import Path
from typing import Any

import gridflex
from gridflex.config.loader import config_fingerprint
from gridflex.config.models import GridFlexConfig

PACKAGES = ("numpy", "pandas", "pulp", "highspy", "scikit-learn", "scipy", "pydantic")


def git_state(path: Path | None = None) -> dict[str, Any]:
    """Commit SHA and dirty flag of the checkout containing ``path`` (if any)."""
    where = path or Path(gridflex.__file__).resolve().parent
    try:
        sha = subprocess.run(
            ["git", "-C", str(where), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(where), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None, "note": "not a git checkout"}
    return {"commit": sha, "dirty": bool(status.strip())}


def package_versions() -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for name in PACKAGES:
        try:
            out[name] = md.version(name)
        except md.PackageNotFoundError:
            out[name] = None
    return out


def run_metadata(cfg: GridFlexConfig, **extra: Any) -> dict[str, Any]:
    """JSON-serialisable record of a run (extend with ``extra``)."""
    return {
        "created_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "gridflex_version": gridflex.__version__,
        "git": git_state(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": package_versions(),
        "config_fingerprint": config_fingerprint(cfg),
        "config": cfg.model_dump(mode="json"),
        **extra,
    }
