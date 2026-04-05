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

# Start local LiteLLM proxy if configured for localhost — mirrors Windows enterprise setup
LITELLM_PID=""
if grep -qE '^LLM_PROXY_URL=http://localhost:4000' .env 2>/dev/null; then
    # Export GEMINI_API_KEY from .env so litellm can reach Gemini
    set -a; source .env; set +a
    if [ -z "$GEMINI_API_KEY" ]; then
        echo "ERROR: GEMINI_API_KEY must be set in .env for local LiteLLM proxy."
        exit 1
    fi
    echo "Starting local LiteLLM proxy on :4000..."
    litellm --config litellm_config.yaml --port 4000 > litellm.log 2>&1 &
    LITELLM_PID=$!
    trap 'kill $LITELLM_PID 2>/dev/null || true' EXIT INT TERM
    # Wait for proxy to be ready
    for i in 1 2 3 4 5 6 7 8 9 10; do
        if curl -sf http://localhost:4000/health/readiness > /dev/null 2>&1; then
            echo "  LiteLLM ready."
            break
        fi
        sleep 1
    done
fi

echo ""
echo "Starting server on http://localhost:8002"
echo ""

python server.py
