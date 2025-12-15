import os
import sys
import json
import time
import signal
import random
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

import requests
from playwright.sync_api import sync_playwright, TimeoutError

# Başlangıçta hemen log bas
print("=" * 60, flush=True)
print("BOT BAŞLATILIYOR...", flush=True)
print(f"Python version: {sys.version}", flush=True)
print(f"Çalışma zamanı: {datetime.utcnow().isoformat()}", flush=True)
print("=" * 60, flush=True)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_IDS = [os.getenv("CHAT_ID"), "7449598531"]
ADMIN_CHAT_IDS = [os.getenv("CHAT_ID"), "7449598531"]

print(f"BOT_TOKEN mevcut: {bool(BOT_TOKEN)}", flush=True)
print(f"CHAT_ID mevcut: {bool(os.getenv('CHAT_ID'))}", flush=True)

URL = "https://www.makrolife.com.tr/tumilanlar"
DATA_FILE = "ilanlar.json"
HISTORY_FILE = "history.json"  # Silinen, değişen ilanların kaydı

# Timeout (saniye) - 25 dakika
SCAN_TIMEOUT = 25 * 60

# Türkiye saati için UTC+3
def get_turkey_time():
    return datetime.utcnow() + timedelta(hours=3)

def get_scan_interval():
    """Saate göre tarama aralığını belirle."""
    hour = get_turkey_time().hour
    if 8 <= hour < 18:
        return 45 * 60  # 08:00-17:59 arası 45 dakika
    else:
        return 120 * 60  # 18:00-07:59 arası 120 dakika

# İstatistikler
bot_stats = {
    "start_time": None,
    "total_scans": 0,
    "total_new_listings": 0,
    "total_price_changes": 0,
    "total_deleted": 0,
    "last_scan_time": None,
    "last_scan_duration": 0,
    "last_scan_listings": 0,
    "errors": 0,
    "timeouts": 0
}

last_update_id = 0


def send_message(text: str, chat_id=None):
    """Telegram'a mesaj gönder."""
    chat_ids = [chat_id] if chat_id else CHAT_IDS
    
    for cid in chat_ids:
        if not cid:
            continue
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
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
            print(f"[TELEGRAM] {cid} - HATA: {e}", flush=True)


def get_updates(offset=None):
    """Telegram'dan gelen mesajları al - hızlı."""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
        params = {"timeout": 1, "limit": 10}  # Hızlı polling
        if offset:
            params["offset"] = offset
        resp = requests.get(url, params=params, timeout=5)
        resp.raise_for_status()
        return resp.json().get("result", [])
    except:
        return []


def normalize_price(fiyat: str) -> str:
    """Fiyattan sadece rakamları çıkar."""
    return ''.join(c for c in fiyat if c.isdigit())


def load_state():
    """State dosyasını yükle."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                print(f"[STATE] Yüklendi - {len(state.get('items', {}))} ilan", flush=True)
                return state
        except Exception as e:
            print(f"[STATE] Yükleme hatası: {e}", flush=True)
    
    return {
        "cycle_start": get_turkey_time().strftime("%Y-%m-%d"),
        "items": {},
        "reported_days": [],
        "first_run_done": False,
        "daily_stats": {}
    }


def save_state(state):
    """State dosyasını kaydet."""
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        print(f"[STATE] Kaydedildi - {len(state.get('items', {}))} ilan", flush=True)
    except Exception as e:
        print(f"[STATE] Kaydetme hatası: {e}", flush=True)


def load_history():
    """Geçmiş dosyasını yükle."""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {"deleted": [], "price_changes": [], "new": []}


def save_history(history):
    """Geçmiş dosyasını kaydet."""
    try:
        # Son 1000 kaydı tut
        for key in ["deleted", "price_changes", "new"]:
            if len(history.get(key, [])) > 1000:
                history[key] = history[key][-1000:]
        
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[HISTORY] Kaydetme hatası: {e}", flush=True)


def format_duration(seconds):
    """Saniyeyi okunabilir formata çevir."""
    if seconds < 60:
        return f"{int(seconds)} sn"
    elif seconds < 3600:
        return f"{int(seconds // 60)} dk {int(seconds % 60)} sn"
    else:
        return f"{int(seconds // 3600)} sa {int((seconds % 3600) // 60)} dk"


def format_number(num):
    """Sayıyı binlik ayraçlı formata çevir."""
    return f"{num:,}".replace(",", ".")


def handle_command(chat_id, command, message_text):
    """Telegram komutlarını işle."""
    state = load_state()
    history = load_history()
    now = get_turkey_time()
    today = now.strftime("%Y-%m-%d")
    
    print(f"[KOMUT] {chat_id}: {command}", flush=True)
    
    if command == "/start":
        interval = get_scan_interval() // 60
        send_message(
            "🤖 <b>Makrolife İlan Takip Botu</b>\n\n"
            f"⏰ Şu anki tarama aralığı: {interval} dakika\n"
            f"📊 Bellekteki ilan: {len(state.get('items', {}))}\n\n"
            "<b>Komutlar:</b>\n"
            "/durum - Bot durumu\n"
            "/istatistik - Detaylı istatistikler\n"
            "/bellek - Bellek durumu\n"
            "/bugun - Bugünkü aktiviteler\n"
            "/hafta - Son 7 gün\n"
            "/son [sayı] - Son ilanlar\n"
            "/ara [kelime] - İlan ara\n"
            "/ucuz [sayı] - En ucuz ilanlar\n"
            "/pahali [sayı] - En pahalı ilanlar\n"
            "/silinenler - Son silinen ilanlar\n"
            "/degisimler - Son fiyat değişimleri\n"
            "/tara - Manuel tarama\n"
            "/yardim - Yardım",
            chat_id
        )
    
    elif command == "/yardim" or command == "/help":
        send_message(
            "📖 <b>Yardım</b>\n\n"
            "<b>Tarama Saatleri:</b>\n"
            "• 08:00-18:00: Her 45 dakikada\n"
            "• 18:00-08:00: Her 120 dakikada\n\n"
            "<b>Bildirimler:</b>\n"
            "🆕 Yeni ilan (ilk 4 sayfa)\n"
            "💰 Fiyat değişikliği\n"
            "🗑️ Silinen ilan\n\n"
            "<b>Timeout:</b> 25 dakika",
            chat_id
        )
    
    elif command == "/durum" or command == "/status":
        uptime = ""
        if bot_stats["start_time"]:
            uptime = format_duration((datetime.utcnow() - bot_stats["start_time"]).total_seconds())
        
        interval = get_scan_interval() // 60
        next_scan = "Bilinmiyor"
        if bot_stats["last_scan_time"]:
            next_time = bot_stats["last_scan_time"] + timedelta(seconds=get_scan_interval())
            remaining = (next_time - datetime.utcnow()).total_seconds()
            if remaining > 0:
                next_scan = format_duration(remaining)
            else:
                next_scan = "Şimdi"
        
        send_message(
            f"📊 <b>Bot Durumu</b>\n\n"
            f"🟢 Aktif | ⏱ {uptime}\n"
            f"🕐 {now.strftime('%H:%M:%S')} (TR)\n\n"
            f"📋 Bellek: {format_number(len(state.get('items', {})))} ilan\n"
            f"🔄 Tarama aralığı: {interval} dk\n"
            f"⏳ Sonraki: {next_scan}\n"
            f"📈 Toplam tarama: {bot_stats['total_scans']}\n"
            f"⚠️ Timeout: {bot_stats['timeouts']}",
            chat_id
        )
    
    elif command == "/istatistik" or command == "/stats":
        items = state.get("items", {})
        prices = [int(normalize_price(v.get("fiyat", "0"))) for v in items.values() if normalize_price(v.get("fiyat", "0"))]
        
        avg_price = sum(prices) // len(prices) if prices else 0
        min_price = min(prices) if prices else 0
        max_price = max(prices) if prices else 0
        
        send_message(
            f"📈 <b>İstatistikler</b>\n\n"
            f"📋 Toplam: {format_number(len(items))} ilan\n"
            f"💰 Ortalama: {format_number(avg_price)} ₺\n"
            f"📉 En düşük: {format_number(min_price)} ₺\n"
            f"📈 En yüksek: {format_number(max_price)} ₺\n\n"
            f"🆕 Yeni bulunan: {bot_stats['total_new_listings']}\n"
            f"💰 Fiyat değişimi: {bot_stats['total_price_changes']}\n"
            f"🗑️ Silinen: {bot_stats['total_deleted']}\n"
            f"❌ Hata: {bot_stats['errors']}",
            chat_id
        )
    
    elif command == "/bellek" or command == "/memory":
        items = state.get("items", {})
        file_size = os.path.getsize(DATA_FILE) if os.path.exists(DATA_FILE) else 0
        
        send_message(
            f"💾 <b>Bellek</b>\n\n"
            f"📁 {DATA_FILE}: {file_size/1024:.1f} KB\n"
            f"📋 İlan: {format_number(len(items))}\n"
            f"📅 Döngü: {state.get('cycle_start', '-')}\n"
            f"✅ İlk çalışma: {'Evet' if state.get('first_run_done') else 'Hayır'}",
            chat_id
        )
    
    elif command == "/bugun" or command == "/today":
        items = state.get("items", {})
        today_items = [k for k, v in items.items() if v.get("tarih") == today]
        daily = state.get("daily_stats", {}).get(today, {})
        
        msg = f"📅 <b>Bugün</b> ({today})\n\n"
        msg += f"🆕 Yeni: {len(today_items)}\n"
        msg += f"💰 Fiyat değişimi: {daily.get('price_changes', 0)}\n"
        msg += f"🗑️ Silinen: {daily.get('deleted', 0)}\n"
        
        if today_items[:5]:
            msg += "\n<b>Son eklenenler:</b>\n"
            for kod in today_items[:5]:
                item = items[kod]
                msg += f"• {kod} - {item.get('fiyat', '-')}\n"
        
        send_message(msg, chat_id)
    
    elif command == "/hafta" or command == "/week":
        items = state.get("items", {})
        daily_stats = state.get("daily_stats", {})
        
        days_tr = {"Monday": "Pzt", "Tuesday": "Sal", "Wednesday": "Çar", "Thursday": "Per", "Friday": "Cum", "Saturday": "Cmt", "Sunday": "Paz"}
        
        msg = "📊 <b>Son 7 Gün</b>\n\n"
        for i in range(7):
            date = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            day = days_tr.get((now - timedelta(days=i)).strftime("%A"), "")
            count = sum(1 for v in items.values() if v.get("tarih") == date)
            stats = daily_stats.get(date, {})
            
            label = "Bugün" if i == 0 else f"{day} {date[5:]}"
            msg += f"📅 {label}: 🆕{count} 💰{stats.get('price_changes', 0)} 🗑️{stats.get('deleted', 0)}\n"
        
        send_message(msg, chat_id)
    
    elif command.startswith("/silinenler"):
        deleted = history.get("deleted", [])[-10:]
        if deleted:
            msg = "🗑️ <b>Son Silinen İlanlar</b>\n\n"
            for item in reversed(deleted):
                msg += f"• <b>{item.get('kod', '-')}</b>\n"
                msg += f"  {item.get('title', '')[:40]}\n"
                msg += f"  💰 {item.get('fiyat', '-')} | 📅 {item.get('tarih', '-')}\n\n"
        else:
            msg = "🗑️ Henüz silinen ilan yok."
        send_message(msg, chat_id)
    
    elif command.startswith("/degisimler"):
        changes = history.get("price_changes", [])[-10:]
        if changes:
            msg = "💰 <b>Son Fiyat Değişimleri</b>\n\n"
            for item in reversed(changes):
                msg += f"• <b>{item.get('kod', '-')}</b>\n"
                msg += f"  {item.get('eski_fiyat', '-')} ➜ {item.get('yeni_fiyat', '-')}\n"
                msg += f"  📅 {item.get('tarih', '-')}\n\n"
        else:
            msg = "💰 Henüz fiyat değişimi yok."
        send_message(msg, chat_id)
    
    elif command.startswith("/ara"):
        parts = message_text.split(maxsplit=1)
        if len(parts) < 2:
            send_message("❌ Kullanım: /ara kelime", chat_id)
            return
        
        keyword = parts[1].lower()
        items = state.get("items", {})
        results = [(k, v) for k, v in items.items() if keyword in v.get("title", "").lower() or keyword in k.lower()]
        
        if results:
            msg = f"🔍 <b>{len(results)} sonuç</b> ({keyword})\n\n"
            for kod, item in results[:10]:
                msg += f"• <b>{kod}</b>\n  {item.get('title', '')[:40]}\n  💰 {item.get('fiyat', '-')}\n\n"
            if len(results) > 10:
                msg += f"... +{len(results)-10} sonuç"
        else:
            msg = f"🔍 '{keyword}' bulunamadı."
        send_message(msg, chat_id)
    
    elif command.startswith("/son"):
        parts = message_text.split()
        count = min(int(parts[1]), 15) if len(parts) > 1 and parts[1].isdigit() else 5
        
        items = state.get("items", {})
        sorted_items = sorted(items.items(), key=lambda x: x[1].get("tarih", ""), reverse=True)[:count]
        
        msg = f"🆕 <b>Son {count} İlan</b>\n\n"
        for kod, item in sorted_items:
            msg += f"• <b>{kod}</b> ({item.get('tarih', '')})\n  {item.get('title', '')[:35]}\n  💰 {item.get('fiyat', '-')}\n\n"
        send_message(msg, chat_id)
    
    elif command.startswith("/ucuz"):
        parts = message_text.split()
        count = min(int(parts[1]), 15) if len(parts) > 1 and parts[1].isdigit() else 10
        
        items = state.get("items", {})
        priced = [(k, v, int(normalize_price(v.get("fiyat", "0")))) for k, v in items.items() if normalize_price(v.get("fiyat", "0"))]
        sorted_items = sorted(priced, key=lambda x: x[2])[:count]
        
        msg = f"💰 <b>En Ucuz {count}</b>\n\n"
        for kod, item, _ in sorted_items:
            msg += f"• <b>{kod}</b>\n  {item.get('title', '')[:35]}\n  💰 {item.get('fiyat', '-')}\n\n"
        send_message(msg, chat_id)
    
    elif command.startswith("/pahali"):
        parts = message_text.split()
        count = min(int(parts[1]), 15) if len(parts) > 1 and parts[1].isdigit() else 10
        
        items = state.get("items", {})
        priced = [(k, v, int(normalize_price(v.get("fiyat", "0")))) for k, v in items.items() if normalize_price(v.get("fiyat", "0"))]
        sorted_items = sorted(priced, key=lambda x: x[2], reverse=True)[:count]
        
        msg = f"💎 <b>En Pahalı {count}</b>\n\n"
        for kod, item, _ in sorted_items:
            msg += f"• <b>{kod}</b>\n  {item.get('title', '')[:35]}\n  💰 {item.get('fiyat', '-')}\n\n"
        send_message(msg, chat_id)
    
    elif command == "/tara" or command == "/scan":
        send_message("🔄 Manuel tarama başlatılıyor...", chat_id)
        return "SCAN"
    
    else:
        send_message("❓ Bilinmeyen komut. /yardim yazın.", chat_id)
    
    return None


def check_telegram_commands():
    """Telegram komutlarını kontrol et - hızlı."""
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
    """
    BOT ALGILAMAYA DAYANIKLI – TIMEOUT ATMAYAN – KARARLI SÜRÜM
    """
    print("[PLAYWRIGHT] Başlatıldı (STABLE MODE)", flush=True)

    results = []
    seen_codes =s = set()

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
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )

        context = new_context()
        page = context.new_page()

        while True:
            page_num += 1
            page_url = URL if page_num == 1 else f"{URL}?page={page_num}"
            print(f"[SAYFA {page_num}] {page_url}", flush=True)

            success = False
            for attempt in range(3):
                try:
                    page.goto(
                        page_url,
                        timeout=60000,
                        wait_until="networkidle"
                    )

                    page.wait_for_selector(
                        'a[href*="ilandetay?ilan_kodu="]',
                        timeout=60000
                    )

                    success = True
                    break

                except TimeoutError:
                    print(f"[SAYFA {page_num}] retry {attempt+1}/3", flush=True)
                    page.wait_for_timeout(3000)

            if not success:
                consecutive_failures += 1
                if consecutive_failures >= MAX_FAILURES:
                    print("[PLAYWRIGHT] Üst üste hata – tarama durduruldu", flush=True)
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
                        kod,
                        fiyat,
                        title,
                        link: "https://www.makrolife.com.tr/" + href
                    });
                });

                return out;
            }""")

            if not listings:
                print(f"[SAYFA {page_num}] Boş – bitiş", flush=True)
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

            print(
                f"[SAYFA {page_num}] {len(listings)} ilan | toplam {len(results)}",
                flush=True
            )

            if len(listings) < 12:
                print("[PLAYWRIGHT] Son sayfa", flush=True)
                break

            if page_num % 5 == 0:
                page.close()
                context.close()
                context = new_context()
                page = context.new_page()
                print("[PLAYWRIGHT] Context reset", flush=True)

            page.wait_for_timeout(random.randint(2500, 4500))

        browser.close()

    print(f"[PLAYWRIGHT] Bitti → {len(results)} ilan", flush=True)
    return results


def run_scan_with_timeout():
    """Timeout ile tarama çalıştır."""
    global bot_stats
    
    scan_start = time.time()
    now = get_turkey_time()
    today = now.strftime("%Y-%m-%d")
    
    print(f"\n[TARAMA] Başlıyor - {now.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)

    state = load_state()
    history = load_history()
    
    if "daily_stats" not in state:
        state["daily_stats"] = {}
    if today not in state["daily_stats"]:
        state["daily_stats"][today] = {"new": 0, "price_changes": 0, "deleted": 0}

    # 30 günlük döngü kontrolü
    try:
        cycle_start = datetime.strptime(state["cycle_start"], "%Y-%m-%d")
        if (now - cycle_start).days >= 30:
            state = {"cycle_start": today, "items": {}, "reported_days": [], "first_run_done": False, "daily_stats": {today: {"new": 0, "price_changes": 0, "deleted": 0}}}
            print("[DÖNGÜ] 30 gün sıfırlandı", flush=True)
    except:
        state["cycle_start"] = today

    # İlanları çek
    try:
        listings = fetch_listings_playwright()
        print(f"[TARAMA] {len(listings)} ilan bulundu", flush=True)
        bot_stats["last_scan_listings"] = len(listings)
    except Exception as e:
        print(f"[HATA] Playwright: {e}", flush=True)
        bot_stats["errors"] += 1
        save_state(state)
        return

    is_first_run = not state.get("first_run_done", False) or len(state["items"]) == 0

    if is_first_run:
        if len(listings) < 50:
            print(f"[UYARI] Yetersiz ilan: {len(listings)}", flush=True)
            save_state(state)
            return
        
        for kod, fiyat, link, title, page_num in listings:
            state["items"][kod] = {"fiyat": fiyat, "tarih": today, "link": link, "title": title}
        
        state["first_run_done"] = True
        
        send_message(
            f"✅ <b>İlk Tarama Tamamlandı</b>\n\n"
            f"📅 {today} {now.strftime('%H:%M')}\n"
            f"📊 Toplam: {len(listings)} ilan\n"
            f"💾 Tümü kaydedildi"
        )
        print(f"[TARAMA] İlk çalışma: {len(listings)} ilan", flush=True)
    else:
        new_count = 0
        price_change_count = 0
        current_codes = set()

        for kod, fiyat, link, title, page_num in listings:
            current_codes.add(kod)
            
            if kod not in state["items"]:
                # YENİ İLAN
                state["items"][kod] = {"fiyat": fiyat, "tarih": today, "link": link, "title": title}
                new_count += 1
                
                # History'e ekle
                history.setdefault("new", []).append({
                    "kod": kod, "fiyat": fiyat, "title": title, "tarih": today, "link": link
                })
                
                if page_num <= 4:
                    send_message(
                        f"🆕 <b>YENİ İLAN</b>\n\n"
                        f"🏷️ {kod}\n"
                        f"📝 {title}\n"
                        f"💰 {fiyat}\n"
                        f"🔗 {link}"
                    )
                    time.sleep(0.3)
            else:
                # FİYAT DEĞİŞİKLİĞİ
                eski = state["items"][kod]["fiyat"]
                if normalize_price(eski) != normalize_price(fiyat):
                    # History'e ekle
                    history.setdefault("price_changes", []).append({
                        "kod": kod, "eski_fiyat": eski, "yeni_fiyat": fiyat, "tarih": today
                    })
                    
                    state["items"][kod]["fiyat"] = fiyat
                    price_change_count += 1
                    
                    # Fiyat farkını hesapla
                    eski_num = int(normalize_price(eski)) if normalize_price(eski) else 0
                    yeni_num = int(normalize_price(fiyat)) if normalize_price(fiyat) else 0
                    fark = yeni_num - eski_num
                    fark_str = f"+{format_number(fark)}" if fark > 0 else format_number(fark)
                    
                    send_message(
                        f"💰 <b>FİYAT DEĞİŞTİ</b>\n\n"
                        f"🏷️ {kod}\n"
                        f"📉 {eski} ➜ {fiyat}\n"
                        f"📊 Fark: {fark_str} ₺\n"
                        f"🔗 {state['items'][kod].get('link', '')}"
                    )
                    time.sleep(0.3)

        # SİLİNEN İLANLAR
        deleted_count = 0
        for kod in list(state["items"].keys()):
            if kod not in current_codes:
                item = state["items"][kod]
                
                # History'e ekle
                history.setdefault("deleted", []).append({
                    "kod": kod, "fiyat": item.get("fiyat", ""), "title": item.get("title", ""), "tarih": today
                })
                
                send_message(
                    f"🗑️ <b>İLAN SİLİNDİ</b>\n\n"
                    f"🏷️ {kod}\n"
                    f"📝 {item.get('title', '')}\n"
                    f"💰 {item.get('fiyat', '')}"
                )
                
                del state["items"][kod]
                deleted_count += 1
                time.sleep(0.3)
        
        # İstatistikleri güncelle
        bot_stats["total_new_listings"] += new_count
        bot_stats["total_price_changes"] += price_change_count
        bot_stats["total_deleted"] += deleted_count
        
        state["daily_stats"][today]["new"] += new_count
        state["daily_stats"][today]["price_changes"] += price_change_count
        state["daily_stats"][today]["deleted"] += deleted_count
        
        print(f"[ÖZET] Yeni: {new_count}, Fiyat: {price_change_count}, Silinen: {deleted_count}", flush=True)

    # Günlük özet (23:30-23:59)
    if now.hour == 23 and now.minute >= 30 and today not in state.get("reported_days", []):
        todays = [k for k, v in state["items"].items() if v.get("tarih") == today]
        send_message(
            f"📋 <b>Günlük Özet</b> ({today})\n\n"
            f"📊 Toplam: {len(state['items'])} ilan\n"
            f"🆕 Bugün eklenen: {len(todays)}\n"
            + ("\n".join(todays[:40]) if todays else "Yeni ilan yok")
            + ("\n..." if len(todays) > 40 else "")
        )
        state.setdefault("reported_days", []).append(today)

    save_state(state)
    save_history(history)
    
    scan_duration = time.time() - scan_start
    bot_stats["total_scans"] += 1
    bot_stats["last_scan_time"] = datetime.utcnow()
    bot_stats["last_scan_duration"] = scan_duration
    
    print(f"[TARAMA] Tamamlandı ({format_duration(scan_duration)})", flush=True)


def run_scan():
    """Taramayı timeout ile çalıştır."""
    global bot_stats
    
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run_scan_with_timeout)
        try:
            future.result(timeout=SCAN_TIMEOUT)
        except FuturesTimeoutError:
            print(f"[TIMEOUT] Tarama {SCAN_TIMEOUT//60} dakikayı aştı!", flush=True)
            bot_stats["timeouts"] += 1
            send_message(
                f"⚠️ <b>TIMEOUT</b>\n\n"
                f"Tarama {SCAN_TIMEOUT//60} dakikayı aştı.\n"
                f"Sonraki tarama bekleniyor..."
            )
        except Exception as e:
            print(f"[HATA] Tarama hatası: {e}", flush=True)
            bot_stats["errors"] += 1


def main():
    """Ana döngü."""
    global bot_stats
    
    print("=" * 60, flush=True)
    print("ANA DÖNGÜ BAŞLIYOR", flush=True)
    print("=" * 60, flush=True)
    
    bot_stats["start_time"] = datetime.utcnow()
    
    interval = get_scan_interval() // 60
    send_message(f"🚀 <b>Bot Başlatıldı!</b>\n\n⏰ Tarama aralığı: {interval} dk\n\n/yardim - Komutlar")
    
    last_scan_time = 0
    
    while True:
        try:
            # Komutları kontrol et (hızlı)
            cmd_result = check_telegram_commands()
            force_scan = (cmd_result == "SCAN")
            
            # Tarama zamanı?
            current_time = time.time()
            scan_interval = get_scan_interval()
            
            if force_scan or (current_time - last_scan_time >= scan_interval):
                print(f"\n{'#' * 50}", flush=True)
                print(f"# TARAMA #{bot_stats['total_scans'] + 1} {'(MANUEL)' if force_scan else ''}", flush=True)
                print(f"# {get_turkey_time().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
                print(f"{'#' * 50}", flush=True)
                
                run_scan()
                last_scan_time = current_time
                
                next_interval = get_scan_interval() // 60
                print(f"[BEKLİYOR] Sonraki tarama {next_interval} dk sonra", flush=True)
            
            time.sleep(1)  # 1 saniye - hızlı komut yanıtı
            
        except KeyboardInterrupt:
            print("\n[DURDURULDU]", flush=True)
            send_message("🛑 Bot durduruldu!")
            break
        except Exception as e:
            print(f"[KRİTİK HATA] {e}", flush=True)
            bot_stats["errors"] += 1
            time.sleep(30)


if __name__ == "__main__":
    print("__main__ başlıyor...", flush=True)
    main()
