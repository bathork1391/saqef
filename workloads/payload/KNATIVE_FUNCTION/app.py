from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os


class Handler(BaseHTTPRequestHandler):
    # W1 payload echo (runbook §28): return the request body unchanged.
    def do_GET(self):
        self._respond(b"")

    def do_POST(self):
        # Read chunked bodies too (of-watchdog sends them; a proxy may as well).
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            parts = []
            while True:
                n = int(self.rfile.readline().split(b";")[0].strip(), 16)
                if n == 0:
                    self.rfile.readline()
                    break
                parts.append(self.rfile.read(n))
                self.rfile.readline()
            self._respond(b"".join(parts))
        else:
            length = int(self.headers.get("Content-Length", 0))
            self._respond(self.rfile.read(length) if length else b"")

    def _respond(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
