"""Bounded working memory: context excerpts, step history and notes that outlive a session.

Older steps keep only an outline in the model context while their full records stay
recallable by number, and findings worth keeping become notes shared by every session on
the same workspace. Notes about observed outcomes must cite the step that observed them.
"""

import os
from pathlib import Path

from prooflane.artifacts import digest
from prooflane.store import Store
from prooflane.tools import IGNORED_DIRECTORIES, workspace_path

# Large enough for a typical source file or test log, small enough to bound repeated context.
CONTEXT_TEXT_LIMIT = 10_000
HISTORY_LIMIT = 60
OUTLINE_TEXT_LIMIT = 160
# Newest active notes shown every turn; older ones stay reachable through notes.search.
MEMORY_NOTES = 40
SEARCH_LIMIT = 20
NOTE_TEXT_LIMIT = 1000
NOTE_FILES_LIMIT = 5
# Notes that state an observed outcome must cite the step whose observation shows it.
EVIDENCED_KINDS = {"fact", "defect", "ruled_out", "environment"}
NOTE_KINDS = EVIDENCED_KINDS | {"hypothesis", "todo"}
EVIDENCE_ACTIONS = {"call", "verify", "finish"}
MEMORY_TOOLS = {"notes.record", "notes.search", "history.recall"}
FINGERPRINT_LIMIT = 2000
FINGERPRINT_FILE_BYTES = 20_000_000
CHANGES_LIMIT = 50

MEMORY_GUIDE = (
    "history outlines every earlier step without its observation; history.recall returns one "
    "step's full record. memory holds notes shared by every session on this workspace: read it "
    "before investigating so you do not repeat research. Record what is worth keeping with "
    "notes.record: facts, defects, ruled_out hypotheses and environment knowledge (how to set up "
    "and run things) must cite the step whose observation shows them; hypotheses and todos need "
    "not. Use replaces to correct or complete a note. A stale note depends on files changed "
    "since it was written and must be rechecked."
)


def context_view(value):
    """Keep full observations on disk and bounded excerpts in the model context."""
    if isinstance(value, str):
        return (
            value
            if len(value) <= CONTEXT_TEXT_LIMIT
            else value[:CONTEXT_TEXT_LIMIT]
            + "\n[truncated; inspect evidence or read targeted file]"
        )
    if isinstance(value, list):
        return [context_view(item) for item in value[:20]]
    if isinstance(value, dict):
        return {key: context_view(item) for key, item in list(value.items())[:40]}
    return value


def clip(text: str) -> str:
    return text if len(text) <= OUTLINE_TEXT_LIMIT else text[:OUTLINE_TEXT_LIMIT] + "…"


def outline(step: dict, number: int | None = None) -> dict:
    """Summarize a step by what was attempted and how it ended, masking its observation."""
    entry = {} if number is None else {"step": number}
    entry.update({key: step[key] for key in ("role", "action", "tool", "attempted") if key in step})
    entry["reason"] = clip(step.get("reason", ""))
    observation = step.get("observation", {})
    if "status" in observation:
        entry["status"] = observation["status"]
    data = observation.get("data")
    target = (
        (data.get("path") or data.get("argv") or data.get("url"))
        if isinstance(data, dict)
        else None
    )
    if target:
        entry["target"] = clip(target if isinstance(target, str) else " ".join(map(str, target)))
    for key in ("message", "task", "blocker", "error"):
        if key in observation:
            entry[key] = clip(str(observation[key]))
    if "verification" in observation:
        entry["verification"] = [result["status"] for result in observation["verification"]]
    return entry


def recent_view(steps: list, window: int) -> list:
    """The newest steps in full (bounded), numbered for recall and evidence citations."""
    start = max(0, len(steps) - window)
    return context_view(
        [{"step": start + index, **step} for index, step in enumerate(steps[start:])]
    )


def history_view(steps: list, window: int, numbered: bool = True) -> dict:
    """Outlines of the steps older than the recent window, newest last."""
    older = steps[: max(0, len(steps) - window)]
    shown = range(max(0, len(older) - HISTORY_LIMIT), len(older))
    return {
        "steps": [outline(older[index], index if numbered else None) for index in shown],
        "omitted": len(older) - len(shown),
    }


def fingerprint(workspace: Path) -> dict | None:
    """Digest workspace files so a later session can see what changed; None if too large."""
    files = {}
    for current, directories, names in os.walk(workspace):
        directories[:] = sorted(name for name in directories if name not in IGNORED_DIRECTORIES)
        for name in sorted(names):
            path = Path(current) / name
            if path.is_symlink():
                continue
            if len(files) == FINGERPRINT_LIMIT:
                return None
            size = path.stat().st_size
            files[str(path.relative_to(workspace))] = (
                digest(path) if size <= FINGERPRINT_FILE_BYTES else f"size:{size}"
            )
    return files


def workspace_changes(before: dict | None, after: dict | None) -> dict | None:
    if before is None or after is None:
        return None
    changes = {
        "modified": sorted(
            path for path in before.keys() & after.keys() if before[path] != after[path]
        ),
        "added": sorted(after.keys() - before.keys()),
        "removed": sorted(before.keys() - after.keys()),
    }
    return {kind: paths[:CHANGES_LIMIT] for kind, paths in changes.items()}


class Notes:
    """Session capabilities for the shared notes of one workspace and this session's steps."""

    def __init__(self, store: Store, task_id: str, workspace: Path, steps: list):
        self.store = store
        self.task_id = task_id
        self.workspace = workspace.resolve()
        # The live step list of the running session, so citations and recall see every step.
        self.steps = steps
        self.handlers = {
            "notes.record": self.record,
            "notes.search": self.search,
            "history.recall": self.recall,
        }
        self.catalog = {
            "notes.record": {
                "arguments": {
                    "kind": "fact/defect/ruled_out/environment (cite step) or hypothesis/todo",
                    "statement": f"one finding, at most {NOTE_TEXT_LIMIT} characters",
                    "step": "number of the earlier step whose observation shows it",
                    "files": f"up to {NOTE_FILES_LIMIT} workspace files it depends on",
                    "replaces": "id of an active note this corrects or completes",
                }
            },
            "notes.search": {"arguments": {"query": "words that must all appear in a note"}},
            "history.recall": {"arguments": {"step": "earlier step number in this session"}},
        }

    def record(
        self,
        directory: Path,
        kind: str,
        statement: str,
        step: int | None = None,
        files: list | None = None,
        replaces: str | None = None,
    ) -> dict:
        if kind not in NOTE_KINDS:
            raise ValueError(f"Note kind must be one of {sorted(NOTE_KINDS)}")
        if not isinstance(statement, str) or not 0 < len(statement.strip()) <= NOTE_TEXT_LIMIT:
            raise ValueError(f"Statement must be 1..{NOTE_TEXT_LIMIT} characters")
        files = files or []
        if not isinstance(files, list) or len(files) > NOTE_FILES_LIMIT:
            raise ValueError(f"List at most {NOTE_FILES_LIMIT} workspace files")
        digests = {}
        for path in files:
            target = workspace_path(self.workspace, path)
            if not target.is_file():
                raise ValueError(f"Note file does not exist: {path}")
            digests[path] = digest(target)
        evidence = self.evidence(step) if step is not None or kind in EVIDENCED_KINDS else None
        note = {"kind": kind, "statement": statement, "evidence": evidence, "files": digests}
        note_id = self.store.record_note(self.task_id, str(self.workspace), note, replaces)
        return {"note": note_id, "kind": kind, "evidence": evidence}

    def evidence(self, step) -> dict:
        if type(step) is not int or not 0 <= step < len(self.steps):
            raise ValueError("Cite the number of an earlier step in this session")
        cited = self.steps[step]
        if (
            cited["action"] not in EVIDENCE_ACTIONS
            or cited.get("tool") in MEMORY_TOOLS
            or "observation" not in cited
        ):
            raise ValueError("Evidence must be a tool observation or verification step")
        observation = cited["observation"]
        if "status" in observation:
            status = observation["status"]
        else:
            passed = all(result["status"] == "passed" for result in observation["verification"])
            status = "passed" if passed else "failed"
        return {
            "task_id": self.task_id,
            "step": step,
            "tool": cited.get("tool", cited["action"]),
            "status": status,
        }

    def search(self, directory: Path, query: str) -> dict:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Search requires a nonempty query")
        words = query.casefold().split()
        matches = [
            note
            for note in self.store.notes(str(self.workspace))
            if all(word in note["statement"].casefold() for word in words)
        ]
        return {
            "notes": [self.view(note) for note in matches[-SEARCH_LIMIT:]],
            "matches": len(matches),
        }

    def recall(self, directory: Path, step: int) -> dict:
        if type(step) is not int or not 0 <= step < len(self.steps):
            raise ValueError("Recall the number of an earlier step in this session")
        return {"step": step, "record": self.steps[step]}

    def view(self, note: dict) -> dict:
        stale = False
        for path, sha256 in note["files"].items():
            target = workspace_path(self.workspace, path)
            stale = stale or not target.is_file() or digest(target) != sha256
        return {
            "id": note["id"],
            "kind": note["kind"],
            "statement": note["statement"],
            "evidence": note["evidence"],
            "files": sorted(note["files"]),
            "stale": stale,
            "this_task": note["task_id"] == self.task_id,
        }

    def memory(self) -> dict:
        notes = self.store.notes(str(self.workspace))
        return {
            "notes": [self.view(note) for note in notes[-MEMORY_NOTES:]],
            "omitted": max(0, len(notes) - MEMORY_NOTES),
        }
