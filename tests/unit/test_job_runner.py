"""Cola durable: reanudación, etapas, reintentos acotados y fallos deterministas."""
from __future__ import annotations

from episodic.adapters.sqlite.repository import SqliteEpisodicRepository
from episodic.application.jobs import JobRunner
from episodic.domain.captures import JobState, StageState

STAGES = (("uno", "Uno"), ("dos", "Dos"))


def _runner(tmp_path, handler, **kwargs):
    repository = SqliteEpisodicRepository(tmp_path / "jobs.db")
    return repository, JobRunner(repository, {"demo": handler}, **kwargs)


def test_stages_are_reported_and_result_persisted(tmp_path) -> None:
    def handler(job, report):
        report("uno", StageState.COMPLETED, "hecho")
        report("dos", StageState.COMPLETED, "")
        return {"target": job.target_id}

    repository, runner = _runner(tmp_path, handler)
    job = runner.submit("demo", "objetivo-1", STAGES)
    assert runner.run_next()

    stored = repository.get_job(job.id)
    assert stored.state == JobState.COMPLETED
    assert stored.result == {"target": "objetivo-1"}
    assert stored.progress == 1.0
    assert stored.stages[0].detail == "hecho"
    repository.close()


def test_pending_jobs_resume_after_restart(tmp_path) -> None:
    calls = []

    def handler(job, report):
        calls.append(job.id)
        return {}

    repository, runner = _runner(tmp_path, handler)
    job = runner.submit("demo", "objetivo-1", STAGES)
    repository.close()  # el proceso se detiene antes de ejecutar

    repository, restarted = _runner(tmp_path, handler)
    restarted.start()
    finished = restarted.wait(job.id, timeout=10)
    restarted.stop()

    assert finished.state == JobState.COMPLETED
    assert calls == [job.id]
    repository.close()


def test_transient_errors_retry_and_deterministic_errors_fail(tmp_path) -> None:
    attempts = {"transitorio": 0, "determinista": 0}

    def handler(job, report):
        attempts[job.target_id] += 1
        report("uno", StageState.RUNNING, "")
        if job.target_id == "transitorio" and attempts["transitorio"] == 1:
            raise RuntimeError("red caída")
        if job.target_id == "determinista":
            raise ValueError("archivo sin contenido")
        return {}

    repository, runner = _runner(tmp_path, handler, max_attempts=2)
    transient = runner.submit("demo", "transitorio", STAGES)
    deterministic = runner.submit("demo", "determinista", STAGES)
    while runner.run_next():
        pass

    assert repository.get_job(transient.id).state == JobState.COMPLETED
    failed = repository.get_job(deterministic.id)
    assert failed.state == JobState.FAILED
    assert failed.error == "archivo sin contenido"
    assert failed.stages[0].state == StageState.FAILED
    assert attempts == {"transitorio": 2, "determinista": 1}
    repository.close()
