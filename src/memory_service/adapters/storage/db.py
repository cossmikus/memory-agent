"""Async SQLite connection manager.

We use a single long-lived connection guarded by an asyncio.Lock for writes —
SQLite serializes writers anyway, so this matches the engine's semantics and
keeps WAL behavior consistent. Reads share the same connection (aiosqlite is
already serialized internally on a single connection).
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from memory_service.adapters.storage.sqlite_vec import init_vec
from memory_service.core.logging import get_logger

log = get_logger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Database:
    def __init__(self, path: Path, embedding_dim: int) -> None:
        self.path = path
        self.embedding_dim = embedding_dim
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path, isolation_level=None)
        self._conn.row_factory = aiosqlite.Row

        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        await self._conn.executescript(schema_sql)
        await init_vec(self._conn, self.embedding_dim)

        # Record the embedding dim. Refuse to start if mismatched (the index
        # is dim-specific and silently mixing dims would corrupt search).
        await self._enforce_embedding_dim()

        log.info("db_opened", path=str(self.path), dim=self.embedding_dim)

    async def _enforce_embedding_dim(self) -> None:
        assert self._conn is not None
        cur = await self._conn.execute(
            "SELECT value FROM meta WHERE key = 'embedding_dim'"
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            await self._conn.execute(
                "INSERT INTO meta (key, value) VALUES ('embedding_dim', ?)",
                (str(self.embedding_dim),),
            )
            return
        existing = int(row["value"])
        if existing != self.embedding_dim:
            raise RuntimeError(
                f"Embedding dimension mismatch: DB was built with dim={existing}, "
                f"service configured for dim={self.embedding_dim}. "
                f"Reset the volume or change EMBEDDING_DIM."
            )

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            log.info("db_closed")

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not opened")
        return self._conn

    @asynccontextmanager
    async def write(self) -> AsyncIterator[aiosqlite.Connection]:
        """Acquire the write lock and yield the connection inside a transaction."""
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
                await self.conn.execute("COMMIT")
            except BaseException:
                await self.conn.execute("ROLLBACK")
                raise
