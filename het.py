#!/usr/bin/env python3
import os, json, re, time, random, logging
from datetime import time as dt_time

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# ========== CẤU HÌNH ==========
TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"         # <-- THAY TOKEN THẬT
DOWNLOAD_DIR = "downloads"
COOKIE_FILE = "cookies.json"
STREAK_FILE = "streak_users.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# ========== QUẢN LÝ TRÌNH DUYỆT ==========
class TikTokBrowser:
    def __init__(self):
        self.browser = None
        self.cookie_file = COOKIE_FILE

    def start_browser(self):
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
        service = Service(ChromeDriverManager().install())
        self.browser = webdriver.Chrome(service=service, options=options)

    def save_cookie(self, cookie_text: str) -> bool:
        cookie_text = cookie_text.strip()
        if not cookie_text:
            return False
        if cookie_text.startswith("["):
            try:
                json.loads(cookie_text)
                with open(COOKIE_FILE, "w") as f:
                    f.write(cookie_text)
                self.cookie_file = COOKIE_FILE
                return True
            except:
                return False
        if cookie_text.startswith("# Netscape") or ".tiktok.com\tTRUE" in cookie_text[:200]:
            with open("cookies.txt", "w") as f:
                f.write(cookie_text)
            self.cookie_file = "cookies.txt"
            return True
        lines = re.split(r";\s*|\n", cookie_text)
        with open("cookies.txt", "w") as f:
            for line in lines:
                line = line.strip()
                if "=" in line:
                    f.write(line + "\n")
        self.cookie_file = "cookies.txt"
        return True

    def load_cookies(self):
        if not os.path.exists(self.cookie_file):
            raise FileNotFoundError("Chưa có cookie. Dùng /setcookie.")
        self.browser.get("https://www.tiktok.com")
        time.sleep(2)
        if self.cookie_file.endswith(".json"):
            with open(self.cookie_file, "r") as f:
                cookies = json.load(f)
            for c in cookies:
                try:
                    self.browser.add_cookie(c)
                except:
                    pass
        else:
            with open(self.cookie_file, "r") as f:
                content = f.read()
            if content.startswith("# Netscape"):
                from http.cookiejar import MozillaCookieJar
                jar = MozillaCookieJar(self.cookie_file)
                jar.load(ignore_discard=True, ignore_expires=True)
                for c in jar:
                    try:
                        self.browser.add_cookie({
                            "name": c.name, "value": c.value,
                            "domain": c.domain, "path": c.path,
                            "secure": c.secure,
                            "expiry": int(c.expires) if c.expires else None
                        })
                    except:
                        pass
            else:
                for line in content.splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        try:
                            self.browser.add_cookie({
                                "name": k.strip(), "value": v.strip(),
                                "domain": ".tiktok.com"
                            })
                        except:
                            pass
        self.browser.refresh()
        time.sleep(5)

    def is_logged_in(self):
        try:
            self.browser.find_element(By.CSS_SELECTOR, '[data-e2e="top-login-avatar"]')
            return True
        except:
            return False

    def get_random_video_url(self):
        self.browser.get("https://www.tiktok.com/foryou")
        time.sleep(3)
        self.browser.execute_script("window.scrollBy(0, 800)")
        time.sleep(2)
        links = self.browser.find_elements(By.CSS_SELECTOR, 'a[href*="/video/"]')
        if links:
            return random.choice(links).get_attribute("href")
        return "https://www.tiktok.com/@tiktok/video/7106593419591814446"

    def send_message(self, username, video_url):
        self.browser.get(f"https://www.tiktok.com/@{username}")
        time.sleep(3)
        try:
            input_box = WebDriverWait(self.browser, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '[contenteditable="true"]'))
            )
            input_box.clear()
            input_box.send_keys(video_url)
            time.sleep(1)
            input_box.send_keys("\ue007")
            time.sleep(2)
            return True
        except Exception as e:
            logger.error(f"Gửi thất bại @{username}: {e}")
            return False

    def close(self):
        if self.browser:
            self.browser.quit()

# ========== QUẢN LÝ DANH SÁCH STREAK ==========
def load_streak():
    if os.path.exists(STREAK_FILE):
        with open(STREAK_FILE, "r") as f:
            return json.load(f)
    return []

def save_streak(users):
    with open(STREAK_FILE, "w") as f:
        json.dump(users, f)

# ========== COMMAND HANDLERS ==========
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Bot giữ streak TikTok.\n\n"
        "/setcookie - Nhập cookie\n"
        "/add @user - Thêm người vào danh sách\n"
        "/remove @user - Xóa khỏi danh sách\n"
        "/list - Xem danh sách\n"
        "/send @user - Gửi video cho 1 người\n"
        "/sendall - Gửi video cho tất cả\n"
        "/schedule - Bật/tắt gửi tự động 23:00"
    )

async def setcookie_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📎 Gửi nội dung cookie để mình lưu.")
    context.user_data["waiting_cookie"] = True

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("waiting_cookie"):
        t = TikTokBrowser()
        if t.save_cookie(update.message.text):
            await update.message.reply_text(f"✅ Cookie đã lưu ({t.cookie_file}).")
        else:
            await update.message.reply_text("❌ Cookie không đúng định dạng.")
        context.user_data["waiting_cookie"] = False
        return
    # Không phải chờ cookie, không làm gì cả
    await update.message.reply_text("Dùng /start để xem lệnh.")

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        target = context.args[0].lstrip("@")
    except IndexError:
        await update.message.reply_text("📌 Dùng: /add @username")
        return
    users = load_streak()
    if target in users:
        await update.message.reply_text(f"@{target} đã có trong danh sách.")
        return
    users.append(target)
    save_streak(users)
    await update.message.reply_text(f"✅ Đã thêm @{target}.")

async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        target = context.args[0].lstrip("@")
    except IndexError:
        await update.message.reply_text("📌 Dùng: /remove @username")
        return
    users = load_streak()
    if target not in users:
        await update.message.reply_text(f"@{target} không có trong danh sách.")
        return
    users.remove(target)
    save_streak(users)
    await update.message.reply_text(f"❌ Đã xóa @{target}.")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = load_streak()
    await update.message.reply_text("📋 " + "\n".join(users) if users else "Danh sách trống.")

async def send_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        target = context.args[0].lstrip("@")
    except IndexError:
        await update.message.reply_text("📌 Dùng: /send @username")
        return
    await update.message.reply_text(f"🎬 Gửi video tới @{target}...")
    t = TikTokBrowser()
    try:
        t.start_browser()
        t.load_cookies()
        if not t.is_logged_in():
            await update.message.reply_text("❌ Cookie không đúng hoặc hết hạn.")
            return
        url = t.get_random_video_url()
        if t.send_message(target, url):
            await update.message.reply_text(f"✅ Đã gửi video cho @{target}")
        else:
            await update.message.reply_text("❌ Gửi thất bại.")
    except FileNotFoundError:
        await update.message.reply_text("❌ Chưa có cookie. Dùng /setcookie.")
    except Exception as e:
        await update.message.reply_text(f"Lỗi: {e}")
    finally:
        t.close()

async def sendall_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = load_streak()
    if not users:
        await update.message.reply_text("❌ Danh sách trống. Dùng /add để thêm người.")
        return
    await update.message.reply_text(f"🚀 Gửi video cho {len(users)} người...")
    t = TikTokBrowser()
    try:
        t.start_browser()
        t.load_cookies()
        if not t.is_logged_in():
            await update.message.reply_text("❌ Cookie lỗi.")
            return
        ok = 0
        for u in users:
            url = t.get_random_video_url()
            if t.send_message(u, url):
                ok += 1
            time.sleep(2)  # tránh spam
        await update.message.reply_text(f"✅ Đã gửi {ok}/{len(users)}.")
    except FileNotFoundError:
        await update.message.reply_text("❌ Chưa có cookie.")
    except Exception as e:
        await update.message.reply_text(f"Lỗi: {e}")
    finally:
        t.close()

# ========== TỰ ĐỘNG GỬI 23:00 ==========
JOB_NAME = "daily_streak"
async def auto_send_job(context: ContextTypes.DEFAULT_TYPE):
    users = load_streak()
    if not users:
        logger.info("Không có danh sách streak.")
        return
    t = TikTokBrowser()
    try:
        t.start_browser()
        t.load_cookies()
        if not t.is_logged_in():
            logger.error("Cookie lỗi.")
            return
        for u in users:
            url = t.get_random_video_url()
            t.send_message(u, url)
            time.sleep(2)
        logger.info("Đã gửi tự động.")
    except Exception as e:
        logger.error(f"Lỗi auto: {e}")
    finally:
        t.close()

async def schedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    jq = context.application.job_queue
    jobs = jq.get_jobs_by_name(JOB_NAME)
    if jobs:
        for j in jobs:
            j.schedule_removal()
        await update.message.reply_text("⏰ Đã TẮT tự động 23:00.")
    else:
        jq.run_daily(auto_send_job, time=dt_time(hour=23, minute=0), name=JOB_NAME)
        await update.message.reply_text("⏰ Đã BẬT tự động 23:00 mỗi ngày.")

# ========== CHẠY BOT ==========
def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("setcookie", setcookie_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("send", send_cmd))
    app.add_handler(CommandHandler("sendall", sendall_cmd))
    app.add_handler(CommandHandler("schedule", schedule_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    logger.info("Bot đã sẵn sàng!")
    app.run_polling()

if __name__ == "__main__":
    main()
