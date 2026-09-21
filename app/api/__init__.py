"""API endpoints package."""
from .manifest import router as manifest_router
from .subtitles import router as subtitles_router
from .download import router as download_router
from .configure import router as configure_router

__all__ = ["manifest_router", "subtitles_router", "download_router", "configure_router"]
