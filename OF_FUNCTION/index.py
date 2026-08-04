import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

import handler


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._respond(handler.handle(""))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else ""
        self._respond(handler.handle(body))

    def _respond(self, text):
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", 8081), Handler).serve_forever()
