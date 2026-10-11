"""Versioned, run-once config migrations (P134 Phase 2).

One runner for Docker and bare metal. Every container calls it at start (`scripts/migrate_configs.py` from the
entrypoint); the first to take the migration lock migrates, the others wait on that lock and then find nothing to
do — flock is exclusive across containers (P134 Phase 0), so no container needs to know it is «the» runner.

State lives in `configs/.config_version` (YAML): `steps` = the last numbered step applied, `templates` = a hash of
`configs_default/`. Nothing is written when both match and no file is missing — a normal start writes nothing.

What a run does, in order (only the parts that apply):
1. `snapshot("pre-migrate-v<N>")` of the whole configs/ folder — before the first change.
2. Create missing files: the pre-P127 repo-root capability map if present, then every file in configs_default/
   (YAML, .md, sub-folders) that configs/ lacks. Never overwrites a file the user changed.
2b. Refresh old template copies: a file in configs/ that is byte-equal (line endings aside) to a version KTSB shipped
   earlier (`config_shipped_versions.json`, generated from the release history) is replaced by the current template —
   the user never changed it (DT03 R10 10.1: old tracked copies after `git pull`, or files copied back by hand).
3. Per template YAML file, under that file's lock: an unreadable or **empty** file is kept in history as `_corrupt`
   and replaced by the template (P134 P-f); otherwise the pending numbered steps for that file run, then missing keys
   are filled from the template (`fill_missing_keys`, idempotent). One write per file, comments kept.
4. Record the new state.

`null` counts as missing and is filled from the template — unchanged behaviour (P134 D6). A user value is never
replaced by a template value otherwise.

Numbered steps are the five ad-hoc migrations `scripts/migrate_configs.py` ran on every start before P134, ported
with the same conditions. Order: the services/settings steps run before the key fill, so the template's new
defaults never mask an old value (before P134 `network.local_subnet` was checked only after the fill had already
added the template's `local_subnets`, so the user's subnet was dropped for the template's).
Add a new step at the end with the next number; never renumber or remove one.
"""

import copy
import hashlib
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from kaare_core import config_store as cs

_log = logging.getLogger(__name__)

VERSION_FILE = ".config_version"
_IN_DOCKER = os.path.exists("/.dockerenv")
WAIT_TIMEOUT_S = 300.0  # how long a container waits for another container's migration


# ── numbered steps ────────────────────────────────────────────────────────────


def _s1_memory_embed(data: dict, defaults: dict) -> list[str]:
    """services.yaml (P126): pre-P126 installs with model files ran MiniLM fp32 → record `minilm` (else the fill
    would switch them to `minilm_int8` and an empty collection); the old `large-v3` Whisper default → '' (lightest
    preset) — Docker only: there it was the old template value and never worked, on bare metal it can be a real
    choice (Stian, 2026-10-09); the non-existent `kaare-memory-embed` URL → `kaare-semantic-embed`."""
    notes = []
    me = data.get("memory_embed")
    if isinstance(me, dict) and not me.get("model") and me.get("model_dir"):
        me["model"] = "minilm"
        notes.append("memory_embed.model = minilm (existing model files)")
    stt = (data.get("voice") or {}).get("stt")
    if _IN_DOCKER and isinstance(stt, dict) and stt.get("faster_whisper_model") == "large-v3":
        stt["faster_whisper_model"] = ""
        notes.append("voice.stt.faster_whisper_model large-v3 → '' (lightest preset)")
    internal = data.get("internal")
    if (
        isinstance(internal, dict)
        and internal.get("semantic_embed") == "http://kaare-memory-embed:11500"
    ):
        internal["semantic_embed"] = "http://kaare-semantic-embed:11500"
        notes.append("internal.semantic_embed → kaare-semantic-embed")
    return notes


def _s2_network_subnets(data: dict, defaults: dict) -> list[str]:
    """settings.yaml: network.local_subnet (string) → network.local_subnets (list)."""
    net = data.get("network")
    if not isinstance(net, dict) or "local_subnets" in net or "local_subnet" not in net:
        return []
    net["local_subnets"] = [net["local_subnet"]]
    del net["local_subnet"]
    return ["network.local_subnet → network.local_subnets"]


def _s3_nodes_is_tv(data: dict, defaults: dict) -> list[str]:
    """nodes.yaml: is_tv: true → has_display: true + has_audio: true."""
    nodes = data.get("nodes")
    if not isinstance(nodes, dict):
        return []
    count = 0
    for node in nodes.values():
        if isinstance(node, dict) and "is_tv" in node:
            node.setdefault("has_display", True)
            node.setdefault("has_audio", True)
            del node["is_tv"]
            count += 1
    return [f"{count} node(s) is_tv → has_display/has_audio"] if count else []


def _s4_llm_system_i18n(data: dict, defaults: dict) -> list[str]:
    """llm.yaml: default/vision .system flat string → {nb, en, de}; the user's text stays as nb."""
    roles = []
    for role in ("default", "vision"):
        cfg = data.get(role)
        if not isinstance(cfg, dict) or not isinstance(cfg.get("system"), str):
            continue
        default_sys = (defaults.get(role) or {}).get("system")
        if not isinstance(default_sys, dict):
            continue
        own = cfg["system"]
        cfg["system"] = {
            "nb": own,
            "en": default_sys.get("en", own),
            "de": default_sys.get("de", own),
        }
        roles.append(role)
    return [f"{', '.join(roles)}.system → nb/en/de"] if roles else []


# Category keys the template used before they were renamed to English. No code reads category names (only the GUI
# lists them), so a rename keeps every domain and stops the key fill from adding a second, English copy.
_TRUSTED_CATEGORY_RENAMES = {
    "film_og_serier": "film_and_tv",
    "kode_og_utvikling": "code_and_development",
    "kunnskap_og_forskning": "knowledge_and_research",
    "mat_og_oppskrifter": "food_and_recipes",
    "norsk_lov_og_offentlig": "law_and_government",
    "nyheter_og_faktasjekk": "news_and_fact_checking",
    "vær_og_natur": "weather_and_nature",
}


def _s5_trusted_category_names(data: dict, defaults: dict) -> list[str]:
    """trusted_sources.yaml: old Norwegian category keys → the template's English keys, content unchanged, in place.
    Skipped per category when the English key already exists (never merges two lists)."""
    sources = data.get("sources")
    if not isinstance(sources, dict):
        return []
    renamed = []
    for old, new in _TRUSTED_CATEGORY_RENAMES.items():
        if old in sources and new not in sources:
            keys = list(sources.keys())
            value = sources.pop(old)
            insert = getattr(sources, "insert", None)
            if insert is not None:
                insert(keys.index(old), new, value)
            else:
                sources[new] = value
            renamed.append(f"{old} → {new}")
    return [f"categories renamed: {', '.join(renamed)}"] if renamed else []


# Template values that shipped as defaults and were later changed. A value still equal to an old default was never
# chosen by the user, so it moves to the new default (DT03 R10 10.2). Read from the release history: v1.9.0 shipped
# kare_reflection.kare_max_tokens 3000 — too small for a thinking model (P117 C1). One table per numbered step, so a
# value the user sets back to the old number later is left alone (steps run once).
_S6_OLD_DEFAULTS: dict[str, tuple] = {
    "kare_reflection.kare_max_tokens": (3000,),
}


def _replace_old_defaults(data: dict, defaults: dict, table: dict[str, tuple]) -> list[str]:
    """Set each dotted key whose value is one of its old defaults to the template's current value, in place."""
    notes = []
    for dotted, old_values in table.items():
        *parents, leaf = dotted.split(".")
        node, tmpl = data, defaults
        for p in parents:
            node = node.get(p) if isinstance(node, dict) else None
            tmpl = tmpl.get(p) if isinstance(tmpl, dict) else None
        if not isinstance(node, dict) or not isinstance(tmpl, dict) or leaf not in tmpl:
            continue
        cur, new = node.get(leaf), tmpl[leaf]
        if cur in old_values and cur != new:
            node[leaf] = copy.deepcopy(new)
            notes.append(f"{dotted} {cur} → {new} (old default)")
    return notes


def _s6_old_defaults_settings(data: dict, defaults: dict) -> list[str]:
    return _replace_old_defaults(data, defaults, _S6_OLD_DEFAULTS)


@dataclass(frozen=True)
class Step:
    number: int
    name: str
    filename: str
    fn: Callable[[dict, dict], list[str]]


STEPS: list[Step] = [
    Step(1, "memory_embed", "services.yaml", _s1_memory_embed),
    Step(2, "network_subnets", "settings.yaml", _s2_network_subnets),
    Step(3, "nodes_is_tv", "nodes.yaml", _s3_nodes_is_tv),
    Step(4, "llm_system_i18n", "llm.yaml", _s4_llm_system_i18n),
    Step(5, "trusted_category_names", "trusted_sources.yaml", _s5_trusted_category_names),
    Step(6, "old_defaults", "settings.yaml", _s6_old_defaults_settings),
]
LATEST = STEPS[-1].number


# ── fill missing keys ─────────────────────────────────────────────────────────

# Mappings whose keys the user chooses (their speakers, rooms, cameras, SSH nodes). The template's entries there are
# examples: they are copied only when the whole mapping is missing, never merged into the user's own — before P134
# every Docker start added «living_room_speaker» & co. to a household's own nodes.
USER_COLLECTIONS: dict[str, set[str]] = {
    "nodes.yaml": {"nodes"},
    "ssh_nodes.yaml": {"nodes"},
    "aliases.yaml": {"rooms", "room_entities", "aliases"},
    "frigate_cameras.yaml": {"cameras"},
}


def fill_missing_keys(
    data: Any, defaults: Any, prefix: str = "", collections: set[str] | frozenset = frozenset()
) -> list[str]:
    """Add keys the template has and `data` lacks (or holds as null), recursively, in place — at the template's
    position where possible. Never changes an existing non-null value; never adds entries inside a mapping whose
    dotted path is in `collections`. Returns the dotted keys added."""
    if not isinstance(defaults, dict) or not isinstance(data, dict):
        return []
    added: list[str] = []
    order = list(defaults.keys())
    for i, key in enumerate(order):
        dv = defaults[key]
        path = f"{prefix}{key}"
        if key not in data or (data[key] is None and dv is not None):
            value = copy.deepcopy(dv)
            if key in data:
                data[key] = value
            else:
                _insert_after_template_neighbour(data, key, value, order[:i])
            added.append(path)
        elif isinstance(dv, dict) and isinstance(data[key], dict) and path not in collections:
            added += fill_missing_keys(data[key], dv, f"{path}.", collections)
    return added


def _insert_after_template_neighbour(data: dict, key: Any, value: Any, before: list) -> None:
    insert = getattr(data, "insert", None)
    keys = list(data.keys())
    for prev in reversed(before):
        if prev in data and insert is not None:
            insert(keys.index(prev) + 1, key, value)
            return
    if insert is not None and before == [] and keys:
        insert(0, key, value)
        return
    data[key] = value


# ── runner ────────────────────────────────────────────────────────────────────


@dataclass
class Report:
    changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    snapshot: Path | None = None
    up_to_date: bool = False
    dry_run: bool = False

    def lines(self) -> list[str]:
        head = "[migrate] DRY RUN — nothing written" if self.dry_run else "[migrate]"
        if not self.changes and not self.warnings:
            # Nothing to change — also after a rerun request (backup restore) that only recorded the version.
            return [f"{head} All configs up to date."]
        out = [f"{head} Config changes{' (would apply)' if self.dry_run else ' applied'}:"]
        out += [f"  {c}" for c in self.changes] or ["  (none)"]
        out += [f"  WARNING: {w}" for w in self.warnings]
        if self.snapshot:
            out.append(f"  snapshot before changes: {self.snapshot}")
        return out


def templates_hash(defaults_dir: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(defaults_dir.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(defaults_dir)).encode())
            h.update(b"\0")
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


# Where an install from before P127 kept a file that now lives in configs/ (paths relative to the install root).
# When configs/ lacks the file, the old copy wins over the template — the user's settings move with the upgrade.
LEGACY_LOCATIONS = {
    "capability_map.yaml": "capability_map.yaml",
    "voice_settings.yaml": "services/voice/configs/voice_settings.yaml",
}


def _missing_files(config_dir: Path, defaults_dir: Path) -> list[tuple[Path, Path]]:
    """(target, source) for every template file configs/ lacks; a valid pre-P127 copy wins over the template.
    The old copy is never removed."""
    out = []
    for src in sorted(defaults_dir.rglob("*")):
        if not src.is_file():
            continue
        dst = config_dir / src.relative_to(defaults_dir)
        if dst.exists():
            continue
        # Docker: these paths are inside the image (never user data) — only bare-metal installs kept settings there.
        use_legacy = dst.parent == config_dir and not _IN_DOCKER
        legacy_rel = LEGACY_LOCATIONS.get(dst.name) if use_legacy else None
        if legacy_rel and _valid_yaml(config_dir.parent / legacy_rel):
            src = config_dir.parent / legacy_rel
        out.append((dst, src))
    return out


# Every config file version KTSB has shipped (sha256, CRLF → LF), minus the current template — generated from the
# release git history by the release tooling. A file in configs/ equal to one of them was never changed by the user:
# an old tracked copy left by `git pull` (DT03 R10 10.1), or a file copied back by hand after an upgrade.
SHIPPED_VERSIONS_FILE = Path(__file__).with_name("config_shipped_versions.json")


def _shipped_versions() -> dict[str, set[str]]:
    try:
        doc = json.loads(SHIPPED_VERSIONS_FILE.read_text(encoding="utf-8"))
        return {rel: set(hashes) for rel, hashes in (doc.get("versions") or {}).items()}
    except Exception:
        _log.warning("config_shipped_versions.json unreadable — old template copies are not refreshed")
        return {}


def _norm_hash(data: bytes) -> str:
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def _superseded_files(config_dir: Path, defaults_dir: Path) -> list[tuple[Path, Path]]:
    """(target, template) for every file in configs/ that is byte-equal (line endings aside) to a version we shipped
    earlier and differs from the current template. User-changed files never match and are never touched."""
    shipped = _shipped_versions()
    out = []
    for src in sorted(defaults_dir.rglob("*")):
        rel = src.relative_to(defaults_dir).as_posix()
        if not src.is_file() or rel not in shipped:
            continue
        dst = config_dir / rel
        raw = cs._read_bytes(dst)
        if raw is None:
            continue
        h = _norm_hash(raw)
        if h in shipped[rel] and h != _norm_hash(src.read_bytes()):
            out.append((dst, src))
    return out


def _valid_yaml(p: Path) -> bool:
    try:
        yaml.safe_load(p.read_text(encoding="utf-8"))
        return p.is_file()
    except Exception:
        return False


def _read_state(config_dir: Path) -> dict:
    try:
        st = cs.read_yaml(config_dir / VERSION_FILE)
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def _plan_file(
    path: Path, default_data: Any, pending: list[Step]
) -> tuple[str, Any, list[str], list[str]]:
    """Return (action, new_data, notes, warnings) for one template YAML file; action ∈ {'', 'write', 'corrupt'}.
    Reads the file without a lock — the caller re-plans under the file lock before writing."""
    raw = cs._read_bytes(path)
    if raw is None:
        return "", None, [], []
    text = raw.decode("utf-8", errors="replace")
    try:
        doc = cs._parse_for_edit(text, path)
    except cs.ConfigCorruptError as e:
        try:
            ref = yaml.safe_load(text)
        except yaml.YAMLError:
            ref = "invalid"
        if ref is not None and ref != "invalid" and not isinstance(ref, dict):
            return "", None, [], [f"{path.name}: not a mapping — left as it is"]
        return (
            "corrupt",
            copy.deepcopy(default_data),
            [f"{path.name}: unreadable ({e}) → kept in history, template used"],
            [],
        )
    notes = []
    for step in pending:
        if step.filename == path.name:
            step_notes = step.fn(doc, default_data if isinstance(default_data, dict) else {})
            notes.extend(f"{path.name}: step {step.number} {step.name}: {n}" for n in step_notes)
    added = fill_missing_keys(doc, default_data, collections=USER_COLLECTIONS.get(path.name, set()))
    if added:
        shown = ", ".join(added[:8]) + (f" … (+{len(added) - 8})" if len(added) > 8 else "")
        notes.append(f"{path.name}: added missing keys: {shown}")
    return ("write" if notes else ""), doc, notes, []


def run(
    config_dir: Path = cs.CONFIG_DIR,
    defaults_dir: Path = cs.DEFAULT_DIR,
    *,
    dry_run: bool = False,
    wait_timeout: float = WAIT_TIMEOUT_S,
) -> Report:
    """Bring configs/ up to the shipped templates and steps. Safe to call from every container at once."""
    config_dir, defaults_dir = Path(config_dir), Path(defaults_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    rep = Report(dry_run=dry_run)
    version_path = config_dir / VERSION_FILE
    with cs.lock(version_path, timeout=wait_timeout):
        state = _read_state(config_dir)
        done = int(state.get("steps") or 0)
        # The shipped-versions list is part of the hash: a new list (next release) makes every install look once.
        thash = templates_hash(defaults_dir) + "-" + _norm_hash(cs._read_bytes(SHIPPED_VERSIONS_FILE) or b"")[:8]
        pending = [s for s in STEPS if s.number > done]
        missing = _missing_files(config_dir, defaults_dir)
        if not pending and state.get("templates") == thash and not missing:
            rep.up_to_date = True
            return rep
        superseded = _superseded_files(config_dir, defaults_dir)
        refreshed = {d for d, _s in superseded}
        # A pre-P127 copy that is itself an unchanged shipped version brings nothing of the user's — use the template.
        shipped_now = _shipped_versions()
        missing = [
            (d, s)
            if s.is_relative_to(defaults_dir)
            or _norm_hash(s.read_bytes()) not in shipped_now.get(d.relative_to(config_dir).as_posix(), ())
            else (d, defaults_dir / d.relative_to(config_dir))
            for d, s in missing
        ]

        plans: dict[str, Any] = {}
        templates: dict[str, Any] = {}
        for default_file in sorted(defaults_dir.glob("*.yaml")):
            try:
                default_data = yaml.safe_load(default_file.read_text(encoding="utf-8")) or {}
            except Exception as e:
                rep.warnings.append(f"template {default_file.name} is invalid YAML ({e}) — skipped")
                continue
            templates[default_file.name] = default_data
            target = config_dir / default_file.name
            if target in refreshed:
                continue  # replaced by the template below — nothing of the old copy to migrate
            action, _data, notes, warns = _plan_file(target, default_data, pending)
            rep.warnings += warns
            rep.changes += notes
            if action:
                plans[default_file.name] = default_data
        rep.changes = (
            [
                f"create {d.relative_to(config_dir)} from {'template' if s.is_relative_to(defaults_dir) else s}"
                for d, s in missing
            ]
            + [f"refresh {d.relative_to(config_dir)} from template (an older shipped version, never changed)"
               for d, _s in superseded]
            + rep.changes
        )

        if dry_run:
            return rep
        if missing or plans or superseded:
            rep.snapshot = cs.snapshot(f"pre-migrate-v{LATEST}", config_dir)
        for dst, src in missing:
            cs.ensure_file(dst, src)
        shipped = _shipped_versions()
        for dst, src in superseded:
            # Check and write under one file lock: a GUI save in between makes it the user's file — left alone.
            with cs.lock(dst):
                raw = cs._read_bytes(dst)
                if raw is not None and _norm_hash(raw) in shipped.get(src.relative_to(defaults_dir).as_posix(), ()):
                    cs._save_history(dst, f"migrate-v{LATEST}-refresh")
                    cs._atomic_write(dst, src.read_bytes(), None)
        # Files created just now (e.g. from a pre-P127 copy) need their steps and key fill too, so every
        # template file is planned again here, not only the ones the report saw.
        for name, default_data in templates.items():
            target = config_dir / name
            with cs.lock(target):
                # Re-plan under the file lock: what we write is computed from what is on disk now.
                action, data, _notes, _w = _plan_file(target, default_data, pending)
                if action == "corrupt":
                    cs.replace_corrupt(target, data, "migrate-corrupt")
                elif action == "write":
                    cs.write_yaml(target, data, f"migrate-v{LATEST}")
        cs.write_yaml(version_path, {"steps": LATEST, "templates": thash}, f"migrate-v{LATEST}")
    return rep


def request_rerun(config_dir: Path = cs.CONFIG_DIR) -> None:
    """Make the next run() apply every step and the key fill again — after a backup restore has put back config
    files from an older version. Steps are idempotent, so a rerun on current files changes nothing."""
    cs.write_yaml(
        Path(config_dir) / VERSION_FILE, {"steps": 0, "templates": ""}, "rerun-after-restore"
    )
