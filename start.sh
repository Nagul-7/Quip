#!/bin/bash

echo "🚀 Starting Quip — FinoLens AI Analyst"
echo "======================================="

# Check if .env exists
if [ ! -f backend/.env ]; then
  echo "❌ Error: backend/.env not found. Copy .env.example and fill in your API keys."
  exit 1
fi

# Check if Redis is running, start if not
if ! pgrep -x "redis-server" > /dev/null; then
  echo "⚡ Starting Redis..."
  redis-server --daemonize yes
fi

# Install dependencies if needed
if [ ! -d "backend/venv" ]; then
  echo "📦 Creating virtual environment..."
  python3 -m venv backend/venv
  source backend/venv/bin/activate
  pip install -r backend/requirements.txt
else
  source backend/venv/bin/activate
fi

# Start FastAPI backend
echo "🔧 Starting Quip backend on http://localhost:8000"
cd backend
uvicorn main:app --reload --port 8000 &
BACKEND_PID=$!
cd ..

# Open frontend
echo "🌐 Opening Quip frontend..."
sleep 2
xdg-open frontend/code.html 2>/dev/null || open frontend/code.html 2>/dev/null

echo ""
echo "✅ Quip is running!"
echo "   Backend API  → http://localhost:8000"
echo "   Health check → http://localhost:8000/health"
echo "   Docs         → http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop."

wait $BACKEND_PID
