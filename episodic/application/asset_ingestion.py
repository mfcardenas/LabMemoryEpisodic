"""Registro de fuentes y activos originales, sin extracción multimodal."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from episodic.adapters.files.metadata import FileInspection, LocalFileInspector
from episodic.domain.entities import Source
from episodic.domain.provenance import Asset, Environment
from episodic.domain.types import EvidenceMediaType, SourceType
from episodic.ports.repository import EpisodicRepository, EvidenceStore


@dataclass(frozen=True)
class AssetIngestionResult:
    asset: Asset
    duplicate: bool


class AssetIngestionService:
    """Construye Environment -> Source -> Asset y aplica deduplicación SHA-256."""

    def __init__(
        self,
        repository: EpisodicRepository,
        inspector: LocalFileInspector,
        managed_store: EvidenceStore | None = None,
    ) -> None:
        self.repository = repository
        self.inspector = inspector
        self.managed_store = managed_store

    def ingest_file(
        self,
        file_path: str | Path,
        *,
        environment_id: str,
        source_id: str,
        source_type: SourceType | None = None,
        captured_at: datetime | None = None,
        original_filename: str | None = None,
    ) -> AssetIngestionResult:
        inspected = self.inspector.inspect(file_path)
        origin_label = original_filename or inspected.path.name
        resolved_source_type = source_type or _source_type_for(inspected.media_type)

        duplicate = self.repository.find_asset_by_hash(inspected.content_hash)
        if duplicate is not None:
            if (
                duplicate.environment_id != environment_id
                or duplicate.source_id != source_id
            ):
                raise ValueError(
                    "El archivo ya está registrado con otra asociación "
                    f"(asset={duplicate.id}, environment={duplicate.environment_id}, "
                    f"source={duplicate.source_id})"
                )
            refreshed = replace(
                duplicate,
                mime_type=inspected.mime_type,
                size_bytes=inspected.size_bytes,
                duration_seconds=inspected.duration_seconds,
                metadata={
                    **duplicate.metadata,
                    "last_ingested_as": origin_label,
                    "inspection": inspected.metadata,
                    "content_extracted": duplicate.metadata.get("content_extracted", False),
                },
            )
            self.repository.save_asset(refreshed)
            return AssetIngestionResult(asset=refreshed, duplicate=True)

        self._ensure_scope(environment_id, source_id, resolved_source_type)
        asset_uri = str(inspected.path)
        storage_metadata: dict[str, str] = {
            "storage_mode": "external_reference",
            "imported_from": str(inspected.path),
        }
        if self.managed_store is not None:
            internal_uri, stored_hash, stored_size = self.managed_store.store_file(
                inspected.path
            )
            if f"sha256:{stored_hash}" != inspected.content_hash:
                raise RuntimeError("El hash del activo gestionado no coincide con el original")
            if stored_size != inspected.size_bytes:
                raise RuntimeError("El tamaño del activo gestionado no coincide con el original")
            asset_uri = str(self.managed_store.resolve_path(internal_uri))
            storage_metadata = {
                "storage_mode": "managed_immutable",
                "managed_uri": internal_uri,
                "imported_from": origin_label,
            }

        asset = _asset_from_inspection(
            inspected,
            environment_id=environment_id,
            source_id=source_id,
            captured_at=captured_at,
            asset_uri=asset_uri,
            storage_metadata=storage_metadata,
            original_filename=original_filename,
        )
        self.repository.save_asset(asset)
        return AssetIngestionResult(asset=asset, duplicate=False)

    def _ensure_scope(
        self,
        environment_id: str,
        source_id: str,
        source_type: SourceType,
    ) -> None:
        environment = self.repository.get_environment(environment_id)
        if environment is None:
            self.repository.save_environment(
                Environment(
                    id=environment_id,
                    name=environment_id,
                    producer="epigraph-ingest",
                    model_version="phase3",
                )
            )

        source = self.repository.get_source(source_id)
        if source is None:
            self.repository.save_source(
                Source(
                    id=source_id,
                    environment_id=environment_id,
                    name=source_id,
                    source_type=source_type,
                    producer="epigraph-ingest",
                    model_version="phase3",
                )
            )
        elif source.environment_id != environment_id:
            raise ValueError(
                f"La fuente {source_id} pertenece al ambiente {source.environment_id}, "
                f"no a {environment_id}"
            )


def _source_type_for(media_type: EvidenceMediaType) -> SourceType:
    return {
        EvidenceMediaType.VIDEO: SourceType.CAMERA,
        EvidenceMediaType.IMAGE: SourceType.PHOTO_SET,
        EvidenceMediaType.AUDIO: SourceType.MICROPHONE,
        EvidenceMediaType.DOCUMENT: SourceType.DOCUMENT,
        EvidenceMediaType.TEXT: SourceType.DOCUMENT,
    }[media_type]


def _asset_from_inspection(
    inspected: FileInspection,
    *,
    environment_id: str,
    source_id: str,
    captured_at: datetime | None,
    asset_uri: str | None = None,
    storage_metadata: dict[str, str] | None = None,
    original_filename: str | None = None,
) -> Asset:
    return Asset(
        environment_id=environment_id,
        source_id=source_id,
        uri=asset_uri or str(inspected.path),
        media_type=inspected.media_type,
        mime_type=inspected.mime_type,
        content_hash=inspected.content_hash,
        original_filename=original_filename or inspected.path.name,
        size_bytes=inspected.size_bytes,
        duration_seconds=inspected.duration_seconds,
        captured_at=captured_at,
        producer="epigraph-ingest",
        model_version="phase3",
        metadata={
            "original_path": str(inspected.path),
            "inspection": inspected.metadata,
            "content_extracted": False,
            **(storage_metadata or {"storage_mode": "external_reference"}),
        },
    )
