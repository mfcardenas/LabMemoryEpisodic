"""Expresión de texto completo a partir de la pregunta del usuario, y qué
texto se indexa de cada pasaje."""
from datetime import datetime, timezone

from episodic.application.passage_search import lexical_query
from episodic.domain.entities import Episode, Observation
from episodic.domain.passages import episode_passages, event_passage_text, passage_text
from episodic.domain.processing import Event


def test_terms_drop_stopwords_and_match_plurals_by_prefix() -> None:
    assert lexical_query("¿Dónde aparecen las mochilas con franja?") == '"aparecen"* OR "mochil"* OR "franj"*'


def test_quoted_phrases_are_kept_and_accents_folded() -> None:
    assert lexical_query('código "suela  blanca" credencial') == '"suela blanca" OR "codig"* OR "credencial"*'


def test_codes_keep_their_digits_and_short_words_go() -> None:
    assert lexical_query("sensor F-102") == '"sensor"* OR "102"'
    assert lexical_query("de la y el") == ""


def test_passage_text_uses_visual_title_summary_and_labels() -> None:
    observation = Observation(
        observation_type="video_event",
        description="Un mecánico corta una pieza.",
        attributes={
            "description": {
                "title": "Corte con radial",
                "summary": "Saltan chispas.",
                "objects": [{"label": "radial", "attributes": ["azul"]}, "gafas"],
            },
            "keyframes": [{"offset_seconds": 1.0}],
        },
    )
    assert passage_text(observation).splitlines() == [
        "Corte con radial",
        "Saltan chispas.",
        "Un mecánico corta una pieza.",
        "radial · azul",
        "gafas",
    ]
    assert passage_text(Observation(observation_type="audio_segment", content="Segmento de 3 s")) == ""


def test_prefix_matches_gender_and_number() -> None:
    assert lexical_query("pieza agarrotada") == lexical_query("piezas agarrotado")


def _event(**overrides) -> Event:
    base = dict(
        id="ev-1",
        event_type="video_segment",
        description="Un mecánico corta una pieza bajo un coche elevado.",
        environment_id="taller",
        start_time=datetime(2026, 10, 12, 14, 21, 15, tzinfo=timezone.utc),
        end_time=datetime(2026, 10, 12, 14, 21, 20, tzinfo=timezone.utc),
        producer="video-event-derivation",
        model_version="events-v2",
        asset_id="asset-1",
        sequence_number=0,
        start_offset_seconds=0.0,
        end_offset_seconds=5.4,
        title="Corte con radial",
        summary="Saltan chispas.",
        attributes={
            "scene": "Taller con luz artificial.",
            "actions": ["cortar"],
            "actors": [{"label": "persona 1", "description": "de espaldas"}],
            "objects": [{"label": "radial", "attributes": ["azul"]}],
            "visible_text": ["Mobil"],
            "speech": [{"observation_id": "obs-speech", "text": "Mira, esto está agarrotado."}],
            "changes_from_previous": "",
        },
        keyframes=[{"offset_seconds": 1.0}],
        metadata={"observation_ids": ["obs-video"], "generation": {"vision_model": "ollama:qwen3.5:4b"}},
    )
    base.update(overrides)
    return Event(**base)


def test_event_passage_text_includes_actors_visible_text_and_speech() -> None:
    assert event_passage_text(_event()).splitlines() == [
        "Corte con radial",
        "Saltan chispas.",
        "Mira, esto está agarrotado.",
        "radial · azul",
        "cortar",
        "persona 1 · de espaldas",
        "Mobil",
        "Un mecánico corta una pieza bajo un coche elevado.",
        "Taller con luz artificial.",
    ]


def test_episode_passages_prefer_events_and_skip_what_they_already_cover() -> None:
    episode = Episode(id="ep-1", title="Taller", environment_ids=["taller"])
    video = Observation(id="obs-video", episode_id="ep-1", observation_type="video_event", description="tramo")
    speech = Observation(id="obs-speech", episode_id="ep-1", observation_type="speech_segment", content="Mira…")
    loose_speech = Observation(
        id="obs-loose", episode_id="ep-1", observation_type="speech_segment", modality="audio",
        content="Vale, cuidamos con el latiguillo.", attributes={"start_offset_seconds": 6.0, "end_offset_seconds": 8.5},
        start_time=datetime(2026, 10, 12, 14, 21, 21, tzinfo=timezone.utc),
        end_time=datetime(2026, 10, 12, 14, 21, 23, tzinfo=timezone.utc),
    )
    noise = Observation(id="obs-status", episode_id="ep-1", observation_type="document_page_status", content="Página 2 sin texto.")
    document = Observation(
        id="obs-doc", episode_id="ep-1", observation_type="document_text_fragment", modality="document",
        content="No usar la radial cerca de latiguillos.", attributes={"page_number": 3},
    )
    passages = episode_passages(episode, [_event()], [video, speech, loose_speech, noise, document])

    assert [(item.kind, item.id) for item in passages] == [
        ("video_event", "ev-1"),
        ("speech_segment", "obs-loose"),
        ("document_text_fragment", "obs-doc"),
    ]
    event_passage = passages[0]
    assert event_passage.event_id == "ev-1" and event_passage.observation_id == "obs-video"
    assert event_passage.keyframes == 1 and event_passage.model == "ollama:qwen3.5:4b"
    assert (event_passage.start_offset_seconds, event_passage.end_offset_seconds) == (0.0, 5.4)
    assert passages[1].start_offset_seconds == 6.0 and passages[1].modality == "audio"
    assert passages[2].page_number == 3 and passages[2].start_time is None
    assert passages[0].to_dict()["observation_type"] == "video_event"  # compatibilidad con el front
