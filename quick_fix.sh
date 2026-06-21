#!/bin/bash
# Quick fix for immediate errors
set -e

echo "=== Quick Fix for ACMG Pipeline ==="

# Fix 1: Install email-validator
echo "📦 Installing email-validator..."
pip install --quiet email-validator 'pydantic[email]'
echo "✓ Installed"

# Fix 2: Initialize database
echo "🔧 Initializing database..."
python -c "from src.api.database import init_db; init_db()" || echo "⚠️  Database already initialized"

# Fix 3: Kill existing processes
echo "🔪 Killing old processes..."
pkill -f "celery.*worker" || true
pkill -f "uvicorn.*main:app" || true
sleep 2

echo ""
echo "✓ Fixes applied!"
echo ""
echo "Now run:"
echo "  1. celery -A src.api.worker worker --loglevel=info --concurrency=2 --queues=acmg_jobs &"
echo "  2. uvicorn src.api.main:app --host 0.0.0.0 --port 8000 &"
