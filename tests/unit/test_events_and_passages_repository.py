"""El evento como unidad de consulta en SQLite: columnas propias, vecinos,
enlace con el episodio, y pasajes con filtros aplicados antes del límite."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from episodic.adapters.sqlite.migrations import MIGRATIONS, init_connection
from episodic.adapters.sqlite.repository import SqliteEpisodicRepository
from episodic.domain.entities import Episode, Observation, Source
from episodic.domain.passages import Passage, episode_passages
from episodic.domain.processing import Event
from episodic.domain.provenance import Asset, Environment
from episodic.domain.times import Interval, TimeSpec
from episodic.domain.types import EvidenceMediaType, SourceType

T0 = datetime(2026, 10, 12, 14, 21, 15, tzinfo=timezone.utc)


def _repository(tmp_path) -> SqliteEpisodicRepository:
    repo = SqliteEpisodicRepository(tmp_path / "memory.db")
    repo.save_environment(Environment(id="taller", name="Taller", producer="test"))
    repo.save_source(Source(id="taller.cam", environment_id="taller", name="cam", source_type=SourceType.CAMERA))
    repo.save_asset(
        Asset(id="asset-1", environment_id="taller", source_id="taller.cam", uri="x://v.mp4",
              media_type=EvidenceMediaType.VIDEO, content_hash="h1")
    )
    return repo


def _event(seq: int, title: str, **extra) -> Event:
    return Event(
        id=f"ev-{seq}", event_type="video_segment", description=f"Descripción {title}", environment_id="taller",
        source_id="taller.cam", asset_id="asset-1", sequence_number=seq, title=title,
        start_offset_seconds=seq * 5.0, end_offset_seconds=seq * 5.0 + 5.0,
        start_time=T0 + timedelta(seconds=seq * 5), end_time=T0 + timedelta(seconds=seq * 5 + 5),
        producer="video-event-derivation", model_version="events-v2",
        metadata={"observation_ids": [f"obs-{seq}"]}, **extra,
    )


def _episode_with_events(repo, external_id="taller_1", seqs=(0, 1, 2)) -> Episode:
    episode = Episode(id=f"ep-{external_id}", title=external_id, external_id=external_id, environment_ids=["taller"],
                      start_time=T0, end_time=T0 + timedelta(seconds=15), source_ids=["taller.cam"])
    repo.save_episode(episode)
    observations = []
    for seq in seqs:
        obs = Observation(id=f"obs-{seq}", episode_id=episode.id, observation_type="video_event",
                          description=f"tramo {seq}", environment_id="taller", source_id="taller.cam", asset_id="asset-1")
        repo.save_observation(obs)
        observations.append(obs)
    episode.observation_ids = [item.id for item in observations]
    repo.save_episode(episode)
    for seq in seqs:
        repo.save_event(_event(seq, f"Evento {seq}"))
    return episode


def test_events_are_linked_to_their_episode_and_listed_in_order(tmp_path) -> None:
    repo = _repository(tmp_path)
    episode = _episode_with_events(repo)
    assert repo.get_event("ev-1").episode_id is None

    assert repo.link_events_to_episode(episode.id, episode.observation_ids) == 3
    linked = repo.get_event("ev-1")
    assert linked.episode_id == episode.id
    assert json.loads(repo._conn.execute("SELECT payload_json FROM events WHERE id='ev-1'").fetchone()[0])["episode_id"] == episode.id
    assert [item.title for item in repo.list_events(episode.id)] == ["Evento 0", "Evento 1", "Evento 2"]
    assert set(repo.get_events(["ev-0", "ev-2", "nope"])) == {"ev-0", "ev-2"}
    assert repo.count_events() == 3

    # Volver a guardar el evento sin episodio no lo desenlaza.
    repo.save_event(_event(1, "Evento 1 bis"))
    assert repo.get_event("ev-1").episode_id == episode.id and repo.get_event("ev-1").title == "Evento 1 bis"


def test_neighbors_follow_the_sequence_within_the_episode(tmp_path) -> None:
    repo = _repository(tmp_path)
    episode = _episode_with_events(repo, seqs=(0, 1, 2, 3))
    repo.link_events_to_episode(episode.id, episode.observation_ids)

    before, after = repo.get_event_neighbors("ev-2", before=2, after=5)
    assert [item.id for item in before] == ["ev-0", "ev-1"]
    assert [item.id for item in after] == ["ev-3"]
    assert repo.get_event_neighbors("ev-0", before=1, after=1) == ([], [repo.get_event("ev-1")])
    assert repo.get_event_neighbors("missing") == ([], [])


def test_stale_events_of_a_reprocessed_asset_are_removed(tmp_path) -> None:
    repo = _repository(tmp_path)
    _episode_with_events(repo, seqs=(0, 1, 2))
    assert sorted(repo.delete_events_for_asset("asset-1", keep_ids=["ev-1"])) == ["ev-0", "ev-2"]
    assert repo.get_event("ev-0") is None and repo.get_event("ev-1") is not None


def test_passages_are_searchable_with_filters_before_the_limit(tmp_path) -> None:
    repo = _repository(tmp_path)
    repo.save_environment(Environment(id="oficina", name="Oficina", producer="test"))
    repo.save_source(Source(id="oficina.cam", environment_id="oficina", name="cam", source_type=SourceType.CAMERA))
    episode = _episode_with_events(repo)
    repo.link_events_to_episode(episode.id, episode.observation_ids)
    other = Episode(id="ep-oficina", title="Oficina", external_id="oficina_1", environment_ids=["oficina"],
                    start_time=T0 + timedelta(hours=2), end_time=T0 + timedelta(hours=2, seconds=10))
    repo.save_episode(other)

    passages = episode_passages(episode, repo.list_events(episode.id), repo.list_observations_for_episode(episode.id))
    assert [item.kind for item in passages] == ["video_event"] * 3
    assert repo.replace_episode_passages(episode.id, passages) == []
    repo.replace_episode_passages(other.id, [
        Passage(id="ps-oficina", kind="speech_segment", episode_id=other.id, text="Descripción Evento oficina",
                environment_id="oficina", modality="audio", start_time=other.start_time, end_time=other.end_time),
    ])
    assert repo.count_passages() == 4

    everything = repo.search_passages('"descripcion"*', 10)
    assert {row[1] for row in everything} == {episode.id, other.id}
    # Con el límite a 1 y sin filtros el taller puede quedar fuera; con filtro no.
    only_office = repo.search_passages('"descripcion"*', 1, environment_ids=("oficina",))
    assert [row[0] for row in only_office] == ["ps-oficina"]
    assert repo.search_passages('"descripcion"*', 10, modalities=("audio",)) and not repo.search_passages(
        '"descripcion"*', 10, kinds=("document_text_fragment",)
    )
    start_ms = int((T0 + timedelta(seconds=9)).timestamp() * 1000)
    end_ms = int((T0 + timedelta(seconds=11)).timestamp() * 1000)
    in_window = repo.search_passages('"descripcion"*', 10, start_ms=start_ms, end_ms=end_ms)
    assert {row[0] for row in in_window} == {"ev-1", "ev-2"}
    assert "\x02" in in_window[0][3]  # fragmento con el término resaltado

    hydrated = repo.get_passages(["ev-1", "ps-oficina"])
    assert hydrated["ev-1"].event_id == "ev-1" and hydrated["ev-1"].start_offset_seconds == 5.0
    assert hydrated["ps-oficina"].modality == "audio"
    assert [item.id for item in repo.list_passages_for_episode(episode.id)] == ["ev-0", "ev-1", "ev-2"]

    # Reemplazar con menos pasajes devuelve los retirados y el FTS los olvida.
    assert repo.replace_episode_passages(episode.id, passages[:1]) == ["ev-1", "ev-2"]
    assert {row[0] for row in repo.search_passages('"descripcion"*', 10)} == {"ev-0", "ps-oficina"}
    assert repo.delete_episode_passages(other.id) == ["ps-oficina"] and repo.count_passages() == 1


def test_episode_interval_filter_is_applied_in_sql(tmp_path) -> None:
    repo = _repository(tmp_path)
    for index in range(3):
        start = T0 + timedelta(hours=index)
        repo.save_episode(Episode(id=f"ep-{index}", title=str(index), external_id=f"e{index}", environment_ids=["taller"],
                                  start_time=start, end_time=start + timedelta(minutes=10)))
    window = Interval(start=TimeSpec.from_utc(T0 + timedelta(minutes=50)), end=TimeSpec.from_utc(T0 + timedelta(minutes=75)))
    episodes, _ = repo.list_episodes(interval=window, limit=1)
    assert [item.id for item in episodes] == ["ep-1"]


def test_migration_v10_backfills_event_columns_from_the_payload(tmp_path) -> None:
    database = tmp_path / "v9.db"
    connection = sqlite3.connect(database)
    init_connection(connection)
    for version, sql in MIGRATIONS[:9]:
        connection.executescript(sql)
        connection.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (version, "2026-01-01T00:00:00+00:00"))
    connection.execute("INSERT INTO environments(id, name, producer, created_at) VALUES ('taller', 'Taller', 't', '2026-01-01T00:00:00+00:00')")
    connection.execute(
        "INSERT INTO episodes(id, title, external_id, created_at) VALUES ('ep-old', 'Old', 'taller_0', '2026-01-01T00:00:00+00:00')"
    )
    connection.execute(
        "INSERT INTO observations(id, episode_id, content, provenance_kind, created_at) "
        "VALUES ('obs-old', 'ep-old', 'tramo', 'model_description', '2026-01-01T00:00:00+00:00')"
    )
    event = _event(4, "Antiguo")
    payload = event.to_dict()
    payload["metadata"] = {"observation_ids": ["obs-old"]}
    for key in ("episode_id", "revision"):
        payload.pop(key, None)
    connection.execute(
        "INSERT INTO events(id, environment_id, event_type, start_time, end_time, created_at, payload_json) "
        "VALUES ('ev-4', 'taller', 'video_segment', ?, ?, ?, ?)",
        (event.start_time.isoformat(), event.end_time.isoformat(), event.created_at.isoformat(), json.dumps(payload)),
    )
    connection.execute("INSERT INTO passage_index(text, title, observation_id, episode_id, observation_type) VALUES ('viejo', '', 'obs-old', 'ep-old', 'video_event')")
    connection.commit()
    connection.close()

    repo = SqliteEpisodicRepository(database)
    try:
        migrated = repo.get_event("ev-4")
        assert migrated.episode_id == "ep-old" and migrated.revision == 1
        row = repo._conn.execute("SELECT episode_id, asset_id, sequence_number, start_offset_seconds, title FROM events WHERE id='ev-4'").fetchone()
        assert tuple(row) == ("ep-old", "asset-1", 4, 20.0, "Antiguo")
        assert [item.id for item in repo.list_events("ep-old")] == ["ev-4"]
        # El índice de texto antiguo se descarta: el reindexado lo rehace.
        assert repo.count_passages() == 0
    finally:
        repo.close()
