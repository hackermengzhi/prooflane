"""Session HTTP API under test. Run with: python service.py --port 8000"""

import argparse
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USERS = {"alice": "Alice-pass-123!", "bob": "Bob-pass-456!"}
MAX_FAILURES = 5
TOKEN_LIFETIME = 60


class SessionHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, body):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return None

    def authenticate(self):
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            return None
        token = authorization[len("Bearer ") :]
        session = self.server.sessions.get(token)
        if session is None:
            return None
        if self.server.clock() >= session["expires_at"]:
            return None
        return token

    def login(self, body):
        user = body.get("user")
        password = body.get("password")
        if not isinstance(user, str) or not user or not isinstance(password, str) or not password:
            self.respond(400, {"error": "user and password must be nonempty strings"})
            return
        if user not in USERS:
            self.respond(401, {"error": "invalid credentials"})
            return
        failures = self.server.failures.get(user, 0)
        if failures >= MAX_FAILURES:
            self.respond(423, {"error": "account locked"})
        elif password != USERS[user]:
            self.server.failures[user] = failures + 1
            self.respond(401, {"error": "invalid credentials"})
        else:
            self.server.failures[user] = 0
            token = secrets.token_urlsafe(32)
            self.server.sessions[token] = {
                "user": user,
                "expires_at": self.server.clock() + TOKEN_LIFETIME,
            }
            self.respond(200, {"token": token})

    def do_GET(self):
        if self.path != "/me":
            self.respond(404, {"error": "not found"})
            return
        with self.server.lock:
            token = self.authenticate()
            if token is None:
                self.respond(401, {"error": "unauthorized"})
            else:
                self.respond(200, {"user": self.server.sessions[token]["user"]})

    def do_POST(self):
        body = self.read_body()
        if not isinstance(body, dict):
            self.respond(400, {"error": "invalid json"})
            return
        with self.server.lock:
            if self.path == "/login":
                self.login(body)
            elif self.path == "/logout":
                token = self.authenticate()
                if token is None:
                    self.respond(401, {"error": "unauthorized"})
                else:
                    self.server.sessions.pop(token)
                    self.respond(200, {"ok": True})
            else:
                self.respond(404, {"error": "not found"})


def create_server(host="127.0.0.1", port=0, *, clock=time.monotonic):
    server = ThreadingHTTPServer((host, port), SessionHandler)
    server.failures = {}
    server.sessions = {}
    server.clock = clock
    server.lock = threading.Lock()
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    create_server(port=parser.parse_args().port).serve_forever()
