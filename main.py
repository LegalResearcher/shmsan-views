import json
import os
import re
import sys
import time

import requests
from account_rotation import select_rotating_account
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
    if not discovered:
        log("⏹️ لم يتم العثور على منشورات في الصفحة.")
        return

    processed = set(read_lines(PROCESSED_PATH))
    new_links = [l for l in discovered if l not in processed]
    if not new_links:
        log("🔎 لا توجد منشورات جديدة.")
        return

    if first_run:
        # أول تشغيل: الأحدث فقط، وباقي ما في الصفحة يُسجَّل كمعالج دون تنفيذ
        append_lines(LINKS_PATH, [new_links[-1]])
        append_lines(PROCESSED_PATH, new_links)
        log(f"🆕 أول تشغيل، أُضيف أحدث رابط فقط: {new_links[-1]}")
        if len(new_links) > 1:
            log(f"ℹ️ تم تجاهل {len(new_links) - 1} منشور أقدم.")
        return

    # الأشغال العادية: كل الجديد بالترتيب (الأقدم أولاً)
    append_lines(LINKS_PATH, new_links)
    append_lines(PROCESSED_PATH, new_links)
    log(f"✨ أُضيف {len(new_links)} رابط جديد إلى الطابور.")


def pending_links(attempts, successes=None, state_dir=STATE_DIR):
    successes = load_successes(state_dir) if successes is None else successes
    result = []
    for link in read_lines(os.path.join(state_dir, "telegram_links.txt")):
        if not can_process(link, state_dir, successes, attempts):
            continue
        if link not in result:
            result.append(link)
    return result


def run_browser_send(sender, state_dir=STATE_DIR):
    attempts = load_failures(state_dir)
    successes = load_successes(state_dir)
    queue = pending_links(attempts, successes, state_dir)
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


def main():
    try:
        account_number, username, password = select_rotating_account(os.environ, STATE_DIR)
    except (OSError, ValueError) as error:
        log(f"❌ تعذر اختيار حساب؛ تحقق من أسرار الحسابات الخمسة وحالة account_rotation.json: {error}")
        return 1

    log(f"🚀 بدء التشغيل | القناة: @{TARGET_CHANNEL} | الوضع: browser | الحساب رقم {account_number}")

    try:
        pull_once()
    except Exception as e:
        log(f"❌ خطأ في السحب: {type(e).__name__}: {e}")

    from browser_sender import BrowserSender

    with BrowserSender(username, password) as sender:
        return run_browser_send(sender)


if __name__ == "__main__":
    sys.exit(main())
