#!/usr/bin/env python3
import os, json, time, logging
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
TOKEN = "YOUR_NEW_BOT_TOKEN"              # <-- THAY TOKEN MỚI
COOKIE_FILE = "cookies.json"
STREAK_FILE = "streak_users.json"
VIDEO_FILE = "saved_video.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def load_saved_video():
    if os.path.exists(VIDEO_FILE):
        with open(VIDEO_FILE, "r") as f:
            return json.load(f).get("url")
    return None

def save_video_url(url):
    with open(VIDEO_FILE, "w") as f:
        json.dump({"url": url}, f)

def load_streak():
    if os.path.exists(STREAK_FILE):
        with open(STREAK_FILE, "r") as f:
            return json.load(f)
    return []

def save_streak(users):
    with open(STREAK_FILE, "w") as f:
        json.dump(users, f)

class TikTokBrowser:
    def __init__(self):
        self.browser = None

    def start_browser(self):
        options = webdriver.ChromeOptions()
        # Chỉ định đường dẫn Chrome binary (đảm bảo tìm thấy)
        chrome_path = "/usr/bin/google-chrome-stable"
        if not os.path.exists(chrome_path):
            chrome_path = "/usr/bin/google-chrome"
        options.binary_location = chrome_path
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
        # Tự động tải chromedriver phù hợp
        service = Service(ChromeDriverManager().install())
        self.browser = webdriver.Chrome(service=service, options=options)

    def save_cookie(self, cookie_text: str) -> bool:
        cookie_text = cookie_text.strip()
        if not cookie_text:
            return False
        try:
            if cookie_text.startswith("["):
                json.loads(cookie_text)
                with open(COOKIE_FILE, "w") as f:
                    f.write(cookie_text)
                return True
            elif cookie_text.startswith("# Netscape") or ".tiktok.com\tTRUE" in cookie_text[:200]:
                with open("cookies.txt", "w") as f:
                    f.write(cookie_text)
                return True
            else:
                with open("cookies.txt", "w") as f:
                    for line in cookie_text.replace(";", "\n").split("\n"):
                        line = line.strip()
                        if "=" in line:
                            f.write(line + "\n")
                return True
        except Exception as e:
            logger.error(f"Lưu cookie lỗi: {e}")
            return False

    def load_cookies(self):
        cookie_file = COOKIE_FILE if os.path.exists(COOKIE_FILE) else "cookies.txt"
        if not os.path.exists(cookie_file):
            raise FileNotFoundError("Chưa có cookie.")
        self.browser.get("https://www.tiktok.com")
        time.sleep(2)
        if cookie_file.endswith(".json"):
            with open(cookie_file, "r") as f:
                cookies = json.load(f)
            for c in cookies:
                try:
                    self.browser.add_cookie(c)
                except:
                    pass
        else:
            with open(cookie_file, "r") as f:
                content = f.read()
            if content.startswith("# Netscape"):
                from http.cookiejar import MozillaCookieJar
                jar = MozillaCookieJar(cookie_file)
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

    def send_message(self, username, video_url):
        if not username or not video_url:
            return False
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

# ========== CÁC LỆNH ==========
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Bot giữ streak.\n"
        "/setcookie – Nhập cookie\n/setvideo <link> – Lưu link\n/getvideo – Xem link\n"
        "/add @user – Thêm người\n/remove @user – Xóa\n/list – Danh sách\n"
        "/send @user – Gửi 1 người\n/sendall – Gửi tất cả\n/schedule – Bật/tắt 23:00"
    )

async def setcookie_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📎 Gửi cookie.")
    context.user_data["waiting_cookie"] = True

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("waiting_cookie"):
        t = TikTokBrowser()
        if t.save_cookie(update.message.text):
            await update.message.reply_text("✅ Cookie đã lưu.")
        else:
            await update.message.reply_text("❌ Cookie sai.")
        context.user_data["waiting_cookie"] = False
        return
    await update.message.reply_text("Dùng /start.")

async def setvideo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("📌 /setvideo <link>")
        return
    url = context.args[0]
    if "tiktok.com" not in url:
        await update.message.reply_text("⚠️ Link không hợp lệ.")
        return
    save_video_url(url)
    await update.message.reply_text(f"✅ Đã lưu: {url}")

async def getvideo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = load_saved_video()
    await update.message.reply_text(f"📼 {url}" if url else "❌ Chưa có video.")

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("📌 /add @user"); return
    target = context.args[0].lstrip("@")
    users = load_streak()
    if target in users:
        await update.message.reply_text(f"@{target} đã có.")
        return
    users.append(target)
    save_streak(users)
    await update.message.reply_text(f"✅ Đã thêm @{target}.")

async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("📌 /remove @user"); return
    target = context.args[0].lstrip("@")
    users = load_streak()
    if target in users:
        users.remove(target)
        save_streak(users)
        await update.message.reply_text(f"❌ Đã xóa @{target}.")
    else:
        await update.message.reply_text("Không có.")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = load_streak()
    await update.message.reply_text("📋 " + ("\n".join(users) if users else "Trống"))

async def send_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("📌 /send @user"); return
    target = context.args[0].lstrip("@")
    video_url = load_saved_video()
    if not video_url:
        await update.message.reply_text("❌ Chưa có video."); return
    await update.message.reply_text(f"🎬 Gửi @{target}...")
    t = TikTokBrowser()
    try:
        t.start_browser(); t.load_cookies()
        if not t.is_logged_in():
            await update.message.reply_text("❌ Cookie hết hạn."); return
        ok = t.send_message(target, video_url)
        await update.message.reply_text("✅ Đã gửi" if ok else "❌ Thất bại")
    except FileNotFoundError:
        await update.message.reply_text("❌ Chưa cookie.")
    except Exception as e:
        await update.message.reply_text(f"Lỗi: {e}")
    finally:
        t.close()

async def sendall_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = load_streak()
    if not users:
        await update.message.reply_text("❌ DS trống."); return
    video_url = load_saved_video()
    if not video_url:
        await update.message.reply_text("❌ Chưa video."); return
    await update.message.reply_text(f"🚀 Gửi {len(users)} người...")
    t = TikTokBrowser()
    try:
        t.start_browser(); t.load_cookies()
        if not t.is_logged_in():
            await update.message.reply_text("❌ Cookie lỗi."); return
        ok = 0
        for u in users:
            if t.send_message(u, video_url): ok += 1
            time.sleep(2)
        await update.message.reply_text(f"✅ {ok}/{len(users)}")
    except FileNotFoundError:
        await update.message.reply_text("❌ Chưa cookie.")
    except Exception as e:
        await update.message.reply_text(f"Lỗi: {e}")
    finally:
        t.close()

# ========== TỰ ĐỘNG 23:00 ==========
async def auto_send_job(context: ContextTypes.DEFAULT_TYPE):
    users = load_streak()
    video_url = load_saved_video()
    if not users or not video_url: return
    t = TikTokBrowser()
    try:
        t.start_browser(); t.load_cookies()
        if not t.is_logged_in(): return
        for u in users:
            t.send_message(u, video_url)
            time.sleep(2)
        logger.info("Auto send OK")
    except Exception as e:
        logger.error(f"Auto lỗi: {e}")
    finally:
        t.close()

async def schedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    jq = context.application.job_queue
    jobs = jq.get_jobs_by_name("daily")
    if jobs:
        for j in jobs: j.schedule_removal()
        await update.message.reply_text("⏰ Đã tắt tự động 23:00.")
    else:
        jq.run_daily(auto_send_job, time=dt_time(hour=23, minute=0), name="daily")
        await update.message.reply_text("⏰ Đã bật tự động 23:00.")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("setcookie", setcookie_cmd))
    app.add_handler(CommandHandler("setvideo", setvideo_cmd))
    app.add_handler(CommandHandler("getvideo", getvideo_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("send", send_cmd))
    app.add_handler(CommandHandler("sendall", sendall_cmd))
    app.add_handler(CommandHandler("schedule", schedule_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    logger.info("Bot sẵn sàng!")
    app.run_polling()

if __name__ == "__main__":
    main()