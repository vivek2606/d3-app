#!/bin/bash
# Double-click to start TaalLoop on a Mac.
# First run: creates a private Python environment in .venv and installs the
# app (a few minutes). Later runs start straight away.

cd "$(dirname "$0")" || exit 1
PORT=8000

finish() {  # keep the Terminal window open long enough to read why
  read -n 1 -s -r -p "Press any key to close this window."
  echo
  exit "${1:-0}"
}

if curl -s -o /dev/null "http://localhost:$PORT/api/config"; then
  echo "TaalLoop is already running - opening it in your browser."
  open "http://localhost:$PORT"
  exit 0
fi

pick_python() {
  for py in python3.13 python3.12 python3.11 python3; do
    if command -v "$py" >/dev/null 2>&1 &&
       "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      echo "$py"
      return 0
    fi
  done
  return 1
}

if [ ! -x .venv/bin/python ]; then
  if ! PY=$(pick_python); then
    echo "TaalLoop needs Python 3.11 or newer. Opening the download page..."
    echo "Install Python, then double-click this file again."
    open "https://www.python.org/downloads/"
    finish 1
  fi
  echo "First run: setting up TaalLoop. This takes a few minutes..."
  "$PY" -m venv .venv || finish 1
fi

# Reinstall only when the requirements changed (first run, or after an update).
REQS="$(cat requirements.txt requirements-demucs.txt)"
if [ "$REQS" != "$(cat .venv/installed-requirements.txt 2>/dev/null)" ]; then
  echo "Installing TaalLoop's components (about 1 GB to download)..."
  if ! .venv/bin/python -m pip install --upgrade pip --quiet ||
     ! .venv/bin/python -m pip install -r requirements-demucs.txt; then
    echo
    echo "The installation failed - see the messages above."
    finish 1
  fi
  printf '%s' "$REQS" > .venv/installed-requirements.txt
fi

# python.org's Mac Python has no CA certificates until "Install Certificates"
# is run, which breaks Demucs's one-time model download; use certifi's bundle.
CERTS=$(.venv/bin/python -c 'import certifi; print(certifi.where())' 2>/dev/null) && export SSL_CERT_FILE="$CERTS"

LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
echo
echo "TaalLoop is starting..."
echo "  On this Mac:                http://localhost:$PORT"
if [ -n "$LAN_IP" ]; then
  echo "  On your iPad (same Wi-Fi):  http://$LAN_IP:$PORT"
fi
echo
echo "Keep this window open while you use TaalLoop. Close it to stop TaalLoop."
echo
(sleep 3 && open "http://localhost:$PORT") &
exec .venv/bin/python -m uvicorn taalloop.main:app --host 0.0.0.0 --port "$PORT"
