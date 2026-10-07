# ─────────────────────────────────────────────────────────────
# All-in-one image: PostgreSQL + FastAPI backend + nginx frontend
# Managed by supervisor in a single container.
# NOT ideal for production (use docker-compose.yml for that),
# but convenient for shipping the whole app as one unit.
# ─────────────────────────────────────────────────────────────
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POSTGRES_USER=postgres \
    POSTGRES_PASSWORD=postgres \
    POSTGRES_DB=bryck_db \
    PGDATA=/var/lib/postgresql/data \
    DATABASE_URL=postgresql://postgres:postgres@localhost:5432/bryck_db

# System deps: postgres, nginx, supervisor, gosu (drop privileges for postgres)
RUN apt-get update && apt-get install -y --no-install-recommends \
        postgresql postgresql-contrib \
        nginx \
        supervisor \
        gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code + frontend
COPY backend/ /app/
COPY frontend/index.html /var/www/html/index.html

# ── nginx: serve the single-page frontend on port 80 ──────────
RUN printf 'server {\n\
    listen 80 default_server;\n\
    root /var/www/html;\n\
    index index.html;\n\
    location / { try_files $uri /index.html; }\n\
}\n' > /etc/nginx/sites-available/default

# ── supervisor: run postgres, backend and nginx together ──────
RUN cat <<'EOF' > /etc/supervisor/conf.d/app.conf
[supervisord]
nodaemon=true
user=root

[program:postgres]
command=/bin/bash -lc 'exec gosu postgres "$(ls -d /usr/lib/postgresql/*/bin | head -n1)/postgres" -D "$PGDATA"'
autorestart=true
priority=10
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0

[program:backend]
directory=/app
command=uvicorn main:app --host 0.0.0.0 --port 8000
autorestart=true
startretries=20
priority=20
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0

[program:nginx]
command=nginx -g 'daemon off;'
autorestart=true
priority=30
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0
EOF

# ── entrypoint: first-run initdb + create user/db, then supervisor ──
RUN cat <<'EOF' > /usr/local/bin/entrypoint.sh
#!/bin/bash
set -e

PG_BIN="$(ls -d /usr/lib/postgresql/*/bin | head -n1)"
export PATH="$PG_BIN:$PATH"

mkdir -p "$PGDATA"
chown -R postgres:postgres "$PGDATA"
chmod 700 "$PGDATA"

# Initialize the database cluster only on first run (empty volume)
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  echo "[entrypoint] Initializing PostgreSQL data directory..."
  gosu postgres initdb -D "$PGDATA"
  echo "host all all 0.0.0.0/0 md5" >> "$PGDATA/pg_hba.conf"
  echo "listen_addresses='localhost'" >> "$PGDATA/postgresql.conf"

  gosu postgres pg_ctl -D "$PGDATA" -o "-c listen_addresses='localhost'" -w start
  gosu postgres psql -v ON_ERROR_STOP=1 --username postgres -c \
      "ALTER USER postgres WITH PASSWORD '${POSTGRES_PASSWORD}';"
  if [ "${POSTGRES_DB}" != "postgres" ]; then
    gosu postgres psql -v ON_ERROR_STOP=1 --username postgres -tc \
        "SELECT 1 FROM pg_database WHERE datname='${POSTGRES_DB}'" | grep -q 1 \
        || gosu postgres createdb -O postgres "${POSTGRES_DB}"
  fi
  gosu postgres pg_ctl -D "$PGDATA" -w stop
  echo "[entrypoint] Database initialized."
fi

exec /usr/bin/supervisord -n -c /etc/supervisor/supervisord.conf
EOF

# Normalize line endings (Dockerfile heredocs may carry CRLF on Windows) + make executable
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh /etc/supervisor/conf.d/app.conf /etc/nginx/sites-available/default \
    && chmod +x /usr/local/bin/entrypoint.sh

# Persist database data
VOLUME ["/var/lib/postgresql/data"]

# 80 = frontend (nginx), 8000 = backend API
EXPOSE 80 8000

CMD ["/usr/local/bin/entrypoint.sh"]
