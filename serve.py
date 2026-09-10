"""Tiny static file server for local demo testing.

Sends no-cache headers so edits to index.html / styles.css / demo-config.js show up
on a normal reload. Usage:

    python serve.py            # http://127.0.0.1:5173
    python serve.py 8080       # choose a port
"""
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Expires", "0")
        super().end_headers()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5173
    print(f"Glamour Day Spa demo -> http://127.0.0.1:{port}  (Ctrl+C to stop)")
    HTTPServer(("127.0.0.1", port), NoCacheHandler).serve_forever()
