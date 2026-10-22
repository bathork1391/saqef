# W6 (runbook §38): the OpenFaaS upstream server for the w6_* handlers. handler.handle returns
# (HTTP status, text); otherwise this is OF_FUNCTION/index.py unchanged.
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import handler


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._respond(*handler.handle(""))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else ""
        self._respond(*handler.handle(body))

    def _respond(self, status, text):
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("127.0.0.1", 8082), Handler).serve_forever()
