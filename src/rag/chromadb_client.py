"""
src/rag/chromadb_client.py

Shared ChromaDB client helper with version compatibility handling.
Addresses ChromaDB 1.5.x bugs with RustBindingsAPI and multi-tenancy.
"""

import logging

logger = logging.getLogger(__name__)


def get_chromadb_client(persist_dir):
    """
    Get ChromaDB client with automatic version compatibility handling.

    Args:
        persist_dir: Path or str path to ChromaDB storage directory

    Returns:
        ChromaDB client instance compatible with installed version

    Note:
        ChromaDB 1.5.x has known bugs:
        - "Could not connect to tenant default_tenant" (multi-tenancy bug)
        - "'RustBindingsAPI' object has no attribute 'bindings'" (Rust backend regression)

        This function works around these by forcing DuckDB backend for 1.5.x.
        For production use, recommend downgrading to chromadb==0.4.24 or 0.5.23.
    """
    import chromadb
    from pathlib import Path

    persist_dir = Path(persist_dir)
    persist_dir.mkdir(parents=True, exist_ok=True)

    try:
        version = chromadb.__version__
        major, minor = map(int, version.split('.')[:2])
        logger.info(f"[chromadb_client] Version: {version}, major={major}, minor={minor}, check={major >= 1 and minor >= 5}")

        if major >= 1 and minor >= 5:
            # ChromaDB 1.5.x workaround: force DuckDB backend
            logger.info(f"[chromadb_client] ENTERING 1.5.x workaround for {version}")
            try:
                from chromadb.config import Settings
                settings = Settings(
                    chroma_db_impl="duckdb+parquet",
                    persist_directory=str(persist_dir),
                    anonymized_telemetry=False
                )
                return chromadb.Client(settings)
            except Exception as e:
                logger.warning(
                    f"ChromaDB 1.5.x workaround failed: {e}. "
                    "Consider downgrading: pip install chromadb==0.4.24"
                )
                # Fallback attempt
                return chromadb.PersistentClient(path=str(persist_dir))
    except Exception as e:
        logger.debug(f"Version detection failed: {e}")

    # ChromaDB < 1.5 (stable versions)
    return chromadb.PersistentClient(path=str(persist_dir))

