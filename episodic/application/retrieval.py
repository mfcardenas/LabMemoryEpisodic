"""Servicio de recuperación multimodal del sistema episódico (RetrievalService).

- Soporta 4 intenciones: SEMANTIC_SEARCH, INTERVAL_SEARCH, LAST_OBSERVATION, TIMELINE.
- Búsqueda multimodal mediante RRF (Nomic text + CLIP text -> image vector).
- Sistema de veredictos explícito (SUPPORTED, CANDIDATE, AMBIGUOUS, INSUFFICIENT_EVIDENCE, NO_MATCH).
- Agrupación por episodio para evitar sesgo por repetición de crops.
- Registro auditable de cada consulta en QueryRun.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from episodic.domain.entities import (
    Episode,
    Observation,
    QueryResult,
    QueryRun,
)
from episodic.domain.times import Interval, TimeSpec, utc_now
from episodic.domain.types import (
    RepresentationSpace,
    SearchIntent,
    Verdict,
)
from episodic.ports.encoders import (
    AlignedTextEncoder,
    EpisodeTextEncoder,
    ImageEncoder,
)
from episodic.ports.repository import EpisodicRepository
from episodic.ports.vector_index import VectorCandidate, VectorIndex
from episodic.application.temporal_index import absolute_interval_filter

logger = logging.getLogger(__name__)


class RetrievalService:
    """Servicio de aplicación para búsqueda y consulta episódica."""

    def __init__(
        self,
        repository: EpisodicRepository,
        vector_index: VectorIndex,
        text_encoder: EpisodeTextEncoder,
        aligned_text_encoder: Optional[AlignedTextEncoder] = None,
        image_encoder: Optional[ImageEncoder] = None,
        default_text_threshold: float = 0.35,
        default_image_threshold: float = 0.25,
        rrf_k: int = 60,
    ) -> None:
        self.repository = repository
        self.vector_index = vector_index
        self.text_encoder = text_encoder
        self.aligned_text_encoder = aligned_text_encoder
        self.image_encoder = image_encoder
        self.default_text_threshold = default_text_threshold
        self.default_image_threshold = default_image_threshold
        self.rrf_k = rrf_k

    def search(
        self,
        text_query: Optional[str] = None,
        image_query_path: Optional[str] = None,
        intent: SearchIntent = SearchIntent.SEMANTIC_SEARCH,
        source_filter: Optional[List[str]] = None,
        interval_filter: Optional[Interval] = None,
        entity_filter: Optional[str] = None,
        structured_vector_filters: Optional[Dict[str, Any]] = None,
        require_unique_resolution: bool = False,
        limit: int = 10,
    ) -> QueryRun:
        """Ejecuta una consulta según la intención especificada."""
        start_time = time.perf_counter()

        if intent == SearchIntent.LAST_OBSERVATION:
            run = self._handle_last_observation(
                entity_filter=entity_filter,
                source_filter=source_filter,
                limit=limit,
            )
        elif intent == SearchIntent.TIMELINE:
            run = self._handle_timeline(
                source_filter=source_filter,
                interval_filter=interval_filter,
                limit=limit,
            )
        elif intent == SearchIntent.INTERVAL_SEARCH:
            run = self._handle_interval_search(
                interval=interval_filter,
                text_query=text_query,
                source_filter=source_filter,
                limit=limit,
            )
        else:
            # SEMANTIC_SEARCH (por defecto)
            run = self._handle_semantic_search(
                text_query=text_query,
                image_query_path=image_query_path,
                source_filter=source_filter,
                interval_filter=interval_filter,
                structured_vector_filters=structured_vector_filters,
                require_unique_resolution=require_unique_resolution,
                limit=limit,
            )

        duration_ms = (time.perf_counter() - start_time) * 1000.0
        run.duration_ms = duration_ms
        run.config = {
            "text_threshold": self.default_text_threshold,
            "image_threshold": self.default_image_threshold,
            "rrf_k": self.rrf_k,
            "require_unique_resolution": require_unique_resolution,
        }

        # Guardar en repositorio para auditoría y trazabilidad
        try:
            self.repository.save_query_run(run)
        except Exception as e:
            logger.warning("No se pudo persistir el QueryRun de auditoría: %s", e)

        return run

    def _handle_semantic_search(
        self,
        text_query: Optional[str],
        image_query_path: Optional[str],
        source_filter: Optional[List[str]],
        interval_filter: Optional[Interval],
        structured_vector_filters: Optional[Dict[str, Any]],
        require_unique_resolution: bool,
        limit: int,
    ) -> QueryRun:
        text_vector: Optional[List[float]] = None
        clip_text_vector: Optional[List[float]] = None
        clip_image_vector: Optional[List[float]] = None

        filters: Dict[str, Any] = dict(structured_vector_filters or {})
        if source_filter:
            filters["source_ids"] = source_filter
        filters.update(absolute_interval_filter(interval_filter))

        # 1. Generar vectores según la consulta
        if text_query:
            text_vector = self.text_encoder.embed_query(text_query)
            if self.aligned_text_encoder:
                try:
                    clip_text_vector = self.aligned_text_encoder.embed_query(text_query)
                except Exception as e:
                    logger.warning("Error codificando consulta con CLIP text: %s", e)

        if image_query_path and self.image_encoder:
            try:
                clip_image_vector = self.image_encoder.embed_images([image_query_path])[0]
            except Exception as e:
                logger.warning("Error codificando imagen de consulta: %s", e)

        # 2. Búsqueda vectorial y fusión RRF
        if text_vector and (clip_text_vector or clip_image_vector):
            img_vec = clip_image_vector or clip_text_vector
            raw_candidates = self.vector_index.search_hybrid(
                text_vector=text_vector,
                image_vector=img_vec,
                filters=filters,
                limit=limit * 2,
                rrf_k=self.rrf_k,
            )
        elif text_vector:
            raw_candidates = self.vector_index.search(
                query_vector=text_vector,
                space=RepresentationSpace.NOMIC_TEXT,
                filters=filters,
                limit=limit * 2,
            )
        elif clip_image_vector:
            raw_candidates = self.vector_index.search(
                query_vector=clip_image_vector,
                space=RepresentationSpace.CLIP_VISION,
                filters=filters,
                limit=limit * 2,
            )
        else:
            return QueryRun(
                intent=SearchIntent.SEMANTIC_SEARCH,
                text_query=text_query,
                overall_verdict=Verdict.NO_MATCH,
                reasons=["Consulta vacía: proporcione texto o imagen para buscar"],
            )

        # 3. Consolidar por episodio para evitar sesgo de crops duplicados
        episode_matches: Dict[str, Dict[str, Any]] = {}
        for c in raw_candidates:
            ep_id = c.payload.get("episode_id", c.target_id)
            if ep_id not in episode_matches:
                episode_matches[ep_id] = {
                    "best_score": c.score,
                    "space": c.space,
                    "target_type": c.payload.get("target_type", "episode"),
                    "evidence_ids": [],
                    "candidates": [c],
                }
            else:
                episode_matches[ep_id]["candidates"].append(c)
                if c.score > episode_matches[ep_id]["best_score"]:
                    episode_matches[ep_id]["best_score"] = c.score

            if c.payload.get("target_type") == "evidence":
                ev_id = c.payload.get("evidence_id", c.target_id)
                if ev_id and ev_id not in episode_matches[ep_id]["evidence_ids"]:
                    episode_matches[ep_id]["evidence_ids"].append(ev_id)

        # 4. Asignar veredictos
        results: List[QueryResult] = []
        sorted_episodes = sorted(
            episode_matches.items(),
            key=lambda item: item[1]["best_score"],
            reverse=True,
        )

        is_rrf = (text_vector is not None and (clip_text_vector is not None or clip_image_vector is not None))
        if is_rrf:
            thresh_strong = 1.0 / (self.rrf_k + 10)
            thresh_base = 1.0 / (self.rrf_k + 30)
            ambiguity_delta = 0.00005
        else:
            thresh_strong = self.default_text_threshold
            thresh_base = self.default_text_threshold * 0.7
            ambiguity_delta = 0.03

        any_supported = False
        for rank, (ep_id, info) in enumerate(sorted_episodes[:limit], start=1):
            ep = self.repository.get_episode(ep_id)
            # Qdrant es una proyección derivada: un punto obsoleto nunca puede
            # devolver un episodio que SQLite ya considera invisible.
            if not ep or not ep.is_visible:
                continue

            # Filtro posterior de intervalo si fue solicitado
            if interval_filter and ep.interval:
                overlap = ep.interval.overlaps(interval_filter)
                if overlap is False:
                    continue

            score = info["best_score"]
            reasons: List[str] = []
            verdict: Verdict

            # Verificar si algún candidato individual supera el umbral de similitud en su espacio
            has_relevant_match = False
            for c in info["candidates"]:
                cand_score = c.raw_score if c.raw_score is not None else c.score
                if c.space == RepresentationSpace.NOMIC_TEXT and cand_score >= self.default_text_threshold:
                    has_relevant_match = True
                    break
                elif c.space != RepresentationSpace.NOMIC_TEXT and cand_score >= self.default_image_threshold:
                    has_relevant_match = True
                    break

            # Lógica de veredicto
            if not has_relevant_match:
                verdict = Verdict.INSUFFICIENT_EVIDENCE
                reasons.append(f"Similitud por debajo del umbral mínimo de evidencia relevante ({self.default_text_threshold:.2f})")
            elif score >= thresh_strong:
                any_supported = True
                if len(info["evidence_ids"]) > 0 or len(ep.evidence_ids) > 0:
                    verdict = Verdict.SUPPORTED
                    reasons.append(f"Coincidencia fuerte ({score:.4f}) respaldada por evidencia verificable")
                else:
                    verdict = Verdict.CANDIDATE
                    reasons.append(f"Coincidencia textual ({score:.4f}) sin evidencia visual directa")
            elif score >= thresh_base:
                verdict = Verdict.CANDIDATE
                reasons.append(f"Coincidencia moderada ({score:.4f}) por encima del umbral base")
            else:
                verdict = Verdict.INSUFFICIENT_EVIDENCE
                reasons.append(f"Score bajo ({score:.4f}), evidencia insuficiente para afirmar coincidencia")

            # Adjuntar evidencias del episodio
            ev_list = info["evidence_ids"] if info["evidence_ids"] else ep.evidence_ids

            results.append(
                QueryResult(
                    episode_id=ep.id,
                    score=score,
                    rank=rank,
                    space=info["space"],
                    original_score=score,
                    evidence_ids=ev_list,
                    is_direct_match=True,
                    verdict=verdict,
                    reasons=reasons,
                )
            )

        # Detectar ambigüedad en el top
        overall_verdict = Verdict.NO_MATCH
        overall_reasons = []

        if not results:
            overall_verdict = Verdict.NO_MATCH
            overall_reasons.append("No se encontraron episodios relevantes para la consulta")
        elif not any_supported and all(r.verdict == Verdict.INSUFFICIENT_EVIDENCE for r in results):
            overall_verdict = Verdict.NO_MATCH
            overall_reasons.append("No se encontró evidencia coincidente que supere los umbrales de relevancia")
        elif (
            require_unique_resolution
            and len(results) >= 2
            and abs(results[0].score - results[1].score) < ambiguity_delta
            and results[0].verdict in (Verdict.SUPPORTED, Verdict.CANDIDATE)
        ):
            overall_verdict = Verdict.AMBIGUOUS
            overall_reasons.append(
                "La consulta exige una resolución única, pero hay múltiples candidatos con puntuaciones próximas"
            )
        elif any_supported:
            first_supported = next(
                (result for result in results if result.verdict == Verdict.SUPPORTED),
                None,
            )
            if first_supported is not None:
                overall_verdict = Verdict.SUPPORTED
                overall_reasons.extend(first_supported.reasons)
            else:
                # Una similitud fuerte sin Evidence legado sigue siendo un
                # candidato. Fase 7 rehidrata despues sus EvidenceReference.
                overall_verdict = results[0].verdict
                overall_reasons.extend(results[0].reasons)
        else:
            overall_verdict = results[0].verdict
            overall_reasons.extend(results[0].reasons)

        return QueryRun(
            intent=SearchIntent.SEMANTIC_SEARCH,
            text_query=text_query,
            source_filter=source_filter,
            interval_filter=interval_filter,
            limit=limit,
            results=results,
            overall_verdict=overall_verdict,
            reasons=overall_reasons,
        )

    def _handle_interval_search(
        self,
        interval: Optional[Interval],
        text_query: Optional[str],
        source_filter: Optional[List[str]],
        limit: int,
    ) -> QueryRun:
        """Búsqueda restringida a un intervalo de tiempo."""
        if not interval:
            return QueryRun(
                intent=SearchIntent.INTERVAL_SEARCH,
                overall_verdict=Verdict.INSUFFICIENT_EVIDENCE,
                reasons=["Se requiere un intervalo temporal explícito para INTERVAL_SEARCH"],
            )

        # Listar episodios filtrando por intervalo y fuente
        first_source = source_filter[0] if source_filter else None
        episodes, _ = self.repository.list_episodes(
            source_id=first_source,
            interval=interval,
            limit=limit * 2,
        )

        results: List[QueryResult] = []
        for rank, ep in enumerate(episodes[:limit], start=1):
            reasons = []
            if ep.interval and ep.interval.overlaps(interval):
                verdict = Verdict.SUPPORTED
                reasons.append("Episodio dentro o solapando con el intervalo solicitado")
            elif ep.interval and ep.interval.start and ep.interval.start.is_unknown:
                verdict = Verdict.INSUFFICIENT_EVIDENCE
                reasons.append("Episodio con tiempo desconocido, no se puede confirmar intervalo")
            else:
                verdict = Verdict.CANDIDATE
                reasons.append("Candidato próximo al intervalo")

            results.append(
                QueryResult(
                    episode_id=ep.id,
                    score=1.0,
                    rank=rank,
                    evidence_ids=ep.evidence_ids,
                    is_direct_match=True,
                    verdict=verdict,
                    reasons=reasons,
                )
            )

        overall_verdict = results[0].verdict if results else Verdict.NO_MATCH
        return QueryRun(
            intent=SearchIntent.INTERVAL_SEARCH,
            text_query=text_query,
            source_filter=source_filter,
            interval_filter=interval,
            limit=limit,
            results=results,
            overall_verdict=overall_verdict,
            reasons=[f"Encontrados {len(results)} episodios en el intervalo temporal"],
        )

    def _handle_last_observation(
        self,
        entity_filter: Optional[str],
        source_filter: Optional[List[str]],
        limit: int,
    ) -> QueryRun:
        """Recupera la última observación de una entidad usando paginación con cursor."""
        if not entity_filter:
            return QueryRun(
                intent=SearchIntent.LAST_OBSERVATION,
                overall_verdict=Verdict.INSUFFICIENT_EVIDENCE,
                reasons=["Se requiere especificar un entity_id o label para LAST_OBSERVATION"],
            )

        # Si pasaron un label, buscar la entidad primero
        entities = self.repository.list_entities()
        target_entity = None
        for ent in entities:
            if ent.id == entity_filter or ent.label.lower() == entity_filter.lower():
                target_entity = ent
                break

        actual_entity_id = target_entity.id if target_entity else entity_filter

        # Paginación con cursor sobre el conjunto completo de observaciones
        all_observations: List[Observation] = []
        cursor: Optional[str] = None

        while True:
            batch, next_cursor = self.repository.list_observations_for_entity(
                entity_id=actual_entity_id,
                limit=100,
                cursor=cursor,
            )
            all_observations.extend(batch)
            if not next_cursor:
                break
            cursor = next_cursor

        if not all_observations:
            return QueryRun(
                intent=SearchIntent.LAST_OBSERVATION,
                entity_filter=entity_filter,
                overall_verdict=Verdict.NO_MATCH,
                reasons=[f"No se registraron observaciones para la entidad '{entity_filter}'"],
            )

        # Revalidar contra episodios visibles y contra el filtro de fuente.
        valid_observations = []
        for obs in all_observations:
            episode = self.repository.get_episode(obs.episode_id)
            if not episode or not episode.is_visible:
                continue
            if source_filter and not set(source_filter).intersection(episode.source_ids):
                continue
            valid_observations.append((obs, episode))

        if not valid_observations:
            return QueryRun(
                intent=SearchIntent.LAST_OBSERVATION,
                entity_filter=entity_filter,
                source_filter=source_filter,
                overall_verdict=Verdict.NO_MATCH,
                reasons=[f"No hay observaciones canónicas visibles para la entidad '{entity_filter}'"],
            )

        known = [
            pair for pair in valid_observations
            if pair[0].capture_time and pair[0].capture_time.absolute
        ]
        unknown_count = len(valid_observations) - len(known)

        if known:
            last_obs, ep = max(known, key=lambda pair: pair[0].capture_time.absolute)
        else:
            return QueryRun(
                intent=SearchIntent.LAST_OBSERVATION,
                entity_filter=entity_filter,
                source_filter=source_filter,
                overall_verdict=Verdict.INSUFFICIENT_EVIDENCE,
                reasons=["Todas las observaciones vigentes tienen tiempo de captura desconocido"],
            )

        reasons = [
            f"Última observación registrada por {last_obs.author} ({last_obs.provenance_kind.value}): '{last_obs.content}'"
        ]
        if last_obs.capture_time and last_obs.capture_time.absolute:
            reasons.append(f"Instante de captura: {last_obs.capture_time.absolute.isoformat()}")

        result_verdict = Verdict.SUPPORTED
        overall_verdict = Verdict.SUPPORTED
        if unknown_count:
            result_verdict = Verdict.CANDIDATE
            overall_verdict = Verdict.INSUFFICIENT_EVIDENCE
            reasons.append(
                f"latest_known: existen {unknown_count} observaciones vigentes con tiempo desconocido"
            )

        result = QueryResult(
            episode_id=last_obs.episode_id,
            score=1.0,
            rank=1,
            evidence_ids=last_obs.evidence_ids,
            is_direct_match=True,
            verdict=result_verdict,
            reasons=reasons,
        )

        return QueryRun(
            intent=SearchIntent.LAST_OBSERVATION,
            entity_filter=entity_filter,
            results=[result],
            overall_verdict=overall_verdict,
            reasons=reasons,
        )

    def _handle_timeline(
        self,
        source_filter: Optional[List[str]],
        interval_filter: Optional[Interval],
        limit: int,
    ) -> QueryRun:
        """Línea temporal secuencial de episodios."""
        first_source = source_filter[0] if source_filter else None
        episodes, _ = self.repository.list_episodes(
            source_id=first_source,
            interval=interval_filter,
            limit=limit,
        )

        # Ordenar cronológicamente (más antiguo a más reciente para timeline)
        episodes.reverse()

        results: List[QueryResult] = []
        for rank, ep in enumerate(episodes, start=1):
            time_str = "Tiempo desconocido"
            if ep.interval and ep.interval.start and ep.interval.start.absolute:
                time_str = ep.interval.start.absolute.isoformat()

            results.append(
                QueryResult(
                    episode_id=ep.id,
                    score=1.0,
                    rank=rank,
                    evidence_ids=ep.evidence_ids,
                    verdict=Verdict.SUPPORTED,
                    reasons=[f"Episodio '{ep.title}' en {ep.location} ({time_str})"],
                )
            )

        return QueryRun(
            intent=SearchIntent.TIMELINE,
            source_filter=source_filter,
            interval_filter=interval_filter,
            limit=limit,
            results=results,
            overall_verdict=Verdict.SUPPORTED if results else Verdict.NO_MATCH,
            reasons=[f"Línea temporal con {len(results)} hitos ordenados cronológicamente"],
        )
