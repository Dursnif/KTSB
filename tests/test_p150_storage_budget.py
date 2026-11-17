"""P150 phase 2 — one storage budget for every data class that grows by itself (2026-11-13).

- Every class sits in one of the Basic groups (user files · logs · Frigate pictures · Frigate text · Kåre's own files) and
  reports its size; a class with no size control yet says so (honest GUI), it never pretends to have one.
- Disk facts per folder at read time («KTSB er satt opp til å bruke x GB av y GB»), and a warning when the limits on one disk
  add up to more than that disk can give.
- A Basic group limit is shared proportionally («forholdsmessig») — every member with a limit scales by the same factor.
- Upgrade guard: a default limit never cuts below what Argus held when the limit first met it, until the user saves one.
- Bug: the snapshot usage endpoint read a hardcoded house folder instead of the store's folder.
Everything runs on tmp folders — never the house.
"""

import asyncio
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

from kaare_core import config, config_store
from kaare_core.domain import frigate_store
from kaare_core.frigate_user import settings as fu_settings
from kaare_core.retention import argus_size
from kaare_core.routers import router_settings as rs
from kaare_core.routers import router_system
from kaare_core.storage import budget, limits, rawlog

MB = 1_048_576


def _write(path: Path, nbytes: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * nbytes)


@pytest.fixture
def house(tmp_path, monkeypatch):
    """A fake install: state/, logs/, configs/ in tmp — every path the budget reads."""
    state, logs, cfg = tmp_path / "state", tmp_path / "logs", tmp_path / "configs"
    for d in (state, logs, cfg):
        d.mkdir()
    (cfg / "settings.yaml").write_text(yaml.dump({
        "images": {"max_per_user_mb": 200, "max_per_user_count": 500},
        "storage_limits": {"argus_events_mb": 2048},
    }), encoding="utf-8")
    (cfg / "frigate_cameras.yaml").write_text(yaml.dump({"storage": {"snapshots_max_mb": 1000}}), encoding="utf-8")
    (cfg / "frigate_user.yaml").write_text(yaml.dump({"episode_frames_max_mb": 400, "episode_frames_keep_mb": 600}),
                                           encoding="utf-8")
    monkeypatch.setattr(budget, "STATE_DIR", state)
    monkeypatch.setattr(budget, "LOGS_DIR", logs)
    monkeypatch.setattr(config, "CONFIG_DIR", cfg)
    monkeypatch.setattr(config_store, "CONFIG_DIR", cfg)
    monkeypatch.setattr(rs, "_SETTINGS_PATH", cfg / "settings.yaml")
    monkeypatch.setattr(frigate_store, "CONFIG_PATH", cfg / "frigate_cameras.yaml")
    monkeypatch.setattr(fu_settings, "CONFIG_PATH", cfg / "frigate_user.yaml", raising=False)
    monkeypatch.setattr(fu_settings, "_path", lambda: cfg / "frigate_user.yaml")
    frigate_store.load_camera_config()
    snaps = state / "frigate_snapshots"
    monkeypatch.setattr(frigate_store, "SNAPSHOT_DIR", snaps)
    monkeypatch.setattr(argus_size, "measure", lambda: {"bytes": 300 * MB, "points": 1000, "source": "telemetry"})
    monkeypatch.setattr(budget, "_qdrant_collection_bytes", lambda name: 4 * MB)
    _write(logs / "ha_events.log", 3 * MB)
    _write(logs / "llm_calls.log", 1 * MB)
    _write(snaps / "a.jpg", 2 * MB)
    _write(snaps / "episodes" / "ep1" / "f.bin", 1 * MB)
    _write(state / "images" / "userone" / "p.png", 1 * MB)
    _write(state / "memory" / "learning.db", 1 * MB)
    _write(state / "think_cache.jsonl", MB // 2)
    yield tmp_path
    frigate_store.load_camera_config()


def _by_key(rep):
    return {c["key"]: c for c in rep["classes"]}


# ── the registry: every class in a Basic group, size measured, honest about control ─

def test_every_class_is_in_one_of_the_five_groups(house):
    rep = budget.report()
    groups = {g["key"] for g in rep["groups"]}
    assert groups == {"user_files", "logs", "frigate_images", "frigate_text", "kare_files"}
    assert all(c["group"] in groups for c in rep["classes"])


def test_sizes_are_measured(house):
    c = _by_key(budget.report())
    # P150 phase 5: ha_events.log is a raw stream now (its own class) — «logs» is the rest (was 4.0 with it)
    assert c["logs"]["used_mb"] == pytest.approx(1.0, abs=0.01)
    assert c["frigate_snapshots"]["used_mb"] == pytest.approx(2.0, abs=0.01), "episodes/ is not a snapshot"
    assert c["episode_frames"]["used_mb"] == pytest.approx(1.0, abs=0.01)
    assert c["argus"]["used_mb"] == pytest.approx(300.0)
    assert c["images"]["used_mb"] == pytest.approx(1.0, abs=0.01)


def test_limits_come_from_the_existing_settings(house):
    c = _by_key(budget.report())
    assert c["argus"]["limit_mb"] == 2048
    assert c["frigate_snapshots"]["limit_mb"] == 1000
    assert c["episode_frames"]["limit_mb"] == 600
    assert c["images"]["limit_mb"] == 200 and c["images"]["per_user"] is True


def test_a_class_without_control_says_so(house):
    c = _by_key(budget.report())
    assert c["logs"]["controlled"] is False and c["logs"]["limit_mb"] is None
    assert c["argus"]["controlled"] is True


# ── disk facts and over-commit ────────────────────────────────────────────────

def test_disk_facts_per_folder(house):
    rep = budget.report()
    total, _, free = shutil.disk_usage(house)
    disk = next(d for d in rep["disks"] if "logs" in d["classes"])
    assert disk["total_gb"] == pytest.approx(total / 1024**3, abs=0.1)
    assert disk["free_gb"] == pytest.approx(free / 1024**3, abs=0.5)
    assert disk["limits_gb"] >= 0


def test_over_commit_warning_when_limits_exceed_the_disk(house, monkeypatch):
    real = budget._disk_usage
    monkeypatch.setattr(budget, "_disk_usage", lambda p: (real(p)[0], 0, 1 * MB))  # 1 MB free
    rep = budget.report()
    disk = next(d for d in rep["disks"] if "frigate_snapshots" in d["classes"])
    assert disk["over_commit"] is True


def test_no_over_commit_with_room(house, monkeypatch):
    monkeypatch.setattr(budget, "_disk_usage", lambda p: (10_000 * 1024**3, 0, 9_000 * 1024**3))
    rep = budget.report()
    assert not any(d["over_commit"] for d in rep["disks"])


# ── Basic group limit, proportional ───────────────────────────────────────────
# 2026-11-13: «frigate ting skal i frigate fanen under integrasjoner» — a limit that already has a home in the GUI is
# set there only; this tab shows its usage and points to that place (no second field for the same setting).

def test_limits_with_a_home_elsewhere_are_not_set_from_this_tab(house):
    with pytest.raises(ValueError):
        budget.set_group_limit("frigate_images", 800)
    with pytest.raises(ValueError):
        budget.set_class_limit("frigate_snapshots", 800)
    with pytest.raises(ValueError):
        budget.set_class_limit("images", 100)
    c = _by_key(budget.report())
    assert c["frigate_snapshots"]["limit_mb"] == 1000 and c["frigate_snapshots"]["home"] == "frigate"
    assert c["images"]["home"] == "images"
    assert c["argus"]["home"] == ""


def test_group_limit_scales_every_member_by_the_same_factor(monkeypatch):
    saved = {}
    fake = [
        budget.DataClass("a", "logs", list, size=lambda: 0, get_limit=lambda: 1000.0,
                         set_limit=lambda mb: saved.__setitem__("a", mb)),
        budget.DataClass("b", "logs", list, size=lambda: 0, get_limit=lambda: 600.0,
                         set_limit=lambda mb: saved.__setitem__("b", mb)),
        budget.DataClass("off", "logs", list, size=lambda: 0, get_limit=lambda: 0.0,
                         set_limit=lambda mb: saved.__setitem__("off", mb)),
        budget.DataClass("none", "logs", list, size=lambda: 0),
    ]
    monkeypatch.setattr(budget, "classes", lambda: fake)
    budget.set_group_limit("logs", 800)  # half of 1600
    assert saved == {"a": 500.0, "b": 300.0}, "0 = off and classes without control are left alone"


def test_unknown_group_is_refused_and_logs_scale_proportionally(house):
    # P150 phase 5: the logs group has limits now (the raw streams), so it no longer serves as «a group without control»
    with pytest.raises(ValueError):
        budget.set_group_limit("no_such_group", 500)
    budget.set_group_limit("logs", 500)  # 700 + 300 defaults → half
    assert limits.get_mb("raw_ha_events_mb") == 350 and limits.get_mb("raw_frigate_mqtt_mb") == 150


def test_the_raw_ha_stream_is_measured_as_its_own_class(house):
    legacy = rawlog.LEGACY["ha_events"]
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b"x" * 3 * MB)
    c = _by_key(budget.report())
    assert c["raw_ha_events"]["used_mb"] == pytest.approx(3.0, abs=0.01)


def test_class_limit_in_expert_mode(house):
    budget.set_class_limit("argus", 4096)
    assert argus_size.get_limit_mb() == 4096


# ── upgrade guard ─────────────────────────────────────────────────────────────

def test_a_default_never_cuts_below_what_existed_until_the_user_saves(tmp_path):
    db = tmp_path / "rollup.db"
    # first meeting: Argus already holds 5 GB, the limit is the 2 GB default nobody chose
    eff = argus_size.effective_limit_mb(2048, size_bytes=5 * 1024 * MB, confirmed=False, db_path=db)
    assert eff == pytest.approx(5 * 1024, abs=1)
    # later nights keep that floor (it does not grow with the data)
    eff = argus_size.effective_limit_mb(2048, size_bytes=6 * 1024 * MB, confirmed=False, db_path=db)
    assert eff == pytest.approx(5 * 1024, abs=1)
    # once the user saves a limit in the GUI, it applies as written
    assert argus_size.effective_limit_mb(2048, size_bytes=6 * 1024 * MB, confirmed=True, db_path=db) == 2048


def test_gui_save_marks_the_argus_limit_confirmed(house):
    asyncio.run(rs.api_put_disk_usage({"argus_events_mb": 3000}, _u=None))
    data = yaml.safe_load((house / "configs" / "settings.yaml").read_text(encoding="utf-8"))
    assert data["storage_limits"]["argus_events_mb"] == 3000
    assert data["storage_limits"]["argus_events_confirmed"] is True
    assert argus_size.limit_confirmed() is True


# ── API ───────────────────────────────────────────────────────────────────────

def test_disk_usage_api_carries_groups_classes_and_disks(house):
    got = asyncio.run(rs.api_get_disk_usage(_u=None))
    assert {"argus", "groups", "classes", "disks"} <= set(got)


def test_disk_usage_api_sets_a_group(house):
    got = asyncio.run(rs.api_put_disk_usage({"group": "kare_files", "limit_mb": 3000}, _u=None))
    assert next(g for g in got["groups"] if g["key"] == "kare_files")["limit_mb"] == 3000


def test_disk_usage_api_refuses_a_frigate_group(house):
    with pytest.raises(HTTPException):
        asyncio.run(rs.api_put_disk_usage({"group": "frigate_images", "limit_mb": 800}, _u=None))


# ── bug: snapshot usage read a hardcoded house folder ─────────────────────────

def test_snapshot_usage_reads_the_store_folder(house):
    got = asyncio.run(router_system.api_get_cameras_storage_usage(_u=None))
    assert got["snapshots_mb"] == pytest.approx(2.0, abs=0.01)


# ── DT04 R0.1b: Docker Desktop — every bind mount is its own mount of the same disk ─

_HOUSE_MOUNTS = ("/kaare/logs", "/kaare/state")


def _bind_mounts(house):
    """Each top folder (state/, logs/) looks like its own mount, as on Docker Desktop's bind mounts. A class path the
    fixture does not move (it points at the real install) is «/elsewhere» — another disk, left out of the asserts."""
    def mount_of(p):
        try:
            rel = Path(p).resolve().relative_to(house.resolve())
        except ValueError:
            return "/elsewhere"
        top = rel.parts[0] if rel.parts else "state"  # a probe on the install root counts with state/
        return f"/kaare/{'logs' if top == 'logs' else 'state'}"
    return mount_of


def _house_disks(rep):
    return [d for d in rep["disks"] if set(d.get("mounts") or [d["mount"]]) & set(_HOUSE_MOUNTS)]


def test_bind_mounts_of_one_disk_are_one_disk(house, monkeypatch):
    monkeypatch.setattr(budget, "_mount_of", _bind_mounts(house))
    disks = _house_disks(budget.report())
    assert len(disks) == 1
    assert {"logs", "frigate_snapshots"} <= set(disks[0]["classes"])
    assert sorted(disks[0]["mounts"]) == list(_HOUSE_MOUNTS)


def test_bind_mounts_with_their_own_device_ids_are_one_disk_when_size_and_free_match(house, monkeypatch):
    """A file-sharing layer (grpcfuse/virtiofs) may give each mount its own st_dev — the same total and free is the disk."""
    mount_of = _bind_mounts(house)
    monkeypatch.setattr(budget, "_mount_of", mount_of)
    monkeypatch.setattr(budget, "_device_of", lambda p: hash(mount_of(p)), raising=False)
    assert len(_house_disks(budget.report())) == 1


def test_the_limits_of_one_disk_are_counted_once_not_per_mount(house, monkeypatch):
    mount_of = _bind_mounts(house)
    single = budget.report()
    monkeypatch.setattr(budget, "_mount_of", mount_of)
    split = budget.report()
    one = next(d for d in single["disks"] if "logs" in d["classes"])
    assert sum(d["limits_gb"] for d in _house_disks(split)) == pytest.approx(one["limits_gb"])


def test_two_real_disks_stay_two(house, monkeypatch):
    mount_of = _bind_mounts(house)
    monkeypatch.setattr(budget, "_mount_of", mount_of)
    monkeypatch.setattr(budget, "_device_of", lambda p: hash(mount_of(p)), raising=False)
    sizes = {"/kaare/logs": (500 * 1024**3, 0, 100 * 1024**3), "/kaare/state": (2000 * 1024**3, 0, 900 * 1024**3),
             "/elsewhere": (4000 * 1024**3, 0, 10 * 1024**3)}
    monkeypatch.setattr(budget, "_disk_usage", lambda p: sizes[mount_of(p)])
    assert len(_house_disks(budget.report())) == 2


def test_over_commit_is_judged_on_the_whole_disk_not_per_mount(house, monkeypatch):
    """Split over two mounts, each half of the limits fits in the free space, the sum does not → still a warning."""
    mount_of = _bind_mounts(house)
    single = budget.report()
    one = next(d for d in single["disks"] if "logs" in d["classes"])
    free = (one["limits_gb"] * 0.75 - one["used_by_ktsb_gb"]) * 1024**3  # between the biggest half and the whole
    real = budget._disk_usage
    monkeypatch.setattr(budget, "_disk_usage", lambda p: (real(p)[0], 0, max(int(free), 1)))
    monkeypatch.setattr(budget, "_mount_of", mount_of)
    split = _house_disks(budget.report())
    assert len(split) == 1 and split[0]["over_commit"] is True
