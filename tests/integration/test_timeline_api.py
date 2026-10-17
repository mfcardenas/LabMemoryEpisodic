"""Línea temporal a escala (Fase T): extensión, densidad por cubos locales,
elementos en ventana, grupos cuando no caben y miniaturas de fotogramas."""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from episodic.bootstrap import create_app_container
from episodic.config import EpisodicConfig
from episodic.domain.entities import Episode, Observation, Source
from episodic.domain.processing import Event
from episodic.domain.provenance import Asset, Environment
from episodic.domain.types import EvidenceMediaType, SourceType
from episodic.interfaces.http.server import create_api_app

T0 = datetime(2026, 10, 12, 14, 21, 15, tzinfo=timezone.utc)
# 22:30 UTC del 12 = 00:30 del 13 en Madrid: cuenta en el día local siguiente.
LATE = datetime(2026, 10, 12, 22, 30, tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _episode(repo, env, source, external_id, start, titles, keyframe_uri=None, speech=None):
    episode = Episode(id=f"ep-{external_id}", title=external_id, external_id=external_id, environment_ids=[env],
                      start_time=start, end_time=start + timedelta(seconds=5 * len(titles)), source_ids=[source])
    repo.save_episode(episode)
    for seq, title in enumerate(titles):
        repo.save_observation(Observation(id=f"obs-{external_id}-{seq}", episode_id=episode.id, observation_type="video_event",
                                          description=title, environment_id=env, source_id=source, asset_id=f"asset-{env}"))
        attributes = {"actions": [title.split()[0].lower()], "actors": [{"label": "persona 1"}],
                      "objects": [{"label": "llave", "attributes": ["roja"]}], "visible_text": ["F-102"]}
        if speech and seq == 0:
            attributes["speech"] = [{"observation_id": "x", "text": speech}]
        repo.save_event(Event(id=f"ev-{external_id}-{seq}", event_type="video_segment", description=f"Alguien decide {title.lower()}.",
                              environment_id=env, source_id=source, asset_id=f"asset-{env}", sequence_number=seq, title=title,
                              start_offset_seconds=seq * 5.0, end_offset_seconds=seq * 5.0 + 5.0,
                              start_time=start + timedelta(seconds=seq * 5), end_time=start + timedelta(seconds=seq * 5 + 5),
                              producer="video-event-derivation", model_version="events-v2", attributes=attributes,
                              keyframes=[{"internal_uri": keyframe_uri, "offset_seconds": 0.0, "frame_number": 0}] if keyframe_uri and seq == 0 else [],
                              metadata={"observation_ids": [f"obs-{external_id}-{seq}"]}))
    episode.observation_ids = [f"obs-{external_id}-{seq}" for seq in range(len(titles))]
    repo.save_episode(episode)
    repo.link_events_to_episode(episode.id, episode.observation_ids)
    return episode


@pytest.fixture()
def memory(tmp_path):
    config = EpisodicConfig(data_dir=str(tmp_path / "data"), watch_config="", query_timezone="Europe/Madrid")
    container = create_app_container(config, use_fake_encoders=True, use_memory_qdrant=True)
    repo = container.repository
    for env, name in (("taller", "Taller"), ("oficina", "Oficina")):
        repo.save_environment(Environment(id=env, name=name, producer="test"))
        repo.save_source(Source(id=f"{env}.cam", environment_id=env, name="cam", source_type=SourceType.CAMERA))
        repo.save_asset(Asset(id=f"asset-{env}", environment_id=env, source_id=f"{env}.cam", uri=f"x://{env}.mp4",
                              media_type=EvidenceMediaType.VIDEO, content_hash=f"h-{env}"))
    repo.save_source(Source(id="taller.mic", environment_id="taller", name="mic", source_type=SourceType.MICROPHONE))
    repo.save_source(Source(id="taller.manual", environment_id="taller", name="manual", source_type=SourceType.DOCUMENT))
    frame = io.BytesIO()
    Image.new("RGB", (640, 360), (200, 80, 40)).save(frame, "JPEG")
    keyframe_uri, _ = container.evidence_store.store(frame.getvalue(), "k.jpg")
    _episode(repo, "taller", "taller.cam", "taller_1", T0, ["Aplicar lubricante", "Girar la pieza", "Retirar la pieza"],
             keyframe_uri=keyframe_uri, speech="Mira, esto está agarrotado.")
    _episode(repo, "oficina", "oficina.cam", "oficina_1", LATE, ["Revisar documentos"])
    repo.save_observation(Observation(id="speech-1", episode_id="ep-taller_1", observation_type="speech_segment",
                                      description="Mira, esto está agarrotado.", environment_id="taller", source_id="taller.mic",
                                      start_time=T0 + timedelta(seconds=1), end_time=T0 + timedelta(seconds=4),
                                      attributes={"start_offset_seconds": 1.0}))
    repo.save_observation(Observation(id="doc-1", episode_id="ep-taller_1", observation_type="document_text_fragment",
                                      description="Par de apriete de la rueda: 110 Nm.", environment_id="taller",
                                      source_id="taller.manual", start_time=T0, end_time=T0, attributes={"page_number": 5}))
    yield container
    container.close()


@pytest.fixture()
def client(memory):
    with TestClient(create_api_app(container=memory, start_watcher=False)) as test_client:
        yield test_client


def test_span_lists_environments_with_counts_and_limits(client) -> None:
    span = client.get("/api/timeline/span").json()
    assert span["timezone"] == "Europe/Madrid" and span["events"] == 4
    assert span["first"].startswith("2026-10-12T14:21:15") and span["last"].startswith("2026-10-12T22:30")
    assert [(item["id"], item["name"], item["events"]) for item in span["environments"]] == [("oficina", "Oficina", 1), ("taller", "Taller", 3)]


def test_density_counts_by_local_day_and_fills_empty_buckets(client) -> None:
    out = client.get("/api/timeline/density", params={"start": "2026-10-11T22:00:00Z", "end": "2026-10-14T22:00:00Z", "bucket": "day"}).json()
    assert out["bucket"] == "day" and [b["key"] for b in out["buckets"]] == ["2026-10-12", "2026-10-13", "2026-10-14"]
    lanes = {lane["environment_id"]: lane["counts"] for lane in out["lanes"]}
    # El evento de la oficina es del 12 en UTC, pero del 13 en Madrid.
    assert lanes == {"oficina": [0, 1, 0], "taller": [3, 0, 0]} and out["totals"] == [3, 1, 0] and out["max"] == 3
    assert out["buckets"][1]["start"] == "2026-10-13T00:00:00+02:00"

    auto = client.get("/api/timeline/density", params={"start": _iso(T0 - timedelta(minutes=1)), "end": _iso(T0 + timedelta(minutes=2)),
                                                       "by_source": True}).json()
    assert auto["bucket"] == "minute" and auto["buckets"][1]["key"] == "2026-10-12T16:21"
    assert [(lane["environment_id"], lane["source_id"], lane["total"]) for lane in auto["lanes"]] == [("taller", "taller.cam", 3)]
    only = client.get("/api/timeline/density", params={"start": "2026-10-12T00:00:00Z", "end": "2026-10-13T00:00:00Z",
                                                       "environment_id": "oficina"}).json()
    assert only["bucket"] == "hour" and only["events"] == 1 and only["lanes"][0]["environment_id"] == "oficina"


def test_items_return_events_speech_documents_and_episodes_of_the_window(client) -> None:
    out = client.get("/api/timeline/items", params={"start": _iso(T0 - timedelta(seconds=10)), "end": _iso(T0 + timedelta(seconds=40))}).json()
    assert out["mode"] == "items" and out["events_total"] == 3 and out["timezone"] == "Europe/Madrid"
    first = out["events"][0]
    assert first["id"] == "ev-taller_1-0" and first["title"] == "Aplicar lubricante" and first["episode_id"] == "ep-taller_1"
    assert first["observation_id"] == "obs-taller_1-0" and first["keyframes"] == 1 and first["start_offset_seconds"] == 0.0
    assert first["actions"] == ["aplicar"] and first["actors"] == ["persona 1"] and first["objects"] == ["llave"]
    assert first["visible_text"] == ["F-102"] and first["speech"] == ["Mira, esto está agarrotado."]
    assert [item["id"] for item in out["speech"]] == ["speech-1"] and out["speech"][0]["source_id"] == "taller.mic"
    assert out["documents"][0]["page"] == 5 and out["documents"][0]["text"].startswith("Par de apriete")
    assert [item["id"] for item in out["episodes"]] == ["ep-taller_1"]

    # Un evento que empezó antes de la ventana y sigue dentro también aparece.
    late_start = client.get("/api/timeline/items", params={"start": _iso(T0 + timedelta(seconds=7)), "end": _iso(T0 + timedelta(seconds=8))}).json()
    assert [item["id"] for item in late_start["events"]] == ["ev-taller_1-1"]
    other = client.get("/api/timeline/items", params={"start": _iso(T0 - timedelta(seconds=10)), "end": _iso(T0 + timedelta(seconds=40)),
                                                      "environment_id": "oficina"}).json()
    assert other["events"] == [] and other["episodes"] == [] and other["speech"] == []


def test_items_fall_back_to_clusters_when_the_window_holds_too_many(client) -> None:
    out = client.get("/api/timeline/items", params={"start": "2026-10-12T00:00:00Z", "end": "2026-10-13T00:00:00Z", "limit": 2}).json()
    assert out["mode"] == "clusters" and out["events_total"] == 4 and "events" not in out
    density = out["density"]
    assert density["bucket"] == "hour" and {(lane["source_id"], lane["total"]) for lane in density["lanes"]} == {("taller.cam", 3), ("oficina.cam", 1)}
    assert {item["id"] for item in out["episodes"]} == {"ep-taller_1", "ep-oficina_1"}


def test_invalid_windows_are_rejected(client) -> None:
    assert client.get("/api/timeline/items", params={"start": "2026-10-12T10:00:00Z", "end": "2026-10-12T09:00:00Z"}).status_code == 422
    assert client.get("/api/timeline/density", params={"start": "2026-10-12T00:00:00Z", "end": "2026-10-13T00:00:00Z", "bucket": "week"}).status_code == 422


def test_keyframe_thumbnails_are_resized_and_cacheable(client) -> None:
    full = client.get("/api/memory/events/ev-taller_1-0/keyframes/0")
    assert full.status_code == 200 and Image.open(io.BytesIO(full.content)).size == (640, 360)
    thumb = client.get("/api/memory/events/ev-taller_1-0/keyframes/0", params={"max_side": 96})
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(thumb.content)).size == (96, 54) and "max-age" in thumb.headers["cache-control"]
    assert client.get("/api/memory/events/ev-taller_1-0/keyframes/0", params={"max_side": 8}).status_code == 422


def test_front_serves_the_new_timeline_and_hides_the_classic_one(client) -> None:
    page = client.get("/").text
    assert 'data-view="cronologia"' in page and 'id="view-cronologia"' in page
    # La vista clásica sigue en la página, oculta, para poder recuperarla.
    assert 'data-view="timeline" hidden' in page and 'id="view-timeline"' in page
    assert "/static/cronologia.js" in page and "/static/cronologia.css" in page
    script = client.get("/static/cronologia.js")
    assert script.status_code == 200 and "/api/timeline/items" in script.text and "/api/timeline/density" in script.text
    assert client.get("/static/cronologia.css").status_code == 200
    app = client.get("/static/app.js").text
    assert "const LEGACY_TIMELINE = false;" in app and "window.EpiGraph = " in app
    # Enlace con Consultas: resultados y citas se abren marcados en la línea temporal.
    assert "data-timeline-all" in app and "data-timeline-index" in app and "data-cite-timeline" in app
    assert "window.EpiGraphChrono.focus(" in app and "window.EpiGraphChrono = { activate, focus };" in script.text
    page_marks = client.get("/").text
    assert 'id="cr-markbar"' in page_marks and 'id="cr-marklayer"' in page_marks


def test_panel_style_is_optional_and_editorial_stays_the_default(client) -> None:
    page = client.get("/").text
    # El estilo «Panel» se carga, pero solo actúa con data-skin="panel" (selector o ?estilo=).
    assert "/static/tema-panel.css" in page and 'class="skin-switch"' in page
    assert 'data-estilo="editorial"' in page and 'data-estilo="panel-claro"' in page and 'data-estilo="panel-oscuro"' in page
    assert "<html lang=\"es\">" in page and "data-skin=" not in page.split("<head>")[0]
    css = client.get("/static/tema-panel.css")
    assert css.status_code == 200
    rules = [line for line in css.text.splitlines() if "{" in line and not line.lstrip().startswith(("/*", "@media", "}"))]
    assert rules and all("data-skin=\"panel\"" in line for line in rules)
    app = client.get("/static/app.js").text
    assert "epigraph.estilo" in app and "epigraph:estilo" in app
    assert 'addEventListener("epigraph:estilo"' in client.get("/static/cronologia.js").text


def test_density_cache_follows_new_events(client, memory) -> None:
    params = {"start": "2026-10-12T00:00:00Z", "end": "2026-10-13T00:00:00Z", "bucket": "day"}
    # Ventana UTC del 12: los tres eventos del taller y el de la oficina (22:30 UTC).
    assert client.get("/api/timeline/density", params=params).json()["events"] == 4
    memory.repository.save_event(Event(id="ev-extra", event_type="video_segment", description="Otro.", environment_id="taller",
                                       source_id="taller.cam", asset_id="asset-taller", sequence_number=9, title="Otro",
                                       start_time=T0 + timedelta(hours=1), end_time=T0 + timedelta(hours=1, seconds=5),
                                       producer="video-event-derivation", model_version="events-v2"))
    assert client.get("/api/timeline/density", params=params).json()["events"] == 5
    assert client.get("/api/timeline/span").json()["events"] == 5
