"""Content addressing of a media file on disk: its SHA-256, which media discovery uses as the file's identity.

The old runtime's metrics and manifests were removed with it in restore-9 (spec restoration-for-every-upload,
design section 15); a restoration's records are the contract's (`services/contracts/media.py`).
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def compute_file_sha256(file_path: str | Path) -> str:
    """Compute the SHA-256 hex digest of a file on disk without mutating it."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
