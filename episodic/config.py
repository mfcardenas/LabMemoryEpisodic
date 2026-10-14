"""Configuración central de EpiGraph para la ejecución local en PC.

Los valores pueden declararse mediante variables ``EPIGRAPH_*`` o pasarse de
forma explícita desde la CLI y los tests. Las rutas relativas se resuelven
siempre contra la raíz del proyecto, no contra el directorio de trabajo del
proceso.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PREFIX = "EPIGRAPH_"
_T = TypeVar("_T")


def load_env_file(path: str | Path | None = None) -> list[str]:
    """Carga las variables ``EPIGRAPH_*`` de un fichero ``.env``.

    Las variables ya definidas en el entorno tienen prioridad. Sin ``path`` se
    usa ``EPIGRAPH_ENV_FILE`` o, en su defecto, ``.env`` en la raíz del
    proyecto. Devuelve los nombres cargados.
    """
    if path is None:
        path = os.environ.get(f"{ENV_PREFIX}ENV_FILE") or PROJECT_ROOT / ".env"
    env_path = Path(path)
    if not env_path.is_file():
        return []
    loaded: list[str] = []
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = (part.strip() for part in line.split("=", 1))
        if name.startswith("export "):
            name = name[len("export "):].strip()
        if not name.startswith(ENV_PREFIX) or name in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        os.environ[name] = value
        loaded.append(name)
    return loaded


def _bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "si", "sí", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"Valor booleano no válido: {value!r}")


def _env(name: str, default: _T, cast: Callable[[str], _T] | None = None) -> _T:
    value = os.environ.get(f"{ENV_PREFIX}{name}")
    if value is None:
        return default
    return cast(value) if cast else value  # type: ignore[return-value]


@dataclass
class EpisodicConfig:
    """Configuración única compartida por CLI, API, frontend y scripts."""

    project_root: Path = field(default_factory=lambda: PROJECT_ROOT)

    # Persistencia local. Una ruta relativa queda anclada a project_root.
    data_dir: str | Path = field(default_factory=lambda: _env("DATA_DIR", "episodic-data"))

    # Backend vectorial existente; esta fase no añade ni cambia backends.
    vector_backend: str = field(default_factory=lambda: _env("VECTOR_BACKEND", "http"))
    qdrant_host: str = field(default_factory=lambda: _env("QDRANT_HOST", "127.0.0.1"))
    qdrant_port: int = field(default_factory=lambda: _env("QDRANT_PORT", 6333, int))
    qdrant_collection: str = field(
        default_factory=lambda: _env("QDRANT_COLLECTION", "episodic_memory")
    )

    # Encoders existentes.
    fastembed_cache_dir: str | Path = field(
        default_factory=lambda: _env(
            "MODEL_CACHE_DIR",
            os.environ.get("FASTEMBED_CACHE_PATH")
            or str(Path.home() / ".cache" / "fastembed"),
        )
    )
    nomic_model: str = field(
        default_factory=lambda: _env("NOMIC_MODEL", "nomic-ai/nomic-embed-text-v1.5")
    )
    nomic_dim: int = field(default_factory=lambda: _env("NOMIC_DIM", 768, int))
    # Prefijos de tarea con los que se entrenó Nomic v1.5 (vacíos para otros modelos).
    nomic_document_prefix: str = field(
        default_factory=lambda: _env("NOMIC_DOCUMENT_PREFIX", "search_document: ")
    )
    nomic_query_prefix: str = field(
        default_factory=lambda: _env("NOMIC_QUERY_PREFIX", "search_query: ")
    )
    clip_vision_model: str = field(
        default_factory=lambda: _env("CLIP_VISION_MODEL", "Qdrant/clip-ViT-B-32-vision")
    )
    clip_text_model: str = field(
        default_factory=lambda: _env("CLIP_TEXT_MODEL", "Qdrant/clip-ViT-B-32-text")
    )
    clip_dim: int = field(default_factory=lambda: _env("CLIP_DIM", 512, int))
    encoder_threads: int = field(default_factory=lambda: _env("ENCODER_THREADS", 2, int))
    encoder_batch_size: int = field(default_factory=lambda: _env("ENCODER_BATCH_SIZE", 1, int))

    # Servidor HTTP local.
    host: str = field(default_factory=lambda: _env("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env("PORT", 8100, int))

    # Logging centralizado. LOG_FILE vacío mantiene solo la salida de consola.
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))
    log_file: str | Path = field(default_factory=lambda: _env("LOG_FILE", ""))

    # Umbrales actuales; no se alteran en esta fase.
    default_text_threshold: float = field(
        default_factory=lambda: _env("TEXT_THRESHOLD", 0.65, float)
    )
    default_image_threshold: float = field(
        default_factory=lambda: _env("IMAGE_THRESHOLD", 0.22, float)
    )
    rrf_k: int = field(default_factory=lambda: _env("RRF_K", 60, int))

    # Búsqueda por pasajes: candidatos de cada vía (significado y texto
    # completo), cuántos pasa al reordenador y cuánto se devuelve.
    passage_candidates: int = field(default_factory=lambda: _env("PASSAGE_CANDIDATES", 60, int))
    passage_max_episodes: int = field(default_factory=lambda: _env("PASSAGE_MAX_EPISODES", 10, int))
    passage_per_episode: int = field(default_factory=lambda: _env("PASSAGE_PER_EPISODE", 5, int))
    # Cross-encoder multilingüe de FastEmbed; vacío desactiva el reordenado.
    rerank_model: str = field(
        default_factory=lambda: _env("RERANK_MODEL", "jinaai/jina-reranker-v2-base-multilingual")
    )
    rerank_candidates: int = field(default_factory=lambda: _env("RERANK_CANDIDATES", 20, int))
    # Hilos de CPU del reordenador (0: todos) y caracteres de cada pasaje que
    # ve: el coste crece con ambos; el título y el resumen van primero.
    rerank_threads: int = field(default_factory=lambda: _env("RERANK_THREADS", 0, int))
    rerank_max_chars: int = field(default_factory=lambda: _env("RERANK_MAX_CHARS", 800, int))
    # Pasajes reordenados por debajo de esta puntuación se descartan (vacío: ninguno)…
    rerank_min_score: Optional[float] = field(
        default_factory=lambda: _env("RERANK_MIN_SCORE", -1.5, lambda value: float(value) if value.strip() else None)
    )
    # …salvo que significado y palabras lo pongan ambos entre sus N primeros
    # (0: sin esta excepción). Las preguntas puntúan bajo aunque haya respuesta.
    passage_agreement_rank: int = field(default_factory=lambda: _env("PASSAGE_AGREEMENT_RANK", 3, int))
    # Zona horaria con la que se entienden «ayer», «el lunes por la tarde» o
    # «después de las 16:30» en las preguntas (IANA, p. ej. Europe/Madrid);
    # vacío: la del sistema.
    query_timezone: str = field(default_factory=lambda: _env("QUERY_TIMEZONE", ""))

    # Indexación outbox existente.
    outbox_batch_size: int = field(default_factory=lambda: _env("OUTBOX_BATCH_SIZE", 50, int))
    outbox_retry_limit: int = field(default_factory=lambda: _env("OUTBOX_RETRY_LIMIT", 3, int))

    # Percepción de vídeo (Fase C). ``vision_backend``: ollama | fake | none.
    vision_backend: str = field(default_factory=lambda: _env("VISION_BACKEND", "ollama"))
    vision_model: str = field(default_factory=lambda: _env("VISION_MODEL", "qwen3.5:4b"))
    ollama_url: str = field(
        default_factory=lambda: _env("OLLAMA_URL", "http://127.0.0.1:11434")
    )
    vision_timeout_seconds: float = field(
        default_factory=lambda: _env("VISION_TIMEOUT_SECONDS", 300.0, float)
    )
    vision_temperature: float = field(
        default_factory=lambda: _env("VISION_TEMPERATURE", 0.2, float)
    )
    # Razonamiento previo del modelo (``think`` de Ollama): más lento y no
    # mejora la descripción visual con los modelos probados.
    vision_think: bool = field(default_factory=lambda: _env("VISION_THINK", False, _bool))
    video_sample_fps: float = field(default_factory=lambda: _env("VIDEO_SAMPLE_FPS", 1.0, float))
    video_min_segment_seconds: float = field(
        default_factory=lambda: _env("VIDEO_MIN_SEGMENT_SECONDS", 2.0, float)
    )
    video_max_segment_seconds: float = field(
        default_factory=lambda: _env("VIDEO_MAX_SEGMENT_SECONDS", 15.0, float)
    )
    video_scene_threshold: float = field(
        default_factory=lambda: _env("VIDEO_SCENE_THRESHOLD", 0.12, float)
    )
    video_max_keyframes: int = field(
        default_factory=lambda: _env("VIDEO_MAX_KEYFRAMES", 3, int)
    )
    video_frame_max_side: int = field(
        default_factory=lambda: _env("VIDEO_FRAME_MAX_SIDE", 768, int)
    )
    video_jpeg_quality: int = field(
        default_factory=lambda: _env("VIDEO_JPEG_QUALITY", 85, int)
    )
    description_language: str = field(
        default_factory=lambda: _env("DESCRIPTION_LANGUAGE", "es")
    )

    # Modelo de texto (resúmenes jerárquicos, Fase H). ``text_backend``:
    # ollama | fake | none. Nota: la etiqueta ``gpt-oss:20b`` descargada hace
    # meses falla en Ollama ≥ 0.35 («size overflows»); ``gpt-oss:latest`` sí
    # funciona (o vuelva a descargar la etiqueta).
    text_backend: str = field(default_factory=lambda: _env("TEXT_BACKEND", "ollama"))
    text_model: str = field(default_factory=lambda: _env("TEXT_MODEL", "gpt-oss:latest"))
    text_timeout_seconds: float = field(default_factory=lambda: _env("TEXT_TIMEOUT_SECONDS", 600.0, float))
    text_temperature: float = field(default_factory=lambda: _env("TEXT_TEMPERATURE", 0.2, float))
    text_num_ctx: int = field(default_factory=lambda: _env("TEXT_NUM_CTX", 16384, int))
    # Esfuerzo de razonamiento de gpt-oss (low | medium | high); otros modelos lo ignoran.
    text_reasoning: str = field(default_factory=lambda: _env("TEXT_REASONING", "low"))
    # Caracteres de entrada que ve el modelo por resumen (eventos + habla + documentos).
    text_max_input_chars: int = field(default_factory=lambda: _env("TEXT_MAX_INPUT_CHARS", 16000, int))
    # Días que entran en el resumen de un entorno.
    digest_environment_days: int = field(default_factory=lambda: _env("DIGEST_ENVIRONMENT_DAYS", 30, int))

    # Agente de respuestas (Fase A2). ``agent_backend``: ollama | fake | none.
    agent_backend: str = field(default_factory=lambda: _env("AGENT_BACKEND", "ollama"))
    # Vacío: el mismo modelo que los resúmenes (EPIGRAPH_TEXT_MODEL).
    agent_model: str = field(default_factory=lambda: _env("AGENT_MODEL", ""))
    agent_reasoning: str = field(default_factory=lambda: _env("AGENT_REASONING", "low"))
    agent_temperature: float = field(default_factory=lambda: _env("AGENT_TEMPERATURE", 0.1, float))
    agent_num_ctx: int = field(default_factory=lambda: _env("AGENT_NUM_CTX", 32768, int))
    agent_timeout_seconds: float = field(default_factory=lambda: _env("AGENT_TIMEOUT_SECONDS", 600.0, float))
    # Presupuesto por pregunta: vueltas del modelo, llamadas a herramientas y segundos.
    agent_max_steps: int = field(default_factory=lambda: _env("AGENT_MAX_STEPS", 8, int))
    agent_max_tool_calls: int = field(default_factory=lambda: _env("AGENT_MAX_TOOL_CALLS", 12, int))
    agent_max_seconds: float = field(default_factory=lambda: _env("AGENT_MAX_SECONDS", 120.0, float))
    # Caracteres de cada resultado de herramienta que ve el modelo.
    # 12 000 ≈ 3 500 tokens: cabe un resultado entero de search_moments
    # (hasta 12 momentos de 900 caracteres) sin recortar el habla ni las
    # tablas de los documentos, donde suele estar la respuesta.
    agent_tool_result_chars: int = field(default_factory=lambda: _env("AGENT_TOOL_RESULT_CHARS", 12000, int))

    # Transcripción del habla (Fase D). ``speech_backend``: http | fake | none.
    # ``http`` llama a un servicio compatible con la API de OpenAI
    # (``/v1/audio/transcriptions``), p. ej. Speaches en Docker.
    speech_backend: str = field(default_factory=lambda: _env("SPEECH_BACKEND", "http"))
    speech_url: str = field(
        default_factory=lambda: _env("SPEECH_URL", "http://127.0.0.1:8010")
    )
    speech_model: str = field(
        default_factory=lambda: _env(
            "SPEECH_MODEL", "deepdml/faster-whisper-large-v3-turbo-ct2"
        )
    )
    # Vacío: el servicio detecta el idioma.
    speech_language: str = field(default_factory=lambda: _env("SPEECH_LANGUAGE", "es"))
    speech_timeout_seconds: float = field(
        default_factory=lambda: _env("SPEECH_TIMEOUT_SECONDS", 600.0, float)
    )
    speech_api_key: str = field(default_factory=lambda: _env("SPEECH_API_KEY", ""))
    # Vocabulario o contexto que orienta al modelo (nombres de piezas, siglas…).
    speech_prompt: str = field(default_factory=lambda: _env("SPEECH_PROMPT", ""))
    speech_temperature: float = field(
        default_factory=lambda: _env("SPEECH_TEMPERATURE", 0.0, float)
    )
    speech_sample_rate_hz: int = field(
        default_factory=lambda: _env("SPEECH_SAMPLE_RATE_HZ", 16000, int)
    )

    # Entrada automática: fichero TOML con las carpetas vigiladas. Vacío: sin
    # vigilancia. ``epigraph serve`` la arranca si está definido.
    watch_config: str | Path = field(default_factory=lambda: _env("WATCH_CONFIG", ""))
    # Líneas que se conservan del registro de ejecución (0: sin límite).
    activity_log_max_rows: int = field(
        default_factory=lambda: _env("ACTIVITY_LOG_MAX_ROWS", 5000, int)
    )
    # Pases del vigilante que se conservan, aparte de las líneas anteriores
    # (0: sin límite). 1000 son ~1,5 h con un barrido cada 5 s.
    activity_log_max_passes: int = field(
        default_factory=lambda: _env("ACTIVITY_LOG_MAX_PASSES", 1000, int)
    )

    # Directorio permitido para importaciones; su aplicación estricta pertenece
    # a la fase del modelo de procedencia, no a esta fase de infraestructura.
    import_root: str | Path = field(default_factory=lambda: _env("IMPORT_ROOT", ""))

    def __post_init__(self) -> None:
        self.project_root = Path(self.project_root).expanduser().resolve()
        self.log_level = self.log_level.upper()
        if self.vector_backend not in {"edge", "http"}:
            raise ValueError(f"Backend vectorial no soportado: {self.vector_backend}")
        if self.vision_backend not in {"ollama", "fake", "none"}:
            raise ValueError(f"Backend de visión no soportado: {self.vision_backend}")
        if self.speech_backend not in {"http", "fake", "none"}:
            raise ValueError(f"Backend de habla no soportado: {self.speech_backend}")
        if self.text_backend not in {"ollama", "fake", "none"}:
            raise ValueError(f"Backend de texto no soportado: {self.text_backend}")
        if self.agent_backend not in {"ollama", "fake", "none"}:
            raise ValueError(f"Backend del agente no soportado: {self.agent_backend}")

    @classmethod
    def from_env(cls, **overrides: Any) -> "EpisodicConfig":
        """Carga variables ``EPIGRAPH_*`` y aplica overrides explícitos."""
        return cls(**{key: value for key, value in overrides.items() if value is not None})

    def resolve_path(self, value: str | Path) -> Path:
        """Resuelve una ruta configurable de forma estable y sin hardcodes."""
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.project_root / path
        return path.resolve()

    @property
    def data_path(self) -> Path:
        return self.resolve_path(self.data_dir)

    @property
    def sqlite_path(self) -> Path:
        return self.data_path / "episodic.db"

    @property
    def media_dir(self) -> Path:
        return self.data_path / "media"

    @property
    def index_dir(self) -> Path:
        return self.data_path / "index"

    @property
    def qdrant_edge_dir(self) -> Path:
        return self.index_dir / "qdrant-edge"

    @property
    def model_cache_path(self) -> Path:
        return self.resolve_path(self.fastembed_cache_dir)

    @property
    def log_file_path(self) -> Path | None:
        return self.resolve_path(self.log_file) if self.log_file else None

    @property
    def watch_config_path(self) -> Path | None:
        return self.resolve_path(self.watch_config) if self.watch_config else None

    @property
    def default_manifest_path(self) -> Path:
        return self.project_root / "data" / "atlas" / "manifest.json"

    def ensure_dirs(self) -> None:
        """Crea exclusivamente los directorios de runtime configurados."""
        for directory in (self.data_path, self.media_dir, self.index_dir):
            directory.mkdir(parents=True, exist_ok=True)
