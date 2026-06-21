# Quick Start: AWS Environment Setup

## TL;DR

```bash
# 1. Choose your environment file
# CPU-only: acmg_minimal.yml
# GPU-enabled: acmg_aws_gpu.yml

# 2. Create environment
conda env create -f acmg_minimal.yml

# 3. Activate
conda activate acmg

# 4. Verify
python verify_environment.py

# 5. Run tests
pytest tests/ -v
```

---

## What Changed?

Your original `acmg.yml` had **450+ packages** (including all transitive dependencies). This caused conflicts on AWS.

The new minimal YAML files contain **only ~60 direct dependencies** that your code actually imports. Conda automatically resolves the rest.

### ✅ Kept (Direct Dependencies)
- Bioinformatics: `bcftools`, `samtools`, `pysam`, `cyvcf2`, `biopython`
- AI/ML: `langchain`, `langgraph`, `chromadb`, `sentence-transformers`, `torch`
- Web: `fastapi`, `uvicorn`, `celery`, `sqlalchemy`, `redis`
- Clinical: `hpo3`, `clinphen` (manual install)
- Utils: `pandas`, `numpy`, `scipy`, `pydantic`, `requests`

### ❌ Removed (Transitive Dependencies)
- 30+ `nvidia-*` packages (auto-installed by torch if needed)
- 20+ `opentelemetry-*` packages (auto-installed by observability tools)
- 50+ system libraries (`libgcc`, `libcurl`, `libxml2`, etc.)
- Unused AI tools (`anthropic`, `vllm`, `ray`, `xformers`)

---

## Files Created

1. **`acmg_minimal.yml`** - CPU-only environment (recommended)
2. **`acmg_aws_gpu.yml`** - GPU-enabled environment (for inference acceleration)
3. **`verify_environment.py`** - Test script to verify all imports work
4. **`ENVIRONMENT_MIGRATION.md`** - Full documentation with troubleshooting
5. **`QUICK_START.md`** - This file

---

## Installation Commands

### Option A: CPU Instance (Most Common)

```bash
# Remove old environment (if exists)
conda deactivate
conda env remove -n acmg

# Create new environment
conda env create -f acmg_minimal.yml

# Activate
conda activate acmg

# Verify
python verify_environment.py

# If all ✓, run tests
pytest tests/ -v
```

### Option B: GPU Instance (For Accelerated Inference)

```bash
# Remove old environment (if exists)
conda deactivate
conda env remove -n acmg

# Create new environment
conda env create -f acmg_aws_gpu.yml

# Activate
conda activate acmg

# Verify GPU
python -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}')"

# Verify all imports
python verify_environment.py

# If all ✓, run tests
pytest tests/ -v
```

---

## Post-Installation

### 1. Install clinphen (if needed)
```bash
# clinphen is a local package, install manually
pip install /path/to/clinphen_pkg/clinphen-1.28
```

### 2. Set Environment Variables
Create/update your `.env` file:
```bash
# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/acmg
REDIS_URL=redis://localhost:6379/0

# Bioinformatics tools (adjust paths to your conda env)
BCFTOOLS_BINARY=/opt/conda/envs/acmg/bin/bcftools
SAMTOOLS_BINARY=/opt/conda/envs/acmg/bin/samtools
VEP_BINARY=/opt/conda/envs/vep/bin/vep
VEP_PERL=/opt/conda/envs/vep/bin/perl
VEP_DATA_DIR=/data/vep

# AWS (for Bedrock)
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your_key
AWS_SECRET_ACCESS_KEY=your_secret

# LLM provider
LLM_PROVIDER=bedrock  # or openai
BEDROCK_MODEL_ID=anthropic.claude-3-sonnet-20240229-v1:0
```

### 3. Set Up VEP (Separate Environment)
```bash
# VEP needs its own environment
conda create -n vep -c bioconda ensembl-vep=115.2 perl
conda activate vep
vep_install -a cf -s homo_sapiens -y GRCh38 --CACHEDIR /data/vep
```

---

## Common Issues

### ❌ "No module named 'clinphen'"
**Fix:**
```bash
pip install /path/to/clinphen_pkg/clinphen-1.28
```

### ❌ "bcftools: command not found" (during pipeline run)
**Fix:** Set environment variable in `.env`:
```bash
BCFTOOLS_BINARY=/opt/conda/envs/acmg/bin/bcftools
```

### ❌ PyTorch CUDA mismatch
**Fix:** Check your CUDA version and adjust:
```bash
nvidia-smi  # Check CUDA version
# If different from 12.1, edit acmg_aws_gpu.yml and change cu121 to your version
```

### ❌ Import errors after environment creation
**Fix:** Run verification script to identify missing packages:
```bash
python verify_environment.py
# Then install any missing packages: pip install <package>
```

---

## Verification Checklist

After environment creation, verify these work:

```bash
# ✅ Core imports
python -c "import fastapi, sqlalchemy, celery, redis; print('✓ Web stack')"
python -c "import langchain, langgraph, chromadb; print('✓ AI stack')"
python -c "import pysam, cyvcf2, Bio; print('✓ Bioinformatics')"

# ✅ CLI tools
which bcftools && which samtools && echo "✓ CLI tools"

# ✅ Full verification
python verify_environment.py

# ✅ Run tests
pytest tests/ -v
```

---

## Next Steps

1. ✅ **Environment created** → Run `python verify_environment.py`
2. ✅ **All imports pass** → Set up `.env` file with your credentials
3. ✅ **VEP installed** → Test with a small VCF file
4. ✅ **Database running** → Run migrations: `python migrate_database.py`
5. ✅ **Tests pass** → Start the API: `uvicorn src.api.main:app --reload`

---

## Getting Help

- **Full documentation:** See `ENVIRONMENT_MIGRATION.md`
- **Verification failed:** Run `python verify_environment.py` to identify issues
- **Pipeline errors:** Check logs in `logs/` directory
- **VEP issues:** Verify VEP environment separately: `conda activate vep && vep --help`

---

## Why This Works Better

| Aspect | Old `acmg.yml` | New `acmg_minimal.yml` |
|--------|----------------|-------------------------|
| Packages | 450+ pinned | ~60 direct |
| Conflicts | High | Low |
| Install time | 20-40 min | 5-10 min |
| Portability | DGX-specific | Cloud-agnostic |
| Maintainability | Hard to update | Easy to update |

**Key insight:** By specifying only direct dependencies, conda can resolve the dependency tree for your specific AWS environment, avoiding conflicts from pinned transitive dependencies.

---

Good luck! 🚀
