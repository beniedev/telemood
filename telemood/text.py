"""Small dependency-free semantic bubble splitter."""

from __future__ import annotations

import re


_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n[ \t\r\n]*")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?。！？])")
_WHITESPACE_BREAK = re.compile(r"\s+")


def split_semantic_bubbles(text: str, max_length: int = 4096) -> tuple[str, ...]:
    """Split plain text conservatively while preserving its body characters.

    Paragraph boundaries are preferred, then sentence boundaries, then any
    whitespace.  If no boundary fits, the remaining text is hard-split.  Only
    outer whitespace is discarded; separators inside the body stay in the
    returned chunks.
    """

    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if not isinstance(max_length, int) or isinstance(max_length, bool) or max_length <= 0:
        raise ValueError("max_length must be a positive integer")
    body = text.strip()
    if not body:
        return ()
    if len(body) <= max_length:
        return (body,)

    chunks: list[str] = []
    start = 0
    while start < len(body):
        remaining = len(body) - start
        if remaining <= max_length:
            chunks.append(body[start:])
            break

        limit = start + max_length
        boundary = _preferred_boundary(body, start, limit)
        if boundary <= start:
            boundary = limit
        chunks.append(body[start:boundary])
        start = boundary

    return tuple(chunk for chunk in chunks if chunk)


def _preferred_boundary(text: str, start: int, limit: int) -> int:
    """Return the furthest preferred split point at or before ``limit``."""

    for pattern in (_PARAGRAPH_BREAK, _SENTENCE_BREAK, _WHITESPACE_BREAK):
        candidates: list[int] = []
        for match in pattern.finditer(text, start, limit + 1):
            end = match.end()
            if start < end <= limit:
                candidates.append(end)
        if candidates:
            return max(candidates)
    return start
