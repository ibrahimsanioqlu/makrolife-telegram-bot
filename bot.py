import os
import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_IDS = [os.getenv("CHAT_ID"), "7449598531"]

URL = "https://www.makrolife.com.tr/tumilanlar"
BASE = "https://www.makrolife.com.tr"
DATA_FILE = "ilanlar.json"

TR_TZ = ZoneInfo("Europe/Istanbul")


def send_message(text: str):
    """Telegram'a mesaj gönder, hata durumunda logla."""
    success = True
    for chat_id in CHAT_IDS:
        if not chat_id:
            continue
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
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
        except Exception as e:
            print(f"Telegram mesaj hatası ({chat_id}): {e}")
            success = False
    return success


def normalize_price(fiyat: str) -> str:
    """Fiyattan sadece rakamları çıkar (karşılaştırma için)."""
    return ''.join(c for c in fiyat if c.isdigit())


def load_state():
    """State dosyasını yükle, yoksa yeni oluştur."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            print("State dosyası bozuk, yeni oluşturuluyor.")
    return {
        "cycle_start": datetime.now(TR_TZ).strftime("%Y-%m-%d"),
        "items": {},
        "reported_days": [],
        "first_run_done": False
    }


def save_state(state):
    """State dosyasını kaydet."""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_listings_playwright(max_pages=40):
    """Playwright ile ilanları çek - sayfa başı 6+ saniye bekleme."""
    results = []
    seen_codes = set()
    consecutive_failures = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        for page_num in range(1, max_pages + 1):
            # URL formatı: ?&page=2 (sitenin kendi formatı)
            if page_num == 1:
                page_url = URL
            else:
                page_url = f"{URL}?&page={page_num}"

            try:
                print(f"Sayfa {page_num} yükleniyor: {page_url}")
                
                # Sayfa yükleme
                page.goto(page_url, timeout=45000, wait_until="domcontentloaded")
                
                # JavaScript içeriğin render edilmesi için bekle
                page.wait_for_timeout(6000)
                
                # İlan kartlarının yüklenmesini bekle
                try:
                    page.wait_for_selector('a[href*="ilandetay?ilan_kodu="]', timeout=15000)
                except:
                    print(f"Sayfa {page_num}: Selector timeout, devam ediliyor...")
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        print("3 ardışık başarısız, durduruluyor.")
                        break
                    continue
                
                consecutive_failures = 0
                print(f"Sayfa {page_num} yüklendi.")
                
            except Exception as e:
                print(f"Sayfa {page_num} yüklenemedi: {e}")
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    print("3 ardışık başarısız, durduruluyor.")
                    break
                continue

            # Debug: HTML'de kaç ilan var
            html_content = page.content()
            ilan_count_in_html = html_content.count("ilan_kodu=")
            print(f"Sayfa {page_num} HTML'de {ilan_count_in_html} ilan linki bulundu.")

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
                print(f"Sayfa {page_num} boş, tarama tamamlandı.")
                break

            page_new_count = 0
            for item in listings:
                if item["kod"] not in seen_codes:
                    seen_codes.add(item["kod"])
                    # Sayfa numarasını da ekle
                    results.append((item["kod"], item["fiyat"], item["link"], item.get("title", ""), page_num))
                    page_new_count += 1
            
            print(f"Sayfa {page_num}: {len(listings)} ilan bulundu, {page_new_count} yeni eklendi. Toplam: {len(results)}")

            # Sayfada 12'den az ilan varsa son sayfaya ulaşılmış demektir
            if len(listings) < 12:
                print(f"Son sayfaya ulaşıldı (sayfa {page_num}, {len(listings)} ilan).")
                break
            
            # Sonraki sayfa için bekleme
            page.wait_for_timeout(1000)

        browser.close()

    return results


def main():
    now = datetime.now(TR_TZ)
    today = now.strftime("%Y-%m-%d")

    state = load_state()

    # 15 günlük döngü kontrolü
    cycle_start = datetime.strptime(state["cycle_start"], "%Y-%m-%d").replace(tzinfo=TR_TZ)
    if now - cycle_start >= timedelta(days=30):
        state = {"cycle_start": today, "items": {}, "reported_days": [], "first_run_done": False}
        print("30 günlük döngü sıfırlandı.")

    # İlanları çek
    try:
        listings = fetch_listings_playwright(max_pages=40)
        print(f"Toplam {len(listings)} ilan bulundu.")
    except Exception as e:
        print(f"Playwright hata: {e}")
        save_state(state)
        return

    # İlk çalışma kontrolü - ilk veri toplama
    is_first_run = not state.get("first_run_done", False) or len(state["items"]) == 0

    if is_first_run:
        # Minimum ilan kontrolü - en az 100 ilan bekliyoruz
        if len(listings) < 100:
            print(f"İlk çalışmada yetersiz ilan: {len(listings)} (minimum 100 bekleniyor)")
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
        print(f"İlk veri toplama tamamlandı: {len(listings)} ilan kaydedildi.")
    else:
        # Normal çalışma - yeni ilan ve fiyat değişikliklerini kontrol et
        new_count = 0
        price_change_count = 0

        for kod, fiyat, link, title, page_num in listings:
            if kod not in state["items"]:
                # Yeni ilan - sadece ilk 3 sayfadakiler için bildirim gönder
                if page_num <= 4:
                    send_message(f"🆕 YENİ İLAN\n📅 {today}\n🏷️ {kod}\n📝 {title}\n💰 {fiyat}\n🔗 {link}")
                    time.sleep(0.5)  # Rate limit koruması
                
                # Tüm yeni ilanları kaydet (bildirim gönderilsin veya gönderilmesin)
                state["items"][kod] = {"fiyat": fiyat, "tarih": today, "link": link, "title": title}
                new_count += 1
            else:
                # Fiyat değişikliği kontrolü
                eski = state["items"][kod]["fiyat"]
                if normalize_price(eski) != normalize_price(fiyat):
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
                send_message(f"🗑️ İLAN SİLİNDİ\n📅 {today}\n🏷️ {kod}\n📝 {item.get('title', '')}\n💰 {item.get('fiyat', '')}\n🔗 {item.get('link', '')}")
                deleted_codes.append(kod)
                deleted_count += 1
                time.sleep(0.5)  # Rate limit koruması
        
        # Silinen ilanları state'den kaldır
        for kod in deleted_codes:
            del state["items"][kod]
        
        print(f"Yeni ilan: {new_count}, Fiyat değişikliği: {price_change_count}, Silinen: {deleted_count}")

    # Günlük özet (23:30-23:59 arası, günde bir kez)
    if (now.hour == 23 and now.minute >= 30) and (today not in state["reported_days"]):
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
    print("İşlem tamamlandı.")


if __name__ == "__main__":
    main()
