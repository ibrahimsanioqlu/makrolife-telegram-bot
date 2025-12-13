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
        "items": {},          # {kod: {"fiyat": "...", "tarih": "YYYY-MM-DD", "link": "..."}}
        "reported_days": []   # ["YYYY-MM-DD", ...]
    }


def save_state(state):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_listings_playwright(limit=50):
    """
    Sayfayı Playwright ile açar (JS çalışır) ve ilk `limit` ilandan:
    (ilan_kodu, fiyat, link) döndürür.
    """
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(URL, timeout=60000, wait_until="domcontentloaded")
        # JS ile yüklenen içerik için biraz bekle
        page.wait_for_timeout(6000)

        # İlan kartlarını bulmak için esnek seçim
        cards = page.query_selector_all("[data-ilan-kodu]")
        for card in cards[:limit]:
            kod = card.get_attribute("data-ilan-kodu")
            if not kod:
                continue
            kod = kod.strip()

            a = card.query_selector("a")
            href = a.get_attribute("href") if a else ""
            link = href if (href and href.startswith("http")) else (BASE + href if href else "")

            fiyat_el = card.query_selector(".ilan-price, .price, [class*='price']")
            fiyat = fiyat_el.inner_text().strip() if fiyat_el else "Fiyat belirtilmemiş"

            results.append((kod, fiyat, link))

        browser.close()

    return results


def main():
    now = datetime.now(TR_TZ)
    today = now.strftime("%Y-%m-%d")

    state = load_state()

    # 15 günde bir tam sıfırlama
    cycle_start = datetime.strptime(state["cycle_start"], "%Y-%m-%d").replace(tzinfo=TR_TZ)
    if now - cycle_start >= timedelta(days=15):
        state = {"cycle_start": today, "items": {}, "reported_days": []}

    # İlanları çek
    try:
        listings = fetch_listings_playwright(limit=50)
    except Exception as e:
        send_message("⚠️ Playwright hata:\n" + str(e))
        save_state(state)
        return

    # ✅ TEST: Bot ilanları okuyor mu?
    send_message(
        "🧪 TEST SONUCU\n"
        f"Toplam bulunan ilan sayısı: {len(listings)}\n"
        + ("\n".join([f"{k} | {f}" for k, f, _ in listings[:10]]) if listings else "")
    )

    # Yeni ilan / fiyat değişimi
    for kod, fiyat, link in listings:
        if kod not in state["items"]:
            send_message(f"🆕 YENİ İLAN\nİlan kodu: {kod}\nFiyat: {fiyat}\n{link}")
            state["items"][kod] = {"fiyat": fiyat, "tarih": today, "link": link}
        else:
            eski = state["items"][kod]["fiyat"]
            if eski != fiyat:
                send_message(
                    f"🔔 FİYAT DEĞİŞTİ\n"
                    f"İlan kodu: {kod}\n"
                    f"Eski: {eski}\nYeni: {fiyat}\n"
                    f"{state['items'][kod].get('link', link) or link}"
                )
                state["items"][kod]["fiyat"] = fiyat

    # 23:30 günlük liste (sadece bugün gelenler) — günde 1 kere
    if (now.hour == 23 and now.minute >= 30) and (today not in state["reported_days"]):
        todays = [k for k, v in state["items"].items() if v.get("tarih") == today]
        send_message("📋 Günlük ilan kodları:\n" + ("\n".join(todays) if todays else "Bugün yeni ilan yok."))
        state["reported_days"].append(today)

    save_state(state)


if __name__ == "__main__":
    main()
