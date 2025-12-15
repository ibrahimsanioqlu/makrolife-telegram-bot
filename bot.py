import os
import sys
import json
import time
import random
import base64
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

import requests
from playwright.sync_api import sync_playwright, TimeoutError

# Data klasoru
os.makedirs("/data", exist_ok=True)

print("=" * 60, flush=True)
print("BOT BASLATILIYOR...", flush=True)
print("Python version: " + sys.version, flush=True)
print("Calisma zamani: " + datetime.utcnow().isoformat(), flush=True)
print("=" * 60, flush=True)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_IDS = [os.getenv("CHAT_ID"), "7449598531"]
ADMIN_CHAT_IDS = [os.getenv("CHAT_ID"), "7449598531"]

# GitHub ayarlari (veri yedekleme icin)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO", "ibrahimsanioglu/makrolife-telegram-bot")

print("BOT_TOKEN mevcut: " + str(bool(BOT_TOKEN)), flush=True)
print("CHAT_ID mevcut: " + str(bool(os.getenv("CHAT_ID"))), flush=True)
print("GITHUB_TOKEN mevcut: " + str(bool(GITHUB_TOKEN)), flush=True)

URL = "https://www.makrolife.com.tr/tumilanlar"
DATA_FILE = "/data/ilanlar.json"
HISTORY_FILE = "/data/history.json"

# Timeout (saniye) - 25 dakika
SCAN_TIMEOUT = 25 * 60

def get_turkey_time():
    return datetime.utcnow() + timedelta(hours=3)

def get_scan_interval():
    hour = get_turkey_time().hour
    if 8 <= hour < 18:
        return 45 * 60
    else:
        return 120 * 60

# Istatistikler
bot_stats = {
    "start_time": None,
    "total_scans": 0,
    "total_new_listings": 0,
    "total_price_changes": 0,
    "total_deleted": 0,
    "last_scan_time": None,
    "last_scan_duration": 0,
    "last_scan_listings": 0,
    "last_scan_pages": 0,
    "errors": 0,
    "timeouts": 0
}

last_update_id = 0


def send_message(text, chat_id=None):
    chat_ids = [chat_id] if chat_id else CHAT_IDS
    
    for cid in chat_ids:
        if not cid:
            continue
        try:
            url = "https://api.telegram.org/bot" + BOT_TOKEN + "/sendMessage"
            resp = requests.post(
                url,
                data={
                    "chat_id": cid,
                    "text": text[:4000],
                    "disable_web_page_preview": True,
                    "parse_mode": "HTML"
                },
                timeout=10
            )
            resp.raise_for_status()
        except Exception as e:
            print("[TELEGRAM] " + str(cid) + " - HATA: " + str(e), flush=True)


def get_updates(offset=None):
    try:
        url = "https://api.telegram.org/bot" + BOT_TOKEN + "/getUpdates"
        params = {"timeout": 1, "limit": 10}
        if offset:
            params["offset"] = offset
        resp = requests.get(url, params=params, timeout=5)
        resp.raise_for_status()
        return resp.json().get("result", [])
    except:
        return []


def normalize_price(fiyat):
    return "".join(c for c in fiyat if c.isdigit())


def github_get_file(filename):
    if not GITHUB_TOKEN:
        return None, None
    
    try:
        url = "https://api.github.com/repos/" + GITHUB_REPO + "/contents/" + filename
        headers = {"Authorization": "token " + GITHUB_TOKEN}
        resp = requests.get(url, headers=headers, timeout=10)
        
        if resp.status_code == 200:
            data = resp.json()
            content = base64.b64decode(data["content"]).decode("utf-8")
            return json.loads(content), data["sha"]
        return None, None
    except Exception as e:
        print("[GITHUB] Okuma hatasi: " + str(e), flush=True)
        return None, None


def github_save_file(filename, content, sha=None):
    if not GITHUB_TOKEN:
        return False
    
    try:
        url = "https://api.github.com/repos/" + GITHUB_REPO + "/contents/" + filename
        headers = {"Authorization": "token " + GITHUB_TOKEN}
        
        content_b64 = base64.b64encode(json.dumps(content, ensure_ascii=False, indent=2).encode()).decode()
        
        data = {
            "message": "Update " + filename + " - " + get_turkey_time().strftime("%Y-%m-%d %H:%M"),
            "content": content_b64
        }
        if sha:
            data["sha"] = sha
        
        resp = requests.put(url, headers=headers, json=data, timeout=15)
        
        if resp.status_code in [200, 201]:
            print("[GITHUB] " + filename + " kaydedildi", flush=True)
            return True
        else:
            print("[GITHUB] Kayit hatasi: " + str(resp.status_code), flush=True)
            return False
    except Exception as e:
        print("[GITHUB] Kayit hatasi: " + str(e), flush=True)
        return False


def load_state():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                if state.get("items"):
                    print("[STATE] Lokal yuklendi - " + str(len(state.get("items", {}))) + " ilan", flush=True)
                    return state
        except Exception as e:
            print("[STATE] Lokal yukleme hatasi: " + str(e), flush=True)
    
    if GITHUB_TOKEN:
        print("[STATE] Lokal bulunamadi, GitHub dan cekiliyor...", flush=True)
        state, _ = github_get_file("ilanlar.json")
        if state and state.get("items"):
            print("[STATE] GitHub dan yuklendi - " + str(len(state.get("items", {}))) + " ilan", flush=True)
            save_state_local(state)
            return state
    
    print("[STATE] Yeni state olusturuluyor", flush=True)
    return {
        "cycle_start": get_turkey_time().strftime("%Y-%m-%d"),
        "items": {},
        "reported_days": [],
        "first_run_done": False,
        "daily_stats": {}
    }


def save_state_local(state):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[STATE] Lokal kayit hatasi: " + str(e), flush=True)


def save_state(state):
    save_state_local(state)
    print("[STATE] Lokal kaydedildi - " + str(len(state.get("items", {}))) + " ilan", flush=True)
    
    if GITHUB_TOKEN and bot_stats["total_scans"] % 3 == 0:
        _, sha = github_get_file("ilanlar.json")
        github_save_file("ilanlar.json", state, sha)


def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {"deleted": [], "price_changes": [], "new": []}


def save_history(history):
    try:
        for key in ["deleted", "price_changes", "new"]:
            if len(history.get(key, [])) > 1000:
                history[key] = history[key][-1000:]
        
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[HISTORY] Kayit hatasi: " + str(e), flush=True)


def format_duration(seconds):
    if seconds < 60:
        return str(int(seconds)) + " sn"
    elif seconds < 3600:
        return str(int(seconds // 60)) + " dk " + str(int(seconds % 60)) + " sn"
    else:
        return str(int(seconds // 3600)) + " sa " + str(int((seconds % 3600) // 60)) + " dk"


def format_number(num):
    return "{:,}".format(num).replace(",", ".")


def handle_command(chat_id, command, message_text):
    state = load_state()
    history = load_history()
    now = get_turkey_time()
    today = now.strftime("%Y-%m-%d")
    
    print("[KOMUT] " + str(chat_id) + ": " + command, flush=True)
    
    if command == "/start":
        interval = get_scan_interval() // 60
        msg = "<b>Makrolife Ilan Takip Botu</b>\n\n"
        msg += "Tarama araligi: " + str(interval) + " dk\n"
        msg += "Bellekteki ilan: " + str(len(state.get("items", {}))) + "\n\n"
        msg += "<b>Komutlar:</b>\n"
        msg += "/durum - Bot durumu\n"
        msg += "/istatistik - Detayli istatistikler\n"
        msg += "/bellek - Bellek durumu\n"
        msg += "/bugun - Bugunku aktiviteler\n"
        msg += "/hafta - Son 7 gun\n"
        msg += "/son [sayi] - Son ilanlar\n"
        msg += "/ara [kelime] - Ilan ara\n"
        msg += "/ucuz [sayi] - En ucuz ilanlar\n"
        msg += "/pahali [sayi] - En pahali ilanlar\n"
        msg += "/silinenler - Silinen ilanlar\n"
        msg += "/degisimler - Fiyat degisimleri\n"
        msg += "/tara - Manuel tarama\n"
        msg += "/yardim - Yardim"
        send_message(msg, chat_id)
    
    elif command == "/yardim" or command == "/help":
        msg = "<b>Yardim</b>\n\n"
        msg += "<b>Tarama Saatleri:</b>\n"
        msg += "08:00-18:00: Her 45 dk\n"
        msg += "18:00-08:00: Her 120 dk\n\n"
        msg += "<b>Bildirimler:</b>\n"
        msg += "Yeni ilan (ilk 4 sayfa)\n"
        msg += "Fiyat degisikligi\n"
        msg += "Silinen ilan\n\n"
        msg += "<b>Timeout:</b> 25 dakika"
        send_message(msg, chat_id)
    
    elif command == "/durum" or command == "/status":
        uptime = ""
        if bot_stats["start_time"]:
            uptime = format_duration((datetime.utcnow() - bot_stats["start_time"]).total_seconds())
        
        interval = get_scan_interval() // 60
        next_scan = "Bilinmiyor"
        if bot_stats["last_scan_time"]:
            next_time = bot_stats["last_scan_time"] + timedelta(seconds=get_scan_interval())
            remaining = (next_time - datetime.utcnow()).total_seconds()
            next_scan = format_duration(remaining) if remaining > 0 else "Simdi"
        
        msg = "<b>Bot Durumu</b>\n\n"
        msg += "Aktif | " + uptime + "\n"
        msg += now.strftime("%H:%M:%S") + " (TR)\n\n"
        msg += "Bellek: " + format_number(len(state.get("items", {}))) + " ilan\n"
        msg += "Tarama araligi: " + str(interval) + " dk\n"
        msg += "Sonraki: " + next_scan + "\n\n"
        msg += "Toplam tarama: " + str(bot_stats["total_scans"]) + "\n"
        msg += "Son tarama: " + str(bot_stats["last_scan_pages"]) + " sayfa\n"
        msg += "Son sure: " + format_duration(bot_stats["last_scan_duration"]) + "\n"
        msg += "Timeout: " + str(bot_stats["timeouts"]) + " | Hata: " + str(bot_stats["errors"])
        send_message(msg, chat_id)
    
    elif command == "/istatistik" or command == "/stats":
        items = state.get("items", {})
        prices = [int(normalize_price(v.get("fiyat", "0"))) for v in items.values() if normalize_price(v.get("fiyat", "0"))]
        
        avg_price = sum(prices) // len(prices) if prices else 0
        min_price = min(prices) if prices else 0
        max_price = max(prices) if prices else 0
        
        msg = "<b>Istatistikler</b>\n\n"
        msg += "Toplam: " + format_number(len(items)) + " ilan\n"
        msg += "Ortalama: " + format_number(avg_price) + " TL\n"
        msg += "En dusuk: " + format_number(min_price) + " TL\n"
        msg += "En yuksek: " + format_number(max_price) + " TL\n\n"
        msg += "Yeni bulunan: " + str(bot_stats["total_new_listings"]) + "\n"
        msg += "Fiyat degisimi: " + str(bot_stats["total_price_changes"]) + "\n"
        msg += "Silinen: " + str(bot_stats["total_deleted"])
        send_message(msg, chat_id)
    
    elif command == "/bellek" or command == "/memory":
        items = state.get("items", {})
        file_size = os.path.getsize(DATA_FILE) if os.path.exists(DATA_FILE) else 0
        
        github_status = "Aktif" if GITHUB_TOKEN else "Kapali"
        
        msg = "<b>Bellek</b>\n\n"
        msg += "Dosya: " + str(round(file_size/1024, 1)) + " KB\n"
        msg += "Ilan: " + format_number(len(items)) + "\n"
        msg += "Dongu: " + state.get("cycle_start", "-") + "\n"
        msg += "Ilk calisma: " + ("Evet" if state.get("first_run_done") else "Hayir") + "\n\n"
        msg += "GitHub yedek: " + github_status
        send_message(msg, chat_id)
    
    elif command == "/bugun" or command == "/today":
        items = state.get("items", {})
        today_items = [k for k, v in items.items() if v.get("tarih") == today]
        daily = state.get("daily_stats", {}).get(today, {})
        
        msg = "<b>Bugun</b> (" + today + ")\n\n"
        msg += "Yeni: " + str(len(today_items)) + "\n"
        msg += "Fiyat degisimi: " + str(daily.get("price_changes", 0)) + "\n"
        msg += "Silinen: " + str(daily.get("deleted", 0)) + "\n"
        
        if today_items[:5]:
            msg += "\n<b>Son eklenenler:</b>\n"
            for kod in today_items[:5]:
                item = items.get(kod, {})
                msg += kod + " - " + item.get("fiyat", "-") + "\n"
        
        send_message(msg, chat_id)
    
    elif command == "/hafta" or command == "/week":
        items = state.get("items", {})
        daily_stats = state.get("daily_stats", {})
        
        days_tr = {"Monday": "Pzt", "Tuesday": "Sal", "Wednesday": "Car", 
                   "Thursday": "Per", "Friday": "Cum", "Saturday": "Cmt", "Sunday": "Paz"}
        
        msg = "<b>Son 7 Gun</b>\n\n"
        for i in range(7):
            date = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            day = days_tr.get((now - timedelta(days=i)).strftime("%A"), "")
            count = sum(1 for v in items.values() if v.get("tarih") == date)
            stats = daily_stats.get(date, {})
            
            label = "Bugun" if i == 0 else day + " " + date[5:]
            msg += label + ": Yeni:" + str(count) + " Fiyat:" + str(stats.get("price_changes", 0)) + " Silinen:" + str(stats.get("deleted", 0)) + "\n"
        
        send_message(msg, chat_id)
    
    elif command.startswith("/silinenler"):
        deleted = history.get("deleted", [])[-10:]
        if deleted:
            msg = "<b>Son Silinen Ilanlar</b>\n\n"
            for item in reversed(deleted):
                msg += "<b>" + item.get("kod", "-") + "</b>\n"
                msg += "  " + item.get("title", "")[:40] + "\n"
                msg += "  " + item.get("fiyat", "-") + " | " + item.get("tarih", "-") + "\n\n"
        else:
            msg = "Henuz silinen ilan yok."
        send_message(msg, chat_id)
    
    elif command.startswith("/degisimler"):
        changes = history.get("price_changes", [])[-10:]
        if changes:
            msg = "<b>Son Fiyat Degisimleri</b>\n\n"
            for item in reversed(changes):
                msg += "<b>" + item.get("kod", "-") + "</b>\n"
                msg += "  " + item.get("eski_fiyat", "-") + " -> " + item.get("yeni_fiyat", "-") + "\n"
                msg += "  " + item.get("tarih", "-") + "\n\n"
        else:
            msg = "Henuz fiyat degisimi yok."
        send_message(msg, chat_id)
    
    elif command.startswith("/ara"):
        parts = message_text.split(None, 1)
        if len(parts) < 2:
            send_message("Kullanim: /ara kelime", chat_id)
            return None
        
        keyword = parts[1].lower()
        items = state.get("items", {})
        results = [(k, v) for k, v in items.items() 
                   if keyword in v.get("title", "").lower() or keyword in k.lower()]
        
        if results:
            msg = "<b>" + str(len(results)) + " sonuc</b> (" + keyword + ")\n\n"
            for kod, item in results[:10]:
                msg += "<b>" + kod + "</b>\n  " + item.get("title", "")[:40] + "\n  " + item.get("fiyat", "-") + "\n\n"
            if len(results) > 10:
                msg += "... +" + str(len(results)-10) + " sonuc"
        else:
            msg = "'" + keyword + "' bulunamadi."
        send_message(msg, chat_id)
    
    elif command.startswith("/son"):
        parts = message_text.split()
        count = min(int(parts[1]), 15) if len(parts) > 1 and parts[1].isdigit() else 5
        
        items = state.get("items", {})
        sorted_items = sorted(items.items(), key=lambda x: x[1].get("tarih", ""), reverse=True)[:count]
        
        msg = "<b>Son " + str(count) + " Ilan</b>\n\n"
        for kod, item in sorted_items:
            msg += "<b>" + kod + "</b> (" + item.get("tarih", "") + ")\n  " + item.get("title", "")[:35] + "\n  " + item.get("fiyat", "-") + "\n\n"
        send_message(msg, chat_id)
    
    elif command.startswith("/ucuz"):
        parts = message_text.split()
        count = min(int(parts[1]), 15) if len(parts) > 1 and parts[1].isdigit() else 10
        
        items = state.get("items", {})
        priced = [(k, v, int(normalize_price(v.get("fiyat", "0")))) 
                  for k, v in items.items() if normalize_price(v.get("fiyat", "0"))]
        sorted_items = sorted(priced, key=lambda x: x[2])[:count]
        
        msg = "<b>En Ucuz " + str(count) + "</b>\n\n"
        for kod, item, _ in sorted_items:
            msg += "<b>" + kod + "</b>\n  " + item.get("title", "")[:35] + "\n  " + item.get("fiyat", "-") + "\n\n"
        send_message(msg, chat_id)
    
    elif command.startswith("/pahali"):
        parts = message_text.split()
        count = min(int(parts[1]), 15) if len(parts) > 1 and parts[1].isdigit() else 10
        
        items = state.get("items", {})
        priced = [(k, v, int(normalize_price(v.get("fiyat", "0")))) 
                  for k, v in items.items() if normalize_price(v.get("fiyat", "0"))]
        sorted_items = sorted(priced, key=lambda x: x[2], reverse=True)[:count]
        
        msg = "<b>En Pahali " + str(count) + "</b>\n\n"
        for kod, item, _ in sorted_items:
            msg += "<b>" + kod + "</b>\n  " + item.get("title", "")[:35] + "\n  " + item.get("fiyat", "-") + "\n\n"
        send_message(msg, chat_id)
    
    elif command == "/tara" or command == "/scan":
        send_message("Manuel tarama baslatiliyor...", chat_id)
        return "SCAN"
    
    else:
        send_message("Bilinmeyen komut. /yardim yazin.", chat_id)
    
    return None


def check_telegram_commands():
    global last_update_id
    
    updates = get_updates(last_update_id + 1 if last_update_id else None)
    
    result = None
    for update in updates:
        last_update_id = update.get("update_id", last_update_id)
        
        message = update.get("message", {})
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = message.get("text", "")
        
        if not text or not chat_id:
            continue
        
        if chat_id not in ADMIN_CHAT_IDS:
            continue
        
        if text.startswith("/"):
            command = text.split()[0].lower()
            cmd_result = handle_command(chat_id, command, text)
            if cmd_result == "SCAN":
                result = "SCAN"
    
    return result


def fetch_listings_playwright():
    print("[PLAYWRIGHT] Baslatiliyor...", flush=True)
    
    results = []
    seen_codes = set()
    page_num = 0
    consecutive_failures = 0
    MAX_FAILURES = 3

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage"
            ]
        )

        def new_context():
            return browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )

        context = new_context()
        page = context.new_page()

        while True:
            page_num += 1
            if page_num == 1:
                page_url = URL
            else:
                page_url = URL + "?&page=" + str(page_num)
            print("[SAYFA " + str(page_num) + "] " + page_url, flush=True)

            success = False
            for attempt in range(3):
                try:
                    page.goto(page_url, timeout=60000, wait_until="networkidle")
                    page.wait_for_selector('a[href*="ilandetay?ilan_kodu="]', timeout=30000)
                    success = True
                    break
                except TimeoutError:
                    print("[SAYFA " + str(page_num) + "] Retry " + str(attempt+1) + "/3", flush=True)
                    page.wait_for_timeout(3000)
                except Exception as e:
                    print("[SAYFA " + str(page_num) + "] Hata: " + str(e), flush=True)
                    break

            if not success:
                consecutive_failures += 1
                if consecutive_failures >= MAX_FAILURES:
                    print("[PLAYWRIGHT] Ust uste hata - durduruluyor", flush=True)
                    break
                continue

            consecutive_failures = 0

            listings = page.evaluate("""() => {
                const out = [];
                const seen = new Set();

                document.querySelectorAll('a[href*="ilandetay?ilan_kodu="]').forEach(a => {
                    const href = a.getAttribute("href");
                    if (!href) return;

                    const m = href.match(/ilan_kodu=([A-Z0-9-]+)/i);
                    if (!m) return;

                    const kod = m[1];
                    if (seen.has(kod)) return;
                    seen.add(kod);

                    let fiyat = "Fiyat yok";
                    let title = "";

                    let el = a;
                    for (let i = 0; i < 8; i++) {
                        if (!el.parentElement) break;
                        el = el.parentElement;

                        const h3 = el.querySelector("h3");
                        const text = el.innerText || "";

                        if (h3 && text.includes("₺")) {
                            title = h3.innerText.trim();
                            for (const line of text.split("\\n")) {
                                if (/^[\\d.,]+\\s*₺$/.test(line.trim())) {
                                    fiyat = line.trim();
                                    break;
                                }
                            }
                            break;
                        }
                    }

                    out.push({
                        kod: kod,
                        fiyat: fiyat,
                        title: title,
                        link: "https://www.makrolife.com.tr/" + href
                    });
                });

                return out;
            }""")

            if not listings:
                print("[SAYFA " + str(page_num) + "] Bos - tarama bitti", flush=True)
                break

            for item in listings:
                if item["kod"] not in seen_codes:
                    seen_codes.add(item["kod"])
                    results.append((
                        item["kod"],
                        item["fiyat"],
                        item["link"],
                        item["title"],
                        page_num
                    ))

            print("[SAYFA " + str(page_num) + "] " + str(len(listings)) + " ilan | Toplam: " + str(len(results)), flush=True)

            if len(listings) < 12:
                print("[PLAYWRIGHT] Son sayfa", flush=True)
                break

            if page_num % 5 == 0:
                page.close()
                context.close()
                context = new_context()
                page = context.new_page()
                print("[PLAYWRIGHT] Context yenilendi", flush=True)

            page.wait_for_timeout(random.randint(2000, 4000))

        browser.close()

    bot_stats["last_scan_pages"] = page_num
    print("[PLAYWRIGHT] Tamamlandi: " + str(len(results)) + " ilan, " + str(page_num) + " sayfa", flush=True)
    return results


def run_scan_with_timeout():
    global bot_stats
    
    scan_start = time.time()
    now = get_turkey_time()
    today = now.strftime("%Y-%m-%d")
    
    print("\n[TARAMA] Basliyor - " + now.strftime("%Y-%m-%d %H:%M:%S"), flush=True)

    state = load_state()
    history = load_history()
    
    if "daily_stats" not in state:
        state["daily_stats"] = {}
    if today not in state["daily_stats"]:
        state["daily_stats"][today] = {"new": 0, "price_changes": 0, "deleted": 0}

    try:
        cycle_start = datetime.strptime(state["cycle_start"], "%Y-%m-%d")
        if (now - cycle_start).days >= 30:
            state = {
                "cycle_start": today, 
                "items": {}, 
                "reported_days": [], 
                "first_run_done": False, 
                "daily_stats": {today: {"new": 0, "price_changes": 0, "deleted": 0}}
            }
            print("[DONGU] 30 gun sifirlandi", flush=True)
    except:
        state["cycle_start"] = today

    try:
        listings = fetch_listings_playwright()
        print("[TARAMA] " + str(len(listings)) + " ilan bulundu", flush=True)
        bot_stats["last_scan_listings"] = len(listings)
    except Exception as e:
        print("[HATA] Playwright: " + str(e), flush=True)
        bot_stats["errors"] += 1
        save_state(state)
        return

    is_first_run = not state.get("first_run_done", False) or len(state["items"]) == 0

    if is_first_run:
        if len(listings) < 50:
            print("[UYARI] Yetersiz ilan: " + str(len(listings)), flush=True)
            save_state(state)
            return
        
        for kod, fiyat, link, title, page_num in listings:
            state["items"][kod] = {"fiyat": fiyat, "tarih": today, "link": link, "title": title}
        
        state["first_run_done"] = True
        
        msg = "<b>Ilk Tarama Tamamlandi</b>\n\n"
        msg += today + " " + now.strftime("%H:%M") + "\n"
        msg += "Toplam: " + str(len(listings)) + " ilan\n"
        msg += str(bot_stats["last_scan_pages"]) + " sayfa tarandi\n"
        msg += "Tumu kaydedildi"
        send_message(msg)
        print("[TARAMA] Ilk calisma: " + str(len(listings)) + " ilan", flush=True)
    else:
        new_count = 0
        price_change_count = 0
        current_codes = set()

        for kod, fiyat, link, title, page_num in listings:
            current_codes.add(kod)
            
            if kod not in state["items"]:
                state["items"][kod] = {"fiyat": fiyat, "tarih": today, "link": link, "title": title}
                new_count += 1
                
                history.setdefault("new", []).append({
                    "kod": kod, "fiyat": fiyat, "title": title, "tarih": today, "link": link
                })
                
                if page_num <= 4:
                    msg = "<b>YENI ILAN</b>\n\n"
                    msg += kod + "\n"
                    msg += title + "\n"
                    msg += fiyat + "\n"
                    msg += link
                    send_message(msg)
                    time.sleep(0.3)
            else:
                eski = state["items"][kod]["fiyat"]
                if normalize_price(eski) != normalize_price(fiyat):
                    history.setdefault("price_changes", []).append({
                        "kod": kod, "eski_fiyat": eski, "yeni_fiyat": fiyat, "tarih": today
                    })
                    
                    state["items"][kod]["fiyat"] = fiyat
                    price_change_count += 1
                    
                    eski_num = int(normalize_price(eski)) if normalize_price(eski) else 0
                    yeni_num = int(normalize_price(fiyat)) if normalize_price(fiyat) else 0
                    fark = yeni_num - eski_num
                    if fark > 0:
                        fark_str = "+" + format_number(fark)
                    else:
                        fark_str = format_number(fark)
                    
                    msg = "<b>FIYAT DEGISTI</b>\n\n"
                    msg += kod + "\n"
                    msg += eski + " -> " + fiyat + "\n"
                    msg += "Fark: " + fark_str + " TL\n"
                    msg += state["items"][kod].get("link", "")
                    send_message(msg)
                    time.sleep(0.3)

        deleted_count = 0
        for kod in list(state["items"].keys()):
            if kod not in current_codes:
                item = state["items"][kod]
                
                history.setdefault("deleted", []).append({
                    "kod": kod, "fiyat": item.get("fiyat", ""), 
                    "title": item.get("title", ""), "tarih": today
                })
                
                msg = "<b>ILAN SILINDI</b>\n\n"
                msg += kod + "\n"
                msg += item.get("title", "") + "\n"
                msg += item.get("fiyat", "")
                send_message(msg)
                
                del state["items"][kod]
                deleted_count += 1
                time.sleep(0.3)
        
        bot_stats["total_new_listings"] += new_count
        bot_stats["total_price_changes"] += price_change_count
        bot_stats["total_deleted"] += deleted_count
        
        state["daily_stats"][today]["new"] += new_count
        state["daily_stats"][today]["price_changes"] += price_change_count
        state["daily_stats"][today]["deleted"] += deleted_count
        
        print("[OZET] Yeni: " + str(new_count) + ", Fiyat: " + str(price_change_count) + ", Silinen: " + str(deleted_count), flush=True)

    if now.hour == 23 and now.minute >= 30 and today not in state.get("reported_days", []):
        todays = [k for k, v in state["items"].items() if v.get("tarih") == today]
        msg = "<b>Gunluk Ozet</b> (" + today + ")\n\n"
        msg += "Toplam: " + str(len(state["items"])) + " ilan\n"
        msg += "Bugun eklenen: " + str(len(todays)) + "\n"
        if todays:
            msg += "\n".join(todays[:40])
        else:
            msg += "Yeni ilan yok"
        if len(todays) > 40:
            msg += "\n..."
        send_message(msg)
        state.setdefault("reported_days", []).append(today)

    save_state(state)
    save_history(history)
    
    scan_duration = time.time() - scan_start
    bot_stats["total_scans"] += 1
    bot_stats["last_scan_time"] = datetime.utcnow()
    bot_stats["last_scan_duration"] = scan_duration
    
    print("[TARAMA] Tamamlandi (" + format_duration(scan_duration) + ")", flush=True)


def run_scan():
    global bot_stats
    
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run_scan_with_timeout)
        try:
            future.result(timeout=SCAN_TIMEOUT)
        except FuturesTimeoutError:
            print("[TIMEOUT] Tarama " + str(SCAN_TIMEOUT//60) + " dakikayi asti!", flush=True)
            bot_stats["timeouts"] += 1
            msg = "<b>TIMEOUT</b>\n\n"
            msg += "Tarama " + str(SCAN_TIMEOUT//60) + " dakikayi asti.\n"
            msg += "Sonraki tarama bekleniyor..."
            send_message(msg)
        except Exception as e:
            print("[HATA] Tarama hatasi: " + str(e), flush=True)
            bot_stats["errors"] += 1


def main():
    global bot_stats
    
    print("=" * 60, flush=True)
    print("ANA DONGU BASLIYOR", flush=True)
    print("=" * 60, flush=True)
    
    bot_stats["start_time"] = datetime.utcnow()
    
    state = load_state()
    item_count = len(state.get("items", {}))
    
    interval = get_scan_interval() // 60
    github_status = "Aktif" if GITHUB_TOKEN else "Kapali"
    msg = "<b>Bot Baslatildi!</b>\n\n"
    msg += "Tarama araligi: " + str(interval) + " dk\n"
    msg += "Bellekteki ilan: " + str(item_count) + "\n"
    msg += "GitHub yedek: " + github_status + "\n\n"
    msg += "/yardim - Komutlar"
    send_message(msg)
    
    last_scan_time = 0
    
    while True:
        try:
            cmd_result = check_telegram_commands()
            force_scan = (cmd_result == "SCAN")
            
            current_time = time.time()
            scan_interval = get_scan_interval()
            
            if force_scan or (current_time - last_scan_time >= scan_interval):
                print("\n" + "#" * 50, flush=True)
                scan_type = "(MANUEL)" if force_scan else ""
                print("# TARAMA #" + str(bot_stats["total_scans"] + 1) + " " + scan_type, flush=True)
                print("# " + get_turkey_time().strftime("%Y-%m-%d %H:%M:%S"), flush=True)
                print("#" * 50, flush=True)
                
                run_scan()
                last_scan_time = current_time
                
                next_interval = get_scan_interval() // 60
                print("[BEKLIYOR] Sonraki tarama " + str(next_interval) + " dk sonra", flush=True)
            
            time.sleep(1)
            
        except KeyboardInterrupt:
            print("\n[DURDURULDU]", flush=True)
            send_message("Bot durduruldu!")
            break
        except Exception as e:
            print("[KRITIK HATA] " + str(e), flush=True)
            bot_stats["errors"] += 1
            time.sleep(30)


if __name__ == "__main__":
    print("__main__ basliyor...", flush=True)
    main()
