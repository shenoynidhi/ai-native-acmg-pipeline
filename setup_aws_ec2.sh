#!/bin/bash
# =============================================================================
# AWS EC2 Setup Script for ACMG Pipeline
# Fixes missing dependencies and configures environment
# =============================================================================

set -e  # Exit on error

echo "=== ACMG Pipeline AWS EC2 Setup ==="
echo ""

# Check if conda environment is active
if [[ -z "$CONDA_DEFAULT_ENV" ]]; then
    echo "❌ ERROR: Conda environment not activated!"
    echo "Please run: conda activate acmg"
    exit 1
fi

echo "✓ Conda environment: $CONDA_DEFAULT_ENV"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Install Missing Python Dependencies
# ---------------------------------------------------------------------------
echo "📦 Installing missing Python packages..."
pip install --quiet email-validator
pip install --quiet 'pydantic[email]'
echo "✓ email-validator installed"
echo ""

# ---------------------------------------------------------------------------
# Step 2: Verify PostgreSQL Database
# ---------------------------------------------------------------------------
echo "🔍 Checking PostgreSQL..."
if ! systemctl is-active --quiet postgresql; then
    echo "⚠️  PostgreSQL not running, starting..."
    sudo systemctl start postgresql
    sudo systemctl enable postgresql
fi

# Create database if it doesn't exist
sudo -u postgres psql -lqt | cut -d \| -f 1 | grep -qw acmg_pipeline || {
    echo "Creating database acmg_pipeline..."
    sudo -u postgres psql -c "CREATE USER acmg_user WITH PASSWORD 'acmg_password';" 2>/dev/null || true
    sudo -u postgres psql -c "CREATE DATABASE acmg_pipeline OWNER acmg_user;"
}
echo "✓ PostgreSQL ready"
echo ""

# ---------------------------------------------------------------------------
# Step 3: Verify Redis
# ---------------------------------------------------------------------------
echo "🔍 Checking Redis..."
if ! systemctl is-active --quiet redis; then
    echo "⚠️  Redis not running, starting..."
    sudo systemctl start redis
    sudo systemctl enable redis
fi
echo "✓ Redis ready"
echo ""

# ---------------------------------------------------------------------------
# Step 4: Initialize Database Tables
# ---------------------------------------------------------------------------
echo "🔧 Initializing database tables..."
python -c "
from src.api.database import init_db
init_db()
print('✓ Database tables created')
"
echo ""

# ---------------------------------------------------------------------------
# Step 5: Verify EBS Database Paths
# ---------------------------------------------------------------------------
echo "🔍 Checking database paths..."
python -c "
from src.config import check_databases
check_databases(genome_build='GRCh38')
"
echo ""

# ---------------------------------------------------------------------------
# Step 6: Create systemd Services (Optional)
# ---------------------------------------------------------------------------
read -p "Create systemd services for auto-start? (y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Creating systemd services..."

    # Celery worker service
    sudo tee /etc/systemd/system/acmg-celery.service > /dev/null <<EOF
[Unit]
Description=ACMG Pipeline Celery Worker
After=network.target redis.service postgresql.service

[Service]
Type=simple
User=$USER
WorkingDirectory=$(pwd)
Environment="PATH=/home/$USER/miniconda3/envs/acmg/bin:/usr/bin"
Environment="PYTHONPATH=$(pwd)"
ExecStart=/home/$USER/miniconda3/envs/acmg/bin/celery -A src.api.worker worker --loglevel=info --concurrency=2 --queues=acmg_jobs
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=multi-user.target
EOF

    # FastAPI service
    sudo tee /etc/systemd/system/acmg-api.service > /dev/null <<EOF
[Unit]
Description=ACMG Pipeline FastAPI Server
After=network.target redis.service postgresql.service

[Service]
Type=simple
User=$USER
WorkingDirectory=$(pwd)
Environment="PATH=/home/$USER/miniconda3/envs/acmg/bin:/usr/bin"
Environment="PYTHONPATH=$(pwd)"
ExecStart=/home/$USER/miniconda3/envs/acmg/bin/uvicorn src.api.main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable acmg-celery acmg-api

    echo "✓ Systemd services created"
    echo ""
    echo "To start services:"
    echo "  sudo systemctl start acmg-celery"
    echo "  sudo systemctl start acmg-api"
    echo ""
    echo "To view logs:"
    echo "  sudo journalctl -u acmg-celery -f"
    echo "  sudo journalctl -u acmg-api -f"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "=== ✓ Setup Complete ==="
echo ""
echo "To start the pipeline manually:"
echo "  # Terminal 1 (Celery worker):"
echo "  celery -A src.api.worker worker --loglevel=info --concurrency=2 --queues=acmg_jobs"
echo ""
echo "  # Terminal 2 (FastAPI server):"
echo "  uvicorn src.api.main:app --host 0.0.0.0 --port 8000"
echo ""
echo "To access the API:"
echo "  curl http://localhost:8000/health"
echo "  http://$(curl -s ifconfig.me):8000/docs"
echo ""
