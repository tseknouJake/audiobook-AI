#!/usr/bin/env bash
# Start Audiobook AI.
#
# Checks this computer has everything the app needs, installs what's missing
# (asking first), helps you add a free Gemini key, then opens the app in your
# browser. Run it the same way every time: ./run.sh
# (or double-click "Start Audiobook AI.command" in Finder).
#
# Nothing needs installing beforehand. If uv (the tool that installs Python
# and the packages) isn't on this Mac yet, it's put inside this folder, in
# .tools, along with Python, the downloads and the voice model, and nothing
# else on the Mac is changed. So to uninstall, drag this folder to the Trash.
# (If you already use uv, your existing setup and caches are used instead.)

set -euo pipefail
cd "$(dirname "$0")"

PORT="${AUDIOBOOK_PORT:-8000}"
export UV_FROZEN=1  # install exactly the versions in uv.lock
# Hide the packages' internal warnings: harmless, but they look like errors.
export PYTHONWARNINGS="ignore" HF_HUB_VERBOSITY="error" HF_HUB_DISABLE_TELEMETRY=1 ORT_DISABLE_TELEMETRY=1

if [ -t 1 ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'; RESET=$'\033[0m'
else
  BOLD=""; DIM=""; RED=""; GREEN=""; RESET=""
fi
step() { printf "\n%s%s%s\n" "$BOLD" "$*" "$RESET"; }
note() { printf "  %s\n" "$*"; }
fail() { printf "\n%s%s%s\n\n" "$RED" "$*" "$RESET" >&2; exit 1; }
ask() {  # ask "Question?" -> true for yes (the default)
  local answer
  read -r -p "  $1 [Y/n] " answer || return 1
  [[ -z "$answer" || "$answer" =~ ^[Yy] ]]
}
open_url() {
  if command -v open >/dev/null 2>&1; then open "$1"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$1" >/dev/null 2>&1 || true
  fi
}

printf "%sAudiobook AI%s\n" "$BOLD" "$RESET"

# ---------- 1. Is this a computer it runs on? ----------
case "$(uname -s)" in
  Darwin)
    if [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = "1" ]; then
      fail "Terminal is running in Intel mode (Rosetta). Quit Terminal, find it in Applications › Utilities, choose File › Get Info, untick \"Open using Rosetta\", then run this again."
    fi
    if [ "$(uname -m)" != "arm64" ]; then
      fail "Sorry, this needs a Mac with Apple silicon (M1 or newer). The voice software isn't available for Intel Macs. (Apple menu › About This Mac shows which chip you have.)"
    fi
    ;;
  Linux)
    note "${DIM}Linux: this should work, but it's only been tested on Macs.${RESET}"
    ;;
  *)
    fail "Sorry, this only runs on a Mac (or Linux)."
    ;;
esac

# ---------- 2. uv: installs Python and the app's packages ----------
TOOLS="$PWD/.tools"  # everything this script installs lives here
UV=""
if [ -x "$TOOLS/bin/uv" ]; then
  UV="$TOOLS/bin/uv"
else
  UV="$(command -v uv || true)"
  for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
    [ -z "$UV" ] && [ -x "$candidate" ] && UV="$candidate"
  done
fi
if [ -z "$UV" ]; then
  step "First, a one-off install"
  note "The app needs a small free tool called uv (from astral.sh) to set up Python and"
  note "its packages. It all goes inside this folder and changes nothing else on your Mac,"
  note "so to uninstall later you just drag this folder to the Trash. No password needed."
  ask "Install it now?" || fail "Stopped. Run ./run.sh again when you're ready."
  command -v curl >/dev/null 2>&1 || fail "Can't download it: the 'curl' command is missing."
  curl -LsSf https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL="$TOOLS/bin" sh \
    || fail "Installing uv didn't work. Check your internet connection and try again."
  UV="$TOOLS/bin/uv"
  [ -x "$UV" ] || fail "uv was installed but can't be found at $UV. Run ./run.sh again."
fi
if [ "$UV" = "$TOOLS/bin/uv" ]; then
  # Keep Python, the download cache, the voice model and the rest in .tools too.
  export UV_CACHE_DIR="$TOOLS/uv-cache" UV_PYTHON_INSTALL_DIR="$TOOLS/python" \
         UV_PYTHON_BIN_DIR="$TOOLS/bin" HF_HOME="$TOOLS/huggingface" AUDIOBOOK_CACHE="$TOOLS/cache"
fi

# ---------- 3. The app's packages ----------
step "Checking the app's packages"
if [ ! -d .venv ]; then
  note "First run: downloading Python and about 1 GB of packages. This takes a few minutes."
fi
"$UV" sync --quiet || fail "Installing the packages didn't work. Check your internet connection and run ./run.sh again."
note "${GREEN}Ready.${RESET}"

AUDIOBOOK=(".venv/bin/audiobook")

# ---------- 4. Gemini key (free) ----------
step "Gemini key"
if [ -t 0 ]; then "${AUDIOBOOK[@]}" setup; else "${AUDIOBOOK[@]}" setup --no-input; fi

# ---------- 5. Voices: downloaded once (about 330 MB) ----------
if ! ls voice_samples/*.mp3 >/dev/null 2>&1; then
  step "Getting the voices ready (first run only, about 330 MB)"
  "${AUDIOBOOK[@]}" voices >/dev/null 2>&1 || fail "Downloading the voices didn't work. Check your internet connection and run ./run.sh again."
  note "${GREEN}Ready.${RESET}"
fi

# ---------- 6. Start it ----------
if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/api/status" >/dev/null 2>&1; then
  step "Audiobook AI is already running"
  note "Opening it in your browser: http://127.0.0.1:$PORT"
  open_url "http://127.0.0.1:$PORT"
  exit 0
fi
port_in_use() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
while port_in_use "$PORT"; do
  PORT=$((PORT + 1))
  [ "$PORT" -gt 8020 ] || continue
  fail "Couldn't find a free port to run on (tried 8000 to 8020)."
done

URL="http://127.0.0.1:$PORT"
step "Starting Audiobook AI"
note "It opens in your browser at $URL"
note "${BOLD}Keep this window open while you use it.${RESET} To stop it, press Ctrl+C or close the window."
note "${DIM}Your readings are saved in the library folder here, so nothing is lost when you stop it.${RESET}"

# Open the browser as soon as the app answers.
(
  for _ in $(seq 1 90); do
    if curl -fsS --max-time 1 "$URL/api/status" >/dev/null 2>&1; then open_url "$URL"; exit 0; fi
    sleep 1
  done
) &

exec "${AUDIOBOOK[@]}" serve --port "$PORT"
