import os
import random
import re
import sys

from playwright.sync_api import sync_playwright

SITE_URL = os.environ.get("SITE_URL", "https://prince.services")
CATEGORY_TEXT = "خدمات مجانية"
SERVICE_TEXT = "9336"
QUANTITY = "100"
CONFIRM_TEXT = "تاكيد الطلب"
SUCCESS_TEXT = "تم استلام طلبك بنجاح"
# HEADLESS=1 للتشغيل بدون نافذة، والافتراضي نافذة حقيقية (على السيرفر: xvfb-run python main.py)
HEADLESS = os.environ.get("HEADLESS") == "1" or (
    sys.platform.startswith("linux") and not os.environ.get("DISPLAY")
)


def log(message):
    print(f"[browser] {message}", flush=True)


def pause(p, lo=300, hi=900):
    p.wait_for_timeout(random.randint(lo, hi))


def human_move(p, loc):
    loc.scroll_into_view_if_needed()
    pause(p, 150, 400)
    b = loc.bounding_box()
    if not b:
        return None
    x = b["x"] + b["width"] * random.uniform(0.25, 0.75)
    y = b["y"] + b["height"] * random.uniform(0.3, 0.7)
    p.mouse.move(x, y, steps=random.randint(18, 40))
    pause(p, 80, 250)
    return x, y


def human_click(p, loc):
    pos = human_move(p, loc)
    if pos is None:
        loc.evaluate("(e) => e.click()")
        return
    # إن غطّى عنصر عائم الزر نرجع للنقر البرمجي
    covered = loc.evaluate(
        """(e, pt) => {
            const t = document.elementFromPoint(pt[0], pt[1]);
            return !(t && (e === t || e.contains(t)));
        }""",
        [pos[0], pos[1]],
    )
    if covered:
        loc.evaluate("(e) => e.click()")
        return
    p.mouse.down()
    pause(p, 40, 130)
    p.mouse.up()


def human_type(p, loc, text):
    human_click(p, loc)
    p.keyboard.press("Control+A")
    p.keyboard.press("Backspace")
    for ch in text:
        p.keyboard.type(ch, delay=random.randint(45, 170))
        if random.random() < 0.06:
            pause(p, 200, 600)  # توقف عرضي
    pause(p, 300, 800)


class BrowserSender:
    """تسجيل الدخول مرة واحدة وإرسال رابط واحد عبر نموذج الموقع."""

    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.pw = None
        self.browser = None
        self.page = None
        self.logged_in = False

    def __enter__(self):
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(
            headless=HEADLESS,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = self.browser.new_context(
            locale="ar",
            timezone_id="Asia/Aden",
            viewport={"width": random.choice([1280, 1366, 1440]), "height": 900},
        )
        context.set_default_timeout(30000)
        self.page = context.new_page()
        return self

    def __exit__(self, *exc):
        try:
            if self.browser:
                self.browser.close()
        finally:
            if self.pw:
                self.pw.stop()

    def _first_visible(self, *locators):
        for locator in locators:
            try:
                if locator.count() and locator.first.is_visible():
                    return locator.first
            except Exception:
                continue
        return None

    def _goto(self, url):
        try:
            self.page.goto(url, wait_until="commit", timeout=180000)
        except Exception as error:
            log(f"تأخر فتح الصفحة: {type(error).__name__}")
            self.page.wait_for_timeout(1000)

    def _link_field(self):
        p = self.page
        return self._first_visible(
            p.locator("#orders-link"),
            p.get_by_label("الرابط", exact=True),
            p.locator("input[name*='link' i]"),
            p.locator("input[placeholder*='رابط']"),
        )

    def _quantity_field(self):
        p = self.page
        return self._first_visible(
            p.locator("#orders-quantity"),
            p.get_by_label("الكمية", exact=True),
            p.locator("input[name*='quantity' i]"),
        )

    def _login(self):
        p = self.page
        self._goto(SITE_URL + "/")
        pause(p, 1000, 2200)
        password_field = p.locator("input[type='password']").first
        try:
            password_field.wait_for(state="visible", timeout=20000)
        except Exception:
            return self._link_field() is not None

        form = password_field.locator("xpath=ancestor::form[1]")
        user_field = form.locator(
            "input[type='text'], input[type='email'], input:not([type])"
        ).first
        human_type(p, user_field, self.username)
        pause(p, 400, 1000)
        human_type(p, password_field, self.password)
        pause(p, 400, 1000)
        submit = self._first_visible(
            form.locator("button[type='submit'], input[type='submit']"),
            form.get_by_role("button", name="تسجيل الدخول"),
        )
        if submit is None:
            raise RuntimeError("زر تسجيل الدخول غير موجود")
        human_click(p, submit)
        pause(p, 1200, 2500)
        return not (
            p.locator("input[type='password']").count()
            and p.locator("input[type='password']").first.is_visible()
        )

    def _open_order_page(self):
        p = self.page
        self._goto(f"{SITE_URL}/?service={SERVICE_TEXT}")
        for _ in range(240):
            service_options = p.locator(
                f"#orderform-service option[value='{SERVICE_TEXT}']"
            )
            if self._link_field() and service_options.count():
                return True
            p.wait_for_timeout(500)
        log("انتهت مهلة تحميل صفحة الخدمة والحقول")
        if self._link_field() or p.locator("select#orderform-category").count():
            return True
        for path in ("/neworder", "/new-order", "/"):
            self._goto(SITE_URL + path)
            pause(p, 800, 1500)
            if self._link_field() or p.locator("select#orderform-category").count():
                return True
        button = p.get_by_text("طلب جديد", exact=True)
        if button.count():
            human_click(p, button.first)
            pause(p, 500, 1000)
        return (
            self._link_field() is not None
            or p.locator("select#orderform-category").count() > 0
        )

    def _select(self, needle, retries=10):
        p = self.page
        for _ in range(retries):
            for s in p.locator("select:visible").all():
                for o in s.locator("option").all():
                    text = o.inner_text()
                    if needle in text:
                        human_move(p, s)
                        s.select_option(value=o.get_attribute("value"))
                        s.evaluate(
                            "(e) => { if (window.jQuery) window.jQuery(e).trigger('change'); }"
                        )
                        pause(p, 400, 1100)
                        return text.strip()
            p.wait_for_timeout(random.randint(400, 700))
        return None

    def __call__(self, link):
        p = self.page
        try:
            if not self.logged_in:
                if not self._login():
                    log("فشل تسجيل الدخول")
                    return "fatal"
                self.logged_in = True

            if not self._open_order_page():
                log("لم تُفتح صفحة الطلب")
                return "fail"
            pause(p, 600, 1500)
            if not self._select(CATEGORY_TEXT):
                log("لم يتم اختيار القسم")
                return "fail"
            pause(p, 700, 1400)
            if not self._select(SERVICE_TEXT):
                log(f"لم يتم اختيار الخدمة {SERVICE_TEXT}")
                return "fail"

            link_field = quantity_field = None
            for _ in range(240):
                link_field = self._link_field()
                quantity_field = self._quantity_field()
                if link_field is not None and quantity_field is not None:
                    break
                p.wait_for_timeout(500)
            if link_field is None or quantity_field is None:
                log("حقلا الرابط والكمية غير متاحين")
                return "fail"

            human_type(p, link_field, link)
            pause(p, 500, 1400)
            human_type(p, quantity_field, QUANTITY)
            pause(p, 700, 2000)  # "مراجعة" قبل التأكيد

            confirm = self._first_visible(
                p.get_by_role("button", name=CONFIRM_TEXT),
                p.locator(f"button:has-text('{CONFIRM_TEXT}')"),
                p.locator(f"input[type='submit'][value*='{CONFIRM_TEXT}']"),
            )
            if confirm is None:
                log("زر التأكيد غير متاح")
                return "fail"

            # النقر بالماوس، ويرجع تلقائياً للنقر البرمجي إن غطّى الزرَّ عنصرٌ عائم
            human_click(p, confirm)
            try:
                p.wait_for_function(
                    """(text) => {
                        const body = document.body.innerText || '';
                        return body.includes(text)
                          || body.includes('تم الطلب')
                          || body.includes('رقم الطلب')
                          || !!document.querySelector('.alert-success');
                    }""",
                    arg=SUCCESS_TEXT,
                    timeout=30000,
                )
            except Exception:
                alerts = p.locator(".alert, [role='alert']")
                message = ""
                if alerts.count():
                    message = alerts.first.inner_text().strip().replace("\n", " ")[:240]
                log(f"لم تظهر نتيجة نجاح بعد التأكيد: {message or 'لا توجد رسالة'}")
                return "fail"

            body = p.inner_text("body")
            order_match = re.search(r"الرقم\s*:?[\s]*(\d+)|(\d+)\s*:?[\s]*الرقم", body)
            if order_match:
                log(f"تم إنشاء الطلب، الرقم: {order_match.group(1) or order_match.group(2)}")
            else:
                log("تم إنشاء الطلب بنجاح")
            try:
                p.goto(SITE_URL + "/", wait_until="commit", timeout=10000)
            except Exception:
                pass
            return "ok"
        except Exception as error:
            log(f"استثناء أثناء الطلب: {type(error).__name__}: {str(error)[:160]}")
            return "fail"
