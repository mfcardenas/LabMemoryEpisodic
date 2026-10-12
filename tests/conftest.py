"""Configuración común: la suite nunca llama a modelos reales por defecto."""
import os

# La visión real (Ollama) se evalúa con scripts explícitos, no en pytest.
os.environ.setdefault("EPIGRAPH_VISION_BACKEND", "fake")
# La transcripción real (servicio HTTP) tampoco se llama desde la suite.
os.environ.setdefault("EPIGRAPH_SPEECH_BACKEND", "fake")
