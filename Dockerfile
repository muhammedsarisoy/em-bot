FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./

ENV PYTHONUNBUFFERED=1
# 1000 kullanıcısının ev klasörü yok; yfinance önbelleği (cookie, tz) buraya yazılsın
ENV HOME=/tmp
# Önceden oluştur: paralel indirmede iki thread aynı anda mkdir yapıp birbirini bozmasın
RUN mkdir -p /tmp/.cache/py-yfinance && chown -R 1000:1000 /tmp/.cache
# Sunucudaki ubuntu kullanıcısı (data/ klasörüne yazabilsin, root çalışmasın)
USER 1000:1000
CMD ["python", "main.py"]
