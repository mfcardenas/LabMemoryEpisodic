"""Fragmentación textual reutilizable con offsets exactos de evidencia."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextFragment:
    index: int
    start: int
    end: int
    text: str


def split_text_with_offsets(
    text: str,
    *,
    chunk_size: int = 1000,
    chunk_overlap: int = 100,
) -> list[TextFragment]:
    """Adapta el algoritmo del ``TextChunker`` legado y conserva posiciones."""
    if chunk_size <= 0:
        raise ValueError("chunk_size debe ser positivo")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap debe ser no negativo y menor que chunk_size")
    if not text.strip():
        return []

    fragments: list[TextFragment] = []
    start = 0
    total_length = len(text)
    while start < total_length:
        proposed_end = min(start + chunk_size, total_length)
        if proposed_end < total_length:
            break_point = text.rfind("\n\n", start, proposed_end)
            if break_point == -1 or break_point <= start:
                break_point = text.rfind(". ", start, proposed_end)
            if break_point == -1 or break_point <= start:
                break_point = text.rfind(" ", start, proposed_end)
            if break_point == -1 or break_point <= start:
                break_point = proposed_end
            else:
                break_point += 1
        else:
            break_point = total_length

        content_start = start
        content_end = break_point
        while content_start < content_end and text[content_start].isspace():
            content_start += 1
        while content_end > content_start and text[content_end - 1].isspace():
            content_end -= 1
        if content_start < content_end:
            fragments.append(
                TextFragment(
                    index=len(fragments),
                    start=content_start,
                    end=content_end,
                    text=text[content_start:content_end],
                )
            )
        if break_point >= total_length:
            break
        step = max(break_point - start - chunk_overlap, 1)
        start += step

    return fragments
