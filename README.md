# Stremio Chinese Subtitles Addon (`stremio-zhsubtitle`)

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Stremio Addon](https://img.shields.io/badge/Stremio-v3%20Addon-purple.svg)](https://stremio.com)
[![Render](https://img.shields.io/badge/Hosted%20on-Render-46e3b7.svg)](https://render.com)

> A high-performance, resilient Chinese subtitle addon built specifically for **Stremio** and **Harbor**, aggregating premier sources from **Zimuku (srtku.com)** and **SubHD (subhd.tv)**.  
> Features smart ASS/SSA effect tag stripping, zero-garble UTF-8 standardization, OpenSubtitles v3 styled release tags (`WEB-DL`, `BluRay`, `1080p`, `x265`), pure-ASCII variant naming, a two-tier caching engine, and a modern Web UI for seamless 1-click installation across all devices.

---

## ⚡ Quick Install (No Hosting Required)

You do **not** need to deploy your own server! You can directly install the public instance into **Stremio** or **Harbor**:

### Option A: 1-Click Install via Stremio Web / Desktop
Click the button below to open Stremio and install in one click:

[![Install on Stremio](https://static.strem.io/addons/install-button.png)](https://staging.strem.io#/?addon=https%3A%2F%2Fstremio-zhsubtitle.onrender.com%2Fmanifest.json)

*(Or customize language priorities first on the **[Configuration Page](https://stremio-zhsubtitle.onrender.com/configure)**)*

---

### Option B: Manual Installation via Manifest URL
Works for **Stremio (Desktop / Android / Android TV / iOS)** and **Harbor**:

1. Copy the public Manifest URL:
   ```text
   https://stremio-zhsubtitle.onrender.com/manifest.json
   ```
2. **In Stremio**:
   - Navigate to the **Addons** tab (the puzzle piece icon 🧩).
   - Paste the URL into the search bar at the top and hit <kbd>Enter</kbd>.
   - Click **Install** to synchronize across all devices linked to your account.
3. **In Harbor Desktop**:
   - Open **Settings** (⚙️) -> **Addons** (or **Player** -> **Subtitles**).
   - Paste the Manifest URL into the **Add Addon** input and press **Add**.
   - Harbor's stream auto-match algorithm will immediately recognize release tags (`WEB-DL`, `BluRay`, `1080p`, etc.) and score subtitles for playback.

> [!NOTE]
> If your Render deployment has a custom subdomain (e.g. `https://stremio-zhsubtitle-xxxx.onrender.com`), replace `stremio-zhsubtitle.onrender.com` with your own service URL.

---

## ✨ Key Features

1. **Dual Premier Chinese Subtitle Providers**:
   - **SubHD**: Supports precise series matching via IMDb ID, automatic download token acquisition, and fallback mirrors (`subhd.tv`, `subhd.me`, `subhd.one`, `subhd.cc`).
   - **Zimuku**: Pure-Python pixel-template solver for 5-digit BMP CAPTCHAs (zero heavy OCR dependencies), automatic Yunsuo WAF challenge bypass, and mirror fallbacks (`srtku.com`, `zmk.pw`, `zimuku.org`).
2. **OpenSubtitles v3 Styled Release Tags**:
   - Formats track labels with rich video tags (e.g., `Title · 2026 · WEB-DL · 1080p · x265 · 10bit · [Bilingual SubHD]`).
   - Full compatibility with Harbor's `streamMatchScore` matching engine and Stremio's native subtitle selector.
3. **Zero Mojibake & ASS Tag Sanitization**:
   - Automatically probes byte encodings (`utf-8`, `gb18030`, `gbk`, `big5`, `utf-16`) and converts them into standardized UTF-8 `.srt`.
   - Strips formatting tags (`{\pos(x,y)}`, `{\b1}`, `{\an8}`, and vector drawing commands `{\p1}...{\p0}`), eliminating garbled text and overlay clutter on Android TV, Apple TV, and Web players.
4. **Clean ASCII Variant Naming**:
   - Generates 100% clean ASCII release filenames (e.g. `Title.2026.WEB-DL.1080p.x265.[Bilingual.SubHD].srt`).
   - Prevents browser URL percent-encoding (`%XX`) from cluttering the variant tooltip in Stremio's player interface.
5. **Smart Episode Matching & Quality Scoring**:
   - Extracts archives in-memory (ZIP, 7z, RAR) and matches target episodes using advanced regular expressions (`S01E14`, `EP14`, `第14集`).
   - Configurable language sorting: Bilingual first (Chinese + English), Simplified Chinese only, or Traditional Chinese only.
6. **Two-Tier Caching (<5ms Response Time)**:
   - **SQLite Search Cache**: Caches query results for 12 hours to prevent redundant scraping and upstream rate-limiting.
   - **Disk LRU Subtitle Cache**: Persists cleaned `.srt` files on disk for instant subsequent streaming.
   - **Single-Flight Lock**: Coalesces concurrent requests for the same media item to avoid upstream load spikes.
7. **Responsive Configuration Web UI**:
   - Sleek dark-mode settings page with one-click `stremio://` protocol installation for desktop and mobile.
   - Quick-copy button for Manifest URLs on Android TV devices.

---

## 🚀 Self-Hosting & Deployment

If you prefer to host your own dedicated private instance, choose one of the following methods:

### Option 1: Render.com + UptimeRobot (100% Free 24/7 Cloud Hosting)

Deploy your own cloud instance without needing a credit card or local server:

1. **Deploy on Render**:
   - Fork this repository on GitHub.
   - Log into [Render Dashboard](https://dashboard.render.com/) and click **New +** -> **Web Service**.
   - Connect your forked GitHub repository.
   - Configure the service:
     - **Name**: `stremio-zhsubtitle` (or any custom name)
     - **Language / Runtime**: `Docker` (Render automatically detects the repository's `Dockerfile`)
     - **Instance Type**: `Free` ($0/mo, no credit card required)
   - Click **Deploy Web Service**.
2. **Keep-Alive with UptimeRobot** *(Prevents 15-min idle spin-down)*:
   - Register a free account at [UptimeRobot](https://uptimerobot.com).
   - Click **Add New Monitor**:
     - **Monitor Type**: `HTTP(s)`
     - **Friendly Name**: `Stremio Subtitles Keepalive`
     - **URL**: `https://<your-render-service>.onrender.com/manifest.json`
     - **Monitoring Interval**: `5 minutes` or `10 minutes`
   - Click **Create Monitor**. Your container will now remain active 24/7 without cold-boot delays.

---

### Option 2: Docker Compose (VPS / Home Server / NAS)

Create or use the included `docker-compose.yml`:

```yaml
version: "3.8"

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
      - UPSTREAM_PROXY="" # Optional HTTP/SOCKS5 proxy if hosted outside Asia
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
3. Open `http://localhost:7000` in your web browser to configure and install the addon into your desktop Stremio or Harbor client.

---

## ⚙️ Environment Variables

| Variable          | Default                        | Description                                             |
| :---------------- | :----------------------------- | :------------------------------------------------------ |
| `PORT`            | `7000` (Docker default `7860`) | Server HTTP listening port                              |
| `HOST`            | `0.0.0.0`                      | Server listening host address                           |
| `CACHE_DIR`       | `./data/cache`                 | Path for SQLite cache and subtitle storage              |
| `CACHE_TTL_HOURS` | `12`                           | Search results cache expiration in hours                |
| `UPSTREAM_PROXY`  | _(empty)_                      | Optional HTTP or SOCKS5 proxy URL for upstream requests |
| `SUBHD_BASE_URL`  | `https://subhd.tv`             | Primary SubHD base URL                                  |
| `ZIMUKU_BASE_URL` | `https://srtku.com`            | Primary Zimuku base URL                                 |

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
- Release tag extraction and OpenSubtitles v3 formatting

---

## 🤖 AI Disclosure

This project was designed, developed, and tested with the assistance of Artificial Intelligence:

- **Architecture & Implementation**: Developed in collaboration with Google DeepMind's **Antigravity** agentic AI pair programmer.
- **Algorithms**: The pure-Python 5-digit BMP CAPTCHA template matching engine, ASS-to-SRT normalization parser, and Stremio v3 protocol integration were synthesized and validated through AI-assisted pair-programming workflows.
- **Human Oversight**: All design decisions, security validations, and feature specifications were guided, audited, and tested by human maintainers.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
