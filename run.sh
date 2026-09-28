#!/usr/bin/env bash
# One-command start for macOS / Linux
set -e

# --- Python venv ---------------------------------------------------------
if [ ! -d ".venv" ]; then
  echo "Creating Python virtual environment..."
  python3 -m venv .venv
fi

echo "Installing / verifying Python dependencies..."
.venv/bin/pip install -q -r requirements.txt

# --- .env ----------------------------------------------------------------
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "Copied .env.example → .env. Edit it to add optional GROQ / Hindsight keys."
fi

# --- Frontend ------------------------------------------------------------
if [ ! -f "frontend/dist/index.html" ]; then
  echo "Building frontend (one-time)..."
  [ ! -d "frontend/node_modules" ] && (cd frontend && npm install)
  (cd frontend && npm run build)
fi

# --- Launch --------------------------------------------------------------
echo ""
echo "Starting MemoryOps at http://localhost:8000"
echo "Press Ctrl+C to stop."
echo ""
.venv/bin/python -m uvicorn backend.app.main:app --port 8000
