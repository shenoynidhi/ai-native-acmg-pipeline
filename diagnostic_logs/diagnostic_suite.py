#!/usr/bin/env python3
"""
ACMG Pipeline Comprehensive Diagnostic Suite

This script runs a complete health check of your AWS pipeline and generates
detailed logs to identify exactly what's working and what's broken.

Usage:
    python diagnostic_suite.py

Output:
    - diagnostic_logs/        # All test logs
    - diagnostic_report.html  # Visual report
    - diagnostic_summary.json # Machine-readable results
"""

import os
import sys
import json
import logging
import subprocess
import traceback
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import importlib.util

# Setup logging
LOG_DIR = Path("diagnostic_logs")
LOG_DIR.mkdir(exist_ok=True)

TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
MAIN_LOG = LOG_DIR / f"diagnostic_main_{TIMESTAMP}.log"

# Configure root logger
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler(MAIN_LOG),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

# Test results storage
test_results = {
    "timestamp": TIMESTAMP,
    "tests": {},
    "summary": {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "warnings": 0
    }
}


class TestResult:
    """Store result of a single test."""
    def __init__(self, name: str, passed: bool, message: str, details: Optional[Dict] = None, warning: bool = False):
        self.name = name
        self.passed = passed
        self.warning = warning
        self.message = message
        self.details = details or {}
        self.log_file = None


def log_test_result(category: str, test_name: str, passed: bool, message: str,
                    details: Optional[Dict] = None, warning: bool = False,
                    log_content: Optional[str] = None):
    """Log a test result and update global results."""
    status = "⚠️  WARN" if warning else ("✅ PASS" if passed else "❌ FAIL")
    logger.info(f"{status} | {category} | {test_name}: {message}")

    # Store result
    if category not in test_results["tests"]:
        test_results["tests"][category] = []

    result = {
        "name": test_name,
        "passed": passed,
        "warning": warning,
        "message": message,
        "details": details or {}
    }

    # Save detailed log if provided
    if log_content:
        log_file = LOG_DIR / f"{category.replace(' ', '_')}_{test_name.replace(' ', '_')}_{TIMESTAMP}.log"
        with open(log_file, 'w') as f:
            f.write(log_content)
        result["log_file"] = str(log_file)
        logger.info(f"   Detailed log: {log_file}")

    test_results["tests"][category].append(result)
    test_results["summary"]["total"] += 1
    if warning:
        test_results["summary"]["warnings"] += 1
    elif passed:
        test_results["summary"]["passed"] += 1
    else:
        test_results["summary"]["failed"] += 1


def run_command(cmd: List[str], timeout: int = 30) -> Tuple[bool, str, str]:
    """Run a shell command and return (success, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", f"Command timed out after {timeout}s"
    except Exception as e:
        return False, "", str(e)


# =============================================================================
# TEST SUITE 1: Environment & Dependencies
# =============================================================================

def test_python_version():
    """Check Python version."""
    version = sys.version_info
    version_str = f"{version.major}.{version.minor}.{version.micro}"

    if version.major == 3 and version.minor >= 10:
        log_test_result("Environment", "Python Version", True,
                       f"Python {version_str}", {"version": version_str})
    else:
        log_test_result("Environment", "Python Version", False,
                       f"Python {version_str} (requires 3.10+)", {"version": version_str})


def test_required_packages():
    """Check all required Python packages."""
    required = [
        "fastapi", "uvicorn", "celery", "redis", "sqlalchemy", "psycopg2",
        "pydantic", "python-dotenv", "bcrypt", "chromadb", "langchain",
        "langgraph", "sentence_transformers", "torch", "pandas", "numpy",
        "cyvcf2", "pysam", "biopython", "requests", "jinja2", "openpyxl"
    ]

    results = {}
    all_passed = True

    for package in required:
        try:
            spec = importlib.util.find_spec(package)
            if spec is not None:
                results[package] = "✅ Installed"
            else:
                results[package] = "❌ Missing"
                all_passed = False
        except (ImportError, ModuleNotFoundError):
            results[package] = "❌ Missing"
            all_passed = False

    missing = [k for k, v in results.items() if "Missing" in v]

    if all_passed:
        log_test_result("Environment", "Python Packages", True,
                       f"All {len(required)} packages installed", results)
    else:
        log_test_result("Environment", "Python Packages", False,
                       f"{len(missing)} packages missing: {', '.join(missing)}", results)


def test_system_binaries():
    """Check required system binaries."""
    binaries = {
        "bcftools": "bcftools --version",
        "samtools": "samtools --version",
        "tabix": "tabix --version",
        "bgzip": "bgzip --version",
        "perl": "perl --version",
        "psql": "psql --version",
        "redis-cli": "redis-cli --version"
    }

    results = {}
    all_passed = True

    for name, cmd in binaries.items():
        success, stdout, stderr = run_command(cmd.split())
        if success:
            version = stdout.split('\n')[0] if stdout else "installed"
            results[name] = f"✅ {version}"
        else:
            results[name] = "❌ Not found"
            all_passed = False

    missing = [k for k, v in results.items() if "Not found" in v]

    if all_passed:
        log_test_result("Environment", "System Binaries", True,
                       f"All {len(binaries)} binaries found", results)
    else:
        log_test_result("Environment", "System Binaries", False,
                       f"{len(missing)} binaries missing: {', '.join(missing)}", results)


def test_vep_installation():
    """Check VEP installation and data."""
    from dotenv import load_dotenv
    load_dotenv()

    vep_binary = os.getenv("VEP_BINARY", "/usr/local/bin/vep")
    vep_data_dir = os.getenv("VEP_DATA_DIR", "/mnt/ebs-databases/vep_databases")

    details = {
        "vep_binary": vep_binary,
        "vep_data_dir": vep_data_dir
    }

    # Check VEP binary
    if not Path(vep_binary).exists():
        log_test_result("Environment", "VEP Installation", False,
                       f"VEP binary not found at {vep_binary}", details)
        return

    # Check VEP data directory
    vep_data_path = Path(vep_data_dir)
    if not vep_data_path.exists():
        log_test_result("Environment", "VEP Installation", False,
                       f"VEP data directory not found at {vep_data_dir}", details)
        return

    # Check for cache
    cache_dir = vep_data_path / "homo_sapiens"
    if cache_dir.exists():
        cache_versions = list(cache_dir.glob("*_GRCh*"))
        details["cache_versions"] = [v.name for v in cache_versions]

        if cache_versions:
            log_test_result("Environment", "VEP Installation", True,
                           f"VEP installed with {len(cache_versions)} cache(s)", details)
        else:
            log_test_result("Environment", "VEP Installation", False,
                           "VEP data directory exists but no cache found", details,
                           warning=True)
    else:
        log_test_result("Environment", "VEP Installation", False,
                       "No homo_sapiens cache directory found", details)


# =============================================================================
# TEST SUITE 2: Database Connections
# =============================================================================

def test_postgresql_connection():
    """Test PostgreSQL connection."""
    from dotenv import load_dotenv
    load_dotenv()

    db_url = os.getenv("DATABASE_URL", "")

    if not db_url:
        log_test_result("Database", "PostgreSQL Config", False,
                       "DATABASE_URL not set in .env")
        return

    try:
        from sqlalchemy import create_engine, text
        engine = create_engine(db_url)

        with engine.connect() as conn:
            result = conn.execute(text("SELECT version()"))
            version = result.fetchone()[0]

            # Check for pgvector
            result = conn.execute(text(
                "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector')"
            ))
            has_pgvector = result.fetchone()[0]

            details = {
                "database_url": db_url.split('@')[1] if '@' in db_url else db_url,
                "version": version.split(',')[0],
                "pgvector": "✅ Installed" if has_pgvector else "❌ Not installed"
            }

            if has_pgvector:
                log_test_result("Database", "PostgreSQL Connection", True,
                               "Connected successfully with pgvector", details)
            else:
                log_test_result("Database", "PostgreSQL Connection", False,
                               "Connected but pgvector extension missing", details,
                               warning=True)

    except Exception as e:
        log_test_result("Database", "PostgreSQL Connection", False,
                       f"Connection failed: {str(e)}",
                       {"error": str(e), "traceback": traceback.format_exc()})


def test_redis_connection():
    """Test Redis connection."""
    from dotenv import load_dotenv
    load_dotenv()

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    try:
        import redis
        r = redis.from_url(redis_url)

        # Test ping
        r.ping()

        # Get info
        info = r.info()

        details = {
            "redis_url": redis_url,
            "version": info.get("redis_version", "unknown"),
            "uptime_days": info.get("uptime_in_days", 0),
            "connected_clients": info.get("connected_clients", 0),
            "used_memory_human": info.get("used_memory_human", "unknown")
        }

        log_test_result("Database", "Redis Connection", True,
                       "Connected successfully", details)

    except Exception as e:
        log_test_result("Database", "Redis Connection", False,
                       f"Connection failed: {str(e)}",
                       {"error": str(e), "redis_url": redis_url})


def test_database_tables():
    """Check if required database tables exist."""
    from dotenv import load_dotenv
    load_dotenv()

    db_url = os.getenv("DATABASE_URL", "")

    if not db_url:
        log_test_result("Database", "Database Tables", False,
                       "DATABASE_URL not set")
        return

    try:
        from sqlalchemy import create_engine, text, inspect
        engine = create_engine(db_url)
        inspector = inspect(engine)

        required_tables = ["users", "sessions", "palace_memories", "palace_knowledge"]
        existing_tables = inspector.get_table_names()

        results = {}
        for table in required_tables:
            if table in existing_tables:
                # Count rows
                with engine.connect() as conn:
                    result = conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
                    count = result.fetchone()[0]
                    results[table] = f"✅ {count} rows"
            else:
                results[table] = "❌ Missing"

        missing = [k for k, v in results.items() if "Missing" in v]

        if not missing:
            log_test_result("Database", "Database Tables", True,
                           "All required tables exist", results)
        else:
            log_test_result("Database", "Database Tables", False,
                           f"{len(missing)} tables missing: {', '.join(missing)}", results)

    except Exception as e:
        log_test_result("Database", "Database Tables", False,
                       f"Failed to check tables: {str(e)}",
                       {"error": str(e)})


# =============================================================================
# TEST SUITE 3: File System & Data
# =============================================================================

def test_data_directories():
    """Check all required data directories exist."""
    from dotenv import load_dotenv
    load_dotenv()

    dirs = {
        "DATABASE_DIR": os.getenv("DATABASE_DIR", "/mnt/ebs-databases/databases"),
        "CHROMADB_DIR": os.getenv("CHROMADB_DIR", "/mnt/ebs-databases/chromadb"),
        "OUTPUT_DIR": os.getenv("OUTPUT_DIR", "/mnt/ebs-databases/output"),
        "REFERENCE_DIR": os.getenv("REFERENCE_DIR", "/mnt/ebs-databases/reference"),
        "VEP_DATA_DIR": os.getenv("VEP_DATA_DIR", "/mnt/ebs-databases/vep_databases")
    }

    results = {}
    all_exist = True

    for name, path in dirs.items():
        path_obj = Path(path)
        if path_obj.exists():
            # Check if writable
            test_file = path_obj / ".write_test"
            try:
                test_file.touch()
                test_file.unlink()
                results[name] = f"✅ {path} (writable)"
            except:
                results[name] = f"⚠️  {path} (read-only)"
                all_exist = False
        else:
            results[name] = f"❌ {path} (not found)"
            all_exist = False

    missing = [k for k, v in results.items() if "not found" in v]

    if all_exist and not missing:
        log_test_result("File System", "Data Directories", True,
                       "All directories exist and writable", results)
    elif missing:
        log_test_result("File System", "Data Directories", False,
                       f"{len(missing)} directories missing: {', '.join(missing)}", results)
    else:
        log_test_result("File System", "Data Directories", False,
                       "Some directories not writable", results, warning=True)


def test_reference_databases():
    """Check for required reference database files."""
    from dotenv import load_dotenv
    load_dotenv()

    db_dir = Path(os.getenv("DATABASE_DIR", "/mnt/ebs-databases/databases"))

    required_files = {
        "HPO": "phenotype.hpoa",
        "gnomAD Constraint": "gnomad.v2.1.1.lof_metrics.by_gene.txt",
        "ClinGen": "ClinGen_gene_curation_list_GRCh38.tsv",
        "HGNC": "hgnc_complete_set.txt"
    }

    results = {}
    all_exist = True

    for name, filename in required_files.items():
        file_path = db_dir / filename
        if file_path.exists():
            size_mb = file_path.stat().st_size / (1024 * 1024)
            results[name] = f"✅ {filename} ({size_mb:.1f} MB)"
        else:
            results[name] = f"❌ {filename} (not found)"
            all_exist = False

    if all_exist:
        log_test_result("File System", "Reference Databases", True,
                       "All reference files found", results)
    else:
        missing = [k for k, v in results.items() if "not found" in v]
        log_test_result("File System", "Reference Databases", False,
                       f"{len(missing)} files missing: {', '.join(missing)}", results)


def test_chromadb_collections():
    """Check ChromaDB collections for RAG."""
    from dotenv import load_dotenv
    load_dotenv()

    chromadb_dir = Path(os.getenv("CHROMADB_DIR", "/mnt/ebs-databases/chromadb"))

    if not chromadb_dir.exists():
        log_test_result("File System", "ChromaDB Collections", False,
                       f"ChromaDB directory not found: {chromadb_dir}")
        return

    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(chromadb_dir))

        collections = client.list_collections()

        required_collections = [
            "acmg_guidelines",
            "clinvar_pathogenic",
            "clinvar_benign",
            "uniprot_domains"
        ]

        results = {}
        for coll_name in required_collections:
            try:
                coll = client.get_collection(coll_name)
                count = coll.count()
                results[coll_name] = f"✅ {count} documents"
            except:
                results[coll_name] = "❌ Not found"

        missing = [k for k, v in results.items() if "Not found" in v]

        if not missing:
            log_test_result("File System", "ChromaDB Collections", True,
                           f"All {len(required_collections)} RAG collections found", results)
        else:
            log_test_result("File System", "ChromaDB Collections", False,
                           f"{len(missing)} collections missing: {', '.join(missing)}", results)

    except Exception as e:
        log_test_result("File System", "ChromaDB Collections", False,
                       f"Failed to check ChromaDB: {str(e)}",
                       {"error": str(e), "chromadb_dir": str(chromadb_dir)})


# =============================================================================
# TEST SUITE 4: AWS Bedrock
# =============================================================================

def test_bedrock_configuration():
    """Check AWS Bedrock configuration."""
    from dotenv import load_dotenv
    load_dotenv()

    provider = os.getenv("LLM_PROVIDER", "")
    token = os.getenv("AWS_BEARER_TOKEN_BEDROCK", "")
    region = os.getenv("BEDROCK_REGION", "")
    model = os.getenv("LLM_MODEL", "")

    details = {
        "provider": provider,
        "region": region,
        "model": model,
        "token_present": "✅ Set" if token else "❌ Missing"
    }

    if provider == "bedrock" and token and region and model:
        log_test_result("AWS Bedrock", "Configuration", True,
                       f"Configured for {model} in {region}", details)
    else:
        missing = []
        if not provider: missing.append("LLM_PROVIDER")
        if not token: missing.append("AWS_BEARER_TOKEN_BEDROCK")
        if not region: missing.append("BEDROCK_REGION")
        if not model: missing.append("LLM_MODEL")

        log_test_result("AWS Bedrock", "Configuration", False,
                       f"Missing: {', '.join(missing)}", details)


def test_bedrock_connectivity():
    """Test actual Bedrock API call."""
    from dotenv import load_dotenv
    load_dotenv()

    provider = os.getenv("LLM_PROVIDER", "")

    if provider != "bedrock":
        log_test_result("AWS Bedrock", "API Connectivity", False,
                       "LLM_PROVIDER not set to 'bedrock'", warning=True)
        return

    try:
        # Try to import and use the LLM client
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        from utils.llm import call_llm

        response = call_llm(
            system_prompt="You are a test assistant.",
            user_prompt="Reply with exactly: TEST_PASSED",
            temperature=0.0,
            max_tokens=20
        )

        if response and "TEST_PASSED" in response:
            log_test_result("AWS Bedrock", "API Connectivity", True,
                           "Successfully called Bedrock API",
                           {"response": response[:100]})
        else:
            log_test_result("AWS Bedrock", "API Connectivity", False,
                           f"Unexpected response: {response[:200]}",
                           {"response": response})

    except Exception as e:
        log_test_result("AWS Bedrock", "API Connectivity", False,
                       f"API call failed: {str(e)}",
                       {"error": str(e), "traceback": traceback.format_exc()})


# =============================================================================
# TEST SUITE 5: Pipeline Components
# =============================================================================

def test_pipeline_imports():
    """Test that all pipeline modules can be imported."""
    sys.path.insert(0, str(Path(__file__).parent / "src"))

    modules = [
        "pipeline.graph",
        "pipeline.runner",
        "pipeline.state",
        "agents.agent1_population",
        "agents.agent2_consequence",
        "agents.agent3_insilico",
        "agents.agent4_database",
        "agents.agent5_functional",
        "agents.agent6_segregation",
        "agents.agent7_denovo",
        "agents.agent8_gene_context",
        "agents.agent9_phenotype",
        "pipeline.nodes.vep_runner",
        "pipeline.nodes.post_process",
        "pipeline.nodes.evidence_aggregator",
        "rag.retriever",
        "config"
    ]

    results = {}
    all_passed = True

    for module_name in modules:
        try:
            __import__(module_name)
            results[module_name] = "✅ OK"
        except Exception as e:
            results[module_name] = f"❌ {str(e)[:50]}"
            all_passed = False

    failed = [k for k, v in results.items() if "❌" in v]

    if all_passed:
        log_test_result("Pipeline", "Module Imports", True,
                       f"All {len(modules)} modules imported successfully", results)
    else:
        log_test_result("Pipeline", "Module Imports", False,
                       f"{len(failed)} modules failed to import", results)


def test_rag_retriever():
    """Test RAG retriever functionality."""
    sys.path.insert(0, str(Path(__file__).parent / "src"))

    try:
        from rag.retriever import RAGRetriever

        retriever = RAGRetriever()

        # Test ACMG guidelines query
        results = retriever.query_acmg_guidelines("What is PM2 criteria?", top_k=3)

        if results and len(results) > 0:
            details = {
                "num_results": len(results),
                "first_result_preview": results[0][:200] if results[0] else "empty"
            }
            log_test_result("Pipeline", "RAG Retriever", True,
                           f"Retrieved {len(results)} ACMG guideline chunks", details)
        else:
            log_test_result("Pipeline", "RAG Retriever", False,
                           "No results returned from ACMG guidelines query",
                           {"results": results})

    except Exception as e:
        log_test_result("Pipeline", "RAG Retriever", False,
                       f"RAG retriever failed: {str(e)}",
                       {"error": str(e), "traceback": traceback.format_exc()})


def test_agent_execution():
    """Test a simple agent execution."""
    sys.path.insert(0, str(Path(__file__).parent / "src"))

    try:
        from agents.agent1_population import agent1_population
        from pipeline.state import VariantState

        # Create a test variant state
        test_state: VariantState = {
            "session_id": "test_session",
            "variant_id": "test_variant",
            "max_gnomad_af": 0.001,
            "gnomad_afr_af": 0.001,
            "gnomad_nhomalt": 0,
            "consequence": "missense_variant"
        }

        # Run agent
        result = agent1_population(test_state)

        if result and "agent_evidence" in result:
            agent_evidence = result["agent_evidence"].get("agent1", {})
            details = {
                "criteria_pathogenic": agent_evidence.get("criteria_pathogenic", {}),
                "criteria_benign": agent_evidence.get("criteria_benign", {}),
                "evidence_notes": agent_evidence.get("evidence_notes", "")[:200]
            }
            log_test_result("Pipeline", "Agent Execution", True,
                           "Agent1 (Population) executed successfully", details)
        else:
            log_test_result("Pipeline", "Agent Execution", False,
                           "Agent1 returned unexpected result",
                           {"result": str(result)[:500]})

    except Exception as e:
        log_test_result("Pipeline", "Agent Execution", False,
                       f"Agent execution failed: {str(e)}",
                       {"error": str(e), "traceback": traceback.format_exc()})


# =============================================================================
# TEST SUITE 6: API Endpoints
# =============================================================================

def test_api_server_running():
    """Check if FastAPI server is running."""
    import requests

    try:
        response = requests.get("http://localhost:8000/health", timeout=5)

        if response.status_code == 200:
            log_test_result("API", "Server Running", True,
                           "API server is healthy",
                           {"status_code": 200, "response": response.json()})
        else:
            log_test_result("API", "Server Running", False,
                           f"Server returned status {response.status_code}",
                           {"status_code": response.status_code})

    except requests.exceptions.ConnectionError:
        log_test_result("API", "Server Running", False,
                       "Cannot connect to API server at http://localhost:8000",
                       warning=True)
    except Exception as e:
        log_test_result("API", "Server Running", False,
                       f"Health check failed: {str(e)}",
                       {"error": str(e)})


def test_celery_worker_running():
    """Check if Celery worker is running."""
    from dotenv import load_dotenv
    load_dotenv()

    try:
        from celery import Celery
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

        app = Celery('test', broker=redis_url, backend=redis_url)

        # Check active workers
        inspect = app.control.inspect()
        stats = inspect.stats()

        if stats:
            worker_count = len(stats)
            log_test_result("API", "Celery Worker", True,
                           f"{worker_count} worker(s) running",
                           {"workers": list(stats.keys())})
        else:
            log_test_result("API", "Celery Worker", False,
                           "No Celery workers detected",
                           warning=True)

    except Exception as e:
        log_test_result("API", "Celery Worker", False,
                       f"Failed to check Celery: {str(e)}",
                       {"error": str(e)})


# =============================================================================
# TEST SUITE 7: End-to-End Test
# =============================================================================

def test_end_to_end_vcf_analysis():
    """Run a minimal end-to-end VCF analysis test."""
    sys.path.insert(0, str(Path(__file__).parent / "src"))

    logger.info("=" * 80)
    logger.info("STARTING END-TO-END VCF ANALYSIS TEST")
    logger.info("=" * 80)

    # Check for custom VCF path in environment variable
    custom_vcf = os.getenv("TEST_VCF_PATH")
    if custom_vcf:
        test_vcf = Path(custom_vcf)
        logger.info(f"Using custom VCF from TEST_VCF_PATH: {test_vcf}")
    else:
        test_vcf = Path("tests/test_data/test_minimal.vcf.gz")
        logger.info(f"Using default test VCF: {test_vcf}")

    if not test_vcf.exists():
        log_test_result("End-to-End", "VCF Analysis", False,
                       f"Test VCF not found at {test_vcf}",
                       warning=True)
        return

    try:
        from pipeline.runner import run_session
        from dotenv import load_dotenv
        load_dotenv()

        session_id = f"e2e_test_{TIMESTAMP}"

        logger.info(f"Running analysis for session: {session_id}")

        # Run pipeline
        result = run_session(
            session_id=session_id,
            proband_vcf_path=str(test_vcf),
            genome_build="GRCh38",
            clinical_notes="Test patient with epilepsy",
            patient_hpo_terms=[],
            proband_sex="Unknown"
        )

        # Check result
        if result and "report_paths" in result:
            details = {
                "session_id": session_id,
                "num_variants": result.get("num_variants", 0),
                "report_paths": result.get("report_paths", {})
            }
            log_test_result("End-to-End", "VCF Analysis", True,
                           f"Analysis completed: {result.get('num_variants', 0)} variants",
                           details)
        else:
            log_test_result("End-to-End", "VCF Analysis", False,
                           "Analysis returned unexpected result",
                           {"result": str(result)[:500]})

    except Exception as e:
        log_content = traceback.format_exc()
        log_test_result("End-to-End", "VCF Analysis", False,
                       f"Analysis failed: {str(e)}",
                       {"error": str(e)},
                       log_content=log_content)


# =============================================================================
# MAIN EXECUTION
# =============================================================================

def generate_html_report():
    """Generate HTML report from test results."""
    html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>ACMG Pipeline Diagnostic Report</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
        }}
        h1 {{
            color: #2c3e50;
            border-bottom: 3px solid #3498db;
            padding-bottom: 10px;
        }}
        .summary {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            margin: 20px 0;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .summary-grid {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 20px;
            margin-top: 20px;
        }}
        .summary-box {{
            text-align: center;
            padding: 20px;
            border-radius: 8px;
        }}
        .summary-box.total {{ background: #3498db; color: white; }}
        .summary-box.passed {{ background: #2ecc71; color: white; }}
        .summary-box.failed {{ background: #e74c3c; color: white; }}
        .summary-box.warnings {{ background: #f39c12; color: white; }}
        .summary-number {{ font-size: 48px; font-weight: bold; }}
        .summary-label {{ font-size: 14px; opacity: 0.9; margin-top: 5px; }}
        .category {{
            background: white;
            margin: 20px 0;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .category-header {{
            background: #34495e;
            color: white;
            padding: 15px 20px;
            border-radius: 8px 8px 0 0;
            font-size: 18px;
            font-weight: bold;
        }}
        .test {{
            padding: 15px 20px;
            border-bottom: 1px solid #ecf0f1;
        }}
        .test:last-child {{ border-bottom: none; }}
        .test-name {{
            font-weight: 600;
            margin-bottom: 5px;
        }}
        .test-message {{
            color: #7f8c8d;
            font-size: 14px;
        }}
        .status {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: bold;
            margin-right: 10px;
        }}
        .status.pass {{ background: #2ecc71; color: white; }}
        .status.fail {{ background: #e74c3c; color: white; }}
        .status.warn {{ background: #f39c12; color: white; }}
        .details {{
            background: #ecf0f1;
            padding: 10px;
            border-radius: 4px;
            margin-top: 10px;
            font-family: monospace;
            font-size: 12px;
            white-space: pre-wrap;
        }}
        .timestamp {{
            color: #95a5a6;
            font-size: 14px;
        }}
    </style>
</head>
<body>
    <h1>🧬 ACMG Pipeline Diagnostic Report</h1>
    <div class="timestamp">Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>

    <div class="summary">
        <h2>Summary</h2>
        <div class="summary-grid">
            <div class="summary-box total">
                <div class="summary-number">{test_results["summary"]["total"]}</div>
                <div class="summary-label">Total Tests</div>
            </div>
            <div class="summary-box passed">
                <div class="summary-number">{test_results["summary"]["passed"]}</div>
                <div class="summary-label">Passed</div>
            </div>
            <div class="summary-box failed">
                <div class="summary-number">{test_results["summary"]["failed"]}</div>
                <div class="summary-label">Failed</div>
            </div>
            <div class="summary-box warnings">
                <div class="summary-number">{test_results["summary"]["warnings"]}</div>
                <div class="summary-label">Warnings</div>
            </div>
        </div>
    </div>
"""

    # Add test results by category
    for category, tests in test_results["tests"].items():
        html += f"""
    <div class="category">
        <div class="category-header">{category}</div>
"""
        for test in tests:
            status_class = "warn" if test["warning"] else ("pass" if test["passed"] else "fail")
            status_text = "⚠️ WARN" if test["warning"] else ("✅ PASS" if test["passed"] else "❌ FAIL")

            html += f"""
        <div class="test">
            <div class="test-name">
                <span class="status {status_class}">{status_text}</span>
                {test["name"]}
            </div>
            <div class="test-message">{test["message"]}</div>
"""
            if test["details"]:
                details_json = json.dumps(test["details"], indent=2)
                html += f"""
            <div class="details">{details_json}</div>
"""

            if test.get("log_file"):
                html += f"""
            <div style="margin-top: 10px;">
                <a href="{test['log_file']}" style="color: #3498db; text-decoration: none;">
                    📄 View detailed log
                </a>
            </div>
"""

            html += """
        </div>
"""

        html += """
    </div>
"""

    html += """
</body>
</html>
"""

    # Save HTML report
    report_path = Path(f"diagnostic_report_{TIMESTAMP}.html")
    with open(report_path, 'w') as f:
        f.write(html)

    logger.info(f"\n📊 HTML Report: {report_path}")
    return report_path


def main():
    """Run all diagnostic tests."""
    logger.info("=" * 80)
    logger.info("ACMG PIPELINE DIAGNOSTIC SUITE")
    logger.info("=" * 80)
    logger.info(f"Timestamp: {TIMESTAMP}")
    logger.info(f"Main log: {MAIN_LOG}")
    logger.info("=" * 80)

    # Run all test suites
    logger.info("\n🔍 TEST SUITE 1: Environment & Dependencies")
    test_python_version()
    test_required_packages()
    test_system_binaries()
    test_vep_installation()

    logger.info("\n🔍 TEST SUITE 2: Database Connections")
    test_postgresql_connection()
    test_redis_connection()
    test_database_tables()

    logger.info("\n🔍 TEST SUITE 3: File System & Data")
    test_data_directories()
    test_reference_databases()
    test_chromadb_collections()

    logger.info("\n🔍 TEST SUITE 4: AWS Bedrock")
    test_bedrock_configuration()
    test_bedrock_connectivity()

    logger.info("\n🔍 TEST SUITE 5: Pipeline Components")
    test_pipeline_imports()
    test_rag_retriever()
    test_agent_execution()

    logger.info("\n🔍 TEST SUITE 6: API Endpoints")
    test_api_server_running()
    test_celery_worker_running()

    logger.info("\n🔍 TEST SUITE 7: End-to-End Test")
    test_end_to_end_vcf_analysis()

    # Generate reports
    logger.info("\n" + "=" * 80)
    logger.info("GENERATING REPORTS")
    logger.info("=" * 80)

    # Save JSON summary
    json_path = Path(f"diagnostic_summary_{TIMESTAMP}.json")
    with open(json_path, 'w') as f:
        json.dump(test_results, f, indent=2)
    logger.info(f"📄 JSON Summary: {json_path}")

    # Generate HTML report
    html_path = generate_html_report()

    # Print final summary
    summary = test_results["summary"]
    logger.info("\n" + "=" * 80)
    logger.info("DIAGNOSTIC SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Total Tests:  {summary['total']}")
    logger.info(f"✅ Passed:    {summary['passed']}")
    logger.info(f"❌ Failed:    {summary['failed']}")
    logger.info(f"⚠️  Warnings:  {summary['warnings']}")
    logger.info("=" * 80)

    if summary["failed"] == 0:
        logger.info("🎉 ALL TESTS PASSED!")
    else:
        logger.info(f"⚠️  {summary['failed']} TESTS FAILED - Check report for details")

    logger.info(f"\n📊 View full report: {html_path}")
    logger.info(f"📄 View JSON summary: {json_path}")
    logger.info(f"📁 All logs in: {LOG_DIR}/")

    return summary["failed"] == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)

