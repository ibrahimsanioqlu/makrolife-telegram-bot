import requests
import os
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

API_URL = "https://www.makrolife.com.tr/api/ilanlar"
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
        "items": {},
        "reported_days": []
    }

def save_state(state):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def fetch_listings():
    r = requests.get(API_URL, timeout=30)
    data = r.json()

    results = []
    for ilan in data.get("data", []):
        kod = ilan.get("ilanKodu")
        fiyat = ilan.get("fiyat")
        link = ilan.get("ilanUrl")

        if kod and fiyat and link:
            results.append((kod, str(fiyat), BASE + link))

    return results

def main():
    now = datetime.now(TR_TZ)
    today = now.strftime("%Y-%m-%d")

    state = load_state()

    # 🔄 15 günde bir sıfırlama
    cycle_start = datetime.strptime(state["cycle_start"], "%Y-%m-%d").replace(tzinfo=TR_TZ)
    if now - cycle_start >= timedelta(days=15):
        state = {
            "cycle_start": today,
            "items": {},
            "reported_days": []
        }

    try:
        listings = fetch_listings()
    except:
        save_state(state)
        return

    # 🧪 TEST: Şu an görülen ilan kodları (ilk 20)
    send_message(
        "🧪 Şu an görülen ilan kodları:\n" +
        ("\n".join(k for k, _, _ in listings[:20]) if listings else "İlan bulunamadı")
    )

    for kod, fiyat, link in listings:
        if kod not in state["items"]:
            send_message(
                f"🆕 YENİ İLAN\n\n"
                f"İlan kodu: {kod}\n"
                f"Fiyat: {fiyat}\n\n"
                f"{link}"
            )
            state["items"][kod] = {
                "fiyat": fiyat,
                "tarih": today
            }
        else:
            eski_fiyat = state["items"][kod]["fiyat"]
            if eski_fiyat != fiyat:
                send_message(
                    f"🔔 FİYAT DEĞİŞTİ\n\n"
                    f"İlan kodu: {kod}\n"
                    f"Eski fiyat: {eski_fiyat}\n"
                    f"Yeni fiyat: {fiyat}\n\n"
                    f"{link}"
                )
                state["items"][kod]["fiyat"] = fiyat

    # 📋 Günlük 23:30 raporu (SADECE bugün)
    if (now.hour == 23 and now.minute >= 30) and (today not in state["reported_days"]):
        todays = [k for k, v in state["items"].items() if v["tarih"] == today]
        if todays:
            send_message("📋 Günlük ilan kodları:\n" + "\n".join(todays))
        else:
            send_message("📋 Günlük ilan kodları:\nBugün yeni ilan yok.")
        state["reported_days"].append(today)

    save_state(state)

if __name__ == "__main__":
    main()
