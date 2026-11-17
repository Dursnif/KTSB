"""P144-s16 (found after restart 2026-10-28): test runs wrote 40 fake Frigate memory lines into the live
state/stm_users/frigate.json — a real STMRegistry (left in app_state by a test importing kaare_api) autosaves to the
hardcoded live path, in a thread that swallows errors. No STM a test creates may autosave under /kaare/state."""

from kaare_core.memory.short_term import ShortTermMemory, STMRegistry


def test_registry_autosave_never_points_at_live_state():
    stm = STMRegistry().get("frigate")  # no dialog added: nothing is written, only the path is checked
    assert not str(stm._autosave_path).startswith("/kaare/state/")


def test_direct_autosave_to_live_state_is_redirected():
    stm = ShortTermMemory(user_id="x")
    stm.configure_autosave("/kaare/state/stm_users/x.json")
    assert not str(stm._autosave_path).startswith("/kaare/state/")
