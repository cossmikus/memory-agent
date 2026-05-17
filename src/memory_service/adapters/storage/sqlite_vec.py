"""sqlite-vec extension loader with a numpy brute-force fallback.

If the sqlite-vec loadable extension fails (older SQLite build, sandboxed env,
ARM/x86 wheel mismatch), we degrade to in-memory cosine over a separate
embeddings table. /health stays green either way.
"""
from __future__ import annotations

import json
import struct

import aiosqlite
import numpy as np

from memory_service.core.logging import get_logger

log = get_logger(__name__)


VEC_EXTENSION_AVAILABLE: bool = False


async def init_vec(conn: aiosqlite.Connection, dim: int) -> None:
    """Try to load sqlite-vec and create the vec0 virtual table.

    On failure, create a plain table that holds the embedding as a BLOB —
    the repo's search_vector will brute-force cosine over it.
    """
    global VEC_EXTENSION_AVAILABLE
    try:
        import sqlite_vec  # type: ignore

        await conn.enable_load_extension(True)
        await conn.load_extension(sqlite_vec.loadable_path())
        await conn.enable_load_extension(False)

        await conn.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0(
                memory_id TEXT PRIMARY KEY,
                embedding float[{dim}]
            )
            """
        )
        VEC_EXTENSION_AVAILABLE = True
        log.info("sqlite_vec_loaded", dim=dim)
    except Exception as exc:
        VEC_EXTENSION_AVAILABLE = False
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vec_memories_fallback (
                memory_id TEXT PRIMARY KEY,
                user_id   TEXT NOT NULL,
                embedding BLOB NOT NULL
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vec_fallback_user ON vec_memories_fallback(user_id)"
        )
        log.warning("sqlite_vec_unavailable_using_fallback", error=str(exc))


def pack_embedding(vec: list[float]) -> bytes:
    """Pack a float vector as little-endian f32 bytes — same wire format as
    sqlite-vec, so the same blobs work for both code paths."""
    arr = np.asarray(vec, dtype=np.float32)
    return arr.tobytes()


def unpack_embedding(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)
