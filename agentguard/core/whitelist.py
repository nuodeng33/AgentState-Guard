"""Path whitelist with traversal protection."""

from pathlib import Path
from typing import List, Optional, Set


class Whitelist:
    """Manages allowed file paths for restore operations."""

    def __init__(self, allowed_paths: Optional[List[str]] = None):
        self._allowed: List[Path] = []
        for p in (allowed_paths or []):
            self._allowed.append(Path(p).resolve())

    def add(self, path: str) -> None:
        self._allowed.append(Path(path).resolve())

    def is_allowed(self, target: Path) -> bool:
        """Check if target path is within any allowed directory or is an allowed file."""
        resolved = self._resolve_safe(target)
        if resolved is None:
            return False
        for allowed in self._allowed:
            if resolved == allowed or resolved.is_relative_to(allowed):
                return True
        return False

    def allowed_file(self, target: Path) -> Optional[Path]:
        """Return the resolved path if allowed, None otherwise."""
        resolved = self._resolve_safe(target)
        if resolved is None:
            return None
        if not self.is_allowed(resolved):
            return None
        return resolved

    def _resolve_safe(self, target: Path) -> Optional[Path]:
        """Resolve path and check for traversal.

        Returns None if:
        - Path contains '..' traversal outside the resolved root
        - Path is absolute and outside the working directory
        - Path is a symlink to a disallowed location
        """
        try:
            resolved = target.resolve(strict=False)
        except (OSError, RuntimeError):
            return None

        # Check for symlink traversal: follow symlinks and verify
        if target.is_symlink():
            try:
                real = target.resolve(strict=True)
                if not self.is_allowed(real):
                    return None
            except (OSError, RuntimeError):
                return None

        # Prevent absolute paths to system directories
        forbidden_prefixes = [
            Path("/etc"), Path("/var"), Path("/proc"), Path("/sys"),
            Path("/dev"), Path("/boot"), Path("/root"),
        ]
        for prefix in forbidden_prefixes:
            try:
                if resolved.is_relative_to(prefix):
                    return None
            except (OSError, RuntimeError):
                continue

        return resolved

    @staticmethod
    def default_restore_paths() -> List[str]:
        """Return default allowed paths for restore operations."""
        home = Path.home()
        return [
            str(home / ".claude" / "settings.json"),
            str(home / ".claude" / "settings.local.json"),
            str(home / ".claude" / "skills"),
        ]

    @staticmethod
    def forbid_system_paths() -> List[str]:
        """Return system paths that are never allowed."""
        return [
            "/etc/", "/var/lib/docker/", "/mnt/",
            "/proc/", "/sys/", "/dev/",
        ]


def check_path_traversal(path: Path) -> bool:
    """Return True if path attempts traversal outside its parent."""
    try:
        raw = str(path)
        # Check raw string for '..' components before resolution
        if "/../" in raw or raw.startswith("../") or raw.endswith("/.."):
            return True
        resolved = path.resolve()
        # Check if any '..' component would escape
        parts = path.parts
        depth = 0
        for part in parts:
            if part == "..":
                depth -= 1
            elif part != "." and part != "":
                depth += 1
            if depth < 0:
                return True
        return False
    except (OSError, RuntimeError):
        return True
