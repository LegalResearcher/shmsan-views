import json
import os
import tempfile

ACCOUNT_COUNT = 5


def select_rotating_account(environ, state_dir):
    """Select one configured account and persist the next account number.

    Scheduled and manual workflows share this cursor through state/.
    Credentials are returned to the caller and are never stored on disk.
    """
    accounts = []
    missing = []
    for number in range(1, ACCOUNT_COUNT + 1):
        username = environ.get(f"SITE_USERNAME_{number}", "").strip()
        password = environ.get(f"SITE_PASSWORD_{number}", "")
        if not username:
            missing.append(f"SITE_USERNAME_{number}")
        if not password:
            missing.append(f"SITE_PASSWORD_{number}")
        accounts.append((username, password))
    if missing:
        raise ValueError("Missing required secrets: " + ", ".join(missing))

    os.makedirs(state_dir, exist_ok=True)
    cursor_path = os.path.join(state_dir, "account_rotation.json")
    next_account = 1
    if os.path.exists(cursor_path):
        with open(cursor_path, "r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            raise ValueError("account_rotation.json must contain an object")
        next_account = data.get("next_account", 1)
        if (not isinstance(next_account, int) or isinstance(next_account, bool)
                or not 1 <= next_account <= ACCOUNT_COUNT):
            raise ValueError("Invalid next_account in account_rotation.json")

    selected = next_account
    following = selected % ACCOUNT_COUNT + 1
    fd, temporary_path = tempfile.mkstemp(prefix=".account-rotation-", dir=state_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump({"next_account": following}, file)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, cursor_path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)

    username, password = accounts[selected - 1]
    return selected, username, password
