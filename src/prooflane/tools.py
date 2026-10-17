"""Workspace-oriented capabilities shared by testing and automation tasks."""

import ast
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, build_opener

from prooflane.artifacts import digest, write_json
from prooflane.models import NoRedirect

MAX_FILE_BYTES = 200_000
MAX_OUTPUT_BYTES = 512_000
IGNORED_DIRECTORIES = {".git", ".venv", "node_modules", "__pycache__", ".prooflane"}
FILE_CHANGING_TOOLS = {"files.write", "files.edit"}
# Tools that may alter the workspace or a target system, so acceptance must exist first.
STATE_CHANGING_TOOLS = FILE_CHANGING_TOOLS | {"process.run", "http.request", "browser.run"}


@dataclass(frozen=True)
class ToolPolicy:
    workspace: Path
    allow_process: bool = False
    allowed_origins: tuple[str, ...] = ()
    browser_channel: str | None = None


def origin(url: str) -> str:
    parsed = urlparse(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Expected an HTTP(S) URL without embedded credentials")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return f"{parsed.scheme}://{parsed.hostname.lower()}:{port}"


class Tools:
    def __init__(self, policy: ToolPolicy, evidence: Path):
        self.policy = policy
        self.workspace = policy.workspace.resolve()
        if not self.workspace.is_dir():
            raise ValueError("Workspace must be an existing directory")
        self.evidence = evidence
        self.origins = {origin(url) for url in policy.allowed_origins}
        self.sequence = 0
        self.handlers = {
            "files.list": self.list_files,
            "files.read": self.read_file,
            "files.write": self.write_file,
            "files.edit": self.edit_file,
            "code.inspect": self.inspect_code,
            "process.run": self.run_process,
            "http.request": self.http_request,
            "browser.run": self.run_browser,
        }

    def catalog(self) -> dict:
        return {
            "files.list": {"arguments": {"path": "relative directory, default ."}},
            "files.read": {"arguments": {"path": "relative UTF-8 file"}},
            "files.write": {"arguments": {"path": "relative file", "content": "UTF-8 text"}},
            "files.edit": {
                "arguments": {
                    "path": "relative existing file",
                    "old": "exact text occurring exactly once",
                    "new": "replacement text",
                }
            },
            "code.inspect": {
                "arguments": {"path": "Python file; returns AST symbols/imports/syntax"}
            },
            "process.run": {
                "enabled": self.policy.allow_process,
                "arguments": {
                    "argv": "list of strings; $PYTHON means current interpreter",
                    "timeout": "1..60 seconds, default 15",
                },
            },
            "http.request": {
                "allowed_origins": sorted(self.origins),
                "arguments": {
                    "url": "allowed HTTP(S) URL",
                    "method": "GET or POST, default GET",
                    "body": "optional JSON object",
                },
            },
            "browser.run": {
                "allowed_origins": sorted(self.origins),
                "arguments": {
                    "url": "allowed URL",
                    "actions": "list: {op: click/fill/expect_text, selector: CSS, value/text: string}",
                },
            },
        }

    def path(self, relative: str) -> Path:
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise ValueError("Use a workspace-relative path")
        path = (self.workspace / relative).resolve()
        if not path.is_relative_to(self.workspace):
            raise ValueError("Path escapes the configured workspace")
        return path

    def check_url(self, url: str) -> None:
        if origin(url) not in self.origins:
            raise ValueError("URL origin is not configured for this task")

    def call(self, name: str, arguments: dict) -> dict:
        self.sequence += 1
        directory = self.evidence / f"{self.sequence:03d}"
        directory.mkdir(parents=True)
        try:
            if name not in self.handlers or not isinstance(arguments, dict):
                raise ValueError("Unknown capability or invalid arguments")
            data = self.handlers[name](directory=directory, **arguments)
            status = data.pop("tool_status", "succeeded")
            result = {"tool": name, "status": status, "data": data}
        except Exception as error:
            result = {
                "tool": name,
                "status": "blocked",
                "data": {"error": f"{type(error).__name__}: {error}"},
            }
        result["evidence"] = str(directory.relative_to(self.evidence.parent))
        # Do not persist supplied argument bodies, which may contain passwords or tokens.
        write_json(directory / "result.json", result)
        return result

    def list_files(self, directory: Path, path: str = ".") -> dict:
        base = self.path(path)
        if not base.is_dir():
            raise ValueError("Expected a directory")
        files = []
        for current, directories, names in os.walk(base, followlinks=False):
            directories[:] = sorted(
                name
                for name in directories
                if name not in IGNORED_DIRECTORIES and not (Path(current) / name).is_symlink()
            )
            for name in sorted(names):
                candidate = Path(current) / name
                if candidate.is_symlink() or name.startswith(".env"):
                    continue
                files.append(str(candidate.relative_to(self.workspace)))
                if len(files) == 200:
                    return {"files": files, "truncated": True}
        return {"files": files, "truncated": False}

    def read_file(self, directory: Path, path: str) -> dict:
        source = self.path(path)
        if source.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("File exceeds the text context limit")
        return {
            "path": path,
            "content": source.read_text(encoding="utf-8"),
            "sha256": digest(source),
        }

    def write_file(self, directory: Path, path: str, content: str) -> dict:
        if not isinstance(content, str) or len(content.encode()) > MAX_FILE_BYTES:
            raise ValueError("Content must be bounded UTF-8 text")
        target = self.path(path)
        previous = None
        if target.exists():
            if target.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("Existing file exceeds the backup limit")
            previous = digest(target)
            (directory / "previous.bin").write_bytes(target.read_bytes())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        (directory / "written.txt").write_text(content, encoding="utf-8")
        return {"path": path, "before_sha256": previous, "sha256": digest(target)}

    def edit_file(self, directory: Path, path: str, old: str, new: str) -> dict:
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise ValueError("Edit requires nonempty old text and new text")
        content = self.read_file(directory, path)["content"]
        occurrences = content.count(old)
        if occurrences != 1:
            raise ValueError(f"Old text must occur exactly once; found {occurrences}")
        return self.write_file(directory, path, content.replace(old, new))

    def inspect_code(self, directory: Path, path: str) -> dict:
        source = self.read_file(directory, path)
        try:
            tree = ast.parse(source["content"])
        except SyntaxError as error:
            return {
                "tool_status": "failed",
                "syntax_valid": False,
                "line": error.lineno,
                "error": error.msg,
            }
        symbols = []
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbols.append(
                    {"name": node.name, "kind": type(node).__name__, "line": node.lineno}
                )
            elif isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        return {
            "syntax_valid": True,
            "symbols": symbols[:200],
            "imports": sorted(set(imports)),
            "sha256": source["sha256"],
        }

    def run_process(self, directory: Path, argv: list[str], timeout: int = 15) -> dict:
        if not self.policy.allow_process:
            raise ValueError("Process capability is disabled for this task")
        if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
            raise ValueError("argv must be a nonempty list of strings")
        if type(timeout) is not int or not 1 <= timeout <= 60:
            raise ValueError("Process timeout must be 1..60 seconds")
        if os.name != "posix":
            raise ValueError("Managed process execution currently supports macOS/Linux")
        command = [sys.executable if item == "$PYTHON" else item for item in argv]
        environment = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"}
        environment["PYTHONPYCACHEPREFIX"] = str((directory / "pycache").resolve())
        output = directory / "output.txt"
        stopped = None
        with output.open("wb") as stream:
            process = subprocess.Popen(
                command,
                cwd=self.workspace,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + timeout
                while process.poll() is None:
                    if time.monotonic() >= deadline:
                        stopped = "timeout"
                        break
                    if output.stat().st_size > MAX_OUTPUT_BYTES:
                        stopped = "output_limit"
                        break
                    time.sleep(0.02)
            finally:
                # Kill only the process group created for this invocation, including descendants.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=3)
        with output.open("rb") as stream:
            captured = stream.read(MAX_OUTPUT_BYTES)
        if output.stat().st_size > MAX_OUTPUT_BYTES:
            stopped = "output_limit"
        output.write_bytes(captured)
        return {
            "tool_status": "succeeded" if process.returncode == 0 and stopped is None else "failed",
            "argv": argv,
            "returncode": process.returncode,
            "stopped": stopped,
            "output": captured.decode("utf-8", errors="replace")[:12000],
        }

    def http_request(
        self, directory: Path, url: str, method: str = "GET", body: dict | None = None
    ) -> dict:
        self.check_url(url)
        if method not in {"GET", "POST"} or (body is not None and not isinstance(body, dict)):
            raise ValueError("Only GET/POST with an optional JSON object are supported")
        request = Request(
            url,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"},
        )
        try:
            response = build_opener(NoRedirect()).open(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                raise ValueError("HTTP response exceeds evidence limit")
            text = raw.decode("utf-8", errors="replace")
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = text
            return {"http_status": response.status, "body": data}

    def run_browser(self, directory: Path, url: str, actions: list[dict] | None = None) -> dict:
        self.check_url(url)
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=self.policy.browser_channel)
            try:
                page = browser.new_page(viewport={"width": 1200, "height": 850})
                page.set_default_timeout(7000)
                network = []

                def route_request(route):
                    if origin(route.request.url) in self.origins:
                        route.continue_()
                    else:
                        route.abort()

                page.route("**/*", route_request)
                page.on(
                    "response",
                    lambda response: network.append(
                        {
                            "method": response.request.method,
                            "path": urlparse(response.url).path,
                            "status": response.status,
                        }
                    ),
                )
                try:
                    page.goto(url, wait_until="load")
                    for action in actions or []:
                        locator = page.locator(action["selector"])
                        if action["op"] == "click":
                            locator.click()
                        elif action["op"] == "fill":
                            locator.fill(action["value"])
                        elif action["op"] == "expect_text":
                            from playwright.sync_api import expect

                            expect(locator).to_contain_text(action["text"])
                        else:
                            raise ValueError("Unknown browser action")
                    return {
                        "title": page.title(),
                        "text": page.locator("body").inner_text()[:8000],
                        "network": network,
                    }
                finally:
                    page.screenshot(path=str(directory / "page.png"), full_page=True)
                    (directory / "page.html").write_text(page.content(), encoding="utf-8")
                    write_json(directory / "network.json", network)
            finally:
                browser.close()
