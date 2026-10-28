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

# Python 3.11+, except on Intel Macs: PyTorch's last Intel Mac release (2.2)
# only exists for Python 3.11 and 3.12. Judged by the architecture Python
# runs as, so a Python under Rosetta counts as Intel too.
PY_CHECK='import sys, platform
v = sys.version_info[:2]
intel = sys.platform == "darwin" and platform.machine() == "x86_64"
sys.exit(0 if v >= (3, 11) and (not intel or v <= (3, 12)) else 1)'

suitable() { command -v "$1" >/dev/null 2>&1 && "$1" -c "$PY_CHECK" 2>/dev/null; }

pick_python() {
  for py in python3.12 python3.11 python3.13 python3.14 python3; do
    if suitable "$py"; then
      command -v "$py"
      return 0
    fi
  done
  return 1
}

# Fetch a private Python 3.12 with uv (used when none suitable is installed,
# typically an Intel Mac with Python 3.13+). Needs any Python 3 to bootstrap.
fetch_python() {
  local boot
  boot=$(command -v python3) || return 1
  echo "Fetching Python 3.12 for TaalLoop (one time, about 30 MB)..." >&2
  { [ -x .uv-tool/bin/uv ] || { "$boot" -m venv .uv-tool && .uv-tool/bin/python -m pip install --quiet uv==0.13.0; }; } >&2 &&
    .uv-tool/bin/uv python install 3.12 >&2 &&
    .uv-tool/bin/uv python find 3.12
}

# A .venv made by an unsuitable Python (e.g. 3.13 on an Intel Mac) can never
# install TaalLoop; start it over.
if [ -x .venv/bin/python ] && ! suitable .venv/bin/python; then
  echo "Rebuilding TaalLoop's environment with a compatible Python..."
  rm -rf .venv
fi

if [ ! -x .venv/bin/python ]; then
  if ! PY=$(pick_python) && ! PY=$(fetch_python); then
    echo "TaalLoop needs Python 3.11 or newer. Opening the download page..."
    echo "Install Python, then double-click this file again."
    open "https://www.python.org/downloads/"
    finish 1
  fi
  echo "First run: setting up TaalLoop with $("$PY" --version). This takes a few minutes..."
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

# The address other devices on the Wi-Fi use: ask macOS which interface carries
# the default route (Wi-Fi isn't always en0), then fall back to the usual ones.
IFACE=$(route -n get default 2>/dev/null | awk '/interface:/ {print $2}')
LAN_IP=$({ [ -n "$IFACE" ] && ipconfig getifaddr "$IFACE"; } 2>/dev/null ||
         ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
echo
echo "TaalLoop is starting..."
echo "  On this Mac:                http://localhost:$PORT"
if [ -n "$LAN_IP" ]; then
  echo "  On your iPad (same Wi-Fi):  http://$LAN_IP:$PORT"
  echo "  (If the iPad can't connect, allow Python in System Settings > Network > Firewall.)"
else
  echo "  On your iPad: http://<this Mac's IP address>:$PORT"
  echo "  (Find the IP address in System Settings > Wi-Fi > Details.)"
fi
echo
echo "Keep this window open while you use TaalLoop. Close it to stop TaalLoop."
echo
(sleep 3 && open "http://localhost:$PORT") &
exec .venv/bin/python -m uvicorn taalloop.main:app --host 0.0.0.0 --port "$PORT"
