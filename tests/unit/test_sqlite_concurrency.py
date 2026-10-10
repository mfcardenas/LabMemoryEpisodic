"""Una conexión compartida por el pool de hilos HTTP no mezcla resultados."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from episodic.adapters.sqlite.repository import SqliteEpisodicRepository
from episodic.domain.entities import Source
from episodic.domain.provenance import Environment
from episodic.domain.types import SourceType


def _seed(repository: SqliteEpisodicRepository, count: int) -> None:
    repository.save_environment(
        Environment(id="env", name="Ambiente", producer="test", model_version="1")
    )
    for index in range(count):
        repository.save_source(
            Source(
                id=f"src-{index}",
                environment_id="env",
                name=f"Fuente {index}",
                source_type=SourceType.CAMERA,
                producer="test",
                model_version="1",
            )
        )


def test_parallel_reads_never_receive_rows_from_another_query(tmp_path) -> None:
    repository = SqliteEpisodicRepository(tmp_path / "shared.db")
    _seed(repository, 40)

    def read(index: int):
        if index % 2:
            return len(repository.list_sources())
        return len(repository.list_environments())

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(read, range(400)))

    # La migración v2 añade un ambiente legado por defecto: siempre son 2.
    assert set(results) == {40, 2}
    repository.close()


def test_transaction_belongs_to_the_thread_that_opened_it(tmp_path) -> None:
    repository = SqliteEpisodicRepository(tmp_path / "tx.db")
    _seed(repository, 1)
    inside = threading.Event()
    release = threading.Event()
    other_committed = threading.Event()

    def writer() -> None:
        repository.begin_transaction()
        repository.save_source(
            Source(
                id="pending",
                environment_id="env",
                name="Pendiente",
                source_type=SourceType.CAMERA,
                producer="test",
                model_version="1",
            )
        )
        inside.set()
        release.wait(5)
        repository.rollback_transaction()

    def intruder() -> None:
        # No debe poder confirmar ni ver a medias la transacción ajena.
        repository.commit_transaction()
        repository.list_sources()
        other_committed.set()

    thread = threading.Thread(target=writer)
    thread.start()
    inside.wait(5)
    intruder_thread = threading.Thread(target=intruder)
    intruder_thread.start()
    assert not other_committed.wait(0.3), "el otro hilo debe esperar al lock"
    release.set()
    thread.join(5)
    intruder_thread.join(5)

    assert other_committed.is_set()
    assert repository.get_source("pending") is None
    repository.close()
