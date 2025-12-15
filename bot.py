import os
import sys
import json
import time
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


def send_message(text: str):
    """Telegram'a mesaj gönder, hata durumunda logla."""
    print(f"[TELEGRAM] Mesaj gönderiliyor: {text[:100]}...", flush=True)
    success = True
    for chat_id in CHAT_IDS:
        if not chat_id:
            print(f"[TELEGRAM] Chat ID boş, atlanıyor", flush=True)
            continue
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            print(f"[TELEGRAM] {chat_id} adresine gönderiliyor...", flush=True)
            resp = requests.post(
                url,
                data={
                    "chat_id": chat_id,
                    "text": text[:4000],
                    "disable_web_page_preview": True
                },
                timeout=30
            )
            resp.raise_for_status()
            print(f"[TELEGRAM] {chat_id} - BAŞARILI", flush=True)
        except Exception as e:
            print(f"[TELEGRAM] {chat_id} - HATA: {e}", flush=True)
            success = False
    return success


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
        "first_run_done": False
    }


def save_state(state):
    """State dosyasını kaydet."""
    print(f"[STATE] Kaydediliyor - {len(state.get('items', {}))} ilan", flush=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    print("[STATE] Kaydedildi", flush=True)


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
            # URL formatı: ?&page=2 (sitenin kendi formatı)
            if page_num == 1:
                page_url = URL
            else:
                page_url = f"{URL}?&page={page_num}"

            try:
                print(f"[SAYFA {page_num}] Yükleniyor: {page_url}", flush=True)
                
                # Sayfa yükleme
                page.goto(page_url, timeout=45000, wait_until="domcontentloaded")
                print(f"[SAYFA {page_num}] DOM yüklendi", flush=True)
                
                # JavaScript içeriğin render edilmesi için bekle
                page.wait_for_timeout(6000)
                print(f"[SAYFA {page_num}] 6 saniye beklendi", flush=True)
                
                # İlan kartlarının yüklenmesini bekle
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

            # Debug: HTML'de kaç ilan var
            html_content = page.content()
            ilan_count_in_html = html_content.count("ilan_kodu=")
            print(f"[SAYFA {page_num}] HTML'de {ilan_count_in_html} ilan linki", flush=True)

            listings = page.evaluate('''() => {
                const results = [];
                const seen = new Set();
                
                // Tüm ilan kartlarını bul - Detayları Gör linklerinden
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
                    
                    // Kartı bul - link'in üst elementlerinde ara
                    let card = link;
                    for (let i = 0; i < 10; i++) {
                        if (!card.parentElement) break;
                        card = card.parentElement;
                        
                        // Kart içinde h3 ve fiyat var mı kontrol et
                        const h3 = card.querySelector('h3');
                        const text = card.innerText || "";
                        
                        if (h3 && text.includes('₺')) {
                            // Başlığı al
                            title = h3.innerText.trim();
                            
                            // Fiyatı bul - satırlarda ₺ içereni ara
                            const lines = text.split('\\n');
                            for (const line of lines) {
                                const trimmed = line.trim();
                                // Fiyat formatı: 1.070.000 ₺ veya 25.000 ₺
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

            # Sayfada ilan yoksa dur
            if not listings:
                print(f"[SAYFA {page_num}] Boş, tarama tamamlandı.", flush=True)
                break

            page_new_count = 0
            for item in listings:
                if item["kod"] not in seen_codes:
                    seen_codes.add(item["kod"])
                    # Sayfa numarasını da ekle
                    results.append((item["kod"], item["fiyat"], item["link"], item.get("title", ""), page_num))
                    page_new_count += 1
            
            print(f"[SAYFA {page_num}] {len(listings)} ilan, {page_new_count} yeni. Toplam: {len(results)}", flush=True)

            # Sayfada 12'den az ilan varsa son sayfaya ulaşılmış demektir
            if len(listings) < 12:
                print(f"[SAYFA {page_num}] Son sayfa ({len(listings)} ilan).", flush=True)
                break
            
            # Sonraki sayfa için bekleme
            page.wait_for_timeout(1000)

        print("[PLAYWRIGHT] Browser kapatılıyor...", flush=True)
        browser.close()
        print("[PLAYWRIGHT] Browser kapatıldı", flush=True)

    print(f"[PLAYWRIGHT] Toplam {len(results)} ilan bulundu", flush=True)
    return results


def run_scan():
    """Tek bir tarama döngüsü çalıştır."""
    now = get_turkey_time()
    today = now.strftime("%Y-%m-%d")
    
    print(f"\n[TARAMA] Başlıyor - {now.strftime('%Y-%m-%d %H:%M:%S')} (TR)", flush=True)

    state = load_state()

    # 30 günlük döngü kontrolü
    cycle_start = datetime.strptime(state["cycle_start"], "%Y-%m-%d")
    if (now - cycle_start).days >= 30:
        state = {"cycle_start": today, "items": {}, "reported_days": [], "first_run_done": False}
        print("[DÖNGÜ] 30 günlük döngü sıfırlandı.", flush=True)

    # İlanları çek
    try:
        listings = fetch_listings_playwright(max_pages=50)
        print(f"[TARAMA] Toplam {len(listings)} ilan bulundu.", flush=True)
    except Exception as e:
        print(f"[HATA] Playwright hatası: {e}", flush=True)
        save_state(state)
        return

    # İlk çalışma kontrolü - ilk veri toplama
    is_first_run = not state.get("first_run_done", False) or len(state["items"]) == 0
    print(f"[TARAMA] İlk çalışma: {is_first_run}", flush=True)

    if is_first_run:
        # Minimum ilan kontrolü - en az 100 ilan bekliyoruz
        if len(listings) < 100:
            print(f"[UYARI] İlk çalışmada yetersiz ilan: {len(listings)} (min 100)", flush=True)
            save_state(state)
            return
        
        # İLK VERİ TOPLAMA - tüm ilanları sessizce kaydet, tek mesaj gönder
        for kod, fiyat, link, title, page_num in listings:
            state["items"][kod] = {"fiyat": fiyat, "tarih": today, "link": link, "title": title}
        
        state["first_run_done"] = True
        
        # Tek özet mesaj gönder
        send_message(
            f"📅 {today}  🕐 {now.strftime('%H:%M')}\n"
            f"📊 Toplam ilan: {len(listings)}\n"
            f"✅ Tüm ilanlar kaydedildi"
        )
        print(f"[TARAMA] İlk veri toplama tamamlandı: {len(listings)} ilan", flush=True)
    else:
        # Normal çalışma - yeni ilan ve fiyat değişikliklerini kontrol et
        new_count = 0
        price_change_count = 0

        for kod, fiyat, link, title, page_num in listings:
            if kod not in state["items"]:
                # Yeni ilan - sadece ilk 4 sayfadakiler için bildirim gönder
                if page_num <= 4:
                    print(f"[YENİ İLAN] {kod} - Sayfa {page_num}", flush=True)
                    send_message(f"🆕 YENİ İLAN\n📅 {today}\n🏷️ {kod}\n📝 {title}\n💰 {fiyat}\n🔗 {link}")
                    time.sleep(0.5)  # Rate limit koruması
                else:
                    print(f"[YENİ İLAN] {kod} - Sayfa {page_num} (bildirim yok)", flush=True)
                
                # Tüm yeni ilanları kaydet (bildirim gönderilsin veya gönderilmesin)
                state["items"][kod] = {"fiyat": fiyat, "tarih": today, "link": link, "title": title}
                new_count += 1
            else:
                # Fiyat değişikliği kontrolü
                eski = state["items"][kod]["fiyat"]
                if normalize_price(eski) != normalize_price(fiyat):
                    print(f"[FİYAT DEĞİŞTİ] {kod}: {eski} -> {fiyat}", flush=True)
                    send_message(f"🔔 FİYAT DEĞİŞTİ\n🏷️ {kod}\n💰 Eski: {eski}\n💰 Yeni: {fiyat}\n🔗 {link}")
                    state["items"][kod]["fiyat"] = fiyat
                    price_change_count += 1
                    time.sleep(0.5)  # Rate limit koruması

        # Silinen ilanları kontrol et
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
                time.sleep(0.5)  # Rate limit koruması
        
        # Silinen ilanları state'den kaldır
        for kod in deleted_codes:
            del state["items"][kod]
        
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
    print("[TARAMA] Tamamlandı", flush=True)


def main():
    """Ana döngü - sürekli çalışır."""
    print("=" * 60, flush=True)
    print("ANA DÖNGÜ BAŞLIYOR", flush=True)
    print("=" * 60, flush=True)
    
    send_message("🚀 Bot başlatıldı! (Railway)")
    
    scan_count = 0
    while True:
        try:
            scan_count += 1
            print(f"\n{'#' * 60}", flush=True)
            print(f"# TARAMA #{scan_count}", flush=True)
            print(f"# Zaman: {get_turkey_time().strftime('%Y-%m-%d %H:%M:%S')} (TR)", flush=True)
            print(f"{'#' * 60}", flush=True)
            
            run_scan()
            
            print(f"\n[BEKLİYOR] Sonraki tarama {SCAN_INTERVAL // 60} dakika sonra...", flush=True)
            time.sleep(SCAN_INTERVAL)
            
        except KeyboardInterrupt:
            print("\n[DURDURULDU] Kullanıcı tarafından", flush=True)
            send_message("🛑 Bot durduruldu!")
            break
        except Exception as e:
            print(f"[KRİTİK HATA] {e}", flush=True)
            print("[BEKLİYOR] 60 saniye sonra tekrar deneniyor...", flush=True)
            time.sleep(60)


if __name__ == "__main__":
    print("__main__ bloğu çalıştırılıyor...", flush=True)
    main()
