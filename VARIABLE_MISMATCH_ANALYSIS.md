# 🔍 Backend-Frontend Variable Mismatch Analysis

## Summary
After deep analysis of all frontend pages and backend APIs, I found **1 CRITICAL MISMATCH** that needs fixing.

---

## ✅ CORRECT Mappings (No Changes Needed)

### 1. **Dashboard.tsx** ✅
| Frontend Variable | Backend Field | Status |
|-------------------|---------------|---------|
| `session_id` | `session_id` | ✅ Match |
| `patient_id` | `patient_id` | ✅ Match |
| `status` | `status` | ✅ Match |
| `progress_pct` | `progress_pct` | ✅ Match |
| `variant_count` | `variant_count` | ✅ Match |
| `analysis_mode` | `analysis_mode` | ✅ Match |
| `genome_build` | `genome_build` | ✅ Match |
| `vcf_filename` | `vcf_filename` | ✅ Match |
| `created_at` | `created_at` | ✅ Match |
| `completed_at` | `completed_at` | ✅ Match |
| `classifications.P` | `classifications.P` | ✅ Match |
| `classifications.LP` | `classifications.LP` | ✅ Match |
| `classifications.VUS` | `classifications.VUS` | ✅ Match |
| `denovo_count` | `denovo_count` | ✅ Match |
| `compound_het_count` | `compound_het_count` | ✅ Match |

**Source:** `src/api/dashboard.py` lines 61-92

---

### 2. **Analyze.tsx** ✅
| Frontend Variable | Backend Field | Status |
|-------------------|---------------|---------|
| `genomeBuild` → `genome_build` | `genome_build` | ✅ Match |
| `patientId` → `patient_id` | `patient_id` | ✅ Match |
| `probandSex` → `proband_sex` | `proband_sex` | ✅ Match |
| `clinicalNotes` → `clinical_notes` | `clinical_notes` | ✅ Match |
| `analysis_mode` | `analysis_mode` | ✅ Match |

**Source:** `src/api/models.py` lines 74-103 (AnalyzeRequest)

---

### 3. **QCResults.tsx** ✅
| Frontend Variable | Backend Field | Status |
|-------------------|---------------|---------|
| `qc_status` | `qc_status` | ✅ Match |
| `qc_score` | `qc_score` | ✅ Match |
| `confidence` | `confidence` | ✅ Match |
| `input_qc` | `input_qc` | ✅ Match |
| `annotation_qc` | `annotation_qc` | ✅ Match |
| `evidence_qc` | `evidence_qc` | ✅ Match |
| `classification_qc` | `classification_qc` | ✅ Match |
| `report_qc` | `report_qc` | ✅ Match |
| `issues` | `issues` | ✅ Match |
| `analysis_mode` | `analysis_mode` | ✅ Match |
| `patient_id` | `patient_id` | ✅ Match |

**Source:** `frontend/src/types/index.ts` lines 47-62 (QCResult interface)

---

## ❌ **CRITICAL MISMATCH FOUND**

### 4. **AnalysisDetail.tsx** ❌

#### **Issue: `error_message` vs `error`**

**Frontend (Line 234):**
```typescript
{isFailed && session.error_message && (
  <Alert variant="destructive" className="mb-6">
    <AlertCircle className="h-4 w-4" />
    <AlertDescription>
      <strong>Analysis Failed:</strong> {session.error_message}
    </AlertDescription>
  </Alert>
)}
```

**Backend Database Model (`src/api/db.py` Line 112):**
```python
error = Column(Text)  # ❌ Field name is "error", not "error_message"
```

**Backend API Response (`src/api/models.py` Line 125):**
```python
class StatusResponse(BaseModel):
    session_id: str
    status: str
    progress_pct: int
    current_step: Optional[str] = None
    variant_count: Optional[int] = None
    report_paths: Optional[Dict[str, str]] = None
    error: Optional[str] = None  # ❌ Field name is "error"
    created_at: datetime
    completed_at: Optional[datetime] = None
```

**Backend API Endpoint (`src/api/main.py` Line 569):**
```python
return StatusResponse(
    session_id=session.session_id,
    status=session.status,
    progress_pct=session.progress_pct or 0,
    current_step=session.current_step,
    variant_count=session.variant_count,
    report_paths=session.report_paths,
    error=session.error,  # ❌ Returns "error"
    created_at=session.created_at,
    completed_at=session.completed_at
)
```

---

## 🛠️ **THE FIX**

### **Option 1: Fix Frontend (RECOMMENDED ✅)**

**Change:**
```diff
- {isFailed && session.error_message && (
+ {isFailed && session.error && (
    <Alert variant="destructive" className="mb-6">
      <AlertCircle className="h-4 w-4" />
      <AlertDescription>
-       <strong>Analysis Failed:</strong> {session.error_message}
+       <strong>Analysis Failed:</strong> {session.error}
      </AlertDescription>
    </Alert>
  )}
```

**Also update TypeScript types:**
```diff
// frontend/src/types/index.ts
export interface Session {
  session_id: string;
  user_id: string;
  status: 'queued' | 'running' | 'complete' | 'failed';
  progress_pct: number;
  patient_id?: string;
  vcf_filename?: string;
  analysis_mode: 'solo' | 'trio';
  genome_build: string;
  variant_count?: number;
  classifications?: {
    P: number;
    LP: number;
    VUS: number;
    LB: number;
    B: number;
  };
  denovo_count?: number;
  compound_het_count?: number;
  created_at: string;
  started_at?: string;
  completed_at?: string;
- error_message?: string;
+ error?: string;
}
```

---

## 📊 **All Variables Cross-Reference Table**

| Page | Frontend Variable | Backend Field | Database Column | Match? |
|------|-------------------|---------------|-----------------|---------|
| **Dashboard.tsx** | | | | |
| | `session_id` | `session_id` | `session_id` | ✅ |
| | `patient_id` | `patient_id` | `patient_id` | ✅ |
| | `status` | `status` | `status` | ✅ |
| | `progress_pct` | `progress_pct` | `progress_pct` | ✅ |
| | `variant_count` | `variant_count` | `variant_count` | ✅ |
| | `analysis_mode` | `analysis_mode` | `analysis_mode` | ✅ |
| | `genome_build` | `genome_build` | `genome_build` | ✅ |
| | `vcf_filename` | `vcf_filename` | `vcf_filename` | ✅ |
| | `created_at` | `created_at` | `created_at` | ✅ |
| | `completed_at` | `completed_at` | `completed_at` | ✅ |
| | `classifications` | `classifications` | `classifications` | ✅ |
| | `denovo_count` | `denovo_count` | `denovo_count` | ✅ |
| | `compound_het_count` | `compound_het_count` | `compound_het_count` | ✅ |
| **AnalysisDetail.tsx** | | | | |
| | `session_id` | `session_id` | `session_id` | ✅ |
| | `status` | `status` | `status` | ✅ |
| | `progress_pct` | `progress_pct` | `progress_pct` | ✅ |
| | `variant_count` | `variant_count` | `variant_count` | ✅ |
| | `analysis_mode` | `analysis_mode` | `analysis_mode` | ✅ |
| | `genome_build` | `genome_build` | `genome_build` | ✅ |
| | `classifications` | `classifications` | `classifications` | ✅ |
| | `denovo_count` | `denovo_count` | `denovo_count` | ✅ |
| | `compound_het_count` | `compound_het_count` | `compound_het_count` | ✅ |
| | **`error_message`** ❌ | **`error`** | **`error`** | **❌ MISMATCH** |
| **Analyze.tsx** | | | | |
| | `genome_build` | `genome_build` | `genome_build` | ✅ |
| | `patient_id` | `patient_id` | `patient_id` | ✅ |
| | `proband_sex` | `proband_sex` | `proband_sex` | ✅ |
| | `clinical_notes` | `clinical_notes` | `clinical_notes` | ✅ |
| | `analysis_mode` | `analysis_mode` | `analysis_mode` | ✅ |
| **QCResults.tsx** | | | | |
| | `qc_status` | `qc_status` | N/A (QC Store) | ✅ |
| | `qc_score` | `qc_score` | N/A (QC Store) | ✅ |
| | `confidence` | `confidence` | N/A (QC Store) | ✅ |
| | `input_qc` | `input_qc` | N/A (QC Store) | ✅ |
| | `annotation_qc` | `annotation_qc` | N/A (QC Store) | ✅ |
| | `evidence_qc` | `evidence_qc` | N/A (QC Store) | ✅ |
| | `classification_qc` | `classification_qc` | N/A (QC Store) | ✅ |
| | `report_qc` | `report_qc` | N/A (QC Store) | ✅ |
| | `issues` | `issues` | N/A (QC Store) | ✅ |

---

## 🎯 **Conclusion**

**ONLY 1 MISMATCH FOUND:**
- **File:** `frontend/src/pages/AnalysisDetail.tsx` (Line 234)
- **Issue:** Uses `session.error_message` instead of `session.error`
- **Fix:** Change `error_message` → `error` in frontend code and TypeScript types

**All other variables match perfectly between frontend and backend!** ✅

---

## 📝 **Files to Update**

1. **frontend/src/pages/AnalysisDetail.tsx** - Line 234 (change `error_message` → `error`)
2. **frontend/src/types/index.ts** - Line 31 (change `error_message?: string` → `error?: string`)
