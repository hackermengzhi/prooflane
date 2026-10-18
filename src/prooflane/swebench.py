"""Generate SWE-bench predictions without executing code from the target repository."""

import json
import os
import re
import subprocess
from pathlib import Path

from prooflane.agent import run_agent
from prooflane.artifacts import write_json
from prooflane.store import Store
from prooflane.tools import (
    MAX_FILE_BYTES,
    MAX_OUTPUT_BYTES,
    MAX_PROCESS_SECONDS,
    ToolPolicy,
    workspace_path,
)

DEFAULT_INSTANCES = Path("evals/swebench/instances.sample.jsonl")
MODEL_NAME = "prooflane-agent"


def load_instances(path: Path) -> list[dict]:
    instances = []
    with Path(path).open(encoding="utf-8") as source:
        for number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                instance = json.loads(line)
            except ValueError as error:
                raise ValueError(f"Invalid instance at line {number}: {error}") from error
            if not isinstance(instance, dict):
                raise ValueError(f"Instance at line {number} must be an object")
            instances.append(instance)
    return instances


def _git(workspace: Path, *arguments: str, input: str | None = None, diff: bool = False) -> str:
    # These fixed Git operations prepare/export data; the Agent cannot call this helper.
    # Ignore inherited Git routing and user filters, and never invoke repository hooks.
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0"
    )
    result = subprocess.run(
        ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false", *arguments],
        cwd=workspace,
        env=environment,
        input=input.encode("utf-8") if input is not None else None,
        capture_output=True,
        timeout=MAX_PROCESS_SECONDS,
    )
    if result.returncode not in ((0, 1) if diff else (0,)):
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Git {arguments[0]} failed: {message}")
    if len(result.stdout) > MAX_OUTPUT_BYTES:
        raise ValueError("Git output exceeds the output limit")
    # Text-mode subprocess pipes normalize CRLF, which would corrupt unified diff content.
    return result.stdout.decode("utf-8")


def _instance_workspace(instance: dict, workdir: Path) -> Path:
    identifier = instance["instance_id"]
    if not isinstance(identifier, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]*", identifier
    ):
        raise ValueError("instance_id must be a single directory name")
    return workspace_path(workdir, identifier)


def _base_commit(instance: dict) -> str:
    commit = instance["base_commit"]
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise ValueError("base_commit must be a full Git commit hash")
    return commit


def prepare_instance(instance: dict, workdir: Path) -> Path:
    """Clone into a fresh per-instance workspace and install its supplied test patch."""
    workdir = Path(workdir).resolve()
    workspace = _instance_workspace(instance, workdir)
    repo = instance["repo"]
    if not isinstance(repo, str) or not re.fullmatch(
        r"[A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*", repo
    ):
        raise ValueError("repo must be a GitHub owner/repository name")
    commit = _base_commit(instance)
    workspace.mkdir(parents=True)
    _git(
        workspace,
        "clone",
        "--depth=1",
        "--no-checkout",
        "--no-tags",
        "--",
        f"https://github.com/{repo}.git",
        ".",
    )
    # Historical base commits usually are not part of a shallow default-branch clone.
    _git(workspace, "fetch", "--depth=1", "--no-tags", "origin", commit)
    _git(workspace, "checkout", "--detach", commit)
    if instance.get("test_patch"):
        _git(workspace, "apply", "-", input=instance["test_patch"])
    return workspace


def _changed_paths(workspace: Path, commit: str) -> tuple[set[str], set[str]]:
    tracked = _git(
        workspace,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--no-renames",
        "--name-only",
        "-z",
        commit,
        "--",
    )
    untracked = _git(workspace, "ls-files", "--others", "--exclude-standard", "-z")
    return set(filter(None, tracked.split("\0"))), set(filter(None, untracked.split("\0")))


def generate_prediction(instance: dict, workspace: Path, run=run_agent) -> dict:
    """Export only Agent changes from a freshly prepared, test-patched workspace."""
    workspace = Path(workspace).resolve()
    commit = _base_commit(instance)
    if _git(workspace, "rev-parse", "--show-toplevel").strip() != str(workspace):
        raise ValueError("Workspace must be the repository root")
    if _git(workspace, "rev-parse", "HEAD").strip() != commit:
        raise ValueError("Workspace is not at base_commit")
    tracked_before, untracked_before = _changed_paths(workspace, commit)
    # Exclude whole supplied test files, including later Agent edits to those files.
    test_paths = tracked_before | untracked_before
    directory = workspace_path(workspace, ".prooflane/swebench")
    directory.mkdir(parents=True)
    goal = (
        "Generate a minimal code fix for this SWE-bench issue by reading and editing files. "
        "Do not execute repository code, tests, installation commands or subprocesses. "
        "The supplied test patch is already applied; do not edit its files. "
        "Define static observation acceptance checks before editing. Their success records "
        "patch-generation evidence only, not test success or resolution of the issue. "
        "Scoring will be performed separately by the official evaluator.\n\n"
        + instance["problem_statement"]
    )
    result = run(
        Store(str(workspace_path(workspace, ".prooflane/swebench/state.db"))),
        goal,
        ToolPolicy(workspace, allow_process=False),
        workspace_path(workspace, ".prooflane/swebench/runs"),
    )
    if result.get("status") != "completed":
        raise RuntimeError(result.get("error") or f"Agent status: {result.get('status')}")
    tracked, untracked = _changed_paths(workspace, commit)
    paths = []
    for name in sorted((tracked | untracked) - test_paths):
        if Path(name).parts[0] == ".prooflane":
            continue
        path = workspace_path(workspace, name)
        if path.is_file() and path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"Changed file exceeds the text context limit: {name}")
        paths.append(name)
    tracked_paths = [f":(top,literal){name}" for name in paths if name in tracked]
    patch = ""
    if tracked_paths:
        patch = _git(
            workspace,
            "diff",
            "--binary",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            commit,
            "--",
            *tracked_paths,
        )
    for name in paths:
        if name in untracked:
            patch += _git(
                workspace,
                "diff",
                "--binary",
                "--no-ext-diff",
                "--no-textconv",
                "--no-index",
                "--",
                os.devnull,
                name,
                diff=True,
            )
    if len(patch.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise ValueError("Prediction patch exceeds the output limit")
    return {
        "instance_id": instance["instance_id"],
        "model_name_or_path": MODEL_NAME,
        "model_patch": patch,
    }


def write_predictions(rows: list[dict], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for row in rows:
            prediction = {
                key: row[key] for key in ("instance_id", "model_name_or_path", "model_patch")
            }
            output.write(json.dumps(prediction, ensure_ascii=False) + "\n")


def run_swebench(
    instances: list[dict], workdir: Path, out: Path, *, prepare=prepare_instance, run=run_agent
) -> dict:
    """Isolate instance failures and persist errors separately from official predictions."""
    workdir = Path(workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    predictions, errors = [], []
    for instance in instances:
        try:
            expected = _instance_workspace(instance, workdir)
            workspace = Path(prepare(instance, workdir)).resolve()
            if workspace != expected:
                raise ValueError("Prepared workspace must match the instance directory")
            predictions.append(generate_prediction(instance, workspace, run=run))
        except Exception as error:
            errors.append(
                {
                    "instance_id": instance.get("instance_id"),
                    "error": f"{type(error).__name__}: {error}",
                }
            )
    write_predictions(predictions, out)
    errors_path = workspace_path(workdir, "errors.json")
    write_json(errors_path, errors)
    return {
        "predictions": len(predictions),
        "failed": len(errors),
        "out": str(out),
        "errors": str(errors_path),
    }
