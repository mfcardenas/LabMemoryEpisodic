"""Configuración común: la suite nunca llama a modelos reales por defecto."""
import os

# La visión real (Ollama) se evalúa con scripts explícitos, no en pytest.
os.environ.setdefault("EPIGRAPH_VISION_BACKEND", "fake")
# La transcripción real (servicio HTTP) tampoco se llama desde la suite.
os.environ.setdefault("EPIGRAPH_SPEECH_BACKEND", "fake")
# El modelo de texto (resúmenes) y el agente son deterministas en las pruebas.
os.environ.setdefault("EPIGRAPH_TEXT_BACKEND", "fake")
os.environ.setdefault("EPIGRAPH_AGENT_BACKEND", "fake")
