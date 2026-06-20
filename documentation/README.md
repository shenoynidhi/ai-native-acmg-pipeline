# Documentation Index

**AI-Native ACMG Variant Classification Pipeline**

Welcome to the complete documentation for the ACMG pipeline. This index helps you find the right guide for your needs.

---

## 📚 Documentation Overview

| Document | Purpose | Size | Audience |
|----------|---------|------|----------|
| [QUICK_START.md](QUICK_START.md) | Get running in 5 minutes | 5 pages | New users, quick setup |
| [STEP6_COMPLETE_GUIDE.md](STEP6_COMPLETE_GUIDE.md) | Full system documentation | 55+ pages | Developers, admins |
| [API_DOCUMENTATION.md](API_DOCUMENTATION.md) | API reference with examples | 35+ pages | API integrators |
| [STEP6_SUMMARY.md](STEP6_SUMMARY.md) | Implementation summary | 10 pages | Project managers |

**Total:** ~95 pages of comprehensive documentation

---

## 🚀 I Want To...

### Start Using the Pipeline

**➜ Read:** [QUICK_START.md](QUICK_START.md)

5-minute guide with copy-paste commands to:
- Install dependencies
- Start services
- Submit your first VCF
- Download results

---

### Integrate with the API

**➜ Read:** [API_DOCUMENTATION.md](API_DOCUMENTATION.md)

Complete API reference with:
- All 15+ endpoints documented
- Request/response examples
- Authentication flows
- Integration examples (Python, R, JavaScript, Bash)
- Error handling
- SSE real-time progress

**Quick Examples:**

**Python:**
```python
client = ACMGClient('http://localhost:8000', 'your-api-key')
session_id = client.submit_vcf('patient.vcf.gz', genome_build='GRCh38')
result = client.wait_for_completion(session_id)
client.download_report(session_id, 'html', 'report.html')
```

**Bash:**
```bash
curl -X POST http://localhost:8000/analyze \
  -H "X-API-Key: $API_KEY" \
  -F "vcf_file=@patient.vcf.gz" \
  -F "genome_build=GRCh38"
```

---

### Modify the System

**➜ Read:** [STEP6_COMPLETE_GUIDE.md](STEP6_COMPLETE_GUIDE.md) → "How to Modify" section

Learn how to:
- Add new API endpoints
- Modify database schema
- Customize progress tracking
- Change MemPalace embedding models
- Update Web UI styling
- Adjust Celery configuration
- Add custom memory wings
- Modify report templates

**Example - Add Endpoint:**
```python
# In src/api/main.py
@app.get("/my-endpoint")
def my_endpoint(user: User = Depends(verify_api_key)):
    return {"message": "Hello!"}
```

---

### Deploy to Production

**➜ Read:** [STEP6_COMPLETE_GUIDE.md](STEP6_COMPLETE_GUIDE.md) → "Deployment Guide" section

Options covered:
1. **Conda Environment** (current setup)
2. **Docker + docker-compose** (with complete Dockerfile)
3. **AWS ECS/EC2** (with configuration)
4. **Production checklist** (security, monitoring, performance)

---

### Understand the Architecture

**➜ Read:** [STEP6_COMPLETE_GUIDE.md](STEP6_COMPLETE_GUIDE.md) → "Architecture" section

Covers:
- System architecture diagram
- Data flow
- Component interactions
- Database schema
- File structure

**Quick Overview:**
```
Browser → FastAPI → Celery → Pipeline → Reports
              ↓        ↓
          Database  Redis (SSE)
              ↓
          MemPalace
```

---

### Troubleshoot Issues

**➜ Read:** [STEP6_COMPLETE_GUIDE.md](STEP6_COMPLETE_GUIDE.md) → "Troubleshooting" section

Common issues covered:
- Connection refused errors
- Celery not processing tasks
- VEP annotation failures
- SSE connection drops
- Quota exceeded
- Slow analysis

**Quick Checks:**
```bash
# PostgreSQL
pg_ctl status -D ~/postgres_data

# Redis
redis-cli ping

# Celery
celery -A src.api.worker inspect active
```

---

### See What Was Built

**➜ Read:** [STEP6_SUMMARY.md](STEP6_SUMMARY.md)

Implementation summary:
- Components delivered
- Testing results
- Files created/modified
- Known limitations
- Future enhancements
- Success criteria

**Key Stats:**
- 18 files created/modified
- ~4000 lines of code
- 15+ API endpoints
- 4 database tables
- 3 major subsystems (API, MemPalace, Web UI)

---

## 📖 Documentation by Topic

### API & Endpoints

**Primary:** [API_DOCUMENTATION.md](API_DOCUMENTATION.md)  
**Also:** [STEP6_COMPLETE_GUIDE.md](STEP6_COMPLETE_GUIDE.md) → Component 5 (FastAPI Main)

Topics:
- All endpoint specifications
- Authentication
- Request/response models
- Error handling
- Rate limits

---

### Real-Time Progress (SSE)

**Primary:** [API_DOCUMENTATION.md](API_DOCUMENTATION.md) → "Server-Sent Events" section  
**Also:** [STEP6_COMPLETE_GUIDE.md](STEP6_COMPLETE_GUIDE.md) → Component 6 (Web UI), Component 9 (Progress Tracking)

Topics:
- SSE vs polling
- Event types
- JavaScript integration
- Fallback strategies
- Progress calculation

---

### MemPalace Semantic Memory

**Primary:** [STEP6_COMPLETE_GUIDE.md](STEP6_COMPLETE_GUIDE.md) → Component 8 (MemPalace)  
**Also:** [API_DOCUMENTATION.md](API_DOCUMENTATION.md) → "MemPalace" endpoints

Topics:
- Semantic search
- Embedding models
- Wing/room structure
- Knowledge graph
- Variant history tracking

---

### Database Schema

**Primary:** [STEP6_COMPLETE_GUIDE.md](STEP6_COMPLETE_GUIDE.md) → Component 1 (Database Layer)  
**Also:** [STEP6_SUMMARY.md](STEP6_SUMMARY.md) → "Database Schema" section

Topics:
- Table definitions
- Indexes
- Relationships
- pgvector configuration
- Migration strategies

---

### Web UI

**Primary:** [STEP6_COMPLETE_GUIDE.md](STEP6_COMPLETE_GUIDE.md) → Component 6 (Web UI)  

Topics:
- HTML structure
- CSS styling
- JavaScript with SSE
- Form handling
- Customization

---

### Authentication & Security

**Primary:** [API_DOCUMENTATION.md](API_DOCUMENTATION.md) → "Authentication" section  
**Also:** [STEP6_COMPLETE_GUIDE.md](STEP6_COMPLETE_GUIDE.md) → Component 3 (Authentication)

Topics:
- API key generation
- bcrypt hashing
- Quota enforcement
- Security considerations
- Production hardening

---

### Deployment

**Primary:** [STEP6_COMPLETE_GUIDE.md](STEP6_COMPLETE_GUIDE.md) → "Deployment Guide" section  
**Quick Start:** [QUICK_START.md](QUICK_START.md)

Topics:
- Conda environment setup
- Docker configuration
- AWS deployment (ECS, EC2)
- Production checklist
- Service management

---

### Performance & Optimization

**Primary:** [STEP6_COMPLETE_GUIDE.md](STEP6_COMPLETE_GUIDE.md) → "Performance Tuning" section  
**Also:** [STEP6_SUMMARY.md](STEP6_SUMMARY.md) → "Performance Metrics"

Topics:
- Database optimization
- Celery tuning
- Redis configuration
- Caching strategies
- Load testing

---

## 🔧 Configuration Reference

### Environment Variables

```bash
# Database
DATABASE_URL=postgresql://postgres@localhost/acmg_pipeline

# Redis
REDIS_URL=redis://localhost:6379/0

# LLM
LLM_BASE_URL=http://your-vllm-server:8000/v1
LLM_MODEL=Qwen/Qwen2.5-14B-Instruct
LLM_API_KEY=fake

# Optional
DEBUG=false
MAX_UPLOAD_SIZE=100000000  # 100MB
```

**See:** [STEP6_COMPLETE_GUIDE.md](STEP6_COMPLETE_GUIDE.md) → "Configuration" section

---

### Service Commands

```bash
# Start services
python -m celery -A src.api.worker worker --loglevel=info &
python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 &

# Stop services
pkill -f "celery.*worker"
pkill -f "uvicorn.*main"

# Check status
celery -A src.api.worker inspect active
curl http://localhost:8000/health
```

**See:** [QUICK_START.md](QUICK_START.md) → "Common Commands" section

---

## 📊 Quick Reference Tables

### API Endpoints

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/register` | POST | No | Create account |
| `/analyze` | POST | Yes | Submit VCF |
| `/status/{id}` | GET | Yes | Check status |
| `/stream/{id}` | GET | Yes | SSE progress |
| `/download/{id}/{fmt}` | GET | Yes | Download report |
| `/history` | GET | Yes | List analyses |
| `/memory/search` | GET | Yes | Semantic search |
| `/memory/gene/{gene}` | GET | Yes | Gene variants |

**Full List:** [API_DOCUMENTATION.md](API_DOCUMENTATION.md) → "API Endpoints" section

---

### Classification Tiers

| Tier | Code | Meaning |
|------|------|---------|
| Pathogenic | P | Strong evidence of disease |
| Likely Pathogenic | LP | Moderate evidence of disease |
| VUS | VUS | Uncertain significance |
| Likely Benign | LB | Moderate evidence benign |
| Benign | B | Strong evidence benign |

**Full Details:** [API_DOCUMENTATION.md](API_DOCUMENTATION.md) → "Data Models" section

---

### Progress Stages

| Stage | Progress % | Description |
|-------|------------|-------------|
| `initialization` | 5% | Session setup |
| `vep_annotation` | 5-25% | VEP running |
| `evidence_collection` | 25-95% | Agent analysis |
| `report_generation` | 95-100% | Creating reports |
| `complete` | 100% | Finished |

**Full Details:** [API_DOCUMENTATION.md](API_DOCUMENTATION.md) → "Server-Sent Events" section

---

## 🎯 Use Case Guides

### Research Lab: Batch Analysis

1. Register users: [API_DOCUMENTATION.md](API_DOCUMENTATION.md) → POST /register
2. Submit multiple VCFs in parallel: Python client example
3. Monitor via SSE: JavaScript example
4. Download all reports: Bash script
5. Query patterns: MemPalace semantic search

---

### Clinical Lab: Patient Reports

1. Web UI for non-technical staff: [QUICK_START.md](QUICK_START.md)
2. Real-time progress tracking: SSE
3. HTML reports for clinicians: /download/{id}/html
4. Variant history tracking: MemPalace knowledge graph
5. Rerun with updated info: POST /rerun/{id}

---

### Bioinformatics Pipeline: Integration

1. REST API integration: [API_DOCUMENTATION.md](API_DOCUMENTATION.md) → Integration Examples
2. Programmatic submission: Python/R clients
3. TSV reports for downstream: /download/{id}/tsv
4. Semantic search for cohort analysis: /memory/search
5. Custom endpoint addition: [STEP6_COMPLETE_GUIDE.md](STEP6_COMPLETE_GUIDE.md) → How to Modify

---

## 📞 Support & Resources

### Documentation
- **This index:** Overview and navigation
- **Quick Start:** [QUICK_START.md](QUICK_START.md)
- **Complete Guide:** [STEP6_COMPLETE_GUIDE.md](STEP6_COMPLETE_GUIDE.md)
- **API Reference:** [API_DOCUMENTATION.md](API_DOCUMENTATION.md)
- **Summary:** [STEP6_SUMMARY.md](STEP6_SUMMARY.md)

### External Resources
- ACMG Guidelines: https://www.acmg.net/
- VEP Documentation: https://ensembl.org/vep
- HPO Browser: https://hpo.jax.org/
- ClinVar: https://www.ncbi.nlm.nih.gov/clinvar/
- gnomAD: https://gnomad.broadinstitute.org/

### Getting Help
1. Check [STEP6_COMPLETE_GUIDE.md](STEP6_COMPLETE_GUIDE.md) → "Troubleshooting"
2. Review [API_DOCUMENTATION.md](API_DOCUMENTATION.md) → "Error Handling"
3. See [QUICK_START.md](QUICK_START.md) → "Troubleshooting"

---

## 📝 Version History

### v1.0 (June 2026)
- ✅ Complete API layer with 15+ endpoints
- ✅ Real-time SSE progress streaming
- ✅ MemPalace semantic memory system
- ✅ Web UI with professional styling
- ✅ PostgreSQL + pgvector database
- ✅ Celery async job processing
- ✅ Multi-format reports (HTML, XLSX, TSV)
- ✅ Comprehensive documentation (95+ pages)

---

## 🗺️ Navigation Tips

**New to the pipeline?**
→ Start with [QUICK_START.md](QUICK_START.md)

**Want to integrate via API?**
→ Jump to [API_DOCUMENTATION.md](API_DOCUMENTATION.md)

**Need to customize/extend?**
→ See [STEP6_COMPLETE_GUIDE.md](STEP6_COMPLETE_GUIDE.md) → "How to Modify"

**Deploying to production?**
→ Read [STEP6_COMPLETE_GUIDE.md](STEP6_COMPLETE_GUIDE.md) → "Deployment Guide"

**Troubleshooting an issue?**
→ Check [STEP6_COMPLETE_GUIDE.md](STEP6_COMPLETE_GUIDE.md) → "Troubleshooting"

**Want implementation details?**
→ Review [STEP6_SUMMARY.md](STEP6_SUMMARY.md)

---

**Happy Analyzing! 🧬**

For the most up-to-date documentation, always check this docs/ directory.
