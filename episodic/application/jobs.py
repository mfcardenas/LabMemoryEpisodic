"""Cola local durable de trabajos con un único hilo de ejecución.

Los trabajos viven en SQLite: si el proceso se detiene, al arrancar se vuelven
a encolar los que quedaron pendientes o a medias. Los manejadores deben ser
idempotentes (los procesadores y el compositor ya lo son).
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import replace
from typing import Callable, Mapping, Sequence

from episodic.domain.captures import Job, JobStage, JobState, StageState
from episodic.domain.times import utc_now
from episodic.ports.repository import EpisodicRepository

logger = logging.getLogger(__name__)

StageReporter = Callable[[str, StageState, str], None]
JobHandler = Callable[[Job, StageReporter], dict]


class JobRunner:
    """Ejecuta trabajos en segundo plano y publica su progreso por etapas.

    ``repository`` debe ser una conexión propia del hilo de trabajo: SQLite
    en modo WAL permite que la API lea mientras el trabajo escribe.
    """

    def __init__(
        self,
        repository: EpisodicRepository,
        handlers: Mapping[str, JobHandler],
        max_attempts: int = 2,
    ) -> None:
        self.repository = repository
        self.handlers = dict(handlers)
        self.max_attempts = max_attempts
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._finished = threading.Condition()

    # -- Ciclo de vida ------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            for job in self.repository.list_jobs(states=[JobState.QUEUED, JobState.RUNNING]):
                if job.state == JobState.RUNNING:
                    logger.warning("Reanudando trabajo interrumpido %s", job.id)
                self._queue.put(job.id)
            self._thread = threading.Thread(
                target=self._loop, name="epigraph-jobs", daemon=True
            )
            self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread is not None and thread.is_alive():
            self._queue.put(None)
            thread.join(timeout)

    # -- API ----------------------------------------------------------------

    def submit(self, kind: str, target_id: str, stages: Sequence[tuple[str, str]]) -> Job:
        if kind not in self.handlers:
            raise ValueError(f"Tipo de trabajo no registrado: {kind}")
        job = Job(
            job_type=kind,
            target_id=target_id,
            stages=[JobStage(name=name, label=label) for name, label in stages],
        )
        self.repository.save_job(job)
        self._queue.put(job.id)
        return job

    def wait(self, job_id: str, timeout: float = 60.0) -> Job:
        """Bloquea hasta que el trabajo termine (CLI y pruebas)."""
        deadline = time.monotonic() + timeout
        with self._finished:
            while True:
                job = self.repository.get_job(job_id)
                if job is None:
                    raise ValueError(f"Trabajo inexistente: {job_id}")
                if job.state in (JobState.COMPLETED, JobState.FAILED):
                    return job
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"El trabajo {job_id} no terminó en {timeout} s")
                self._finished.wait(min(remaining, 0.5))

    def run_next(self) -> bool:
        """Ejecuta un trabajo en el hilo actual; útil sin hilo de fondo."""
        try:
            job_id = self._queue.get_nowait()
        except queue.Empty:
            return False
        if job_id is not None:
            self._execute(job_id)
        return True

    # -- Ejecución ----------------------------------------------------------

    def _loop(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                return
            try:
                self._execute(job_id)
            except Exception:  # pragma: no cover - el bucle nunca debe morir
                logger.exception("Fallo inesperado ejecutando el trabajo %s", job_id)

    def _execute(self, job_id: str) -> None:
        job = self.repository.get_job(job_id)
        if job is None or job.state in (JobState.COMPLETED, JobState.FAILED):
            return
        job = replace(
            job,
            state=JobState.RUNNING,
            attempts=job.attempts + 1,
            error=None,
            updated_at=utc_now(),
        )
        self.repository.save_job(job)

        def report(name: str, state: StageState, detail: str = "") -> None:
            nonlocal job
            stages = [
                replace(stage, state=state, detail=detail) if stage.name == name else stage
                for stage in job.stages
            ]
            job = replace(job, stages=stages, updated_at=utc_now())
            self.repository.save_job(job)

        try:
            result = self.handlers[job.job_type](job, report)
        except Exception as exc:
            logger.exception("El trabajo %s falló", job.id)
            stages = [
                replace(stage, state=StageState.FAILED, detail=stage.detail or str(exc))
                if stage.state == StageState.RUNNING
                else stage
                for stage in job.stages
            ]
            retry = job.attempts < self.max_attempts and not isinstance(exc, ValueError)
            job = replace(
                job,
                state=JobState.QUEUED if retry else JobState.FAILED,
                stages=stages,
                error=str(exc),
                updated_at=utc_now(),
            )
            self.repository.save_job(job)
            if retry:
                self._queue.put(job.id)
        else:
            job = replace(
                job,
                state=JobState.COMPLETED,
                result=result or {},
                updated_at=utc_now(),
            )
            self.repository.save_job(job)
        finally:
            with self._finished:
                self._finished.notify_all()
