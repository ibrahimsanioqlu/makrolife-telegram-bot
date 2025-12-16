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
LAST_SCAN_FILE = "/data/last_scan_time.json"

# Timeout (saniye) - 25 dakika
SCAN_TIMEOUT = 25 * 60

# Global progress tracking
progress_message_id = None
progress_chat_id = None

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
            return resp.json().get("result", {}).get("message_id")
        except Exception as e:
            print("[TELEGRAM] " + str(cid) + " - HATA: " + str(e), flush=True)
    return None


def edit_message(message_id, text, chat_id=None):
    chat_ids = [chat_id] if chat_id else CHAT_IDS
    
    for cid in chat_ids:
        if not cid:
            continue
        try:
            url = "https://api.telegram.org/bot" + BOT_TOKEN + "/editMessageText"
            resp = requests.post(
                url,
                data={
                    "chat_id": cid,
                    "message_id": message_id,
                    "text": text[:4000],
                    "disable_web_page_preview": True,
                    "parse_mode": "HTML"
                },
                timeout=10
            )
            resp.raise_for_status()
        except Exception as e:
            print("[TELEGRAM] Edit hatasi: " + str(e), flush=True)


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
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}"
        headers = {
            "Authorization": "token " + GITHUB_TOKEN,
            "Accept": "application/vnd.github+json",
            "User-Agent": "railway-makrolife-bot"
        }

        content_b64 = base64.b64encode(
            json.dumps(content, ensure_ascii=False, indent=2).encode()
        ).decode()

        data = {
            "message": "Update " + filename + " - " + get_turkey_time().strftime("%Y-%m-%d %H:%M"),
            "content": content_b64,
            "branch": "main"
        }

        if sha:
            data["sha"] = sha

        resp = requests.put(url, headers=headers, json=data, timeout=20)

        if resp.status_code in (200, 201):
            print(f"[GITHUB] {filename} kaydedildi", flush=True)
            return True
        elif resp.status_code == 422:
            print(f"[GITHUB] Dosya mevcut, sha aliniyor...", flush=True)
            _, existing_sha = github_get_file(filename)
            if existing_sha:
                data["sha"] = existing_sha
                resp = requests.put(url, headers=headers, json=data, timeout=20)
                if resp.status_code in (200, 201):
                    print(f"[GITHUB] {filename} kaydedildi (retry)", flush=True)
                    return True
            print(f"[GITHUB] Kayit hatasi: {resp.status_code} {resp.text}", flush=True)
            return False
        else:
            print(f"[GITHUB] Kayit hatasi: {resp.status_code} {resp.text}", flush=True)
            return False

    except Exception as e:
        print(f"[GITHUB] Kayit hatasi: {e}", flush=True)
        return False


def load_last_scan_time():
    """Son tarama zamanini yukle"""
    if os.path.exists(LAST_SCAN_FILE):
        try:
            with open(LAST_SCAN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("last_scan_time", 0)
        except:
            pass
    return 0


def save_last_scan_time(timestamp):
    """Son tarama zamanini kaydet"""
    try:
        with open(LAST_SCAN_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_scan_time": timestamp}, f)
    except Exception as e:
        print("[LAST_SCAN] Kayit hatasi: " + str(e), flush=True)


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
        "daily_stats": {},
        "scan_sequence": 0
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
    
    if GITHUB_TOKEN:
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
        msg += "/ozellikler - Bot ozellikleri\n"
        msg += "/yardim - Yardim"
        send_message(msg, chat_id)
    
    elif command == "/yardim" or command == "/help":
        msg = "<b>Makrolife Ilan Takip Botu</b>\n\n"
        msg += "<b>Kullanilabilir komutlar:</b>\n\n"
        msg += "<b>Istatistikler</b>\n"
        msg += "/durum - Bot durumu ve ozet bilgiler\n"
        msg += "/istatistik - Detayli istatistikler\n"
        msg += "/bellek - Bellekteki ilan sayisi\n"
        msg += "/bugun - Bugunku aktiviteler\n"
        msg += "/hafta - Son 7 gunluk ozet\n\n"
        msg += "<b>Arama</b>\n"
        msg += "/ara [kelime] - Ilan ara\n"
        msg += "/son [sayi] - Son eklenen ilanlar\n"
        msg += "/ucuz [sayi] - En ucuz ilanlar\n"
        msg += "/pahali [sayi] - En pahali ilanlar\n\n"
        msg += "<b>Yonetim</b>\n"
        msg += "/tara - Manuel tarama baslat"
        send_message(msg, chat_id)
    
    elif command == "/ozellikler" or command == "/features":
        msg = "<b>🤖 Bot Ozellikleri</b>\n\n"
        msg += "<b>📊 Tarama Sistemi:</b>\n"
        msg += "• Otomatik tarama (45-120 dk aralikla)\n"
        msg += "• 53+ sayfa tarama kapasitesi\n"
        msg += "• Akilli timeout yonetimi (25 dk)\n"
        msg += "• Container restart sonrasi sureyi hatirlar\n\n"
        msg += "<b>🔔 Bildirimler:</b>\n"
        msg += "• Yeni ilan (ilk 4 sayfa)\n"
        msg += "• Fiyat degisiklikleri\n"
        msg += "• Silinen ilanlar\n"
        msg += "• Gunluk ozet (23:30)\n\n"
        msg += "<b>💾 Veri Yonetimi:</b>\n"
        msg += "• Lokal + GitHub yedekleme\n"
        msg += "• 30 gunluk dongu sistemi\n"
        msg += "• Gecmis kayitlari (1000 kayit)\n"
        msg += "• Gunluk istatistikler\n\n"
        msg += "<b>🔍 Arama & Filtreleme:</b>\n"
        msg += "• Kelime bazli arama\n"
        msg += "• Fiyat siralama (ucuz/pahali)\n"
        msg += "• Tarih bazli listeleme\n"
        msg += "• Haftalik raporlar\n\n"
        msg += "<b>⚙️ Teknik:</b>\n"
        msg += "• Platform: Railway\n"
        msg += "• Scraping: Playwright\n"
        msg += "• API: Telegram Bot\n"
        msg += "• Yedek: GitHub API"
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
        daily = state.get("daily_stats", {}).get(today, {})
        
        all_items = [(k, v) for k, v in items.items()]
        all_items.sort(key=lambda x: x[1].get("position", 999999))
        
        msg = "<b>Bugun</b> (" + today + ")\n\n"
        msg += "Yeni: " + str(daily.get("new", 0)) + "\n"
        msg += "Fiyat degisimi: " + str(daily.get("price_changes", 0)) + "\n"
        msg += "Silinen: " + str(daily.get("deleted", 0)) + "\n"
        
        if all_items[:5]:
            msg += "\n<b>Son eklenenler:</b>\n"
            for kod, item in all_items[:5]:
                msg += kod + " - " + item.get("fiyat", "-") + "\n"
        
        send_message(msg, chat_id)
    
    elif command == "/hafta" or command == "/week":
        daily_stats = state.get("daily_stats", {})
        
        days_tr = {"Monday": "Pzt", "Tuesday": "Sal", "Wednesday": "Car", 
                   "Thursday": "Per", "Friday": "Cum", "Saturday": "Cmt", "Sunday": "Paz"}
        
        msg = "<b>Son 7 Gun</b>\n\n"
        for i in range(7):
            date = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            day = days_tr.get((now - timedelta(days=i)).strftime("%A"), "")
            
            stats = daily_stats.get(date, {})
            new_count = stats.get("new", 0)
            price_changes = stats.get("price_changes", 0)
            deleted = stats.get("deleted", 0)
            
            label = "Bugun" if i == 0 else day + " " + date[5:]
            msg += label + ": Yeni:" + str(new_count) + " Fiyat:" + str(price_changes) + " Silinen:" + str(deleted) + "\n"
        
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
                msg += "<b>" + kod + "</b>\n"
                msg += "🏷️ " + item.get("title", "")[:50] + "\n"
                msg += "💰 " + item.get("fiyat", "-") + "\n"
                msg += "🔗 " + item.get("link", "-") + "\n\n"
            if len(results) > 10:
                msg += "... +" + str(len(results)-10) + " sonuc daha"
        else:
            msg = "'" + keyword + "' bulunamadi."
        send_message(msg, chat_id)
    
    elif command.startswith("/son"):
        parts = message_text.split()
        count = min(int(parts[1]), 20) if len(parts) > 1 and parts[1].isdigit() else 5
        
        items = state.get("items", {})
        sorted_items = sorted(
            items.items(),
            key=lambda x: x[1].get("position", 999999)
        )[:count]
        
        msg = "<b>Son " + str(count) + " Eklenen İlan</b>\n\n"
        for kod, item in sorted_items:
            msg += "<b>" + kod + "</b> (" + item.get("tarih", "") + ")\n"
            msg += "  " + item.get("title", "")[:35] + "\n"
            msg += "  " + item.get("fiyat", "-") + "\n\n"
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
    global progress_message_id, progress_chat_id
    
    print("[PLAYWRIGHT] Baslatiliyor...", flush=True)
    
    # İlerleme mesajı gönder
    progress_msg = "🔍 <b>Tarama Başladı</b>\n\n"
    progress_msg += "⏳ Tarayıcı hazırlanıyor...\n"
    progress_msg += "📄 Sayfa: 0\n"
    progress_msg += "📊 İlan: 0"
    progress_message_id = send_message(progress_msg)
