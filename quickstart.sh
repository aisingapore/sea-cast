#!/usr/bin/env bash
# SEA-CAST quickstart: run the whole stack locally in one terminal.
#   whisper.cpp (speech-to-text) + Ollama (translation) + backend + frontend.
#
# The models come from backend/sea-cast.yaml, which the first run creates from
# backend/sea-cast.example.yaml:
#   - whisper.cpp runs for the STT profile at http://localhost:$WHISPER_PORT, with
#     that profile's model (e.g. large-v3-turbo-q5_0). The script offers to
#     download the model if you don't have it.
#   - Ollama pulls the model of every translator profile at http://localhost:11434.
# Profiles that point elsewhere (hosted APIs, other servers) need neither, and a
# config with no local profiles starts only SEA-CAST itself.
#
# The first run also installs the backend and frontend dependencies. Later runs
# reuse all of that. Override a setting below inline, e.g.:
#   WHISPER_MODEL_FILE=/path/to/ggml-model.bin ./quickstart.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$REPO/backend"
FRONTEND="$REPO/frontend"

# --- Settings -----------------------------------------------------------------
# Where downloaded whisper.cpp models go. Set WHISPER_MODEL_FILE to use a model
# file you already have instead of the profile's model.
WHISPER_MODELS_DIR="${WHISPER_MODELS_DIR:-$BACKEND/data/models}"
WHISPER_MODEL_FILE="${WHISPER_MODEL_FILE:-}"
WHISPER_BIN="${WHISPER_BIN:-whisper-server}"
WHISPER_PORT="${WHISPER_PORT:-8000}"
BACKEND_PORT="${BACKEND_PORT:-8080}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
# ------------------------------------------------------------------------------

say() { echo "==> $*"; }
fail() { echo "error: $*" >&2; exit 1; }

# Wait until a URL answers (any HTTP status), or give up after N seconds. Fails
# straight away if the process (PID) that should answer exits first, e.g. on a
# config error, instead of waiting out the timeout.
wait_for() {
  local name=$1 url=$2 secs=$3 pid=$4
  say "waiting for $name (up to ${secs}s)"
  for _ in $(seq "$secs"); do
    curl -s -o /dev/null "$url" && return 0
    kill -0 "$pid" 2>/dev/null || fail "$name exited before it was ready; see its output above."
    sleep 1
  done
  fail "$name did not start within ${secs}s ($url)"
}

# Fail if something already listens on a port we're about to use.
port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
need_free_port() {
  local name=$1 port=$2 hint=$3
  if port_in_use "$port"; then
    fail "port $port, which $name needs, is already in use (is SEA-CAST or another server still running?). Stop it, or $hint."
  fi
}

missing=0
need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing: $1. $2" >&2
    missing=1
  fi
}
check_needs() {
  [ "$missing" = 0 ] || fail "install the missing tools above, then run this again."
}

# 1. Prerequisites for SEA-CAST itself ----------------------------------------------
need curl "Install curl with your package manager."
need uv "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
need npm "Install Node.js 20 or later: https://nodejs.org/"
check_needs

# 2. Config and dependencies --------------------------------------------------------
if [ ! -f "$BACKEND/sea-cast.yaml" ]; then
  cp "$BACKEND/sea-cast.example.yaml" "$BACKEND/sea-cast.yaml"
  say "created backend/sea-cast.yaml (local whisper.cpp + Ollama); edit it to change models or add providers"
fi
say "installing backend dependencies"
(cd "$BACKEND" && uv sync --quiet)
if [ ! -d "$FRONTEND/node_modules" ]; then
  say "installing frontend dependencies"
  (cd "$FRONTEND" && npm ci --no-audit --no-fund)
fi

# Which local services the config uses (sets WHISPER_PROFILE_MODEL and
# OLLAMA_MODELS). This loads the config as the backend does, so a broken
# sea-cast.yaml or a missing API key stops here with the backend's message.
services="$(cd "$BACKEND" && uv run --quiet python -m scripts.local_services \
  --whisper-port "$WHISPER_PORT")" || fail "fix backend/sea-cast.yaml (see above), then run this again."
eval "$services"

# 3. Prerequisites for the local services -------------------------------------------
if [ -n "$WHISPER_PROFILE_MODEL" ]; then
  case "$(uname -s)" in
    Darwin) whisper_hint="Install whisper.cpp: brew install whisper-cpp" ;;
    Linux) whisper_hint="Build whisper.cpp (https://github.com/ggml-org/whisper.cpp#quick-start):
    git clone https://github.com/ggml-org/whisper.cpp && cd whisper.cpp
    cmake -B build && cmake --build build -j --config Release   # add -DGGML_CUDA=1 for an NVIDIA GPU
  then put build/bin/whisper-server on your PATH, or run with WHISPER_BIN=/path/to/whisper-server" ;;
    *) whisper_hint="Install whisper.cpp: https://github.com/ggml-org/whisper.cpp (on Windows, run this script inside WSL)" ;;
  esac
  need "$WHISPER_BIN" "$whisper_hint"
fi
if [ -n "$OLLAMA_MODELS" ]; then
  need ollama "Install Ollama: https://ollama.com/download"
fi
check_needs

# 4. Whisper model ------------------------------------------------------------------
if [ -n "$WHISPER_PROFILE_MODEL" ]; then
  WHISPER_MODEL_FILE="${WHISPER_MODEL_FILE:-$WHISPER_MODELS_DIR/ggml-$WHISPER_PROFILE_MODEL.bin}"
  if [ ! -f "$WHISPER_MODEL_FILE" ]; then
    url="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$WHISPER_PROFILE_MODEL.bin"
    say "Whisper model not found: $WHISPER_MODEL_FILE"
    read -r -p "    Download ggml-$WHISPER_PROFILE_MODEL.bin from Hugging Face (large-v3-turbo-q5_0 is about 550 MB)? [y/N] " answer || answer=""
    case "$answer" in
      [yY]*) ;;
      *) fail "no model. Download one, or set WHISPER_MODEL_FILE to a model you have." ;;
    esac
    mkdir -p "$(dirname "$WHISPER_MODEL_FILE")"
    if ! curl -fL --progress-bar -o "$WHISPER_MODEL_FILE.part" "$url"; then
      rm -f "$WHISPER_MODEL_FILE.part"
      fail "couldn't download $url. Is '$WHISPER_PROFILE_MODEL' (the whisper.cpp profile's model in backend/sea-cast.yaml) a name from https://huggingface.co/ggerganov/whisper.cpp/tree/main, without \"ggml-\" and \".bin\"?"
    fi
    mv "$WHISPER_MODEL_FILE.part" "$WHISPER_MODEL_FILE"
  fi
fi

# The frontend (on its own port) calls the backend directly, so the backend must
# allow its origin. Respect a CORS_ORIGINS the user already set in backend/.env.
if ! grep -qs '^CORS_ORIGINS=' "$BACKEND/.env"; then
  export CORS_ORIGINS="[\"http://localhost:$FRONTEND_PORT\",\"http://127.0.0.1:$FRONTEND_PORT\"]"
fi
# The frontend assumes the backend is on :8080 in dev; tell it otherwise if not.
if [ "$BACKEND_PORT" != 8080 ]; then
  export VITE_SERVER_URL="http://localhost:$BACKEND_PORT"
fi

# 5. Start everything ------------------------------------------------------------------
# Check the ports first, so nothing starts if one is taken. (A running Ollama is
# reused instead, below.)
if [ -n "$WHISPER_PROFILE_MODEL" ]; then
  need_free_port "whisper.cpp" "$WHISPER_PORT" \
    "run with WHISPER_PORT=<port> and change the port in the whisper.cpp profile's base_url to match"
fi
need_free_port "the backend" "$BACKEND_PORT" "run with BACKEND_PORT=<port>"
need_free_port "the frontend" "$FRONTEND_PORT" "run with FRONTEND_PORT=<port>"

# From here on, stop every process this script started on exit or Ctrl-C.
trap 'echo; say "stopping SEA-CAST"; kill 0' EXIT INT TERM

if [ -n "$OLLAMA_MODELS" ]; then
  if curl -sf http://localhost:11434/api/version >/dev/null 2>&1; then
    say "ollama:   already running on :11434 (reusing it)"
  else
    say "ollama:   starting"
    ollama serve >/dev/null 2>&1 &
    wait_for "Ollama" http://localhost:11434/api/version 30 $!
  fi
  for model in $OLLAMA_MODELS; do
    # `ollama list` adds ":latest" to a model named without a tag.
    if ! ollama list | awk 'NR > 1 { print $1 }' | grep -qxF -e "$model" -e "$model:latest"; then
      say "ollama:   pulling $model (first run only)"
      ollama pull "$model"
    fi
  done
else
  say "ollama:   no translator profile uses it; not starting it"
fi

if [ -n "$WHISPER_PROFILE_MODEL" ]; then
  say "whisper:  starting on :$WHISPER_PORT with $(basename "$WHISPER_MODEL_FILE")"
  "$WHISPER_BIN" -m "$WHISPER_MODEL_FILE" --host 127.0.0.1 --port "$WHISPER_PORT" \
    --inference-path /v1/audio/transcriptions -l auto &
  whisper_pid=$!
else
  say "whisper:  no speech-to-text profile at localhost:$WHISPER_PORT; not starting it"
fi

say "backend:  starting on :$BACKEND_PORT"
(cd "$BACKEND" && exec uv run uvicorn app.server:app --factory \
  --host 0.0.0.0 --port "$BACKEND_PORT") &
backend_pid=$!

say "frontend: starting on :$FRONTEND_PORT"
# Pin the host: Vite's default, "localhost", can resolve to IPv6 only (::1), which
# the IPv4 checks here (port_in_use, wait_for) wouldn't see. Browsers still reach
# it at http://localhost.
(cd "$FRONTEND" && exec npm run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT" --strictPort) &
frontend_pid=$!

if [ -n "$WHISPER_PROFILE_MODEL" ]; then
  wait_for "whisper.cpp" "http://127.0.0.1:$WHISPER_PORT/" 120 "$whisper_pid"
fi
wait_for "the backend" "http://127.0.0.1:$BACKEND_PORT/health" 120 "$backend_pid"
wait_for "the frontend" "http://127.0.0.1:$FRONTEND_PORT/" 60 "$frontend_pid"

echo
say "SEA-CAST is running. Open the speaker dashboard: http://localhost:$FRONTEND_PORT/"
say "Ctrl-C stops everything."
wait
