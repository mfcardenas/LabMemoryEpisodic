"""Segmentación determinista de tramos de vídeo a partir de firmas de fotograma."""
from __future__ import annotations

import pytest

from episodic.application.video_segmentation import (
    SceneSegmenter,
    SegmentationConfig,
    signature_distance,
)
from episodic.ports.video import SampledFrame

DARK = (10,) * 16
BRIGHT = (240,) * 16


def _frames(levels):
    return [
        SampledFrame(offset_seconds=float(second), frame_number=second * 30, jpeg=b"", signature=level)
        for second, level in enumerate(levels)
    ]


def test_scene_change_opens_a_new_segment_after_minimum_duration() -> None:
    segments = SceneSegmenter(SegmentationConfig(min_segment_seconds=2)).segment(
        _frames([DARK, DARK, DARK, BRIGHT, BRIGHT]), duration_seconds=5.0
    )

    assert [(item.start_offset_seconds, item.end_offset_seconds) for item in segments] == [
        (0.0, 3.0),
        (3.0, 5.0),
    ]
    assert [item.boundary_reason for item in segments] == ["start", "scene_change"]
    assert segments[1].scene_change_score == pytest.approx(230 / 255, abs=1e-3)


def test_change_before_minimum_duration_does_not_cut() -> None:
    segments = SceneSegmenter(SegmentationConfig(min_segment_seconds=2)).segment(
        _frames([DARK, BRIGHT, BRIGHT]), duration_seconds=3.0
    )

    assert len(segments) == 1
    assert segments[0].end_offset_seconds == 3.0


def test_static_scene_is_cut_at_maximum_duration_with_bounded_keyframes() -> None:
    segments = SceneSegmenter(
        SegmentationConfig(min_segment_seconds=2, max_segment_seconds=4, max_keyframes=3)
    ).segment(_frames([DARK] * 10), duration_seconds=10.0)

    assert [item.start_offset_seconds for item in segments] == [0.0, 4.0, 8.0]
    assert segments[-1].end_offset_seconds == 10.0
    assert all(item.boundary_reason == "max_duration" for item in segments[1:])
    assert [frame.offset_seconds for frame in segments[0].keyframes] == [0.0, 2.0, 3.0]


def test_signature_distance_is_normalized() -> None:
    assert signature_distance(DARK, DARK) == 0
    assert signature_distance((0,) * 4, (255,) * 4) == 1
    with pytest.raises(ValueError):
        signature_distance((1, 2), (1,))
