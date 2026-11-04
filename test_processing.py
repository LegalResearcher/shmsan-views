import os
import tempfile
import unittest
from unittest.mock import patch

import main
from main import is_after_start_link, run_browser_send
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
        self.links = [f"https://t.me/shmsannews/{number}" for number in range(2293, 2300)]
        with open(os.path.join(self.state, "telegram_links.txt"), "w", encoding="utf-8") as file:
            file.write("\n".join(self.links) + "\n")

    def tearDown(self):
        self.temp.cleanup()

    def test_start_cutoff_excludes_anchor_and_older_posts(self):
        self.assertFalse(is_after_start_link("https://t.me/shmsannews/2291"))
        self.assertFalse(is_after_start_link("https://t.me/shmsannews/2292"))
        self.assertTrue(is_after_start_link("https://t.me/shmsannews/2293"))
        blocked = FakeSender(["ok"])
        self.assertEqual(process_manual_retry("https://t.me/shmsannews/2292", blocked, self.state), 2)
        self.assertEqual(blocked.calls, [])

    def test_first_pull_queues_only_posts_after_start_cutoff(self):
        fresh_state = os.path.join(self.state, "first_pull")
        os.makedirs(fresh_state)
        links_path = os.path.join(fresh_state, "telegram_links.txt")
        processed_path = os.path.join(fresh_state, "processed_links.txt")
        discovered = [f"https://t.me/shmsannews/{number}" for number in (2291, 2292, 2293, 2294)]
        with patch.object(main, "LINKS_PATH", links_path), \
             patch.object(main, "PROCESSED_PATH", processed_path), \
             patch.object(main, "fetch_links", return_value=discovered):
            main.pull_once()
        with open(links_path, encoding="utf-8") as file:
            self.assertEqual(file.read().splitlines(), ["https://t.me/shmsannews/2294"])
        with open(processed_path, encoding="utf-8") as file:
            self.assertEqual(file.read().splitlines(), ["https://t.me/shmsannews/2293", "https://t.me/shmsannews/2294"])

    def test_each_run_is_limited_to_five_and_successes_accumulate_to_five(self):
        first = FakeSender(["ok"] * 5)
        self.assertEqual(run_browser_send(first, self.state), 0)
        self.assertEqual(len(first.calls), 5)
        self.assertEqual(first.calls, list(reversed(self.links[-5:])))
        self.assertEqual(load_successes(self.state)[self.links[-1]], 1)
        second = FakeSender(["ok"] * 5)
        self.assertEqual(run_browser_send(second, self.state), 0)
        self.assertEqual(len(second.calls), 5)
        for _ in range(3):
            run_browser_send(FakeSender(["ok"] * 5), self.state)
        self.assertEqual(load_successes(self.state)[self.links[-1]], 5)
        remaining = FakeSender(["ok"] * 5)
        run_browser_send(remaining, self.state)
        self.assertEqual(remaining.calls, list(reversed(self.links[:2])))

    def test_main_run_sends_same_eligible_links_from_all_three_accounts(self):
        accounts = [(n, f"user-{n}", f"password-{n}") for n in range(1, 4)]

        def successful_account(account, links):
            return [(link, "ok") for link in links]

        with patch.object(main, "_send_account", side_effect=successful_account) as send:
            self.assertEqual(main.run_all_accounts(accounts, self.state), 0)

        self.assertEqual(send.call_count, 3)
        sent_batches = [call.args[1] for call in send.call_args_list]
        self.assertEqual(sent_batches[0], sent_batches[1])
        self.assertEqual(sent_batches[1], sent_batches[2])
        self.assertEqual(len(sent_batches[0]), 5)
        self.assertEqual(
            load_successes(self.state),
            {link: 3 for link in sent_batches[0]},
        )

    def test_new_links_are_processed_before_queue_newest_to_oldest(self):
        fresh_links = self.links[-3:]
        sender = FakeSender(["ok"] * 5)
        self.assertEqual(run_browser_send(sender, self.state, priority_links=fresh_links), 0)
        self.assertEqual(
            sender.calls,
            list(reversed(fresh_links)) + list(reversed(self.links[:-3]))[:2],
        )

    def test_failures_do_not_increment_success_and_three_failures_skip(self):
        run_browser_send(FakeSender(["fail"] * 5), self.state)
        self.assertEqual(load_successes(self.state), {})
        self.assertEqual(load_failures(self.state)[self.links[-1]], 1)
        run_browser_send(FakeSender(["fail"] * 5), self.state)
        run_browser_send(FakeSender(["fail"] * 5), self.state)
        self.assertEqual(load_failures(self.state)[self.links[-1]], 3)
        later = FakeSender(["ok"] * 5)
        run_browser_send(later, self.state)
        self.assertNotIn(self.links[-1], later.calls)

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
