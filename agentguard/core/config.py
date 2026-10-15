"""Configuration loader for agentguard.toml using Python 3.11+ tomllib."""

import os
import tomllib
from pathlib import Path
from typing import Any

from ..core.sanitizer import sanitize_dict

DEFAULT_CONFIG: dict[str, Any] = {
    "checks": {
        "commands": ["docker", "node", "python3", "git"],
        "container_name": "agent-dev",
        "port": 3001,
    },
    "security": {
        "restore_whitelist": [],
        "sanitize_patterns": [],
        "ignore_directories": [
            "__pycache__", ".git", "node_modules", ".npm-cache",
        ],
    },
    "paths": {
        "state_dir": ".agentguard",
        "snapshot_dir": ".agentguard/snapshots",
        "docs_dir": "docs",
        "reports_dir": "reports",
        "config_file": "config/agentguard.toml",
    },
    "docs": {
        "current_state": "docs/CURRENT_STATE.md",
        "next_steps": "docs/NEXT_STEPS.md",
        "decisions": "docs/DECISIONS.md",
        "changelog": "docs/CHANGELOG.md",
    },
}

PRODUCT_CONFIG_TEMPLATE = """# AgentState Guard product configuration

[checks]
commands = ["docker", "node", "python3", "git"]
container_name = "agent-dev"
port = 3001

[security]
restore_whitelist = []
sanitize_patterns = []
ignore_directories = ["__pycache__", ".git", "node_modules", ".npm-cache", ".agentguard"]

[paths]
state_dir = ".agentguard"
snapshot_dir = ".agentguard/snapshots"
docs_dir = "docs"
reports_dir = "reports"
config_file = "config/agentguard.toml"

[docs]
current_state = "docs/CURRENT_STATE.md"
next_steps = "docs/NEXT_STEPS.md"
decisions = "docs/DECISIONS.md"
changelog = "docs/CHANGELOG.md"
"""


class Config:
    """Project configuration loaded from TOML with defaults."""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir.resolve()
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        # Try TOML first, then YAML for backward compat
        toml_path = self.base_dir / "config" / "agentguard.toml"
        yaml_path = self.base_dir / "config" / "agentguard.yaml"

        merged = dict(DEFAULT_CONFIG)

        if toml_path.is_file():
            try:
                with toml_path.open("rb") as f:
                    user_config = tomllib.load(f)
                self._deep_merge(merged, user_config)
            except (tomllib.TOMLDecodeError, OSError):
                pass
        elif yaml_path.is_file():
            # Fallback: try PyYAML
            try:
                import yaml
                with yaml_path.open("r", encoding="utf-8") as f:
                    user_config = yaml.safe_load(f) or {}
                self._deep_merge(merged, user_config)
            except ImportError:
                pass
            except (OSError, ValueError):
                pass

        return merged

    def _deep_merge(self, base: dict, override: dict) -> None:
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    def ensure_product_config(self) -> Path:
        """Create the product-owned controlled-change target only when absent."""
        config_dir = self.base_dir / "config"
        target = config_dir / "agentguard.toml"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        if self.base_dir.is_symlink():
            raise OSError("PRODUCT_CONFIG_BASE_UNSAFE")
        config_dir.mkdir(parents=True, exist_ok=True)
        if config_dir.is_symlink():
            raise OSError("PRODUCT_CONFIG_DIRECTORY_UNSAFE")
        if target.exists() or target.is_symlink():
            if not target.is_file() or target.is_symlink():
                raise OSError("PRODUCT_CONFIG_TARGET_UNSAFE")
            return target
        try:
            with target.open("x", encoding="utf-8", newline="\n") as output:
                output.write(PRODUCT_CONFIG_TEMPLATE)
                output.flush()
                os.fsync(output.fileno())
        except FileExistsError:
            if not target.is_file() or target.is_symlink():
                raise OSError("PRODUCT_CONFIG_TARGET_UNSAFE") from None
        return target

    @property
    def restore_whitelist(self) -> list[str]:
        return self._data.get("security", {}).get("restore_whitelist", [])

    @property
    def container_name(self) -> str:
        return self._data.get("checks", {}).get("container_name", "agent-dev")

    @property
    def port(self) -> int:
        return int(self._data.get("checks", {}).get("port", 3001))

    @property
    def ignore_dirs(self) -> list[str]:
        return self._data.get("security", {}).get("ignore_directories", [])

    def snapshot_dir(self) -> Path:
        return self.base_dir / self._data["paths"]["snapshot_dir"]

    def state_dir(self) -> Path:
        return self.base_dir / self._data["paths"]["state_dir"]

    def state_db(self) -> Path:
        return self.state_dir() / "state.db"

    def docs_path(self, key: str) -> Path:
        return self.base_dir / self._data["docs"][key]

    def reports_dir(self) -> Path:
        return self.base_dir / self._data["paths"]["reports_dir"]

    def to_dict(self) -> dict[str, Any]:
        return sanitize_dict(dict(self._data))
