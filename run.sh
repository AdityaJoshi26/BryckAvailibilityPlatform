#!/usr/bin/env bash
#
# Bryck Availability Platform — one-shot launcher (no Docker)
# Checks/installs dependencies, prepares the database, starts the
# backend (FastAPI) and frontend (static server), then exits.
#
set -euo pipefail

# ── Locate project directory ──────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
RUN_DIR="$SCRIPT_DIR/.run"
VENV_DIR="$BACKEND_DIR/venv"
mkdir -p "$RUN_DIR"

BACKEND_PORT=8000
FRONTEND_PORT=5500

log()  { printf '\033[1;34m[bryck]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[bryck]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[bryck]\033[0m %s\n' "$*" >&2; exit 1; }

# ── sudo helper (no-op if already root) ───────────────────────────
if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  if command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
  else
    SUDO=""
    warn "sudo not found; package installs may fail if not run as root."
  fi
fi

# ── Detect package manager ────────────────────────────────────────
if command -v apt-get >/dev/null 2>&1; then
  PM="apt"
elif command -v dnf >/dev/null 2>&1; then
  PM="dnf"
elif command -v yum >/dev/null 2>&1; then
  PM="yum"
else
  PM="unknown"
fi

pkg_install() {
  # pkg_install <apt-pkgs...> "|" <dnf/yum-pkgs...>
  local apt_pkgs=() rpm_pkgs=() seen_sep=0
  for p in "$@"; do
    if [ "$p" = "|" ]; then seen_sep=1; continue; fi
    if [ "$seen_sep" -eq 0 ]; then apt_pkgs+=("$p"); else rpm_pkgs+=("$p"); fi
  done
  case "$PM" in
    apt) $SUDO apt-get update -y && $SUDO apt-get install -y "${apt_pkgs[@]}" ;;
    dnf) $SUDO dnf install -y "${rpm_pkgs[@]}" ;;
    yum) $SUDO yum install -y "${rpm_pkgs[@]}" ;;
    *)   die "Unsupported package manager. Install manually: ${apt_pkgs[*]}" ;;
  esac
}

# ── 1. Python 3 + venv + pip ──────────────────────────────────────
if ! command -v python3 >/dev/null 2>&1; then
  log "Installing Python 3..."
  pkg_install python3 python3-venv python3-pip "|" python3 python3-pip
else
  log "Python 3 present: $(python3 --version)"
fi

# Ensure the venv module is usable (Debian ships it separately).
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  log "Installing python3-venv..."
  pkg_install python3-venv "|" python3
fi

# ── 2. PostgreSQL ─────────────────────────────────────────────────
if ! command -v psql >/dev/null 2>&1; then
  log "Installing PostgreSQL..."
  pkg_install postgresql postgresql-contrib "|" postgresql-server postgresql-contrib
  # Initialize the cluster on RHEL-family systems (Debian does this automatically).
  if [ "$PM" = "dnf" ] || [ "$PM" = "yum" ]; then
    if command -v postgresql-setup >/dev/null 2>&1; then
      $SUDO postgresql-setup --initdb >/dev/null 2>&1 || true
    fi
  fi
else
  log "PostgreSQL present: $(psql --version)"
fi

# Start the PostgreSQL service.
if command -v systemctl >/dev/null 2>&1; then
  $SUDO systemctl enable postgresql >/dev/null 2>&1 || true
  $SUDO systemctl start  postgresql >/dev/null 2>&1 || true
elif command -v service >/dev/null 2>&1; then
  $SUDO service postgresql start >/dev/null 2>&1 || true
fi

# ── 3. Environment file ───────────────────────────────────────────
ENV_FILE="$BACKEND_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  log "Creating backend/.env with default local settings..."
  echo "DATABASE_URL=postgresql://postgres:postgres@localhost:5432/bryck_db" > "$ENV_FILE"
fi

# Pull DATABASE_URL out of the env file.
DATABASE_URL="$(grep -E '^DATABASE_URL=' "$ENV_FILE" | head -n1 | cut -d= -f2-)"
[ -n "$DATABASE_URL" ] || die "DATABASE_URL missing in backend/.env"

# Parse postgresql://USER:PASS@HOST:PORT/DBNAME
creds="${DATABASE_URL#*://}"            # user:pass@host:port/db
userpass="${creds%%@*}"                 # user:pass
hostpart="${creds#*@}"                  # host:port/db
DB_USER="${userpass%%:*}"
DB_PASS="${userpass#*:}"
DB_NAME="${hostpart##*/}"
hostport="${hostpart%%/*}"              # host:port
DB_HOST="${hostport%%:*}"
DB_PORT="${hostport##*:}"
[ "$DB_PORT" = "$DB_HOST" ] && DB_PORT=5432   # no port supplied

# ── 4. Database + role setup (only for a local server) ────────────
if [ "$DB_HOST" = "localhost" ] || [ "$DB_HOST" = "127.0.0.1" ]; then
  log "Ensuring database role '$DB_USER' and database '$DB_NAME' exist..."
  $SUDO -u postgres psql -v ON_ERROR_STOP=1 <<SQL || warn "DB setup step had warnings (may already exist)."
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_USER}') THEN
    CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';
  ELSE
    ALTER ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASS}';
  END IF;
END
\$\$;
SELECT 'CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${DB_NAME}')\gexec
SQL
else
  log "Remote DB host ($DB_HOST) — skipping local database provisioning."
fi

# ── 5. Python virtual environment + dependencies ──────────────────
if [ ! -d "$VENV_DIR" ]; then
  log "Creating virtual environment..."
  python3 -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

log "Installing Python dependencies..."
WHEELS_DIR="$SCRIPT_DIR/wheels"
if [ -d "$WHEELS_DIR" ] && ls "$WHEELS_DIR"/*.whl >/dev/null 2>&1; then
  # Offline install from pre-downloaded wheels (no internet needed).
  log "Found wheels/ — installing offline (no network required)."
  pip install --quiet --no-index --find-links "$WHEELS_DIR" -r "$BACKEND_DIR/requirements.txt"
else
  # Online install. If this fails, the host likely has no internet.
  if ! pip install --quiet -r "$BACKEND_DIR/requirements.txt"; then
    die "pip could not reach PyPI (offline host?).
   Fix: on an internet-connected machine with the SAME OS/Python/arch run:
       pip download -r backend/requirements.txt -d wheels/
   then copy the 'wheels/' folder into $SCRIPT_DIR and rerun ./run.sh
   Or set HTTP_PROXY/HTTPS_PROXY if behind a corporate proxy."
  fi
fi

# ── 6. Start backend ──────────────────────────────────────────────
if curl -s "http://localhost:${BACKEND_PORT}/docs" >/dev/null 2>&1; then
  warn "Backend already responding on :${BACKEND_PORT}, not starting a second one."
else
  log "Starting backend on :${BACKEND_PORT}..."
  ( cd "$BACKEND_DIR" && nohup "$VENV_DIR/bin/uvicorn" main:app \
      --host 0.0.0.0 --port "$BACKEND_PORT" \
      > "$RUN_DIR/backend.log" 2>&1 & echo $! > "$RUN_DIR/backend.pid" )
fi

# ── 7. Start frontend (static server) ─────────────────────────────
if curl -s "http://localhost:${FRONTEND_PORT}" >/dev/null 2>&1; then
  warn "Frontend already responding on :${FRONTEND_PORT}, not starting a second one."
else
  log "Starting frontend on :${FRONTEND_PORT}..."
  ( cd "$FRONTEND_DIR" && nohup python3 -m http.server "$FRONTEND_PORT" \
      > "$RUN_DIR/frontend.log" 2>&1 & echo $! > "$RUN_DIR/frontend.pid" )
fi

deactivate || true

# ── 8. ngrok tunnel (exposes backend for Slack slash commands) ────
NGROK_API="http://127.0.0.1:4040/api/tunnels"
NGROK_URL=""

get_ngrok_url() {
  curl -s "$NGROK_API" 2>/dev/null | python3 -c \
    "import sys, json
d = json.load(sys.stdin)
urls = [t['public_url'] for t in d.get('tunnels', []) if t['public_url'].startswith('https')]
print(urls[0] if urls else '')" 2>/dev/null
}

if ! command -v ngrok >/dev/null 2>&1; then
  warn "ngrok not installed — skipping tunnel. Install: https://ngrok.com/download"
elif pgrep -f "ngrok http" >/dev/null 2>&1; then
  NGROK_URL="$(get_ngrok_url)"
  if [ -n "$NGROK_URL" ]; then
    log "ngrok already running — public URL: $NGROK_URL"
  else
    warn "ngrok already running, but no tunnel URL found yet (check $RUN_DIR/ngrok.log)."
  fi
else
  log "Starting ngrok tunnel on :${BACKEND_PORT}..."
  ( nohup ngrok http "$BACKEND_PORT" --log=stdout > "$RUN_DIR/ngrok.log" 2>&1 & echo $! > "$RUN_DIR/ngrok.pid" )
  for _ in $(seq 1 20); do
    NGROK_URL="$(get_ngrok_url)"
    [ -n "$NGROK_URL" ] && break
    sleep 0.5
  done
  if [ -n "$NGROK_URL" ]; then
    log "ngrok tunnel ready — public URL: $NGROK_URL"
  else
    warn "ngrok started but no tunnel URL detected yet. Check $RUN_DIR/ngrok.log"
  fi
fi

# ── Summary ───────────────────────────────────────────────────────
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
log "All set. Services are running in the background:"
echo "   Backend  → http://localhost:${BACKEND_PORT}   (docs: /docs)"
echo "   Frontend → http://localhost:${FRONTEND_PORT}"
[ -n "${IP:-}" ] && echo "   LAN access → http://${IP}:${FRONTEND_PORT}"
[ -n "$NGROK_URL" ] && echo "   ngrok     → $NGROK_URL"
echo
echo "   Logs:  $RUN_DIR/backend.log , $RUN_DIR/frontend.log , $RUN_DIR/ngrok.log"
echo "   Stop:  kill \$(cat $RUN_DIR/backend.pid $RUN_DIR/frontend.pid $RUN_DIR/ngrok.pid 2>/dev/null)"
echo

