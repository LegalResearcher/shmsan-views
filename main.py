import json
import os
import random
import re
import sys
import time
import requests
from account_rotation import select_rotating_account
from processing_state import (
    can_process,
    load_account_history,
    load_failures,
    load_successes,
    mark_account_handled,
    is_automation_paused,
    pause_automation,
    record_result,
)

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(ROOT, "state")
os.makedirs(STATE_DIR, exist_ok=True)
PROCESSED_PATH = os.path.join(STATE_DIR, "processed_links.txt")
TARGET_CHANNEL = "shmsannews"
START_AFTER_POST_ID = 2292
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def is_after_start_link(link):
    match = re.fullmatch(rf"https://t\.me/{re.escape(TARGET_CHANNEL)}/(\d+)", link)
    return bool(match and int(match.group(1)) > START_AFTER_POST_ID)


def read_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as file:
        return [line.strip() for line in file if line.strip()]


def append_lines(path, lines):
    existing = set(read_lines(path))
    with open(path, "a", encoding="utf-8") as file:
        for line in lines:
            if line not in existing:
                file.write(line + "\n")
                existing.add(line)


def load_previous_batch(state_dir=STATE_DIR):
    path = os.path.join(state_dir, "previous_batch.json")
    if not os.path.exists(path):
        # ترحيل لمرة واحدة: فقط أول دفعة كانت مؤهلة في النظام السابق.
        legacy_links = list(dict.fromkeys(read_lines(os.path.join(state_dir, "telegram_links.txt"))))
        successes = load_successes(state_dir)
        failures = load_failures(state_dir)
        eligible = [link for link in legacy_links
                    if can_process(link, state_dir, successes, failures)]
        return eligible
    try:
        with open(path, "r", encoding="utf-8") as file:
            value = json.load(file)
        if isinstance(value, list):
            return list(dict.fromkeys(item for item in value if isinstance(item, str)))
    except (OSError, ValueError):
        pass
    return []


def save_previous_batch(links, state_dir=STATE_DIR):
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, "previous_batch.json")
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(list(dict.fromkeys(links)), file, ensure_ascii=False, indent=2)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def fetch_links():
    """روابط المنشورات الظاهرة في القناة، من الأقدم إلى الأحدث."""
    url = f"https://t.me/s/{TARGET_CHANNEL}"
    response = requests.get(url, headers=HEADERS, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"كود الحالة {response.status_code}")
    pattern = rf"https://t\.me/{TARGET_CHANNEL}/(\d+)"
    ids = sorted({int(number) for number in re.findall(pattern, response.text)})
    return [f"https://t.me/{TARGET_CHANNEL}/{number}" for number in ids]


def discover_new_links(state_dir=STATE_DIR, discovered=None):
    """يسجل الروابط المكتشفة حديثاً ويعيدها مرة واحدة فقط."""
    discovered = fetch_links() if discovered is None else discovered
    discovered = [link for link in discovered if is_after_start_link(link)]
    processed_path = os.path.join(state_dir, "processed_links.txt")
    known = set(read_lines(processed_path))
    new_links = [link for link in discovered if link not in known]
    append_lines(processed_path, new_links)
    log(f"🆕 روابط جديدة: {len(new_links)}" if new_links else "🔎 لا توجد روابط جديدة.")
    return list(dict.fromkeys(new_links))


def prepare_run_batch(new_links, state_dir=STATE_DIR):
    """الجديد أولاً ثم دفعة التشغيل السابق، مع إزالة التكرار."""
    previous = load_previous_batch(state_dir)
    batch = [link for link in dict.fromkeys(list(new_links) + previous)
             if is_after_start_link(link)]
    return batch


def pending_links(links, account_number, state_dir=STATE_DIR):
    history = load_account_history(state_dir)
    successes = load_successes(state_dir)
    failures = load_failures(state_dir)
    return [link for link in dict.fromkeys(links)
            if account_number not in history.get(link, [])
            and can_process(link, state_dir, successes, failures)]


def run_browser_send(sender, state_dir=STATE_DIR, account_number=1, links=None):
    """يعالج الجديد ثم الدفعة السابقة، ولا يكرر الرابط على الحساب نفسه."""
    batch = load_previous_batch(state_dir) if links is None else list(dict.fromkeys(links))
    queue = pending_links(batch, account_number, state_dir)
    if not queue:
        log(f"💤 لا روابط متاحة للحساب {account_number} ضمن الدفعة الحالية.")
        return 0
    log(f"📋 روابط مؤهلة للحساب {account_number}: {len(queue)}.")
    tries = 0
    for link in queue:
        if tries > 0:
            delay = random.uniform(40, 150)
            log(f"انتظار {delay:.1f} ثانية قبل الطلب التالي.")
            time.sleep(delay)
        log(f"معالجة الرابط للحساب {account_number}: {link}")
        # نسجل قبل الإرسال حتى لا يعاد الرابط للحساب نفسه إذا انقطع التشغيل بعد الطلب.
        mark_account_handled(link, account_number, state_dir)
        status = sender(link)
        if status == "fatal":
            pause_automation(state_dir, "fatal account/login/balance failure")
            log("⏹️ توقف: تعذر إكمال المعالجة بسبب حالة الحساب أو الرصيد.")
            return 1
        tries += 1
        _, result = record_result(link, status, state_dir)
        if result == "success":
            log(f"✅ نجحت معالجة الرابط على الحساب {account_number}.")
        else:
            if result == "failure_limit":
                log("🚫 تم تخطي الرابط بعد ثلاث محاولات فاشلة.")
            log(f"⚠️ فشلت محاولة الرابط على الحساب {account_number}؛ سيُترك للحسابات التالية.")
    log(f"انتهى التشغيل بعد {tries} طلبات.")
    save_previous_batch(
        [item for item in batch if can_process(item, state_dir)], state_dir
    )
    return 0


def main():
    if is_automation_paused(STATE_DIR):
        log("⏸️ الجدولة متوقفة مؤقتًا؛ احذف state/automation_paused وأعد تفعيل workflow يدويًا.")
        return 1
    try:
        new_links = discover_new_links()
    except Exception as error:
        log(f"❌ خطأ في السحب: {type(error).__name__}: {error}")
        new_links = []
    batch = prepare_run_batch(new_links, STATE_DIR)
    eligible = [link for link in batch if can_process(link, STATE_DIR)]
    if not eligible:
        save_previous_batch([], STATE_DIR)
        log("💤 لا توجد روابط جديدة أو في الدفعة السابقة.")
        return 0
    eligible_accounts = [number for number in range(1, 10)
                         if pending_links(eligible, number, STATE_DIR)]
    if not eligible_accounts:
        save_previous_batch([], STATE_DIR)
        log("💤 لا روابط مؤهلة لأي حساب.")
        return 0
    try:
        selected = select_rotating_account(
            os.environ, STATE_DIR,
            eligible_accounts=eligible_accounts,
        )
        if selected is None:
            save_previous_batch([], STATE_DIR)
            log("💤 لا روابط مؤهلة لأي حساب.")
            return 0
        account_number, username, password = selected
    except (OSError, ValueError) as error:
        log(f"❌ تعذر اختيار حساب؛ تحقق من الأسرار التسعة وحالة account_rotation.json: {error}")
        return 1
    log(f"🚀 بدء التشغيل | القناة: @{TARGET_CHANNEL} | الحساب رقم {account_number}")
    from browser_sender import BrowserSender
    with BrowserSender(username, password) as sender:
        return run_browser_send(sender, STATE_DIR, account_number, batch)


if __name__ == "__main__":
    sys.exit(main())
