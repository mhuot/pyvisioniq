FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data logs cache

# EXPOSE is informational only - actual port binding is handled by docker-compose
# Removing since we use dynamic PORT variable

CMD ["sh", "-c", "python -m gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 1 src.web.app:app"]