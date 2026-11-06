"""Offline security invariants for Instagram proposal queue."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError

from dotagent.integrations.instagram_safe_queue import InstagramAction, SafeQueue, fingerprint


class InstagramQueueTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.queue = SafeQueue(Path(self.temp.name) / "mail-safe.db", allowed_account_id="12345")
        self.due = datetime.now(timezone.utc) + timedelta(hours=1)

    def tearDown(self):
        self.queue.close()
        self.temp.cleanup()

    def action(self, **overrides):
        args = {"kind": "reply_comment", "account_id": "12345",
                "target_id": "42", "text": "Thanks for your feedback", "due_at": self.due}
        args.update(overrides)
        return InstagramAction(**args)

    def test_proposals_never_execute(self):
        action = self.action()
        pid = self.queue.propose(action)
        self.assertEqual(self.queue.inspect(pid)["state"], "pending")
        self.assertEqual(self.queue.list_ready_for_manual_review(), [])

    def test_other_account_rejected(self):
        with self.assertRaises(PermissionError):
            self.queue.propose(self.action(account_id="99999"))

    def test_explicit_review_and_exact_fingerprint(self):
        action = self.action()
        pid = self.queue.propose(action)
        with self.assertRaises(PermissionError):
            self.queue.approve_by_human(pid, reviewed_fingerprint=fingerprint(action), human_confirmed=False)
        with self.assertRaises(PermissionError):
            self.queue.approve_by_human(pid, reviewed_fingerprint="bad", human_confirmed=True)
        self.queue.approve_by_human(pid, reviewed_fingerprint=fingerprint(action), human_confirmed=True)
        self.assertEqual(self.queue.inspect(pid)["state"], "approved")
        self.assertEqual(self.queue.list_ready_for_manual_review(), [])

    def test_local_media_url_rejected(self):
        for url in ("http://example.com/p.jpg", "https://localhost/a", "https://127.0.0.1/a"):
            with self.subTest(url=url):
                with self.assertRaises(ValidationError):
                    self.action(kind="publish_image", target_id=None, media_url=url)

    def test_only_reply_targets(self):
        with self.assertRaises(ValidationError):
            self.action(target_id=None)


if __name__ == "__main__":
    unittest.main()
