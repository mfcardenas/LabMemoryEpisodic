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


def test_speech_and_vision_parameters_come_from_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("EPIGRAPH_SPEECH_BACKEND", "http")
    monkeypatch.setenv("EPIGRAPH_SPEECH_URL", "http://gpu-box:9000")
    monkeypatch.setenv("EPIGRAPH_SPEECH_MODEL", "Systran/faster-whisper-small")
    monkeypatch.setenv("EPIGRAPH_SPEECH_LANGUAGE", "")
    monkeypatch.setenv("EPIGRAPH_VISION_THINK", "true")
    monkeypatch.setenv("EPIGRAPH_VIDEO_MAX_KEYFRAMES", "5")

    config = EpisodicConfig.from_env(project_root=tmp_path)

    assert config.speech_url == "http://gpu-box:9000"
    assert config.speech_model == "Systran/faster-whisper-small"
    assert config.speech_language == ""
    assert config.vision_think is True
    assert config.video_max_keyframes == 5


def test_unknown_speech_backend_is_rejected(tmp_path) -> None:
    import pytest

    with pytest.raises(ValueError, match="habla"):
        EpisodicConfig(project_root=tmp_path, speech_backend="whisper-local")


def test_env_file_loads_only_prefixed_variables_without_overriding(monkeypatch, tmp_path) -> None:
    from episodic.config import load_env_file

    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comentario\n"
        "EPIGRAPH_SPEECH_MODEL=Systran/faster-whisper-small  # modelo ligero\n"
        "EPIGRAPH_SPEECH_PROMPT=\"latiguillo, radial\"\n"
        "EPIGRAPH_PORT=9999\n"
        "SPEECH_IMAGE=ghcr.io/speaches-ai/speaches:latest-cpu\n",
        encoding="utf-8",
    )
    for name in ("EPIGRAPH_SPEECH_MODEL", "EPIGRAPH_SPEECH_PROMPT", "SPEECH_IMAGE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EPIGRAPH_PORT", "8100")

    loaded = load_env_file(env_file)

    assert sorted(loaded) == ["EPIGRAPH_SPEECH_MODEL", "EPIGRAPH_SPEECH_PROMPT"]
    config = EpisodicConfig.from_env(project_root=tmp_path)
    assert config.speech_model == "Systran/faster-whisper-small"
    assert config.speech_prompt == "latiguillo, radial"
    assert config.port == 8100
    import os

    assert "SPEECH_IMAGE" not in os.environ
    # Los delenv previos registran el estado original: monkeypatch lo restaura.
