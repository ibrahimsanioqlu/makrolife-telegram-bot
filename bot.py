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
    try:
        requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "text": text[:4000],
                "disable_web_page_preview": True
            },
            timeout=30
        )
    except Exception as e:
        print(f"Mesaj gönderilemedi: {e}")


def load_state():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "cycle_start": datetime.now(TR_TZ).strftime("%Y-%m-%d"),
        "items": {},
        "reported_days": [],
        "initialized": False
    }


def save_state(state):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_listings_playwright(max_pages=10):
    """
    Sayfaları tarar ve ilanları çeker.
    """
    all_results = []
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

            listings = page.evaluate('''() => {
                const results = [];
                const processedKods = new Set();
                
                const links = document.querySelectorAll('a[href*="ilandetay?ilan_kodu="]');
                
                links.forEach(link => {
                    const href = link.getAttribute("href");
                    if (!href) return;
                    
                    const kodMatch = href.match(/ilan_kodu=([A-Z0-9-]+)/i);
                    if (!kodMatch) return;
                    
                    const kod = kodMatch[1];
                    if (processedKods.has(kod)) return;
                    processedKods.add(kod);
                    
                    let card = link;
                    for (let i = 0; i < 10; i++) {
                        if (!card.parentElement) break;
                        card = card.parentElement;
                        if (card.querySelector('img') && card.innerText.includes('₺')) {
                            break;
                        }
                    }
                    
                    const cardText = card.innerText || "";
                    const lines = cardText.split("\\n").map(l => l.trim()).filter(l => l);
                    
                    let fiyat = "Fiyat yok";
                    for (const line of lines) {
                        if (/^[\\d.,]+\\s*₺$/.test(line)) {
                            fiyat = line;
                            break;
                        }
                    }
                    
                    let baslik = "";
                    const h3 = card.querySelector("h3");
                    if (h3) {
                        baslik = h3.innerText.trim();
                    } else {
                        for (const line of lines) {
                            if (line.length > baslik.length && !line.includes("₺") && !line.includes("m²")) {
                                baslik = line;
                            }
                        }
                    }
                    
                    results.push({
                        kod: kod,
                        fiyat: fiyat,
                        baslik: baslik || "Başlık yok",
                        link: "https://www.makrolife.com.tr/" + href
                    });
                });
                
                return results;
            }''')

            if not listings:
                print(f"Sayfa {page_num}: ilan yok, durduruluyor.")
                break

            for item in listings:
                if item["kod"] not in seen_codes:
                    seen_codes.add(item["kod"])
                    all_results.append(item)

            print(f"Sayfa {page_num}: {len(listings)} ilan (Toplam: {len(all_results)})")

        browser.close()

    return all_results


def main():
    now = datetime.now(TR_TZ)
    today = now.strftime("%Y-%m-%d")

    state = load_state()

    cycle_start = datetime.strptime(state["cycle_start"], "%Y-%m-%d").replace(tzinfo=TR_TZ)
    if now - cycle_start >= timedelta(days=15):
        state = {
            "cycle_start": today,
            "items": {},
            "reported_days": [],
            "initialized": False
        }

    try:
        listings = fetch_listings_playwright(max_pages=10)
    except Exception as e:
        send_message("⚠️ Playwright hata:\n" + str(e))
        save_state(state)
        return

    # ✅ TEST MESAJI
    send_message(
        "🧪 TEST SONUCU\n"
        f"Toplam bulunan ilan: {len(listings)}\n"
        f"Kayıtlı ilan: {len(state['items'])}\n"
        + ("\n".join([f"{item['kod']} | {item['fiyat']}" for item in listings[:5]]) if listings else "")
    )

    is_first_run = not state.get("initialized", False)

    new_count = 0
    price_change_count = 0

    for item in listings:
        kod = item["kod"]
        fiyat = item["fiyat"]
        baslik = item["baslik"]
        link = item["link"]

        if kod not in state["items"]:
            new_count += 1
            state["items"][kod] = {
                "fiyat": fiyat,
                "baslik": baslik,
                "tarih": today,
                "link": link
            }

            # İlk çalışmada tek tek mesaj ATMA
            if not is_first_run:
                send_message(
                    f"🆕 YENİ İLAN\n"
                    f"📅 {today}\n"
                    f"🏷️ {kod}\n"
                    f"📝 {baslik}\n"
                    f"💰 {fiyat}\n"
                    f"🔗 {link}"
                )
        else:
            eski_fiyat = state["items"][kod]["fiyat"]
            if eski_fiyat != fiyat:
                price_change_count += 1
                state["items"][kod]["fiyat"] = fiyat

                send_message(
                    f"🔔 FİYAT DEĞİŞTİ\n"
                    f"🏷️ {kod}\n"
                    f"📝 {baslik}\n"
                    f"💰 Eski: {eski_fiyat}\n"
                    f"💰 Yeni: {fiyat}\n"
                    f"🔗 {link}"
                )

    # İlk çalışma - TEK MESAJ ile bildir
    if is_first_run:
        send_message(
            f"🚀 BOT BAŞLATILDI!\n"
            f"📅 {today}\n"
            f"📊 {len(listings)} mevcut ilan kaydedildi.\n\n"
            f"✅ Artık sadece YENİ ilanlar ve FİYAT değişiklikleri bildirilecek."
        )
        state["initialized"] = True

    # 23:30 günlük özet
    if (now.hour == 23 and now.minute >= 30) and (today not in state["reported_days"]):
        todays = [k for k, v in state["items"].items() if v.get("tarih") == today]
        msg = f"📋 GÜNLÜK ÖZET\n📅 {today}\n\n"
        if todays:
            msg += f"Bugün {len(todays)} yeni ilan:\n\n"
            for kod in todays[:50]:
                item = state["items"][kod]
                msg += f"• {kod} - {item.get('fiyat', '?')}\n"
        else:
            msg += "Bugün yeni ilan yok."
        send_message(msg)
        state["reported_days"].append(today)

    save_state(state)


if __name__ == "__main__":
    main()
