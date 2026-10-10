from datetime import datetime, timezone

from episodic.application.temporal_index import absolute_interval_filter, timeline_payload
from episodic.domain.times import Interval, TimeSpec


def _at(second: int) -> TimeSpec:
    return TimeSpec.from_utc(datetime(2026, 1, 1, 18, 0, second, tzinfo=timezone.utc))


def test_interval_payload_preserves_exact_timeline() -> None:
    payload = timeline_payload(interval=Interval(start=_at(1), end=_at(4)))

    assert payload["timeline_kind"] == "interval"
    assert payload["timeline_unknown"] is False
    assert payload["timeline_end_ms"] - payload["timeline_start_ms"] == 3000
    assert payload["timeline_filter_end_ms"] == payload["timeline_end_ms"]


def test_point_payload_keeps_instant_and_has_filter_width() -> None:
    instant = _at(2)
    payload = timeline_payload(instant=instant)

    assert payload["timeline_kind"] == "point"
    assert payload["timeline_start"] == payload["timeline_end"]
    assert payload["timeline_start_ms"] == payload["timeline_end_ms"]
    assert payload["timeline_filter_end_ms"] == payload["timeline_end_ms"] + 1


def test_unknown_time_is_not_converted_to_absolute_filter() -> None:
    unknown = Interval(start=TimeSpec.unknown(), end=TimeSpec.unknown())

    assert timeline_payload(interval=unknown) == {
        "timeline_unknown": True,
        "timeline_kind": "unknown",
    }
    assert absolute_interval_filter(unknown) == {}
