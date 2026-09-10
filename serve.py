"""Tiny static file server for local demo testing.

Sends no-cache headers so edits to index.html / styles.css / demo-config.js show up
on a normal reload. Binds dual-stack so BOTH http://localhost:PORT and
http://127.0.0.1:PORT work (Retell's allowed-domain check is hostname-based).

    python serve.py            # port 5173
    python serve.py 8080       # choose a port
"""
import socket
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stdout.write("%s - %s\n" % (self.address_string(), fmt % args))
        sys.stdout.flush()


class DualStackServer(HTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self):
        # accept IPv6 and IPv4-mapped (so "localhost" works whether it resolves to ::1 or 127.0.0.1)
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError):
            pass
        super().server_bind()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5173
    try:
        httpd = DualStackServer(("::", port), NoCacheHandler)
    except OSError:
        httpd = HTTPServer(("0.0.0.0", port), NoCacheHandler)
    print(f"Glamour Day Spa demo -> http://localhost:{port}/  and  http://127.0.0.1:{port}/  (Ctrl+C to stop)")
    httpd.serve_forever()
