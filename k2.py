#!/usr/bin/env python3
import os, json, time, logging, subprocess
from datetime import time as dt_time

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ========== CẤU HÌNH ==========
TOKEN = "8542542587:AAHfZYddqwRUOo1o8LWezz1K1nGo_KmANi4"          # <-- Thay token mới
COOKIE_FILE = "cookies.json"
STREAK_FILE = "streak_users.json"
VIDEO_FILE = "saved_video.json"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def load_saved_video():
    if os.path.exists(VIDEO_FILE):
        with open(VIDEO_FILE, 'r') as f:
            return json.load(f).get('url')
    return None

def save_video_url(url):
    with open(VIDEO_FILE, 'w') as f:
        json.dump({'url': url}, f)

def load_streak():
    if os.path.exists(STREAK_FILE):
        with open(STREAK_FILE, 'r') as f:
            return json.load(f)
    return []

def save_streak(users):
    with open(STREAK_FILE, 'w') as f:
        json.dump(users, f)

# ========== TRÌNH DUYỆT UNDETECTED ==========
class TikTokBrowser:
    def __init__(self):
        self.browser = None

    def start_browser(self):
        options = uc.ChromeOptions()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--window-size=1920,1080')
        self.browser = uc.Chrome(options=options)

    def save_cookie(self, cookie_text: str) -> bool:
        cookie_text = cookie_text.strip()
        if not cookie_text:
            return False
        try:
            if cookie_text.startswith('['):
                json.loads(cookie_text)
                with open(COOKIE_FILE, 'w') as f:
                    f.write(cookie_text)
                return True
            elif cookie_text.startswith('# Netscape') or '.tiktok.com\tTRUE' in cookie_text[:200]:
                with open('cookies.txt', 'w') as f:
                    f.write(cookie_text)
                return True
            else:
                # Chuyển key=value sang JSON cơ bản
                cookies = []
                for pair in cookie_text.split(';'):
                    if '=' in pair:
                        name, value = pair.split('=', 1)
                        cookies.append({
                            'domain': '.tiktok.com',
                            'name': name.strip(),
                            'value': value.strip(),
                            'path': '/',
                            'secure': True,
                            'httpOnly': 'sessionid' in name or 'sid_' in name,
                            'sameSite': 'no_restriction'
                        })
                with open(COOKIE_FILE, 'w') as f:
                    json.dump(cookies, f)
                return True
        except Exception as e:
            logger.error(f"Lưu cookie lỗi: {e}")
            return False

    def load_cookies(self):
        cookie_file = COOKIE_FILE if os.path.exists(COOKIE_FILE) else 'cookies.txt'
        if not os.path.exists(cookie_file):
            raise FileNotFoundError("Chưa có cookie. Dùng /setcookie.")
        self.browser.get('https://www.tiktok.com')
        time.sleep(2)
        if cookie_file.endswith('.json'):
            with open(cookie_file, 'r') as f:
                cookies = json.load(f)
            for c in cookies:
                try:
                    self.browser.add_cookie(c)
                except:
                    pass
        else:
            # Xử lý file txt...
            pass
        self.browser.refresh()
        time.sleep(5)

    def is_logged_in(self):
        try:
            self.browser.find_element(By.CSS_SELECTOR, '[data-e2e="upload-icon"]')
            return True
        except:
            return False

    def send_message(self, username, video_url):
        if not username or not video_url:
            return False
        self.browser.get(f'https://www.tiktok.com/@{username}')
        time.sleep(3)
        try:
            input_box = WebDriverWait(self.browser, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '[contenteditable="true"]'))
            )
            input_box.clear()
            input_box.send_keys(video_url)
            time.sleep(1)
            input_box.send_keys('\ue007')
            time.sleep(2)
            return True
        except Exception as e:
            logger.error(f"Gửi thất bại @{username}: {e}")
            return False

    def close(self):
        if self.browser:
            self.browser.quit()

# ========== HANDLERS ==========
# (Giữ nguyên các hàm start, setcookie, text_handler, setvideo, add, remove, list, send, sendall, schedule, auto_send_job như code trước, chỉ thay thế import và class TikTokBrowser bằng undetected_chromedriver)

if __name__ == '__main__':
    # ... (main function)
    pass