#!/usr/bin/env bash
set -e

echo "========================================"
echo "  XBRL Agent — Web UI"
echo "========================================"
echo ""

# Find a Python >= 3.10 (pydantic-ai 1.77+ requires it)
PYTHON_BIN=""
for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" &> /dev/null; then
        ver=$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
        major=${ver%.*}
        minor=${ver#*.}
        if [ "$major" -eq 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON_BIN=$(command -v "$candidate")
            echo "Using Python $ver at $PYTHON_BIN"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "ERROR: Python 3.10+ is required (pydantic-ai 1.77+ dependency)."
    echo "Install with: brew install python@3.12"
    exit 1
fi

# Create venv if needed (or if existing venv has wrong Python version)
if [ -d "venv" ]; then
    existing_ver=$(venv/bin/python -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "0.0")
    existing_major=${existing_ver%.*}
    existing_minor=${existing_ver#*.}
    if [ "$existing_major" != "3" ] || [ "$existing_minor" -lt 10 ]; then
        echo "Existing venv uses Python $existing_ver — recreating with $PYTHON_BIN..."
        rm -rf venv
    fi
fi

if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    "$PYTHON_BIN" -m venv venv
fi

# Activate venv
source venv/bin/activate

# Install Python deps
echo "Installing Python dependencies..."
pip install -r requirements.txt -q

# Check Node.js
if command -v node &> /dev/null; then
    if [ -f "web/package.json" ]; then
        echo "Installing frontend dependencies..."
        cd web
        npm install
        echo "Building frontend..."
        npm run build
        cd ..
    fi
else
    echo "WARNING: Node.js not found. Frontend will not be built."
    echo "Install Node.js 18+ from https://nodejs.org/"
fi

# --- Local LiteLLM proxy (simulates enterprise proxy) ---
LITELLM_PORT=4000
LITELLM_URL="http://localhost:${LITELLM_PORT}"

echo ""
echo "Starting local LiteLLM proxy on ${LITELLM_URL}..."

# Kill any leftover proxy from a previous run
if lsof -ti :${LITELLM_PORT} &>/dev/null; then
    echo "  Stopping existing process on port ${LITELLM_PORT}..."
    kill $(lsof -ti :${LITELLM_PORT}) 2>/dev/null || true
    sleep 1
fi

# Launch proxy in background — logs go to litellm.log
litellm --config litellm_config.yaml --port ${LITELLM_PORT} \
    2>&1 > litellm.log &
LITELLM_PID=$!

# Wait for proxy to be ready (up to 15 seconds)
echo -n "  Waiting for proxy"
for i in $(seq 1 15); do
    if curl -s -H "Authorization: Bearer sk-local-dev-key" "${LITELLM_URL}/health" >/dev/null 2>&1; then
        echo " ready!"
        break
    fi
    echo -n "."
    sleep 1
done

if ! curl -s -H "Authorization: Bearer sk-local-dev-key" "${LITELLM_URL}/health" >/dev/null 2>&1; then
    echo ""
    echo "WARNING: LiteLLM proxy didn't start. Check litellm.log"
    echo "Falling back to direct API mode (no proxy)."
    kill $LITELLM_PID 2>/dev/null || true
    LITELLM_PID=""
else
    # Point the server at the local proxy (simulates enterprise setup)
    export LLM_PROXY_URL="${LITELLM_URL}/v1"
    export GOOGLE_API_KEY="sk-local-dev-key"
    echo "  LLM_PROXY_URL=${LLM_PROXY_URL}"
fi

# Cleanup proxy on exit
cleanup() {
    if [ -n "$LITELLM_PID" ]; then
        echo ""
        echo "Stopping LiteLLM proxy (PID $LITELLM_PID)..."
        kill $LITELLM_PID 2>/dev/null || true
    fi
}
trap cleanup EXIT

echo ""
echo "Starting server on http://localhost:8002"
echo ""

python server.py
