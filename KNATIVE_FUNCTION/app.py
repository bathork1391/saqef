from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import time


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 0.005:
            pass
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
