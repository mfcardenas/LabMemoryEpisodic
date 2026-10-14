"""Búsqueda por pasajes: el momento exacto que responde, no solo el episodio.

Dos vías independientes sobre los mismos pasajes:

- **Significado**: Nomic en Qdrant (sinónimos, paráfrasis, otra forma de
  decirlo).
- **Texto completo**: BM25 en SQLite FTS5 (nombres de pieza, códigos,
  herramientas concretas: lo que un experto escribe tal cual).

Se fusionan por posición con RRF (sus puntuaciones no son comparables), un
cross-encoder reordena los mejores, y SQLite hidrata cada pasaje con su
episodio, su hora y su origen. Todo es determinista y sin LLM.
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from typing import Optional

from episodic.application.temporal_index import absolute_interval_filter
from episodic.domain.passages import (
    PassageGroup,
    PassageHit,
    PassageScores,
    PassageSearchRun,
    order_score,
    passage_span,
    passage_text,
)
from episodic.domain.retrieval import HybridSearchFilters
from episodic.domain.types import RepresentationSpace
from episodic.ports.encoders import EpisodeTextEncoder, PassageReranker
from episodic.ports.repository import EpisodicRepository
from episodic.ports.vector_index import VectorIndex

logger = logging.getLogger(__name__)

# Marca «usar el valor configurado» (None ya significa «sin umbral»).
DEFAULT = object()

# Palabras que no aportan a la búsqueda por texto completo.
_STOPWORDS = frozenset(
    """a al algo ante con contra cual cuales cuando de del desde donde e el ella ellos en
    entre era es esa ese eso esta este esto fue han hay la las le les lo los mas me mi muy
    no o para pero por que quien se sin sobre su sus tambien te tiene un una uno unos unas
    y ya como cuanto cuantos cuantas cual cuales hubo ser son estan esta estaba""".split()
)

_PHRASE = re.compile(r'"([^"]+)"')
_WORD = re.compile(r"\w+", re.UNICODE)


def _fold(text: str) -> str:
    """Minúsculas y sin tildes, como el tokenizador del índice."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _stem(term: str) -> str:
    """Raíz aproximada para casar plural y género con un prefijo
    (chispas → chispa*, agarrotada → agarrotad*)."""
    if len(term) > 5 and term.endswith("es"):
        term = term[:-2]
    elif len(term) > 4 and term.endswith("s"):
        term = term[:-1]
    if len(term) > 5 and term[-1] in "ao":
        term = term[:-1]
    return term


def lexical_query(text: str) -> str:
    """Expresión FTS5 a partir de la pregunta: frases entre comillas tal cual
    y, para el resto, cada término significativo como prefijo, unidos con OR."""
    clauses: list[str] = []
    for phrase in _PHRASE.findall(text):
        words = _WORD.findall(_fold(phrase))
        if words:
            clauses.append('"' + " ".join(words) + '"')
    rest = _PHRASE.sub(" ", text)
    seen: set[str] = set()
    for word in _WORD.findall(_fold(rest)):
        if word in _STOPWORDS or (len(word) < 3 and not word.isdigit()):
            continue
        term = _stem(word)
        if term in seen:
            continue
        seen.add(term)
        clauses.append(f'"{term}"*' if len(term) >= 4 else f'"{term}"')
    return " OR ".join(clauses)


class PassageSearchService:
    def __init__(
        self,
        repository: EpisodicRepository,
        vector_index: VectorIndex,
        text_encoder: EpisodeTextEncoder,
        reranker: Optional[PassageReranker] = None,
        *,
        candidates: int = 60,
        rerank_candidates: int = 20,
        rerank_max_chars: int = 800,
        rerank_min_score: Optional[float] = None,
        agreement_rank: int = 0,
        max_episodes: int = 10,
        per_episode: int = 5,
        rrf_k: int = 60,
    ) -> None:
        self.repository = repository
        self.vector_index = vector_index
        self.text_encoder = text_encoder
        self.reranker = reranker
        self.candidates = candidates
        self.rerank_candidates = rerank_candidates
        self.rerank_max_chars = rerank_max_chars
        self.rerank_min_score = rerank_min_score
        # La nota del reordenador no basta para descartar: una pregunta
        # («¿cuándo se usó…?») puntúa bajo aunque el pasaje la responda. Si
        # las dos vías lo ponen entre sus `agreement_rank` primeros, se queda.
        self.agreement_rank = agreement_rank
        self.max_episodes = max_episodes
        self.per_episode = per_episode
        self.rrf_k = rrf_k

    def settings(self) -> dict:
        """Parámetros vigentes, para mostrarlos y poder cambiarlos por consulta."""
        return {
            "candidates": self.candidates,
            "rerank_model": self.reranker.model_id if self.reranker is not None else None,
            "rerank_candidates": self.rerank_candidates,
            "rerank_max_chars": self.rerank_max_chars,
            "rerank_min_score": self.rerank_min_score,
            "agreement_rank": self.agreement_rank,
            "max_episodes": self.max_episodes,
            "per_episode": self.per_episode,
            "rrf_k": self.rrf_k,
            "indexed_passages": self.repository.count_passages(),
        }

    def warm_up(self) -> None:
        """Carga los modelos antes de la primera consulta (la carga tarda segundos)."""
        self.text_encoder.embed_query("calentamiento")
        if self.reranker is not None:
            self.reranker.rerank("calentamiento", ["calentamiento"])

    def search(
        self,
        query: str,
        filters: HybridSearchFilters | None = None,
        *,
        max_episodes: Optional[int] = None,
        per_episode: Optional[int] = None,
        rerank: bool = True,
        min_score: Optional[float] | object = DEFAULT,
        agreement_rank: Optional[int] = None,
    ) -> PassageSearchRun:
        """`min_score` y `agreement_rank` sustituyen a los configurados solo en
        esta consulta; `min_score=None` quita el umbral y `agreement_rank=0`
        desactiva el acuerdo entre vías."""
        query = (query or "").strip()
        if not query:
            raise ValueError("La consulta está vacía")
        started = time.perf_counter()
        filters = filters or HybridSearchFilters()
        min_score = self.rerank_min_score if min_score is DEFAULT else min_score
        agreement_rank = self.agreement_rank if agreement_rank is None else agreement_rank
        run = PassageSearchRun(
            query=query,
            groups=[],
            lexical_query=lexical_query(query),
            indexed_passages=self.repository.count_passages(),
        )

        # 1. Significado (Qdrant, filtrado antes del top-k).
        dense: list[tuple[str, float]] = []
        vector_filters: dict = {"target_type": "passage"}
        for key in ("environment_ids", "source_ids", "modalities", "observation_types"):
            if getattr(filters, key):
                vector_filters[key] = list(getattr(filters, key))
        vector_filters.update(absolute_interval_filter(filters.interval))
        for candidate in self.vector_index.search(
            query_vector=self.text_encoder.embed_query(query),
            space=RepresentationSpace.NOMIC_TEXT,
            filters=vector_filters,
            limit=self.candidates,
        ):
            observation_id = candidate.payload.get("observation_id") or candidate.target_id
            dense.append((observation_id, candidate.score))
        run.dense_candidates = len(dense)

        # 2. Texto completo (BM25). Los filtros se aplican al hidratar.
        lexical: list[tuple[str, float, str]] = []
        if run.lexical_query:
            try:
                lexical = [
                    (obs_id, score, fragment)
                    for obs_id, _episode_id, score, fragment in self.repository.search_passages(
                        run.lexical_query, self.candidates
                    )
                ]
            except Exception as exc:  # una expresión rara no debe tumbar la búsqueda
                logger.warning("Búsqueda por texto completo fallida (%s): %s", run.lexical_query, exc)
        run.lexical_candidates = len(lexical)

        # 3. Fusión por posición (RRF).
        scores: dict[str, PassageScores] = {}
        snippets: dict[str, str] = {}
        for rank, (obs_id, score) in enumerate(dense, start=1):
            entry = scores.setdefault(obs_id, PassageScores())
            entry.dense, entry.dense_rank = score, rank
            entry.fused += 1.0 / (self.rrf_k + rank)
        for rank, (obs_id, score, fragment) in enumerate(lexical, start=1):
            entry = scores.setdefault(obs_id, PassageScores())
            entry.lexical, entry.lexical_rank = score, rank
            entry.fused += 1.0 / (self.rrf_k + rank)
            snippets[obs_id] = fragment
        run.fused_candidates = len(scores)

        # 4. Hidratar desde SQLite (la verdad) y aplicar filtros.
        hits: list[PassageHit] = []
        episodes: dict[str, object] = {}
        for obs_id, entry in sorted(scores.items(), key=lambda item: item[1].fused, reverse=True):
            observation = self.repository.get_observation(obs_id)
            if observation is None or not observation.episode_id:
                continue
            if observation.episode_id not in episodes:
                episodes[observation.episode_id] = self.repository.get_episode(observation.episode_id)
            episode = episodes[observation.episode_id]
            if episode is None or not episode.is_visible:
                continue
            start, end = passage_span(observation, episode)
            if not _matches(observation, episode, filters, start, end):
                continue
            text = passage_text(observation)
            if not text:
                continue
            hits.append(PassageHit(observation, text, entry, snippets.get(obs_id, ""), start, end))

        # 5. Reordenar los mejores con el cross-encoder. Con reordenado solo
        # quedan los que puntuó: los que llegan al umbral o en los que ambas
        # vías coinciden. Los descartados se devuelven aparte, a la vista.
        discarded: list[PassageHit] = []
        if rerank and self.reranker is not None and hits:
            run.rerank_model = self.reranker.model_id
            head = hits[: self.rerank_candidates]
            try:
                texts = [hit.text[: self.rerank_max_chars] for hit in head]
                for hit, value in zip(head, self.reranker.rerank(query, texts)):
                    hit.scores.rerank = value
                run.rerank_applied = True
                run.rerank_min_score = min_score
                run.agreement_rank = agreement_rank
                kept = []
                for hit in head:
                    hit.kept_by = _kept_by(hit.scores, min_score, agreement_rank)
                    (kept if hit.kept_by else discarded).append(hit)
                run.discarded_low_relevance = len(discarded)
                run.not_reranked = len(hits) - len(head)
                hits = sorted(kept, key=order_score, reverse=True)
                discarded.sort(key=order_score, reverse=True)
            except Exception as exc:
                logger.warning("Reordenador %s no disponible: %s", run.rerank_model, exc)
                run.rerank_error = str(exc)

        # 6. Agrupar por episodio, en el orden de su mejor pasaje.
        max_episodes = max_episodes or self.max_episodes
        per_episode = per_episode or self.per_episode
        run.groups = _group(hits, episodes, max_episodes, per_episode)
        run.discarded_groups = _group(discarded, episodes, max_episodes, per_episode)
        run.took_ms = (time.perf_counter() - started) * 1000
        return run


def _kept_by(scores: PassageScores, min_score: Optional[float], agreement_rank: int) -> Optional[str]:
    """Por qué se queda un pasaje reordenado, o None si se descarta."""
    if min_score is None or scores.rerank >= min_score:
        return "threshold"
    if (
        agreement_rank
        and scores.dense_rank is not None
        and scores.lexical_rank is not None
        and scores.dense_rank <= agreement_rank
        and scores.lexical_rank <= agreement_rank
    ):
        return "agreement"
    return None


def _group(hits: list[PassageHit], episodes: dict, max_episodes: int, per_episode: int) -> list[PassageGroup]:
    groups: dict[str, PassageGroup] = {}
    for hit in hits:
        group = groups.get(hit.observation.episode_id)
        if group is None:
            if len(groups) >= max_episodes:
                continue
            group = groups[hit.observation.episode_id] = PassageGroup(episodes[hit.observation.episode_id])
        if len(group.passages) < per_episode:
            group.passages.append(hit)
    return list(groups.values())


def _matches(observation, episode, filters: HybridSearchFilters, start, end) -> bool:
    environments = {observation.environment_id} if observation.environment_id else set(episode.environment_ids)
    if filters.environment_ids and not environments & set(filters.environment_ids):
        return False
    if filters.source_ids and observation.source_id not in filters.source_ids:
        return False
    if filters.modalities and observation.modality not in filters.modalities:
        return False
    if filters.observation_types and observation.observation_type not in filters.observation_types:
        return False
    interval = filters.interval
    if interval and interval.start and interval.end and interval.start.absolute and interval.end.absolute:
        if start is None or not (start < interval.end.absolute and (end or start) >= interval.start.absolute):
            return False
    return True

