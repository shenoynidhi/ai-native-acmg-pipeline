#!/usr/bin/env python3
"""
Environment Verification Script
Run this after creating your new conda environment to verify all dependencies are working.

Usage:
    conda activate acmg
    python verify_environment.py
"""

import sys
from pathlib import Path


def test_imports():
    """Test all critical imports from the codebase."""

    tests = {
        "Bioinformatics Tools": [
            ("pysam", "BAM/SAM file manipulation"),
            ("cyvcf2", "Fast VCF parsing"),
            ("Bio", "Biopython utilities"),
            ("numpy", "Numerical computing"),
            ("scipy.stats", "Statistical functions"),
            ("pandas", "Data manipulation"),
        ],
        "AI/ML Stack": [
            ("langchain", "LangChain core"),
            ("langgraph.graph", "LangGraph orchestration"),
            ("chromadb", "Vector database"),
            ("sentence_transformers", "Embeddings"),
            ("torch", "PyTorch"),
            ("transformers", "Hugging Face models"),
        ],
        "Web Framework": [
            ("fastapi", "REST API framework"),
            ("uvicorn", "ASGI server"),
            ("pydantic", "Data validation"),
            ("starlette", "ASGI components"),
        ],
        "Database & Caching": [
            ("sqlalchemy", "ORM"),
            ("redis", "Redis client"),
            ("pgvector.sqlalchemy", "Vector similarity"),
        ],
        "Task Queue": [
            ("celery", "Distributed tasks"),
        ],
        "Document Processing": [
            ("fitz", "PyMuPDF for PDF parsing"),
            ("PyPDF2", "Alternative PDF reader"),
            ("openpyxl", "Excel file manipulation"),
            ("jinja2", "Templating"),
        ],
        "Clinical/Medical": [
            ("hpo3", "HPO library"),
        ],
        "Utilities": [
            ("requests", "HTTP client"),
            ("httpx", "Async HTTP client"),
            ("bcrypt", "Password hashing"),
            ("dotenv", "Environment variables"),
        ],
    }

    results = {"passed": [], "failed": [], "warnings": []}

    print("=" * 70)
    print("ENVIRONMENT VERIFICATION")
    print("=" * 70)
    print()

    for category, imports in tests.items():
        print(f"\n📦 {category}")
        print("-" * 70)

        for module, description in imports:
            try:
                __import__(module)
                print(f"  ✓ {module:30s} - {description}")
                results["passed"].append(module)
            except ImportError as e:
                print(f"  ✗ {module:30s} - FAILED: {str(e)[:40]}")
                results["failed"].append((module, description, str(e)))
            except Exception as e:
                print(f"  ⚠ {module:30s} - WARNING: {str(e)[:40]}")
                results["warnings"].append((module, description, str(e)))

    return results


def test_cli_tools():
    """Test command-line bioinformatics tools."""
    import shutil

    print("\n\n🔧 Command-Line Tools")
    print("-" * 70)

    tools = [
        ("bcftools", "BCF/VCF file manipulation"),
        ("samtools", "SAM/BAM file manipulation"),
    ]

    results = []
    for tool, description in tools:
        path = shutil.which(tool)
        if path:
            print(f"  ✓ {tool:20s} - {path}")
            results.append((tool, True, path))
        else:
            print(f"  ✗ {tool:20s} - NOT FOUND in PATH")
            results.append((tool, False, None))

    return results


def test_pytorch_cuda():
    """Test PyTorch CUDA availability."""
    print("\n\n🚀 PyTorch & CUDA")
    print("-" * 70)

    try:
        import torch
        print(f"  PyTorch version:     {torch.__version__}")
        print(f"  CUDA available:      {torch.cuda.is_available()}")

        if torch.cuda.is_available():
            print(f"  CUDA version:        {torch.version.cuda}")
            print(f"  GPU count:           {torch.cuda.device_count()}")
            if torch.cuda.device_count() > 0:
                print(f"  GPU name:            {torch.cuda.get_device_name(0)}")
        else:
            print("  ℹ️  Running in CPU-only mode (expected for acmg_minimal.yml)")

        return True
    except Exception as e:
        print(f"  ✗ PyTorch test failed: {e}")
        return False


def test_database_drivers():
    """Test database connectivity libraries."""
    print("\n\n💾 Database Drivers")
    print("-" * 70)

    # PostgreSQL
    try:
        import psycopg2
        print(f"  ✓ psycopg2:          {psycopg2.__version__} (PostgreSQL driver)")
    except ImportError as e:
        print(f"  ✗ psycopg2:          FAILED - {e}")

    # pgvector
    try:
        from pgvector.sqlalchemy import Vector
        print(f"  ✓ pgvector:          Vector extension loaded")
    except ImportError as e:
        print(f"  ✗ pgvector:          FAILED - {e}")

    # Redis
    try:
        import redis
        print(f"  ✓ redis:             {redis.__version__}")
    except ImportError as e:
        print(f"  ✗ redis:             FAILED - {e}")


def print_summary(import_results):
    """Print final summary."""
    print("\n\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    total = len(import_results["passed"]) + len(import_results["failed"]) + len(import_results["warnings"])
    passed = len(import_results["passed"])
    failed = len(import_results["failed"])
    warnings = len(import_results["warnings"])

    print(f"\nTotal tests:    {total}")
    print(f"✓ Passed:       {passed}")
    print(f"✗ Failed:       {failed}")
    print(f"⚠ Warnings:     {warnings}")

    if failed > 0:
        print("\n⚠️  FAILED IMPORTS:")
        for module, description, error in import_results["failed"]:
            print(f"   - {module}: {error}")
        print("\n   To fix, run: pip install <missing-package>")

    if warnings > 0:
        print("\n⚠️  WARNINGS:")
        for module, description, error in import_results["warnings"]:
            print(f"   - {module}: {error}")

    if failed == 0:
        print("\n✅ Environment is ready!")
        print("\nNext steps:")
        print("  1. Set environment variables in .env file")
        print("  2. Run: pytest tests/ -v")
        print("  3. Start your pipeline: python -m src.pipeline.runner")
    else:
        print("\n❌ Please fix the failed imports before proceeding.")
        return 1

    return 0


def main():
    """Run all verification tests."""
    print(f"Python version: {sys.version}")
    print(f"Python executable: {sys.executable}")
    print()

    # Test imports
    import_results = test_imports()

    # Test CLI tools
    test_cli_tools()

    # Test PyTorch/CUDA
    test_pytorch_cuda()

    # Test database drivers
    test_database_drivers()

    # Print summary
    exit_code = print_summary(import_results)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
