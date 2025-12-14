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
        "reported_days": []
    }


def save_state(state):
    """State dosyasını kaydet."""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_listings_playwright(max_pages=10):
    """Playwright ile ilanları çek."""
    results = []
    seen_codes = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for page_num in range(1, max_pages + 1):
            page_url = f"{URL}?&page={page_num}" if page_num > 1 else URL

            try:
                page.goto(page_url, timeout=60000, wait_until="domcontentloaded")
                
                # Loading screen geçene kadar bekle - ilan linkleri görünene kadar
                page.wait_for_selector('a[href*="ilandetay?ilan_kodu="]', timeout=30000)
                
                # Ekstra bekleme - tüm içeriğin yüklenmesi için
                page.wait_for_timeout(2000)
                
            except Exception as e:
                print(f"Sayfa {page_num} yüklenemedi: {e}")
                break

            listings = page.evaluate('''() => {
                const results = [];
                const seen = new Set();
                
                const links = document.querySelectorAll('a[href*="ilandetay?ilan_kodu="]');
                
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
                    let el = link.parentElement;
                    
                    // Başlığı bul - h3 içinde veya link textinde
                    const h3 = el.closest('.card, .listing-item, [class*="ilan"]')?.querySelector('h3, h4, .title, [class*="title"]');
                    if (h3) {
                        title = h3.innerText.trim();
                    }
                    if (!title) {
                        // Link'in üst elementlerinde h3 ara
                        let parent = el;
                        for (let i = 0; i < 5; i++) {
                            if (!parent) break;
                            const h = parent.querySelector('h3');
                            if (h) {
                                title = h.innerText.trim();
                                break;
                            }
                            parent = parent.parentElement;
                        }
                    }
                    
                    for (let i = 0; i < 5; i++) {
                        if (!el) break;
                        
                        const children = el.childNodes;
                        for (const child of children) {
                            if (child.nodeType === 3) {
                                const text = child.textContent.trim();
                                const fiyatMatch = text.match(/^([\\d.,]+)\\s*₺$/);
                                if (fiyatMatch) {
                                    fiyat = fiyatMatch[0];
                                    break;
                                }
                            }
                        }
                        
                        if (fiyat !== "Fiyat yok") break;
                        
                        const allText = el.innerText || "";
                        const lines = allText.split("\\n");
                        for (const line of lines) {
                            const trimmed = line.trim();
                            if (/^[\\d.,]+\\s*₺$/.test(trimmed)) {
                                fiyat = trimmed;
                                break;
                            }
                        }
                        
                        if (fiyat !== "Fiyat yok") break;
                        el = el.parentElement;
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
                break

            for item in listings:
                if item["kod"] not in seen_codes:
                    seen_codes.add(item["kod"])
                    results.append((item["kod"], item["fiyat"], item["link"], item.get("title", "")))

        browser.close()

    return results


def main():
    now = datetime.now(TR_TZ)
    today = now.strftime("%Y-%m-%d")

    state = load_state()

    # 15 günlük döngü kontrolü
    cycle_start = datetime.strptime(state["cycle_start"], "%Y-%m-%d").replace(tzinfo=TR_TZ)
    if now - cycle_start >= timedelta(days=15):
        state = {"cycle_start": today, "items": {}, "reported_days": []}
        print("15 günlük döngü sıfırlandı.")

    # İlanları çek
    try:
        listings = fetch_listings_playwright(max_pages=10)
        print(f"Toplam {len(listings)} ilan bulundu.")
    except Exception as e:
        send_message("⚠️ Playwright hata:\n" + str(e))
        save_state(state)
        return

    # TEST MESAJI
    test_lines = [f"• {k} | {f}" for k, f, _, _ in listings[:10]]
    send_message(
        f"🧪 TEST SONUCU\n"
        f"📅 {today}\n"
        f"🕐 {now.strftime('%H:%M')}\n"
        f"📊 Toplam ilan: {len(listings)}\n"
        + ("\n".join(test_lines) if test_lines else "İlan bulunamadı")
    )

    # Yeni ilan ve fiyat değişikliklerini kontrol et
    new_count = 0
    price_change_count = 0

    for kod, fiyat, link, title in listings:
        if kod not in state["items"]:
            # Yeni ilan
            send_message(f"🆕 YENİ İLAN\n📅 {today}\n🏷️ {kod}\n📝 {title}\n💰 {fiyat}\n🔗 {link}")
            state["items"][kod] = {"fiyat": fiyat, "tarih": today, "link": link, "title": title}
            new_count += 1
            time.sleep(0.5)  # Rate limit koruması
        else:
            # Fiyat değişikliği kontrolü (normalize edilmiş karşılaştırma)
            eski = state["items"][kod]["fiyat"]
            if normalize_price(eski) != normalize_price(fiyat):
                send_message(f"🔔 FİYAT DEĞİŞTİ\n🏷️ {kod}\n💰 Eski: {eski}\n💰 Yeni: {fiyat}\n🔗 {link}")
                state["items"][kod]["fiyat"] = fiyat
                price_change_count += 1
                time.sleep(0.5)  # Rate limit koruması

    print(f"Yeni ilan: {new_count}, Fiyat değişikliği: {price_change_count}")

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
