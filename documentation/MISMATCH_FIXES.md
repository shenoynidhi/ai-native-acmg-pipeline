# 🔧 Backend-Frontend Mismatch Fixes

## Overview
This document lists all the mismatches found between backend API and frontend expectations, and how they were fixed.

---

## 1. ❌ Missing `vcf_path` Column in Database

### Problem:
- **Frontend/Chat**: Tried to create sessions with `vcf_path=vcf_path`
- **Database**: Only had `vcf_filename` column, no `vcf_path`
- **Error**: `OperationalError: column "vcf_path" does not exist`

### Files Affected:
- `src/api/chat.py` (line 427)
- `src/api/db.py` (Session model)

### Fix:
```python
# src/api/db.py - Added to Session model
vcf_path = Column(String)  # Full path to VCF file
```

---

## 2. ❌ Missing `analysis_mode` Column

### Problem:
- **Frontend**: Expected `session.analysis_mode` in Dashboard.tsx
- **Database**: Only had `trio_mode` (boolean), no `analysis_mode` (string)
- **Result**: Dashboard showed "undefined" for mode column

### Files Affected:
- `frontend/src/pages/Dashboard.tsx` (line 286)
- `src/api/db.py` (Session model)

### Fix:
```python
# src/api/db.py
analysis_mode = Column(String, default="solo")  # "solo" or "trio"
```

```python
# src/api/chat.py - When creating session
analysis_mode=mode,  # "solo" or "trio"
trio_mode=(mode == "trio"),  # Boolean for backwards compatibility
```

---

## 3. ❌ Classification Format Mismatch

### Problem:
**Backend stored:**
```json
{
  "1:12345:A:G": "P",
  "2:67890:C:T": "LP",
  "3:11111:G:A": "VUS"
}
```

**Frontend expected:**
```json
{
  "P": 2,
  "LP": 1,
  "VUS": 3
}
```

### Files Affected:
- `src/api/dashboard.py` (lines 60-75, 145-164)
- `frontend/src/pages/Dashboard.tsx` (lines 294-306)

### Fix:
```python
# src/api/dashboard.py - Convert format when returning to frontend
classification_counts = {"P": 0, "LP": 0, "VUS": 0, "LB": 0, "B": 0}
if s.classifications:
    for variant_id, classification in s.classifications.items():
        if classification in classification_counts:
            classification_counts[classification] += 1

item["classifications"] = classification_counts
```

---

## 4. ❌ Dashboard Stats API Field Name Mismatch

### Problem:
- **Backend returned**: `classifications` and `total_variants_classified`
- **Frontend expected**: `classification_distribution` and `total_variants`

### Files Affected:
- `src/api/dashboard.py` (line 183-196)
- `frontend/src/pages/Dashboard.tsx` (line 20-26)
- `frontend/src/types/index.ts` (line 70-76)

### Fix:
```python
# src/api/dashboard.py - Return statement
return {
    "total_analyses": total_analyses,
    "completed": completed,
    "running": running,
    "queued": queued,
    "failed": failed,
    "total_variants": int(total_variants),  # Changed from total_variants_classified
    "classification_distribution": classification_totals,  # Changed from classifications
    ...
}
```

---

## 5. ❌ Missing Patient/Family Tracking Fields

### Problem:
- **Dashboard API**: Referenced `patient_id`, `father_id`, `mother_id`
- **Database**: These columns didn't exist
- **Result**: NULL values in API responses

### Files Affected:
- `src/api/dashboard.py` (lines 49, 64, 81-82, 230, 243-244)
- `src/api/db.py` (Session model)

### Fix:
```python
# src/api/db.py - Added to Session model
patient_id = Column(String, nullable=True)
father_id = Column(String, nullable=True)
mother_id = Column(String, nullable=True)
hpo_terms = Column(JSON, nullable=True)  # Also added for phenotype tracking
```

---

## 6. ❌ Chat Submission Not Creating Complete Records

### Problem:
When submitting analysis via chat:
- Created session with only `session_id`, `user_id`, `vcf_path`
- Missing: `vcf_filename`, `genome_build`, `clinical_notes`, etc.
- **Result**: Dashboard showed incomplete data

### Files Affected:
- `src/api/chat.py` (lines 421-437)

### Fix:
```python
# src/api/chat.py - Now creates complete session record
db_session = DBSession(
    session_id=session_id,
    user_id=user.user_id,
    vcf_path=vcf_path,
    vcf_filename=Path(vcf_path).name if vcf_path else None,  # Added
    genome_build=params["genome_build"],  # Added
    analysis_mode=mode,  # Added
    trio_mode=(mode == "trio"),  # Added
    clinical_notes=params.get("clinical_notes", ""),  # Added
    proband_sex=params.get("proband_sex", "unknown"),  # Added
    hpo_terms=params.get("patient_hpo_terms", []),  # Added
    status="queued",
    progress_pct=0,
    current_step="Queued for processing..."
)
```

---

## 7. ❌ Main API `/analyze` Endpoint Missing Fields

### Problem:
- `/analyze` endpoint created session without `vcf_path` and `analysis_mode`
- Used for direct file uploads (non-chat interface)

### Files Affected:
- `src/api/main.py` (lines 495-514)

### Fix:
```python
# src/api/main.py - Added missing fields
session = DBSession(
    session_id=session_id,
    user_id=user.user_id,
    genome_build=genome_build,
    clinical_notes=clinical_notes,
    proband_sex=proband_sex,
    vcf_filename=vcf_file.filename,
    vcf_path=str(vcf_path),  # Added
    analysis_mode="trio" if trio_mode else "solo",  # Added
    hpo_terms=hpo_terms,  # Added
    trio_mode=trio_mode,
    ...
)
```

---

## 8. ❌ Rerun Endpoint Not Copying All Fields

### Problem:
- `/rerun/{session_id}` endpoint created new session
- Didn't copy `vcf_path`, `analysis_mode`, `hpo_terms` from original

### Files Affected:
- `src/api/main.py` (lines 789-802)

### Fix:
```python
# src/api/main.py
session = DBSession(
    session_id=new_session_id,
    user_id=user.user_id,
    genome_build=params.get("genome_build"),
    clinical_notes=params.get("clinical_notes"),
    proband_sex=params.get("proband_sex"),
    vcf_filename=original.vcf_filename,
    vcf_path=original.vcf_path,  # Added
    analysis_mode=original.analysis_mode or "solo",  # Added
    trio_mode=original.trio_mode,
    hpo_terms=params.get("patient_hpo_terms"),  # Added
    status="queued",
    ...
)
```

---

## Database Migration Required

All existing databases need this migration:

```sql
-- Add new columns
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS patient_id VARCHAR;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS vcf_path VARCHAR;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS analysis_mode VARCHAR DEFAULT 'solo';
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS father_id VARCHAR;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS mother_id VARCHAR;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS hpo_terms JSONB;

-- Update existing records
UPDATE sessions
SET analysis_mode = CASE
    WHEN trio_mode = TRUE THEN 'trio'
    ELSE 'solo'
END
WHERE analysis_mode IS NULL;
```

---

## Testing Checklist

After deploying fixes:

- [x] Chat submission creates complete database records
- [x] Dashboard displays analysis_mode correctly
- [x] Classifications show as counts (P: 5, LP: 3...)
- [x] Patient/family IDs visible in trio mode
- [x] Status endpoint returns all required fields
- [x] `/analyze` endpoint sets all new fields
- [x] `/rerun` endpoint copies all fields correctly
- [x] No "undefined" or NULL errors in frontend

---

## Files Changed

### Backend:
1. `src/api/db.py` - Added 6 new columns to Session model
2. `src/api/chat.py` - Fixed session creation to set all fields
3. `src/api/dashboard.py` - Fixed classification format conversion
4. `src/api/main.py` - Fixed `/analyze` and `/rerun` endpoints

### Migration:
5. `migrations/add_session_fields.sql` - SQL migration for existing databases

### Documentation:
6. `DEPLOYMENT_STEPS.md` - Step-by-step deployment guide
7. `MISMATCH_FIXES.md` - This file

---

## Commits

1. **d191276** - Fix backend-frontend schema mismatches
2. **b210650** - Add detailed deployment guide for EC2

---

## Impact

### Before Fixes:
- ❌ Dashboard showed "No analyses yet" even when running
- ❌ Frontend displayed "undefined" for analysis mode
- ❌ Classifications appeared as empty objects `{}`
- ❌ Chat submissions invisible to dashboard
- ❌ API errors: "column does not exist"

### After Fixes:
- ✅ Analyses visible immediately after submission
- ✅ All metadata displays correctly
- ✅ Classifications show proper counts
- ✅ Complete tracking of patient/family data
- ✅ No frontend/backend type mismatches
