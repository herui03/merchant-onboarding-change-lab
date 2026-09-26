#!/usr/bin/env bash
# Merchant Onboarding Policy Change Lab — local launcher.
# macOS: double-click this file. Any OS with bash: ./launch_demo.command
#
#   ./launch_demo.command               start (keeps the existing database; seeds only if missing)
#   ./launch_demo.command --reset       back up the database to instance/backups/, reseed, start
#   ./launch_demo.command --check-only  run the checks and database preparation, then exit
#
# It never installs anything. If something is missing it prints the exact commands to run.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

PORT=5058
URL="http://127.0.0.1:${PORT}/"
INSTANCE_DIR="${MOBLAB_INSTANCE_DIR:-$HERE/instance}"
DB="$INSTANCE_DIR/merchant_onboarding_lab.db"
RESET=0
CHECK_ONLY=0

for arg in "$@"; do
  case "$arg" in
    --reset) RESET=1 ;;
    --check-only) CHECK_ONLY=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg (use --reset, --check-only or --help)"; exit 2 ;;
  esac
done

fallback() {
  cat <<EOF

If double-clicking does not work, open Terminal and run exactly:
  cd "$HERE"
  ./launch_demo.command
Or start the server directly:
  cd "$HERE" && .venv/bin/python -m moblab run --db "$DB"
EOF
}

setup_help() {
  cat <<EOF

Set up once (nothing is installed automatically):
  cd "$HERE"
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
  ./launch_demo.command
Python 3.11 or newer is required (python.org or your package manager).
EOF
}

echo "Merchant Onboarding Policy Change Lab (synthetic, local only)"
echo "Folder: $HERE"

# 1. Pick an interpreter: explicit override, then the project venv, then python3 on PATH.
PY="${MOBLAB_PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x "$HERE/.venv/bin/python" ]; then PY="$HERE/.venv/bin/python"; else PY="$(command -v python3 || true)"; fi
fi
if [ -z "$PY" ] || ! "$PY" -c 'import sys' >/dev/null 2>&1; then
  echo "ERROR: no usable Python interpreter found."
  setup_help; fallback; exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
  echo "ERROR: $PY is Python $("$PY" -c 'import platform; print(platform.python_version())'); 3.11+ is required."
  setup_help; fallback; exit 1
fi
if ! "$PY" -c 'import flask' >/dev/null 2>&1; then
  echo "ERROR: Flask is not installed for $PY."
  setup_help; fallback; exit 1
fi
echo "Python: $PY ($("$PY" -c 'import platform; print(platform.python_version())')), Flask $("$PY" -c 'import flask, importlib.metadata as m; print(m.version("flask"))')"

port_busy() {
  "$PY" -c "import socket,sys; s=socket.socket(); s.settimeout(0.5); sys.exit(0 if s.connect_ex(('127.0.0.1', $PORT)) == 0 else 1)"
}

# 2. Busy-port guard BEFORE any database change (DEF-004): if the lab (or anything else) already
#    answers on 5058, stop here without resetting or seeding. `moblab reset` additionally refuses
#    while any server holds this database's run lock, whatever port it uses.
if [ "$CHECK_ONLY" -eq 0 ] || [ "$RESET" -eq 1 ]; then
  if port_busy; then
    echo "ERROR: 127.0.0.1:$PORT is already in use. Is the lab already running? Try $URL"
    echo "Nothing was changed: the database was not reset or seeded. Stop the other process and run this again."
    fallback; exit 1
  fi
fi

# 3. Database: reset only on request (with backup); otherwise preserve, seeding only if missing.
if [ "$RESET" -eq 1 ]; then
  echo "Reset requested: backing up and reseeding."
  "$PY" -m moblab reset --db "$DB" --yes
elif [ -f "$DB" ]; then
  echo "Using existing database (preserved): $DB"
else
  echo "No database yet; creating the synthetic seed: $DB"
  "$PY" -m moblab seed --db "$DB"
fi
"$PY" -m moblab check --db "$DB"

if [ "$CHECK_ONLY" -eq 1 ]; then
  echo "Check-only mode: not starting the server."
  exit 0
fi

# 4. Start (another portfolio app may use 5057/5059; this lab always uses 5058).
echo "Starting on $URL  (Ctrl+C to stop)"
if [ "$(uname -s)" = "Darwin" ] && [ -t 1 ]; then
  ( sleep 1.5; open "$URL" >/dev/null 2>&1 || true ) &
fi
exec "$PY" -m moblab run --db "$DB" --port "$PORT"
