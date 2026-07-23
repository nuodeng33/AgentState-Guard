"""File hashing with multiple algorithms."""

import hashlib
from pathlib import Path
from typing import Dict, Optional


def hash_file(path: Path, algorithm: str = "sha256") -> Optional[str]:
    """Compute file hash. Returns None if file doesn't exist or can't be read."""
    if not path.is_file():
        return None
    try:
        h = hashlib.new(algorithm)
        with path.open("rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()
    except (OSError, PermissionError, ValueError):
        return None


def hash_bytes(data: bytes, algorithm: str = "sha256") -> str:
    """Compute hash of raw bytes."""
    h = hashlib.new(algorithm)
    h.update(data)
    return h.hexdigest()


def hash_text(text: str, algorithm: str = "sha256") -> str:
    """Compute hash of a string (UTF-8 encoded)."""
    return hash_bytes(text.encode("utf-8"), algorithm)


def check_known_hash(path: Path, known_hash: str, algorithm: str = "sha256") -> bool:
    """Verify a file matches a known hash."""
    current = hash_file(path, algorithm)
    if current is None:
        return False
    return current == known_hash
