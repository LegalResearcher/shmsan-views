import json
import os
import re
import sys
import time

import requests

# ══════════════ الإعدادات ══════════════
ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(ROOT, "state")
os.makedirs(STATE_DIR, exist_ok=True)

LINKS_PATH = os.path.join(STATE_DIR, "telegram_links.txt")        # طابور التنفيذ
PROCESSED_PATH = os.path.join(STATE_DIR, "processed_links.txt")   # روابط تم سحبها
DONE_PATH = os.path.join(STATE_DIR, "done_links.txt")             # روابط تم تنفيذها
ATTEMPTS_PATH = os.path.join(STATE_DIR, "failed_attempts.json")   # عدّاد الفشل لكل رابط

TARGET_CHANNEL = "shmsannews"

SERVICE_ID = "9336"
QUANTITY = "100"

MAX_ATTEMPTS_PER_LINK = 3   # بعد 3 إخفاقات يُتخطى الرابط نهائياً
MAX_TRIES_PER_RUN = 3       # أقصى عدد طلبات في التشغيل الواحد

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
    if not os.path.exists(ATTEMPTS_PATH):
        return {}
    try:
        with open(ATTEMPTS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return {}


def save_attempts(data):
    with open(ATTEMPTS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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


def pending_links(attempts):
    done = set(read_lines(DONE_PATH))
    result = []
    for link in read_lines(LINKS_PATH):
        if link in done:
            continue
        if attempts.get(link, 0) >= MAX_ATTEMPTS_PER_LINK:
            continue
        if link not in result:
            result.append(link)
    return result


def run_browser_send(sender):
    attempts = load_attempts()
    queue = pending_links(attempts)
    if not queue:
        log("💤 الطابور فارغ، لا شيء للتنفيذ.")
        return 0

    log(f"📋 في الطابور: {len(queue)} رابط.")
    tries = 0
    for link in queue:
        if tries >= MAX_TRIES_PER_RUN:
            break
        tries += 1
        log(f"معالجة: {link}")
        status = sender(link)

        if status == "ok":
            append_lines(DONE_PATH, [link])
            return 0  # رابط ناجح واحد لكل تشغيل
        if status == "fatal":
            log("⏹️ توقف: تحقق من بيانات الدخول أو الرصيد.")
            return 1

        attempts[link] = attempts.get(link, 0) + 1
        save_attempts(attempts)
        if attempts[link] >= MAX_ATTEMPTS_PER_LINK:
            log(f"🚫 تم تخطي الرابط نهائياً بعد {MAX_ATTEMPTS_PER_LINK} محاولات فاشلة.")

    log("⚠️ لم ينجح أي طلب في هذا التشغيل.")
    return 0


def main():
    username = os.environ.get("SITE_USERNAME", "").strip()
    password = os.environ.get("SITE_PASSWORD", "")
    if not (username and password):
        log("❌ أضف SITE_USERNAME وSITE_PASSWORD في Secrets and variables > Actions.")
        return 1

    log(f"🚀 بدء التشغيل | القناة: @{TARGET_CHANNEL} | الوضع: browser")

    try:
        pull_once()
    except Exception as e:
        log(f"❌ خطأ في السحب: {type(e).__name__}: {e}")

    from browser_sender import BrowserSender

    with BrowserSender(username, password) as sender:
        return run_browser_send(sender)


if __name__ == "__main__":
    sys.exit(main())
