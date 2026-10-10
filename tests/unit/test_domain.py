"""Tests unitarios para el modelo de dominio puro (sin dependencias externas)."""
from datetime import datetime, timezone
import pytest

from episodic.domain.entities import (
    Annotation,
    CaptureSession,
    Entity,
    Episode,
    Evidence,
    Observation,
    Source,
)
from episodic.domain.times import Interval, TimeSpec, utc_now
from episodic.domain.types import (
    EvidenceMediaType,
    EvidenceState,
    ProvenanceKind,
    RepresentationSpace,
    SourceType,
    Verdict,
)


def test_timespec_utc_enforcement():
    naive_dt = datetime(2026, 1, 1, 18, 0, 0)
    spec = TimeSpec.from_utc(naive_dt)
    assert spec.absolute is not None
    assert spec.absolute.tzinfo == timezone.utc


def test_timespec_unknown_explicit():
    unknown = TimeSpec.unknown()
    assert unknown.is_unknown is True
    assert unknown.absolute is None

    spec_known = TimeSpec.from_utc(utc_now())
    assert unknown.is_comparable_with(spec_known) is False


def test_timespec_comparability():
    dt1 = datetime(2026, 1, 1, 18, 0, 0, tzinfo=timezone.utc)
    dt2 = datetime(2026, 1, 1, 18, 5, 0, tzinfo=timezone.utc)
    t1 = TimeSpec.from_utc(dt1)
    t2 = TimeSpec.from_utc(dt2)
    assert t1.is_comparable_with(t2) is True

    rel1 = TimeSpec.from_relative(10.0, clock_source="cam_1")
    rel2 = TimeSpec.from_relative(20.0, clock_source="cam_1")
    rel_other = TimeSpec.from_relative(20.0, clock_source="cam_2")
    assert rel1.is_comparable_with(rel2) is True
    assert rel1.is_comparable_with(rel_other) is False


def test_interval_semantics_and_overlap():
    dt_1800 = datetime(2026, 1, 1, 18, 0, 0, tzinfo=timezone.utc)
    dt_1810 = datetime(2026, 1, 1, 18, 10, 0, tzinfo=timezone.utc)
    dt_1805 = datetime(2026, 1, 1, 18, 5, 0, tzinfo=timezone.utc)
    dt_1815 = datetime(2026, 1, 1, 18, 15, 0, tzinfo=timezone.utc)
    dt_1820 = datetime(2026, 1, 1, 18, 20, 0, tzinfo=timezone.utc)

    inv1 = Interval(start=TimeSpec.from_utc(dt_1800), end=TimeSpec.from_utc(dt_1810))
    inv2 = Interval(start=TimeSpec.from_utc(dt_1805), end=TimeSpec.from_utc(dt_1820))
    inv3 = Interval(start=TimeSpec.from_utc(dt_1815), end=TimeSpec.from_utc(dt_1820))

    assert inv1.overlaps(inv2) is True
    assert inv1.overlaps(inv3) is False
    assert inv1.contains_instant(TimeSpec.from_utc(dt_1805)) is True
    assert inv1.contains_instant(TimeSpec.from_utc(dt_1815)) is False


def test_entities_separation_and_provenance():
    # Dos entidades con la misma etiqueta tienen identidades distintas
    e1 = Entity(label="Portátil rojo", entity_type="object", created_by="admin")
    e2 = Entity(label="Portátil rojo", entity_type="object", created_by="admin")
    assert e1.id != e2.id
    assert e1.label == e2.label

    # Observaciones conservan procedencia
    obs_human = Observation(content="Declaración del guardia", provenance_kind=ProvenanceKind.HUMAN_DECLARATION)
    obs_model = Observation(content="Detección de objeto YOLO", provenance_kind=ProvenanceKind.VISUAL_DETECTION)
    assert obs_human.provenance_kind != obs_model.provenance_kind
