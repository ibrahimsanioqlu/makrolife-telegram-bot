import os
import json
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
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": text,
            "disable_web_page_preview": True
        },
        timeout=30
    )


def load_state():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "cycle_start": datetime.now(TR_TZ).strftime("%Y-%m-%d"),
        "items": {},
        "reported_days": []
    }


def save_state(state):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_listings_playwright(max_pages=10):
    results = []
    seen_codes = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for page_num in range(1, max_pages + 1):
            page_url = f"{URL}?&page={page_num}" if page_num > 1 else URL

            try:
                page.goto(page_url, timeout=60000, wait_until="domcontentloaded")
                page.wait_for_timeout(8000)
            except Exception as e:
                print(f"Sayfa {page_num} yüklenemedi: {e}")
                break

            # DEBUG: Sayfadaki elementleri say
            debug_info = page.evaluate('''() => {
                return {
                    allLinks: document.querySelectorAll('a').length,
                    ilanLinks: document.querySelectorAll('a[href*="ilan_kodu"]').length,
                    h3Count: document.querySelectorAll('h3').length,
                    h3aCount: document.querySelectorAll('h3 a').length,
                    bodyText: document.body.innerText.substring(0, 1000)
                };
            }''')
            print(f"DEBUG Page {page_num}: {debug_info}")

            listings = page.evaluate('''() => {
                const results = [];
                
                // TÜM linkleri tara
                const allLinks = document.querySelectorAll('a[href*="ilan_kodu"]');
                const seen = new Set();
                
                allLinks.forEach(link => {
                    const href = link.getAttribute("href") || "";
                    const kodMatch = href.match(/ilan_kodu=([A-Z]{2}-\\d+-\\d+)/i);
                    if (!kodMatch) return;
                    
                    const kod = kodMatch[1];
                    if (seen.has(kod)) return;
                    seen.add(kod);
                    
                    // Fiyatı bulmak için parent'lara çık
                    let fiyat = "Fiyat yok";
                    let el = link;
                    for (let i = 0; i < 10; i++) {
                        if (!el.parentElement) break;
                        el = el.parentElement;
                        const text = el.innerText || "";
                        const match = text.match(/(\\d{1,3}(?:[.,]\\d{3})*(?:[.,]\\d+)?)\\s*₺/);
                        if (match) {
                            fiyat = match[0];
                            break;
                        }
                    }
                    
                    results.push({
                        kod: kod,
                        fiyat: fiyat,
                        link: "https://www.makrolife.com.tr/" + href
                    });
                });
                
                return results;
            }''')

            if not listings:
                print(f"Sayfa {page_num}: 0 ilan bulundu")
                if page_num == 1:
                    break
                continue

            for item in listings:
                if item["kod"] not in seen_codes:
                    seen_codes.add(item["kod"])
                    results.append((item["kod"], item["fiyat"], item["link"]))

        browser.close()

    return results


def main():
    now = datetime.now(TR_TZ)
    today = now.strftime("%Y-%m-%d")

    state = load_state()

    # 15 günde bir sıfırlama
    cycle_start = datetime.strptime(state["cycle_start"], "%Y-%m-%d").replace(tzinfo=TR_TZ)
    if now - cycle_start >= timedelta(days=15):
        state = {"cycle_start": today, "items": {}, "reported_days": []}

    try:
        listings = fetch_listings_playwright(max_pages=10)
    except Exception as e:
        send_message("⚠️ Playwright hata:\n" + str(e))
        save_state(state)
        return

    # TEST MESAJI - Çalıştığını doğruladıktan sonra bu bloğu sil
    send_message(
        "🧪 TEST SONUCU\n"
        f"Toplam ilan: {len(listings)}\n"
        + ("\n".join([f"{k} | {f}" for k, f, _ in listings[:10]]) if listings else "")
    )

    # Yeni ilan / fiyat değişimi
    for kod, fiyat, link in listings:
        if kod not in state["items"]:
            send_message(f"🆕 YENİ İLAN\n📅 {today}\n🏷️ {kod}\n💰 {fiyat}\n🔗 {link}")
            state["items"][kod] = {"fiyat": fiyat, "tarih": today, "link": link}
        else:
            eski = state["items"][kod]["fiyat"]
            if eski != fiyat:
                send_message(f"🔔 FİYAT DEĞİŞTİ\n🏷️ {kod}\n💰 Eski: {eski}\n💰 Yeni: {fiyat}\n🔗 {link}")
                state["items"][kod]["fiyat"] = fiyat

    # 23:30 günlük özet
    if (now.hour == 23 and now.minute >= 30) and (today not in state["reported_days"]):
        todays = [k for k, v in state["items"].items() if v.get("tarih") == today]
        send_message(f"📋 Günlük Özet ({today}):\n" + ("\n".join(todays) if todays else "Bugün yeni ilan yok."))
        state["reported_days"].append(today)

    save_state(state)


if __name__ == "__main__":
    main()
