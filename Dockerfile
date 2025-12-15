FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

WORKDIR /app

# Volume için data klasörü
VOLUME /data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

CMD ["python", "-u", "bot.py"]
