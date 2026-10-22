import os
import re
import sys

from browser_sender import BrowserSender

LINK_PATTERN = re.compile(r"^https://t\.me/shmsannews/\d+$")
DONE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "done_links.txt")


def log(message):
    print(message, flush=True)


def mark_done(link):
    os.makedirs(os.path.dirname(DONE_PATH), exist_ok=True)
    existing = []
    if os.path.exists(DONE_PATH):
        with open(DONE_PATH, "r", encoding="utf-8") as file:
            existing = [line.strip() for line in file if line.strip()]
    if link not in existing:
        with open(DONE_PATH, "a", encoding="utf-8") as file:
            file.write(link + "\n")


def main():
    username = os.environ.get("SITE_USERNAME", "").strip()
    password = os.environ.get("SITE_PASSWORD", "")
    link = os.environ.get("TARGET_LINK", "").strip()
    if not (username and password):
        log("❌ أضف SITE_USERNAME وSITE_PASSWORD في Secrets and variables > Actions.")
        return 1
    if not LINK_PATTERN.fullmatch(link):
        log("❌ الرابط غير صالح؛ يلزم رابط منشور من قناة shmsannews.")
        return 2

    log(f"🚀 إعادة تشغيل يدوية للرابط المحدد: {link}")
    with BrowserSender(username, password) as sender:
        status = sender(link)

    if status == "ok":
        log("✅ تم الطلب عبر الموقع.")
        mark_done(link)
        return 0
    log("⚠️ لم ينجح الطلب عبر الموقع.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
