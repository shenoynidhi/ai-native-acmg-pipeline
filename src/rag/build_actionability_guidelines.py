"""
src/rag/build_actionability_guidelines.py

Build clinical actionability RAG database from:
1. OncoKB downloadable data (primary source for gene-drug-disease associations)
2. CIViC API (supplementary clinical evidence)
3. Manual curated NCCN/ASCO/ESMO extracts (optional - can be added later)

This creates a ChromaDB collection 'clinical_actionability' with therapeutic
recommendations, genetic counseling guidelines, and clinical trial information.

Usage:
    python src/rag/build_actionability_guidelines.py

    OR from project root:

    python -m src.rag.build_actionability_guidelines

Data sources:
- OncoKB: https://www.oncokb.org/api/v1/utils/allActionableVariants.txt
- CIViC: https://civicdb.org/downloads/nightly/nightly-ClinicalEvidenceSummaries.tsv
- Manual: data/guidelines/nccn/, data/guidelines/asco/, data/guidelines/esmo/
"""

import os
import sys
import json
import logging
import pandas as pd
from pathlib import Path
from typing import List, Dict, Optional

# Add project root to Python path if running as script
if __name__ == "__main__":
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

import chromadb
from chromadb.utils import embedding_functions

from src.config import CHROMADB_DIR

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Output collection name
COLLECTION_NAME = "clinical_actionability"

# Data directories - use absolute paths relative to project root
if __name__ == "__main__":
    # Running as script - find project root
    PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
else:
    # Imported as module - use current working directory as project root
    PROJECT_ROOT = Path.cwd()

DATA_DIR = PROJECT_ROOT / "data" / "guidelines"
ONCOKB_FILE = DATA_DIR / "oncokb_actionable_variants.txt"
CIVIC_FILE = DATA_DIR / "civic_clinical_evidence.tsv"
MANUAL_DIR = DATA_DIR / "manual_extracts"


# ---------------------------------------------------------------------------
# Cancer gene lists
# ---------------------------------------------------------------------------

# Curated list of cancer-related genes
# Source: COSMIC Cancer Gene Census + OncoKB + common hereditary cancer genes
CANCER_GENES = {
    # Breast/Ovarian/Pancreatic
    "BRCA1", "BRCA2", "PALB2", "CHEK2", "ATM", "BARD1", "BRIP1", "RAD51C", "RAD51D",
    "TP53", "PTEN", "STK11", "CDH1", "NF1",

    # Lynch syndrome / Colorectal
    "MLH1", "MSH2", "MSH6", "PMS2", "EPCAM", "APC", "MUTYH", "SMAD4", "BMPR1A",

    # Lung cancer
    "EGFR", "ALK", "ROS1", "BRAF", "KRAS", "NRAS", "MET", "RET", "ERBB2", "HER2",
    "NTRK1", "NTRK2", "NTRK3",

    # Melanoma
    "BRAF", "NRAS", "KIT", "CDKN2A", "BAP1",

    # Prostate
    "BRCA1", "BRCA2", "ATM", "CHEK2", "PALB2", "HOXB13",

    # Gastric/GI
    "CDH1", "CTNNA1", "TP53", "KRAS", "PIK3CA", "ERBB2",

    # Glioma
    "IDH1", "IDH2", "TP53", "ATRX", "EGFR", "PTEN", "NF1", "BRAF",

    # Targeted therapy genes
    "PIK3CA", "AKT1", "MTOR", "TSC1", "TSC2", "FGFR1", "FGFR2", "FGFR3",
    "PDGFRA", "KIT", "FLT3", "JAK2", "ABL1", "SRC",

    # Immunotherapy biomarkers (genes, not single variants)
    "MSH2", "MLH1", "MSH6", "PMS2",  # MSI-H markers

    # Other common cancer genes
    "VHL", "RB1", "CDKN2A", "SMARCB1", "TSC1", "TSC2", "MAX", "SDHB", "SDHC", "SDHD",
}

# HPO term to cancer type mapping (for phenotype-driven matching)
HPO_TO_CANCER_TYPE = {
    "HP:0100013": "breast",
    "HP:0002664": "multiple",
    "HP:0002858": "melanoma",
    "HP:0100526": "lung",
    "HP:0002894": "pancreatic",
    "HP:0002895": "hepatocellular",
    "HP:0002896": "colorectal",
    "HP:0100615": "ovarian",
    "HP:0002885": "glioma",
    "HP:0100273": "renal",
    "HP:0002893": "gastric",
    "HP:0030448": "prostate",
}


# ---------------------------------------------------------------------------
# OncoKB data parser
# ---------------------------------------------------------------------------

def load_oncokb_data() -> List[Dict]:
    """
    Load and parse OncoKB actionable variants data.

    OncoKB file format (TSV):
    Gene | Alteration | Cancer Type | Level | Drugs | PMIDs

    Returns list of chunks for RAG database.
    """
    if not ONCOKB_FILE.exists():
        logger.warning(
            f"OncoKB data file not found: {ONCOKB_FILE}\n"
            f"Please download from: https://www.oncokb.org/api/v1/utils/allActionableVariants.txt\n"
            f"Save to: {ONCOKB_FILE}"
        )
        return []

    logger.info(f"Loading OncoKB data from {ONCOKB_FILE}...")

    try:
        # OncoKB format may vary - try common delimiters
        df = pd.read_csv(ONCOKB_FILE, sep="\t", comment="#")

        # Normalize column names
        df.columns = df.columns.str.strip().str.lower().str.replace(" ", "_")

        chunks = []
        for idx, row in df.iterrows():
            gene = row.get("gene", "").strip()
            alteration = row.get("alteration", row.get("variant", "")).strip()
            cancer_type = row.get("cancer_type", row.get("tumor_type", "")).strip()
            level = row.get("level", "").strip()
            drugs = row.get("drugs", row.get("drug", "")).strip()
            pmids = row.get("pmids", "")

            if not gene or not alteration:
                continue

            # Map OncoKB levels to our evidence levels
            evidence_level = _map_oncokb_level(level)

            # Build chunk text
            chunk_text = (
                f"Gene: {gene}\n"
                f"Variant: {alteration}\n"
                f"Cancer Type: {cancer_type}\n"
                f"Evidence Level: {evidence_level}\n"
                f"Therapeutic Recommendation: {drugs}\n"
                f"Source: OncoKB {level}\n"
            )

            if pmids:
                chunk_text += f"References: {pmids}\n"

            # Create chunk metadata
            chunk_id = f"oncokb_{gene}_{alteration}_{cancer_type}".replace(" ", "_").replace("/", "_")

            chunks.append({
                "id": chunk_id[:250],  # ChromaDB ID length limit
                "text": chunk_text,
                "metadata": {
                    "source": "OncoKB",
                    "gene": gene,
                    "variant": alteration,
                    "cancer_type": cancer_type,
                    "evidence_level": evidence_level,
                    "drugs": drugs,
                    "chunk_type": "therapeutic_recommendation",
                }
            })

        logger.info(f"Loaded {len(chunks)} OncoKB actionable variants")
        return chunks

    except Exception as e:
        logger.error(f"Error loading OncoKB data: {e}")
        return []


def _map_oncokb_level(level: str) -> str:
    """Map OncoKB evidence levels to our standardized levels."""
    level_upper = level.upper()

    if "1" in level_upper or "FDA" in level_upper:
        return "FDA_Level1"
    elif "2" in level_upper or "STANDARD" in level_upper:
        return "NCCN_Category1"
    elif "3" in level_upper:
        return "Clinical_Trial"
    elif "4" in level_upper:
        return "Preclinical"
    elif "R1" in level_upper:
        return "Resistance_Standard"
    elif "R2" in level_upper:
        return "Resistance_Clinical"
    else:
        return "Clinical_Evidence"


# ---------------------------------------------------------------------------
# CIViC data parser
# ---------------------------------------------------------------------------

def load_civic_data() -> List[Dict]:
    """
    Load and parse CIViC clinical evidence summaries.

    CIViC file format (TSV):
    molecular_profile | disease | therapies | evidence_type | evidence_level |
    evidence_direction | significance | evidence_statement |
    citation_id | source_type

    Note: CIViC uses 'molecular_profile' (e.g., "BRAF V600E") instead of
    separate gene/variant columns. We parse it to extract both.

    Returns list of chunks for RAG database.
    """
    if not CIVIC_FILE.exists():
        logger.warning(
            f"CIViC data file not found: {CIVIC_FILE}\n"
            f"Please download from: https://civicdb.org/downloads/nightly/nightly-ClinicalEvidenceSummaries.tsv\n"
            f"Save to: {CIVIC_FILE}"
        )
        return []

    logger.info(f"Loading CIViC data from {CIVIC_FILE}...")

    try:
        df = pd.read_csv(CIVIC_FILE, sep="\t")

        # Normalize column names
        df.columns = df.columns.str.strip().str.lower().str.replace(" ", "_")

        chunks = []
        for idx, row in df.iterrows():
            # CIViC has 'molecular_profile' instead of separate gene/variant
            molecular_profile = str(row.get("molecular_profile", "")).strip()
            if not molecular_profile or molecular_profile == "nan":
                continue

            # Parse molecular_profile to extract gene and variant
            # Format is usually "GENE VARIANT" (e.g., "BRAF V600E", "EGFR L858R")
            parts = molecular_profile.split(None, 1)  # Split on first whitespace
            if len(parts) >= 2:
                gene = parts[0].strip()
                variant = parts[1].strip()
            elif len(parts) == 1:
                gene = parts[0].strip()
                variant = ""
            else:
                continue

            # Get other fields, safely handling NaN
            disease = str(row.get("disease", "")).strip()
            if disease == "nan":
                disease = ""

            therapies = str(row.get("therapies", ""))
            if therapies == "nan":
                therapies = ""

            evidence_type = str(row.get("evidence_type", ""))
            evidence_level = str(row.get("evidence_level", ""))
            significance = str(row.get("significance", ""))
            statement = str(row.get("evidence_statement", ""))
            citation = str(row.get("citation_id", ""))

            # Only include Predictive (therapeutic) and Diagnostic evidence
            if evidence_type not in ["Predictive", "Diagnostic"]:
                continue

            # Map CIViC evidence level
            mapped_level = _map_civic_level(evidence_level)

            # Build chunk text
            chunk_text = (
                f"Gene: {gene}\n"
                f"Variant: {variant}\n"
                f"Disease: {disease}\n"
                f"Evidence Level: {mapped_level}\n"
            )

            if therapies and therapies != "nan" and therapies != "":
                chunk_text += f"Therapeutic Recommendation: {therapies}\n"

            if statement and statement != "nan" and statement != "":
                chunk_text += f"Clinical Significance: {statement}\n"

            chunk_text += f"Source: CIViC ({evidence_type}, {evidence_level})\n"

            if citation and citation != "nan" and citation != "":
                chunk_text += f"Reference: PMID:{citation}\n"

            # Create chunk metadata
            # Safe ID creation - handle special characters
            safe_gene = gene.replace("/", "_").replace(" ", "_")[:50]
            safe_variant = variant.replace("/", "_").replace(" ", "_")[:50]
            safe_disease = disease.replace("/", "_").replace(" ", "_")[:50]
            chunk_id = f"civic_{safe_gene}_{safe_variant}_{safe_disease}_{idx}"
            chunk_id = chunk_id[:250]  # ChromaDB ID length limit

            chunks.append({
                "id": chunk_id,
                "text": chunk_text,
                "metadata": {
                    "source": "CIViC",
                    "gene": gene,
                    "variant": variant,
                    "cancer_type": disease,
                    "evidence_level": mapped_level,
                    "drugs": therapies if therapies else "",
                    "chunk_type": "therapeutic_recommendation",
                }
            })

        logger.info(f"Loaded {len(chunks)} CIViC clinical evidence records")
        return chunks

    except Exception as e:
        logger.error(f"Error loading CIViC data: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return []


def _map_civic_level(level: str) -> str:
    """Map CIViC evidence levels to our standardized levels."""
    level_upper = str(level).upper()

    if "A" in level_upper:
        return "FDA_Level1"
    elif "B" in level_upper:
        return "NCCN_Category1"
    elif "C" in level_upper:
        return "Clinical_Trial"
    elif "D" in level_upper:
        return "Preclinical"
    elif "E" in level_upper:
        return "Clinical_Evidence"
    else:
        return "Clinical_Evidence"


# ---------------------------------------------------------------------------
# Manual curated guidelines (NCCN/ASCO/ESMO)
# ---------------------------------------------------------------------------

def load_manual_guidelines() -> List[Dict]:
    """
    Load manually curated NCCN/ASCO/ESMO guideline extracts.

    Expected format: JSON files in data/guidelines/manual_extracts/

    Each JSON file structure:
    {
      "gene": "BRCA2",
      "guideline_source": "NCCN Genetic/Familial High-Risk v2.2024",
      "recommendations": [
        {
          "recommendation_type": "genetic_counseling",
          "text": "Cascade testing recommended for first-degree relatives",
          "evidence_level": "NCCN_Category1",
          "cancer_types": ["breast", "ovarian", "pancreatic"],
          "references": ["NCCN.2024.v2"]
        },
        ...
      ]
    }
    """
    if not MANUAL_DIR.exists():
        logger.info(f"Manual guidelines directory not found: {MANUAL_DIR} (optional)")
        return []

    logger.info(f"Loading manual guideline extracts from {MANUAL_DIR}...")

    chunks = []
    for json_file in MANUAL_DIR.glob("*.json"):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            gene = data.get("gene", "")
            guideline_source = data.get("guideline_source", "")

            for rec in data.get("recommendations", []):
                rec_type = rec.get("recommendation_type", "")
                text = rec.get("text", "")
                evidence_level = rec.get("evidence_level", "Clinical_Evidence")
                cancer_types = rec.get("cancer_types", [])
                references = rec.get("references", [])

                if not text:
                    continue

                # Build chunk text
                chunk_text = (
                    f"Gene: {gene}\n"
                    f"Recommendation Type: {rec_type}\n"
                    f"Guideline: {text}\n"
                    f"Evidence Level: {evidence_level}\n"
                    f"Cancer Types: {', '.join(cancer_types)}\n"
                    f"Source: {guideline_source}\n"
                )

                if references:
                    chunk_text += f"References: {', '.join(references)}\n"

                # Create chunk ID
                chunk_id = f"manual_{gene}_{rec_type}_{json_file.stem}".replace(" ", "_")

                chunks.append({
                    "id": chunk_id[:250],
                    "text": chunk_text,
                    "metadata": {
                        "source": guideline_source,
                        "gene": gene,
                        "evidence_level": evidence_level,
                        "chunk_type": rec_type,
                        "cancer_types": ",".join(cancer_types),
                    }
                })

        except Exception as e:
            logger.error(f"Error loading {json_file}: {e}")
            continue

    logger.info(f"Loaded {len(chunks)} manual guideline extracts")
    return chunks


# ---------------------------------------------------------------------------
# Hereditary cancer syndrome guidelines
# ---------------------------------------------------------------------------

def generate_hereditary_guidelines() -> List[Dict]:
    """
    Generate hereditary cancer syndrome management guidelines.

    These are static, well-established guidelines for germline variants
    in cancer susceptibility genes (BRCA1/2, Lynch, etc.).
    """
    logger.info("Generating hereditary cancer syndrome guidelines...")

    chunks = []

    # BRCA1/2 hereditary breast-ovarian cancer syndrome
    chunks.append({
        "id": "hereditary_BRCA1_BRCA2_management",
        "text": """Gene: BRCA1, BRCA2
Syndrome: Hereditary Breast and Ovarian Cancer Syndrome
Genetic Counseling Recommendations:
- Cascade testing recommended for all first-degree relatives
- Enhanced breast cancer screening: Annual MRI + mammography starting age 25-30
- Risk-reducing bilateral mastectomy discussion (reduces risk by 90-95%)
- Risk-reducing bilateral salpingo-oophorectomy at age 40-45 or after childbearing (reduces ovarian cancer risk by 80-90%, breast cancer risk by 50%)
- Pancreatic cancer screening if family history present (annual MRI/EUS starting age 50)
- Prostate cancer screening in males (PSA + DRE starting age 40)
- Consider genetic counseling for family planning
Evidence Level: NCCN_Category1
Source: NCCN Genetic/Familial High-Risk Assessment: Breast, Ovarian, and Pancreatic v2.2024
References: NCCN.2024.v2, PMID:27993846, PMID:28632563
""",
        "metadata": {
            "source": "NCCN_v2.2024",
            "gene": "BRCA1,BRCA2",
            "chunk_type": "genetic_counseling",
            "evidence_level": "NCCN_Category1",
            "cancer_types": "breast,ovarian,pancreatic,prostate",
        }
    })

    # Lynch syndrome
    chunks.append({
        "id": "hereditary_Lynch_management",
        "text": """Gene: MLH1, MSH2, MSH6, PMS2, EPCAM
Syndrome: Lynch Syndrome (Hereditary Non-Polyposis Colorectal Cancer)
Genetic Counseling Recommendations:
- Cascade testing for all first-degree relatives
- Colonoscopy every 1-2 years starting age 20-25 (or 2-5 years before earliest family diagnosis)
- Annual endometrial biopsy and transvaginal ultrasound starting age 30-35
- Consider prophylactic hysterectomy and bilateral salpingo-oophorectomy after childbearing
- Upper endoscopy every 3-5 years starting age 30-35 (for gastric cancer screening)
- Annual urinalysis starting age 30-35 (for urinary tract cancer)
- Consider aspirin chemoprevention (81-325mg daily) - reduces colorectal cancer risk by 60%
- Enhanced surveillance for other Lynch-associated cancers (urothelial, ovarian, brain, sebaceous)
Evidence Level: NCCN_Category1
Source: NCCN Genetic/Familial High-Risk Assessment: Colorectal v1.2024
References: NCCN.2024.v1, PMID:31711668, PMID:30516886
""",
        "metadata": {
            "source": "NCCN_v1.2024",
            "gene": "MLH1,MSH2,MSH6,PMS2,EPCAM",
            "chunk_type": "genetic_counseling",
            "evidence_level": "NCCN_Category1",
            "cancer_types": "colorectal,endometrial,gastric,ovarian",
        }
    })

    # Li-Fraumeni syndrome
    chunks.append({
        "id": "hereditary_TP53_LiFraumeni",
        "text": """Gene: TP53
Syndrome: Li-Fraumeni Syndrome
Genetic Counseling Recommendations:
- Cascade testing for all first-degree relatives
- Annual whole-body MRI starting age 18 (or earlier if family history)
- Annual brain MRI
- Breast MRI + mammography annually for women starting age 20-25
- Colonoscopy every 2-5 years starting age 25
- Biochemical testing every 4 months in childhood (CBC, CMP, LDH, ESR)
- Avoid ionizing radiation when possible (increases cancer risk)
- Consider dermatologic examination every 6-12 months
- Aggressive surveillance due to high lifetime cancer risk (>90% by age 70)
Evidence Level: NCCN_Category1
Source: NCCN Genetic/Familial High-Risk Assessment: Breast, Ovarian, and Pancreatic v2.2024
References: NCCN.2024.v2, PMID:31522936
""",
        "metadata": {
            "source": "NCCN_v2.2024",
            "gene": "TP53",
            "chunk_type": "genetic_counseling",
            "evidence_level": "NCCN_Category1",
            "cancer_types": "multiple",
        }
    })

    logger.info(f"Generated {len(chunks)} hereditary syndrome guidelines")
    return chunks


# ---------------------------------------------------------------------------
# Build ChromaDB collection
# ---------------------------------------------------------------------------

def build_actionability_database():
    """
    Main function to build the clinical actionability RAG database.
    """
    logger.info("=" * 70)
    logger.info("Building Clinical Actionability RAG Database")
    logger.info("=" * 70)

    # Ensure data directory exists
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Load all data sources
    oncokb_chunks = load_oncokb_data()
    civic_chunks = load_civic_data()
    manual_chunks = load_manual_guidelines()
    hereditary_chunks = generate_hereditary_guidelines()

    # Combine all chunks
    all_chunks = oncokb_chunks + civic_chunks + manual_chunks + hereditary_chunks

    if not all_chunks:
        logger.error("No data loaded! Cannot build database.")
        logger.error("Please download OncoKB and/or CIViC data files.")
        return False

    logger.info(f"Total chunks to index: {len(all_chunks)}")

    # Create ChromaDB client
    from src.rag.chromadb_client import get_chromadb_client
    client = get_chromadb_client(CHROMADB_DIR)

    # Create embedding function
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL
    )

    # Delete existing collection if it exists
    try:
        client.delete_collection(COLLECTION_NAME)
        logger.info(f"Deleted existing collection: {COLLECTION_NAME}")
    except:
        pass

    # Create new collection
    collection = client.create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef,
        metadata={"description": "Clinical actionability guidelines (ASCO/NCCN/ESMO/OncoKB/CIViC)"}
    )

    logger.info(f"Created collection: {COLLECTION_NAME}")

    # Add chunks in batches
    BATCH_SIZE = 500
    for i in range(0, len(all_chunks), BATCH_SIZE):
        batch = all_chunks[i:i+BATCH_SIZE]

        ids = [chunk["id"] for chunk in batch]
        documents = [chunk["text"] for chunk in batch]
        metadatas = [chunk["metadata"] for chunk in batch]

        collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas
        )

        logger.info(f"Added batch {i//BATCH_SIZE + 1}/{(len(all_chunks)-1)//BATCH_SIZE + 1} ({len(batch)} chunks)")

    logger.info("=" * 70)
    logger.info("✓ Clinical Actionability Database Built Successfully!")
    logger.info(f"  Total documents: {len(all_chunks)}")
    logger.info(f"  Collection: {COLLECTION_NAME}")
    logger.info(f"  Location: {CHROMADB_DIR}")
    logger.info("=" * 70)

    # Print summary statistics
    logger.info("\nData Source Summary:")
    logger.info(f"  OncoKB:             {len(oncokb_chunks):4d} chunks")
    logger.info(f"  CIViC:              {len(civic_chunks):4d} chunks")
    logger.info(f"  Manual NCCN/ASCO:   {len(manual_chunks):4d} chunks")
    logger.info(f"  Hereditary syndromes: {len(hereditary_chunks):4d} chunks")
    logger.info(f"  {'─'*40}")
    logger.info(f"  TOTAL:              {len(all_chunks):4d} chunks")

    return True


# ---------------------------------------------------------------------------
# Helper: Export cancer gene list
# ---------------------------------------------------------------------------

def export_cancer_gene_list():
    """Export the cancer gene list for use by the pipeline."""
    output_file = Path("data/guidelines/cancer_genes.json")
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, 'w') as f:
        json.dump({
            "cancer_genes": sorted(list(CANCER_GENES)),
            "hpo_to_cancer_type": HPO_TO_CANCER_TYPE,
            "last_updated": "2024-06-19",
        }, f, indent=2)

    logger.info(f"✓ Exported cancer gene list to {output_file}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logger.info("Clinical Actionability RAG Builder")
    logger.info("")
    logger.info("This script builds a RAG database for therapeutic recommendations")
    logger.info("from ASCO, NCCN, ESMO, OncoKB, and CIViC guidelines.")
    logger.info("")
    logger.info("Required data files:")
    logger.info(f"  1. OncoKB: {ONCOKB_FILE}")
    logger.info(f"     Download: https://www.oncokb.org/api/v1/utils/allActionableVariants.txt")
    logger.info(f"  2. CIViC: {CIVIC_FILE}")
    logger.info(f"     Download: https://civicdb.org/downloads/nightly/nightly-ClinicalEvidenceSummaries.tsv")
    logger.info(f"  3. Manual extracts (optional): {MANUAL_DIR}/*.json")
    logger.info("")

    # Check if data files exist
    missing = []
    if not ONCOKB_FILE.exists():
        missing.append("OncoKB")
    if not CIVIC_FILE.exists():
        missing.append("CIViC")

    if missing:
        logger.warning(f"⚠ Missing data files: {', '.join(missing)}")
        logger.warning("The database will be built with available data only.")
        logger.warning("For full functionality, please download the missing files.")
        logger.warning("")

        response = input("Continue anyway? (y/n): ")
        if response.lower() != 'y':
            logger.info("Aborted.")
            sys.exit(0)

    # Build database
    success = build_actionability_database()

    if success:
        # Export cancer gene list
        export_cancer_gene_list()

        logger.info("")
        logger.info("Next steps:")
        logger.info("  1. Restart your pipeline to load the new collection")
        logger.info("  2. Test with: python -m pytest tests/test_actionability.py")
        logger.info("  3. Run on a known actionable variant (e.g., BRAF V600E)")
        logger.info("")
        sys.exit(0)
    else:
        logger.error("Failed to build database.")
        sys.exit(1)

