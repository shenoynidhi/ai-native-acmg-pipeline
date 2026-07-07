# 🚀 Deployment Steps for EC2

## Step 1: Run Database Migration

The latest changes added new columns to the `sessions` table. Run this migration first:

```bash
# Connect to PostgreSQL
sudo -u postgres psql -d acmg_pipeline

# Run the migration
\i /home/ubuntu/ai-native-acmg-pipeline/migrations/add_session_fields.sql

# Verify the changes
\d sessions

# Exit PostgreSQL
\q
```

Expected output should show new columns:
- `patient_id` (VARCHAR)
- `vcf_path` (VARCHAR)
- `analysis_mode` (VARCHAR, default 'solo')
- `father_id` (VARCHAR)
- `mother_id` (VARCHAR)
- `hpo_terms` (JSONB)

## Step 2: Pull Latest Code

```bash
cd ~/ai-native-acmg-pipeline
git pull origin main
```

Expected output:
```
Updating 1f0ea81..d191276
Fast-forward
 migrations/add_session_fields.sql | 37 +++++++++++++++++++
 src/api/chat.py                   |  8 +++++
 src/api/dashboard.py              | 32 ++++++++++------
 src/api/db.py                     |  6 ++++
 src/api/main.py                   |  7 ++++
 5 files changed, 77 insertions(+), 13 deletions(-)
```

## Step 3: Restart Services

```bash
# Stop API server
pkill -f "uvicorn src.api.main:app"

# Stop Celery worker (let current tasks finish)
pkill -TERM -f "celery -A src.api.worker"

# Wait 5 seconds for graceful shutdown
sleep 5

# Start API server
nohup uvicorn src.api.main:app --host 0.0.0.0 --port 8000 > api.log 2>&1 &

# Start Celery worker
nohup celery -A src.api.worker worker --loglevel=info --concurrency=2 --queues=acmg_jobs > celery.log 2>&1 &

# Verify services are running
ps aux | grep uvicorn
ps aux | grep celery
```

## Step 4: Test the Complete Flow

### 4.1 Test Chat Submission

1. Open the chat interface: `http://your-ec2-ip:3000/chat`
2. Create new chat
3. Type `/analyze`
4. Select `1` for Solo mode
5. Upload a VCF file
6. Follow the prompts:
   - Genome build: `38`
   - Patient sex: `M`
   - Clinical notes: `Test case`
   - HPO terms: `skip`
7. You should see: ✅ **Analysis submitted successfully!**

### 4.2 Verify Database Record

```bash
sudo -u postgres psql -d acmg_pipeline -c "SELECT session_id, status, analysis_mode, vcf_path, vcf_filename FROM sessions ORDER BY created_at DESC LIMIT 1;"
```

Expected output should show:
- `status`: `queued` or `running`
- `analysis_mode`: `solo`
- `vcf_path`: Full path to VCF
- `vcf_filename`: Filename only

### 4.3 Check Dashboard Visibility

1. Open dashboard: `http://your-ec2-ip:3000/`
2. You should now see the analysis in "Recent Analyses" table
3. Click "View" button
4. Analysis detail page should load with progress

### 4.4 Monitor Celery Logs

```bash
tail -f celery.log
```

Expected flow:
1. `VCF annotation...` (VEP running)
2. `Pre-filtering: 100 variants -> 55`
3. `MAF filtering: 55 -> 5 variants`
4. `Running agents for variant 1/5...`
5. `[session_xxx] Analysis complete`

## Step 5: Verify API Responses

### Check Status Endpoint
```bash
# Replace SESSION_ID with actual session ID
curl -H "X-API-Key: YOUR_API_KEY" \
  http://localhost:8000/status/SESSION_ID | jq
```

Expected fields:
```json
{
  "session_id": "session_xxx",
  "status": "running",
  "progress_pct": 45,
  "current_step": "Running agents...",
  "variant_count": 5,
  "created_at": "2026-06-21T..."
}
```

### Check Dashboard API
```bash
curl -H "X-API-Key: YOUR_API_KEY" \
  http://localhost:8000/dashboard/analyses | jq
```

Expected structure:
```json
{
  "sessions": [
    {
      "session_id": "session_xxx",
      "analysis_mode": "solo",
      "vcf_filename": "test.vcf.gz",
      "classifications": {
        "P": 2,
        "LP": 1,
        "VUS": 2,
        "LB": 0,
        "B": 0
      }
    }
  ]
}
```

## Troubleshooting

### Issue: "Column does not exist" error
**Solution:** Run the migration script (Step 1)

### Issue: Dashboard shows "No analyses yet"
**Solution:** Check that `analysis_mode` field is set in sessions table:
```sql
SELECT session_id, analysis_mode, vcf_path FROM sessions;
```

If NULL, update:
```sql
UPDATE sessions SET analysis_mode = 'solo' WHERE analysis_mode IS NULL AND trio_mode = FALSE;
UPDATE sessions SET analysis_mode = 'trio' WHERE analysis_mode IS NULL AND trio_mode = TRUE;
```

### Issue: Classifications show as `{}`
**Solution:** This is expected until analysis completes. After completion, it should show `{P: 2, LP: 1, ...}`

### Issue: VEP annotation fails
**Solution:** Check VEP Docker is running:
```bash
docker ps
docker pull ensemblorg/ensembl-vep:release_112.0
```

## Summary of Changes

### Backend Changes:
1. **Database Schema** (`src/api/db.py`):
   - Added `vcf_path`, `analysis_mode`, `patient_id`
   - Added `father_id`, `mother_id`, `hpo_terms`

2. **Chat Submission** (`src/api/chat.py`):
   - Now creates complete session records with all fields
   - Sets `vcf_path`, `vcf_filename`, `analysis_mode`, `trio_mode`

3. **Dashboard API** (`src/api/dashboard.py`):
   - Converts classifications from `{variant_id: "P"}` to `{P: 5, LP: 3}`
   - Returns `analysis_mode` field for frontend
   - Fixed stats endpoint to use `classification_distribution`

4. **Main API** (`src/api/main.py`):
   - `/analyze` endpoint sets all new fields
   - `/rerun` endpoint copies all fields from original session

### What This Fixes:
- ✅ Analyses now visible in dashboard immediately after submission
- ✅ Dashboard shows correct analysis mode (solo/trio)
- ✅ Classification counts display properly
- ✅ No more "undefined" errors in frontend
- ✅ Proper tracking of VCF paths and patient identifiers

## Next Steps

After successful deployment:
1. Monitor a complete analysis end-to-end
2. Verify report generation works
3. Test trio mode if needed
4. Check download endpoints for XLSX/HTML reports
