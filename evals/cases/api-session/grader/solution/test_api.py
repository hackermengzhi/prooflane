"""Reference answer used only to validate the grader; never copied into agent workspaces."""

import io
import json
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from service import create_server

PASSWORDS = {"alice": "Alice-pass-123!", "bob": "Bob-pass-456!"}
UNAUTHORIZED = (401, {"error": "unauthorized"})


class SessionApiTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.responses = []
        self.base = self.start_server()

    def start_server(self):
        server = create_server("127.0.0.1", 0, clock=lambda: self.now)
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def call(self, path, body=None, *, authorization=None, base=None):
        if isinstance(body, bytes):
            data = body
        else:
            data = None if body is None else json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
        if authorization is not None:
            headers["Authorization"] = authorization
        request = Request((base or self.base) + path, data=data, headers=headers)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            text = response.read().decode()
            self.responses.append(str(response.headers) + text)
            return response.status, json.loads(text)

    def login(self, user="alice", password=None):
        return self.call(
            "/login", {"user": user, "password": PASSWORDS[user] if password is None else password}
        )

    def token(self, user="alice"):
        status, body = self.login(user)
        self.assertEqual(status, 200)
        token = body["token"]
        self.assertIsInstance(token, str)
        self.assertGreaterEqual(len(token), 32)
        self.assertNotEqual(token, user)
        self.assertNotIn(PASSWORDS[user], token)
        return token

    def me(self, token):
        return self.call("/me", authorization=f"Bearer {token}")

    def logout(self, token):
        return self.call("/logout", {}, authorization=f"Bearer {token}")

    def test_s1_login_issues_distinct_opaque_tokens(self):
        first, second = self.token(), self.token()
        self.assertNotEqual(first, second)
        for token in (first, second):
            self.assertEqual(self.me(token), (200, {"user": "alice"}))
        self.assertEqual(self.me(self.token("bob")), (200, {"user": "bob"}))

    def test_s1_wrong_password_and_unknown_user_are_indistinguishable(self):
        wrong = self.login(password="wrong-password")
        unknown = self.call("/login", {"user": "nobody", "password": "wrong-password"})
        self.assertEqual(wrong, (401, {"error": "invalid credentials"}))
        self.assertEqual(unknown, wrong)

    def test_s1_invalid_login_fields_do_not_count_as_password_failures(self):
        bodies = [
            b"{",
            b"null",
            [],
            {},
            {"user": "alice"},
            {"password": "wrong-password"},
            {"user": "alice", "password": ""},
            {"user": "", "password": "wrong-password"},
            {"user": 1, "password": "wrong-password"},
            {"user": "alice", "password": 1},
            {"user": "alice", "password": None},
        ]
        for body in bodies:
            self.assertEqual(self.call("/login", body)[0], 400, body)
        self.token()

    def test_s2_fifth_failure_locks_only_that_user(self):
        existing = self.token()
        for attempt in range(5):
            self.assertEqual(self.login(password="wrong-password")[0], 401, attempt + 1)
        self.assertEqual(self.login(), (423, {"error": "account locked"}))
        self.assertEqual(self.login(password="wrong-password")[0], 423)
        self.assertEqual(self.me(existing), (200, {"user": "alice"}))
        self.assertEqual(self.me(self.token("bob")), (200, {"user": "bob"}))

    def test_s2_success_resets_failure_count(self):
        for cycle in range(2):
            for attempt in range(4):
                self.assertEqual(self.login(password="wrong-password")[0], 401, (cycle, attempt))
            self.token()

    def test_s3_missing_unknown_and_malformed_authorization(self):
        token = self.token()
        for authorization in [
            None,
            "",
            "Bearer ",
            "Bearer unknown-token",
            token,
            f"Basic {token}",
            f"Basic  {token}",
            f"bearer {token}",
            f"Bearer  {token}",
        ]:
            self.assertEqual(self.call("/me", authorization=authorization), UNAUTHORIZED)
        self.assertEqual(self.me(token), (200, {"user": "alice"}))

    def test_s3_instances_do_not_share_tokens_or_lockout(self):
        token = self.token()
        other = self.start_server()
        self.assertEqual(
            self.call("/me", authorization=f"Bearer {token}", base=other), UNAUTHORIZED
        )
        for attempt in range(5):
            self.assertEqual(self.login(password="wrong-password")[0], 401, attempt)
        status, body = self.call(
            "/login", {"user": "alice", "password": PASSWORDS["alice"]}, base=other
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.me(body["token"]), UNAUTHORIZED)

    def test_s4_fixed_expiry_includes_exact_boundary(self):
        token = self.token()
        for elapsed in (20, 59, 59.999):
            self.now = 1000 + elapsed
            self.assertEqual(self.me(token), (200, {"user": "alice"}))
        for elapsed in (60, 61):
            self.now = 1000 + elapsed
            self.assertEqual(self.me(token), UNAUTHORIZED)
            self.assertEqual(self.logout(token), UNAUTHORIZED)

    def test_s4_tokens_expire_from_their_own_issue_time(self):
        first = self.token()
        self.now += 10
        second = self.token()
        self.now = 1060
        self.assertEqual(self.me(first), UNAUTHORIZED)
        self.assertEqual(self.logout(first), UNAUTHORIZED)
        self.assertEqual(self.me(second), (200, {"user": "alice"}))
        self.now = 1070
        self.assertEqual(self.me(second), UNAUTHORIZED)

    def test_s5_logout_revokes_only_current_token(self):
        first, second, bob = self.token(), self.token(), self.token("bob")
        self.assertEqual(self.logout(first), (200, {"ok": True}))
        self.assertEqual(self.me(first), UNAUTHORIZED)
        self.assertEqual(self.logout(first), UNAUTHORIZED)
        self.assertEqual(self.me(second), (200, {"user": "alice"}))
        self.assertEqual(self.me(bob), (200, {"user": "bob"}))

    def test_s5_invalid_logout_preserves_sessions(self):
        token = self.token()
        for authorization in [None, "Bearer unknown-token", f"Basic {token}", f"Basic  {token}"]:
            self.assertEqual(self.call("/logout", {}, authorization=authorization), UNAUTHORIZED)
        self.assertEqual(self.me(token), (200, {"user": "alice"}))

    def test_s6_responses_and_logs_do_not_expose_passwords(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        submitted = [*PASSWORDS.values(), "wrong-secret-789!", "unknown-secret-012!"]
        with redirect_stdout(stdout), redirect_stderr(stderr):
            alice, bob = self.token(), self.token("bob")
            self.me(alice)
            self.logout(bob)
            for attempt in range(5):
                self.login(password=submitted[2])
            self.login()
            self.login(password=submitted[2])
            self.call("/login", {"user": "nobody", "password": submitted[3]})
        observed = "\n".join(self.responses) + stdout.getvalue() + stderr.getvalue()
        for password in submitted:
            self.assertNotIn(password, observed)


if __name__ == "__main__":
    unittest.main()
