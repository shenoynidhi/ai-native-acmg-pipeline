# Quick Reference - New Features

**All features implemented and ready to use!**

---

## 🚀 Quick Start

```bash
# 1. Migrate database (if existing installation)
python migrate_database.py

# 2. Create admin user
python create_admin.py

# 3. Restart services
pkill -f "celery"; pkill -f "uvicorn"
python -m celery -A src.api.worker worker --loglevel=info &
python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 &
```

---

## ✨ New Features

### 1. VCF Auto-Indexing
**Automatic** - no user action needed!  
VCF.gz files are automatically indexed before analysis.

### 2. Lost API Key Recovery
**MVP Version (No Email):**
```bash
curl -X POST http://localhost:8000/regenerate-key \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com"}'
```

**Production Version (2-Step with Email):**
```bash
# Step 1: Request code
curl -X POST http://localhost:8000/request-key-reset \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com"}'

# Step 2: Confirm with code (from email)
curl -X POST http://localhost:8000/confirm-key-reset \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "code": "123456"}'
```

### 3. Admin Panel
```bash
# Create admin
python create_admin.py

# List users
curl http://localhost:8000/admin/users -H "X-API-Key: $ADMIN_KEY"

# System stats
curl http://localhost:8000/admin/stats -H "X-API-Key: $ADMIN_KEY"

# Update quota
curl -X POST "http://localhost:8000/admin/users/UUID/quota?new_quota=500" \
  -H "X-API-Key: $ADMIN_KEY"
```

### 4. User NCBI Keys
Users can provide their own NCBI API key during registration (optional):
- Speeds up PubMed searches (10 req/sec vs 3 req/sec)
- Falls back to shared key if not provided
- Added to registration form automatically

### 5. Email Configuration (Optional)
```bash
# Gmail/SMTP
export SMTP_HOST=smtp.gmail.com
export SMTP_PORT=587
export SMTP_USER=your-email@gmail.com
export SMTP_PASSWORD=your-app-password

# Or SendGrid
export SENDGRID_API_KEY=your-key
```

---

## 📋 Admin Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /admin/users` | List all users |
| `GET /admin/sessions` | List all sessions |
| `GET /admin/stats` | System statistics |
| `POST /admin/users/{id}/quota` | Update user quota |
| `POST /admin/users/{id}/deactivate` | Deactivate user |
| `POST /admin/users/{id}/activate` | Reactivate user |

---

## 🔐 Security

- ✅ bcrypt password hashing
- ✅ Admin-only access control
- ✅ Email verification (2-step reset)
- ✅ Code expiration (15 minutes)
- ✅ Input validation
- ✅ SQL injection prevention

---

## 📚 Full Documentation

- **Implementation Details:** [docs/IMPLEMENTATION_COMPLETE.md](docs/IMPLEMENTATION_COMPLETE.md)
- **Testing Guide:** [docs/IMPLEMENTATION_COMPLETE.md#testing-checklist](docs/IMPLEMENTATION_COMPLETE.md#testing-checklist)
- **Production Deployment:** [docs/STEP6_COMPLETE_GUIDE.md](docs/STEP6_COMPLETE_GUIDE.md)

---

**Status:** ✅ ALL FEATURES COMPLETE AND TESTED
