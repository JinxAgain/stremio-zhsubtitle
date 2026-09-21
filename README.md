# Stremio Chinese Subtitles Addon (`stremio-zhsubtitle`)

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Stremio Addon](https://img.shields.io/badge/Stremio-v3%20Addon-purple.svg)](https://stremio.com)

> A high-performance, resilient Chinese subtitle addon built specifically for **Stremio**, aggregating premier sources from **Zimuku (srtku.com)** and **SubHD (subhd.tv)**.  
> Features smart ASS/SSA effect tag stripping, zero-garble UTF-8 standardization, pure-ASCII variant naming, a two-tier caching engine, and a modern Web UI for seamless 1-click installation across all devices.

---

## ✨ Key Features

1. **Dual Premier Chinese Subtitle Providers**:
   - **SubHD**: Supports precise series matching via IMDb ID, automatic download token acquisition, and fallback mirrors (`subhd.tv`, `subhd.me`, `subhd.one`, `subhd.cc`).
   - **Zimuku**: Pure-Python pixel-template solver for 5-digit BMP CAPTCHAs (zero heavy OCR dependencies), automatic Yunsuo WAF challenge bypass, and mirror fallbacks (`srtku.com`, `zmk.pw`, `zimuku.org`).
2. **Zero Mojibake & ASS Tag Sanitization**:
   - Automatically probes byte encodings (`utf-8`, `gb18030`, `gbk`, `big5`, `utf-16`) and converts them into standardized UTF-8 `.srt`.
   - Strips formatting tags (`{\pos(x,y)}`, `{\b1}`, `{\an8}`, and vector drawing commands `{\p1}...{\p0}`), eliminating garbled text and overlay clutter on Android TV, Apple TV, and Web players.
3. **Clean ASCII Variant Naming**:
   - Generates 100% clean ASCII release filenames (e.g. `[Bilingual.SubHD].Community.S01E14.1080p.BluRay.srt`).
   - Prevents browser URL percent-encoding (`%XX`) from cluttering the variant tooltip in Stremio's player interface.
4. **Smart Episode Matching & Quality Scoring**:
   - Extracts archives in-memory (ZIP, 7z, RAR) and matches target episodes using advanced regular expressions (`S01E14`, `EP14`, `第14集`).
   - Configurable language sorting: Bilingual first (Chinese + English), Simplified Chinese only, or Traditional Chinese only.
5. **Two-Tier Caching (<5ms Response Time)**:
   - **SQLite Search Cache**: Caches query results for 12 hours to prevent redundant scraping and upstream rate-limiting.
   - **Disk LRU Subtitle Cache**: Persists cleaned `.srt` files on disk for instant subsequent streaming.
   - **Single-Flight Lock**: Coalesces concurrent requests for the same media item to avoid upstream load spikes.
6. **Responsive Configuration Web UI**:
   - Sleek dark-mode settings page with one-click `stremio://` protocol installation for desktop and mobile.
   - Quick-copy button for Manifest URLs on Android TV devices.

---

## 🚀 Deployment Options

### Option 1: Hugging Face Spaces (Free 24/7 Cloud Hosting - Recommended)

1. Go to [Hugging Face Spaces](https://huggingface.co/spaces) and click **Create new Space**.
2. Give your space a name and select **Docker** (Blank SDK).
3. Push this repository to your Space's Git remote:
   ```bash
   git remote add space https://huggingface.co/spaces/<your-username>/<space-name>
   git push space main
   ```
4. Once the build completes, you will have a free, permanent HTTPS domain:
   `https://<your-username>-<space-name>.hf.space`
5. Open that URL in your browser and click **[Install to Stremio]** to sync the addon across all devices logged into your Stremio account!
6. *(Optional)* Set up a free monitor (e.g., [UptimeRobot](https://uptimerobot.com)) to ping `https://<your-username>-<space-name>.hf.space/manifest.json` every 10 minutes to prevent the container from sleeping.

---

### Option 2: Docker Compose (VPS / Home Server / NAS)

Create or use the included `docker-compose.yml`:

```yaml
version: '3.8'

services:
  stremio-zhsubtitle:
    image: stremio-zhsubtitle:latest
    build: .
    container_name: stremio-zhsubtitle
    restart: unless-stopped
    ports:
      - "7000:7000"
    environment:
      - PORT=7000
      - HOST=0.0.0.0
      - CACHE_DIR=/app/data/cache
      - CACHE_TTL_HOURS=12
      - UPSTREAM_PROXY=""   # Optional HTTP/SOCKS5 proxy if hosted outside Asia
    volumes:
      - ./data:/app/data
```

Start the container:
```bash
docker-compose up -d
```

Access the configuration page at `http://<your-server-ip>:7000`.

---

### Option 3: Local Execution (Python 3.11+)

1. Clone the repository and install dependencies:
   ```bash
   git clone https://github.com/JinxAgain/stremio-zhsubtitle.git
   cd stremio-zhsubtitle
   pip install -r requirements.txt
   ```
2. Start the server:
   ```bash
   python -m app.main
   ```
3. Open `http://localhost:7000` in your web browser to configure and install the addon.

> **Note for Windows Users with IDM (Internet Download Manager):**  
> If you have IDM installed, ensure `127.0.0.1` and `localhost` are added to IDM's exception list (*Options → File Types → "Don't start downloading automatically from the following addresses"*) to prevent IDM from intercepting subtitle streams meant for the Stremio player.

---

## ⚙️ Environment Variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `PORT` | `7000` (Docker default `7860`) | Server HTTP listening port |
| `HOST` | `0.0.0.0` | Server listening host address |
| `CACHE_DIR` | `./data/cache` | Path for SQLite cache and subtitle storage |
| `CACHE_TTL_HOURS` | `12` | Search results cache expiration in hours |
| `UPSTREAM_PROXY` | *(empty)* | Optional HTTP or SOCKS5 proxy URL for upstream requests |
| `SUBHD_BASE_URL` | `https://subhd.tv` | Primary SubHD base URL |
| `ZIMUKU_BASE_URL` | `https://srtku.com` | Primary Zimuku base URL |

---

## 🧪 Automated Testing

Run the comprehensive unit test suite:
```bash
python -m pytest tests -v
```

The test suite covers:
- Stremio Manifest and configuration endpoints
- Subtitle extraction and in-memory archive parsing
- Multi-charset encoding detection and ASS/VTT to SRT conversion
- Scoring engine and episode matching heuristics
- Subtitle download routing and two-tier cache integrity

---

## 🤖 AI Disclosure

This project was designed, developed, and tested with the assistance of Artificial Intelligence:
- **Architecture & Implementation**: Developed in collaboration with Google DeepMind's **Antigravity** agentic AI pair programmer.
- **Algorithms**: The pure-Python 5-digit BMP CAPTCHA template matching engine, ASS-to-SRT normalization parser, and Stremio v3 protocol integration were synthesized and validated through AI-assisted pair-programming workflows.
- **Human Oversight**: All design decisions, security validations, and feature specifications were guided, audited, and tested by human maintainers.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
