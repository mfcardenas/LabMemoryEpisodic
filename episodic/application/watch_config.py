"""Configuración de la entrada automática: qué carpetas se vigilan y cómo.

Se lee de un fichero TOML (``EPIGRAPH_WATCH_CONFIG`` o ``epigraph watch
--config``). Las rutas relativas se resuelven contra la carpeta del propio
fichero. Una clave desconocida es un error: una errata no debe dejar una
fuente vigilada con valores por defecto sin que nadie lo note.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

GROUPINGS = ("manifest", "folder", "file", "time_gap")
AFTER_ACTIONS = ("keep", "move")
MEDIA_TYPES = ("video", "audio", "image", "document", "text")
DEFAULT_EXCLUDE = (
    ".*",
    "~$*",
    "*.tmp",
    "*.part",
    "*.partial",
    "*.crdownload",
    "*.download",
    "Thumbs.db",
    "desktop.ini",
)


class WatchConfigError(ValueError):
    """El fichero de configuración de la vigilancia no es válido."""


@dataclass(frozen=True)
class WatchSource:
    """Una carpeta vigilada y la regla con la que sus ficheros forman capturas.

    ``grouping``:
    - ``manifest`` (recomendado): la herramienta de captura deja cada captura
      en una subcarpeta con un manifiesto ``epigraph.capture/v1``
      (``manifest_file``) que declara entorno, instante (epoch) y
      dispositivos. La captura se registra cuando el manifiesto existe y todos
      los ficheros que declara están completos.
    - ``folder``: cada subcarpeta es una captura (vídeo, audio, textos… o solo
      parte de ellos). Se cierra cuando aparece ``ready_marker`` o, si no se
      usa marcador, tras ``close_after_seconds`` sin cambios. Un fichero suelto
      en la raíz es una captura por sí mismo.
    - ``file``: cada fichero es una captura.
    - ``time_gap``: los ficheros sueltos separados menos de
      ``max_gap_seconds`` (por fecha de modificación) forman una captura.

    ``environment_id`` es obligatorio salvo con ``manifest``, donde lo
    declara la herramienta en cada captura.
    """

    name: str
    path: Path
    environment_id: str = ""
    grouping: str = "manifest"
    close_after_seconds: float = 30.0
    max_gap_seconds: float = 120.0
    ready_marker: str = ""
    manifest_file: str = "capture.json"
    missing_files_timeout_seconds: float = 600.0
    manifest_warning_seconds: float = 60.0
    include: tuple[str, ...] = ("*",)
    exclude: tuple[str, ...] = DEFAULT_EXCLUDE
    after: str = "keep"
    archive_dir: Path | None = None
    source_ids: dict[str, str] = field(default_factory=dict)
    window_duration_seconds: float = 60.0
    overlap_seconds: float = 5.0
    audio_offset_seconds: float = 0.0
    enabled: bool = True

    @property
    def control_files(self) -> frozenset[str]:
        """Ficheros que gobiernan la captura y no forman parte de ella."""
        if self.grouping == "manifest":
            return frozenset({self.manifest_file})
        return frozenset({self.ready_marker} if self.ready_marker else ())

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path),
            "environment_id": self.environment_id,
            "grouping": self.grouping,
            "close_after_seconds": self.close_after_seconds,
            "max_gap_seconds": self.max_gap_seconds,
            "ready_marker": self.ready_marker,
            "manifest_file": self.manifest_file,
            "missing_files_timeout_seconds": self.missing_files_timeout_seconds,
            "manifest_warning_seconds": self.manifest_warning_seconds,
            "include": list(self.include),
            "exclude": list(self.exclude),
            "after": self.after,
            "archive_dir": str(self.archive_dir) if self.archive_dir else None,
            "source_ids": dict(self.source_ids),
            "window_duration_seconds": self.window_duration_seconds,
            "overlap_seconds": self.overlap_seconds,
            "audio_offset_seconds": self.audio_offset_seconds,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class WatchSettings:
    interval_seconds: float = 5.0
    settle_seconds: float = 10.0
    sources: tuple[WatchSource, ...] = ()
    config_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "interval_seconds": self.interval_seconds,
            "settle_seconds": self.settle_seconds,
            "config_path": str(self.config_path) if self.config_path else None,
            "sources": [source.to_dict() for source in self.sources],
        }


_WATCH_KEYS = {"interval_seconds", "settle_seconds"}
_SOURCE_KEYS = {
    "name",
    "path",
    "environment_id",
    "grouping",
    "close_after_seconds",
    "max_gap_seconds",
    "ready_marker",
    "manifest_file",
    "missing_files_timeout_seconds",
    "manifest_warning_seconds",
    "include",
    "exclude",
    "after",
    "archive_dir",
    "source_ids",
    "window_duration_seconds",
    "overlap_seconds",
    "audio_offset_seconds",
    "enabled",
}


def load_watch_settings(path: str | Path) -> WatchSettings:
    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise WatchConfigError(f"No existe el fichero de vigilancia: {config_path}")
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise WatchConfigError(f"{config_path}: TOML no válido: {exc}") from exc
    return parse_watch_settings(raw, base_dir=config_path.parent, config_path=config_path)


def parse_watch_settings(
    raw: dict[str, Any], base_dir: Path, config_path: Path | None = None
) -> WatchSettings:
    _reject_unknown(raw, {"watch", "sources"}, "raíz")
    watch = raw.get("watch", {})
    if not isinstance(watch, dict):
        raise WatchConfigError("[watch] debe ser una tabla")
    _reject_unknown(watch, _WATCH_KEYS, "[watch]")
    interval = _positive(watch.get("interval_seconds", 5.0), "[watch].interval_seconds")
    settle = _non_negative(watch.get("settle_seconds", 10.0), "[watch].settle_seconds")

    entries = raw.get("sources", [])
    if not isinstance(entries, list) or not entries:
        raise WatchConfigError("Declare al menos una fuente en [[sources]]")
    sources = tuple(_parse_source(entry, index, base_dir) for index, entry in enumerate(entries))
    names = [source.name for source in sources]
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise WatchConfigError(f"Nombres de fuente repetidos: {', '.join(repeated)}")
    paths = [source.path for source in sources]
    for index, current in enumerate(paths):
        for other in paths[index + 1:]:
            if current == other or current in other.parents or other in current.parents:
                raise WatchConfigError(
                    f"Las carpetas vigiladas no pueden coincidir ni anidarse: {current} · {other}"
                )
    return WatchSettings(
        interval_seconds=interval,
        settle_seconds=settle,
        sources=sources,
        config_path=config_path,
    )


def _parse_source(entry: Any, index: int, base_dir: Path) -> WatchSource:
    where = f"[[sources]] n.º {index + 1}"
    if not isinstance(entry, dict):
        raise WatchConfigError(f"{where} debe ser una tabla")
    _reject_unknown(entry, _SOURCE_KEYS, where)
    name = _text(entry.get("name"), f"{where}.name")
    where = f"fuente '{name}'"
    path = _resolve(base_dir, _text(entry.get("path"), f"{where}.path"))

    grouping = entry.get("grouping", "manifest")
    if grouping not in GROUPINGS:
        raise WatchConfigError(f"{where}.grouping debe ser uno de {GROUPINGS}")
    if grouping == "manifest":
        # El manifiesto de cada captura declara entorno, dispositivos y tiempos.
        for key in (
            "environment_id",
            "source_ids",
            "audio_offset_seconds",
            "ready_marker",
            "close_after_seconds",
            "max_gap_seconds",
        ):
            if key in entry:
                raise WatchConfigError(
                    f"{where}.{key} no se usa con grouping = \"manifest\": "
                    "lo declara la herramienta de captura en cada manifiesto"
                )
        environment_id = ""
    else:
        environment_id = _text(entry.get("environment_id"), f"{where}.environment_id")
        manifest_only = sorted(
            {"manifest_file", "missing_files_timeout_seconds", "manifest_warning_seconds"}
            & set(entry)
        )
        if manifest_only:
            raise WatchConfigError(
                f"{where}: {', '.join(manifest_only)}: opciones que solo se usan "
                "con grouping = \"manifest\""
            )
    after = entry.get("after", "keep")
    if after not in AFTER_ACTIONS:
        raise WatchConfigError(f"{where}.after debe ser uno de {AFTER_ACTIONS}")
    archive_dir = None
    if after == "move":
        archive_dir = _resolve(path, str(entry.get("archive_dir") or "_procesados"))
        if archive_dir == path:
            raise WatchConfigError(f"{where}.archive_dir no puede ser la propia carpeta")
    elif "archive_dir" in entry:
        raise WatchConfigError(f"{where}.archive_dir solo tiene sentido con after = \"move\"")

    source_ids = entry.get("source_ids", {})
    if not isinstance(source_ids, dict):
        raise WatchConfigError(f"{where}.source_ids debe ser una tabla")
    unknown_media = sorted(set(source_ids) - set(MEDIA_TYPES))
    if unknown_media:
        raise WatchConfigError(
            f"{where}.source_ids: tipos desconocidos {unknown_media}; use {MEDIA_TYPES}"
        )

    return WatchSource(
        name=name,
        path=path,
        environment_id=environment_id,
        grouping=grouping,
        close_after_seconds=_non_negative(
            entry.get("close_after_seconds", 30.0), f"{where}.close_after_seconds"
        ),
        max_gap_seconds=_positive(entry.get("max_gap_seconds", 120.0), f"{where}.max_gap_seconds"),
        ready_marker=_file_name(entry.get("ready_marker", ""), f"{where}.ready_marker"),
        manifest_file=_required_file_name(
            entry.get("manifest_file", "capture.json"), f"{where}.manifest_file"
        ),
        missing_files_timeout_seconds=_positive(
            entry.get("missing_files_timeout_seconds", 600.0),
            f"{where}.missing_files_timeout_seconds",
        ),
        manifest_warning_seconds=_non_negative(
            entry.get("manifest_warning_seconds", 60.0), f"{where}.manifest_warning_seconds"
        ),
        include=_patterns(entry.get("include", ["*"]), f"{where}.include"),
        exclude=_patterns(entry.get("exclude", list(DEFAULT_EXCLUDE)), f"{where}.exclude"),
        after=after,
        archive_dir=archive_dir,
        source_ids={key: _text(value, f"{where}.source_ids.{key}") for key, value in source_ids.items()},
        window_duration_seconds=_positive(
            entry.get("window_duration_seconds", 60.0), f"{where}.window_duration_seconds"
        ),
        overlap_seconds=_non_negative(entry.get("overlap_seconds", 5.0), f"{where}.overlap_seconds"),
        audio_offset_seconds=_number(entry.get("audio_offset_seconds", 0.0), f"{where}.audio_offset_seconds"),
        enabled=_boolean(entry.get("enabled", True), f"{where}.enabled"),
    )


def _reject_unknown(table: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise WatchConfigError(f"{where}: claves desconocidas {unknown}")


def _resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WatchConfigError(f"{where} es obligatorio y debe ser texto")
    return value.strip()


def _file_name(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise WatchConfigError(f"{where} debe ser texto")
    value = value.strip()
    if value and (Path(value).name != value):
        raise WatchConfigError(f"{where} debe ser un nombre de fichero, no una ruta")
    return value


def _required_file_name(value: Any, where: str) -> str:
    name = _file_name(value, where)
    if not name:
        raise WatchConfigError(f"{where} no puede estar vacío")
    return name


def _patterns(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise WatchConfigError(f"{where} debe ser una lista de patrones")
    return tuple(value)


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WatchConfigError(f"{where} debe ser un número")
    return float(value)


def _positive(value: Any, where: str) -> float:
    number = _number(value, where)
    if number <= 0:
        raise WatchConfigError(f"{where} debe ser mayor que 0")
    return number


def _non_negative(value: Any, where: str) -> float:
    number = _number(value, where)
    if number < 0:
        raise WatchConfigError(f"{where} no puede ser negativo")
    return number


def _boolean(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise WatchConfigError(f"{where} debe ser true o false")
    return value
