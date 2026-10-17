"""Lecturas de inspección para hacer auditable la memoria desde la UI."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from episodic.domain.entities import Episode, Observation
from episodic.domain.episodes import MicroEpisode
from episodic.domain.processing import Event, ProcessingWindow
from episodic.ports.repository import EpisodicRepository


class MemoryInspectionService:
    """Construye vistas de solo lectura sin convertir texto derivado en hechos."""

    def __init__(self, repository: EpisodicRepository) -> None:
        self.repository = repository

    def list_assets(self, environment_id: str | None = None) -> list[dict[str, Any]]:
        return [self.asset_summary(asset.id) for asset in self.repository.list_assets(environment_id)]

    def asset_summary(self, asset_id: str) -> dict[str, Any]:
        asset = self.repository.get_asset(asset_id)
        if asset is None:
            raise ValueError(f"Asset no encontrado: {asset_id}")
        observations = self.repository.list_observations_for_asset(asset.id)
        windows = self.repository.list_processing_windows_for_asset(asset.id)
        references = self.repository.list_evidence_references_for_asset(asset.id)
        return {
            "asset": asset.to_dict(),
            "storage": {
                "mode": asset.metadata.get("storage_mode", "external_reference"),
                "available": Path(asset.uri).is_file(),
                "managed_uri": asset.metadata.get("managed_uri"),
            },
            "counts": {
                "processing_windows": len(windows),
                "observations": len(observations),
                "evidence_references": len(references),
            },
        }

    def asset_detail(self, asset_id: str) -> dict[str, Any]:
        summary = self.asset_summary(asset_id)
        asset = self.repository.get_asset(asset_id)
        assert asset is not None
        source = self.repository.get_source(asset.source_id)
        environment = self.repository.get_environment(asset.environment_id)
        observations = self.repository.list_observations_for_asset(asset.id)
        return {
            **summary,
            "source": source.to_dict() if source else None,
            "environment": environment.to_dict() if environment else None,
            "processing_windows": [
                item.to_dict()
                for item in self.repository.list_processing_windows_for_asset(asset.id)
            ],
            "observations": [item.to_dict() for item in observations],
            "evidence_references": [
                item.to_dict()
                for item in self.repository.list_evidence_references_for_asset(asset.id)
            ],
        }

    def episode_detail(self, episode_id: str) -> dict[str, Any]:
        episode = self.repository.get_episode(episode_id)
        if episode is None:
            raise ValueError(f"Episodio no encontrado: {episode_id}")

        observations = self.repository.list_observations_for_episode(episode.id)
        observations_by_id = {item.id: item for item in observations}
        microepisodes = [
            item
            for item_id in episode.microepisode_ids
            if (item := self.repository.get_microepisode(item_id)) is not None
        ]
        events = self._events_for_microepisodes(microepisodes)

        reference_ids = list(episode.metadata.get("evidence_reference_ids", []))
        reference_ids.extend(
            reference_id
            for observation in observations
            for reference_id in observation.evidence_reference_ids
        )
        reference_ids.extend(
            reference_id
            for event in events
            for reference_id in event.evidence_reference_ids
        )
        relations = self.repository.list_relations(node_type="episode", node_id=episode.id)
        hypotheses = self.repository.list_hypotheses(node_type="episode", node_id=episode.id)
        reference_ids.extend(
            reference_id for relation in relations for reference_id in relation.evidence_reference_ids
        )
        reference_ids.extend(
            reference_id
            for hypothesis in hypotheses
            for reference_id in hypothesis.evidence_reference_ids
        )
        references = [
            item
            for reference_id in dict.fromkeys(reference_ids)
            if (item := self.repository.get_evidence_reference(reference_id)) is not None
        ]

        asset_ids = list(
            dict.fromkeys(
                [item.asset_id for item in observations if item.asset_id]
                + [item.asset_id for item in references]
            )
        )
        assets = [
            item for asset_id in asset_ids if (item := self.repository.get_asset(asset_id)) is not None
        ]
        source_ids = list(
            dict.fromkeys(
                episode.source_ids
                + [item.source_id for item in observations if item.source_id]
            )
        )
        environment_ids = list(
            dict.fromkeys(
                episode.environment_ids
                + [item.environment_id for item in observations if item.environment_id]
            )
        )

        nested_microepisodes = []
        grouped_observation_ids: set[str] = set()
        for microepisode in microepisodes:
            micro_observations = [
                observations_by_id[item_id]
                for item_id in microepisode.observation_ids
                if item_id in observations_by_id
            ]
            grouped_observation_ids.update(item.id for item in micro_observations)
            micro_events = [
                event for event in events if event.id in microepisode.event_ids
            ]
            nested_microepisodes.append(
                {
                    "microepisode": microepisode.to_dict(),
                    "observations": [item.to_dict() for item in micro_observations],
                    "events": [item.to_dict() for item in micro_events],
                }
            )

        representations = self.repository.list_representations_for_target(episode.id)
        return {
            "episode": episode.to_dict(),
            "microepisodes": nested_microepisodes,
            "ungrouped_observations": [
                item.to_dict() for item in observations if item.id not in grouped_observation_ids
            ],
            "events": [item.to_dict() for item in events],
            # Texto generado por el modelo, con su revisión; None si no lo hay.
            "summary": (
                current.to_dict()
                if (current := self.repository.get_current_summary("episode", episode.id)) is not None
                else None
            ),
            "summary_revisions": len(self.repository.list_summary_revisions("episode", episode.id)),
            "evidence_references": [item.to_dict() for item in references],
            "assets": [
                {
                    **item.to_dict(),
                    "available": Path(item.uri).is_file(),
                    "storage_mode": item.metadata.get("storage_mode", "external_reference"),
                }
                for item in assets
            ],
            "sources": [
                item.to_dict()
                for source_id in source_ids
                if (item := self.repository.get_source(source_id)) is not None
            ],
            "environments": [
                item.to_dict()
                for environment_id in environment_ids
                if (item := self.repository.get_environment(environment_id)) is not None
            ],
            "relations": [item.to_dict() for item in relations],
            "hypotheses": [item.to_dict() for item in hypotheses],
            "vector_projection": {
                "representations": [
                    {
                        "id": item.id,
                        "target_type": item.target_type,
                        "target_id": item.target_id,
                        "space": item.space.value,
                        "model_id": item.model_id,
                        "model_revision": item.model_revision,
                        "dimension": item.dimension,
                        "indexing_state": item.indexing_state.value,
                        "qdrant_point_id": item.qdrant_point_id,
                        "created_at": item.created_at.isoformat(),
                    }
                    for item in representations
                ],
                "indexed": any(item.indexing_state.value == "indexed" for item in representations),
                "point_ids": [
                    item.qdrant_point_id for item in representations if item.qdrant_point_id
                ],
            },
        }

    def timeline(
        self,
        *,
        environment_id: str | None = None,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        observations = self.repository.list_observations(environment_id)
        if source_id:
            observations = [item for item in observations if item.source_id == source_id]
        observation_ids = {item.id for item in observations}

        microepisodes = self.repository.list_microepisodes(environment_id)
        if source_id:
            microepisodes = [item for item in microepisodes if source_id in item.source_ids]
        microepisode_ids = {item.id for item in microepisodes}

        episodes = self._episodes(environment_id)
        if source_id:
            episodes = [item for item in episodes if source_id in item.source_ids]

        assets = self.repository.list_assets(environment_id)
        if source_id:
            assets = [item for item in assets if item.source_id == source_id]
        windows = [
            window
            for asset in assets
            for window in self.repository.list_processing_windows_for_asset(asset.id)
        ]
        events = self._events_for_microepisodes(microepisodes)

        items: list[dict[str, Any]] = []
        items.extend(self._timeline_window(item) for item in windows)
        items.extend(self._timeline_observation(item) for item in observations)
        items.extend(self._timeline_event(item) for item in events)
        items.extend(self._timeline_microepisode(item) for item in microepisodes)
        items.extend(self._timeline_episode(item) for item in episodes)
        items.sort(key=lambda item: (item["start_time"] or "", item["kind"], item["id"]))

        lane_source_ids = sorted(
            {
                item["source_id"]
                for item in items
                if item.get("source_id")
            }
        )
        lanes = [
            {
                "id": source_id_value,
                "label": source.name if (source := self.repository.get_source(source_id_value)) else source_id_value,
                "kind": "source",
            }
            for source_id_value in lane_source_ids
        ]
        lanes.append({"id": "episodic", "label": "Memoria episódica", "kind": "memory"})
        return {"lanes": lanes, "items": items}

    def _episodes(self, environment_id: str | None) -> list[Episode]:
        if environment_id:
            return self.repository.list_episodes_for_environment(environment_id)
        result: list[Episode] = []
        cursor = None
        while True:
            page, cursor = self.repository.list_episodes(limit=100, cursor=cursor)
            result.extend(page)
            if cursor is None:
                return result

    def _events_for_microepisodes(self, microepisodes: list[MicroEpisode]) -> list[Event]:
        return [
            item
            for event_id in dict.fromkeys(
                event_id for microepisode in microepisodes for event_id in microepisode.event_ids
            )
            if (item := self.repository.get_event(event_id)) is not None
        ]

    @staticmethod
    def _base_timeline(
        *,
        item_id: str,
        kind: str,
        title: str,
        start_time,
        end_time,
        source_id: str | None,
        environment_id: str | None,
        parent_id: str | None = None,
        confidence: float | None = None,
        evidence_reference_ids: list[str] | None = None,
        technical: bool = False,
    ) -> dict[str, Any]:
        return {
            "id": item_id,
            "kind": kind,
            "title": title,
            "start_time": start_time.isoformat() if start_time else None,
            "end_time": end_time.isoformat() if end_time else None,
            "source_id": source_id,
            "environment_id": environment_id,
            "lane_id": source_id or "episodic",
            "parent_id": parent_id,
            "confidence": confidence,
            "evidence_reference_ids": evidence_reference_ids or [],
            "technical": technical,
        }

    def _timeline_window(self, item: ProcessingWindow) -> dict[str, Any]:
        return self._base_timeline(
            item_id=item.id,
            kind="processing_window",
            title=f"Ventana {item.sequence_number}",
            start_time=item.start_time,
            end_time=item.end_time,
            source_id=item.source_id,
            environment_id=item.environment_id,
            parent_id=item.asset_id,
            confidence=item.confidence,
            technical=True,
        )

    def _timeline_observation(self, item: Observation) -> dict[str, Any]:
        return self._base_timeline(
            item_id=item.id,
            kind="observation",
            title=item.observation_type,
            start_time=item.start_time,
            end_time=item.end_time,
            source_id=item.source_id,
            environment_id=item.environment_id,
            parent_id=item.episode_id,
            confidence=item.confidence,
            evidence_reference_ids=item.evidence_reference_ids,
        )

    def _timeline_event(self, item: Event) -> dict[str, Any]:
        return self._base_timeline(
            item_id=item.id,
            kind="event",
            title=item.event_type,
            start_time=item.start_time,
            end_time=item.end_time,
            source_id=item.source_id,
            environment_id=item.environment_id,
            confidence=item.confidence,
            evidence_reference_ids=item.evidence_reference_ids,
        )

    def _timeline_microepisode(self, item: MicroEpisode) -> dict[str, Any]:
        return self._base_timeline(
            item_id=item.id,
            kind="microepisode",
            title=item.title,
            start_time=item.start_time,
            end_time=item.end_time,
            source_id=item.source_ids[0] if len(item.source_ids) == 1 else None,
            environment_id=item.environment_id,
            confidence=item.confidence,
            evidence_reference_ids=item.evidence_reference_ids,
        )

    def _timeline_episode(self, item: Episode) -> dict[str, Any]:
        return {
            **self._base_timeline(
                item_id=item.id,
                kind="episode",
                title=item.title,
                start_time=item.start_time,
                end_time=item.end_time,
                source_id=None,
                environment_id=item.environment_ids[0] if len(item.environment_ids) == 1 else None,
                confidence=item.confidence,
                evidence_reference_ids=list(item.metadata.get("evidence_reference_ids", [])),
            ),
            # Nombre de la captura en su herramienta de origen (<entorno>_<epoch>).
            "external_id": item.external_id,
        }
