import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from processing_state import (
    MAX_FAILURES_PER_LINK,
    MAX_SUCCESSES_PER_LINK,
    can_process,
    load_failures,
    load_successes,
    record_result,
)

# ══════════════ الإعدادات ══════════════
ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(ROOT, "state")
os.makedirs(STATE_DIR, exist_ok=True)

LINKS_PATH = os.path.join(STATE_DIR, "telegram_links.txt")        # طابور التنفيذ
PROCESSED_PATH = os.path.join(STATE_DIR, "processed_links.txt")   # روابط تم سحبها
DONE_PATH = os.path.join(STATE_DIR, "done_links.txt")             # روابط تم تنفيذها

TARGET_CHANNEL = "shmsannews"
ACCOUNT_COUNT = 3
START_AFTER_POST_ID = 2292

SERVICE_ID = "9336"
QUANTITY = "100"

MAX_ATTEMPTS_PER_LINK = MAX_FAILURES_PER_LINK  # بعد 3 إخفاقات يُتخطى الرابط نهائياً
MAX_TRIES_PER_RUN = 5       # أقصى عدد طلبات في التشغيل الواحد

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def is_after_start_link(link):
    match = re.fullmatch(rf"https://t\.me/{re.escape(TARGET_CHANNEL)}/(\d+)", link)
    return bool(match and int(match.group(1)) > START_AFTER_POST_ID)


# ══════════════ ملفات ══════════════
def read_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def append_lines(path, lines):
    with open(path, "a", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")


def load_attempts():
    return load_failures(STATE_DIR)


def save_attempts(data):
    from processing_state import save_failures
    save_failures(STATE_DIR, data)


# ══════════════ السحب ══════════════
def fetch_links():
    """روابط المنشورات الظاهرة في القناة، من الأقدم للأحدث."""
    url = f"https://t.me/s/{TARGET_CHANNEL}"
    response = requests.get(url, headers=HEADERS, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"كود الحالة {response.status_code}")
    pattern = rf"https://t\.me/{TARGET_CHANNEL}/(\d+)"
    ids = sorted({int(n) for n in re.findall(pattern, response.text)})
    return [f"https://t.me/{TARGET_CHANNEL}/{n}" for n in ids]


def pull_once():
    first_run = not os.path.exists(PROCESSED_PATH)
    discovered = fetch_links()
    discovered = [link for link in discovered if is_after_start_link(link)]
    if not discovered:
        log("⏹️ لم يتم العثور على منشورات في الصفحة.")
        return []

    processed = set(read_lines(PROCESSED_PATH))
    new_links = [l for l in discovered if l not in processed]
    if not new_links:
        log("🔎 لا توجد منشورات جديدة.")
        return []

    if first_run:
        # أول تشغيل: الأحدث فقط، وباقي ما في الصفحة يُسجَّل كمعالج دون تنفيذ
        append_lines(LINKS_PATH, [new_links[-1]])
        append_lines(PROCESSED_PATH, new_links)
        log(f"🆕 أول تشغيل، أُضيف أحدث رابط فقط: {new_links[-1]}")
        if len(new_links) > 1:
            log(f"ℹ️ تم تجاهل {len(new_links) - 1} منشور أقدم.")
        return [new_links[-1]]

    # الأشغال العادية: كل الجديد بالترتيب (الأقدم أولاً)
    append_lines(LINKS_PATH, new_links)
    append_lines(PROCESSED_PATH, new_links)
    log(f"✨ أُضيف {len(new_links)} رابط جديد إلى الطابور.")
    return new_links


def pending_links(attempts, successes=None, state_dir=STATE_DIR, priority_links=None):
    successes = load_successes(state_dir) if successes is None else successes
    links = read_lines(os.path.join(state_dir, "telegram_links.txt"))
    queued = set(links)
    priority = list(dict.fromkeys(reversed(priority_links or [])))
    priority_set = set(priority)
    ordered_links = [link for link in priority if link in queued]
    ordered_links.extend(link for link in reversed(links) if link not in priority_set)
    result = []
    for link in ordered_links:
        if not is_after_start_link(link) or not can_process(link, state_dir, successes, attempts):
            continue
        if link not in result:
            result.append(link)
    return result


def run_browser_send(sender, state_dir=STATE_DIR, priority_links=None):
    attempts = load_failures(state_dir)
    successes = load_successes(state_dir)
    queue = pending_links(attempts, successes, state_dir, priority_links)
    if not queue:
        log("💤 الطابور فارغ، لا شيء للتنفيذ.")
        return 0

    log(f"📋 روابط مؤهلة: {len(queue)} رابط؛ حد التشغيل {MAX_TRIES_PER_RUN} طلبات.")
    tries = 0
    for link in queue:
        if tries >= MAX_TRIES_PER_RUN:
            break
        tries += 1
        log(f"معالجة ({successes.get(link, 0)}/{MAX_SUCCESSES_PER_LINK} نجاح): {link}")
        status = sender(link)

        if status == "ok":
            _, result = record_result(link, status, state_dir, successes, attempts)
            if result == "success":
                log(f"✅ نجاح الرابط: {successes[link]}/{MAX_SUCCESSES_PER_LINK}.")
            if successes.get(link, 0) >= MAX_SUCCESSES_PER_LINK:
                log("✅ اكتمل هذا الرابط بعد بلوغ خمسة نجاحات.")
            continue
        if status == "fatal":
            log("⏹️ توقف: تحقق من بيانات الدخول أو الرصيد.")
            return 1

        record_result(link, status, state_dir, successes, attempts)
        if attempts[link] >= MAX_ATTEMPTS_PER_LINK:
            log(f"🚫 تم تخطي الرابط نهائياً بعد {MAX_ATTEMPTS_PER_LINK} محاولات فاشلة.")

    log(f"انتهى التشغيل بعد {tries} طلبات؛ حد التشغيل {MAX_TRIES_PER_RUN}.")
    return 0


def load_accounts(environ):
    accounts = []
    missing = []
    for number in range(1, ACCOUNT_COUNT + 1):
        username = environ.get(f"SITE_USERNAME_{number}", "").strip()
        password = environ.get(f"SITE_PASSWORD_{number}", "")
        if not username:
            missing.append(f"SITE_USERNAME_{number}")
        if not password:
            missing.append(f"SITE_PASSWORD_{number}")
        accounts.append((number, username, password))
    if missing:
        raise ValueError("Missing required secrets: " + ", ".join(missing))
    return accounts


def _send_account(account, links):
    number, username, password = account
    results = []
    try:
        from browser_sender import BrowserSender

        with BrowserSender(username, password) as sender:
            for link in links:
                log(f"الحساب {number}: معالجة {link}")
                try:
                    status = sender(link)
                except Exception as error:
                    log(f"الحساب {number}: تعذر تنفيذ الرابط ({type(error).__name__}: {error})")
                    status = "fail"
                results.append((link, status))
                if status == "fatal":
                    log(f"الحساب {number}: توقف بسبب مشكلة دخول أو رصيد.")
                    break
    except Exception as error:
        log(f"الحساب {number}: تعذر تشغيل المتصفح ({type(error).__name__}: {error})")
        results.extend((link, "fail") for link in links[len(results):])
    return results


def run_all_accounts(accounts, state_dir=STATE_DIR, priority_links=None):
    """Submit the same eligible links concurrently from all three accounts."""
    attempts = load_failures(state_dir)
    successes = load_successes(state_dir)
    queue = pending_links(attempts, successes, state_dir, priority_links)
    if not queue:
        log("💤 الطابور فارغ، لا شيء للتنفيذ.")
        return 0

    links = queue[:MAX_TRIES_PER_RUN]
    log(f"📋 روابط مؤهلة: {len(links)}؛ سترسل الحسابات الثلاثة طلبًا لكل رابط بالتوازي.")
    with ThreadPoolExecutor(max_workers=ACCOUNT_COUNT) as pool:
        futures = [pool.submit(_send_account, account, links) for account in accounts]
        account_results = [future.result() for future in futures]

    had_failure = False
    for link in links:
        statuses = [dict(result).get(link) for result in account_results]
        successes = load_successes(state_dir)
        attempts = load_failures(state_dir)
        for status in statuses:
            if status == "fatal":
                had_failure = True
                continue
            if status is None:
                continue
            _, result = record_result(link, status, state_dir, successes, attempts)
            if status != "ok":
                had_failure = True
                if result == "failure_limit":
                    log(f"🚫 تم تخطي الرابط نهائياً بعد {MAX_ATTEMPTS_PER_LINK} محاولات فاشلة.")
        successes = load_successes(state_dir)
        log(f"نتيجة الرابط {link}: {successes.get(link, 0)}/{MAX_SUCCESSES_PER_LINK} نجاحات.")

    if any(len(result) < len(links) for result in account_results):
        had_failure = True
    log(f"انتهى التشغيل بعد {len(links)} روابط؛ حتى {len(links) * ACCOUNT_COUNT} طلبات.")
    return 1 if had_failure else 0


def main():
    try:
        accounts = load_accounts(os.environ)
    except ValueError as error:
        log(f"❌ تعذر تحميل الحسابات؛ تحقق من الأسرار الستة: {error}")
        return 1

    log(f"🚀 بدء التشغيل | القناة: @{TARGET_CHANNEL} | الحسابات الثلاثة بالتوازي")

    new_links = []
    try:
        new_links = pull_once()
    except Exception as e:
        log(f"❌ خطأ في السحب: {type(e).__name__}: {e}")

    return run_all_accounts(accounts, priority_links=new_links)


if __name__ == "__main__":
    sys.exit(main())
