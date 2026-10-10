"""Validación y serialización del modelo canónico de Fase 2."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from episodic.domain.entities import Episode
from episodic.domain.model import DomainValidationError
from episodic.domain.processing import ProcessingWindow
from episodic.domain.provenance import EvidenceReference
from episodic.domain.types import EvidenceReferenceKind
from episodic.examples.pink_pipette import build_pink_pipette_fixture


def _all_required_entities(fixture):
    return [
        fixture.environments[0],
        fixture.sources[0],
        fixture.assets[0],
        fixture.processing_windows[0],
        fixture.observations[0],
        fixture.entities[0],
        fixture.events[0],
        fixture.microepisodes[0],
        fixture.episodes[0],
        fixture.relations[0],
        fixture.hypotheses[0],
        fixture.evidence_references[0],
        fixture.processing_runs[0],
    ]


def test_all_required_entities_round_trip_without_losing_type() -> None:
    fixture = build_pink_pipette_fixture()

    for entity in _all_required_entities(fixture):
        payload = entity.to_dict()
        restored = type(entity).from_dict(payload)

        assert payload["kind"] == type(entity).__name__
        assert restored == entity


def test_processing_window_is_not_episode_when_serialized() -> None:
    window = build_pink_pipette_fixture().processing_windows[0]
    payload = window.to_dict()

    assert not isinstance(window, Episode)
    assert payload["kind"] == "ProcessingWindow"
    assert "microepisode_ids" not in payload
    with pytest.raises(DomainValidationError):
        Episode.from_dict(payload)


def test_intervals_require_complete_timezone_aware_timestamps() -> None:
    fixture = build_pink_pipette_fixture()
    window = fixture.processing_windows[0]

    assert window.start_time.tzinfo is not None
    assert window.end_time.tzinfo is not None
    assert window.start_time <= window.end_time

    with pytest.raises(DomainValidationError, match="zona horaria"):
        ProcessingWindow(
            processing_run_id=window.processing_run_id,
            environment_id=window.environment_id,
            source_id=window.source_id,
            asset_id=window.asset_id,
            start_time=datetime(2026, 3, 18, 10, 0),
            end_time=datetime(2026, 3, 18, 10, 1),
            producer="test",
            model_version="1",
        )


@pytest.mark.parametrize(
    ("kind", "locator"),
    [
        (EvidenceReferenceKind.FILE, {}),
        (
            EvidenceReferenceKind.AUDIOVISUAL_INTERVAL,
            {"start_offset_seconds": 1.0, "end_offset_seconds": 2.5},
        ),
        (EvidenceReferenceKind.FRAME, {"frame_number": 12}),
        (
            EvidenceReferenceKind.REGION,
            {"region": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}},
        ),
        (EvidenceReferenceKind.PAGE, {"page_number": 3}),
        (
            EvidenceReferenceKind.TEXT_FRAGMENT,
            {"text_start": 4, "text_end": 17, "text_quote": "pipeta rosada"},
        ),
    ],
)
def test_evidence_reference_supports_every_required_locator(kind, locator) -> None:
    reference = EvidenceReference(
        asset_id="49722838-ce77-5bd3-962f-c46c0a2ce20a",
        reference_kind=kind,
        producer="test",
        model_version="1",
        **locator,
    )

    assert EvidenceReference.from_dict(reference.to_dict()) == reference


def test_fixture_identifiers_are_stable_across_builds() -> None:
    first = build_pink_pipette_fixture()
    second = build_pink_pipette_fixture()

    assert [item.id for item in first.environments] == [item.id for item in second.environments]
    assert [item.id for item in first.assets] == [item.id for item in second.assets]
    assert first.observations[0].id == second.observations[0].id
