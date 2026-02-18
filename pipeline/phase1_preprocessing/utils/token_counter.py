"""토큰 카운트 유틸리티"""
import json
import tiktoken


_encoder = None


def get_encoder():
    """tiktoken 인코더 싱글턴"""
    global _encoder
    if _encoder is None:
        _encoder = tiktoken.get_encoding("cl100k_base")
    return _encoder


def count_tokens(text: str) -> int:
    """텍스트의 토큰 수 반환"""
    if not text:
        return 0
    return len(get_encoder().encode(text))


def count_chunk_tokens(chunk: dict) -> int:
    """청크 전체의 토큰 수 계산 (텍스트 + 테이블 직렬화)"""
    total = 0
    total += count_tokens(chunk.get("text", ""))
    for table in chunk.get("tables", []):
        total += count_tokens(json.dumps(table.get("rows", []), ensure_ascii=False))
    for note in chunk.get("notes", []):
        total += count_tokens(note)
    return total
