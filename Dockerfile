FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /install /usr/local
COPY . .

RUN mkdir -p data logs cache

EXPOSE 5000

CMD ["sh", "-c", "python -m gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 1 src.web.app:app"]
