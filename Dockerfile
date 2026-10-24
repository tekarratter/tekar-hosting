# ---------- frontend ----------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- trivy (scanner) ----------
FROM aquasec/trivy:0.71.2 AS trivy

# ---------- backend ----------
FROM python:3.12-slim-bookworm AS backend
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg tzdata; \
    install -m 0755 -d /etc/apt/keyrings; \
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg; \
    echo "deb [signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list; \
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /etc/apt/keyrings/pgdg.gpg; \
    echo "deb [signed-by=/etc/apt/keyrings/pgdg.gpg] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin docker-buildx-plugin git postgresql-client-17; \
    apt-get purge -y gnupg; apt-get autoremove -y; rm -rf /var/lib/apt/lists/*; \
    git config --system --add safe.directory '*'  # repo của app thuộc user trên host, git chạy bằng root

COPY --from=trivy /usr/local/bin/trivy /usr/local/bin/trivy
WORKDIR /srv
COPY backend/requirements.txt .
RUN pip install -r requirements.txt
COPY backend/app ./app
ENV TZ=Asia/Ho_Chi_Minh TH_STATIC_DIR=/srv/static
EXPOSE 8000

# ---------- final ----------
FROM backend
COPY --from=web /web/dist ./static
