"""La pregunta se entiende sin modelos: intención, intervalo y texto a buscar."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from episodic.application.query_understanding import parse_query

MADRID = ZoneInfo("Europe/Madrid")
# Miércoles 14 de octubre de 2026, 18:00 en Madrid (16:00 UTC).
NOW = datetime(2026, 10, 14, 18, 0, tzinfo=MADRID)


def _plan(text, **kwargs):
    return parse_query(text, now=NOW, tz=MADRID, **kwargs)


def _utc(plan):
    return plan.interval.start.absolute.astimezone(timezone.utc), plan.interval.end.absolute.astimezone(timezone.utc)


@pytest.mark.parametrize(
    "text, start, end",
    [
        ("¿qué pasó hoy?", "2026-10-13T22:00", "2026-10-14T22:00"),
        ("¿qué pasó ayer?", "2026-10-12T22:00", "2026-10-13T22:00"),
        ("anteayer por la mañana", "2026-10-12T04:00", "2026-10-12T10:00"),
        ("ayer por la tarde en el taller", "2026-10-13T12:00", "2026-10-13T19:00"),
        ("esta tarde", "2026-10-14T12:00", "2026-10-14T19:00"),
        ("esta semana", "2026-10-11T22:00", "2026-10-18T22:00"),
        ("la semana pasada", "2026-10-04T22:00", "2026-10-11T22:00"),
        ("el mes pasado", "2026-08-31T22:00", "2026-09-30T22:00"),
        ("hace tres días", "2026-10-10T22:00", "2026-10-11T22:00"),
        ("en las últimas 2 horas", "2026-10-14T14:00", "2026-10-14T16:01"),
        ("el lunes", "2026-10-11T22:00", "2026-10-12T22:00"),
        ("el lunes por la noche", "2026-10-12T19:00", "2026-10-12T22:00"),
        ("el 12 de octubre", "2026-10-11T22:00", "2026-10-12T22:00"),
        ("el 12 de octubre de 2026 después de las 16:30", "2026-10-12T14:30", "2026-10-12T22:00"),
        ("el 25 de diciembre", "2025-12-24T23:00", "2025-12-25T23:00"),
        ("2026-10-12 antes de las 9", "2026-10-11T22:00", "2026-10-12T07:00"),
        ("12/10/2026 entre las 16:00 y las 17:00", "2026-10-12T14:00", "2026-10-12T15:00"),
        ("ayer a las 9", "2026-10-13T07:00", "2026-10-13T08:00"),
        ("ayer sobre las 10", "2026-10-13T07:30", "2026-10-13T08:30"),
    ],
)
def test_temporal_expressions_become_absolute_intervals(text, start, end) -> None:
    plan = _plan(text)
    assert plan.interval is not None, text
    got = _utc(plan)
    assert got == (
        datetime.fromisoformat(start).replace(tzinfo=timezone.utc),
        datetime.fromisoformat(end).replace(tzinfo=timezone.utc),
    ), text


def test_time_expression_leaves_the_search_text() -> None:
    plan = _plan("¿Qué capturas se hicieron el 12 de octubre de 2026 después de las 16:30?")
    assert plan.time_expression == "el 12 de octubre de 2026 · después de las 16:30"
    assert plan.search_text == "¿Qué capturas se hicieron ?"
    assert plan.timezone == "Europe/Madrid"
    assert plan.to_dict()["interval"]["start"].startswith("2026-10-12T14:30:00")


def test_without_time_there_is_no_interval_and_the_text_is_kept() -> None:
    plan = _plan("¿Cuándo se usó una tarjeta de crédito?")
    assert plan.interval is None and plan.time_expression is None
    assert plan.search_text == "¿Cuándo se usó una tarjeta de crédito?"
    assert plan.intent == "cuando" and plan.neighbors == 0


@pytest.mark.parametrize(
    "text, intent",
    [
        ("¿Qué pasó justo después de cortar la pieza?", "antes_despues"),
        ("¿Qué hacía antes de conectar el cable?", "antes_despues"),
        ("¿Cuándo se usó una tarjeta?", "cuando"),
        ("¿A qué hora empezó la reunión?", "cuando"),
        ("¿Quién pidió el informe?", "quien"),
        ("¿En qué captura hay una ventana?", "donde"),
        ("¿Dónde aparece la marca Mobil?", "donde"),
        ("¿Cuántas personas había?", "cuanto"),
        ("¿Qué dice el manual sobre la radial?", "documento"),
        ("¿Qué faltaba por citar?", "que"),
        ("chispas al cortar", "libre"),
        ("¿Qué pasó ayer después de las 16:30?", "que"),  # «después de las» es tiempo, no vecindad
        ("¿Quién iba a terminar antes de las 6?", "quien"),
    ],
)
def test_intent_detection(text, intent) -> None:
    plan = _plan(text)
    assert plan.intent == intent
    assert plan.neighbors == (2 if intent == "antes_despues" else 0)


def test_a_bare_time_without_a_day_is_content_not_a_filter() -> None:
    plan = _plan("¿Quién se comprometió a terminar la presentación antes de las 6?")
    assert plan.interval is None and plan.time_expression is None
    assert plan.search_text == plan.text  # la hora se sigue buscando como texto
    assert plan.intent == "quien"


def test_named_environment_or_source_becomes_a_filter() -> None:
    plan = _plan(
        "¿qué pasó en taller_01 ayer?",
        environments=[("taller_01", "Taller principal"), ("oficina", "Oficina")],
        sources=[("taller_01.cam", "cam")],
    )
    assert plan.environment_ids == ["taller_01"] and plan.source_ids == []
    assert plan.search_text == "¿qué pasó ?"
    by_name = _plan("la reunión de la Oficina", environments=[("oficina", "Oficina")])
    assert by_name.environment_ids == ["oficina"]


def test_quoted_phrases_are_listed() -> None:
    assert _plan('código "suela blanca"').phrases == ["suela blanca"]
