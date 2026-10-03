import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import handler


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._respond(handler.handle(""))

    def do_POST(self):
        # W1 payload (runbook §28): of-watchdog forwards the body chunked, with no
        # Content-Length, so the CPU-bound wrapper's read saw 0 bytes. Read both forms.
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            parts = []
            while True:
                n = int(self.rfile.readline().split(b";")[0].strip(), 16)
                if n == 0:
                    self.rfile.readline()
                    break
                parts.append(self.rfile.read(n))
                self.rfile.readline()
            raw = b"".join(parts)
        else:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
        self._respond(handler.handle(raw.decode("utf-8")))

    def _respond(self, text):
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


# ThreadingHTTPServer so concurrent invocations run in parallel (each does its
# own 5 ms spin), mirroring Fn's per-invocation concurrency model.
# Note: upstream runs on 8082, NOT 8081 - of-watchdog 0.9.x binds its metrics
# listener to 8081, so binding python there causes "Address in use" at startup.
ThreadingHTTPServer(("127.0.0.1", 8082), Handler).serve_forever()
