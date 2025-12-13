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
    """Telegram mesajı gönder"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "text": text[:4000],  # Telegram limit
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


def fetch_listings_playwright():
    """Debug versiyonu - HTML içeriğini göster"""
    all_results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-blink-features=AutomationControlled']
        )
        
        context = browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='tr-TR'
        )
        
        page = context.new_page()
        
        # Anti-bot detection bypass
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """)

        try:
            page.goto(URL, timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(10000)  # 10 saniye bekle
            
            html = page.content()
            title = page.title()
            
            # HTML'in ilk 1500 karakterini gönder
            send_message(f"🔍 DEBUG HTML\nTitle: {title}\nHTML uzunluğu: {len(html)}\n\nİlk 1500 karakter:\n{html[:1500]}")
            
            # Body text'ini de gönder
            body_text = page.evaluate('() => document.body ? document.body.innerText.substring(0, 1500) : "Body yok"')
            send_message(f"📄 BODY TEXT:\n{body_text}")
            
        except Exception as e:
            send_message(f"❌ Hata: {e}")
            browser.close()
            return []

        browser.close()

    return all_results


def main():
    now = datetime.now(TR_TZ)
    today = now.strftime("%Y-%m-%d")

    state = load_state()

    # İlanları çek (debug modunda)
    try:
        listings = fetch_listings_playwright()
    except Exception as e:
        send_message("⚠️ Playwright hata:\n" + str(e))
        save_state(state)
        return

    send_message(f"🧪 Debug tamamlandı.\nBulunan: {len(listings)}")
    save_state(state)


if __name__ == "__main__":
    main()
