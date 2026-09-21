"""Application configuration and environment settings."""

import os
from pathlib import Path
from typing import List


class Settings:
    """Application settings loaded from environment variables."""

    # Server binding
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "7000"))

    # Caching configuration
    CACHE_DIR: Path = Path(os.getenv("CACHE_DIR", "./data/cache"))
    CACHE_TTL_HOURS: int = int(os.getenv("CACHE_TTL_HOURS", "12"))
    MAX_CACHE_SIZE_MB: int = int(os.getenv("MAX_CACHE_SIZE_MB", "2048"))

    # Optional proxy for external requests
    UPSTREAM_PROXY: str = os.getenv("UPSTREAM_PROXY", "").strip()

    # Provider endpoints & mirrors
    SUBHD_BASE_URL: str = os.getenv("SUBHD_BASE_URL", "https://subhd.tv").rstrip("/")
    SUBHD_FALLBACKS: List[str] = [
        url.strip().rstrip("/")
        for url in os.getenv("SUBHD_FALLBACKS", "https://subhd.me,https://subhd.one,https://subhd.cc").split(",")
        if url.strip()
    ]

    ZIMUKU_BASE_URL: str = os.getenv("ZIMUKU_BASE_URL", "https://srtku.com").rstrip("/")
    ZIMUKU_FALLBACKS: List[str] = [
        url.strip().rstrip("/")
        for url in os.getenv("ZIMUKU_FALLBACKS", "https://zmk.pw,https://zimuku.org").split(",")
        if url.strip()
    ]

    # Cinemeta metadata service URL
    CINEMETA_URL: str = os.getenv("CINEMETA_URL", "https://v3-cinemeta.strem.io").rstrip("/")

    # Addon identification
    ADDON_ID: str = "org.stremio.zhsubtitle"
    ADDON_NAME: str = "Chinese Subtitles (Zimuku & SubHD)"
    ADDON_VERSION: str = "1.0.0"
    ADDON_DESCRIPTION: str = (
        "High-quality Chinese subtitles aggregated from Zimuku & SubHD with zero-garble UTF-8 output."
    )

    def ensure_directories(self) -> None:
        """Ensure necessary cache directories exist."""
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (self.CACHE_DIR / "subtitles").mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_directories()
