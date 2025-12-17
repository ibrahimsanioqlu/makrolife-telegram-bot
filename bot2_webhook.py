#!/usr/bin/env python3
# bot2_webhook.py
# Telegram Bot #2 için webhook sunucusu:
# - Inline buton callback_query yakalar
# - Onaylanınca sitendeki /admin endpointlerine istek atar
#
# Railway ortam değişkenleri:
#   BOT_TOKEN2         : 8365622394:AAE3Zba7xNKQvT9GwxxnFcF_YIg1inyIo9g
#   WEBHOOK_SECRET     : setWebhook secret_token (önerilir)
#   SITE_BASE_URL      : örn https://www.diyarbakiremlakmarket.com/admin
#   SITE_API_KEY       : (opsiyonel) sitendeki API anahtarı
#   PORT               : Railway otomatik verir

import os
import json
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import requests

BOT2_TOKEN = os.getenv("BOT_TOKEN2") or os.getenv("MIRROR_BOT_TOKEN") or ""
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET") or ""
SITE_BASE_URL = (os.getenv("SITE_BASE_URL") or "https://www.diyarbakiremlakmarket.com/admin").rstrip("/")
SITE_API_KEY = os.getenv("SITE_API_KEY") or ""
PORT = int(os.getenv("PORT") or "8080")

TG_API = f"https://api.telegram.org/bot{BOT2_TOKEN}"

def tg_post(method: str, data: dict, timeout: int = 20) -> dict:
    url = f"{TG_API}/{method}"
    r = requests.post(url, data=data, timeout=timeout)
    try:
        return r.json()
    except Exception:
        return {"ok": False, "status": r.status_code, "text": (r.text or "")[:500]}

def site_post(path: str, data: dict, timeout: int = 25):
    url = f"{SITE_BASE_URL}/{path.lstrip('/')}"
    if SITE_API_KEY:
        data = {**data, "api_key": SITE_API_KEY}
    r = requests.post(url, data=data, timeout=timeout)
    # JSON bekliyoruz; ama bazen HTML gelebilir. İkisini de dön.
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"raw": (r.text or "")[:800]}

def fetch_listing_from_site(ilan_kodu: str) -> dict:
    status, resp = site_post("listing_scraper.php", {"listing_code": ilan_kodu}, timeout=35)
    if status != 200:
        raise RuntimeError(f"listing_scraper HTTP {status}: {resp}")
    if not resp.get("success"):
        raise RuntimeError(f"listing_scraper success=false: {resp}")
    return resp.get("data") or {}

def action_add(ilan_kodu: str) -> str:
    data = fetch_listing_from_site(ilan_kodu)
    title = (data.get("title") or "").strip()
    price = data.get("price") or 0
    photos = data.get("photos") or []
    # site api endpoint (JSON) - aşağıda verdiğim dosya
    status, resp = site_post("api-ilan-ekle.php", {
        "ilan_kodu": ilan_kodu,
        "baslik": title,
        "fiyat": price,
        "resim_listesi": json.dumps(photos, ensure_ascii=False)
    }, timeout=35)
    if status != 200:
        raise RuntimeError(f"api-ilan-ekle HTTP {status}: {resp}")
    if not resp.get("success"):
        raise RuntimeError(f"api-ilan-ekle success=false: {resp}")
    return resp.get("message") or "Siteye eklendi."

def action_price_update(ilan_kodu: str) -> str:
    data = fetch_listing_from_site(ilan_kodu)
    price = data.get("price") or 0
    status, resp = site_post("api-ilan-fiyat-guncelle.php", {
        "ilan_kodu": ilan_kodu,
        "fiyat": price
    }, timeout=30)
    if status != 200:
        raise RuntimeError(f"api-ilan-fiyat-guncelle HTTP {status}: {resp}")
    if not resp.get("success"):
        raise RuntimeError(f"api-ilan-fiyat-guncelle success=false: {resp}")
    return resp.get("message") or "Fiyat güncellendi."

def action_delete(ilan_kodu: str) -> str:
    # mevcut dosyan JSON dönüyor
    status, resp = site_post("ilan-sil.php", {"ilan_kodu": ilan_kodu}, timeout=25)
    if status != 200:
        raise RuntimeError(f"ilan-sil HTTP {status}: {resp}")
    # ilan-sil.php formatı: {basarili:bool, mesaj:string}
    if "basarili" in resp and not resp.get("basarili"):
        raise RuntimeError(resp.get("mesaj") or "Silme başarısız.")
    if resp.get("success") is False:
        raise RuntimeError(resp.get("message") or "Silme başarısız.")
    return resp.get("mesaj") or resp.get("message") or "Siteden silindi."

def parse_callback(data: str):
    # Beklenen: "A|ML-123", "P|ML-123", "D|ML-123", "S|ML-123"
    parts = (data or "").split("|", 1)
    if len(parts) != 2:
        return None, None
    return parts[0].strip().upper(), parts[1].strip()

class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict):
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.startswith("/health"):
            return self._send(200, {"ok": True, "ts": int(time.time())})
        return self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        # Telegram secret_token kontrolü (önerilir)
        if WEBHOOK_SECRET:
            secret = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if secret != WEBHOOK_SECRET:
                return self._send(401, {"ok": False, "error": "bad secret token"})

        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length).decode("utf-8", errors="ignore")
        try:
            update = json.loads(body) if body else {}
        except Exception:
            return self._send(400, {"ok": False, "error": "invalid json"})

        try:
            # callback_query yakala
            cq = update.get("callback_query")
            if not cq:
                return self._send(200, {"ok": True})

            cb_id = cq.get("id")
            data = cq.get("data") or ""
            msg = cq.get("message") or {}
            chat_id = (msg.get("chat") or {}).get("id")
            message_id = msg.get("message_id")

            action, ilan_kodu = parse_callback(data)
            if not action or not ilan_kodu:
                tg_post("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Geçersiz buton verisi."})
                return self._send(200, {"ok": True})

            # Hızlı ACK
            tg_post("answerCallbackQuery", {"callback_query_id": cb_id, "text": "İşleniyor..."})

            # İşlem
            if action == "S":
                result_text = f"Atlandı: {ilan_kodu}"
            elif action == "A":
                result_text = action_add(ilan_kodu)
            elif action == "P":
                result_text = action_price_update(ilan_kodu)
            elif action == "D":
                result_text = action_delete(ilan_kodu)
            else:
                result_text = "Bilinmeyen işlem."

            # Mesajdaki butonları kaldır (tekrar basılmasın)
            if chat_id and message_id:
                tg_post("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id, "reply_markup": "{}"})

            # Sonucu bir mesaj olarak da yaz (istersen kapatabilirsin)
            if chat_id:
                tg_post("sendMessage", {
                    "chat_id": chat_id,
                    "text": f"✅ {result_text}",
                    "disable_web_page_preview": True
                })

            return self._send(200, {"ok": True})

        except Exception as e:
            # Telegram'a hata bildir
            try:
                cq = update.get("callback_query") or {}
                cb_id = cq.get("id")
                chat_id = ((cq.get("message") or {}).get("chat") or {}).get("id")
                if cb_id:
                    tg_post("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Hata oluştu."})
                if chat_id:
                    tg_post("sendMessage", {"chat_id": chat_id, "text": f"❌ Hata: {str(e)}"})
            except Exception:
                pass

            print("WEBHOOK ERROR:", e, flush=True)
            traceback.print_exc()
            return self._send(200, {"ok": True})

def main():
    if not BOT2_TOKEN:
        raise SystemExit("BOT_TOKEN2 yok! Railway Variables'a BOT_TOKEN2 ekle.")

    print("BOT2 Webhook server başlıyor...", flush=True)
    print("SITE_BASE_URL =", SITE_BASE_URL, flush=True)
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    httpd.serve_forever()

if __name__ == "__main__":
    main()
