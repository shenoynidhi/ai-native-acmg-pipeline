# API Documentation

**ACMG Variant Classification Pipeline - REST API**  
**Version:** 1.0  
**Base URL:** `http://your-server:8000`

---

## Table of Contents

1. [Authentication](#authentication)
2. [API Endpoints](#api-endpoints)
   - [User Management](#user-management)
   - [Analysis Operations](#analysis-operations)
   - [Results & Download](#results--download)
   - [MemPalace](#mempalace)
   - [System](#system)
3. [Data Models](#data-models)
4. [Error Handling](#error-handling)
5. [Rate Limits & Quotas](#rate-limits--quotas)
6. [Server-Sent Events (SSE)](#server-sent-events-sse)
7. [Integration Examples](#integration-examples)

---

## Authentication

All protected endpoints require an API key in the `X-API-Key` header.

### Obtaining an API Key

API keys are issued during registration and shown **only once**. Store them securely.

**Request:**
```bash
POST /register
Content-Type: application/json

{
  "email": "researcher@lab.edu",
  "name": "Dr. Jane Smith",
  "organisation": "University Genomics Lab"
}
```

**Response:**
```json
{
  "user_id": "123e4567-e89b-12d3-a456-426614174000",
  "api_key": "your-secret-key-save-this-now",
  "message": "Account created successfully. Save your API key - it won't be shown again."
}
```

### Using API Keys

Include the key in all protected requests:

```bash
curl http://localhost:8000/status/session_abc123 \
  -H "X-API-Key: your-api-key"
```

### Authentication Errors

| Status | Error | Meaning |
|--------|-------|---------|
| 401 | Unauthorized | Invalid or missing API key |
| 403 | Forbidden | Account inactive |
| 429 | Too Many Requests | Quota exceeded |

---

## API Endpoints

### User Management

#### POST /register

Create a new user account and receive an API key.

**Request Body:**
```json
{
  "email": "user@example.com",      // Required, must be unique
  "name": "Dr. John Doe",            // Required
  "organisation": "Lab Name"         // Required
}
```

**Response:** `200 OK`
```json
{
  "user_id": "uuid",
  "api_key": "secret-key",
  "message": "Account created successfully. Save your API key - it won't be shown again."
}
```

**Errors:**
- `400 Bad Request`: Invalid email format or missing fields
- `409 Conflict`: Email already registered

**Example:**
```bash
curl -X POST http://localhost:8000/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "lab@genomics.edu",
    "name": "Dr. Smith",
    "organisation": "Genomics Research Center"
  }'
```

---

### Analysis Operations

#### POST /analyze

Submit a VCF file for ACMG classification.

**Authentication:** Required

**Request:** `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `vcf_file` | file | ✅ | VCF or VCF.gz file |
| `genome_build` | string | ✅ | "GRCh38" or "GRCh37" |
| `clinical_notes` | string | ❌ | Free-text clinical history |
| `patient_hpo_terms` | string | ❌ | Comma-separated HPO IDs (e.g., "HP:0001250,HP:0001263") |
| `proband_sex` | string | ❌ | "male", "female", or "unknown" (default) |
| `parent1_vcf` | file | ❌ | Maternal VCF for trio analysis |
| `parent2_vcf` | file | ❌ | Paternal VCF for trio analysis |
| `proband_bam` | file | ❌ | Proband BAM for phasing (experimental) |
| `parent1_bam` | file | ❌ | Maternal BAM for phasing |
| `parent2_bam` | file | ❌ | Paternal BAM for phasing |
| `case_database_csv` | file | ❌ | Case database for PS4 evaluation |

**Response:** `202 Accepted`
```json
{
  "session_id": "session_abc123",
  "status": "queued",
  "message": "Analysis queued successfully. Use session_id to check status."
}
```

**Errors:**
- `400 Bad Request`: Invalid file format or missing required fields
- `401 Unauthorized`: Invalid API key
- `413 Payload Too Large`: File exceeds size limit (default: 100MB)
- `429 Too Many Requests`: User quota exceeded

**Example:**
```bash
curl -X POST http://localhost:8000/analyze \
  -H "X-API-Key: your-api-key" \
  -F "vcf_file=@patient001.vcf.gz" \
  -F "genome_build=GRCh38" \
  -F "clinical_notes=Patient with early-onset breast cancer" \
  -F "proband_sex=female" \
  -F "patient_hpo_terms=HP:0003002,HP:0001250"
```

**HPO Term Format:**
- Comma-separated list: `HP:0001250,HP:0001263`
- Or newline-separated: `HP:0001250\nHP:0001263`
- See: https://hpo.jax.org/

---

#### GET /status/{session_id}

Check analysis status and retrieve results.

**Authentication:** Required

**Path Parameters:**
- `session_id`: Session ID from `/analyze` response

**Response:** `200 OK`
```json
{
  "session_id": "session_abc123",
  "status": "complete",                    // queued | running | complete | failed
  "progress_pct": 100,
  "variant_count": 3,
  "vcf_filename": "patient001.vcf.gz",
  "genome_build": "GRCh38",
  "created_at": "2026-06-17T10:00:00Z",
  "completed_at": "2026-06-17T10:15:23Z",
  "report_paths": {
    "html": "/workspace/.../report.html",
    "xlsx": "/workspace/.../report.xlsx",
    "tsv": "/workspace/.../report.tsv"
  },
  "classifications": {
    "13:32338080:A:C": "Likely_Pathogenic",
    "7:117548628:A:G": "VUS",
    "1:69091:A:T": "Benign"
  },
  "error_message": null
}
```

**Status Values:**
- `queued`: Waiting in Celery queue
- `running`: Currently processing
- `complete`: Successfully finished
- `failed`: Error occurred (see `error_message`)

**Errors:**
- `404 Not Found`: Session ID doesn't exist
- `403 Forbidden`: Session belongs to different user

**Example:**
```bash
curl http://localhost:8000/status/session_abc123 \
  -H "X-API-Key: your-api-key"
```

---

#### GET /stream/{session_id}

Stream real-time progress updates via Server-Sent Events (SSE).

**Authentication:** Query parameter (EventSource limitation)

**URL:** `/stream/{session_id}?api_key={your_api_key}`

**Response:** `text/event-stream`

**Event Types:**

1. **connected** - Initial connection confirmation
```
event: connected
data: {"session_id": "session_abc123"}
```

2. **progress** - Progress update
```
event: progress
data: {
  "stage": "evidence_collection",
  "progress": 0.48,
  "message": "Completed BRCA2: Likely_Pathogenic (1/3)",
  "variant_id": "13:32338080:A:C",
  "gene": "BRCA2",
  "timestamp": "2026-06-17T10:05:32.123456"
}
```

3. **complete** - Analysis finished
```
event: complete
data: {
  "stage": "complete",
  "progress": 1.0,
  "message": "Classification complete - 3 variants",
  "timestamp": "2026-06-17T10:15:23.987654"
}
```

4. **failed** - Analysis failed
```
event: failed
data: {
  "stage": "failed",
  "message": "VEP annotation failed: invalid chromosome",
  "timestamp": "2026-06-17T10:02:15.123456"
}
```

**Stage Values:**
- `initialization`: Session setup
- `vep_annotation`: VEP annotation running
- `evidence_collection`: Processing variants through agents
- `report_generation`: Generating final reports
- `complete`: Finished successfully
- `failed`: Error occurred

**Progress Values:**
- `0.0 - 0.25`: VEP annotation
- `0.25 - 0.95`: Variant processing (agents + debate)
- `0.95 - 1.0`: Report generation

**JavaScript Example:**
```javascript
const eventSource = new EventSource(
  `/stream/session_abc123?api_key=${apiKey}`
);

eventSource.addEventListener('connected', (e) => {
  console.log('Connected:', JSON.parse(e.data));
});

eventSource.addEventListener('progress', (e) => {
  const data = JSON.parse(e.data);
  updateProgressBar(data.progress * 100);
  console.log(`${data.stage}: ${data.message}`);
});

eventSource.addEventListener('complete', (e) => {
  eventSource.close();
  fetchResults();
});

eventSource.onerror = (e) => {
  eventSource.close();
  console.error('SSE error:', e);
  // Fall back to polling /status
};
```

**cURL Example:**
```bash
curl -N http://localhost:8000/stream/session_abc123?api_key=your-key
```

**Security Note:**  
API key is in query param due to EventSource browser API limitations. For production, use short-lived tokens:
1. Return SSE token in `/analyze` response
2. Token expires in 1 hour
3. Validate token in `/stream` endpoint

---

#### GET /history

List all past analyses for the authenticated user.

**Authentication:** Required

**Query Parameters:**
- `limit` (optional): Max results, default 20, max 100
- `status` (optional): Filter by status (queued | running | complete | failed)

**Response:** `200 OK`
```json
{
  "sessions": [
    {
      "session_id": "session_abc123",
      "vcf_filename": "patient001.vcf.gz",
      "genome_build": "GRCh38",
      "variant_count": 3,
      "status": "complete",
      "created_at": "2026-06-17T10:00:00Z",
      "completed_at": "2026-06-17T10:15:23Z",
      "classifications": {
        "13:32338080:A:C": "Likely_Pathogenic",
        "7:117548628:A:G": "VUS"
      }
    },
    {
      "session_id": "session_xyz789",
      "vcf_filename": "patient002.vcf.gz",
      "genome_build": "GRCh37",
      "variant_count": 5,
      "status": "running",
      "progress_pct": 67,
      "created_at": "2026-06-17T11:00:00Z"
    }
  ],
  "total": 2,
  "limit": 20
}
```

**Example:**
```bash
# Get last 10 analyses
curl "http://localhost:8000/history?limit=10" \
  -H "X-API-Key: your-api-key"

# Get only completed analyses
curl "http://localhost:8000/history?status=complete" \
  -H "X-API-Key: your-api-key"
```

---

#### POST /rerun/{session_id}

Rerun an existing analysis with modified parameters (e.g., updated clinical notes, different HPO terms).

**Authentication:** Required

**Path Parameters:**
- `session_id`: Original session ID to rerun

**Request Body:** (all fields optional)
```json
{
  "clinical_notes": "Updated clinical information",
  "patient_hpo_terms": ["HP:0001250", "HP:0001263", "HP:0001252"],
  "proband_sex": "female"
}
```

**Response:** `202 Accepted`
```json
{
  "session_id": "session_new456",
  "original_session_id": "session_abc123",
  "status": "queued",
  "message": "Rerun queued successfully. New session_id: session_new456",
  "overrides": {
    "clinical_notes": "Updated clinical information",
    "patient_hpo_terms": ["HP:0001250", "HP:0001263", "HP:0001252"]
  }
}
```

**Notes:**
- VCF files are reused from original session (not re-uploaded)
- Only specified parameters are overridden; others use original values
- Creates a new session with new session_id
- Quota is consumed for the new analysis

**Errors:**
- `404 Not Found`: Original session doesn't exist
- `403 Forbidden`: Session belongs to different user
- `429 Too Many Requests`: Quota exceeded

**Example:**
```bash
curl -X POST http://localhost:8000/rerun/session_abc123 \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "clinical_notes": "Patient also presents with developmental delay",
    "patient_hpo_terms": ["HP:0001250", "HP:0001263", "HP:0012758"]
  }'
```

---

### Results & Download

#### GET /download/{session_id}/{format}

Download analysis report in specified format.

**Authentication:** Required

**Path Parameters:**
- `session_id`: Session ID
- `format`: `html` | `xlsx` | `tsv`

**Response:** File download with appropriate MIME type

| Format | MIME Type | Description |
|--------|-----------|-------------|
| `html` | `text/html` | Interactive HTML report with styling |
| `xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | Excel workbook |
| `tsv` | `text/tab-separated-values` | Tab-delimited text |

**Errors:**
- `404 Not Found`: Session doesn't exist or report not generated
- `403 Forbidden`: Session belongs to different user
- `425 Too Early`: Analysis still running (status != complete)

**Examples:**
```bash
# Download HTML report
curl http://localhost:8000/download/session_abc123/html \
  -H "X-API-Key: your-api-key" \
  -o report.html

# Download Excel report
curl http://localhost:8000/download/session_abc123/xlsx \
  -H "X-API-Key: your-api-key" \
  -o report.xlsx

# Download TSV report
curl http://localhost:8000/download/session_abc123/tsv \
  -H "X-API-Key: your-api-key" \
  -o report.tsv
```

**Report Contents:**

All formats include:
- Variant identification (chr, pos, ref, alt, gene)
- VEP annotations (consequence, transcript, protein change)
- ACMG classification (Pathogenic, Likely Pathogenic, VUS, Likely Benign, Benign)
- ACMG criteria applied (e.g., PVS1, PM2, PP3)
- Population frequencies (gnomAD, 1000 Genomes)
- In silico predictions (CADD, REVEL, SpliceAI)
- ClinVar evidence
- Phenotype match scores
- Evidence summary (plain English explanation)

HTML report additionally includes:
- Interactive table with sorting/filtering
- Color-coded classifications
- Expandable evidence sections
- HPO term mapping
- Gene-disease associations

---

### MemPalace

MemPalace provides semantic search and knowledge graph tracking of all your analyses.

#### GET /memory/search

Semantic search across all stored memories.

**Authentication:** Required

**Query Parameters:**
- `query` (required): Search query text
- `wing` (optional): Filter by memory category (analysis_history | variants | preferences)
- `limit` (optional): Max results, default 10, max 50

**Response:** `200 OK`
```json
{
  "query": "BRCA2 pathogenic variants",
  "results": [
    {
      "id": "mem_uuid_123",
      "wing": "analysis_history",
      "room": "session_abc123",
      "content": "Session session_abc123: Analyzed 3 variants on GRCh38. Found 1 Likely_Pathogenic in BRCA2 (13:32338080:A:C) with PM2+PP3 criteria.",
      "created_at": "2026-06-17T10:15:23Z",
      "similarity": 0.89
    },
    {
      "id": "mem_uuid_456",
      "wing": "variants",
      "room": "BRCA2",
      "content": "BRCA2 variant 13:32338080:A:C classified as Likely_Pathogenic. Missense p.Arg2520His, absent from gnomAD, CADD 28.4.",
      "created_at": "2026-06-15T14:22:10Z",
      "similarity": 0.85
    }
  ],
  "count": 2
}
```

**Similarity Scores:**
- `0.9 - 1.0`: Highly relevant, nearly identical semantics
- `0.7 - 0.9`: Relevant, similar concepts
- `0.5 - 0.7`: Somewhat relevant
- `< 0.5`: Low relevance (usually filtered out)

**Example:**
```bash
# Search all memories
curl "http://localhost:8000/memory/search?query=missense%20variants%20in%20cancer%20genes&limit=5" \
  -H "X-API-Key: your-api-key"

# Search only analysis history
curl "http://localhost:8000/memory/search?query=recent%20pathogenic%20findings&wing=analysis_history" \
  -H "X-API-Key: your-api-key"
```

**Use Cases:**
- Find similar past cases
- Check if variant was analyzed before
- Retrieve clinical context from previous analyses
- Discover patterns across analyses

---

#### GET /memory/gene/{gene}

Get all variants in a specific gene with current classifications.

**Authentication:** Required

**Path Parameters:**
- `gene`: Gene symbol (e.g., BRCA2, CFTR, TP53)

**Response:** `200 OK`
```json
{
  "gene": "BRCA2",
  "variants": [
    {
      "variant_id": "13:32338080:A:C",
      "gene": "BRCA2",
      "current_classification": "Likely_Pathogenic",
      "sessions": ["session_abc123", "session_xyz789"],
      "first_seen": "2026-05-10T00:00:00Z",
      "last_seen": "2026-06-17T00:00:00Z"
    },
    {
      "variant_id": "13:32340800:G:T",
      "gene": "BRCA2",
      "current_classification": "VUS",
      "sessions": ["session_def456"],
      "first_seen": "2026-06-01T00:00:00Z",
      "last_seen": "2026-06-01T00:00:00Z"
    }
  ],
  "count": 2
}
```

**Example:**
```bash
curl "http://localhost:8000/memory/gene/BRCA2" \
  -H "X-API-Key: your-api-key"
```

---

#### GET /memory/variant/{gene}/{variant_id}

Get full classification history for a specific variant.

**Authentication:** Required

**Path Parameters:**
- `gene`: Gene symbol
- `variant_id`: Variant ID (chr:pos:ref:alt format)

**Response:** `200 OK`
```json
{
  "gene": "BRCA2",
  "variant_id": "13:32338080:A:C",
  "history": [
    {
      "classification": "Likely_Pathogenic",
      "valid_from": "2026-06-17T00:00:00Z",
      "valid_until": null,
      "is_current": true,
      "session_id": "session_abc123"
    },
    {
      "classification": "VUS",
      "valid_from": "2026-05-10T00:00:00Z",
      "valid_until": "2026-06-17T00:00:00Z",
      "is_current": false,
      "session_id": "session_old789"
    }
  ]
}
```

**Notes:**
- `valid_until: null` means currently active classification
- History shows how classification changed over time
- Useful for tracking reclassifications as evidence accumulates

**Example:**
```bash
curl "http://localhost:8000/memory/variant/BRCA2/13:32338080:A:C" \
  -H "X-API-Key: your-api-key"
```

---

#### GET /memory/recent

Get recent analyses with variant summaries.

**Authentication:** Required

**Query Parameters:**
- `limit` (optional): Max results, default 10, max 50

**Response:** `200 OK`
```json
{
  "recent_analyses": [
    {
      "session_id": "session_abc123",
      "date": "2026-06-17T00:00:00Z",
      "variants": [
        "BRCA2:13:32338080:A:C",
        "CFTR:7:117548628:A:G",
        "OR4F5:1:69091:A:T"
      ]
    },
    {
      "session_id": "session_xyz789",
      "date": "2026-06-15T00:00:00Z",
      "variants": [
        "TP53:17:7577120:C:T"
      ]
    }
  ],
  "count": 2
}
```

**Example:**
```bash
curl "http://localhost:8000/memory/recent?limit=5" \
  -H "X-API-Key: your-api-key"
```

---

### System

#### GET /health

Health check endpoint (no authentication required).

**Response:** `200 OK`
```json
{
  "status": "healthy",
  "database": "connected",
  "redis": "connected",
  "celery_workers": 2,
  "timestamp": "2026-06-17T10:30:00Z"
}
```

**Status Values:**
- `healthy`: All services operational
- `degraded`: Some services down (still functional)
- `unhealthy`: Critical services unavailable

**Example:**
```bash
curl http://localhost:8000/health
```

---

#### GET /

Web UI homepage (no authentication required).

Returns the HTML web interface for browser-based usage.

---

#### GET /docs

Interactive API documentation (Swagger UI).

Auto-generated OpenAPI documentation with request/response examples and try-it-out functionality.

**URL:** `http://localhost:8000/docs`

---

## Data Models

### VariantClassification

ACMG classification categories:

| Classification | Abbreviation | Meaning |
|----------------|--------------|---------|
| Pathogenic | P | Strong evidence of disease causation |
| Likely Pathogenic | LP | Moderate evidence of disease causation |
| VUS | VUS | Uncertain Significance |
| Likely Benign | LB | Moderate evidence of benign impact |
| Benign | B | Strong evidence of benign impact |

### ACMG Criteria

Evidence codes applied during classification:

**Very Strong:**
- `PVS1`: Null variant in gene with LoF as mechanism

**Strong:**
- `PS1`: Same amino acid change, known pathogenic
- `PS2`: De novo (confirmed paternity/maternity)
- `PS3`: Functional studies show damaging effect
- `PS4`: Prevalence in affected > controls

**Moderate:**
- `PM1`: Mutational hot spot or critical domain
- `PM2`: Absent/rare in population databases
- `PM3`: Compound heterozygous with pathogenic variant
- `PM4`: Protein length change (in-frame indel)
- `PM5`: Different amino acid, known pathogenic
- `PM6`: De novo (paternity/maternity not confirmed)

**Supporting:**
- `PP1`: Segregation in multiple affected family members
- `PP2`: Missense in gene with low benign missense
- `PP3`: In silico predictions support damage
- `PP4`: Patient phenotype highly specific for gene
- `PP5`: Reported pathogenic by reputable source

**Benign:**
- `BA1`: Allele frequency > 5% in population
- `BS1`: Allele frequency too high for disorder
- `BS2`: Healthy adult with recessive condition
- `BS3`: Functional studies show no damaging effect
- `BS4`: Non-segregation in affected family

**Supporting Benign:**
- `BP1`: Missense in gene with many benign missense
- `BP2`: Trans with pathogenic, phenotype inconsistent
- `BP3`: In-frame indel in repetitive region
- `BP4`: In silico predictions support benign
- `BP5`: Alternate molecular basis for phenotype
- `BP7`: Silent variant, splicing predictions benign

### HPO Terms

Human Phenotype Ontology identifiers (e.g., `HP:0001250`).

**Common Examples:**
- `HP:0001250`: Seizures
- `HP:0001263`: Global developmental delay
- `HP:0003002`: Breast carcinoma
- `HP:0012758`: Neurodevelopmental delay

**Browse:** https://hpo.jax.org/

---

## Error Handling

### Standard Error Response

```json
{
  "detail": "Error message explaining what went wrong",
  "error_code": "SPECIFIC_ERROR_CODE",
  "timestamp": "2026-06-17T10:30:00Z"
}
```

### HTTP Status Codes

| Code | Meaning | Common Causes |
|------|---------|---------------|
| 200 | OK | Request succeeded |
| 202 | Accepted | Async job queued |
| 400 | Bad Request | Invalid input, malformed JSON |
| 401 | Unauthorized | Missing or invalid API key |
| 403 | Forbidden | Valid key but access denied |
| 404 | Not Found | Resource doesn't exist |
| 409 | Conflict | Duplicate resource (e.g., email) |
| 413 | Payload Too Large | File exceeds size limit |
| 422 | Unprocessable Entity | Validation error |
| 425 | Too Early | Resource not ready yet |
| 429 | Too Many Requests | Quota exceeded |
| 500 | Internal Server Error | Server-side error |
| 503 | Service Unavailable | Database or Celery down |

### Error Codes

| Code | Description | Resolution |
|------|-------------|------------|
| `INVALID_API_KEY` | API key not found | Check key spelling |
| `QUOTA_EXCEEDED` | Analysis limit reached | Contact admin to increase quota |
| `INVALID_VCF` | VCF format error | Validate VCF with vcf-validator |
| `VEP_FAILED` | VEP annotation error | Check genome build, VCF format |
| `SESSION_NOT_FOUND` | Session ID doesn't exist | Verify session_id |
| `ANALYSIS_STILL_RUNNING` | Report not ready | Wait for status=complete |
| `DATABASE_ERROR` | Database connection failed | Retry or contact support |

---

## Rate Limits & Quotas

### Default Quotas (Per User)

- **Analyses:** 100 per account lifetime
- **Concurrent Jobs:** 5 maximum
- **Upload Size:** 100 MB per VCF
- **API Calls:** 1000 per hour

### Increasing Quotas

Contact your administrator to increase limits. Quotas are set in the database:

```sql
UPDATE users SET max_analyses = 500 WHERE email = 'user@example.com';
```

### Checking Your Usage

```bash
curl http://localhost:8000/status/session_latest \
  -H "X-API-Key: your-api-key"
```

Response includes:
```json
{
  "user_quota": {
    "analyses_used": 45,
    "analyses_limit": 100,
    "analyses_remaining": 55
  }
}
```

---

## Server-Sent Events (SSE)

### Why SSE?

- **Real-time updates** without polling
- **Efficient** - single connection, server pushes updates
- **Native browser support** via `EventSource` API
- **Automatic reconnection** on disconnect

### SSE vs Polling

**Polling (Old Way):**
```javascript
// Poll every 2 seconds
setInterval(async () => {
  const status = await fetch(`/status/${sessionId}`);
  updateUI(await status.json());
}, 2000);
```

**SSE (Current Implementation):**
```javascript
// Connect once, receive push updates
const eventSource = new EventSource(`/stream/${sessionId}?api_key=${key}`);
eventSource.addEventListener('progress', (e) => {
  updateUI(JSON.parse(e.data));
});
```

### Progress Events Timeline

Typical event sequence for 3-variant analysis:

```
event: connected
data: {"session_id": "..."}

event: progress
data: {"stage": "vep_annotation", "progress": 0.05, "message": "Starting VEP annotation"}

event: progress
data: {"stage": "vep_annotation", "progress": 0.25, "message": "VEP complete - 3 variants parsed"}

event: progress
data: {"stage": "evidence_collection", "progress": 0.25, "message": "Starting analysis of BRCA2 variant (1/3)"}

event: progress
data: {"stage": "evidence_collection", "progress": 0.48, "message": "Completed BRCA2: Likely_Pathogenic (1/3)"}

event: progress
data: {"stage": "evidence_collection", "progress": 0.48, "message": "Starting analysis of CFTR variant (2/3)"}

event: progress
data: {"stage": "evidence_collection", "progress": 0.72, "message": "Completed CFTR: Likely_Pathogenic (2/3)"}

event: progress
data: {"stage": "evidence_collection", "progress": 0.72, "message": "Starting analysis of OR4F5 variant (3/3)"}

event: progress
data: {"stage": "evidence_collection", "progress": 0.95, "message": "Completed OR4F5: VUS (3/3)"}

event: progress
data: {"stage": "report_generation", "progress": 0.95, "message": "Generating TSV, XLSX, and HTML reports"}

event: complete
data: {"stage": "complete", "progress": 1.0, "message": "Classification complete - 3 variants"}
```

### Handling Disconnections

SSE connections can drop due to network issues, server restarts, or timeouts. Implement fallback:

```javascript
const eventSource = new EventSource(url);
let lastProgress = 0;

eventSource.addEventListener('progress', (e) => {
  const data = JSON.parse(e.data);
  lastProgress = data.progress;
  updateUI(data);
});

eventSource.onerror = (e) => {
  console.warn('SSE connection lost, falling back to polling');
  eventSource.close();
  
  // Fall back to polling
  const pollInterval = setInterval(async () => {
    const status = await fetch(`/status/${sessionId}`, {
      headers: {'X-API-Key': apiKey}
    });
    const data = await status.json();
    
    if (data.status === 'complete' || data.status === 'failed') {
      clearInterval(pollInterval);
      handleCompletion(data);
    } else {
      updateUI(data);
    }
  }, 3000);  // Poll every 3 seconds
};
```

---

## Integration Examples

### Python Client

```python
import requests
from typing import Optional, Dict

class ACMGClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.headers = {'X-API-Key': api_key}
    
    def submit_vcf(
        self,
        vcf_path: str,
        genome_build: str,
        clinical_notes: Optional[str] = None,
        hpo_terms: Optional[list] = None,
        proband_sex: str = "unknown"
    ) -> str:
        """Submit VCF for analysis, return session_id."""
        files = {'vcf_file': open(vcf_path, 'rb')}
        data = {
            'genome_build': genome_build,
            'proband_sex': proband_sex
        }
        
        if clinical_notes:
            data['clinical_notes'] = clinical_notes
        if hpo_terms:
            data['patient_hpo_terms'] = ','.join(hpo_terms)
        
        response = requests.post(
            f'{self.base_url}/analyze',
            headers=self.headers,
            files=files,
            data=data
        )
        response.raise_for_status()
        return response.json()['session_id']
    
    def get_status(self, session_id: str) -> Dict:
        """Get analysis status."""
        response = requests.get(
            f'{self.base_url}/status/{session_id}',
            headers=self.headers
        )
        response.raise_for_status()
        return response.json()
    
    def wait_for_completion(self, session_id: str, poll_interval: int = 5) -> Dict:
        """Poll until analysis completes."""
        import time
        
        while True:
            status = self.get_status(session_id)
            
            if status['status'] == 'complete':
                return status
            elif status['status'] == 'failed':
                raise Exception(f"Analysis failed: {status.get('error_message')}")
            
            print(f"Progress: {status['progress_pct']}%")
            time.sleep(poll_interval)
    
    def download_report(self, session_id: str, format: str, output_path: str):
        """Download report to file."""
        response = requests.get(
            f'{self.base_url}/download/{session_id}/{format}',
            headers=self.headers
        )
        response.raise_for_status()
        
        with open(output_path, 'wb') as f:
            f.write(response.content)
    
    def search_memory(self, query: str, limit: int = 10) -> list:
        """Semantic search of past analyses."""
        response = requests.get(
            f'{self.base_url}/memory/search',
            headers=self.headers,
            params={'query': query, 'limit': limit}
        )
        response.raise_for_status()
        return response.json()['results']


# Usage
client = ACMGClient('http://localhost:8000', 'your-api-key')

# Submit analysis
session_id = client.submit_vcf(
    vcf_path='patient001.vcf.gz',
    genome_build='GRCh38',
    clinical_notes='Early-onset breast cancer',
    hpo_terms=['HP:0003002', 'HP:0001250']
)

print(f"Analysis submitted: {session_id}")

# Wait for completion
result = client.wait_for_completion(session_id)
print(f"Complete! {result['variant_count']} variants classified")

# Download HTML report
client.download_report(session_id, 'html', 'report.html')

# Search past analyses
similar = client.search_memory('BRCA2 pathogenic variants')
for memory in similar:
    print(f"  [{memory['similarity']:.2f}] {memory['content'][:80]}")
```

---

### R Client

```r
library(httr)
library(jsonlite)

analyze_vcf <- function(vcf_path, api_key, base_url = "http://localhost:8000", 
                        genome_build = "GRCh38", clinical_notes = NULL, 
                        hpo_terms = NULL) {
  
  # Prepare upload
  body <- list(
    vcf_file = upload_file(vcf_path),
    genome_build = genome_build
  )
  
  if (!is.null(clinical_notes)) body$clinical_notes <- clinical_notes
  if (!is.null(hpo_terms)) body$patient_hpo_terms <- paste(hpo_terms, collapse = ",")
  
  # Submit
  response <- POST(
    url = paste0(base_url, "/analyze"),
    add_headers(`X-API-Key` = api_key),
    body = body,
    encode = "multipart"
  )
  
  stop_for_status(response)
  content(response)$session_id
}

get_status <- function(session_id, api_key, base_url = "http://localhost:8000") {
  response <- GET(
    url = paste0(base_url, "/status/", session_id),
    add_headers(`X-API-Key` = api_key)
  )
  
  stop_for_status(response)
  content(response)
}

wait_for_completion <- function(session_id, api_key, base_url = "http://localhost:8000", 
                                poll_interval = 5) {
  repeat {
    status <- get_status(session_id, api_key, base_url)
    
    if (status$status == "complete") {
      return(status)
    } else if (status$status == "failed") {
      stop(paste("Analysis failed:", status$error_message))
    }
    
    cat(sprintf("Progress: %d%%\n", status$progress_pct))
    Sys.sleep(poll_interval)
  }
}

download_report <- function(session_id, format, output_path, api_key, 
                           base_url = "http://localhost:8000") {
  response <- GET(
    url = paste0(base_url, "/download/", session_id, "/", format),
    add_headers(`X-API-Key` = api_key),
    write_disk(output_path, overwrite = TRUE)
  )
  
  stop_for_status(response)
  output_path
}

# Usage
api_key <- "your-api-key"

session_id <- analyze_vcf(
  vcf_path = "patient001.vcf.gz",
  api_key = api_key,
  genome_build = "GRCh38",
  clinical_notes = "Early-onset breast cancer",
  hpo_terms = c("HP:0003002", "HP:0001250")
)

cat("Analysis submitted:", session_id, "\n")

result <- wait_for_completion(session_id, api_key)
cat("Complete!", result$variant_count, "variants classified\n")

download_report(session_id, "xlsx", "report.xlsx", api_key)
```

---

### Bash Script

```bash
#!/bin/bash
# acmg_analyze.sh - Submit VCF and download results

set -e

BASE_URL="http://localhost:8000"
API_KEY="your-api-key"
VCF_FILE="$1"
GENOME_BUILD="${2:-GRCh38}"

if [ -z "$VCF_FILE" ]; then
    echo "Usage: $0 <vcf_file> [genome_build]"
    exit 1
fi

echo "Submitting $VCF_FILE for analysis..."

# Submit analysis
RESPONSE=$(curl -s -X POST "$BASE_URL/analyze" \
    -H "X-API-Key: $API_KEY" \
    -F "vcf_file=@$VCF_FILE" \
    -F "genome_build=$GENOME_BUILD")

SESSION_ID=$(echo "$RESPONSE" | jq -r '.session_id')
echo "Session ID: $SESSION_ID"

# Poll for completion
echo "Waiting for completion..."
while true; do
    STATUS_RESPONSE=$(curl -s "$BASE_URL/status/$SESSION_ID" \
        -H "X-API-Key: $API_KEY")
    
    STATUS=$(echo "$STATUS_RESPONSE" | jq -r '.status')
    PROGRESS=$(echo "$STATUS_RESPONSE" | jq -r '.progress_pct')
    
    if [ "$STATUS" = "complete" ]; then
        echo "✓ Analysis complete!"
        break
    elif [ "$STATUS" = "failed" ]; then
        echo "✗ Analysis failed"
        echo "$STATUS_RESPONSE" | jq '.error_message'
        exit 1
    fi
    
    echo "Progress: $PROGRESS%"
    sleep 5
done

# Download reports
echo "Downloading reports..."
for FORMAT in html xlsx tsv; do
    curl -s "$BASE_URL/download/$SESSION_ID/$FORMAT" \
        -H "X-API-Key: $API_KEY" \
        -o "report_${SESSION_ID}.${FORMAT}"
    echo "  ✓ report_${SESSION_ID}.${FORMAT}"
done

echo "Done!"
```

---

### JavaScript/Node.js

```javascript
const FormData = require('form-data');
const fs = require('fs');
const axios = require('axios');

class ACMGClient {
  constructor(baseUrl, apiKey) {
    this.baseUrl = baseUrl;
    this.apiKey = apiKey;
    this.axios = axios.create({
      baseURL: baseUrl,
      headers: {'X-API-Key': apiKey}
    });
  }
  
  async submitVCF(vcfPath, options = {}) {
    const form = new FormData();
    form.append('vcf_file', fs.createReadStream(vcfPath));
    form.append('genome_build', options.genomeBuild || 'GRCh38');
    
    if (options.clinicalNotes) {
      form.append('clinical_notes', options.clinicalNotes);
    }
    if (options.hpoTerms) {
      form.append('patient_hpo_terms', options.hpoTerms.join(','));
    }
    
    const response = await this.axios.post('/analyze', form, {
      headers: form.getHeaders()
    });
    
    return response.data.session_id;
  }
  
  async getStatus(sessionId) {
    const response = await this.axios.get(`/status/${sessionId}`);
    return response.data;
  }
  
  async waitForCompletion(sessionId, pollInterval = 5000) {
    while (true) {
      const status = await this.getStatus(sessionId);
      
      if (status.status === 'complete') {
        return status;
      } else if (status.status === 'failed') {
        throw new Error(`Analysis failed: ${status.error_message}`);
      }
      
      console.log(`Progress: ${status.progress_pct}%`);
      await new Promise(resolve => setTimeout(resolve, pollInterval));
    }
  }
  
  async downloadReport(sessionId, format, outputPath) {
    const response = await this.axios.get(`/download/${sessionId}/${format}`, {
      responseType: 'stream'
    });
    
    return new Promise((resolve, reject) => {
      const writer = fs.createWriteStream(outputPath);
      response.data.pipe(writer);
      writer.on('finish', resolve);
      writer.on('error', reject);
    });
  }
  
  async searchMemory(query, limit = 10) {
    const response = await this.axios.get('/memory/search', {
      params: {query, limit}
    });
    return response.data.results;
  }
}

// Usage
(async () => {
  const client = new ACMGClient('http://localhost:8000', 'your-api-key');
  
  // Submit analysis
  const sessionId = await client.submitVCF('patient001.vcf.gz', {
    genomeBuild: 'GRCh38',
    clinicalNotes: 'Early-onset breast cancer',
    hpoTerms: ['HP:0003002', 'HP:0001250']
  });
  
  console.log(`Analysis submitted: ${sessionId}`);
  
  // Wait for completion
  const result = await client.waitForCompletion(sessionId);
  console.log(`Complete! ${result.variant_count} variants classified`);
  
  // Download reports
  await client.downloadReport(sessionId, 'html', 'report.html');
  await client.downloadReport(sessionId, 'xlsx', 'report.xlsx');
  
  // Search past analyses
  const similar = await client.searchMemory('BRCA2 pathogenic variants');
  similar.forEach(memory => {
    console.log(`  [${memory.similarity.toFixed(2)}] ${memory.content.slice(0, 80)}`);
  });
})();
```

---

## Support

For questions, bug reports, or feature requests:
- **Documentation:** See [docs/STEP6_COMPLETE_GUIDE.md](STEP6_COMPLETE_GUIDE.md)
- **GitHub:** [your-repo-url]
- **Email:** [your-contact-email]

---

**Last Updated:** June 2026  
**API Version:** 1.0
