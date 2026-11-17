"""MQTT settings saved in the GUI are used without a restart (DT04-S05d, rule 25).

The listener is the camera pipe's lifeline, so: it reconnects only when a connection key really changed, never on
other saves; a bad host gives an honest status without a crash or a tight loop; a new password is used.
"""

import asyncio
from pathlib import Path
from typing import ClassVar

import pytest

import adapters.mqtt_adapter as m


class _FakeClient:
    instances: ClassVar[list] = []
    fail_hosts: ClassVar[set] = set()

    def __init__(self, **kw):
        self.kw = kw
        self.closed = False
        self._stop = asyncio.Event()
        _FakeClient.instances.append(self)

    async def __aenter__(self):
        if self.kw["hostname"] in _FakeClient.fail_hosts:
            raise OSError(f"cannot reach {self.kw['hostname']}")
        return self

    async def __aexit__(self, *exc):
        self.closed = True
        self._stop.set()
        return False

    async def subscribe(self, topic):
        return None

    async def unsubscribe(self, topic):
        return None

    @property
    def messages(self):
        async def _gen():
            await self._stop.wait()
            if False:
                yield None

        return _gen()


@pytest.fixture
def env(tmp_path, monkeypatch):
    svc = {"mqtt": {"host": "broker-a", "port": 1883, "reconnect_interval": 30}, "frigate": {}}

    def fake_svc(section, key=None):
        sec = svc.get(section, {})
        return sec if key is None else sec.get(key)

    _FakeClient.instances = []
    _FakeClient.fail_hosts = set()
    envfile = tmp_path / "mqtt.env"
    envfile.write_text("MQTT_USER=u\nMQTT_PASSWORD=old\n", encoding="utf-8")
    monkeypatch.setattr(m, "_svc", fake_svc)
    monkeypatch.setattr(m, "_MQTT_ENV_PATH", envfile)
    monkeypatch.setattr(m.aiomqtt, "Client", _FakeClient)
    monkeypatch.setattr(m, "_write_event_log", lambda *a, **k: None)
    monkeypatch.setattr(m, "_load_zigbee_awareness", list)
    monkeypatch.setattr(m, "_handler_topics", lambda: set())
    monkeypatch.setattr(m, "_STATUS", {"connected": False, "last_event_ts": None, "events_received": 0})
    return svc, envfile


async def _settle(n=20):
    for _ in range(n):
        await asyncio.sleep(0)


def _saved(name="services.yaml"):
    m._on_config_written(Path("/somewhere") / name)


def test_changed_host_reconnects_to_the_new_host(env):
    svc, _ = env

    async def run():
        task = asyncio.create_task(m.run_mqtt_listener())
        await _settle()
        assert [c.kw["hostname"] for c in _FakeClient.instances] == ["broker-a"]
        svc["mqtt"]["host"] = "broker-b"
        _saved()
        await _settle()
        task.cancel()
        return [c.kw["hostname"] for c in _FakeClient.instances], _FakeClient.instances[0].closed

    hosts, first_closed = asyncio.run(run())
    assert hosts == ["broker-a", "broker-b"]
    assert first_closed


def test_unchanged_mqtt_keys_do_not_disconnect(env):
    svc, _ = env

    async def run():
        task = asyncio.create_task(m.run_mqtt_listener())
        await _settle()
        svc["web_search"] = {"provider": "x"}  # another save of services.yaml
        _saved()
        _saved("settings.yaml")  # not an MQTT file at all
        await _settle()
        state = (len(_FakeClient.instances), _FakeClient.instances[0].closed, dict(m._STATUS))
        task.cancel()
        return state

    count, closed, status = asyncio.run(run())
    assert count == 1 and not closed
    assert status["connected"] is True


def test_bad_host_gives_honest_status_and_no_tight_loop(env, monkeypatch):
    _FakeClient.fail_hosts = {"broker-a"}
    waits = []
    real_wait_for = asyncio.wait_for

    async def fake_wait_for(aw, timeout):
        waits.append(timeout)
        return await real_wait_for(aw, 0.001)

    monkeypatch.setattr(m.asyncio, "wait_for", fake_wait_for)

    async def run():
        task = asyncio.create_task(m.run_mqtt_listener())
        await asyncio.sleep(0.05)
        status = dict(m._STATUS)
        task.cancel()
        return status

    status = asyncio.run(run())
    assert status["connected"] is False
    assert "broker-a" in status["error"]
    assert waits and all(w >= 1 for w in waits)  # always waits reconnect_interval (min 1 s), never spins


def test_new_password_is_used(env, monkeypatch):
    _, envfile = env
    monkeypatch.setenv("MQTT_PASSWORD", "old")
    envfile.write_text("MQTT_USER=u\nMQTT_PASSWORD=new\n", encoding="utf-8")
    assert m._load_mqtt_config()["password"] == "new"


def test_new_password_reconnects(env):
    _, envfile = env

    async def run():
        task = asyncio.create_task(m.run_mqtt_listener())
        await _settle()
        envfile.write_text("MQTT_USER=u\nMQTT_PASSWORD=new\n", encoding="utf-8")
        _saved("mqtt.env")
        await _settle()
        task.cancel()
        return [c.kw["password"] for c in _FakeClient.instances]

    assert asyncio.run(run()) == ["old", "new"]


def test_no_host_waits_and_connects_when_one_is_saved(env):
    svc, _ = env
    svc["mqtt"]["host"] = ""

    async def run():
        task = asyncio.create_task(m.run_mqtt_listener())
        await _settle()
        before = len(_FakeClient.instances)
        svc["mqtt"]["host"] = "broker-c"
        _saved()
        await _settle()
        task.cancel()
        return before, [c.kw["hostname"] for c in _FakeClient.instances]

    before, hosts = asyncio.run(run())
    assert before == 0 and hosts == ["broker-c"]
