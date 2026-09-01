#!/usr/bin/env python3
"""
Dummy static-site server for testing web_search_adapter.py's fetch+extract
pipeline (trafilatura) against controlled, reproducible content instead of
the real internet. Not meant to be search-engine-discoverable — add its
address to configs/trusted_sources.yaml and call _fetch_content() directly.

Usage: python3 dummy_websites_server.py [port]  (default port: 8899)
"""
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PAGES = {
    "/artikkel-1": (
        "Test: Norsk vinterklima",
        """<html><head><title>Norsk vinterklima</title></head><body>
        <article>
        <h1>Norsk vinterklima</h1>
        <p>Norge har store regionale forskjeller i vinterklima. Kystklimaet langs
        Vestlandet er mildt og nedbørsrikt, mens innlandet og Finnmarksvidda kan
        oppleve temperaturer ned mot minus tretti grader celsius i kalde perioder.</p>
        <p>Snømengden varierer sterkt fra ar til ar, og pavirkes av blant annet
        NAO-indeksen (Nord-Atlanteren-oscillasjonen), som styrer hvor lavtrykkbanene
        legger seg gjennom vintersesongen.</p>
        </article>
        </body></html>"""
    ),
    "/artikkel-2": (
        "Test: Hjemmeautomasjon",
        """<html><head><title>Hjemmeautomasjon</title></head><body>
        <article>
        <h1>Hjemmeautomasjon i moderne hjem</h1>
        <p>Smarthusteknologi lar beboere styre lys, varme og sikkerhet fra en
        sentral enhet eller app. Protokoller som Zigbee og Matter gjor det mulig
        for enheter fra ulike produsenter a snakke sammen uten en felles sky-tjeneste.</p>
        <p>Lokal styring, uten avhengighet til internett, regnes av mange som et
        viktig prinsipp for palitelighet og personvern.</p>
        </article>
        </body></html>"""
    ),
}

INDEX = """<html><head><title>Dummy Test-nettsider</title></head><body>
<h1>Dummy Test-nettsider — KTSB testing</h1>
<ul>{}</ul>
</body></html>""".format(
    "".join(f'<li><a href="{path}">{title}</a></li>' for path, (title, _) in PAGES.items())
)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            body = INDEX
        elif self.path in PAGES:
            body = PAGES[self.path][1]
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, fmt, *args):
        print(f"[dummy-websites] {self.address_string()} - {fmt % args}", flush=True)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    print(f"[dummy-websites] Serving {len(PAGES)} pages on 0.0.0.0:{port}", flush=True)
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()
