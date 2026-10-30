import os
import tempfile
import unittest

from main import run_browser_send
from processing_state import load_failures, load_successes
from retry_link import process_manual_retry


class FakeSender:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, link):
        self.calls.append(link)
        return self.outcomes.pop(0) if self.outcomes else "ok"


class ProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = self.temp.name
        self.links = [f"https://t.me/shmsannews/{number}" for number in range(1, 8)]
        with open(os.path.join(self.state, "telegram_links.txt"), "w", encoding="utf-8") as file:
            file.write("\n".join(self.links) + "\n")

    def tearDown(self):
        self.temp.cleanup()

    def test_each_run_is_limited_to_five_and_successes_accumulate_to_five(self):
        first = FakeSender(["ok"] * 5)
        self.assertEqual(run_browser_send(first, self.state), 0)
        self.assertEqual(len(first.calls), 5)
        self.assertEqual(load_successes(self.state)[self.links[0]], 1)
        second = FakeSender(["ok"] * 5)
        self.assertEqual(run_browser_send(second, self.state), 0)
        self.assertEqual(len(second.calls), 5)
        for _ in range(3):
            run_browser_send(FakeSender(["ok"] * 5), self.state)
        self.assertEqual(load_successes(self.state)[self.links[0]], 5)
        remaining = FakeSender(["ok"] * 5)
        run_browser_send(remaining, self.state)
        self.assertEqual(remaining.calls, self.links[5:])

    def test_failures_do_not_increment_success_and_three_failures_skip(self):
        run_browser_send(FakeSender(["fail"] * 5), self.state)
        self.assertEqual(load_successes(self.state), {})
        self.assertEqual(load_failures(self.state)[self.links[0]], 1)
        run_browser_send(FakeSender(["fail"] * 5), self.state)
        run_browser_send(FakeSender(["fail"] * 5), self.state)
        self.assertEqual(load_failures(self.state)[self.links[0]], 3)
        later = FakeSender(["ok"] * 5)
        run_browser_send(later, self.state)
        self.assertNotIn(self.links[0], later.calls)

    def test_manual_retry_uses_same_counter_and_rejects_at_five(self):
        for _ in range(4):
            self.assertEqual(process_manual_retry(self.links[0], FakeSender(["ok"]), self.state), 0)
        self.assertEqual(load_successes(self.state)[self.links[0]], 4)
        self.assertEqual(process_manual_retry(self.links[0], FakeSender(["ok"]), self.state), 0)
        blocked = FakeSender(["ok"])
        self.assertEqual(process_manual_retry(self.links[0], blocked, self.state), 2)
        self.assertEqual(blocked.calls, [])
        self.assertEqual(load_successes(self.state)[self.links[0]], 5)

    def test_legacy_done_link_is_unchanged_and_never_retried(self):
        done_path = os.path.join(self.state, "done_links.txt")
        with open(done_path, "w", encoding="utf-8") as file:
            file.write(self.links[0] + "\n")
        with open(done_path, "rb") as file:
            original = file.read()
        sender = FakeSender(["ok"] * 5)
        run_browser_send(sender, self.state)
        self.assertNotIn(self.links[0], sender.calls)
        self.assertNotIn(self.links[0], load_successes(self.state))
        with open(done_path, "rb") as file:
            self.assertEqual(file.read(), original)
        blocked = FakeSender(["ok"])
        self.assertEqual(process_manual_retry(self.links[0], blocked, self.state), 2)
        self.assertEqual(blocked.calls, [])


if __name__ == "__main__":
    unittest.main()
