"""P127: all configuration in one place — configs_default/ ships, configs/ is local and GUI-written."""

import asyncio
import re
from pathlib import Path

import yaml

import kaare_core.config as config
import kaare_core.routers.router_settings as rs
from kaare_core import config_migrations as cm
from kaare_core.users import auth
from kaare_core.voice import media_host as mh

ROOT = Path(__file__).resolve().parent.parent

# ── capability_map.yaml → configs/ ──────────────────────────────────────────────


def test_capability_map_lives_in_configs():
    assert config.CAPABILITY_MAP_PATH == config.CONFIG_DIR / "capability_map.yaml"


def test_capability_map_template_is_generic():
    tpl = yaml.safe_load((ROOT / "configs_default/capability_map.yaml").read_text(encoding="utf-8"))
    domains = tpl["domains"]
    # Integrations a stranger has not set up are off; the GUI/onboarding turns them on.
    assert domains["home_assistant"]["enabled"] is False
    assert domains["frigate"]["enabled"] is False
    assert "url" not in domains["frigate"]
    assert "environment" not in tpl and "rooms" not in tpl  # house sensors/rooms never ship
    assert tpl["distribution_profile"]


def test_no_reader_keeps_its_own_capability_map_path():
    """One constant (config.CAPABILITY_MAP_PATH); the old root path only as the legacy source."""
    allowed = {ROOT / "kaare_core/config.py"}
    hits = []
    for base in ("kaare_api.py", "adapters", "kaare_core", "kaare_ha_gateway.py", "services"):
        p = ROOT / base
        files = [p] if p.is_file() else [f for f in p.rglob("*.py") if "venv" not in f.parts]
        for f in files:
            if f in allowed:
                continue
            if "/kaare/capability_map.yaml" in f.read_text(encoding="utf-8", errors="ignore"):
                hits.append(str(f.relative_to(ROOT)))
    assert hits == []


def _point_config_at(monkeypatch, tmp_path, legacy: bool):
    cfg_dir = tmp_path / "configs"
    cfg_dir.mkdir()
    default_dir = tmp_path / "configs_default"
    default_dir.mkdir()
    (default_dir / "capability_map.yaml").write_text(
        "distribution_profile: medium\n", encoding="utf-8"
    )
    legacy_path = tmp_path / "capability_map.yaml"
    if legacy:
        legacy_path.write_text("distribution_profile: full\n", encoding="utf-8")
    monkeypatch.setattr(config, "CAPABILITY_MAP_PATH", cfg_dir / "capability_map.yaml")
    monkeypatch.setattr(config, "_LEGACY_CAPABILITY_MAP_PATH", legacy_path)
    monkeypatch.setattr(config, "_DEFAULT_DIR", default_dir)
    return cfg_dir, legacy_path


def test_ensure_capability_map_copies_legacy_and_keeps_it(monkeypatch, tmp_path):
    _cfg_dir, legacy_path = _point_config_at(monkeypatch, tmp_path, legacy=True)
    path = config.ensure_capability_map()
    assert (
        yaml.safe_load(path.read_text())["distribution_profile"] == "full"
    )  # user's settings kept
    assert legacy_path.exists()  # copy only, never removed (rule 1)


def test_ensure_capability_map_falls_back_to_template(monkeypatch, tmp_path):
    _point_config_at(monkeypatch, tmp_path, legacy=False)
    path = config.ensure_capability_map()
    assert yaml.safe_load(path.read_text())["distribution_profile"] == "medium"


def test_ensure_capability_map_never_overwrites(monkeypatch, tmp_path):
    cfg_dir, _ = _point_config_at(monkeypatch, tmp_path, legacy=True)
    (cfg_dir / "capability_map.yaml").write_text("distribution_profile: light\n", encoding="utf-8")
    config.ensure_capability_map()
    assert (
        yaml.safe_load((cfg_dir / "capability_map.yaml").read_text())["distribution_profile"]
        == "light"
    )


def test_migrate_copies_legacy_before_default(monkeypatch, tmp_path):
    root = tmp_path / "kaare"
    (root / "configs").mkdir(parents=True)
    (root / "capability_map.yaml").write_text("distribution_profile: full\n", encoding="utf-8")
    rep = cm.run(root / "configs", ROOT / "configs_default")  # P134: the versioned runner
    assert yaml.safe_load((root / "configs/capability_map.yaml").read_text())["distribution_profile"] == "full"
    assert (root / "capability_map.yaml").exists() and any("capability_map" in c for c in rep.changes)
    # Second run: nothing to do.
    assert cm.run(root / "configs", ROOT / "configs_default").up_to_date


# ── Home Assistant switch + gateway reload (BUG-log 2026-10-06) ────────────────


def test_ha_enabled_written_to_capability_map(monkeypatch, tmp_path):
    cap = tmp_path / "capability_map.yaml"
    cap.write_text(
        "domains:\n  home_assistant:\n    enabled: false\n    route: ha\n", encoding="utf-8"
    )
    services = tmp_path / "services.yaml"
    services.write_text("home_assistant:\n  url: ''\n", encoding="utf-8")
    settings = tmp_path / "settings.yaml"
    settings.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(rs, "_CAPABILITY_MAP_PATH", cap)
    monkeypatch.setattr(rs, "_SERVICES_PATH", services)
    monkeypatch.setattr(rs, "_SETTINGS_PATH", settings)
    reloads = []

    async def fake_reload():
        reloads.append(1)
        return True

    monkeypatch.setattr(rs, "_reload_ha_gateway", fake_reload)

    asyncio.run(rs.api_put_services_ha({"url": "http://ha.local:8123", "enabled": True}, _u=None))
    data = yaml.safe_load(cap.read_text())
    assert data["domains"]["home_assistant"] == {"enabled": True, "route": "ha"}
    assert yaml.safe_load(services.read_text())["home_assistant"]["url"] == "http://ha.local:8123"
    assert "enabled" not in yaml.safe_load(services.read_text())["home_assistant"]
    assert reloads == [1]  # a new URL reaches the gateway without a restart


def test_ha_token_save_reloads_gateway(monkeypatch, tmp_path):
    monkeypatch.setattr(rs, "_HA_TOKEN_PATH", tmp_path / "ha_token.env")
    reloads = []

    async def fake_reload():
        reloads.append(1)
        return True

    monkeypatch.setattr(rs, "_reload_ha_gateway", fake_reload)
    asyncio.run(rs.api_put_ha_token({"token": "abc"}, _u=None))
    assert "HA_TOKEN=abc" in (tmp_path / "ha_token.env").read_text()
    assert reloads == [1]


def test_reload_ha_gateway_never_raises(monkeypatch):
    monkeypatch.setattr(rs, "get_service", lambda *a: "http://127.0.0.1:1")  # nothing listens
    assert asyncio.run(rs._reload_ha_gateway()) is False


def test_onboarding_turns_configured_integrations_on():
    src = (ROOT / "frontend/src/pages/Onboarding.tsx").read_text(encoding="utf-8")
    assert re.search(r"apiPutHa\(tokenSaved \? \{ url: state\.haUrl, enabled: true \}", src)
    assert "apiPutFrigate({ url: state.frigateUrl, enabled: true })" in src
    assert "apiPutHaToken(" in src


# ── voice_settings.yaml → configs/, media_host, browser voice path ─────────────


def test_voice_settings_template_is_generic_and_complete():
    tpl = yaml.safe_load((ROOT / "configs_default/voice_settings.yaml").read_text(encoding="utf-8"))
    text = (ROOT / "configs_default/voice_settings.yaml").read_text(encoding="utf-8")
    assert "serve_host" not in text and "/mnt/" not in text and "192.168.0.2" not in text
    assert tpl["audio"]["mic_device"] is None
    # Every key voice_bridge reads with cfg[...] must exist in the template.
    src = (ROOT / "services/voice/voice_bridge.py").read_text(encoding="utf-8")
    for section, key in re.findall(r'cfg\["(\w+)"\]\["(\w+)"\]', src):
        assert key in tpl[section], f"{section}.{key} missing in the template"


def test_voice_bridge_reads_configs_not_its_own_folder():
    src = (ROOT / "services/voice/voice_bridge.py").read_text(encoding="utf-8")
    assert 'ensure_config_file(Path("/kaare/configs/voice_settings.yaml"))' in src
    assert 'BASE_DIR / "configs" / "voice_settings.yaml"' not in src
    assert "serve_host" not in src and "BRIDGE_HOST" not in src
    assert 'return {"path": path}' in src  # browser gets a path, not the LAN address


def test_services_template_has_empty_media_host():
    tpl = yaml.safe_load((ROOT / "configs_default/services.yaml").read_text(encoding="utf-8"))
    assert tpl["voice"]["media_host"] == ""


def test_media_host_prefers_config_then_detection(monkeypatch):
    monkeypatch.setattr(mh, "_read_yaml_live", lambda name: {"voice": {"media_host": " 10.1.2.3 "}})
    assert mh.media_host() == "10.1.2.3"
    monkeypatch.setattr(mh, "_read_yaml_live", lambda name: {"voice": {"media_host": ""}})
    monkeypatch.setattr(mh, "detect_lan_address", lambda: "192.168.5.9")
    assert mh.media_host() == "192.168.5.9"
    monkeypatch.setattr(mh, "detect_lan_address", lambda: "")
    assert mh.media_host() == "127.0.0.1"


def test_detect_lan_address_sends_nothing_and_never_raises():
    addr = mh.detect_lan_address()
    assert isinstance(addr, str)


def test_media_host_endpoint_validates_and_saves(monkeypatch, tmp_path):
    services = tmp_path / "services.yaml"
    services.write_text("voice:\n  stt: {}\n", encoding="utf-8")
    monkeypatch.setattr(rs, "_SERVICES_PATH", services)
    monkeypatch.setattr(rs, "get_service", lambda *a: "http://127.0.0.1:1")  # voice not running
    r = asyncio.run(rs.api_put_media_host({"media_host": "192.168.1.20"}, _u=None))
    assert r == {"ok": True, "applied": False}
    assert yaml.safe_load(services.read_text())["voice"] == {
        "stt": {},
        "media_host": "192.168.1.20",
    }
    for bad in ("http://192.168.1.20", "host:8011", "a b"):
        try:
            asyncio.run(rs.api_put_media_host({"media_host": bad}, _u=None))
            raise AssertionError(bad)
        except rs.HTTPException as e:
            assert e.status_code == 400


def test_browser_voice_uses_same_origin_over_https():
    api_ts = (ROOT / "frontend/src/services/api.ts").read_text(encoding="utf-8")
    assert "`${window.location.origin}/voice`" in api_ts
    caddy = (ROOT / "docker/Caddyfile").read_text(encoding="utf-8")
    assert re.search(r"handle_path /voice/\* \{\s*reverse_proxy kaare-voice:8011", caddy)


# ── Local network default + onboarding suggestion ──────────────────────────────


def test_default_local_subnet_is_every_home_lan_not_one_house():
    assert config.DEFAULT_LOCAL_SUBNET == "192.168.0.0/16"
    tpl = yaml.safe_load((ROOT / "configs_default/settings.yaml").read_text(encoding="utf-8"))
    assert tpl["network"]["local_subnets"] == ["192.168.0.0/16"]


def test_default_classifies_other_home_lans_local_and_vpn_still_vpn(monkeypatch):
    monkeypatch.setattr(
        auth,
        "get_network_settings",
        lambda: {"local_subnets": [config.DEFAULT_LOCAL_SUBNET], "vpn_subnet": "10.0.0.0/24"},
    )
    assert auth.get_network_context("192.168.1.44") == "local"  # a stranger's 192.168.1.x
    assert auth.get_network_context("192.168.0.20") == "local"
    assert auth.get_network_context("10.0.0.7") == "vpn"
    assert auth.get_network_context("8.8.8.8") == "external"


def test_subnet_suggestion_never_docker_gateway_vpn_or_public():
    s = rs.suggest_local_subnet
    assert s("192.168.1.37", "10.0.0.0/24") == "192.168.1.0/24"
    assert s("10.20.3.4", "10.0.0.0/24") == "10.20.3.0/24"
    assert s("172.18.0.1", "10.0.0.0/24") == ""  # Docker Desktop NAT gateway (DT03 R6)
    assert s("172.17.0.1", "10.0.0.0/24") == ""
    assert s("10.0.0.5", "10.0.0.0/24") == ""  # the VPN is not «home»
    for addr in ("127.0.0.1", "8.8.8.8", "169.254.1.1", "fe80::1", "not-an-ip"):
        assert s(addr, "10.0.0.0/24") == "", addr


def test_frontend_subnet_guess_leaves_out_docker_range():
    src = (ROOT / "frontend/src/lib/network.ts").read_text(encoding="utf-8")
    assert "const isPrivate = a === 10 || (a === 192 && b === 168);" in src


def test_no_house_subnet_fallback_left_in_code():
    for f in (
        "kaare_core/config.py",
        "kaare_core/routers/router_settings.py",
        "kaare_core/users/auth.py",
    ):
        assert "192.168.0.0/24" not in (ROOT / f).read_text(encoding="utf-8"), f


# ── Guard: no house values in what ships (P127 point 7, rules 13/24) ──────────

_HOUSE_VALUE = re.compile(
    r"192\.168\.0\.\d{1,3}(?![\d/])"  # a host on the house's LAN (subnets like 192.168.0.0/24 are examples)
    r"|/mnt/ai_disk|/mnt/wiki|/home/stian"  # house disks / home folder
    r"|\bainuc\b|\bdnspi\b|\bproxypi\b|\bha-nuc\b"  # house machines
)
_SHIPPED = [
    "kaare_core",
    "adapters",
    "services",
    "scripts",
    "configs_default",
    "frontend/src",
    "docker",
    "semantic_embed",
]
_SKIP_PARTS = {"venv", "node_modules", "__pycache__", "cache", "models", "ov_cache", "convert_venv"}
_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
    ".sh",
    ".md",
    ".json",
    ".conf",
    ".txt",
    ".toml",
    "",
}
# Kept out of the release repo by the private sync's exclude list (house research tools / the old voice file).
_NOT_SHIPPED = {
    "kaare_core/tools/wiki_structured_store.py",
    "scripts/wiki_indexer.py",
    "scripts/wiki_indexer_bge.py",
    "services/qdrant/docker-compose-wiki.yml",
    "services/voice/configs/voice_settings.yaml",
}


def test_no_house_values_in_shipped_files():
    files = [
        p
        for d in _SHIPPED
        for p in (ROOT / d).rglob("*")
        if p.is_file() and not (set(p.parts) & _SKIP_PARTS) and p.suffix in _SUFFIXES
    ]
    files += [
        p
        for p in ROOT.glob("*")
        if p.is_file() and (p.suffix in {".py", ".yml"} or p.name.startswith("Dockerfile"))
    ]
    hits = []
    for p in files:
        rel = str(p.relative_to(ROOT))
        if rel in _NOT_SHIPPED:
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if _HOUSE_VALUE.search(line):
                hits.append(f"{rel}:{i}: {line.strip()[:120]}")
    assert hits == [], (
        "house values in shipped files (use 192.168.0.x / a config value):\n" + "\n".join(hits)
    )


# ── configs/ untracked: product content ships in configs_default/ ─────────────


def test_product_content_has_defaults():
    d = ROOT / "configs_default"
    assert len(list((d / "personalities").glob("*.md"))) >= 12
    assert len(list((d / "meeting_leder").glob("*.md"))) >= 24
    for name in (
        "personality_core.md",
        "personality_core_letvekt.md",
        "personality_core_minimal.md",
        "personality_behavior.md",
        "personality.md",
    ):
        for suffix in ("", "_en", "_de"):
            if name == "personality.md" and suffix:
                continue
            assert (d / name.replace(".md", f"{suffix}.md")).is_file(), name + suffix


def test_migrate_copies_missing_non_yaml_and_never_overwrites(monkeypatch, tmp_path):
    defaults, configs = tmp_path / "configs_default", tmp_path / "configs"
    (defaults / "personalities").mkdir(parents=True)
    configs.mkdir()
    (defaults / "personalities/standard.md").write_text("default", encoding="utf-8")
    (defaults / "personality_core.md").write_text("default core", encoding="utf-8")
    (defaults / "x.yaml").write_text("a: 1\n", encoding="utf-8")
    (configs / "personality_core.md").write_text("user edit", encoding="utf-8")
    rep = cm.run(configs, defaults)  # P134: one runner copies every missing file, yaml included
    assert (configs / "personalities/standard.md").read_text() == "default"
    assert (configs / "personality_core.md").read_text() == "user edit"
    assert (configs / "x.yaml").read_text() == "a: 1\n"
    assert len([c for c in rep.changes if c.startswith("create")]) == 2
