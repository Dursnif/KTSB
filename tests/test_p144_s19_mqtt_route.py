"""P144-s19: the second face sensor's topics ride on the existing MQTT connection — routed to their handler, never into
the Frigate event path; Frigate's own topic prefix is read from the Frigate settings (item 6) with the old key as the
fallback. No broker here."""

import asyncio

from adapters import mqtt_adapter


def test_extra_topic_goes_to_its_handler_not_to_frigate(monkeypatch):
    got = []
    monkeypatch.setattr(mqtt_adapter, "_TOPIC_HANDLERS", [])
    mqtt_adapter.register_topic_handler(lambda: ["dt/matches/#"], lambda t: t.startswith("dt/"),
                                        lambda t, p: got.append((t, p)))
    mqtt_adapter.register_topic_handler(lambda: ["dt/matches/#"], lambda t: t.startswith("dt/"),
                                        lambda t, p: got.append((t, p)))
    assert len(mqtt_adapter._TOPIC_HANDLERS) == 2  # two different lambdas — a real re-register is one (below)
    monkeypatch.setattr(mqtt_adapter, "_TOPIC_HANDLERS", [])
    topics, handles = (lambda: ["dt/matches/#"]), (lambda t: t.startswith("dt/"))

    def on_message(t, p):
        got.append((t, p))
    mqtt_adapter.register_topic_handler(topics, handles, on_message)
    mqtt_adapter.register_topic_handler(topics, handles, on_message)
    assert len(mqtt_adapter._TOPIC_HANDLERS) == 1
    assert mqtt_adapter._handler_topics() == {"dt/matches/#"}
    got.clear()
    assert mqtt_adapter._route_extra("dt/matches/x", b"{}") is True
    assert mqtt_adapter._route_extra("frigate/events", b"{}") is False
    assert got == [("dt/matches/x", b"{}")]


def test_a_failing_handler_never_raises(monkeypatch):
    monkeypatch.setattr(mqtt_adapter, "_TOPIC_HANDLERS", [])

    def boom(t, p):
        raise RuntimeError("x")
    mqtt_adapter.register_topic_handler(lambda: (_ for _ in ()).throw(RuntimeError()), lambda t: True, boom)
    assert mqtt_adapter._handler_topics() == set()
    assert mqtt_adapter._route_extra("dt/x", b"") is True


def test_frigate_topic_prefix_moves_to_the_frigate_settings(monkeypatch):
    cfg = {"mqtt": {"host": "h", "topic_prefix": "old"}, "frigate": {"mqtt_topic_prefix": "cams"}}
    monkeypatch.setattr(mqtt_adapter, "_svc", lambda name: cfg.get(name))
    assert mqtt_adapter._load_mqtt_config()["topic_events"] == "cams/events"
    cfg["frigate"] = {}
    assert mqtt_adapter._load_mqtt_config()["topic_events"] == "old/events"  # an install from before
    cfg["mqtt"] = {"host": "h"}
    assert mqtt_adapter._load_mqtt_config()["topic_events"] == "frigate/events"


def test_subscription_follows_the_switch(monkeypatch):
    wanted = {"on": ["dt/available"]}
    monkeypatch.setattr(mqtt_adapter, "_TOPIC_HANDLERS", [])
    monkeypatch.setattr(mqtt_adapter, "_RESUB_S", 0)
    mqtt_adapter.register_topic_handler(lambda: wanted["on"], lambda t: False, lambda t, p: None)

    class Client:
        def __init__(self):
            self.log = []

        async def subscribe(self, t):
            self.log.append(("sub", t))

        async def unsubscribe(self, t):
            self.log.append(("unsub", t))

    async def main():
        c = Client()
        subscribed: set[str] = set()
        task = asyncio.create_task(mqtt_adapter._keep_extra_subscriptions(c, subscribed))
        await asyncio.sleep(0.01)
        wanted["on"] = []
        await asyncio.sleep(0.01)
        task.cancel()
        return c.log
    log = asyncio.run(main())
    assert ("sub", "dt/available") in log and ("unsub", "dt/available") in log
