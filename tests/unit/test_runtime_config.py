"""Pruebas de configuración, rutas y logging de la base ejecutable."""
from __future__ import annotations

import logging

from episodic.config import EpisodicConfig
from episodic.logging_config import configure_logging


def test_relative_runtime_paths_are_anchored_to_project_root(tmp_path) -> None:
    config = EpisodicConfig(project_root=tmp_path, data_dir="runtime/data")

    assert config.data_path == (tmp_path / "runtime" / "data").resolve()
    assert config.sqlite_path == (tmp_path / "runtime" / "data" / "episodic.db").resolve()
    assert config.default_manifest_path == tmp_path / "data" / "atlas" / "manifest.json"


def test_environment_configuration_has_single_prefix(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("EPIGRAPH_DATA_DIR", "custom-data")
    monkeypatch.setenv("EPIGRAPH_PORT", "8123")
    monkeypatch.setenv("EPIGRAPH_LOG_LEVEL", "debug")

    config = EpisodicConfig.from_env(project_root=tmp_path)

    assert config.data_path == (tmp_path / "custom-data").resolve()
    assert config.port == 8123
    assert config.log_level == "DEBUG"


def test_logging_can_write_to_configured_relative_file(tmp_path) -> None:
    config = EpisodicConfig(
        project_root=tmp_path,
        log_level="INFO",
        log_file="runtime/logs/epigraph.log",
    )

    configure_logging(config.log_level, config.log_file_path)
    logging.getLogger("episodic.test").info("startup-ok")
    logging.shutdown()

    assert config.log_file_path is not None
    assert "startup-ok" in config.log_file_path.read_text(encoding="utf-8")
