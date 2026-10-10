"""Script para construir el corpus de demostración Atlas Security:
- Genera imágenes sintéticas en data/atlas/media/ con marca explícita de medio sintético.
- Construye data/atlas/manifest.json cumpliendo con la especificación completa del prompt (§13).
- Construye data/atlas/queries.json con las 8 consultas de referencia.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

DATA_DIR = Path("data/atlas")
MEDIA_DIR = DATA_DIR / "media"
EVENTS_DIR = DATA_DIR / "events"

MEDIA_DIR.mkdir(parents=True, exist_ok=True)


def create_synthetic_image(filename: str, title: str, bg_color: tuple, text_color: tuple = (255, 255, 255)) -> str:
    """Genera una imagen sintética de 640x480 con texto identificativo y marca sintética."""
    img_path = MEDIA_DIR / filename
    if img_path.exists():
        return str(img_path.relative_to(DATA_DIR)).replace("\\", "/")

    img = Image.new("RGB", (640, 480), color=bg_color)
    draw = ImageDraw.Draw(img)

    # Marco visual de seguridad
    draw.rectangle([10, 10, 630, 470], outline=(200, 200, 200), width=3)
    draw.rectangle([20, 20, 620, 60], fill=(30, 30, 30))

    # Título y marca sintética
    draw.text((30, 30), f"ATLAS CCTV [SYNTHETIC] - {filename}", fill=(0, 255, 0))
    draw.text((40, 100), title, fill=text_color)
    draw.text((40, 420), "EVIDENCIA SINTETICA DE REFERENCIA - MEMORIA EPISODICA", fill=(255, 200, 0))

    img.save(img_path, "PNG")
    return str(img_path.relative_to(DATA_DIR)).replace("\\", "/")


def build_dataset():
    # 1. Crear imágenes sintéticas
    img_ep1_px1 = create_synthetic_image("c01_180147_px1_entry.png", "Cam C01: PX1 con chaqueta azul y mochila franja reflectante", (25, 35, 60))
    img_ep1_p01 = create_synthetic_image("c01_180133_p01_entry.png", "Cam C01: Laura Mendez P01 con abrigo beige y bufanda", (45, 45, 55))
    img_ep2_px2 = create_synthetic_image("c01_180542_px2_courier.png", "Cam C01: Mensajero PX2 con caja O02 y mochila negra lisa", (40, 30, 30))
    img_ep3_o01 = create_synthetic_image("c03_181200_laptop_armario.png", "Cam C03: Portatil O01 colocado en armario C12", (20, 40, 35))
    img_ep4_cross = create_synthetic_image("c02_181710_cruce_pasillo.png", "Cam C02: Cruce en pasillo entre PX1 y PX2 (ambas mochilas)", (35, 35, 45))
    img_ep6_alarm = create_synthetic_image("c03_182345_lab_access.png", "Cam C03: Acceso al laboratorio durante alarma con credencial B0714", (60, 20, 20))
    img_ep7_recep = create_synthetic_image("c02_182410_px2_recepcion.png", "Cam C02: Mensajero PX2 permanece en recepcion durante alarma", (30, 50, 40))
    img_ep8_stairs = create_synthetic_image("c04_182615_escalera_huella.png", "Cam C04: Transito por escalera y suela blanca", (35, 35, 35))
    img_ep9_car = create_synthetic_image("c05_182900_parking_exit.png", "Cam C05: Vehiculo no registrado saliendo por el aparcamiento", (25, 25, 40))
    img_ep10_precinto = create_synthetic_image("f007_precinto_8419.png", "Foto F007: Fragmento de precinto rojo con caracteres 8419", (70, 25, 25))

    # 2. Fuentes
    sources = [
        {"id": "src-c01", "name": "C01 Entrada Principal", "source_type": "camera", "scope": "Acceso exterior y torno", "clock_source": "cctv_ntp"},
        {"id": "src-c02", "name": "C02 Recepción y Pasillo Este", "source_type": "camera", "scope": "Mostrador y sala de espera", "clock_source": "cctv_ntp"},
        {"id": "src-c03", "name": "C03 Laboratorio Atlas", "source_type": "camera", "scope": "Armarios y mesas de trabajo", "clock_source": "cctv_ntp"},
        {"id": "src-c04", "name": "C04 Escalera de Servicio", "source_type": "camera", "scope": "Ruta técnica entre plantas", "clock_source": "cctv_ntp"},
        {"id": "src-c05", "name": "C05 Aparcamiento Exterior", "source_type": "camera", "scope": "Barrera y viales de salida", "clock_source": "cctv_ntp"},
        {"id": "src-acc", "name": "Registro de Accesos D-LAB", "source_type": "sensor", "scope": "Cerradura electrónica D-LAB-2", "clock_source": "access_ntp"},
        {"id": "src-fire", "name": "Sistema Central de Incendios", "source_type": "sensor", "scope": "Detectores de humo edificio", "clock_source": "fire_central"},
        {"id": "src-manual", "name": "Declaraciones de Testigos", "source_type": "manual", "scope": "Entrevistas de seguridad", "clock_source": "manual"},
    ]

    # 3. Sesiones de captura
    sessions = [
        {"id": "sess-incident-20260101", "source_id": "src-c01", "external_id": "INC-ATLAS-2026-01-01", "started_at": {"absolute": "2026-01-01T18:00:00Z"}, "ended_at": {"absolute": "2026-01-01T18:50:00Z"}},
    ]

    # 4. Entidades
    entities = [
        {"id": "ent-p01", "label": "Laura Méndez", "entity_type": "person", "description": "Investigadora principal Laboratorio Atlas", "created_by": "security_admin", "provenance_kind": "human_declaration"},
        {"id": "ent-px1", "label": "Visitante Desconocido PX1", "entity_type": "person", "description": "Persona con chaqueta azul marino y mochila con franja reflectante", "created_by": "guard_c01", "provenance_kind": "visual_detection"},
        {"id": "ent-px2", "label": "Mensajero PX2", "entity_type": "person", "description": "Repartidor con mochila negra lisa y caja de archivo", "created_by": "guard_c01", "provenance_kind": "visual_detection"},
        {"id": "ent-o01", "label": "Portátil Atlas O01", "entity_type": "object", "description": "Equipo con prototipos confidenciales de memoria", "created_by": "inventory_admin", "provenance_kind": "human_declaration"},
        {"id": "ent-o02", "label": "Caja de Archivo O02", "entity_type": "object", "description": "Caja precintada con código S88419", "created_by": "courier_record", "provenance_kind": "human_declaration"},
        {"id": "ent-o03", "label": "Mochila con Franja O03", "entity_type": "object", "description": "Mochila oscura con franja reflectante horizontal", "created_by": "detector_yolo", "provenance_kind": "visual_detection"},
        {"id": "ent-b0714", "label": "Credencial B0714", "entity_type": "object", "description": "Tarjeta de acceso asignada a contrata de mantenimiento", "created_by": "access_system", "provenance_kind": "system_derived"},
    ]

    # 5. Episodios
    episodes = [
        # EP-001
        {
            "id": "ep-atlas-001",
            "external_id": "ATLAS-EP-001",
            "title": "Llegada del visitante a recepción",
            "interval": {"start": {"absolute": "2026-01-01T18:01:33Z"}, "end": {"absolute": "2026-01-01T18:03:10Z"}},
            "source_ids": ["src-c01", "src-c02"],
            "location": "Entrada principal y recepción",
            "tags": ["entrada", "visitante", "persona_desconocida", "mochila", "recepción"],
            "indexable_text": "A las 18:01:33, Laura Méndez entra por la puerta principal con abrigo beige y bufanda verde. Catorce segundos después entra una persona no identificada con chaqueta azul marino, pantalón oscuro, zapatillas con suela blanca y una mochila oscura atravesada por una franja reflectante plateada (PX1, mochila O03). En recepción, PX1 pregunta por Sergio y afirma tener una reunión a las 18:15.",
            "evidence": [
                {"id": "ev-001-1", "session_id": "sess-incident-20260101", "source_id": "src-c01", "media_type": "image", "file_path": img_ep1_p01, "external_capture_id": "c01_frame_180133"},
                {"id": "ev-001-2", "session_id": "sess-incident-20260101", "source_id": "src-c01", "media_type": "image", "file_path": img_ep1_px1, "external_capture_id": "c01_frame_180147"},
            ],
            "observations": [
                {"content": "Detección visual de Laura Méndez P01 entrando con abrigo beige", "provenance_kind": "visual_detection", "author": "detector_face", "confidence": 0.94, "entity_id": "ent-p01"},
                {"content": "Detección visual de PX1 con chaqueta azul marino y mochila con franja reflectante O03", "provenance_kind": "visual_detection", "author": "detector_yolo", "confidence": 0.89, "entity_id": "ent-px1"},
                {"content": "Declaración del recepcionista: PX1 afirmó tener cita con Sergio a las 18:15", "provenance_kind": "human_declaration", "author": "recepcionista_marcos"},
            ],
            "annotations": []
        },
        # EP-002
        {
            "id": "ep-atlas-002",
            "external_id": "ATLAS-EP-002",
            "title": "Entrega de la caja de archivo por mensajero",
            "interval": {"start": {"absolute": "2026-01-01T18:05:42Z"}, "end": {"absolute": "2026-01-01T18:08:11Z"}},
            "source_ids": ["src-c01", "src-c02"],
            "location": "Recepción",
            "tags": ["mensajería", "caja", "precinto", "entrega"],
            "indexable_text": "El mensajero PX2 llega a recepción con una caja de archivo O02 asegurada con el precinto rojo numerado S88419. PX2 lleva uniforme de reparto y una mochila negra completamente lisa, sin franjas reflectantes.",
            "evidence": [
                {"id": "ev-002-1", "session_id": "sess-incident-20260101", "source_id": "src-c01", "media_type": "image", "file_path": img_ep2_px2, "external_capture_id": "c01_frame_180542"},
            ],
            "observations": [
                {"content": "Detección de mensajero PX2 con mochila negra lisa y caja precintada S88419", "provenance_kind": "visual_detection", "author": "detector_yolo", "confidence": 0.91, "entity_id": "ent-px2"},
            ],
            "annotations": []
        },
        # EP-003
        {
            "id": "ep-atlas-003",
            "external_id": "ATLAS-EP-003",
            "title": "Última observación confirmada del portátil",
            "interval": {"start": {"absolute": "2026-01-01T18:11:18Z"}, "end": {"absolute": "2026-01-01T18:14:36Z"}},
            "source_ids": ["src-c03"],
            "location": "Laboratorio Atlas",
            "tags": ["portátil", "armario", "laboratorio", "resguardo"],
            "indexable_text": "A las 18:12:00, Laura Méndez guarda el portátil O01 en el armario de seguridad C12 del Laboratorio Atlas. En el registro C03 se observa el equipo guardado en la balda superior.",
            "evidence": [
                {"id": "ev-003-1", "session_id": "sess-incident-20260101", "source_id": "src-c03", "media_type": "image", "file_path": img_ep3_o01, "external_capture_id": "c03_frame_181200"},
            ],
            "observations": [
                {"content": "Confirmación visual de portátil O01 resguardado en armario C12 balda superior", "provenance_kind": "visual_detection", "author": "detector_obj", "confidence": 0.96, "entity_id": "ent-o01"},
            ],
            "annotations": []
        },
        # EP-004 (Cruce y distinción de mochilas similares)
        {
            "id": "ep-atlas-004",
            "external_id": "ATLAS-EP-004",
            "title": "Cruce entre visitante y mensajero en pasillo este",
            "interval": {"start": {"absolute": "2026-01-01T18:16:05Z"}, "end": {"absolute": "2026-01-01T18:18:02Z"}},
            "source_ids": ["src-c02"],
            "location": "Pasillo este",
            "tags": ["cruce", "mochila_similar", "pasillo", "identidad_distinta"],
            "indexable_text": "PX1 y PX2 coinciden en el pasillo este a las 18:17:10. Ambos portan mochilas oscuras de dimensiones similares; sin embargo, la mochila de PX1 tiene una franja reflectante plateada (O03), mientras que la mochila del mensajero PX2 es completamente lisa y negra. Son dos mochilas con identidad y procedencia distintas.",
            "evidence": [
                {"id": "ev-004-1", "session_id": "sess-incident-20260101", "source_id": "src-c02", "media_type": "image", "file_path": img_ep4_cross, "external_capture_id": "c02_frame_181710"},
            ],
            "observations": [
                {"content": "Coincidencia de dos individuos con mochilas oscuras: O03 posee banda reflectante, la de PX2 carece de bandas", "provenance_kind": "visual_detection", "author": "detector_feature_match", "confidence": 0.92},
            ],
            "annotations": []
        },
        # EP-005
        {
            "id": "ep-atlas-005",
            "external_id": "ATLAS-EP-005",
            "title": "Activación de la alarma de incendios",
            "interval": {"start": {"absolute": "2026-01-01T18:22:47Z"}, "end": {"absolute": "2026-01-01T18:23:35Z"}},
            "source_ids": ["src-fire"],
            "location": "Office / Cocina planta 1",
            "tags": ["alarma", "incendio", "evacuación", "humo"],
            "indexable_text": "El sensor central de incendios registra activación por detector de humo óptico en la cocina de la planta 1 a las 18:22:47. Se inicia protocolo de evacuación automática en pasillos.",
            "evidence": [],
            "observations": [
                {"content": "Disparo de sensor de humo en office planta 1 (código F-102)", "provenance_kind": "system_derived", "author": "fire_central", "confidence": 1.0},
            ],
            "annotations": []
        },
        # EP-006 (Simultáneo con EP-007)
        {
            "id": "ep-atlas-006",
            "external_id": "ATLAS-EP-006",
            "title": "Acceso no autorizado al laboratorio durante la alarma",
            "interval": {"start": {"absolute": "2026-01-01T18:23:18Z"}, "end": {"absolute": "2026-01-01T18:25:06Z"}},
            "source_ids": ["src-c03", "src-acc"],
            "location": "Laboratorio Atlas puerta D-LAB-2",
            "tags": ["acceso_no_autorizado", "alarma", "credencial", "simultáneo"],
            "indexable_text": "Durante la alarma de incendios, la cerradura D-LAB-2 registra apertura a las 18:23:18 con la credencial técnica B0714. La cámara C03 capta a un individuo con chaqueta azul y mochila con franja reflectante acercándose rápidamente al armario C12.",
            "evidence": [
                {"id": "ev-006-1", "session_id": "sess-incident-20260101", "source_id": "src-c03", "media_type": "image", "file_path": img_ep6_alarm, "external_capture_id": "c03_frame_182345"},
            ],
            "observations": [
                {"content": "Apertura electrónica de puerta D-LAB-2 validada con credencial B0714", "provenance_kind": "system_derived", "author": "access_sensor", "confidence": 1.0, "entity_id": "ent-b0714"},
                {"content": "Individuo con zapatillas de suela blanca y mochila con franja reflectante accede al armario C12", "provenance_kind": "visual_detection", "author": "detector_yolo", "confidence": 0.88},
            ],
            "annotations": []
        },
        # EP-007 (Simultáneo con EP-006 en recepción, descartando a PX2)
        {
            "id": "ep-atlas-007",
            "external_id": "ATLAS-EP-007",
            "title": "Localización simultánea del mensajero en recepción",
            "interval": {"start": {"absolute": "2026-01-01T18:23:24Z"}, "end": {"absolute": "2026-01-01T18:25:12Z"}},
            "source_ids": ["src-c02"],
            "location": "Recepción",
            "tags": ["simultáneo", "coartada", "recepción", "mensajero"],
            "indexable_text": "Al mismo tiempo que ocurre el acceso al laboratorio (18:23:24 a 18:25:12), la cámara C02 registra de forma continua al mensajero PX2 esperando en el mostrador de recepción junto al recepcionista. Queda demostrado que PX2 se encontraba en recepción y no pudo acceder al laboratorio.",
            "evidence": [
                {"id": "ev-007-1", "session_id": "sess-incident-20260101", "source_id": "src-c02", "media_type": "image", "file_path": img_ep7_recep, "external_capture_id": "c02_frame_182410"},
            ],
            "observations": [
                {"content": "Presencia continuada de PX2 en recepción durante el intervalo exacto de la intrusión en laboratorio", "provenance_kind": "visual_detection", "author": "tracker_c02", "confidence": 0.98, "entity_id": "ent-px2"},
            ],
            "annotations": []
        },
        # EP-008 (Detalle sin etiqueta global: patrón de suela blanca)
        {
            "id": "ep-atlas-008",
            "external_id": "ATLAS-EP-008",
            "title": "Tránsito por la escalera de servicio y marca de suela",
            "interval": {"start": {"absolute": "2026-01-01T18:25:42Z"}, "end": {"absolute": "2026-01-01T18:27:18Z"}},
            "source_ids": ["src-c04"],
            "location": "Escalera de servicio norte",
            "tags": ["escalera", "suela_blanca", "huella", "huida"],
            "indexable_text": "A las 18:26:15, el sujeto de chaqueta azul desciende por la escalera de servicio con la mochila O03 visiblemente más abultada. Se aprecia una marca de suciedad con patrón parcial de suela blanca en el rellano de la planta baja.",
            "evidence": [
                {"id": "ev-008-1", "session_id": "sess-incident-20260101", "source_id": "src-c04", "media_type": "image", "file_path": img_ep8_stairs, "external_capture_id": "c04_frame_182615"},
            ],
            "observations": [
                {"content": "Sujeto desciende con calzado de suela blanca y mochila con bulto rígido rectangular", "provenance_kind": "visual_detection", "author": "detector_yolo", "confidence": 0.85},
            ],
            "annotations": []
        },
        # EP-009
        {
            "id": "ep-atlas-009",
            "external_id": "ATLAS-EP-009",
            "title": "Salida rápida por el aparcamiento exterior",
            "interval": {"start": {"absolute": "2026-01-01T18:28:09Z"}, "end": {"absolute": "2026-01-01T18:30:41Z"}},
            "source_ids": ["src-c05"],
            "location": "Aparcamiento exterior",
            "tags": ["aparcamiento", "vehículo", "salida"],
            "indexable_text": "El sujeto sale por la puerta de emergencia que da al aparcamiento a las 18:28:09 y sube a un sedán gris oscuro que abandona el recinto a gran velocidad por el vial sur.",
            "evidence": [
                {"id": "ev-009-1", "session_id": "sess-incident-20260101", "source_id": "src-c05", "media_type": "image", "file_path": img_ep9_car, "external_capture_id": "c05_frame_182900"},
            ],
            "observations": [
                {"content": "Vehículo sedan gris oscuro sale sin matrícula delantera visible", "provenance_kind": "visual_detection", "author": "anpr_camera", "confidence": 0.70},
            ],
            "annotations": []
        },
        # EP-010 (Tiempo parcial/desconocido + Enriquecimiento por anotación + Precinto 8419 + Tensión declaración humana vs sensor)
        {
            "id": "ep-atlas-010",
            "external_id": "ATLAS-EP-010",
            "title": "Detección de la desaparición y evidencia de precinto",
            "interval": {"start": {"absolute": "2026-01-01T18:36:30Z"}, "end": {"absolute": "2026-01-01T18:44:12Z"}},
            "source_ids": ["src-c03", "src-manual"],
            "location": "Laboratorio Atlas",
            "tags": ["desaparición", "precinto_8419", "auditoría", "enriquecimiento", "llave"],
            "indexable_text": "A las 18:37:11, Laura Méndez comprueba que el portátil O01 no está en el armario C12. La llave estaba puesta en la cerradura exterior, circunstancia que P01 afirma no recordar (tensión entre declaración y evidencia física). En el suelo se halla un fragmento de precinto rojo con los números 8419, compatible con el precinto S88419 de O02.",
            "evidence": [
                {"id": "ev-010-1", "session_id": "sess-incident-20260101", "source_id": "src-c03", "media_type": "image", "file_path": img_ep10_precinto, "external_capture_id": "f007_macro_precinto"},
            ],
            "observations": [
                {"content": "Declaración de Laura Méndez: Afirma que cerró con llave a las 18:14 y guardó la llave en su bolsillo", "provenance_kind": "human_declaration", "author": "laura_mendez"},
                {"content": "Inspección fotográfica: Llave insertada en la cerradura exterior del armario C12 sin signos de forzado", "provenance_kind": "visual_detection", "author": "inspeccion_forense", "confidence": 1.0},
                {"content": "Fragmento de precinto plástico rojo recuperado junto al armario; texto legible '8419'", "provenance_kind": "ocr_extraction", "author": "ocr_tesseract", "confidence": 0.95},
            ],
            "annotations": [
                {
                    "revision_number": 1,
                    "content": "Revisión técnica de seguridad: La credencial B0714 fue extraviada por la contrata de limpieza el día anterior, no reportada oportunamente.",
                    "provenance_kind": "human_declaration",
                    "author": "supervisor_seguridad",
                    "evidence_ids": ["ev-010-1"]
                }
            ]
        }
    ]

    manifest = {
        "version": "1.0",
        "description": "Corpus de demostración Atlas Security Scenario con 10 episodios multimodales",
        "sources": sources,
        "sessions": sessions,
        "entities": entities,
        "episodes": episodes,
    }

    manifest_file = DATA_DIR / "manifest.json"
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"Manifiesto guardado en {manifest_file}")

    # 6. Consultas de referencia según Prompt §13
    queries = [
        {
            "id": "q1_multiple_appearances",
            "name": "Múltiples apariciones del mismo individuo o mochila",
            "intent": "semantic_search",
            "text_query": "individuo con chaqueta azul marino y mochila con franja reflectante",
            "expected_verdict": "supported",
            "expected_episodes": ["ATLAS-EP-001", "ATLAS-EP-004", "ATLAS-EP-006", "ATLAS-EP-008"],
            "comment": "Rastreo transversal del visitante PX1 en diferentes fuentes y momentos"
        },
        {
            "id": "q2_similar_objects",
            "name": "Dos objetos similares pero con identidad distinta",
            "intent": "semantic_search",
            "text_query": "mochila negra lisa sin bandas reflectantes llevada por mensajero",
            "expected_verdict": "supported",
            "expected_episodes": ["ATLAS-EP-002", "ATLAS-EP-004", "ATLAS-EP-007"],
            "comment": "Debe distinguir la mochila lisa de PX2 de la mochila reflectante O03 de PX1"
        },
        {
            "id": "q3_simultaneous_events",
            "name": "Sucesos simultáneos en fuentes distintas (coartada)",
            "intent": "interval_search",
            "interval": {"start": {"absolute": "2026-01-01T18:23:20Z"}, "end": {"absolute": "2026-01-01T18:25:10Z"}},
            "expected_verdict": "supported",
            "expected_episodes": ["ATLAS-EP-006", "ATLAS-EP-007"],
            "comment": "Demuestra que mientras ocurría la intrusión en C03, PX2 estaba en recepción ante C02"
        },
        {
            "id": "q4_unknown_time",
            "name": "Suceso con tiempo desconocido o no registrado",
            "intent": "interval_search",
            "interval": {"start": {"is_unknown": True}, "end": {"is_unknown": True}},
            "expected_verdict": "insufficient_evidence",
            "expected_episodes": [],
            "comment": "Verifica que vacíos temporales no se inventen como instantes certeros"
        },
        {
            "id": "q5_unlabeled_detail",
            "name": "Detalle relevante sin etiqueta global (precinto 8419)",
            "intent": "semantic_search",
            "text_query": "fragmento de precinto rojo con número 8419 hallado cerca del armario",
            "expected_verdict": "supported",
            "expected_episodes": ["ATLAS-EP-010"],
            "comment": "Recupera por detalle en la observación/OCR aunque no haya entidad etiquetada"
        },
        {
            "id": "q6_declaration_vs_observation",
            "name": "Declaración humana en tensión con observación",
            "intent": "semantic_search",
            "text_query": "declaración de Laura Méndez sobre la llave del armario C12",
            "expected_verdict": "supported",
            "expected_episodes": ["ATLAS-EP-010"],
            "comment": "Evidencia la contradicción entre el testimonio de P01 y la foto de la llave puesta"
        },
        {
            "id": "q7_last_observation",
            "name": "Última observación de una entidad específica",
            "intent": "last_observation",
            "entity_filter": "ent-o01",
            "expected_verdict": "supported",
            "expected_episodes": ["ATLAS-EP-003"],
            "comment": "Última vez que el portátil O01 fue visto antes de desaparecer"
        },
        {
            "id": "q8_no_match",
            "name": "Consulta sin evidencia coincidente en el corpus",
            "intent": "semantic_search",
            "text_query": "camión de bomberos estacionado en la entrada principal",
            "expected_verdict": "no_match",
            "expected_episodes": [],
            "comment": "Debe responder NO_MATCH o INSUFFICIENT_EVIDENCE de forma transparente"
        }
    ]

    queries_file = DATA_DIR / "queries.json"
    with open(queries_file, "w", encoding="utf-8") as f:
        json.dump(queries, f, indent=2, ensure_ascii=False)
    print(f"Consultas de referencia guardadas en {queries_file}")


if __name__ == "__main__":
    build_dataset()
