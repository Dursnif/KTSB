"""P142 ## Bugs — replay could not see long replies: think_cache cut `response` at 500 chars.
Raised to 4000 (2026-10-22). Kåre's own reading of his think blocks must keep working (D9)."""

import asyncio

import pytest
import yaml
from fastapi import HTTPException

import kaare_core.config as cfg
import kaare_core.routers.router_settings as rs
from kaare_core import log_buffer
from kaare_core.tools import think_cache


@pytest.fixture
def cache(monkeypatch, tmp_path):
    path = tmp_path / "think_cache.jsonl"
    monkeypatch.setattr(think_cache, "_CACHE_PATH", path)
    return path


def _entries(path):
    log_buffer.flush(path)
    return think_cache.read_think_history(n=10)


def test_long_reply_is_kept_up_to_4000_chars(cache):
    reply = "x" * 3000
    think_cache.log_think(think_text="tenker", response=reply, role="kare", model="m", rid="r")
    assert _entries(cache)[0]["response"] == reply


def test_reply_is_still_capped(cache):
    think_cache.log_think(think_text="tenker", response="y" * 9000, role="kare", model="m", rid="r")
    assert len(_entries(cache)[0]["response"]) == 4000


def test_kare_still_reads_his_think_blocks(cache):
    think_cache.log_think(
        think_text="Jeg sjekker agendaen først.", response="z" * 3500, role="kare", model="m", rid="r"
    )
    out = think_cache.format_for_kare(_entries(cache))
    assert "agendaen" in out
    assert "z" * 600 not in out, "Kåre's view shows the thinking, not the whole reply"


# ── GUI setting (2026-10-22: «la det bli en gui redigering også») ────────

@pytest.mark.parametrize(
    ("content", "expected"),
    [({}, 4000), ({"think_cache": {"response_max_chars": 8000}}, 8000),
     ({"think_cache": {"response_max_chars": 10}}, 500), ({"think_cache": {"response_max_chars": 10**6}}, 20000)],
)
def test_accessor_reads_and_clamps(monkeypatch, tmp_path, content, expected):
    (tmp_path / "settings.yaml").write_text(yaml.dump(content), encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "_think_resp_cache", {"mtime": None, "chars": 4000})
    assert cfg.get_think_response_max_chars() == expected


@pytest.fixture
def settings_file(monkeypatch, tmp_path):
    p = tmp_path / "settings.yaml"
    p.write_text(yaml.dump({"think_cache": {"max_entries": 150}, "other": 1}), encoding="utf-8")
    monkeypatch.setattr(rs, "_SETTINGS_PATH", p)
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "_think_resp_cache", {"mtime": None, "chars": 4000})
    return p


def test_api_shows_and_saves_the_length(settings_file):
    d = asyncio.run(rs.api_get_logs_and_data(_u=None))
    assert d["think_response_max_chars"] == 4000
    assert d["defaults"]["think_response_max_chars"] == 4000
    assert (d["limits"]["think_response_min"], d["limits"]["think_response_max"]) == (500, 20000)
    out = asyncio.run(rs.api_put_logs_and_data({"think_response_max_chars": 6000}, _u=None))
    assert out["think_response_max_chars"] == 6000
    data = yaml.safe_load(settings_file.read_text(encoding="utf-8"))
    assert data["think_cache"] == {"max_entries": 150, "response_max_chars": 6000}
    assert data["other"] == 1


@pytest.mark.parametrize("bad", [100, 50000, "mye", None])
def test_api_rejects_bad_length(settings_file, bad):
    with pytest.raises(HTTPException) as e:
        asyncio.run(rs.api_put_logs_and_data({"think_response_max_chars": bad}, _u=None))
    assert e.value.status_code == 400
    assert "response_max_chars" not in yaml.safe_load(settings_file.read_text(encoding="utf-8"))["think_cache"]


def test_shipped_default():
    tpl = cfg.Path(__file__).resolve().parents[1] / "configs_default" / "settings.yaml"
    assert yaml.safe_load(tpl.read_text(encoding="utf-8"))["think_cache"]["response_max_chars"] == 4000
