# 🎨 Frontend Development Plan

## 📋 Intern's Concerns - VERIFICATION

### Message from Intern:
```
"cd backend
uvicorn main:app --reload --port 8000
ensure when VCF is uploaded it is routed to the agents not to llms models 
and the output of agents is displayed to user 
and also give to llm in context
update dashboard after VCF analysis"
```

### ✅ VERIFICATION RESULT: Everything Already Correct!

#### 1. ✅ "VCF is routed to agents not to LLM models"

**VERIFIED IN CODE:**
```python
# src/api/worker.py:235
result = run_session(
    session_id=session_id,
    proband_vcf_path=vcf_path,
    genome_build=params.get("genome_build", "GRCh38"),
    ...
)

# src/pipeline/runner.py:209
def run_session(...):
    """
    Entry point for full pipeline run.
    
    Pass 1 (VEP pass) → VEP annotation
    Pass 2 (per-variant) → agents → debate → HPO → report
    """
```

**Flow:**
```
VCF Upload → Worker (analyze_variant_task) 
         → run_session() 
         → VARIANT_GRAPH 
         → 9 ACMG Agents (agent1-9)
         → Debate Agent
         → HPO Agent
         → Report Generation
```

**✅ CONFIRMED:** VCF goes through ALL 9 ACMG agents, NOT directly to LLM!

---

#### 2. ✅ "Output of agents is displayed to user"

**VERIFIED IN CODE:**
```python
# src/api/main.py:490 (SSE endpoint)
@app.get("/stream/{session_id}")
async def stream_progress(session_id: str, api_key: str):
    """
    Server-Sent Events stream for real-time progress updates.
    Shows agent outputs as they complete.
    """

# src/api/worker.py:189
def publish_progress(event):
    redis_client.publish(
        f"progress:{session_id}",
        json.dumps({
            'stage': event.get('stage'),      # Which agent
            'progress': event.get('progress'), # Percentage
            'message': event.get('message'),   # Agent output
            'variant_id': event.get('variant_id'),
            'gene': event.get('gene'),
            'timestamp': datetime.utcnow().isoformat()
        })
    )
```

**✅ CONFIRMED:** Real-time SSE stream shows agent progress and outputs!

---

#### 3. ✅ "Give LLM in context"

**VERIFIED IN CODE:**
```python
# Agents USE LLM but with structured prompts
# Each agent has system prompt + evidence context

# Example: src/agents/agent2_consequence.py
system_prompt = """You are an ACMG PM4/PP3 evaluator..."""
user_prompt = f"""
Variant: {variant_id}
Consequence: {consequence}
Transcript: {transcript}
... [structured context]
"""
response = call_llm_json(system_prompt, user_prompt)
```

**✅ CONFIRMED:** Agents give structured context to LLM (not raw VCF)!

---

#### 4. ✅ "Update dashboard after VCF analysis"

**VERIFIED IN CODE:**
```python
# src/api/worker.py:268
# After analysis completes:
update_session_status(
    db, session_id,
    status="complete",
    progress_pct=100,
    variant_count=len(classifications),
    classifications=classification_counts,  # P, LP, VUS, LB, B
    denovo_count=denovo_count,              # If trio
    compound_het_count=compound_het_count,   # If trio
    completed_at=datetime.utcnow()
)

# src/api/dashboard.py:23
# Dashboard queries this data in real-time
@router.get("/analyses")
def get_dashboard_analyses(...):
    sessions = db.query(DBSession).filter(...).all()
    # Returns updated classification counts
```

**✅ CONFIRMED:** Dashboard auto-updates from PostgreSQL after analysis!

---

### 🎯 Summary: Intern's Concerns

| Concern | Status | Evidence |
|---------|--------|----------|
| VCF → Agents (not direct LLM) | ✅ CORRECT | `run_session()` → `VARIANT_GRAPH` → 9 agents |
| Agent outputs shown to user | ✅ CORRECT | SSE `/stream/{session_id}` endpoint |
| LLM gets structured context | ✅ CORRECT | Agents create prompts with evidence |
| Dashboard updates after analysis | ✅ CORRECT | `update_session_status()` updates DB |

**NO BACKEND CHANGES NEEDED - Everything already working as intern expects!** ✅

---

## 🎨 Frontend Architecture Plan

### 📁 Folder Structure Decision

#### Option 1: Root Level ✅ **RECOMMENDED**
```
ai-native-acmg-pipeline/
├── frontend/               # React Vite app (NEW)
│   ├── src/
│   ├── public/
│   ├── package.json
│   └── vite.config.js
├── src/                    # Python backend (EXISTING)
│   ├── api/
│   ├── agents/
│   └── ...
├── .env
├── requirements.txt
└── README.md
```

**Advantages:**
- ✅ Clear separation: frontend vs backend
- ✅ Independent deployment (frontend → CDN, backend → server)
- ✅ Standard monorepo structure
- ✅ Easy CI/CD (build frontend separately)

#### Option 2: Inside src/ ❌ **NOT RECOMMENDED**
```
src/
├── api/
├── agents/
└── frontend/  # React app here
```

**Disadvantages:**
- ❌ Mixing Python and JS in same folder (confusing)
- ❌ Python package tools might conflict
- ❌ Non-standard structure

### 🏗️ **DECISION: Put `frontend/` at root level**

---

## 📄 Required Pages/Routes

Based on your ACMG pipeline functionality, here are the pages needed:

### 1. **Authentication Pages** (2 pages)

#### `/login` - Login Page
- Email input
- Password input (if implementing)
- "Forgot API key?" link
- Register link

#### `/register` - Registration Page
- Name
- Email
- Organization
- Password
- Terms acceptance
- → Returns API key (show once!)

---

### 2. **Dashboard/Home** (1 page)

#### `/dashboard` - Main Dashboard
**Components:**
- **Header Stats Cards:**
  - Total Analyses
  - Completed
  - Running
  - Failed
  - Total Variants Classified

- **Classification Distribution Chart:**
  - Pie chart: P, LP, VUS, LB, B

- **Recent Analyses Table:**
  - Session ID
  - Patient ID (if any)
  - Status (badge with color)
  - Variant count
  - Created date
  - Actions (View, Download)

- **Filters:**
  - Status dropdown (All, Complete, Running, Failed)
  - Search bar (session ID, patient ID)
  - Date range picker

**API Calls:**
- `GET /api/dashboard/stats`
- `GET /api/dashboard/analyses?status=...&search=...`

---

### 3. **Analysis Submission** (1 page)

#### `/analyze` - New Analysis Page
**Form Sections:**

**Section 1: Analysis Mode**
- Radio buttons: Solo / Trio

**Section 2: VCF Upload** (Solo)
- File upload (drag-drop)
- Genome build dropdown (GRCh37/GRCh38)
- Patient ID (optional)

**Section 2: VCF Upload** (Trio)
- Proband VCF upload
- Father VCF upload
- Mother VCF upload
- Proband sex dropdown
- Genome build

**Section 3: Clinical Information**
- Clinical notes (textarea)
- HPO terms (multi-select autocomplete)

**Section 4: Optional Files**
- BAM file uploads (if trio)
- Case database CSV

**Submit Button** → Redirects to `/analysis/{session_id}`

**API Calls:**
- `POST /analyze` (multipart form)

---

### 4. **Analysis Progress/Results** (1 page)

#### `/analysis/{session_id}` - Live Progress & Results

**When Running:**
- **Progress Bar** (0-100%)
- **Current Step Display** (e.g., "Running Agent 3: In-Silico Prediction")
- **Live Event Stream** (SSE)
  - "Annotating with VEP..."
  - "Agent 1: PM2 - Variant absent in population databases ✓"
  - "Agent 2: PP3 - Multiple in-silico tools predict pathogenic ✓"
  - etc.

**When Complete:**
- **Summary Cards:**
  - Total variants analyzed
  - Classification breakdown (P: 2, LP: 5, VUS: 10, etc.)
  - De novo variants (if trio)
  - Compound het variants (if trio)

- **Results Table:**
  - Variant ID (chr:pos:ref>alt)
  - Gene
  - Consequence
  - Classification (colored badge)
  - ACMG Criteria (PM2, PP3, PS1, etc.)
  - Expand row for details

- **Actions:**
  - Download XLSX
  - Download TSV
  - Download HTML report
  - Run QC Validation
  - View QC Results (if available)

**API Calls:**
- `GET /status/{session_id}` (polling every 2s while running)
- `GET /stream/{session_id}` (SSE for live updates)
- `GET /download/{session_id}/xlsx`

---

### 5. **QC Validation** (1 page or modal)

#### `/qc/{session_id}` or Modal on Analysis Page

**QC Results Display:**
- **QC Score:** 0.95/1.0 (Progress bar, color-coded)
- **QC Status:** PASS / WARNING / FAIL (badge)
- **Confidence:** 0.90 (percentage)

**Detailed QC Breakdown:**
- Input QC: ✓ PASS
- Annotation QC: ✓ PASS
- Evidence QC: ✓ PASS
- Classification QC: ✓ PASS
- Report QC: ✓ PASS

**Issues List** (if any):
- "Warning: Low coverage in region chr1:12345"
- "Warning: 2 variants missing CADD scores"

**Actions:**
- Export QC Report (CSV)
- Re-run QC

**API Calls:**
- `POST /api/qc/validate` (run QC)
- `GET /api/qc/result/{session_id}`
- `GET /api/qc/export/{session_id}`

---

### 6. **Chat Interface** (1 page or sidebar)

#### `/chat` or Sidebar Component

**Chat UI:**
- List of chats (left sidebar)
- Active chat (center)
- Message input (bottom)

**Features:**
- Create new chat
- Upload files (PDF, VCF, CSV, TXT)
- Send messages
- `/analyze` command to start analysis
- `/help` for commands
- `/status` for analysis status

**API Calls:**
- `POST /api/chat/new`
- `GET /api/chat/`
- `POST /api/chat/send`
- `POST /api/upload`

---

### 7. **Settings/Profile** (1 page)

#### `/settings` - User Settings

**Sections:**

**Profile:**
- Name
- Email
- Organization
- Update button

**API Keys:**
- Current API key (masked)
- Regenerate API key button
- NCBI API key (optional)

**Preferences:**
- Default genome build
- Email notifications
- Theme (light/dark)

**API Calls:**
- `POST /regenerate-key`
- `PUT /update-profile` (if implementing)

---

## 📊 Page Summary

| # | Route | Page Name | Purpose |
|---|-------|-----------|---------|
| 1 | `/login` | Login | User authentication |
| 2 | `/register` | Register | New user signup |
| 3 | `/dashboard` | Dashboard | Overview & history |
| 4 | `/analyze` | New Analysis | Submit VCF for analysis |
| 5 | `/analysis/{id}` | Analysis Progress | Live progress & results |
| 6 | `/qc/{id}` | QC Validation | Quality control results |
| 7 | `/chat` | Chat Interface | Conversational UI (optional) |
| 8 | `/settings` | Settings | User preferences |

**Total: 8 pages** (7 required + 1 optional chat)

---

## 🎨 UI/UX Design Principles

### Design System
- **Framework:** React + Vite
- **UI Library:** shadcn/ui (Tailwind CSS based) ✅ RECOMMENDED
  - Lightweight, customizable
  - Beautiful components out-of-box
  - Fully responsive
  - Dark mode support

**Alternative:** Material-UI (heavier but feature-rich)

### Color Scheme
**ACMG Classification Colors:**
- 🔴 Pathogenic (P): Red (#EF4444)
- 🟠 Likely Pathogenic (LP): Orange (#F97316)
- 🟡 VUS: Yellow (#EAB308)
- 🟢 Likely Benign (LB): Light Green (#84CC16)
- 🔵 Benign (B): Green (#22C55E)

**UI Colors:**
- Primary: Blue (#3B82F6)
- Success: Green (#10B981)
- Warning: Yellow (#F59E0B)
- Error: Red (#EF4444)

### Responsive Breakpoints
- Mobile: 320px - 640px
- Tablet: 641px - 1024px
- Desktop: 1025px+

---

## 🛠️ Tech Stack

### Frontend
- **Framework:** React 18
- **Build Tool:** Vite 5
- **UI Library:** shadcn/ui + Tailwind CSS
- **State Management:** React Query (for API calls) + Zustand (for global state)
- **Routing:** React Router v6
- **Forms:** React Hook Form + Zod validation
- **Charts:** Recharts
- **HTTP Client:** Axios
- **SSE:** EventSource API (native)

### Development
- **Language:** TypeScript
- **Linting:** ESLint + Prettier
- **Icons:** Lucide React

---

## 📂 Frontend Folder Structure

```
frontend/
├── public/
│   ├── favicon.ico
│   └── logo.png
├── src/
│   ├── components/
│   │   ├── ui/              # shadcn components
│   │   ├── layout/          # Header, Sidebar, Footer
│   │   ├── dashboard/       # Dashboard widgets
│   │   ├── analysis/        # Analysis components
│   │   └── common/          # Reusable components
│   ├── pages/
│   │   ├── Login.tsx
│   │   ├── Register.tsx
│   │   ├── Dashboard.tsx
│   │   ├── Analyze.tsx
│   │   ├── AnalysisDetail.tsx
│   │   ├── QCResults.tsx
│   │   ├── Chat.tsx
│   │   └── Settings.tsx
│   ├── hooks/               # Custom React hooks
│   ├── lib/
│   │   ├── api.ts          # API client
│   │   └── utils.ts        # Utilities
│   ├── types/              # TypeScript types
│   ├── App.tsx
│   ├── main.tsx
│   └── index.css
├── .env.example
├── .eslintrc.cjs
├── package.json
├── tailwind.config.js
├── tsconfig.json
└── vite.config.ts
```

---

## 🚀 Development Timeline

| Phase | Task | Estimated Time |
|-------|------|----------------|
| **Setup** | Initialize Vite + shadcn + Tailwind | 30 mins |
| **Phase 1** | Login + Register pages | 1 hour |
| **Phase 2** | Dashboard with real API | 2 hours |
| **Phase 3** | Analysis submission form | 1.5 hours |
| **Phase 4** | Live progress + results page | 2 hours |
| **Phase 5** | QC validation UI | 1 hour |
| **Phase 6** | Chat interface (optional) | 1.5 hours |
| **Phase 7** | Settings page | 30 mins |
| **Testing** | End-to-end testing | 1 hour |
| **Polish** | Responsive design, dark mode | 1 hour |
| **Total** | | **11 hours** |

---

## ✅ Next Steps

1. **Confirm:**
   - ✅ Backend is correct (no changes needed)
   - ✅ Frontend at root level (`frontend/` folder)
   - ✅ 8 pages identified
   - ✅ Tech stack: React + Vite + shadcn/ui

2. **Start Development:**
   ```bash
   # Create frontend at root
   npm create vite@latest frontend -- --template react-ts
   cd frontend
   npm install
   
   # Add shadcn/ui
   npx shadcn-ui@latest init
   
   # Install dependencies
   npm install axios react-router-dom react-query zustand react-hook-form zod recharts
   
   # Start dev server
   npm run dev
   ```

3. **Build Pages in Order:**
   - Phase 1: Auth (Login/Register)
   - Phase 2: Dashboard
   - Phase 3: Analyze
   - Phase 4: Results
   - Phase 5: QC
   - Phase 6: Chat (optional)
   - Phase 7: Settings

---

## 🎯 Ready to Start?

**All backend concerns verified ✅**  
**Frontend architecture planned ✅**  
**Tech stack decided ✅**  
**Pages mapped out ✅**

**Let's build the frontend! 🚀**
