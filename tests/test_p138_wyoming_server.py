"""P138 — Wyoming server: reply events (routed/error/audio), rate conversion, server-side end of speech.

Needs the `wyoming` package, which only the voice venv has; skipped elsewhere. Run with
`PYTHONPATH=/kaare:<voice pytest env>/lib services/voice/venv/bin/python -m pytest …`.
"""

import asyncio

import pytest

pytest.importorskip("wyoming")

from kaare_core.voice import wyoming_server as ws

# ── Wyoming reply events ──────────────────────────────────────────────────────


class FakeWriter:
    def __init__(self):
        self.buf = bytearray()

    def write(self, data):
        self.buf.extend(data)

    def writelines(self, lines):
        for line in lines:
            self.buf.extend(line)

    async def drain(self):
        return None

    def types(self) -> list[str]:
        """Read the bytes back with the wyoming library itself (header line + data/payload segments)."""

        async def read() -> list[str]:
            reader = asyncio.StreamReader()
            reader.feed_data(bytes(self.buf))
            reader.feed_eof()
            out = []
            while (event := await ws.async_read_event(reader)) is not None:
                out.append(event.type)
            return out

        return asyncio.run(read())


def test_routed_reply_sends_routed_event_and_bare_audio_stop():
    w = FakeWriter()
    asyncio.run(ws._deliver_reply(w, asyncio.Lock(), ws.WyomingReply(routed_to=["nest"])))
    assert w.types() == [ws.ROUTED_EVENT, "audio-stop"]


def test_unreachable_without_speaker_sends_error_then_stop():
    w = FakeWriter()
    reply = ws.WyomingReply(error_code=ws.ERROR_OUTPUT_UNREACHABLE, error_text="Nest svarer ikke")
    asyncio.run(ws._deliver_reply(w, asyncio.Lock(), reply))
    assert w.types() == ["error", "audio-stop"]


def test_reply_on_satellite_sends_audio(tmp_path, monkeypatch):
    wav = tmp_path / "a.wav"
    with ws.wave.open(str(wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 1600)

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(ws.asyncio, "sleep", no_sleep)
    w = FakeWriter()
    asyncio.run(ws._deliver_reply(w, asyncio.Lock(), ws.WyomingReply(play_here=wav)))
    assert w.types()[0] == "audio-start" and w.types()[-1] == "audio-stop"
    assert not wav.exists()


def test_is_satellite_connected():
    ws._active_connections["10.9.9.9"] = (None, None, None)
    try:
        assert ws.is_satellite_connected("10.9.9.9")
        assert not ws.is_satellite_connected("10.9.9.8")
        assert not ws.is_satellite_connected("")
    finally:
        ws._active_connections.pop("10.9.9.9", None)


# ── Phase 3: server-side end of speech ────────────────────────────────────────

RATE = 16000
CHUNK_SEC = 0.1


def tone(level: float, seconds: float) -> list[bytes]:
    """Square-ish int16 signal with mean |x| ≈ level (0..1), split in 100 ms chunks."""
    amp = int(level * 32767)
    n = int(RATE * CHUNK_SEC)
    chunk = b"".join(
        int.to_bytes(amp if i % 2 else -amp, 2, "little", signed=True) for i in range(n)
    )
    return [chunk] * round(seconds / CHUNK_SEC)


def feed_until_end(det, chunks) -> int | None:
    for i, c in enumerate(chunks):
        if det.feed(c):
            return i
    return None


def test_silence_after_speech_ends_round():
    det = ws.SilenceDetector(1.0, RATE, 2)
    chunks = tone(0.002, 0.5) + tone(0.1, 1.5) + tone(0.002, 2.0)
    end = feed_until_end(det, chunks)
    assert det.reason == "silence"
    assert end is not None and 29 <= end <= 31  # 0.5 + 1.5 + ~1.0 s


def test_short_pause_does_not_end():
    det = ws.SilenceDetector(1.0, RATE, 2)
    chunks = tone(0.002, 0.3) + tone(0.1, 1.0) + tone(0.002, 0.5) + tone(0.1, 1.0)
    assert feed_until_end(det, chunks) is None


def test_quiet_mic_still_detected():
    det = ws.SilenceDetector(1.0, RATE, 2)
    chunks = tone(0.0005, 0.5) + tone(0.01, 1.0) + tone(0.0005, 1.5)
    assert feed_until_end(det, chunks) is not None and det.reason == "silence"


def test_nothing_said_ends_with_no_speech():
    det = ws.SilenceDetector(1.0, RATE, 2, no_speech_sec=2.0)
    assert feed_until_end(det, tone(0.002, 3.0)) is not None
    assert det.reason == "no_speech"


def _serve(server):
    async def main(client_fn):
        srv = await asyncio.start_server(server._handle_client, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        try:
            return await client_fn(port)
        finally:
            srv.close()
            await srv.wait_closed()

    return main


def test_server_vad_sends_voice_stopped_and_runs_pipeline():
    seen = {}

    async def stt(audio, rate):
        seen["stt_bytes"] = len(audio)
        return "hei"

    async def kaare(text, peer):
        return "svar"

    async def route(answer, peer):
        seen["answer"] = answer
        return ws.WyomingReply(routed_to=["nest"])

    server = ws.WyomingServer(
        "127.0.0.1", 0, stt, kaare, lambda t: None, route_fn=route, silence_sec=0.5
    )

    async def client(port):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        start = ws.AudioStart(rate=RATE, width=2, channels=1).event()
        start.data[ws.SERVER_VAD_KEY] = ws.SERVER_VAD_VALUE
        await ws.async_write_event(start, writer)
        for c in tone(0.002, 0.3) + tone(0.1, 0.6) + tone(0.002, 1.0):
            await ws.async_write_event(
                ws.AudioChunk(rate=RATE, width=2, channels=1, audio=c).event(), writer
            )
        got = []
        while True:
            ev = await asyncio.wait_for(ws.async_read_event(reader), 5)
            got.append(ev.type)
            if ev.type == "audio-stop":
                break
        writer.close()
        return got

    got = asyncio.run(_serve(server)(client))
    assert got[0] == "voice-stopped"
    assert "transcript" in got and ws.ROUTED_EVENT in got and got[-1] == "audio-stop"
    assert seen["answer"] == "svar" and seen["stt_bytes"] > 0


def test_satellite_without_server_vad_unchanged():
    calls = []

    async def stt(audio, rate):
        calls.append("stt")
        return ""

    server = ws.WyomingServer("127.0.0.1", 0, stt, None, lambda t: None, silence_sec=0.5)

    async def client(port):
        _reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await ws.async_write_event(ws.AudioStart(rate=RATE, width=2, channels=1).event(), writer)
        for c in tone(0.002, 0.3) + tone(0.1, 0.6) + tone(0.002, 1.0):
            await ws.async_write_event(
                ws.AudioChunk(rate=RATE, width=2, channels=1, audio=c).event(), writer
            )
        await asyncio.sleep(0.2)
        assert calls == []  # no early end without kare_vad
        await ws.async_write_event(ws.AudioStop().event(), writer)
        await asyncio.sleep(0.3)
        writer.close()

    asyncio.run(_serve(server)(client))
    assert calls == ["stt"]


def test_reply_converted_to_satellite_speaker_rate(tmp_path, monkeypatch):
    wav = tmp_path / "b.wav"
    with ws.wave.open(str(wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(22050)
        wf.writeframes(b"\x01\x00" * 22050)

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(ws.asyncio, "sleep", no_sleep)
    w = FakeWriter()
    asyncio.run(
        ws._deliver_reply(w, asyncio.Lock(), ws.WyomingReply(play_here=wav), out_rate=16000)
    )

    async def read():
        reader = asyncio.StreamReader()
        reader.feed_data(bytes(w.buf))
        reader.feed_eof()
        events = []
        while (ev := await ws.async_read_event(reader)) is not None:
            events.append(ev)
        return events

    events = asyncio.run(read())
    start = ws.AudioStart.from_event(events[0])
    chunks = [ws.AudioChunk.from_event(e) for e in events if e.type == "audio-chunk"]
    assert start.rate == 16000 and all(c.rate == 16000 for c in chunks)
    total = sum(len(c.audio) for c in chunks) // 2
    assert 15500 <= total <= 16500  # one second at 16 kHz
