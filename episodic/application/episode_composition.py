"""Construcción determinista de microepisodios y episodios (Fase 5)."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from episodic.domain.captures import Capture
from episodic.domain.entities import Episode, Observation
from episodic.domain.episodes import MicroEpisode
from episodic.domain.processing import Event
from episodic.ports.repository import EpisodicRepository


_ID_NAMESPACE = uuid.UUID("8ae3659e-5f7c-4d88-a26f-f168c1fa657d")
_COMPOSER_KEY = "phase5-deterministic-composer"


@dataclass(frozen=True)
class EpisodeRuleConfig:
    """Reglas serializables; ningún campo depende de ProcessingWindow."""

    max_observation_gap_seconds: float = 5.0
    max_episode_gap_seconds: float = 60.0
    scene_change_threshold: float = 0.7
    acoustic_relevance_threshold: float = 0.7
    synchronized_source_groups: tuple[tuple[str, ...], ...] = ()
    compatible_action_groups: tuple[tuple[str, ...], ...] = ()
    compatible_state_groups: tuple[tuple[str, ...], ...] = ()
    require_source_compatibility: bool = True
    require_entity_continuity: bool = True
    require_action_compatibility: bool = True
    require_state_compatibility: bool = True
    boundary_on_entity_transition: bool = True
    boundary_on_interaction_transition: bool = True
    boundary_on_relevant_acoustic_event: bool = True

    def __post_init__(self) -> None:
        if self.max_observation_gap_seconds < 0 or self.max_episode_gap_seconds < 0:
            raise ValueError("Los umbrales temporales no pueden ser negativos")
        for name, value in (
            ("scene_change_threshold", self.scene_change_threshold),
            ("acoustic_relevance_threshold", self.acoustic_relevance_threshold),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} debe estar entre 0 y 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_observation_gap_seconds": self.max_observation_gap_seconds,
            "max_episode_gap_seconds": self.max_episode_gap_seconds,
            "scene_change_threshold": self.scene_change_threshold,
            "acoustic_relevance_threshold": self.acoustic_relevance_threshold,
            "synchronized_source_groups": [list(group) for group in self.synchronized_source_groups],
            "compatible_action_groups": [list(group) for group in self.compatible_action_groups],
            "compatible_state_groups": [list(group) for group in self.compatible_state_groups],
            "require_source_compatibility": self.require_source_compatibility,
            "require_entity_continuity": self.require_entity_continuity,
            "require_action_compatibility": self.require_action_compatibility,
            "require_state_compatibility": self.require_state_compatibility,
            "boundary_on_entity_transition": self.boundary_on_entity_transition,
            "boundary_on_interaction_transition": self.boundary_on_interaction_transition,
            "boundary_on_relevant_acoustic_event": self.boundary_on_relevant_acoustic_event,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EpisodeRuleConfig":
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError(f"Reglas desconocidas: {', '.join(unknown)}")
        values = dict(payload)
        for key in (
            "synchronized_source_groups",
            "compatible_action_groups",
            "compatible_state_groups",
        ):
            if key in values:
                values[key] = tuple(tuple(str(item) for item in group) for group in values[key])
        return cls(**values)

    @classmethod
    def from_json_file(cls, path: str | Path) -> "EpisodeRuleConfig":
        with Path(path).open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
        if not isinstance(payload, dict):
            raise ValueError("El fichero de reglas debe contener un objeto JSON")
        return cls.from_dict(payload)

    @property
    def fingerprint(self) -> str:
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class EpisodeCompositionResult:
    microepisodes: tuple[MicroEpisode, ...]
    episodes: tuple[Episode, ...]
    observation_count: int
    removed_microepisode_ids: tuple[str, ...] = ()
    removed_episode_ids: tuple[str, ...] = ()


@dataclass
class _ObservationGroup:
    observations: list[Observation] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    boundary_start_rules: list[str] = field(default_factory=list)
    termination_rules: list[str] = field(default_factory=list)


@dataclass
class _MicroEpisodeGroup:
    microepisodes: list[MicroEpisode] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    termination_rules: list[str] = field(default_factory=list)


class DeterministicEpisodeComposer:
    """Agrupa observaciones por reglas explícitas y reconstruibles."""

    producer = _COMPOSER_KEY
    model_version = "phase5-rules-v1"

    def __init__(
        self,
        repository: EpisodicRepository,
        config: EpisodeRuleConfig | None = None,
    ) -> None:
        self.repository = repository
        self.config = config or EpisodeRuleConfig()

    def compose(self, environment_id: str) -> EpisodeCompositionResult:
        """Recompone el ambiente por huecos temporales.

        Las observaciones que pertenecen a una captura quedan fuera: su
        episodio lo define la captura y no cambia por otras subidas.
        """
        if self.repository.get_environment(environment_id) is None:
            raise ValueError(f"Ambiente no encontrado: {environment_id}")
        captured_assets: dict[str, bool] = {}

        def belongs_to_capture(observation: Observation) -> bool:
            if not observation.asset_id:
                return False
            if observation.asset_id not in captured_assets:
                asset = self.repository.get_asset(observation.asset_id)
                captured_assets[observation.asset_id] = bool(
                    asset and asset.metadata.get("capture_id")
                )
            return captured_assets[observation.asset_id]

        observations = [
            item
            for item in self.repository.list_observations(environment_id)
            if not belongs_to_capture(item)
        ]
        groups = self._group_observations(observations)
        microepisodes = tuple(self._make_microepisode(group, environment_id) for group in groups)
        episode_groups = self._group_microepisodes(microepisodes)
        episodes = tuple(self._make_episode(group, environment_id) for group in episode_groups)
        return self._persist_reconstruction(
            environment_id, environment_id, observations, microepisodes, episodes
        )

    def compose_capture(
        self, capture: Capture, events: Sequence[Event] = ()
    ) -> EpisodeCompositionResult:
        """Construye exactamente un episodio con todas las observaciones de la captura.

        Cada evento se enlaza al microepisodio que contiene su observación de origen.
        """
        asset_ids = set(capture.asset_ids)
        observations = latest_processor_outputs(
            item
            for item in self.repository.list_observations(capture.environment_id)
            if item.asset_id in asset_ids
        )
        if not observations:
            raise ValueError(f"La captura {capture.id} no tiene observaciones que componer")
        # Los archivos de una captura se tomaron juntos: sus fuentes están
        # sincronizadas por declaración del usuario.
        sources = tuple(_ordered_unique(item.source_id for item in observations if item.source_id))
        scoped = DeterministicEpisodeComposer(
            self.repository,
            replace(
                self.config,
                synchronized_source_groups=(*self.config.synchronized_source_groups, sources),
            ),
        )
        groups = scoped._group_observations(observations)
        events_by_observation: dict[str, list[str]] = {}
        for event in sorted(events, key=lambda item: (item.start_time, item.sequence_number)):
            for observation_id in event.metadata.get("observation_ids", []):
                events_by_observation.setdefault(observation_id, []).append(event.id)
        microepisodes = tuple(
            replace(
                micro,
                event_ids=_ordered_unique(
                    event_id
                    for observation_id in micro.observation_ids
                    for event_id in events_by_observation.get(observation_id, [])
                ),
            )
            for micro in (
                scoped._make_microepisode(group, capture.environment_id, scope=capture.id)
                for group in groups
            )
        )
        episode = scoped._make_capture_episode(capture, microepisodes)
        return self._persist_reconstruction(
            capture.id, capture.environment_id, observations, microepisodes, (episode,)
        )

    def _group_observations(self, observations: Sequence[Observation]) -> list[_ObservationGroup]:
        ordered = sorted(observations, key=_observation_sort_key)
        groups: list[_ObservationGroup] = []
        current: _ObservationGroup | None = None
        for observation in ordered:
            if current is None:
                current = _ObservationGroup(
                    observations=[observation],
                    decisions=[_decision(observation.id, "started", ["first_observation"])],
                )
                continue
            boundary_rules = self._microepisode_boundary_rules(current.observations, observation)
            if boundary_rules:
                current.termination_rules = boundary_rules
                groups.append(current)
                current = _ObservationGroup(
                    observations=[observation],
                    decisions=[_decision(observation.id, "started", boundary_rules)],
                    boundary_start_rules=list(boundary_rules),
                )
            else:
                continuation_rules = self._continuation_rules(current.observations, observation)
                current.observations.append(observation)
                current.decisions.append(
                    _decision(observation.id, "continued", continuation_rules)
                )
        if current is not None:
            current.termination_rules = ["end_of_input"]
            groups.append(current)
        return groups

    def _microepisode_boundary_rules(
        self, current: Sequence[Observation], candidate: Observation
    ) -> list[str]:
        previous = current[-1]
        rules: list[str] = []
        gap = max(0.0, (_start(candidate) - max(_end(item) for item in current)).total_seconds())
        if gap > self.config.max_observation_gap_seconds:
            rules.append("temporal_gap_exceeded")
        if previous.environment_id != candidate.environment_id:
            rules.append("environment_changed")
        if self.config.require_source_compatibility and not self._sources_compatible(
            previous.source_id, candidate.source_id
        ):
            rules.append("source_not_synchronized")
        if self.config.require_entity_continuity:
            previous_entities = _entities(previous)
            candidate_entities = _entities(candidate)
            if previous_entities and candidate_entities and previous_entities.isdisjoint(candidate_entities):
                rules.append("entity_discontinuity")
        if self.config.require_action_compatibility and not _values_compatible(
            _attribute(previous, "action"),
            _attribute(candidate, "action"),
            self.config.compatible_action_groups,
        ):
            rules.append("action_incompatible")
        if self.config.require_state_compatibility and not _values_compatible(
            _attribute(previous, "state"),
            _attribute(candidate, "state"),
            self.config.compatible_state_groups,
        ):
            rules.append("state_incompatible")
        if _numeric_attribute(candidate, "scene_change_score") >= self.config.scene_change_threshold:
            rules.append("significant_scene_change")
        if bool(candidate.attributes.get("significant_scene_change")):
            rules.append("significant_scene_change")
        if self.config.boundary_on_entity_transition:
            if _attribute(candidate, "entity_transition") == "entry":
                rules.append("entity_entry")
            if _attribute(previous, "entity_transition") == "exit":
                rules.append("entity_exit")
        if self.config.boundary_on_interaction_transition:
            if _attribute(candidate, "interaction_phase") == "start":
                rules.append("interaction_start")
            if _attribute(previous, "interaction_phase") == "end":
                rules.append("interaction_end")
        if self.config.boundary_on_relevant_acoustic_event and (
            self._relevant_acoustic_event(candidate) or self._relevant_acoustic_event(previous)
        ):
            rules.append("relevant_acoustic_event")
        if _is_contradictory(candidate, current):
            rules.append("contradiction_boundary")
        return _deduplicate(rules)

    def _continuation_rules(
        self, current: Sequence[Observation], candidate: Observation
    ) -> list[str]:
        previous = current[-1]
        rules = ["temporal_proximity"]
        if previous.source_id and previous.source_id == candidate.source_id:
            rules.append("same_source")
        elif self._sources_compatible(previous.source_id, candidate.source_id):
            rules.append("synchronized_sources")
        if _entities(previous) & _entities(candidate):
            rules.append("entity_continuity")
        if _attribute(previous, "action") and _values_compatible(
            _attribute(previous, "action"),
            _attribute(candidate, "action"),
            self.config.compatible_action_groups,
        ):
            rules.append("action_compatible")
        if _attribute(previous, "state") and _values_compatible(
            _attribute(previous, "state"),
            _attribute(candidate, "state"),
            self.config.compatible_state_groups,
        ):
            rules.append("state_compatible")
        return _deduplicate(rules)

    def _sources_compatible(self, left: str | None, right: str | None) -> bool:
        if not left or not right or left == right:
            return True
        return any(left in group and right in group for group in self.config.synchronized_source_groups)

    def _relevant_acoustic_event(self, observation: Observation) -> bool:
        if observation.modality != "audio":
            return False
        return bool(observation.attributes.get("relevant_acoustic_event")) or (
            _numeric_attribute(observation, "acoustic_relevance")
            >= self.config.acoustic_relevance_threshold
        )

    def _make_microepisode(
        self, group: _ObservationGroup, environment_id: str, scope: str | None = None
    ) -> MicroEpisode:
        scope = scope or environment_id
        observations = group.observations
        observation_ids = [item.id for item in observations]
        sources = _ordered_unique(item.source_id for item in observations if item.source_id)
        evidence = _ordered_unique(
            reference_id
            for item in observations
            for reference_id in item.evidence_reference_ids
        )
        entities = sorted(set().union(*(_entities(item) for item in observations)))
        actions = _ordered_unique(
            value for item in observations if (value := _attribute(item, "action"))
        )
        states = _ordered_unique(
            value for item in observations if (value := _attribute(item, "state"))
        )
        locations = _ordered_unique(
            value for item in observations if (value := _attribute(item, "location"))
        )
        micro_id = _stable_id(
            "microepisode", self.config.fingerprint, scope, *observation_ids
        )
        title_value = actions[0] if len(actions) == 1 else observations[0].observation_type
        return MicroEpisode(
            id=micro_id,
            title=f"Microepisodio: {title_value}",
            environment_id=environment_id,
            source_ids=sources,
            observation_ids=observation_ids,
            event_ids=[],
            evidence_reference_ids=evidence,
            start_time=min(_start(item) for item in observations),
            end_time=max(_end(item) for item in observations),
            producer=self.producer,
            model_version=self.model_version,
            confidence=_minimum_confidence(observations),
            created_at=min(item.created_at for item in observations),
            metadata={
                "composer_key": _COMPOSER_KEY,
                "composition_scope": scope,
                "config_fingerprint": self.config.fingerprint,
                "rules": self.config.to_dict(),
                "grouping_decisions": group.decisions,
                "boundary_start_rules": group.boundary_start_rules,
                "termination_rules": group.termination_rules,
                "entity_ids": entities,
                "actions": actions,
                "states": states,
                "locations": locations,
                "processing_window_ids": _ordered_unique(
                    item.processing_window_id
                    for item in observations
                    if item.processing_window_id
                ),
                "contains_contradiction": any(
                    bool(item.attributes.get("contradiction"))
                    or bool(item.attributes.get("contradicts_observation_ids"))
                    for item in observations
                ),
            },
        )

    def _group_microepisodes(
        self, microepisodes: Sequence[MicroEpisode]
    ) -> list[_MicroEpisodeGroup]:
        groups: list[_MicroEpisodeGroup] = []
        current: _MicroEpisodeGroup | None = None
        for microepisode in sorted(microepisodes, key=lambda item: (item.start_time, item.end_time, item.id)):
            if current is None:
                current = _MicroEpisodeGroup(
                    microepisodes=[microepisode],
                    decisions=[_decision(microepisode.id, "started", ["first_microepisode"])],
                )
                continue
            rules = self._episode_relation_rules(current.microepisodes, microepisode)
            if not rules:
                current.termination_rules = ["no_episode_relation"]
                groups.append(current)
                current = _MicroEpisodeGroup(
                    microepisodes=[microepisode],
                    decisions=[_decision(microepisode.id, "started", ["no_episode_relation"])],
                )
            else:
                current.microepisodes.append(microepisode)
                current.decisions.append(_decision(microepisode.id, "continued", rules))
        if current is not None:
            current.termination_rules = ["end_of_input"]
            groups.append(current)
        return groups

    def _episode_relation_rules(
        self, current: Sequence[MicroEpisode], candidate: MicroEpisode
    ) -> list[str]:
        current_end = max(item.end_time for item in current if item.end_time is not None)
        gap = max(0.0, (candidate.start_time - current_end).total_seconds())  # type: ignore[operator]
        if gap > self.config.max_episode_gap_seconds:
            return []
        rules = ["temporal_overlap" if candidate.start_time <= current_end else "temporal_proximity"]
        current_entities = set().union(*(set(item.metadata.get("entity_ids", [])) for item in current))
        if current_entities & set(candidate.metadata.get("entity_ids", [])):
            rules.append("shared_entity")
        current_actions = set().union(*(set(item.metadata.get("actions", [])) for item in current))
        if current_actions & set(candidate.metadata.get("actions", [])):
            rules.append("shared_action")
        current_locations = set().union(*(set(item.metadata.get("locations", [])) for item in current))
        if current_locations & set(candidate.metadata.get("locations", [])):
            rules.append("shared_location")
        if any(item.environment_id == candidate.environment_id for item in current):
            rules.append("shared_environment")
        return rules

    def _make_episode(self, group: _MicroEpisodeGroup, environment_id: str) -> Episode:
        micros = group.microepisodes
        observation_ids = _ordered_unique(
            observation_id for micro in micros for observation_id in micro.observation_ids
        )
        source_ids = _ordered_unique(source_id for micro in micros for source_id in micro.source_ids)
        evidence_reference_ids = _ordered_unique(
            reference_id
            for micro in micros
            for reference_id in micro.evidence_reference_ids
        )
        episode_id = _stable_id(
            "episode", self.config.fingerprint, environment_id, *(item.id for item in micros)
        )
        indexable_text = "\n".join(
            observation.description
            for observation_id in observation_ids
            if (observation := self.repository.get_observation(observation_id)) is not None
        )
        return Episode(
            id=episode_id,
            title=f"Episodio compuesto ({len(micros)} microepisodio(s))",
            source_ids=source_ids,
            location=environment_id,
            observation_ids=observation_ids,
            indexable_text=indexable_text,
            environment_ids=[environment_id],
            microepisode_ids=[item.id for item in micros],
            start_time=min(item.start_time for item in micros if item.start_time is not None),
            end_time=max(item.end_time for item in micros if item.end_time is not None),
            producer=self.producer,
            model_version=self.model_version,
            confidence=_minimum_confidence(micros),
            created_at=min(item.created_at for item in micros),
            metadata={
                "composer_key": _COMPOSER_KEY,
                "composition_scope": environment_id,
                "config_fingerprint": self.config.fingerprint,
                "aggregation_decisions": group.decisions,
                "termination_rules": group.termination_rules,
                "evidence_reference_ids": evidence_reference_ids,
                "contains_contradiction": any(
                    bool(item.metadata.get("contains_contradiction")) for item in micros
                ),
            },
        )

    def _make_capture_episode(
        self, capture: Capture, microepisodes: Sequence[MicroEpisode]
    ) -> Episode:
        observation_ids = _ordered_unique(
            observation_id for micro in microepisodes for observation_id in micro.observation_ids
        )
        evidence_reference_ids = _ordered_unique(
            reference_id for micro in microepisodes for reference_id in micro.evidence_reference_ids
        )
        indexable_text = "\n".join(
            observation.description
            for observation_id in observation_ids
            if (observation := self.repository.get_observation(observation_id)) is not None
        )
        return Episode(
            # Estable por captura: reprocesar no cambia el enlace del episodio.
            id=_stable_id("episode", "capture", capture.id),
            # Nombre que la herramienta de captura dio a la captura, si lo hay.
            external_id=capture.metadata.get("capture_key"),
            title=capture.title,
            source_ids=_ordered_unique(
                source_id for micro in microepisodes for source_id in micro.source_ids
            ),
            location=capture.environment_id,
            observation_ids=observation_ids,
            indexable_text=indexable_text,
            environment_ids=[capture.environment_id],
            microepisode_ids=[item.id for item in microepisodes],
            start_time=min(item.start_time for item in microepisodes if item.start_time is not None),
            end_time=max(item.end_time for item in microepisodes if item.end_time is not None),
            producer=self.producer,
            model_version=self.model_version,
            confidence=_minimum_confidence(microepisodes),
            created_at=capture.created_at,
            metadata={
                "composer_key": _COMPOSER_KEY,
                "composition_scope": capture.id,
                "capture_id": capture.id,
                "captured_at_origin": capture.captured_at_origin.value,
                "config_fingerprint": self.config.fingerprint,
                "aggregation_decisions": [
                    _decision(item.id, "continued" if index else "started", ["same_capture"])
                    for index, item in enumerate(microepisodes)
                ],
                "termination_rules": ["capture_boundary"],
                "evidence_reference_ids": evidence_reference_ids,
                "contains_contradiction": any(
                    bool(item.metadata.get("contains_contradiction")) for item in microepisodes
                ),
            },
        )

    def _persist_reconstruction(
        self,
        scope: str,
        environment_id: str,
        observations: Sequence[Observation],
        microepisodes: tuple[MicroEpisode, ...],
        episodes: tuple[Episode, ...],
    ) -> EpisodeCompositionResult:
        old_episodes = [
            item
            for item in self.repository.list_episodes_for_environment(environment_id)
            if item.producer == self.producer
            and item.metadata.get("composition_scope") == scope
        ]
        old_micros = [
            item
            for item in self.repository.list_microepisodes(environment_id)
            if item.producer == self.producer
            and item.metadata.get("composition_scope") == scope
        ]
        new_episode_ids = {item.id for item in episodes}
        new_micro_ids = {item.id for item in microepisodes}
        stale_episode_ids = tuple(sorted(item.id for item in old_episodes if item.id not in new_episode_ids))
        stale_micro_ids = tuple(sorted(item.id for item in old_micros if item.id not in new_micro_ids))
        episode_for_observation = {
            observation_id: episode.id
            for episode in episodes
            for observation_id in episode.observation_ids
        }

        self.repository.begin_transaction()
        try:
            for episode_id in stale_episode_ids:
                self.repository.delete_episode_record(episode_id)
            for microepisode_id in stale_micro_ids:
                self.repository.delete_microepisode(microepisode_id)
            for microepisode in microepisodes:
                self.repository.save_microepisode(microepisode)
            for episode in episodes:
                self.repository.save_episode(episode)
            for observation in observations:
                expected_episode_id = episode_for_observation.get(observation.id)
                if observation.episode_id != expected_episode_id:
                    self.repository.save_observation(
                        replace(observation, episode_id=expected_episode_id)
                    )
            # Los eventos nacidos de esas observaciones pertenecen al mismo episodio.
            for episode in episodes:
                self.repository.link_events_to_episode(episode.id, list(episode.observation_ids))
            self.repository.commit_transaction()
        except Exception:
            self.repository.rollback_transaction()
            raise
        return EpisodeCompositionResult(
            microepisodes=microepisodes,
            episodes=episodes,
            observation_count=len(observations),
            removed_microepisode_ids=stale_micro_ids,
            removed_episode_ids=stale_episode_ids,
        )


def latest_processor_outputs(observations: Iterable[Observation]) -> list[Observation]:
    """Por activo y procesador, conserva solo la versión más reciente.

    Reprocesar con otro modelo crea observaciones nuevas (los IDs incluyen la
    versión); las anteriores se conservan en SQLite pero no se mezclan.
    """
    items = list(observations)
    latest: dict[tuple[str | None, str], tuple] = {}
    for item in items:
        key = (item.asset_id, item.producer)
        marker = (item.created_at, item.model_version or "")
        if key not in latest or marker > latest[key]:
            latest[key] = marker
    return [
        item
        for item in items
        if (item.model_version or "") == latest[(item.asset_id, item.producer)][1]
    ]


def _decision(target_id: str, action: str, rules: Sequence[str]) -> dict[str, Any]:
    return {"target_id": target_id, "action": action, "rules": list(rules)}


def _stable_id(kind: str, *parts: str) -> str:
    return str(uuid.uuid5(_ID_NAMESPACE, "|".join((kind, *parts))))


def _start(observation: Observation) -> datetime:
    return observation.start_time or observation.created_at


def _end(observation: Observation) -> datetime:
    return observation.end_time or observation.start_time or observation.created_at


def _observation_sort_key(observation: Observation) -> tuple[datetime, datetime, str]:
    return _start(observation), _end(observation), observation.id


def _attribute(observation: Observation, key: str) -> str | None:
    value = observation.attributes.get(key)
    return str(value).strip().lower() if value is not None and str(value).strip() else None


def _numeric_attribute(observation: Observation, key: str) -> float:
    value = observation.attributes.get(key, 0.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _entities(observation: Observation) -> set[str]:
    values = set()
    if observation.entity_id:
        values.add(observation.entity_id)
    extra = observation.attributes.get("entity_ids", [])
    if isinstance(extra, str):
        values.add(extra)
    elif isinstance(extra, Iterable):
        values.update(str(value) for value in extra if value)
    return values


def _values_compatible(
    left: str | None, right: str | None, groups: Sequence[Sequence[str]]
) -> bool:
    if not left or not right or left == right:
        return True
    return any(left in group and right in group for group in groups)


def _is_contradictory(candidate: Observation, current: Sequence[Observation]) -> bool:
    if bool(candidate.attributes.get("contradiction")):
        return True
    raw = candidate.attributes.get("contradicts_observation_ids", [])
    targets = {raw} if isinstance(raw, str) else set(raw or [])
    return bool(targets & {item.id for item in current})


def _ordered_unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _deduplicate(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _minimum_confidence(items: Sequence[Any]) -> float | None:
    values = [item.confidence for item in items if item.confidence is not None]
    return min(values) if values else None
