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


def test_activity_log_records_each_stage_and_the_outcome(tmp_path) -> None:
    from episodic.application.activity import ActivityLog

    def handler(job, report):
        report("uno", StageState.RUNNING, "video.mp4 (1/2)")
        report("uno", StageState.COMPLETED, "3 observación(es)")
        if job.target_id == "roto":
            raise ValueError("sin contenido")
        return {}

    repository = SqliteEpisodicRepository(tmp_path / "jobs.db")
    runner = JobRunner(
        repository,
        {"demo": handler},
        activity=ActivityLog(repository),
        describe=lambda job: f"captura «{job.target_id}»",
    )
    ok = runner.submit("demo", "bien", STAGES)
    runner.submit("demo", "roto", STAGES)
    while runner.run_next():
        pass

    entries = repository.list_activity(limit=50, component="jobs")
    messages = [entry.message for entry in entries]
    assert messages[:6] == [
        "Trabajo encolado: captura «bien»",
        "Trabajo encolado: captura «roto»",
        "Inicio: captura «bien»",
        "Uno · en curso: video.mp4 (1/2)",
        "Uno · hecho: 3 observación(es)",
        "Completado: captura «bien»",
    ]
    assert messages[-1] == "Fallido: captura «roto»: sin contenido"
    assert entries[-1].level.value == "error"
    assert entries[3].job_id == ok.id
    assert entries[3].data == {"job_type": "demo", "target_id": "bien", "stage": "uno"}

    # Retención: se conservan las líneas más recientes.
    assert repository.prune_activity(keep=2) == len(entries) - 2
    assert [e.message for e in repository.list_activity()] == messages[-2:]
    repository.close()


def test_progress_notes_update_the_running_stage_and_the_log(tmp_path) -> None:
    from episodic.application.activity import ActivityLog
    from episodic.application.progress import note, progress_scope

    seen = []

    def handler(job, report):
        note("fuera de etapa")
        report("uno", StageState.RUNNING, "video.mp4 (1/1)")
        with progress_scope("video.mp4 (1/1)"):
            note("tramo 1/2 descrito")
            seen.append(repository.get_job(job.id).stages[0].detail)
            note("tramo 2/2 descrito")
        report("uno", StageState.COMPLETED, "2 observación(es)")
        return {}

    repository = SqliteEpisodicRepository(tmp_path / "jobs.db")
    runner = JobRunner(repository, {"demo": handler}, activity=ActivityLog(repository))
    job = runner.submit("demo", "objetivo", STAGES)
    assert runner.run_next()

    # La ficha del trabajo muestra el paso en curso mientras dura la etapa.
    assert seen == ["video.mp4 (1/1) · tramo 1/2 descrito"]
    assert repository.get_job(job.id).stages[0].detail == "2 observación(es)"
    steps = [entry for entry in repository.list_activity() if entry.data.get("step")]
    assert [entry.message for entry in steps] == [
        "Uno · video.mp4 (1/1) · tramo 1/2 descrito",
        "Uno · video.mp4 (1/1) · tramo 2/2 descrito",
    ]
    assert steps[0].data["stage"] == "uno" and steps[0].job_id == job.id
    assert "fuera de etapa" in [entry.message for entry in repository.list_activity()]
    note("sin trabajo en curso: no hace nada")
    repository.close()


def test_watcher_passes_are_pruned_apart_from_the_rest(tmp_path) -> None:
    from episodic.application.activity import ActivityLog
    from episodic.domain.watch import ActivityLevel

    repository = SqliteEpisodicRepository(tmp_path / "jobs.db")
    log = ActivityLog(repository, max_rows=3, max_debug_rows=2)
    log.info("watch", "Captura creada")
    for number in range(1, 6):
        log.debug("watch", f"Pase {number}")
    log.info("jobs", "Completado")

    # Los pases no desplazan las líneas importantes, ni al revés.
    assert repository.prune_activity(2, [ActivityLevel.DEBUG]) == 3
    assert repository.prune_activity(3, [ActivityLevel.INFO]) == 0
    assert [entry.message for entry in repository.list_activity()] == [
        "Captura creada",
        "Pase 4",
        "Pase 5",
        "Completado",
    ]
    repository.close()
