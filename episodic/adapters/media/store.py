"""Almacén de medios en sistema de archivos local (FilesystemEvidenceStore).

Implementa EvidenceStore.
- Deduplicación basada en contenido (SHA-256).
- Escritura atómica mediante archivo temporal y reemplazo.
- Comprobación de referencias antes de purgar blobs huérfanos.
"""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path
from typing import Callable, Optional, Tuple, Union

logger = logging.getLogger(__name__)


class FilesystemEvidenceStore:
    """Almacén de archivos de evidencia en disco local."""

    def __init__(
        self,
        media_dir: Union[str, Path],
        ref_checker: Optional[Callable[[str], int]] = None,
    ) -> None:
        self.media_dir = Path(media_dir).resolve()
        self.tmp_dir = self.media_dir / ".tmp"
        self.blobs_dir = self.media_dir / "blobs"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        self.ref_checker = ref_checker

    def set_ref_checker(self, ref_checker: Callable[[str], int]) -> None:
        """Establece la función de consulta de referencias activas."""
        self.ref_checker = ref_checker

    def store(self, data: bytes, filename: str) -> Tuple[str, str]:
        """Guarda un blob atómicamente si no existe y devuelve (internal_uri, content_hash).

        internal_uri es relativo a self.media_dir, e.g. "blobs/3a/3a1b2c3d...ext"
        """
        content_hash = hashlib.sha256(data).hexdigest()
        ext = Path(filename).suffix.lower() if filename else ".bin"
        if not ext:
            ext = ".bin"

        # Sharding de 2 niveles por prefijo de hash para evitar directorios gigantes
        prefix = content_hash[:2]
        blob_subfolder = self.blobs_dir / prefix
        blob_subfolder.mkdir(parents=True, exist_ok=True)

        target_file = blob_subfolder / f"{content_hash}{ext}"
        relative_uri = str(target_file.relative_to(self.media_dir)).replace("\\", "/")

        if not target_file.exists():
            # Escritura atómica a través del directorio temporal en el mismo volumen
            with tempfile.NamedTemporaryFile(dir=self.tmp_dir, delete=False) as tf:
                tf.write(data)
                temp_path = Path(tf.name)
            try:
                os.replace(temp_path, target_file)
            except Exception:
                if temp_path.exists():
                    temp_path.unlink(missing_ok=True)
                raise
            logger.info("Stored new blob: %s (hash: %s)", relative_uri, content_hash)
        else:
            logger.debug("Blob already exists (deduplicated): %s", relative_uri)

        return relative_uri, content_hash

    def store_file(self, source_path: Union[str, Path]) -> Tuple[str, str, int]:
        """Copia atómica de un archivo existente hacia el almacén de medios."""
        src = Path(source_path)
        digest = hashlib.sha256()
        size = 0
        temp_path: Path | None = None
        try:
            with src.open("rb") as source, tempfile.NamedTemporaryFile(
                dir=self.tmp_dir, delete=False
            ) as temporary:
                temp_path = Path(temporary.name)
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
                    size += len(block)
                    temporary.write(block)

            content_hash = digest.hexdigest()
            extension = src.suffix.lower() or ".bin"
            target_dir = self.blobs_dir / content_hash[:2]
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{content_hash}{extension}"
            relative_uri = str(target.relative_to(self.media_dir)).replace("\\", "/")
            if target.exists():
                temp_path.unlink(missing_ok=True)
            else:
                os.replace(temp_path, target)
            return relative_uri, content_hash, size
        except Exception:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise

    def retrieve(self, internal_uri: str) -> bytes:
        """Lee el contenido binario del blob identificado por su URI interna."""
        file_path = self.resolve_path(internal_uri)
        if not file_path.exists():
            raise FileNotFoundError(f"Blob no encontrado en almacén: {internal_uri}")
        with open(file_path, "rb") as f:
            return f.read()

    def resolve_path(self, internal_uri: str) -> Path:
        """Devuelve la ruta absoluta en disco asegurando que no escape de media_dir."""
        # Evitar path traversal
        clean_rel = Path(internal_uri.replace("/", os.sep).replace("\\", os.sep))
        resolved = (self.media_dir / clean_rel).resolve()
        if not resolved.is_relative_to(self.media_dir):
            raise ValueError(f"Ruta de evidencia inválida (escape de directorio): {internal_uri}")
        return resolved

    def exists(self, internal_uri: str) -> bool:
        """Comprueba si el blob existe en el almacén."""
        try:
            return self.resolve_path(internal_uri).exists()
        except ValueError:
            return False

    def delete_if_unreferenced(self, internal_uri: str, content_hash: str) -> bool:
        """Elimina el archivo en disco únicamente si no quedan referencias activas."""
        if self.ref_checker:
            active_refs = self.ref_checker(content_hash)
            if active_refs > 0:
                logger.info(
                    "Blob %s no se elimina: todavía tiene %d referencias activas",
                    content_hash,
                    active_refs,
                )
                return False

        try:
            target = self.resolve_path(internal_uri)
            if target.exists():
                target.unlink()
                logger.info("Purged unreferenced blob: %s", internal_uri)
                return True
        except Exception as e:
            logger.warning("Error eliminando blob %s: %s", internal_uri, e)
        return False
