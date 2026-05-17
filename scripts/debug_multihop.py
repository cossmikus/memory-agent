"""Inspect what the LLM actually extracted for the multi_hop scenario."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp(prefix="dbg_")) / "memory.db")
os.environ.setdefault("LOG_LEVEL", "WARNING")

from httpx import ASGITransport, AsyncClient  # noqa: E402


async def main() -> None:
    from memory_service.config import reset_settings_cache
    reset_settings_cache()
    from memory_service.main import create_app

    fix = json.loads((ROOT / "fixtures" / "scenarios" / "03_multi_hop.json").read_text())

    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            for turn in fix["turns"]:
                r = await c.post(
                    "/turns",
                    json={
                        "session_id": turn["session_id"],
                        "user_id": fix["user_id"],
                        "messages": turn["messages"],
                        "timestamp": turn["timestamp"],
                        "metadata": {},
                    },
                )
                assert r.status_code == 201, r.text

            service = app.state.service
            repo = service._memories
            user_id = fix["user_id"]

            # Direct calls into the recall pipeline.
            embedder = service._embedder
            qvec = await embedder.embed("Biscuit lives in which city?")
            bm25 = await repo.search_fts(user_id, "Biscuit lives in which city", 30)
            vec = await repo.search_vector(user_id, qvec, 30)
            print("===== BM25 hits =====")
            for m, s in bm25:
                print(f"  score={s:.4f}  type={m.type.value}  key={m.key}  value={m.value!r}")
            print("\n===== VECTOR hits =====")
            for m, s in vec:
                print(f"  score={s:.4f}  type={m.type.value}  key={m.key}  value={m.value!r}")

            # Run full recall pipeline directly.
            recall = service._recall
            scored = await recall.recall(user_id, "Biscuit lives in which city?")
            print("\n===== AFTER RECALL PIPELINE (post-multihop+filter) =====")
            for sm in scored:
                print(f"  score={sm.score:.4f}  src={sm.source}  type={sm.memory.type.value}  key={sm.memory.key}  value={sm.memory.value!r}")

            print("\n===== /recall response =====")
            rec = (await c.post(
                "/recall",
                json={"query": "Biscuit lives in which city?", "session_id": "dbg",
                      "user_id": user_id, "max_tokens": 512},
            )).json()
            print(rec["context"])


if __name__ == "__main__":
    asyncio.run(main())
