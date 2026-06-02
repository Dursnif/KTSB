"""
HTTP push client for remote inner voice machines.
When push_url is configured, runners POST results to the KTSB API
instead of writing state files directly (useful when running on a
separate device without a direct filesystem mount).
"""
import json
import sys
import urllib.request
from urllib.error import URLError


def push_thought(push_url: str, push_token: str, source: str, content: str) -> None:
    """
    POST generated thoughts to the KTSB inner-voices push endpoint.
    source: "jing" or "jang"
    Logs errors to stderr but never raises — push failures must not crash the loop.
    """
    payload = json.dumps({"source": source, "content": content}).encode()
    req = urllib.request.Request(
        push_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {push_token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                print(
                    f"[{source}] Push returned HTTP {resp.status}",
                    file=sys.stderr, flush=True,
                )
    except URLError as e:
        print(f"[{source}] Push failed: {e}", file=sys.stderr, flush=True)
