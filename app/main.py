"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.configure import router as configure_router
from .api.download import router as download_router
from .api.manifest import router as manifest_router
from .api.subtitles import router as subtitles_router
from .cache.manager import cache_manager
from .config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("stremio-zhsubtitle")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle management."""
    settings.ensure_directories()
    cache_manager.clean_expired()
    logger.info(f"🚀 Starting {settings.ADDON_NAME} v{settings.ADDON_VERSION} on port {settings.PORT}")
    yield
    logger.info("🛑 Shutting down stremio-zhsubtitle")


app = FastAPI(
    title=settings.ADDON_NAME,
    version=settings.ADDON_VERSION,
    description=settings.ADDON_DESCRIPTION,
    lifespan=lifespan
)

# Enable CORS for all origins (required by Stremio web and mobile clients)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static assets
static_dir = Path(__file__).resolve().parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Mount API Routers
app.include_router(configure_router)
app.include_router(manifest_router)
app.include_router(download_router)
app.include_router(subtitles_router)


def run():
    """Run server via uvicorn programmatically."""
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=False
    )


if __name__ == "__main__":
    run()
