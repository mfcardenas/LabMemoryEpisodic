"""Test unitario de aislamiento de espacios de representación (Criterio #4 y #8)."""
import pytest
from episodic.domain.entities import Representation
from episodic.domain.types import RepresentationSpace
from episodic.ports.encoders import EncoderInfo
from episodic.adapters.models.fastembed import FastEmbedEpisodeTextEncoder


def test_encoder_space_isolation():
    # Espacios estrictamente separados
    nomic_space = RepresentationSpace.NOMIC_TEXT
    clip_vision_space = RepresentationSpace.CLIP_VISION
    clip_text_space = RepresentationSpace.CLIP_TEXT

    assert nomic_space != clip_vision_space
    assert nomic_space != clip_text_space

    # Metadatos de encoder inmutables y versionados
    info_nomic = EncoderInfo(
        model_id="nomic-ai/nomic-embed-text-v1.5",
        revision="v1.5",
        dimension=768,
        normalized=True,
    )
    info_clip = EncoderInfo(
        model_id="Qdrant/clip-ViT-B-32-vision",
        revision="v1",
        dimension=512,
        normalized=True,
    )

    assert info_nomic.dimension != info_clip.dimension
    assert info_nomic.model_id != info_clip.model_id


def test_nomic_adapter_reports_raw_vectors_as_not_normalized():
    encoder = FastEmbedEpisodeTextEncoder()

    assert encoder.info.dimension == 768
    assert encoder.info.normalized is False
