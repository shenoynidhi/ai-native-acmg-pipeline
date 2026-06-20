# Step 6 Complete Guide: API, Web UI, SSE & MemPalace

**Date:** June 2026  
**Status:** ✅ COMPLETE  
**Version:** 1.0

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Components Built](#components-built)
4. [File Structure](#file-structure)
5. [Configuration](#configuration)
6. [How to Use](#how-to-use)
7. [How to Modify](#how-to-modify)
8. [Deployment Guide](#deployment-guide)
9. [Troubleshooting](#troubleshooting)

---

## Overview

Step 6 adds a complete API layer, web interface, real-time progress tracking, and semantic memory system on top of the existing ACMG classification pipeline.

### What Was Built

- **FastAPI REST API**: 15+ endpoints for analysis, authentication, history
- **Celery Worker**: Async job processing with Redis broker
- **PostgreSQL + pgvector**: Database with vector similarity search
- **Web UI**: HTML/CSS/JS interface for VCF upload and results
- **SSE (Server-Sent Events)**: Real-time progress streaming
- **MemPalace**: Semantic memory system for analysis history
- **Knowledge Graph**: Tracks variant classifications over time

### Key Features

✅ User registration with API keys  
✅ Async VCF processing with progress tracking  
✅ Real-time updates via SSE (no polling needed)  
✅ Automatic memory storage of all analyses  
✅ Semantic search of past analyses  
✅ Variant classification history tracking  
✅ Multi-format reports (HTML, XLSX, TSV)  
✅ Full authentication & quota management  

---

## Architecture

```
┌─────────────┐
│   Browser   │ ← User accesses Web UI
└─────┬───────┘
      │ HTTP/SSE
┌─────▼────────────────────────────────────────┐
│              FastAPI (main.py)               │
│  • REST endpoints                             │
│  • File upload handling                       │
│  • SSE streaming                              │
│  • Static file serving                        │
└──────┬────────────┬──────────────────────────┘
       │            │
       │            │ Redis Pub/Sub (SSE)
       │            ▼
       │     ┌─────────────┐
       │     │    Redis    │
       │     │  (Broker)   │
       │     └─────────────┘
       │            │
       │ Submit Job │
       │            ▼
       │     ┌──────────────────────────┐
       │     │  Celery Worker           │
       │     │  (worker.py)             │
       │     │  • Runs pipeline         │
       │     │  • Emits progress        │
       │     │  • Stores in MemPalace   │
       │     └──────┬───────────────────┘
       │            │
       │            │ Progress Callback
       │            │
       │            ▼
       │     ┌──────────────────────────┐
       │     │  Pipeline Runner         │
       │     │  (runner.py)             │
       │     │  • VEP → Agents → Debate │
       │     │  • Emits SSE events      │
       │     └──────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────┐
│         PostgreSQL + pgvector                 │
│  • users (API keys, quotas)                   │
│  • sessions (job status, results)             │
│  • palace_memories (semantic search)          │
│  • palace_knowledge (variant history)         │
└───────────────────────────────────────────────┘
```

### Data Flow

1. **User uploads VCF** → FastAPI `/analyze` endpoint
2. **FastAPI** → Saves VCF, creates session record, submits Celery task
3. **Celery worker** → Runs pipeline with progress callback
4. **Progress events** → Published to Redis channel
5. **SSE endpoint** → Subscribes to Redis, streams to browser
6. **Pipeline completes** → Results stored in DB + MemPalace
7. **User downloads** → FastAPI serves reports

---

## Components Built

### 1. Database Layer (`src/api/db.py`)

**Purpose:** SQLAlchemy models for PostgreSQL + pgvector

**Tables:**
- `users`: User accounts, API keys (bcrypt hashed), quotas
- `sessions`: Analysis jobs with status tracking
- `palace_memories`: Semantic memories with 384-dim embeddings
- `palace_knowledge`: Variant classification relationships

**Key Functions:**
```python
init_db()                    # Create all tables + pgvector index
get_db()                     # FastAPI dependency for DB sessions
SessionLocal()               # Create new DB session
```

**Where to modify:**
- Add fields: Edit model classes in `db.py`, then `alembic revision --autogenerate`
- Change table names: Edit `__tablename__` in model classes
- Database URL: Set `DATABASE_URL` environment variable

---

### 2. API Models (`src/api/models.py`)

**Purpose:** Pydantic request/response validation

**Key Models:**
- `RegisterRequest/Response`: User registration
- `AnalyzeRequest/Response`: VCF submission
- `StatusResponse`: Job progress
- `HistoryResponse`: Past analyses
- `RerunRequest`: Re-analyze with new params

**Where to modify:**
- Add request fields: Edit model classes, FastAPI auto-validates
- Change validation: Use Pydantic validators (`@field_validator`)
- API examples: Edit `Config.json_schema_extra`

**Example - Add new field:**
```python
class AnalyzeRequest(BaseModel):
    # Existing fields...
    custom_field: Optional[str] = None  # Add this
```

---

### 3. Authentication (`src/api/auth.py`)

**Purpose:** User registration, API key validation, quota enforcement

**Key Functions:**
```python
register_user(request, db)            # Create user, return API key
verify_api_key(x_api_key, db)         # Validate key, check quota
increment_usage(user, db)             # Increment analysis counter
```

**Security:**
- API keys: 256-bit random (bcrypt hashed)
- Shown once during registration
- All protected endpoints require `X-API-Key` header

**Where to modify:**
- Change quota limits: Edit `max_analyses` default in `register_user()`
- Add permissions: Add fields to `User` model, check in `verify_api_key()`
- Different auth: Replace with JWT/OAuth in `main.py` endpoints

**Change default quota:**
```python
# In src/api/auth.py, line ~40
user = User(
    # ...
    max_analyses=500,  # Change from 100 to 500
)
```

---

### 4. Celery Worker (`src/api/worker.py`)

**Purpose:** Async job processing with progress tracking

**Key Components:**
```python
celery_app                           # Celery application
analyze_variant_task()               # Main analysis task
update_session_status()              # Update job in DB
publish_progress()                   # Publish SSE events to Redis
```

**Flow:**
1. Task receives: `session_id`, `vcf_path`, `params`
2. Creates `ProgressCallback` that publishes to Redis
3. Runs `run_session()` with callback
4. Stores results in DB + MemPalace
5. Returns: `session_id`, `status`, `report_paths`

**Where to modify:**
- Task timeout: Edit `task_time_limit` in celery config (default: 1 hour)
- Progress messages: Edit `publish_progress()` callback
- Error handling: Edit exception block in `analyze_variant_task()`

**Change timeout:**
```python
# In src/api/worker.py, line ~35
celery_app.conf.update(
    task_time_limit=7200,  # Change to 2 hours (7200 seconds)
)
```

---

### 5. FastAPI Main (`src/api/main.py`)

**Purpose:** REST API endpoints + SSE streaming + Web UI

**Endpoints:**

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/register` | No | Create user account |
| POST | `/analyze` | Yes | Submit VCF for analysis |
| GET | `/status/{session_id}` | Yes | Check job status |
| GET | `/stream/{session_id}` | Yes | SSE real-time progress |
| GET | `/download/{session_id}/{format}` | Yes | Download report |
| GET | `/history` | Yes | List past analyses |
| POST | `/rerun/{session_id}` | Yes | Re-analyze with overrides |
| GET | `/memory/search` | Yes | Semantic search memories |
| GET | `/memory/gene/{gene}` | Yes | Get gene variant history |
| GET | `/memory/variant/{gene}/{variant_id}` | Yes | Get variant classification history |
| GET | `/memory/recent` | Yes | Recent analyses |
| GET | `/` | No | Web UI homepage |
| GET | `/health` | No | Health check |

**Where to modify:**
- Add endpoint: Add `@app.get/post()` decorated function
- Change URLs: Edit path in decorator
- CORS settings: Edit `CORSMiddleware` config (line ~50)
- Upload limits: Add `File(..., max_size=...)` to upload endpoints

**Add new endpoint:**
```python
@app.get("/my-endpoint", tags=["Custom"])
def my_endpoint(user: User = Depends(verify_api_key)):
    """My custom endpoint."""
    return {"message": "Hello from custom endpoint"}
```

---

### 6. Web UI (`src/frontend/`)

**Files:**
- `index.html`: Page structure, forms, sections
- `styles.css`: Styling, colors, responsive design
- `app.js`: JavaScript logic, API calls, SSE connection

**Sections:**
- Registration form
- Analysis submission form
- Progress display (with SSE)
- Results + download buttons
- Error display

**Where to modify:**

**Change colors/branding (`styles.css`):**
```css
:root {
    --primary: #1a237e;        /* Main brand color */
    --success: #2e7d32;        /* Success green */
    --error: #c62828;          /* Error red */
}
```

**Add form field (`index.html`):**
```html
<!-- Add after existing form fields -->
<div class="form-group">
    <label for="my-field">My Custom Field</label>
    <input type="text" id="my-field" name="my-field">
</div>
```

**Handle new field (`app.js`):**
```javascript
// In analysis form submit handler
formData.append('my_custom_field', document.getElementById('my-field').value);
```

**Change logo (`index.html`):**
```html
<header>
    <img src="/static/logo.png" alt="Your Lab" style="height: 60px;">
    <h1>🧬 Your Lab Name</h1>
</header>
```

---

### 7. SSE Progress (`/stream/{session_id}`)

**Purpose:** Real-time progress streaming (no polling!)

**How it works:**
1. Worker publishes progress to Redis: `progress:{session_id}`
2. SSE endpoint subscribes to Redis channel
3. Events streamed to browser via `EventSource`

**Event types:**
- `connected`: Initial connection
- `progress`: Progress update (stage, percentage, message)
- `complete`: Analysis finished
- `failed`: Analysis failed

**Where to modify:**

**Add custom progress stage (`src/pipeline/progress_emitter.py`):**
```python
def my_custom_stage(self, details: str):
    self._emit('my_stage', 0.80, f'Running custom stage: {details}')
```

**Handle new event type (`src/frontend/app.js`):**
```javascript
eventSource.addEventListener('my_custom_event', (e) => {
    const data = JSON.parse(e.data);
    // Handle custom event
});
```

**Change SSE URL format:**
Currently uses query param for API key (EventSource limitation). For production, use short-lived tokens:

```python
# Generate token on /analyze response
import jwt
token = jwt.encode({'session_id': session_id, 'exp': ...}, SECRET_KEY)
return {'session_id': session_id, 'sse_token': token}

# Validate in /stream endpoint
payload = jwt.decode(api_key, SECRET_KEY)
```

---

### 8. MemPalace (`src/mempalace/`)

**Purpose:** Semantic memory system with knowledge graph

**Components:**

**`palace.py` - Core memory operations:**
```python
mine_memory(user_id, wing, room, content)    # Store memory
search_memories(user_id, query, wing, limit) # Semantic search
wake_up(user_id, context, wings, limit)      # Context-aware retrieval
delete_memory(memory_id)                      # Soft-delete
update_memory(memory_id, new_content)        # Update + re-embed
mine_session_summary(...)                     # Convenience: store session
```

**`knowledge_graph.py` - Variant tracking:**
```python
record_classification(user_id, variant_id, gene, classification, session_id)
track_reclassification(user_id, variant_id, gene, old_class, new_class, session_id)
get_variant_history(user_id, variant_id, gene)
get_gene_variants(user_id, gene)
get_recent_analyses(user_id, limit)
```

**Palace Structure:**
```
Wing: analysis_history
  Room: session_abc123
    Memory: "Analyzed 3 variants, found 1 LP..."

Wing: preferences
  Room: default_params
    Memory: "User prefers GRCh38, always includes HPO terms"

Wing: variants
  Room: BRCA2
    Memory: "Found PM2+PP3, classified as LP"
```

**Embeddings:**
- Model: `sentence-transformers/all-MiniLM-L6-v2`
- Dimensions: 384
- Device: CPU (forced to avoid GPU issues)
- Similarity: Cosine distance via pgvector

**Where to modify:**

**Change embedding model:**
```python
# In src/mempalace/palace.py, _get_embedding_model()
_embedding_model = SentenceTransformer(
    'sentence-transformers/all-mpnet-base-v2',  # Better quality, slower
    device='cpu'
)
```

**Add custom memory type:**
```python
# 1. Define new wing in your code
mine_memory(
    user_id=user_id,
    wing="custom_wing",       # New wing type
    room="custom_context",
    content="Your custom memory content"
)

# 2. Query it
results = search_memories(
    user_id=user_id,
    query="your search query",
    wing="custom_wing"
)
```

**Disable MemPalace (for testing):**
```python
# In src/api/worker.py, comment out MemPalace section
# if db_session and db_session.user_id:
#     try:
#         mine_session_summary(...)
#         record_classification(...)
#     except Exception as mem_error:
#         print(f"MemPalace error: {mem_error}")
```

---

### 9. Progress Tracking (`src/pipeline/progress_emitter.py`)

**Purpose:** Detailed progress updates throughout pipeline

**Key Features:**
- Per-variant tracking (starting, complete)
- Classification results in progress
- Progress calculation: VEP (25%) + Variants (70%) + Reports (5%)

**Current Progress Events:**
1. VEP starting (5%)
2. VEP complete (25%)
3. Starting variant 1/3 (25%)
4. Completed variant 1/3 with classification (48%)
5. Starting variant 2/3 (48%)
6. ... repeat ...
7. Generating reports (95%)
8. Complete (100%)

**Where to modify:**

**Add agent-level progress:**

This requires passing progress through graph state. Current implementation tracks per-variant. To add per-agent:

```python
# In src/pipeline/runner.py, around line 322
# Add to state passed to graph:
variant_state['_progress_emitter'] = progress

# In each agent file (e.g., src/agents/agent1_population.py)
# At the top of the agent function:
progress = state.get('_progress_emitter')
if progress:
    progress.agent_running('Population Frequency', 
                          state['variant_id'], 
                          state['gene'])
```

**Change progress weights:**
```python
# In src/pipeline/progress_emitter.py, __init__
self.base_progress = 0.20         # VEP takes 20% (was 25%)
self.variant_progress_range = 0.75  # Variants take 75% (was 70%)
```

---

## File Structure

```
src/
├── api/
│   ├── __init__.py
│   ├── main.py              # FastAPI app + all endpoints
│   ├── worker.py            # Celery task definitions
│   ├── models.py            # Pydantic request/response models
│   ├── auth.py              # Authentication logic
│   └── db.py                # SQLAlchemy models + DB setup
│
├── frontend/
│   ├── index.html           # Web UI structure
│   ├── styles.css           # Web UI styling
│   └── app.js               # Web UI JavaScript + SSE
│
├── mempalace/
│   ├── __init__.py
│   ├── palace.py            # Core memory operations
│   └── knowledge_graph.py   # Variant relationship tracking
│
├── pipeline/
│   ├── runner.py            # Modified to emit progress
│   └── progress_emitter.py  # Detailed progress helper
│
└── utils/
    └── logging_config.py    # ProgressCallback class (from Phase 2/3)

test_mempalace.py            # MemPalace test script
```

---

## Configuration

### Environment Variables

Create `.env` file in project root:

```bash
# Database
DATABASE_URL=postgresql://postgres@localhost/acmg_pipeline

# Redis
REDIS_URL=redis://localhost:6379/0

# LLM
LLM_BASE_URL=http://172.29.127.170:8000/v1
LLM_MODEL=Qwen/Qwen2.5-14B-Instruct
LLM_API_KEY=fake

# Optional
DEBUG=false
MAX_UPLOAD_SIZE=100000000  # 100MB
```

### Database Initialization

```bash
# One-time setup
cd /workspace/data/acmg-pipeline
conda activate acmg

export DATABASE_URL="postgresql://postgres@localhost/acmg_pipeline"

# Create tables
python src/api/db.py
```

### Redis Setup

```bash
# Start Redis server
redis-server --daemonize yes

# Verify
redis-cli ping  # Should return: PONG
```

### PostgreSQL + pgvector Setup

```bash
# Already installed in conda env
# Verify:
python -c "from src.api.db import init_db; init_db()"
```

---

## How to Use

### 1. Start Services

**Terminal 1: Celery Worker**
```bash
cd /workspace/data/acmg-pipeline
conda activate acmg

export DATABASE_URL="postgresql://postgres@localhost/acmg_pipeline"
export REDIS_URL="redis://localhost:6379/0"

python -m celery -A src.api.worker worker --loglevel=info
```

**Terminal 2: FastAPI Server**
```bash
cd /workspace/data/acmg-pipeline
conda activate acmg

export DATABASE_URL="postgresql://postgres@localhost/acmg_pipeline"

python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

### 2. Register User

```bash
curl -X POST http://localhost:8000/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "researcher@lab.edu",
    "name": "Dr. Smith",
    "organisation": "Genomics Lab"
  }'

# Response:
# {
#   "user_id": "123e4567-...",
#   "api_key": "your-api-key-save-this",
#   "message": "Account created successfully..."
# }
```

**Save the API key!** It's shown only once.

### 3. Submit Analysis

```bash
API_KEY="your-api-key"

curl -X POST http://localhost:8000/analyze \
  -H "X-API-Key: $API_KEY" \
  -F "vcf_file=@path/to/your.vcf.gz" \
  -F "genome_build=GRCh38" \
  -F "clinical_notes=Patient presents with seizures" \
  -F "proband_sex=female" \
  -F "patient_hpo_terms=HP:0001250,HP:0001263"

# Response:
# {
#   "session_id": "session_abc123",
#   "status": "queued",
#   "message": "Analysis queued successfully"
# }
```

### 4. Watch Progress (SSE)

```bash
SESSION_ID="session_abc123"

curl -N http://localhost:8000/stream/$SESSION_ID?api_key=$API_KEY
```

Output:
```
event: connected
data: {"session_id": "session_abc123"}

event: progress
data: {"stage": "vep_annotation", "progress": 0.25, "message": "VEP complete - 3 variants parsed"}

event: progress
data: {"stage": "evidence_collection", "progress": 0.48, "message": "Completed BRCA2: Likely_Pathogenic (1/3)"}

event: complete
data: {"stage": "complete", "progress": 1.0, "message": "Classification complete - 3 variants"}
```

### 5. Check Status (Polling Alternative)

```bash
curl http://localhost:8000/status/$SESSION_ID \
  -H "X-API-Key: $API_KEY"

# Response:
# {
#   "session_id": "session_abc123",
#   "status": "complete",
#   "progress_pct": 100,
#   "variant_count": 3,
#   "report_paths": {
#     "html": "/path/to/report.html",
#     "xlsx": "/path/to/report.xlsx",
#     "tsv": "/path/to/report.tsv"
#   }
# }
```

### 6. Download Reports

```bash
# HTML report
curl http://localhost:8000/download/$SESSION_ID/html \
  -H "X-API-Key: $API_KEY" \
  -o report.html

# Excel report
curl http://localhost:8000/download/$SESSION_ID/xlsx \
  -H "X-API-Key: $API_KEY" \
  -o report.xlsx

# TSV report
curl http://localhost:8000/download/$SESSION_ID/tsv \
  -H "X-API-Key: $API_KEY" \
  -o report.tsv
```

### 7. Query MemPalace

**Semantic search:**
```bash
curl "http://localhost:8000/memory/search?query=BRCA2%20pathogenic%20variants&limit=5" \
  -H "X-API-Key: $API_KEY"

# Response:
# {
#   "query": "BRCA2 pathogenic variants",
#   "results": [
#     {
#       "id": "...",
#       "wing": "analysis_history",
#       "content": "Session xyz: Analyzed 3 variants, found 1 LP in BRCA2",
#       "similarity": 0.89
#     }
#   ]
# }
```

**Gene history:**
```bash
curl "http://localhost:8000/memory/gene/BRCA2" \
  -H "X-API-Key: $API_KEY"

# Response:
# {
#   "gene": "BRCA2",
#   "variants": [
#     {
#       "variant_id": "13:32338080:A:C",
#       "current_classification": "Likely_Pathogenic",
#       "sessions": ["session_abc123", "session_xyz789"],
#       "first_seen": "2026-06-17"
#     }
#   ]
# }
```

**Variant classification history:**
```bash
curl "http://localhost:8000/memory/variant/BRCA2/13:32338080:A:C" \
  -H "X-API-Key: $API_KEY"

# Response:
# {
#   "gene": "BRCA2",
#   "variant_id": "13:32338080:A:C",
#   "history": [
#     {
#       "classification": "Likely_Pathogenic",
#       "valid_from": "2026-06-17",
#       "valid_until": null,
#       "is_current": true
#     },
#     {
#       "classification": "VUS",
#       "valid_from": "2026-05-10",
#       "valid_until": "2026-06-17",
#       "is_current": false
#     }
#   ]
# }
```

### 8. View History

```bash
curl "http://localhost:8000/history?limit=10" \
  -H "X-API-Key: $API_KEY"

# Response:
# {
#   "sessions": [
#     {
#       "session_id": "session_abc123",
#       "vcf_filename": "patient001.vcf.gz",
#       "genome_build": "GRCh38",
#       "variant_count": 3,
#       "status": "complete",
#       "created_at": "2026-06-17T10:00:00Z",
#       "classifications": {
#         "13:32338080:A:C": "Likely_Pathogenic",
#         "7:117548628:A:G": "VUS"
#       }
#     }
#   ],
#   "total": 1
# }
```

### 9. Rerun Analysis

```bash
curl -X POST "http://localhost:8000/rerun/$SESSION_ID" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "clinical_notes": "Updated: Patient also has developmental delay",
    "patient_hpo_terms": ["HP:0001250", "HP:0001263", "HP:0001252"]
  }'

# Response:
# {
#   "session_id": "session_new456",
#   "status": "queued",
#   "message": "Rerun queued successfully. New session_id: session_new456"
# }
```

---

## How to Modify

### Change API Behavior

**Add custom validation:**
```python
# In src/api/models.py
from pydantic import field_validator

class AnalyzeRequest(BaseModel):
    # ... existing fields ...
    
    @field_validator('genome_build')
    def validate_genome_build(cls, v):
        if v not in ['GRCh38', 'GRCh37']:
            raise ValueError('genome_build must be GRCh38 or GRCh37')
        return v
```

**Add new endpoint:**
```python
# In src/api/main.py
@app.get("/custom-endpoint", tags=["Custom"])
def my_custom_endpoint(
    param1: str,
    param2: Optional[int] = None,
    user: User = Depends(verify_api_key)
):
    """Your custom endpoint description."""
    return {
        "message": "Custom endpoint response",
        "param1": param1,
        "user_id": str(user.user_id)
    }
```

### Modify Database Schema

**Add field to existing table:**
```python
# 1. Edit src/api/db.py
class User(Base):
    # ... existing fields ...
    custom_field = Column(String, default="default_value")

# 2. Install alembic (if not already)
pip install alembic

# 3. Initialize alembic (first time only)
alembic init alembic

# 4. Configure alembic.ini
# Set: sqlalchemy.url = postgresql://postgres@localhost/acmg_pipeline

# 5. Generate migration
alembic revision --autogenerate -m "Add custom_field to users"

# 6. Apply migration
alembic upgrade head
```

**Or manually update:**
```sql
ALTER TABLE users ADD COLUMN custom_field VARCHAR DEFAULT 'default_value';
```

### Customize Progress Tracking

**Change progress weights:**
```python
# In src/pipeline/progress_emitter.py, __init__ method
self.base_progress = 0.20         # VEP takes 20% (was 25%)
self.variant_progress_range = 0.75  # Variants take 75% (was 70%)
```

**Add custom progress stage:**
```python
# In src/pipeline/progress_emitter.py
def my_custom_stage(self, details: str):
    """Emit progress for custom stage."""
    self._emit('my_custom_stage', 0.80, f'Running custom stage: {details}')

# In src/pipeline/runner.py
progress.my_custom_stage("Processing custom data")
```

### Change MemPalace Embedding Model

**Switch to better model:**
```python
# In src/mempalace/palace.py, _get_embedding_model()
_embedding_model = SentenceTransformer(
    'sentence-transformers/all-mpnet-base-v2',  # Better quality, 768 dims
    device='cpu'
)
```

**Note:** If changing dimensions, update database:
```sql
-- Drop old index
DROP INDEX IF EXISTS idx_palace_memories_embedding;

-- Recreate with new dimensions
CREATE INDEX idx_palace_memories_embedding ON palace_memories 
USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
```

### Customize Web UI

**Change colors:**
```css
/* In src/frontend/styles.css */
:root {
    --primary: #0066cc;        /* Change brand color */
    --success: #00aa44;        /* Change success green */
    --error: #dd0000;          /* Change error red */
}
```

**Add form field:**
```html
<!-- In src/frontend/index.html, analysis-section -->
<div class="form-group">
    <label for="custom-field">Custom Field</label>
    <input type="text" id="custom-field" name="custom-field" placeholder="Enter custom value">
</div>
```

```javascript
// In src/frontend/app.js, submitAnalysis function
formData.append('custom_field', document.getElementById('custom-field').value);
```

```python
# In src/api/main.py, /analyze endpoint
custom_field: Optional[str] = Form(None)
# Use custom_field in logic
```

### Change Celery Configuration

**Adjust timeouts and concurrency:**
```python
# In src/api/worker.py
celery_app.conf.update(
    task_time_limit=7200,          # Task timeout: 2 hours (was 1 hour)
    worker_prefetch_multiplier=2,  # Tasks to prefetch per worker
    worker_max_tasks_per_child=50, # Restart worker after N tasks (prevent memory leaks)
)
```

**Add priority queue:**
```python
# In src/api/worker.py
celery_app.conf.task_routes = {
    'analyze_variant': {'queue': 'high_priority'},
    'generate_report': {'queue': 'low_priority'},
}

# Start workers for specific queues
python -m celery -A src.api.worker worker -Q high_priority --loglevel=info
python -m celery -A src.api.worker worker -Q low_priority --loglevel=info
```

### Add Custom Memory Wing

**Create new memory category:**
```python
# In your code (e.g., src/api/worker.py or custom module)
from src.mempalace.palace import mine_memory

# Store custom memory
mine_memory(
    user_id=user_id,
    wing="custom_category",      # New wing
    room="context_identifier",
    content="Your custom memory content here",
    db=db
)

# Query custom memories
from src.mempalace.palace import search_memories

results = search_memories(
    user_id=user_id,
    query="search query",
    wing="custom_category",
    limit=5,
    db=db
)
```

**Common wing examples:**
- `analysis_history`: Session summaries
- `variants`: Variant-specific findings
- `preferences`: User preferences
- `clinical_context`: Patient histories
- `qa_decisions`: Quality assurance decisions

### Modify Report Format

**Customize HTML template:**
```python
# Reports use Jinja2 templates in src/report_templates/
# Edit src/report_templates/acmg_report.html.j2

# Change header
<h1>{{ institution_name }} - Variant Classification Report</h1>

# Add custom section
<section class="custom-section">
    <h2>Custom Analysis</h2>
    {% for variant in variants %}
        <p>{{ variant.gene }}: {{ variant.custom_field }}</p>
    {% endfor %}
</section>
```

**Add data to template context:**
```python
# In src/pipeline/nodes/report_generator.py
template_data = {
    # ... existing fields ...
    'institution_name': 'Your Lab Name',
    'custom_field': 'custom_value',
}
```

### Change Quota Limits

**Set default quota:**
```python
# In src/api/auth.py, register_user()
user = User(
    email=request.email,
    api_key_hash=api_key_hash,
    max_analyses=500,  # Change from 100 to 500
    analyses_used=0,
)
```

**Update existing user:**
```python
# Via Python
from src.api.db import SessionLocal, User

db = SessionLocal()
user = db.query(User).filter(User.email == "user@example.com").first()
user.max_analyses = 1000
db.commit()
```

```sql
-- Via SQL
UPDATE users SET max_analyses = 1000 WHERE email = 'user@example.com';
```

---

## Deployment Guide

### Conda Environment (Current Setup)

**Already configured for:**
- Linux server (Kubernetes pod)
- Conda environment: `acmg`
- Services: PostgreSQL, Redis, Celery, FastAPI

**To replicate on new server:**
```bash
# 1. Install conda
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh

# 2. Create environment
conda create -n acmg python=3.11
conda activate acmg

# 3. Install dependencies
pip install -r requirements.txt

# 4. Install system packages
conda install -c conda-forge postgresql redis vep

# 5. Initialize PostgreSQL
mkdir -p ~/postgres_data
initdb -D ~/postgres_data
pg_ctl -D ~/postgres_data -l ~/postgres_data/logfile start

# 6. Create database
createdb acmg_pipeline

# 7. Start Redis
redis-server --daemonize yes

# 8. Initialize database tables
python src/api/db.py

# 9. Start services
python -m celery -A src.api.worker worker --loglevel=info &
python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000
```

---

### Docker Deployment (Future)

**When ready to Dockerize:**

**1. Create Dockerfile:**
```dockerfile
FROM continuumio/miniconda3:latest

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create conda environment
COPY environment.yml .
RUN conda env create -f environment.yml

# Copy application
COPY . .

# Activate conda env
SHELL ["conda", "run", "-n", "acmg", "/bin/bash", "-c"]

# Install Python packages
RUN pip install -r requirements.txt

# Expose port
EXPOSE 8000

# Start services (use supervisor or docker-compose for multi-service)
CMD ["conda", "run", "-n", "acmg", "uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**2. Create docker-compose.yml:**
```yaml
version: '3.8'

services:
  postgres:
    image: pgvector/pgvector:pg17
    environment:
      POSTGRES_DB: acmg_pipeline
      POSTGRES_PASSWORD: secure_password
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"
  
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
  
  celery:
    build: .
    command: celery -A src.api.worker worker --loglevel=info
    environment:
      DATABASE_URL: postgresql://postgres:secure_password@postgres/acmg_pipeline
      REDIS_URL: redis://redis:6379/0
      LLM_BASE_URL: ${LLM_BASE_URL}
      LLM_MODEL: ${LLM_MODEL}
    depends_on:
      - postgres
      - redis
    volumes:
      - ./data:/app/data
  
  api:
    build: .
    command: uvicorn src.api.main:app --host 0.0.0.0 --port 8000
    environment:
      DATABASE_URL: postgresql://postgres:secure_password@postgres/acmg_pipeline
      REDIS_URL: redis://redis:6379/0
      LLM_BASE_URL: ${LLM_BASE_URL}
      LLM_MODEL: ${LLM_MODEL}
    depends_on:
      - postgres
      - redis
      - celery
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data

volumes:
  postgres_data:
```

**3. Build and run:**
```bash
# Build
docker-compose build

# Start all services
docker-compose up -d

# View logs
docker-compose logs -f api
docker-compose logs -f celery

# Stop
docker-compose down
```

---

### AWS Deployment

**Option 1: AWS ECS (Elastic Container Service)**

```bash
# 1. Build and push Docker image
aws ecr create-repository --repository-name acmg-pipeline
docker build -t acmg-pipeline .
docker tag acmg-pipeline:latest <account>.dkr.ecr.<region>.amazonaws.com/acmg-pipeline:latest
docker push <account>.dkr.ecr.<region>.amazonaws.com/acmg-pipeline:latest

# 2. Create task definition (JSON)
{
  "family": "acmg-pipeline",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "2048",
  "memory": "4096",
  "containerDefinitions": [
    {
      "name": "api",
      "image": "<account>.dkr.ecr.<region>.amazonaws.com/acmg-pipeline:latest",
      "portMappings": [{"containerPort": 8000}],
      "environment": [
        {"name": "DATABASE_URL", "value": "postgresql://..."},
        {"name": "REDIS_URL", "value": "redis://..."}
      ]
    }
  ]
}

# 3. Create service
aws ecs create-service \
  --cluster acmg-cluster \
  --service-name acmg-api \
  --task-definition acmg-pipeline \
  --desired-count 2 \
  --launch-type FARGATE

# 4. Use RDS for PostgreSQL, ElastiCache for Redis
```

**Option 2: AWS EC2 with Docker**

```bash
# 1. Launch EC2 instance (t3.large or larger)
# 2. Install Docker
sudo yum update -y
sudo yum install -y docker
sudo service docker start

# 3. Copy docker-compose.yml and build
docker-compose up -d

# 4. Configure security groups (ports 8000, 5432, 6379)
# 5. Set up Elastic IP or Load Balancer
```

**Option 3: AWS Lambda (for serverless)**

- Package pipeline as Lambda function
- Use API Gateway for REST endpoints
- Use SQS for job queue (instead of Celery)
- Use RDS Aurora Serverless for database
- Challenge: VEP annotation may exceed Lambda timeout (15 min)

---

### Production Checklist

Before deploying to production:

**Security:**
- [ ] Use HTTPS (Let's Encrypt, AWS Certificate Manager)
- [ ] Generate strong database passwords
- [ ] Set `DEBUG=false` in environment
- [ ] Use JWT tokens for SSE (not query params)
- [ ] Enable CORS only for trusted domains
- [ ] Rate limit API endpoints
- [ ] Regular security updates

**Database:**
- [ ] Set up automated backups (daily)
- [ ] Configure connection pooling
- [ ] Add database monitoring
- [ ] Set up read replicas (if high load)

**Monitoring:**
- [ ] Add logging aggregation (ELK stack, CloudWatch)
- [ ] Set up error tracking (Sentry)
- [ ] Monitor Celery queue length
- [ ] Track API response times
- [ ] Set up alerts for failures

**Performance:**
- [ ] Add Redis caching for repeated queries
- [ ] Optimize database indexes
- [ ] Configure Celery autoscaling
- [ ] Use CDN for static files
- [ ] Enable gzip compression

**Reliability:**
- [ ] Set up health checks
- [ ] Configure auto-restart on failure
- [ ] Add request timeouts
- [ ] Implement retry logic
- [ ] Load testing

---

## Troubleshooting

### Common Issues

**1. "Connection refused" to PostgreSQL**
```bash
# Check if PostgreSQL is running
pg_ctl status -D ~/postgres_data

# Start if not running
pg_ctl -D ~/postgres_data -l ~/postgres_data/logfile start

# Check connection
psql -d acmg_pipeline -c "SELECT 1"
```

**2. "Connection refused" to Redis**
```bash
# Check if Redis is running
redis-cli ping  # Should return: PONG

# Start if not running
redis-server --daemonize yes
```

**3. Celery worker not processing tasks**
```bash
# Check worker status
celery -A src.api.worker inspect active

# Check queue length
redis-cli LLEN celery

# Restart worker
pkill -f "celery.*worker"
python -m celery -A src.api.worker worker --loglevel=info
```

**4. "ModuleNotFoundError" errors**
```bash
# Ensure conda environment activated
conda activate acmg

# Reinstall requirements
pip install -r requirements.txt
```

**5. VEP annotation fails**
```bash
# Check VEP installation
vep --help

# Verify cache directory
ls $VEP_CACHE_DIR

# Check genome build matches cache
# GRCh38 needs homo_sapiens cache, not homo_sapiens_merged
```

**6. CUDA device error in MemPalace**
```python
# Already fixed in palace.py with CPU forcing
# If issue persists, verify:
import os
os.environ['CUDA_VISIBLE_DEVICES'] = ''
```

**7. SSE connection drops**
```javascript
// Implement fallback to polling
eventSource.onerror = (e) => {
    eventSource.close();
    // Start polling /status endpoint
};
```

**8. "Quota exceeded" error**
```sql
-- Check user quota
SELECT email, analyses_used, max_analyses FROM users WHERE email = 'user@example.com';

-- Increase quota
UPDATE users SET max_analyses = 500 WHERE email = 'user@example.com';
```

**9. Reports not generating**
```bash
# Check output directory permissions
ls -la data/output/<session_id>/reports/

# Check Celery worker logs for errors
tail -f celery_worker.log
```

**10. Slow analysis**
```bash
# Check Celery concurrency
celery -A src.api.worker inspect stats

# Increase workers
celery -A src.api.worker worker --concurrency=8

# Check LLM API latency
curl -X POST $LLM_BASE_URL/v1/chat/completions \
  -H "Authorization: Bearer $LLM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "$LLM_MODEL", "messages": [{"role": "user", "content": "test"}]}'
```

---

## Performance Tuning

### Database Optimization

```sql
-- Add indexes for common queries
CREATE INDEX idx_sessions_user_created ON sessions(user_id, created_at DESC);
CREATE INDEX idx_palace_knowledge_variant ON palace_knowledge(subject, relation);

-- Vacuum and analyze regularly
VACUUM ANALYZE;

-- Check slow queries
SELECT query, mean_exec_time, calls 
FROM pg_stat_statements 
ORDER BY mean_exec_time DESC 
LIMIT 10;
```

### Celery Optimization

```python
# In src/api/worker.py
celery_app.conf.update(
    worker_prefetch_multiplier=1,      # Prevent worker hoarding tasks
    task_acks_late=True,               # Ack after task completes
    worker_max_tasks_per_child=100,    # Restart worker after N tasks
    task_compression='gzip',           # Compress large task payloads
)
```

### Redis Optimization

```bash
# In redis.conf
maxmemory 2gb
maxmemory-policy allkeys-lru  # Evict least recently used keys

# Enable persistence (optional)
save 900 1    # Save if 1 key changed in 15 min
save 300 10   # Save if 10 keys changed in 5 min
```

---

## Additional Resources

**Documentation:**
- See [API_DOCUMENTATION.md](API_DOCUMENTATION.md) for full API reference
- See Phase 2/3 handoffs for ACMG criteria implementation details

**External References:**
- ACMG Guidelines: https://www.acmg.net/
- VEP Documentation: https://ensembl.org/vep
- HPO Browser: https://hpo.jax.org/
- ClinVar: https://www.ncbi.nlm.nih.gov/clinvar/
- gnomAD: https://gnomad.broadinstitute.org/

**Dependencies:**
- FastAPI: https://fastapi.tiangolo.com/
- Celery: https://docs.celeryq.dev/
- pgvector: https://github.com/pgvector/pgvector
- sentence-transformers: https://www.sbert.net/

---

**Last Updated:** June 2026  
**Author:** AI-Native ACMG Pipeline Team  
**Version:** 1.0