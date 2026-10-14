"""Mantener el índice al día sin que nadie tenga que acordarse.

Qdrant y el índice de texto son proyecciones de SQLite. Cada captura nueva se
indexa al procesarse, pero lo ya indexado queda obsoleto cuando cambia *cómo*
se indexa: el modelo de texto o de imagen, los prefijos de tarea o el formato
de los pasajes. SQLite guarda la **huella** con la que se indexó; al arrancar
se compara con la configuración vigente y, si no coincide, o faltan pasajes, o
Qdrant está vacío, se encola un trabajo de reindexado que se ve en
Vigilancia como cualquier captura. La reconstrucción completa se hace en una
generación aparte: la búsqueda sigue funcionando hasta que se activa.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from episodic.application.indexing import PASSAGES_TARGET, IndexingWorker
from episodic.application.lifecycle import LifecycleService
from episodic.application.progress import note
from episodic.domain.captures import Job, JobState, StageState
from episodic.ports.repository import EpisodicRepository
from episodic.ports.vector_index import VectorIndex

logger = logging.getLogger(__name__)

REINDEX_JOB_KIND = "reindex"
REINDEX_STAGES: tuple[tuple[str, str], ...] = (
    ("check", "Comprobación"),
    ("index", "Indexación"),
)
# Quién lo pidió: va en el target_id del trabajo.
TRIGGER_STARTUP = "arranque"
TRIGGER_MANUAL = "manual"

PROFILE_KEY = "index_profile"
# Súbase al cambiar el texto que se indexa (episode_index_text o
# passage_text): obliga a reindexar lo existente al arrancar.
INDEX_FORMAT_VERSION = 1

_PROFILE_LABELS = {
    "text_model": "modelo de texto",
    "document_prefix": "prefijo de documento",
    "image_model": "modelo de imagen",
    "format_version": "formato de los pasajes y episodios",
}


def index_profile(*, text_model: str, document_prefix: str, image_model: str) -> dict:
    """Todo lo que, si cambia, deja obsoletos los vectores o los pasajes guardados
    (el prefijo de consulta no: solo afecta a la pregunta)."""
    return {
        "text_model": text_model,
        "document_prefix": document_prefix,
        "image_model": image_model,
        "format_version": INDEX_FORMAT_VERSION,
    }


@dataclass
class IndexStatus:
    """Si el índice refleja la configuración vigente y, si no, por qué."""

    episodes: int
    points: int
    indexed_passages: int
    current_profile: dict
    stored_profile: Optional[dict]
    reasons: list[str] = field(default_factory=list)
    # Todo de nuevo (vectores obsoletos) o solo lo que falta.
    full: bool = False
    missing_episode_ids: list[str] = field(default_factory=list)

    @property
    def up_to_date(self) -> bool:
        return not self.reasons

    def to_dict(self) -> dict:
        return {
            "up_to_date": self.up_to_date,
            "reasons": list(self.reasons),
            "full": self.full,
            "missing_episodes": len(self.missing_episode_ids),
            "episodes": self.episodes,
            "points": self.points,
            "indexed_passages": self.indexed_passages,
            "current_profile": self.current_profile,
            "stored_profile": self.stored_profile,
        }


class IndexMaintenanceService:
    def __init__(
        self,
        repository: EpisodicRepository,
        vector_index: VectorIndex,
        lifecycle: LifecycleService,
        worker: IndexingWorker,
        profile: dict,
    ) -> None:
        self.repository = repository
        self.vector_index = vector_index
        self.lifecycle = lifecycle
        self.worker = worker
        self.profile = profile

    # -- Comprobación -------------------------------------------------------

    def status(self) -> IndexStatus:
        episodes = self._visible_episodes()
        stored = self.repository.get_system_state(PROFILE_KEY)
        status = IndexStatus(
            episodes=len(episodes),
            points=self.vector_index.count(),
            indexed_passages=self.repository.count_passages(),
            current_profile=self.profile,
            stored_profile=stored,
        )
        if not episodes:
            return status
        if stored is None:
            status.reasons.append("no consta con qué configuración se indexó (índice anterior a esta comprobación)")
        elif stored != self.profile:
            for key, label in _PROFILE_LABELS.items():
                if stored.get(key) != self.profile.get(key):
                    status.reasons.append(f"cambió el {label}: {stored.get(key)!r} → {self.profile.get(key)!r}")
        if status.points == 0:
            status.reasons.append(f"Qdrant no tiene puntos y hay {len(episodes)} episodio(s)")
        status.full = bool(status.reasons)
        status.missing_episode_ids = [
            episode.id
            for episode in episodes
            if not any(
                rep.target_type == PASSAGES_TARGET
                for rep in self.repository.list_representations_for_target(episode.id)
            )
        ]
        if status.missing_episode_ids and not status.full:
            status.reasons.append(f"{len(status.missing_episode_ids)} episodio(s) sin pasajes indexados")
        return status

    def ensure_current(self, submit: Callable[[str], Job]) -> Optional[Job]:
        """Al arrancar: encola el reindexado si hace falta y no hay ya uno."""
        status = self.status()
        if status.episodes == 0 and status.stored_profile != self.profile:
            # Base vacía: lo que llegue se indexará ya con esta configuración.
            self.repository.set_system_state(PROFILE_KEY, self.profile)
            return None
        if status.up_to_date:
            return None
        pending = self.active_job()
        if pending is not None:
            return pending
        logger.info("El índice no está al día (%s): se encola el reindexado", "; ".join(status.reasons))
        return submit(TRIGGER_STARTUP)

    def active_job(self) -> Optional[Job]:
        return next(
            (
                job
                for job in self.repository.list_jobs(states=[JobState.QUEUED, JobState.RUNNING])
                if job.job_type == REINDEX_JOB_KIND
            ),
            None,
        )

    # -- Ejecución ------------------------------------------------------------

    def run(self, job: Job, report) -> dict:
        """Manejador del trabajo: reindexa lo necesario (o todo, si se pidió)."""
        report("check", StageState.RUNNING, "")
        status = self.status()
        full = status.full or job.target_id == TRIGGER_MANUAL
        if job.target_id == TRIGGER_MANUAL and not status.reasons:
            status.reasons.append("pedido a mano")
        report(
            "check",
            StageState.COMPLETED,
            "; ".join(status.reasons) if status.reasons else "el índice ya estaba al día",
        )

        report("index", StageState.RUNNING, "")
        if full:
            total = self.rebuild()
            detail = f"reconstrucción completa: {total} representación(es), {self.vector_index.count()} puntos"
        elif status.missing_episode_ids:
            note(f"indexando los pasajes de {len(status.missing_episode_ids)} episodio(s)")
            self.lifecycle.index_episodes(self.worker, status.missing_episode_ids)
            total = len(status.missing_episode_ids)
            detail = f"{total} episodio(s) completados"
        else:
            total = 0
            detail = "nada que hacer"
        report("index", StageState.COMPLETED, detail)
        after = self.status()
        return {"reasons": status.reasons, "full": full, "indexed": total, "status": after.to_dict()}

    def rebuild(self) -> int:
        """Reconstrucción completa; al terminar, la huella pasa a ser la vigente."""
        total = self.lifecycle.rebuild_index(self.worker)
        self.repository.set_system_state(PROFILE_KEY, self.profile)
        return total

    def _visible_episodes(self) -> list:
        episodes, cursor = [], None
        while True:
            page, cursor = self.repository.list_episodes(limit=100, cursor=cursor)
            episodes.extend(episode for episode in page if episode.is_visible)
            if not cursor:
                return episodes
