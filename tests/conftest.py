"""Configuración común: la suite nunca llama a modelos reales por defecto."""
import os

# La visión real (Ollama) se evalúa con scripts explícitos, no en pytest.
os.environ.setdefault("EPIGRAPH_VISION_BACKEND", "fake")
