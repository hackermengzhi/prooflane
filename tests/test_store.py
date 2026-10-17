import sqlite3
import tempfile
import unittest
from pathlib import Path
from prooflane.store import Store


class StoreTests(unittest.TestCase):
    def test_task_and_evidence_survive_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "state.db")
            store = Store(path)
            task = store.create("支付回归")
            evidence = store.attach(task, "artifact://request-1", "回调响应")
            reopened = Store(path)
            self.assertEqual(reopened.tasks()[0]["title"], "支付回归")
            self.assertEqual(reopened.tasks()[0]["status"], "pending")
            with reopened.connect() as db:
                self.assertEqual(db.execute("SELECT task_id FROM evidence WHERE id = ?", (evidence,)).fetchone()[0], task)

    def test_unknown_task_cannot_receive_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(str(Path(directory) / "state.db"))
            with self.assertRaises(sqlite3.IntegrityError):
                store.attach("missing", "artifact://request", "response")

    def test_blank_title_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(str(Path(directory) / "state.db"))
            with self.assertRaises(ValueError):
                store.create("  ")
