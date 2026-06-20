# Quick DGX Server Setup Guide

## Step 1: SSH into DGX
```bash
ssh dgx-i-molsys@210.212.207.65
# Enter your password
```

## Step 2: Enter Kubernetes Pod
```bash
kubectl exec -it molsys-pod-a -- /bin/bash
```

## Step 3: Navigate to Project
```bash
cd /workspace/data/acmg-pipeline
```

## Step 4: Check if Project Exists
```bash
ls -la
# If ai-native-acmg-pipeline doesn't exist, we'll need to upload it
```

## Step 5: Start Backend Server
```bash
# Make sure you're in the project directory
cd ai-native-acmg-pipeline

# Check if dependencies are installed
pip list | grep fastapi

# If not installed, install them:
pip install -r requirements.txt

# Start the backend
python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

## Step 6: Configure Port Forwarding (From Windows PowerShell)

Open a NEW PowerShell window on your Windows machine:

```powershell
# Forward port 8000 from DGX to your local machine
ssh -L 8000:localhost:8000 dgx-i-molsys@210.212.207.65

# After connecting, run this to forward from pod:
kubectl port-forward molsys-pod-a 8000:8000
```

## Step 7: Update Frontend API URL

On your Windows machine, update the frontend to use the DGX backend:

Edit: `frontend/src/pages/Chat.tsx`
```typescript
// Change this line:
const API_BASE_URL = 'http://localhost:8000';
```

## Step 8: Test Connection

In your browser on Windows:
- Frontend: http://localhost:5173
- Backend API: http://localhost:8000/docs

---

## Alternative: Direct Connection (If Port Forward Doesn't Work)

Update frontend to connect directly to DGX:

```typescript
const API_BASE_URL = 'http://210.212.207.65:8000';
```

**Note**: This requires the DGX firewall to allow external connections on port 8000.

---

## Troubleshooting

### Backend won't start?
```bash
# Check if port 8000 is already in use
netstat -tulpn | grep 8000

# Kill existing process if needed
kill -9 <PID>
```

### Database connection issues?
Check `.env` file in the project directory:
```bash
cat .env
# Make sure DATABASE_URL points to the correct PostgreSQL instance
```

### Can't access from Windows?
```bash
# Make sure backend is listening on 0.0.0.0, not 127.0.0.1
# Check with:
netstat -tulpn | grep 8000
```
