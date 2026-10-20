FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install -e . && pip install telethon cryptography
COPY config/scryner.example.yaml ./config/scryner.example.yaml
COPY --from=web /web/dist ./web/dist
RUN useradd -m scryner && mkdir -p /app/data && chown -R scryner /app
USER scryner
EXPOSE 8080
ENTRYPOINT ["python", "-m", "scryner"]
CMD ["all"]
