#!/usr/bin/env python3
"""CLI debug tool for Kaare voice assistant.

Usage:
    cli.py say "Hva er klokka?"
    cli.py say "Skru på lyset" --server ws://192.168.87.242:8765
    cli.py say "Hei" --satellite living-room
    cli.py chat                        # interactive multi-turn mode
    cli.py chat --server ws://myserver:8765
    cli.py --list-devices              # list audio output devices
    cli.py say "Hei" --device 2        # use specific output device
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

import websockets

# Global output device (set via --device flag)
_output_device: int | None = None


async def send_text(
    server: str, text: str, satellite_id: str = "cli-debug",
) -> None:
    """Send text to the server and print responses."""
    async with websockets.connect(server) as ws:
        await ws.send(json.dumps({
            "type": "text_input",
            "text": text,
            "satellite_id": satellite_id,
        }))

        # Collect responses until server sends "done"
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=120)
            msg = json.loads(raw)
            if msg.get("type") == "done":
                break
            _print_response(msg)


async def chat_mode(server: str, satellite_id: str = "cli-debug") -> None:
    """Interactive multi-turn conversation."""
    print(f"Kobler til {server} (satellite={satellite_id})")
    if _output_device is not None:
        import sounddevice as sd
        dev = sd.query_devices(_output_device)
        print(f"Lydutgang: {dev['name']} (device {_output_device})")
    print("Skriv 'q' for a avslutte.\n")

    async with websockets.connect(server) as ws:
        while True:
            try:
                text = input("\033[1mDu:\033[0m ")
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if text.strip().lower() in ("q", "quit", "exit"):
                break

            if not text.strip():
                continue

            await ws.send(json.dumps({
                "type": "text_input",
                "text": text,
                "satellite_id": satellite_id,
            }))

            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=120)
                msg = json.loads(raw)
                if msg.get("type") == "done":
                    break
                _print_response(msg)


def _play_audio(payload_b64: str, sample_rate: int = 22050) -> None:
    """Decode and play base64 PCM audio."""
    try:
        import base64
        import numpy as np
        import sounddevice as sd

        pcm = base64.b64decode(payload_b64)
        if len(pcm) < 1000:
            return
        audio_i16 = np.frombuffer(pcm, dtype=np.int16)
        audio_f32 = audio_i16.astype(np.float32) / 32768.0
        duration_s = len(audio_f32) / sample_rate
        print(f"  \033[2m[spiller {duration_s:.1f}s lyd]\033[0m")
        sd.play(audio_f32, samplerate=sample_rate, device=_output_device, blocking=True)
    except Exception as e:
        print(f"  \033[2m[kunne ikke spille lyd: {e}]\033[0m")


def _print_response(msg: dict) -> None:
    """Pretty-print a server response message."""
    msg_type = msg.get("type")

    if msg_type == "intent":
        action = msg.get("action", "?")
        confidence = msg.get("confidence", "?")
        response = msg.get("response_text", "")
        entities = msg.get("entities", {})

        print(f"\033[1mKaare:\033[0m {response}")
        details = f"  \033[2m[{action}] conf={confidence}"
        if entities:
            details += f" {entities}"
        details += "\033[0m"
        print(details)

    elif msg_type == "audio_response":
        _play_audio(
            msg.get("payload", ""),
            msg.get("sample_rate", 22050),
        )

    elif msg_type == "transcript":
        # Only show if it differs from what we sent (shouldn't for text_input)
        pass

    elif msg_type == "listen":
        timeout = msg.get("timeout_s", 10)
        print(f"  \033[2m[venter pa svar... {timeout}s]\033[0m")

    else:
        print(f"  \033[2m[{msg_type}]: {json.dumps(msg, ensure_ascii=False)}\033[0m")


def main() -> None:
    global _output_device

    parser = argparse.ArgumentParser(description="Kaare CLI debug tool")
    parser.add_argument(
        "--server", default="ws://192.168.87.242:8765",
        help="WebSocket server URL (default: ws://192.168.87.242:8765)",
    )
    parser.add_argument(
        "--satellite", default="cli-debug",
        help="Satellite ID for conversation tracking",
    )
    parser.add_argument(
        "--device", type=int, default=None,
        help="Audio output device index (use --list-devices to see options)",
    )
    parser.add_argument(
        "--list-devices", action="store_true",
        help="List audio devices and exit",
    )

    sub = parser.add_subparsers(dest="command")

    say_p = sub.add_parser("say", help="Send a single utterance")
    say_p.add_argument("text", help="Text to send")

    sub.add_parser("chat", help="Interactive multi-turn conversation")

    args = parser.parse_args()

    if args.list_devices:
        import sounddevice as sd
        print(sd.query_devices())
        return

    _output_device = args.device

    if args.command == "say":
        asyncio.run(send_text(args.server, args.text, args.satellite))
    elif args.command == "chat":
        asyncio.run(chat_mode(args.server, args.satellite))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
