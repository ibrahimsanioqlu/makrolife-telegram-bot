import os
import sys
import json
import time
import threading
from datetime import datetime, timedelta

import requests
from playwright.sync_api import sync_playwright

# Başlangıçta hemen log bas
print("=" * 60, flush=True)
print("BOT BAŞLATILIYOR...", flush=True)
print(f"Python version: {sys.version}", flush=True)
print(f"Çalışma zamanı: {datetime.utcnow().isoformat()}", flush=True)
print("=" * 60, flush=True)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_IDS = [os.getenv("CHAT_ID"), "7449598531"]
ADMIN_CHAT_IDS = [os.getenv("CHAT_ID"), "7449598531"]  # Komut kullanabilecek kişiler

print(f"BOT_TOKEN mevcut: {bool(BOT_TOKEN)}", flush=True)
print(f"CHAT_ID mevcut: {bool(os.getenv('CHAT_ID'))}", flush=True)
print(f"CHAT_IDS: {CHAT_IDS}", flush=True)

URL = "https://www.makrolife.com.tr/tumilanlar"
BASE = "https://www.makrolife.com.tr"
DATA_FILE = "ilanlar.json"

# Türkiye saati için UTC+3
def get_turkey_time():
    return datetime.utcnow() + timedelta(hours=3)

# Tarama aralığı (saniye) - 10 dakika
SCAN_INTERVAL = 10 * 60

# İstatistikler için global değişkenler
bot_stats = {
    "start_time": None,
    "total_scans": 0,
    "total_new_listings": 0,
    "total_price_changes": 0,
    "total_deleted": 0,
    "last_scan_time": None,
    "last_scan_duration": 0,
    "last_scan_listings": 0,
    "errors": 0
}

# Son mesaj ID'si (duplicate önlemek için)
last_update_id = 0


def send_message(text: str, chat_id=None):
    """Telegram'a mesaj gönder, hata durumunda logla."""
    if chat_id:
        # Tek kişiye gönder
        chat_ids = [chat_id]
    else:
        # Herkese gönder
        chat_ids = CHAT_IDS
    
    print(f"[TELEGRAM] Mesaj gönderiliyor: {text[:100]}...", flush=True)
    success = True
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
                timeout=30
            )
            resp.raise_for_status()
            print(f"[TELEGRAM] {cid} - BAŞARILI", flush=True)
        except Exception as e:
            print(f"[TELEGRAM] {cid} - HATA: {e}", flush=True)
            success = False
    return success


def get_updates(offset=None):
    """Telegram'dan gelen mesajları al."""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
        params = {"timeout": 5}
        if offset:
            params["offset"] = offset
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json().get("result", [])
    except Exception as e:
        print(f"[TELEGRAM] getUpdates hatası: {e}", flush=True)
        return []


def normalize_price(fiyat: str) -> str:
    """Fiyattan sadece rakamları çıkar (karşılaştırma için)."""
    return ''.join(c for c in fiyat if c.isdigit())


def load_state():
    """State dosyasını yükle, yoksa yeni oluştur."""
    print(f"[STATE] Dosya kontrol ediliyor: {DATA_FILE}", flush=True)
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                print(f"[STATE] Yüklendi - {len(state.get('items', {}))} ilan mevcut", flush=True)
                return state
        except json.JSONDecodeError as e:
            print(f"[STATE] Dosya bozuk: {e}", flush=True)
    print("[STATE] Yeni state oluşturuluyor", flush=True)
    return {
        "cycle_start": get_turkey_time().strftime("%Y-%m-%d"),
        "items": {},
        "reported_days": [],
        "first_run_done": False,
        "daily_stats": {}
    }


def save_state(state):
    """State dosyasını kaydet."""
    print(f"[STATE] Kaydediliyor - {len(state.get('items', {}))} ilan", flush=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    print("[STATE] Kaydedildi", flush=True)


def format_duration(seconds):
    """Saniyeyi okunabilir formata çevir."""
    if seconds < 60:
        return f"{int(seconds)} saniye"
    elif seconds < 3600:
        return f"{int(seconds // 60)} dakika {int(seconds % 60)} saniye"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours} saat {minutes} dakika"


def format_number(num):
    """Sayıyı binlik ayraçlı formata çevir."""
    return f"{num:,}".replace(",", ".")


def handle_command(chat_id, command, message_text):
    """Telegram komutlarını işle."""
    global bot_stats
    
    state = load_state()
    now = get_turkey_time()
    today = now.strftime("%Y-%m-%d")
    
    print(f"[KOMUT] {chat_id}: {command}", flush=True)
    
    if command == "/start":
        send_message(
            "🤖 <b>Makrolife İlan Takip Botu</b>\n\n"
            "Kullanılabilir komutlar:\n\n"
            "📊 <b>İstatistikler</b>\n"
            "/durum - Bot durumu ve özet bilgiler\n"
            "/istatistik - Detaylı istatistikler\n"
            "/bellek - Bellekteki ilan sayısı\n"
            "/bugun - Bugünkü aktiviteler\n"
            "/hafta - Son 7 günlük özet\n\n"
            "🔍 <b>Arama</b>\n"
            "/ara [kelime] - İlan ara\n"
            "/son [sayı] - Son eklenen ilanlar\n"
            "/ucuz [sayı] - En ucuz ilanlar\n"
            "/pahali [sayı] - En pahalı ilanlar\n\n"
            "⚙️ <b>Yönetim</b>\n"
            "/tara - Manuel tarama başlat\n"
            "/yardim - Yardım menüsü",
            chat_id
        )
    
    elif command == "/yardim" or command == "/help":
        send_message(
            "📖 <b>Yardım Menüsü</b>\n\n"
            "<b>Bot ne yapar?</b>\n"
            "• Her 10 dakikada makrolife.com.tr'yi tarar\n"
            "• Yeni ilanları bildirir (ilk 4 sayfa)\n"
            "• Fiyat değişikliklerini bildirir\n"
            "• Silinen ilanları bildirir\n"
            "• Günlük özet gönderir (23:30)\n\n"
            "<b>Komut örnekleri:</b>\n"
            "/ara daire - 'daire' içeren ilanları bul\n"
            "/son 5 - Son 5 ilanı göster\n"
            "/ucuz 10 - En ucuz 10 ilanı göster\n\n"
            "<b>Sorun bildirimi:</b>\n"
            "Bot ile ilgili sorunları yöneticiye bildirin.",
            chat_id
        )
    
    elif command == "/durum" or command == "/status":
        uptime = ""
        if bot_stats["start_time"]:
            uptime_seconds = (datetime.utcnow() - bot_stats["start_time"]).total_seconds()
            uptime = format_duration(uptime_seconds)
        
        last_scan = "Henüz tarama yapılmadı"
        if bot_stats["last_scan_time"]:
            last_scan = bot_stats["last_scan_time"].strftime("%H:%M:%S")
        
        next_scan = "Bilinmiyor"
        if bot_stats["last_scan_time"]:
            next_time = bot_stats["last_scan_time"] + timedelta(seconds=SCAN_INTERVAL)
            remaining = (next_time - datetime.utcnow()).total_seconds()
            if remaining > 0:
                next_scan = f"{int(remaining // 60)} dk {int(remaining % 60)} sn sonra"
            else:
                next_scan = "Şimdi"
        
        send_message(
            f"📊 <b>Bot Durumu</b>\n\n"
            f"🟢 Durum: Aktif\n"
            f"⏱ Çalışma süresi: {uptime}\n"
            f"🕐 Şu an: {now.strftime('%H:%M:%S')} (TR)\n\n"
            f"📋 <b>Bellek</b>\n"
            f"• Toplam ilan: {format_number(len(state.get('items', {})))}\n"
            f"• Döngü başlangıcı: {state.get('cycle_start', 'Bilinmiyor')}\n\n"
            f"🔄 <b>Tarama</b>\n"
            f"• Son tarama: {last_scan}\n"
            f"• Sonraki tarama: {next_scan}\n"
            f"• Tarama süresi: {format_duration(bot_stats['last_scan_duration'])}\n"
            f"• Toplam tarama: {bot_stats['total_scans']}",
            chat_id
        )
    
    elif command == "/istatistik" or command == "/stats":
        items = state.get("items", {})
        
        # Fiyat istatistikleri
        prices = []
        for item in items.values():
            price_str = normalize_price(item.get("fiyat", "0"))
            if price_str:
                prices.append(int(price_str))
        
        avg_price = sum(prices) // len(prices) if prices else 0
        min_price = min(prices) if prices else 0
        max_price = max(prices) if prices else 0
        
        # Tarih istatistikleri
        today_count = sum(1 for v in items.values() if v.get("tarih") == today)
        
        send_message(
            f"📈 <b>Detaylı İstatistikler</b>\n\n"
            f"📋 <b>İlan Sayıları</b>\n"
            f"• Toplam ilan: {format_number(len(items))}\n"
            f"• Bugün eklenen: {today_count}\n\n"
            f"💰 <b>Fiyat Analizi</b>\n"
            f"• Ortalama: {format_number(avg_price)} ₺\n"
            f"• En düşük: {format_number(min_price)} ₺\n"
            f"• En yüksek: {format_number(max_price)} ₺\n\n"
            f"🔄 <b>Bot İstatistikleri</b>\n"
            f"• Toplam tarama: {bot_stats['total_scans']}\n"
            f"• Bulunan yeni ilan: {bot_stats['total_new_listings']}\n"
            f"• Fiyat değişikliği: {bot_stats['total_price_changes']}\n"
            f"• Silinen ilan: {bot_stats['total_deleted']}\n"
            f"• Hata sayısı: {bot_stats['errors']}",
            chat_id
        )
    
    elif command == "/bellek" or command == "/memory":
        items = state.get("items", {})
        
        # Dosya boyutu
        file_size = 0
        if os.path.exists(DATA_FILE):
            file_size = os.path.getsize(DATA_FILE)
        
        file_size_kb = file_size / 1024
        file_size_mb = file_size / (1024 * 1024)
        
        send_message(
            f"💾 <b>Bellek Durumu</b>\n\n"
            f"📁 Dosya: {DATA_FILE}\n"
            f"📊 Boyut: {file_size_kb:.1f} KB ({file_size_mb:.2f} MB)\n"
            f"📋 Kayıtlı ilan: {format_number(len(items))}\n"
            f"📅 Döngü başlangıcı: {state.get('cycle_start', 'Bilinmiyor')}\n"
            f"🔄 İlk çalışma tamamlandı: {'Evet' if state.get('first_run_done') else 'Hayır'}",
            chat_id
        )
    
    elif command == "/bugun" or command == "/today":
        items = state.get("items", {})
        daily_stats = state.get("daily_stats", {}).get(today, {})
        
        today_items = [(k, v) for k, v in items.items() if v.get("tarih") == today]
        
        msg = f"📅 <b>Bugünkü Aktiviteler</b> ({today})\n\n"
        msg += f"🆕 Eklenen ilan: {len(today_items)}\n"
        msg += f"🔔 Fiyat değişikliği: {daily_stats.get('price_changes', 0)}\n"
        msg += f"🗑 Silinen ilan: {daily_stats.get('deleted', 0)}\n\n"
        
        if today_items:
            msg += "<b>Bugün eklenen son 10 ilan:</b>\n"
            for kod, item in today_items[:10]:
                msg += f"• {kod} - {item.get('fiyat', 'Fiyat yok')}\n"
            if len(today_items) > 10:
                msg += f"... ve {len(today_items) - 10} ilan daha"
        else:
            msg += "Bugün henüz yeni ilan eklenmedi."
        
        send_message(msg, chat_id)
    
    elif command == "/hafta" or command == "/week":
        items = state.get("items", {})
        daily_stats = state.get("daily_stats", {})
        
        msg = "📊 <b>Son 7 Günlük Özet</b>\n\n"
        
        for i in range(7):
            date = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            day_name = (now - timedelta(days=i)).strftime("%A")
            
            # Türkçe gün adları
            day_names_tr = {
                "Monday": "Pazartesi", "Tuesday": "Salı", "Wednesday": "Çarşamba",
                "Thursday": "Perşembe", "Friday": "Cuma", "Saturday": "Cumartesi", "Sunday": "Pazar"
            }
            day_name = day_names_tr.get(day_name, day_name)
            
            day_items = sum(1 for v in items.values() if v.get("tarih") == date)
            stats = daily_stats.get(date, {})
            
            if i == 0:
                msg += f"📅 <b>Bugün</b> ({date})\n"
            else:
                msg += f"📅 {day_name} ({date})\n"
            
            msg += f"   🆕 {day_items} ilan"
            if stats.get("price_changes"):
                msg += f" | 🔔 {stats['price_changes']} fiyat"
            if stats.get("deleted"):
                msg += f" | 🗑 {stats['deleted']} silindi"
            msg += "\n"
        
        send_message(msg, chat_id)
    
    elif command.startswith("/ara") or command.startswith("/search"):
        parts = message_text.split(maxsplit=1)
        if len(parts) < 2:
            send_message("❌ Kullanım: /ara [kelime]\nÖrnek: /ara daire", chat_id)
            return
        
        keyword = parts[1].lower()
        items = state.get("items", {})
        
        results = []
        for kod, item in items.items():
            title = item.get("title", "").lower()
            if keyword in title or keyword in kod.lower():
                results.append((kod, item))
        
        if results:
            msg = f"🔍 <b>Arama Sonuçları</b> ({len(results)} sonuç)\n"
            msg += f"Aranan: \"{keyword}\"\n\n"
            for kod, item in results[:15]:
                msg += f"• <b>{kod}</b>\n"
                msg += f"  {item.get('title', '')[:50]}\n"
                msg += f"  💰 {item.get('fiyat', 'Fiyat yok')}\n\n"
            if len(results) > 15:
                msg += f"... ve {len(results) - 15} sonuç daha"
        else:
            msg = f"🔍 \"{keyword}\" için sonuç bulunamadı."
        
        send_message(msg, chat_id)
    
    elif command.startswith("/son") or command.startswith("/recent"):
        parts = message_text.split()
        count = 5
        if len(parts) > 1 and parts[1].isdigit():
            count = min(int(parts[1]), 20)
        
        items = state.get("items", {})
        
        # Tarihe göre sırala
        sorted_items = sorted(items.items(), key=lambda x: x[1].get("tarih", ""), reverse=True)
        
        msg = f"🆕 <b>Son {count} İlan</b>\n\n"
        for kod, item in sorted_items[:count]:
            msg += f"• <b>{kod}</b> ({item.get('tarih', '')})\n"
            msg += f"  {item.get('title', '')[:40]}\n"
            msg += f"  💰 {item.get('fiyat', 'Fiyat yok')}\n\n"
        
        send_message(msg, chat_id)
    
    elif command.startswith("/ucuz") or command.startswith("/cheap"):
        parts = message_text.split()
        count = 10
        if len(parts) > 1 and parts[1].isdigit():
            count = min(int(parts[1]), 20)
        
        items = state.get("items", {})
        
        # Fiyata göre sırala
        priced_items = []
        for kod, item in items.items():
            price_str = normalize_price(item.get("fiyat", "0"))
            if price_str and int(price_str) > 0:
                priced_items.append((kod, item, int(price_str)))
        
        sorted_items = sorted(priced_items, key=lambda x: x[2])
        
        msg = f"💰 <b>En Ucuz {count} İlan</b>\n\n"
        for kod, item, price in sorted_items[:count]:
            msg += f"• <b>{kod}</b>\n"
            msg += f"  {item.get('title', '')[:40]}\n"
            msg += f"  💰 {item.get('fiyat', 'Fiyat yok')}\n\n"
        
        send_message(msg, chat_id)
    
    elif command.startswith("/pahali") or command.startswith("/expensive"):
        parts = message_text.split()
        count = 10
        if len(parts) > 1 and parts[1].isdigit():
            count = min(int(parts[1]), 20)
        
        items = state.get("items", {})
        
        # Fiyata göre sırala
        priced_items = []
        for kod, item in items.items():
            price_str = normalize_price(item.get("fiyat", "0"))
            if price_str and int(price_str) > 0:
                priced_items.append((kod, item, int(price_str)))
        
        sorted_items = sorted(priced_items, key=lambda x: x[2], reverse=True)
        
        msg = f"💎 <b>En Pahalı {count} İlan</b>\n\n"
        for kod, item, price in sorted_items[:count]:
            msg += f"• <b>{kod}</b>\n"
            msg += f"  {item.get('title', '')[:40]}\n"
            msg += f"  💰 {item.get('fiyat', 'Fiyat yok')}\n\n"
        
        send_message(msg, chat_id)
    
    elif command == "/tara" or command == "/scan":
        send_message("🔄 Manuel tarama başlatılıyor...", chat_id)
        # Bu komut ana döngüde işlenecek
        return "SCAN"
    
    else:
        send_message(
            "❓ Bilinmeyen komut.\n"
            "Kullanılabilir komutları görmek için /yardim yazın.",
            chat_id
        )
    
    return None


def check_telegram_commands():
    """Telegram'dan gelen komutları kontrol et."""
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
        
        # Sadece admin'ler komut kullanabilir
        if chat_id not in ADMIN_CHAT_IDS:
            print(f"[KOMUT] Yetkisiz kullanıcı: {chat_id}", flush=True)
            continue
        
        if text.startswith("/"):
            command = text.split()[0].lower()
            cmd_result = handle_command(chat_id, command, text)
            if cmd_result == "SCAN":
                result = "SCAN"
    
    return result


def fetch_listings_playwright(max_pages=50):
    """Playwright ile ilanları çek - sayfa başı 6+ saniye bekleme."""
    print(f"[PLAYWRIGHT] Başlatılıyor, max_pages={max_pages}", flush=True)
    results = []
    seen_codes = set()
    consecutive_failures = 0

    with sync_playwright() as p:
        print("[PLAYWRIGHT] sync_playwright başlatıldı", flush=True)
        
        print("[PLAYWRIGHT] Chromium başlatılıyor...", flush=True)
        browser = p.chromium.launch(headless=True)
        print("[PLAYWRIGHT] Chromium başlatıldı", flush=True)
        
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        print("[PLAYWRIGHT] Context oluşturuldu", flush=True)
        
        page = context.new_page()
        print("[PLAYWRIGHT] Sayfa oluşturuldu", flush=True)

        for page_num in range(1, max_pages + 1):
            if page_num == 1:
                page_url = URL
            else:
                page_url = f"{URL}?&page={page_num}"

            try:
                print(f"[SAYFA {page_num}] Yükleniyor: {page_url}", flush=True)
                
                page.goto(page_url, timeout=45000, wait_until="domcontentloaded")
                print(f"[SAYFA {page_num}] DOM yüklendi", flush=True)
                
                page.wait_for_timeout(6000)
                print(f"[SAYFA {page_num}] 6 saniye beklendi", flush=True)
                
                try:
                    page.wait_for_selector('a[href*="ilandetay?ilan_kodu="]', timeout=15000)
                    print(f"[SAYFA {page_num}] Selector bulundu", flush=True)
                except:
                    print(f"[SAYFA {page_num}] Selector timeout, devam ediliyor...", flush=True)
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        print("[HATA] 3 ardışık başarısız, durduruluyor.", flush=True)
                        break
                    continue
                
                consecutive_failures = 0
                
            except Exception as e:
                print(f"[SAYFA {page_num}] HATA: {e}", flush=True)
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    print("[HATA] 3 ardışık başarısız, durduruluyor.", flush=True)
                    break
                continue

            html_content = page.content()
            ilan_count_in_html = html_content.count("ilan_kodu=")
            print(f"[SAYFA {page_num}] HTML'de {ilan_count_in_html} ilan linki", flush=True)

            listings = page.evaluate('''() => {
                const results = [];
                const seen = new Set();
                
                const detayLinks = document.querySelectorAll('a[href*="ilandetay?ilan_kodu="]');
                
                detayLinks.forEach(link => {
                    const href = link.getAttribute("href");
                    if (!href) return;
                    
                    const match = href.match(/ilan_kodu=([A-Z0-9-]+)/i);
                    if (!match) return;
                    
                    const kod = match[1];
                    if (seen.has(kod)) return;
                    seen.add(kod);
                    
                    let fiyat = "Fiyat yok";
                    let title = "";
                    
                    let card = link;
                    for (let i = 0; i < 10; i++) {
                        if (!card.parentElement) break;
                        card = card.parentElement;
                        
                        const h3 = card.querySelector('h3');
                        const text = card.innerText || "";
                        
                        if (h3 && text.includes('₺')) {
                            title = h3.innerText.trim();
                            
                            const lines = text.split('\\n');
                            for (const line of lines) {
                                const trimmed = line.trim();
                                if (/^[\\d.,]+\\s*₺$/.test(trimmed)) {
                                    fiyat = trimmed;
                                    break;
                                }
                            }
                            break;
                        }
                    }
                    
                    results.push({
                        kod: kod,
                        fiyat: fiyat,
                        title: title,
                        link: "https://www.makrolife.com.tr/" + href
                    });
                });
                
                return results;
            }''')

            if not listings:
                print(f"[SAYFA {page_num}] Boş, tarama tamamlandı.", flush=True)
                break

            page_new_count = 0
            for item in listings:
                if item["kod"] not in seen_codes:
                    seen_codes.add(item["kod"])
                    results.append((item["kod"], item["fiyat"], item["link"], item.get("title", ""), page_num))
                    page_new_count += 1
            
            print(f"[SAYFA {page_num}] {len(listings)} ilan, {page_new_count} yeni. Toplam: {len(results)}", flush=True)

            if len(listings) < 12:
                print(f"[SAYFA {page_num}] Son sayfa ({len(listings)} ilan).", flush=True)
                break
            
            page.wait_for_timeout(1000)

        print("[PLAYWRIGHT] Browser kapatılıyor...", flush=True)
        browser.close()
        print("[PLAYWRIGHT] Browser kapatıldı", flush=True)

    print(f"[PLAYWRIGHT] Toplam {len(results)} ilan bulundu", flush=True)
    return results


def run_scan():
    """Tek bir tarama döngüsü çalıştır."""
    global bot_stats
    
    scan_start = time.time()
    now = get_turkey_time()
    today = now.strftime("%Y-%m-%d")
    
    print(f"\n[TARAMA] Başlıyor - {now.strftime('%Y-%m-%d %H:%M:%S')} (TR)", flush=True)

    state = load_state()
    
    # daily_stats yoksa oluştur
    if "daily_stats" not in state:
        state["daily_stats"] = {}
    if today not in state["daily_stats"]:
        state["daily_stats"][today] = {"new": 0, "price_changes": 0, "deleted": 0}

    # 30 günlük döngü kontrolü
    cycle_start = datetime.strptime(state["cycle_start"], "%Y-%m-%d")
    if (now - cycle_start).days >= 30:
        state = {"cycle_start": today, "items": {}, "reported_days": [], "first_run_done": False, "daily_stats": {}}
        print("[DÖNGÜ] 30 günlük döngü sıfırlandı.", flush=True)

    # İlanları çek
    try:
        listings = fetch_listings_playwright(max_pages=50)
        print(f"[TARAMA] Toplam {len(listings)} ilan bulundu.", flush=True)
        bot_stats["last_scan_listings"] = len(listings)
    except Exception as e:
        print(f"[HATA] Playwright hatası: {e}", flush=True)
        bot_stats["errors"] += 1
        save_state(state)
        return

    is_first_run = not state.get("first_run_done", False) or len(state["items"]) == 0
    print(f"[TARAMA] İlk çalışma: {is_first_run}", flush=True)

    if is_first_run:
        if len(listings) < 100:
            print(f"[UYARI] İlk çalışmada yetersiz ilan: {len(listings)} (min 100)", flush=True)
            save_state(state)
            return
        
        for kod, fiyat, link, title, page_num in listings:
            state["items"][kod] = {"fiyat": fiyat, "tarih": today, "link": link, "title": title}
        
        state["first_run_done"] = True
        
        send_message(
            f"📅 {today}  🕐 {now.strftime('%H:%M')}\n"
            f"📊 Toplam ilan: {len(listings)}\n"
            f"✅ Tüm ilanlar kaydedildi"
        )
        print(f"[TARAMA] İlk veri toplama tamamlandı: {len(listings)} ilan", flush=True)
    else:
        new_count = 0
        price_change_count = 0

        for kod, fiyat, link, title, page_num in listings:
            if kod not in state["items"]:
                if page_num <= 4:
                    print(f"[YENİ İLAN] {kod} - Sayfa {page_num}", flush=True)
                    send_message(f"🆕 YENİ İLAN\n📅 {today}\n🏷️ {kod}\n📝 {title}\n💰 {fiyat}\n🔗 {link}")
                    time.sleep(0.5)
                else:
                    print(f"[YENİ İLAN] {kod} - Sayfa {page_num} (bildirim yok)", flush=True)
                
                state["items"][kod] = {"fiyat": fiyat, "tarih": today, "link": link, "title": title}
                new_count += 1
            else:
                eski = state["items"][kod]["fiyat"]
                if normalize_price(eski) != normalize_price(fiyat):
                    print(f"[FİYAT DEĞİŞTİ] {kod}: {eski} -> {fiyat}", flush=True)
                    send_message(f"🔔 FİYAT DEĞİŞTİ\n🏷️ {kod}\n💰 Eski: {eski}\n💰 Yeni: {fiyat}\n🔗 {link}")
                    state["items"][kod]["fiyat"] = fiyat
                    price_change_count += 1
                    time.sleep(0.5)

        current_codes = set(kod for kod, fiyat, link, title, page_num in listings)
        deleted_count = 0
        deleted_codes = []
        
        for kod in list(state["items"].keys()):
            if kod not in current_codes:
                item = state["items"][kod]
                print(f"[SİLİNDİ] {kod}", flush=True)
                send_message(f"🗑️ İLAN SİLİNDİ\n📅 {today}\n🏷️ {kod}\n📝 {item.get('title', '')}\n💰 {item.get('fiyat', '')}\n🔗 {item.get('link', '')}")
                deleted_codes.append(kod)
                deleted_count += 1
                time.sleep(0.5)
        
        for kod in deleted_codes:
            del state["items"][kod]
        
        # İstatistikleri güncelle
        bot_stats["total_new_listings"] += new_count
        bot_stats["total_price_changes"] += price_change_count
        bot_stats["total_deleted"] += deleted_count
        
        state["daily_stats"][today]["new"] += new_count
        state["daily_stats"][today]["price_changes"] += price_change_count
        state["daily_stats"][today]["deleted"] += deleted_count
        
        print(f"[ÖZET] Yeni: {new_count}, Fiyat değişikliği: {price_change_count}, Silinen: {deleted_count}", flush=True)

    # Günlük özet (23:30-23:59 arası, günde bir kez)
    if (now.hour == 23 and now.minute >= 30) and (today not in state["reported_days"]):
        print("[GÜNLÜK ÖZET] Hazırlanıyor...", flush=True)
        todays = [k for k, v in state["items"].items() if v.get("tarih") == today]
        total = len(state["items"])
        send_message(
            f"📋 Günlük Özet ({today}):\n"
            f"📊 Toplam takip edilen: {total}\n"
            f"🆕 Bugün eklenen: {len(todays)}\n"
            + ("\n".join(todays[:40]) if todays else "Bugün yeni ilan yok.")
            + ("\n..." if len(todays) > 40 else "")
        )
        state["reported_days"].append(today)

    save_state(state)
    
    # Tarama istatistiklerini güncelle
    scan_duration = time.time() - scan_start
    bot_stats["total_scans"] += 1
    bot_stats["last_scan_time"] = datetime.utcnow()
    bot_stats["last_scan_duration"] = scan_duration
    
    print(f"[TARAMA] Tamamlandı ({format_duration(scan_duration)})", flush=True)


def main():
    """Ana döngü - sürekli çalışır."""
    global bot_stats
    
    print("=" * 60, flush=True)
    print("ANA DÖNGÜ BAŞLIYOR", flush=True)
    print("=" * 60, flush=True)
    
    bot_stats["start_time"] = datetime.utcnow()
    
    send_message("🚀 Bot başlatıldı! (Railway)\n\nKomutlar için /yardim yazın.")
    
    last_scan_time = 0
    
    while True:
        try:
            # Telegram komutlarını kontrol et (her 2 saniyede)
            cmd_result = check_telegram_commands()
            
            # Manuel tarama komutu geldi mi?
            force_scan = (cmd_result == "SCAN")
            
            # Zamanı geldi mi veya manuel tarama mı?
            current_time = time.time()
            if force_scan or (current_time - last_scan_time >= SCAN_INTERVAL):
                print(f"\n{'#' * 60}", flush=True)
                print(f"# TARAMA #{bot_stats['total_scans'] + 1}", flush=True)
                print(f"# Zaman: {get_turkey_time().strftime('%Y-%m-%d %H:%M:%S')} (TR)", flush=True)
                if force_scan:
                    print("# Tip: MANUEL TARAMA", flush=True)
                print(f"{'#' * 60}", flush=True)
                
                run_scan()
                last_scan_time = current_time
                
                print(f"\n[BEKLİYOR] Sonraki tarama {SCAN_INTERVAL // 60} dakika sonra...", flush=True)
            
            time.sleep(2)  # 2 saniye bekle
            
        except KeyboardInterrupt:
            print("\n[DURDURULDU] Kullanıcı tarafından", flush=True)
            send_message("🛑 Bot durduruldu!")
            break
        except Exception as e:
            print(f"[KRİTİK HATA] {e}", flush=True)
            bot_stats["errors"] += 1
            print("[BEKLİYOR] 60 saniye sonra tekrar deneniyor...", flush=True)
            time.sleep(60)


if __name__ == "__main__":
    print("__main__ bloğu çalıştırılıyor...", flush=True)
    main()
