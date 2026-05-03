#!/bin/bash

# ─────────────────────────────────────────
#   QUIP — FinoLens AI Analyst
# ─────────────────────────────────────────

clear
echo ""
echo "  ██████╗ ██╗   ██╗██╗██████╗ "
echo "  ██╔═══██╗██║   ██║██║██╔══██╗"
echo "  ██║   ██║██║   ██║██║██████╔╝"
echo "  ██║▄▄ ██║██║   ██║██║██╔═══╝ "
echo "  ╚██████╔╝╚██████╔╝██║██║     "
echo "   ╚══▀▀═╝  ╚═════╝ ╚═╝╚═╝     "
echo ""
echo "  FinoLens AI Analyst — by Nagul"
echo "  ─────────────────────────────"
echo ""

# Kill anything running on port 8000
echo "  ⚡ Checking port 8000..."
PID=$(lsof -ti:8000)
if [ ! -z "$PID" ]; then
  echo "  🔴 Killing existing process on port 8000 (PID: $PID)"
  kill -9 $PID
  sleep 1
fi
echo "  ✅ Port 8000 is free"
echo ""

# Check .env
if [ ! -f backend/.env ]; then
  echo "  ❌ backend/.env not found!"
  echo "  Copy backend/.env.example and fill in your API keys."
  exit 1
fi
echo "  ✅ Environment config found"

# Start Redis if not running
echo "  ⚡ Checking Redis..."
if ! pgrep -x "redis-server" > /dev/null; then
  echo "  🔄 Starting Redis..."
  redis-server --daemonize yes 2>/dev/null
  sleep 1
fi
echo "  ✅ Redis is running"
echo ""

# Resolve venv uvicorn — prefer venv over system to ensure correct packages
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UVICORN=""
if [ -f "$SCRIPT_DIR/backend/venv/bin/uvicorn" ]; then
  UVICORN="$SCRIPT_DIR/backend/venv/bin/uvicorn"
  echo "  ✅ Virtual environment activated (backend/venv)"
elif [ -f "$SCRIPT_DIR/venv/bin/uvicorn" ]; then
  UVICORN="$SCRIPT_DIR/venv/bin/uvicorn"
  echo "  ✅ Virtual environment activated (venv)"
else
  UVICORN="uvicorn"
  echo "  ⚠️  No project venv found — using system uvicorn"
fi

echo ""
echo "  🚀 Starting Quip backend..."
echo "  ─────────────────────────"
echo ""

cd "$SCRIPT_DIR/backend"
$UVICORN main:app --reload --port 8000 --host 0.0.0.0
