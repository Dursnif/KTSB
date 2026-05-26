# KTSB Windows-installasjon — Debug & Status
_Sist oppdatert: 2026-05-26 (v1.0.15). Les denne filen for å fortsette arbeid etter context-compact._

> **Kontekst:** Windows-PC (192.168.0.203) er testplatform for GitHub-slippet.
> Kode endres alltid i `/kaare` på AI-PC, synces til `/mnt/ai_disk/ktsb-release`, pushes til GitHub.
> Se [`CURRENT.md`](/kaare/CURRENT.md) for overordnet prosjektstatus.

---

## Arbeidsplan — punkt for punkt (2026-05-26)

Alle punkter gjelder feil/persondata oppdaget på Windows-testinstallasjon.
Kryss av med `[x]` etterhvert som hvert punkt er ferdig og verifisert på Windows-PC.

### Gruppe A — Datalekkasjer (persondata i GitHub-repoen)

- [x] **A1: `configs/frigate_cameras.yaml` fjernes fra GitHub**
  - Legg til `--exclude='configs/frigate_cameras.yaml'` i `scripts/sync_to_release.sh`
  - Lag ren `configs_default/frigate_cameras.yaml` med tom `cameras: {}`
  - Slett filen fra `/mnt/ai_disk/ktsb-release/configs/` med `git rm`
  - Verifiser: Windows-PC har ikke kameranavn i GUI etter `git pull + docker compose up`

- [x] **A2: `configs_default/models.yaml` — bytt til generiske defaults**
  - `kare: qwen3:8b` (ikke `huihui_ai/Qwen3.6-abliterated:27b`)
  - `miss_kare: qwen3:4b` (ikke `huihui_ai/qwen3.5-abliterated:9b`)
  - `library: qwen3:4b` (ikke `huihui_ai/qwen3-abliterated:8b`)
  - Merk: endrer ikke eksisterende installasjoner (entrypoint kopierer kun ved første oppstart)
  - Verifiser: LLM/Modeller-fanen viser generiske modellnavn for ny installasjon

- [x] **A3: Frontend-placeholder "stian" → generisk**
  - `frontend/src/pages/admin/Nodes.tsx:201`: `placeholder="stian"` → `placeholder="admin"`

- [x] **A4: Frontend-placeholder modellnavn → generisk**
  - `frontend/src/pages/admin/Settings.tsx:878`: `placeholder="f.eks. huihui_ai/qwen3.5-abliterated:9b"` → `placeholder="f.eks. qwen3:8b"`

### Gruppe B — Bugs

- [x] **B1: Alias-fanen krasjer — sort skjerm (KRITISK)**
  - Fix backend `kaare_api.py:2491`: `data.get("aliases", {})` → `data.get("aliases") or {}`
  - Fix frontend `Aliases.tsx:323`: `config.aliases` → `config.aliases ?? {}` (null-guard)
  - Verifiser: Alias-fanen åpnes uten krasj på Windows-PC

- [x] **B2: Systemsjekk bruker `127.0.0.1` — feiler i Docker**
  - `scripts/health_check.py:170-180`: SERVICES-listen hardkoder `http://127.0.0.1:PORT`
  - Fix: Les URL-er fra `services.yaml` (Docker-hostnavn: `kaare-ha-gateway:8002`, `qdrant:6333`, etc.)
  - Alternativ: Kjør sjekken fra innsiden av kaare-api-containeren med interne hostnavn
  - Verifiser: Systemsjekk viser korrekte feil (kun HA gateway / vLLM / Ollama 9B som forventet)

- [x] **B3: Miss Kåre vises "online" i dashboard selv uten modell**
  - Årsak: v1.0.10 `_IN_DOCKER`-fix returnerer `active: true` for alle tjenester
  - Fix: Modellkort i dashboard bør sjekke `ollama-model://` (faktisk modell tilgjengelig?) ikke systemd-status
  - Verifiser: Miss Kåre-kortet viser "offline" på Windows-PC (9B ikke installert)

- [x] **B4: Hot-reload feilmelding — "gateway: All connection attempts failed"**
  - Årsak: Hot-reload prøver alltid å nå HA-gateway, selv om HA ikke er konfigurert
  - Fix: Sjekk `home_assistant.url` i services.yaml — hopp over gateway-reload og vis "hoppet over" hvis tom
  - Verifiser: Hot-reload på Windows-PC gir ikke feilmelding om gateway

### Gruppe C — Etter A+B er ferdig

- [x] **C1: Oppdater `configs_default/aliases.yaml` — fjern `aliases: null`**
  - Filen har `aliases: null` som krasjer alias-fanen. Bytt til `aliases: {}`
  - Dette er en del av B1-fiksen (backend-fix er primær, men default bør også ryddes)

- [x] **C2: Release — sync, commit, tag, push**
  - v1.0.13: Alle A1–B4 + C1 fikser. Verifisert i container.
  - v1.0.14: Embedding startup-guard + health check hopper over deaktiverte tjenester.
  - Gjenstår: Windows-PC trenger `docker compose pull && docker compose up -d` (kjøres manuelt pga. credentials-problem)

- [ ] **C3: Test alle fikser på Windows-PC** ← NESTE STEG
  - Krever at Windows-PC kjører v1.0.15-images (se under)
  - `docker compose pull && docker compose up -d` på Windows-PC
  - Alias-fanen åpner uten krasj ✅ (verifisert i container via Python)
  - Kameranavn er borte (tom kamera-fane) ✅ (verifisert: `configs/frigate_cameras.yaml` slettet)
  - Modellnavn er generiske i LLM/Modeller ✅ (verifisert: `qwen3:8b` i container)
  - Systemsjekk viser 0 feil (Embedding + Semantic embed hoppet over)
  - Miss Kåre-kort viser offline (ikke online)
  - Hot-reload gir ikke falsk feilmelding om gateway
  - `configs/services.yaml` er reparert (migration erstatter ødelagt YAML automatisk)

---

## Gjenstående etter v1.0.14 — oppdatering av eksisterende install

**Problem:** Windows-PC kjørte første gang med gammel `configs_default/services.yaml` (uten `embedding.enabled: false`).
Entrypoint kopierer kun fra defaults ved første oppstart. Eksisterende `configs/services.yaml` mangler flagget.

**Symptom:** Embedding-containeren starter og prøver å laste ned BGE-M3 (~2GB) fra HuggingFace automatisk.

**Fix for eksisterende installasjon (Windows-PC):**
Legg til `enabled: false` under `embedding:` i `C:\Users\stian\Documents\KTSB\configs\services.yaml` manuelt,
eller kjør dette i PowerShell på Windows-PC:
```powershell
# Alternativ: rediger filen direkte
notepad C:\Users\stian\Documents\KTSB\configs\services.yaml
# Legg til "  enabled: false" under "embedding:" seksjonen
```

**Nye installasjoner:** Får automatisk `enabled: false` fra `configs_default/services.yaml` via entrypoint.

**Oppdater til v1.0.14 (pga. credentials-problem i Docker på Windows):**
```powershell
# Kjør i PowerShell på Windows-PC (192.168.0.203):
cd C:\Users\stian\Documents\KTSB
git pull
docker compose pull
docker compose up -d
```
Forrige gang fungerte `docker compose pull` etter at det ble kjørt direkte i PowerShell (ikke via SSH).

---

## Miljø

| | |
|---|---|
| Windows-PC IP | 192.168.0.203 |
| Windows-bruker | `verksted-pc\stian` |
| SSH fra AI-PC | `ssh stian@192.168.0.203` |
| SSH-nøkkel | `stian@AI-pc` (ed25519, i `C:\ProgramData\ssh\administrators_authorized_keys`) |
| KTSB-mappe | `C:\Users\stian\Documents\KTSB\` |
| Docker-compose | `docker compose` (v2) |
| Installert Ollama-modell | `huihui_ai/qwen3.5-abliterated:0.8B` (1.0 GB, 0.87B) |
| Gjeldende image | `ghcr.io/dursnif/kaare:latest` (v1.0.12) |

---

## Containerstatus (v1.0.12)

Alle containere kjører. Ingen `Restarting`.

```
kaare-kaare-api-1           Up   port 8000   ✅
kaare-kaare-agents-1        Up   port 11450  ✅
kaare-kaare-reflection-1    Up   (sover — reflection disabled by default) ✅
kaare-kaare-ha-gateway-1    Up   port 8002   ✅
kaare-kaare-ha-log-bridge-1 Up   (kobler til HA WS — ingen HA konfigurert, gjør ingenting) ✅
kaare-kaare-memory-embed-1  Up   port 11500  ✅ (sover — disabled + ingen modell)
kaare-kaare-vaktmester-1    Up               ✅
kaare-kaare-embedding-1     Up   port 11446  ✅
kaare-kaare-frontend-1      Up   port 5173   ✅
kaare-caddy-1               Up   port 80/443 ✅
kaare-qdrant-1              Up   port 6333   ✅
kaare-ollama-1              Up   port 11434  ✅
```

---

## Konfig på Windows-PC (etter alle fikser)

```yaml
# configs/models.yaml
kare: huihui_ai/qwen3.5-abliterated:0.8B

# configs/llm.yaml — default-seksjonen
default:
  base_url: http://ollama:11434
  provider: ollama
  options:
    num_predict: 512      # redusert fra 2048 (v1.0.9)
    temperature: 1
    top_p: 0.75
    presence_penalty: 1.5
  # num_ctx IKKE satt — Ollama bruker modellens native kontekst

# configs/settings.yaml
personality_mode: minimal  # kun kjernepersonlighet, ingen selvbilde/world/behavior
memory_embed:
  enabled: false   # ingen lokal embed-modell
kare_reflection:
  enabled: false   # reflection kjøres ikke
home_assistant:
  url: ""          # HA ikke satt opp — HA-gateway gjør ingenting
```

---

## Gjennomførte fikser (alle versjoner)

### v1.0.2
| Fix | Fil | Beskrivelse |
|-----|-----|-------------|
| SQLite migrate krasj | `kaare_core/memory/long_term.py` | `_migrate()` krasjet på tom DB — `ALTER TABLE` på ikke-eksisterende tabell. |
| HA gateway crash | `kaare_ha_gateway.py` | `load_env()` krasjet med `FileNotFoundError` når `kare_ha.env` mangler. |
| Manglende pakker | `requirements.txt` | `onnxruntime` og `tokenizers` manglet. |

### v1.0.3
| Fix | Fil | Beskrivelse |
|-----|-----|-------------|
| Alle LLM-kall blokkert | `adapters/llm_adapter.py` | `_kare_is_busy()` fanget `FileNotFoundError` (ingen `gpu.lock` i Docker) → returnerte `True` → ingen LLM-kall. |

### v1.0.4
| Fix | Fil | Beskrivelse |
|-----|-----|-------------|
| Reflection restart-loop | `kaare_reflection_runner.py` | Omskrevet til evig sleep-loop. Default OFF. |
| docker-compose | `docker-compose.yml` | `kaare-reflection`: `unless-stopped` → `on-failure`. |

### v1.0.5
| Fix | Fil | Beskrivelse |
|-----|-----|-------------|
| memory-embed startup-vakt | `memory_embed_server/server.py` | Sover 5 min hvis disabled eller modell mangler. Default OFF. |
| memory_embed konfigurerbar | `kaare_api.py` + `Settings.tsx` | GUI-kort for toggle + modellsti. |

### v1.0.6
| Fix | Fil | Beskrivelse |
|-----|-----|-------------|
| HA gateway krasj | `kaare_ha_gateway.py` | Hardkodet `raise RuntimeError` erstattet med default `http://kaare-api:8000`. |

### v1.0.7
| Fix | Fil | Beskrivelse |
|-----|-----|-------------|
| `num_ctx: 16384` blokkerte chat | `configs_default/llm.yaml` | Fjernet fra `default` og `reason_freely`. 0.8B med 16384 kontekst → 9 min svar. |
| num_ctx safety-net | `adapters/llm_adapter.py` | `_clean_ollama_options()`: hopper over `num_ctx` hvis 0 eller mangler. |

### v1.0.8
| Fix | Fil | Beskrivelse |
|-----|-----|-------------|
| Tool-gating — tier-system | `kaare_core/tools/definitions.py` | `TOOL_MODEL_TIERS`: tier 0 / 3 / 9B. |
| Tool-gating — filter | `kaare_core/config.py` | `filter_tools_by_model(tools, size_b)`. |
| Modellstørrelsesdeteksjon | `adapters/llm_adapter.py` | `get_model_size_b()`: `POST /api/show` → parser `parameter_size`. Caches i prosesslevetid. |
| Router bruker filter | `kaare_core/routers/router_generate.py` | Kaller filter etter `get_tools_for_role()`. |

### v1.0.9
| Fix | Fil | Beskrivelse |
|-----|-----|-------------|
| Regex M-suffiks | `adapters/llm_adapter.py` | Ollama returnerer `"873.44M"` for sub-1B. Regex utvidet: M-suffiks ÷ 1000. |
| `num_predict` 2048→512 | `configs_default/llm.yaml` | 2048 tokens CPU = ~200s. 512 = ~50s. |

### v1.0.10
| Fix | Fil | Beskrivelse |
|-----|-----|-------------|
| Docker restart krasj | `kaare_api.py` | `sudo`/`systemctl` finnes ikke i Docker → krasjet ASGI-appen. `_IN_DOCKER`-sjekk: selvrestart bruker `os.kill(os.getpid(), signal.SIGTERM)`. |
| Tjenestestatus Docker | `kaare_api.py` | `systemctl is-active` ikke tilgjengelig → returnerer `active: true` i Docker. |
| MQTT startup-guard | `adapters/mqtt_adapter.py` | Avslutter stille hvis `mqtt.host` er tom — ingen 30s reconnect-spam. |

### v1.0.11
| Fix | Fil | Beskrivelse |
|-----|-----|-------------|
| Default personality minimal | `configs_default/settings.yaml` | `personality_mode: minimal` for nye installasjoner — fjerner selvbilde/behavior/world fra prompten. |

### v1.0.12
| Fix | Fil | Beskrivelse |
|-----|-----|-------------|
| 0 tools under 9B | `kaare_core/config.py` | `filter_tools_by_model()` returnerer `[]` for `size_b < 9.0`. Tool-JSON er ~2300 tokens overhead en 0.8B-modell ikke kan bruke. 9B (qwen3.5-abliterated Q4_K_M, 6.6GB) er minimumsmodellen for tool-bruk. |

---

## Promptstørrelse — kartlegging

Full oversikt over alle komponenter som inngår i en prompt (utført 2026-05-25):

### System-melding — `_build_system()` i `adapters/llm_adapter.py`

| Komponent | Størrelse | I minimal-modus? |
|-----------|-----------|-----------------|
| `_ASSISTANT_NAME_BLOKK` | ~50 chars | Ja |
| `_PERSONALITY_CORE` (minimal.md) | 315 chars | Ja (minimal versjon) |
| `_current_user_block` | ~50 chars | Ja |
| `behavior` (personlighetsvariant) | 1896 chars | **Nei** |
| `_LOKASJON_BLOKK` | ~100 chars | Ja |
| `_LANGUAGE_BLOKK` | ~30 chars | Ja |
| `base` (fra `llm.yaml system:`) | ~200 chars | Ja |
| `personality_self` (selvbilde) | 1232 chars | **Nei** |
| `kare_huske` (notatliste) | variabel | Ja |
| `_HOUSEHOLD_BLOCK` (husstand) | variabel | Ja |
| `profile_top` (brukerprofil) | variabel | Ja |
| `user_obs` (observasjoner om bruker) | opp til 1500 chars | Ja |
| `world_ctx` (world.md) | 8863 chars | **Nei** |
| `_build_disabled_modules_block()` | liten | Ja |
| `_tid_blokk()` | ~80 chars | Ja |

**System-melding i minimal ≈ 700 chars ≈ ~175 tokens**

### Messages-array

| Komponent | Maks størrelse | Kilde |
|-----------|---------------|-------|
| `context_block` (STM: state + actions + dialog) | **20 000 chars** (`context_max_chars`) | `memory.build_prompt_context()` |
| `ltm_block` (RAG fra langtidsminnet) | liten (3 episoder) | `search_memory()` |
| Dialog-par (siste 4 turer = 8 meldinger) | ~4800 chars | `memory.get_dialog_pairs(n=4)` |
| Nåværende brukermelding | liten | bruker-input |

> **OBS:** `context_max_chars: 20000` i `settings.yaml` er aggressivt for liten modell.
> Fersk install: 0 tokens STM. Etter mange samtaler: kan vokse til ~5000 tokens.
> For fremtidig vurdering: sett lavere cap for modeller < 9B.

### Tools

| Modell | Antall tools | Tokens |
|--------|-------------|--------|
| < 9B (v1.0.12+) | **0** | **0** |
| 9B+ | opp til 25 (tier-basert) | ~2300+ |
| vLLM/cloud (999B) | alle | ingen filter |

### Oppsummert (Windows PC, 0.87B)

| | Gammelt (pre-v1.0.12) | Nå (v1.0.12) | Fersk install (v1.0.12) |
|---|---|---|---|
| System | ~175 tokens | ~175 tokens | ~175 tokens |
| Tools | ~2300 tokens | **0 tokens** | **0 tokens** |
| STM (akkumulert) | ~750 tokens | ~750 tokens | **0 tokens** |
| Dialog-par | ~1200 tokens | ~1200 tokens | **0 tokens** |
| Melding + ltm | ~100 tokens | ~100 tokens | ~20 tokens |
| **Total** | **~4525 tokens** | **~2225 tokens** | **~195 tokens** |

---

## Gjenstående problemer

### 1. Miss Kåre evaluator — 404 ⚠️ (ikke kritisk)
**Symptom:** `[Miss Kåre] evaluator feilet: Client error '404 Not Found' for url 'http://ollama:11434/api/chat'`
**Årsak:** Miss Kåre 9B-modellen er ikke installert på Windows-PC.
**Konsekvens:** Evaluatoren feiler stille, `[STILLE]` returneres. Ingen brukersynlig effekt.
**Løsning:** Installer `qwen3.5-abliterated:9b` i Ollama på Windows, eller la være (test-maskin).

### 2. HA gateway / HA log bridge — ingen startup-vakt 📋 (ikke kritisk)
**Symptom:** Starter og er Up uten HA konfigurert — gjør ingenting farlig.
**Mulig løsning:** Sjekk `home_assistant.url` fra services.yaml. Sov i loop hvis tom (som memory-embed).

### 3. STM vokser over tid 📋 (ikke kritisk på fersk install)
**Symptom:** `context_max_chars: 20000` fylles ved lang bruk → prompt blåser opp.
**Mulig løsning:** Redusere cap for modeller < 9B. Ikke nødvendig for nye brukere (tom historikk).

---

## Persondata-lekkasjer og GUI-bugs oppdaget 2026-05-26

Oppdaget ved gjennomgang av Windows-testinstallasjon. Alle er i GitHub-slippet, ikke bare lokal data.

### Datalekkasjer — persondata i GitHub

#### L1: Kameranavn — `configs/frigate_cameras.yaml` (KRITISK)
**Symptom:** Kameranavn vises i GUI (Bilder/Kamera-fanen).
**Årsak:** `configs/frigate_cameras.yaml` er **ikke** ekskludert i `sync_to_release.sh` → Stians kameraer (`utekamera_nedside_vest`, `ringeklokke_kamera`, `cam_ae836b98`, `cam_a4e5b958`) havner direkte i release-repoen og i Docker-imaget.
**Flyt:** `git clone` → `./configs/frigate_cameras.yaml` finnes på host → Docker volume mount → container ser filen.
**Fix:** Legg til `--exclude='configs/frigate_cameras.yaml'` i sync_to_release.sh. Legg til tom `configs_default/frigate_cameras.yaml`. Slett filen fra release-repoen.

#### L2: Modellnavn — `configs_default/models.yaml`
**Symptom:** Stians 27B/9B/8B abliterated-modeller vises som defaults i LLM/Modeller-fanen.
**Årsak:** `configs_default/models.yaml` inneholder `huihui_ai/Qwen3.6-abliterated:27b` osv. → kopieres til `configs/models.yaml` av `docker_entrypoint.sh` ved første oppstart.
**Fix:** Bytt til generiske, lavkravs defaults (f.eks. `qwen3:8b`) som passer ny bruker uten kraftig GPU.

#### L3: Placeholder "stian" — `frontend/src/pages/admin/Nodes.tsx:201`
**Symptom:** `placeholder="stian"` i Noder-fanens "standard bruker"-felt — Stians brukernavn er eksempel.
**Fix:** Bytt til `placeholder="admin"` eller `placeholder="(valgfri)"`.

#### L4: Placeholder modellnavn — `frontend/src/pages/admin/Settings.tsx:878`
**Symptom:** `placeholder="f.eks. huihui_ai/qwen3.5-abliterated:9b"` — Stians modell som eksempel.
**Fix:** Bytt til generisk, f.eks. `placeholder="f.eks. qwen3:8b"`.

### Bugs

#### B1: Alias-fanen krasjer — sort skjerm (KRITISK)
**Symptom:** Åpner Alias-fanen → sort skjerm → må reloade og logge inn på nytt.
**Årsak:** `configs_default/aliases.yaml` har `aliases: null` (None i YAML). `api_get_aliases` bruker `data.get("aliases", {})` som returnerer `None` når nøkkelen finnes med null-verdi (ikke `{}`). Frontend mottar `{"aliases": null}` og krasjer på `Object.keys(null)` i `Aliases.tsx:323`.
**Fil:** `kaare_api.py:2491` + `frontend/src/pages/admin/Aliases.tsx:323`
**Fix backend:** `data.get("aliases") or {}` (behandler både manglende og None som tomt dict).
**Fix frontend (defensiv):** `config.aliases ?? {}` i alle `Object.keys(config.aliases)` og `Object.entries(data)` kall.

#### B2: Miss Kåre vises "online" i dashboard (kosmetisk)
**Symptom:** Dashboard viser Miss Kåre som online, men 9B-modellen er ikke installert.
**Årsak:** v1.0.10-fix (`_IN_DOCKER`-sjekk) returnerer `active: true` for ALLE tjenester i Docker. Ment for systemd-status, men brukes også av modell-status i dashboard.
**Fix:** Dashboard-modellkort bør sjekke Ollama `/api/tags` (finnes modellen?) — ikke systemd-status.

#### B3: Systemsjekk viser 8 feil — Qdrant/Agents/Embedding "Connection refused" (feil i Docker)
**Symptom:** System-siden → Systemsjekk → 8 feil, inkludert Qdrant, Agents server og Embedding som burde kjøre.
**Årsak:** `scripts/health_check.py` SERVICES-listen bruker `127.0.0.1` for alle tjenester. Fungerer på AI-PC (alt på samme host), men i Docker er tjenestene i separate containere og nås via Docker-hostnavn (`kaare-ha-gateway`, `qdrant:6333`, etc.) — ikke via localhost.
**Fil:** `scripts/health_check.py:170-180`
**Fix:** Les URLs fra `services.yaml` (internt Docker-nettverk) i stedet for hardkodede `127.0.0.1`.

#### B4: Hot-reload — "gateway: All connection attempts failed" (forventet, men misvisende)
**Symptom:** Hot-reload sier "Feil — kunne ikke nå API: gateway: All connection attempts failed".
**Årsak:** HA-gateway (port 8002) er ikke konfigurert (ingen HA-URL) → gjør ingenting, men hot-reload prøver å nå den likevel og feiler.
**Konsekvens:** Ikke kritisk — reload av andre configs fungerer. Feilmeldingen er misvisende for ny bruker.
**Fix:** Hot-reload bør sjekke om gateway er konfigurert før den forsøker å nå den, og vise "hoppet over (ikke konfigurert)" i stedet for feil.

### Faktafunn — Docker-arkitektur (viktig for fremtidig feilsøking)

- `Dockerfile` bruker `COPY . .` → ALLE filer i release-repoen bakes inn i imaget.
- `docker-compose.yml` bruker `./configs:/kaare/configs` volume mount → host-configs **erstatter** bakte configs.
- `scripts/docker_entrypoint.sh` kopierer `configs_default/*.yaml` til `configs/` kun ved **første oppstart** (sjekker om `settings.yaml` mangler).
- `configs/frigate_cameras.yaml` er i release-repoen → havner på host ved `git clone` → er synlig i containeren via volume mount.
- `configs_default/` finnes IKKE som volum — disse er bakt inn i imaget og kopieres bare én gang.

---

## Credential-problem — `docker pull` feilet

```
error getting credentials - err: exit status 1
```

**Årsak:** Windows Credential Manager hadde utløpte credentials for `ghcr.io`.
**Løst ved:** `git pull` + `docker compose pull` + `docker compose up -d` — credentials fungerte uten ny PAT.

**Hvis det skjer igjen:**
```powershell
docker login ghcr.io -u Dursnif --password-stdin
# Lim inn PAT med read:packages-scope
```

---

## Arkitekturnotat: Prism fjernet

Prism (Meilisearch/Tantivy) fjernet 2026-05-17. Erstattet av Qdrant.
Embed-modellen (`paraphrase-multilingual-MiniLM-L12-v2` ONNX, 449 MB) ligger på AI-PC:
```
/kaare/state/models/semantic-embed/model.onnx
/kaare/state/models/semantic-embed/tokenizer.json
```
Konfigureres via GUI: Innstillinger → LLM/Modeller → Semantisk minne. Ikke del av Docker-imaget.

---

## Versjonsoversikt

| Tag | Innhold |
|-----|---------|
| v1.0.1 | Første Docker-release |
| v1.0.2 | SQLite migrate fix, HA gateway load_env fix, onnxruntime i requirements |
| v1.0.3 | llm_adapter FileNotFoundError fix (gpu.lock → behandles som not-busy) |
| v1.0.4 | Reflection: sleep-loop, default OFF, on-failure restart |
| v1.0.5 | memory-embed: startup-vakt, konfigurerbar via GUI, default OFF |
| v1.0.6 | HA gateway: KARE_LOG_URL default http://kaare-api:8000 |
| v1.0.7 | num_ctx fjernet fra defaults, adapter-safety-net |
| v1.0.8 | Tool-gating: TOOL_MODEL_TIERS + filter_tools_by_model + get_model_size_b |
| v1.0.9 | Regex M-suffiks (873M→0.87B), num_predict 512 |
| v1.0.10 | Docker restart (SIGTERM), tjenestestatus Docker, MQTT startup-guard |
| v1.0.11 | personality_mode: minimal som default |
| v1.0.12 | **0 tools under 9B** — 9B+ er minimum for tool-bruk |
| v1.0.13 | Fjern persondata (kameranavn), generiske modell-defaults, alias-krasj fikset, systemsjekk Docker-aware, modellstatus presis match, hot-reload hopper over ukonfigurert gateway |
| v1.0.14 | Embedding startup-guard (sover hvis `enabled: false`), health check hopper over deaktiverte tjenester (Semantic embed + BGE) |
| v1.0.15 | Config-migrering: `scripts/migrate_configs.py` kjører ved hver oppstart — fikser ødelagt YAML, legger til manglende nøkler fra defaults |

---

## Nyttige kommandoer

```bash
# Containerstatus
ssh stian@192.168.0.203 "docker ps -a"

# Logger
ssh stian@192.168.0.203 "docker logs kaare-kaare-api-1 --since 5m 2>&1"
ssh stian@192.168.0.203 "docker logs kaare-ollama-1 --since 5m 2>&1"

# Oppdater til siste versjon
ssh stian@192.168.0.203 "cd C:/Users/stian/Documents/KTSB && git pull && docker compose pull && docker compose up -d"

# Les konfig fra container
ssh stian@192.168.0.203 "docker exec kaare-kaare-api-1 cat /kaare/configs/llm.yaml"
ssh stian@192.168.0.203 "docker exec kaare-kaare-api-1 cat /kaare/configs/settings.yaml"

# Release workflow (fra AI-PC)
bash /kaare/scripts/sync_to_release.sh
cd /mnt/ai_disk/ktsb-release
git add -A && git commit -m "fix: ..." && git tag v1.0.X && git push && git push --tags

# Sjekk siste tag
cd /mnt/ai_disk/ktsb-release && git tag --sort=-version:refname | head -3
```
