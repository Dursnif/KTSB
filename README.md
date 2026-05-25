# Kåre The Smart Butler

![Version](https://img.shields.io/badge/version-v1.0.1--beta-blue)
![License](https://img.shields.io/badge/license-Personal%20Use-green)
![Docker](https://img.shields.io/badge/docker-ghcr.io%2Fdursnif%2Fkaare-blue)

**KTSB** is a local AI operating system for your home. Natural-language voice and text commands,
smart home control via Home Assistant, local LLM inference via Ollama — running entirely on your
own hardware.

> The GUI supports Norwegian, English, and German. Voice language, assistant name, and hot-word are configurable via settings.

---

## What Kåre does

- Accepts voice and text commands in natural language
- Controls your smart home via Home Assistant (lights, locks, climate, media, scenes)
- Answers questions and executes tasks with local LLMs
- Remembers your interactions (short-term and long-term memory, per user)
- Runs a semantic search engine over a local Wikipedia corpus (optional)
- Multi-user with PIN-based access and role-based permissions
- Onboarding wizard guides first-time setup — no config files to edit manually

---

## Requirements

Kåre runs on **Linux, Windows, and macOS** via Docker.

| | Minimum | Recommended |
|---|---|---|
| **Docker** | Docker Desktop (Win/Mac) or Docker Engine (Linux) + Compose v2 | Latest stable |
| **RAM** | 8 GB | 16 GB+ |
| **Disk** | 20 GB free | 60 GB+ (model storage) |
| **GPU** | — (CPU works, slow) | NVIDIA 8 GB+ VRAM |

### Platform notes

**Linux** — Docker Engine + Compose. For GPU: install the
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html).

**Windows** — [Docker Desktop](https://www.docker.com/products/docker-desktop/) with the WSL 2 backend.
For GPU: NVIDIA driver 535+ and the NVIDIA Container Toolkit for WSL 2 are required.

**macOS** — [Docker Desktop](https://www.docker.com/products/docker-desktop/).
GPU acceleration for LLMs is not available inside Docker containers on macOS.
Use a cloud LLM provider (Settings → LLM → Cloud) or run Ollama natively on macOS
and point Kåre at it.

**No GPU?** Kåre still runs fully. Configure a cloud LLM provider under Settings → LLM → Cloud
instead of local Ollama models.

---

## Quick start

**Linux / macOS:**
```bash
git clone https://github.com/dursnif/KTSB.git
cd KTSB
cp .env.example .env        # edit if needed
docker compose up -d
```

**Windows (PowerShell):**
```powershell
git clone https://github.com/dursnif/KTSB.git
cd KTSB
copy .env.example .env      # edit if needed
docker compose up -d
```

Then open your browser and go to:

```
http://localhost:5173
```

The first start pulls Docker images (~5 GB) and initialises default configuration.
Allow 2–3 minutes on first run.

> **Port 5173** is the direct frontend address and works on all platforms.
> If you set up a domain in `.env` (`KAARE_DOMAIN=...`), Caddy also serves Kåre on port 80/443.

---

## First run — onboarding wizard

When you open the UI for the first time, the onboarding wizard walks you through:

1. **Profile** — assistant name, hot-word, language, timezone, location
2. **User** — create your admin account with a PIN
3. **Distribution** — choose a service profile (see below)
4. **LLM source** — use the built-in Ollama (~10 GB download) or point to your own LLM server
5. **Integrations** — connect Home Assistant, MQTT, and other services (optional, skip for now)
6. **Done** — start chatting

All settings can be changed later in the admin GUI (Settings).

---

## Profiles

Set `COMPOSE_PROFILES` in `.env` to enable optional services (combine with commas):

| Profile | What it adds | Typical use |
|---|---|---|
| `ollama` | Built-in Ollama LLM server (~10 GB image) | Default for new users |
| `medium` | BGE-M3 embedding (semantic memory, wiki search, ~3 GB) | Most setups |
| `full` | Voice bridge — speech-to-text and text-to-speech | Full voice control |

```dotenv
# .env — default (built-in Ollama + embedding)
COMPOSE_PROFILES=ollama,medium

# If you have your own Ollama/vLLM running elsewhere:
COMPOSE_PROFILES=medium
```

> Run `setup.sh` (Linux/macOS) or `setup.ps1` (Windows) to auto-detect your hardware
> and generate the right `.env` — including GPU support for Ollama.

---

## Models

Kåre uses **Ollama** for local LLM inference. Pull models through the admin GUI
(Settings → LLM → Manage models) — no command line needed.

### Recommended models per hardware tier

| VRAM | Main model | Supporting models | Notes |
|---|---|---|---|
| 6–8 GB | `qwen2.5:7b` | Same model for all roles | One model, low memory |
| 12–16 GB | `qwen3:8b` | `qwen3:8b` | Better reasoning |
| 24 GB+ | `qwen3:14b` or larger | Separate 7–8B for agents | Best results |

**Tips:**
- Configure the same model for all roles (Kåre, Miss Kåre, Library, Pettersmart) to reduce VRAM usage.
- Qwen3 models support extended thinking — enable it per role in Settings → LLM.
- Any Ollama-compatible model works. The model names above are `ollama pull` names.

### Embedding (BGE-M3)

Required for semantic memory and wiki search (`medium`/`full` profiles). The model (~2 GB) is
automatically downloaded from HuggingFace on first start. No manual action needed.

---

## HTTPS and voice input

Browsers block microphone access on plain HTTP. To use voice input, Kåre must be served over
HTTPS.

Kåre includes **Caddy**, which obtains a free Let's Encrypt certificate automatically:

1. Get a free subdomain at [DuckDNS](https://www.duckdns.org/) — e.g. `mykaare.duckdns.org`
2. Point it to your machine's public IP
3. Open ports **80** and **443** in your router/firewall
4. Set `KAARE_DOMAIN=mykaare.duckdns.org` in `.env`
5. Restart: `docker compose up -d`

For local use without voice input, `KAARE_DOMAIN=localhost` (the default) serves HTTP on port 80.

---

## Integrations

All integrations are optional and configured through the admin GUI (Settings → Integrations).
Credentials are stored in `configs/*.env` files — never in code or Docker images.

| Integration | What it enables |
|---|---|
| **Home Assistant** | Voice/text control of lights, locks, climate, media, scenes |
| **MQTT** | Publish/subscribe to home automation events |
| **Frigate** | Camera snapshots, motion event monitoring |
| **Plex** | Search and cast media to your TV |
| **Brave Search** | Web search (requires a [free API key](https://brave.com/search/api/)) |

---

## Development

Code-mounted mode — edit files on disk, restart the container, no rebuild:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

Change cycle:
1. Edit a Python file on disk
2. `docker compose restart kaare-api` (~5 seconds)
3. Done

---

## Updating

**Linux / macOS:**
```bash
git pull
docker compose pull
docker compose up -d
```

**Windows (PowerShell):**
```powershell
git pull
docker compose pull
docker compose up -d
```

Your configuration (`configs/`), state (`state/`), and data (`data/`) are stored as bind-mounted
directories outside the images. They survive updates.

---

## Project structure

```
configs/        ← All configuration (YAML, .env secrets) — mounted as volume
state/          ← Persisted runtime state (STM, Qdrant snapshots, timers)
data/           ← SQLite databases (users, LTM interactions)
logs/           ← All service logs
docker/         ← Caddyfile, nginx.conf
scripts/        ← Entrypoint and utility scripts
kaare_core/     ← Shared Python library
services/       ← Separate services (embedding, voice, agents)
frontend/       ← React/Vite GUI (TypeScript)
```

---

## License

[MIT](LICENSE)
