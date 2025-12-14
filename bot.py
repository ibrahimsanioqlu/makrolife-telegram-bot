import os
import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

URL = "https://www.makrolife.com.tr/tumilanlar"
BASE = "https://www.makrolife.com.tr"
DATA_FILE = "ilanlar.json"

TR_TZ = ZoneInfo("Europe/Istanbul")


def send_message(text: str):
    """Telegram'a mesaj gönder, hata durumunda logla."""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        resp = requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "text": text[:4000],
                "disable_web_page_preview": True
            },
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"Telegram mesaj hatası: {e}")
        return False


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
        "initialized": False
    }


def save_state(state):
    """State dosyasını kaydet."""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_listings_playwright(max_pages=50):
    """Playwright ile ilanları çek."""
    results = []
    seen_codes = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox'
            ]
        )
        
        context = browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        
        page = context.new_page()

        for page_num in range(1, max_pages + 1):
            page_url = f"{URL}?&page={page_num}" if page_num > 1 else URL

            try:
                page.goto(page_url, timeout=60000, wait_until="domcontentloaded")
                
                # Loading screen için 7 saniye bekle
                page.wait_for_timeout(7000)
                
            except Exception as e:
                print(f"Sayfa {page_num} yüklenemedi: {e}")
                break

            listings = page.evaluate('''() => {
                const results = [];
                const seen = new Set();
                
                const links = document.querySelectorAll('a[href*="ilandetay"]');
                
                links.forEach(link => {
                    const href = link.getAttribute("href");
                    if (!href) return;
                    
                    const match = href.match(/ilan_kodu=([A-Z0-9-]+)/i);
                    if (!match) return;
                    
                    const kod = match[1];
                    if (seen.has(kod)) return;
                    seen.add(kod);
                    
                    let fiyat = "Fiyat yok";
                    let title = "";
                    
                    let card = link.closest('div');
                    for (let i = 0; i < 10; i++) {
                        if (!card) break;
                        
                        if (!title) {
                            const h = card.querySelector('h3, h4, .title, [class*="title"]');
                            if (h) {
                                title = h.innerText.trim();
                            }
                        }
                        
                        if (fiyat === "Fiyat yok") {
                            const text = card.innerText || "";
                            const lines = text.split("\\n");
                            for (const line of lines) {
                                const trimmed = line.trim();
                                if (/^[\\d.,]+\\s*₺$/.test(trimmed)) {
                                    fiyat = trimmed;
                                    break;
                                }
                            }
                        }
                        
                        if (title && fiyat !== "Fiyat yok") break;
                        
                        card = card.parentElement;
                    }
                    
                    results.push({
                        kod: kod,
                        fiyat: fiyat,
                        title: title || kod,
                        link: href.startsWith('http') ? href : "https://www.makrolife.com.tr/" + href
                    });
                });
                
                return results;
            }''')

            if not listings:
                print(f"Sayfa {page_num}: İlan bulunamadı, tarama durduruluyor.")
                break

            for item in listings:
                if item["kod"] not in seen_codes:
                    seen_codes.add(item["kod"])
                    results.append((item["kod"], item["fiyat"], item["link"], item.get("title", "")))

            print(f"Sayfa {page_num}: {len(listings)} ilan. Toplam: {len(results)}")

        browser.close()

    return results


def main():
    now = datetime.now(TR_TZ)
    today = now.strftime("%Y-%m-%d")

    state = load_state()

    # 15 günlük döngü kontrolü
    cycle_start = datetime.strptime(state["cycle_start"], "%Y-%m-%d").replace(tzinfo=TR_TZ)
    if now - cycle_start >= timedelta(days=15):
        state = {"cycle_start": today, "items": {}, "reported_days": [], "initialized": False}
        print("15 günlük döngü sıfırlandı.")

    # İlanları çek
    try:
        listings = fetch_listings_playwright(max_pages=50)
        print(f"Toplam {len(listings)} ilan bulundu.")
    except Exception as e:
        send_message("⚠️ Playwright hata:\n" + str(e))
        save_state(state)
        return

    # Hiç ilan bulunamadıysa hata mesajı gönder
    if not listings:
        send_message(
            f"⚠️ UYARI: İlan bulunamadı!\n"
            f"📅 {today}\n"
            f"🕐 {now.strftime('%H:%M')}\n"
            f"Site erişim sorunu olabilir."
        )
        save_state(state)
        return

    # İlk çalışma mı kontrol et
    is_first_run = not state.get("initialized", False)

    if is_first_run:
        # İLK ÇALIŞMA: Tüm ilanları kaydet, sadece özet mesaj gönder
        for kod, fiyat, link, title in listings:
            state["items"][kod] = {"fiyat": fiyat, "tarih": today, "link": link, "title": title}
        
        state["initialized"] = True
        
        # Özet mesaj gönder
        sample_lines = [f"• {k} | {f}" for k, f, _, _ in listings[:10]]
        send_message(
            f"✅ İLK VERİ TOPLAMA TAMAMLANDI\n"
            f"📅 {today}\n"
            f"🕐 {now.strftime('%H:%M')}\n"
            f"📊 Toplam ilan: {len(listings)}\n"
            f"💾 Tüm ilanlar kaydedildi\n\n"
            f"Örnek ilanlar:\n"
            + "\n".join(sample_lines)
        )
        
        print(f"İlk çalışma: {len(listings)} ilan kaydedildi.")
    
    else:
        # SONRAKI ÇALIŞMALAR: Sadece yeni ilanları ve fiyat değişikliklerini bildir
        new_count = 0
        price_change_count = 0
        new_listings = []

        for kod, fiyat, link, title in listings:
            if kod not in state["items"]:
                # Yeni ilan
                new_listings.append((kod, fiyat, link, title))
                state["items"][kod] = {"fiyat": fiyat, "tarih": today, "link": link, "title": title}
                new_count += 1
            else:
                # Fiyat değişikliği kontrolü
                eski = state["items"][kod]["fiyat"]
                if normalize_price(eski) != normalize_price(fiyat):
                    send_message(f"🔔 FİYAT DEĞİŞTİ\n🏷️ {kod}\n💰 Eski: {eski}\n💰 Yeni: {fiyat}\n🔗 {link}")
                    state["items"][kod]["fiyat"] = fiyat
                    price_change_count += 1
                    time.sleep(1)

        # Yeni ilanları tek tek bildir
        for kod, fiyat, link, title in new_listings:
            send_message(f"🆕 YENİ İLAN\n📅 {today}\n🏷️ {kod}\n📝 {title}\n💰 {fiyat}\n🔗 {link}")
            time.sleep(1)

        if new_count > 0 or price_change_count > 0:
            print(f"Yeni ilan: {new_count}, Fiyat değişikliği: {price_change_count}")
        else:
            print("Değişiklik yok.")

    # Günlük özet (23:30-23:59 arası, günde bir kez)
    if (now.hour == 23 and now.minute >= 30) and (today not in state["reported_days"]):
        todays = [k for k, v in state["items"].items() if v.get("tarih") == today]
        total = len(state["items"])
        send_message(
            f"📋 Günlük Özet ({today}):\n"
            f"📊 Toplam takip edilen: {total}\n"
            f"🆕 Bugün eklenen: {len(todays)}\n"
            + ("\n".join(todays[:20]) if todays else "Bugün yeni ilan yok.")
            + ("\n..." if len(todays) > 20 else "")
        )
        state["reported_days"].append(today)

    save_state(state)
    print("İşlem tamamlandı.")


if __name__ == "__main__":
    main()
