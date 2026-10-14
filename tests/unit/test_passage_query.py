"""Expresión de texto completo a partir de la pregunta del usuario."""
from episodic.application.passage_search import lexical_query
from episodic.domain.entities import Observation
from episodic.domain.passages import passage_text


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
