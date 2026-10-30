import os
import re
import sys

from account_rotation import select_rotating_account
from browser_sender import BrowserSender
from processing_state import (
    MAX_SUCCESSES_PER_LINK,
    can_process,
    load_failures,
    load_successes,
    record_result,
)

LINK_PATTERN = re.compile(r"^https://t\.me/shmsannews/\d+$")
STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")


def log(message):
    print(message, flush=True)


def process_manual_retry(link, sender, state_dir=STATE_DIR):
    successes = load_successes(state_dir)
    failures = load_failures(state_dir)
    if not can_process(link, state_dir, successes, failures):
        if successes.get(link, 0) >= MAX_SUCCESSES_PER_LINK:
            log(f"⛔ رُفض الطلب: بلغ الرابط الحد الأقصى ({MAX_SUCCESSES_PER_LINK} نجاحات).")
        else:
            log("⛔ رُفض الطلب: الرابط مكتمل في السجل القديم أو تم تخطيه بعد ثلاث إخفاقات.")
        return 2

    status = sender(link)
    if status == "fatal":
        log("⏹️ توقف: تحقق من بيانات الدخول أو الرصيد.")
        return 1
    _, result = record_result(link, status, state_dir, successes, failures)
    if result == "success":
        log(f"✅ نجاح الطلب؛ العداد التراكمي {successes[link]}/{MAX_SUCCESSES_PER_LINK}.")
        return 0
    if result == "failure_limit":
        log("🚫 تم تخطي الرابط بعد ثلاث محاولات فاشلة.")
    log("⚠️ لم ينجح الطلب عبر الموقع.")
    return 1


def main():
    link = os.environ.get("TARGET_LINK", "").strip()
    if not LINK_PATTERN.fullmatch(link):
        log("❌ الرابط غير صالح؛ يلزم رابط منشور من قناة shmsannews.")
        return 2

    successes = load_successes(STATE_DIR)
    failures = load_failures(STATE_DIR)
    if not can_process(link, STATE_DIR, successes, failures):
        if successes.get(link, 0) >= MAX_SUCCESSES_PER_LINK:
            log(f"⛔ رُفض الطلب: بلغ الرابط الحد الأقصى ({MAX_SUCCESSES_PER_LINK} نجاحات).")
        else:
            log("⛔ رُفض الطلب: الرابط مكتمل في السجل القديم أو تم تخطيه بعد ثلاث إخفاقات.")
        return 2

    try:
        account_number, username, password = select_rotating_account(os.environ, STATE_DIR)
    except (OSError, ValueError) as error:
        log(f"❌ تعذر اختيار حساب؛ تحقق من أسرار الحسابات الخمسة وحالة account_rotation.json: {error}")
        return 1

    log(f"🚀 إعادة تشغيل يدوية للرابط المحدد: {link} | الحساب رقم {account_number}")
    with BrowserSender(username, password) as sender:
        return process_manual_retry(link, sender)


if __name__ == "__main__":
    sys.exit(main())
