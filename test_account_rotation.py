import json
import os
import tempfile
import unittest

from account_rotation import select_rotating_account


class AccountRotationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state_dir = self.temp.name
        self.env = {}
        for number in range(1, 10):
            self.env[f"SITE_USERNAME_{number}"] = f"user-{number}"
            self.env[f"SITE_PASSWORD_{number}"] = f"password-{number}"

    def tearDown(self):
        self.temp.cleanup()

    def test_round_robin_persists_and_wraps(self):
        selected = []
        for _ in range(20):
            selected.append(select_rotating_account(self.env, self.state_dir))

        expected = list(range(1, 10)) + list(range(1, 10)) + [1, 2]
        self.assertEqual([entry[0] for entry in selected], expected)
        self.assertEqual([entry[1] for entry in selected], [f"user-{n}" for n in expected])
        self.assertEqual([entry[2] for entry in selected], [f"password-{n}" for n in expected])

        cursor_path = os.path.join(self.state_dir, "account_rotation.json")
        with open(cursor_path, "r", encoding="utf-8") as file:
            self.assertEqual(json.load(file), {"next_account": 3})
        with open(cursor_path, "r", encoding="utf-8") as file:
            saved = file.read()
        self.assertNotIn("user-", saved)
        self.assertNotIn("password-", saved)

    def test_missing_secret_fails_without_advancing(self):
        del self.env["SITE_PASSWORD_2"]
        with self.assertRaisesRegex(ValueError, "SITE_PASSWORD_2"):
            select_rotating_account(self.env, self.state_dir)
        self.assertFalse(os.path.exists(os.path.join(self.state_dir, "account_rotation.json")))

    def test_selects_next_eligible_account_from_cursor(self):
        with open(os.path.join(self.state_dir, "account_rotation.json"), "w", encoding="utf-8") as file:
            json.dump({"next_account": 8}, file)
        selected = select_rotating_account(self.env, self.state_dir, eligible_accounts=[2, 9])
        self.assertEqual(selected, (9, "user-9", "password-9"))
        with open(os.path.join(self.state_dir, "account_rotation.json"), "r", encoding="utf-8") as file:
            self.assertEqual(json.load(file), {"next_account": 1})

    def test_no_eligible_account_does_not_advance_cursor(self):
        cursor_path = os.path.join(self.state_dir, "account_rotation.json")
        with open(cursor_path, "w", encoding="utf-8") as file:
            json.dump({"next_account": 4}, file)
        self.assertIsNone(select_rotating_account(self.env, self.state_dir, eligible_accounts=[]))
        with open(cursor_path, "r", encoding="utf-8") as file:
            self.assertEqual(json.load(file), {"next_account": 4})

    def test_invalid_cursor_fails_closed(self):
        cursor_path = os.path.join(self.state_dir, "account_rotation.json")
        with open(cursor_path, "w", encoding="utf-8") as file:
            file.write('{"next_account": 0}\n')
        with self.assertRaisesRegex(ValueError, "Invalid next_account"):
            select_rotating_account(self.env, self.state_dir)
        with open(cursor_path, "r", encoding="utf-8") as file:
            self.assertEqual(json.load(file), {"next_account": 0})


if __name__ == "__main__":
    unittest.main()
