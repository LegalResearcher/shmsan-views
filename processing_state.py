"""Persistent per-link counters shared by scheduled and manual processing."""
import json
import os
import tempfile

MAX_SUCCESSES_PER_LINK = 5
MAX_FAILURES_PER_LINK = 3


def _path(state_dir, filename):
    return os.path.join(state_dir, filename)


def read_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as file:
        return [line.strip() for line in file if line.strip()]


def _load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as file:
            value = json.load(file)
        return value if isinstance(value, dict) else {}
    except (ValueError, OSError):
        return {}


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".state-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_successes(state_dir):
    raw = _load_json(_path(state_dir, "success_counts.json"))
    return {
        link: count for link, count in raw.items()
        if isinstance(link, str) and isinstance(count, int)
        and not isinstance(count, bool) and count >= 0
    }


def save_successes(state_dir, counts):
    _save_json(_path(state_dir, "success_counts.json"), counts)


def load_failures(state_dir):
    raw = _load_json(_path(state_dir, "failed_attempts.json"))
    return {
        link: count for link, count in raw.items()
        if isinstance(link, str) and isinstance(count, int)
        and not isinstance(count, bool) and count >= 0
    }


def save_failures(state_dir, counts):
    _save_json(_path(state_dir, "failed_attempts.json"), counts)


def load_account_history(state_dir):
    """Return account numbers that have already attempted each link."""
    raw = _load_json(_path(state_dir, "link_account_history.json"))
    result = {}
    for link, accounts in raw.items():
        if not isinstance(link, str) or not isinstance(accounts, list):
            continue
        result[link] = sorted({account for account in accounts
                               if isinstance(account, int) and not isinstance(account, bool)
                               and 1 <= account <= 5})
    return result


def mark_account_handled(link, account_number, state_dir):
    """Persist that this account already attempted this link."""
    if (not isinstance(account_number, int) or isinstance(account_number, bool)
            or not 1 <= account_number <= 5):
        raise ValueError("account_number must be between 1 and 5")
    history = load_account_history(state_dir)
    accounts = set(history.get(link, []))
    accounts.add(account_number)
    history[link] = sorted(accounts)
    _save_json(_path(state_dir, "link_account_history.json"), history)


def is_legacy_complete(link, state_dir):
    """Treat the old completion log as authoritative without rewriting it."""
    return link in set(read_lines(_path(state_dir, "done_links.txt")))


def can_process(link, state_dir, successes=None, failures=None):
    successes = load_successes(state_dir) if successes is None else successes
    failures = load_failures(state_dir) if failures is None else failures
    return (
        not is_legacy_complete(link, state_dir)
        and successes.get(link, 0) < MAX_SUCCESSES_PER_LINK
        and failures.get(link, 0) < MAX_FAILURES_PER_LINK
    )


def record_result(link, status, state_dir, successes=None, failures=None):
    """Persist each result; only a successful request increments successes."""
    successes = load_successes(state_dir) if successes is None else successes
    failures = load_failures(state_dir) if failures is None else failures
    if status == "ok":
        if is_legacy_complete(link, state_dir):
            return False, "legacy_complete"
        if successes.get(link, 0) >= MAX_SUCCESSES_PER_LINK:
            return False, "success_limit"
        successes[link] = successes.get(link, 0) + 1
        save_successes(state_dir, successes)
        return True, "success"
    failures[link] = failures.get(link, 0) + 1
    save_failures(state_dir, failures)
    return False, "failure_limit" if failures[link] >= MAX_FAILURES_PER_LINK else "failure"
