# syntax=docker/dockerfile:1

FROM node:22-alpine AS frontend
WORKDIR /build/frontend
RUN npm install -g pnpm@9.15.4
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 panbox \
    && useradd --uid 1000 --gid 1000 --create-home panbox

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./
COPY backend ./backend
COPY --from=frontend /build/frontend/dist ./frontend/dist

RUN mkdir -p /app/data && chown -R panbox:panbox /app

USER panbox
EXPOSE 8088

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/api/health', timeout=3)"

CMD ["python", "app.py", "serve", "--host", "0.0.0.0", "--port", "8088"]
