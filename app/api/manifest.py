"""Stremio Addon Manifest endpoints."""

import base64
import json
from typing import Any, Dict

from fastapi import APIRouter, Request

from ..config import settings

router = APIRouter()


def build_manifest(config_data: Dict[str, Any] = None) -> Dict[str, Any]:
    """Generate Stremio Addon manifest with optional user configuration indicators."""
    addon_name = settings.ADDON_NAME
    if config_data:
        lang = config_data.get("lang", "")
        if lang == "chs":
            addon_name += " [简]"
        elif lang == "cht":
            addon_name += " [繁]"
        elif lang == "bilingual":
            addon_name += " [双语]"

    return {
        "id": settings.ADDON_ID,
        "version": settings.ADDON_VERSION,
        "name": addon_name,
        "description": settings.ADDON_DESCRIPTION,
        "resources": [
            {
                "name": "subtitles",
                "types": ["movie", "series"],
                "extra": [
                    {"name": "videoHash", "isRequired": False},
                    {"name": "videoSize", "isRequired": False},
                    {"name": "filename", "isRequired": False}
                ]
            }
        ],
        "types": ["movie", "series"],
        "catalogs": [],
        "behaviorHints": {
            "configurable": True,
            "configurationRequired": False
        }
    }


def parse_config(config_str: str) -> Dict[str, Any]:
    """Decode configuration from URL-safe base64 or JSON string."""
    if not config_str:
        return {}
    try:
        # Pad base64 string if necessary
        padded = config_str + "=" * ((4 - len(config_str) % 4) % 4)
        decoded_bytes = base64.urlsafe_b64decode(padded)
        return json.loads(decoded_bytes.decode("utf-8"))
    except Exception:
        try:
            return json.loads(config_str)
        except Exception:
            return {}


@router.api_route("/manifest.json", methods=["GET", "HEAD"])
async def get_default_manifest(request: Request):
    """Serve default Addon Manifest."""
    return build_manifest()


@router.api_route("/{config}/manifest.json", methods=["GET", "HEAD"])
async def get_configured_manifest(config: str, request: Request):
    """Serve customized Addon Manifest for configured installations."""
    config_data = parse_config(config)
    return build_manifest(config_data)

