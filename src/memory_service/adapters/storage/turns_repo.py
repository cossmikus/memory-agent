"""Turns repository — persists raw conversation turns + messages."""
from __future__ import annotations

import json
from datetime import datetime

from memory_service.adapters.storage.db import Database
from memory_service.domain.models import Message, Turn


def _iso(ts: datetime) -> str:
    return ts.astimezone().isoformat() if ts.tzinfo else ts.isoformat() + "Z"


class TurnsRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def save_turn(self, turn: Turn) -> None:
        async with self._db.write() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO turns
                    (id, session_id, user_id, timestamp, metadata_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    turn.id,
                    turn.session_id,
                    turn.user_id,
                    _iso(turn.timestamp),
                    json.dumps(turn.metadata, ensure_ascii=False),
                ),
            )
            await conn.execute(
                "DELETE FROM messages WHERE turn_id = ?", (turn.id,)
            )
            for i, msg in enumerate(turn.messages):
                await conn.execute(
                    """
                    INSERT INTO messages (id, turn_id, role, name, content, position)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{turn.id}:{i}",
                        turn.id,
                        msg.role,
                        msg.name,
                        msg.content,
                        i,
                    ),
                )

    async def get_turn(self, turn_id: str) -> Turn | None:
        cur = await self._db.conn.execute(
            "SELECT id, session_id, user_id, timestamp, metadata_json FROM turns WHERE id = ?",
            (turn_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        if not row:
            return None

        mcur = await self._db.conn.execute(
            "SELECT role, name, content, position FROM messages "
            "WHERE turn_id = ? ORDER BY position",
            (turn_id,),
        )
        mrows = await mcur.fetchall()
        await mcur.close()

        return Turn(
            id=row["id"],
            session_id=row["session_id"],
            user_id=row["user_id"],
            timestamp=datetime.fromisoformat(row["timestamp"].rstrip("Z")),
            metadata=json.loads(row["metadata_json"] or "{}"),
            messages=[
                Message(role=m["role"], name=m["name"], content=m["content"], position=m["position"])
                for m in mrows
            ],
        )

    async def get_turn_snippet(self, turn_id: str, max_chars: int = 200) -> str:
        cur = await self._db.conn.execute(
            "SELECT role, content FROM messages WHERE turn_id = ? ORDER BY position",
            (turn_id,),
        )
        rows = await cur.fetchall()
        await cur.close()
        parts = []
        for r in rows:
            piece = f"{r['role']}: {r['content']}".strip()
            parts.append(piece)
            if sum(len(p) for p in parts) > max_chars:
                break
        text = " | ".join(parts)
        return text if len(text) <= max_chars else text[: max_chars - 1] + "…"

    async def delete_session(self, session_id: str) -> None:
        async with self._db.write() as conn:
            await conn.execute(
                "DELETE FROM turns WHERE session_id = ?", (session_id,)
            )

    async def delete_user(self, user_id: str) -> None:
        async with self._db.write() as conn:
            await conn.execute("DELETE FROM turns WHERE user_id = ?", (user_id,))
