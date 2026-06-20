#!/bin/bash
# Start Backend on DGX Server
# Run this AFTER connecting to the molsys-pod-a

echo "========================================="
echo "Starting ACMG Pipeline Backend"
echo "========================================="
echo ""

# Check if we're in the right directory
if [ ! -f "src/api/main.py" ]; then
    echo "❌ Error: src/api/main.py not found!"
    echo "Make sure you're in the ai-native-acmg-pipeline directory"
    exit 1
fi

echo "✅ Found project files"
echo ""

# Check Python version
echo "🐍 Python version:"
python --version
echo ""

# Check if .env exists
if [ ! -f ".env" ]; then
    echo "⚠️  Warning: .env file not found!"
    echo "Creating .env from .env.example..."
    cp .env.example .env
    echo "⚠️  Please edit .env to configure database connection"
    echo ""
fi

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python -m venv venv
    echo "✅ Virtual environment created"
    echo ""
fi

# Activate virtual environment
echo "🔌 Activating virtual environment..."
source venv/bin/activate
echo ""

# Check if dependencies are installed
echo "📦 Checking dependencies..."
if ! pip list | grep -q fastapi; then
    echo "⚠️  Dependencies not installed. Installing now..."
    pip install -r requirements.txt
    echo "✅ Dependencies installed"
    echo ""
fi

# Check if Redis is running
echo "🔍 Checking Redis connection..."
if ! redis-cli ping > /dev/null 2>&1; then
    echo "⚠️  Warning: Redis not responding"
    echo "Make sure Redis is running: redis-server"
    echo ""
fi

# Check PostgreSQL connection
echo "🔍 Checking PostgreSQL connection..."
if ! python -c "from src.api.db import engine; engine.connect()" 2>/dev/null; then
    echo "⚠️  Warning: Cannot connect to PostgreSQL"
    echo "Check your .env DATABASE_URL setting"
    echo ""
fi

echo "========================================="
echo "🚀 Starting Backend Server..."
echo "========================================="
echo ""
echo "Backend will be available at:"
echo "  - http://localhost:8000"
echo "  - http://0.0.0.0:8000 (for external access)"
echo ""
echo "API Documentation:"
echo "  - http://localhost:8000/docs (Swagger UI)"
echo "  - http://localhost:8000/redoc (ReDoc)"
echo ""
echo "Press CTRL+C to stop the server"
echo ""
echo "========================================="
echo ""

# Start Uvicorn
python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
