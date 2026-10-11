"""P134 Phase 1 — kaare_core.config_store: atomic, locked, kept, comment-preserving, verified writes."""

import errno
import multiprocessing as mp
import os
import time
from pathlib import Path

import pytest
import yaml

from kaare_core import config_store as cs

DEFAULTS = Path("/kaare/configs_default")


# ── helpers run in child processes (module level so they pickle) ─────────────


def _increment(path: str, key: str, n: int) -> None:
    for _ in range(n):
        with cs.edit_yaml(Path(path), f"inc-{key}") as data:
            data[key] = int(data.get(key, 0)) + 1


def _writer_loop(path: str, n: int) -> None:
    for i in range(n):
        cs.write_yaml(Path(path), {"v": i, "pad": "x" * 20000, "end": i}, "loop")


def _hold_lock(path: str, started) -> None:
    with cs.lock(Path(path)):
        started.set()
        time.sleep(1.5)


def _reader_loop(path: str, seconds: float, out) -> None:
    bad = 0
    reads = 0
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        data = cs.read_yaml(Path(path))
        reads += 1
        if not data or data.get("v") != data.get("end") or len(data.get("pad", "")) != 20000:
            bad += 1
    out.put((reads, bad))


# ── atomic + lock ─────────────────────────────────────────────────────────────


def test_concurrent_processes_lose_no_update(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("# keep this comment\nstart: 1\n")
    ctx = mp.get_context("spawn")  # not fork: pytest is multi-threaded by now
    procs = [ctx.Process(target=_increment, args=(str(p), k, 40)) for k in ("a", "b", "c", "a")]
    for pr in procs:
        pr.start()
    for pr in procs:
        pr.join(60)
    data = yaml.safe_load(p.read_text())
    assert data == {"start": 1, "a": 80, "b": 40, "c": 40}
    assert "# keep this comment" in p.read_text()


def test_reader_never_sees_empty_or_partial(tmp_path):
    p = tmp_path / "r.yaml"
    cs.write_yaml(p, {"v": -1, "pad": "x" * 20000, "end": -1}, "init")
    ctx = mp.get_context("spawn")  # not fork: pytest is multi-threaded by now
    q = ctx.Queue()
    w = ctx.Process(target=_writer_loop, args=(str(p), 300))
    r = ctx.Process(target=_reader_loop, args=(str(p), 2.0, q))
    w.start()
    r.start()
    w.join(60)
    r.join(60)
    reads, bad = q.get(timeout=5)
    assert reads > 100
    assert bad == 0


def test_threads_share_lock_and_lock_is_reentrant(tmp_path):
    p = tmp_path / "t.yaml"
    with cs.lock(p), cs.edit_yaml(p, "nested") as data:
        data["x"] = 1
    assert cs.read_yaml(p) == {"x": 1}


def test_lock_timeout_when_other_process_holds(tmp_path):
    p = tmp_path / "l.yaml"
    ctx = mp.get_context("spawn")
    started = ctx.Event()
    pr = ctx.Process(target=_hold_lock, args=(str(p), started))
    pr.start()
    assert started.wait(5)
    with pytest.raises(cs.ConfigLockTimeout), cs.lock(p, timeout=0.3):
        pass
    pr.join(5)
    with cs.lock(p, timeout=1):
        pass


def test_exception_in_edit_writes_nothing(tmp_path):
    p = tmp_path / "e.yaml"
    p.write_text("a: 1\n")
    with pytest.raises(RuntimeError), cs.edit_yaml(p, "boom") as data:
        data["a"] = 2
        raise RuntimeError("caller failed")
    assert p.read_text() == "a: 1\n"
    assert cs.history(p) == []


def test_no_change_writes_nothing(tmp_path):
    p = tmp_path / "n.yaml"
    p.write_text("a: 1  # c\n")
    st = p.stat()
    with cs.edit_yaml(p, "noop"):
        pass
    assert p.stat().st_mtime_ns == st.st_mtime_ns and cs.history(p) == []


def test_permissions_kept_and_temp_files_gone(tmp_path):
    p = tmp_path / "s.env"
    p.write_text("A=1\n")
    os.chmod(p, 0o640)
    cs.set_env_keys(p, {"A": "2", "B": "3"}, "gui")
    assert p.read_text() == "A=2\nB=3\n"
    assert p.stat().st_mode & 0o777 == 0o640
    assert not [x for x in tmp_path.iterdir() if x.name.endswith(".tmp")]


# ── history ───────────────────────────────────────────────────────────────────


def test_history_keeps_newest_n_and_corrupt_copies(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "HISTORY_KEEP", 3)
    p = tmp_path / "h.yaml"
    p.write_text("")
    cs.write_yaml(p, {"i": 0}, "repair")  # empty file → kept as _corrupt
    for i in range(1, 7):
        cs.write_yaml(p, {"i": i}, f"w{i}")
    names = [h.name for h in cs.history(p)]
    assert len([n for n in names if cs.CORRUPT_TAG in n]) == 1
    normal = [n for n in names if cs.CORRUPT_TAG not in n]
    assert len(normal) == 3
    assert normal[0].endswith("_w6.yaml")  # newest first; holds the value before the 6th write
    assert yaml.safe_load((cs.history_dir(p) / normal[0]).read_text()) == {"i": 5}


def test_snapshot_copies_tree_without_history_or_locks(tmp_path):
    (tmp_path / "a.yaml").write_text("a: 1\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.md").write_text("b")
    cs.write_yaml(tmp_path / "a.yaml", {"a": 2}, "x")
    snap = cs.snapshot("pre-migrate-v1", tmp_path)
    assert (snap / "a.yaml").read_text() == "a: 2\n"
    assert (snap / "sub" / "b.md").read_text() == "b"
    assert not (snap / cs.HISTORY_DIRNAME).exists() and not (snap / cs.LOCK_DIRNAME).exists()


# ── corrupt / empty ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("content", ["", "   \n", "a: [unclosed\n", "- just\n- a list\n"])
def test_edit_refuses_corrupt_and_leaves_it(tmp_path, content):
    p = tmp_path / "x.yaml"
    p.write_text(content)
    with pytest.raises(cs.ConfigCorruptError), cs.edit_yaml(p, "gui"):
        pass
    assert p.read_text() == content


def test_replace_corrupt_keeps_the_original(tmp_path):
    p = tmp_path / "x.yaml"
    p.write_text("a: [unclosed\n")
    kept = cs.replace_corrupt(p, {"a": 1}, "migrate")
    assert kept is not None and kept.read_text() == "a: [unclosed\n" and cs.CORRUPT_TAG in kept.name
    assert cs.read_yaml(p) == {"a": 1}


def test_ensure_file_never_overwrites(tmp_path):
    src = tmp_path / "src.yaml"
    src.write_text("t: 1\n")
    tgt = tmp_path / "tgt.yaml"
    cs.ensure_file(tgt, src)
    assert tgt.read_text() == "t: 1\n"
    tgt.write_text("user: 2\n")
    cs.ensure_file(tgt, src)
    assert tgt.read_text() == "user: 2\n"
    assert cs.ensure_file(tmp_path / "x.yaml", tmp_path / "missing.yaml") == tmp_path / "x.yaml"


# ── YAML fidelity ─────────────────────────────────────────────────────────────

RISKY = {
    "on": "on",
    "yes": "yes",
    "no": "no",
    "off": "off",
    "y": "y",
    "n": "n",
    "sexa": "1:30",
    "octal": "0123",
    "float": "1e3",
    "tilde": "~",
    "null": "null",
    "true": "True",
    "date": "2026-10-09",
    "empty": "",
    "space": " lead",
    "colon": "a: b",
    "hash": "a #b",
    "multi": "line1\nline2\n",
    "num": 5,
    "bool": False,
    "none": None,
    "f": 0.5,
    "list": ["on", 1, None],
}


def test_strings_pyyaml_would_misread_are_quoted(tmp_path):
    p = tmp_path / "q.yaml"
    p.write_text("# head\nx: 1  # tail\n")
    with cs.edit_yaml(p, "t") as data:
        data.update(RISKY)
    back = yaml.safe_load(p.read_text())
    assert back == {"x": 1, **RISKY}
    assert "# head" in p.read_text() and "# tail" in p.read_text()
    assert "%YAML" not in p.read_text()


def test_yaml11_values_in_user_file_keep_their_meaning(tmp_path):
    p = tmp_path / "u.yaml"
    p.write_text("enabled: yes\nmode: on\nt: 1:30\nk: 'yes'\n")
    ref = yaml.safe_load(p.read_text())
    with cs.edit_yaml(p, "t") as data:
        data["new"] = 1
    back = yaml.safe_load(p.read_text())
    del back["new"]
    assert back == ref == {"enabled": True, "mode": True, "t": 90, "k": "yes"}


@pytest.mark.parametrize("src", sorted(DEFAULTS.glob("*.yaml")), ids=lambda p: p.name)
def test_every_template_round_trips_value_identical(tmp_path, src):
    p = tmp_path / src.name
    p.write_bytes(src.read_bytes())
    ref = yaml.safe_load(src.read_text())
    if not isinstance(ref, dict):
        pytest.skip("not a mapping")
    comments = src.read_text().count("#")
    with cs.edit_yaml(p, "t") as data:
        data["__p134_probe__"] = "on"
    back = yaml.safe_load(p.read_text())
    assert back.pop("__p134_probe__") == "on"
    assert cs.same_data(back, ref)
    assert p.read_text().count("#") >= comments


def test_write_yaml_keeps_comments_of_surviving_keys(tmp_path):
    p = tmp_path / "w.yaml"
    p.write_text("# top\na: 1  # about a\nb:\n  c: 2  # about c\ngone: 3\n")
    cs.write_yaml(p, {"a": 1, "b": {"c": 5}, "new": [1, 2]}, "gui")
    text = p.read_text()
    assert yaml.safe_load(text) == {"a": 1, "b": {"c": 5}, "new": [1, 2]}
    assert "# top" in text and "# about a" in text


def test_update_yaml_accepts_replacement(tmp_path):
    p = tmp_path / "f.yaml"
    p.write_text("a: 1\nb: 2\n")
    out = cs.update_yaml(p, lambda d: {"a": 9}, "fn")
    assert out == {"a": 9} and cs.read_yaml(p) == {"a": 9}


def test_dump_verify_refuses_unrepresentable():
    with pytest.raises(Exception):  # noqa: B017 — ruamel raises its own representer error or ConfigVerifyError
        cs.dump_yaml({"x": object()})


# ── reads ─────────────────────────────────────────────────────────────────────


def test_read_missing_and_empty(tmp_path):
    assert cs.read_yaml(tmp_path / "none.yaml") == {}
    assert cs.read_yaml(tmp_path / "none.yaml", default={"d": 1}) == {"d": 1}
    (tmp_path / "e.yaml").write_text("")
    assert cs.read_yaml(tmp_path / "e.yaml") == {}


def test_read_missing_never_written_does_not_wait(tmp_path):
    t = time.monotonic()
    for _ in range(50):
        cs.read_yaml(tmp_path / "never.yaml")
    assert time.monotonic() - t < 0.2


def test_read_retries_transient_enodata(tmp_path, monkeypatch):
    p = tmp_path / "x.yaml"
    cs.write_yaml(p, {"a": 1}, "init")
    real = Path.read_bytes
    calls = {"n": 0}

    def flaky(self):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise OSError(errno.ENODATA, "No data available")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", flaky)
    assert cs.read_yaml(p) == {"a": 1}
    assert calls["n"] == 3


def test_cached_read_sees_every_write(tmp_path):
    p = tmp_path / "c.yaml"
    cs.write_yaml(p, {"a": 1}, "w")
    assert cs.read_yaml_cached(p) == {"a": 1}
    for i in range(2, 6):
        cs.write_yaml(p, {"a": i}, "w")
        assert cs.read_yaml_cached(p) == {"a": i}
    got = cs.read_yaml_cached(p)
    got["a"] = "mutated"
    assert cs.read_yaml_cached(p) == {"a": 5}


def test_cached_read_keeps_last_good_on_corrupt(tmp_path):
    p = tmp_path / "c.yaml"
    cs.write_yaml(p, {"a": 1}, "w")
    assert cs.read_yaml_cached(p) == {"a": 1}
    p.write_text("a: [broken\n")
    assert cs.read_yaml_cached(p) == {"a": 1}


# ── load_for_update + three-way write (two GUI saves at once) ─────────────────


def test_two_interleaved_saves_both_survive(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("# doc\na:\n  x: 1\n  y: 1\nb: 1\nc: [1, 2]\n")
    first = cs.load_for_update(p)
    second = cs.load_for_update(p)
    first["a"]["x"] = 2
    del first["b"]
    second["a"]["y"] = 3
    second["c"] = [9]
    second["new"] = "on"
    cs.write_yaml(p, first, "gui:first")
    cs.write_yaml(p, second, "gui:second")
    assert cs.read_yaml(p) == {"a": {"x": 2, "y": 3}, "c": [9], "new": "on"}
    assert "# doc" in p.read_text()


def test_same_key_last_writer_wins(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("a: 1\n")
    one, two = cs.load_for_update(p), cs.load_for_update(p)
    one["a"] = 2
    two["a"] = 3
    cs.write_yaml(p, one, "1")
    cs.write_yaml(p, two, "2")
    assert cs.read_yaml(p) == {"a": 3}


def test_untouched_load_for_update_writes_nothing(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("a: 1\n")
    doc = cs.load_for_update(p)
    p.write_text("a: 5\n")  # someone else saved in between
    assert cs.write_yaml(p, doc, "noop") is False
    assert cs.read_yaml(p) == {"a": 5}


def test_load_for_update_missing_file(tmp_path):
    p = tmp_path / "new.yaml"
    doc = cs.load_for_update(p)
    doc["k"] = {"v": 1}
    cs.write_yaml(p, doc, "create")
    assert cs.read_yaml(p) == {"k": {"v": 1}}


def _gui_save(path: str, key: str, n: int) -> None:
    for i in range(n):
        doc = cs.load_for_update(Path(path))
        doc.setdefault("section", {})[key] = i
        cs.write_yaml(Path(path), doc, f"gui-{key}")


def test_concurrent_gui_saves_on_different_keys_all_survive(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_text("section: {}\n")
    ctx = mp.get_context("spawn")  # not fork: pytest is multi-threaded by now
    procs = [ctx.Process(target=_gui_save, args=(str(p), k, 30)) for k in ("a", "b", "c", "d")]
    for pr in procs:
        pr.start()
    for pr in procs:
        pr.join(60)
    assert cs.read_yaml(p)["section"] == {"a": 29, "b": 29, "c": 29, "d": 29}


def test_round_trip_reader_agrees_with_pyyaml_on_tricky_scalars(tmp_path):
    p = tmp_path / "t.yaml"
    text = (
        "# keep\na:\n  x: 1\n  y: 1\n  n: no\n  t: 1:30\n  o: on\n  d: 2026-10-09\n  dt: 2026-10-09 10:00:00\n"
        "  f: 1e3\n  f2: 1.0e+3\n  oc: 0123\n  h: 0x1F\n  nul: ~\n  inf: .inf\n  under: 1_000\nb: Yes\nN: NULL\n"
    )
    p.write_text(text)
    ref = yaml.safe_load(text)
    with cs.edit_yaml(p, "t") as data:
        data["z"] = 1
    back = yaml.safe_load(p.read_text())
    assert back.pop("z") == 1 and back == ref
    assert "# keep" in p.read_text()  # no fallback to comment-less save
