# Kåre The Smart Butler

![Version](https://img.shields.io/github/v/tag/dursnif/KTSB?label=version&sort=semver)
![License](https://img.shields.io/badge/license-Personal%20Use-blue)
![Docker](https://img.shields.io/badge/docker-ghcr.io%2Fdursnif%2Fkaare-blue)
![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows%20%7C%20macOS-lightgrey)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-dursnif-FFDD00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/dursnif)

**The AI layer your smart home is missing.**

---

## Why does this exist?

Most local AI projects are built by developers, for developers. They require you to live in the terminal, copy-paste cryptic commands, and edit complex config files just to get a basic feature working. If you don't know how to code, setting up a private, local AI agent network feels impossible.

**KTSB was built to change that.**

The creator of KTSB didn't know how to code either — still doesn't, and has never written a single line of it. The project started in June 2025: about eight months of ChatGPT copy-paste between terminal and chat, then Claude from late February 2026, and Claude Code directly in the terminal from April 2026. It was born out of pure frustration with how uncomfortable, difficult, and text-heavy the local AI landscape is for regular people — and the conviction that architecture, decisions, and years of frustration are still ownership, even without a single commit written by hand.

KTSB is built for **the average user**, not the terminal-comfortable developer: full access to the most advanced AI agent features spoken about today — completely free for private use, entirely local, and managed 100% through a beautiful, intuitive web GUI.

No terminal required after the initial setup, and no configuration files to edit — except two optional extras:
HTTPS for voice from other devices and the VPN each need a line or two in `.env`.

KTSB fits seamlessly alongside your existing **Home Assistant** and **Frigate** setup, running on its own dedicated machine and connecting to both over the network — each system keeps doing what it already does best.

And because it's yours, you decide how deep you go: tune the personality, prompts, and model choices yourself, or just use the ready-built defaults and start talking.

- Understanding what you say in natural language
- Controlling your smart home through Home Assistant
- Analysing camera footage through Frigate
- Answering questions, setting timers, searching the web
- Remembering your preferences and conversations over time

![KTSB Dashboard](docs/dashboard.jpg)

---

## What can the main agent do?

KTSB's main agent is called *Kåre* by default — you can give it any name under **Settings → Assistant Settings**.

### Talk and understand
Ask the main agent anything in natural language — by voice or text. No rigid commands, no keyword memorisation. *"Turn off everything downstairs and lock the door"* works just as well as *"lock front door"*.

The GUI supports **Norwegian, English, and German**. The assistant name, hot-word, and response language are all configurable.

### Control your smart home
The main agent connects to Home Assistant and can control anything HA knows about: lights, locks, thermostats, covers, media players, scenes, and scripts. You define your own room names and device nicknames so you can say *"the lamp in the corner of the bedroom"* and the main agent knows exactly what you mean.

### See through your cameras
Ask *"is there anyone outside?"* or *"what happened at the front door in the last hour?"* and the main agent checks your Frigate cameras and describes what it sees. Motion events and snapshots can be analysed automatically and announced over your speakers.

### Remember and learn
The main agent maintains a short-term memory of your recent conversations and a long-term memory that persists across sessions. It builds a self-image over time based on its interactions — the longer you use it, the better it knows you. Its own notes are sorted into drawers — short follow-ups, observations, lessons it always keeps in mind, and topics it has settled — so old notes do not crowd out the conversation.

### A team of AI assistants
Behind the scenes, the main agent has a small team working alongside it:

- **Miss Kåre** — a quiet evaluator who reads the main agent's answers and adds a short comment when something
  touches her
- **Mechanic** — a technical assistant for deep-dives, system inspection, and complex problems
- **Miss Library** — searches the web (and an optional local Wikipedia corpus — not part of the Docker install yet)
  for factual answers
- **Jing and Jang** — background inner voices that process and reflect while you are not watching

Every night (03:00 by default, set under **Settings → Reflection**) a night sequence runs in order: memory compression, a weekly review of what the main agent believes about the world, a reflection meeting that reviews the day, and a dev slot that investigates problems with read-only tools and leaves proposals for an admin to confirm in chat. Nothing changes without that confirmation.

### Multi-user with proper access control
Create separate accounts for everyone in your household. Each user gets a PIN, a role (Child, Teen, Young adult, Adult, Admin), and their own memory and conversation history. Sensitive tools — web search, system inspection, camera access — are gated by role.

Remote access via WireGuard VPN is available as an optional service, with per-user access levels: local only, AI chat only, or full smart home access from anywhere.

### Flexible AI backend
KTSB uses **Ollama** for local LLM inference out of the box. No cloud account required. Models can run on this machine or on another machine in your network — point each role at its address under **Settings → LLM**. With a 24 GB GPU you can run the 27B as the main agent (in Ollama, or vLLM for more speed) for the full experience. A cloud provider (OpenAI-compatible, HuggingFace, NVIDIA NIM …) can be added for explicit «ask online» questions; it does not run the main agent's tools (smart home, memory, cameras) yet.

---

## What you need

KTSB runs via **Docker**, so it needs nothing else installed by hand. It is tested on Windows 11 and runs every day on
Linux; macOS should work the same way but has not been tested (see *What's actually been tested*).

What you need comes in two parts: KTSB itself, which is small, and the language model it talks with, which is not.
The model can run on the same machine or on another machine in your network (**Settings → LLM**).

**1. KTSB itself — without a language model: under 1 GB RAM and under one CPU core, also during a chat.** Measured on
our Windows 11 + Docker Desktop test machine, default profile `medium`, the 13 KTSB containers without a language
model (median of three rounds at idle; during three chats with tool calls, sampled every 2 seconds):

| | KTSB alone |
|---|---|
| **RAM** | about **0.7–0.8 GB** at idle, about **0.85 GB** at the peak of a chat (the API is the largest, ~170 MB) |
| **CPU** | under 1 % at idle; about half of one core at the peak of a chat |
| **Disk** | about **6 GB** for the images + about **1 GB** for the data folder at the start (mostly the vector database reserving room for its empty collections) |
| **GPU** | none |
| **OS** | Linux or Windows 10/11 with Docker; macOS not tested |

The `full` profile (semantic search model and speech models loaded at start) uses more — not measured. Camera image
analysis is off in the shipped settings, so the load of the camera pipe is not measured either.

**2. The language model.** What it needs depends on the model you pick — the sizes are in the table under
[Choosing an AI model](#choosing-an-ai-model) (the default 9B is about 6.6 GB, the 27B about 17 GB). Its RAM or GPU
memory comes on top of KTSB's when it runs on the same machine. Measured: the built-in Ollama with the 9B on the CPU
(32k context) holds about **8 GB RAM** while the model is loaded and uses every core it can get while it answers (8 on
our test machine). The 27B on a GPU has not been measured in Docker. A 24 GB+ NVIDIA GPU runs the 27B, the full main agent;
without a GPU the 9B runs on the CPU, slowly (see below).

**Everything on one machine with the built-in Ollama and the 9B:** about **30 GB** free disk (images about 15 GB with
Ollama + the 6.6 GB model + data). With the model on another machine, KTSB needs its own ~7 GB plus room for what it
stores over time.

### What to expect from the model you run

Be honest with yourself about this one — it decides what the main agent can do:

- **27B on a 24 GB+ GPU — KTSB as intended.** The main agent picks the right tool, controls the house, remembers, searches, and tells you when it doesn't know.
- **9B or smaller (the default on a fresh install) — basic mode.** Conversation works. Tool use is unreliable: it may skip a search, pick the wrong tool, or claim to know things it has no source for. Speed on CPU only, measured on two machines: on our development machine (12–18 threads in use) the first answer after start takes several minutes, later answers about a minute; on an 8-core laptop (Ryzen 7 8745H, no GPU) it took about 6 minutes to read one search result and about 13 minutes for one question with web search (our Windows test, November 2026). Speed depends heavily on the CPU; without a GPU the 9B is a way to try KTSB, not KTSB.
- **Cloud** — only for explicit «ask online» questions today; it does not run the main agent's tools.

A smaller model is a way to try KTSB, not the finished experience. A tuned KTSB-sized small model is an idea we are
working towards — not a promise.

**No Home Assistant or Frigate?** That is fine too. KTSB works as a standalone AI assistant and you can add integrations whenever you are ready.

**Already running Home Assistant or Frigate?** KTSB's own code is lightweight — it is the LLM behind it that is demanding. Running KTSB on the same machine as Home Assistant or Frigate can cause KTSB to introduce delays on those systems instead of the other way around. A separate machine, even a modest one, keeps everything responsive.

### What's actually been tested

This is a one-person hobby project, not a company with a QA team. In short — the full log is linked at the end.

**Where it runs every day:** the developer's own house, on Linux with two NVIDIA GPUs and the 27B main agent —
**directly on the machine (systemd), not in Docker**. Every feature runs there first.

**How v1.11.0 was tested:** on a separate Windows 11 PC with Docker Desktop, an AMD laptop CPU, 32 GB RAM and **no
GPU Docker can use**, in November 2026, with the 9B on that CPU and the house's 27B over the network:

- a fresh install from the release bundle, the model download from the GUI, and starting with Home Assistant,
  Frigate, MQTT or a sound device missing (no crash loops, honest messages)
- notes, lists, timers, web search, law lookup, roles, backup/restore and the night sequence — checked against the
  stored files and logs, not only the replies
- the camera pipe against a real Frigate: episodes, «Was it you?» cards, see-vs-analyse per role
- speech, pictures, live camera and recipes on a Google TV and a Nest Hub, also while YouTube plays
- the three personality levels on the same 12 prompts (Minimal 4/12, Standard 11/12, Complete 10/12 tools called)
- a security sweep of every API route, without a login and as a teen
- the signed images and release bundle checked with cosign

**The update button** was tested for real: a v1.10.3 install with data was updated to v1.11.0 from the Maintenance
page in about two minutes — signatures checked, no rollback, all data, users and settings kept. On upgrade, child and
teen roles gain «see stored camera pictures» (untick under Users if you don't want that).

**Not tested:** macOS · a GPU inside Docker · real Plex playback, a Samsung screen page and a
microphone satellite on the Docker install · weeks of running in Docker.

**Known issues:**

- The «Minimal» personality is much weaker at tools; new installs start at «Complete».
- On CPU only, the 9B backup model can time out (120 s) on a cold start, and answers with web search can take over ten
  minutes on a laptop.
- Volume on a Cast screen by name needs Home Assistant for now.
- A TV returned to its home screen after live camera is called «standby» in the reply.
- A timer can promise to speak on an install with no speaker; it then comes to the chat instead, with the reason.
- Right after you switch language, the main agent may keep answering in the old one for a while.
- In onboarding, «External LLM server» sets every role to the Ollama API — for vLLM, LM Studio or another
  OpenAI-compatible server, change the provider afterwards under **Settings → LLM / Models**.

**Fixed in v1.11.1 — if you are on v1.11.0:**

- **«Go back to v1.10.3» on the Maintenance page leaves the GUI waiting forever.** v1.11.0 wrote a setting that v1.10.3
  misreads, so its browser page is blocked. Update to v1.11.1 before you go back. If it already happened: open
  `configs/settings.yaml`, delete the line `cors_origins:` and the line `- "same-host"` under it, then run
  `docker compose restart kaare-api`.
- **A fresh v1.11.0 install can lock you out** («Temporary PIN has expired») if the first login comes more than an hour
  after setup. In v1.11.1 the default PIN no longer expires, and an install that is already locked out is let in again
  when v1.11.1 starts: set `KTSB_VERSION=v1.11.1` in `.env`, then run `docker compose pull` and `docker compose up -d`.

**Full test log, including October's round and how to check the signatures yourself: [docs/testing.md](docs/testing.md).**

If you hit a problem, especially on macOS or a GPU install, [open an issue](https://github.com/Dursnif/KTSB/issues).

---

## Getting started

### Step 1 — Install Docker

Docker is a tool that packages software into self-contained boxes that run the same way on any computer. You do not need to install Python, databases, or any other programming tools — Docker handles all of it.

**Windows:** Download and install [Docker Desktop](https://www.docker.com/products/docker-desktop/). During installation it will ask you to enable WSL 2 (a Linux environment that runs inside Windows — Docker needs it) — say yes. Restart your computer when prompted.

After restarting, **open the Docker Desktop application itself** (find it in your Start menu, not just a terminal) and wait for it to say "Docker Desktop is running" — this can take a minute. Every command in the steps below needs Docker Desktop actually open and fully started first, not just installed — running a command too early gives a confusing error instead of a clear "Docker isn't running" message.

> **If Docker Desktop shows an unclear error instead of asking about WSL 2 during install:** this happened during our own testing — Docker Desktop doesn't always prompt for WSL 2 automatically. Open PowerShell **as Administrator**, run `wsl --install`, restart your computer, then open Docker Desktop again.

**macOS:** Download and install [Docker Desktop](https://www.docker.com/products/docker-desktop/). Open it once to finish setup, and — same as Windows — make sure it shows as running before continuing.

**Linux:** Follow the [Docker Engine install guide](https://docs.docker.com/engine/install/) for your distribution. For NVIDIA GPU support, also install the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html).

Verify Docker is working by opening a terminal and running:
```
docker info
```
You should see a long list that includes a **Server** section. If it ends with an error instead (for example «cannot connect» or «error during connect»), Docker is installed but not running — open Docker Desktop (Windows/macOS) and wait until it shows as running, then try again. (`docker --version` is not enough for this check: it answers even when Docker is not running.)

---

### Step 2 — Download KTSB

There are two ways; both give the same install.

**The release file (no extra tools needed — the simplest way on Windows, which has no git built in):** download `ktsb-docker-<version>.tar.gz` from the [latest release](https://github.com/Dursnif/KTSB/releases/latest), unpack it, and open a terminal inside the folder — **PowerShell** on Windows, **Terminal** on Mac or Linux. Use the release file, not GitHub's «Download ZIP» — the ZIP carries no version, so the update button stays off.

**With git** (if you have it — on Windows it can be installed with `winget install Git.Git`):

```bash
git clone https://github.com/Dursnif/KTSB.git
cd KTSB
```

---

### Step 3 — Run setup, then start KTSB

The setup script checks your hardware (GPU or CPU), asks one or two questions and writes `.env`.

- **«Include built-in Ollama? [Y/n]»** — Ollama is the program that runs the AI model. Answer **Y** (the default, just press Enter) unless you already have Ollama or another LLM server running somewhere; without one, KTSB has no model to answer with. It adds a large download (see below).
- **«Convert now? [Y/n]»** — only asked if you downloaded with git. Answer **Y**: it lets KTSB update itself from the Maintenance page later (your files stay where they are; `git pull` is then no longer used). Setup then writes down the most recent release tag your download is based on as the installed version, and KTSB runs that release's images. With the release file this question does not appear — that folder is updatable as it is.

**Linux / macOS:**
```bash
./setup.sh
docker compose up -d
```

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
docker compose up -d
```
(Windows blocks scripts by default; `-ExecutionPolicy Bypass` allows this one script, this one time. We ran it
on Windows 11 with scripts blocked, in Windows PowerShell 5.1.)

Docker Compose 2.20 or newer is needed — Docker Desktop and current Docker Engine already have it.

**Your own compose changes** go in `docker-compose.override.yml` (Docker Compose reads it on its own; updates never
replace it). With a GPU, setup writes that file to give Ollama the GPU. It is setup's file as long as its first line
starts with `# Auto-generated by setup`. Setup only rewrites or removes it then. If you edited it, setup first copies
it to `docker-compose.override.yml.bak-<date+time>` and tells you. To make the file yours, remove that first line.
After that, setup never touches it again. If it needs GPU lines, it prints them so you can add them yourself.

The first start downloads the Docker images. Measured on the v1.11.0 test images (as `docker images` shows them, unpacked): about **6 GB** without the built-in Ollama, about **15 GB** with it. The download itself is compressed and much smaller — roughly **1.5 GB** without Ollama and **5 GB** with it (our estimate from the test images; the release page shows the exact sizes). The AI model itself comes on top, when you pick one in the onboarding wizard (the 9B model is about 6.6 GB). On a slow line this takes a while. You only need to do this once — future starts are quick.

Watch the progress:
```bash
docker compose logs -f
```
Press `Ctrl+C` to stop watching logs. The services keep running in the background.

---

### Step 4 — Open the browser

Go to **http://localhost:5173/login** in your web browser.

Log in with the default admin credentials:

| Username | PIN |
|----------|-----|
| `admin` | `1234` |

> ⚠️ **You will be forced to change the PIN on first login.** Do not skip this — the default PIN is public knowledge.
> Until you change it, the default PIN works only from your home network (not over VPN or from outside). It does not
> expire, so there is no hurry.

The onboarding wizard walks you through the rest — no config files to edit manually:

1. **Profile** — your assistant's name, language, timezone, and location (used for weather)
2. **User** — create your personal user account with a PIN (min. 6 digits)
3. **Distribution** — choose how much of the system to enable (start with *Medium*)
4. **LLM source** — the built-in Ollama, or your own Ollama server elsewhere in the network
5. **Integrations** — connect Home Assistant (URL and token), Frigate, and others (you can skip this and do it later)
6. **Done** — install your first AI model and start chatting

---

## Connecting Home Assistant

In the KTSB admin panel, go to **Integrations → Home Assistant**.

You need two things from your Home Assistant install:

**1. The URL** — the address you use to open HA in your browser. Usually something like `http://homeassistant.local:8123` or `http://192.168.1.x:8123`.

**2. A long-lived access token** — this gives KTSB permission to control HA on your behalf.

To create one: in Home Assistant, click your profile picture in the bottom-left corner → **Security** → scroll down to **Long-lived access tokens** → **Create token** → give it a name like *KTSB* → copy it and save it somewhere safe.

Paste the URL and token into **Integrations → Home Assistant** in KTSB and save. That is it.

**Setting up device names** — the main agent understands room names and device nicknames you define yourself. Open the **Rooms and aliases** page to map your own names (like *"the bedroom lamp"*) to Home Assistant entity IDs. The alias editor has a search function to help you find the right entity.

---

## Connecting Frigate

In KTSB admin, go to **Integrations → Frigate**.

You need:
- **Frigate URL** — the address of your Frigate instance (e.g. `http://192.168.1.x:5000`)

For real-time motion event detection, also connect **MQTT** under **Integrations → MQTT** with your broker address and credentials. This lets the main agent react to Frigate events as they happen.

On the same **Integrations → Frigate** tab, below the connection, configure which cameras the main agent should monitor, what objects to watch for (people, cars, animals), confidence thresholds, and whether to announce detections over your speakers.

### What KTSB remembers from your cameras

Once Frigate is connected, KTSB keeps its own memory of what the cameras saw (the **Frigate user**, on by default; it does nothing until Frigate is connected): each detection, situations grouped across cameras, and a few pictures per situation. A name from Frigate's face recognition is kept only after KTSB has checked it against the picture, and a person who turned off «Frigate may track me» (on the Users page) is never stored by name.

- **Encrypted on disk.** Names, descriptions and pictures are stored encrypted with a key that never leaves the machine. A copy of the files — a backup on a NAS, a lost disk — cannot be read without it. The flip side: if you restore a backup on a *different* machine, KTSB cannot read the old camera memory there.
- **Light by default.** The steps that ask the main model to describe a whole situation frame by frame and write a conclusion are **off** (Integrations → Frigate → **Modules**). On a small machine that model is the one KTSB answers you with, so turning them on can make answers slower while the cameras are busy.
- **Size limits** for the camera memory are set on the same tab (**Storage limits**, in Expert mode).

---

## Choosing an AI model

After the onboarding wizard, open **Settings → LLM / Models**, expand **Manage model** on a role's card, paste the
model name from [ollama.com](https://ollama.com) and click **Pull model**. No command line needed.

| Your GPU VRAM | Recommended model | Size | How we have used it |
|---|---|---|---|
| No GPU (CPU) | `huihui_ai/qwen3.5-abliterated:2b` | 1.9 GB | Not tested by us as the main agent — expect very limited tool use |
| 4–6 GB | `huihui_ai/qwen3.5-abliterated:4b` | 3.3 GB | Ran Miss Library in our tests; not tested as the main agent |
| 8–16 GB | `huihui_ai/qwen3.5-abliterated:9b` | 6.6 GB | Runs Miss Kåre, Mechanic and Miss Library in our house every day; the fresh-install default (basic mode as main agent) |
| 24 GB+ | `huihui_ai/Qwen3.6-abliterated:27b` | 17 GB | Our main agent until August 2026 — the full main agent (see *What to expect* above) |

A fresh install expects `huihui_ai/qwen3.5-abliterated:9b` for every role (main agent, Miss Kåre, Library, Mechanic) on the built-in Ollama — pull that one first. One model for all roles uses less memory and keeps things simple. On a smaller machine, switch all roles to a smaller model instead; with a 24 GB+ GPU you can move Kåre to the 27B. You can assign each agent its own model later under **Settings → LLM**.

**Why abliterated models?** KTSB is a multi-agent system — agents evaluate each other, inspect logs, and ask sensitive questions internally. Standard models can refuse these internal requests, breaking the pipeline. Abliterated variants have the refusal direction removed, which makes the whole system more reliable. Abliteration is not one method: variants differ in how much they change the model, and a heavier edit can cost quality. The recommendations above are from [huihui_ai on Ollama](https://ollama.com/huihui_ai) because they are easy to pull and we have run them.

**What we run ourselves.** The house KTSB is developed in runs a different main agent than the table above: `orcarouter/Qwen3.8-27B-Uncensored` — a Qwen3.8 27B with a single refusal direction removed, ranked first in a public comparison of 12 abliterated Qwen3.8-27B variants ([abliterlitics.dev](https://abliterlitics.dev), October 2026). We quantized it ourselves to NVFP4 for vLLM, calibrated on KTSB-shaped conversations (system prompt, tool definitions, thinking and tool calls — no user data), Norwegian Wikipedia text and some English and German chat, so Norwegian stays clean after quantization; the model's speculative-decoding (MTP) head is kept for speed. This build is **not published**, and NVFP4 needs an NVIDIA Blackwell GPU — so it is not in the table, and nothing in KTSB depends on it.

**Multimodal:** All recommended models understand both text and images. For the full agent setup — main agent, Miss Kåre, Library, and Mechanic — using the same multimodal model across all roles ensures camera snapshots and images can be passed between agents without gaps.

**Extended thinking:** Qwen3 models can pause and reason before answering complex questions. Enable it per role under **Settings → LLM**.

**Prefer cloud?** Under **Settings → LLM / Models**, the cloud role takes an address and API key for OpenAI, NVIDIA NIM, or another OpenAI-compatible provider. It answers only explicit «ask online» questions and has no tools.

---

## Voice input

Browsers require HTTPS for microphone access. For voice input to work, KTSB needs to be served over HTTPS.

KTSB includes **Caddy**, which obtains a free Let's Encrypt certificate automatically. You will need a free dynamic DNS hostname from [DuckDNS](https://www.duckdns.org/).

1. Create a free account at [DuckDNS](https://www.duckdns.org/) and register a subdomain — e.g. `mykaare.duckdns.org`
2. Set it to point to your home's public IP address (shown on the DuckDNS dashboard)
3. Open ports **80** and **443** in your router (search for *port forwarding* in your router's manual)
4. Open your `.env` file and set `KAARE_DOMAIN=mykaare.duckdns.org`
5. Restart: `docker compose up -d`

Voice input will work as soon as the certificate is issued — usually within 30 seconds of first start.

Access KTSB at `https://mykaare.duckdns.org` from any device on your local network.

> For local use without voice, the default `KAARE_DOMAIN=localhost` works fine.

---

## Service profiles

Set `COMPOSE_PROFILES` in your `.env` file to control which optional services start:

| Value | What starts | When to use |
|---|---|---|
| *(empty)* | Core: API, GUI, HA gateway, semantic memory service, Qdrant, SearXNG, Caddy, night runner, updater | You run your own LLM server elsewhere |
| `ollama` | + Built-in Ollama LLM server | The default; leave it out when you use your own server |
| `medium` | + BGE-M3 embedding service | Local wiki search, Argus search, better «Relevant now» |
| `full` | + BGE-M3, and speech: the speech-to-text model and a Piper voice are downloaded and loaded at start | When you want voice input and output |
| `vpn` | + WireGuard (wg-easy) | Remote access, see below |
| `inner_voices` | + Jing and Jang | Optional background inner voices |

The voice bridge (port 8011) runs in every profile: it is how KTSB sends speech, pictures, NRK, YouTube, Plex and web pages to speakers and TVs (Cast, DLNA) and steers TVs with their own remote protocol (Android TV, Samsung). Without `full` it downloads no speech models at start — only if someone actually uses speech.

```
# .env — what setup writes (ollama,full when it finds a GPU)
COMPOSE_PROFILES=ollama,medium
```

---

## Remote access (VPN)

To access KTSB securely from outside your home network, enable the built-in WireGuard VPN service.

> ⚠️ **Not tested with the wg-easy version KTSB now pins (15).** wg-easy 15 is a rewrite: by its own documentation it
> no longer reads `WG_HOST` / `PASSWORD_HASH` and sets the host and admin password in a browser wizard on first
> start instead. The steps below were written for the older wg-easy and may not work as written. Until this is
> checked, follow [wg-easy's own documentation](https://wg-easy.github.io/wg-easy/) for the first start.

**Requirements:** a public hostname (DuckDNS recommended — you can reuse `KAARE_DOMAIN`) and UDP port 51820 open in your router.

### Setup

1. Open port **51820 UDP** in your router's port forwarding settings.
2. Generate a password hash for the VPN management web UI:
   ```bash
   docker run --rm ghcr.io/wg-easy/wg-easy:15 wg-easy --hash YourPassword
   ```
3. Add to your `.env` file:
   ```
   WG_HOST=mykaare.duckdns.org
   WG_PASSWORD_HASH=$$2a$$12$$...   # note: $$ escapes $ in Docker .env files
   ```
4. Add `vpn` to `COMPOSE_PROFILES` in `.env`:
   ```
   COMPOSE_PROFILES=medium,vpn
   ```
5. Restart: `docker compose up -d`

The VPN management UI is now available at `http://your-machine-ip:51821`. Add a client there, download its config or scan the QR code, and connect from your phone or laptop.

Once connected via VPN, KTSB detects you automatically as a VPN user.

### Per-user access levels

In **Admin → Users**, set each user's remote access level:

| Level | What it allows |
|---|---|
| **Local only** | Access only from your home network |
| **AI chat only** | Full chat and memory over VPN, no smart home control |
| **Full access** | All features available remotely |

New users default to **Full access**. Restrict individual users under Admin → Users if needed.

> **Note:** The VPN service requires `NET_ADMIN` and `SYS_MODULE` kernel capabilities. These work on standard Linux hosts. Some cloud VPS providers restrict kernel modules — check your host's documentation if wg-easy fails to start.

---

## Updating

From v1.10, update from the GUI: **Maintenance → Update KTSB**. It checks GitHub for a newer release and, after
you enter your PIN:

1. saves a backup point (marked «automatic», it does not count against your five),
2. checks that the release files and images are signed by KTSB's own release workflow,
3. downloads the new images while KTSB keeps running, then switches over,
4. checks that KTSB answers with the new version — and puts the previous version back by itself if not.

The button needs an install made with `setup.sh` / `setup.ps1` (Step 3). Updates are never installed on their
own, and never while the night sequence runs.

Your configuration, state, and data live in folders outside the Docker images and are kept. On the first start
of a new version, KTSB adds any new settings to your files in `configs/` — your own values and comments stay. KTSB
copies the whole folder to `configs/.history/_snapshots/` first, and every save from the settings pages
keeps the previous version of that file in `configs/.history/<file>/` (the newest 20 per file).

After an update from the GUI, the first time you run `docker compose up -d` yourself, Docker Desktop recreates
every container once. That is expected.

### Updating from v1.9 or older (once)

Getting from v1.9 to the update button takes one manual update — run these in your KTSB folder:

```bash
git pull
./setup.sh              # Windows: powershell -ExecutionPolicy Bypass -File .\setup.ps1
docker compose up -d
```
Answer **yes** when setup offers to make the install updatable. We tested this on Windows with a v1.9 install
that had changed settings, two users and encrypted chat history: settings, logins and memory were all kept.

Your settings are kept. The GUI keeps your configuration in `configs/` and the shipped templates live in
`configs_default/`; files you never changed are replaced by the new templates on the first start, files you changed
stay exactly as you left them (a copy of the folder is saved to `configs/.history/_snapshots/` first).
Setup keeps your git history as `.git-before-ktsb-updater` — after that, updates come from the GUI, not `git pull`.
If you edited `docker-compose.yml`, move your changes to `docker-compose.override.yml` first; setup warns you.
Setup keeps that file as described under *Your own compose changes* in Step 3.

If you use speakers on your network (ESP32, Chromecast, Home Assistant media players), set
*Settings → LLM / Models → Piper → LAN address for speakers* once after the upgrade — Docker cannot detect it.

---

## What stays home, and what goes out

KTSB runs on your own hardware. **Conversations, memories, camera images and user data never leave your
machine** — unless you set up a cloud model and ask it something explicitly.

Some things do go out with the default settings, because the feature cannot work otherwise:

- **Web search** — the search words go to public search engines (through the bundled SearXNG), and KTSB then
  opens the pages it found. When a question is about your area, the town or country from your settings is
  added to the search.
- **Weather** — your location (latitude and longitude) goes to the weather service when someone asks.
- **Update check** — when an admin opens the Maintenance page, KTSB asks GitHub whether a newer release exists.
- **First voice start** (`full` profile) — the speech models download from Hugging Face.

The web interface's fonts ship with KTSB itself — opening the GUI contacts no font service.

Everything else that reaches the internet — cloud model, image generation, Brave Search, Norwegian law, tide
data, model downloads, a HTTPS certificate — happens only after you switch it on or click it. The full list,
with how to switch each one off: [docs/capabilities.md](docs/capabilities.md#what-leaves-your-machine).

---

## For developers

Code-mounted mode — edit files on disk without rebuilding images:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

Edit a Python file → `docker compose restart kaare-api` (~5 seconds) → done.

For a full breakdown of every feature, agent, tool, and service profile: [docs/capabilities.md](docs/capabilities.md)

---

## Project layout

```
configs/        ← Your configuration, written by the GUI (YAML + .env secrets) — not in git
configs_default/ ← Shipped templates; missing files are copied into configs/ on start
state/          ← Runtime state and databases (users, memory, knowledge, timers, Qdrant, backups)
models/         ← Models downloaded from the GUI (speech, semantic memory)
logs/           ← Service logs
kaare_core/     ← Shared Python library
services/       ← Embedding, voice, agents
frontend/       ← React/Vite GUI (TypeScript)
```

---

## License

Free for personal, educational and non-commercial use. Commercial use (hosting it for customers, bundling it
into a product, or running it in a business) needs a separate license — see [LICENSE](LICENSE). This is KTSB's own
license, not an OSI open-source license.
