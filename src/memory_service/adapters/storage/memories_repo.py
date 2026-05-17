"""Memories repository — structured memories + triples + FTS5 + vector index.

Writes are transactional: a memory and its FTS5 row and its embedding are
inserted in the same write block. This is what gives us the "after /turns
returns, /recall sees it" guarantee.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import numpy as np

from memory_service.adapters.storage.db import Database
from memory_service.adapters.storage.sqlite_vec import (
    VEC_EXTENSION_AVAILABLE,
    cosine_similarity,
    pack_embedding,
    unpack_embedding,
)
from memory_service.domain.models import Memory, MemoryType, Triple


def _iso(ts: datetime) -> str:
    return ts.isoformat() + "Z" if ts.tzinfo is None else ts.astimezone().isoformat()


def _row_to_memory(row: Any) -> Memory:
    return Memory(
        id=row["id"],
        user_id=row["user_id"],
        type=MemoryType(row["type"]),
        key=row["key"],
        value=row["value"],
        value_normalized=row["value_normalized"],
        confidence=float(row["confidence"]),
        salience=float(row["salience"]),
        source_turn_id=row["source_turn_id"],
        source_session_id=row["source_session_id"],
        created_at=datetime.fromisoformat(row["created_at"].rstrip("Z")),
        updated_at=datetime.fromisoformat(row["updated_at"].rstrip("Z")),
        supersedes=row["supersedes"],
        active=bool(row["active"]),
        history=json.loads(row["history_json"] or "[]"),
        metadata=json.loads(row["metadata_json"] or "{}"),
    )


class MemoriesRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ─── writes ───────────────────────────────────────────────────────

    async def insert_memory(
        self,
        memory: Memory,
        embedding: list[float] | None,
    ) -> None:
        async with self._db.write() as conn:
            await self._insert_memory_inner(conn, memory, embedding)

    async def _insert_memory_inner(
        self,
        conn,
        memory: Memory,
        embedding: list[float] | None,
    ) -> None:
        await conn.execute(
            """
            INSERT OR REPLACE INTO memories
                (id, user_id, type, key, value, value_normalized,
                 confidence, salience, source_turn_id, source_session_id,
                 created_at, updated_at, supersedes, active,
                 history_json, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory.id,
                memory.user_id,
                memory.type.value,
                memory.key,
                memory.value,
                memory.value_normalized,
                memory.confidence,
                memory.salience,
                memory.source_turn_id,
                memory.source_session_id,
                _iso(memory.created_at),
                _iso(memory.updated_at),
                memory.supersedes,
                1 if memory.active else 0,
                json.dumps(memory.history, ensure_ascii=False),
                json.dumps(memory.metadata, ensure_ascii=False),
            ),
        )

        # FTS5: drop+insert is fine, IDs are unique.
        await conn.execute(
            "DELETE FROM memories_fts WHERE memory_id = ?", (memory.id,)
        )
        await conn.execute(
            "INSERT INTO memories_fts (memory_id, user_id, key, value_normalized) "
            "VALUES (?, ?, ?, ?)",
            (memory.id, memory.user_id, memory.key, memory.value_normalized),
        )

        # Vector index.
        if embedding is not None:
            blob = pack_embedding(embedding)
            if VEC_EXTENSION_AVAILABLE:
                await conn.execute(
                    "DELETE FROM vec_memories WHERE memory_id = ?", (memory.id,)
                )
                await conn.execute(
                    "INSERT INTO vec_memories (memory_id, embedding) VALUES (?, ?)",
                    (memory.id, blob),
                )
            else:
                await conn.execute(
                    "INSERT OR REPLACE INTO vec_memories_fallback "
                    "(memory_id, user_id, embedding) VALUES (?, ?, ?)",
                    (memory.id, memory.user_id, blob),
                )

    async def update_memory(self, memory: Memory) -> None:
        """Update without touching FTS5 or embedding (used for activation flips
        and history appends — text fields unchanged in those paths)."""
        async with self._db.write() as conn:
            await conn.execute(
                """
                UPDATE memories
                SET value = ?, value_normalized = ?, confidence = ?, salience = ?,
                    updated_at = ?, supersedes = ?, active = ?,
                    history_json = ?, metadata_json = ?
                WHERE id = ?
                """,
                (
                    memory.value,
                    memory.value_normalized,
                    memory.confidence,
                    memory.salience,
                    _iso(memory.updated_at),
                    memory.supersedes,
                    1 if memory.active else 0,
                    json.dumps(memory.history, ensure_ascii=False),
                    json.dumps(memory.metadata, ensure_ascii=False),
                    memory.id,
                ),
            )

    async def mark_superseded(self, old_id: str, new_id: str) -> None:
        async with self._db.write() as conn:
            now = _iso(datetime.utcnow())
            await conn.execute(
                "UPDATE memories SET active = 0, updated_at = ? WHERE id = ?",
                (now, old_id),
            )
            await conn.execute(
                "UPDATE memories SET supersedes = ?, updated_at = ? WHERE id = ?",
                (old_id, now, new_id),
            )

    async def insert_triples(self, triples: list[Triple]) -> None:
        if not triples:
            return
        async with self._db.write() as conn:
            for t in triples:
                tid = (
                    f"{t.source_memory_id}:{t.predicate}:{hash((t.subject, t.object)) & 0xFFFFFFFF:x}"
                    if t.source_memory_id
                    else f"orphan:{t.predicate}:{hash((t.subject, t.object)) & 0xFFFFFFFF:x}"
                )
                user_id = ""
                # Triples carry user_id via source memory; resolve here.
                if t.source_memory_id:
                    cur = await conn.execute(
                        "SELECT user_id FROM memories WHERE id = ?",
                        (t.source_memory_id,),
                    )
                    row = await cur.fetchone()
                    await cur.close()
                    user_id = row["user_id"] if row else ""
                await conn.execute(
                    """
                    INSERT OR REPLACE INTO triples
                        (id, user_id, subject, predicate, object, source_memory_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (tid, user_id, t.subject, t.predicate, t.object, t.source_memory_id),
                )

    # ─── reads ────────────────────────────────────────────────────────

    async def get_active_by_key(self, user_id: str, key: str) -> Memory | None:
        cur = await self._db.conn.execute(
            "SELECT * FROM memories WHERE user_id = ? AND key = ? AND active = 1 "
            "ORDER BY updated_at DESC LIMIT 1",
            (user_id, key),
        )
        row = await cur.fetchone()
        await cur.close()
        return _row_to_memory(row) if row else None

    async def list_user_memories(
        self,
        user_id: str,
        only_active: bool = False,
    ) -> list[Memory]:
        sql = "SELECT * FROM memories WHERE user_id = ?"
        if only_active:
            sql += " AND active = 1"
        sql += " ORDER BY created_at ASC"
        cur = await self._db.conn.execute(sql, (user_id,))
        rows = await cur.fetchall()
        await cur.close()
        return [_row_to_memory(r) for r in rows]

    async def search_fts(
        self,
        user_id: str,
        query: str,
        limit: int,
    ) -> list[tuple[Memory, float]]:
        # Sanitize FTS5 query — strip control chars; quote terms with operators
        sanitized = _sanitize_fts_query(query)
        if not sanitized:
            return []
        cur = await self._db.conn.execute(
            """
            SELECT m.*, bm25(memories_fts) AS bm25_score
            FROM memories_fts
            JOIN memories m ON m.id = memories_fts.memory_id
            WHERE memories_fts MATCH ? AND memories_fts.user_id = ? AND m.active = 1
            ORDER BY bm25_score LIMIT ?
            """,
            (sanitized, user_id, limit),
        )
        rows = await cur.fetchall()
        await cur.close()
        results: list[tuple[Memory, float]] = []
        for r in rows:
            mem = _row_to_memory(r)
            # bm25() returns smaller = better; invert to make larger = better.
            score = 1.0 / (1.0 + max(0.0, float(r["bm25_score"])))
            results.append((mem, score))
        return results

    async def search_vector(
        self,
        user_id: str,
        embedding: list[float],
        limit: int,
    ) -> list[tuple[Memory, float]]:
        q_vec = np.asarray(embedding, dtype=np.float32)
        if VEC_EXTENSION_AVAILABLE:
            # sqlite-vec K-NN over the full index, then filter by user. The
            # virtual table doesn't allow filtering on UNINDEXED cols in MATCH,
            # so we over-fetch and filter, which is fine at this scale.
            cur = await self._db.conn.execute(
                """
                SELECT m.*, v.distance
                FROM vec_memories v
                JOIN memories m ON m.id = v.memory_id
                WHERE v.embedding MATCH ? AND k = ?
                  AND m.user_id = ? AND m.active = 1
                ORDER BY v.distance
                """,
                (pack_embedding(embedding), max(limit * 4, 50), user_id),
            )
            rows = await cur.fetchall()
            await cur.close()
            results: list[tuple[Memory, float]] = []
            for r in rows[:limit]:
                mem = _row_to_memory(r)
                # sqlite-vec uses cosine distance by default; convert to similarity.
                similarity = 1.0 - float(r["distance"])
                results.append((mem, similarity))
            return results

        # Fallback: brute-force cosine over user's embeddings.
        cur = await self._db.conn.execute(
            """
            SELECT m.*, v.embedding AS emb_blob
            FROM vec_memories_fallback v
            JOIN memories m ON m.id = v.memory_id
            WHERE v.user_id = ? AND m.active = 1
            """,
            (user_id,),
        )
        rows = await cur.fetchall()
        await cur.close()
        scored: list[tuple[Memory, float]] = []
        for r in rows:
            mem = _row_to_memory(r)
            vec = unpack_embedding(r["emb_blob"])
            scored.append((mem, cosine_similarity(q_vec, vec)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    async def search_keyword(
        self,
        user_id: str,
        query: str,
        limit: int,
    ) -> list[tuple[Memory, float]]:
        """Naive LIKE search — used as the v1 baseline and as a robustness
        backstop when FTS5 query sanitization strips everything."""
        terms = [t for t in query.lower().split() if len(t) > 2]
        if not terms:
            return []
        clauses = " OR ".join(
            "LOWER(value_normalized) LIKE ? OR LOWER(key) LIKE ?" for _ in terms
        )
        params: list[Any] = [user_id]
        for t in terms:
            params.extend([f"%{t}%", f"%{t}%"])
        cur = await self._db.conn.execute(
            f"SELECT * FROM memories WHERE user_id = ? AND active = 1 AND ({clauses}) "
            f"ORDER BY updated_at DESC LIMIT ?",
            (*params, limit),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [(_row_to_memory(r), 0.5) for r in rows]

    async def find_triples_by_object(
        self,
        user_id: str,
        object_value: str,
    ) -> list[Triple]:
        cur = await self._db.conn.execute(
            """
            SELECT subject, predicate, object, source_memory_id
            FROM triples
            WHERE user_id = ? AND LOWER(object) = LOWER(?)
            """,
            (user_id, object_value),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [
            Triple(r["subject"], r["predicate"], r["object"], r["source_memory_id"])
            for r in rows
        ]

    async def find_triples_by_subject(
        self,
        user_id: str,
        subject: str,
    ) -> list[Triple]:
        cur = await self._db.conn.execute(
            """
            SELECT subject, predicate, object, source_memory_id
            FROM triples
            WHERE user_id = ? AND LOWER(subject) = LOWER(?)
            """,
            (user_id, subject),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [
            Triple(r["subject"], r["predicate"], r["object"], r["source_memory_id"])
            for r in rows
        ]

    async def delete_user(self, user_id: str) -> None:
        async with self._db.write() as conn:
            cur = await conn.execute(
                "SELECT id FROM memories WHERE user_id = ?", (user_id,)
            )
            ids = [r["id"] for r in await cur.fetchall()]
            await cur.close()
            await conn.execute("DELETE FROM memories WHERE user_id = ?", (user_id,))
            await conn.execute("DELETE FROM triples WHERE user_id = ?", (user_id,))
            await conn.execute(
                "DELETE FROM memories_fts WHERE user_id = ?", (user_id,)
            )
            if VEC_EXTENSION_AVAILABLE:
                for mid in ids:
                    await conn.execute(
                        "DELETE FROM vec_memories WHERE memory_id = ?", (mid,)
                    )
            else:
                await conn.execute(
                    "DELETE FROM vec_memories_fallback WHERE user_id = ?", (user_id,)
                )

    async def delete_session(self, session_id: str) -> None:
        async with self._db.write() as conn:
            cur = await conn.execute(
                "SELECT id, user_id FROM memories WHERE source_session_id = ?",
                (session_id,),
            )
            rows = await cur.fetchall()
            await cur.close()
            for r in rows:
                mid = r["id"]
                await conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
                await conn.execute(
                    "DELETE FROM memories_fts WHERE memory_id = ?", (mid,)
                )
                await conn.execute(
                    "DELETE FROM triples WHERE source_memory_id = ?", (mid,)
                )
                if VEC_EXTENSION_AVAILABLE:
                    await conn.execute(
                        "DELETE FROM vec_memories WHERE memory_id = ?", (mid,)
                    )
                else:
                    await conn.execute(
                        "DELETE FROM vec_memories_fallback WHERE memory_id = ?",
                        (mid,),
                    )


def _sanitize_fts_query(query: str) -> str:
    """Reduce arbitrary user text to a safe FTS5 OR-query.

    FTS5 has many operator tokens (NEAR, OR, AND, NOT, parens, quotes, ^, -, +,
    *, :). Easiest correct path is to keep only alphanumeric + underscore +
    inner hyphens, join the surviving tokens with OR.
    """
    cleaned: list[str] = []
    for raw in query.split():
        token = "".join(c for c in raw if c.isalnum() or c == "_")
        token = token.strip("_-")
        # Skip FTS5 reserved keywords that would parse as operators.
        if token.upper() in {"AND", "OR", "NOT", "NEAR"}:
            continue
        if len(token) >= 2:
            cleaned.append(token)
    return " OR ".join(cleaned)
