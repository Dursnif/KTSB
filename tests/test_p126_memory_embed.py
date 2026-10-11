"""Semantic memory model choice (P126): catalogue, active model, config migration, no fixed dimension."""

import importlib.util
import re
import sys
import types
from pathlib import Path

import yaml

import kaare_core.memory.memory_embed_models as mem
from kaare_core import config_migrations as cm
from kaare_core.voice import stt_models as stt
from kaare_core.voice import tts_models as tts

ROOT = Path(__file__).resolve().parent.parent


def test_catalogue_is_consistent():
    collections = [spec["collection"] for spec in mem.MODELS.values()]
    assert len(collections) == len(set(collections)), "each model needs its own collection"
    assert {spec["tier"] for spec in mem.MODELS.values()} >= {"small", "medium", "large"}
    assert mem.DEFAULT_MODEL in mem.MODELS and mem.MODELS[mem.DEFAULT_MODEL]["tier"] == "small"
    for key, spec in mem.MODELS.items():
        assert spec["dim"] > 0 and 0 < spec["min_score"] < 1, key
        if spec["backend"] == "local":
            assert spec["repo"] and set(spec["files"]) == {"model.onnx", "tokenizer.json"}, key
    # The old collection name stays with the old model, so existing installs keep their memory.
    assert mem.MODELS["minilm"]["collection"] == "kaare_memory"


def test_active_model_follows_config(monkeypatch):
    monkeypatch.setattr(mem, "get_memory_embed_config", lambda: {"model": "mpnet"})
    assert mem.active_model()["dim"] == 768
    # Pre-P126 config with model files → the old MiniLM fp32, not the new default.
    monkeypatch.setattr(mem, "get_memory_embed_config", lambda: {"model_dir": "/x"})
    assert mem.active_model_key() == "minilm"
    monkeypatch.setattr(mem, "get_memory_embed_config", dict)
    assert mem.active_model_key() == mem.DEFAULT_MODEL
    monkeypatch.setattr(mem, "get_memory_embed_config", lambda: {"model": "nonsense"})
    assert mem.active_model_key() == mem.DEFAULT_MODEL


def test_model_dir_remembers_previous_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(mem, "get_models_dir", lambda: tmp_path)
    monkeypatch.setattr(
        mem,
        "get_memory_embed_config",
        lambda: {
            "model": "mpnet",
            "model_dir": "/m/mpnet",
            "model_dirs": {"minilm": "/old/minilm"},
        },
    )
    assert mem.model_dir_for("mpnet") == Path("/m/mpnet")
    assert mem.model_dir_for("minilm") == Path("/old/minilm")
    assert mem.model_dir_for("minilm_int8") == tmp_path / "semantic-embed" / "minilm_int8"


def test_migration_keeps_legacy_model_and_fixes_hostname(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "_IN_DOCKER", True)  # large-v3 → '' is Docker-only since P134
    cfg = tmp_path / "services.yaml"
    cfg.write_text(
        yaml.dump(
            {
                "memory_embed": {
                    "enabled": True,
                    "model_dir": "/kaare/state/models/semantic-embed",
                },
                "internal": {"semantic_embed": "http://kaare-memory-embed:11500"},
                "voice": {"stt": {"faster_whisper_model": "large-v3"}},
            }
        )
    )
    data = yaml.safe_load(cfg.read_text())
    changed = cm._s1_memory_embed(data, {})  # P134: numbered step 1 (was _migrate_memory_embed)
    assert data["memory_embed"]["model"] == "minilm"
    assert data["internal"]["semantic_embed"] == "http://kaare-semantic-embed:11500"
    assert data["voice"]["stt"]["faster_whisper_model"] == ""
    assert changed


def test_migration_leaves_fresh_and_custom_values(tmp_path):
    cfg = tmp_path / "services.yaml"
    original = {
        "memory_embed": {"enabled": False, "model_dir": ""},
        "internal": {"semantic_embed": "http://127.0.0.1:11500"},
    }
    cfg.write_text(yaml.dump(original))
    data = yaml.safe_load(cfg.read_text())
    changed = cm._s1_memory_embed(data, {})
    assert data == original and not changed


def test_template_defaults():
    services = yaml.safe_load((ROOT / "configs_default/services.yaml").read_text())
    assert services["memory_embed"]["model"] == mem.DEFAULT_MODEL
    assert services["internal"]["semantic_embed"] == "http://kaare-semantic-embed:11500"
    settings = yaml.safe_load((ROOT / "configs_default/settings.yaml").read_text())
    assert settings["models_dir"] == "/kaare/models"


def test_no_fixed_vector_size_in_memory_code():
    """Rules 24/25: the vector size comes from the chosen model, never a constant."""
    for rel in (
        "kaare_core/memory/semantic_memory.py",
        "kaare_core/memory/semantic_reindex.py",
        "semantic_embed/local_embedder.py",
        "semantic_embed/server.py",
    ):
        src = (ROOT / rel).read_text()
        assert not re.search(r"\b(384|768|1024)\b", src), rel


# ── Whisper (faster-whisper) model choice ─────────────────────────────────────


def test_stt_repo_normalization_only_accepts_hf():
    assert stt.normalize_repo("NbAiLab/nb-whisper-small") == "NbAiLab/nb-whisper-small"
    assert (
        stt.normalize_repo("https://huggingface.co/Systran/faster-whisper-small/tree/main")
        == "Systran/faster-whisper-small"
    )
    assert stt.normalize_repo("large-v3") == "Systran/faster-whisper-large-v3"
    for bad in ("", "http://example.com/a/b", "a/b; rm -rf", "../x", "just-a-name"):
        assert stt.normalize_repo(bad) is None, bad


def test_stt_ct2_layout_check():
    assert stt.is_ct2_layout({"model.bin", "config.json", "vocabulary.json"})
    assert stt.is_ct2_layout({"model.bin", "config.json", "tokenizer.json"})
    assert not stt.is_ct2_layout({"openvino_encoder_model.xml", "config.json"})
    assert not stt.is_ct2_layout({"model.safetensors", "config.json", "tokenizer.json"})


def test_stt_presets_follow_language():
    assert all(p["repo"].startswith("NbAiLab/") for p in stt.presets_for("nb"))
    assert not any(p["repo"].startswith("NbAiLab/") for p in stt.presets_for("de"))
    for lang in ("nb", "en"):
        assert [p["tier"] for p in stt.presets_for(lang)] == ["small", "medium", "large"]


def test_stt_default_is_lightest_preset_for_language():
    assert stt.resolve_model("", "nb") == stt.presets_for("nb")[0]["repo"]
    assert stt.resolve_model("", "de") == stt.presets_for("de")[0]["repo"]
    assert stt.resolve_model("large-v3", "nb") == "Systran/faster-whisper-large-v3"
    assert stt.resolve_model("/models/my-ct2", "nb") == "/models/my-ct2"


def test_stt_cpu_threads_auto(monkeypatch):
    assert stt.cpu_threads(6) == 6
    for cores, expected in ((4, 2), (8, 4), (20, 8), (64, 8), (None, 2)):
        monkeypatch.setattr(stt.os, "cpu_count", lambda c=cores: c)
        assert stt.cpu_threads(0) == expected, cores


def test_semantic_embed_server_resolves_empty_model_dir(tmp_path, monkeypatch):
    """DT03 P126 test 2: an empty model_dir must mean the GUI download folder of the chosen model."""
    spec = importlib.util.spec_from_file_location("p126_server", ROOT / "semantic_embed/server.py")
    # The embed service's own venv has onnxruntime; this test only needs the config reading.
    monkeypatch.setitem(sys.modules, "local_embedder", types.SimpleNamespace(DEFAULT_MAX_LEN=128))
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    services, settings = tmp_path / "services.yaml", tmp_path / "settings.yaml"
    monkeypatch.setattr(server, "SERVICES_PATH", services)
    monkeypatch.setattr(server, "SETTINGS_PATH", settings)
    services.write_text(
        yaml.dump({"memory_embed": {"enabled": True, "model": "minilm_int8", "model_dir": ""}})
    )
    settings.write_text(yaml.dump({"models_dir": "/data/models"}))
    assert server._read_config() == (True, "/data/models/semantic-embed/minilm_int8")
    services.write_text(
        yaml.dump({"memory_embed": {"enabled": True, "model": "mpnet", "model_dir": "/x"}})
    )
    assert server._read_config() == (True, "/x")


def test_voice_bridge_reads_api_address_from_services_yaml():
    """DT03 2026-10-07: a fixed 127.0.0.1 in voice_settings.yaml broke voice in Docker (the API is
    another container). The address must come from services.yaml internal.kaare_api."""
    src = (ROOT / "services/voice/voice_bridge.py").read_text()
    assert '_svc.get("internal", {}).get("kaare_api")' in src
    assert 'cfg["kaare"]["api_url"]' not in src
    settings = yaml.safe_load((ROOT / "services/voice/configs/voice_settings.yaml").read_text())
    assert "api_url" not in settings["kaare"] and "ws_url" not in settings["kaare"]
    assert (
        yaml.safe_load((ROOT / "configs_default/services.yaml").read_text())["internal"][
            "kaare_api"
        ]
        == "http://kaare-api:8000"
    )


def test_piper_paths_follow_models_dir(monkeypatch, tmp_path):
    """Piper voices live under <models_dir>/voice/piper in the repo's own layout (was a hardcoded house path)."""
    monkeypatch.setattr(tts, "get_models_dir", lambda: tmp_path)
    assert (
        tts.piper_local_path("no_NO-talesyntese-medium")
        == tmp_path / "voice/piper/no/no_NO/talesyntese/medium/no_NO-talesyntese-medium.onnx"
    )
    assert tts.piper_hf_paths("de_DE-eva_k-x_low") == (
        "de/de_DE/eva_k/x_low/de_DE-eva_k-x_low.onnx",
        "de/de_DE/eva_k/x_low/de_DE-eva_k-x_low.onnx.json",
    )
    for lang, name in tts.DEFAULT_VOICES.items():
        assert name in {p["model"] for p in tts.PIPER_PRESETS[lang]}
    assert tts.tts_lang("nn") == "nb" and tts.tts_lang("de") == "de"
    assert (
        "/mnt/"
        not in (ROOT / "kaare_core/routers/router_settings.py")
        .read_text()
        .split("Piper TTS")[1][:3000]
    )
