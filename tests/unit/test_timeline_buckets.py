"""Cubos de tiempo local de la línea temporal: cambios de horario y claves únicas."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from episodic.application.timeline import bucket_keys, choose_bucket, offset_segments

MADRID = ZoneInfo("Europe/Madrid")
UTC = timezone.utc


def test_offset_segments_split_exactly_at_the_dst_changes() -> None:
    autumn = offset_segments(datetime(2026, 10, 24, tzinfo=UTC), datetime(2026, 10, 26, tzinfo=UTC), MADRID)
    assert [(s.isoformat(), e.isoformat(), o) for s, e, o in autumn] == [
        ("2026-10-24T00:00:00+00:00", "2026-10-25T01:00:00+00:00", 120),
        ("2026-10-25T01:00:00+00:00", "2026-10-26T00:00:00+00:00", 60),
    ]
    spring = offset_segments(datetime(2026, 3, 1, tzinfo=UTC), datetime(2026, 4, 1, tzinfo=UTC), MADRID)
    assert [o for _, _, o in spring] == [60, 120] and spring[0][1] == datetime(2026, 3, 29, 1, tzinfo=UTC)
    whole_year = offset_segments(datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC), MADRID)
    assert [o for _, _, o in whole_year] == [60, 120, 60]
    assert offset_segments(datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 2, tzinfo=UTC), UTC) == [
        (datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 2, tzinfo=UTC), 0)
    ]


def test_bucket_keys_are_local_consecutive_and_unique() -> None:
    days = bucket_keys(datetime(2026, 10, 12, 21, tzinfo=UTC), datetime(2026, 10, 13, 23, tzinfo=UTC), "day", MADRID)
    assert [key for key, _, _ in days] == ["2026-10-12", "2026-10-13", "2026-10-14"]
    assert days[1][1].isoformat() == "2026-10-13T00:00:00+02:00"
    # Al retrasar el reloj, la hora local 02 se repite: un solo cubo de dos horas.
    hours = bucket_keys(datetime(2026, 10, 24, 23, tzinfo=UTC), datetime(2026, 10, 25, 3, tzinfo=UTC), "hour", MADRID)
    keys = [key for key, _, _ in hours]
    assert keys == ["2026-10-25T01", "2026-10-25T02", "2026-10-25T03"] and len(set(keys)) == len(keys)
    # (en UTC: restar dos horas locales con la misma zona ignora el cambio de horario)
    assert (hours[1][2].astimezone(UTC) - hours[1][1].astimezone(UTC)).total_seconds() == 7200
    assert hours[1][1].isoformat() == "2026-10-25T02:00:00+02:00" and hours[1][2].isoformat() == "2026-10-25T03:00:00+01:00"
    minutes = bucket_keys(datetime(2026, 10, 12, 14, 21, 15, tzinfo=UTC), datetime(2026, 10, 12, 14, 24, tzinfo=UTC), "minute", MADRID)
    assert [key for key, _, _ in minutes] == ["2026-10-12T16:21", "2026-10-12T16:22", "2026-10-12T16:23"]


def test_choose_bucket_by_span() -> None:
    t0 = datetime(2026, 10, 12, tzinfo=UTC)
    assert choose_bucket(t0, t0.replace(hour=5)) == "minute"
    assert choose_bucket(t0, datetime(2026, 10, 15, tzinfo=UTC)) == "hour"
    assert choose_bucket(t0, datetime(2026, 12, 1, tzinfo=UTC)) == "day"
