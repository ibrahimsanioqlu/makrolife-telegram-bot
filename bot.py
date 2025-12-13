import requests
from bs4 import BeautifulSoup
import os
import json
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

URL = "https://www.makrolife.com.tr/tumilanlar"
BASE = "https://www.makrolife.com.tr"
DATA_FILE = "ilanlar.json"

TR_TZ = ZoneInfo("Europe/Istanbul")

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": text,
            "disable_web_page_preview": True
        }
    )

def load_state():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "cycle_start": datetime.now(TR_TZ).strftime("%Y-%m-%d"),
        "items": [],
        "reported_days": []
    }

def save_state(state):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def fetch_listings():
    r = requests.get(URL, timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")

    ilanlar = soup.select(".ilan-item")
    results = []

    for ilan in ilanlar:
        baslik_el = ilan.select_one(".ilan-title")
        fiyat_el = ilan.select_one(".ilan-price")

        kod = ilan.get("data-ilan-kodu")
        a = ilan.select_one("a")
        if not a or not kod:
            continue

        href = a.get("href", "")
        link = href if href.startswith("http") else (BASE + href)

        baslik = baslik_el.text.strip() if baslik_el else "(Başlık yok)"
        fiyat = fiyat_el.text.strip() if fiyat_el else "Fiyat belirtilmemiş"

        results.append((kod, baslik, fiyat, link))

    return results

def main():
    send_message("🧪 TEST: Bot şu an çalışıyor.")

    now = datetime.now(TR_TZ)
    today_str = now.strftime("%Y-%m-%d")

    state = load_state()

    # 15 günde bir sıfırlama
    cycle_start = datetime.strptime(state["cycle_start"], "%Y-%m-%d").replace(tzinfo=TR_TZ)
    if now - cycle_start >= timedelta(days=15):
        state = {
            "cycle_start": today_str,
            "items": [],
            "reported_days": []
        }

    items_by_code = {i["kod"]: i for i in state["items"]}

    try:
        listings = fetch_listings()
    except:
        save_state(state)
        return

    for kod, baslik, fiyat, link in listings:
        if kod not in items_by_code:
            # YENİ İLAN
            send_message(
                f"🆕 YENİ İLAN\n\n"
                f"Tarih: {now.strftime('%d.%m.%Y')}\n"
                f"Başlık: {baslik}\n"
                f"İlan kodu: {kod}\n"
                f"Fiyat: {fiyat}\n\n"
                f"{link}"
            )
            state["items"].append({
                "kod": kod,
                "tarih": today_str,
                "fiyat": fiyat
            })
        else:
            # FİYAT DEĞİŞİMİ KONTROLÜ
            eski_fiyat = items_by_code[kod]["fiyat"]
            if eski_fiyat != fiyat:
                send_message(
                    f"🔔 FİYAT DEĞİŞTİ\n\n"
                    f"İlan kodu: {kod}\n"
                    f"Başlık: {baslik}\n\n"
                    f"Eski fiyat: {eski_fiyat}\n"
                    f"Yeni fiyat: {fiyat}\n\n"
                    f"{link}"
                )
                items_by_code[kod]["fiyat"] = fiyat

    # Günlük 23:30 raporu (sadece BUGÜN gelen ilanlar)
    if (now.hour == 23 and now.minute >= 30) and (today_str not in state["reported_days"]):
        todays = [i["kod"] for i in state["items"] if i["tarih"] == today_str]
        if todays:
            send_message("📋 Günlük ilan kodları:\n" + "\n".join(todays))
        else:
            send_message("📋 Günlük ilan kodları:\nBugün yeni ilan yok.")
        state["reported_days"].append(today_str)

    save_state(state)

if __name__ == "__main__":
    main()
