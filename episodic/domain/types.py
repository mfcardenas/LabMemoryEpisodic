"""Enums y tipos de resultado del dominio episódico.

Ningún import externo (Qdrant, FastEmbed, etc.) debe aparecer aquí.
"""
from __future__ import annotations

from enum import Enum, unique


@unique
class ProvenanceKind(str, Enum):
    """Origen de una observación o anotación."""
    HUMAN_DECLARATION = "human_declaration"
    VISUAL_DETECTION = "visual_detection"
    AUDIO_TRANSCRIPTION = "audio_transcription"
    OCR_EXTRACTION = "ocr_extraction"
    MODEL_DESCRIPTION = "model_description"
    SYSTEM_DERIVED = "system_derived"
    SCENARIO_FIXTURE = "scenario_fixture"


@unique
class Verdict(str, Enum):
    """Resultado de una consulta: nunca un booleano simple."""
    CANDIDATE = "candidate"
    SUPPORTED = "supported"
    AMBIGUOUS = "ambiguous"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NO_MATCH = "no_match"


@unique
class SearchIntent(str, Enum):
    """Intención de búsqueda con semántica propia."""
    SEMANTIC_SEARCH = "semantic_search"
    INTERVAL_SEARCH = "interval_search"
    LAST_OBSERVATION = "last_observation"
    TIMELINE = "timeline"


@unique
class EvidenceState(str, Enum):
    """Estado de una evidencia en su ciclo de vida."""
    ACTIVE = "active"
    PENDING_INDEX = "pending_index"
    PENDING_DELETE = "pending_delete"
    DELETED = "deleted"


@unique
class SourceType(str, Enum):
    """Tipo de fuente de captura."""
    CAMERA = "camera"
    MICROPHONE = "microphone"
    PHOTO_SET = "photo_set"
    DOCUMENT = "document"
    SENSOR = "sensor"
    MANUAL = "manual"


@unique
class EvidenceMediaType(str, Enum):
    """Tipo de medio de una evidencia."""
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    TEXT = "text"
    DOCUMENT = "document"


@unique
class EvidenceReferenceKind(str, Enum):
    """Localizador exacto dentro de un activo original."""
    FILE = "file"
    AUDIOVISUAL_INTERVAL = "audiovisual_interval"
    FRAME = "frame"
    REGION = "region"
    PAGE = "page"
    TEXT_FRAGMENT = "text_fragment"


@unique
class ValidationState(str, Enum):
    """Estado explícito de una relación verificable."""
    PROPOSED = "proposed"
    VALIDATED = "validated"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


@unique
class LogicalRelationType(str, Enum):
    """Vocabulario inicial del grafo lógico de EpiGraph."""

    OBSERVED_IN = "OBSERVED_IN"
    BEFORE = "BEFORE"
    AFTER = "AFTER"
    OVERLAPS = "OVERLAPS"
    LOCATED_IN = "LOCATED_IN"
    POSSIBLY_SAME_ENTITY_AS = "POSSIBLY_SAME_ENTITY_AS"
    SAME_ENTITY_AS = "SAME_ENTITY_AS"
    MOVED_TO = "MOVED_TO"
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    DERIVED_FROM = "DERIVED_FROM"
    REVIEWED_BY = "REVIEWED_BY"


@unique
class TransitionMode(str, Enum):
    """Procedencia de una transición espacial."""

    OBSERVED = "observed"
    INFERRED = "inferred"


@unique
class HypothesisState(str, Enum):
    """Ciclo de vida separado de una hipótesis."""
    OPEN = "open"
    SUPPORTED = "supported"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


@unique
class ProcessingRunState(str, Enum):
    """Estado de una ejecución de procesamiento determinista."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@unique
class IndexingState(str, Enum):
    """Estado de indexación de una representación vectorial."""
    PENDING = "pending"
    INDEXED = "indexed"
    FAILED = "failed"
    STALE = "stale"


@unique
class RepresentationSpace(str, Enum):
    """Espacio de embedding — nunca mezclar scores de espacios distintos."""
    NOMIC_TEXT = "nomic_text"
    CLIP_VISION = "clip_vision"
    CLIP_TEXT = "clip_text"
