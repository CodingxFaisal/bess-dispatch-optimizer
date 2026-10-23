"""Raw-payload cache and its invalidation policy.

Layout (one directory per series and resolution)::

    <root>/smard/<region>/<series_id>/<resolution>/index.json
    <root>/smard/<region>/<series_id>/<resolution>/index.meta.json
    <root>/smard/<region>/<series_id>/<resolution>/chunk_<start_ms>.json
    <root>/smard/<region>/<series_id>/<resolution>/chunk_<start_ms>.meta.json

``*.json`` files are the bytes exactly as served by SMARD (raw preservation).
``*.meta.json`` sidecars record the URL, UTC retrieval time, SHA-256 of the
payload, HTTP attempts, point/null counts and SMARD's own ``created`` stamp.
A payload whose hash no longer matches its sidecar is treated as corrupt.

Invalidation policy (online mode; ``refresh=True`` bypasses all of it):

* **Index** files are re-downloaded when older than ``index_max_age_minutes``.
* A **chunk** is *final* when it contains no null values and was retrieved at
  least ``finalisation_lag_days`` after the chunk ended. Final chunks are never
  re-downloaded, so later SMARD revisions are only picked up with ``refresh``.
* Any other chunk is re-downloaded when older than
  ``open_chunk_max_age_minutes``.

Offline mode never touches the network: cached files are used whatever their
age (non-final, expired ones are flagged ``stale``) and a missing file raises
:class:`~gridflex.data.errors.DataUnavailableError`.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from gridflex.config.models import CachePolicy
from gridflex.data.errors import DataIntegrityError

CACHE_FORMAT_VERSION = 1


@dataclass(frozen=True)
class CacheEntryMeta:
    """Provenance of one cached raw payload."""

    url: str
    retrieved_at_utc: str
    sha256: str
    status_code: int
    attempts: int
    n_points: int | None = None
    n_null: int | None = None
    source_created_utc: str | None = None
    format_version: int = CACHE_FORMAT_VERSION

    @property
    def retrieved_at(self) -> dt.datetime:
        return dt.datetime.fromisoformat(self.retrieved_at_utc)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CacheEntryMeta:
        return cls(**raw)


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class RawCache:
    """Filesystem store for raw SMARD payloads plus provenance sidecars."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def series_dir(self, region: str, series_id: int, resolution: str) -> Path:
        return self.root / "smard" / region / str(series_id) / resolution

    def index_path(self, region: str, series_id: int, resolution: str) -> Path:
        return self.series_dir(region, series_id, resolution) / "index.json"

    def chunk_path(
        self, region: str, series_id: int, resolution: str, chunk_start_ms: int
    ) -> Path:
        return (
            self.series_dir(region, series_id, resolution)
            / f"chunk_{chunk_start_ms}.json"
        )

    @staticmethod
    def meta_path(path: Path) -> Path:
        return path.with_name(path.stem + ".meta.json")

    def read(self, path: Path) -> tuple[bytes, CacheEntryMeta] | None:
        """Return payload and metadata, ``None`` if absent; raise if corrupt."""
        meta_file = self.meta_path(path)
        if not path.exists() or not meta_file.exists():
            return None
        content = path.read_bytes()
        try:
            meta = CacheEntryMeta.from_dict(json.loads(meta_file.read_text("utf-8")))
        except (json.JSONDecodeError, TypeError) as exc:
            raise DataIntegrityError(
                f"unreadable cache metadata {meta_file}: {exc}"
            ) from exc
        if sha256_hex(content) != meta.sha256:
            raise DataIntegrityError(
                f"cache file {path} does not match its recorded SHA-256; "
                "delete it or re-run with refresh=True"
            )
        return content, meta

    def write(self, path: Path, content: bytes, meta: CacheEntryMeta) -> None:
        """Atomically write payload then sidecar (sidecar last marks completion)."""
        if sha256_hex(content) != meta.sha256:
            raise ValueError("metadata hash does not match content")
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, content)
        _atomic_write(
            self.meta_path(path),
            json.dumps(asdict(meta), indent=2, sort_keys=True).encode("utf-8"),
        )


def _atomic_write(path: Path, content: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(content)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# Policy                                                                      #
# --------------------------------------------------------------------------- #
def index_is_fresh(meta: CacheEntryMeta, now: dt.datetime, policy: CachePolicy) -> bool:
    age = now - meta.retrieved_at
    return age <= dt.timedelta(minutes=policy.index_max_age_minutes)


def chunk_is_final(
    meta: CacheEntryMeta, chunk_end_utc: dt.datetime, policy: CachePolicy
) -> bool:
    if meta.n_null is None or meta.n_null > 0:
        return False
    return meta.retrieved_at >= chunk_end_utc + dt.timedelta(
        days=policy.finalisation_lag_days
    )


def chunk_is_fresh(
    meta: CacheEntryMeta,
    chunk_end_utc: dt.datetime,
    now: dt.datetime,
    policy: CachePolicy,
) -> bool:
    if chunk_is_final(meta, chunk_end_utc, policy):
        return True
    return now - meta.retrieved_at <= dt.timedelta(
        minutes=policy.open_chunk_max_age_minutes
    )
