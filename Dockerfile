# BM (dtransport.uz) avtomatizatsiya — VDS uchun Docker image.
# Build: docker compose build
# Digest bilan qulflangan (2026-10-29): tag ko'chirilsa ham baza image
# o'zgarmaydi. Yangilash: `docker pull python:3.12-slim` → digest'ni almashtirish.
FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Playwright (OneID login fallback) + tzdata tizim paketlari
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY . .

# Runtimeda saqlanadigan kataloglar
RUN mkdir -p state reports data logs

EXPOSE 8080

CMD ["python", "-m", "bm_automation", "bot"]
