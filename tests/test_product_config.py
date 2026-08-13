"""Fresh-install product configuration bootstrap behavior."""

from __future__ import annotations

import tomllib

from agentguard.api.server import create_app
from agentguard.core.config import Config


def test_product_config_bootstrap_creates_parseable_server_owned_target(tmp_path):
    config = Config(tmp_path)

    target = config.ensure_product_config()

    assert target == tmp_path / "config" / "agentguard.toml"
    parsed = tomllib.loads(target.read_text(encoding="utf-8"))
    assert parsed["paths"]["config_file"] == "config/agentguard.toml"
    assert parsed["security"]["restore_whitelist"] == []


def test_product_config_bootstrap_never_overwrites_existing_content(tmp_path):
    target = tmp_path / "config" / "agentguard.toml"
    target.parent.mkdir(parents=True)
    target.write_text("custom = true\n", encoding="utf-8")

    Config(tmp_path).ensure_product_config()

    assert target.read_text(encoding="utf-8") == "custom = true\n"


def test_create_app_bootstraps_controlled_change_target_on_fresh_install(tmp_path):
    create_app(state_db_path=tmp_path / "state.db", config={"base_dir": str(tmp_path)})

    target = tmp_path / "config" / "agentguard.toml"
    assert target.is_file()
    assert tomllib.loads(target.read_text(encoding="utf-8"))["checks"]["port"] == 3001
