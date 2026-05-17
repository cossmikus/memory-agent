"""FastAPI application factory + lifespan.

The lifespan context manager:
- Loads settings (validation fails fast on missing required env).
- Opens the SQLite connection and runs migrations.
- Constructs the MemoryService with all its adapters wired up.
- Stores the service on app.state for dependency injection.
- Closes the DB on shutdown.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from memory_service import __version__
from memory_service.adapters.embeddings.openai_embed import (
    NullEmbedder,
    OpenAIEmbedder,
)
from memory_service.adapters.llm.openai_llm import OpenAILLM
from memory_service.adapters.llm.regex_llm import RegexLLM
from memory_service.adapters.storage.db import Database
from memory_service.adapters.storage.memories_repo import MemoriesRepo
from memory_service.adapters.storage.turns_repo import TurnsRepo
from memory_service.api.errors import register_error_handlers
from memory_service.api.router import register_routes
from memory_service.config import get_settings
from memory_service.core.logging import configure_logging, get_logger
from memory_service.domain.service import MemoryService

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    log.info("startup", version=__version__, db_path=str(settings.db_path))

    if not settings.has_openai:
        log.warning(
            "no_openai_key",
            note=(
                "Service will run with regex extractor + null embedder. "
                "Recall quality will be degraded. Set OPENAI_API_KEY for full features."
            ),
        )

    db = Database(path=settings.db_path, embedding_dim=settings.embedding_dim)
    await db.open()
    app.state.db = db

    turns_repo = TurnsRepo(db)
    memories_repo = MemoriesRepo(db)

    if settings.has_openai:
        embedder = OpenAIEmbedder(
            api_key=settings.openai_api_key or "",
            model=settings.embedding_model,
            dim=settings.embedding_dim,
        )
        llm = OpenAILLM(
            api_key=settings.openai_api_key or "",
            model=settings.extraction_model,
        )
        reranker = llm  # the OpenAI LLM doubles as reranker
    else:
        embedder = NullEmbedder(dim=settings.embedding_dim)
        llm = RegexLLM()
        reranker = None

    service = MemoryService(
        turns_repo=turns_repo,
        memories_repo=memories_repo,
        extractor=llm,
        embedder=embedder,
        reranker=reranker,
        settings=settings,
    )
    app.state.service = service
    app.state.ready = True
    log.info("ready")

    try:
        yield
    finally:
        app.state.ready = False
        await db.close()
        log.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Memory Service",
        version=__version__,
        description="Memory service for an AI agent — extracts structured knowledge "
        "from conversations and serves recall queries.",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def _size_limit(request, call_next):
        # Reject obviously oversized requests before parsing.
        cl = request.headers.get("content-length")
        if cl is not None and cl.isdigit() and int(cl) > settings.max_request_bytes:
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=413,
                content={"error": "payload_too_large"},
            )
        return await call_next(request)

    register_error_handlers(app)
    register_routes(app)
    return app


app = create_app()
