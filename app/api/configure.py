"""Web configuration router serving the 1-click install interface."""

from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()

HTML_PATH = Path(__file__).resolve().parent.parent.parent / "static" / "index.html"


@router.get("/")
@router.get("/configure")
async def serve_configure_page():
    """Serve the modern configuration web page."""
    return FileResponse(HTML_PATH, media_type="text/html")
