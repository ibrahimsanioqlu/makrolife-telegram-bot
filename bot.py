import requests
from bs4 import BeautifulSoup
import os
import json
from datetime import datetime, timedelta

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

URL = "https://www.makrolife.com.tr/tumilanlar"
DATA_FILE = "ilanlar.json"

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": text})

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def main():
    response = requests.get(URL, timeout=30)
    soup = BeautifulSoup(response.text, "html.parser")

    ilanlar = soup.select(".ilan-item")
    kayitli = load_data()
    kayitli_kodlar = [i["kod"] for i in kayitli]

    yeni_ilanlar = []

    for ilan in ilanlar:
        baslik = ilan.select_one(".ilan-title")
        kod = ilan.get("data-ilan-kodu")
        link = ilan.select_one("a")["href"]

        if not kod or kod in kayitli_kodlar:
            continue

        tarih = datetime.now().strftime("%d.%m.%Y")

        mesaj = (
            f"Tarih: {tarih}\n\n"
            f"Başlık: {baslik.text.strip()}\n"
            f"İlan kodu: {kod}\n\n"
            f"İlan linki: https://www.makrolife.com.tr{link}"
        )

        send_message(mesaj)

        yeni_ilanlar.append({
            "kod": kod,
            "tarih": tarih
        })

    tum_ilanlar = kayitli + yeni_ilanlar

    # 15 günden eski ilanları sil
    sinir = datetime.now() - timedelta(days=15)
    tum_ilanlar = [
        i for i in tum_ilanlar
        if datetime.strptime(i["tarih"], "%d.%m.%Y") >= sinir
    ]

    # Saat 23:30 ise günlük liste gönder
    now = datetime.now()
    if now.hour == 23 and now.minute >= 30:
        if tum_ilanlar:
            liste = "\n".join(i["kod"] for i in tum_ilanlar)
            send_message(f"Günlük ilan kodları:\n{liste}")

    save_data(tum_ilanlar)

if __name__ == "__main__":
    main()
