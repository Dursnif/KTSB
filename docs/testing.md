# What has been tested

This is the full test log behind the short summary in the [README](../README.md#whats-actually-been-tested). KTSB is
a one-person hobby project — there is no QA team. This page says what was run, how, what came out, and — just as
important — what was not run. Nothing here is a promise about your machine.

---

## Where it runs every day

The developer's own house, on Linux with two NVIDIA GPUs and a 27B main agent. That install runs directly on the
machine (systemd), **not in Docker**. Every feature runs there first, with real users, a real Home Assistant, a real
Frigate and real screens and speakers.

## The release test machine

The Docker release is tested on a separate machine, set up the way a stranger would:

| | |
|---|---|
| OS | Windows 11 Pro, Docker Desktop on WSL2 |
| CPU / RAM | AMD laptop CPU (8 cores / 16 threads), 32 GB RAM |
| GPU | **None that Docker can use** (an AMD integrated GPU only) → the built-in Ollama runs on CPU |
| Main agent | The house's 27B over the network for the chat tests (a GUI-configured address, like any LLM server in your LAN); the 9B on that CPU for the default path |
| Integrations | A small test Home Assistant and a dummy Plex in their own containers; the house's Frigate read over the network; a one-way MQTT relay for camera events (inbound only) |
| Screens and speakers | A Google TV in a test room, a Nest Hub, a Samsung TV (DLNA) |

---

## v1.11.1 — November 2026

Found on the test machine right after v1.11.0 was published, and fixed in v1.11.1:

- **Going back to the previous version left the GUI waiting forever.** «Go back to v1.10.3» put the old images,
  compose file and `.env` back (about 42 seconds, data intact), but the settings stayed as v1.11 had migrated them.
  v1.10.3 read one of them (`cors_origins: ["same-host"]`) as a literal web address, so the browser blocked every answer
  and the page spun on «KTSB is restarting …». The same would have happened on an automatic rollback.
  - Now the updater keeps a copy of the settings at the moment it switches, and every rollback puts it back before the
    old version starts — or, when the previous version was installed by an older updater, the snapshot the new version
    took before it migrated anything. The data folder is never touched. The card says whether the settings were set back.
  - «same-host» is now the default in code, so it is no longer written to the settings file at all.
  - After a rollback the update check no longer shows the old, cached «you are up to date».
- **The update card spun forever** when the API did not come back. Now it stops after one minute, says that KTSB does
  not answer, says why when the browser can tell (KTSB answers but the browser blocks it, or nothing answers), and
  offers «Reload the page». A first version of this fix still waited forever on Docker Desktop, where calls to a
  stopped API hang instead of failing; now every call has its own time limit, only one runs at a time, and the clock
  runs on its own — checked in a real browser with calls that hang for good (before: «Loading status…» without end;
  after: «KTSB has not answered» after about a minute).
- **The update card could offer an older version** («Update to v1.11.0» on v1.11.1) when the install's `.env` held no
  version it could read. Now the version the running image reports is used; if no version is known, nothing is offered
  and the card says so.
- **An admin's own «go back» was reported as a failed update**, with an English cause in the Norwegian GUI. Now «you
  went back to vX from vY» and a failed update are told apart, with the cause in nb/en/de.
- **A fresh install could lock its only admin out.** The default admin's PIN expired one hour after the install; a
  first login later than that was refused for good. Now the default PIN does not expire but works only from the home
  network until it is changed; an install that is already locked out this way is let in again at start (only when the
  built-in admin still has the default PIN and no other admin exists). Temporary PINs an admin gives others still expire.

**How it was tested:** automated tests for each fix, each shown failing first (rollback with and without a settings
copy, a failing settings restore, the update card's texts and its one-minute limit, the default PIN from the home
network, over VPN and from outside, and the lock-out healing). A real update-and-go-back round on the Windows machine
follows after v1.11.1 is published.

## v1.11.0 — November 2026

Tested on release-candidate images built exactly as the release workflow builds them, then on the signed images
from GitHub, over several rounds of images. Bugs found this way were fixed before the release or are listed under
*Known issues* below.

### What was tested

**Install and start**
- Fresh install from the release bundle with `setup.ps1`: profile choice on a machine without NVIDIA (CPU mode,
  honest note for the AMD GPU), `.env` and secrets written, 13–14 containers up, no restarts.
- `setup.ps1` with your own `docker-compose.override.yml`: an edited file is copied to `.bak-<time>` first, a file you
  wrote yourself is left unchanged.
- Downloading the default 9B model from the GUI on a fresh install (6.6 GB, about 3 minutes on that network).
- Image sizes measured: about 6.2 GB unpacked without Ollama, about 14.7 GB with it (about 1.4 / 4.6 GB compressed).
- Resource use of KTSB itself (13 containers, profile `medium`, no language model; median of three rounds): about
  0.7–0.8 GB RAM and under 1 % CPU at idle; about 0.85 GB RAM and half of one CPU core at the peak of a chat with tool
  calls (sampled every 2 seconds). Data folder about 1 GB at the start, mostly the vector database reserving room. The
  built-in Ollama with the 9B on the CPU (32k context): about 8 GB RAM while loaded, every available core (8 here) while
  it answers. Not measured: the `full` profile, the 27B on a GPU in Docker, and the camera pipe under load (image
  analysis is off in the shipped settings).

**Without things (graceful absence)**
- No Home Assistant, no Frigate, no MQTT, inner voices off, no image-capable model, no sound device: no crash loop,
  no retry spam, an honest message where the user would look.
- Home Assistant with a non-admin token: the room and device import works read-only.

**Chat and tools (house 27B over the network)**
- Notes, shopping list, timers (set, fired, delivered), user profile writes — checked against the stored files, not
  only the reply.
- Web search, Norwegian law lookup (built locally in under a minute; quotes verified against the source).
- Language switch to English and German (tool receipts and GUI fully switched).
- Roles: a teen account is refused admin routes and admin tools; the reason given is the role.
- Backup → restore of a save point with PIN; seed-phrase account recovery.
- The night sequence started by hand: all 9 steps, about 21 minutes, morning report written.

**Personality level** — the same 12 prompts (write, lookup, camera, «shall I …?») with a fresh adult user per level,
counted per request from the tool log:

| Level | Expected tool called | Tool call written as text | Answered from context |
|---|---|---|---|
| Minimal | 4/12 | 6/12 | 2/12 |
| Standard | 11/12 | 0 | 1/12 |
| Complete | 10/12 | 0 | 2/12 |

New installs therefore start at **Complete**. One run per level — a signal, not a rate.

**Cameras (house Frigate, read only)**
- Episodes made from live events, stored encrypted on disk.
- «Was it you?» question cards in chat: answer, «don't know», correcting an earlier answer, a card surviving an API
  restart; a person who switched off camera recognition is never asked or named.
- Children and teens can see stored camera data but not start a new image-model analysis (checked: no image-model call).
- Stuck episodes marked after the GUI limit, «Analyse again» with the image model unreachable (honest failure in seconds).

**Screens and speakers**
- Speech and a camera picture over Cast on a Google TV (checked by eye and ear).
- Speech to an unknown speaker name: said back with the known ones, nothing played silently elsewhere.
- Speech and a picture while YouTube plays: speech is not sent («YouTube is playing»), the picture appears as an
  overlay, YouTube keeps playing. On a Nest Hub without an overlay the picture is not shown, and the reply says so.
- Live camera on the Google TV (moving picture, stops by itself after 2 minutes and returns to the screen as it was),
  a recipe card on the screen, and the screens switch turned off (the screen page answers «switched off»).
- A DLNA picture sent to a Samsung TV using the stored description address (no network discovery needed in Docker);
  the TV fetched it, but nobody confirmed by eye that it showed.
- A node started by a Home Assistant button press (non-admin token).

**Settings and maintenance**
- Saving Home Assistant, MQTT, Frigate and Plex in the GUI: used at once, MQTT reconnects by itself; the page says
  «Saved and in use» or which service needs a restart.
- Choosing a model that is not downloaded: the warning appears at once, without a restart, and the shared model on the
  same Ollama is not unloaded.
- The restart buttons on the Maintenance page: each service came back; a service outside the active profile says so.
- «Logs and disk usage» on Docker Desktop: one disk, with the same free space Windows reports for that drive.
- Every admin page and tab in English and German: no raw text keys.

**Security**
- 289 API routes read for their lock; every GET route called without a login and as a teen. Without a login only
  `/`, `/api/show`, the GUI language and the assistant name answer. Settings, service addresses, token stubs,
  hardware and service states are admin only; a teen gets 403.
- Pictures: a user's own picture is served to them and to an admin, to anyone else «not found».
- Personal-data check: 0 hits in the shipped files.

**The update button (after the push)**
- The update button was tested for real on 17 November 2026: a v1.10.3 install with data (users, notes, a shopping
  list, a timer, a changed setting) on Windows 11 + Docker Desktop was updated to v1.11.0 from the Maintenance page.
  It took about two minutes: a save point first, the release bundle's signature and checksum checked, the signed
  images pulled and checked, a health check, no rollback. The API was down for about ten seconds. All data and both
  users' PINs were kept, settings kept, new settings added. On upgrade, child and teen roles gain «see stored camera
  pictures» (untick under Users if you don't want that).

**Signed release (after the push)**
- All six images of the release candidate are signed. Checked with cosign v3.0.5 against the release workflow's
  identity for that tag; a check against a wrong identity was refused (the control). The release bundle's signature (`verify-blob` with its
  `.sigstore.json` bundle) and `SHA256SUMS` match.

### What was not tested

- **macOS** — never installed on a Mac. From reading the code, `setup.sh` uses shell features macOS's default shell lacks.
- **A GPU in Docker** — the test machine has no NVIDIA GPU, so `setup.sh`'s GPU setup and Ollama on a GPU inside
  Docker are untested. The GPU install the developer runs is outside Docker.
- **A real Plex server playing on a TV from Docker** (only a dummy Plex), **a Samsung TV showing KTSB's screen page**,
  **a real voice node** (microphone satellite) on the Docker install, **music/radio via MPD in Docker** (not in the image).
- **Long-term running in Docker** — the night sequence has run a few nights there, not weeks. Daily log files on a
  Windows bind mount were written and cleaned at night, but the switch to a new day's file was not watched.
- **The VPN (`vpn` profile)** — not started in this round. KTSB pins wg-easy 15, a rewrite that by its own
  documentation is set up in a browser wizard instead of the `WG_HOST` / `PASSWORD_HASH` settings the README's older
  steps use.
- **AMD integrated GPUs and NPUs** — Ollama in Docker on Windows cannot use them, so they stay idle; KTSB runs on the CPU.
- **Live volume changes on a real speaker while a node listens** — built and tested with simulated speakers.

### Known issues

- **Minimal personality is much weaker at tools** (4 of 12 above): it often writes the tool call as text. The answer
  check now catches and corrects that, but «Complete» or «Standard» give better results.
- **CPU only:** the 9B backup model can time out (120 s) when it starts cold, so the fallback fails; answers with web
  search can take over ten minutes on a laptop.
- **Volume on a Cast screen by name without Home Assistant** is not supported yet — the main agent cannot find a way
  and searches for minutes.
- A TV returned to its **home screen** after live camera is called «standby» in the reply.
- A timer can promise to say something **out loud** on an install with no speaker; it then comes to the chat (with
  a line saying why), so nothing is lost.
- Right after you switch language, the main agent may keep answering in the old one for a while.
- In onboarding, «External LLM server» sets every role to the Ollama API. For vLLM, LM Studio or another
  OpenAI-compatible server, change the provider afterwards under Settings → LLM / Models.
- A per-user reply language is not active yet.
- **The 9B on a fresh install with nothing connected** can claim to know rooms and sensors it has no source for, and
  can ignore web search results it did fetch. The 27B does far better; see «What to expect from the model you run» in
  the README.
- A few GUI texts are English in every language (service descriptions on the dashboard, module descriptions under
  Settings → Distribution), and the shipped trusted-source descriptions are Norwegian only.
- When an app starts on a Cast device, the device itself may raise its volume (seen on a Google TV when YouTube or
  the screen page started); KTSB does not set it.

---

## v1.10 — October 2026

Same Windows machine, same approach. What was run:

- upgrading a real v1.9 install (data, users and settings kept) and a fresh install from scratch
- the default 9B model on that CPU, and a 27B main agent running on another machine in the same network
- backup → wipe → restore, a server using its own Ollama instead of the built-in one, several users at once
- all containers starting at once on old and new configs, and two settings saved at the same moment
- an attempt to get in from another machine on the network: login limits, default PIN, open endpoints, ports
- the night sequence started by hand and started by itself
- voice in the browser over HTTPS through the bundled Caddy

More than 30 real bugs were found and fixed before that release, four of them security holes. The last check ran on
the release-candidate images from GitHub and found one more: `setup.ps1` did not start in Windows PowerShell 5.1.

## Earlier rounds

- **August 2026** — the first install on this Windows machine: a fresh clone, the onboarding wizard, account recovery
  with the seed phrase, and a dummy Home Assistant, Plex, web pages and MQTT broker checked end to end. Five bugs found
  and fixed (among them the onboarding wizard's «create user» step and a settings template that never reached GitHub).
  The AMD integrated GPU and NPU were looked at: no supported way to use them from Docker.
- **May–August 2026** — an older Windows PC with a small 0.8B model followed the 1.0 releases (v1.0.1 to v1.0.17). That
  machine is retired; its detailed log is no longer available.

---

## Check the signatures yourself

You need [cosign](https://docs.sigstore.dev/cosign/system_config/installation/) **v3 or newer**. Replace `v1.11.0`
with the release you have.

**An image** (repeat for `kaare-embedding`, `kaare-voice`, `kaare-frontend`, `kaare-inner-voices`, `kaare-updater`):

```bash
TAG=v1.11.0
cosign verify ghcr.io/dursnif/kaare:$TAG \
  --certificate-identity "https://github.com/Dursnif/KTSB/.github/workflows/build.yml@refs/tags/$TAG" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com"
```

**The release bundle** (download `ktsb-docker-$TAG.tar.gz`, its `.sigstore.json` and `SHA256SUMS` from the release page):

```bash
sha256sum -c SHA256SUMS
cosign verify-blob ktsb-docker-$TAG.tar.gz \
  --bundle ktsb-docker-$TAG.tar.gz.sigstore.json \
  --certificate-identity "https://github.com/Dursnif/KTSB/.github/workflows/build.yml@refs/tags/$TAG" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com"
```

`cosign verify` lists the checks it performed and exits with 0; `verify-blob` ends with `Verified OK`. The in-app
updater checks `SHA256SUMS` and the same signature identity before it installs anything.

If you hit a problem, especially on macOS or a GPU install, [open an issue](https://github.com/Dursnif/KTSB/issues).
