"""FastAPI application entrypoint.

Run with: uv run fastapi dev recourse/api/main.py
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from recourse.api import health
from recourse.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Recourse API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    return app


app = create_app()
