"""Procesadores deterministas y adaptadores opcionales de observaciones."""

from episodic.processors.audio import AudioProcessor
from episodic.processors.document import DocumentProcessor
from episodic.processors.image import ImageProcessor
from episodic.processors.mock import MockProcessor
from episodic.processors.text import TextProcessor
from episodic.processors.video import VideoProcessor

__all__ = [
    "AudioProcessor",
    "DocumentProcessor",
    "ImageProcessor",
    "MockProcessor",
    "TextProcessor",
    "VideoProcessor",
]
