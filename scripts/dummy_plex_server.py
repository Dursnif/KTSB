#!/usr/bin/env python3
"""
Dummy Plex Media Server stub for testing adapters/plex_adapter.py's functions
against a controlled fake server, without a real Plex account/claim token.
Implements the exact endpoints plex_adapter.py calls, returning
Plex-API-shaped JSON (MediaContainer wrapper) with one fake movie, one fake
show/season/episode, one fake session, one fake client, and playback that
always "succeeds".

Usage: python3 dummy_plex_server.py [port]  (default port: 32499)
Point configs/services.yaml media.plex.url at http://<host>:<port>
Any non-empty PLEX_TOKEN in configs/plex.env satisfies the adapter (this
stub does not check the token value).
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

MACHINE_ID = "dummy0000-0000-0000-0000-000000000001"

MOVIE = {
    "ratingKey": "1001", "type": "movie", "title": "Test-filmen",
    "year": 2024, "librarySectionTitle": "Filmer",
}
SHOW = {"ratingKey": "2001", "type": "show", "title": "Test-serien"}
SEASON = {
    "ratingKey": "2002", "type": "season", "title": "Sesong 1",
    "parentIndex": 1, "leafCount": 2, "viewedLeafCount": 1,
}
EPISODE = {
    "ratingKey": "2003", "type": "episode", "title": "Test-episoden",
    "grandparentTitle": "Test-serien", "parentIndex": 1, "index": 1,
    "duration": 1320000, "viewCount": 0,
}
CLIENT = {
    "name": "Stue-TV (dummy)", "product": "Plex for Android TV",
    "platform": "Android", "machineIdentifier": "dummy-client-0001",
}


def container(**extra):
    return {"MediaContainer": {"size": 1, **extra}}


class Handler(BaseHTTPRequestHandler):
    def _json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/":
            self._json(container(machineIdentifier=MACHINE_ID, friendlyName="Dummy Plex"))
        elif path == "/sessions":
            sess = {
                **EPISODE, "User": {"title": "Testbruker"},
                "Player": {"title": "Stue-TV (dummy)", "state": "playing"},
                "viewOffset": 300000,
            }
            self._json(container(Metadata=[sess]))
        elif path == "/accounts":
            self._json(container(Account=[{"id": 1, "name": "Testbruker"}]))
        elif path == "/status/sessions/history/all":
            hist = {**MOVIE, "accountID": 1, "viewedAt": 1735689600, "deviceID": "dummy"}
            self._json(container(Metadata=[hist]))
        elif path == "/hubs/search":
            query = (qs.get("query") or [""])[0]
            self._json(container(Hub=[
                {"type": "movie", "Metadata": [MOVIE]},
                {"type": "show", "Metadata": [SHOW]},
            ]))
            print(f"[dummy-plex] search query={query!r}", flush=True)
        elif path == "/library/sections":
            self._json(container(Directory=[
                {"title": "Filmer", "type": "movie", "count": 1, "key": "1"},
                {"title": "TV-serier", "type": "show", "count": 1, "key": "2"},
            ]))
        elif path.startswith("/library/metadata/") and path.endswith("/children"):
            key = path.split("/")[3]
            if key == SHOW["ratingKey"]:
                self._json(container(title2="Test-serien", Metadata=[SEASON]))
            elif key == SEASON["ratingKey"]:
                self._json(container(title2="Sesong 1", Metadata=[EPISODE]))
            else:
                self._json(container(Metadata=[]))
        elif path.startswith("/library/metadata/"):
            key = path.split("/")[3]
            item = {MOVIE["ratingKey"]: MOVIE, SHOW["ratingKey"]: SHOW,
                    SEASON["ratingKey"]: SEASON, EPISODE["ratingKey"]: EPISODE}.get(key)
            self._json(container(Metadata=[item] if item else []))
        elif path == "/clients":
            self._json(container(Device=[CLIENT]))
        elif path == "/player/playback/playMedia":
            self.send_response(200)
            self.end_headers()
        else:
            self._json({"error": "not found"}, status=404)

    def log_message(self, fmt, *args):
        print(f"[dummy-plex] {self.address_string()} - {fmt % args}", flush=True)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 32499
    print(f"[dummy-plex] Fake Plex Media Server on 0.0.0.0:{port}", flush=True)
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()
