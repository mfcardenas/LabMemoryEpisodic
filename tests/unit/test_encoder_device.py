"""Selección del dispositivo de los encoders: auto detecta CUDA, cpu/cuda fuerzan."""
from __future__ import annotations

import pytest

from episodic.adapters.models import fastembed as adapters
from episodic.config import EpisodicConfig


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    monkeypatch.setattr(adapters, "_resolved_device", None)
    monkeypatch.setattr(adapters, "_preload_cuda", lambda: None)


def test_explicit_devices_are_respected() -> None:
    assert adapters.resolve_device("cpu") == "cpu"
    assert adapters.resolve_device("CUDA") == "cuda"
    assert adapters._load_options("cpu") == {} and adapters._load_options("cuda") == {"cuda": True}


def test_auto_picks_cuda_only_when_onnxruntime_offers_it(monkeypatch) -> None:
    import onnxruntime

    monkeypatch.setattr(onnxruntime, "get_available_providers", lambda: ["CPUExecutionProvider"])
    assert adapters.resolve_device("auto") == "cpu"
    monkeypatch.setattr(adapters, "_resolved_device", None)
    monkeypatch.setattr(onnxruntime, "get_available_providers", lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"])
    assert adapters.resolve_device("auto") == "cuda"
    assert adapters.FastEmbedEpisodeTextEncoder(device="auto").info.runtime == "fastembed-onnx-cuda"


def test_config_validates_the_device(monkeypatch) -> None:
    monkeypatch.setenv("EPIGRAPH_ENCODER_DEVICE", "GPU")
    with pytest.raises(ValueError):
        EpisodicConfig.from_env()
    monkeypatch.setenv("EPIGRAPH_ENCODER_DEVICE", "Cuda")
    assert EpisodicConfig.from_env().encoder_device == "cuda"
