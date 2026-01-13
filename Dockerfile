FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy

WORKDIR /app

# Bağımlılıkları kopyala ve kur
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Uygulama dosyalarını kopyala
COPY . .

# Bot'u çalıştır
CMD ["python", "bot.py"]
