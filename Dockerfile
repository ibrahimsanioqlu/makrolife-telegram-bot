FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .
COPY ilanlar.json* ./
COPY history.json* ./

CMD ["python", "-u", "bot.py"]
