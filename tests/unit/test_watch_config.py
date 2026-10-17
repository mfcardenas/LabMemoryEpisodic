"""Configuración de las carpetas vigiladas: valores por defecto y errores claros."""
from __future__ import annotations

import pytest

from episodic.application.watch_config import (
    DEFAULT_EXCLUDE,
    WatchConfigError,
    load_watch_settings,
    parse_watch_settings,
)
from episodic.config import EpisodicConfig


def _source(**overrides):
    return {
        "name": "taller",
        "path": "entrada",
        "grouping": "folder",
        "environment_id": "taller_01",
        **overrides,
    }


def test_defaults_and_relative_paths_resolve_against_the_config_file(tmp_path) -> None:
    config_file = tmp_path / "conf" / "watch.toml"
    config_file.parent.mkdir()
    config_file.write_text(
        '[[sources]]\nname = "taller"\npath = "../entrada"\nafter = "move"\n',
        encoding="utf-8",
    )
    settings = load_watch_settings(config_file)

    assert settings.interval_seconds == 5.0 and settings.settle_seconds == 10.0
    source = settings.sources[0]
    assert source.path == (tmp_path / "entrada").resolve()
    # Por defecto, la herramienta de captura declara cada captura en su manifiesto.
    assert source.grouping == "manifest"
    assert source.environment_id == ""
    assert source.manifest_file == "capture.json"
    assert source.missing_files_timeout_seconds == 600.0
    assert source.archive_dir == source.path / "_procesados"
    assert source.exclude == DEFAULT_EXCLUDE
    assert source.control_files == {"capture.json"}


def test_folder_grouping_keeps_its_own_settings(tmp_path) -> None:
    settings = parse_watch_settings(
        {"sources": [_source(ready_marker="LISTO", source_ids={"video": "taller_01.camara_fija"})]},
        base_dir=tmp_path,
    )
    source = settings.sources[0]
    assert source.environment_id == "taller_01"
    assert source.close_after_seconds == 30.0
    assert source.control_files == {"LISTO"}
    assert source.source_ids == {"video": "taller_01.camara_fija"}


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"sources": []}, "al menos una fuente"),
        ({"sources": [_source(gruping="folder")]}, "claves desconocidas ['gruping']"),
        ({"watch": {"intervalo": 3}, "sources": [_source()]}, "claves desconocidas ['intervalo']"),
        ({"sources": [_source(grouping="day")]}, "grouping"),
        ({"sources": [_source(after="delete")]}, "after"),
        ({"sources": [_source(archive_dir="hecho")]}, 'solo tiene sentido con after = "move"'),
        ({"sources": [_source(environment_id="")]}, "environment_id es obligatorio"),
        ({"sources": [_source(settle_seconds=3)]}, "claves desconocidas"),
        ({"watch": {"interval_seconds": 0}, "sources": [_source()]}, "mayor que 0"),
        ({"sources": [_source(close_after_seconds=-1)]}, "no puede ser negativo"),
        ({"sources": [_source(ready_marker="sub/LISTO")]}, "nombre de fichero"),
        ({"sources": [_source(source_ids={"camara": "x"})]}, "tipos desconocidos"),
        ({"sources": [_source(), _source()]}, "repetidos"),
        ({"sources": [_source(), _source(name="otra", path="entrada/sub")]}, "anidarse"),
        ({"sources": [_source(grouping="manifest")]}, "environment_id no se usa con grouping"),
        (
            {"sources": [{"name": "t", "path": "e", "ready_marker": "LISTO"}]},
            "ready_marker no se usa con grouping",
        ),
        ({"sources": [_source(manifest_file="m.json")]}, "solo se usan con grouping"),
        ({"sources": [{"name": "t", "path": "e", "manifest_file": ""}]}, "no puede estar vacío"),
        (
            {"sources": [{"name": "t", "path": "e", "missing_files_timeout_seconds": 0}]},
            "mayor que 0",
        ),
    ],
)
def test_invalid_settings_fail_with_a_clear_message(tmp_path, raw, message) -> None:
    with pytest.raises(WatchConfigError) as error:
        parse_watch_settings(raw, base_dir=tmp_path)
    assert message in str(error.value)


def test_missing_or_broken_file_is_an_error(tmp_path) -> None:
    with pytest.raises(WatchConfigError, match="No existe"):
        load_watch_settings(tmp_path / "no.toml")
    broken = tmp_path / "roto.toml"
    broken.write_text("[[sources]\n", encoding="utf-8")
    with pytest.raises(WatchConfigError, match="TOML no válido"):
        load_watch_settings(broken)


def test_watch_config_path_comes_from_the_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("EPIGRAPH_WATCH_CONFIG", "conf/watch.toml")
    monkeypatch.setenv("EPIGRAPH_ACTIVITY_LOG_MAX_ROWS", "100")
    config = EpisodicConfig(project_root=tmp_path)
    assert config.watch_config_path == (tmp_path / "conf" / "watch.toml").resolve()
    assert config.activity_log_max_rows == 100
    monkeypatch.delenv("EPIGRAPH_WATCH_CONFIG")
    assert EpisodicConfig(project_root=tmp_path).watch_config_path is None
