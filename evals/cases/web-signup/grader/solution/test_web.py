"""Reference answer used only to validate the grader; never copied into agent workspaces."""

import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGE = (Path(__file__).parent / "app" / "index.html").resolve().as_uri()
VALID = {"username": "alice_01", "password": "secret123", "confirm": "secret123", "age": "30"}


class SignupPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)
        self.page.goto(PAGE)

    def submit(self, **changes):
        for field, value in {**VALID, **changes}.items():
            self.page.fill(f"#{field}", value)
        self.page.click("#submit")
        return self.page.text_content("#error")

    def test_w1_username_rules(self):
        message = "用户名需为 3-16 位小写字母、数字或下划线"
        for username in ["ab", "a" * 17, "Alice", "al-ice"]:
            self.assertEqual(self.submit(username=username), message, username)
        self.assertEqual(self.submit(username="a" * 16), "")

    def test_w1_username_is_trimmed(self):
        self.assertEqual(self.submit(username="  bob  "), "")
        self.assertEqual(self.page.text_content("#welcome"), "注册成功，欢迎 bob")

    def test_w2_taken_username(self):
        self.assertEqual(self.submit(username="admin"), "用户名已被占用")

    def test_w3_password_rules(self):
        message = "密码至少 8 位且包含数字"
        self.assertEqual(self.submit(password="abc1234", confirm="abc1234"), message)
        self.assertEqual(self.submit(password="abcdefgh", confirm="abcdefgh"), message)

    def test_w4_confirm_must_match(self):
        self.assertEqual(self.submit(confirm="secret124"), "两次密码不一致")

    def test_w5_age_bounds(self):
        message = "年龄需为 18-120 的整数"
        for age in ["17", "121", "18.5", "abc", ""]:
            self.assertEqual(self.submit(age=age), message, age)
        self.assertEqual(self.submit(age="18"), "")

    def test_w5_age_upper_bound_inclusive(self):
        self.assertEqual(self.submit(age="120"), "")

    def test_w6_first_error_wins(self):
        self.assertEqual(
            self.submit(username="x", password="short"), "用户名需为 3-16 位小写字母、数字或下划线"
        )

    def test_w7_success_clears_error_and_disables_submit(self):
        self.submit(age="10")
        self.assertEqual(self.submit(), "")
        self.assertEqual(self.page.text_content("#welcome"), "注册成功，欢迎 alice_01")
        self.assertTrue(self.page.is_disabled("#submit"))


if __name__ == "__main__":
    unittest.main()
