import json
import os
import tempfile
import unittest
from unittest.mock import patch
from main import (
    discover_new_links,
    load_previous_batch,
    pending_links,
    prepare_run_batch,
    run_browser_send,
)
from processing_state import load_account_history


class FakeSender:
    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [])
        self.calls = []

    def __call__(self, link):
        self.calls.append(link)
        return self.outcomes.pop(0) if self.outcomes else "ok"


class ProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = self.temp.name
        self.links = [f"https://t.me/shmsannews/{number}" for number in range(1, 8)]
        self.sleep_patch = patch("main.time.sleep", return_value=None)
        self.sleep_patch.start()

    def tearDown(self):
        self.sleep_patch.stop()
        self.temp.cleanup()

    def test_new_links_precede_previous_batch_and_are_deduplicated(self):
        with open(os.path.join(self.state, "previous_batch.json"), "w", encoding="utf-8") as file:
            json.dump([self.links[0], self.links[1]], file)
        batch = prepare_run_batch([self.links[2], self.links[0]], self.state)
        self.assertEqual(batch, [self.links[2], self.links[0], self.links[1]])
        self.assertEqual(load_previous_batch(self.state), batch)

    def test_legacy_queue_migration_only_keeps_first_five_incomplete_links(self):
        with open(os.path.join(self.state, "telegram_links.txt"), "w", encoding="utf-8") as file:
            file.write("\n".join(self.links) + "\n")
        with open(os.path.join(self.state, "success_counts.json"), "w", encoding="utf-8") as file:
            json.dump({self.links[0]: 5}, file)
        migrated = load_previous_batch(self.state)
        self.assertEqual(migrated, self.links[1:6])

    def test_only_new_links_are_discovered_once(self):
        found = discover_new_links(self.state, [self.links[0], self.links[1]])
        self.assertEqual(found, self.links[:2])
        self.assertEqual(discover_new_links(self.state, [self.links[0], self.links[1], self.links[2]]), [self.links[2]])

    def test_account_is_not_given_same_link_twice_but_next_account_can_process_it(self):
        batch = self.links[:2]
        first = FakeSender()
        self.assertEqual(run_browser_send(first, self.state, 1, batch), 0)
        self.assertEqual(first.calls, batch)
        repeated = FakeSender()
        self.assertEqual(run_browser_send(repeated, self.state, 1, batch), 0)
        self.assertEqual(repeated.calls, [])
        next_account = FakeSender()
        self.assertEqual(run_browser_send(next_account, self.state, 2, batch), 0)
        self.assertEqual(next_account.calls, batch)
        self.assertEqual(load_account_history(self.state), {link: [1, 2] for link in batch})

    def test_failed_attempt_is_still_recorded_for_that_account(self):
        sender = FakeSender(["fail"])
        run_browser_send(sender, self.state, 3, [self.links[0]])
        self.assertEqual(pending_links([self.links[0]], 3, self.state), [])
        self.assertEqual(pending_links([self.links[0]], 4, self.state), [self.links[0]])

    def test_run_is_limited_to_five_requests_and_remaining_links_stay_in_batch(self):
        batch = self.links[:7]
        sender = FakeSender()
        run_browser_send(sender, self.state, 1, batch)
        self.assertEqual(sender.calls, batch[:5])
        self.assertEqual(pending_links(batch, 1, self.state), batch[5:])

    def test_fatal_result_does_not_retry_link_on_same_account(self):
        sender = FakeSender(["fatal"])
        self.assertEqual(run_browser_send(sender, self.state, 5, [self.links[0]]), 1)
        self.assertEqual(pending_links([self.links[0]], 5, self.state), [])
        self.assertEqual(pending_links([self.links[0]], 1, self.state), [self.links[0]])


if __name__ == "__main__":
    unittest.main()
