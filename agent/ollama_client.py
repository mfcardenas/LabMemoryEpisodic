from typing import Optional
import requests
from config import settings


class OllamaLLMClient:
    """Cliente ligero para interactuar con modelos de lenguaje servidos en Ollama."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
        timeout: int = 120,
    ):
        self.base_url = (base_url or settings.ollama_url).rstrip("/")
        self.model_name = model_name or settings.ollama_llm_model
        self.timeout = timeout

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Genera una respuesta a partir de un prompt y opcionalmente un system prompt."""
        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
        }
        if system_prompt:
            payload["system"] = system_prompt

        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            return data.get("response", "").strip()
        except Exception as exc:
            raise RuntimeError(
                f"Error al invocar modelo LLM ({self.model_name}): {exc}"
            ) from exc
