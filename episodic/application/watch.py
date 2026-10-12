"""Entrada automática: vigila carpetas y convierte lo que llega en capturas.

Una herramienta de captura externa deja en las carpetas lo que graba
(vídeo, audio, textos… o solo parte) y, con ``grouping = "manifest"``,
declara en un manifiesto ``epigraph.capture/v1`` el entorno, el instante
(epoch) y los dispositivos. El vigilante:

1. Detecta cada fichero y espera a que esté completo: tamaño y fecha sin
   cambios durante ``settle_seconds`` y legible.
2. Lo inspecciona (SHA-256, tipo) y lo anota en ``inbox_files``: un reinicio
   no reprocesa ni pierde nada.
3. Agrupa los ficheros en capturas según la regla de la fuente y, cuando el
   grupo se cierra, la registra con ``CaptureService`` y encola su trabajo en
   la cola durable, igual que una subida desde el front.
4. Opcionalmente archiva los originales (la memoria guarda su propia copia).

Todo queda en el registro de ejecución. Solo un proceso vigila a la vez: el
que tiene la concesión ``inbox-watcher`` en SQLite.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import os
import shutil
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from episodic.adapters.files.metadata import LocalFileInspector, UnsupportedAssetTypeError
from episodic.application.activity import ActivityLog
from episodic.application.captures import CaptureConflictError, CaptureFile, CaptureService
from episodic.application.watch_config import WatchSettings, WatchSource
from episodic.domain.capture_manifest import (
    CaptureManifest,
    CaptureManifestError,
    parse_capture_manifest,
)
from episodic.domain.captures import Job
from episodic.domain.types import EvidenceMediaType
from episodic.domain.times import utc_now
from episodic.domain.watch import InboxFile, InboxFileState
from episodic.ports.repository import EpisodicRepository

logger = logging.getLogger(__name__)

ACTIVITY_COMPONENT = "watch"
LEASE_NAME = "inbox-watcher"
_OPEN_STATES = (InboxFileState.PENDING, InboxFileState.READY)

CaptureSubmitter = Callable[[str], Job]


@dataclass(frozen=True)
class _DiskFile:
    rel_path: str
    path: Path
    size: int
    mtime_ns: int


@dataclass
class _SourceStatus:
    available: bool | None = None
    error: str | None = None
    last_scan_at: float | None = None
    files_on_disk: int = 0


@dataclass
class ScanReport:
    detected: int = 0
    ready: int = 0
    open_groups: int = 0
    capture_ids: list[str] = field(default_factory=list)
    job_ids: list[str] = field(default_factory=list)


class InboxWatcher:
    """Barre las fuentes configuradas a intervalos y crea las capturas.

    ``repository`` debe ser una conexión propia del hilo del vigilante, y
    ``capture_service`` debe usar esa misma conexión. ``clock`` devuelve la
    hora de pared en segundos (inyectable en pruebas).
    """

    def __init__(
        self,
        repository: EpisodicRepository,
        capture_service: CaptureService,
        settings: WatchSettings,
        submit: CaptureSubmitter,
        activity: ActivityLog,
        *,
        inspector: LocalFileInspector | None = None,
        clock: Callable[[], float] = time.time,
        owner: str | None = None,
    ) -> None:
        self.repository = repository
        self.capture_service = capture_service
        self.settings = settings
        self.submit = submit
        self.activity = activity
        self.inspector = inspector or LocalFileInspector()
        self.clock = clock
        self.owner = owner or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self.lease_ttl_seconds = max(3 * settings.interval_seconds, 15.0)
        self.has_lease = False
        self._waiting_logged = False
        self.last_scan_at: float | None = None
        self.last_error: str | None = None
        self._status = {source.name: _SourceStatus() for source in settings.sources}
        # Por (fuente, grupo): cuándo se vio el manifiesto esperando ficheros y
        # con qué huella falló, para reintentar si la herramienta lo reescribe.
        self._manifest_waiting: dict[tuple[str, str], float] = {}
        self._manifest_failed: dict[tuple[str, str], tuple[int, int]] = {}
        self._scan_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()

    # -- Ciclo de vida ------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="epigraph-watch", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            self._stop.set()
            self._wake.set()
            thread.join(timeout)
        if self.has_lease:
            self.repository.release_lease(LEASE_NAME, self.owner)
            self.has_lease = False
            self.activity.info(ACTIVITY_COMPONENT, "Vigilancia detenida")

    def request_scan(self) -> None:
        """Adelanta el próximo barrido (botón «Barrer ahora» del front)."""
        self._wake.set()

    def _loop(self) -> None:
        sources = ", ".join(f"{item.name} → {item.path}" for item in self.settings.sources)
        self.activity.info(
            ACTIVITY_COMPONENT,
            f"Vigilante en marcha cada {self.settings.interval_seconds:g} s: {sources}",
        )
        while not self._stop.is_set():
            self.tick()
            self._wake.wait(self.settings.interval_seconds)
            self._wake.clear()

    def tick(self) -> ScanReport | None:
        """Un barrido si este proceso tiene (o consigue) la concesión."""
        try:
            acquired = self.repository.acquire_lease(
                LEASE_NAME, self.owner, self.lease_ttl_seconds, self.clock()
            )
            if acquired and not self.has_lease:
                self.activity.info(ACTIVITY_COMPONENT, "Este proceso vigila las carpetas")
                self._waiting_logged = False
            elif not acquired and not self._waiting_logged:
                holder = self.repository.get_lease(LEASE_NAME)
                self.activity.warning(
                    ACTIVITY_COMPONENT,
                    "Otro proceso vigila ya estas carpetas"
                    + (f" ({holder[0]})" if holder else "")
                    + "; este queda a la espera",
                )
                self._waiting_logged = True
            self.has_lease = acquired
            if not acquired:
                return None
            return self.scan_once()
        except Exception as exc:  # el bucle nunca debe morir
            self.last_error = str(exc)
            logger.exception("Fallo en el barrido de las carpetas vigiladas")
            self.activity.error(ACTIVITY_COMPONENT, f"Fallo en el barrido: {exc}")
            return None

    # -- Barrido ------------------------------------------------------------

    def scan_once(self) -> ScanReport:
        report = ScanReport()
        with self._scan_lock:
            for source in self.settings.sources:
                if not source.enabled:
                    continue
                try:
                    self._scan_source(source, report)
                except Exception as exc:
                    status = self._status[source.name]
                    status.error = str(exc)
                    logger.exception("Fallo vigilando la fuente %s", source.name)
                    self.activity.error(
                        ACTIVITY_COMPONENT,
                        f"Fallo vigilando {source.name}: {exc}",
                        source_name=source.name,
                    )
            self.last_scan_at = self.clock()
            self.last_error = None
        return report

    def has_open_files(self) -> bool:
        """Quedan ficheros por completar o grupos por cerrar."""
        return any(
            row.state in _OPEN_STATES and row.capture_id is None
            for source in self.settings.sources
            if source.enabled
            for row in self.repository.list_live_inbox_files(source.name)
        )

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "has_lease": self.has_lease,
            "owner": self.owner,
            "last_scan_at": _iso(self.last_scan_at),
            "last_error": self.last_error,
            "sources": {
                name: {
                    "available": status.available,
                    "error": status.error,
                    "last_scan_at": _iso(status.last_scan_at),
                    "files_on_disk": status.files_on_disk,
                }
                for name, status in self._status.items()
            },
        }

    def _scan_source(self, source: WatchSource, report: ScanReport) -> None:
        status = self._status[source.name]
        now = self.clock()
        if not source.path.is_dir():
            if status.available is not False:
                self.activity.warning(
                    ACTIVITY_COMPONENT,
                    f"Carpeta no disponible: {source.path}",
                    source_name=source.name,
                )
            status.available, status.error, status.last_scan_at = False, "carpeta no disponible", now
            return
        if status.available is False:
            self.activity.info(
                ACTIVITY_COMPONENT, f"Carpeta disponible de nuevo: {source.path}", source_name=source.name
            )
        status.available, status.error = True, None

        disk, controls = self._list_files(source)
        status.files_on_disk = len(disk)
        rows = {row.rel_path: row for row in self.repository.list_live_inbox_files(source.name)}
        closed_keys = set(self.repository.list_inbox_group_keys(source.name))

        for rel_path, disk_file in disk.items():
            row = rows.get(rel_path)
            if row is None:
                rows[rel_path] = self._detect(source, disk_file, closed_keys, now)
                report.detected += 1
            elif (row.size_bytes, row.mtime_ns) != (disk_file.size, disk_file.mtime_ns):
                rows[rel_path] = self._on_changed(source, row, disk_file, now)
            elif row.state == InboxFileState.PENDING and now - row.changed_at >= self.settings.settle_seconds:
                rows[rel_path] = self._inspect(source, row, disk_file, report)

        for rel_path, row in list(rows.items()):
            if rel_path not in disk:
                self._on_missing(source, row)
                del rows[rel_path]

        if source.grouping == "manifest":
            self._retry_rewritten_manifests(source, controls, rows)
            self._close_manifest_groups(source, list(rows.values()), controls, closed_keys, now, report)
        else:
            self._close_groups(source, list(rows.values()), controls, closed_keys, now, report)
        status.last_scan_at = now

    def _list_files(self, source: WatchSource) -> tuple[dict[str, _DiskFile], dict[str, Path]]:
        """Ficheros de datos y, por subcarpeta, su fichero de control
        (manifiesto o marcador de cierre)."""
        files: dict[str, _DiskFile] = {}
        controls: dict[str, Path] = {}
        for dirpath, dirnames, filenames in os.walk(source.path):
            current = Path(dirpath)
            dirnames[:] = sorted(
                name
                for name in dirnames
                if not _matches(name, source.exclude)
                and (source.archive_dir is None or current / name != source.archive_dir)
            )
            for name in sorted(filenames):
                file_path = current / name
                rel_path = file_path.relative_to(source.path).as_posix()
                if name in source.control_files:
                    folder = _top_folder(rel_path)
                    if folder is not None and rel_path == f"{folder}/{name}":
                        controls[folder] = file_path
                    continue
                if _matches(name, source.exclude) or not _matches(name, source.include):
                    continue
                try:
                    stat = file_path.stat()
                except OSError:
                    continue
                files[rel_path] = _DiskFile(rel_path, file_path, stat.st_size, stat.st_mtime_ns)
        return files, controls

    # -- Ficheros -----------------------------------------------------------

    def _save(self, row: InboxFile, **changes: Any) -> InboxFile:
        updated = replace(row, updated_at=utc_now(), **changes)
        self.repository.save_inbox_file(updated)
        return updated

    def _detect(
        self, source: WatchSource, disk_file: _DiskFile, closed_keys: set[str], now: float
    ) -> InboxFile:
        group_key = ""
        folder = _top_folder(disk_file.rel_path)
        if source.grouping == "file":
            group_key = _unique_key(disk_file.rel_path, closed_keys)
        elif source.grouping in ("folder", "manifest"):
            group_key = _unique_key(folder or disk_file.rel_path, closed_keys)
        row = InboxFile(
            source_name=source.name,
            rel_path=disk_file.rel_path,
            group_key=group_key,
            size_bytes=disk_file.size,
            mtime_ns=disk_file.mtime_ns,
            changed_at=now,
        )
        if source.grouping == "manifest" and folder is None:
            # Con manifiesto, cada captura vive en su propia subcarpeta.
            row = replace(
                row,
                state=InboxFileState.IGNORED,
                error="fuera de una carpeta de captura (con manifiesto)",
            )
        self.repository.save_inbox_file(row)
        self.activity.info(
            ACTIVITY_COMPONENT,
            f"Detectado: {disk_file.rel_path} ({_size(disk_file.size)})",
            source_name=source.name,
            path=disk_file.rel_path,
        )
        return row

    def _on_changed(
        self, source: WatchSource, row: InboxFile, disk_file: _DiskFile, now: float
    ) -> InboxFile:
        if row.state in (InboxFileState.CAPTURED, InboxFileState.DUPLICATE):
            self.activity.warning(
                ACTIVITY_COMPONENT,
                f"{row.rel_path} cambió después de capturarse; no se vuelve a procesar",
                source_name=source.name,
                path=row.rel_path,
                capture_id=row.capture_id,
            )
            return self._save(
                row,
                size_bytes=disk_file.size,
                mtime_ns=disk_file.mtime_ns,
                error="modificado después de capturarse",
            )
        if row.state != InboxFileState.PENDING:
            self.activity.info(
                ACTIVITY_COMPONENT,
                f"{row.rel_path} cambió; se vuelve a comprobar",
                source_name=source.name,
                path=row.rel_path,
            )
        return self._save(
            row,
            state=InboxFileState.PENDING,
            size_bytes=disk_file.size,
            mtime_ns=disk_file.mtime_ns,
            changed_at=now,
            content_hash=None,
            media_type=None,
            error=None,
        )

    def _inspect(
        self, source: WatchSource, row: InboxFile, disk_file: _DiskFile, report: ScanReport
    ) -> InboxFile:
        try:
            # Un fichero que otro proceso aún escribe en exclusiva no se abre.
            with disk_file.path.open("rb") as handle:
                handle.read(1)
            inspection = self.inspector.inspect(disk_file.path)
            after = disk_file.path.stat()
        except UnsupportedAssetTypeError as exc:
            self.activity.warning(
                ACTIVITY_COMPONENT,
                f"Formato no admitido, se ignora: {row.rel_path} ({exc})",
                source_name=source.name,
                path=row.rel_path,
            )
            return self._save(row, state=InboxFileState.IGNORED, error=str(exc))
        except OSError:
            return row  # bloqueado o en movimiento: se reintenta en el próximo barrido
        except ValueError as exc:
            self.activity.warning(
                ACTIVITY_COMPONENT,
                f"No se puede leer, se ignora: {row.rel_path} ({exc})",
                source_name=source.name,
                path=row.rel_path,
            )
            return self._save(row, state=InboxFileState.IGNORED, error=str(exc))
        if (after.st_size, after.st_mtime_ns) != (row.size_bytes, row.mtime_ns):
            return row  # cambió mientras se calculaba el hash
        report.ready += 1
        self.activity.info(
            ACTIVITY_COMPONENT,
            f"Completo: {row.rel_path} · {inspection.media_type.value} · {_size(row.size_bytes)}",
            source_name=source.name,
            path=row.rel_path,
            data={"sha256": inspection.content_hash},
        )
        return self._save(
            row,
            state=InboxFileState.READY,
            content_hash=inspection.content_hash,
            media_type=inspection.media_type.value,
        )

    def _on_missing(self, source: WatchSource, row: InboxFile) -> None:
        if row.state in _OPEN_STATES:
            self.activity.warning(
                ACTIVITY_COMPONENT,
                f"Desapareció antes de capturarse: {row.rel_path}",
                source_name=source.name,
                path=row.rel_path,
            )
            self._save(row, state=InboxFileState.VANISHED, present=False)
        else:
            self._save(row, present=False)

    # -- Grupos y capturas --------------------------------------------------

    def _close_groups(
        self,
        source: WatchSource,
        live_rows: list[InboxFile],
        controls: dict[str, Path],
        closed_keys: set[str],
        now: float,
        report: ScanReport,
    ) -> None:
        open_rows = [row for row in live_rows if row.capture_id is None and row.state in _OPEN_STATES]
        if source.grouping == "time_gap":
            groups = self._time_gap_groups(source, open_rows, closed_keys, now, report)
        else:
            by_key: dict[str, list[InboxFile]] = {}
            for row in open_rows:
                by_key.setdefault(row.group_key, []).append(row)
            groups = []
            for key, rows in sorted(by_key.items()):
                if any(row.state == InboxFileState.PENDING for row in rows):
                    report.open_groups += 1
                    continue
                folder = _top_folder(rows[0].rel_path) if source.grouping == "folder" else None
                if folder is not None:
                    if source.ready_marker:
                        closed = folder in controls
                    else:
                        last_change = max(
                            row.changed_at for row in live_rows if row.group_key == key
                        )
                        closed = now - last_change >= source.close_after_seconds
                    if not closed:
                        report.open_groups += 1
                        continue
                groups.append((key, rows))
        for key, rows in groups:
            self._register_group(source, key, rows, closed_keys, report)

    def _time_gap_groups(
        self,
        source: WatchSource,
        open_rows: list[InboxFile],
        closed_keys: set[str],
        now: float,
        report: ScanReport,
    ) -> list[tuple[str, list[InboxFile]]]:
        chunks: list[list[InboxFile]] = []
        for row in sorted(open_rows, key=lambda item: (item.mtime_ns, item.rel_path)):
            if chunks and (row.mtime_ns - chunks[-1][-1].mtime_ns) / 1e9 <= source.max_gap_seconds:
                chunks[-1].append(row)
            else:
                chunks.append([row])
        groups: list[tuple[str, list[InboxFile]]] = []
        for chunk in chunks:
            newest = chunk[-1].mtime_ns / 1e9
            if any(row.state == InboxFileState.PENDING for row in chunk) or now - newest < source.max_gap_seconds:
                report.open_groups += 1
                continue
            first = datetime.fromtimestamp(chunk[0].mtime_ns / 1e9, timezone.utc)
            key = _unique_key(f"gap-{first:%Y%m%dT%H%M%S}", closed_keys)
            groups.append((key, [self._save(row, group_key=key) for row in chunk]))
        return groups

    def _register_group(
        self,
        source: WatchSource,
        key: str,
        rows: list[InboxFile],
        closed_keys: set[str],
        report: ScanReport,
    ) -> None:
        folder_name = _top_folder(rows[0].rel_path) if source.grouping == "folder" else None
        folder = source.path / folder_name if folder_name else None
        title = None
        if folder_name:
            part = key.partition("#")[2]
            title = f"{folder_name} · parte {part}" if part else folder_name

        def files_for(fresh: list[InboxFile]) -> list[CaptureFile]:
            return [
                CaptureFile(
                    source.path / row.rel_path,
                    Path(row.rel_path).name,
                    source_id=source.source_ids.get(row.media_type or ""),
                )
                for row in fresh
            ]

        self._create_capture(
            source,
            key,
            folder,
            rows,
            closed_keys,
            report,
            files_for,
            environment_id=source.environment_id,
            title=title,
            audio_offset_seconds=source.audio_offset_seconds,
        )

    # -- Capturas con manifiesto --------------------------------------------

    def _close_manifest_groups(
        self,
        source: WatchSource,
        live_rows: list[InboxFile],
        controls: dict[str, Path],
        closed_keys: set[str],
        now: float,
        report: ScanReport,
    ) -> None:
        by_key: dict[str, list[InboxFile]] = {}
        for row in live_rows:
            if row.capture_id is None and row.state in _OPEN_STATES:
                by_key.setdefault(row.group_key, []).append(row)
        for key, open_rows in sorted(by_key.items()):
            folder = _top_folder(open_rows[0].rel_path)
            manifest_path = controls.get(folder or "")
            if folder is None or manifest_path is None:
                report.open_groups += 1  # la herramienta aún no ha escrito el manifiesto
                continue
            try:
                raw = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest = parse_capture_manifest(raw)
            except OSError:
                report.open_groups += 1  # se está escribiendo: se reintenta
                continue
            except (json.JSONDecodeError, CaptureManifestError) as exc:
                self._fail_manifest(source, key, open_rows, manifest_path, f"{source.manifest_file}: {exc}")
                continue

            group_rows = {row.rel_path: row for row in live_rows if row.group_key == key}
            declared = {f"{folder}/{name}": device for name, device in manifest.files.items()}
            filtered = [
                path for path in declared
                if path not in group_rows and (source.path / path).is_file()
            ]
            if filtered:
                self._fail_manifest(
                    source, key, open_rows, manifest_path,
                    "ficheros declarados excluidos por include/exclude de la fuente: "
                    + ", ".join(sorted(filtered)),
                )
                continue
            missing = sorted(path for path in declared if path not in group_rows)
            pending = [
                path for path in declared
                if path in group_rows and group_rows[path].state == InboxFileState.PENDING
            ]
            if missing or pending:
                waiting_since = self._manifest_waiting.setdefault((source.name, key), now)
                if missing and now - waiting_since >= source.missing_files_timeout_seconds:
                    self._fail_manifest(
                        source, key, open_rows, manifest_path,
                        "faltan ficheros declarados en el manifiesto: " + ", ".join(missing),
                    )
                else:
                    report.open_groups += 1
                continue
            self._manifest_waiting.pop((source.name, key), None)

            for row in open_rows:
                if row.rel_path not in declared:
                    self._save(row, state=InboxFileState.IGNORED, error="no declarado en el manifiesto")
                    self.activity.warning(
                        ACTIVITY_COMPONENT,
                        f"No declarado en el manifiesto, se ignora: {row.rel_path}",
                        source_name=source.name,
                        path=row.rel_path,
                    )
            usable = [
                group_rows[path] for path in declared
                if group_rows[path].state == InboxFileState.READY
            ]
            for path in declared:
                if group_rows[path].state == InboxFileState.IGNORED:
                    self.activity.warning(
                        ACTIVITY_COMPONENT,
                        f"Declarado pero no utilizable, la captura sigue sin él: {path} "
                        f"({group_rows[path].error})",
                        source_name=source.name,
                        path=path,
                    )
            mismatched = [
                f"{path} ({declared[path].id} es {declared[path].kind.value}, el fichero es {row.media_type})"
                for path, row in ((row.rel_path, row) for row in usable)
                if not declared[path].accepts(EvidenceMediaType(row.media_type))
            ]
            if mismatched:
                self._fail_manifest(
                    source, key, usable, manifest_path,
                    "el tipo de fichero no corresponde al dispositivo: " + "; ".join(mismatched),
                )
                continue
            if not usable:
                self._fail_manifest(
                    source, key, open_rows, manifest_path,
                    "ningún fichero declarado es utilizable",
                )
                continue
            self._register_manifest_group(
                source, key, folder, usable, declared, manifest, raw, closed_keys, report
            )

    def _register_manifest_group(
        self,
        source: WatchSource,
        key: str,
        folder: str,
        rows: list[InboxFile],
        declared: dict,
        manifest: CaptureManifest,
        raw: dict,
        closed_keys: set[str],
        report: ScanReport,
    ) -> None:
        def files_for(fresh: list[InboxFile]) -> list[CaptureFile]:
            files = []
            for row in fresh:
                device = declared[row.rel_path]
                files.append(
                    CaptureFile(
                        source.path / row.rel_path,
                        Path(row.rel_path).name,
                        source_id=device.source_id(manifest.environment_id),
                        source_type=device.kind,
                        captured_at=device.started_at,
                    )
                )
            return files

        created = self._create_capture(
            source,
            key,
            source.path / folder,
            rows,
            closed_keys,
            report,
            files_for,
            environment_id=manifest.environment_id,
            title=manifest.title or manifest.capture_key,
            captured_at=manifest.captured_at,
            capture_key=manifest.capture_key,
            extra_metadata={"manifest": raw},
            manifest_path=f"{folder}/{source.manifest_file}",
        )
        if not created:
            manifest_path = source.path / folder / source.manifest_file
            self._remember_failed_manifest(source, key, manifest_path)

    def _fail_manifest(
        self, source: WatchSource, key: str, rows: list[InboxFile], manifest_path: Path, message: str
    ) -> None:
        self._manifest_waiting.pop((source.name, key), None)
        self._fail(source, key, rows, message)
        self._remember_failed_manifest(source, key, manifest_path)

    def _remember_failed_manifest(self, source: WatchSource, key: str, manifest_path: Path) -> None:
        try:
            stat = manifest_path.stat()
        except OSError:
            return
        self._manifest_failed[(source.name, key)] = (stat.st_size, stat.st_mtime_ns)

    def _retry_rewritten_manifests(
        self, source: WatchSource, controls: dict[str, Path], rows: dict[str, InboxFile]
    ) -> None:
        """Si la herramienta reescribe el manifiesto de una captura fallida, se reintenta."""
        for (source_name, key), fingerprint in list(self._manifest_failed.items()):
            if source_name != source.name:
                continue
            manifest_path = controls.get(key.partition("#")[0])
            try:
                stat = manifest_path.stat() if manifest_path else None
            except OSError:
                stat = None
            if stat is None or (stat.st_size, stat.st_mtime_ns) == fingerprint:
                continue
            del self._manifest_failed[(source_name, key)]
            for rel_path, row in list(rows.items()):
                if row.group_key == key and row.state == InboxFileState.FAILED:
                    rows[rel_path] = self._save(
                        row, state=InboxFileState.PENDING, changed_at=0.0,
                        content_hash=None, media_type=None, error=None,
                    )
                elif row.group_key == key and row.state == InboxFileState.IGNORED and row.error == "no declarado en el manifiesto":
                    rows[rel_path] = self._save(
                        row, state=InboxFileState.PENDING, changed_at=0.0, error=None,
                    )
            self.activity.info(
                ACTIVITY_COMPONENT,
                f"Manifiesto reescrito en {key}; se reintenta la captura",
                source_name=source.name,
            )

    # -- Núcleo común -------------------------------------------------------

    def _create_capture(
        self,
        source: WatchSource,
        key: str,
        folder: Path | None,
        rows: list[InboxFile],
        closed_keys: set[str],
        report: ScanReport,
        files_for: Callable[[list[InboxFile]], list[CaptureFile]],
        *,
        environment_id: str,
        title: str | None,
        captured_at: datetime | None = None,
        audio_offset_seconds: float = 0.0,
        capture_key: str | None = None,
        extra_metadata: dict | None = None,
        manifest_path: str | None = None,
    ) -> bool:
        """Registra la captura del grupo, encola su trabajo y archiva si procede."""
        fresh = self._screen_known_content(source, key, rows)
        if not fresh:
            closed_keys.add(key)
            if source.after == "move":
                self._archive(source, folder, rows)
            return True
        try:
            capture = self.capture_service.register(
                files_for(fresh),
                environment_id=environment_id,
                title=title,
                captured_at=captured_at,
                window_duration_seconds=source.window_duration_seconds,
                overlap_seconds=source.overlap_seconds,
                audio_offset_seconds=audio_offset_seconds,
                origin={
                    "kind": "watch",
                    "source": source.name,
                    "group": key,
                    "root": str(source.path),
                    "files": [row.rel_path for row in fresh],
                    **({"manifest": manifest_path} if manifest_path else {}),
                },
                file_time=datetime.fromtimestamp(
                    min(row.mtime_ns for row in fresh) / 1e9, timezone.utc
                ),
                capture_key=capture_key,
                extra_metadata=extra_metadata,
            )
        except (CaptureConflictError, ValueError, OSError) as exc:
            self._fail(source, key, fresh, str(exc))
            return False

        kinds = ", ".join(sorted({row.media_type or "?" for row in fresh}))
        self.activity.info(
            ACTIVITY_COMPONENT,
            f"Captura creada: «{capture.title}» con {len(fresh)} fichero(s) ({kinds})",
            source_name=source.name,
            capture_id=capture.id,
            data={"group": key, "files": [row.rel_path for row in fresh]},
        )
        # Primero el trabajo y después la anotación: si el proceso se corta
        # entre ambos, el siguiente barrido recupera la captura sin duplicarla.
        job = self.submit(capture.id)
        self._mark_captured(fresh, capture.id)
        closed_keys.add(key)
        report.capture_ids.append(capture.id)
        report.job_ids.append(job.id)
        if source.after == "move":
            self._archive(source, folder, rows)
        return True

    def _screen_known_content(
        self, source: WatchSource, key: str, rows: list[InboxFile]
    ) -> list[InboxFile]:
        """Separa lo que ya está en la memoria.

        Un contenido que ya pertenece a una captura creada desde este mismo
        grupo es una recuperación (el proceso se detuvo entre registrar la
        captura y anotarla aquí); si pertenece a otra captura, es un duplicado.
        """
        fresh: list[InboxFile] = []
        recovered: dict[str, list[InboxFile]] = {}
        for row in rows:
            asset = self.repository.find_asset_by_hash(row.content_hash or "")
            capture_id = asset.metadata.get("capture_id") if asset is not None else None
            if not capture_id:
                fresh.append(row)
                continue
            capture = self.repository.get_capture(capture_id)
            origin = capture.metadata.get("origin", {}) if capture is not None else {}
            if origin.get("kind") == "watch" and origin.get("source") == source.name and origin.get("group") == key:
                recovered.setdefault(capture_id, []).append(row)
                continue
            self._save(
                row,
                state=InboxFileState.DUPLICATE,
                error=f"su contenido ya pertenece a la captura {capture_id}",
            )
            self.activity.warning(
                ACTIVITY_COMPONENT,
                f"Duplicado, no se vuelve a capturar: {row.rel_path}",
                source_name=source.name,
                path=row.rel_path,
                capture_id=capture_id,
            )
        for capture_id, captured in recovered.items():
            jobs = self.repository.list_jobs(target_id=capture_id)
            job = jobs[-1] if jobs else self.submit(capture_id)
            self._mark_captured(captured, capture_id)
            self.activity.info(
                ACTIVITY_COMPONENT,
                f"Recuperada la captura del grupo {key} tras una interrupción",
                source_name=source.name,
                capture_id=capture_id,
                job_id=job.id,
            )
        return fresh

    def _mark_captured(self, rows: list[InboxFile], capture_id: str) -> None:
        self.repository.begin_transaction()
        try:
            for row in rows:
                self._save(row, state=InboxFileState.CAPTURED, capture_id=capture_id, error=None)
            self.repository.commit_transaction()
        except Exception:
            self.repository.rollback_transaction()
            raise

    def _fail(self, source: WatchSource, key: str, rows: list[InboxFile], message: str) -> None:
        for row in rows:
            self._save(row, state=InboxFileState.FAILED, error=message)
        self.activity.error(
            ACTIVITY_COMPONENT,
            f"No se pudo crear la captura del grupo {key}: {message}",
            source_name=source.name,
            data={"group": key, "files": [row.rel_path for row in rows]},
        )

    def _archive(self, source: WatchSource, folder: Path | None, rows: list[InboxFile]) -> None:
        """Mueve los originales del grupo, ya custodiados, a ``archive_dir``."""
        assert source.archive_dir is not None
        # Estado vigente: las filas del grupo acaban de marcarse capturadas.
        live = {row.id: row for row in self.repository.list_live_inbox_files(source.name)}
        rows = [live[row.id] for row in rows if row.id in live]
        moved: dict[str, Path] = {}
        targets: list[InboxFile] = rows
        try:
            source.archive_dir.mkdir(parents=True, exist_ok=True)
            prefix = f"{folder.name}/" if folder is not None else None
            in_folder = [row for row in live.values() if prefix and row.rel_path.startswith(prefix)]
            row_ids = {row.id for row in rows}
            # La carpeta entera solo se mueve si nada de ella sigue abierto.
            if folder is not None and all(
                row.id in row_ids or row.state not in _OPEN_STATES for row in in_folder
            ):
                destination = _free_path(source.archive_dir / folder.name)
                shutil.move(str(folder), str(destination))
                moved = {
                    row.id: destination / Path(row.rel_path).relative_to(folder.name)
                    for row in in_folder
                }
                targets = in_folder
            else:
                for row in rows:
                    destination = _free_path(source.archive_dir / row.rel_path)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(source.path / row.rel_path), str(destination))
                    moved[row.id] = destination
        except OSError as exc:
            self.activity.warning(
                ACTIVITY_COMPONENT,
                f"No se pudieron archivar los originales: {exc}",
                source_name=source.name,
            )
        for row in targets:
            if row.id in moved:
                self._save(row, present=False, archived_to=str(moved[row.id]))
        if moved:
            self.activity.info(
                ACTIVITY_COMPONENT,
                f"Originales archivados en {source.archive_dir}",
                source_name=source.name,
            )


def retry_failed_group(
    repository: EpisodicRepository, activity: ActivityLog, source_name: str, group_key: str
) -> int:
    """Devuelve los ficheros fallidos de un grupo a la cola del vigilante."""
    rows = [
        row
        for row in repository.list_live_inbox_files(source_name)
        if row.group_key == group_key and row.state == InboxFileState.FAILED
    ]
    for row in rows:
        # ``changed_at = 0``: se reinspecciona y el grupo se cierra en el próximo barrido.
        repository.save_inbox_file(
            replace(
                row,
                state=InboxFileState.PENDING,
                changed_at=0.0,
                content_hash=None,
                media_type=None,
                error=None,
                updated_at=utc_now(),
            )
        )
    if rows:
        activity.info(
            ACTIVITY_COMPONENT,
            f"Reintento solicitado para el grupo {group_key} ({len(rows)} fichero(s))",
            source_name=source_name,
        )
    return len(rows)


def _top_folder(rel_path: str) -> str | None:
    head, separator, _rest = rel_path.partition("/")
    return head if separator else None


def _unique_key(base: str, taken: set[str]) -> str:
    key, number = base, 2
    while key in taken:
        key, number = f"{base}#{number}", number + 1
    return key


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatchcase(lowered, pattern.lower()) for pattern in patterns)


def _free_path(path: Path) -> Path:
    if not path.exists():
        return path
    number = 2
    while True:
        candidate = path.with_name(f"{path.stem}-{number}{path.suffix}")
        if not candidate.exists():
            return candidate
        number += 1


def _size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"  # pragma: no cover


def _iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
